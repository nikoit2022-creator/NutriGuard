"""
Regression coverage for the Cyrillic E-number identity/catalogue-
resolution fix (branch `fix/backend-cyrillic-e-number`).

Fixture (task regression label -- a TRANSCRIBED Bulgarian carbonated-
drink label, not an actual photographed image; this pins the
parser/API regression against known text, not real OCR/image
recognition):

    Вода, захар, въглероден диоксид; киселина: Е300; консерванти: Е202,
    Е211, аромат; стабилизатор: Е414, глицеринов естер от колофон;
    антиоксидант: аскорбинова киселина; концентрат от сок от екзотични
    плодове; регулатор на киселинността: натриев цитрат; оцветител:
    бета каротин. Подсладители: Е955, Е950.

Every E-number above is written with the CYRILLIC letter "Е" (U+0415),
visually identical to Latin "E" (U+0045). `BULGARIAN_LABEL_LATIN_E`
below is the same label with every E-number re-spelled in Latin script,
so both variants are exercised through the exact same assertions.

Covers, end-to-end through the real HTTP API (every Gemini call is
mocked -- no live network call, matching `tests/conftest.py`):
  - a barcode-not-found response (`POST /scan/barcode`, structured 404
    `labelScanRequired`) followed by label-image enrichment with that
    SAME barcode (`POST /scan/label-image`);
  - the free-text OCR path with a barcode (`POST /scan/ocr-text`);
  - that catalogue identity (E-number, `commonName`, and -- for the
    curated additives this label actually contains -- seeded narrative
    content such as `purposeInFood`) survives the INITIAL response,
    persistence, AND a subsequent product lookup (`POST /scan/barcode`
    again), which exercises `ocr_normalizer.reconstruct_synthetic_
    ingredient` re-tokenizing the persisted raw text from scratch.

Only E300, E202, E211, E955, E950 are curated/seeded ingredients (see
`app/seed/e_additives_curated_starter.csv`) -- E414 and the non-additive
tokens (water, sugar, carbon dioxide) have no catalogue row by design
(confirmed by direct seed inspection, not assumed) and are expected to
resolve to SYNTHETIC ingredients with a correct, non-fabricated name
and/or E-number -- never the generic, identity-losing "Ingredient
detected on label" placeholder, and never a fabricated score, health
claim, or verification status. No nutrition data is required for any
of this -- every assertion here is about ingredient identity alone.
"""
import io
import json

import pytest
from PIL import Image
from sqlalchemy import func, select

from app.core.config import settings
from app.integrations.barcode_providers.base import (
    BarcodeProductProvider,
    ProviderMetadata,
    ProviderTimeoutError,
)
from app.integrations.gemini import gemini_service
from app.models.enums import IngredientSource, IngredientVerificationStatus, RiskLevel
from app.models.ingredient import Ingredient
from app.models.product import Product
from app.services import barcode_discovery
from app.services.ingredient_normalization import normalize_ingredient_name

BULGARIAN_LABEL_CYRILLIC = (
    "Вода, захар, въглероден диоксид; киселина: Е300; консерванти: "
    "Е202, Е211, аромат; стабилизатор: Е414, глицеринов естер от "
    "колофон; антиоксидант: аскорбинова киселина; концентрат от сок от "
    "екзотични плодове; регулатор на киселинността: натриев цитрат; "
    "оцветител: бета каротин. Подсладители: Е955, Е950."
)
# Cyrillic "Е" (U+0415) -> Latin "E" (U+0045) everywhere it appears as
# an E-number prefix above; every other Cyrillic letter is untouched.
BULGARIAN_LABEL_LATIN_E = BULGARIAN_LABEL_CYRILLIC.replace("Е", "E")
assert BULGARIAN_LABEL_LATIN_E != BULGARIAN_LABEL_CYRILLIC
assert "Е" not in BULGARIAN_LABEL_LATIN_E

# Checksum-valid EAN-13s, not used by any other test file in this suite.
BARCODE_LABEL_IMAGE_CYRILLIC = "8200334455664"
BARCODE_LABEL_IMAGE_LATIN = "8200223344550"
BARCODE_OCR_TEXT_CYRILLIC = "8200445566778"


# The test DB fixture (`tests/conftest.py`) does NOT run the production
# seed loader (`app/seed/load_seed.py`'s `load_seed()`) -- matching the
# rest of this suite's convention (see `test_ingredient_catalog.py`'s
# `_seeded_ingredient`), the 5 curated additives this label actually
# names are inserted directly here, mirroring exactly what `load_seed()`
# persists from `app/seed/e_additives_curated_starter.csv` (id format,
# field values, and `purposeInFood` content all taken straight from
# that CSV, not invented) -- the SEPARATE question of whether that CSV
# itself loads correctly into a fresh database is already covered by
# `tests/integration/test_load_seed.py`, not re-tested here.
_CURATED_ADDITIVES = [
    dict(e_number="E300", name="Ascorbic acid", category="Antioxidant", purpose_in_food="Vitamin C; antioxidant/reducing agent"),
    dict(e_number="E202", name="Potassium sorbate", category="Preservative", purpose_in_food="Preservation against yeasts and moulds"),
    dict(e_number="E211", name="Sodium benzoate", category="Preservative", purpose_in_food="Antimicrobial preservative"),
    dict(e_number="E955", name="Sucralose", category="Sweetener", purpose_in_food="High-intensity chlorinated sucrose derivative"),
    dict(e_number="E950", name="Acesulfame K", category="Sweetener", purpose_in_food="High-intensity sweetener"),
]


def _starter_additive_id(e_number: str, name: str) -> str:
    import re as _re

    slug = _re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:48]
    return f"{e_number.lower()}_{slug}" if slug else e_number.lower()


async def _seed_curated_additives(db_session) -> None:
    for row in _CURATED_ADDITIVES:
        ingredient = Ingredient(
            id=_starter_additive_id(row["e_number"], row["name"]),
            common_name=row["name"],
            normalized_name=normalize_ingredient_name(row["name"]),
            scientific_name="",
            e_number=row["e_number"],
            category=row["category"],
            description="",
            purpose_in_food=row["purpose_in_food"],
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
        db_session.add(ingredient)
    await db_session.commit()


class _AllProvidersMiss(BarcodeProductProvider):
    """Every discovery provider genuinely has nothing for this barcode
    -- the real "barcode not found" precondition task item 2 asks for,
    not just an untested local-row absence."""

    def __init__(self, name: str, trust: float):
        self.metadata = ProviderMetadata(name=name, base_trust=trust)

    async def fetch(self, barcode):
        raise ProviderTimeoutError(f"no upstream data for {barcode} in this test")


def _patch_all_providers_miss(monkeypatch):
    monkeypatch.setattr(settings, "BARCODE_DISCOVERY_ENABLED", True)
    monkeypatch.setattr(settings, "OPEN_FOOD_FACTS_ENABLED", True)
    monkeypatch.setattr(settings, "GS1_RESOLVER_ENABLED", True)
    monkeypatch.setattr(settings, "UPCITEMDB_ENABLED", True)
    monkeypatch.setattr(
        barcode_discovery, "OpenFoodFactsProvider", lambda: _AllProvidersMiss("open_food_facts", 0.75)
    )
    monkeypatch.setattr(
        barcode_discovery, "GS1DigitalLinkResolver", lambda: _AllProvidersMiss("gs1_digital_link", 0.60)
    )
    monkeypatch.setattr(barcode_discovery, "UpcItemDbProvider", lambda: _AllProvidersMiss("upcitemdb", 0.45))


async def _register_device(client, device_id: str) -> dict:
    resp = await client.post("/api/v1/auth/device", json={"deviceId": device_id})
    token = resp.json()["accessToken"]
    return {"Authorization": f"Bearer {token}"}


def _fake_jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color=(5, 5, 5)).save(buf, format="JPEG")
    return buf.getvalue()


def _mock_label_image_gemini(monkeypatch, raw_text: str) -> None:
    payload = {
        "productName": "Exotic Fruit Soda",
        "brand": "Bolt Beverages",
        "sugarGrams": 10.0,
        "sodiumMg": 20.0,
        "saturatedFatGrams": 0.0,
        "nutritionBasis": "PER_100_ML",
        "hasArtificialSweeteners": True,
        "hasPreservatives": True,
        "isGlutenFree": None,
        "isLactoseFree": None,
        "isVegan": None,
        "isVegetarian": None,
        "isHalal": None,
        "isKosher": None,
        "novaGroup": 4,
        "rawIngredientText": raw_text,
        # Empty -> forces fallback to raw-text tokenization (same
        # convention `test_mixed_english_bulgarian_content_deduplicated_
        # end_to_end` in test_label_barcode_enrichment.py uses), so this
        # test exercises the REAL tokenizer/matcher, not a hand-fed
        # ingredient list.
        "ingredients": [],
    }

    async def fake_analyze_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        return json.dumps(payload)

    monkeypatch.setattr(gemini_service, "analyze_image", fake_analyze_image)


def _by_e_number(ingredients_out: list[dict]) -> dict[str, dict]:
    return {ing["eNumber"]: ing for ing in ingredients_out if ing.get("eNumber")}


def _assert_regression_label_identity_preserved(ingredients_out: list[dict]) -> None:
    """Shared assertions for the regression label's expected ingredient
    identities -- reused for the Cyrillic spelling, the Latin
    respelling, the label-image path, the OCR-text path, and the
    post-reload re-fetch, so all five are held to the exact same bar."""
    by_e = _by_e_number(ingredients_out)

    # The 5 curated/seeded additives this label actually names resolve
    # to their REAL catalogue row -- a stable, non-synthetic id, the
    # correct scientific/common name, and real seeded narrative content
    # (not just a bare recognized E-number with nothing behind it).
    for e_number, expected_name_fragment in (
        ("E300", "ascorbic"),
        ("E202", "sorbate"),
        ("E211", "benzoate"),
        ("E955", "sucralose"),
        ("E950", "acesulfame"),
    ):
        assert e_number in by_e, f"{e_number} missing from ingredients: {sorted(by_e)}"
        ing = by_e[e_number]
        assert not ing["id"].startswith("synth_"), (
            f"{e_number} should resolve to the curated catalog row, got synthetic id {ing['id']!r}"
        )
        assert expected_name_fragment in ing["commonName"].lower()
        assert ing["purposeInFood"], f"{e_number} lost its seeded purposeInFood description"

    # E414 has no catalogue row at all (see
    # app/seed/e_additives_curated_starter.csv) -- it is EXPECTED to
    # fall back to a synthetic ingredient, but its E-number identity
    # must still be preserved rather than silently dropped.
    assert "E414" in by_e
    assert by_e["E414"]["id"].startswith("synth_")

    # No ingredient from this label is ever reduced to the generic,
    # identity-losing placeholder, or to a bare number with no "E"
    # prefix recovered at all (both would be silent identity loss).
    common_names = {ing["commonName"] for ing in ingredients_out}
    assert "Ingredient detected on label" not in common_names
    assert "950" not in common_names
    assert "300" not in common_names

    # Non-additive, non-curated commodity ingredients are still
    # recognizable by name (never required to carry nutrition data or a
    # fabricated score/health claim to be displayed).
    lowered_names = {name.lower() for name in common_names}
    assert any("вода" in name or "water" in name for name in lowered_names)
    assert any("захар" in name or "sugar" in name for name in lowered_names)


# --- 1. Barcode not found, then Cyrillic label-image enrichment -----------


@pytest.mark.asyncio
async def test_barcode_not_found_then_cyrillic_label_image_preserves_identity(
    app_client, monkeypatch, db_session
):
    barcode = BARCODE_LABEL_IMAGE_CYRILLIC
    await _seed_curated_additives(db_session)
    _patch_all_providers_miss(monkeypatch)
    headers = await _register_device(app_client, "cyrillic-not-found-device")

    not_found = await app_client.post("/api/v1/scan/barcode", json={"barcode": barcode}, headers=headers)
    assert not_found.status_code == 404
    assert not_found.json()["error"]["details"]["labelScanRequired"] is True

    _mock_label_image_gemini(monkeypatch, BULGARIAN_LABEL_CYRILLIC)
    resp = await app_client.post(
        "/api/v1/scan/label-image",
        headers=headers,
        files={"image": ("label.jpg", _fake_jpeg_bytes(), "image/jpeg")},
        data={"barcode": barcode},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["product"]["barcode"] == barcode
    assert body["product"]["hasVerifiedIngredients"] is True
    # Original Cyrillic text is preserved verbatim, not transformed.
    assert "Е300" in body["product"]["rawIngredientText"] or "E300" in body["product"]["rawIngredientText"]
    _assert_regression_label_identity_preserved(body["ingredients"])

    # --- Persistence + subsequent product lookup ---
    reload_resp = await app_client.post("/api/v1/scan/barcode", json={"barcode": barcode}, headers=headers)
    assert reload_resp.status_code == 200
    reload_body = reload_resp.json()
    assert reload_body["isFromDatabaseCache"] is True
    _assert_regression_label_identity_preserved(reload_body["ingredients"])

    assert (
        await db_session.execute(select(func.count()).select_from(Product).where(Product.barcode == barcode))
    ).scalar_one() == 1


# --- 2. Same flow, every E-number respelled in Latin script ---------------


@pytest.mark.asyncio
async def test_barcode_not_found_then_latin_e_label_image_preserves_identity(
    app_client, monkeypatch, db_session
):
    barcode = BARCODE_LABEL_IMAGE_LATIN
    await _seed_curated_additives(db_session)
    _patch_all_providers_miss(monkeypatch)
    headers = await _register_device(app_client, "latin-e-not-found-device")

    not_found = await app_client.post("/api/v1/scan/barcode", json={"barcode": barcode}, headers=headers)
    assert not_found.status_code == 404

    _mock_label_image_gemini(monkeypatch, BULGARIAN_LABEL_LATIN_E)
    resp = await app_client.post(
        "/api/v1/scan/label-image",
        headers=headers,
        files={"image": ("label.jpg", _fake_jpeg_bytes(), "image/jpeg")},
        data={"barcode": barcode},
    )
    assert resp.status_code == 200
    body = resp.json()
    _assert_regression_label_identity_preserved(body["ingredients"])

    reload_resp = await app_client.post("/api/v1/scan/barcode", json={"barcode": barcode}, headers=headers)
    assert reload_resp.status_code == 200
    _assert_regression_label_identity_preserved(reload_resp.json()["ingredients"])


# --- 3. OCR-text path (no image) with the same Cyrillic label -------------


@pytest.mark.asyncio
async def test_ocr_text_with_barcode_cyrillic_label_preserves_identity(app_client, db_session):
    barcode = BARCODE_OCR_TEXT_CYRILLIC
    await _seed_curated_additives(db_session)
    headers = await _register_device(app_client, "cyrillic-ocr-text-device")

    resp = await app_client.post(
        "/api/v1/scan/ocr-text",
        json={"rawText": BULGARIAN_LABEL_CYRILLIC, "barcode": barcode},
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["product"]["hasVerifiedIngredients"] is True
    _assert_regression_label_identity_preserved(body["ingredients"])

    # `/scan/ocr-text` never verifies nutrition by design (documented
    # quirk -- see `analyze_ocr_text_with_barcode`'s own docstring), so
    # a SUBSEQUENT `/scan/barcode` lookup for the same barcode correctly
    # returns the structured `labelScanRequired` 404 (matching
    # `test_standalone_ocr_text_returns_success_without_health_score`'s
    # already-established pattern for this exact situation) -- but its
    # `error.details.ingredients` is real, persisted partial evidence
    # (`fetch_ingredients_for_product`, the same raw-text-reconstruction
    # path this whole test file is about -- see `analyze_barcode`),
    # which is exactly what must still carry the preserved identity.
    reload_resp = await app_client.post("/api/v1/scan/barcode", json={"barcode": barcode}, headers=headers)
    assert reload_resp.status_code == 404
    reload_details = reload_resp.json()["error"]["details"]
    assert reload_details["labelScanRequired"] is True
    _assert_regression_label_identity_preserved(reload_details["ingredients"])

    stored = await db_session.get(Product, barcode)
    assert stored is not None
    assert "Е300" in stored.raw_ingredient_text or "E300" in stored.raw_ingredient_text
