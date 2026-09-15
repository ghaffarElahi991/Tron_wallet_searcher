import httpx
import pytest

from app.config import get_settings
from app.services import server_wallet
from tests.helpers import (
    CLIENT_PRIVATE_SCALAR,
    deterministic_server_key_share,
    matching_candidate,
    valid_job_payload,
)

WORKER_HEADERS = {
    "X-Worker-API-Key": get_settings().worker_api_key.get_secret_value(),
}
pytestmark = pytest.mark.anyio


async def test_verified_candidate_becomes_owner_visible_result(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server_wallet, "create_server_key_share", deterministic_server_key_share)
    created = await client.post("/api/v1/jobs", json=valid_job_payload(), headers=auth_headers)
    job_id = created.json()["id"]
    address, offset = matching_candidate()

    submitted = await client.post(
        f"/api/v1/internal/results/{job_id}",
        json={"worker_id": "local-gpu-0", "address": address, "offset": offset},
        headers=WORKER_HEADERS,
    )
    assert submitted.status_code == 200
    assert submitted.json()["accepted"] is True
    assert submitted.json()["status"] == "ready"

    fetched = await client.get(f"/api/v1/jobs/{job_id}", headers=auth_headers)
    assert fetched.status_code == 200
    result = fetched.json()["result"]
    assert result["address"] == address
    expected_private_key = (CLIENT_PRIVATE_SCALAR + int(offset, 16)).to_bytes(32, "big").hex()
    assert result["private_key"] == expected_private_key
    assert "offset" not in result


async def test_candidate_requires_worker_auth_and_correct_derivation(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server_wallet, "create_server_key_share", deterministic_server_key_share)
    created = await client.post("/api/v1/jobs", json=valid_job_payload(), headers=auth_headers)
    job_id = created.json()["id"]
    address, offset = matching_candidate()

    unauthorized = await client.post(
        f"/api/v1/internal/results/{job_id}",
        json={"worker_id": "gpu-0", "address": address, "offset": offset},
    )
    assert unauthorized.status_code == 401

    wrong_offset = (int(offset, 16) + 1).to_bytes(32, "big").hex()
    rejected = await client.post(
        f"/api/v1/internal/results/{job_id}",
        json={"worker_id": "gpu-0", "address": address, "offset": wrong_offset},
        headers=WORKER_HEADERS,
    )
    assert rejected.status_code == 422

    fetched = await client.get(f"/api/v1/jobs/{job_id}", headers=auth_headers)
    assert fetched.json()["status"] == "queued"
    assert fetched.json()["result"] is None
