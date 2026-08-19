from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limit import AUTH_RATE, limiter
from app.database.session import get_db
from app.schemas.auth import DeviceAuthRequest, RefreshRequest, TokenResponse
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/device", response_model=TokenResponse)
@limiter.limit(AUTH_RATE)
async def auth_device(
    request: Request, body: DeviceAuthRequest, db: AsyncSession = Depends(get_db)
) -> TokenResponse:
    """API Contract 3.2 — anonymous device registration/authentication."""
    result = await auth_service.authenticate_device(
        db, device_id=body.device_id, platform=body.platform, app_version=body.app_version
    )
    return TokenResponse(**result)


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit(AUTH_RATE)
async def refresh(
    request: Request, body: RefreshRequest, db: AsyncSession = Depends(get_db)
) -> TokenResponse:
    result = await auth_service.refresh_access_token(db, body.refresh_token)
    return TokenResponse(**result)
