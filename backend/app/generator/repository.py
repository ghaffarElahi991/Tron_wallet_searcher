import secrets
import uuid
from datetime import timedelta

from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import SessionLocal
from app.generator.protocol import GpuInfo, WorkAssignment, WorkerEvent, WorkerEventType
from app.models import (
    GenerationJob,
    GenerationShard,
    GpuDevice,
    GpuStatus,
    JobStatus,
    ShardStatus,
    utc_now,
)
from app.services.patterns import SECP256K1_ORDER
from app.services.results import (
    CandidateJobNotFound,
    CandidateStateConflict,
    CandidateVerificationError,
    finalize_candidate,
)

ACTIVE_JOB_STATUSES = {JobStatus.SEARCHING, JobStatus.VERIFYING}
ACTIVE_SHARD_STATUSES = {ShardStatus.ASSIGNED, ShardStatus.RUNNING}


def scalar_hex(value: int) -> str:
    return value.to_bytes(32, "big").hex()


class SchedulerRepository:
    async def initialize(self, devices: list[GpuInfo]) -> None:
        async with SessionLocal() as db:
            await db.execute(
                update(GpuDevice).values(
                    status=GpuStatus.OFFLINE,
                    utilization_percent=0,
                    current_job_id=None,
                    worker_pid=None,
                )
            )
            for info in devices:
                device = await db.get(GpuDevice, info.uuid)
                if device is None:
                    device = GpuDevice(
                        uuid=info.uuid,
                        device_index=info.device_index,
                        name=info.name,
                        memory_total_mb=info.memory_total_mb,
                        kernel_version=info.kernel_version,
                    )
                    db.add(device)
                device.device_index = info.device_index
                device.name = info.name
                device.memory_total_mb = info.memory_total_mb
                device.benchmark_rate = info.benchmark_rate
                device.kernel_version = info.kernel_version
                device.status = GpuStatus.STARTING
                device.last_error = None
            await db.commit()

    async def recover_interrupted_work(self) -> None:
        async with SessionLocal() as db:
            shards = list(
                (
                    await db.scalars(
                        select(GenerationShard).where(
                            GenerationShard.status.in_(ACTIVE_SHARD_STATUSES)
                        )
                    )
                ).all()
            )
            for shard in shards:
                shard.status = ShardStatus.FAILED
                shard.completed_at = utc_now()
                shard.failure_message = "Scheduler restarted while this shard was active."

            jobs = list(
                (
                    await db.scalars(
                        select(GenerationJob).where(GenerationJob.status.in_(ACTIVE_JOB_STATUSES))
                    )
                ).all()
            )
            for job in jobs:
                job.status = JobStatus.QUEUED
                job.next_offset = job.search_origin
                job.started_at = None
                job.deadline_at = None
                job.search_rate = 0
            await db.commit()

    async def claim_next_job(self, combined_rate: int, timeout_seconds: int) -> uuid.UUID | None:
        async with SessionLocal() as db:
            existing = await db.scalar(
                select(GenerationJob).where(GenerationJob.status.in_(ACTIVE_JOB_STATUSES)).limit(1)
            )
            if existing is not None:
                return existing.id

            job = await db.scalar(
                select(GenerationJob)
                .where(GenerationJob.status == JobStatus.QUEUED)
                .order_by(desc(GenerationJob.priority), GenerationJob.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if job is None:
                return None

            now = utc_now()
            if job.search_origin is None:
                job.search_origin = scalar_hex(secrets.randbelow(SECP256K1_ORDER - 1) + 1)
            job.next_offset = job.search_origin
            job.status = JobStatus.SEARCHING
            job.started_at = now
            job.deadline_at = now + timedelta(seconds=timeout_seconds)
            job.completed_at = None
            job.search_rate = combined_rate
            await db.commit()
            return job.id

    async def allocate_shard(
        self,
        *,
        job_id: uuid.UUID,
        gpu_uuid: str,
        shard_seconds: int,
        kernel_batch_ms: int,
    ) -> WorkAssignment | None:
        async with SessionLocal() as db:
            job = await db.scalar(
                select(GenerationJob).where(GenerationJob.id == job_id).with_for_update()
            )
            device = await db.scalar(
                select(GpuDevice).where(GpuDevice.uuid == gpu_uuid).with_for_update()
            )
            if (
                job is None
                or device is None
                or job.status != JobStatus.SEARCHING
                or device.status != GpuStatus.READY
                or not job.next_offset
            ):
                return None

            rate = max(1, device.benchmark_rate)
            range_count = max(1, rate * shard_seconds)
            range_start = int(job.next_offset, 16)
            if range_start + range_count >= SECP256K1_ORDER:
                job.status = JobStatus.FAILED
                job.failure_code = "search_space_exhausted"
                job.failure_message = "The allocated secp256k1 search range was exhausted."
                job.completed_at = utc_now()
                await db.commit()
                return None

            shard = GenerationShard(
                job_id=job.id,
                gpu_uuid=device.uuid,
                range_start=scalar_hex(range_start),
                range_count=range_count,
                status=ShardStatus.ASSIGNED,
            )
            db.add(shard)
            job.next_offset = scalar_hex(range_start + range_count)
            device.status = GpuStatus.SEARCHING
            device.utilization_percent = 100
            device.current_job_id = job.id
            device.last_heartbeat = utc_now()
            await db.flush()
            assignment = WorkAssignment(
                job_id=job.id,
                shard_id=shard.id,
                lease_token=shard.lease_token,
                gpu_uuid=device.uuid,
                client_public_key=job.client_public_key,
                pattern=job.pattern_type.value,
                prefix=job.prefix,
                suffix=job.suffix,
                range_start=range_start,
                range_count=range_count,
                kernel_batch_size=max(1, rate * kernel_batch_ms // 1000),
                simulated_rate=rate,
            )
            await db.commit()
            return assignment

    async def record_event(self, event: WorkerEvent) -> None:
        async with SessionLocal() as db:
            device = await db.get(GpuDevice, event.gpu_uuid)
            if device is None:
                return
            device.worker_pid = event.worker_pid or None
            device.last_heartbeat = utc_now()
            if event.observed_rate > 0:
                current_rate = max(0, device.benchmark_rate)
                device.benchmark_rate = (
                    event.observed_rate
                    if current_rate == 0
                    else max(1, (current_rate * 3 + event.observed_rate) // 4)
                )

            if event.event_type == WorkerEventType.STARTING:
                device.status = GpuStatus.STARTING
            elif event.event_type == WorkerEventType.SELF_TESTING:
                device.status = GpuStatus.SELF_TESTING
            elif event.event_type == WorkerEventType.READY:
                device.status = GpuStatus.READY
                device.utilization_percent = 0
                device.current_job_id = None
                device.last_error = None
            elif event.event_type == WorkerEventType.STOPPED:
                device.status = GpuStatus.OFFLINE
                device.utilization_percent = 0
                device.current_job_id = None
            elif event.event_type == WorkerEventType.FAILED:
                device.status = GpuStatus.UNHEALTHY
                device.utilization_percent = 0
                device.current_job_id = None
                device.last_error = event.error

            shard = await self._matching_shard(db, event)
            if shard is not None:
                if event.event_type == WorkerEventType.STARTED:
                    shard.status = ShardStatus.RUNNING
                    shard.started_at = utc_now()
                elif event.event_type == WorkerEventType.PROGRESS:
                    previous = shard.processed
                    shard.processed = max(previous, event.processed_total)
                    job = await db.get(GenerationJob, shard.job_id)
                    if job is not None:
                        job.attempts += shard.processed - previous
                elif event.event_type == WorkerEventType.EXHAUSTED:
                    shard.status = ShardStatus.EXHAUSTED
                    shard.processed = max(shard.processed, event.processed_total)
                    shard.completed_at = utc_now()
                elif event.event_type == WorkerEventType.FOUND:
                    shard.status = ShardStatus.FOUND
                    previous = shard.processed
                    shard.processed = max(previous, event.processed_total)
                    shard.completed_at = utc_now()
                    job = await db.get(GenerationJob, shard.job_id)
                    if job is not None:
                        job.attempts += shard.processed - previous
                    if event.address is None or event.offset is None:
                        shard.status = ShardStatus.FAILED
                        shard.failure_message = (
                            "CUDA winner event is missing its address or offset."
                        )
                        device.status = GpuStatus.UNHEALTHY
                        device.last_error = shard.failure_message
                        if job is not None:
                            job.status = JobStatus.FAILED
                            job.failure_code = "candidate_verification_failed"
                            job.failure_message = shard.failure_message
                            job.completed_at = utc_now()
                    else:
                        try:
                            await finalize_candidate(
                                db,
                                job_id=shard.job_id,
                                worker_id=f"{event.gpu_uuid}:{event.worker_pid}",
                                address=event.address,
                                offset=event.offset,
                            )
                        except CandidateStateConflict:
                            shard.status = ShardStatus.CANCELED
                        except (CandidateJobNotFound, CandidateVerificationError) as exc:
                            shard.status = ShardStatus.FAILED
                            shard.failure_message = str(exc)
                            device.status = GpuStatus.UNHEALTHY
                            device.last_error = str(exc)
                            if job is not None:
                                job.status = JobStatus.FAILED
                                job.failure_code = "candidate_verification_failed"
                                job.failure_message = str(exc)
                                job.completed_at = utc_now()
                elif event.event_type == WorkerEventType.CANCELED:
                    shard.status = ShardStatus.CANCELED
                    shard.processed = max(shard.processed, event.processed_total)
                    shard.completed_at = utc_now()
                elif event.event_type == WorkerEventType.FAILED:
                    shard.status = ShardStatus.FAILED
                    shard.completed_at = utc_now()
                    shard.failure_message = event.error
            await db.commit()

    async def _matching_shard(self, db: AsyncSession, event: WorkerEvent) -> GenerationShard | None:
        if event.shard_id is None or event.lease_token is None:
            return None
        shard = await db.get(GenerationShard, event.shard_id)
        if shard is None or shard.lease_token != event.lease_token:
            return None
        return shard

    async def active_job_status(self, job_id: uuid.UUID) -> JobStatus | None:
        async with SessionLocal() as db:
            job = await db.get(GenerationJob, job_id)
            if job is None:
                return None
            if (
                job.status == JobStatus.SEARCHING
                and job.deadline_at is not None
                and job.deadline_at <= utc_now()
            ):
                job.status = JobStatus.TIMED_OUT
                job.completed_at = utc_now()
                await db.commit()
            return job.status

    async def mark_devices_offline(self) -> None:
        async with SessionLocal() as db:
            await db.execute(
                update(GpuDevice).values(
                    status=GpuStatus.OFFLINE,
                    utilization_percent=0,
                    current_job_id=None,
                    worker_pid=None,
                )
            )
            await db.commit()
