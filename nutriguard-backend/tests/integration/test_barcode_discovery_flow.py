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

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.integrations.barcode_providers.base import (
    BarcodeProductProvider,
    NutritionFacts,
    ProviderMetadata,
    ProviderProductResult,
    ProviderTimeoutError,
)
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

    headers = await _register_device(app_client, "discovery-local-hit")
    seed = await app_client.post(
        "/api/v1/scan/ocr-text", json={"rawText": "Sodium Nitrite, Corn Syrup"}, headers=headers
    )
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
    assert row.is_verified is False


@pytest.mark.asyncio
async def test_open_food_facts_miss_falls_back_to_upcitemdb(app_client, monkeypatch):
    _patch_providers(monkeypatch, off=FakeProvider("open_food_facts", 0.75, None), upc=FakeProvider("upcitemdb", 0.45, _upc_result()))

    headers = await _register_device(app_client, "discovery-off-miss-upc-hit")
    resp = await app_client.post(
        "/api/v1/scan/barcode", json={"barcode": VALID_UNKNOWN_BARCODE_2}, headers=headers
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["product"]["productName"] == "Acme Cereal"
    # UPCitemdb-only identity has no verified nutrition -> an INFO warning must say so.
    conditions = [w["condition"] for w in body["warnings"]]
    assert "Data Completeness" in conditions


@pytest.mark.asyncio
async def test_provider_timeout_then_successful_fallback(app_client, monkeypatch):
    _patch_providers(
        monkeypatch,
        off=FakeProvider("open_food_facts", 0.75, ProviderTimeoutError("timed out")),
        upc=FakeProvider("upcitemdb", 0.45, _upc_result(name="Timeout Fallback Item")),
    )

    headers = await _register_device(app_client, "discovery-timeout-fallback")
    resp = await app_client.post(
        "/api/v1/scan/barcode", json={"barcode": "9780201379624"}, headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["product"]["productName"] == "Timeout Fallback Item"


@pytest.mark.asyncio
async def test_provider_rate_limit_then_successful_fallback(app_client, monkeypatch):
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
    assert resp.status_code == 200
    assert resp.json()["product"]["productName"] == "Rate Limit Fallback Item"


@pytest.mark.asyncio
async def test_malformed_upstream_response_isolated_and_falls_back(app_client, monkeypatch):
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
    assert resp.status_code == 200
    assert resp.json()["product"]["productName"] == "Malformed Fallback Item"


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
