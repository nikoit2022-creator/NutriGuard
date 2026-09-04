"""
DB-backed tests for `app.services.ingredient_catalog` -- the persistent
ingredient knowledge cache's local-first canonical-identity resolution
(task: "persistent ingredient knowledge cache"). Uses the `db_session`
fixture directly (repository/service layer, no HTTP) -- end-to-end
coverage through the actual scan endpoints lives in
`tests/integration/test_ingredient_knowledge_cache_end_to_end.py`.
"""
from dataclasses import replace

import pytest

from app.models.enums import IngredientSource, IngredientVerificationStatus, RiskLevel
from app.models.ingredient import Ingredient
from app.repositories import ingredient_alias_repository, ingredient_repository
from app.services import ingredient_catalog
from app.services.ingredient_normalization import normalize_ingredient_name
from app.services.ocr_normalizer import create_synthetic_ingredient


def _seeded_ingredient(**overrides) -> Ingredient:
    """A minimal, plausible curated row -- mirrors what `load_seed.py`
    actually persists."""
    defaults = dict(
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
    defaults.update(overrides)
    return Ingredient(**defaults)


# --- 1. Local cache hit with no external "request" (no new row) ------------


@pytest.mark.asyncio
async def test_local_cache_hit_by_e_number_reuses_the_curated_row_without_creating_one(db_session):
    curated = _seeded_ingredient()
    db_session.add(curated)
    await db_session.flush()

    synthetic = create_synthetic_ingredient("Citric Acid (E330)")
    assert synthetic.e_number == "E330"

    resolved = await ingredient_catalog.get_or_create_catalog_ingredient(db_session, synthetic)

    assert resolved.id == "e330_citric_acid"
    assert await ingredient_repository.count(db_session) == 1  # nothing new was created


@pytest.mark.asyncio
async def test_e_number_hit_registers_the_observed_display_text_as_a_new_alias(db_session):
    """Verification-review regression: resolving via E-number (a
    display text with no alias of its own yet, e.g. a scanning quirk
    like a stray trailing period OCR left in the token) must ALSO
    register that exact text as a new alias -- so a LATER scan of the
    same display text that doesn't also happen to catch the E-number
    (a blurrier crop) still resolves directly via alias instead of
    falling through to creating a second, duplicate row."""
    curated = _seeded_ingredient()
    db_session.add(curated)
    await db_session.flush()

    first = create_synthetic_ingredient("Citric Acid Extra (E330)")
    assert first.e_number == "E330"
    normalized_text = normalize_ingredient_name(first.common_name)
    resolved_via_e_number = await ingredient_catalog.get_or_create_catalog_ingredient(db_session, first)
    assert resolved_via_e_number.id == "e330_citric_acid"

    alias = await ingredient_alias_repository.get_by_normalized(db_session, normalized_text)
    assert alias is not None
    assert alias.ingredient_id == "e330_citric_acid"

    # A later, otherwise-identical observation reuses the alias just
    # registered (and, since `Ingredient` already exists and the alias
    # already exists, this is now purely an alias-table hit, not a new
    # E-number lookup outcome) -- no duplicate row either way.
    second = create_synthetic_ingredient("Citric Acid Extra (E330)")
    resolved_again = await ingredient_catalog.get_or_create_catalog_ingredient(db_session, second)
    assert resolved_again.id == "e330_citric_acid"
    assert await ingredient_repository.count(db_session) == 1


@pytest.mark.asyncio
async def test_local_cache_hit_by_english_name_alias(db_session):
    curated = _seeded_ingredient()
    db_session.add(curated)
    await db_session.flush()
    await ingredient_alias_repository.get_or_create(
        db_session,
        ingredient_id=curated.id,
        alias_text="Citric Acid",
        alias_normalized="citric acid",
        language="en",
        source=IngredientSource.CURATED_SEED,
    )

    # No E-number in this OCR token -- must resolve via the alias, not
    # by chance/name-hash.
    synthetic = create_synthetic_ingredient("citric acid")
    assert synthetic.e_number is None

    resolved = await ingredient_catalog.get_or_create_catalog_ingredient(db_session, synthetic)

    assert resolved.id == "e330_citric_acid"
    assert await ingredient_repository.count(db_session) == 1


@pytest.mark.asyncio
async def test_local_cache_hit_by_bulgarian_alias(db_session):
    """Task's own example: citric acid / E330 / лимонена киселина must
    never become three separate records once reliably identified as
    the same ingredient."""
    curated = _seeded_ingredient()
    db_session.add(curated)
    await db_session.flush()
    await ingredient_alias_repository.get_or_create(
        db_session,
        ingredient_id=curated.id,
        alias_text="лимонена киселина",
        alias_normalized="лимонена киселина",
        language="bg",
        source=IngredientSource.CURATED_SEED,
    )

    synthetic = create_synthetic_ingredient("лимонена киселина")
    resolved = await ingredient_catalog.get_or_create_catalog_ingredient(db_session, synthetic)

    assert resolved.id == "e330_citric_acid"
    assert await ingredient_repository.count(db_session) == 1


# --- 2. Cache miss -> "fetch" -> validate -> persist -> subsequent hit -----


@pytest.mark.asyncio
async def test_cache_miss_persists_a_minimal_unverified_row_then_hits_on_reuse(db_session):
    synthetic = create_synthetic_ingredient("Xylitol")

    first = await ingredient_catalog.get_or_create_catalog_ingredient(db_session, synthetic)
    await db_session.flush()

    assert first.verification_status == IngredientVerificationStatus.UNVERIFIED
    assert first.source == IngredientSource.OCR_HEURISTIC
    assert first.risk_assessment_available is False
    assert first.risk_level == RiskLevel.SAFE
    # Task requirement 4: no fabricated scientific/regulatory claim.
    assert first.description == ""
    assert first.health_concerns == ""
    assert first.efsa_status == ""
    assert first.acceptable_daily_intake == ""
    assert await ingredient_repository.count(db_session) == 1

    # Same ingredient, second occurrence (a different product's scan) --
    # must reuse the SAME row, not create a second one.
    second = await ingredient_catalog.get_or_create_catalog_ingredient(
        db_session, create_synthetic_ingredient("Xylitol")
    )
    assert second.id == first.id
    assert await ingredient_repository.count(db_session) == 1


@pytest.mark.asyncio
async def test_materialize_ingredients_replaces_synthetic_stubs_and_passes_through_curated_rows(db_session):
    curated = _seeded_ingredient()
    db_session.add(curated)
    await db_session.flush()

    mixed = [curated, create_synthetic_ingredient("Unobtainium Extract")]
    materialized = await ingredient_catalog.materialize_ingredients(db_session, mixed)

    assert materialized[0] is curated  # untouched
    assert isinstance(materialized[1], Ingredient)
    assert materialized[1].id == "synth_unobtainium_extract"
    assert await ingredient_repository.count(db_session) == 2


# --- 3. Deduplication across spelling/case variants -------------------------


@pytest.mark.asyncio
async def test_spelling_and_case_variants_of_a_new_ingredient_converge_on_one_row(db_session):
    first = await ingredient_catalog.get_or_create_catalog_ingredient(
        db_session, create_synthetic_ingredient("Xanthan Gum Extract")
    )
    await db_session.flush()
    second = await ingredient_catalog.get_or_create_catalog_ingredient(
        db_session, create_synthetic_ingredient("xanthan   gum   extract")
    )
    assert second.id == first.id
    assert await ingredient_repository.count(db_session) == 1


# --- 4. Identity fields may be filled (never overwritten) on an existing row ---


@pytest.mark.asyncio
async def test_a_later_observation_can_fill_a_previously_missing_e_number(db_session):
    """`_fill_missing_identity_fields`'s real contract: the SAME
    normalized name resolving via the alias table a second time, this
    time with an E-number the first observation's OCR text didn't
    contain (e.g. an unreadable label segment the first time round) --
    constructed directly (`dataclasses.replace`) rather than depending
    on `create_synthetic_ingredient`'s own text-parsing behavior, since
    that always extracts whatever E-number IS present in a given string
    deterministically -- two *different* raw strings ("Ascorbic Acid"
    vs "Ascorbic Acid (E300)") normalize to different text and are a
    separate scenario (a fresh, unrelated stub), not this one."""
    stub = await ingredient_catalog.get_or_create_catalog_ingredient(
        db_session, create_synthetic_ingredient("Ascorbic Acid")
    )
    await db_session.flush()
    assert stub.e_number is None

    second_observation = replace(create_synthetic_ingredient("Ascorbic Acid"), e_number="E300")
    updated = await ingredient_catalog.get_or_create_catalog_ingredient(db_session, second_observation)

    assert updated.id == stub.id
    assert updated.e_number == "E300"
    assert updated.ins_number == "300"
    assert await ingredient_repository.count(db_session) == 1


# --- 5. Race-safe insert (simulated conflict -- see the real Postgres ------
#        concurrency test for genuine concurrent-session coverage) ---------


@pytest.mark.asyncio
async def test_insert_new_returns_none_on_a_primary_key_conflict_instead_of_raising(db_session):
    row_a = _seeded_ingredient(id="synth_race_test", common_name="Race Test", normalized_name="race test")
    db_session.add(row_a)
    await db_session.flush()

    row_b = _seeded_ingredient(id="synth_race_test", common_name="Race Test", normalized_name="race test")
    result = await ingredient_repository.insert_new(db_session, row_b)

    assert result is None  # conflict handled, not raised
    assert await ingredient_repository.count(db_session) == 1


@pytest.mark.asyncio
async def test_alias_get_or_create_is_idempotent_under_a_normalized_alias_conflict(db_session):
    curated = _seeded_ingredient()
    db_session.add(curated)
    await db_session.flush()

    first = await ingredient_alias_repository.get_or_create(
        db_session,
        ingredient_id=curated.id,
        alias_text="Citric Acid",
        alias_normalized="citric acid",
        language="en",
        source=IngredientSource.CURATED_SEED,
    )
    second = await ingredient_alias_repository.get_or_create(
        db_session,
        ingredient_id=curated.id,
        alias_text="Citric Acid (again)",
        alias_normalized="citric acid",
        language="en",
        source=IngredientSource.OCR_HEURISTIC,
    )
    assert second.id == first.id
    assert second.alias_text == "Citric Acid"  # the original row, unchanged
