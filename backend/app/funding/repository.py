import uuid
from datetime import timedelta

from sqlalchemy import case, select

from app.database import SessionLocal
from app.models import FundingStatus, FundingTransaction, utc_now

PROCESSABLE_STATUSES = {
    FundingStatus.REQUESTED,
    FundingStatus.SIGNED,
    FundingStatus.BROADCAST,
    FundingStatus.UNKNOWN,
}


class FundingRepository:
    async def recover_interrupted_work(self) -> None:
        async with SessionLocal() as db:
            interrupted = list(
                (
                    await db.scalars(
                        select(FundingTransaction).where(
                            FundingTransaction.status == FundingStatus.PREPARING
                        )
                    )
                ).all()
            )
            for funding in interrupted:
                funding.status = FundingStatus.REQUESTED
                funding.failure_code = "worker_restarted"
                funding.failure_message = (
                    "Funding preparation will be retried after worker restart."
                )
            await db.commit()

    async def claim_next(self) -> FundingTransaction | None:
        async with SessionLocal() as db:
            priority = case(
                (FundingTransaction.status == FundingStatus.BROADCAST, 0),
                (FundingTransaction.status == FundingStatus.UNKNOWN, 1),
                (FundingTransaction.status == FundingStatus.SIGNED, 2),
                else_=3,
            )
            funding = await db.scalar(
                select(FundingTransaction)
                .where(FundingTransaction.status.in_(PROCESSABLE_STATUSES))
                .order_by(priority, FundingTransaction.updated_at, FundingTransaction.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if funding is None:
                return None
            if funding.status == FundingStatus.REQUESTED:
                funding.status = FundingStatus.PREPARING
                funding.attempt_count += 1
                funding.failure_code = None
                funding.failure_message = None
                await db.commit()
            return funding

    async def mark_prepared(
        self,
        funding_id: uuid.UUID,
        *,
        source_address: str,
        txid: str,
        encrypted_signed_transaction: str,
        transaction_expires_at,
    ) -> None:
        async with SessionLocal() as db:
            funding = await db.get(FundingTransaction, funding_id)
            if funding is None or funding.status != FundingStatus.PREPARING:
                return
            funding.source_address = source_address
            funding.txid = txid
            funding.encrypted_signed_transaction = encrypted_signed_transaction
            funding.transaction_expires_at = transaction_expires_at
            funding.status = FundingStatus.SIGNED
            await db.commit()

    async def retry_preparation(self, funding_id: uuid.UUID, message: str) -> None:
        async with SessionLocal() as db:
            funding = await db.get(FundingTransaction, funding_id)
            if funding is None or funding.status != FundingStatus.PREPARING:
                return
            funding.status = FundingStatus.REQUESTED
            funding.failure_code = "node_unavailable"
            funding.failure_message = message[:1000]
            await db.commit()

    async def mark_broadcast(self, funding_id: uuid.UUID) -> None:
        async with SessionLocal() as db:
            funding = await db.get(FundingTransaction, funding_id)
            if funding is None or funding.status not in {
                FundingStatus.SIGNED,
                FundingStatus.UNKNOWN,
            }:
                return
            funding.status = FundingStatus.BROADCAST
            funding.broadcast_at = funding.broadcast_at or utc_now()
            funding.last_checked_at = utc_now()
            funding.failure_code = None
            funding.failure_message = None
            await db.commit()

    async def mark_unknown(self, funding_id: uuid.UUID, message: str) -> None:
        async with SessionLocal() as db:
            funding = await db.get(FundingTransaction, funding_id)
            if funding is None or funding.status in {
                FundingStatus.CONFIRMED,
                FundingStatus.FAILED,
            }:
                return
            funding.status = FundingStatus.UNKNOWN
            funding.last_checked_at = utc_now()
            funding.failure_code = "broadcast_outcome_unknown"
            funding.failure_message = message[:1000]
            await db.commit()

    async def mark_checked(
        self,
        funding_id: uuid.UUID,
        *,
        confirmation_timeout_seconds: int,
    ) -> None:
        async with SessionLocal() as db:
            funding = await db.get(FundingTransaction, funding_id)
            if funding is None:
                return
            now = utc_now()
            funding.last_checked_at = now
            if (
                funding.status == FundingStatus.BROADCAST
                and funding.broadcast_at is not None
                and now - funding.broadcast_at > timedelta(seconds=confirmation_timeout_seconds)
            ):
                funding.status = FundingStatus.UNKNOWN
                funding.failure_code = "confirmation_delayed"
                funding.failure_message = (
                    "No solidified receipt was found before the confirmation timeout. "
                    "The original transaction will continue to be reconciled."
                )
            await db.commit()

    async def mark_confirmed(self, funding_id: uuid.UUID) -> None:
        async with SessionLocal() as db:
            funding = await db.get(FundingTransaction, funding_id)
            if funding is None:
                return
            funding.status = FundingStatus.CONFIRMED
            funding.confirmed_at = utc_now()
            funding.last_checked_at = funding.confirmed_at
            funding.failure_code = None
            funding.failure_message = None
            funding.encrypted_signed_transaction = None
            await db.commit()

    async def mark_failed(self, funding_id: uuid.UUID, code: str, message: str) -> None:
        async with SessionLocal() as db:
            funding = await db.get(FundingTransaction, funding_id)
            if funding is None or funding.status == FundingStatus.CONFIRMED:
                return
            funding.status = FundingStatus.FAILED
            funding.failure_code = code[:64]
            funding.failure_message = message[:1000]
            funding.last_checked_at = utc_now()
            funding.encrypted_signed_transaction = None
            await db.commit()
