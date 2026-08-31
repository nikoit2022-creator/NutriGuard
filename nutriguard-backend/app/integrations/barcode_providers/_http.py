"""
Shared HTTP helper for barcode provider adapters: one place that applies
the connect/read timeout, the limited-retry policy, and the
transient-vs-fatal error classification, so `open_food_facts.py`,
`gs1_resolver.py` and `upcitemdb.py` don't each reimplement it.

Retries only ever happen for genuinely transient failures (timeout,
network error, 5xx) — never for a 4xx or a malformed body, since retrying
those wastes the provider's rate-limit budget for no benefit. A 429 is
treated as fatal-for-this-attempt too (see `ProviderRateLimitedError`):
retrying immediately into a rate limit would be counterproductive: NEXT
provider is the correct move, not a retry loop.
"""
import httpx
import structlog
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_fixed

from app.integrations.barcode_providers.base import (
    ProviderHttpError,
    ProviderMalformedResponseError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
)

logger = structlog.get_logger(__name__)


class _TransientProviderError(Exception):
    """Internal-only: marks an error tenacity should retry. Never
    escapes `get_json`."""


async def get_json(
    *,
    provider: str,
    url: str,
    params: dict | None,
    headers: dict | None,
    timeout_seconds: float,
    max_retries: int,
    transport: httpx.BaseTransport | None = None,
) -> dict:
    """GETs `url` and returns the parsed JSON body. Raises a
    `ProviderError` subclass on any failure; never logs the URL's query
    string, headers, or response body (only the provider name, status
    code, and error class).

    `transport` is test-only: pass an `httpx.MockTransport` to exercise
    this function (and the adapters built on it) with zero real network
    access — see tests/unit/test_barcode_providers.py. Production
    callers never pass it, so `None` -> a real `httpx.AsyncClient`."""

    async def _attempt() -> dict:
        try:
            # GS1 Digital Link resolvers work by redirecting (301/302/307)
            # to the actual linked resource -- httpx defaults to NOT
            # following redirects, which would otherwise misparse a
            # redirect response as malformed JSON. Following redirects is
            # a no-op for Open Food Facts/UPCitemdb, which don't redirect.
            async with httpx.AsyncClient(
                timeout=timeout_seconds, transport=transport, follow_redirects=True
            ) as client:
                response = await client.get(url, params=params, headers=headers)
        except httpx.TimeoutException as exc:
            raise _TransientProviderError("timeout") from exc
        except httpx.HTTPError as exc:
            raise _TransientProviderError("network_error") from exc

        if response.status_code == 429:
            raise ProviderRateLimitedError(f"{provider} rate-limited this request.")
        if response.status_code == 404:
            # Not "malformed" or "unavailable" — the caller decides how
            # to interpret a clean 404 (usually: barcode not found here).
            return {"__not_found__": True}
        if 500 <= response.status_code < 600:
            raise _TransientProviderError(f"http_{response.status_code}")
        if response.status_code >= 400:
            raise ProviderHttpError(f"{provider} returned status {response.status_code}.")

        try:
            return response.json()
        except ValueError as exc:
            raise ProviderMalformedResponseError(f"{provider} response was not valid JSON.") from exc

    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(max(1, max_retries + 1)),
            wait=wait_fixed(0.2),
            retry=retry_if_exception_type(_TransientProviderError),
            reraise=True,
        ):
            with attempt:
                return await _attempt()
    except _TransientProviderError as exc:
        reason = str(exc)
        logger.warning("barcode_provider_transient_failure", provider=provider, reason=reason)
        if reason == "timeout":
            raise ProviderTimeoutError(f"{provider} timed out.") from exc
        raise ProviderHttpError(f"{provider} failed after retries ({reason}).") from exc

    # Unreachable (AsyncRetrying with reraise=True always returns or raises above).
    raise ProviderHttpError(f"{provider} failed for an unknown reason.")
