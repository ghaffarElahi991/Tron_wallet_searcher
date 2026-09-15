import re
import uuid

from fastapi import APIRouter, Header, HTTPException, Query, status
from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentUser, DatabaseSession
from app.core.security import get_result_secret_box
from app.models import GenerationJob, JobStatus, utc_now
from app.schemas import GenerationJobCreate, GenerationJobRead, JobListResponse
from app.services import server_wallet
from app.services.jobs import count_owned_jobs, get_owned_job, serialize_job
from app.services.patterns import compile_match_spec

router = APIRouter(prefix="/jobs", tags=["generation jobs"])
IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
TERMINAL_STATUSES = {
    JobStatus.READY,
    JobStatus.OWNERSHIP_VERIFIED,
    JobStatus.FAILED,
    JobStatus.CANCELED,
    JobStatus.TIMED_OUT,
}


@router.post("", response_model=GenerationJobRead, status_code=status.HTTP_201_CREATED)
async def create_job(
    payload: GenerationJobCreate,
    db: DatabaseSession,
    user: CurrentUser,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> GenerationJobRead:
    if idempotency_key is not None and not IDEMPOTENCY_KEY.fullmatch(idempotency_key):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Idempotency-Key must be 1-128 safe ASCII characters.",
        )
    if idempotency_key is not None:
        existing = await db.scalar(
            select(GenerationJob)
            .where(
                GenerationJob.user_id == user.id,
                GenerationJob.idempotency_key == idempotency_key,
            )
            .options(selectinload(GenerationJob.result))
        )
        if existing is not None:
            return serialize_job(existing)

    base_private_key, public_key = server_wallet.create_server_key_share()
    job = GenerationJob(
        user_id=user.id,
        idempotency_key=idempotency_key,
        pattern_type=payload.pattern,
        prefix=payload.prefix,
        suffix=payload.suffix,
        client_public_key=public_key,
        encrypted_base_private_key=get_result_secret_box().encrypt(base_private_key),
        match_spec=compile_match_spec(payload.prefix, payload.suffix),
        status=JobStatus.QUEUED,
    )
    db.add(job)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if idempotency_key is not None:
            existing = await db.scalar(
                select(GenerationJob)
                .where(
                    GenerationJob.user_id == user.id,
                    GenerationJob.idempotency_key == idempotency_key,
                )
                .options(selectinload(GenerationJob.result))
            )
            if existing is not None:
                return serialize_job(existing)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Generation job already exists."
        ) from exc
    created = await get_owned_job(db, job_id=job.id, user=user)
    if created is None:  # Defensive: the committed row must be readable by its owner.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Generation job was created but could not be loaded.",
        )
    return serialize_job(created)


@router.get("", response_model=JobListResponse)
async def list_jobs(
    db: DatabaseSession,
    user: CurrentUser,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> JobListResponse:
    statement = (
        select(GenerationJob)
        .where(GenerationJob.user_id == user.id)
        .options(selectinload(GenerationJob.result))
        .order_by(desc(GenerationJob.created_at))
        .limit(limit)
        .offset(offset)
    )
    jobs = list((await db.scalars(statement)).all())
    return JobListResponse(
        items=[serialize_job(job) for job in jobs],
        total=await count_owned_jobs(db, user),
        limit=limit,
        offset=offset,
    )


@router.get("/{job_id}", response_model=GenerationJobRead)
async def get_job(job_id: uuid.UUID, db: DatabaseSession, user: CurrentUser) -> GenerationJobRead:
    job = await get_owned_job(db, job_id=job_id, user=user)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Generation job not found."
        )
    return serialize_job(job)


@router.post("/{job_id}/cancel", response_model=GenerationJobRead)
async def cancel_job(
    job_id: uuid.UUID, db: DatabaseSession, user: CurrentUser
) -> GenerationJobRead:
    job = await get_owned_job(db, job_id=job_id, user=user)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Generation job not found."
        )
    if job.status == JobStatus.CANCELED:
        return serialize_job(job)
    if job.status in TERMINAL_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A {job.status.value} job cannot be canceled.",
        )
    job.status = JobStatus.CANCELED
    job.completed_at = utc_now()
    await db.commit()
    canceled = await get_owned_job(db, job_id=job.id, user=user)
    if canceled is None:  # Defensive: the committed row must be readable by its owner.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Generation job was canceled but could not be loaded.",
        )
    return serialize_job(canceled)
