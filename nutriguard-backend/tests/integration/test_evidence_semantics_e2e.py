"""
PR #22 review (finding 2) END-TO-END, through the real HTTP endpoints
(in-memory SQLite, Gemini/providers faked -- no network).

Substring heuristics used to promote a mention ("coconut milk") into a
confirmed product-level claim (`isVegan=false`, `isLactoseFree=false`,
allergen "Milk") that then produced confirmed-incompatibility WARNINGS.
Contract now: text yields a claim only through exact ingredient-entry
identity; catalog rows only when they are trusted evidence; everything else
is unknown -- which produces neither a confirmed-incompatibility warning nor
a positive suitability claim, and never an allergen-free claim.
"""
import json

import pytest

from app.models.enums import IngredientSource, IngredientVerificationStatus, RiskLevel
from app.models.ingredient import Ingredient
from app.services.ingredient_normalization import normalize_ingredient_name

from tests.integration.test_barcode_discovery_flow import FakeProvider, _off_result, _patch_providers, _register_device
from tests.integration.test_truthful_unknown_values_e2e import (
    BARCODE,
    FLAG_KEYS,
    _dietary_titles,
    _label_image,
    _set_strict_profile,
    _verified_label_payload,
)


def _assert_unknown_everywhere(body: dict) -> None:
    """No claim either way: every flag unknown, no dietary warning, no
    positive suitability claim, and no allergen-free/detected claim."""
    product = body["product"]
    assert all(product[key] is None for key in FLAG_KEYS), {key: product[key] for key in FLAG_KEYS}
    assert all(product[key] is not True for key in FLAG_KEYS)
    assert _dietary_titles(body) == set()
    assert product["allergensDetected"] == ""
    assert product["allergensDetected"] != "None"


def _catalog_row(id_: str, name: str, **overrides) -> Ingredient:
    fields = dict(
        id=id_,
        common_name=name,
        normalized_name=normalize_ingredient_name(name),
        scientific_name="",
        risk_level=RiskLevel.SAFE,
        risk_assessment_available=False,
        verification_status=IngredientVerificationStatus.UNVERIFIED,
        source=IngredientSource.OCR_HEURISTIC,
        confidence=0.5,
    )
    fields.update(overrides)
    return Ingredient(**fields)


# --- text: mention is not identity --------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "Coconut milk (58%), water, guar gum",
        "Milk (plant-based), rice flour",  # PR #22 owner follow-up: a qualifier stays attached to its parent
        "Rice flour, milk (coconut), sugar",
        "Ingredients: sugar, milk (plant-based), salt",
        "Oat milk, almond milk, sea salt",
        "Gluten-free oat flour, sugar",
        "Free from milk, free from gluten. Water, sugar",
        "May contain: milk, soy, wheat. Rice flour, water",
        "Oat flour, Gluten-\nfree, sugar",  # line-wrapped OCR text
        "Sugar, rice flour.\nMay contain\nmilk, soy",
        "Rice flour, wheat, gluten and dairy free, sugar",
        "Gluten: none. Lactose (<0.01 g/100 g). Water",
    ],
)
async def test_scored_label_with_only_ambiguous_or_negated_mentions_is_unknown_and_never_warns(app_client, monkeypatch, text):
    """A SCORED label (personalized warnings really are computed) under a
    strict profile that avoids gluten/lactose and requires vegan/halal/kosher."""
    headers = await _register_device(app_client, "evidence-ambiguous")
    await _set_strict_profile(app_client, headers)

    resp = await _label_image(app_client, headers, monkeypatch, _verified_label_payload(text))
    assert resp.status_code == 200
    body = resp.json()
    assert body["product"]["isVerified"] is True  # so warnings were computed
    _assert_unknown_everywhere(body)


@pytest.mark.asyncio
async def test_ocr_text_with_a_plant_milk_reports_no_dairy_allergen_and_no_flags(app_client):
    headers = await _register_device(app_client, "evidence-ocr-plant-milk")
    resp = await app_client.post(
        "/api/v1/scan/ocr-text", json={"rawText": "Coconut milk, oat milk, water"}, headers=headers
    )
    assert resp.status_code == 200
    _assert_unknown_everywhere(resp.json())


@pytest.mark.asyncio
async def test_real_dairy_and_soy_declarations_in_ocr_text_are_still_reported(app_client):
    headers = await _register_device(app_client, "evidence-ocr-declared")
    resp = await app_client.post(
        "/api/v1/scan/ocr-text",
        json={"rawText": "Sugar, skimmed milk powder, soy lecithin. May contain traces of wheat."},
        headers=headers,
    )
    product = resp.json()["product"]
    assert product["allergensDetected"] == "Soy, Milk"  # real declarations, in a stable order
    assert product["isVegan"] is False  # dairy entry
    assert product["isLactoseFree"] is None  # not inferred from a milk entry
    assert product["isGlutenFree"] is None  # "may contain traces of wheat" is precautionary, not an ingredient
    assert all(product[key] is not True for key in FLAG_KEYS)


@pytest.mark.asyncio
async def test_a_genuine_ingredient_next_to_a_negated_phrase_still_warns_and_only_for_itself(app_client, monkeypatch):
    headers = await _register_device(app_client, "evidence-genuine-plus-negated")
    await _set_strict_profile(app_client, headers)

    resp = await _label_image(
        app_client, headers, monkeypatch,
        _verified_label_payload("Gluten-free oats, wheat flour, sugar. Free from milk. May contain: soy"),
    )
    body = resp.json()
    product = body["product"]
    assert product["isGlutenFree"] is False  # wheat flour is a real occurrence
    assert product["isLactoseFree"] is None and product["isVegan"] is None  # "free from milk" is not milk
    assert _dietary_titles(body) == {"Gluten Violation"}


# --- explicit supported claims vs uncertain / conflicting evidence ------------------


@pytest.mark.asyncio
async def test_an_uncertain_match_does_not_erase_an_explicit_supported_claim_but_real_evidence_does(app_client, monkeypatch):
    headers = await _register_device(app_client, "evidence-explicit")
    await _set_strict_profile(app_client, headers)
    explicit = dict(isVegan=True, isLactoseFree=True, isVegetarian=True)

    # "coconut milk" is not evidence of dairy: the explicit claims stand.
    kept = (await _label_image(app_client, headers, monkeypatch, _verified_label_payload("Coconut milk, water", **explicit))).json()
    assert kept["product"]["isVegan"] is True and kept["product"]["isLactoseFree"] is True
    assert _dietary_titles(kept) == set()

    # "skimmed milk powder" genuinely contradicts "vegan": two conflicting claims support neither.
    conflicted = (
        await _label_image(app_client, headers, monkeypatch, _verified_label_payload("Skimmed milk powder, sugar", **explicit))
    ).json()
    assert conflicted["product"]["isVegan"] is None
    assert conflicted["product"]["isLactoseFree"] is True  # a milk entry is not lactose evidence
    assert conflicted["product"]["isVegetarian"] is True
    assert _dietary_titles(conflicted) == set()  # unknown never produces a confirmed warning


@pytest.mark.asyncio
async def test_a_provider_declared_allergen_survives_while_a_plant_milk_text_adds_nothing(app_client, monkeypatch):
    off = FakeProvider(
        "open_food_facts",
        0.75,
        _off_result(
            raw_ingredient_text="Oat drink (water, oats), oat milk, salt",
            dietary_flags={"is_vegan": True},
            allergens=["Milk"],  # the provider's OWN declared allergen list: real evidence
        ),
    )
    _patch_providers(monkeypatch, off=off)
    headers = await _register_device(app_client, "evidence-provider")
    resp = await app_client.post("/api/v1/scan/barcode", json={"barcode": BARCODE}, headers=headers)
    assert resp.status_code == 200
    product = resp.json()["product"]
    assert product["allergensDetected"] == "Milk"  # declared, so kept
    assert product["isVegan"] is True  # the provider's explicit claim is not contradicted by "oat milk"
    assert product["isLactoseFree"] is None and product["isGlutenFree"] is None


@pytest.mark.asyncio
async def test_provider_ingredient_text_with_only_plant_milk_yields_unknown_flags(app_client, monkeypatch):
    off = FakeProvider(
        "open_food_facts", 0.75,
        _off_result(raw_ingredient_text="Coconut milk (58%), water, guar gum", dietary_flags={}, allergens=[]),
    )
    _patch_providers(monkeypatch, off=off)
    headers = await _register_device(app_client, "evidence-provider-plant")
    resp = await app_client.post("/api/v1/scan/barcode", json={"barcode": BARCODE}, headers=headers)
    assert resp.status_code == 200
    _assert_unknown_everywhere(resp.json())


# --- catalog routing: a bool on an untrusted row is not a product-level claim ---------


@pytest.mark.asyncio
async def test_untrusted_catalog_rows_with_definite_booleans_do_not_become_product_claims(app_client, monkeypatch, db_session):
    """A legacy/OCR-observed row can hold fabricated booleans (the columns
    only became nullable later). Matching it in a label must not turn them
    into a confirmed product-level incompatibility."""
    for id_, name, overrides in [
        ("legacy_ocr_row", "Zorbex Gum", dict()),
        ("gemini_row", "Quillon Extract", dict(source=IngredientSource.GEMINI)),
        ("limited_row", "Plexar Powder", dict(verification_status=IngredientVerificationStatus.LIMITED_DATA,
                                              source=IngredientSource.CURATED_SEED)),
    ]:
        db_session.add(_catalog_row(id_, name, is_gluten=True, is_lactose=True, is_vegan=False, is_vegetarian=False,
                                    is_halal=False, is_kosher=False, **overrides))
    await db_session.commit()

    headers = await _register_device(app_client, "evidence-untrusted-catalog")
    await _set_strict_profile(app_client, headers)
    body = (
        await _label_image(
            app_client, headers, monkeypatch, _verified_label_payload("Zorbex Gum, Quillon Extract, Plexar Powder, water")
        )
    ).json()
    assert body["product"]["isVerified"] is True
    _assert_unknown_everywhere(body)
    # the untrusted rows really were matched (not silently ignored because of a name miss)
    names = {ing["commonName"] for ing in body["ingredients"]}
    assert {"Zorbex Gum", "Quillon Extract", "Plexar Powder"} <= names


@pytest.mark.asyncio
async def test_a_trusted_generic_named_row_is_not_evidence_for_a_plant_milk_label(app_client, monkeypatch, db_session):
    """The catalog matcher links "coconut milk" to a trusted row named "Milk" by substring. That link is not identity:
    the trusted row's booleans must not become a confirmed product claim or a warning."""
    db_session.add(_catalog_row(
        "trusted_milk", "Milk", is_lactose=True, is_vegan=False, is_vegetarian=True,
        verification_status=IngredientVerificationStatus.VERIFIED, source=IngredientSource.CURATED_SEED,
        risk_assessment_available=True,
    ))
    await db_session.commit()
    headers = await _register_device(app_client, "evidence-substring-catalog")
    await _set_strict_profile(app_client, headers)

    plant = (await _label_image(app_client, headers, monkeypatch, _verified_label_payload("Coconut milk, water"))).json()
    assert "Milk" in {ing["commonName"] for ing in plant["ingredients"]}  # the substring link really happened
    _assert_unknown_everywhere(plant)

    dairy = (await _label_image(app_client, headers, monkeypatch, _verified_label_payload("Water, Milk"))).json()
    assert dairy["product"]["isLactoseFree"] is False and dairy["product"]["isVegan"] is False  # an exact entry: evidence
    assert _dietary_titles(dairy) == {"Lactose Contained", "Non-Vegan Product"}


@pytest.mark.asyncio
async def test_line_wrapped_ocr_text_reports_no_precautionary_or_negated_claim(app_client):
    headers = await _register_device(app_client, "evidence-wrapped-ocr")
    for text in ("Oat flour, Gluten-\nfree, sugar", "Sugar, rice flour.\nMay contain\nmilk, soy"):
        resp = await app_client.post("/api/v1/scan/ocr-text", json={"rawText": text}, headers=headers)
        assert resp.status_code == 200
        _assert_unknown_everywhere(resp.json())


@pytest.mark.asyncio
async def test_a_trusted_verified_catalog_row_is_still_independent_evidence(app_client, monkeypatch, db_session):
    db_session.add(_catalog_row(
        "trusted_row", "Malted Barley Extract", is_gluten=True, is_vegan=True,
        verification_status=IngredientVerificationStatus.VERIFIED, source=IngredientSource.CURATED_SEED,
        risk_assessment_available=True,
    ))
    await db_session.commit()

    headers = await _register_device(app_client, "evidence-trusted-catalog")
    await _set_strict_profile(app_client, headers)
    body = (
        await _label_image(app_client, headers, monkeypatch, _verified_label_payload("Malted Barley Extract, Пшенично брашно, water"))
    ).json()
    product = body["product"]
    assert product["isGlutenFree"] is False  # supported by the trusted row, in a mixed-language label
    assert product["isVegan"] is None  # a favourable ingredient flag never makes the PRODUCT vegan
    assert all(product[key] is not True for key in FLAG_KEYS)
    assert _dietary_titles(body) == {"Gluten Violation"}


@pytest.mark.asyncio
async def test_bulgarian_and_empty_text_stay_unknown_end_to_end(app_client, monkeypatch):
    headers = await _register_device(app_client, "evidence-bg-empty")
    await _set_strict_profile(app_client, headers)
    bulgarian = (
        await _label_image(app_client, headers, monkeypatch, _verified_label_payload("Пшенично брашно, прясно мляко, сол"))
    ).json()
    _assert_unknown_everywhere(bulgarian)

    resp = await app_client.post("/api/v1/scan/ocr-text", json={"rawText": "  ,  "}, headers=headers)
    if resp.status_code == 200:  # an empty text may also be rejected outright; either way never a claim
        _assert_unknown_everywhere(resp.json())
    else:
        assert resp.status_code in (400, 422)
