import json
import os
import queue
import select
import subprocess
import time
from multiprocessing.queues import Queue

from coincurve import PrivateKey

from app.generator.protocol import (
    CancelJob,
    GpuInfo,
    ShutdownWorker,
    WorkAssignment,
    WorkerCommand,
    WorkerEvent,
    WorkerEventType,
)
from app.services.patterns import tron_address_from_public_key, validate_tron_address


def emit(event_queue: Queue, event_type: WorkerEventType, gpu: GpuInfo, **values: object) -> None:
    event_queue.put(
        WorkerEvent(event_type=event_type, gpu_uuid=gpu.uuid, worker_pid=os.getpid(), **values)
    )


def run_self_test() -> None:
    public_key = PrivateKey.from_int(1).public_key
    address = tron_address_from_public_key(public_key)
    if not validate_tron_address(address):
        raise RuntimeError("CPU address self-test failed.")


def scalar_hex(value: int) -> str:
    if value < 1:
        raise ValueError("CUDA range start must be positive.")
    return value.to_bytes(32, "big").hex()


def stop_native_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


class NativeCudaSession:
    def __init__(self, native_binary: str, gpu: GpuInfo) -> None:
        self.native_binary = native_binary
        self.gpu = gpu
        self.process: subprocess.Popen[str] | None = None
        self.chain_count = 0

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        self.stop(graceful=False)
        try:
            self.process = subprocess.Popen(
                [
                    self.native_binary,
                    "serve-gpu",
                    "--device",
                    str(self.gpu.device_index),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            payload = self.read_response(timeout=60)
        except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
            self.stop(graceful=False)
            raise RuntimeError(
                f"Persistent CUDA session failed to start for {self.gpu.uuid}: {exc}"
            ) from exc
        if (
            payload is None
            or payload.get("ready") is not True
            or payload.get("self_test_passed") is not True
            or payload.get("protocol") != 1
        ):
            self.stop(graceful=False)
            raise RuntimeError(
                f"Persistent CUDA session returned an invalid handshake for {self.gpu.uuid}."
            )
        try:
            self.chain_count = max(0, int(payload.get("chain_count", 0)))
        except (TypeError, ValueError) as exc:
            self.stop(graceful=False)
            raise RuntimeError(
                f"Persistent CUDA session returned an invalid chain count for {self.gpu.uuid}."
            ) from exc

    def submit_search(self, assignment: WorkAssignment, batch_start: int, batch_count: int) -> None:
        self.start()
        process = self._require_process()
        if process.stdin is None:
            raise RuntimeError("Persistent CUDA session has no input stream.")
        request = " ".join(
            [
                "SEARCH",
                assignment.client_public_key,
                assignment.pattern,
                assignment.prefix[1:],
                assignment.suffix,
                scalar_hex(batch_start),
                str(batch_count),
            ]
        )
        try:
            process.stdin.write(f"{request}\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise RuntimeError(self._failure_detail("Persistent CUDA session stopped.")) from exc

    def read_response(self, timeout: float) -> dict[str, object] | None:
        process = self._require_process()
        if process.stdout is None:
            raise RuntimeError("Persistent CUDA session has no output stream.")
        readable, _, _ = select.select([process.stdout], [], [], timeout)
        if not readable:
            if process.poll() is not None:
                raise RuntimeError(self._failure_detail("Persistent CUDA session exited."))
            return None
        line = process.stdout.readline()
        if not line:
            raise RuntimeError(self._failure_detail("Persistent CUDA session closed its output."))
        try:
            payload = json.loads(line)
        except (ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("Persistent CUDA session returned invalid JSON output.") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Persistent CUDA session returned a non-object response.")
        error = payload.get("error")
        if isinstance(error, str) and error:
            raise RuntimeError(error)
        return payload

    def stop(self, *, graceful: bool = True) -> None:
        process = self.process
        self.process = None
        if process is None:
            return
        if process.poll() is None and graceful and process.stdin is not None:
            try:
                process.stdin.write("QUIT\n")
                process.stdin.flush()
                process.wait(timeout=2)
            except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                pass
        stop_native_process(process)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()

    def _require_process(self) -> subprocess.Popen[str]:
        if self.process is None:
            raise RuntimeError("Persistent CUDA session is not running.")
        return self.process

    def _failure_detail(self, fallback: str) -> str:
        process = self.process
        if process is None or process.stderr is None or process.poll() is None:
            return fallback
        detail = process.stderr.read().strip()[:2_000]
        return detail or fallback


def execute_cuda_assignment(
    session: NativeCudaSession,
    gpu: GpuInfo,
    assignment: WorkAssignment,
    command_queue: Queue,
    event_queue: Queue,
) -> bool:
    emit(
        event_queue,
        WorkerEventType.STARTED,
        gpu,
        job_id=assignment.job_id,
        shard_id=assignment.shard_id,
        lease_token=assignment.lease_token,
    )
    processed = 0
    shutdown = False
    maximum_native_batch = 67_108_864
    adaptive_rate = max(1, assignment.simulated_rate)
    target_batch_seconds = max(
        0.01,
        assignment.kernel_batch_size / max(1, assignment.simulated_rate),
    )
    while processed < assignment.range_count:
        canceled, shutdown_requested = drain_control_commands(command_queue, assignment)
        shutdown = shutdown or shutdown_requested
        if canceled or shutdown:
            emit(
                event_queue,
                WorkerEventType.CANCELED,
                gpu,
                job_id=assignment.job_id,
                shard_id=assignment.shard_id,
                lease_token=assignment.lease_token,
                processed_total=processed,
            )
            return shutdown

        adaptive_batch_size = max(1, int(adaptive_rate * target_batch_seconds))
        remaining = assignment.range_count - processed
        batch_count = min(
            adaptive_batch_size,
            maximum_native_batch,
            remaining,
        )
        chain_count = max(0, int(getattr(session, "chain_count", 0)))
        if chain_count > 0 and batch_count >= chain_count:
            batch_count -= batch_count % chain_count
        batch_start = assignment.range_start + processed
        batch_started = time.monotonic()
        session.submit_search(assignment, batch_start, batch_count)
        payload: dict[str, object] | None = None
        while payload is None:
            canceled, shutdown_requested = drain_control_commands(command_queue, assignment)
            shutdown = shutdown or shutdown_requested
            if canceled or shutdown:
                session.stop(graceful=False)
                emit(
                    event_queue,
                    WorkerEventType.CANCELED,
                    gpu,
                    job_id=assignment.job_id,
                    shard_id=assignment.shard_id,
                    lease_token=assignment.lease_token,
                    processed_total=processed,
                )
                return shutdown
            payload = session.read_response(timeout=0.02)
        try:
            attempts = int(payload["attempts"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("Native CUDA search returned invalid JSON output.") from exc
        if attempts != batch_count:
            raise RuntimeError("Native CUDA search reported an unexpected attempt count.")
        batch_elapsed = max(time.monotonic() - batch_started, 1e-9)
        observed_rate = max(1, int(attempts / batch_elapsed))
        # Smooth transient clock/desktop-load variation while adapting the next launch to
        # the operator's configured batch duration.
        adaptive_rate = max(1, (adaptive_rate * 3 + observed_rate) // 4)
        processed += attempts
        if payload.get("found") is True:
            address = payload.get("address")
            offset = payload.get("offset")
            if not isinstance(address, str) or not isinstance(offset, str):
                raise RuntimeError("Native CUDA winner did not include an address and offset.")
            emit(
                event_queue,
                WorkerEventType.FOUND,
                gpu,
                job_id=assignment.job_id,
                shard_id=assignment.shard_id,
                lease_token=assignment.lease_token,
                processed_total=processed,
                attempts_delta=attempts,
                observed_rate=observed_rate,
                address=address,
                offset=offset,
            )
            return shutdown
        emit(
            event_queue,
            WorkerEventType.PROGRESS,
            gpu,
            job_id=assignment.job_id,
            shard_id=assignment.shard_id,
            lease_token=assignment.lease_token,
            processed_total=processed,
            attempts_delta=attempts,
            observed_rate=observed_rate,
        )

    emit(
        event_queue,
        WorkerEventType.EXHAUSTED,
        gpu,
        job_id=assignment.job_id,
        shard_id=assignment.shard_id,
        lease_token=assignment.lease_token,
        processed_total=processed,
    )
    return shutdown


def drain_control_commands(command_queue: Queue, assignment: WorkAssignment) -> tuple[bool, bool]:
    canceled = False
    shutdown = False
    while True:
        try:
            command: WorkerCommand = command_queue.get_nowait()
        except queue.Empty:
            break
        if isinstance(command, ShutdownWorker):
            shutdown = True
        elif isinstance(command, CancelJob) and command.job_id == assignment.job_id:
            canceled = True
        elif isinstance(command, WorkAssignment):
            raise RuntimeError("Worker received overlapping assignments.")
    return canceled, shutdown


def execute_simulated_assignment(
    gpu: GpuInfo,
    assignment: WorkAssignment,
    command_queue: Queue,
    event_queue: Queue,
) -> bool:
    emit(
        event_queue,
        WorkerEventType.STARTED,
        gpu,
        job_id=assignment.job_id,
        shard_id=assignment.shard_id,
        lease_token=assignment.lease_token,
    )
    processed = 0
    shutdown = False
    while processed < assignment.range_count:
        canceled, shutdown = drain_control_commands(command_queue, assignment)
        if canceled or shutdown:
            emit(
                event_queue,
                WorkerEventType.CANCELED,
                gpu,
                job_id=assignment.job_id,
                shard_id=assignment.shard_id,
                lease_token=assignment.lease_token,
                processed_total=processed,
            )
            return shutdown

        batch_count = min(assignment.kernel_batch_size, assignment.range_count - processed)
        duration = batch_count / max(1, assignment.simulated_rate)
        time.sleep(duration)
        processed += batch_count
        emit(
            event_queue,
            WorkerEventType.PROGRESS,
            gpu,
            job_id=assignment.job_id,
            shard_id=assignment.shard_id,
            lease_token=assignment.lease_token,
            processed_total=processed,
            attempts_delta=batch_count,
        )

    emit(
        event_queue,
        WorkerEventType.EXHAUSTED,
        gpu,
        job_id=assignment.job_id,
        shard_id=assignment.shard_id,
        lease_token=assignment.lease_token,
        processed_total=processed,
    )
    return shutdown


def simulator_worker_main(gpu: GpuInfo, command_queue: Queue, event_queue: Queue) -> None:
    emit(event_queue, WorkerEventType.STARTING, gpu)
    try:
        emit(event_queue, WorkerEventType.SELF_TESTING, gpu)
        run_self_test()
        emit(event_queue, WorkerEventType.READY, gpu)
        while True:
            command: WorkerCommand = command_queue.get()
            if isinstance(command, ShutdownWorker):
                break
            if isinstance(command, CancelJob):
                continue
            try:
                should_shutdown = execute_simulated_assignment(
                    gpu, command, command_queue, event_queue
                )
            except Exception as exc:
                emit(
                    event_queue,
                    WorkerEventType.FAILED,
                    gpu,
                    job_id=command.job_id,
                    shard_id=command.shard_id,
                    lease_token=command.lease_token,
                    error=str(exc),
                )
                should_shutdown = False
            if should_shutdown:
                break
            emit(event_queue, WorkerEventType.READY, gpu)
    except Exception as exc:
        emit(event_queue, WorkerEventType.FAILED, gpu, error=str(exc))
    finally:
        emit(event_queue, WorkerEventType.STOPPED, gpu)


def cuda_worker_main(
    native_binary: str, gpu: GpuInfo, command_queue: Queue, event_queue: Queue
) -> None:
    emit(event_queue, WorkerEventType.STARTING, gpu)
    session = NativeCudaSession(native_binary, gpu)
    try:
        emit(event_queue, WorkerEventType.SELF_TESTING, gpu)
        run_self_test()
        session.start()
        emit(event_queue, WorkerEventType.READY, gpu)
        while True:
            command: WorkerCommand = command_queue.get()
            if isinstance(command, ShutdownWorker):
                break
            if isinstance(command, CancelJob):
                continue
            try:
                should_shutdown = execute_cuda_assignment(
                    session, gpu, command, command_queue, event_queue
                )
            except Exception as exc:
                session.stop(graceful=False)
                emit(
                    event_queue,
                    WorkerEventType.FAILED,
                    gpu,
                    job_id=command.job_id,
                    shard_id=command.shard_id,
                    lease_token=command.lease_token,
                    error=str(exc),
                )
                should_shutdown = False
            if should_shutdown:
                break
            emit(event_queue, WorkerEventType.READY, gpu)
    except Exception as exc:
        emit(event_queue, WorkerEventType.FAILED, gpu, error=str(exc))
    finally:
        session.stop()
        emit(event_queue, WorkerEventType.STOPPED, gpu)
