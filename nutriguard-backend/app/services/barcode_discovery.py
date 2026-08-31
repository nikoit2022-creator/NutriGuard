"""
Multi-source barcode product discovery orchestration.

Required flow (see README "Barcode product discovery"):
  1. (caller's responsibility, before this module is ever invoked) the
     local database is checked first — see `food_analysis.analyze_barcode`.
  2. Open Food Facts.
  3. GS1 Digital Link resolution, attempted independently of (2) — it
     contributes an official resource URL, never a product identity.
  4. UPCitemdb, only as a fallback when (2) found nothing or found an
     incomplete identity (missing product name and/or brand).
  5. Normalize/validate and merge into one `DiscoveredProduct`.

Error isolation: a timeout, HTTP error, malformed response, or rate
limit from any one provider is caught here and logged (provider name +
error class only — never a response body, header, or query string) and
never prevents trying the next provider. Every provider call is bounded
by `BARCODE_PROVIDER_TIMEOUT_SECONDS`.

This module never treats UPCitemdb's nutrition data as authoritative —
structurally true by construction, since `UpcItemDbProvider` never
populates a `NutritionFacts` field in the first place (see
`upcitemdb.py`), so there is nothing here to accidentally trust.
"""
import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog

from app.core.config import settings
from app.integrations.barcode_providers.base import (
    BarcodeProductProvider,
    NutritionFacts,
    ProviderError,
    ProviderProductResult,
    ProviderRateLimitedError,
    ProviderTimeoutError,
)
from app.integrations.barcode_providers.gs1_resolver import GS1DigitalLinkResolver
from app.integrations.barcode_providers.open_food_facts import OpenFoodFactsProvider
from app.integrations.barcode_providers.upcitemdb import UpcItemDbProvider
from app.services.barcode_text_safety import clean_optional, clean_required, to_safe_display_text
from app.services.barcode_validation import BarcodeInfo

logger = structlog.get_logger(__name__)

# Provider-specific trust weights (requirement: "keep provider-specific
# trust weights configurable in code"). Deliberately not env-configurable
# — these encode a considered trust ordering, not a deployment knob.
PROVIDER_BASE_TRUST: dict[str, float] = {
    "open_food_facts": 0.75,
    "gs1_digital_link": 0.60,
    "upcitemdb": 0.45,
}

_GS1_AGREEMENT_BONUS = 0.05
_MAX_CONFIDENCE = 0.98  # never claim absolute (1.0) confidence in an unverified external result


@dataclass(frozen=True)
class ProviderAttempt:
    """One provider call's outcome, kept only to build the "not found /
    label scan required" response — never exposes response content."""

    provider: str
    outcome: str  # "found" | "not_found" | "timeout" | "rate_limited" | "error" | "skipped"


@dataclass(frozen=True)
class DiscoveredProduct:
    """Validated, merged, not-yet-persisted discovery result — the
    bridge between the provider layer and `Product`/`ProductSource`."""

    barcode: BarcodeInfo
    product_name: str
    brand: str
    category: str
    image_url: str | None
    raw_ingredient_text: str
    nutrition: NutritionFacts
    allergens: list[str]
    dietary_flags: dict[str, bool]
    language: str | None
    primary_provider: str
    confidence: float
    source_url: str | None
    external_last_modified: datetime | None
    nutrition_known: bool
    ingredients_known: bool
    is_conflicting: bool
    contributing: list[ProviderProductResult] = field(default_factory=list)


@dataclass(frozen=True)
class DiscoveryOutcome:
    product: DiscoveredProduct | None
    attempts: list[ProviderAttempt]


def _build_providers() -> dict[str, BarcodeProductProvider]:
    return {
        "open_food_facts": OpenFoodFactsProvider(),
        "gs1_digital_link": GS1DigitalLinkResolver(),
        "upcitemdb": UpcItemDbProvider(),
    }


async def _try_provider(
    provider: BarcodeProductProvider, barcode: BarcodeInfo
) -> tuple[ProviderProductResult | None, ProviderAttempt]:
    name = provider.metadata.name
    started = time.perf_counter()
    try:
        result = await asyncio.wait_for(
            provider.fetch(barcode), timeout=settings.BARCODE_PROVIDER_TIMEOUT_SECONDS + 0.5
        )
    except (asyncio.TimeoutError, ProviderTimeoutError) as exc:
        logger.warning("barcode_provider_timeout", provider=name, reason=type(exc).__name__)
        return None, ProviderAttempt(provider=name, outcome="timeout")
    except ProviderRateLimitedError:
        logger.warning("barcode_provider_rate_limited", provider=name)
        return None, ProviderAttempt(provider=name, outcome="rate_limited")
    except ProviderError as exc:
        logger.warning("barcode_provider_error", provider=name, reason=type(exc).__name__)
        return None, ProviderAttempt(provider=name, outcome="error")
    except Exception as exc:  # noqa: BLE001 - isolation boundary: a bug in one adapter must not sink discovery
        logger.error("barcode_provider_unexpected_error", provider=name, reason=type(exc).__name__)
        return None, ProviderAttempt(provider=name, outcome="error")
    finally:
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        logger.info("barcode_provider_call", provider=name, duration_ms=duration_ms)

    if result is None:
        return None, ProviderAttempt(provider=name, outcome="not_found")
    return result, ProviderAttempt(provider=name, outcome="found")


def _lacks_basic_identity(result: ProviderProductResult | None) -> bool:
    """"Basic identity data" = a usable product name AND brand. Missing
    either is enough to justify consulting the UPCitemdb fallback (it
    only ever contributes identity/brand/image fields, never nutrition)."""
    if result is None or not result.has_basic_identity:
        return True
    return not (result.brand and result.brand.strip())


def _names_conflict(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    na, nb = a.strip().lower(), b.strip().lower()
    if na == nb or na in nb or nb in na:
        return False
    return True


def _select_display_name(result: ProviderProductResult) -> str:
    return to_safe_display_text(result.product_name, fallback="Discovered Product")


async def discover_product(barcode: BarcodeInfo) -> DiscoveryOutcome:
    if not settings.BARCODE_DISCOVERY_ENABLED:
        return DiscoveryOutcome(product=None, attempts=[])

    providers = _build_providers()
    attempts: list[ProviderAttempt] = []

    off_result: ProviderProductResult | None = None
    if settings.OPEN_FOOD_FACTS_ENABLED:
        off_result, attempt = await _try_provider(providers["open_food_facts"], barcode)
        attempts.append(attempt)
    else:
        attempts.append(ProviderAttempt(provider="open_food_facts", outcome="skipped"))

    gs1_result: ProviderProductResult | None = None
    if settings.GS1_RESOLVER_ENABLED:
        gs1_result, attempt = await _try_provider(providers["gs1_digital_link"], barcode)
        attempts.append(attempt)
    else:
        attempts.append(ProviderAttempt(provider="gs1_digital_link", outcome="skipped"))

    upc_result: ProviderProductResult | None = None
    if not settings.UPCITEMDB_ENABLED:
        attempts.append(ProviderAttempt(provider="upcitemdb", outcome="skipped"))
    elif _lacks_basic_identity(off_result):
        upc_result, attempt = await _try_provider(providers["upcitemdb"], barcode)
        attempts.append(attempt)
    else:
        # Open Food Facts already gave a complete identity -- UPCitemdb
        # is a fallback, not a routine lookup, so it's never spent here.
        attempts.append(ProviderAttempt(provider="upcitemdb", outcome="skipped"))

    primary = off_result if (off_result and off_result.has_basic_identity) else (
        upc_result if (upc_result and upc_result.has_basic_identity) else None
    )
    if primary is None:
        return DiscoveryOutcome(product=None, attempts=attempts)

    contributing = [r for r in (off_result, gs1_result, upc_result) if r is not None]

    is_conflicting = False
    brand = clean_optional(primary.brand) or ""
    image_url = clean_optional(primary.image_url)
    if primary is off_result and upc_result is not None:
        if _names_conflict(off_result.product_name, upc_result.product_name):
            is_conflicting = True
        else:
            brand = brand or (clean_optional(upc_result.brand) or "")
        image_url = image_url or clean_optional(upc_result.image_url)

    confidence = PROVIDER_BASE_TRUST.get(primary.provider, 0.3)
    source_url = primary.source_url or (gs1_result.source_url if gs1_result else None)
    if gs1_result is not None and gs1_result.source_url:
        confidence = min(_MAX_CONFIDENCE, confidence + _GS1_AGREEMENT_BONUS)
    if is_conflicting:
        confidence = max(0.0, confidence - 0.15)

    raw_ingredient_text = clean_optional(primary.raw_ingredient_text) or ""
    if not raw_ingredient_text and primary.ingredients_tokens:
        raw_ingredient_text = ", ".join(primary.ingredients_tokens)

    nutrition_known = any(
        v is not None
        for v in (
            primary.nutrition.sugar_grams,
            primary.nutrition.sodium_mg,
            primary.nutrition.saturated_fat_grams,
        )
    )
    ingredients_known = bool(raw_ingredient_text.strip())

    discovered = DiscoveredProduct(
        barcode=barcode,
        product_name=_select_display_name(primary),
        brand=clean_required(brand, fallback=""),
        category=to_safe_display_text(primary.category, fallback="Discovered Product"),
        image_url=image_url,
        raw_ingredient_text=raw_ingredient_text,
        nutrition=primary.nutrition,
        allergens=list(primary.allergens),
        dietary_flags=dict(primary.dietary_flags),
        language=primary.language,
        primary_provider=primary.provider,
        confidence=round(confidence, 3),
        source_url=source_url,
        external_last_modified=primary.external_last_modified,
        nutrition_known=nutrition_known,
        ingredients_known=ingredients_known,
        is_conflicting=is_conflicting,
        contributing=contributing,
    )
    return DiscoveryOutcome(product=discovered, attempts=attempts)
