"""
Loads app/seed/ingredients_seed.json (extracted verbatim from the
Android client's InitialScientificData.kt) into the `ingredients`
table. Idempotent: safe to run multiple times (upsert by primary key).

Also registers each curated row's canonical-identity alias data (task:
"persistent ingredient knowledge cache", requirement 2) -- its own
name, plus a small set of known Bulgarian/spelling/abbreviation
variants for the entries that already had one in
`app.services.label_language._BULGARIAN_INGREDIENT_ALIASES` (that
in-memory dict is UNCHANGED and still does its own job -- matching-time
text substitution for a request that hasn't touched the DB yet; this
is the same knowledge additionally made a persistent, queryable
`IngredientAlias` row so `app.services.ingredient_catalog`'s alias
lookup resolves it directly on any FUTURE occurrence too, in either
language, without depending on that hardcoded substitution running
first).

Usage:
    python -m app.seed.load_seed
"""
import asyncio
import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import structlog
from sqlalchemy import select

from app.database.session import AsyncSessionLocal
from app.models.enums import IngredientSource, IngredientVerificationStatus, RiskLevel
from app.models.ingredient import Ingredient
from app.repositories import ingredient_alias_repository
from app.services.ingredient_catalog import derive_ins_number_from_e_number
from app.services.ingredient_normalization import normalize_ingredient_name

logger = structlog.get_logger(__name__)

_SEED_FILE = Path(__file__).parent / "ingredients_seed.json"
_E_ADDITIVE_SEED_FILE = Path(__file__).parent / "e_additives_curated_starter.csv"
_E_ADDITIVE_SOURCE_VERSION = "2026-09-08"

_CAMEL_TO_SNAKE = {
    "id": "id",
    "commonName": "common_name",
    "scientificName": "scientific_name",
    "eNumber": "e_number",
    "category": "category",
    "description": "description",
    "purposeInFood": "purpose_in_food",
    "healthConcerns": "health_concerns",
    "evidenceLevel": "evidence_level",
    "countriesRestrictedOrBanned": "countries_restricted_or_banned",
    "efsaStatus": "efsa_status",
    "fdaStatus": "fda_status",
    "whoIarcClassification": "who_iarc_classification",
    "acceptableDailyIntake": "acceptable_daily_intake",
    "sideEffects": "side_effects",
    "allergens": "allergens",
    "references": "references",
    "riskLevel": "risk_level",
    "isGluten": "is_gluten",
    "isLactose": "is_lactose",
    "isVegan": "is_vegan",
    "isVegetarian": "is_vegetarian",
    "isHalal": "is_halal",
    "isKosher": "is_kosher",
    "badForDiabetes": "bad_for_diabetes",
    "badForHypertension": "bad_for_hypertension",
    "badForKidneyDisease": "bad_for_kidney_disease",
    "badForGout": "bad_for_gout",
    "badForPregnancy": "bad_for_pregnancy",
    "badForChildren": "bad_for_children",
    "badForHighCholesterol": "bad_for_high_cholesterol",
}

# (ingredient id, alias text, language|None) -- curated, hand-verified
# variants beyond a row's own `commonName` (which is registered as an
# alias automatically for every row, see `load_seed`). Deliberately
# small and explicit, exactly like `label_language._BULGARIAN_INGREDIENT_ALIASES`
# it partly mirrors -- never auto-generated/fuzzy-matched, so every
# entry here is a real, reviewed identity claim, not a guess.
_EXTRA_ALIASES: list[tuple[str, str, str | None]] = [
    ("e951_aspartame", "аспартам", "bg"),
    ("e951_aspartame", "Aspartam", "en"),  # common spelling/OCR variant (missing trailing "e")
    ("e621_msg", "MSG", "en"),
    ("e250_sodium_nitrite", "натриев нитрит", "bg"),
    ("high_fructose_corn_syrup", "HFCS", "en"),
]


def _row_to_kwargs(row: dict) -> dict:
    kwargs = {}
    for camel, snake in _CAMEL_TO_SNAKE.items():
        value = row.get(camel)
        if snake == "risk_level":
            value = RiskLevel(value)
        kwargs[snake] = value
    now = datetime.now(timezone.utc)
    kwargs["normalized_name"] = normalize_ingredient_name(kwargs["common_name"])
    kwargs["ins_number"] = derive_ins_number_from_e_number(kwargs["e_number"])
    kwargs["verification_status"] = IngredientVerificationStatus.VERIFIED
    kwargs["source"] = IngredientSource.CURATED_SEED
    kwargs["source_record_id"] = kwargs["id"]
    kwargs["retrieved_at"] = now
    kwargs["last_verified_at"] = now
    kwargs["confidence"] = 1.0
    kwargs["schema_version"] = 1
    kwargs["risk_assessment_available"] = True
    return kwargs


def _starter_additive_id(e_number: str, name: str) -> str:
    """Stable readable id for a starter-pack additive.

    The E-number is the canonical identity.  The slug is display/debug
    convenience only and is intentionally ASCII-bounded.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:48]
    return f"{e_number.lower()}_{slug}" if slug else e_number.lower()


def _join_present_sections(*sections: tuple[str, str]) -> str:
    return "\n".join(f"{label}: {value.strip()}" for label, value in sections if value.strip())


def _starter_row_to_kwargs(row: dict[str, str]) -> dict:
    """Map one reviewed starter row without inventing missing science.

    The pack is explicitly a curation starter, not a production-ready
    regulatory assessment.  Rows therefore enter as LIMITED_DATA and
    can provide identity/function text, but never a risk assessment,
    approval badge, ADI calculation or Health Score deduction until a
    later field-specific verified-source merge confirms those claims.
    """
    e_number = row["e_number"].strip().upper()
    name = row["name"].strip()
    now = datetime.now(timezone.utc)
    return {
        "id": _starter_additive_id(e_number, name),
        "common_name": name,
        "normalized_name": normalize_ingredient_name(name),
        "scientific_name": "",
        "e_number": e_number,
        "ins_number": row["ins_number"].strip() or None,
        "cas_number": None,
        "category": row["functional_class"].strip(),
        "description": _join_present_sections(
            ("Digestion and absorption", row["digestion_absorption"]),
            ("Metabolism", row["metabolism"]),
        ),
        "purpose_in_food": row["typical_role_or_foods"].strip(),
        "health_concerns": row["potential_effects"].strip(),
        "evidence_level": _join_present_sections(
            ("Human evidence", row["human_evidence"]),
            ("Animal evidence", row["animal_evidence"]),
        ),
        "countries_restricted_or_banned": "",
        "efsa_status": row["efsa_assessment"].strip(),
        "fda_status": "",
        "who_iarc_classification": None,
        "acceptable_daily_intake": row["adi_tdi"].strip(),
        "side_effects": "",
        "allergens": "",
        "references": row["primary_sources"].strip(),
        "risk_level": RiskLevel.SAFE,
        "risk_assessment_available": False,
        "verification_status": IngredientVerificationStatus.LIMITED_DATA,
        "source": IngredientSource.CURATED_SEED,
        "source_record_id": f"e-number-starter:{_E_ADDITIVE_SOURCE_VERSION}:{e_number}",
        "source_url": None,
        "retrieved_at": now,
        "last_verified_at": None,
        "confidence": 0.75,
        "schema_version": 1,
        "field_provenance_json": None,
        "is_gluten": None,
        "is_lactose": None,
        "is_vegan": None,
        "is_vegetarian": None,
        "is_halal": None,
        "is_kosher": None,
        "bad_for_diabetes": False,
        "bad_for_hypertension": False,
        "bad_for_kidney_disease": False,
        "bad_for_gout": False,
        "bad_for_pregnancy": False,
        "bad_for_children": False,
        "bad_for_high_cholesterol": False,
    }


async def _load_e_additive_starter(session) -> int:
    """Insert only the 43 populated starter rows, preserving richer data.

    The separate 800-row registry is deliberately not shipped or read:
    its remaining 757 records are coverage placeholders, not confirmed
    assigned/currently-authorized additives.
    """
    with _E_ADDITIVE_SEED_FILE.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    inserted = 0
    for row in rows:
        kwargs = _starter_row_to_kwargs(row)
        existing = await session.execute(
            select(Ingredient).where(Ingredient.e_number == kwargs["e_number"]).limit(1)
        )
        ingredient = existing.scalar_one_or_none()
        if ingredient is not None:
            # Existing curated data is intentionally richer and remains
            # untouched.  In particular, never collapse generic E322
            # "Lecithins" into the existing soy-specific ingredient.
            continue

        ingredient = Ingredient(**kwargs)
        session.add(ingredient)
        await session.flush()
        inserted += 1

        await ingredient_alias_repository.get_or_create(
            session,
            ingredient_id=ingredient.id,
            alias_text=ingredient.common_name,
            alias_normalized=ingredient.normalized_name,
            language="en",
            source=IngredientSource.CURATED_SEED,
        )
    return inserted


async def load_seed() -> int:
    rows = json.loads(_SEED_FILE.read_text(encoding="utf-8"))
    count = 0
    async with AsyncSessionLocal() as session:
        for row in rows:
            kwargs = _row_to_kwargs(row)
            ingredient = Ingredient(**kwargs)
            await session.merge(ingredient)
            count += 1

            # Register this row's own name as its first/primary alias.
            # Curated seed data always wins (SOURCE_PRIORITY['CURATED_SEED']
            # is the highest rank -- see app.services.ingredient_catalog) --
            # but `get_or_create` never overwrites an existing alias row,
            # so if this normalized name was somehow already claimed by a
            # DIFFERENT ingredient id (e.g. an OCR-only stub persisted
            # before this curated entry existed in the seed file), that
            # pre-existing mapping is left exactly as-is and only logged,
            # not silently repointed -- reconciling/merging that older
            # stub into this curated row is a deliberate, reviewed
            # maintenance operation, not something a seed load should
            # ever do automatically to production data. See
            # docs/CODEX_HANDOFF.md.
            normalized = kwargs["normalized_name"]
            alias = await ingredient_alias_repository.get_or_create(
                session,
                ingredient_id=kwargs["id"],
                alias_text=kwargs["common_name"],
                alias_normalized=normalized,
                language="en",
                source=IngredientSource.CURATED_SEED,
            )
            if alias.ingredient_id != kwargs["id"]:
                logger.warning(
                    "seed_alias_collision",
                    ingredient_id=kwargs["id"],
                    normalized_name=normalized,
                    already_claimed_by=alias.ingredient_id,
                )

        for ingredient_id, alias_text, language in _EXTRA_ALIASES:
            normalized = normalize_ingredient_name(alias_text)
            alias = await ingredient_alias_repository.get_or_create(
                session,
                ingredient_id=ingredient_id,
                alias_text=alias_text,
                alias_normalized=normalized,
                language=language,
                source=IngredientSource.CURATED_SEED,
            )
            if alias.ingredient_id != ingredient_id:
                logger.warning(
                    "seed_alias_collision",
                    ingredient_id=ingredient_id,
                    normalized_name=normalized,
                    already_claimed_by=alias.ingredient_id,
                )

        count += await _load_e_additive_starter(session)

        await session.commit()
    logger.info("seed_loaded", count=count)
    return count


if __name__ == "__main__":
    inserted = asyncio.run(load_seed())
    print(f"Seeded {inserted} ingredients.")
