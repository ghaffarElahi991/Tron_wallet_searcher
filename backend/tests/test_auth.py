from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest

from app.config import get_settings

pytestmark = pytest.mark.anyio


async def test_single_user_can_login_and_read_profile(client: httpx.AsyncClient) -> None:
    settings = get_settings()
    login = await client.post(
        "/api/v1/auth/token",
        json={
            "username": settings.admin_username,
            "password": settings.admin_password.get_secret_value(),
        },
    )
    assert login.status_code == 200
    token = login.json()["access_token"]

    profile = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert profile.status_code == 200
    assert profile.json()["username"] == settings.admin_username


async def test_bad_password_and_other_username_are_rejected(client: httpx.AsyncClient) -> None:
    settings = get_settings()
    login = await client.post(
        "/api/v1/auth/token",
        json={"username": settings.admin_username, "password": "WrongPassword123"},
    )
    assert login.status_code == 401

    other_user = await client.post(
        "/api/v1/auth/token",
        json={
            "username": "somebody-else",
            "password": settings.admin_password.get_secret_value(),
        },
    )
    assert other_user.status_code == 401


async def test_public_registration_endpoint_does_not_exist(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/auth/register", json={})
    assert response.status_code == 404


async def test_browser_session_renews_rotates_and_logs_out(client: httpx.AsyncClient) -> None:
    settings = get_settings()
    origin = {"Origin": "http://localhost:3000"}
    login = await client.post(
        "/api/v1/auth/token",
        json={
            "username": settings.admin_username,
            "password": settings.admin_password.get_secret_value(),
        },
        headers=origin,
    )
    assert login.status_code == 200
    initial_cookie = client.cookies.get("tronforge_refresh")
    assert initial_cookie
    assert "httponly" in login.headers["set-cookie"].lower()
    assert "samesite=strict" in login.headers["set-cookie"].lower()

    renewal = await client.post("/api/v1/auth/refresh", headers=origin)
    assert renewal.status_code == 200
    renewed_cookie = client.cookies.get("tronforge_refresh")
    assert renewed_cookie and renewed_cookie != initial_cookie
    assert renewal.json()["expires_in"] == settings.access_token_minutes * 60
    profile = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {renewal.json()['access_token']}"},
    )
    assert profile.status_code == 200

    replay = await client.post(
        "/api/v1/auth/refresh",
        headers={**origin, "Cookie": f"tronforge_refresh={initial_cookie}"},
    )
    assert replay.status_code == 401

    logout = await client.post("/api/v1/auth/logout", headers=origin)
    assert logout.status_code == 200
    assert client.cookies.get("tronforge_refresh") is None
    revoked = await client.post(
        "/api/v1/auth/refresh",
        headers={**origin, "Cookie": f"tronforge_refresh={renewed_cookie}"},
    )
    assert revoked.status_code == 401


async def test_telegram_login_does_not_create_browser_cookie(client: httpx.AsyncClient) -> None:
    settings = get_settings()
    login = await client.post(
        "/api/v1/auth/token",
        json={
            "username": settings.admin_username,
            "password": settings.admin_password.get_secret_value(),
        },
    )
    assert login.status_code == 200
    assert "set-cookie" not in login.headers
    assert client.cookies.get("tronforge_refresh") is None


async def test_browser_login_rejects_unapproved_origin(client: httpx.AsyncClient) -> None:
    settings = get_settings()
    login = await client.post(
        "/api/v1/auth/token",
        json={
            "username": settings.admin_username,
            "password": settings.admin_password.get_secret_value(),
        },
        headers={"Origin": "https://untrusted.example"},
    )
    assert login.status_code == 403
    assert client.cookies.get("tronforge_refresh") is None


async def test_expired_access_token_can_be_replaced_from_browser_cookie(
    client: httpx.AsyncClient,
) -> None:
    settings = get_settings()
    origin = {"Origin": "http://localhost:3000"}
    login = await client.post(
        "/api/v1/auth/token",
        json={
            "username": settings.admin_username,
            "password": settings.admin_password.get_secret_value(),
        },
        headers=origin,
    )
    assert login.status_code == 200
    claims = jwt.decode(
        login.json()["access_token"],
        settings.jwt_secret.get_secret_value(),
        algorithms=["HS256"],
        audience=settings.app_name,
        issuer=settings.app_name,
    )
    claims["exp"] = datetime.now(UTC) - timedelta(minutes=1)
    expired = jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm="HS256")
    rejected = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {expired}"}
    )
    assert rejected.status_code == 401

    renewed = await client.post("/api/v1/auth/refresh", headers=origin)
    assert renewed.status_code == 200
    accepted = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {renewed.json()['access_token']}"},
    )
    assert accepted.status_code == 200


async def test_malformed_refresh_cookie_is_rejected_and_can_be_cleared(
    client: httpx.AsyncClient,
) -> None:
    headers = {"Origin": "http://localhost:3000", "Cookie": "tronforge_refresh=not-a-token"}
    renewal = await client.post("/api/v1/auth/refresh", headers=headers)
    assert renewal.status_code == 401
    logout = await client.post("/api/v1/auth/logout", headers=headers)
    assert logout.status_code == 200
    assert "max-age=0" in logout.headers["set-cookie"].lower()
