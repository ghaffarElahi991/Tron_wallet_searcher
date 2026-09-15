import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DatabaseSession
from app.config import get_settings
from app.core.security import create_access_token, verify_password
from app.models import AuthSession, User, utc_now
from app.schemas import LoginRequest, TokenResponse, UserRead

router = APIRouter(prefix="/auth", tags=["authentication"])
REFRESH_COOKIE = "tronforge_refresh"
REFRESH_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{64}$")


def _check_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if origin is not None and origin not in get_settings().cors_origin_list:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Origin not allowed.")
    return origin is not None


def _refresh_hash(token: str) -> str | None:
    if not REFRESH_TOKEN_RE.fullmatch(token):
        return None
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _set_refresh_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=token,
        max_age=settings.browser_session_days * 86_400,
        path=f"{settings.api_prefix}/auth",
        secure=settings.environment != "development",
        httponly=True,
        samesite="strict",
    )


def _clear_refresh_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        key=REFRESH_COOKIE,
        path=f"{settings.api_prefix}/auth",
        secure=settings.environment != "development",
        httponly=True,
        samesite="strict",
    )


def _unauthorized_session() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Browser session is no longer valid. Please sign in again.",
    )


@router.post("/token", response_model=TokenResponse)
async def issue_token(
    payload: LoginRequest, db: DatabaseSession, request: Request, response: Response
) -> TokenResponse:
    browser_login = _check_origin(request)
    settings = get_settings()
    user = None
    if payload.username == settings.admin_username:
        user = await db.scalar(select(User).where(User.username == settings.admin_username))
    password = payload.password.get_secret_value()
    if user is None or not verify_password(password, user.password_hash) or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token, expires_in = create_access_token(str(user.id))
    if browser_login:
        refresh_token = secrets.token_urlsafe(48)
        db.add(
            AuthSession(
                user_id=user.id,
                refresh_token_hash=_refresh_hash(refresh_token),
                expires_at=utc_now() + timedelta(days=settings.browser_session_days),
            )
        )
        await db.commit()
        _set_refresh_cookie(response, refresh_token)
    return TokenResponse(access_token=token, expires_in=expires_in)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_browser_session(
    db: DatabaseSession, request: Request, response: Response
) -> TokenResponse:
    _check_origin(request)
    refresh_token = request.cookies.get(REFRESH_COOKIE)
    if not refresh_token:
        raise _unauthorized_session()
    refresh_hash = _refresh_hash(refresh_token)
    if refresh_hash is None:
        raise _unauthorized_session()
    session = await db.scalar(
        select(AuthSession)
        .where(AuthSession.refresh_token_hash == refresh_hash)
        .with_for_update()
    )
    if session is None:
        raise _unauthorized_session()
    expiry = session.expires_at
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    user = await db.get(User, session.user_id)
    if (
        session.revoked_at is not None
        or expiry <= datetime.now(UTC)
        or user is None
        or not user.is_active
        or user.username != get_settings().admin_username
    ):
        raise _unauthorized_session()
    next_refresh_token = secrets.token_urlsafe(48)
    session.refresh_token_hash = _refresh_hash(next_refresh_token)
    session.expires_at = utc_now() + timedelta(days=get_settings().browser_session_days)
    await db.commit()
    access_token, expires_in = create_access_token(str(user.id))
    _set_refresh_cookie(response, next_refresh_token)
    return TokenResponse(access_token=access_token, expires_in=expires_in)


@router.post("/logout")
async def logout_browser_session(
    db: DatabaseSession, request: Request, response: Response
) -> dict[str, bool]:
    _check_origin(request)
    refresh_token = request.cookies.get(REFRESH_COOKIE)
    refresh_hash = _refresh_hash(refresh_token) if refresh_token else None
    if refresh_hash:
        session = await db.scalar(
            select(AuthSession)
            .where(AuthSession.refresh_token_hash == refresh_hash)
            .with_for_update()
        )
        if session is not None and session.revoked_at is None:
            session.revoked_at = utc_now()
            await db.commit()
    _clear_refresh_cookie(response)
    return {"signed_out": True}


@router.get("/me", response_model=UserRead)
async def current_user(user: CurrentUser) -> User:
    return user
