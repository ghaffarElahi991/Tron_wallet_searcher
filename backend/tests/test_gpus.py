import httpx
import pytest

from app.config import get_settings

pytestmark = pytest.mark.anyio


async def test_gpu_fleet_requires_authentication(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/gpus")

    assert response.status_code == 401


async def test_gpu_fleet_is_empty_before_scheduler_starts(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.get("/api/v1/gpus", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {
        "mode": get_settings().generator_mode,
        "total": 0,
        "ready": 0,
        "searching": 0,
        "unhealthy": 0,
        "combined_benchmark_rate": 0,
        "devices": [],
    }
