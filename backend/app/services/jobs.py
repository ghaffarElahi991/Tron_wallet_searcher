from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import get_result_secret_box
from app.models import GenerationJob, User
from app.schemas import GenerationJobRead, GenerationResultRead


async def get_owned_job(db: AsyncSession, *, job_id: object, user: User) -> GenerationJob | None:
    statement = (
        select(GenerationJob)
        .where(GenerationJob.id == job_id, GenerationJob.user_id == user.id)
        .options(selectinload(GenerationJob.result))
    )
    return await db.scalar(statement)


def observed_search_rate(job: GenerationJob, now: datetime | None = None) -> int | None:
    if job.attempts <= 0 or job.started_at is None:
        return None
    started = job.started_at
    ended = job.completed_at or now or datetime.now(UTC)
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    if ended.tzinfo is None:
        ended = ended.replace(tzinfo=UTC)
    elapsed = (ended - started).total_seconds()
    if elapsed <= 0:
        return None
    return max(1, int(job.attempts / elapsed))


def serialize_job(job: GenerationJob) -> GenerationJobRead:
    result = None
    if job.result is not None and job.result.encrypted_private_key is not None:
        result = GenerationResultRead(
            address=job.result.address,
            private_key=get_result_secret_box().decrypt(job.result.encrypted_private_key),
            verified_at=job.result.verified_at or job.result.created_at,
        )
    return GenerationJobRead(
        id=job.id,
        pattern=job.pattern_type,
        prefix=job.prefix,
        suffix=job.suffix,
        status=job.status,
        attempts=job.attempts,
        search_rate=job.search_rate,
        observed_rate=observed_search_rate(job),
        created_at=job.created_at,
        updated_at=job.updated_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        failure_code=job.failure_code,
        failure_message=job.failure_message,
        result=result,
    )


async def count_owned_jobs(db: AsyncSession, user: User) -> int:
    statement = (
        select(func.count()).select_from(GenerationJob).where(GenerationJob.user_id == user.id)
    )
    return int(await db.scalar(statement) or 0)
