"""
DRY-RUN remediation plan for duplicate / degraded E-number ingredient rows.

Never writes: it only SELECTs (`SET TRANSACTION READ ONLY` on PostgreSQL,
always rolled back) and prints a JSON plan describing what a LATER,
separately reviewed step would do. It does not delete, merge, repoint or
re-score anything, and the plan itself never modifies the canonical
target's narrative, provenance, verification status, risk/scoring or
regulatory fields (`fields_modified_on_target` is always empty).

Two row families are planned:
  * CASE_COLLISION -- several rows whose `e_number` differs only in case
    (live example: OCR "E150D" vs curated "E150d").
  * LEGACY_CYRILLIC_E -- rows minted before the Cyrillic-E fold whose text
    carries a literal Cyrillic "Е"+digits E-number but whose `e_number` is
    NULL (live examples: "Киселина: Е300", "Е950", ...).

A suffix letter is never normalized away: E150a, E150b, E150c and E150d are
different identifiers; only letter CASE and script are folded.

Usage (from `nutriguard-backend/`):
    python -m scripts.plan_e_number_remediation --database-url postgresql+asyncpg://...
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections import defaultdict
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.models.enums import TRUSTED_INGREDIENT_SOURCES
from app.models.ingredient import Ingredient
from app.models.ingredient_alias import IngredientAlias
from app.models.ingredient_localization import IngredientLocalization
from app.models.product import Product

NARRATIVE = ("description", "purpose_in_food", "health_concerns", "references", "side_effects", "effect_conditions", "dietary_guidance")
_CYR_E_NUMBER = re.compile(r"(?<![^\W\d_])[Ее][- ]?(\d{3,4}[A-Za-z]?)\b")

_UNIQUE_INDEX_PROPOSAL = [
    "-- ONLY after `remaining_case_collisions` is 0 and the repoint step has been reviewed and applied:",
    "CREATE UNIQUE INDEX CONCURRENTLY ix_ingredients_e_number_upper ON ingredients (upper(e_number)) WHERE e_number IS NOT NULL;",
    "CREATE UNIQUE INDEX CONCURRENTLY ix_ingredients_ins_number_upper ON ingredients (upper(ins_number)) WHERE ins_number IS NOT NULL;",
]


def _empty(v: Any) -> bool:
    return v is None or str(v).strip() == ""


def _enum(v: Any) -> Any:
    return getattr(v, "value", v)


def _rank(ing: Ingredient) -> tuple:
    trusted = ing.source in TRUSTED_INGREDIENT_SOURCES
    populated = sum(1 for f in NARRATIVE if not _empty(getattr(ing, f, "")))
    return (not trusted, -float(ing.confidence or 0), -populated, ing.id)


def _row(ing: Ingredient, aliases: list, locs: list, refs: list[str]) -> dict:
    return {
        "id": ing.id,
        "common_name": ing.common_name,
        "e_number": ing.e_number,
        "ins_number": ing.ins_number,
        "source": _enum(ing.source),
        "verification_status": _enum(ing.verification_status),
        "confidence": float(ing.confidence or 0),
        "non_empty_narrative_fields": [f for f in NARRATIVE if not _empty(getattr(ing, f, ""))],
        "aliases": sorted(a.alias_text for a in aliases),
        "localizations": sorted(loc.language for loc in locs),
        "product_barcodes": sorted(refs),
    }


def _disposition(dup: dict, target: dict) -> dict:
    conflicts_on_localization = sorted(set(dup["localizations"]) & set(target["localizations"]))
    needs_review = bool(dup["non_empty_narrative_fields"])
    return {
        "row": dup,
        "proposed_steps": [
            f"repoint aliases {dup['aliases']} -> {target['id']} (alias_normalized is globally unique; only ingredient_id changes)",
            f"rewrite Product.ingredient_ids {dup['id']} -> {target['id']} for {len(dup['product_barcodes'])} product(s), "
            "dropping a duplicate id when the product already lists the target",
            "leave the source row in place; deleting it is a separate, explicitly approved step after zero references remain",
        ],
        "preserve": {
            "source_row_kept": True,
            "localizations_with_target_conflict_kept_on_source": conflicts_on_localization,
            "non_empty_narrative_needs_manual_review": needs_review,
        },
        "requires_manual_review": needs_review or bool(conflicts_on_localization),
    }


def build_plan(
    ingredients: list[Ingredient],
    aliases: list[IngredientAlias],
    localizations: list[IngredientLocalization],
    products: list[tuple[str, str, str]],  # (barcode, raw_ingredient_text, ingredient_ids)
) -> dict:
    aliases_by: dict[str, list] = defaultdict(list)
    for a in aliases:
        aliases_by[a.ingredient_id].append(a)
    locs_by: dict[str, list] = defaultdict(list)
    for loc in localizations:
        locs_by[loc.ingredient_id].append(loc)
    refs_by: dict[str, list[str]] = defaultdict(list)
    for barcode, _raw, ids in products:
        for i in {x for x in (ids or "").split(",") if x}:
            refs_by[i].append(barcode)

    def summarize(ing: Ingredient) -> dict:
        return _row(ing, aliases_by[ing.id], locs_by[ing.id], refs_by[ing.id])

    groups: dict[str, list[Ingredient]] = defaultdict(list)
    for ing in ingredients:
        if ing.e_number and ing.e_number.strip():
            groups[ing.e_number.strip().upper()].append(ing)

    collisions = []
    for key, rows in sorted(groups.items()):
        if len(rows) < 2:
            continue
        ranked = sorted(rows, key=_rank)
        tie = len(ranked) > 1 and _rank(ranked[0])[:3] == _rank(ranked[1])[:3]
        target = summarize(ranked[0])
        collisions.append(
            {
                "kind": "CASE_COLLISION",
                "normalized_e_number": key,
                "canonical_target": target,
                "target_selection": "trusted source, then confidence, then populated narrative fields, then id",
                "needs_manual_decision": tie,
                "fields_modified_on_target": [],
                "duplicates": [_disposition(summarize(r), target) for r in ranked[1:]],
            }
        )

    legacy = []
    for ing in ingredients:
        if ing.e_number and ing.e_number.strip():
            continue
        texts = [ing.common_name or ""] + [a.alias_text for a in aliases_by[ing.id]]
        found = {m.group(1).upper() for t in texts for m in _CYR_E_NUMBER.finditer(t)}
        if len(found) != 1:
            continue
        key = "E" + next(iter(found))
        candidates = sorted(groups.get(key, []), key=_rank)
        target = summarize(candidates[0]) if candidates else None
        entry: dict = {
            "kind": "LEGACY_CYRILLIC_E",
            "normalized_e_number": key,
            "canonical_target": target,
            "fields_modified_on_target": [],
            "note": None if target else "no catalogue row for this E-number yet; nothing to repoint to (content gap, not a duplicate)",
        }
        if target:
            entry["duplicates"] = [_disposition(summarize(ing), target)]
        else:
            entry["duplicates"] = [{"row": summarize(ing), "proposed_steps": [], "requires_manual_review": False}]
        legacy.append(entry)

    ins_groups: dict[str, list[str]] = defaultdict(list)
    for ing in ingredients:
        if ing.ins_number and ing.ins_number.strip():
            ins_groups[ing.ins_number.strip().upper()].append(ing.id)
    return {
        "dry_run": True,
        "case_collisions": collisions,
        "legacy_cyrillic_e_rows": legacy,
        "ins_number_case_collisions": {k: v for k, v in sorted(ins_groups.items()) if len(v) > 1},
        "remaining_case_collisions": len(collisions),
        "unique_index_proposal_not_applied": _UNIQUE_INDEX_PROPOSAL,
    }


async def _load(session: AsyncSession) -> dict:
    ings = (await session.execute(select(Ingredient))).scalars().all()
    aliases = (await session.execute(select(IngredientAlias))).scalars().all()
    locs = (await session.execute(select(IngredientLocalization))).scalars().all()
    prods = [
        (b, r or "", i or "")
        for b, r, i in (
            await session.execute(select(Product.barcode, Product.raw_ingredient_text, Product.ingredient_ids))
        ).all()
    ]
    return build_plan(list(ings), list(aliases), list(locs), prods)


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args(argv)
    database_url = args.database_url or settings.DATABASE_URL
    engine = create_async_engine(database_url, future=True)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    print(f"[plan] DRY RUN, read-only, against: {database_url.split('@')[-1]}", file=sys.stderr)
    try:
        async with factory() as session:
            if engine.dialect.name == "postgresql":
                await session.execute(text("SET TRANSACTION READ ONLY"))
            plan = await _load(session)
            await session.rollback()
    finally:
        await engine.dispose()
    print(json.dumps(plan, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
