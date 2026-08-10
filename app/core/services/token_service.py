import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Union

from jose import JWTError, jwt

from app.core.config import settings


class TokenService:
    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def create_access_token(
        subject: Union[str, Any],
        roles: list[str],
        expires_delta: Optional[timedelta] = None,
    ) -> str:
        expire = datetime.now(timezone.utc) + (
            expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        )
        to_encode = {
            "exp": expire,
            "sub": str(subject),
            "roles": roles,
            "type": "access",
        }
        return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.ALGORITHM)

    @staticmethod
    def create_refresh_token(
        subject: Union[str, Any], expires_delta: Optional[timedelta] = None
    ) -> str:
        expire = datetime.now(timezone.utc) + (
            expires_delta or timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
        )
        to_encode = {
            "exp": expire,
            "sub": str(subject),
            "jti": str(uuid.uuid4()),
            "type": "refresh",
        }
        return jwt.encode(
            to_encode, settings.JWT_REFRESH_SECRET, algorithm=settings.ALGORITHM
        )

    @staticmethod
    def decode_token(token: str, is_refresh: bool = False) -> Optional[dict[str, Any]]:
        secret = settings.JWT_REFRESH_SECRET if is_refresh else settings.JWT_SECRET
        try:
            return jwt.decode(token, secret, algorithms=[settings.ALGORITHM])
        except JWTError:
            return None
