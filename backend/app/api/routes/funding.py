import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Header, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, DatabaseSession
from app.config import get_settings
from app.models import FundingStatus, FundingTransaction, GenerationJob, JobStatus, User
from app.schemas import FundingConfigRead, FundingCreate, FundingRead
from app.services.funding import serialize_funding
from app.services.jobs import get_owned_job

router = APIRouter(tags=["wallet funding"])
settings = get_settings()
IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
RESERVED_DAILY_STATUSES = {
    FundingStatus.REQUESTED,
    FundingStatus.PREPARING,
    FundingStatus.SIGNED,
    FundingStatus.BROADCAST,
    FundingStatus.CONFIRMED,
    FundingStatus.UNKNOWN,
}


@router.get("/funding/config", response_model=FundingConfigRead)
async def funding_config(_user: CurrentUser) -> FundingConfigRead:
    return FundingConfigRead(
        enabled=settings.funding_mode != "disabled",
        mode=settings.funding_mode,
        network=settings.funding_network,
        contract_address=settings.funding_contract_address,
    )


@router.post(
    "/jobs/{job_id}/funding",
    response_model=FundingRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_funding(
    job_id: uuid.UUID,
    payload: FundingCreate,
    db: DatabaseSession,
    user: CurrentUser,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> FundingRead:
    if settings.funding_mode == "disabled":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Wallet funding is disabled on this server.",
        )
    if idempotency_key is None or not IDEMPOTENCY_KEY.fullmatch(idempotency_key):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A 1-128 character safe ASCII Idempotency-Key is required.",
        )

    # Serialize this operator's funding reservations so concurrent requests cannot race the
    # configured daily limit. The unique job constraint independently prevents double funding.
    locked_user = await db.scalar(select(User).where(User.id == user.id).with_for_update())
    if locked_user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User is unavailable.")

    job = await get_owned_job(db, job_id=job_id, user=user)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Generation job not found."
        )
    if job.status not in {JobStatus.READY, JobStatus.OWNERSHIP_VERIFIED} or job.result is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only a verified, completed wallet can be funded.",
        )

    existing = await db.scalar(
        select(FundingTransaction).where(FundingTransaction.job_id == job.id)
    )
    if existing is not None:
        if existing.amount_micro_usdt != payload.amount_micro_usdt:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This wallet already has a funding request for a different amount.",
            )
        return serialize_funding(existing)

    day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    reserved_today = int(
        await db.scalar(
            select(func.coalesce(func.sum(FundingTransaction.amount_micro_usdt), 0)).where(
                FundingTransaction.user_id == user.id,
                FundingTransaction.created_at >= day_start,
                FundingTransaction.status.in_(RESERVED_DAILY_STATUSES),
            )
        )
        or 0
    )
    daily_limit = int(Decimal(settings.funding_daily_limit_usdt) * 1_000_000)
    if reserved_today + payload.amount_micro_usdt > daily_limit:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This funding request would exceed the configured daily USDT limit.",
        )

    funding = FundingTransaction(
        user_id=user.id,
        job_id=job.id,
        idempotency_key=idempotency_key,
        destination_address=job.result.address,
        amount_micro_usdt=payload.amount_micro_usdt,
        network=settings.funding_network,
        contract_address=settings.funding_contract_address,
        status=FundingStatus.REQUESTED,
    )
    db.add(funding)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        existing = await db.scalar(
            select(FundingTransaction).where(FundingTransaction.job_id == job.id)
        )
        if existing is not None and existing.amount_micro_usdt == payload.amount_micro_usdt:
            return serialize_funding(existing)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A funding request already exists.",
        ) from exc
    await db.refresh(funding)
    return serialize_funding(funding)


@router.get("/jobs/{job_id}/funding", response_model=FundingRead)
async def get_funding(
    job_id: uuid.UUID, db: DatabaseSession, user: CurrentUser
) -> FundingRead:
    owned_job = await db.scalar(
        select(GenerationJob.id).where(
            GenerationJob.id == job_id,
            GenerationJob.user_id == user.id,
        )
    )
    if owned_job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Generation job not found."
        )
    funding = await db.scalar(
        select(FundingTransaction).where(FundingTransaction.job_id == job_id)
    )
    if funding is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Funding request not found."
        )
    return serialize_funding(funding)
