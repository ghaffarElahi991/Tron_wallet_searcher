import uuid
from dataclasses import dataclass
from enum import StrEnum


class WorkerEventType(StrEnum):
    STARTING = "starting"
    SELF_TESTING = "self_testing"
    READY = "ready"
    STARTED = "started"
    PROGRESS = "progress"
    EXHAUSTED = "exhausted"
    FOUND = "found"
    CANCELED = "canceled"
    FAILED = "failed"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class GpuInfo:
    device_index: int
    uuid: str
    name: str
    memory_total_mb: int
    benchmark_rate: int
    kernel_version: str = "simulator-v1"


@dataclass(frozen=True, slots=True)
class WorkAssignment:
    job_id: uuid.UUID
    shard_id: uuid.UUID
    lease_token: uuid.UUID
    gpu_uuid: str
    client_public_key: str
    pattern: str
    prefix: str
    suffix: str
    range_start: int
    range_count: int
    kernel_batch_size: int
    simulated_rate: int


@dataclass(frozen=True, slots=True)
class CancelJob:
    job_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class ShutdownWorker:
    pass


@dataclass(frozen=True, slots=True)
class WorkerEvent:
    event_type: WorkerEventType
    gpu_uuid: str
    worker_pid: int
    job_id: uuid.UUID | None = None
    shard_id: uuid.UUID | None = None
    lease_token: uuid.UUID | None = None
    processed_total: int = 0
    attempts_delta: int = 0
    observed_rate: int = 0
    address: str | None = None
    offset: str | None = None
    error: str | None = None


WorkerCommand = WorkAssignment | CancelJob | ShutdownWorker
