from sqlalchemy import select, update

from app.config import get_settings
from app.core.security import hash_password, verify_password
from app.database import SessionLocal
from app.models import AuthSession, User, utc_now


async def ensure_single_user() -> None:
    """Create the configured operator account and synchronize its password."""
    settings = get_settings()
    password = settings.admin_password.get_secret_value()
    if len(password) < 12:
        raise RuntimeError("TRONFORGE_ADMIN_PASSWORD must contain at least 12 characters.")

    async with SessionLocal() as db:
        user = await db.scalar(select(User).where(User.username == settings.admin_username))
        if user is None:
            db.add(
                User(
                    username=settings.admin_username,
                    password_hash=hash_password(password),
                    is_active=True,
                )
            )
        else:
            user.is_active = True
            if not verify_password(password, user.password_hash):
                user.password_hash = hash_password(password)
                await db.execute(
                    update(AuthSession)
                    .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
                    .values(revoked_at=utc_now())
                )
        await db.commit()
