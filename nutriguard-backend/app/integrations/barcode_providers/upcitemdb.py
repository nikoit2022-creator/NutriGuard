"""
UPCitemdb adapter — fallback-only source used when neither the local
database, Open Food Facts, nor a GS1 resolution produced a usable
product identity. UPCitemdb's own catalog is retail/commerce-oriented,
not scientific: it is a reasonable source for a product's name, brand,
category and image, and is NOT trusted for nutrition/health data at all
(it rarely has any, and what little it has is not sourced the way a food
database's is) — see `barcode_discovery.py`, which never reads a
`NutritionFacts` field off a UPCitemdb result.

Endpoint (free "trial" tier, no credentials needed):
`GET {UPCITEMDB_BASE_URL}/lookup?upc={gtin13}`. When
`UPCITEMDB_USER_KEY`/`UPCITEMDB_API_KEY` are configured (paid plan),
they're sent as request headers only — never logged, never put in a
query string, never returned to the client.
"""
from typing import Any

import structlog

from app.core.config import settings
from app.integrations.barcode_providers._http import get_json
from app.integrations.barcode_providers.base import (
    BarcodeProductProvider,
    ProviderMalformedResponseError,
    ProviderMetadata,
    ProviderProductResult,
    ProviderRateLimitedError,
)
from app.services.barcode_validation import BarcodeInfo

logger = structlog.get_logger(__name__)

_RATE_LIMIT_CODES = {"OVER_QUOTA", "OVER_RATE", "EXCEED_LIMIT"}
_NOT_FOUND_CODES = {"INVALID_UPC"}  # a structurally-valid-to-us GTIN UPCitemdb doesn't recognize at all


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if settings.UPCITEMDB_USER_KEY:
        headers["user_key"] = settings.UPCITEMDB_USER_KEY
    if settings.UPCITEMDB_API_KEY:
        headers["Authorization"] = f"Bearer {settings.UPCITEMDB_API_KEY}"
    return headers


def _first_image(item: dict) -> str | None:
    images = item.get("images")
    if isinstance(images, list):
        for img in images:
            if isinstance(img, str) and img.strip():
                return img.strip()
    return None


class UpcItemDbProvider(BarcodeProductProvider):
    metadata = ProviderMetadata(name="upcitemdb", base_trust=0.45)

    def __init__(self, transport=None) -> None:
        self._transport = transport  # test-only, see _http.get_json

    def _endpoint(self) -> str:
        return f"{settings.UPCITEMDB_BASE_URL.rstrip('/')}/lookup"

    async def fetch(self, barcode: BarcodeInfo) -> ProviderProductResult | None:
        body = await get_json(
            provider=self.metadata.name,
            url=self._endpoint(),
            params={"upc": barcode.gtin13},
            headers=_headers(),
            timeout_seconds=settings.BARCODE_PROVIDER_TIMEOUT_SECONDS,
            max_retries=settings.BARCODE_PROVIDER_MAX_RETRIES,
            transport=self._transport,
        )

        if body.get("__not_found__"):
            return None

        code = body.get("code")
        if code in _RATE_LIMIT_CODES:
            raise ProviderRateLimitedError("upcitemdb rate-limited this request.")
        if code in _NOT_FOUND_CODES:
            return None
        if code != "OK":
            raise ProviderMalformedResponseError("upcitemdb returned an unexpected code.")

        items = body.get("items")
        if not isinstance(items, list) or not items:
            return None

        item: dict[str, Any] = items[0]
        title = item.get("title")
        if not isinstance(title, str) or not title.strip():
            raise ProviderMalformedResponseError("upcitemdb item had no usable title.")

        return ProviderProductResult(
            provider=self.metadata.name,
            external_id=item.get("ean") or item.get("upc") or barcode.gtin13,
            product_name=title,
            brand=item.get("brand") if isinstance(item.get("brand"), str) else None,
            category=item.get("category") if isinstance(item.get("category"), str) else None,
            image_url=_first_image(item),
            raw_ingredient_text=None,  # UPCitemdb has no ingredient/nutrition data to offer
            source_url=None,
            raw_metadata={"upc": item.get("upc")},
        )
