"""
DB access for `IngredientWikipediaLookup` provenance/cache rows. No
lookup-eligibility, caching, or merge decisions live here (see
`app.services.ingredient_wikipedia_enrichment`) -- this module only
knows how to read and insert-or-refresh the one row for a given
ingredient.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ingredient_wikipedia_lookup import IngredientWikipediaLookup


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def get_by_ingredient_id(db: AsyncSession, ingredient_id: str) -> IngredientWikipediaLookup | None:
    stmt = select(IngredientWikipediaLookup).where(IngredientWikipediaLookup.ingredient_id == ingredient_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


def _apply_fields(
    row: IngredientWikipediaLookup,
    *,
    query_text: str,
    match_status: str,
    review_reason: str | None,
    matched_page_title: str | None,
    source_url: str | None,
    extracted_summary: str | None,
    fields_populated_json: str | None,
    confidence: float,
    retrieved_at: datetime | None,
    error_detail: str | None,
    now: datetime,
) -> None:
    row.query_text = query_text
    row.match_status = match_status
    row.review_reason = review_reason
    row.matched_page_title = matched_page_title
    row.source_url = source_url
    row.extracted_summary = extracted_summary
    row.fields_populated_json = fields_populated_json
    row.confidence = confidence
    row.retrieved_at = retrieved_at
    row.attempted_at = now
    row.error_detail = error_detail


async def record_lookup(
    db: AsyncSession,
    *,
    ingredient_id: str,
    query_text: str,
    match_status: str,
    review_reason: str | None = None,
    matched_page_title: str | None = None,
    source_url: str | None = None,
    extracted_summary: str | None = None,
    fields_populated_json: str | None = None,
    confidence: float = 0.0,
    retrieved_at: datetime | None = None,
    error_detail: str | None = None,
) -> IngredientWikipediaLookup:
    """Insert-or-refresh the one row for `ingredient_id` (dedup, same
    SAVEPOINT convention as `ingredient_alias_repository.get_or_create`/
    `product_source_repository.record_discovery`): a repeated lookup
    attempt for the same ingredient updates this row in place rather
    than accumulating duplicates. `attempted_at` is always advanced to
    now, success or failure -- it is what the negative-cache window
    gates on."""
    now = _utcnow()
    existing = await get_by_ingredient_id(db, ingredient_id)
    if existing is not None:
        _apply_fields(
            existing,
            query_text=query_text, match_status=match_status, review_reason=review_reason,
            matched_page_title=matched_page_title, source_url=source_url, extracted_summary=extracted_summary,
            fields_populated_json=fields_populated_json, confidence=confidence, retrieved_at=retrieved_at,
            error_detail=error_detail, now=now,
        )
        await db.flush()
        return existing

    row = IngredientWikipediaLookup(id=uuid.uuid4(), ingredient_id=ingredient_id)
    _apply_fields(
        row,
        query_text=query_text, match_status=match_status, review_reason=review_reason,
        matched_page_title=matched_page_title, source_url=source_url, extracted_summary=extracted_summary,
        fields_populated_json=fields_populated_json, confidence=confidence, retrieved_at=retrieved_at,
        error_detail=error_detail, now=now,
    )
    try:
        async with db.begin_nested():
            db.add(row)
            await db.flush()
        return row
    except IntegrityError:
        existing = await get_by_ingredient_id(db, ingredient_id)
        if existing is not None:
            return existing
        raise
