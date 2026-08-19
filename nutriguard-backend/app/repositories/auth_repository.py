import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth import Device, RefreshToken, User
from app.models.enums import Platform


async def get_device_by_device_id(db: AsyncSession, device_id: str) -> Device | None:
    stmt = select(Device).where(Device.device_id == device_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_or_create_user_and_device(
    db: AsyncSession, device_id: str, platform: Platform, app_version: str
) -> tuple[User, Device]:
    """
    Implements the anonymous device-registration flow from API Contract 3.2:
    a first-seen `device_id` creates a new logical User + Device pair; a
    known `device_id` is simply re-authenticated (idempotent).
    """
    device = await get_device_by_device_id(db, device_id)
    if device is not None:
        device.last_seen_at = datetime.now(timezone.utc)
        device.app_version = app_version or device.app_version
        user = await db.get(User, device.user_id)
        await db.flush()
        return user, device

    user = User()
    db.add(user)
    await db.flush()

    device = Device(device_id=device_id, user_id=user.id, platform=platform, app_version=app_version)
    db.add(device)
    await db.flush()
    return user, device


async def store_refresh_token(
    db: AsyncSession, *, jti: str, user_id: uuid.UUID, device_id: str, expires_at: datetime
) -> RefreshToken:
    token = RefreshToken(jti=jti, user_id=user_id, device_id=device_id, expires_at=expires_at)
    db.add(token)
    await db.flush()
    return token


async def get_refresh_token(db: AsyncSession, jti: str) -> RefreshToken | None:
    stmt = select(RefreshToken).where(RefreshToken.jti == jti)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def revoke_refresh_token(db: AsyncSession, jti: str) -> None:
    await db.execute(update(RefreshToken).where(RefreshToken.jti == jti).values(revoked=True))
