import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.health_profile import UserHealthProfileModel


async def get_for_user(db: AsyncSession, user_id: uuid.UUID) -> UserHealthProfileModel | None:
    stmt = select(UserHealthProfileModel).where(UserHealthProfileModel.user_id == user_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def upsert(
    db: AsyncSession, user_id: uuid.UUID, data: dict
) -> UserHealthProfileModel:
    """Mirrors UserHealthProfileDao.saveProfile (Room OnConflictStrategy.REPLACE)."""
    existing = await get_for_user(db, user_id)
    if existing:
        for key, value in data.items():
            setattr(existing, key, value)
        await db.flush()
        return existing

    profile = UserHealthProfileModel(user_id=user_id, **data)
    db.add(profile)
    await db.flush()
    return profile


def default_profile_dict() -> dict:
    """Matches the client's `UserHealthProfile()` default-constructor fallback
    used when no row exists yet (API Contract 6.8)."""
    return {
        "has_diabetes": False,
        "has_hypertension": False,
        "has_kidney_disease": False,
        "has_gout": False,
        "is_pregnant": False,
        "for_children": False,
        "has_high_cholesterol": False,
        "avoid_gluten": False,
        "avoid_lactose": False,
        "avoid_peanuts": False,
        "avoid_soy": False,
        "avoid_tree_nuts": False,
        "require_vegan": False,
        "require_vegetarian": False,
        "require_halal": False,
        "require_kosher": False,
    }
