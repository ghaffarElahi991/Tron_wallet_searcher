import uuid

import httpx
import pytest

from app.core.security import create_access_token
from tests.helpers import valid_job_payload

pytestmark = pytest.mark.anyio


async def test_job_requires_authentication(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/jobs", json=valid_job_payload())
    assert response.status_code == 401


async def test_create_list_read_and_cancel_job(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    headers = {**auth_headers, "Idempotency-Key": "request-001"}
    created = await client.post("/api/v1/jobs", json=valid_job_payload(), headers=headers)
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "queued"
    assert body["observed_rate"] is None
    assert body["result"] is None

    duplicate = await client.post("/api/v1/jobs", json=valid_job_payload(), headers=headers)
    assert duplicate.status_code == 201
    assert duplicate.json()["id"] == body["id"]

    listing = await client.get("/api/v1/jobs", headers=auth_headers)
    assert listing.status_code == 200
    assert listing.json()["total"] == 1

    fetched = await client.get(f"/api/v1/jobs/{body['id']}", headers=auth_headers)
    assert fetched.status_code == 200
    assert fetched.json()["prefix"] == valid_job_payload()["prefix"]

    canceled = await client.post(f"/api/v1/jobs/{body['id']}/cancel", headers=auth_headers)
    assert canceled.status_code == 200
    assert canceled.json()["status"] == "canceled"


async def test_rejects_unsupported_or_invalid_patterns(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    invalid_pattern = {
        "pattern": "4x6",
        "prefix": "TABX",
        "suffix": "999999",
    }
    response = await client.post("/api/v1/jobs", json=invalid_pattern, headers=auth_headers)
    assert response.status_code == 422

    lowercase_first = {
        "pattern": "3x4",
        "prefix": "TaBC",
        "suffix": "7777",
    }
    response = await client.post("/api/v1/jobs", json=lowercase_first, headers=auth_headers)
    assert response.status_code == 422

    invalid_base58 = {
        "pattern": "3x4",
        "prefix": "TABC",
        "suffix": "00Il",
    }
    response = await client.post("/api/v1/jobs", json=invalid_base58, headers=auth_headers)
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("pattern", "prefix", "suffix"),
    [
        ("3x4", "TABC", "7777"),
        ("2x5", "TXR", "88888"),
        ("4x3", "TRXQ9", "999"),
        ("2x2", "TQR", "99"),
    ],
)
async def test_fixed_t_is_not_counted_as_a_prefix_character(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    pattern: str,
    prefix: str,
    suffix: str,
) -> None:
    response = await client.post(
        "/api/v1/jobs",
        json={"pattern": pattern, "prefix": prefix, "suffix": suffix},
        headers=auth_headers,
    )
    assert response.status_code == 201
    assert response.json()["prefix"] == prefix


async def test_rejects_prefix_that_counts_fixed_t(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/api/v1/jobs",
        json={"pattern": "3x4", "prefix": "TAB", "suffix": "7777"},
        headers=auth_headers,
    )
    assert response.status_code == 422


async def test_unknown_token_subject_cannot_read_operator_job(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    created = await client.post("/api/v1/jobs", json=valid_job_payload(), headers=auth_headers)
    job_id = created.json()["id"]

    token, _ = create_access_token(str(uuid.uuid4()))
    response = await client.get(
        f"/api/v1/jobs/{job_id}", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 401
