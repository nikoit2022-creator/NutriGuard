from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import InvalidTokenError
from app.core.security import create_access_token, create_refresh_token, decode_token
from app.models.enums import Platform
from app.repositories import auth_repository
from app.repositories.health_profile_repository import default_profile_dict, upsert as upsert_profile


async def authenticate_device(
    db: AsyncSession, device_id: str, platform: Platform, app_version: str
) -> dict:
    user, device = await auth_repository.get_or_create_user_and_device(
        db, device_id=device_id, platform=platform, app_version=app_version
    )

    # Ensure a default health profile row exists so GET /health-profile
    # and the warning engine always have a concrete row to read (API
    # Contract 5.7 / 6.8 still tolerate a missing row via defaults, but
    # creating it eagerly keeps behavior simple and predictable).
    await upsert_profile(db, user.id, default_profile_dict())

    access_token = create_access_token(str(user.id), device_id)
    refresh_token = create_refresh_token(str(user.id), device_id)
    refresh_payload = decode_token(refresh_token, expected_type="refresh")

    await auth_repository.store_refresh_token(
        db,
        jti=refresh_payload.jti,
        user_id=user.id,
        device_id=device_id,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=settings.REFRESH_TOKEN_EXPIRE_SECONDS),
    )
    await db.commit()

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "Bearer",
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_SECONDS,
        "user_id": str(user.id),
    }


async def refresh_access_token(db: AsyncSession, refresh_token: str) -> dict:
    payload = decode_token(refresh_token, expected_type="refresh")

    stored = await auth_repository.get_refresh_token(db, payload.jti)
    if stored is None or stored.revoked:
        raise InvalidTokenError("Refresh token has been revoked or does not exist.")
    if stored.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise InvalidTokenError("Refresh token has expired.")

    # Rotate: revoke the used refresh token and issue a fresh pair.
    await auth_repository.revoke_refresh_token(db, payload.jti)

    new_access = create_access_token(payload.sub, payload.device_id)
    new_refresh = create_refresh_token(payload.sub, payload.device_id)
    new_payload = decode_token(new_refresh, expected_type="refresh")

    await auth_repository.store_refresh_token(
        db,
        jti=new_payload.jti,
        user_id=UUID(payload.sub),
        device_id=payload.device_id,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=settings.REFRESH_TOKEN_EXPIRE_SECONDS),
    )
    await db.commit()

    return {
        "access_token": new_access,
        "refresh_token": new_refresh,
        "token_type": "Bearer",
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_SECONDS,
        "user_id": payload.sub,
    }
