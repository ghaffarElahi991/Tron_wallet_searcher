import asyncio
import logging
from datetime import UTC, datetime

from app.config import Settings
from app.core.security import get_result_secret_box
from app.funding.gateway import (
    FundingGatewayError,
    FundingNetworkError,
    FundingRejected,
    ReceiptState,
    create_gateway,
)
from app.funding.repository import FundingRepository
from app.models import FundingStatus, FundingTransaction

logger = logging.getLogger(__name__)


class FundingProcessor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.repository = FundingRepository()
        self.gateway = create_gateway(settings)

    async def run(self, stop_event: asyncio.Event) -> None:
        await self.repository.recover_interrupted_work()
        logger.info(
            "Funding processor started in %s mode on %s.",
            self.settings.funding_mode,
            self.settings.funding_network,
        )
        try:
            while not stop_event.is_set():
                funding = await self.repository.claim_next()
                if funding is None:
                    await self._wait(stop_event)
                    continue
                try:
                    await self._process(funding)
                except Exception:
                    logger.exception("Unexpected funding processor failure for %s", funding.id)
                    await self.repository.mark_unknown(
                        funding.id,
                        "An internal funding worker error occurred; reconciliation will continue.",
                    )
                await self._wait(stop_event)
        finally:
            await self.gateway.close()

    async def _wait(self, stop_event: asyncio.Event) -> None:
        try:
            await asyncio.wait_for(
                stop_event.wait(), timeout=self.settings.funding_poll_interval_seconds
            )
        except TimeoutError:
            pass

    async def _process(self, funding: FundingTransaction) -> None:
        if funding.status == FundingStatus.PREPARING:
            await self._prepare(funding)
        elif funding.status == FundingStatus.SIGNED:
            await self._broadcast(funding)
        elif funding.status in {FundingStatus.BROADCAST, FundingStatus.UNKNOWN}:
            await self._confirm(funding)

    async def _prepare(self, funding: FundingTransaction) -> None:
        try:
            prepared = await self.gateway.prepare_transfer(
                funding_id=str(funding.id),
                destination=funding.destination_address,
                amount_micro_usdt=funding.amount_micro_usdt,
                contract_address=funding.contract_address,
            )
        except FundingNetworkError as exc:
            await self.repository.retry_preparation(funding.id, str(exc))
            return
        except FundingRejected as exc:
            await self.repository.mark_failed(funding.id, "transfer_rejected", str(exc))
            return
        encrypted = get_result_secret_box().encrypt(prepared.signed_payload)
        await self.repository.mark_prepared(
            funding.id,
            source_address=prepared.source_address,
            txid=prepared.txid,
            encrypted_signed_transaction=encrypted,
            transaction_expires_at=prepared.expires_at,
        )

    async def _broadcast(self, funding: FundingTransaction) -> None:
        if funding.txid is None or funding.encrypted_signed_transaction is None:
            await self.repository.mark_failed(
                funding.id,
                "signed_transaction_missing",
                "The persisted signed transaction is incomplete.",
            )
            return
        try:
            payload = get_result_secret_box().decrypt(funding.encrypted_signed_transaction)
            await self.gateway.broadcast(payload, funding.txid)
        except FundingNetworkError as exc:
            await self.repository.mark_unknown(funding.id, str(exc))
            return
        except FundingGatewayError as exc:
            if funding.status == FundingStatus.UNKNOWN:
                await self.repository.mark_unknown(funding.id, str(exc))
            else:
                await self.repository.mark_failed(funding.id, "broadcast_rejected", str(exc))
            return
        await self.repository.mark_broadcast(funding.id)

    async def _confirm(self, funding: FundingTransaction) -> None:
        if funding.txid is None:
            await self.repository.mark_failed(
                funding.id, "transaction_id_missing", "Funding transaction ID is missing."
            )
            return
        try:
            receipt = await self.gateway.receipt(
                txid=funding.txid,
                contract_address=funding.contract_address,
                destination=funding.destination_address,
                amount_micro_usdt=funding.amount_micro_usdt,
            )
        except FundingNetworkError as exc:
            await self.repository.mark_unknown(funding.id, str(exc))
            return
        if receipt.state == ReceiptState.CONFIRMED:
            await self.repository.mark_confirmed(funding.id)
            return
        if receipt.state == ReceiptState.UNKNOWN:
            await self.repository.mark_unknown(
                funding.id,
                receipt.message
                or "The successful receipt could not be matched to the expected transfer.",
            )
            return
        if receipt.state == ReceiptState.FAILED:
            await self.repository.mark_failed(
                funding.id,
                "execution_failed",
                receipt.message or "The TRC-20 transfer execution failed.",
            )
            return
        if (
            funding.status == FundingStatus.UNKNOWN
            and funding.transaction_expires_at is not None
            and _aware(funding.transaction_expires_at) > datetime.now(UTC)
            and funding.encrypted_signed_transaction is not None
        ):
            await self._broadcast(funding)
            return
        await self.repository.mark_checked(
            funding.id,
            confirmation_timeout_seconds=self.settings.funding_confirmation_timeout_seconds,
        )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
