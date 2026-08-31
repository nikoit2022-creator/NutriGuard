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


_SUPPORTED_LANGUAGES = {"en", "bg"}


def _declared_language(product: dict) -> str:
    """OFF's own stated language for its language-neutral fields
    (`product_name`, `ingredients_text`, ...) — `lang` (preferred) or
    `lc` ("language of the community"/data entry). Never inferred from
    the text itself: script (Latin vs. Cyrillic vs. other) is NOT a
    language, and treating it as one is exactly the bug this function
    exists to avoid — French, German, Albanian, etc. are all Latin
    script too."""
    value = product.get("lang") or product.get("lc")
    return value.strip().lower() if isinstance(value, str) else ""


def _language_gated_field(product: dict, *, en_key: str, bg_key: str, default_key: str, lang: str) -> str | None:
    """English/Bulgarian selection policy: an explicit `<field>_en` or
    `<field>_bg` key is always safe to use (OFF itself asserts the
    language via the key name). The language-neutral `<field>` key is
    only used when OFF's own declared record language (`lang`/`lc`) is
    English or Bulgarian — never merely because the text happens to be
    in a Latin/Cyrillic script, which every European language shares.
    Returns None rather than falling back to a French/German/Albanian/
    etc. value — the caller (barcode_discovery.py / the UPCitemdb
    fallback) is responsible for what happens when no field qualifies."""
    explicit = product.get(en_key) or product.get(bg_key)
    if isinstance(explicit, str) and explicit.strip():
        return explicit
    if lang in _SUPPORTED_LANGUAGES:
        value = product.get(default_key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _clean_taxonomy_id(raw_id: str) -> str | None:
    """OFF's `ingredients[].id` is always its own canonical taxonomy
    identifier in English (e.g. `"en:sodium-benzoate"`), REGARDLESS of
    the record's declared language — unlike `.text`, which is the
    literal, language-specific label text. Safe to use even when the
    record's declared language is unsupported."""
    cleaned = raw_id.split(":", 1)[-1].replace("-", " ").strip()
    return cleaned or None


def _ingredient_tokens(product: dict, lang: str) -> list[str]:
    """Same language policy as `_language_gated_field`, applied
    per-token: `.text` (the literal, language-specific ingredient
    label) is only used when the record's declared language is
    English/Bulgarian. For any other declared language, only the
    English taxonomy `.id` is used — never the raw foreign-language
    `.text` — so an unsupported-language record can still contribute
    identifiable (English) ingredient tokens without ever leaking
    untranslated French/German/Albanian/etc. text into
    `raw_ingredient_text` (PR #7 review, round 2, finding 2)."""
    ingredients = product.get("ingredients")
    if not isinstance(ingredients, list) or not ingredients:
        return []

    tokens: list[str] = []
    for item in ingredients:
        if not isinstance(item, dict):
            continue
        text: str | None = None
        if lang in _SUPPORTED_LANGUAGES:
            candidate = item.get("text")
            if isinstance(candidate, str) and candidate.strip():
                text = candidate.strip()
        if text is None:
            raw_id = item.get("id")
            if isinstance(raw_id, str) and raw_id.strip():
                text = _clean_taxonomy_id(raw_id)
        if text:
            tokens.append(text)
    return tokens


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

        # Language policy (English/Bulgarian only — see
        # _language_gated_field docstring): explicit `_en`/`_bg` fields
        # first; the language-neutral default field only when OFF's own
        # declared record language is English or Bulgarian. A record in
        # any other language (French, German, Albanian, ...) yields None
        # here rather than leaking through — script-safety downstream in
        # barcode_discovery.py is a secondary placeholder/junk-scrubbing
        # net, not a substitute for this language check.
        lang = _declared_language(product)
        product_name = _language_gated_field(
            product, en_key="product_name_en", bg_key="product_name_bg", default_key="product_name", lang=lang
        ) or _language_gated_field(
            product, en_key="generic_name_en", bg_key="generic_name_bg", default_key="generic_name", lang=lang
        )
        raw_text = _language_gated_field(
            product,
            en_key="ingredients_text_en",
            bg_key="ingredients_text_bg",
            default_key="ingredients_text",
            lang=lang,
        )
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
            ingredients_tokens=_ingredient_tokens(product, lang),
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
            language=lang or None,
            source_url=f"{settings.OPEN_FOOD_FACTS_BASE_URL}/product/{barcode.gtin13}",
            external_last_modified=last_modified,
            raw_metadata={"code": body.get("code")},
        )
