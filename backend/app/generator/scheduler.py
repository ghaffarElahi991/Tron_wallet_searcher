import asyncio
import logging
import uuid

from app.config import Settings
from app.generator.discovery import resolve_native_binary
from app.generator.protocol import GpuInfo, WorkerEvent, WorkerEventType
from app.generator.repository import SchedulerRepository
from app.generator.supervisor import LocalGpuSupervisor
from app.models import JobStatus

logger = logging.getLogger(__name__)
TERMINAL_JOB_STATUSES = {
    JobStatus.READY,
    JobStatus.OWNERSHIP_VERIFIED,
    JobStatus.FAILED,
    JobStatus.CANCELED,
    JobStatus.TIMED_OUT,
}


class GenerationScheduler:
    def __init__(self, settings: Settings, devices: list[GpuInfo]) -> None:
        self.settings = settings
        self.devices = devices
        native_binary = (
            str(resolve_native_binary(settings)) if settings.generator_mode == "cuda" else None
        )
        self.supervisor = LocalGpuSupervisor(
            devices,
            mode=settings.generator_mode,
            native_binary=native_binary,
        )
        self.repository = SchedulerRepository()
        self.idle_gpus: set[str] = set()
        self.active_job_id: uuid.UUID | None = None
        self.draining_job_id: uuid.UUID | None = None

    async def run(self, stop_event: asyncio.Event) -> None:
        await self.repository.recover_interrupted_work()
        await self.repository.initialize(self.devices)
        self.supervisor.start()
        logger.info("Started %d local GPU workers", len(self.devices))
        try:
            while not stop_event.is_set():
                events = self.supervisor.poll_events()
                events.extend(self.supervisor.restart_dead_workers())
                for event in events:
                    await self._handle_event(event)

                await self._check_active_job()
                await self._finish_draining()
                await self._claim_if_ready()
                await self._fill_idle_gpus()
                await asyncio.sleep(self.settings.generator_poll_interval_ms / 1000)
        finally:
            if self.active_job_id is not None:
                self.supervisor.cancel_job(self.active_job_id)
            self.supervisor.stop()
            await self.repository.mark_devices_offline()
            logger.info("GPU scheduler stopped")

    async def _handle_event(self, event: WorkerEvent) -> None:
        await self.repository.record_event(event)
        if event.event_type == WorkerEventType.READY:
            self.idle_gpus.add(event.gpu_uuid)
            logger.info("GPU %s is ready", event.gpu_uuid)
        elif event.event_type in {WorkerEventType.STARTED, WorkerEventType.PROGRESS}:
            self.idle_gpus.discard(event.gpu_uuid)
        elif event.event_type == WorkerEventType.FOUND:
            logger.info("GPU %s found a candidate for job %s", event.gpu_uuid, event.job_id)
        elif event.event_type == WorkerEventType.FAILED:
            self.idle_gpus.discard(event.gpu_uuid)
            logger.error("GPU %s failed: %s", event.gpu_uuid, event.error)
        elif event.event_type == WorkerEventType.STOPPED:
            self.idle_gpus.discard(event.gpu_uuid)

    async def _check_active_job(self) -> None:
        if self.active_job_id is None:
            return
        status = await self.repository.active_job_status(self.active_job_id)
        if status is not None and status not in TERMINAL_JOB_STATUSES:
            return
        logger.info("Draining completed job %s with status %s", self.active_job_id, status)
        self.supervisor.cancel_job(self.active_job_id)
        self.draining_job_id = self.active_job_id
        self.active_job_id = None

    async def _finish_draining(self) -> None:
        if self.draining_job_id is None:
            return
        if self.idle_gpus == self.supervisor.gpu_uuids:
            logger.info("All GPUs drained from job %s", self.draining_job_id)
            self.draining_job_id = None

    async def _claim_if_ready(self) -> None:
        if self.active_job_id is not None or self.draining_job_id is not None:
            return
        if self.idle_gpus != self.supervisor.gpu_uuids:
            return
        combined_rate = sum(device.benchmark_rate for device in self.devices)
        self.active_job_id = await self.repository.claim_next_job(
            combined_rate=combined_rate,
            timeout_seconds=self.settings.generator_job_timeout_seconds,
        )
        if self.active_job_id is not None:
            logger.info("Claimed FIFO generation job %s for all GPUs", self.active_job_id)

    async def _fill_idle_gpus(self) -> None:
        if self.active_job_id is None or self.draining_job_id is not None:
            return
        for gpu_uuid in sorted(self.idle_gpus):
            assignment = await self.repository.allocate_shard(
                job_id=self.active_job_id,
                gpu_uuid=gpu_uuid,
                shard_seconds=self.settings.generator_shard_seconds,
                kernel_batch_ms=self.settings.generator_kernel_batch_ms,
            )
            if assignment is None:
                continue
            self.supervisor.dispatch(assignment)
            self.idle_gpus.discard(gpu_uuid)
