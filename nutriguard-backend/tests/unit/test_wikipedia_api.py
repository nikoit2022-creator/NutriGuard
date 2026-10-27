"""
`WikipediaApiClient` adapter unit tests. Every HTTP call is mocked via
`httpx.MockTransport` (built into httpx, already a pinned dependency) --
no live network access, same convention as
tests/unit/test_barcode_providers.py.
"""
import httpx
import pytest

from app.integrations.wikipedia_api import (
    WikipediaApiClient,
    WikipediaLookupHttpError,
    WikipediaLookupMalformedResponseError,
    WikipediaLookupRateLimitedError,
    WikipediaLookupTimeoutError,
)

SUMMARY_BODY = {
    "title": "Citric acid",
    "extract": "Citric acid is a weak organic acid found in citrus fruits.",
    "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Citric_acid"}},
    "type": "standard",
}

DISAMBIGUATION_BODY = {
    "title": "Mercury",
    "extract": "Mercury may refer to several things.",
    "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Mercury"}},
    "type": "disambiguation",
}


def _json_transport(json_body: dict | None, status_code: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if json_body is None:
            return httpx.Response(status_code)
        return httpx.Response(status_code, json=json_body)

    return httpx.MockTransport(handler)


def _client(transport: httpx.MockTransport, max_retries: int = 1) -> WikipediaApiClient:
    return WikipediaApiClient(
        base_url="https://en.wikipedia.org/api/rest_v1",
        user_agent="NutriGuard-Test/1.0 (+https://example.invalid)",
        timeout_seconds=1.0,
        max_retries=max_retries,
        transport=transport,
    )


@pytest.mark.asyncio
async def test_fetch_summary_parses_title_extract_and_url():
    client = _client(_json_transport(SUMMARY_BODY))
    summary = await client.fetch_summary("Citric acid")

    assert summary is not None
    assert summary.title == "Citric acid"
    assert summary.extract.startswith("Citric acid is a weak organic acid")
    assert summary.page_url == "https://en.wikipedia.org/wiki/Citric_acid"
    assert summary.is_disambiguation is False


@pytest.mark.asyncio
async def test_fetch_summary_returns_none_on_clean_404():
    client = _client(_json_transport(None, status_code=404))
    summary = await client.fetch_summary("Totally Not A Real Ingredient Name")
    assert summary is None


@pytest.mark.asyncio
async def test_fetch_summary_flags_disambiguation_pages():
    client = _client(_json_transport(DISAMBIGUATION_BODY))
    summary = await client.fetch_summary("Mercury")

    assert summary is not None
    assert summary.is_disambiguation is True


@pytest.mark.asyncio
async def test_fetch_summary_raises_malformed_on_missing_title():
    client = _client(_json_transport({"extract": "no title here"}))
    with pytest.raises(WikipediaLookupMalformedResponseError):
        await client.fetch_summary("Something")


@pytest.mark.asyncio
async def test_fetch_summary_returns_none_when_extract_is_blank():
    client = _client(_json_transport({"title": "Stub Page", "extract": "   "}))
    summary = await client.fetch_summary("Stub Page")
    assert summary is None


@pytest.mark.asyncio
async def test_fetch_summary_raises_rate_limited_on_429():
    client = _client(_json_transport(None, status_code=429))
    with pytest.raises(WikipediaLookupRateLimitedError):
        await client.fetch_summary("Something")


@pytest.mark.asyncio
async def test_fetch_summary_raises_http_error_on_404_retry_exhaustion_from_5xx():
    client = _client(_json_transport(None, status_code=500), max_retries=0)
    with pytest.raises(WikipediaLookupHttpError):
        await client.fetch_summary("Something")


@pytest.mark.asyncio
async def test_fetch_summary_raises_timeout_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("boom", request=request)

    client = _client(httpx.MockTransport(handler), max_retries=0)
    with pytest.raises(WikipediaLookupTimeoutError):
        await client.fetch_summary("Something")


@pytest.mark.asyncio
async def test_fetch_summary_truncates_a_very_long_extract():
    long_extract = "x" * 5000
    client = _client(_json_transport({"title": "Long Page", "extract": long_extract}))
    summary = await client.fetch_summary("Long Page")

    assert summary is not None
    assert len(summary.extract) == 1000
