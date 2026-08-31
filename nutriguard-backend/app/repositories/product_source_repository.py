"""
DB access for `ProductSource` provenance rows. No merge/trust decisions
live here (see app/services/food_analysis.py and barcode_discovery.py
for those) — this module only knows how to read and insert-or-refresh
one (barcode, provider) row.
"""
import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.barcode_providers.base import ProviderProductResult
from app.models.product_source import ProductSource


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def get_by_barcode_and_provider(db: AsyncSession, barcode: str, provider: str) -> ProductSource | None:
    stmt = select(ProductSource).where(
        ProductSource.barcode == barcode, ProductSource.provider == provider
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def list_for_barcode(db: AsyncSession, barcode: str) -> list[ProductSource]:
    stmt = select(ProductSource).where(ProductSource.barcode == barcode).order_by(
        ProductSource.discovered_at.desc()
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


def _nutrition_json(result: ProviderProductResult) -> str:
    n = result.nutrition
    return json.dumps(
        {
            "sugarGrams": n.sugar_grams,
            "sodiumMg": n.sodium_mg,
            "saturatedFatGrams": n.saturated_fat_grams,
            "hasArtificialSweeteners": n.has_artificial_sweeteners,
            "hasPreservatives": n.has_preservatives,
            "novaGroup": n.nova_group,
        }
    )


def _apply_fields(row: ProductSource, result: ProviderProductResult, *, confidence: float, is_conflicting: bool,
                   used_for_persisted_product: bool) -> None:
    row.source_url = result.source_url
    row.confidence = confidence
    row.external_last_modified = result.external_last_modified
    row.last_verified_at = _utcnow()
    row.product_name = result.product_name
    row.brand = result.brand
    row.image_url = result.image_url
    row.raw_ingredient_text = result.raw_ingredient_text
    row.normalized_ingredients_json = (
        json.dumps(result.ingredients_tokens[:50]) if result.ingredients_tokens else None
    )
    row.nutrition_json = _nutrition_json(result)
    row.allergens = ", ".join(result.allergens) if result.allergens else None
    row.language = result.language
    row.external_id = result.external_id
    row.raw_metadata_json = json.dumps(result.raw_metadata) if result.raw_metadata else None
    row.is_conflicting = is_conflicting or row.is_conflicting
    row.used_for_persisted_product = used_for_persisted_product


async def record_discovery(
    db: AsyncSession,
    *,
    barcode: str,
    result: ProviderProductResult,
    confidence: float,
    is_conflicting: bool,
    used_for_persisted_product: bool,
) -> ProductSource:
    """
    Insert-or-refresh the (barcode, provider) provenance row. Repeated
    discovery of the same barcode from the same provider updates this
    one row in place (dedup) instead of accumulating duplicates; a
    different provider for the same barcode keeps its own row, so
    disagreements between sources stay visible (see `is_conflicting`).

    MUST be called only after the corresponding `Product` write has
    already been committed (see food_analysis.py) — an IntegrityError
    here is handled with its own rollback, which would otherwise also
    discard an uncommitted Product insert earlier in the same
    transaction.
    """
    existing = await get_by_barcode_and_provider(db, barcode, result.provider)
    if existing is not None:
        _apply_fields(
            existing, result, confidence=confidence, is_conflicting=is_conflicting,
            used_for_persisted_product=used_for_persisted_product,
        )
        await db.flush()
        return existing

    row = ProductSource(barcode=barcode, provider=result.provider, discovered_at=_utcnow())
    _apply_fields(
        row, result, confidence=confidence, is_conflicting=is_conflicting,
        used_for_persisted_product=used_for_persisted_product,
    )
    db.add(row)
    try:
        await db.flush()
        return row
    except IntegrityError:
        await db.rollback()
        existing = await get_by_barcode_and_provider(db, barcode, result.provider)
        if existing is not None:
            return existing
        raise
