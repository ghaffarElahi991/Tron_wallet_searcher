from collections.abc import AsyncGenerator
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.base import Base
from app.config import get_settings
from app.core.security import hash_password
from app.database import get_db
from app.main import app
from app.models import User


class AsyncSessionAdapter:
    """Runs async route logic against an isolated synchronous SQLite test database."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, instance: object) -> None:
        self.session.add(instance)

    async def get(self, entity: type[Any], identifier: object) -> Any:
        return self.session.get(entity, identifier)

    async def scalar(self, statement: object) -> Any:
        return self.session.scalar(statement)

    async def scalars(self, statement: object) -> Any:
        return self.session.scalars(statement)

    async def commit(self) -> None:
        self.session.commit()

    async def rollback(self) -> None:
        self.session.rollback()

    async def refresh(self, instance: object) -> None:
        self.session.refresh(instance)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client() -> AsyncGenerator[httpx.AsyncClient, None]:
    test_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(test_engine)
    settings = get_settings()
    with testing_session() as session:
        session.add(
            User(
                username=settings.admin_username,
                password_hash=hash_password(settings.admin_password.get_secret_value()),
            )
        )
        session.commit()

    async def override_get_db() -> AsyncGenerator[AsyncSessionAdapter, None]:
        with testing_session() as session:
            yield AsyncSessionAdapter(session)

    app.dependency_overrides[get_db] = override_get_db
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(test_engine)
        test_engine.dispose()


@pytest.fixture
async def auth_headers(client: httpx.AsyncClient) -> dict[str, str]:
    settings = get_settings()
    token_response = await client.post(
        "/api/v1/auth/token",
        json={
            "username": settings.admin_username,
            "password": settings.admin_password.get_secret_value(),
        },
    )
    assert token_response.status_code == 200
    token = token_response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
