from decimal import Decimal

from app.models import FundingTransaction
from app.schemas import FundingRead


def format_micro_usdt(value: int) -> str:
    amount = format(Decimal(value) / Decimal(1_000_000), "f")
    return amount.rstrip("0").rstrip(".") if "." in amount else amount


def transaction_explorer_url(network: str, txid: str | None) -> str | None:
    if txid is None:
        return None
    hosts = {
        "mainnet": "https://tronscan.org",
        "nile": "https://nile.tronscan.org",
        "shasta": "https://shasta.tronscan.org",
    }
    host = hosts.get(network)
    return f"{host}/#/transaction/{txid}" if host else None


def serialize_funding(funding: FundingTransaction) -> FundingRead:
    return FundingRead(
        id=funding.id,
        job_id=funding.job_id,
        source_address=funding.source_address,
        destination_address=funding.destination_address,
        amount_usdt=format_micro_usdt(funding.amount_micro_usdt),
        network=funding.network,
        contract_address=funding.contract_address,
        status=funding.status,
        txid=funding.txid,
        explorer_url=transaction_explorer_url(funding.network, funding.txid),
        failure_code=funding.failure_code,
        failure_message=funding.failure_message,
        created_at=funding.created_at,
        updated_at=funding.updated_at,
        broadcast_at=funding.broadcast_at,
        confirmed_at=funding.confirmed_at,
    )
