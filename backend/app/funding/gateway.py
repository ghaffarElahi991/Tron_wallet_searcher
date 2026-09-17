import hashlib
import json
import re
import stat
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx
from tronpy import AsyncTron
from tronpy.exceptions import TransactionNotFound
from tronpy.keys import PrivateKey, is_base58check_address, to_hex_address
from tronpy.providers.async_http import AsyncHTTPProvider

from app.config import Settings

HEX_PRIVATE_KEY = re.compile(r"^[0-9a-fA-F]{64}$")
SECP256K1_ORDER = int("FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141", 16)
TRANSFER_EVENT_TOPIC = "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
SIMULATOR_SOURCE = "TMVQGm1qAQYVdetCeGRRkTWYYrLXuHK2HC"


class FundingGatewayError(RuntimeError):
    """Base class for errors safe to persist without secret material."""


class FundingRejected(FundingGatewayError):
    pass


class FundingNetworkError(FundingGatewayError):
    pass


class ReceiptState(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    NOT_FOUND = "not_found"


@dataclass(frozen=True, slots=True)
class PreparedTransfer:
    source_address: str
    txid: str
    signed_payload: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ReceiptResult:
    state: ReceiptState
    message: str | None = None


class SimulatorFundingGateway:
    async def close(self) -> None:
        return None

    async def prepare_transfer(
        self,
        *,
        funding_id: str,
        destination: str,
        amount_micro_usdt: int,
        contract_address: str,
    ) -> PreparedTransfer:
        material = f"tronforge-simulator:{funding_id}:{destination}:{amount_micro_usdt}"
        txid = hashlib.sha256(material.encode()).hexdigest()
        payload = json.dumps(
            {
                "simulated": True,
                "txID": txid,
                "contract": contract_address,
                "destination": destination,
                "amount": amount_micro_usdt,
            },
            separators=(",", ":"),
        )
        return PreparedTransfer(
            source_address=SIMULATOR_SOURCE,
            txid=txid,
            signed_payload=payload,
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )

    async def broadcast(self, signed_payload: str, expected_txid: str) -> None:
        payload = json.loads(signed_payload)
        if payload.get("txID") != expected_txid or payload.get("simulated") is not True:
            raise FundingRejected("Stored simulator transaction failed integrity validation.")

    async def receipt(
        self,
        *,
        txid: str,
        contract_address: str,
        destination: str,
        amount_micro_usdt: int,
    ) -> ReceiptResult:
        return ReceiptResult(ReceiptState.CONFIRMED)


def _read_master_private_key(settings: Settings) -> str:
    key_file = settings.funding_master_private_key_file.strip()
    if key_file:
        path = Path(key_file).expanduser().resolve()
        if not path.is_file():
            raise FundingRejected("The configured master private-key file does not exist.")
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            raise FundingRejected(
                "The master private-key file must have permissions 0600 or stricter."
            )
        value = path.read_text(encoding="utf-8").strip()
    else:
        value = settings.funding_master_private_key.get_secret_value().strip()
    if not HEX_PRIVATE_KEY.fullmatch(value):
        raise FundingRejected("The master private key must be exactly 64 hexadecimal characters.")
    if not 1 <= int(value, 16) < SECP256K1_ORDER:
        raise FundingRejected("The master private key is outside the secp256k1 scalar range.")
    return value


def _normalize_log_address(value: str) -> str:
    normalized = value.lower().removeprefix("0x")
    return normalized[2:] if len(normalized) == 42 and normalized.startswith("41") else normalized


def _transfer_event_matches(
    info: dict[str, Any], *, contract_address: str, destination: str, amount: int
) -> bool:
    contract_hex = _normalize_log_address(to_hex_address(contract_address))
    destination_hex = _normalize_log_address(to_hex_address(destination)).rjust(64, "0")
    amount_hex = f"{amount:064x}"
    for event in info.get("log", []):
        topics = [str(topic).lower().removeprefix("0x") for topic in event.get("topics", [])]
        event_contract = _normalize_log_address(str(event.get("address", "")))
        data = str(event.get("data", "")).lower().removeprefix("0x").rjust(64, "0")
        if (
            event_contract == contract_hex
            and len(topics) >= 3
            and topics[0] == TRANSFER_EVENT_TOPIC
            and topics[2].rjust(64, "0") == destination_hex
            and data == amount_hex
        ):
            return True
    return False


def _decode_node_message(payload: dict[str, Any]) -> str:
    raw = payload.get("message") or payload.get("Error") or payload.get("code")
    if not isinstance(raw, str):
        return "The TRON node rejected the transaction."
    try:
        decoded = bytes.fromhex(raw).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        decoded = raw
    return decoded[:500]


class TronFundingGateway:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        key_hex = _read_master_private_key(settings)
        try:
            self.private_key = PrivateKey(bytes.fromhex(key_hex))
        except ValueError as exc:
            raise FundingRejected("The master private key is invalid.") from exc
        self.source_address = self.private_key.public_key.to_base58check_address()
        configured_address = settings.funding_master_address.strip()
        if configured_address and configured_address != self.source_address:
            raise FundingRejected("The configured master address does not match its private key.")
        api_key = settings.funding_node_api_key.get_secret_value().strip() or None
        provider = AsyncHTTPProvider(
            settings.funding_node_url.strip(), timeout=15.0, api_key=api_key
        )
        self.client = AsyncTron(provider=provider, network=settings.funding_network)

    async def close(self) -> None:
        await self.client.close()

    async def prepare_transfer(
        self,
        *,
        funding_id: str,
        destination: str,
        amount_micro_usdt: int,
        contract_address: str,
    ) -> PreparedTransfer:
        if not is_base58check_address(destination):
            raise FundingRejected("The generated funding destination is not a valid TRON address.")
        if not is_base58check_address(contract_address):
            raise FundingRejected("The configured TRC-20 contract is not a valid TRON address.")
        try:
            contract = await self.client.get_contract(contract_address)
            decimals_method = contract.functions.decimals.with_owner(self.source_address)
            decimals = int(await decimals_method())
            if decimals != 6:
                raise FundingRejected("The configured funding token must use six decimals.")
            balance_method = contract.functions.balanceOf.with_owner(self.source_address)
            balance = int(await balance_method(self.source_address))
            if balance < amount_micro_usdt:
                raise FundingRejected("The master wallet has insufficient token balance.")
            available_energy = int(await self.client.get_energy(self.source_address))
            available_bandwidth = int(await self.client.get_bandwidth(self.source_address))
            trx_balance_sun = int(
                (await self.client.get_account_balance(self.source_address)) * 1_000_000
            )
            if (
                available_energy < self.settings.funding_min_available_energy
                and trx_balance_sun < self.settings.funding_fee_limit_sun
            ):
                raise FundingRejected(
                    "The master wallet lacks the configured Energy or TRX fee reserve."
                )
            if (
                available_bandwidth < self.settings.funding_min_available_bandwidth
                and trx_balance_sun < 1_000_000
            ):
                raise FundingRejected(
                    "The master wallet lacks the configured Bandwidth or TRX reserve."
                )
            transfer_method = contract.functions.transfer.with_owner(self.source_address)
            builder = await transfer_method(destination, amount_micro_usdt)
            transaction = await builder.fee_limit(self.settings.funding_fee_limit_sun).build()
            signed = transaction.sign(self.private_key)
            payload = signed.to_json()
        except FundingRejected:
            raise
        except (httpx.HTTPError, OSError) as exc:
            raise FundingNetworkError("The TRON node is temporarily unavailable.") from exc
        except Exception as exc:
            raise FundingRejected(f"Could not prepare the TRC-20 transfer: {exc}") from exc
        expiration_ms = int(payload["raw_data"]["expiration"])
        return PreparedTransfer(
            source_address=self.source_address,
            txid=signed.txid,
            signed_payload=json.dumps(payload, separators=(",", ":")),
            expires_at=datetime.fromtimestamp(expiration_ms / 1000, tz=UTC),
        )

    async def broadcast(self, signed_payload: str, expected_txid: str) -> None:
        try:
            payload = json.loads(signed_payload)
        except (TypeError, ValueError) as exc:
            raise FundingRejected("The stored signed transaction is invalid.") from exc
        if payload.get("txID") != expected_txid:
            raise FundingRejected("The stored signed transaction ID does not match its record.")
        try:
            response = await self.client.provider.make_request(
                "wallet/broadcasttransaction", payload
            )
        except (httpx.HTTPError, OSError) as exc:
            raise FundingNetworkError("The transaction broadcast outcome is unknown.") from exc
        if response.get("result") is not True:
            if response.get("code") == "DUP_TRANSACTION_ERROR":
                return
            raise FundingRejected(_decode_node_message(response))
        returned_txid = response.get("txid")
        if returned_txid is not None and returned_txid != expected_txid:
            raise FundingRejected("The TRON node returned a different transaction ID.")

    async def receipt(
        self,
        *,
        txid: str,
        contract_address: str,
        destination: str,
        amount_micro_usdt: int,
    ) -> ReceiptResult:
        try:
            info = await self.client.get_solid_transaction_info(txid)
        except TransactionNotFound:
            return ReceiptResult(ReceiptState.NOT_FOUND)
        except (httpx.HTTPError, OSError) as exc:
            raise FundingNetworkError(
                "Could not query the solidified transaction receipt."
            ) from exc
        except Exception as exc:
            raise FundingNetworkError(
                "The TRON node returned an invalid transaction receipt."
            ) from exc

        execution = str(info.get("receipt", {}).get("result", ""))
        if info.get("result") == "FAILED" or (execution and execution != "SUCCESS"):
            message = str(info.get("resMessage") or execution or "TRC-20 execution failed")[:500]
            return ReceiptResult(ReceiptState.FAILED, message)
        if not _transfer_event_matches(
            info,
            contract_address=contract_address,
            destination=destination,
            amount=amount_micro_usdt,
        ):
            return ReceiptResult(
                ReceiptState.FAILED,
                "The solidified receipt does not contain the expected USDT Transfer event.",
            )
        return ReceiptResult(ReceiptState.CONFIRMED)


def create_gateway(settings: Settings) -> SimulatorFundingGateway | TronFundingGateway:
    if settings.funding_mode == "simulator":
        return SimulatorFundingGateway()
    if settings.funding_mode == "live":
        return TronFundingGateway(settings)
    raise FundingRejected("Wallet funding is disabled.")
