from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import RiskLevel
from app.models.ingredient import Ingredient


async def get_by_id_or_e_number(db: AsyncSession, identifier: str) -> Ingredient | None:
    """Mirrors IngredientDao.getIngredientByIdOrEnum: WHERE id = :id OR eNumber = :id"""
    stmt = select(Ingredient).where(
        or_(Ingredient.id == identifier, Ingredient.e_number == identifier)
    ).limit(1)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_all(db: AsyncSession) -> list[Ingredient]:
    stmt = select(Ingredient).order_by(Ingredient.common_name.asc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def search(
    db: AsyncSession,
    *,
    query: str | None,
    risk_level: RiskLevel | None,
    page: int,
    page_size: int,
) -> tuple[list[Ingredient], int]:
    stmt = select(Ingredient)
    count_stmt = select(func.count()).select_from(Ingredient)

    if query:
        like = f"%{query}%"
        clause = or_(
            Ingredient.common_name.ilike(like),
            Ingredient.scientific_name.ilike(like),
            Ingredient.e_number.ilike(like),
        )
        stmt = stmt.where(clause)
        count_stmt = count_stmt.where(clause)

    if risk_level:
        stmt = stmt.where(Ingredient.risk_level == risk_level)
        count_stmt = count_stmt.where(Ingredient.risk_level == risk_level)

    stmt = stmt.order_by(Ingredient.common_name.asc()).offset((page - 1) * page_size).limit(page_size)

    total = (await db.execute(count_stmt)).scalar_one()
    rows = (await db.execute(stmt)).scalars().all()
    return list(rows), total


async def get_many_by_ids(db: AsyncSession, ids: list[str]) -> list[Ingredient]:
    if not ids:
        return []
    stmt = select(Ingredient).where(Ingredient.id.in_(ids))
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def upsert(db: AsyncSession, ingredient: Ingredient) -> Ingredient:
    merged = await db.merge(ingredient)
    return merged


async def count(db: AsyncSession) -> int:
    result = await db.execute(select(func.count()).select_from(Ingredient))
    return result.scalar_one()
