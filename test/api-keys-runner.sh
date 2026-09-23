#!/usr/bin/env bash
# Read-only validation for TronForge Telegram, TronGrid, funding-wallet, and API credentials.
set -Eeuo pipefail
umask 077

TEST_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd -- "${TEST_DIR}/.." && pwd -P)"
BACKEND_DIR="${PROJECT_ROOT}/backend"
VENV_PYTHON="${BACKEND_DIR}/.venv/bin/python"
OFFLINE=false

usage() {
  cat <<'EOF'
Usage: ./test/api-keys-runner.sh [--offline]

Checks credentials from backend/app/local_constants.py and remaining settings from
backend/.env without printing their values or sending funds.

  --offline  Validate configuration and the funding private-key/address pair only.
             Skip Telegram, TronGrid, and the optional running FastAPI login probe.
EOF
}

fail() {
  printf 'Credential test error: %s\n' "$1" >&2
  exit 1
}

case "${1:-}" in
  "") ;;
  --offline) OFFLINE=true ;;
  -h|--help)
    usage
    exit 0
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
[[ $# -le 1 ]] || fail "Too many arguments."

[[ -f "${BACKEND_DIR}/.env" ]] || fail "backend/.env is missing."
[[ -f "${BACKEND_DIR}/app/local_constants.py" ]] || \
  fail "backend/app/local_constants.py is missing; copy local_constants.py.example first."
[[ -x "$VENV_PYTHON" ]] || fail "backend/.venv Python is missing; run ./install.sh first."

if [[ "$OFFLINE" == true ]]; then
  export TRONFORGE_CREDENTIAL_CHECK_OFFLINE=1
else
  unset TRONFORGE_CREDENTIAL_CHECK_OFFLINE || true
fi

(cd "$BACKEND_DIR" && "$VENV_PYTHON" - <<'PY'
import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
from pydantic import ValidationError

from app.config import get_settings
from app.funding.gateway import FundingGatewayError, TronFundingGateway


TIMEOUT = httpx.Timeout(15.0, connect=8.0)
OFFLINE = os.environ.get("TRONFORGE_CREDENTIAL_CHECK_OFFLINE") == "1"
failures = 0


def passed(label: str, detail: str = "") -> None:
    suffix = f" — {detail}" if detail else ""
    print(f"PASS  {label}{suffix}")


def failed(label: str, detail: str) -> None:
    global failures
    failures += 1
    print(f"FAIL  {label} — {detail}")


def skipped(label: str, detail: str) -> None:
    print(f"SKIP  {label} — {detail}")


def endpoint(base_url: str, path: str) -> str:
    parsed = urlsplit(base_url.strip())
    clean_path = parsed.path.rstrip("/")
    target_path = f"{clean_path}/{path.lstrip('/')}"
    return urlunsplit((parsed.scheme, parsed.netloc, target_path, "", ""))


def response_detail(response: httpx.Response) -> str:
    if response.status_code in {401, 403}:
        return f"HTTP {response.status_code}: credential rejected or blocked by key policy"
    if response.status_code == 429:
        return "HTTP 429: API quota or rate limit reached"
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError):
        return f"HTTP {response.status_code}"
    if isinstance(payload, dict):
        raw = payload.get("Error") or payload.get("error") or payload.get("message")
        if isinstance(raw, str):
            return f"HTTP {response.status_code}: {raw[:200]}"
    return f"HTTP {response.status_code}"


try:
    settings = get_settings()
except ValidationError as exc:
    print("FAIL  backend/.env configuration — settings validation failed")
    for error in exc.errors(include_url=False):
        location = ".".join(str(item) for item in error.get("loc", ())) or "settings"
        print(f"      {location}: {error.get('msg', 'invalid value')}")
    raise SystemExit(1) from None

passed("application configuration", "local constants and environment settings loaded")
print(f"INFO  Funding mode: {settings.funding_mode}")
print(f"INFO  Telegram funding button: {'enabled' if settings.telegram_funding_enabled else 'disabled'}")

gateway = None
if settings.funding_mode == "live":
    try:
        gateway = TronFundingGateway(settings)
    except (FundingGatewayError, OSError, ValueError) as exc:
        failed("master funding wallet", str(exc))
    else:
        passed("master funding wallet", "private key derives the configured address")
elif settings.funding_mode == "simulator":
    skipped("master funding wallet", "simulator mode does not use a real master key")
else:
    skipped("master funding wallet", "funding mode is disabled")


async def check_telegram() -> None:
    token = settings.telegram_bot_token.get_secret_value().strip()
    if not token:
        failed("Telegram bot token", "TELEGRAM_BOT_TOKEN is empty in local_constants.py")
        return
    # Never include the token in error output: Telegram embeds it in the request URL.
    url = f"https://api.telegram.org/bot{quote(token, safe='')}/getMe"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        failed("Telegram bot token", f"Telegram is unreachable ({type(exc).__name__})")
        return
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code == 200 and payload.get("ok") is True:
        bot = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        identity = str(bot.get("username") or bot.get("id") or "authenticated bot")
        passed("Telegram bot token", f"authenticated as {identity}")
        return
    if response.status_code == 401:
        failed("Telegram bot token", "Telegram rejected the token")
    else:
        failed("Telegram bot token", f"Telegram returned HTTP {response.status_code}")


async def check_funding_node() -> None:
    if settings.funding_mode != "live":
        skipped("funding node/API key", "live funding is not enabled")
        return
    base_url = settings.funding_node_url.strip()
    api_key = settings.funding_node_api_key.get_secret_value().strip()
    if not base_url:
        failed("funding node/API key", "FUNDING_NODE_URL is empty in local_constants.py")
        return
    headers = {"Accept": "application/json", "User-Agent": "TronForge-Credential-Check/1"}
    if api_key:
        headers["TRON-PRO-API-KEY"] = api_key
    url = endpoint(base_url, "wallet/getnowblock")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(url, headers=headers, json={"visible": True})
    except httpx.HTTPError as exc:
        failed("funding node/API key", f"node is unreachable ({type(exc).__name__})")
        return
    if not response.is_success:
        failed("funding node/API key", response_detail(response))
        return
    try:
        payload = response.json()
    except ValueError:
        failed("funding node/API key", "node returned non-JSON data")
        return
    block_header = payload.get("block_header") if isinstance(payload, dict) else None
    if not payload.get("blockID") or not isinstance(block_header, dict):
        detail = response_detail(response)
        failed("funding node/API key", f"unexpected node response ({detail})")
        return
    provider_name = "TronGrid" if "trongrid" in base_url.lower() else "configured TRON node"
    passed("funding node/API key", f"{provider_name} returned the current block")


async def check_funding_reads() -> None:
    if gateway is None:
        skipped("funding read permissions", "a valid live funding gateway is unavailable")
        return
    try:
        contract = await gateway.client.get_contract(settings.funding_contract_address)
        decimals_method = contract.functions.decimals.with_owner(gateway.source_address)
        decimals = int(await decimals_method())
        balance_method = contract.functions.balanceOf.with_owner(gateway.source_address)
        await balance_method(gateway.source_address)
        await gateway.client.get_account_resource(gateway.source_address)
    except Exception as exc:
        failed("funding read permissions", f"required read failed ({type(exc).__name__})")
        return
    if decimals != 6:
        failed("funding token contract", f"configured token has {decimals} decimals, expected 6")
        return
    passed("funding read permissions", "contract, balance, and resource queries succeeded")


async def check_backend_login() -> None:
    url = endpoint(settings.telegram_api_url, "auth/token")
    payload = {
        "username": settings.admin_username,
        "password": settings.admin_password.get_secret_value(),
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0)) as client:
            response = await client.post(url, json=payload)
    except httpx.HTTPError:
        skipped("Telegram-to-FastAPI credentials", "the configured FastAPI endpoint is not running")
        return
    if response.status_code == 200:
        try:
            body = response.json()
        except ValueError:
            body = {}
        if isinstance(body.get("access_token"), str) and body["access_token"]:
            passed("Telegram-to-FastAPI credentials", "local API login succeeded")
            return
    if response.status_code == 401:
        failed("Telegram-to-FastAPI credentials", "configured username or password was rejected")
    else:
        failed("Telegram-to-FastAPI credentials", response_detail(response))


async def main() -> None:
    try:
        if OFFLINE:
            skipped("external API checks", "--offline was requested")
            return
        await check_telegram()
        await check_funding_node()
        await check_funding_reads()
        await check_backend_login()
    finally:
        if gateway is not None:
            await gateway.close()


asyncio.run(main())

if failures:
    print(f"\nCredential check completed with {failures} failure(s).")
    raise SystemExit(1)
print("\nAll applicable credential checks passed.")
PY
)
