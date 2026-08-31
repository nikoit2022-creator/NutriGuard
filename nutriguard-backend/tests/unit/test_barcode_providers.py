"""
Provider-adapter unit tests. Every HTTP call is mocked via
`httpx.MockTransport` (built into httpx, already a pinned dependency) —
no live network access, per the task's testing requirements.
"""
import httpx
import pytest

from app.integrations.barcode_providers.base import (
    ProviderMalformedResponseError,
    ProviderRateLimitedError,
)
from app.integrations.barcode_providers.gs1_resolver import GS1DigitalLinkResolver
from app.integrations.barcode_providers.open_food_facts import OpenFoodFactsProvider
from app.integrations.barcode_providers.upcitemdb import UpcItemDbProvider
from app.services.barcode_validation import validate_and_normalize
from tests.fixtures.barcode_provider_responses import (
    GS1_LINKSET_EMPTY,
    GS1_LINKSET_FOUND,
    OFF_BULGARIAN_RECORD,
    OFF_EXPLICIT_ENGLISH_FIELD_ON_NON_ENGLISH_RECORD,
    OFF_FOUND_FULL,
    OFF_FRENCH_ONLY,
    OFF_MALFORMED,
    OFF_NOT_FOUND,
    UPCITEMDB_FOUND,
    UPCITEMDB_MALFORMED,
    UPCITEMDB_NOT_FOUND,
    UPCITEMDB_RATE_LIMITED,
)

BARCODE = validate_and_normalize("4006381333931")
assert BARCODE is not None


def _json_transport(json_body: dict, status_code: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=json_body)

    return httpx.MockTransport(handler)


def _sequence_transport(responses: list) -> httpx.MockTransport:
    """`responses` is a list of (status_code, json_body_or_None) tuples,
    or exceptions to raise, consumed in order across repeated calls."""
    state = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        item = responses[min(state["i"], len(responses) - 1)]
        state["i"] += 1
        if isinstance(item, Exception):
            raise item
        status, body = item
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


# --- Open Food Facts --------------------------------------------------------


@pytest.mark.asyncio
async def test_off_found_parses_identity_nutrition_and_flags():
    provider = OpenFoodFactsProvider(transport=_json_transport(OFF_FOUND_FULL))
    result = await provider.fetch(BARCODE)

    assert result is not None
    assert result.has_basic_identity
    assert result.product_name == "Fizzy Orange Soda"
    assert result.brand == "Sunburst"
    assert result.raw_ingredient_text.startswith("Carbonated Water")
    assert result.nutrition.sugar_grams == 10.5
    assert result.nutrition.sodium_mg == 40.0  # 0.04g -> 40mg
    assert result.nutrition.nova_group == 4
    assert result.dietary_flags.get("is_vegan") is True
    assert result.dietary_flags.get("is_gluten_free") is True
    assert result.nutrition.has_preservatives is True  # e211 is a preservative E-number


@pytest.mark.asyncio
async def test_off_not_found_returns_none_not_an_error():
    provider = OpenFoodFactsProvider(transport=_json_transport(OFF_NOT_FOUND))
    result = await provider.fetch(BARCODE)
    assert result is None


@pytest.mark.asyncio
async def test_off_malformed_body_raises_malformed_error():
    provider = OpenFoodFactsProvider(transport=_json_transport(OFF_MALFORMED))
    with pytest.raises(ProviderMalformedResponseError):
        await provider.fetch(BARCODE)


@pytest.mark.asyncio
async def test_off_rate_limited_raises_rate_limit_error():
    provider = OpenFoodFactsProvider(transport=_json_transport({}, status_code=429))
    with pytest.raises(ProviderRateLimitedError):
        await provider.fetch(BARCODE)


@pytest.mark.asyncio
async def test_off_transient_5xx_retries_then_succeeds():
    transport = _sequence_transport([(503, {}), (200, OFF_FOUND_FULL)])
    provider = OpenFoodFactsProvider(transport=transport)
    result = await provider.fetch(BARCODE)
    assert result is not None
    assert result.product_name == "Fizzy Orange Soda"


# --- Open Food Facts language policy (PR #7 review, finding 4) -------------
# "Latin-script detection is not language detection": a French/German/
# Albanian name is Latin script too, so only an explicit `_en`/`_bg`
# field, or the default field when OFF's own declared `lang`/`lc` is
# en/bg, may become the primary display name/ingredient text.


@pytest.mark.asyncio
async def test_off_french_only_record_is_not_used_as_identity():
    """No product_name_en/_bg, and the declared language is French --
    the language-neutral default field must NOT be used, so this
    provider result must not claim a usable identity at all (letting
    orchestration fall through to UPCitemdb instead)."""
    provider = OpenFoodFactsProvider(transport=_json_transport(OFF_FRENCH_ONLY))
    result = await provider.fetch(BARCODE)
    assert result is not None
    assert result.product_name is None
    assert result.raw_ingredient_text is None
    assert result.has_basic_identity is False


@pytest.mark.asyncio
async def test_off_explicit_english_field_used_even_on_non_english_record():
    """The record's own declared language is German, but it supplies an
    explicit English translation -- that must always be preferred,
    regardless of `lang`."""
    provider = OpenFoodFactsProvider(transport=_json_transport(OFF_EXPLICIT_ENGLISH_FIELD_ON_NON_ENGLISH_RECORD))
    result = await provider.fetch(BARCODE)
    assert result is not None
    assert result.product_name == "Hazelnut Spread"
    assert result.raw_ingredient_text == "Sugar, palm oil, hazelnuts, cocoa"


@pytest.mark.asyncio
async def test_off_bulgarian_declared_record_uses_default_field():
    """No explicit product_name_bg, but the record's own declared
    language IS Bulgarian -- the default field is accepted (and
    preserved verbatim, in Cyrillic, per the bilingual display policy)."""
    provider = OpenFoodFactsProvider(transport=_json_transport(OFF_BULGARIAN_RECORD))
    result = await provider.fetch(BARCODE)
    assert result is not None
    assert result.product_name == "Кисело Мляко"
    assert result.raw_ingredient_text == "Прясно мляко, млечни закваски"
    assert result.language == "bg"


@pytest.mark.asyncio
async def test_off_english_declared_record_uses_default_field():
    off_english_default = {
        "code": "1234567890128",
        "status": "success",
        "result": {"id": "product_found"},
        "product": {
            "product_name": "Plain English Snack",
            "ingredients_text": "Wheat flour, sugar, salt",
            "lang": "en",
        },
    }
    provider = OpenFoodFactsProvider(transport=_json_transport(off_english_default))
    result = await provider.fetch(BARCODE)
    assert result is not None
    assert result.product_name == "Plain English Snack"


# --- GS1 Digital Link resolver ----------------------------------------------


@pytest.mark.asyncio
async def test_gs1_found_returns_source_url_only_never_identity():
    provider = GS1DigitalLinkResolver(transport=_json_transport(GS1_LINKSET_FOUND))
    result = await provider.fetch(BARCODE)
    assert result is not None
    assert result.source_url == "https://brand.example.com/products/04006381333931"
    assert result.product_name is None
    assert result.has_basic_identity is False


@pytest.mark.asyncio
async def test_gs1_empty_linkset_returns_none():
    provider = GS1DigitalLinkResolver(transport=_json_transport(GS1_LINKSET_EMPTY))
    result = await provider.fetch(BARCODE)
    assert result is None


@pytest.mark.asyncio
async def test_gs1_404_returns_none():
    provider = GS1DigitalLinkResolver(transport=_json_transport({}, status_code=404))
    result = await provider.fetch(BARCODE)
    assert result is None


# --- UPCitemdb ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_upcitemdb_found_parses_identity_only():
    provider = UpcItemDbProvider(transport=_json_transport(UPCITEMDB_FOUND))
    result = await provider.fetch(BARCODE)
    assert result is not None
    assert result.product_name == "Acme Toasted Oats Cereal, 18oz"
    assert result.brand == "Acme"
    assert result.image_url == "https://images.example-upcitemdb.com/036000291452.jpg"
    # UPCitemdb must never contribute nutrition -- structurally impossible here.
    assert result.raw_ingredient_text is None
    assert result.nutrition.sugar_grams is None
    assert result.nutrition.sodium_mg is None
    assert result.nutrition.has_preservatives is None


@pytest.mark.asyncio
async def test_upcitemdb_not_found_returns_none():
    provider = UpcItemDbProvider(transport=_json_transport(UPCITEMDB_NOT_FOUND))
    result = await provider.fetch(BARCODE)
    assert result is None


@pytest.mark.asyncio
async def test_upcitemdb_over_quota_raises_rate_limit_error():
    provider = UpcItemDbProvider(transport=_json_transport(UPCITEMDB_RATE_LIMITED))
    with pytest.raises(ProviderRateLimitedError):
        await provider.fetch(BARCODE)


@pytest.mark.asyncio
async def test_upcitemdb_missing_title_raises_malformed_error():
    provider = UpcItemDbProvider(transport=_json_transport(UPCITEMDB_MALFORMED))
    with pytest.raises(ProviderMalformedResponseError):
        await provider.fetch(BARCODE)


@pytest.mark.asyncio
async def test_upcitemdb_works_without_any_credentials_configured():
    """Backend must keep working with no UPCITEMDB_API_KEY/USER_KEY set
    (the free trial tier needs none) -- see app/core/config.py defaults."""
    from app.core.config import settings

    assert settings.UPCITEMDB_API_KEY == ""
    assert settings.UPCITEMDB_USER_KEY == ""
    provider = UpcItemDbProvider(transport=_json_transport(UPCITEMDB_FOUND))
    result = await provider.fetch(BARCODE)
    assert result is not None
