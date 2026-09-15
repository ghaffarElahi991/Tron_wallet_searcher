import asyncio
import time
import uuid
from typing import Any

import httpx

from app.config import Settings
from app.models import PatternType
from app.schemas import GenerationJobRead, GpuFleetRead, JobListResponse, TokenResponse


class TronForgeApiError(RuntimeError):
    pass


def response_error(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    if isinstance(detail, list):
        messages = [
            str(item.get("msg", item)) if isinstance(item, dict) else str(item)
            for item in detail
        ]
        return " ".join(messages)
    if isinstance(detail, str):
        return detail
    return f"TronForge API returned HTTP {response.status_code}."


class TronForgeApiClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.client = httpx.AsyncClient(
            base_url=f"{settings.telegram_api_url.rstrip('/')}/",
            timeout=httpx.Timeout(15),
            transport=transport,
        )
        self.token: str | None = None
        self.next_auth_at = 0.0
        self.auth_lock = asyncio.Lock()

    async def close(self) -> None:
        await self.client.aclose()

    def _token_is_current(self) -> bool:
        return self.token is not None and time.monotonic() < self.next_auth_at

    async def authenticate(
        self, *, force: bool = False, stale_token: str | None = None
    ) -> None:
        if not force and self._token_is_current():
            return
        async with self.auth_lock:
            if not force and self._token_is_current():
                return
            if force and stale_token is not None and self.token != stale_token:
                # Another request already renewed the token that received the 401.
                if self._token_is_current():
                    return
            try:
                response = await self.client.post(
                    "auth/token",
                    json={
                        "username": self.settings.admin_username,
                        "password": self.settings.admin_password.get_secret_value(),
                    },
                )
            except httpx.HTTPError as exc:
                raise TronForgeApiError("Cannot connect to the TronForge API.") from exc
            if not response.is_success:
                raise TronForgeApiError(response_error(response))
            token_response = TokenResponse.model_validate(response.json())
            self.token = token_response.access_token
            expires_in = max(1, token_response.expires_in)
            renewal_margin = min(60, max(1, expires_in // 10))
            self.next_auth_at = time.monotonic() + max(1, expires_in - renewal_margin)

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        await self.authenticate()
        request_headers = dict(headers or {})
        sent_token = self.token
        request_headers["Authorization"] = f"Bearer {sent_token}"
        try:
            response = await self.client.request(
                method,
                path.lstrip("/"),
                json=json_body,
                headers=request_headers,
            )
        except httpx.HTTPError as exc:
            raise TronForgeApiError("Cannot connect to the TronForge API.") from exc
        if response.status_code == 401:
            await self.authenticate(force=True, stale_token=sent_token)
            request_headers["Authorization"] = f"Bearer {self.token}"
            try:
                response = await self.client.request(
                    method,
                    path.lstrip("/"),
                    json=json_body,
                    headers=request_headers,
                )
            except httpx.HTTPError as exc:
                raise TronForgeApiError("Cannot connect to the TronForge API.") from exc
        if not response.is_success:
            raise TronForgeApiError(response_error(response))
        return response

    async def create_job(
        self, pattern: PatternType, prefix: str, suffix: str
    ) -> GenerationJobRead:
        response = await self.request(
            "POST",
            "jobs",
            json_body={"pattern": pattern.value, "prefix": prefix, "suffix": suffix},
            headers={"Idempotency-Key": f"telegram-{uuid.uuid4()}"},
        )
        return GenerationJobRead.model_validate(response.json())

    async def get_job(self, job_id: uuid.UUID) -> GenerationJobRead:
        response = await self.request("GET", f"jobs/{job_id}")
        return GenerationJobRead.model_validate(response.json())

    async def list_jobs(self, limit: int = 10) -> JobListResponse:
        response = await self.request("GET", f"jobs?limit={limit}&offset=0")
        return JobListResponse.model_validate(response.json())

    async def cancel_job(self, job_id: uuid.UUID) -> GenerationJobRead:
        response = await self.request("POST", f"jobs/{job_id}/cancel")
        return GenerationJobRead.model_validate(response.json())

    async def get_gpu_fleet(self) -> GpuFleetRead:
        response = await self.request("GET", "gpus")
        return GpuFleetRead.model_validate(response.json())
