import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError
from tronpy.keys import to_hex_address

from app.api.routes import funding as funding_routes
from app.config import MAINNET_USDT_CONTRACT, Settings, get_settings
from app.funding.gateway import (
    TRANSFER_EVENT_TOPIC,
    ReceiptResult,
    ReceiptState,
    SimulatorFundingGateway,
    TronFundingGateway,
    _transfer_event_matches,
)
from app.funding.processor import FundingProcessor
from app.models import FundingStatus
from app.services import server_wallet
from tests.helpers import deterministic_server_key_share, matching_candidate, valid_job_payload

WORKER_HEADERS = {
    "X-Worker-API-Key": get_settings().worker_api_key.get_secret_value(),
}
pytestmark = pytest.mark.anyio


async def create_ready_wallet(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[str, str]:
    monkeypatch.setattr(server_wallet, "create_server_key_share", deterministic_server_key_share)
    created = await client.post("/api/v1/jobs", json=valid_job_payload(), headers=auth_headers)
    job_id = created.json()["id"]
    address, offset = matching_candidate()
    result = await client.post(
        f"/api/v1/internal/results/{job_id}",
        json={"worker_id": "funding-test", "address": address, "offset": offset},
        headers=WORKER_HEADERS,
    )
    assert result.status_code == 200
    return job_id, address


async def test_funding_is_disabled_by_default(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    response = await client.post(
        "/api/v1/jobs/00000000-0000-0000-0000-000000000001/funding",
        json={"amount": "1"},
        headers={**auth_headers, "Idempotency-Key": "disabled-test"},
    )
    assert response.status_code == 503


async def test_create_and_read_idempotent_funding_request(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(funding_routes.settings, "funding_mode", "simulator")
    job_id, address = await create_ready_wallet(client, auth_headers, monkeypatch)
    headers = {**auth_headers, "Idempotency-Key": "fund-wallet-001"}

    created = await client.post(
        f"/api/v1/jobs/{job_id}/funding",
        json={"amount": "250.123456"},
        headers=headers,
    )
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "requested"
    assert body["amount_usdt"] == "250.123456"
    assert body["destination_address"] == address
    assert body["txid"] is None

    duplicate = await client.post(
        f"/api/v1/jobs/{job_id}/funding",
        json={"amount": "250.123456"},
        headers={**auth_headers, "Idempotency-Key": "a-different-retry-key"},
    )
    assert duplicate.status_code == 201
    assert duplicate.json()["id"] == body["id"]

    conflicting = await client.post(
        f"/api/v1/jobs/{job_id}/funding",
        json={"amount": "251"},
        headers=headers,
    )
    assert conflicting.status_code == 409

    fetched = await client.get(f"/api/v1/jobs/{job_id}/funding", headers=auth_headers)
    assert fetched.status_code == 200
    assert fetched.json()["id"] == body["id"]


@pytest.mark.parametrize("amount", ["0.999999", "1500.000001", "1.0000001", "NaN"])
async def test_funding_amount_guardrails(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    amount: str,
) -> None:
    monkeypatch.setattr(funding_routes.settings, "funding_mode", "simulator")
    job_id, _address = await create_ready_wallet(client, auth_headers, monkeypatch)
    response = await client.post(
        f"/api/v1/jobs/{job_id}/funding",
        json={"amount": amount},
        headers={**auth_headers, "Idempotency-Key": "invalid-amount"},
    )
    assert response.status_code == 422


async def test_simulator_gateway_is_deterministic_and_confirms() -> None:
    gateway = SimulatorFundingGateway()
    prepared = await gateway.prepare_transfer(
        funding_id="funding-1",
        destination="TQriju7D5yYGFMiwnTEyKJ3eQ4eGupnY99",
        amount_micro_usdt=1_000_000,
        contract_address=MAINNET_USDT_CONTRACT,
    )
    assert len(prepared.txid) == 64
    assert prepared.expires_at > datetime.now(UTC)
    await gateway.broadcast(prepared.signed_payload, prepared.txid)
    receipt = await gateway.receipt(
        txid=prepared.txid,
        contract_address=MAINNET_USDT_CONTRACT,
        destination="TQriju7D5yYGFMiwnTEyKJ3eQ4eGupnY99",
        amount_micro_usdt=1_000_000,
    )
    assert receipt.state == ReceiptState.CONFIRMED


async def test_processor_persists_before_broadcast_and_then_confirms() -> None:
    class FakeRepository:
        def __init__(self) -> None:
            self.prepared: dict | None = None
            self.broadcast_id = None
            self.confirmed_id = None

        async def mark_prepared(self, funding_id, **values) -> None:
            self.prepared = {"funding_id": funding_id, **values}

        async def mark_broadcast(self, funding_id) -> None:
            self.broadcast_id = funding_id

        async def mark_confirmed(self, funding_id) -> None:
            self.confirmed_id = funding_id

        async def mark_failed(self, *_args, **_kwargs) -> None:
            raise AssertionError("Simulator funding should not fail")

        async def mark_unknown(self, *_args, **_kwargs) -> None:
            raise AssertionError("Simulator funding should not become unknown")

        async def retry_preparation(self, *_args, **_kwargs) -> None:
            raise AssertionError("Simulator funding should not retry")

    settings = Settings(_env_file=None, funding_mode="simulator")
    processor = FundingProcessor(settings)
    repository = FakeRepository()
    processor.repository = repository  # type: ignore[assignment]
    funding_id = uuid.uuid4()
    common = {
        "id": funding_id,
        "destination_address": "TQriju7D5yYGFMiwnTEyKJ3eQ4eGupnY99",
        "amount_micro_usdt": 1_000_000,
        "contract_address": MAINNET_USDT_CONTRACT,
        "transaction_expires_at": None,
    }

    await processor._process(SimpleNamespace(status=FundingStatus.PREPARING, **common))
    assert repository.prepared is not None
    assert repository.prepared["txid"]
    assert repository.broadcast_id is None

    signed = SimpleNamespace(
        status=FundingStatus.SIGNED,
        txid=repository.prepared["txid"],
        encrypted_signed_transaction=repository.prepared["encrypted_signed_transaction"],
        **common,
    )
    await processor._process(signed)
    assert repository.broadcast_id == funding_id
    assert repository.confirmed_id is None

    await processor._process(
        SimpleNamespace(
            status=FundingStatus.BROADCAST,
            txid=signed.txid,
            encrypted_signed_transaction=signed.encrypted_signed_transaction,
            **common,
        )
    )
    assert repository.confirmed_id == funding_id
    await processor.gateway.close()


async def test_processor_keeps_unverified_successful_receipt_in_reconciliation() -> None:
    class UnknownGateway:
        async def receipt(self, **_kwargs) -> ReceiptResult:
            return ReceiptResult(ReceiptState.UNKNOWN, "Receipt event needs reconciliation.")

    class FakeRepository:
        def __init__(self) -> None:
            self.unknown: tuple[uuid.UUID, str] | None = None

        async def mark_unknown(self, funding_id: uuid.UUID, message: str) -> None:
            self.unknown = funding_id, message

        async def mark_confirmed(self, *_args, **_kwargs) -> None:
            raise AssertionError("Unverified receipt must not be confirmed")

        async def mark_failed(self, *_args, **_kwargs) -> None:
            raise AssertionError("Unverified successful receipt must not be marked failed")

        async def mark_checked(self, *_args, **_kwargs) -> None:
            raise AssertionError("Unknown receipt should be persisted explicitly")

    settings = Settings(_env_file=None, funding_mode="simulator")
    processor = FundingProcessor(settings)
    repository = FakeRepository()
    processor.repository = repository  # type: ignore[assignment]
    await processor.gateway.close()
    processor.gateway = UnknownGateway()  # type: ignore[assignment]
    funding_id = uuid.uuid4()

    await processor._confirm(
        SimpleNamespace(
            id=funding_id,
            status=FundingStatus.BROADCAST,
            txid="ab" * 32,
            contract_address=MAINNET_USDT_CONTRACT,
            destination_address="TJDPwALmYf7W4XbWPyNSvkyDQ76p7wQYnN",
            amount_micro_usdt=4_000_000,
            transaction_expires_at=None,
            encrypted_signed_transaction=None,
        )
    )

    assert repository.unknown == (funding_id, "Receipt event needs reconciliation.")


def test_expected_transfer_event_must_match_contract_destination_and_amount() -> None:
    destination = "TQriju7D5yYGFMiwnTEyKJ3eQ4eGupnY99"
    contract_hex = to_hex_address(MAINNET_USDT_CONTRACT)[2:]
    destination_topic = to_hex_address(destination)[2:].rjust(64, "0")
    receipt = {
        "log": [
            {
                "address": contract_hex,
                "topics": [TRANSFER_EVENT_TOPIC, "0" * 64, destination_topic],
                "data": f"{1_000_000:064x}",
            }
        ]
    }
    assert _transfer_event_matches(
        receipt,
        contract_address=MAINNET_USDT_CONTRACT,
        destination=destination,
        amount=1_000_000,
    )
    assert not _transfer_event_matches(
        receipt,
        contract_address=MAINNET_USDT_CONTRACT,
        destination=destination,
        amount=2_000_000,
    )


def test_visible_trongrid_receipt_accepts_base58_contract_address() -> None:
    destination = "TJDPwALmYf7W4XbWPyNSvkyDQ76p7wQYnN"
    destination_topic = to_hex_address(destination)[2:].rjust(64, "0")
    receipt = {
        "receipt": {"result": "SUCCESS"},
        "log": [
            {
                "address": MAINNET_USDT_CONTRACT,
                "topics": [TRANSFER_EVENT_TOPIC, "0" * 64, destination_topic],
                "data": f"{4_000_000:064x}",
            }
        ],
    }

    assert _transfer_event_matches(
        receipt,
        contract_address=MAINNET_USDT_CONTRACT,
        destination=destination,
        amount=4_000_000,
    )


def test_public_telegram_uses_default_shared_master_wallet_funding() -> None:
    settings = Settings(
        _env_file=None,
        telegram_public_access=True,
        telegram_allowed_user_id=0,
    )
    assert settings.telegram_funding_enabled is True


def test_live_mainnet_requires_explicit_circuit_breaker() -> None:
    with pytest.raises(ValidationError, match="ALLOW_MAINNET"):
        Settings(
            _env_file=None,
            funding_mode="live",
            funding_network="mainnet",
            funding_node_url="http://node.invalid",
            funding_master_private_key="01".rjust(64, "0"),
        )


async def test_live_gateway_derives_and_checks_master_address_without_network_call() -> None:
    settings = Settings(
        _env_file=None,
        funding_mode="live",
        funding_network="nile",
        funding_contract_address="TQriju7D5yYGFMiwnTEyKJ3eQ4eGupnY99",
        funding_node_url="http://127.0.0.1:1",
        funding_master_private_key="01".rjust(64, "0"),
        funding_master_address="TMVQGm1qAQYVdetCeGRRkTWYYrLXuHK2HC",
    )
    gateway = TronFundingGateway(settings)
    assert gateway.source_address == settings.funding_master_address
    await gateway.close()
