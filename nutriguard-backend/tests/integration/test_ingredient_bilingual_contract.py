"""
Code-review follow-up (issue 5): "Canonical English translation alone
is not Bulgarian coverage." This file documents and proves, end-to-end,
EXACTLY what `IngredientOut.localizations` contains for a runtime-
translated (never curated) ingredient, and how that differs from a
curated ingredient with a real, reviewed Bulgarian profile.

Contract (see `app.services.ingredient_localization.build_localizations`,
UNCHANGED by this task -- this file adds coverage, not new production
behavior):

  - `localizations.en` is ALWAYS present -- it mirrors the canonical
    `Ingredient` row's own fields, whatever language they are actually
    in. For a row created via the language pipeline
    (`app.services.ingredient_catalog._resolve_ingredient_languages`),
    that's always genuinely English (translated or already-English),
    never the untranslated original.
  - `localizations.bg` is present ONLY when a REVIEWED
    `IngredientLocalization` row exists for that exact ingredient id,
    with a `source_content_hash` matching the CURRENT canonical English
    text. The runtime translation pipeline (`ingredient_translation.py`)
    NEVER writes an `IngredientLocalization` row at all -- it only ever
    sets `Ingredient.common_name`/`source=GEMINI`/etc directly. So a
    brand-new synthetic ingredient resolved via translation has NO
    Bulgarian localization, ever, until a human curator adds one through
    the SAME seed/curation path the 12 curated ingredients already use
    (`app/seed/ingredients_seed_bg.json` + `app/seed/load_seed.py`) --
    machine translation is never auto-promoted to `REVIEWED`.
  - When a foreign-language OCR token happens to resolve (via official
    identifier or alias) to an EXISTING CURATED ingredient instead of
    creating a new synthetic row, the FULL curated profile -- including
    any real, reviewed Bulgarian localization -- is reused as-is. This
    is the "reuse the existing localization contract where appropriate"
    the task asks for: identity resolution (not translation) is what
    connects a foreign-language mention to already-curated bilingual
    content.
"""
from datetime import datetime, timezone

import pytest

from app.integrations.gemini import gemini_service
from app.models.enums import (
    IngredientSource,
    IngredientTranslationSource,
    IngredientTranslationStatus,
    IngredientVerificationStatus,
    RiskLevel,
)
from app.models.ingredient import Ingredient
from app.models.ingredient_localization import IngredientLocalization
from app.repositories import ingredient_alias_repository
from app.schemas.ingredient import IngredientOut
from app.services import ingredient_catalog
from app.services.ingredient_localization import canonical_text_hash
from app.services.ocr_normalizer import create_synthetic_ingredient


def _fake_translate_one(original: str, translated: str, *, confidence: float = 0.9):
    import json

    async def _fake(tokens, *, context=None):
        return json.dumps(
            [
                {
                    "originalText": t,
                    "detectedLanguage": "ro",
                    "confidence": confidence,
                    "translatedText": translated if t == original else t,
                }
                for t in tokens
            ]
        )

    return _fake


@pytest.mark.asyncio
async def test_freshly_translated_ingredient_has_english_localization_only(db_session, monkeypatch):
    """A brand-new, never-curated ingredient resolved purely through
    translation has `localizations.en` (genuinely English) and NO `bg`
    key at all -- translating a foreign name into English does not, by
    itself, produce or claim any Bulgarian coverage."""
    monkeypatch.setattr(
        gemini_service,
        "translate_ingredient_list",
        _fake_translate_one("Ulei de rapiță", "Oil Made From Rapeseed"),
    )

    synthetic = create_synthetic_ingredient("Ulei de rapiță")
    materialized, translation_occurred = await ingredient_catalog.materialize_ingredients(
        db_session, [synthetic]
    )
    assert translation_occurred is True
    row = materialized[0]

    out = IngredientOut.model_validate(row)
    dumped = out.model_dump(by_alias=True)

    assert "en" in dumped["localizations"]
    assert dumped["localizations"]["en"]["commonName"] == "Oil Made From Rapeseed"
    assert "bg" not in dumped["localizations"]


@pytest.mark.asyncio
async def test_translation_never_writes_an_ingredient_localization_row_or_marks_anything_reviewed(
    db_session, monkeypatch
):
    """Task: "never automatically mark machine translations REVIEWED".
    Verified directly against the database, not just the wire shape --
    the runtime translation path must never create an
    `IngredientLocalization` row at all (curated seed data is the ONLY
    writer of that table -- see `app/seed/load_seed.py`), so there is
    nothing that could even be mistakenly marked REVIEWED."""
    monkeypatch.setattr(
        gemini_service,
        "translate_ingredient_list",
        _fake_translate_one("Semințe de mac", "Made With Poppy Seeds"),
    )

    synthetic = create_synthetic_ingredient("Semințe de mac")
    materialized, _ = await ingredient_catalog.materialize_ingredients(db_session, [synthetic])
    row = materialized[0]

    from sqlalchemy import select

    from app.models.ingredient_localization import IngredientLocalization

    result = await db_session.execute(
        select(IngredientLocalization).where(IngredientLocalization.ingredient_id == row.id)
    )
    assert result.scalars().all() == []


async def _seed_curated_ingredient_with_reviewed_bg(db_session) -> Ingredient:
    """A minimal, self-contained curated row + a REVIEWED Bulgarian
    localization -- mirrors exactly what `app/seed/load_seed.py` persists
    for a real curated ingredient, without depending on the full seed
    file being loaded into this particular test fixture."""
    curated = Ingredient(
        id="e330_citric_acid",
        common_name="Citric Acid",
        normalized_name="citric acid",
        scientific_name="2-hydroxypropane-1,2,3-tricarboxylic acid",
        e_number="E330",
        category="Acidity Regulator",
        description="A weak organic acid used as a natural preservative and flavoring.",
        purpose_in_food="Acidity regulator, preservative, flavoring.",
        health_concerns="",
        evidence_level="Strong Scientific Consensus",
        countries_restricted_or_banned="",
        efsa_status="Authorized (No ADI limit necessary)",
        fda_status="GRAS",
        acceptable_daily_intake="Not limited",
        side_effects="",
        allergens="None",
        references="EFSA Journal 2016;14(3):4416",
        risk_level=RiskLevel.SAFE,
        risk_assessment_available=True,
        verification_status=IngredientVerificationStatus.VERIFIED,
        source=IngredientSource.CURATED_SEED,
        confidence=1.0,
    )
    db_session.add(curated)
    await db_session.flush()

    await ingredient_alias_repository.get_or_create(
        db_session,
        ingredient_id=curated.id,
        alias_text=curated.common_name,
        alias_normalized=curated.normalized_name,
        language="en",
        source=IngredientSource.CURATED_SEED,
    )

    db_session.add(
        IngredientLocalization(
            ingredient_id=curated.id,
            language="bg",
            common_name="Лимонена киселина",
            category="Регулатор на киселинност",
            description="Слаба органична киселина.",
            purpose_in_food="Регулатор на киселинност, консервант.",
            health_concerns="",
            evidence_level="Силен научен консенсус",
            countries_restricted_or_banned="",
            efsa_status="Разрешен",
            fda_status="GRAS",
            acceptable_daily_intake="Неограничен",
            side_effects="",
            allergens="Няма",
            translation_status=IngredientTranslationStatus.REVIEWED,
            translation_source=IngredientTranslationSource.HUMAN_CURATED,
            source_content_hash=canonical_text_hash(curated),
            reviewed_at=datetime.now(timezone.utc),
        )
    )
    await db_session.flush()
    return curated


@pytest.mark.asyncio
async def test_foreign_language_mention_of_a_curated_ingredient_reuses_its_real_bulgarian_profile(
    db_session, monkeypatch
):
    """The "reuse the existing localization contract" case: an official-
    identifier match (an E-number) connects a foreign-language OCR token
    to an EXISTING curated row -- the full curated profile, including
    its real reviewed Bulgarian localization, is served as-is. No
    translation is needed or attempted for identity here; the E-number
    alone is definitive."""

    def _must_not_be_called(tokens, *, context=None):
        raise AssertionError("translate_ingredient_list must not be called for an E-number match")

    monkeypatch.setattr(gemini_service, "translate_ingredient_list", _must_not_be_called)

    await _seed_curated_ingredient_with_reviewed_bg(db_session)

    # A Romanian OCR mention that still carries the E-number -- resolves
    # via official identifier, never via translation.
    synthetic = create_synthetic_ingredient("Acid citric (E330)")
    materialized, translation_occurred = await ingredient_catalog.materialize_ingredients(
        db_session, [synthetic]
    )
    assert translation_occurred is False
    row = materialized[0]
    assert row.id == "e330_citric_acid"

    out = IngredientOut.model_validate(row)
    dumped = out.model_dump(by_alias=True)
    assert dumped["localizations"]["en"]["commonName"] == "Citric Acid"
    assert "bg" in dumped["localizations"]
    assert dumped["localizations"]["bg"]["commonName"] == "Лимонена киселина"
    assert dumped["localizations"]["bg"]["translationStatus"] == IngredientTranslationStatus.REVIEWED.value


@pytest.mark.asyncio
async def test_effect_conditions_and_dietary_guidance_stay_empty_for_a_translated_ingredient(
    db_session, monkeypatch
):
    """Task: "effectConditions and dietaryGuidance must remain empty
    where unsupported. Do not fabricate scientific content to fill the
    UI." A runtime-translated synthetic ingredient never has curated
    scientific content -- these two fields must be "" (never a
    generic placeholder), same as every other scientific/regulatory
    field on a fresh OCR/translated row."""
    monkeypatch.setattr(
        gemini_service,
        "translate_ingredient_list",
        _fake_translate_one("Aluat acrisor", "Made With Sourdough"),
    )

    synthetic = create_synthetic_ingredient("Aluat acrisor")
    materialized, _ = await ingredient_catalog.materialize_ingredients(db_session, [synthetic])
    row = materialized[0]

    out = IngredientOut.model_validate(row)
    dumped = out.model_dump(by_alias=True)

    assert dumped["effectConditions"] == ""
    assert dumped["dietaryGuidance"] == ""
    assert dumped["localizations"]["en"]["effectConditions"] == ""
    assert dumped["localizations"]["en"]["dietaryGuidance"] == ""


# --- ProductOut: original text + source language (task requirement) -------


async def _register_device(client, device_id: str) -> dict:
    resp = await client.post("/api/v1/auth/device", json={"deviceId": device_id})
    token = resp.json()["accessToken"]
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_product_out_exposes_original_text_and_source_language(app_client, monkeypatch):
    """Task: "expose original ingredient text and its source language
    through ProductOut using additive camelCase fields and truthful
    missing values, including applicable response paths." Driven
    through the real `/scan/ocr-text` -> `GET /products/{barcode}`
    round trip so both response paths are covered."""
    headers = await _register_device(app_client, "bilingual-contract-device")

    resp = await app_client.post(
        "/api/v1/scan/ocr-text",
        json={"rawText": "Ingredients: Water, Sugar, Salt", "barcode": "5328710122696"},
        headers=headers,
    )
    assert resp.status_code == 200
    product = resp.json()["product"]
    assert "originalIngredientText" in product
    assert "ingredientTextSourceLanguage" in product
    assert product["originalIngredientText"] != ""
    assert product["ingredientTextSourceLanguage"] == "en"

    fetched = await app_client.get(f"/api/v1/products/{product['barcode']}", headers=headers)
    assert fetched.status_code == 200
    fetched_product = fetched.json()["product"]
    assert fetched_product["originalIngredientText"] == product["originalIngredientText"]
    assert fetched_product["ingredientTextSourceLanguage"] == "en"


def test_product_out_original_text_defaults_to_empty_not_null():
    """A barcode-only discovery (no label/OCR text ever extracted) must
    report `originalIngredientText: ""` (the honest "nothing to show",
    matching `Product.original_ingredient_text`'s own NOT NULL column
    default) and `ingredientTextSourceLanguage: null` -- never omitted,
    never a fabricated placeholder. Checked directly against the
    schema's own declared defaults (see `app.schemas.product.ProductOut`)
    rather than round-tripping a real barcode discovery, which needs a
    live external provider this test suite never calls."""
    from app.schemas.product import ProductOut

    assert ProductOut.model_fields["original_ingredient_text"].default == ""
    assert ProductOut.model_fields["ingredient_text_source_language"].default is None
