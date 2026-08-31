from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import Product


async def get_by_barcode(db: AsyncSession, barcode: str) -> Product | None:
    """Mirrors ProductDao.getProductByBarcode"""
    stmt = select(Product).where(Product.barcode == barcode).limit(1)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


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


async def insert_new(db: AsyncSession, product: Product) -> Product | None:
    """
    Attempts to INSERT a brand-new product row (used only by barcode
    discovery — see app/services/food_analysis.py). Returns `None` (after
    rolling back cleanly) if a row for this barcode already exists —
    e.g. a concurrent discovery request for the same barcode won the
    race. Makes no merge/overwrite decision itself (no business logic,
    per the repository layer's job); the caller re-fetches with
    `get_by_barcode` and decides how to proceed.
    """
    db.add(product)
    try:
        await db.flush()
        return product
    except IntegrityError:
        await db.rollback()
        return None
