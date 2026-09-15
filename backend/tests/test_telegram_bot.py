import asyncio
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage

from app.config import Settings
from app.models import PatternType
from app.schemas import GenerationJobRead
from app.telegram_bot import api_client as api_client_module
from app.telegram_bot.api_client import TronForgeApiClient
from app.telegram_bot.handlers import create_router, wallet_commands_allowed
from app.telegram_bot.messages import job_progress, wallet_result
from app.telegram_bot.runtime import BotRuntime
from app.telegram_bot.validation import (
    PatternInputError,
    validate_custom_prefix,
    validate_suffix,
)


def test_telegram_pattern_validation_skips_fixed_t() -> None:
    assert validate_custom_prefix(PatternType.THREE_BY_FOUR, "Abc") == "TAbc"
    assert validate_suffix(PatternType.THREE_BY_FOUR, "wqe1") == "wqe1"


def test_telegram_pattern_validation_rejects_invalid_input() -> None:
    for value in ("abc", "AOc", "TAbc"):
        try:
            validate_custom_prefix(PatternType.THREE_BY_FOUR, value)
        except PatternInputError:
            pass
        else:
            raise AssertionError(f"Expected invalid Telegram prefix: {value}")


def test_wallet_result_message_contains_address_and_private_key() -> None:
    now = datetime.now(UTC)
    private_key = "ab" * 32
    address = "TQriju7D5yYGFMiwnTEyKJ3eQ4eGupnY99"
    job = GenerationJobRead.model_validate(
        {
            "id": uuid.uuid4(),
            "pattern": "2x2",
            "prefix": "TQr",
            "suffix": "99",
            "status": "ready",
            "attempts": 1,
            "search_rate": 33_000_000,
            "observed_rate": 78_200_000,
            "created_at": now,
            "updated_at": now,
            "started_at": now,
            "completed_at": now,
            "failure_code": None,
            "failure_message": None,
            "result": {"address": address, "private_key": private_key, "verified_at": now},
        }
    )
    text = wallet_result(job)
    assert address in text
    assert private_key in text
    assert "Final observed average: <b>78.2M/s</b>" in text
    assert "Anyone with this key controls the wallet" in text


def test_telegram_progress_uses_observed_average_not_initial_estimate() -> None:
    now = datetime.now(UTC)
    job = GenerationJobRead.model_validate(
        {
            "id": uuid.uuid4(),
            "pattern": "2x2",
            "prefix": "TQr",
            "suffix": "99",
            "status": "searching",
            "attempts": 67_092_480,
            "search_rate": 50_800_000,
            "observed_rate": 78_200_000,
            "created_at": now,
            "updated_at": now,
            "started_at": now,
            "completed_at": None,
            "failure_code": None,
            "failure_message": None,
            "result": None,
        }
    )

    text = job_progress(job)
    assert "Observed average: <b>78.2M/s</b>" in text
    assert "50.8M" not in text


@pytest.mark.parametrize(
    ("user_id", "is_bot", "chat_id", "chat_type", "restricted", "group_id", "expected"),
    [
        (42, False, 42, "private", True, 0, True),
        (43, False, 43, "private", True, 0, False),
        (42, False, -1001, "supergroup", True, -1001, True),
        (43, False, -1001, "group", True, -1001, False),
        (43, False, -1001, "group", False, -1001, True),
        (43, False, -1002, "group", False, -1001, False),
        (43, False, 43, "private", False, -1001, False),
        (43, False, -1001, "group", False, 0, False),
        (43, True, -1001, "group", False, -1001, False),
        (43, False, -1001, "channel", False, -1001, False),
    ],
)
def test_group_access_respects_env_switch_and_exact_chat(
    user_id: int,
    is_bot: bool,
    chat_id: int,
    chat_type: str,
    restricted: bool,
    group_id: int,
    expected: bool,
) -> None:
    assert wallet_commands_allowed(
        user_id=user_id,
        is_bot=is_bot,
        chat_id=chat_id,
        chat_type=chat_type,
        allowed_user_id=42,
        restrict_user_id=restricted,
        allowed_group_id=group_id,
    ) is expected


@pytest.mark.parametrize(
    ("user_id", "is_bot", "chat_id", "chat_type", "expected"),
    [
        (43, False, 43, "private", True),
        (43, False, -1001, "group", True),
        (43, False, -1002, "supergroup", True),
        (43, False, -1001, "channel", False),
        (43, True, -1001, "group", False),
    ],
)
def test_public_mode_allows_any_human_in_private_or_group_chat(
    user_id: int, is_bot: bool, chat_id: int, chat_type: str, expected: bool
) -> None:
    assert wallet_commands_allowed(
        user_id=user_id,
        is_bot=is_bot,
        chat_id=chat_id,
        chat_type=chat_type,
        allowed_user_id=42,
        restrict_user_id=True,
        allowed_group_id=-1001,
        public_access=True,
    ) is expected


@pytest.mark.anyio
async def test_group_monitor_delivers_private_key_only_by_dm() -> None:
    now = datetime.now(UTC)
    key = "ab" * 32
    job_id = uuid.uuid4()
    job = GenerationJobRead.model_validate(
        {
            "id": job_id,
            "pattern": "2x2",
            "prefix": "TQr",
            "suffix": "99",
            "status": "ready",
            "attempts": 10,
            "search_rate": 10,
            "observed_rate": 10,
            "created_at": now,
            "updated_at": now,
            "started_at": now,
            "completed_at": now,
            "failure_code": None,
            "failure_message": None,
            "result": {
                "address": "TQriju7D5yYGFMiwnTEyKJ3eQ4eGupnY99",
                "private_key": key,
                "verified_at": now,
            },
        }
    )

    class FakeApi:
        async def get_job(self, requested_id: uuid.UUID) -> GenerationJobRead:
            assert requested_id == job_id
            return job

    class FakeBot:
        def __init__(self, *, block_dm: bool = False) -> None:
            self.edited: list[tuple[int, str]] = []
            self.sent: list[tuple[int, str]] = []
            self.block_dm = block_dm

        async def edit_message_text(self, *, chat_id: int, text: str, **_kwargs) -> None:
            self.edited.append((chat_id, text))

        async def send_message(self, chat_id: int, text: str) -> None:
            if self.block_dm and chat_id == 43:
                raise TelegramForbiddenError(
                    SendMessage(chat_id=chat_id, text=text), "DM not opened"
                )
            self.sent.append((chat_id, text))

    bot = FakeBot()
    runtime = BotRuntime(FakeApi(), poll_interval=0.01)  # type: ignore[arg-type]
    await runtime._monitor(  # type: ignore[arg-type]
        bot,
        chat_id=-1001,
        message_id=1,
        job_id=job_id,
        recipient_user_id=43,
    )
    assert bot.edited and bot.edited[0][0] == -1001
    assert key not in bot.edited[0][1]
    assert bot.sent == [(43, wallet_result(job))]

    blocked = FakeBot(block_dm=True)
    await runtime._monitor(  # type: ignore[arg-type]
        blocked,
        chat_id=-1001,
        message_id=1,
        job_id=job_id,
        recipient_user_id=43,
    )
    assert len(blocked.sent) == 1
    assert blocked.sent[0][0] == -1001
    assert "send /start" in blocked.sent[0][1]
    assert key not in blocked.sent[0][1]

    public_bot = FakeBot()
    public_runtime = BotRuntime(
        FakeApi(), poll_interval=0.01, broadcast_results_to_chat=True
    )  # type: ignore[arg-type]
    await public_runtime._monitor(  # type: ignore[arg-type]
        public_bot,
        chat_id=-1001,
        message_id=1,
        job_id=job_id,
        recipient_user_id=43,
    )
    assert public_bot.sent == [(-1001, wallet_result(job))]
    assert key in public_bot.sent[0][1]


def test_group_access_settings_are_read_from_env(monkeypatch) -> None:
    monkeypatch.setenv("TRONFORGE_TELEGRAM_RESTRICT_USER_ID", "false")
    monkeypatch.setenv("TRONFORGE_TELEGRAM_ALLOWED_GROUP_ID", "-100123456")
    settings = Settings(_env_file=None)
    assert settings.telegram_restrict_user_id is False
    assert settings.telegram_allowed_group_id == -100123456


def test_public_access_setting_is_read_from_env(monkeypatch) -> None:
    monkeypatch.setenv("TRONFORGE_TELEGRAM_PUBLIC_ACCESS", "true")
    settings = Settings(_env_file=None)
    assert settings.telegram_public_access is True


@pytest.mark.anyio
async def test_public_reveal_posts_wallet_in_group_instead_of_dm() -> None:
    now = datetime.now(UTC)
    key = "cd" * 32
    job_id = uuid.uuid4()
    job = GenerationJobRead.model_validate(
        {
            "id": job_id,
            "pattern": "2x2",
            "prefix": "TQr",
            "suffix": "99",
            "status": "ready",
            "attempts": 1,
            "search_rate": 1,
            "observed_rate": 1,
            "created_at": now,
            "updated_at": now,
            "started_at": now,
            "completed_at": now,
            "failure_code": None,
            "failure_message": None,
            "result": {
                "address": "TQriju7D5yYGFMiwnTEyKJ3eQ4eGupnY99",
                "private_key": key,
                "verified_at": now,
            },
        }
    )
    deliveries: list[tuple[int, str]] = []
    answers: list[str] = []

    class FakeBot:
        async def send_message(self, chat_id: int, text: str) -> None:
            deliveries.append((chat_id, text))

    class FakeCallback:
        data = f"job:reveal:{job_id}"
        message = SimpleNamespace(chat=SimpleNamespace(id=-1001))
        from_user = SimpleNamespace(id=43)
        bot = FakeBot()

        async def answer(self, text: str, **_kwargs) -> None:
            answers.append(text)

    class FakeApi:
        async def get_job(self, requested_id: uuid.UUID) -> GenerationJobRead:
            assert requested_id == job_id
            return job

    runtime = SimpleNamespace(api=FakeApi())
    router = create_router(0, public_access=True)
    handler = next(
        item.callback
        for item in router.callback_query.handlers
        if item.callback.__name__ == "reveal_job"
    )
    await handler(FakeCallback(), runtime)
    assert deliveries == [(-1001, wallet_result(job))]
    assert key in deliveries[0][1]
    assert answers == ["Wallet data sent."]


@pytest.mark.anyio
async def test_telegram_api_client_uses_versioned_backend_path() -> None:
    requested_paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path.endswith("/auth/token"):
            return httpx.Response(
                200,
                json={"access_token": "test-token", "token_type": "bearer", "expires_in": 1800},
            )
        assert request.headers["Authorization"] == "Bearer test-token"
        return httpx.Response(
            200,
            json={
                "mode": "cuda",
                "total": 0,
                "ready": 0,
                "searching": 0,
                "unhealthy": 0,
                "combined_benchmark_rate": 0,
                "devices": [],
            },
        )

    settings = Settings(
        _env_file=None,
        admin_username="admin",
        admin_password="test-password",
        telegram_api_url="http://backend.test/api/v1",
    )
    api = TronForgeApiClient(settings, transport=httpx.MockTransport(handler))
    try:
        fleet = await api.get_gpu_fleet()
    finally:
        await api.close()

    assert fleet.mode == "cuda"
    assert requested_paths == ["/api/v1/auth/token", "/api/v1/gpus"]


@pytest.mark.anyio
async def test_telegram_api_client_renews_before_expiry_without_401(monkeypatch) -> None:
    now = [100.0]
    monkeypatch.setattr(api_client_module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    logins = 0
    used_tokens: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal logins
        if request.url.path.endswith("/auth/token"):
            logins += 1
            return httpx.Response(
                200,
                json={
                    "access_token": f"token-{logins}",
                    "token_type": "bearer",
                    "expires_in": 180,
                },
            )
        used_tokens.append(request.headers["Authorization"])
        return httpx.Response(200, json={"ok": True})

    settings = Settings(_env_file=None, telegram_api_url="http://backend.test/api/v1")
    api = TronForgeApiClient(settings, transport=httpx.MockTransport(handler))
    try:
        await api.request("GET", "gpus")
        now[0] = 250.0
        await api.request("GET", "gpus")
        now[0] = 263.0
        await api.request("GET", "gpus")
    finally:
        await api.close()

    assert logins == 2
    assert used_tokens == ["Bearer token-1", "Bearer token-1", "Bearer token-2"]


@pytest.mark.anyio
async def test_telegram_api_client_retries_401_with_same_idempotency_key() -> None:
    logins = 0
    sent_headers: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal logins
        if request.url.path.endswith("/auth/token"):
            logins += 1
            return httpx.Response(
                200,
                json={
                    "access_token": f"token-{logins}",
                    "token_type": "bearer",
                    "expires_in": 1800,
                },
            )
        sent_headers.append(
            (request.headers["Authorization"], request.headers["Idempotency-Key"])
        )
        return httpx.Response(401 if logins == 1 else 200, json={"ok": True})

    settings = Settings(_env_file=None, telegram_api_url="http://backend.test/api/v1")
    api = TronForgeApiClient(settings, transport=httpx.MockTransport(handler))
    try:
        response = await api.request(
            "POST", "jobs", json_body={"pattern": "2x2"}, headers={"Idempotency-Key": "once"}
        )
    finally:
        await api.close()

    assert response.status_code == 200
    assert logins == 2
    assert sent_headers == [("Bearer token-1", "once"), ("Bearer token-2", "once")]


@pytest.mark.anyio
async def test_concurrent_401_requests_share_one_telegram_reauthentication() -> None:
    old_requests = 0
    logins = 0
    both_old_sent = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal old_requests, logins
        if request.url.path.endswith("/auth/token"):
            logins += 1
            return httpx.Response(
                200,
                json={
                    "access_token": f"token-{logins}",
                    "token_type": "bearer",
                    "expires_in": 1800,
                },
            )
        if request.headers["Authorization"] == "Bearer token-1":
            old_requests += 1
            if old_requests == 2:
                both_old_sent.set()
            await both_old_sent.wait()
            return httpx.Response(401, json={"detail": "expired"})
        assert request.headers["Authorization"] == "Bearer token-2"
        return httpx.Response(200, json={"ok": True})

    settings = Settings(_env_file=None, telegram_api_url="http://backend.test/api/v1")
    api = TronForgeApiClient(settings, transport=httpx.MockTransport(handler))
    try:
        responses = await asyncio.wait_for(
            asyncio.gather(api.request("GET", "gpus"), api.request("GET", "jobs")),
            timeout=2,
        )
    finally:
        await api.close()

    assert [response.status_code for response in responses] == [200, 200]
    assert old_requests == 2
    assert logins == 2
