"""
Repository for `IngredientAlias` -- the reverse-lookup index from any
known name/spelling variant to its one canonical `Ingredient` row (see
`app.services.ingredient_catalog`). No business logic here (matching
strategy, when to learn a new alias, confidence/source priority all
live in the service layer) -- just race-safe persistence.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import IngredientSource
from app.models.ingredient_alias import IngredientAlias


async def get_by_normalized(db: AsyncSession, alias_normalized: str) -> IngredientAlias | None:
    stmt = select(IngredientAlias).where(IngredientAlias.alias_normalized == alias_normalized).limit(1)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def list_for_ingredient(db: AsyncSession, ingredient_id: str) -> list[IngredientAlias]:
    stmt = select(IngredientAlias).where(IngredientAlias.ingredient_id == ingredient_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_or_create(
    db: AsyncSession,
    *,
    ingredient_id: str,
    alias_text: str,
    alias_normalized: str,
    language: str | None,
    source: IngredientSource,
) -> IngredientAlias:
    """Idempotent, race-safe insert: two concurrent scans learning the
    same new alias for the same (or, having independently created two
    different minimal ingredient rows for the same text, two DIFFERENT)
    ingredient id must converge on exactly one alias row rather than
    erroring or duplicating -- mirrors
    `product_source_repository.record_discovery`'s SAVEPOINT pattern
    (`begin_nested`), proven safe against both SQLite (this test suite)
    and PostgreSQL (production) for the same shape of race.

    Returns the EXISTING row unchanged if one already exists for this
    normalized alias -- even if it points at a different `ingredient_id`
    than requested here (see `ingredient_catalog.get_or_create_catalog_ingredient`,
    which re-resolves against whatever this call actually returns rather
    than assuming its own value won).
    """
    existing = await get_by_normalized(db, alias_normalized)
    if existing is not None:
        return existing

    row = IngredientAlias(
        id=uuid.uuid4(),
        ingredient_id=ingredient_id,
        alias_text=alias_text,
        alias_normalized=alias_normalized,
        language=language,
        source=source,
    )
    try:
        async with db.begin_nested():
            db.add(row)
            await db.flush()
        return row
    except IntegrityError:
        existing = await get_by_normalized(db, alias_normalized)
        if existing is not None:
            return existing
        raise
