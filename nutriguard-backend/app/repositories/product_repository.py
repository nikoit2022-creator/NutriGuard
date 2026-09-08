from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import Product


async def get_by_barcode(db: AsyncSession, barcode: str) -> Product | None:
    """Mirrors ProductDao.getProductByBarcode"""
    stmt = select(Product).where(Product.barcode == barcode).limit(1)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


_LIKE_ESCAPE_CHAR = "\\"


def _escape_like(value: str) -> str:
    """Escapes `%`/`_`/the escape char itself so `value` is matched as a
    LITERAL substring by a `LIKE ... ESCAPE '\\'` pattern -- SQL `LIKE`
    otherwise treats a bare `_` as "any single character" and `%` as
    "any run of characters", which real `Ingredient.id`s routinely
    contain (e.g. "e300_ascorbic_acid"): an escaped `_id` could
    otherwise wildcard-match a DIFFERENT id that merely has the same
    length/shape (review fix -- the original unescaped version was not
    actually boundary-safe as its own docstring claimed)."""
    return (
        value.replace(_LIKE_ESCAPE_CHAR, _LIKE_ESCAPE_CHAR * 2)
        .replace("%", f"{_LIKE_ESCAPE_CHAR}%")
        .replace("_", f"{_LIKE_ESCAPE_CHAR}_")
    )


async def has_ingredient_reference(db: AsyncSession, ingredient_id: str) -> bool:
    """Whether any `Product.ingredient_ids` (a comma-separated list of
    `Ingredient.id`s -- see `food_analysis._ingredient_ids_string`,
    there is no FK/junction table) already includes this exact id --
    used only to decide whether deleting a duplicate `Ingredient` row
    is provably safe (see
    `ingredient_catalog._reconcile_official_identifier_conflict`, PR
    #13 review: "canonical identity precedence"). Boundary-safe -- never
    a false positive from one id being a plain substring of another
    (e.g. "e300" inside "e3001"), NOR from `LIKE`'s own `_`/`%`
    wildcards matching an unrelated id that merely has the same
    shape (`_escape_like`): matches `ingredient_id` as a whole,
    LITERAL, comma-delimited token, not a bare substring or wildcard
    pattern, exactly like a real reader of this column
    (`food_analysis.fetch_ingredients_for_product`'s own `.split(",")`)
    would."""
    escaped = _escape_like(ingredient_id)
    stmt = (
        select(Product.barcode)
        .where(
            or_(
                Product.ingredient_ids == ingredient_id,
                Product.ingredient_ids.like(f"{escaped},%", escape=_LIKE_ESCAPE_CHAR),
                Product.ingredient_ids.like(f"%,{escaped}", escape=_LIKE_ESCAPE_CHAR),
                Product.ingredient_ids.like(f"%,{escaped},%", escape=_LIKE_ESCAPE_CHAR),
            )
        )
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none() is not None


async def search(
    db: AsyncSession, *, query: str | None, page: int, page_size: int
) -> tuple[list[Product], int]:
    """Mirrors ProductDao.getAllProducts / searchProducts"""
    stmt = select(Product)
    count_stmt = select(func.count()).select_from(Product)

    if query:
        like = f"%{query}%"
        clause = or_(Product.product_name.ilike(like), Product.brand.ilike(like))
        stmt = stmt.where(clause)
        count_stmt = count_stmt.where(clause)

    stmt = stmt.order_by(Product.timestamp.desc()).offset((page - 1) * page_size).limit(page_size)

    total = (await db.execute(count_stmt)).scalar_one()
    rows = (await db.execute(stmt)).scalars().all()
    return list(rows), total


async def upsert(db: AsyncSession, product: Product) -> Product:
    """Mirrors ProductDao.insertProduct (Room OnConflictStrategy.REPLACE)."""
    merged = await db.merge(product)
    await db.flush()
    return merged


async def get_by_barcode_or_aliases(
    db: AsyncSession, raw: str, alias_keys: list[str], *, for_update: bool = False
) -> Product | None:
    """
    Barcode-scan lookup used by `food_analysis.analyze_barcode`: tries
    the exact string the client sent first (matches a legacy/pre-
    canonicalization row, or a non-GTIN synthetic `ocr_.../img_...` id
    verbatim), then every plausible alias key for the same physical
    barcode (`alias_keys` — see `barcode_validation.alias_keys`, which
    computes these; this function takes a plain list rather than
    importing that service module itself, per the project's layering
    rule that repositories don't depend on services).

    This is what makes "036000291452" (UPC-A), "0036000291452" (its
    EAN-13-zero-padded equivalent), and "036-000-291452" (same digits,
    dashes) all resolve to the same row regardless of which one was
    scanned first, AND regardless of which one a pre-existing/legacy
    row happens to be stored under — new rows are always persisted
    under the canonical GTIN-13 (`alias_keys[0]`, see `insert_new`), but
    a row from before that convention (or written by another path)
    might not be, and must still be found rather than duplicated.
    """
    async def _get(key: str) -> Product | None:
        stmt = select(Product).where(Product.barcode == key).limit(1)
        if for_update:
            stmt = stmt.with_for_update()
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    product = await _get(raw)
    if product is not None:
        return product
    for key in alias_keys:
        if key == raw:
            continue
        product = await _get(key)
        if product is not None:
            return product
    return None


async def insert_new(db: AsyncSession, product: Product) -> Product | None:
    """
    Attempts to INSERT a brand-new product row (used only by barcode
    discovery — see app/services/food_analysis.py). Returns `None` if a
    row for this barcode already exists — e.g. a concurrent discovery
    request for the same barcode (or an equivalent representation, once
    canonicalized) won the race. Makes no merge/overwrite decision
    itself (no business logic, per the repository layer's job); the
    caller re-fetches with `get_by_barcode` and decides how to proceed.

    Uses a SAVEPOINT (`begin_nested`) rather than a full `db.rollback()`
    so a conflict here can never discard other work already flushed
    earlier in the same transaction (verified against both SQLite and
    PostgreSQL) — see `product_source_repository.record_discovery` for
    the same pattern applied to provenance rows, which is where this
    actually matters in practice (barcode inserts happen once per
    request, provenance inserts happen in a loop).
    """
    try:
        async with db.begin_nested():
            db.add(product)
            await db.flush()
        return product
    except IntegrityError:
        return None
