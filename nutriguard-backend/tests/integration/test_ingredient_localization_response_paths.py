"""
Regression coverage for the reviewed-ingredient-localization feature's
response-serialization paths, using REAL SQLAlchemy objects backed by
the test database -- not `SimpleNamespace`/fake-class stand-ins.

`tests/unit/test_ingredient_schema_data_quality.py::
test_unloaded_localization_relationship_falls_back_to_english_without_lazy_io`
and the tests in `tests/unit/test_ingredient_localization*.py` already
cover the pure-function contract with plain Python objects. This file
adds the two things those don't: (1) a GENUINELY unloaded async
relationship on a real, persistent `Ingredient` row (forced via an
explicit `lazyload()` query option, since the mapper's own
`lazy="selectin"` default already protects every normal ORM query --
see that option's own comment below for why this is the realistic way
an already-loaded object could reach a serializer without its
translations attached), and (2) the four real request/response paths
the task called out by name: a normal product detail response, a
product-listing response, a partial (`labelScanRequired`) response,
and a full label-image scan response -- each exercised through the
actual FastAPI app and a real reviewed Bulgarian row, not mocked.
"""
import pytest
from sqlalchemy import inspect as sa_inspect, select
from sqlalchemy.orm import lazyload

from app.models.enums import IngredientTranslationSource, IngredientTranslationStatus
from app.models.ingredient import Ingredient
from app.models.ingredient_localization import IngredientLocalization
from app.schemas.ingredient import IngredientOut
from app.services.food_analysis import _ingredient_out_dict, _label_scan_required_details, fetch_ingredients_for_product
from app.services.ingredient_localization import canonical_text_hash


def _curated_msg_kwargs(**overrides):
    defaults = dict(
        id="e621_msg",
        common_name="Monosodium Glutamate",
        normalized_name="monosodium glutamate",
        scientific_name="",
        e_number="E621",
        category="Flavor Enhancer",
        description="Flavor enhancer.",
        purpose_in_food="Enhances umami flavor.",
        health_concerns="Generally recognized as safe.",
        evidence_level="Strong Evidence",
        countries_restricted_or_banned="",
        efsa_status="Authorized",
        fda_status="Approved (GRAS)",
        acceptable_daily_intake="Not specified (ADI not limited)",
        side_effects="",
        allergens="",
        references="EFSA (2017) Re-evaluation of MSG",
        risk_level="SAFE",
        risk_assessment_available=True,
        verification_status="VERIFIED",
        source="CURATED_SEED",
        is_gluten=False,
        is_lactose=False,
        is_vegan=True,
        is_vegetarian=True,
        is_halal=True,
        is_kosher=True,
    )
    defaults.update(overrides)
    return defaults


async def _insert_curated_msg_with_reviewed_bg(db_session) -> Ingredient:
    ingredient = Ingredient(**_curated_msg_kwargs())
    db_session.add(ingredient)
    await db_session.flush()
    localization = IngredientLocalization(
        ingredient_id=ingredient.id,
        language="bg",
        common_name="Мононатриев глутамат",
        category="Овкусител",
        description="Овкусител.",
        purpose_in_food="Подсилва вкуса.",
        health_concerns="Обичайно се счита за безопасен.",
        evidence_level="Силни доказателства",
        countries_restricted_or_banned="",
        efsa_status="Разрешен",
        fda_status="Одобрен",
        acceptable_daily_intake="Не е определен",
        side_effects="",
        allergens="",
        translation_status=IngredientTranslationStatus.REVIEWED,
        translation_source=IngredientTranslationSource.MACHINE_TRANSLATED,
        source_content_hash=canonical_text_hash(ingredient),
    )
    db_session.add(localization)
    await db_session.commit()
    return ingredient


# --- 1. The actual MissingGreenlet failure mode, with a real, persistent,
#        genuinely-unloaded relationship (not a mock) --------------------


@pytest.mark.asyncio
async def test_a_genuinely_unloaded_relationship_on_a_real_row_falls_back_safely_in_both_serializers(db_session):
    await _insert_curated_msg_with_reviewed_bg(db_session)
    db_session.expire_all()

    # `lazyload()` forces a real, persistent Ingredient row to come back
    # with its relationship NOT preloaded, overriding the mapper's own
    # `lazy="selectin"` default for this one query -- the same shape any
    # future query-option change, `Session.merge()`, or hand-rolled
    # loader could reintroduce even though today's normal repository
    # queries never do this themselves (confirmed: no `noload`/
    # `lazyload`/`raiseload` appears anywhere else in `app/`).
    result = await db_session.execute(
        select(Ingredient).where(Ingredient.id == "e621_msg").options(lazyload(Ingredient.localization_rows))
    )
    fetched = result.scalar_one()

    state = sa_inspect(fetched)
    assert "localization_rows" in state.unloaded, "test setup didn't actually produce an unloaded relationship"

    # Touching the raw relationship synchronously is the exact crash
    # CI hit -- proves the vulnerability class is real on this object.
    with pytest.raises(Exception) as exc_info:
        _ = fetched.localization_rows
    assert "greenlet" in str(exc_info.value).lower() or "MissingGreenlet" in type(exc_info.value).__name__

    # Path 1: Pydantic schema validation (IngredientOut / normal + listing responses).
    out = IngredientOut.model_validate(fetched)
    dumped = out.model_dump(by_alias=True)
    assert dumped["localizations"].keys() == {"en"}
    assert dumped["localizations"]["en"]["commonName"] == "Monosodium Glutamate"

    # Path 2: the hand-built partial-analysis dict mirror.
    plain = _ingredient_out_dict(fetched)
    assert plain["localizations"].keys() == {"en"}
    assert plain["localizations"]["en"]["commonName"] == "Monosodium Glutamate"


# --- 2. Normal product detail response (GET /products/{barcode}) --------


@pytest.mark.asyncio
async def test_normal_product_detail_response_surfaces_reviewed_bulgarian_localization(app_client, db_session):
    from app.models.product import Product

    await _insert_curated_msg_with_reviewed_bg(db_session)
    product = Product(
        barcode="9990000000017",
        product_name="Umami Broth",
        brand="TestBrand",
        category="Soup",
        raw_ingredient_text="Water, Monosodium Glutamate",
        ingredient_ids="e621_msg",
        health_score=80,
        nova_group=3,
        sugar_grams=0,
        sodium_mg=500,
        saturated_fat_grams=0,
        allergens_detected="None",
        source="local",
        is_verified=True,
        has_verified_nutrition=True,
        has_verified_ingredients=True,
    )
    db_session.add(product)
    await db_session.commit()

    resp = await app_client.post("/api/v1/auth/device", json={"deviceId": "loc-product-detail-device"})
    headers = {"Authorization": f"Bearer {resp.json()['accessToken']}"}

    resp = await app_client.get("/api/v1/products/9990000000017", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    ingredient = next(i for i in body["ingredients"] if i["id"] == "e621_msg")
    assert ingredient["localizations"]["en"]["commonName"] == "Monosodium Glutamate"
    assert ingredient["localizations"]["bg"]["commonName"] == "Мононатриев глутамат"
    assert ingredient["localizations"]["bg"]["translationStatus"] == "REVIEWED"
    # Scientific identity/citations are unaffected by localization.
    assert ingredient["eNumber"] == "E621"
    assert ingredient["references"] == "EFSA (2017) Re-evaluation of MSG"


# --- 3. Product listing (GET /ingredients) --------------------------------


@pytest.mark.asyncio
async def test_ingredient_listing_response_surfaces_reviewed_bulgarian_localization(app_client, db_session):
    await _insert_curated_msg_with_reviewed_bg(db_session)

    resp = await app_client.post("/api/v1/auth/device", json={"deviceId": "loc-listing-device"})
    headers = {"Authorization": f"Bearer {resp.json()['accessToken']}"}

    resp = await app_client.get("/api/v1/ingredients", headers=headers, params={"search": "Monosodium"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["totalItems"] >= 1
    ingredient = next(i for i in body["items"] if i["id"] == "e621_msg")
    assert ingredient["localizations"]["bg"]["commonName"] == "Мононатриев глутамат"
    assert ingredient["localizations"]["en"]["commonName"] == "Monosodium Glutamate"


# --- 4. Partial (labelScanRequired) hand-built response -------------------


@pytest.mark.asyncio
async def test_partial_label_scan_required_response_includes_reviewed_bulgarian_localization(db_session):
    from app.models.product import Product

    await _insert_curated_msg_with_reviewed_bg(db_session)
    product = Product(
        barcode="9990000000024",
        product_name="Discovered But Incomplete Broth",
        brand="TestBrand",
        category="Soup",
        raw_ingredient_text="Water, Monosodium Glutamate",
        ingredient_ids="e621_msg",
        health_score=0,
        nova_group=1,
        allergens_detected="None",
        source="open_food_facts",
        is_verified=False,
        has_verified_nutrition=False,
        has_verified_ingredients=True,
    )
    db_session.add(product)
    await db_session.commit()

    ingredients = await fetch_ingredients_for_product(db_session, product)
    details = _label_scan_required_details(product, ingredients)

    assert details["labelScanRequired"] is True
    assert details["ingredientsScanRequired"] is False
    assert details["nutritionScanRequired"] is True
    partial_ingredient = next(i for i in details["ingredients"] if i["id"] == "e621_msg")
    assert partial_ingredient["localizations"]["bg"]["commonName"] == "Мононатриев глутамат"
    assert partial_ingredient["localizations"]["en"]["commonName"] == "Monosodium Glutamate"


# --- 5. Full label-image scan response ------------------------------------


@pytest.mark.asyncio
async def test_label_image_scan_response_surfaces_reviewed_bulgarian_localization_for_a_recognized_ingredient(
    app_client, monkeypatch, db_session
):
    import io
    import json as _json

    from PIL import Image

    from app.integrations.gemini import gemini_service

    await _insert_curated_msg_with_reviewed_bg(db_session)

    payload = {
        "productName": "Instant Ramen",
        "brand": "NoodleCo",
        "sugarGrams": 1.0,
        "sodiumMg": 900.0,
        "saturatedFatGrams": 2.0,
        "nutritionBasis": "PER_100G",
        "hasArtificialSweeteners": False,
        "hasPreservatives": True,
        "isGlutenFree": False,
        "isLactoseFree": True,
        "isVegan": True,
        "isVegetarian": True,
        "isHalal": True,
        "isKosher": True,
        "novaGroup": 4,
        "rawIngredientText": "Noodles, Salt, Monosodium Glutamate (E621)",
        "ingredients": [
            {"commonName": "Monosodium Glutamate", "eNumber": "E621"},
        ],
    }

    async def fake_analyze_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        return _json.dumps(payload)

    monkeypatch.setattr(gemini_service, "analyze_image", fake_analyze_image)

    resp = await app_client.post("/api/v1/auth/device", json={"deviceId": "loc-label-scan-device"})
    headers = {"Authorization": f"Bearer {resp.json()['accessToken']}"}

    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color=(1, 2, 3)).save(buf, format="JPEG")
    files = {"image": ("label.jpg", buf.getvalue(), "image/jpeg")}

    resp = await app_client.post("/api/v1/scan/label-image", headers=headers, files=files)
    assert resp.status_code == 200
    body = resp.json()
    msg_ingredient = next(i for i in body["ingredients"] if i["eNumber"] == "E621")
    # Resolved via the official E-number identifier to the SAME curated
    # row inserted above (not a new synthetic stub), so its reviewed
    # Bulgarian localization is attached automatically.
    assert msg_ingredient["id"] == "e621_msg"
    assert msg_ingredient["localizations"]["bg"]["commonName"] == "Мононатриев глутамат"
    assert msg_ingredient["localizations"]["en"]["commonName"] == "Monosodium Glutamate"
