"""
DB-backed tests for `app.services.ingredient_wikipedia_enrichment` --
the Wikipedia fallback lookup for ingredient information (task: "Add
Wikipedia-API as a fallback source for ingredient information").

Uses the `db_session` fixture directly (service layer, no HTTP) and
injects a `WikipediaApiClient` backed by `httpx.MockTransport` -- no
live network access, same convention as test_barcode_providers.py.
"""
import json

import httpx
import pytest

from app.integrations.wikipedia_api import WikipediaApiClient, WikipediaLookupHttpError
from app.models.enums import IngredientSource, IngredientVerificationStatus, RiskLevel
from app.models.ingredient import Ingredient
from app.repositories import ingredient_wikipedia_lookup_repository as lookup_repo
from app.services import ingredient_wikipedia_enrichment as enrichment

MATCH_BODY = {
    "title": "Xanthan gum",
    "extract": "Xanthan gum is a polysaccharide used as a food thickening agent and stabilizer.",
    "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Xanthan_gum"}},
    "type": "standard",
}


def _blank_ingredient(**overrides) -> Ingredient:
    """A minimal, genuinely-unverified row -- mirrors what
    `ingredient_catalog._build_minimal_row` persists for a fresh OCR/
    Gemini observation with no curated match: blank description,
    `UNVERIFIED`, low confidence."""
    defaults = dict(
        id="synth_xanthan_gum",
        common_name="Xanthan Gum",
        normalized_name="xanthan gum",
        risk_level=RiskLevel.SAFE,
        risk_assessment_available=False,
        verification_status=IngredientVerificationStatus.UNVERIFIED,
        source=IngredientSource.OCR_HEURISTIC,
        confidence=0.2,
    )
    defaults.update(overrides)
    return Ingredient(**defaults)


def _json_transport(json_body: dict | None, status_code: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if json_body is None:
            return httpx.Response(status_code)
        return httpx.Response(status_code, json=json_body)

    return httpx.MockTransport(handler)


def _client(transport: httpx.MockTransport) -> WikipediaApiClient:
    return WikipediaApiClient(
        base_url="https://en.wikipedia.org/api/rest_v1",
        user_agent="NutriGuard-Test/1.0 (+https://example.invalid)",
        timeout_seconds=1.0,
        max_retries=0,
        transport=transport,
    )


class _NeverCallClient(WikipediaApiClient):
    """Proves a code path never reaches the network: any call fails
    the test immediately, rather than merely asserting an unrelated
    end-state."""

    def __init__(self) -> None:
        pass

    async def fetch_summary(self, title: str):
        raise AssertionError(f"Wikipedia API must not be called, but was called with title={title!r}")


class _RaisingClient(WikipediaApiClient):
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def fetch_summary(self, title: str):
        raise self._error


# --- 1. Local cache hit: sufficient local data -> no network call ----------


@pytest.mark.asyncio
async def test_local_cache_hit_skips_wikipedia_when_description_already_present(db_session):
    curated = _blank_ingredient(
        description="A real, already-known description.",
        verification_status=IngredientVerificationStatus.VERIFIED,
        source=IngredientSource.CURATED_SEED,
        confidence=1.0,
    )
    db_session.add(curated)
    await db_session.flush()

    await enrichment.enrich_ingredient_from_wikipedia(db_session, curated, client=_NeverCallClient())

    assert curated.description == "A real, already-known description."
    assert curated.source == IngredientSource.CURATED_SEED  # untouched
    assert await lookup_repo.get_by_ingredient_id(db_session, curated.id) is None


@pytest.mark.asyncio
async def test_negative_cache_hit_skips_repeat_network_call_within_window(db_session):
    """A prior ERROR/NOT_FOUND/etc. attempt within the negative-cache
    TTL must not trigger another network call -- task: "cache results
    to avoid unnecessary repeat requests."."""
    row = _blank_ingredient()
    db_session.add(row)
    await db_session.flush()
    await lookup_repo.record_lookup(
        db_session, ingredient_id=row.id, query_text=row.common_name, match_status="ERROR",
        review_reason="API_ERROR", error_detail="http_500",
    )
    await db_session.flush()

    await enrichment.enrich_ingredient_from_wikipedia(db_session, row, client=_NeverCallClient())

    assert row.description == ""  # still untouched


# --- 2. Successful Wikipedia lookup -----------------------------------------


@pytest.mark.asyncio
async def test_successful_lookup_merges_description_and_records_full_provenance(db_session):
    row = _blank_ingredient()
    db_session.add(row)
    await db_session.flush()

    await enrichment.enrich_ingredient_from_wikipedia(
        db_session, row, client=_client(_json_transport(MATCH_BODY))
    )

    assert row.description == MATCH_BODY["extract"]
    assert row.source == IngredientSource.WIKIPEDIA_API
    assert row.verification_status == IngredientVerificationStatus.LIMITED_DATA
    # Never promoted to VERIFIED, and regulatory fields never touched.
    assert row.efsa_status == ""
    assert row.acceptable_daily_intake == ""

    lookup = await lookup_repo.get_by_ingredient_id(db_session, row.id)
    assert lookup is not None
    assert lookup.match_status == "MATCHED"
    assert lookup.matched_page_title == "Xanthan gum"
    assert lookup.source_url == "https://en.wikipedia.org/wiki/Xanthan_gum"
    assert lookup.extracted_summary == MATCH_BODY["extract"]
    assert json.loads(lookup.fields_populated_json) == ["description"]
    assert lookup.retrieved_at is not None
    assert lookup.review_reason is None


@pytest.mark.asyncio
async def test_successful_lookup_never_overwrites_curated_or_regulatory_data(db_session):
    """Task: "External data does not overwrite curated or regulatory
    local values." A VERIFIED/CURATED_SEED row is never even attempted
    (needs_enrichment is False once VERIFIED), regardless of what
    Wikipedia would have returned."""
    curated = _blank_ingredient(
        description="Curated description.",
        efsa_status="Authorized",
        verification_status=IngredientVerificationStatus.VERIFIED,
        source=IngredientSource.CURATED_SEED,
        confidence=1.0,
    )
    db_session.add(curated)
    await db_session.flush()

    await enrichment.enrich_ingredient_from_wikipedia(
        db_session, curated, client=_client(_json_transport(MATCH_BODY))
    )

    assert curated.description == "Curated description."
    assert curated.efsa_status == "Authorized"
    assert curated.source == IngredientSource.CURATED_SEED


# --- 3. Unclear name: never searched, flagged for review -------------------


@pytest.mark.asyncio
async def test_unclear_name_is_never_searched_and_is_flagged_for_review(db_session):
    row = _blank_ingredient(id="synth_flavouring", common_name="Flavouring", normalized_name="flavouring")
    db_session.add(row)
    await db_session.flush()

    await enrichment.enrich_ingredient_from_wikipedia(db_session, row, client=_NeverCallClient())

    assert row.description == ""
    assert row.source == IngredientSource.OCR_HEURISTIC  # untouched

    lookup = await lookup_repo.get_by_ingredient_id(db_session, row.id)
    assert lookup is not None
    assert lookup.match_status == "SKIPPED_UNCLEAR_NAME"
    assert lookup.review_reason == "GENERIC_FUNCTION_TERM"


@pytest.mark.asyncio
async def test_identity_uncertain_name_is_never_searched_and_is_flagged_for_review(db_session):
    row = _blank_ingredient(identity_uncertain=True, uncertainty_reason="DUPLICATE_TOKEN_FRAGMENT")
    db_session.add(row)
    await db_session.flush()

    await enrichment.enrich_ingredient_from_wikipedia(db_session, row, client=_NeverCallClient())

    lookup = await lookup_repo.get_by_ingredient_id(db_session, row.id)
    assert lookup is not None
    assert lookup.match_status == "SKIPPED_UNCLEAR_NAME"
    assert lookup.review_reason == "IDENTITY_UNCERTAIN"


# --- 4. API error: logged, never fabricated, never blocks the batch --------


@pytest.mark.asyncio
async def test_api_error_is_recorded_and_does_not_raise(db_session):
    row = _blank_ingredient()
    db_session.add(row)
    await db_session.flush()

    await enrichment.enrich_ingredient_from_wikipedia(
        db_session, row, client=_RaisingClient(WikipediaLookupHttpError("Wikipedia API returned status 503."))
    )

    assert row.description == ""  # nothing fabricated
    assert row.source == IngredientSource.OCR_HEURISTIC  # untouched

    lookup = await lookup_repo.get_by_ingredient_id(db_session, row.id)
    assert lookup is not None
    assert lookup.match_status == "ERROR"
    assert lookup.review_reason == "API_ERROR"
    assert "503" in lookup.error_detail


@pytest.mark.asyncio
async def test_not_found_page_is_recorded_without_fabricating_a_description(db_session):
    row = _blank_ingredient(id="synth_unknown_thing", common_name="Some Very Specific Compound XYZ123")
    db_session.add(row)
    await db_session.flush()

    await enrichment.enrich_ingredient_from_wikipedia(
        db_session, row, client=_client(_json_transport(None, status_code=404))
    )

    assert row.description == ""
    lookup = await lookup_repo.get_by_ingredient_id(db_session, row.id)
    assert lookup is not None
    assert lookup.match_status == "NOT_FOUND"
