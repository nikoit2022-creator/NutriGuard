"""
Persistence + product-reload regression for backward-compatible synthetic
ingredient identity (follow-up to `fix/backend-cyrillic-e-number`).

Pre-fold code hashed a Cyrillic E-number token AS WRITTEN, and pre-(c)
alias code hashed the English ALIAS of a Bulgarian word ("Water" for
"Вода") while `Product.raw_ingredient_text` kept the original. After the
fold fix those ids stopped round-tripping, so a product persisted by older
code degraded on reload ("300"-style bare numbers, hash-only names).

These tests persist a `Product` carrying exactly such legacy ids (computed
the way old code did: `_synthetic_id` over the UNFOLDED/aliased text), then
reload it through the real HTTP API (`POST /scan/barcode`, DB-cache hit).
Run against the pre-fix `ocr_normalizer`, the legacy-identity assertions
fail; with the fix they pass. Nothing here rewrites the stored row.
"""
import pytest
from sqlalchemy import select

from app.models.product import Product
from app.services.ocr_normalizer import _synthetic_id

CYR_E = "Е"
BARCODE_LEGACY = "8200556677884"
BARCODE_BARE = "8200667788995"

RAW_LEGACY_TEXT = f"Вода, захар, въглероден диоксид; стабилизатор: {CYR_E}414, аромат."


def _legacy_ids() -> list[str]:
    # As OLD code minted them: unfolded E-number token; English alias text
    # for "Вода"/"захар"; plain text for everything else.
    return [
        _synthetic_id("Water"),
        _synthetic_id("Sugar"),
        _synthetic_id("въглероден диоксид"),
        _synthetic_id(f"стабилизатор: {CYR_E}414"),
        _synthetic_id("аромат"),
    ]


def _persisted_product(barcode: str, raw_text: str, ingredient_ids: str) -> Product:
    return Product(
        barcode=barcode,
        product_name="Legacy Soda",
        brand="Bolt",
        category="Beverages",
        raw_ingredient_text=raw_text,
        ingredient_ids=ingredient_ids,
        health_score=50,
        nova_group=4,
        sugar_grams=10,
        sodium_mg=20,
        saturated_fat_grams=0,
        allergens_detected="",
        source="label_scan",
        is_verified=True,
        has_verified_nutrition=True,
        has_verified_ingredients=True,
        nutrition_basis="PER_100_ML",
    )


async def _headers(client) -> dict:
    resp = await client.post("/api/v1/auth/device", json={"deviceId": "legacy-identity-device"})
    return {"Authorization": f"Bearer {resp.json()['accessToken']}"}


@pytest.mark.asyncio
async def test_product_persisted_with_legacy_ids_reloads_with_identity(app_client, db_session):
    ids = _legacy_ids()
    db_session.add(_persisted_product(BARCODE_LEGACY, RAW_LEGACY_TEXT, ",".join(ids)))
    await db_session.commit()

    resp = await app_client.post(
        "/api/v1/scan/barcode", json={"barcode": BARCODE_LEGACY}, headers=await _headers(app_client)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["isFromDatabaseCache"] is True
    by_id = {ing["id"]: ing for ing in body["ingredients"]}

    # Every stored id is preserved verbatim (no live-record rewrite).
    assert list(by_id) == ids

    e414 = by_id[ids[3]]
    assert e414["eNumber"] == "E414"  # recovered from the stored Cyrillic-E text
    assert e414["commonName"].endswith("E414")

    # Alias-substituted legacy ids read back as the ORIGINAL stored words.
    assert by_id[ids[0]]["commonName"] == "Вода"
    assert by_id[ids[1]]["commonName"] == "Захар"

    names = {ing["commonName"] for ing in body["ingredients"]}
    assert "Ingredient detected on label" not in names
    assert "414" not in names

    # Persistence: the stored row is untouched by the read.
    row = (await db_session.execute(select(Product).where(Product.barcode == BARCODE_LEGACY))).scalar_one()
    assert row.ingredient_ids == ",".join(ids)
    assert row.raw_ingredient_text == RAW_LEGACY_TEXT


@pytest.mark.asyncio
async def test_bare_number_in_stored_text_is_never_turned_into_an_e_number_on_reload(app_client, db_session):
    legacy_id = _synthetic_id(f"{CYR_E}414")
    db_session.add(_persisted_product(BARCODE_BARE, "Вода, 414, аромат", legacy_id))
    await db_session.commit()

    resp = await app_client.post(
        "/api/v1/scan/barcode", json={"barcode": BARCODE_BARE}, headers=await _headers(app_client)
    )
    assert resp.status_code == 200, resp.text
    ing = resp.json()["ingredients"][0]
    assert ing["id"] == legacy_id
    assert not ing.get("eNumber")


@pytest.mark.asyncio
async def test_legacy_cyrillic_e_reference_reaches_the_curated_catalogue_row(app_client, db_session):
    """Existing content must be reachable: a legacy id whose stored token is
    literally "Е300" resolves to the curated E300 row (its narrative), via the
    official identifier -- for BOTH older id generations. The stored product
    row is untouched."""
    from app.models.enums import IngredientSource, IngredientVerificationStatus, RiskLevel
    from app.models.ingredient import Ingredient

    db_session.add(
        Ingredient(
            id="e300_ascorbic_acid", common_name="Ascorbic acid", normalized_name="ascorbic acid",
            scientific_name="", e_number="E300", category="Antioxidant", description="",
            purpose_in_food="Vitamin C; antioxidant/reducing agent", health_concerns="",
            evidence_level="", countries_restricted_or_banned="", efsa_status="", fda_status="",
            acceptable_daily_intake="", side_effects="", allergens="", references="",
            risk_level=RiskLevel.SAFE, risk_assessment_available=False,
            verification_status=IngredientVerificationStatus.LIMITED_DATA,
            source=IngredientSource.CURATED_SEED, confidence=0.75,
        )
    )
    gen0 = "synth_300"  # generation-0 bare slug of "Е300"
    gen2 = _synthetic_id(f"{CYR_E}300")  # hash of the unfolded token
    barcode = "8200778899006"
    stored_ids = f"{gen0},{gen2}"
    db_session.add(_persisted_product(barcode, f"Вода, {CYR_E}300", stored_ids))
    await db_session.commit()

    resp = await app_client.post("/api/v1/scan/barcode", json={"barcode": barcode}, headers=await _headers(app_client))
    assert resp.status_code == 200, resp.text
    ingredients = resp.json()["ingredients"]
    assert [i["id"] for i in ingredients] == ["e300_ascorbic_acid", "e300_ascorbic_acid"]
    assert all(i["purposeInFood"] for i in ingredients)

    row = (await db_session.execute(select(Product).where(Product.barcode == barcode))).scalar_one()
    assert row.ingredient_ids == stored_ids  # no live-record rewrite
