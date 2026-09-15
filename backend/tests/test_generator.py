import json
import queue
import time
import uuid
from pathlib import Path

from app.config import Settings
from app.generator.discovery import (
    GpuDiscoveryError,
    benchmark_cuda_device,
    discover_gpus,
    parse_nvidia_smi,
)
from app.generator.protocol import GpuInfo, WorkAssignment, WorkerEventType
from app.generator.supervisor import LocalGpuSupervisor
from app.generator.worker import NativeCudaSession, execute_cuda_assignment


def wait_for_events(
    supervisor: LocalGpuSupervisor,
    event_type: WorkerEventType,
    expected_gpus: set[str],
    timeout: float = 5,
) -> set[str]:
    observed: set[str] = set()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and observed != expected_gpus:
        for event in supervisor.poll_events():
            if event.event_type == event_type:
                observed.add(event.gpu_uuid)
        time.sleep(0.01)
    return observed


def simulated_devices(count: int = 2) -> list[GpuInfo]:
    return [
        GpuInfo(
            device_index=index,
            uuid=f"SIM-TEST-{index}",
            name=f"Test GPU {index}",
            memory_total_mb=24_576,
            benchmark_rate=1_000_000,
        )
        for index in range(count)
    ]


def test_parses_nvidia_smi_inventory() -> None:
    output = "0, GPU-abc, NVIDIA RTX 4090, 24564\n1, GPU-def, NVIDIA RTX 4090, 24564\n"
    devices = parse_nvidia_smi(output)
    assert [device.uuid for device in devices] == ["GPU-abc", "GPU-def"]
    assert devices[0].memory_total_mb == 24_564


def test_simulator_requires_explicit_gpu_count() -> None:
    settings = Settings(generator_mode="simulator", simulated_gpu_count=0)
    try:
        discover_gpus(settings)
    except GpuDiscoveryError as exc:
        assert "SIMULATED_GPU_COUNT" in str(exc)
    else:
        raise AssertionError("GPU discovery should reject an empty simulator fleet.")


def test_parses_native_cuda_benchmark(monkeypatch) -> None:
    captured: list[str] = []

    def fake_run(command: list[str], **_kwargs: object) -> object:
        captured.extend(command)
        return type(
            "Completed",
            (),
            {"stdout": json.dumps({"candidates_per_second": 38_302_542.22})},
        )()

    monkeypatch.setattr("app.generator.discovery.subprocess.run", fake_run)
    rate = benchmark_cuda_device(Path("/native/tronforge-generator"), 2, 1_048_576)
    assert rate == 38_302_542
    assert captured[1:4] == ["search-gpu-chained16", "--device", "2"]


def test_cuda_worker_streams_assignment_to_persistent_native_session() -> None:
    requests: list[tuple[WorkAssignment, int, int]] = []

    class NativeSession:
        def submit_search(
            self, assignment: WorkAssignment, batch_start: int, batch_count: int
        ) -> None:
            requests.append((assignment, batch_start, batch_count))

        def read_response(self, timeout: float) -> dict[str, object]:
            assert timeout == 0.02
            return {
                "backend": "cuda-persistent",
                "found": True,
                "attempts": 1,
                "address": "TDvSsdrNM5eeXNL3czpa6AxLDHZA9nwe9K",
                "offset": "0" * 63 + "1",
            }

        def stop(self, *, graceful: bool = True) -> None:
            raise AssertionError(f"Healthy search unexpectedly stopped (graceful={graceful}).")

    gpu = simulated_devices(1)[0]
    assignment = WorkAssignment(
        job_id=uuid.uuid4(),
        shard_id=uuid.uuid4(),
        lease_token=uuid.uuid4(),
        gpu_uuid=gpu.uuid,
        client_public_key="0279be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798",
        pattern="2x2",
        prefix="TDv",
        suffix="9K",
        range_start=1,
        range_count=1,
        kernel_batch_size=1,
        simulated_rate=1,
    )
    commands: queue.Queue = queue.Queue()
    events: queue.Queue = queue.Queue()
    stopped = execute_cuda_assignment(
        NativeSession(),  # type: ignore[arg-type]
        gpu,
        assignment,
        commands,
        events,
    )
    assert stopped is False
    emitted = [events.get_nowait(), events.get_nowait()]
    assert [event.event_type for event in emitted] == [
        WorkerEventType.STARTED,
        WorkerEventType.FOUND,
    ]
    assert emitted[-1].offset == "0" * 63 + "1"
    assert requests == [(assignment, 1, 1)]


def test_cuda_worker_adapts_batch_size_to_observed_rate(monkeypatch) -> None:
    requests: list[tuple[int, int]] = []

    class NativeSession:
        def submit_search(
            self, _assignment: WorkAssignment, batch_start: int, batch_count: int
        ) -> None:
            requests.append((batch_start, batch_count))

        def read_response(self, timeout: float) -> dict[str, object]:
            assert timeout == 0.02
            return {"found": False, "attempts": requests[-1][1]}

        def stop(self, *, graceful: bool = True) -> None:
            raise AssertionError(f"Healthy search unexpectedly stopped (graceful={graceful}).")

    clock = iter((0.0, 0.5, 0.5, 1.0, 1.0, 1.5))
    monkeypatch.setattr("app.generator.worker.time.monotonic", lambda: next(clock))
    gpu = simulated_devices(1)[0]
    assignment = WorkAssignment(
        job_id=uuid.uuid4(),
        shard_id=uuid.uuid4(),
        lease_token=uuid.uuid4(),
        gpu_uuid=gpu.uuid,
        client_public_key="0279be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798",
        pattern="2x2",
        prefix="TDv",
        suffix="9K",
        range_start=1,
        range_count=300,
        kernel_batch_size=100,
        simulated_rate=100,
    )
    commands: queue.Queue = queue.Queue()
    events: queue.Queue = queue.Queue()

    stopped = execute_cuda_assignment(
        NativeSession(),  # type: ignore[arg-type]
        gpu,
        assignment,
        commands,
        events,
    )

    assert stopped is False
    assert requests == [(1, 100), (101, 125), (226, 75)]
    emitted = []
    while not events.empty():
        emitted.append(events.get_nowait())
    progress = [event for event in emitted if event.event_type == WorkerEventType.PROGRESS]
    assert [event.observed_rate for event in progress] == [200, 250, 150]


def test_cuda_worker_aligns_large_batches_to_persistent_chain_count(monkeypatch) -> None:
    requests: list[tuple[int, int]] = []

    class NativeSession:
        chain_count = 64

        def submit_search(
            self, _assignment: WorkAssignment, batch_start: int, batch_count: int
        ) -> None:
            requests.append((batch_start, batch_count))

        def read_response(self, timeout: float) -> dict[str, object]:
            assert timeout == 0.02
            return {"found": False, "attempts": requests[-1][1]}

        def stop(self, *, graceful: bool = True) -> None:
            raise AssertionError(f"Healthy search unexpectedly stopped (graceful={graceful}).")

    clock = iter((0.0, 1.0, 1.0, 2.0))
    monkeypatch.setattr("app.generator.worker.time.monotonic", lambda: next(clock))
    gpu = simulated_devices(1)[0]
    assignment = WorkAssignment(
        job_id=uuid.uuid4(),
        shard_id=uuid.uuid4(),
        lease_token=uuid.uuid4(),
        gpu_uuid=gpu.uuid,
        client_public_key="0279be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798",
        pattern="2x2",
        prefix="TDv",
        suffix="9K",
        range_start=1,
        range_count=130,
        kernel_batch_size=128,
        simulated_rate=128,
    )

    stopped = execute_cuda_assignment(
        NativeSession(),  # type: ignore[arg-type]
        gpu,
        assignment,
        queue.Queue(),
        queue.Queue(),
    )

    assert stopped is False
    assert requests == [(1, 128), (129, 2)]


def test_native_cuda_session_reuses_one_process(monkeypatch) -> None:
    commands: list[list[str]] = []

    class InputStream:
        def __init__(self) -> None:
            self.writes: list[str] = []

        def write(self, value: str) -> int:
            self.writes.append(value)
            return len(value)

        def flush(self) -> None:
            pass

        def close(self) -> None:
            pass

    class OutputStream:
        def __init__(self) -> None:
            self.lines = [
                json.dumps(
                    {
                        "ready": True,
                        "backend": "cuda-persistent",
                        "protocol": 1,
                        "device": 0,
                        "self_test_passed": True,
                        "chain_count": 81_920,
                    }
                )
                + "\n",
                json.dumps({"found": False, "attempts": 1}) + "\n",
                json.dumps({"found": False, "attempts": 1}) + "\n",
            ]

        def readline(self) -> str:
            return self.lines.pop(0)

        def close(self) -> None:
            pass

    class ErrorStream:
        def read(self) -> str:
            return ""

        def close(self) -> None:
            pass

    class NativeProcess:
        def __init__(self) -> None:
            self.stdin = InputStream()
            self.stdout = OutputStream()
            self.stderr = ErrorStream()
            self.running = True

        def poll(self) -> int | None:
            return None if self.running else 0

        def wait(self, timeout: float) -> int:
            assert timeout == 2
            self.running = False
            return 0

        def terminate(self) -> None:
            self.running = False

        def kill(self) -> None:
            self.running = False

    process = NativeProcess()

    def fake_popen(command: list[str], **_kwargs: object) -> NativeProcess:
        commands.append(command)
        return process

    def fake_select(*_args: object) -> tuple[list[OutputStream], list[object], list[object]]:
        return [process.stdout], [], []

    monkeypatch.setattr("app.generator.worker.subprocess.Popen", fake_popen)
    monkeypatch.setattr("app.generator.worker.select.select", fake_select)
    gpu = simulated_devices(1)[0]
    assignment = WorkAssignment(
        job_id=uuid.uuid4(),
        shard_id=uuid.uuid4(),
        lease_token=uuid.uuid4(),
        gpu_uuid=gpu.uuid,
        client_public_key="0279be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798",
        pattern="2x2",
        prefix="TDv",
        suffix="9K",
        range_start=1,
        range_count=1,
        kernel_batch_size=1,
        simulated_rate=1,
    )
    session = NativeCudaSession("/native/tronforge-generator", gpu)
    session.start()
    assert session.chain_count == 81_920
    session.submit_search(assignment, 1, 1)
    assert session.read_response(0.02) == {"found": False, "attempts": 1}
    session.submit_search(assignment, 2, 1)
    assert session.read_response(0.02) == {"found": False, "attempts": 1}
    session.stop()

    assert commands == [["/native/tronforge-generator", "serve-gpu", "--device", "0"]]
    assert process.stdin.writes == [
        "SEARCH 0279be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798 "
        "2x2 Dv 9K 0000000000000000000000000000000000000000000000000000000000000001 1\n",
        "SEARCH 0279be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798 "
        "2x2 Dv 9K 0000000000000000000000000000000000000000000000000000000000000002 1\n",
        "QUIT\n",
    ]


def test_all_workers_process_distinct_ranges_for_one_job() -> None:
    devices = simulated_devices()
    supervisor = LocalGpuSupervisor(devices)
    supervisor.start()
    try:
        expected_gpus = {device.uuid for device in devices}
        assert wait_for_events(supervisor, WorkerEventType.READY, expected_gpus) == expected_gpus

        job_id = uuid.uuid4()
        assignments = [
            WorkAssignment(
                job_id=job_id,
                shard_id=uuid.uuid4(),
                lease_token=uuid.uuid4(),
                gpu_uuid=device.uuid,
                client_public_key="02" + "00" * 32,
                pattern="3x4",
                prefix="TAab",
                suffix="1111",
                range_start=1 + index * 10_000,
                range_count=10_000,
                kernel_batch_size=2_000,
                simulated_rate=device.benchmark_rate,
            )
            for index, device in enumerate(devices)
        ]
        for assignment in assignments:
            supervisor.dispatch(assignment)

        exhausted = wait_for_events(supervisor, WorkerEventType.EXHAUSTED, expected_gpus)
        assert exhausted == expected_gpus
        first_end = assignments[0].range_start + assignments[0].range_count
        assert first_end <= assignments[1].range_start
    finally:
        supervisor.stop()


def test_worker_cancels_within_a_short_batch() -> None:
    device = simulated_devices(count=1)[0]
    supervisor = LocalGpuSupervisor([device])
    supervisor.start()
    try:
        assert wait_for_events(supervisor, WorkerEventType.READY, {device.uuid}) == {device.uuid}
        job_id = uuid.uuid4()
        assignment = WorkAssignment(
            job_id=job_id,
            shard_id=uuid.uuid4(),
            lease_token=uuid.uuid4(),
            gpu_uuid=device.uuid,
            client_public_key="02" + "00" * 32,
            pattern="3x4",
            prefix="TAab",
            suffix="1111",
            range_start=1,
            range_count=10_000_000,
            kernel_batch_size=50_000,
            simulated_rate=device.benchmark_rate,
        )
        supervisor.dispatch(assignment)
        assert wait_for_events(supervisor, WorkerEventType.STARTED, {device.uuid}) == {device.uuid}

        started = time.monotonic()
        supervisor.cancel_job(job_id)
        assert wait_for_events(supervisor, WorkerEventType.CANCELED, {device.uuid}) == {device.uuid}
        assert time.monotonic() - started < 1
    finally:
        supervisor.stop()
