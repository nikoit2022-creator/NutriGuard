"""
GS1 Digital Link resolver adapter.

Deliberately NOT a product database lookup: a GS1 resolver's job is to
redirect/link a GTIN to whatever official resource(s) the brand owner
registered for it (a product page, a digital product passport, ...). It
almost never carries structured nutrition/ingredient data itself, so
this adapter only ever contributes a `source_url` (an official
manufacturer-linked resource) and corroborating provenance — never a
`product_name`/`brand`/nutrition claim. `ProviderProductResult.
has_basic_identity` is therefore always False for this provider, so the
orchestration service can never treat "GS1 resolved a link" as
"the product was found" on its own (see barcode_discovery.py).

Endpoint: `GET {GS1_RESOLVER_BASE_URL}/01/{gtin14}?linkType=all`,
`Accept: application/linkset+json` — the standard GS1 Digital Link
resolver "linkset" response shape (a list of link relations grouped by
GTIN, keyed by GS1's registered link-type URIs such as `gs1:pip` for a
"Product Information Page").
"""
import structlog

from app.core.config import settings
from app.integrations.barcode_providers._http import get_json
from app.integrations.barcode_providers.base import (
    BarcodeProductProvider,
    ProviderMetadata,
    ProviderProductResult,
)
from app.services.barcode_validation import BarcodeInfo

logger = structlog.get_logger(__name__)

# Preference order for which linkset relation counts as "the" official
# resource URL when more than one is present.
_LINK_TYPE_PREFERENCE = ("gs1:pip", "gs1:productImage", "gs1:brandHomepage", "gs1:manufacturerHomepage")


def _extract_source_url(body: dict) -> str | None:
    linkset = body.get("linkset")
    if not isinstance(linkset, list) or not linkset:
        return None
    entry = linkset[0]
    if not isinstance(entry, dict):
        return None
    for link_type in _LINK_TYPE_PREFERENCE:
        links = entry.get(link_type)
        if isinstance(links, list):
            for link in links:
                if isinstance(link, dict):
                    href = link.get("href")
                    if isinstance(href, str) and href.strip():
                        return href.strip()
    return None


class GS1DigitalLinkResolver(BarcodeProductProvider):
    metadata = ProviderMetadata(name="gs1_digital_link", base_trust=0.60)

    def __init__(self, transport=None) -> None:
        self._transport = transport  # test-only, see _http.get_json

    def _endpoint(self, gtin14: str) -> str:
        return f"{settings.GS1_RESOLVER_BASE_URL}/01/{gtin14}"

    async def fetch(self, barcode: BarcodeInfo) -> ProviderProductResult | None:
        body = await get_json(
            provider=self.metadata.name,
            url=self._endpoint(barcode.gtin14),
            params={"linkType": "all"},
            headers={"Accept": "application/linkset+json, application/json"},
            timeout_seconds=settings.BARCODE_PROVIDER_TIMEOUT_SECONDS,
            max_retries=settings.BARCODE_PROVIDER_MAX_RETRIES,
            transport=self._transport,
        )

        if body.get("__not_found__"):
            return None

        source_url = _extract_source_url(body)
        if not source_url:
            # A registered GTIN with no usable link is, for our
            # purposes, the same as "nothing to resolve here".
            return None

        return ProviderProductResult(
            provider=self.metadata.name,
            external_id=barcode.gtin14,
            product_name=None,  # never claims identity — see module docstring
            brand=None,
            category=None,
            image_url=None,
            raw_ingredient_text=None,
            source_url=source_url,
            raw_metadata={"gtin14": barcode.gtin14},
        )
