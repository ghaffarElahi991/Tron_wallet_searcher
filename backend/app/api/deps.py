import hmac
import uuid
from typing import Annotated

import jwt
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.security import decode_access_token
from app.database import get_db
from app.models import User

settings = get_settings()
bearer_scheme = HTTPBearer(auto_error=False)
DatabaseSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(
    db: DatabaseSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise credentials_error
    try:
        user_id = uuid.UUID(decode_access_token(credentials.credentials))
    except (jwt.InvalidTokenError, ValueError) as exc:
        raise credentials_error from exc
    user = await db.get(User, user_id)
    if user is None or not user.is_active or user.username != settings.admin_username:
        raise credentials_error
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_worker_key(
    provided_key: Annotated[str | None, Header(alias="X-Worker-API-Key")] = None,
) -> None:
    expected_key = settings.worker_api_key.get_secret_value()
    if provided_key is None or not hmac.compare_digest(provided_key, expected_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid worker credentials."
        )


WorkerAuthenticated = Annotated[None, Depends(require_worker_key)]
