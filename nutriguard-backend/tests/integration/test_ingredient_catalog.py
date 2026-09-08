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
from app.models.product import Product
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

    synthetic = create_synthetic_ingredient("Unobtainium Extract")
    mixed = [curated, synthetic]
    materialized = await ingredient_catalog.materialize_ingredients(db_session, mixed)

    assert materialized[0] is curated  # untouched
    assert isinstance(materialized[1], Ingredient)
    assert materialized[1].id == synthetic.id
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


# --- 6. Canonical identity precedence: official identifier > alias --------
# PR #13 review blocker: an official-identifier match used to lose to a
# pre-existing alias owned by an older, weaker (UNVERIFIED synthetic) row
# -- `_register_alias_and_resolve_canonical` would see the alias's
# existing owner and return THAT instead of the official-identifier
# match, silently downgrading a curated/verified resolution to an
# unverified one. Fixed by `_reconcile_official_identifier_conflict`.


async def _stub_owning_alias(db_session, *, ingredient_id: str, alias_normalized: str) -> Ingredient:
    """A pre-existing UNVERIFIED synthetic row that already owns
    `alias_normalized` -- built directly (not via
    `get_or_create_catalog_ingredient`) so the test controls the exact
    starting state: this row's alias predates any knowledge of the
    curated/official row created separately in each test below."""
    stub = Ingredient(
        id=ingredient_id,
        common_name="Ascorbic Acid",
        normalized_name=alias_normalized,
        verification_status=IngredientVerificationStatus.UNVERIFIED,
        source=IngredientSource.OCR_HEURISTIC,
        confidence=0.2,
        risk_level=RiskLevel.SAFE,
        risk_assessment_available=False,
    )
    db_session.add(stub)
    await db_session.flush()
    await ingredient_alias_repository.get_or_create(
        db_session,
        ingredient_id=stub.id,
        alias_text="Ascorbic Acid",
        alias_normalized=alias_normalized,
        language=None,
        source=IngredientSource.OCR_HEURISTIC,
    )
    return stub


@pytest.mark.asyncio
async def test_official_identifier_match_outranks_a_pre_existing_alias_owned_by_a_different_row(db_session):
    """The exact PR #13 scenario: an older UNVERIFIED synthetic row
    already owns the "ascorbic acid" alias; a later observation supplies
    an E-number that resolves to a SEPARATE, curated/verified row. The
    curated row must win -- never the alias's pre-existing owner."""
    curated = _seeded_ingredient(
        id="e300_ascorbic_acid",
        common_name="L-Ascorbic Acid",  # deliberately NOT "Ascorbic Acid" -- its
        normalized_name="l-ascorbic acid",  # own auto-registered alias must not
        e_number="E300",  # already claim "ascorbic acid" for this test's setup
    )
    db_session.add(curated)
    await db_session.flush()

    stub = await _stub_owning_alias(db_session, ingredient_id="synth_ascorbic_stub", alias_normalized="ascorbic acid")
    assert await ingredient_repository.count(db_session) == 2

    later_observation = replace(create_synthetic_ingredient("Ascorbic Acid"), e_number="E300")
    assert normalize_ingredient_name(later_observation.common_name) == "ascorbic acid"

    resolved = await ingredient_catalog.get_or_create_catalog_ingredient(db_session, later_observation)

    assert resolved.id == curated.id  # the curated/verified row wins, never the stub
    assert resolved.verification_status == IngredientVerificationStatus.VERIFIED

    # The contested alias now converges on the curated row -- any future
    # resolution of "ascorbic acid" (with or without the E-number) hits
    # the curated row directly.
    alias = await ingredient_alias_repository.get_by_normalized(db_session, "ascorbic acid")
    assert alias is not None
    assert alias.ingredient_id == curated.id

    # The stub had no product referencing it and isn't curated/verified
    # -- provably safe to remove, so it must not be left behind as an
    # orphan duplicate.
    assert await ingredient_repository.get_by_id(db_session, stub.id) is None
    assert await ingredient_repository.count(db_session) == 1


@pytest.mark.asyncio
async def test_official_identifier_conflict_resolution_never_deletes_a_curated_or_verified_row(db_session):
    """Defense in depth: even if the ALIAS OWNER (the "losing" side of
    the conflict) is itself curated/verified, it must never be deleted
    -- only a genuinely disposable synthetic duplicate may ever be
    removed."""
    official = _seeded_ingredient(
        id="e300_ascorbic_acid", common_name="L-Ascorbic Acid", normalized_name="l-ascorbic acid", e_number="E300"
    )
    other_curated = _seeded_ingredient(
        id="curated_ascorbic_acid_variant",
        common_name="Ascorbic Acid",
        normalized_name="ascorbic acid",
        e_number=None,
    )
    db_session.add_all([official, other_curated])
    await db_session.flush()
    await ingredient_alias_repository.get_or_create(
        db_session,
        ingredient_id=other_curated.id,
        alias_text="Ascorbic Acid",
        alias_normalized="ascorbic acid",
        language=None,
        source=IngredientSource.CURATED_SEED,
    )

    later_observation = replace(create_synthetic_ingredient("Ascorbic Acid"), e_number="E300")
    resolved = await ingredient_catalog.get_or_create_catalog_ingredient(db_session, later_observation)

    assert resolved.id == official.id
    # `other_curated` is itself VERIFIED/CURATED_SEED -- never deleted,
    # even though it lost the alias to `official`.
    assert await ingredient_repository.get_by_id(db_session, other_curated.id) is not None
    assert await ingredient_repository.count(db_session) == 2

    alias = await ingredient_alias_repository.get_by_normalized(db_session, "ascorbic acid")
    assert alias.ingredient_id == official.id


@pytest.mark.asyncio
async def test_official_identifier_conflict_resolution_preserves_existing_product_relationships(db_session):
    """Deterministic behavior when the alias-owning duplicate ALREADY
    has a product relationship (task: "define deterministic behavior if
    both rows already have product references"): the stub is left in
    place -- unreachable by name from now on, but still valid for the
    product that already references its id directly -- rather than
    deleted out from under that product."""
    curated = _seeded_ingredient(
        id="e300_ascorbic_acid", common_name="L-Ascorbic Acid", normalized_name="l-ascorbic acid", e_number="E300"
    )
    db_session.add(curated)
    await db_session.flush()

    stub = await _stub_owning_alias(db_session, ingredient_id="synth_ascorbic_stub", alias_normalized="ascorbic acid")

    product = Product(
        barcode="0000000000001",
        product_name="Legacy Product Referencing The Stub",
        brand="",
        category="",
        raw_ingredient_text="ascorbic acid",
        ingredient_ids=stub.id,
        health_score=50,
        nova_group=1,
        sugar_grams=0,
        sodium_mg=0,
        saturated_fat_grams=0,
        allergens_detected="None",
        source="label_scan",
    )
    db_session.add(product)
    await db_session.flush()

    later_observation = replace(create_synthetic_ingredient("Ascorbic Acid"), e_number="E300")
    resolved = await ingredient_catalog.get_or_create_catalog_ingredient(db_session, later_observation)

    assert resolved.id == curated.id  # official identifier still wins for FUTURE resolution

    # The stub survives -- deleting it would leave `product` with a
    # dangling ingredient reference.
    still_there = await ingredient_repository.get_by_id(db_session, stub.id)
    assert still_there is not None
    assert await ingredient_repository.count(db_session) == 2

    # But it is no longer reachable by name -- every future OCR
    # observation of "ascorbic acid" converges on the curated row.
    alias = await ingredient_alias_repository.get_by_normalized(db_session, "ascorbic acid")
    assert alias.ingredient_id == curated.id

    # `product`'s own ingredient reference is untouched -- still valid.
    persisted_product = await db_session.get(Product, product.barcode)
    assert persisted_product.ingredient_ids == stub.id


@pytest.mark.asyncio
async def test_official_identifier_match_with_no_prior_alias_conflict_is_unaffected(db_session):
    """No conflict at all (the common case, already covered by
    `test_local_cache_hit_by_e_number_reuses_the_curated_row_without_creating_one`)
    must remain completely unaffected by the new reconciliation path --
    it should never even be invoked."""
    curated = _seeded_ingredient()
    db_session.add(curated)
    await db_session.flush()

    synthetic = create_synthetic_ingredient("Citric Acid (E330)")
    resolved = await ingredient_catalog.get_or_create_catalog_ingredient(db_session, synthetic)

    assert resolved.id == curated.id
    assert await ingredient_repository.count(db_session) == 1
