"""
Open Food Facts adapter (API v3) — the first, and generally most
complete, external barcode data source: crowd-sourced but structured,
with actual nutrition figures and ingredient text for a large share of
retail food barcodes worldwide.

Endpoint: `GET {OPEN_FOOD_FACTS_BASE_URL}/api/v3/product/{gtin13}`. OFF
v3 answers with HTTP 200 either way and signals "not found" via
`result.id` / `status` in the body (not via a 404), so that's checked
explicitly rather than relying on `_http.get_json`'s 404 handling.
"""
from datetime import datetime, timezone
from typing import Any

import structlog

from app.core.config import settings
from app.integrations.barcode_providers._http import get_json
from app.integrations.barcode_providers.base import (
    BarcodeProductProvider,
    NutritionFacts,
    ProviderMalformedResponseError,
    ProviderMetadata,
    ProviderProductResult,
)
from app.services.barcode_validation import BarcodeInfo

logger = structlog.get_logger(__name__)

# E-number ranges used only to derive the two boolean flags the Health
# Score Calculator needs (has_artificial_sweeteners / has_preservatives)
# from OFF's `additives_tags` — the same kind of keyword-level heuristic
# `fallback_local_analysis` already applies to raw OCR text, just driven
# by OFF's structured additive tags instead of free text.
_SWEETENER_E_NUMBERS = {f"e9{n:02d}" for n in range(50, 70)}
_PRESERVATIVE_E_NUMBERS = {f"e2{n:02d}" for n in range(0, 100)}


def _first_csv(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.split(",")[0].strip()


def _to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _sodium_mg(nutriments: dict) -> float | None:
    sodium_g = _to_float(nutriments.get("sodium_100g"))
    if sodium_g is not None:
        return round(sodium_g * 1000, 2)
    salt_g = _to_float(nutriments.get("salt_100g"))
    if salt_g is not None:
        # Standard food-labeling conversion: sodium (g) = salt (g) / 2.5
        return round((salt_g / 2.5) * 1000, 2)
    return None


def _tag_has_any(tags: Any, needles: set[str]) -> bool:
    if not isinstance(tags, list):
        return False
    return any(isinstance(t, str) and t.lower().replace("en:", "") in needles for t in tags)


def _dietary_flags(product: dict) -> dict[str, bool]:
    analysis = product.get("ingredients_analysis_tags") or []
    labels = product.get("labels_tags") or []
    flags: dict[str, bool] = {}
    if _tag_has_any(analysis, {"vegan"}):
        flags["is_vegan"] = True
    elif _tag_has_any(analysis, {"non-vegan"}):
        flags["is_vegan"] = False
    if _tag_has_any(analysis, {"vegetarian"}):
        flags["is_vegetarian"] = True
    elif _tag_has_any(analysis, {"non-vegetarian"}):
        flags["is_vegetarian"] = False
    if _tag_has_any(analysis, {"palm-oil-free"}):
        pass  # not a tracked Product flag; ignored deliberately
    if _tag_has_any(labels, {"gluten-free"}):
        flags["is_gluten_free"] = True
    if _tag_has_any(labels, {"lactose-free", "no-lactose"}):
        flags["is_lactose_free"] = True
    if _tag_has_any(labels, {"halal"}):
        flags["is_halal"] = True
    if _tag_has_any(labels, {"kosher"}):
        flags["is_kosher"] = True
    return flags


def _allergens(product: dict) -> list[str]:
    tags = product.get("allergens_tags") or []
    return sorted({t.split(":", 1)[-1].strip().title() for t in tags if isinstance(t, str) and t.strip()})


def _additive_flags(product: dict) -> tuple[bool | None, bool | None]:
    tags = product.get("additives_tags") or []
    if not isinstance(tags, list) or not tags:
        return None, None
    normalized = {t.lower().replace("en:", "").replace("-", "") for t in tags if isinstance(t, str)}
    has_sweeteners = any(e.replace("-", "") in normalized for e in _SWEETENER_E_NUMBERS)
    has_preservatives = any(e.replace("-", "") in normalized for e in _PRESERVATIVE_E_NUMBERS)
    return has_sweeteners, has_preservatives


def _ingredient_tokens(product: dict) -> list[str]:
    ingredients = product.get("ingredients")
    if isinstance(ingredients, list) and ingredients:
        tokens = []
        for item in ingredients:
            if isinstance(item, dict):
                text = item.get("text") or item.get("id")
                if isinstance(text, str) and text.strip():
                    tokens.append(text.strip())
        if tokens:
            return tokens
    return []


class OpenFoodFactsProvider(BarcodeProductProvider):
    metadata = ProviderMetadata(name="open_food_facts", base_trust=0.75)

    def __init__(self, transport=None) -> None:
        self._transport = transport  # test-only, see _http.get_json

    def _endpoint(self, gtin13: str) -> str:
        return f"{settings.OPEN_FOOD_FACTS_BASE_URL}/api/v3/product/{gtin13}"

    async def fetch(self, barcode: BarcodeInfo) -> ProviderProductResult | None:
        body = await get_json(
            provider=self.metadata.name,
            url=self._endpoint(barcode.gtin13),
            params=None,
            headers={
                "User-Agent": settings.OPEN_FOOD_FACTS_USER_AGENT,
                "Accept": "application/json",
            },
            timeout_seconds=settings.BARCODE_PROVIDER_TIMEOUT_SECONDS,
            max_retries=settings.BARCODE_PROVIDER_MAX_RETRIES,
            transport=self._transport,
        )

        if body.get("__not_found__"):
            return None

        result_id = ((body.get("result") or {}).get("id") if isinstance(body.get("result"), dict) else None)
        status = body.get("status")
        if result_id == "product_not_found" or status in ("failure", 0):
            return None

        product = body.get("product")
        if not isinstance(product, dict) or not product:
            # A 200 with neither an explicit not-found marker nor a
            # usable `product` object doesn't match the documented
            # shape at all — treat it as malformed rather than silently
            # returning nothing (a real "not found" is always explicit).
            raise ProviderMalformedResponseError("open_food_facts response had no `product` object.")

        # Language policy: prefer the English record when OFF has one
        # (canonical internal matching name), then a Bulgarian record
        # (the app's other supported display language), then whatever
        # OFF considers the product's default name. A third-language
        # name that slips through here is still caught downstream by
        # `text_localization.to_bilingual_display_text` before it ever
        # reaches a client — see barcode_discovery.py.
        product_name = (
            product.get("product_name_en")
            or product.get("product_name_bg")
            or product.get("product_name")
            or product.get("generic_name")
            or None
        )
        raw_text = product.get("ingredients_text") or product.get("ingredients_text_en") or None
        nutriments = product.get("nutriments") or {}
        has_sweeteners, has_preservatives = _additive_flags(product)

        last_modified = None
        ts = product.get("last_modified_t")
        if isinstance(ts, (int, float)) and ts > 0:
            try:
                last_modified = datetime.fromtimestamp(ts, tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                last_modified = None

        return ProviderProductResult(
            provider=self.metadata.name,
            external_id=body.get("code") or barcode.gtin13,
            product_name=product_name,
            brand=_first_csv(product.get("brands")),
            category=_first_csv(product.get("categories")),
            image_url=product.get("image_front_url") or product.get("image_url"),
            raw_ingredient_text=raw_text,
            ingredients_tokens=_ingredient_tokens(product),
            nutrition=NutritionFacts(
                sugar_grams=_to_float(nutriments.get("sugars_100g")),
                sodium_mg=_sodium_mg(nutriments),
                saturated_fat_grams=_to_float(nutriments.get("saturated-fat_100g")),
                has_artificial_sweeteners=has_sweeteners,
                has_preservatives=has_preservatives,
                nova_group=product.get("nova_group") if isinstance(product.get("nova_group"), int) else None,
            ),
            allergens=_allergens(product),
            dietary_flags=_dietary_flags(product),
            language=product.get("lang") or product.get("lc"),
            source_url=f"{settings.OPEN_FOOD_FACTS_BASE_URL}/product/{barcode.gtin13}",
            external_last_modified=last_modified,
            raw_metadata={"code": body.get("code")},
        )
