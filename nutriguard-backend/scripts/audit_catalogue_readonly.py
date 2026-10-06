"""
Read-only catalogue audit (ingredient identity, unresolved product
references, E-number duplicates, priority-ingredient completeness).

Reads ONLY the `ingredients`, `ingredient_aliases`, `ingredient_localizations`
tables and, from `products`, just `barcode`, `raw_ingredient_text`,
`ingredient_ids` and `has_verified_ingredients`. It never touches users,
health profiles, scan history, secrets or logs, and never writes: on
PostgreSQL the transaction is opened `READ ONLY` and always rolled back.

Usage (from `nutriguard-backend/`):
    python -m scripts.audit_catalogue_readonly --database-url postgresql+asyncpg://... > audit.json

Output is JSON; counts are observations at the time of the run, not
immutable acceptance numbers.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.models.ingredient import Ingredient
from app.models.ingredient_alias import IngredientAlias
from app.models.ingredient_localization import IngredientLocalization
from app.models.product import Product
from app.services.ingredient_localization import LOCALIZED_FIELDS, canonical_text_hash
from app.services.ingredient_normalization import normalize_ingredient_name
from scripts.plan_e_number_remediation import build_plan
from app.services.ocr_normalizer import (
    SyntheticIdentityMatch,
    normalize_and_extract_tokens,
    reconstruct_synthetic_ingredient,
    resolve_synthetic_identity,
)

SEED_DIR = Path(__file__).resolve().parent.parent / "app" / "seed"
PRIORITY = [
    ("E300", ["ascorbic acid"]),
    ("E202", ["potassium sorbate"]),
    ("E211", ["sodium benzoate"]),
    ("E414", ["gum arabic", "acacia gum"]),
    ("E950", ["acesulfame k", "acesulfame potassium"]),
    ("E955", ["sucralose"]),
    (None, ["water"]),
    (None, ["sugar"]),
    (None, ["carbon dioxide"]),
]
NARRATIVE = (
    "description", "purpose_in_food", "health_concerns", "references", "side_effects", "effect_conditions", "dietary_guidance"
)
_PLACEHOLDER = re.compile(
    r"^(n/?a|none|unknown|not (available|specified|stated|assessed)|no data|tbd|-+|\?+|ingredient detected on label)\.?$", re.I
)
_E_SHAPE = re.compile(r"^[eE]\d{3,4}[a-zA-Z]?$")


def _empty(v) -> bool:
    return v is None or str(v).strip() == ""


def _placeholder(v) -> bool:
    return not _empty(v) and bool(_PLACEHOLDER.match(str(v).strip()))


def _norm_e(v: str | None) -> str | None:
    return v.strip().upper() if v and v.strip() else None


def _seed_sources() -> dict:
    out: dict = {"csv": {}, "seed_json": {}, "openfoodtox_pilot": []}
    with open(SEED_DIR / "e_additives_curated_starter.csv", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            e = _norm_e(row.get("e_number"))
            if e:
                out["csv"][e] = {
                    "name": row.get("name"),
                    "has_effects": any(
                        (row.get(k) or "").strip() for k in ("potential_effects", "human_evidence", "digestion_absorption")
                    ),
                    "has_adi": bool((row.get("adi_tdi") or "").strip()),
                    "curation_status": row.get("curation_status"),
                    "last_reviewed": row.get("last_reviewed"),
                }
    for item in json.load(open(SEED_DIR / "ingredients_seed.json", encoding="utf-8")):
        out["seed_json"][_norm_e(item.get("eNumber")) or item.get("id")] = item.get("id")
    pilot = json.load(open(SEED_DIR / "openfoodtox_pilot_profiles.json", encoding="utf-8"))
    out["openfoodtox_pilot"] = sorted(pilot.get("allowlisted_e_numbers", []))
    return out


def _row_summary(ing: Ingredient, aliases: list, locs: list, refs: int) -> dict:
    current_hash = canonical_text_hash(ing)
    return {
        "id": ing.id,
        "common_name": ing.common_name,
        "e_number": ing.e_number,
        "source": getattr(ing.source, "value", ing.source),
        "verification_status": getattr(ing.verification_status, "value", ing.verification_status),
        "confidence": float(ing.confidence or 0),
        "identity_uncertain": bool(ing.identity_uncertain),
        "risk_assessment_available": bool(ing.risk_assessment_available),
        "empty_fields": [f for f in NARRATIVE if _empty(getattr(ing, f, ""))],
        "placeholder_fields": [f for f in NARRATIVE if _placeholder(getattr(ing, f, ""))],
        "alias_count": len(aliases),
        "aliases": sorted(a.alias_text for a in aliases)[:12],
        "product_references": refs,
        "localizations": [
            {
                "language": loc.language,
                "status": getattr(loc.translation_status, "value", loc.translation_status),
                "source": getattr(loc.translation_source, "value", loc.translation_source),
                "stale": loc.source_content_hash != current_hash,
                "owner_approved_without_review": bool(loc.owner_approved_without_review),
                "empty_fields": [f for f in LOCALIZED_FIELDS if _empty(getattr(loc, f, ""))],
            }
            for loc in locs
        ],
    }


async def _audit(session: AsyncSession) -> dict:
    ings = (await session.execute(select(Ingredient))).scalars().all()
    aliases = (await session.execute(select(IngredientAlias))).scalars().all()
    locs = (await session.execute(select(IngredientLocalization))).scalars().all()
    prod_rows = (
        await session.execute(
            select(Product.barcode, Product.raw_ingredient_text, Product.ingredient_ids, Product.has_verified_ingredients)
        )
    ).all()

    by_id = {i.id: i for i in ings}
    aliases_by_ing: dict[str, list] = defaultdict(list)
    for a in aliases:
        aliases_by_ing[a.ingredient_id].append(a)
    locs_by_ing: dict[str, list] = defaultdict(list)
    for loc in locs:
        locs_by_ing[loc.ingredient_id].append(loc)
    refs_by_id: Counter = Counter()
    for _, _, ids, _ in prod_rows:
        for i in (ids or "").split(","):
            if i:
                refs_by_id[i] += 1

    # --- 1. counts -----------------------------------------------------
    n_empty = {f: sum(1 for i in ings if _empty(getattr(i, f, ""))) for f in NARRATIVE}
    counts = {
        "products": len(prod_rows),
        "ingredients": len(ings),
        "aliases": len(aliases),
        "localizations": len(locs),
        "identity_uncertain": sum(1 for i in ings if i.identity_uncertain),
        "empty_by_field": n_empty,
        "by_source": dict(Counter(str(getattr(i.source, "value", i.source)) for i in ings)),
        "by_verification_status": dict(Counter(str(getattr(i.verification_status, "value", i.verification_status)) for i in ings)),
    }

    # --- 2. unresolved product -> ingredient references ----------------
    classes: dict[str, list] = defaultdict(list)
    name_index: dict[str, set] = defaultdict(set)
    for i in ings:
        name_index[normalize_ingredient_name(i.common_name or "")].add(i.id)
    for a in aliases:
        name_index[a.alias_normalized].add(a.ingredient_id)
    for barcode, raw_text, ids, _verified in prod_rows:
        for ingredient_id in [x for x in (ids or "").split(",") if x]:
            if ingredient_id in by_id:
                continue
            res = resolve_synthetic_identity(ingredient_id, raw_text or "")
            rec = reconstruct_synthetic_ingredient(ingredient_id, raw_text or "")
            if res.match in (SyntheticIdentityMatch.CURRENT,):
                cls = "RECOVERABLE_CURRENT_SYNTHETIC"
            elif res.match == SyntheticIdentityMatch.LEGACY_CYRILLIC_E:
                cls = "RECOVERABLE_LEGACY_CYRILLIC_E"
            elif res.match == SyntheticIdentityMatch.LEGACY_ALIAS:
                cls = "RECOVERABLE_LEGACY_ALIAS"
            elif res.match == SyntheticIdentityMatch.LEGACY_BARE_SLUG:
                cls = "RECOVERABLE_LEGACY_BARE_SLUG_GEN0"
            elif res.match == SyntheticIdentityMatch.LEGACY_HASH10:
                cls = "RECOVERABLE_LEGACY_HASH10_GEN1"
            elif res.match == SyntheticIdentityMatch.AMBIGUOUS:
                cls = "AMBIGUOUS"
            elif not ingredient_id.startswith("synth_"):
                cls = "MISSING_NON_SYNTHETIC_ID_DANGLING_OR_CORRUPT"
            elif not (raw_text or "").strip():
                cls = "MISSING_SYNTHETIC_NO_STORED_TEXT"
            else:
                shown = rec.common_name
                if shown == "Ingredient detected on label":
                    cls = "MISSING_SYNTHETIC_PLACEHOLDER"
                elif re.fullmatch(r"\d+", shown.strip()):
                    cls = "MISSING_SYNTHETIC_BARE_NUMBER"
                else:
                    cls = "MISSING_SYNTHETIC_READABLE_SLUG_ONLY"
            candidates = sorted(
                {c for t in normalize_and_extract_tokens(raw_text or "") for c in name_index.get(normalize_ingredient_name(t), ())}
            )
            classes[cls].append(
                {
                    "barcode": barcode,
                    "id": ingredient_id,
                    "displayed_name": rec.common_name,
                    "e_number": rec.e_number,
                    "catalogue_name_candidates_in_text": candidates[:5],
                }
            )
    all_unresolved = [r for rows in classes.values() for r in rows]
    unresolved = {
        "total_references": len(all_unresolved),
        "distinct_ids": len({r["id"] for r in all_unresolved}),
        "distinct_products": len({r["barcode"] for r in all_unresolved}),
        "by_class": {
            c: {
                "references": len(rows),
                "distinct_ids": len({r["id"] for r in rows}),
                "distinct_products": len({r["barcode"] for r in rows}),
            }
            for c, rows in sorted(classes.items())
        },
        "rows": {c: rows for c, rows in sorted(classes.items())},
    }

    # --- 3. E-number uniqueness / script normalization -----------------
    groups: dict[str, list] = defaultdict(list)
    for i in ings:
        key = _norm_e(i.e_number)
        if key:
            groups[key].append(i)
    dup_e = {
        k: [_row_summary(i, aliases_by_ing[i.id], locs_by_ing[i.id], refs_by_id[i.id]) for i in rows]
        for k, rows in groups.items()
        if len(rows) > 1
    }
    non_ascii_e = [
        {"id": i.id, "e_number": i.e_number} for i in ings if i.e_number and not i.e_number.isascii()
    ]
    non_ascii_alias_e = [
        {"ingredient_id": a.ingredient_id, "alias": a.alias_text}
        for a in aliases
        if not a.alias_text.isascii() and re.search(r"[Ее]\s?-?\d{3,4}", a.alias_text)
    ]
    e_in_name_only = [
        {"id": i.id, "common_name": i.common_name}
        for i in ings
        if not i.e_number and re.search(r"(?<![0-9A-Za-z])[eEеЕ][- ]?\d{3,4}[a-zA-Z]?\b", i.common_name or "")
    ]
    bad_shape = [{"id": i.id, "e_number": i.e_number} for i in ings if i.e_number and not _E_SHAPE.match(i.e_number)]
    e150 = [
        _row_summary(i, aliases_by_ing[i.id], locs_by_ing[i.id], refs_by_id[i.id])
        for i in ings
        if _norm_e(i.e_number) == "E150D" or "150d" in (i.id or "").lower() or "e150d" in (i.common_name or "").lower()
    ]
    e150_alias_owners = [
        {"alias": a.alias_text, "alias_normalized": a.alias_normalized, "ingredient_id": a.ingredient_id}
        for a in aliases
        if "150d" in a.alias_normalized.replace(" ", "").lower()
    ]
    e150_refs = [
        {"barcode": b, "ids": [x for x in (ids or "").split(",") if x in {r["id"] for r in e150}]}
        for b, _t, ids, _v in prod_rows
        if any(x in {r["id"] for r in e150} for x in (ids or "").split(","))
    ]
    uniq = {
        "case_collisions_by_e_number": dup_e,
        "non_ascii_e_number_values": non_ascii_e,
        "cyrillic_e_prefixed_aliases": non_ascii_alias_e,
        "e_number_only_in_common_name": e_in_name_only[:50],
        "e_number_values_with_unexpected_shape": bad_shape,
        "e150d_rows": e150,
        "e150d_aliases": e150_alias_owners,
        "e150d_product_references": e150_refs,
    }

    # --- 4. priority ingredient completeness ---------------------------
    seeds = _seed_sources()
    priority = []
    for e_number, names in PRIORITY:
        found: dict[str, Ingredient] = {}
        for i in ings:
            if e_number and _norm_e(i.e_number) == e_number:
                found[i.id] = i
            if normalize_ingredient_name(i.common_name or "") in {normalize_ingredient_name(n) for n in names}:
                found[i.id] = i
        for a in aliases:
            if a.alias_normalized in {normalize_ingredient_name(n) for n in names} and a.ingredient_id in by_id:
                found[a.ingredient_id] = by_id[a.ingredient_id]
        synth_refs = Counter()
        for _b, raw, ids, _v in prod_rows:
            for ingredient_id in [x for x in (ids or "").split(",") if x and x not in by_id]:
                rec = reconstruct_synthetic_ingredient(ingredient_id, raw or "")
                if (e_number and rec.e_number == e_number) or normalize_ingredient_name(rec.common_name) in {
                    normalize_ingredient_name(n) for n in names
                }:
                    synth_refs[ingredient_id] += 1
        priority.append(
            {
                "target": e_number or names[0],
                "rows": [
                    _row_summary(i, aliases_by_ing[i.id], locs_by_ing[i.id], refs_by_id[i.id]) for i in found.values()
                ],
                "synthetic_references_not_reaching_a_row": dict(synth_refs),
                "seed_candidates": {
                    "curated_csv": seeds["csv"].get(e_number) if e_number else None,
                    "ingredients_seed_json": seeds["seed_json"].get(e_number) if e_number else None,
                    "openfoodtox_pilot_allowlisted": bool(e_number and e_number in seeds["openfoodtox_pilot"]),
                },
            }
        )
    # --- 5. content-completeness backlog -------------------------------
    e_index: dict[str, list[Ingredient]] = defaultdict(list)
    for i in ings:
        if _norm_e(i.e_number):
            e_index[_norm_e(i.e_number)].append(i)
    seed_e = set(seeds["csv"]) | {k for k in seeds["seed_json"] if k and k.startswith("E")} | {
        _norm_e(x) for x in seeds["openfoodtox_pilot"]
    }

    def _states(i: Ingredient, loc_rows: list, refs: int) -> list[str]:
        out = []
        if all(_empty(getattr(i, f, "")) for f in ("description", "purpose_in_food", "health_concerns")):
            out.append("EXISTING_IDENTITY_NO_NARRATIVE")
        if str(getattr(i.source, "value", i.source)) == "OCR_HEURISTIC" and str(
            getattr(i.verification_status, "value", i.verification_status)
        ) == "UNVERIFIED":
            out.append("UNVERIFIED_OCR_STUB")
        if i.identity_uncertain:
            out.append("IDENTITY_UNCERTAIN")
        if any(_placeholder(getattr(i, f, "")) for f in NARRATIVE):
            out.append("PLACEHOLDER_TEXT")
        bg = [x for x in loc_rows if x.language == "bg"]
        if not bg:
            out.append("BG_LOCALIZATION_MISSING")
        elif any(x.source_content_hash != canonical_text_hash(i) for x in bg):
            out.append("BG_LOCALIZATION_STALE")
        elif all(str(getattr(x.translation_status, "value", x.translation_status)) != "REVIEWED" for x in bg):
            out.append("BG_LOCALIZATION_DRAFT_NOT_HUMAN_REVIEWED")
        out.append(
            "NOT_YET_RESEARCHED" if i.e_number else "APPLICABILITY_UNDECIDED_NOT_APPLICABLE_CANDIDATE"
        )
        if _norm_e(i.e_number) in seed_e:
            out.append("HAS_SEED_CANDIDATE")
        return out

    backlog = sorted(
        (
            {
                "id": i.id,
                "common_name": i.common_name,
                "e_number": i.e_number,
                "product_references": refs_by_id[i.id],
                "states": _states(i, locs_by_ing[i.id], refs_by_id[i.id]),
            }
            for i in ings
            if refs_by_id[i.id] > 0
        ),
        key=lambda r: (-r["product_references"], r["id"]),
    )
    state_counts = Counter(st for r in backlog for st in r["states"])
    content_not_reached = []
    for cls, rows_ in classes.items():
        for r in rows_:
            e = _norm_e(r["e_number"])
            targets = [t for t in e_index.get(e, []) if not t.id.startswith("synth_")] if e else []
            if targets:
                content_not_reached.append(
                    {
                        "reference_id": r["id"],
                        "barcode": r["barcode"],
                        "e_number": e,
                        "catalogue_row": targets[0].id,
                        "catalogue_row_has_narrative": any(not _empty(getattr(targets[0], f, "")) for f in NARRATIVE),
                    }
                )
    completeness = {
        "note": "Non-empty fields do not mean scientifically complete; states below are evidence flags, not review verdicts.",
        "state_counts_among_referenced_ingredients": dict(state_counts),
        "top_referenced_ingredients": backlog[:40],
        "references_reaching_no_row_but_matching_a_catalogue_e_number": {
            "count": len(content_not_reached),
            "distinct_reference_ids": len({r["reference_id"] for r in content_not_reached}),
            "rows": content_not_reached,
        },
        "legacy_cyrillic_e_rows_with_null_e_number": build_plan(
            list(ings), list(aliases), list(locs), [(b, t or "", i or "") for b, t, i, _v in prod_rows]
        )["legacy_cyrillic_e_rows"],
    }
    return {
        "counts": counts,
        "unresolved_references": unresolved,
        "e_number_uniqueness": uniq,
        "priority_ingredients": priority,
        "completeness": completeness,
        "seed_sources": {
            "curated_csv_e_numbers": len(seeds["csv"]),
            "ingredients_seed_json_entries": len(seeds["seed_json"]),
            "openfoodtox_pilot_allowlist": seeds["openfoodtox_pilot"],
        },
    }


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args(argv)
    database_url = args.database_url or settings.DATABASE_URL
    engine = create_async_engine(database_url, future=True)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    print(f"[audit] READ-ONLY run against: {database_url.split('@')[-1]}", file=sys.stderr)
    try:
        async with factory() as session:
            if engine.dialect.name == "postgresql":
                await session.execute(text("SET TRANSACTION READ ONLY"))
            report = await _audit(session)
            await session.rollback()
    finally:
        await engine.dispose()
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
