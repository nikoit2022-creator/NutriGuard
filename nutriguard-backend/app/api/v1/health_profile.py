from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_id
from app.core.rate_limit import PROFILE_RATE, limiter
from app.database.session import get_db
from app.repositories import health_profile_repository
from app.schemas.health_profile import HealthProfileIn, HealthProfileOut

router = APIRouter(prefix="/health-profile", tags=["health-profile"])


@router.get("", response_model=HealthProfileOut)
@limiter.limit(PROFILE_RATE)
async def get_health_profile(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> HealthProfileOut:
    """API Contract 6.8 — returns defaults (not 404) when no row exists yet."""
    profile = await health_profile_repository.get_for_user(db, user_id)
    if profile is None:
        return HealthProfileOut(**health_profile_repository.default_profile_dict())
    return HealthProfileOut.model_validate(profile)


@router.put("", response_model=HealthProfileOut)
@limiter.limit(PROFILE_RATE)
async def put_health_profile(
    request: Request,
    body: HealthProfileIn,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> HealthProfileOut:
    """API Contract 6.9 — upsert semantics (Room OnConflictStrategy.REPLACE)."""
    saved = await health_profile_repository.upsert(db, user_id, body.model_dump())
    await db.commit()
    return HealthProfileOut.model_validate(saved)
