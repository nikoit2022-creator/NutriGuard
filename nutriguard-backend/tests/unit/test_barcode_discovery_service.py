"""
Orchestration-level tests for `app.services.barcode_discovery`: provider
priority, error isolation (a timeout/error/rate-limit from one provider
never blocks the next), the UPCitemdb-as-fallback-only rule, and the
conflicting-data / confidence-merge rules. Every provider here is a
lightweight in-memory fake (no HTTP at all) so these tests exercise pure
orchestration logic in isolation from the adapters (already covered in
test_barcode_providers.py).
"""
import pytest

from app.core.config import settings
from app.integrations.barcode_providers.base import (
    BarcodeProductProvider,
    NutritionFacts,
    ProviderMetadata,
    ProviderProductResult,
    ProviderRateLimitedError,
    ProviderTimeoutError,
)
from app.services import barcode_discovery
from app.services.barcode_validation import validate_and_normalize

BARCODE = validate_and_normalize("4006381333931")
assert BARCODE is not None


class FakeProvider(BarcodeProductProvider):
    def __init__(self, name: str, trust: float, outcome):
        self.metadata = ProviderMetadata(name=name, base_trust=trust)
        self._outcome = outcome  # ProviderProductResult | None | Exception instance

    async def fetch(self, barcode):
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def _result(provider: str, name: str | None, brand: str | None = None, **kw) -> ProviderProductResult:
    defaults = dict(
        provider=provider,
        external_id="ext-1",
        product_name=name,
        brand=brand,
        category=None,
        image_url=None,
        raw_ingredient_text=None,
        nutrition=NutritionFacts(),
    )
    defaults.update(kw)
    return ProviderProductResult(**defaults)


@pytest.fixture(autouse=True)
def _enable_discovery(monkeypatch):
    monkeypatch.setattr(settings, "BARCODE_DISCOVERY_ENABLED", True)
    monkeypatch.setattr(settings, "OPEN_FOOD_FACTS_ENABLED", True)
    monkeypatch.setattr(settings, "GS1_RESOLVER_ENABLED", True)
    monkeypatch.setattr(settings, "UPCITEMDB_ENABLED", True)


def _patch_providers(monkeypatch, *, off=None, gs1=None, upc=None):
    monkeypatch.setattr(
        barcode_discovery,
        "OpenFoodFactsProvider",
        lambda: FakeProvider("open_food_facts", 0.75, off),
    )
    monkeypatch.setattr(
        barcode_discovery, "GS1DigitalLinkResolver", lambda: FakeProvider("gs1_digital_link", 0.60, gs1)
    )
    monkeypatch.setattr(barcode_discovery, "UpcItemDbProvider", lambda: FakeProvider("upcitemdb", 0.45, upc))


@pytest.mark.asyncio
async def test_off_success_is_used_and_upc_not_consulted(monkeypatch):
    off = _result("open_food_facts", "Fizzy Orange Soda", brand="Sunburst", raw_ingredient_text="sugar, water")
    _patch_providers(monkeypatch, off=off, gs1=None, upc=Exception("must not be called"))

    outcome = await barcode_discovery.discover_product(BARCODE)

    assert outcome.product is not None
    assert outcome.product.primary_provider == "open_food_facts"
    assert outcome.product.product_name == "Fizzy Orange Soda"
    upc_attempt = next(a for a in outcome.attempts if a.provider == "upcitemdb")
    assert upc_attempt.outcome == "skipped"  # OFF already had a complete identity


@pytest.mark.asyncio
async def test_off_miss_falls_back_to_upcitemdb(monkeypatch):
    upc = _result("upcitemdb", "Acme Cereal", brand="Acme")
    _patch_providers(monkeypatch, off=None, gs1=None, upc=upc)

    outcome = await barcode_discovery.discover_product(BARCODE)

    assert outcome.product is not None
    assert outcome.product.primary_provider == "upcitemdb"
    assert outcome.product.product_name == "Acme Cereal"
    assert outcome.product.nutrition_known is False  # UPCitemdb never supplies nutrition
    assert outcome.product.ingredients_known is False


@pytest.mark.asyncio
async def test_provider_timeout_does_not_block_next_provider(monkeypatch):
    upc = _result("upcitemdb", "Acme Cereal", brand="Acme")
    _patch_providers(monkeypatch, off=ProviderTimeoutError("timed out"), gs1=None, upc=upc)

    outcome = await barcode_discovery.discover_product(BARCODE)

    assert outcome.product is not None
    assert outcome.product.primary_provider == "upcitemdb"
    off_attempt = next(a for a in outcome.attempts if a.provider == "open_food_facts")
    assert off_attempt.outcome == "timeout"


@pytest.mark.asyncio
async def test_provider_rate_limit_does_not_block_next_provider(monkeypatch):
    upc = _result("upcitemdb", "Acme Cereal", brand="Acme")
    _patch_providers(monkeypatch, off=ProviderRateLimitedError("rate limited"), gs1=None, upc=upc)

    outcome = await barcode_discovery.discover_product(BARCODE)

    assert outcome.product is not None
    off_attempt = next(a for a in outcome.attempts if a.provider == "open_food_facts")
    assert off_attempt.outcome == "rate_limited"


@pytest.mark.asyncio
async def test_all_providers_unavailable_returns_no_product(monkeypatch):
    _patch_providers(
        monkeypatch,
        off=ProviderTimeoutError("x"),
        gs1=ProviderTimeoutError("x"),
        upc=ProviderTimeoutError("x"),
    )

    outcome = await barcode_discovery.discover_product(BARCODE)

    assert outcome.product is None
    assert {a.outcome for a in outcome.attempts} == {"timeout"}


@pytest.mark.asyncio
async def test_discovery_disabled_makes_zero_provider_calls(monkeypatch):
    monkeypatch.setattr(settings, "BARCODE_DISCOVERY_ENABLED", False)
    _patch_providers(monkeypatch, off=Exception("must not run"), gs1=Exception("must not run"), upc=Exception("must not run"))

    outcome = await barcode_discovery.discover_product(BARCODE)

    assert outcome.product is None
    assert outcome.attempts == []


@pytest.mark.asyncio
async def test_off_missing_brand_triggers_upc_supplement_without_losing_off_name(monkeypatch):
    off = _result("open_food_facts", "Fizzy Orange Soda", brand=None, raw_ingredient_text="sugar, water")
    upc = _result("upcitemdb", "Fizzy Orange Soda 500ml", brand="Sunburst")
    _patch_providers(monkeypatch, off=off, gs1=None, upc=upc)

    outcome = await barcode_discovery.discover_product(BARCODE)

    assert outcome.product is not None
    assert outcome.product.primary_provider == "open_food_facts"
    assert outcome.product.product_name == "Fizzy Orange Soda"  # OFF's own name, never overwritten
    assert outcome.product.brand == "Sunburst"  # supplemented from UPCitemdb
    assert outcome.product.is_conflicting is False  # names agree closely enough (one contains the other)


@pytest.mark.asyncio
async def test_conflicting_identity_between_off_and_upc_is_flagged_and_off_wins(monkeypatch):
    off = _result("open_food_facts", "Fizzy Orange Soda", brand=None, raw_ingredient_text="sugar, water")
    upc = _result("upcitemdb", "Completely Different Snack Bar", brand="OtherBrand")
    _patch_providers(monkeypatch, off=off, gs1=None, upc=upc)

    outcome = await barcode_discovery.discover_product(BARCODE)

    assert outcome.product is not None
    assert outcome.product.is_conflicting is True
    assert outcome.product.product_name == "Fizzy Orange Soda"  # never silently merged/replaced
    assert outcome.product.brand == ""  # UPCitemdb's conflicting brand is NOT used either


@pytest.mark.asyncio
async def test_gs1_agreement_increases_confidence(monkeypatch):
    off = _result("open_food_facts", "Fizzy Orange Soda", brand="Sunburst")
    gs1 = _result("gs1_digital_link", None, source_url="https://brand.example.com/p/1")
    _patch_providers(monkeypatch, off=off, gs1=gs1, upc=None)

    baseline = barcode_discovery.PROVIDER_BASE_TRUST["open_food_facts"]
    outcome = await barcode_discovery.discover_product(BARCODE)

    assert outcome.product is not None
    assert outcome.product.confidence > baseline
    assert outcome.product.source_url == "https://brand.example.com/p/1"


@pytest.mark.asyncio
async def test_gs1_never_counts_as_product_identity_on_its_own(monkeypatch):
    gs1 = _result("gs1_digital_link", None, source_url="https://brand.example.com/p/1")
    _patch_providers(monkeypatch, off=None, gs1=gs1, upc=None)

    outcome = await barcode_discovery.discover_product(BARCODE)

    assert outcome.product is None  # GS1 alone never "finds" a product


# --- Language policy (English/Bulgarian display names) -----------------------


@pytest.mark.asyncio
async def test_bulgarian_product_name_is_preserved(monkeypatch):
    off = _result("open_food_facts", "Портокалова Газирана Напитка", brand="Sunburst")
    _patch_providers(monkeypatch, off=off, gs1=None, upc=None)

    outcome = await barcode_discovery.discover_product(BARCODE)

    assert outcome.product is not None
    assert outcome.product.product_name == "Портокалова Газирана Напитка"


@pytest.mark.asyncio
async def test_third_language_name_is_never_used_as_primary_display_name(monkeypatch):
    # Greek script -- neither English nor Bulgarian (Latin/Cyrillic).
    off = _result("open_food_facts", "Πορτοκαλάδα", brand="Sunburst")
    _patch_providers(monkeypatch, off=off, gs1=None, upc=None)

    outcome = await barcode_discovery.discover_product(BARCODE)

    assert outcome.product is not None
    assert outcome.product.product_name != "Πορτοκαλάδα"
    assert outcome.product.product_name == "Discovered Product"  # safe fallback, not fabricated text


@pytest.mark.asyncio
async def test_literal_null_placeholder_is_not_returned_as_a_name(monkeypatch):
    off = _result("open_food_facts", "null", brand="None")
    _patch_providers(monkeypatch, off=off, gs1=None, upc=None)

    outcome = await barcode_discovery.discover_product(BARCODE)

    assert outcome.product is not None
    assert outcome.product.product_name == "Discovered Product"
    assert outcome.product.brand == ""
