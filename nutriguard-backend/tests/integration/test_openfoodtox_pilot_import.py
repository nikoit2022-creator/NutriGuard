"""Coverage for app/seed/load_openfoodtox_pilot_content.py --
docs/OPENFOODTOX_APP_PILOT_INTEGRATION_TASK.md's explicit, transactional,
idempotent dry-run/apply import for the four owner-approved OpenFoodTox
pilot identities.

Seeds a realistic baseline via the REAL `app.seed.load_seed.load_seed()`
(monkeypatched onto a disposable in-memory SQLite engine, the same
pattern `tests/integration/test_load_seed.py` already uses) so this
import is exercised against the actual shape of production data -- E250
and E951 already exist (VERIFIED, from the main curated seed), E330
exists via the CSV starter (LIMITED_DATA), and E150d does not exist at
all.

The three classes at the bottom of this file
(`TestRestartPreservesPilotContent`, `TestEnOnlyApprovedUpdateKeepsBg`,
`TestBgCommonNamesAreLocalized`) are the permanent regressions for
docs/OPENFOODTOX_APP_INTEGRATION_FIX_TASK.md's three reproduced
defects -- each replaces one of Codex's temporary review probes.
"""
import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.enums import (
    IngredientSource,
    IngredientTranslationSource,
    IngredientTranslationStatus,
    IngredientVerificationStatus,
    RiskLevel,
)
from app.models.ingredient import Ingredient
from app.models.ingredient_localization import IngredientLocalization
from app.models.product import Product
from app.seed import load_openfoodtox_pilot_content as import_module
from app.seed import load_seed as load_seed_module
from app.services import health_score
from app.services.ingredient_localization import build_localizations


@pytest.fixture
def session_factory(db_engine, monkeypatch):
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(load_seed_module, "AsyncSessionLocal", factory)
    monkeypatch.setattr(import_module, "AsyncSessionLocal", factory)
    return factory


@pytest.fixture
async def seeded_engine(session_factory):
    await load_seed_module.load_seed()
    return session_factory


async def _get(session_factory, ingredient_id: str) -> Ingredient:
    async with session_factory() as db:
        return await db.get(Ingredient, ingredient_id)


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(seeded_engine):
    session_factory = seeded_engine
    async with session_factory() as db:
        plans = await import_module.build_plan(db)
        assert not db.new and not db.dirty and not db.deleted
    e_numbers = {p.e_number for p in plans}
    assert e_numbers == {"E250", "E150d", "E330", "E951"}
    create_plans = {p.e_number for p in plans if p.action == "create"}
    assert create_plans == {"E150d"}
    update_plans = {p.e_number for p in plans if p.action == "update"}
    assert update_plans == {"E250", "E330", "E951"}


@pytest.mark.asyncio
async def test_apply_creates_e150d_with_safe_scoring_defaults(seeded_engine):
    session_factory = seeded_engine
    exit_code = await import_module.run(apply=True)
    assert exit_code == 0

    e150d = await _get(session_factory, "e150d_sulphite_ammonia_caramel")
    assert e150d is not None
    assert e150d.e_number == "E150d"
    assert e150d.verification_status == IngredientVerificationStatus.LIMITED_DATA
    assert e150d.risk_assessment_available is False
    assert e150d.risk_level == RiskLevel.SAFE
    assert e150d.acceptable_daily_intake == ""
    assert e150d.efsa_status == ""
    assert e150d.is_vegan is None and e150d.is_gluten is None
    assert e150d.bad_for_pregnancy is False
    assert e150d.description


@pytest.mark.asyncio
async def test_apply_preserves_scoring_and_regulatory_fields_on_existing_rows(seeded_engine):
    session_factory = seeded_engine
    before = {}
    async with session_factory() as db:
        for ing_id in ("e250_sodium_nitrite", "e951_aspartame"):
            ing = await db.get(Ingredient, ing_id)
            before[ing_id] = {
                "risk_level": ing.risk_level,
                "risk_assessment_available": ing.risk_assessment_available,
                "verification_status": ing.verification_status,
                "source": ing.source,
                "efsa_status": ing.efsa_status,
                "fda_status": ing.fda_status,
                "acceptable_daily_intake": ing.acceptable_daily_intake,
                "is_vegan": ing.is_vegan,
                "is_gluten": ing.is_gluten,
                "bad_for_pregnancy": ing.bad_for_pregnancy,
                "bad_for_children": ing.bad_for_children,
                "common_name": ing.common_name,
                "category": ing.category,
            }

    exit_code = await import_module.run(apply=True)
    assert exit_code == 0

    async with session_factory() as db:
        for ing_id, snapshot in before.items():
            ing = await db.get(Ingredient, ing_id)
            for field_name, old_value in snapshot.items():
                assert getattr(ing, field_name) == old_value, f"{ing_id}.{field_name} changed"
            # The actual pilot content SHOULD have landed on the narrative fields.
            assert ing.description
            assert ing.purpose_in_food


@pytest.mark.asyncio
async def test_apply_is_idempotent(seeded_engine):
    session_factory = seeded_engine
    assert await import_module.run(apply=True) == 0
    async with session_factory() as db:
        snapshot_1 = (await db.get(Ingredient, "e250_sodium_nitrite")).description
        e150d_count_1 = len((await db.execute(select(Ingredient).where(Ingredient.e_number == "E150d"))).scalars().all())

    assert await import_module.run(apply=True) == 0
    async with session_factory() as db:
        plans = await import_module.build_plan(db)
        snapshot_2 = (await db.get(Ingredient, "e250_sodium_nitrite")).description
        e150d_count_2 = len((await db.execute(select(Ingredient).where(Ingredient.e_number == "E150d"))).scalars().all())

    assert snapshot_1 == snapshot_2
    assert e150d_count_1 == e150d_count_2 == 1
    assert all(p.action == "no_op" for p in plans), [(p.e_number, p.action) for p in plans]


@pytest.mark.asyncio
async def test_exact_e_number_match_never_collapses_suffixes(seeded_engine, session_factory):
    """A pre-existing E150 (no suffix) or E150a/b/c row must never be
    matched/updated by the E150d import -- identity resolution is an
    exact string match only."""
    async with session_factory() as db:
        decoy = Ingredient(
            id="e150a_plain_caramel",
            common_name="Plain caramel",
            normalized_name="plain caramel",
            e_number="E150a",
            category="Colour",
            risk_level=RiskLevel.SAFE,
            verification_status=IngredientVerificationStatus.LIMITED_DATA,
            source=IngredientSource.CURATED_SEED,
        )
        db.add(decoy)
        await db.commit()

    exit_code = await import_module.run(apply=True)
    assert exit_code == 0

    async with session_factory() as db:
        decoy_after = await db.get(Ingredient, "e150a_plain_caramel")
        assert decoy_after.description == ""  # untouched
        e150d = await db.get(Ingredient, "e150d_sulphite_ammonia_caramel")
        assert e150d is not None and e150d.e_number == "E150d"


@pytest.mark.asyncio
async def test_owner_approved_bg_content_is_served_as_honest_draft(seeded_engine):
    session_factory = seeded_engine
    await import_module.run(apply=True)

    async with session_factory() as db:
        e150d = await db.get(Ingredient, "e150d_sulphite_ammonia_caramel")
        bg_row = await db.get(IngredientLocalization, (e150d.id, "bg"))
        assert bg_row.translation_status == IngredientTranslationStatus.DRAFT
        assert bg_row.translation_source == IngredientTranslationSource.MACHINE_TRANSLATED
        assert bg_row.owner_approved_without_review is True

        localizations = build_localizations(e150d)
        assert "bg" in localizations
        assert localizations["bg"]["translationStatus"] == "DRAFT"
        assert localizations["bg"]["ownerApprovedWithoutReview"] is True
        assert localizations["bg"]["description"]


@pytest.mark.asyncio
async def test_human_curated_bg_translation_is_never_overwritten(seeded_engine, session_factory):
    async with session_factory() as db:
        ing = await db.get(Ingredient, "e250_sodium_nitrite")
        human_bg = await db.get(IngredientLocalization, (ing.id, "bg"))
        human_bg.translation_source = IngredientTranslationSource.HUMAN_CURATED
        human_bg.description = "Human-written description, never touch."
        await db.commit()

    await import_module.run(apply=True)

    async with session_factory() as db:
        bg_row = await db.get(IngredientLocalization, ("e250_sodium_nitrite", "bg"))
        assert bg_row.translation_source == IngredientTranslationSource.HUMAN_CURATED
        assert bg_row.description == "Human-written description, never touch."
        assert bg_row.owner_approved_without_review is False


@pytest.mark.asyncio
async def test_rollback_on_failure_leaves_no_partial_writes(seeded_engine, session_factory, monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(import_module, "_apply_create", _boom)

    with pytest.raises(RuntimeError, match="simulated failure"):
        await import_module.run(apply=True)

    async with session_factory() as db:
        e150d = await db.get(Ingredient, "e150d_sulphite_ammonia_caramel")
        assert e150d is None
        # E250's update would have been applied earlier in the same loop,
        # but the whole call is one transaction -- it must not be committed.
        e250 = await db.get(Ingredient, "e250_sodium_nitrite")
        assert e250.description == "Inorganic compound used to cure meats and inhibit Clostridium botulinum."


@pytest.mark.asyncio
async def test_existing_product_health_score_is_unchanged_by_the_import(seeded_engine, session_factory):
    async with session_factory() as db:
        ing = await db.get(Ingredient, "e250_sodium_nitrite")
        product = Product(
            barcode="0000000000001",
            product_name="Test cured meat",
            ingredient_ids=ing.id,
            sugar_grams=0, sodium_mg=0, saturated_fat_grams=0,
            has_artificial_sweeteners=False, has_preservatives=True, nova_group=4,
            health_score=50,
        )
        db.add(product)
        await db.commit()

    async def _score():
        async with session_factory() as db:
            ing = await db.get(Ingredient, "e250_sodium_nitrite")
            risk_levels = [ing.risk_level] if ing.risk_assessment_available else []
            return health_score.calculate(
                ingredient_risk_levels=risk_levels,
                sugar_grams=0.0, sodium_mg=0.0, saturated_fat_grams=0.0,
                has_artificial_sweeteners=False, has_preservatives=True, nova_group=4,
            )

    before = await _score()
    await import_module.run(apply=True)
    after = await _score()

    assert before == after

    async with session_factory() as db:
        product_after = await db.get(Product, "0000000000001")
        assert product_after.ingredient_ids == "e250_sodium_nitrite"


# --- OPENFOODTOX_APP_INTEGRATION_FIX_TASK.md defect 1 ----------------------
# "Restart overwrites imported profiles": production's SEED_ON_START=true
# re-invokes `load_seed()` on every container restart. Before the fix,
# `load_seed()`'s unconditional `session.merge` (plus `_load_bg_localizations`'s
# unconditional BG upsert) silently reverted the pilot's owner-approved EN/BG
# content back to the old curated-seed text.


class TestRestartPreservesPilotContent:
    @pytest.mark.asyncio
    async def test_en_description_survives_a_seed_reload_after_apply(self, seeded_engine, session_factory):
        assert await import_module.run(apply=True) == 0
        async with session_factory() as db:
            e951 = await db.get(Ingredient, "e951_aspartame")
            pilot_description = e951.description
        # Sanity: the pilot content actually differs from the pre-pilot
        # curated seed text this restart must not revert to.
        assert pilot_description != (
            "High-intensity artificial sweetener approximately 200 times sweeter than sucrose."
        )
        assert pilot_description

        # Simulate a production restart: SEED_ON_START=true re-invokes
        # this exact function with no pilot re-apply in between.
        await load_seed_module.load_seed()

        async with session_factory() as db:
            e951_after_restart = await db.get(Ingredient, "e951_aspartame")
            assert e951_after_restart.description == pilot_description

    @pytest.mark.asyncio
    async def test_en_description_survives_two_consecutive_seed_reloads(self, seeded_engine, session_factory):
        """The provenance marker that protects the pilot's content must
        itself survive a reseed -- otherwise the SECOND restart reverts
        the content the FIRST restart had correctly preserved."""
        assert await import_module.run(apply=True) == 0
        async with session_factory() as db:
            pilot_description = (await db.get(Ingredient, "e951_aspartame")).description

        await load_seed_module.load_seed()
        await load_seed_module.load_seed()

        async with session_factory() as db:
            e951 = await db.get(Ingredient, "e951_aspartame")
            assert e951.description == pilot_description

    @pytest.mark.asyncio
    async def test_bg_translation_survives_a_seed_reload_after_apply(self, seeded_engine, session_factory):
        assert await import_module.run(apply=True) == 0
        async with session_factory() as db:
            bg_before = await db.get(IngredientLocalization, ("e951_aspartame", "bg"))
            pilot_bg_description = bg_before.description
            assert bg_before.owner_approved_without_review is True
            assert bg_before.translation_status == IngredientTranslationStatus.DRAFT
        assert pilot_bg_description != (
            "Високоинтензивен изкуствен подсладител, приблизително 200 пъти по-сладък от захарозата."
        )

        await load_seed_module.load_seed()

        async with session_factory() as db:
            e951 = await db.get(Ingredient, "e951_aspartame")
            bg_after = await db.get(IngredientLocalization, ("e951_aspartame", "bg"))
            assert bg_after.description == pilot_bg_description
            assert bg_after.owner_approved_without_review is True
            assert bg_after.translation_status == IngredientTranslationStatus.DRAFT
            # And it must still actually be SERVED, not just stored.
            assert "bg" in build_localizations(e951)

    @pytest.mark.asyncio
    async def test_reload_still_reseeds_unrelated_ingredients_normally(self, seeded_engine, session_factory):
        """The fix must not turn `load_seed()` into a no-op for
        ingredients the pilot never touched."""
        async with session_factory() as db:
            decoy = await db.get(Ingredient, "e250_sodium_nitrite")
            decoy.description = "Corrupted by something else entirely."
            await db.commit()

        await load_seed_module.load_seed()

        async with session_factory() as db:
            restored = await db.get(Ingredient, "e250_sodium_nitrite")
            assert restored.description == (
                "Inorganic compound used to cure meats and inhibit Clostridium botulinum."
            )


# --- OPENFOODTOX_APP_INTEGRATION_FIX_TASK.md defect 2 ----------------------
# "EN-only content update makes approved BG disappear": an approved
# artifact update that changes only the EN text must not silently stop
# serving the (unchanged, still-approved) BG translation.


class TestEnOnlyApprovedUpdateKeepsBg:
    @pytest.mark.asyncio
    async def test_en_only_update_refreshes_hash_and_keeps_serving_bg(
        self, seeded_engine, session_factory, monkeypatch, tmp_path
    ):
        assert await import_module.run(apply=True) == 0
        async with session_factory() as db:
            e951_before = await db.get(Ingredient, "e951_aspartame")
            bg_before = await db.get(IngredientLocalization, ("e951_aspartame", "bg"))
            original_bg_description = bg_before.description
            assert "bg" in build_localizations(e951_before)

        artifact = json.loads(import_module._ARTIFACT_PATH.read_text(encoding="utf-8"))
        artifact["profiles"]["E951"]["description"]["en"] += " Updated, owner-approved wording."
        patched_path = tmp_path / "openfoodtox_pilot_profiles.json"
        patched_path.write_text(json.dumps(artifact), encoding="utf-8")
        monkeypatch.setattr(import_module, "_ARTIFACT_PATH", patched_path)

        assert await import_module.run(apply=True) == 0

        async with session_factory() as db:
            e951_after = await db.get(Ingredient, "e951_aspartame")
            bg_after = await db.get(IngredientLocalization, ("e951_aspartame", "bg"))
            assert e951_after.description.endswith("Updated, owner-approved wording.")
            # The BG prose itself is untouched (artifact's BG text didn't change)...
            assert bg_after.description == original_bg_description
            # ...but the hash must have been refreshed against the NEW EN content,
            # or this approved, unchanged BG translation stops being servable.
            localizations = build_localizations(e951_after)
            assert "bg" in localizations, "approved BG must still be served after an EN-only approved update"
            assert localizations["bg"]["description"] == original_bg_description

    @pytest.mark.asyncio
    async def test_unapproved_direct_en_edit_still_invalidates_bg(self, seeded_engine, session_factory):
        """The fix must not widen the hash gate into never invalidating
        BG at all -- an EN edit that does NOT go through the governed
        pilot importer (e.g. a direct, ungoverned edit to the column)
        must still make the stale BG translation stop being served."""
        assert await import_module.run(apply=True) == 0
        async with session_factory() as db:
            e951 = await db.get(Ingredient, "e951_aspartame")
            assert "bg" in build_localizations(e951)
            e951.description = "An ungoverned direct edit, never approved for this BG translation."
            await db.commit()

        async with session_factory() as db:
            e951_after = await db.get(Ingredient, "e951_aspartame")
            assert "bg" not in build_localizations(e951_after)


# --- OPENFOODTOX_APP_INTEGRATION_FIX_TASK.md defect 3 ----------------------
# "BG names are English": `_apply_bg` used to initialize/leave a new BG
# row's `commonName` from the canonical English `common_name` instead of
# an explicitly approved Bulgarian name.


class TestBgCommonNamesAreLocalized:
    @pytest.mark.asyncio
    async def test_new_ingredient_bg_common_name_is_localized(self, seeded_engine, session_factory):
        assert await import_module.run(apply=True) == 0
        async with session_factory() as db:
            e150d = await db.get(Ingredient, "e150d_sulphite_ammonia_caramel")
            localizations = build_localizations(e150d)
            assert localizations["bg"]["commonName"] == "Сулфитно-амонячен карамел"
            assert localizations["bg"]["commonName"] != e150d.common_name

    @pytest.mark.asyncio
    async def test_existing_ingredient_without_prior_bg_row_gets_localized_common_name(
        self, seeded_engine, session_factory
    ):
        """E330 (LIMITED_DATA via the CSV starter) has no pre-existing BG
        row at all -- the exact case that used to default to English."""
        assert await import_module.run(apply=True) == 0
        async with session_factory() as db:
            e330 = (await db.execute(select(Ingredient).where(Ingredient.e_number == "E330"))).scalar_one()
            assert e330.common_name == "Citric acid"
            localizations = build_localizations(e330)
            assert localizations["bg"]["commonName"] == "Лимонена киселина"
            assert localizations["bg"]["commonName"] != "Citric acid"

    @pytest.mark.asyncio
    async def test_existing_bg_common_names_are_unchanged(self, seeded_engine, session_factory):
        """E250/E951 already had correct, reviewed BG common names from
        the main curated seed -- the fix must assert the SAME approved
        name, never silently replace already-correct data."""
        async with session_factory() as db:
            bg_e250_before = await db.get(IngredientLocalization, ("e250_sodium_nitrite", "bg"))
            bg_e951_before = await db.get(IngredientLocalization, ("e951_aspartame", "bg"))
            assert bg_e250_before.common_name == "Натриев нитрит"
            assert bg_e951_before.common_name == "Аспартам"

        assert await import_module.run(apply=True) == 0

        async with session_factory() as db:
            bg_e250_after = await db.get(IngredientLocalization, ("e250_sodium_nitrite", "bg"))
            bg_e951_after = await db.get(IngredientLocalization, ("e951_aspartame", "bg"))
            assert bg_e250_after.common_name == "Натриев нитрит"
            assert bg_e951_after.common_name == "Аспартам"
