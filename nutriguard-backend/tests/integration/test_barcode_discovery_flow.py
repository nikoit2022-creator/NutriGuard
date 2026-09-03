"""
End-to-end tests for multi-source barcode discovery through
`POST /api/v1/scan/barcode`. Every external provider is replaced with an
in-memory fake (no HTTP, no live network) via monkeypatching the
provider classes `app.services.barcode_discovery` builds — the same
approach `test_barcode_discovery_service.py` uses at the unit level;
here the point is proving the whole stack (validation -> discovery ->
persistence -> Health Score/Warnings -> API response) works together.
"""
import asyncio
import io
import json

import pytest
from PIL import Image
from sqlalchemy import func, select

from app.core.config import settings
from app.integrations.barcode_providers.base import (
    BarcodeProductProvider,
    NutritionFacts,
    ProviderMetadata,
    ProviderProductResult,
    ProviderTimeoutError,
)
from app.integrations.gemini import gemini_service
from app.models.product import Product
from app.models.product_source import ProductSource
from app.services import barcode_discovery

# A real, checksum-valid EAN-13 not present in the seeded/local database.
VALID_UNKNOWN_BARCODE = "4006381333931"
# A second valid EAN-13, distinct from the one above, for tests that need
# their own barcode so runs don't collide with each other's persisted rows.
VALID_UNKNOWN_BARCODE_2 = "5000112548167"


class FakeProvider(BarcodeProductProvider):
    def __init__(self, name: str, trust: float, outcome, delay: float = 0.0):
        self.metadata = ProviderMetadata(name=name, base_trust=trust)
        self._outcome = outcome
        self._delay = delay

    async def fetch(self, barcode):
        if self._delay:
            await asyncio.sleep(self._delay)
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def _off_result(name="Fizzy Orange Soda", brand="Sunburst", **kw) -> ProviderProductResult:
    defaults = dict(
        provider="open_food_facts",
        external_id="ext-1",
        product_name=name,
        brand=brand,
        category="Sodas",
        image_url="https://images.example.org/front.jpg",
        raw_ingredient_text="Carbonated Water, Sugar, Citric Acid",
        nutrition=NutritionFacts(sugar_grams=10.5, sodium_mg=40.0, saturated_fat_grams=0.0, nova_group=4),
        allergens=[],
        dietary_flags={"is_vegan": True},
    )
    defaults.update(kw)
    return ProviderProductResult(**defaults)


def _upc_result(name="Acme Cereal", brand="Acme") -> ProviderProductResult:
    return ProviderProductResult(
        provider="upcitemdb",
        external_id="ext-2",
        product_name=name,
        brand=brand,
        category="Food",
        image_url="https://images.example.org/upc.jpg",
        raw_ingredient_text=None,
    )


def _patch_providers(monkeypatch, *, off=None, gs1=None, upc=None):
    monkeypatch.setattr(settings, "BARCODE_DISCOVERY_ENABLED", True)
    monkeypatch.setattr(settings, "OPEN_FOOD_FACTS_ENABLED", True)
    monkeypatch.setattr(settings, "GS1_RESOLVER_ENABLED", True)
    monkeypatch.setattr(settings, "UPCITEMDB_ENABLED", True)
    monkeypatch.setattr(barcode_discovery, "OpenFoodFactsProvider", lambda: off or FakeProvider("open_food_facts", 0.75, None))
    monkeypatch.setattr(barcode_discovery, "GS1DigitalLinkResolver", lambda: gs1 or FakeProvider("gs1_digital_link", 0.60, None))
    monkeypatch.setattr(barcode_discovery, "UpcItemDbProvider", lambda: upc or FakeProvider("upcitemdb", 0.45, None))


async def _register_device(client, device_id: str):
    resp = await client.post("/api/v1/auth/device", json={"deviceId": device_id})
    token = resp.json()["accessToken"]
    return {"Authorization": f"Bearer {token}"}


# --- Local hit short-circuits discovery -------------------------------------


@pytest.mark.asyncio
async def test_local_database_hit_makes_no_external_request(app_client, monkeypatch):
    async def _must_not_be_called(*args, **kwargs):
        raise AssertionError("discover_product must not run for a known barcode")

    monkeypatch.setattr(barcode_discovery, "discover_product", _must_not_be_called)

    # Seeds a fully verified, locally-stored product via `/scan/label-image`
    # (a single request can genuinely verify both evidence groups) --
    # review round 5, finding 1: `/scan/ocr-text` alone can no longer do
    # this, since its nutrition is always a heuristic guess, never
    # genuinely verified.
    async def fake_analyze_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        return json.dumps(
            {
                "productName": "Local Hit Product",
                "rawIngredientText": "Sodium Nitrite, Corn Syrup",
                "ingredients": [{"commonName": "Sodium Nitrite"}, {"commonName": "Corn Syrup"}],
                "sugarGrams": 2.0,
                "sodiumMg": 450.0,
                "saturatedFatGrams": 0.5,
                "nutritionBasis": "PER_100_G",
            }
        )

    monkeypatch.setattr(gemini_service, "analyze_image", fake_analyze_image)

    headers = await _register_device(app_client, "discovery-local-hit")
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), color=(4, 5, 6)).save(buf, format="JPEG")
    seed = await app_client.post(
        "/api/v1/scan/label-image",
        headers=headers,
        files={"image": ("label.jpg", buf.getvalue(), "image/jpeg")},
    )
    assert seed.status_code == 200
    barcode = seed.json()["product"]["barcode"]

    resp = await app_client.post("/api/v1/scan/barcode", json={"barcode": barcode}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["isFromDatabaseCache"] is True


# --- Successful discovery ----------------------------------------------------


@pytest.mark.asyncio
async def test_open_food_facts_successful_discovery(app_client, monkeypatch, db_session):
    off = FakeProvider("open_food_facts", 0.75, _off_result())
    _patch_providers(monkeypatch, off=off)

    headers = await _register_device(app_client, "discovery-off-success")
    resp = await app_client.post(
        "/api/v1/scan/barcode", json={"barcode": VALID_UNKNOWN_BARCODE}, headers=headers
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["product"]["barcode"] == VALID_UNKNOWN_BARCODE
    assert body["product"]["productName"] == "Fizzy Orange Soda"
    assert body["product"]["brand"] == "Sunburst"
    assert body["isFromDatabaseCache"] is False
    assert body["healthScore"] >= 0

    row = (
        await db_session.execute(select(Product).where(Product.barcode == VALID_UNKNOWN_BARCODE))
    ).scalar_one()
    assert row.source == "open_food_facts"
    # V11 (PR #9 review round 4): `is_verified` now means "both
    # evidence groups (nutrition, ingredients) are genuinely verified",
    # not "was this locally-sourced" -- a genuinely complete discovery
    # (this one has real nutrition AND real ingredients_text from
    # `_off_result()`) is verified, and correctly protected from being
    # overwritten by a later, lower-value re-discovery or a rejected
    # label scan -- see test_food_analysis_discovery_bridge.py and
    # README section 11.8.
    assert row.has_verified_nutrition is True
    assert row.has_verified_ingredients is True
    assert row.is_verified is True


@pytest.mark.asyncio
async def test_partial_nutrition_with_ingredients_still_refuses_a_score(app_client, monkeypatch):
    """PR #7 review, round 2, finding 1: ingredients present (so the
    ingredients gate alone would pass) but only ONE of the three core
    nutrition fields (sugar) present -- must still be treated as
    incomplete and return labelScanRequired, never a score computed
    with sodium/saturated-fat silently zero-filled."""
    off = FakeProvider(
        "open_food_facts",
        0.75,
        _off_result(
            name="Partial Nutrition Item",
            raw_ingredient_text="Water, Sugar, Citric Acid",
            nutrition=NutritionFacts(sugar_grams=12.0, sodium_mg=None, saturated_fat_grams=None),
        ),
    )
    _patch_providers(monkeypatch, off=off)

    headers = await _register_device(app_client, "discovery-partial-nutrition")
    resp = await app_client.post(
        "/api/v1/scan/barcode", json={"barcode": "8901058851397"}, headers=headers
    )

    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "PRODUCT_NOT_FOUND"
    assert body["error"]["details"]["labelScanRequired"] is True
    assert body["error"]["details"]["discoveredIdentity"]["productName"] == "Partial Nutrition Item"
    assert "healthScore" not in body
    assert "product" not in body


@pytest.mark.asyncio
async def test_open_food_facts_miss_falls_back_to_upcitemdb(app_client, monkeypatch, db_session):
    """UPCitemdb is identity-only by construction (see upcitemdb.py) --
    a fallback that only reaches UPCitemdb is therefore always
    materially incomplete (V6): it persists the identity (so a repeat
    scan doesn't re-hit providers) but must return `labelScanRequired`,
    never a confident score built from placeholder nutrition."""
    _patch_providers(monkeypatch, off=FakeProvider("open_food_facts", 0.75, None), upc=FakeProvider("upcitemdb", 0.45, _upc_result()))

    headers = await _register_device(app_client, "discovery-off-miss-upc-hit")
    resp = await app_client.post(
        "/api/v1/scan/barcode", json={"barcode": VALID_UNKNOWN_BARCODE_2}, headers=headers
    )

    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "PRODUCT_NOT_FOUND"
    details = body["error"]["details"]
    assert details["labelScanRequired"] is True
    assert details["discoveredIdentity"]["productName"] == "Acme Cereal"
    assert "healthScore" not in body
    assert "product" not in body

    row = (
        await db_session.execute(select(Product).where(Product.barcode == VALID_UNKNOWN_BARCODE_2))
    ).scalar_one()
    assert row.has_verified_nutrition is False
    assert row.product_name == "Acme Cereal"

    # A repeat scan must behave identically -- a local cache hit on the
    # same (still-incomplete) row, never a fabricated score.
    resp2 = await app_client.post(
        "/api/v1/scan/barcode", json={"barcode": VALID_UNKNOWN_BARCODE_2}, headers=headers
    )
    assert resp2.status_code == 404
    assert resp2.json()["error"]["details"]["labelScanRequired"] is True


@pytest.mark.asyncio
async def test_provider_timeout_then_isolated_fallback_still_refuses_fake_score(app_client, monkeypatch):
    """Proves provider isolation (OFF's timeout never propagates/crashes
    the request, UPCitemdb is still tried) AND V6 (the UPCitemdb-only
    result it falls through to is correctly treated as incomplete)."""
    _patch_providers(
        monkeypatch,
        off=FakeProvider("open_food_facts", 0.75, ProviderTimeoutError("timed out")),
        upc=FakeProvider("upcitemdb", 0.45, _upc_result(name="Timeout Fallback Item")),
    )

    headers = await _register_device(app_client, "discovery-timeout-fallback")
    resp = await app_client.post(
        "/api/v1/scan/barcode", json={"barcode": "9780201379624"}, headers=headers
    )
    assert resp.status_code == 404
    details = resp.json()["error"]["details"]
    assert details["labelScanRequired"] is True
    assert details["discoveredIdentity"]["productName"] == "Timeout Fallback Item"


@pytest.mark.asyncio
async def test_provider_rate_limit_then_isolated_fallback_still_refuses_fake_score(app_client, monkeypatch):
    from app.integrations.barcode_providers.base import ProviderRateLimitedError

    _patch_providers(
        monkeypatch,
        off=FakeProvider("open_food_facts", 0.75, ProviderRateLimitedError("rate limited")),
        upc=FakeProvider("upcitemdb", 0.45, _upc_result(name="Rate Limit Fallback Item")),
    )

    headers = await _register_device(app_client, "discovery-ratelimit-fallback")
    resp = await app_client.post(
        "/api/v1/scan/barcode", json={"barcode": "9501101530003"}, headers=headers
    )
    assert resp.status_code == 404
    details = resp.json()["error"]["details"]
    assert details["labelScanRequired"] is True
    assert details["discoveredIdentity"]["productName"] == "Rate Limit Fallback Item"


@pytest.mark.asyncio
async def test_malformed_upstream_response_isolated_fallback_still_refuses_fake_score(app_client, monkeypatch):
    from app.integrations.barcode_providers.base import ProviderMalformedResponseError

    _patch_providers(
        monkeypatch,
        off=FakeProvider("open_food_facts", 0.75, ProviderMalformedResponseError("bad json")),
        upc=FakeProvider("upcitemdb", 0.45, _upc_result(name="Malformed Fallback Item")),
    )

    headers = await _register_device(app_client, "discovery-malformed-fallback")
    resp = await app_client.post(
        "/api/v1/scan/barcode", json={"barcode": "4801981123452"}, headers=headers
    )
    assert resp.status_code == 404
    details = resp.json()["error"]["details"]
    assert details["labelScanRequired"] is True
    assert details["discoveredIdentity"]["productName"] == "Malformed Fallback Item"


@pytest.mark.asyncio
async def test_complete_open_food_facts_discovery_still_returns_a_real_score(app_client, monkeypatch):
    """Contrast case for V6: when Open Food Facts itself supplies real
    nutrition AND ingredient text, the normal scoring pipeline still
    runs and a genuine 200 with a Health Score is returned."""
    off = FakeProvider("open_food_facts", 0.75, _off_result(name="Complete Discovery Item"))
    _patch_providers(monkeypatch, off=off)

    headers = await _register_device(app_client, "discovery-complete-score")
    resp = await app_client.post(
        "/api/v1/scan/barcode", json={"barcode": "7622210410337"}, headers=headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["product"]["productName"] == "Complete Discovery Item"
    assert isinstance(body["healthScore"], int)


@pytest.mark.asyncio
async def test_all_providers_unavailable_returns_structured_not_found(app_client, monkeypatch):
    _patch_providers(
        monkeypatch,
        off=FakeProvider("open_food_facts", 0.75, ProviderTimeoutError("x")),
        gs1=FakeProvider("gs1_digital_link", 0.60, ProviderTimeoutError("x")),
        upc=FakeProvider("upcitemdb", 0.45, ProviderTimeoutError("x")),
    )

    headers = await _register_device(app_client, "discovery-all-unavailable")
    resp = await app_client.post(
        "/api/v1/scan/barcode", json={"barcode": "7622210410337"}, headers=headers
    )

    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "PRODUCT_NOT_FOUND"
    details = body["error"]["details"]
    assert details["labelScanRequired"] is True
    assert {p["provider"] for p in details["providersChecked"]} == {
        "open_food_facts",
        "gs1_digital_link",
        "upcitemdb",
    }
    assert "product" not in body


@pytest.mark.asyncio
async def test_invalid_barcode_rejected_without_any_network_call(app_client, monkeypatch):
    _patch_providers(
        monkeypatch,
        off=FakeProvider("open_food_facts", 0.75, Exception("must not be called")),
        gs1=FakeProvider("gs1_digital_link", 0.60, Exception("must not be called")),
        upc=FakeProvider("upcitemdb", 0.45, Exception("must not be called")),
    )

    headers = await _register_device(app_client, "discovery-invalid-barcode")
    # Fails the EAN-13 checksum (last digit corrupted vs. a valid one).
    resp = await app_client.post(
        "/api/v1/scan/barcode", json={"barcode": "4006381333930"}, headers=headers
    )

    assert resp.status_code == 404
    assert resp.json()["error"]["details"]["providersChecked"] == []


# --- Concurrency / dedup ------------------------------------------------------
#
# NOTE: the actual race-resolution code path (product_repository.insert_new's
# IntegrityError -> re-fetch -> confidence/verified comparison) is exercised
# directly and deterministically by
# test_persist_discovered_product_never_overwrites_verified_row_on_race below.
# A true `asyncio.gather`-driven concurrent-request test was tried here too,
# but the test suite's in-memory SQLite (a single shared connection across
# "concurrent" AsyncSessions, per tests/conftest.py) does not give two
# concurrent requests real transaction isolation the way separate Postgres
# connections would in production -- it produces spurious cross-session
# errors that are an artifact of that single connection, not of the
# discovery/persistence logic itself (the project's own README makes the
# same call for migrations: real concurrent-transaction behavior is a
# Postgres concern, not something the SQLite-backed `pytest` suite can
# faithfully exercise). The sequential test below still proves the
# practically-important guarantee: repeated discovery of the same barcode
# converges on exactly one row, never a duplicate.


@pytest.mark.asyncio
async def test_repeated_discovery_of_same_barcode_does_not_duplicate_product(app_client, monkeypatch, db_session):
    off = FakeProvider("open_food_facts", 0.75, _off_result(name="Repeated Discovery Item"))
    _patch_providers(monkeypatch, off=off)

    headers = await _register_device(app_client, "discovery-repeated")
    barcode = "8901058851397"

    resp1 = await app_client.post("/api/v1/scan/barcode", json={"barcode": barcode}, headers=headers)
    assert resp1.status_code == 200
    assert resp1.json()["isFromDatabaseCache"] is False

    resp2 = await app_client.post("/api/v1/scan/barcode", json={"barcode": barcode}, headers=headers)
    assert resp2.status_code == 200
    assert resp2.json()["isFromDatabaseCache"] is True  # second lookup is a plain local hit, no re-discovery
    assert resp2.json()["product"]["productName"] == "Repeated Discovery Item"

    count = (
        await db_session.execute(select(func.count()).select_from(Product).where(Product.barcode == barcode))
    ).scalar_one()
    assert count == 1


# --- Trust / provenance rules --------------------------------------------------


@pytest.mark.asyncio
async def test_existing_verified_local_data_is_not_overwritten(app_client, monkeypatch, db_session):
    """A product already persisted through the app's own label-scan
    pipeline (is_verified=True) must win over a later barcode-discovery
    attempt for the same barcode, even if discovery reports higher
    confidence data for it."""
    from app.services import food_analysis

    verified = Product(
        barcode=VALID_UNKNOWN_BARCODE,
        product_name="Verified Local Product",
        brand="LocalBrand",
        category="cat",
        raw_ingredient_text="water",
        ingredient_ids="",
        health_score=90,
        nova_group=1,
        sugar_grams=0,
        sodium_mg=0,
        saturated_fat_grams=0,
        allergens_detected="None",
        source="label_scan",
        is_verified=True,
        has_verified_nutrition=True,
        has_verified_ingredients=True,
    )
    db_session.add(verified)
    await db_session.flush()
    await db_session.commit()

    off = FakeProvider("open_food_facts", 0.75, _off_result(name="Should Not Win"))
    _patch_providers(monkeypatch, off=off)

    headers = await _register_device(app_client, "discovery-verified-protection")
    resp = await app_client.post(
        "/api/v1/scan/barcode", json={"barcode": VALID_UNKNOWN_BARCODE}, headers=headers
    )

    assert resp.status_code == 200
    # A local hit: discovery is never even attempted -- the verified row
    # is simply what's already there. This is the actual mechanism that
    # protects it (see `analyze_barcode`: discovery only runs on a miss).
    assert resp.json()["product"]["productName"] == "Verified Local Product"
    assert resp.json()["isFromDatabaseCache"] is True


@pytest.mark.asyncio
async def test_persist_discovered_product_never_overwrites_verified_row_on_race(db_session):
    """Directly exercises the race-resolution path in
    `food_analysis._persist_discovered_product`: a verified row that
    appears between the initial miss and the insert attempt (e.g. a
    concurrent request) must never be overwritten, regardless of the
    new discovery's confidence."""
    from app.services import food_analysis
    from app.services.barcode_validation import validate_and_normalize

    verified = Product(
        barcode=VALID_UNKNOWN_BARCODE,
        product_name="Verified Local Product",
        brand="LocalBrand",
        category="cat",
        raw_ingredient_text="water",
        ingredient_ids="",
        health_score=90,
        nova_group=1,
        sugar_grams=0,
        sodium_mg=0,
        saturated_fat_grams=0,
        allergens_detected="None",
        source="label_scan",
        is_verified=True,
        has_verified_nutrition=True,
        has_verified_ingredients=True,
    )
    db_session.add(verified)
    await db_session.flush()
    await db_session.commit()

    barcode_info = validate_and_normalize(VALID_UNKNOWN_BARCODE)
    off_result = _off_result(name="External Discovery Attempt")
    discovered = barcode_discovery.DiscoveredProduct(
        barcode=barcode_info,
        product_name="External Discovery Attempt",
        brand="ExternalBrand",
        category="cat",
        image_url=None,
        raw_ingredient_text="sugar",
        nutrition=NutritionFacts(sugar_grams=1, sodium_mg=1, saturated_fat_grams=1),
        allergens=[],
        dietary_flags={},
        language="en",
        primary_provider="open_food_facts",
        confidence=0.99,  # deliberately higher than any verified-row concept of confidence
        source_url=None,
        external_last_modified=None,
        nutrition_known=True,
        ingredients_known=True,
        is_conflicting=False,
        contributing=[off_result],
    )

    product, warnings = await food_analysis._persist_discovered_product(db_session, discovered)

    assert product.product_name == "Verified Local Product"
    assert product.brand == "LocalBrand"
    assert product.is_verified is True


@pytest.mark.asyncio
async def test_conflicting_provider_data_is_preserved_not_silently_merged(app_client, monkeypatch, db_session):
    off = FakeProvider("open_food_facts", 0.75, _off_result(name="Fizzy Orange Soda", brand=None))
    upc = FakeProvider("upcitemdb", 0.45, _upc_result(name="Totally Unrelated Snack", brand="OtherBrand"))
    _patch_providers(monkeypatch, off=off, upc=upc)

    headers = await _register_device(app_client, "discovery-conflict")
    barcode = "6291041500213"
    resp = await app_client.post("/api/v1/scan/barcode", json={"barcode": barcode}, headers=headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["product"]["productName"] == "Fizzy Orange Soda"  # never silently replaced
    conditions = [w["condition"] for w in body["warnings"]]
    assert "Data Completeness" in conditions

    sources = (
        await db_session.execute(select(ProductSource).where(ProductSource.barcode == barcode))
    ).scalars().all()
    by_provider = {s.provider: s for s in sources}
    assert by_provider["open_food_facts"].is_conflicting is True
    assert by_provider["upcitemdb"].is_conflicting is True
    # Both sources' own claims stay on disk for review, not overwritten into one row.
    assert by_provider["upcitemdb"].product_name == "Totally Unrelated Snack"
    assert by_provider["open_food_facts"].product_name == "Fizzy Orange Soda"


# --- Migration backfill (PR #7 review, finding 3) ----------------------------


def test_migration_backfills_existing_rows_as_verified():
    """Structural check mirroring the manual real-Postgres verification
    (see PR description): the migration's ADD COLUMN statements for
    is_verified/has_verified_nutrition must default existing rows to
    verified=true, not false -- local/OCR/label-image products (the
    only kind that could possibly predate this migration) are
    trustworthy by construction. Real DDL execution against Postgres
    isn't run via `pytest` -- see tests/unit/test_barcode_discovery_migration.py
    and the PR description for that verification."""
    from pathlib import Path

    migration_path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "cf5522508f9a_barcode_discovery_provenance.py"
    )
    source = migration_path.read_text()
    assert "sa.Column('is_verified', sa.Boolean(), server_default=sa.text('true')" in source
    assert "sa.Column('has_verified_nutrition', sa.Boolean(), server_default=sa.text('true')" in source


# --- Canonical barcode representation (PR #7 review, finding 5) -------------


@pytest.mark.asyncio
async def test_equivalent_barcode_representations_resolve_to_one_product(app_client, monkeypatch, db_session):
    """Scanning the UPC-A form, then later the dashed/EAN-13-equivalent
    form of the exact same barcode, must resolve to the SAME persisted
    row -- never a second discovery, never a duplicate product."""
    off = FakeProvider("open_food_facts", 0.75, _off_result(name="Canonical Form Item"))
    _patch_providers(monkeypatch, off=off)

    headers = await _register_device(app_client, "discovery-canonical-form")

    resp1 = await app_client.post(
        "/api/v1/scan/barcode", json={"barcode": "036000291452"}, headers=headers  # UPC-A
    )
    assert resp1.status_code == 200
    assert resp1.json()["isFromDatabaseCache"] is False
    canonical_barcode = resp1.json()["product"]["barcode"]
    assert canonical_barcode == "0036000291452"  # canonical GTIN-13 form, not the raw UPC-A input

    # A provider call here would prove a second discovery happened --
    # make it fail loudly so the test catches a regression.
    monkeypatch.setattr(
        barcode_discovery, "OpenFoodFactsProvider", lambda: FakeProvider("open_food_facts", 0.75, Exception("must not be called again"))
    )

    resp2 = await app_client.post(
        "/api/v1/scan/barcode", json={"barcode": "0036000291452"}, headers=headers  # explicit EAN-13 form
    )
    assert resp2.status_code == 200
    assert resp2.json()["isFromDatabaseCache"] is True
    assert resp2.json()["product"]["barcode"] == canonical_barcode

    resp3 = await app_client.post(
        "/api/v1/scan/barcode", json={"barcode": "036-000-291452"}, headers=headers  # dashed UPC-A
    )
    assert resp3.status_code == 200
    assert resp3.json()["isFromDatabaseCache"] is True
    assert resp3.json()["product"]["barcode"] == canonical_barcode

    count = (
        await db_session.execute(
            select(func.count()).select_from(Product).where(Product.barcode == canonical_barcode)
        )
    ).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_legacy_upc_a_row_is_found_by_a_later_equivalent_ean13_scan(app_client, monkeypatch, db_session):
    """PR #7 review, round 2, finding 3: a pre-existing row stored under
    the 12-digit UPC-A form (e.g. written before this canonicalization
    scheme existed, or by any other path) must still be found by a
    later scan of the EAN-13-zero-padded equivalent -- not just the
    other way around. `get_by_barcode_or_aliases` must check every
    alias, not only the raw input and the canonical GTIN-13."""
    legacy_row = Product(
        barcode="036000291452",  # legacy: stored as bare UPC-A, not the canonical GTIN-13
        product_name="Legacy UPC-A Product",
        brand="LegacyBrand",
        category="cat",
        raw_ingredient_text="water",
        ingredient_ids="",
        health_score=90,
        nova_group=1,
        sugar_grams=0,
        sodium_mg=0,
        saturated_fat_grams=0,
        allergens_detected="None",
        source="open_food_facts",
        # A fully complete legacy row (real nutrition AND ingredients,
        # matching how the pre-V11 migration backfill treats a row that
        # was already fully verified -- see the a1b2c3d4e5f6 migration):
        # `is_verified` now means "both evidence groups verified", not
        # merely "not overwritten yet".
        is_verified=True,
        source_confidence=0.75,
        has_verified_nutrition=True,
        has_verified_ingredients=True,
    )
    db_session.add(legacy_row)
    await db_session.flush()
    await db_session.commit()

    # A provider call here would prove a (wrong) second discovery
    # happened instead of finding the legacy row.
    monkeypatch.setattr(
        barcode_discovery,
        "OpenFoodFactsProvider",
        lambda: FakeProvider("open_food_facts", 0.75, Exception("must not be called -- legacy row should be found")),
    )
    monkeypatch.setattr(settings, "BARCODE_DISCOVERY_ENABLED", True)

    headers = await _register_device(app_client, "discovery-legacy-alias")
    resp = await app_client.post(
        "/api/v1/scan/barcode", json={"barcode": "0036000291452"}, headers=headers  # EAN-13-equivalent scan
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["isFromDatabaseCache"] is True
    assert body["product"]["barcode"] == "036000291452"  # the legacy row itself, not a new canonical one
    assert body["product"]["productName"] == "Legacy UPC-A Product"

    count = (
        await db_session.execute(
            select(func.count()).select_from(Product).where(
                Product.barcode.in_(["036000291452", "0036000291452"])
            )
        )
    ).scalar_one()
    assert count == 1  # no duplicate second row under the canonical form


# --- Provenance race-safety (PR #7 review, finding 6) -----------------------


@pytest.mark.asyncio
async def test_provenance_conflict_does_not_roll_back_earlier_source_in_same_transaction(db_session, monkeypatch):
    """Directly exercises `product_source_repository.record_discovery`'s
    savepoint-based conflict handling: a uniqueness conflict on a SECOND
    provider's row, within the SAME uncommitted transaction as a FIRST
    provider's already-flushed row, must not discard that first row. A
    plain `db.rollback()` (the pre-fix behavior) would have discarded
    it; only a SAVEPOINT (or an upsert) keeps it -- see
    product_source_repository.py."""
    from app.repositories import product_source_repository

    barcode = "6291041500213"
    product = Product(
        barcode=barcode,
        product_name="Race Test Product",
        brand="",
        category="cat",
        raw_ingredient_text="",
        ingredient_ids="",
        health_score=0,
        nova_group=0,
        sugar_grams=0,
        sodium_mg=0,
        saturated_fat_grams=0,
        allergens_detected="None",
        is_verified=False,
        has_verified_nutrition=False,
    )
    db_session.add(product)
    await db_session.flush()

    # Provider A's provenance row: flushed, but the transaction is NOT
    # committed yet -- mirrors _persist_discovered_product's provenance
    # loop, which commits only once, after every provider is processed.
    result_a = _off_result(name="Race Test Product")
    await product_source_repository.record_discovery(
        db_session, barcode=barcode, result=result_a, confidence=0.75,
        is_conflicting=False, used_for_persisted_product=True,
    )

    # Simulate a genuinely concurrent writer that already inserted
    # provider B's row (a real race would do this on another
    # connection/session); record_discovery's own pre-check is made to
    # miss it exactly once, exactly like a stale read in a real race.
    concurrent_row = ProductSource(
        barcode=barcode, provider="upcitemdb", confidence=0.45,
        is_conflicting=False, used_for_persisted_product=False,
    )
    db_session.add(concurrent_row)
    await db_session.flush()

    original_lookup = product_source_repository.get_by_barcode_and_provider
    call_count = {"n": 0}

    async def _stale_read_once(db, barcode_, provider):
        call_count["n"] += 1
        if call_count["n"] == 1 and provider == "upcitemdb":
            return None  # stale/racy read: "not found" even though it now exists
        return await original_lookup(db, barcode_, provider)

    monkeypatch.setattr(product_source_repository, "get_by_barcode_and_provider", _stale_read_once)

    result_b = _upc_result(name="Race Test Product Alt")
    # Must recover from the IntegrityError this deliberately provokes,
    # not raise it.
    recovered = await product_source_repository.record_discovery(
        db_session, barcode=barcode, result=result_b, confidence=0.45,
        is_conflicting=False, used_for_persisted_product=False,
    )
    assert recovered.provider == "upcitemdb"

    # The key assertion: provider A's row, flushed earlier in this same
    # still-uncommitted transaction, must have survived provider B's
    # conflict-recovery -- and the whole transaction must still commit.
    await db_session.commit()
    rows = await product_source_repository.list_for_barcode(db_session, barcode)
    assert {r.provider for r in rows} == {"open_food_facts", "upcitemdb"}
