"""
English Wikipedia REST "page summary" adapter -- fallback source for a
general ingredient description when local DB/seed data is insufficient
(see `app.services.ingredient_wikipedia_enrichment`).

Endpoint: `GET {WIKIPEDIA_API_BASE_URL}/page/summary/{title}` (the
Wikimedia REST API's Page Content Service). Chosen over the general
Action API (`opensearch`/`query`) because it returns exactly the small,
curated shape this task needs -- a short extract plus the canonical
page title/URL -- in one call, with no HTML or wikitext to parse.

Same conventions as `app.integrations.barcode_providers`: a bounded
timeout, limited retry only for genuinely transient failures (timeout/
network/5xx -- never a 4xx), and a `transport` constructor param for
test injection via `httpx.MockTransport` (never a real network call in
tests). Never returns more than a short extract -- never the full
article body, and never anything beyond what `WikipediaPageSummary`
declares.
"""
import urllib.parse
from dataclasses import dataclass

import httpx
import structlog
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_fixed

logger = structlog.get_logger(__name__)

# Bounded -- this is meant to be a short fallback description (task:
# "retrieve a short description"), never the article body.
_MAX_EXTRACT_LENGTH = 1000


class WikipediaLookupError(Exception):
    """Base class for a Wikipedia adapter failure. Callers MUST catch
    this -- and every subclass -- and treat the ingredient as
    unresolved rather than letting it interrupt a batch of other
    ingredients. Never includes a response body, header, or query
    string in the message."""


class WikipediaLookupTimeoutError(WikipediaLookupError):
    pass


class WikipediaLookupRateLimitedError(WikipediaLookupError):
    pass


class WikipediaLookupHttpError(WikipediaLookupError):
    pass


class WikipediaLookupMalformedResponseError(WikipediaLookupError):
    pass


class _TransientLookupError(Exception):
    """Internal-only: marks an error tenacity should retry. Never
    escapes `_fetch_json`."""


@dataclass(frozen=True)
class WikipediaPageSummary:
    title: str
    extract: str
    page_url: str
    # Wikipedia's own signal that `title` names an ambiguous disambiguation
    # page, not a specific topic -- the caller must treat this as an
    # uncertain match, never a confident identification (task rule 3).
    is_disambiguation: bool


async def _fetch_json(
    *,
    url: str,
    headers: dict,
    timeout_seconds: float,
    max_retries: int,
    transport: httpx.BaseTransport | None,
) -> dict | None:
    """Returns the parsed JSON body, or `None` for a clean 404 (the page
    genuinely does not exist -- not an error). Raises a
    `WikipediaLookupError` subclass for anything else that prevented a
    real answer.

    `transport` is test-only: pass an `httpx.MockTransport` to exercise
    this function with zero real network access -- see
    tests/unit/test_wikipedia_api.py. Production callers never pass it."""

    async def _attempt() -> dict | None:
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds, transport=transport) as client:
                response = await client.get(url, headers=headers)
        except httpx.TimeoutException as exc:
            raise _TransientLookupError("timeout") from exc
        except httpx.HTTPError as exc:
            raise _TransientLookupError("network_error") from exc

        if response.status_code == 404:
            return None
        if response.status_code == 429:
            raise WikipediaLookupRateLimitedError("Wikipedia API rate-limited this request.")
        if 500 <= response.status_code < 600:
            raise _TransientLookupError(f"http_{response.status_code}")
        if response.status_code >= 400:
            raise WikipediaLookupHttpError(f"Wikipedia API returned status {response.status_code}.")

        try:
            return response.json()
        except ValueError as exc:
            raise WikipediaLookupMalformedResponseError("Wikipedia API response was not valid JSON.") from exc

    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(max(1, max_retries + 1)),
            wait=wait_fixed(0.2),
            retry=retry_if_exception_type(_TransientLookupError),
            reraise=True,
        ):
            with attempt:
                return await _attempt()
    except _TransientLookupError as exc:
        reason = str(exc)
        logger.warning("wikipedia_lookup_transient_failure", reason=reason)
        if reason == "timeout":
            raise WikipediaLookupTimeoutError("Wikipedia API timed out.") from exc
        raise WikipediaLookupHttpError(f"Wikipedia API failed after retries ({reason}).") from exc

    # Unreachable (AsyncRetrying with reraise=True always returns or raises above).
    raise WikipediaLookupHttpError("Wikipedia API failed for an unknown reason.")


class WikipediaApiClient:
    """Thin adapter over the English Wikipedia REST summary endpoint.
    `transport` is test-only (see tests/unit/test_wikipedia_api.py) --
    production callers never pass it."""

    def __init__(
        self,
        *,
        base_url: str,
        user_agent: str,
        timeout_seconds: float,
        max_retries: int,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._user_agent = user_agent
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._transport = transport

    async def fetch_summary(self, title: str) -> WikipediaPageSummary | None:
        """`title` is a plain ingredient name (not yet URL-encoded).
        Returns `None` when the page genuinely does not exist (a clean
        404, or a 2xx body with no usable extract). Raises a
        `WikipediaLookupError` subclass for anything else."""
        encoded = urllib.parse.quote(title.strip().replace(" ", "_"), safe="")
        url = f"{self._base_url}/page/summary/{encoded}"
        body = await _fetch_json(
            url=url,
            headers={"User-Agent": self._user_agent, "Accept": "application/json"},
            timeout_seconds=self._timeout_seconds,
            max_retries=self._max_retries,
            transport=self._transport,
        )
        if body is None:
            return None

        page_title = body.get("title")
        extract = body.get("extract")
        if not isinstance(page_title, str) or not page_title.strip():
            raise WikipediaLookupMalformedResponseError("Wikipedia summary response had no page title.")
        if not isinstance(extract, str) or not extract.strip():
            # A real page with no usable extract (e.g. a bare stub) is
            # not a usable match either -- never invented from the title.
            return None

        content_urls = body.get("content_urls")
        desktop = content_urls.get("desktop") if isinstance(content_urls, dict) else None
        page_url = (
            desktop.get("page")
            if isinstance(desktop, dict) and isinstance(desktop.get("page"), str)
            else f"https://en.wikipedia.org/wiki/{encoded}"
        )

        return WikipediaPageSummary(
            title=page_title.strip(),
            extract=extract.strip()[:_MAX_EXTRACT_LENGTH],
            page_url=page_url,
            is_disambiguation=(body.get("type") == "disambiguation"),
        )
