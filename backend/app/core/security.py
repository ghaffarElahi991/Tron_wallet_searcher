import base64
import hashlib
from datetime import UTC, datetime, timedelta

import jwt
from cryptography.fernet import Fernet
from pwdlib import PasswordHash

from app.config import get_settings

ALGORITHM = "HS256"
password_hasher = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return password_hasher.verify(password, password_hash)


def create_access_token(subject: str) -> tuple[str, int]:
    settings = get_settings()
    expires_in = settings.access_token_minutes * 60
    now = datetime.now(UTC)
    payload = {
        "sub": subject,
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
        "iss": settings.app_name,
        "aud": settings.app_name,
    }
    token = jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm=ALGORITHM)
    return token, expires_in


def decode_access_token(token: str) -> str:
    settings = get_settings()
    payload = jwt.decode(
        token,
        settings.jwt_secret.get_secret_value(),
        algorithms=[ALGORITHM],
        issuer=settings.app_name,
        audience=settings.app_name,
    )
    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject:
        raise jwt.InvalidTokenError("Token subject is missing.")
    return subject


class SecretBox:
    def __init__(self, secret: str) -> None:
        digest = hashlib.sha256(f"tronforge-result-v1:{secret}".encode()).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(digest))

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        return self._fernet.decrypt(value.encode()).decode()


def get_result_secret_box() -> SecretBox:
    return SecretBox(get_settings().result_encryption_key.get_secret_value())
