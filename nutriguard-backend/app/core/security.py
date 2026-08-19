"""
Device-token authentication (API Contract section 3.2).

The Android client has no login screen. Instead, it registers an
anonymous device (persisted UUID) once and receives a short-lived JWT
access token plus a long-lived refresh token. All subsequent requests
carry `Authorization: Bearer <access_token>`.

This module is intentionally decoupled from any specific user-account
model so that a full email/password or OAuth flow can be added later
(API Contract section 3.3) without breaking the token format consumed
by protected endpoints.
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from pydantic import BaseModel

from app.core.config import settings
from app.core.exceptions import InvalidTokenError

ALGORITHM = settings.JWT_ALGORITHM


class TokenPayload(BaseModel):
    sub: str  # user_id
    device_id: str
    type: str  # "access" | "refresh"
    exp: int
    jti: str


def _create_token(user_id: str, device_id: str, token_type: str, expires_in: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "device_id": device_id,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=ALGORITHM)


def create_access_token(user_id: str, device_id: str) -> str:
    return _create_token(user_id, device_id, "access", settings.ACCESS_TOKEN_EXPIRE_SECONDS)


def create_refresh_token(user_id: str, device_id: str) -> str:
    return _create_token(user_id, device_id, "refresh", settings.REFRESH_TOKEN_EXPIRE_SECONDS)


def decode_token(token: str, expected_type: Optional[str] = None) -> TokenPayload:
    try:
        raw = jwt.decode(token, settings.JWT_SECRET, algorithms=[ALGORITHM])
        payload = TokenPayload(**raw)
    except (JWTError, ValueError) as exc:
        raise InvalidTokenError("Token is invalid, malformed, or expired.") from exc

    if expected_type and payload.type != expected_type:
        raise InvalidTokenError(f"Expected a {expected_type} token, got {payload.type}.")

    return payload
