from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import RiskLevel
from app.models.ingredient import Ingredient


async def get_by_id(db: AsyncSession, ingredient_id: str) -> Ingredient | None:
    return await db.get(Ingredient, ingredient_id)


async def get_by_official_identifier(
    db: AsyncSession, *, e_number: str | None = None, ins_number: str | None = None, cas_number: str | None = None
) -> Ingredient | None:
    """Strongest canonical-identity lookup (task requirement 2): an
    official regulatory identifier, when one was actually recognized in
    the OCR text, always wins over a name-based match. Only ever
    queries the identifiers actually supplied -- a bare OCR name never
    reaches this function's `e_number`/`ins_number`/`cas_number`
    parameters (see `ingredient_catalog`), so a coincidental name
    collision can never be misread as an identifier match."""
    if not e_number and not ins_number and not cas_number:
        return None
    clauses = []
    if e_number:
        clauses.append(Ingredient.e_number == e_number)
    if ins_number:
        clauses.append(Ingredient.ins_number == ins_number)
    if cas_number:
        clauses.append(Ingredient.cas_number == cas_number)
    stmt = select(Ingredient).where(or_(*clauses)).limit(1)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def insert_new(db: AsyncSession, ingredient: Ingredient) -> Ingredient | None:
    """Attempts to INSERT a brand-new ingredient row. Returns `None` on
    a primary-key conflict (another concurrent scan already created
    this exact id -- see `ingredient_catalog.get_or_create_catalog_ingredient`,
    which re-fetches and reuses the winner rather than erroring); never
    called for a row that might collide with an existing curated one by
    normalized name/alias, since the caller always tries the alias
    lookup first.

    Same SAVEPOINT (`begin_nested`) pattern as
    `product_repository.insert_new`/`product_source_repository.record_discovery`
    -- a conflict here must only undo THIS row's insert, never any other
    work already flushed earlier in the same request/transaction.
    """
    try:
        async with db.begin_nested():
            db.add(ingredient)
            await db.flush()
        return ingredient
    except IntegrityError:
        return None


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
