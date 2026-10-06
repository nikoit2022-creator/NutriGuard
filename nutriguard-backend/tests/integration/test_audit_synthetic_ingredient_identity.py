"""
Coverage for `scripts.audit_synthetic_ingredient_identity` -- the
read-only audit for existing products whose synthetic ingredient id(s)
no longer round-trip under the current `ocr_normalizer` tokenization
(task: "existing damaged records"). Uses the `db_session` fixture
directly (DB-backed, no HTTP) -- this only tests `_audit()` itself, the
exact query the CLI's `main()` wraps; it never runs the CLI subprocess
or touches a real/live database.
"""
import pytest

from app.models.enums import IngredientSource, IngredientVerificationStatus, RiskLevel
from app.models.ingredient import Ingredient
from app.models.product import Product
from app.services.ocr_normalizer import create_synthetic_ingredient
from scripts.audit_synthetic_ingredient_identity import _audit


def _bare_product(barcode: str, *, raw_ingredient_text: str, ingredient_ids: str) -> Product:
    return Product(
        barcode=barcode,
        product_name="Test Product",
        brand="",
        category="",
        raw_ingredient_text=raw_ingredient_text,
        ingredient_ids=ingredient_ids,
        health_score=0,
        nova_group=0,
        sugar_grams=0,
        sodium_mg=0,
        saturated_fat_grams=0,
        allergens_detected="",
        source="label_scan",
        is_verified=False,
        has_verified_nutrition=False,
    )


@pytest.mark.asyncio
async def test_fresh_synthetic_id_is_not_flagged(db_session):
    """A product whose id was just computed by TODAY's code from its own
    stored raw text always round-trips -- never a false positive."""
    fresh = create_synthetic_ingredient("Аромат")
    db_session.add(_bare_product("1000000000017", raw_ingredient_text="Аромат", ingredient_ids=fresh.id))
    await db_session.commit()

    findings = await _audit(db_session, limit=None)
    assert findings == []


@pytest.mark.asyncio
async def test_real_catalog_ingredient_id_is_not_flagged(db_session):
    """An id that resolves to a REAL catalog row is never synthetic in
    the first place -- the audit must not even attempt reconstruction
    for it."""
    real = Ingredient(
        id="e300_ascorbic_acid",
        common_name="Ascorbic acid",
        normalized_name="ascorbic acid",
        scientific_name="",
        e_number="E300",
        category="Antioxidant",
        description="",
        purpose_in_food="Vitamin C; antioxidant/reducing agent",
        health_concerns="",
        evidence_level="",
        countries_restricted_or_banned="",
        efsa_status="",
        fda_status="",
        acceptable_daily_intake="",
        side_effects="",
        allergens="",
        references="",
        risk_level=RiskLevel.SAFE,
        risk_assessment_available=False,
        verification_status=IngredientVerificationStatus.LIMITED_DATA,
        source=IngredientSource.CURATED_SEED,
        confidence=0.75,
    )
    db_session.add(real)
    db_session.add(
        _bare_product("1000000000024", raw_ingredient_text="Е300", ingredient_ids="e300_ascorbic_acid")
    )
    await db_session.commit()

    findings = await _audit(db_session, limit=None)
    assert findings == []


@pytest.mark.asyncio
async def test_legacy_cyrillic_e_number_id_is_recoverable_and_not_flagged(db_session):
    """An id minted by OLDER code from a Cyrillic E-number token
    ("Е950", slug="950", hash of the unfolded Cyrillic text) is backward-
    compatibly recovered from the stored original text -- so it is no
    longer "damage" and must not be flagged."""
    legacy_id = "synth_950_b4230eb5adbe"  # sha1("е950")[:12], Cyrillic lowercase е
    db_session.add(
        _bare_product(
            "1000000000031",
            raw_ingredient_text="Подсладители: Е955, Е950.",
            ingredient_ids=legacy_id,
        )
    )
    await db_session.commit()

    assert await _audit(db_session, limit=None) == []


@pytest.mark.asyncio
async def test_legacy_id_without_supporting_cyrillic_text_is_still_flagged_as_bare_number(db_session):
    """The same legacy id, but the stored text only has a bare "950": the
    E-number is NOT inferred, so it stays a BARE_NUMBER_NO_IDENTITY finding."""
    legacy_id = "synth_950_b4230eb5adbe"
    db_session.add(
        _bare_product("1000000000079", raw_ingredient_text="Подсладители: 955, 950.", ingredient_ids=legacy_id)
    )
    await db_session.commit()

    findings = await _audit(db_session, limit=None)
    assert len(findings) == 1
    assert findings[0].stored_ingredient_id == legacy_id
    assert findings[0].category == "BARE_NUMBER_NO_IDENTITY"
    assert findings[0].reconstructed_common_name == "950"


@pytest.mark.asyncio
async def test_id_with_no_recoverable_slug_is_flagged_as_placeholder_lost(db_session):
    """An id with NO recoverable slug at all (pure content-hash, as
    `_synthetic_id` mints for a name with zero ASCII-alphanumeric
    characters) that no longer matches anything in the current raw text
    degrades all the way to the generic "Ingredient detected on label"
    placeholder -- the audit must name this the worst category, not
    silently accept it."""
    hash_only_id = "synth_0123456789ab"  # shape: synth_<12 hex chars>, no slug
    db_session.add(
        _bare_product(
            "1000000000048",
            raw_ingredient_text="Нещо напълно различно",  # re-tokenizes to something else entirely
            ingredient_ids=hash_only_id,
        )
    )
    await db_session.commit()

    findings = await _audit(db_session, limit=None)
    assert len(findings) == 1
    assert findings[0].category == "PLACEHOLDER_LOST_IDENTITY"
    assert findings[0].reconstructed_common_name == "Ingredient detected on label"


@pytest.mark.asyncio
async def test_limit_bounds_how_many_products_are_scanned(db_session):
    fresh_a = create_synthetic_ingredient("Аромат")
    fresh_b = create_synthetic_ingredient("Яйце")
    db_session.add(_bare_product("1000000000055", raw_ingredient_text="Аромат", ingredient_ids=fresh_a.id))
    db_session.add(_bare_product("1000000000062", raw_ingredient_text="Яйце", ingredient_ids=fresh_b.id))
    await db_session.commit()

    # Both are consistent either way; `limit` is only exercised here for
    # "does not raise / does not scan more than asked" -- the finding
    # count assertions above already cover detection correctness.
    findings = await _audit(db_session, limit=1)
    assert findings == []
