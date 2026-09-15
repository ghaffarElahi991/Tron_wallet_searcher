import multiprocessing
import queue
import uuid
from dataclasses import dataclass
from multiprocessing.context import BaseContext
from multiprocessing.process import BaseProcess
from multiprocessing.queues import Queue

from app.generator.protocol import (
    CancelJob,
    GpuInfo,
    ShutdownWorker,
    WorkAssignment,
    WorkerEvent,
    WorkerEventType,
)
from app.generator.worker import cuda_worker_main, simulator_worker_main


@dataclass(slots=True)
class WorkerHandle:
    gpu: GpuInfo
    commands: Queue
    process: BaseProcess
    assignment: WorkAssignment | None = None


class LocalGpuSupervisor:
    def __init__(
        self,
        devices: list[GpuInfo],
        *,
        mode: str = "simulator",
        native_binary: str | None = None,
    ) -> None:
        if not devices:
            raise ValueError("At least one GPU is required.")
        self.devices = devices
        self.mode = mode
        self.native_binary = native_binary
        if mode not in {"simulator", "cuda"}:
            raise ValueError("Worker mode must be simulator or cuda.")
        if mode == "cuda" and not native_binary:
            raise ValueError("CUDA worker mode requires a native binary path.")
        self.context: BaseContext = multiprocessing.get_context("spawn")
        self.events: Queue = self.context.Queue()
        self.handles: dict[str, WorkerHandle] = {}
        self.stopping = False

    @property
    def gpu_uuids(self) -> set[str]:
        return {device.uuid for device in self.devices}

    def _spawn(self, gpu: GpuInfo) -> WorkerHandle:
        commands = self.context.Queue()
        if self.mode == "cuda":
            target = cuda_worker_main
            args = (self.native_binary or "", gpu, commands, self.events)
        else:
            target = simulator_worker_main
            args = (gpu, commands, self.events)
        process = self.context.Process(
            target=target,
            args=args,
            name=f"tronforge-{gpu.uuid}",
            daemon=True,
        )
        process.start()
        return WorkerHandle(gpu=gpu, commands=commands, process=process)

    def start(self) -> None:
        if self.handles:
            raise RuntimeError("GPU supervisor has already started.")
        self.handles = {device.uuid: self._spawn(device) for device in self.devices}

    def dispatch(self, assignment: WorkAssignment) -> None:
        handle = self.handles[assignment.gpu_uuid]
        if handle.assignment is not None:
            raise RuntimeError(f"GPU {assignment.gpu_uuid} is already assigned.")
        if not handle.process.is_alive():
            raise RuntimeError(f"GPU worker {assignment.gpu_uuid} is not alive.")
        handle.assignment = assignment
        handle.commands.put(assignment)

    def cancel_job(self, job_id: uuid.UUID) -> None:
        for handle in self.handles.values():
            if handle.assignment is not None and handle.assignment.job_id == job_id:
                handle.commands.put(CancelJob(job_id=job_id))

    def poll_events(self) -> list[WorkerEvent]:
        collected: list[WorkerEvent] = []
        while True:
            try:
                event: WorkerEvent = self.events.get_nowait()
            except queue.Empty:
                break
            handle = self.handles.get(event.gpu_uuid)
            if handle is not None and event.event_type in {
                WorkerEventType.EXHAUSTED,
                WorkerEventType.FOUND,
                WorkerEventType.CANCELED,
                WorkerEventType.FAILED,
            }:
                handle.assignment = None
            collected.append(event)
        return collected

    def restart_dead_workers(self) -> list[WorkerEvent]:
        synthetic_events: list[WorkerEvent] = []
        if self.stopping:
            return synthetic_events
        for gpu_uuid, handle in list(self.handles.items()):
            if handle.process.is_alive():
                continue
            if handle.assignment is not None:
                synthetic_events.append(
                    WorkerEvent(
                        event_type=WorkerEventType.FAILED,
                        gpu_uuid=gpu_uuid,
                        worker_pid=handle.process.pid or 0,
                        job_id=handle.assignment.job_id,
                        shard_id=handle.assignment.shard_id,
                        lease_token=handle.assignment.lease_token,
                        error="GPU worker exited before completing its assignment.",
                    )
                )
            self.handles[gpu_uuid] = self._spawn(handle.gpu)
        return synthetic_events

    def stop(self, timeout: float = 5.0) -> None:
        self.stopping = True
        for handle in self.handles.values():
            if handle.process.is_alive():
                handle.commands.put(ShutdownWorker())
        for handle in self.handles.values():
            handle.process.join(timeout=timeout)
            if handle.process.is_alive():
                handle.process.terminate()
                handle.process.join(timeout=1)
        self.handles.clear()
