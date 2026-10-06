"""
DRY-RUN, read-only inventory of historical error-text "ingredients".

Finds catalogue rows whose name (or an alias) is one of the known provider-
failure placeholder sentences (`CandidateFlag.PLACEHOLDER_TEXT`, see
`app.services.ingredient_candidate_flags`) and lists everything that points
at them: aliases, localizations, candidate-queue rows and products (with
`created_at`/`source` so a reviewer can judge whether a row predates the
prevention in `food_analysis._run_label_image_pipeline`).

It does NOT delete, repair, repoint or otherwise write: SELECT only,
`SET TRANSACTION READ ONLY` on PostgreSQL, always rolled back. Whether any
CURRENT scan still creates such rows is not decided here; the prevention
test `test_a_failed_label_scan_no_longer_puts_error_text_into_the_catalog`
and this inventory's product dates are the evidence to weigh.

Usage (from `nutriguard-backend/`):
    python -m scripts.inventory_error_text_ingredients --database-url postgresql+asyncpg://...
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.models.ingredient import Ingredient
from app.models.ingredient_alias import IngredientAlias
from app.models.ingredient_candidate import IngredientCandidate
from app.models.ingredient_localization import IngredientLocalization
from app.models.product import Product
from app.services.ingredient_candidate_flags import CandidateFlag, classify_token


def _is_error_text(value: str | None) -> bool:
    return bool(value) and CandidateFlag.PLACEHOLDER_TEXT in classify_token(value)


async def _inventory(session: AsyncSession) -> dict:
    ings = [i for i in (await session.execute(select(Ingredient))).scalars().all() if _is_error_text(i.common_name)]
    aliases = (await session.execute(select(IngredientAlias))).scalars().all()
    alias_error_rows = [a for a in aliases if _is_error_text(a.alias_text)]
    ids = {i.id for i in ings} | {a.ingredient_id for a in alias_error_rows}
    locs = (await session.execute(select(IngredientLocalization))).scalars().all()
    cands = (await session.execute(select(IngredientCandidate))).scalars().all()
    products = (
        await session.execute(
            select(
                Product.barcode, Product.ingredient_ids, Product.raw_ingredient_text, Product.source,
                Product.created_at, Product.has_verified_ingredients,
            )
        )
    ).all()

    by_id = {i.id: i for i in (await session.execute(select(Ingredient).where(Ingredient.id.in_(ids)))).scalars().all()} if ids else {}
    refs: dict[str, list[dict]] = defaultdict(list)
    for barcode, ingredient_ids, raw, source, created_at, verified in products:
        for ingredient_id in {x for x in (ingredient_ids or "").split(",") if x}:
            if ingredient_id in ids:
                refs[ingredient_id].append(
                    {
                        "barcode": barcode,
                        "source": source,
                        "created_at": created_at.isoformat() if created_at else None,
                        "has_verified_ingredients": bool(verified),
                        "stored_raw_text_is_empty": not (raw or "").strip(),
                    }
                )
    rows = []
    for ingredient_id in sorted(ids):
        ing = by_id.get(ingredient_id)
        rows.append(
            {
                "id": ingredient_id,
                "common_name": ing.common_name if ing else None,
                "source": getattr(getattr(ing, "source", None), "value", None),
                "verification_status": getattr(getattr(ing, "verification_status", None), "value", None),
                "retrieved_at": ing.retrieved_at.isoformat() if ing and ing.retrieved_at else None,
                "aliases": sorted(a.alias_text for a in aliases if a.ingredient_id == ingredient_id),
                "localization_languages": sorted(loc.language for loc in locs if loc.ingredient_id == ingredient_id),
                "candidate_queue_rows": [
                    {"id": c.id, "status": c.status, "encounter_count": c.encounter_count, "flags": c.flags}
                    for c in cands
                    if c.ingredient_id == ingredient_id
                ],
                "product_references": sorted(refs.get(ingredient_id, []), key=lambda r: r["barcode"]),
            }
        )
    orphan_candidates = [
        {"id": c.id, "display_name": c.display_name, "status": c.status, "encounter_count": c.encounter_count,
         "ingredient_id": c.ingredient_id}
        for c in cands
        if _is_error_text(c.display_name) and c.ingredient_id not in ids
    ]
    dates = [r["created_at"] for row in rows for r in row["product_references"] if r["created_at"]]
    return {
        "dry_run": True,
        "note": "Inventory only; no deletion or repair is proposed or performed. Current contamination is not asserted.",
        "error_text_rows": len(rows),
        "distinct_products_referencing": len({r["barcode"] for row in rows for r in row["product_references"]}),
        "total_product_references": sum(len(row["product_references"]) for row in rows),
        "oldest_referencing_product_created_at": min(dates) if dates else None,
        "newest_referencing_product_created_at": max(dates) if dates else None,
        "candidate_rows_without_a_catalogue_row": orphan_candidates,
        "rows": rows,
    }


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args(argv)
    database_url = args.database_url or settings.DATABASE_URL
    engine = create_async_engine(database_url, future=True)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    print(f"[inventory] DRY RUN, read-only, against: {database_url.split('@')[-1]}", file=sys.stderr)
    try:
        async with factory() as session:
            if engine.dialect.name == "postgresql":
                await session.execute(text("SET TRANSACTION READ ONLY"))
            report = await _inventory(session)
            await session.rollback()
    finally:
        await engine.dispose()
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
