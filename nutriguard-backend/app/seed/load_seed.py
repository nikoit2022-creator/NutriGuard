"""
Loads app/seed/ingredients_seed.json (extracted verbatim from the
Android client's InitialScientificData.kt) into the `ingredients`
table. Idempotent: safe to run multiple times (upsert by primary key).

Usage:
    python -m app.seed.load_seed
"""
import asyncio
import json
from pathlib import Path

import structlog

from app.database.session import AsyncSessionLocal
from app.models.enums import RiskLevel
from app.models.ingredient import Ingredient

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


def _row_to_kwargs(row: dict) -> dict:
    kwargs = {}
    for camel, snake in _CAMEL_TO_SNAKE.items():
        value = row.get(camel)
        if snake == "risk_level":
            value = RiskLevel(value)
        kwargs[snake] = value
    return kwargs


async def load_seed() -> int:
    rows = json.loads(_SEED_FILE.read_text(encoding="utf-8"))
    count = 0
    async with AsyncSessionLocal() as session:
        for row in rows:
            ingredient = Ingredient(**_row_to_kwargs(row))
            await session.merge(ingredient)
            count += 1
        await session.commit()
    logger.info("seed_loaded", count=count)
    return count


if __name__ == "__main__":
    inserted = asyncio.run(load_seed())
    print(f"Seeded {inserted} ingredients.")
