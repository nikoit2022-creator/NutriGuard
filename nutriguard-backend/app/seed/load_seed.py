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
import json
from datetime import datetime, timezone
from pathlib import Path

import structlog

from app.database.session import AsyncSessionLocal
from app.models.enums import IngredientSource, IngredientVerificationStatus, RiskLevel
from app.models.ingredient import Ingredient
from app.repositories import ingredient_alias_repository
from app.services.ingredient_catalog import derive_ins_number_from_e_number
from app.services.ingredient_normalization import normalize_ingredient_name

logger = structlog.get_logger(__name__)

_SEED_FILE = Path(__file__).parent / "ingredients_seed.json"

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

        await session.commit()
    logger.info("seed_loaded", count=count)
    return count


if __name__ == "__main__":
    inserted = asyncio.run(load_seed())
    print(f"Seeded {inserted} ingredients.")
