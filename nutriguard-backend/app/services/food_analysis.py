"""
Port of `com.example.data.repository.FoodAnalysisRepository`.

This is the single place that ties together: product/ingredient lookup,
the Gemini/fallback AI chain, the Health Score Calculator, and the
Personalized Warning Engine, exactly mirroring the client's repository
so behavior stays identical regardless of which endpoint triggered it.

CONTRACT CHANGE (V2, documented, see README "Deviations" / CHANGELOG):
`analyze_barcode` no longer fabricates a product for an unknown barcode.
A barcode is an identifier, not free-text label content — inventing
ingredients/nutrition/health-score for a barcode the trusted product
source has never seen produces misleading, non-deterministic output.
Unknown barcodes now return PRODUCT_NOT_FOUND (404), matching the
existing GET /products/{barcode} behavior. `analyze_ocr_text` is
UNCHANGED: it still exists specifically to analyze free-text label
content via the Gemini/fallback chain.

BUG FIX (V3, documented, see README "Deviations"): `analyze_label_image`
previously discarded a successful Gemini image analysis and always fell
back to keyword-heuristic analysis of only the extracted product
name/raw text. It now uses the full structured Gemini JSON (nutrition
figures, dietary flags, NOVA group, ingredients) per API Contract 6.3
when Gemini succeeds — see `gemini_image_parser.py`. `analyze_ocr_text`
is intentionally NOT touched by this fix: it preserves a separate,
documented quirk (see `gemini_result_parser.py`) that is out of scope
for this task.

BARCODE PRODUCT DISCOVERY (V5, documented, see README "Deviations"):
`analyze_barcode` no longer stops at a bare "not found" the moment the
local database misses. It now attempts multi-source discovery (Open
Food Facts, a GS1 Digital Link resolution, UPCitemdb as an
identity-only fallback — see `barcode_discovery.py`) before giving up.
A discovered product is normalized, persisted, and returned through the
same `FullProductAnalysisOut` contract; a barcode no provider recognizes
still returns 404 `PRODUCT_NOT_FOUND`, now carrying a structured
`details` payload (`labelScanRequired: true` + which providers were
tried) instead of a bare, uninformative 404. This still never fabricates
data from the barcode alone — a barcode with genuinely no data anywhere
gets the same "scan the label instead" signal as before, just a more
actionable one.

V6 (PR #7 review fixes, documented, see README "Deviations"):
  - Unknown dietary flags (vegan/vegetarian/gluten-free/lactose-free/
    halal/kosher) from a barcode discovery now default to False, never
    True — see `_to_analyzed_data_from_discovery`'s docstring. Missing
    data must never read as a positive certification claim.
  - A discovery whose nutrition and/or ingredients are materially
    incomplete (`Product.has_verified_nutrition=False`) never reaches
    the Health Score Calculator, in `analyze_barcode` OR on any later
    cache hit of the same persisted row — it returns the same
    structured `labelScanRequired` response `_not_found_details`
    already established, via `_label_scan_required_details`, instead of
    a confident-looking score computed from placeholder zeros.
  - Barcode storage/lookup uses a canonical GTIN-13 key
    (`product_repository.get_by_barcode_or_aliases` +
    `barcode_validation.alias_keys`), so whitespace/dash variants and
    UPC-A/EAN-13-equivalent representations of the same barcode
    converge on one row instead of creating duplicates — including a
    pre-existing/legacy row stored under a non-canonical alias.

V7 (PR #7 review, round 2 fixes, documented, see README "Deviations"):
  - `nutrition_known` now requires ALL THREE core numeric inputs the
    Health Score Calculator consumes (sugar/sodium/saturated fat), not
    just one — a partial answer is exactly as unreliable as no answer.
  - Open Food Facts' `ingredients[].text` (language-specific label text)
    is only used when the record's declared language is English/
    Bulgarian; for any other language, only the always-English
    `ingredients[].id` taxonomy identifier is used, never the raw
    foreign-language text — closes a gap where an unsupported-language
    record's `ingredients_text` was correctly gated but its structured
    `ingredients` array fallback was not.
  - `get_by_barcode_or_aliases` now checks every alias representation
    of a barcode (not just the raw input and the canonical GTIN-13), so
    a pre-existing/legacy row stored under a non-canonical form (e.g. a
    12-digit UPC-A row, found via a later 13-digit EAN-13-equivalent
    scan) is still found instead of duplicated.
"""
import time
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    AIServiceUnavailableError,
    ImageUnreadableError,
    ProductNotFoundError,
    ValidationAppError,
)
from app.integrations.barcode_providers.base import ProviderProductResult
from app.integrations.gemini import GeminiUnavailableError, gemini_service
from app.models.enums import ScanType, WarningSeverity
from app.models.product import Product
from app.models.scan_history import ScanHistory
from app.repositories import (
    health_profile_repository,
    ingredient_repository,
    product_repository,
    product_source_repository,
    scan_history_repository,
)
from app.services import barcode_discovery, gemini_image_parser, health_score, warning_engine
from app.services import barcode_validation
from app.services.barcode_validation import validate_and_normalize
from app.services.fallback_analysis import AnalyzedProductData, fallback_local_analysis
from app.services.gemini_image_parser import parse_gemini_image_json_result
from app.services.gemini_result_parser import parse_gemini_json_result
from app.services import label_language
from app.services.label_language import LabelTextResult, resolve_label_text
from app.services.ocr_normalizer import create_synthetic_ingredient, match_against_database, normalize_and_extract_tokens
from app.services.warning_engine import HealthWarning

logger = structlog.get_logger(__name__)


async def _run_ai_or_fallback(title_hint: str, raw_text: str, db_ingredients: list[Any]) -> tuple:
    """
    Implements the fallback chain from API Contract 7.4:
      1. Try Gemini.
      2. On any Gemini failure -> deterministic local fallback (never an error).
      3. Only if the fallback itself raises -> AIServiceUnavailableError.
    """
    try:
        if gemini_service.is_configured:
            raw_response = await gemini_service.analyze_text(raw_text)
            return parse_gemini_json_result(raw_response, raw_text, db_ingredients)
        raise GeminiUnavailableError("Gemini not configured; using local fallback.")
    except GeminiUnavailableError as exc:
        logger.info("gemini_fallback_triggered", reason=str(exc))
        try:
            return fallback_local_analysis(title_hint, raw_text, db_ingredients)
        except Exception as fallback_exc:  # noqa: BLE001
            logger.error("fallback_analysis_failed", error=str(fallback_exc))
            raise AIServiceUnavailableError(
                "Both the AI service and the local fallback analysis failed."
            ) from fallback_exc


def _ingredient_ids_string(ingredients: list[Any]) -> str:
    return ",".join(ing.id for ing in ingredients)


def _to_product_model(
    barcode: str,
    data: AnalyzedProductData,
    ingredients: list[Any],
    *,
    source: str = "label_scan",
    source_confidence: float | None = None,
    is_verified: bool = True,
    has_verified_nutrition: bool = True,
) -> Product:
    now = datetime.now(timezone.utc)
    return Product(
        barcode=barcode,
        product_name=data.product_name,
        brand=data.brand,
        category=data.category,
        image_url=data.image_url,
        raw_ingredient_text=data.raw_ingredient_text,
        ingredient_ids=_ingredient_ids_string(ingredients),
        health_score=0,  # placeholder, recomputed live on every read (see module docstring)
        nova_group=data.nova_group,
        sugar_grams=data.sugar_grams,
        sodium_mg=data.sodium_mg,
        saturated_fat_grams=data.saturated_fat_grams,
        has_artificial_sweeteners=data.has_artificial_sweeteners,
        has_preservatives=data.has_preservatives,
        is_gluten_free=data.is_gluten_free,
        is_lactose_free=data.is_lactose_free,
        is_vegan=data.is_vegan,
        is_vegetarian=data.is_vegetarian,
        is_halal=data.is_halal,
        is_kosher=data.is_kosher,
        allergens_detected=data.allergens_detected,
        timestamp=int(time.time() * 1000),
        source=source,
        source_confidence=source_confidence,
        is_verified=is_verified,
        has_verified_nutrition=has_verified_nutrition,
        discovered_at=now if source != "label_scan" else None,
        last_verified_at=now,
    )


def _apply_discovered_fields(
    existing: Product,
    data: AnalyzedProductData,
    ingredients: list[Any],
    *,
    source: str,
    confidence: float,
    has_verified_nutrition: bool,
) -> None:
    """Overwrites a previously-discovered (never a verified/local) row
    with a higher-confidence re-discovery. Never called for a row whose
    `is_verified` is True — see `_persist_discovered_product`."""
    existing.product_name = data.product_name
    existing.brand = data.brand
    existing.category = data.category
    existing.image_url = data.image_url
    existing.raw_ingredient_text = data.raw_ingredient_text
    existing.ingredient_ids = _ingredient_ids_string(ingredients)
    existing.nova_group = data.nova_group
    existing.sugar_grams = data.sugar_grams
    existing.sodium_mg = data.sodium_mg
    existing.saturated_fat_grams = data.saturated_fat_grams
    existing.has_artificial_sweeteners = data.has_artificial_sweeteners
    existing.has_preservatives = data.has_preservatives
    existing.is_gluten_free = data.is_gluten_free
    existing.is_lactose_free = data.is_lactose_free
    existing.is_vegan = data.is_vegan
    existing.is_vegetarian = data.is_vegetarian
    existing.is_halal = data.is_halal
    existing.is_kosher = data.is_kosher
    existing.allergens_detected = data.allergens_detected
    existing.source = source
    existing.source_confidence = confidence
    existing.has_verified_nutrition = has_verified_nutrition
    existing.last_verified_at = datetime.now(timezone.utc)
    existing.timestamp = int(time.time() * 1000)


def _to_analyzed_data_from_discovery(
    discovered: "barcode_discovery.DiscoveredProduct", db_ingredients: list[Any]
) -> tuple[AnalyzedProductData, list[Any], bool]:
    """
    Bridges a validated `DiscoveredProduct` (external, provider-sourced)
    into the same `AnalyzedProductData` shape the OCR/Gemini pipelines
    use, so it goes through the identical ingredient-matching, Health
    Score, and Warning Engine code paths — see module docstring, V5/V6.

    Returns `(data, ingredients, has_verified_nutrition)`.
    `has_verified_nutrition` is False whenever the discovery is
    materially incomplete (no real nutrition figures AND/OR no
    ingredient text — see `DiscoveredProduct.nutrition_known` /
    `.ingredients_known`); the caller (`_persist_discovered_product` /
    `analyze_barcode`) uses this to skip the Health Score/Warning
    pipeline entirely for such a row — see V6 in the module docstring —
    rather than trust a score computed from placeholder numbers.

    Dietary flags/allergens: OFF's own structured tags (`dietary_flags`/
    `allergens` on `DiscoveredProduct`) are used where present. Anything
    OFF did NOT state defaults to False (NOT True) for every one of
    is_gluten_free/is_lactose_free/is_vegan/is_vegetarian/is_halal/
    is_kosher — "unknown" must never be presented as a positive
    certification claim. False is the conservative direction here: for
    a health- or religion-relevant dietary flag, a false negative
    (a genuinely gluten-free product not marked as such) is far safer
    than a false positive (a product silently claimed gluten-free/halal/
    kosher with zero evidence for it) — the same asymmetry the
    Personalized Warning Engine already assumes when it warns a user off
    a product that isn't flagged as meeting their requirement.
    """
    is_complete = discovered.nutrition_known and discovered.ingredients_known

    ingredient_list: list[Any] = []
    if discovered.ingredients_known:
        tokens = normalize_and_extract_tokens(discovered.raw_ingredient_text)
        norm = match_against_database(tokens, db_ingredients)
        ingredient_list = list(norm.matched_ingredients)
        for unk in norm.unknown_ingredients:
            ingredient_list.append(create_synthetic_ingredient(unk))

    n = discovered.nutrition
    nova_group = n.nova_group
    if nova_group is None:
        # Only guessed when there's real ingredient/nutrition data to
        # base the guess on (same heuristic `fallback_local_analysis`
        # uses for OCR text with no explicit NOVA signal); an
        # incomplete discovery gets an explicit "unclassified" 0
        # instead of a guess dressed up as a real NOVA group — moot for
        # scoring either way since an incomplete row never reaches the
        # Health Score Calculator (see docstring above), but this keeps
        # the stored value honest for anyone inspecting the row directly.
        nova_group = (
            (4 if (len(ingredient_list) > 5 or n.has_artificial_sweeteners or n.has_preservatives) else 3)
            if is_complete
            else 0
        )

    flags = discovered.dietary_flags
    allergens_text = ", ".join(discovered.allergens) if discovered.allergens else "None"

    data = AnalyzedProductData(
        barcode=discovered.barcode.gtin13,
        product_name=discovered.product_name,
        brand=discovered.brand or "Unknown Brand",
        category=discovered.category,
        image_url=discovered.image_url,
        raw_ingredient_text=discovered.raw_ingredient_text,
        nova_group=nova_group,
        sugar_grams=n.sugar_grams if n.sugar_grams is not None else 0.0,
        sodium_mg=n.sodium_mg if n.sodium_mg is not None else 0.0,
        saturated_fat_grams=n.saturated_fat_grams if n.saturated_fat_grams is not None else 0.0,
        has_artificial_sweeteners=bool(n.has_artificial_sweeteners),
        has_preservatives=bool(n.has_preservatives),
        is_gluten_free=flags.get("is_gluten_free", False),
        is_lactose_free=flags.get("is_lactose_free", False),
        is_vegan=flags.get("is_vegan", False),
        is_vegetarian=flags.get("is_vegetarian", False),
        is_halal=flags.get("is_halal", False),
        is_kosher=flags.get("is_kosher", False),
        allergens_detected=allergens_text,
    )
    return data, ingredient_list, is_complete


def _not_found_details(barcode: str, attempts: list["barcode_discovery.ProviderAttempt"]) -> dict:
    return {
        "labelScanRequired": True,
        "reason": "Barcode not found in the local database or any configured external source.",
        "providersChecked": [{"provider": a.provider, "outcome": a.outcome} for a in attempts],
        "suggestedAction": "Use POST /scan/label-image or POST /scan/ocr-text to analyze the product's label directly.",
    }


def _label_scan_required_details(product: Product) -> dict:
    """Structured response for a barcode whose *identity* is known (was
    discovered and persisted — see `_persist_discovered_product`) but
    whose nutrition/ingredient data is too incomplete to compute a real
    Health Score. Deliberately shaped like `_not_found_details` (same
    `labelScanRequired`/`suggestedAction` keys) so a client can handle
    both with one code path, plus the identity fields worth showing the
    user even without a score."""
    return {
        "labelScanRequired": True,
        "reason": (
            "This product's identity was found, but its nutrition and/or ingredient data is "
            "too incomplete for a reliable Health Score."
        ),
        "discoveredIdentity": {
            "barcode": product.barcode,
            "productName": product.product_name,
            "brand": product.brand or None,
            "imageUrl": product.image_url,
        },
        "suggestedAction": "Use POST /scan/label-image or POST /scan/ocr-text to analyze the product's label directly.",
    }


async def _persist_discovered_product(
    db: AsyncSession, discovered: "barcode_discovery.DiscoveredProduct"
) -> tuple[Product, list[HealthWarning]]:
    """
    Normalizes + validates + persists a multi-source discovery result.
    Concurrency-safe (see `product_repository.insert_new`) and never
    overwrites an existing verified (local/OCR/label-image) row, nor an
    existing externally-discovered row of equal-or-higher confidence —
    see the module docstring's V5/V6 entries and README "Deviations".

    Always persists identity/provenance when a provider found *something*
    — including a materially-incomplete, `has_verified_nutrition=False`
    result — so a repeat scan doesn't re-hit external providers every
    time and so the identity is available for `_label_scan_required_details`.
    It is the caller's (`analyze_barcode`'s) job to check
    `product.has_verified_nutrition` and refuse to compute/return a
    Health Score for such a row.
    """
    all_db_ingredients = await ingredient_repository.get_all(db)
    data, ingredients, has_verified_nutrition = _to_analyzed_data_from_discovery(discovered, all_db_ingredients)
    # Canonical GTIN-13 storage key (see product_repository.get_by_barcode_or_aliases)
    # — every equivalent representation of this barcode converges on one row.
    canonical_barcode = discovered.barcode.gtin13

    candidate = _to_product_model(
        canonical_barcode,
        data,
        ingredients,
        source=discovered.primary_provider,
        source_confidence=discovered.confidence,
        is_verified=False,
        has_verified_nutrition=has_verified_nutrition,
    )

    inserted = await product_repository.insert_new(db, candidate)
    used_for_persisted_product = True
    if inserted is not None:
        product = inserted
    else:
        # Lost a race with a concurrent discovery of the same barcode.
        existing = await product_repository.get_by_barcode(db, canonical_barcode)
        if existing is None:
            raise AIServiceUnavailableError("Could not persist the discovered product.")
        if existing.is_verified or (existing.source_confidence or 0) >= discovered.confidence:
            product = existing
            used_for_persisted_product = False
        else:
            _apply_discovered_fields(
                existing,
                data,
                ingredients,
                source=discovered.primary_provider,
                confidence=discovered.confidence,
                has_verified_nutrition=has_verified_nutrition,
            )
            await db.flush()
            product = existing

    # Checkpoint the product row on its own before touching provenance
    # rows, so a later (unrelated) provenance-write race can never
    # unwind this insert/update — see product_source_repository.py.
    await db.commit()

    for result in discovered.contributing:
        is_primary = result.provider == discovered.primary_provider
        confidence = (
            discovered.confidence if is_primary else barcode_discovery.PROVIDER_BASE_TRUST.get(result.provider, 0.3)
        )
        await product_source_repository.record_discovery(
            db,
            barcode=canonical_barcode,
            result=result,
            confidence=confidence,
            is_conflicting=discovered.is_conflicting,
            used_for_persisted_product=is_primary and used_for_persisted_product,
        )
    await db.commit()

    extra_warnings: list[HealthWarning] = []
    if discovered.is_conflicting:
        extra_warnings.append(
            HealthWarning(
                title="Conflicting Source Data",
                description=(
                    "Independent external sources disagreed about this product's identity; "
                    "the highest-confidence match was used and the disagreement was preserved "
                    "for review."
                ),
                condition="Data Completeness",
                trigger_factor=discovered.primary_provider,
                severity=WarningSeverity.INFO,
            )
        )
    return product, extra_warnings


async def fetch_ingredients_for_product(db: AsyncSession, product: Product) -> list[Any]:
    """
    Mirrors FoodAnalysisRepository.fetchIngredientsForProduct: resolves
    each comma-separated id against the scientific database, and
    reconstructs a synthetic ingredient on the fly for any id that isn't
    found there (matching the client's lossy-but-intentional behavior of
    calling `OcrNormalizer.createSyntheticIngredient(id)` with the raw id
    string when no DB row exists).
    """
    if not product.ingredient_ids:
        return []

    ids = [i for i in product.ingredient_ids.split(",") if i]
    if not ids:
        return []

    db_matches = await ingredient_repository.get_many_by_ids(db, ids)
    by_id = {ing.id: ing for ing in db_matches}

    resolved: list[Any] = []
    for ingredient_id in ids:
        if ingredient_id in by_id:
            resolved.append(by_id[ingredient_id])
        else:
            resolved.append(create_synthetic_ingredient(ingredient_id))
    return resolved


async def _get_profile_namespace(db: AsyncSession, user_id: uuid.UUID) -> SimpleNamespace:
    profile = await health_profile_repository.get_for_user(db, user_id)
    if profile is not None:
        return SimpleNamespace(
            has_diabetes=profile.has_diabetes,
            has_hypertension=profile.has_hypertension,
            has_kidney_disease=profile.has_kidney_disease,
            has_gout=profile.has_gout,
            is_pregnant=profile.is_pregnant,
            for_children=profile.for_children,
            has_high_cholesterol=profile.has_high_cholesterol,
            avoid_gluten=profile.avoid_gluten,
            avoid_lactose=profile.avoid_lactose,
            avoid_peanuts=profile.avoid_peanuts,
            avoid_soy=profile.avoid_soy,
            avoid_tree_nuts=profile.avoid_tree_nuts,
            require_vegan=profile.require_vegan,
            require_vegetarian=profile.require_vegetarian,
            require_halal=profile.require_halal,
            require_kosher=profile.require_kosher,
        )
    return SimpleNamespace(**health_profile_repository.default_profile_dict())


def _score_and_warnings(product: Product, ingredients: list[Any], profile: SimpleNamespace):
    risk_levels = [ing.risk_level for ing in ingredients]
    breakdown = health_score.calculate(
        ingredient_risk_levels=risk_levels,
        sugar_grams=float(product.sugar_grams),
        sodium_mg=float(product.sodium_mg),
        saturated_fat_grams=float(product.saturated_fat_grams),
        has_artificial_sweeteners=product.has_artificial_sweeteners,
        has_preservatives=product.has_preservatives,
        nova_group=product.nova_group,
    )
    warnings = warning_engine.generate_warnings(product, ingredients, profile)
    return breakdown.total_score, warnings


async def analyze_barcode(db: AsyncSession, user_id: uuid.UUID, barcode: str) -> dict:
    """
    API Contract 6.1 (revised, V2 + V5): a barcode is an identifier,
    looked up against trusted product sources — the local database
    first, then (V5) multi-source external discovery on a local miss
    (`barcode_discovery.discover_product` — Open Food Facts, a GS1
    Digital Link resolution, UPCitemdb as an identity-only fallback).
    This still never fabricates a product from the barcode alone: a
    barcode no source recognizes returns PRODUCT_NOT_FOUND with a
    structured "label scan required" payload, not an invented product.
    Use `analyze_ocr_text` or `analyze_label_image` to analyze actual
    label content (those endpoints are unaffected by this change).
    """
    barcode_info = validate_and_normalize(barcode)
    alias_keys = barcode_validation.alias_keys(barcode_info) if barcode_info is not None else []
    # Tries the raw string first (matches a legacy row or a non-GTIN
    # synthetic ocr_.../img_... id verbatim), then every alias for the
    # same physical barcode — so "036000291452" (UPC-A), "0036000291452"
    # (its EAN-13 equivalent), and "036-000-291452" (same digits,
    # dashes) all resolve to the same persisted row REGARDLESS of which
    # exact form a pre-existing/legacy row happens to be stored under
    # (see product_repository.get_by_barcode_or_aliases).
    product = await product_repository.get_by_barcode_or_aliases(db, barcode, alias_keys)
    was_cache_hit = product is not None
    extra_warnings: list[HealthWarning] = []

    if product is None:
        if barcode_info is None:
            raise ProductNotFoundError(
                f"No product found for barcode {barcode}.",
                details=_not_found_details(barcode, []),
            )
        outcome = await barcode_discovery.discover_product(barcode_info)
        if outcome.product is None:
            raise ProductNotFoundError(
                f"No product found for barcode {barcode}.",
                details=_not_found_details(barcode, outcome.attempts),
            )
        product, extra_warnings = await _persist_discovered_product(db, outcome.product)

    # Applies equally to a product just discovered above AND to a cache
    # hit of a previously-discovered-but-incomplete row: neither ever
    # gets a fabricated/placeholder-derived Health Score (V6) — see
    # `has_verified_nutrition`'s docstring on the Product model.
    if not product.has_verified_nutrition:
        raise ProductNotFoundError(
            f"Product {product.barcode} was found but lacks sufficient verified data for a Health Score.",
            details=_label_scan_required_details(product),
        )

    ingredients = await fetch_ingredients_for_product(db, product)

    profile = await _get_profile_namespace(db, user_id)
    score, warnings = _score_and_warnings(product, ingredients, profile)
    warnings = list(warnings) + extra_warnings
    product.health_score = score
    await db.flush()

    await scan_history_repository.insert(
        db,
        ScanHistory(
            user_id=user_id,
            barcode=product.barcode,
            product_name=product.product_name,
            brand=product.brand,
            health_score=score,
            scan_type=ScanType.BARCODE,
        ),
    )
    await db.commit()

    return {
        "product": product,
        "ingredients": ingredients,
        "health_score": score,
        "warnings": warnings,
        "is_from_database_cache": was_cache_hit,
    }


async def analyze_ocr_text(db: AsyncSession, user_id: uuid.UUID, raw_text: str) -> dict:
    all_db_ingredients = await ingredient_repository.get_all(db)
    data, ingredients = await _run_ai_or_fallback("Scanned Product", raw_text, all_db_ingredients)

    barcode = data.barcode or f"ocr_{int(time.time() * 1000)}"
    product = _to_product_model(barcode, data, ingredients)
    product = await product_repository.upsert(db, product)

    profile = await _get_profile_namespace(db, user_id)
    score, warnings = _score_and_warnings(product, ingredients, profile)
    product.health_score = score
    await db.flush()

    await scan_history_repository.insert(
        db,
        ScanHistory(
            user_id=user_id,
            barcode=product.barcode,
            product_name=product.product_name,
            brand=product.brand,
            health_score=score,
            scan_type=ScanType.OCR_LABEL,
        ),
    )
    await db.commit()

    return {
        "product": product,
        "ingredients": ingredients,
        "health_score": score,
        "warnings": warnings,
        "is_from_database_cache": False,
    }


# ---------------------------------------------------------------------------
# Barcode + label enrichment (V8, documented, see README section 11
# "Barcode + label enrichment and multilingual label normalization").
#
# When barcode discovery (`analyze_barcode`) cannot produce a complete
# product it returns `labelScanRequired` (see `_not_found_details` /
# `_label_scan_required_details`). The Android client then resubmits a
# label image (or OCR text) together with the *same* barcode, via the
# optional `barcode` field on `/scan/label-image` and `/scan/ocr-text`
# (see `app/api/v1/scan.py`). `analyze_label_image_with_barcode` /
# `analyze_ocr_text_with_barcode` below combine that barcode identity
# with the freshly-analyzed label content into ONE canonical,
# barcode-keyed product row, instead of the synthetic `img_.../ocr_...`
# row those two endpoints create when no barcode is supplied.
#
# Neither existing endpoint's no-barcode behavior is touched by any of
# this: `analyze_label_image`/`analyze_ocr_text` above are unchanged,
# and the router only calls into this section when a caller explicitly
# supplies a barcode (see `app/api/v1/scan.py`).
#
# Merge rules (mirrors the barcode-discovery trust model in
# `_persist_discovered_product` above, applied one layer down to
# per-field merging rather than whole-row replacement):
#   - A row that is already fully trusted (`is_verified` AND
#     `has_verified_nutrition`) is NEVER modified by this path -- the
#     label analysis is still run (so its provenance is recorded, see
#     `_record_label_provenance`) but never allowed to overwrite it.
#   - Otherwise, identity fields (name/brand/category/image) are kept
#     from the existing row when already meaningful (not empty, not a
#     known discovery placeholder like "Discovered Product"/"Unknown
#     Brand") and filled from the label analysis only when missing.
#   - Nutrition/ingredient fields are kept from the existing row only
#     when `has_verified_nutrition` is already True; otherwise the
#     fresh label analysis's numbers are used (real per-100g figures
#     read directly off the physical label are always more trustworthy
#     than a placeholder/zero-filled incomplete discovery).
#   - `has_verified_nutrition` only flips True when the label analysis
#     itself produced genuinely known nutrition (see
#     `gemini_image_parser.nutrition_fields_present`) AND non-empty
#     ingredient text -- the same "all three core numeric inputs
#     required" gate `barcode_discovery.discover_product` already uses
#     (V7). `is_verified` is kept equal to `has_verified_nutrition`: an
#     incomplete row stays open to a *later* enrichment attempt for the
#     same barcode, and only a genuinely complete one is protected from
#     being overwritten again.
#   - A barcode that fails `barcode_validation.validate_and_normalize`
#     is rejected with the standard `ValidationAppError` before any
#     work is done -- never silently treated as "no barcode".
# ---------------------------------------------------------------------------

# Identity values considered "not meaningful" -- either genuinely empty
# or one of the explicit placeholder fallbacks `barcode_discovery.py` /
# `_to_analyzed_data_from_discovery` writes when a source didn't state
# a real value. An existing row holding one of these is treated as
# missing that field for enrichment purposes, not as a value to protect.
_PLACEHOLDER_IDENTITY_VALUES = {"", "discovered product", "unknown brand"}


def _is_meaningful_identity(value: str | None) -> bool:
    return bool(value) and value.strip().lower() not in _PLACEHOLDER_IDENTITY_VALUES


def _label_nutrition_is_complete(data: AnalyzedProductData, nutrition_known: bool) -> bool:
    return nutrition_known and bool(data.raw_ingredient_text.strip())


def _apply_label_enrichment(
    existing: Product,
    data: AnalyzedProductData,
    ingredients: list[Any],
    nutrition_known: bool,
    source_label: str,
) -> None:
    """Mutates `existing` IN PLACE, filling only what it is missing --
    see the merge rules in the section docstring above. Never called
    for a row that is already `is_verified and has_verified_nutrition`
    (see `_persist_enriched_product`)."""
    if not _is_meaningful_identity(existing.product_name):
        existing.product_name = data.product_name
    if not _is_meaningful_identity(existing.brand):
        existing.brand = data.brand
    if not _is_meaningful_identity(existing.category):
        existing.category = data.category
    if existing.image_url is None:
        existing.image_url = data.image_url

    if not existing.has_verified_nutrition:
        existing.raw_ingredient_text = data.raw_ingredient_text
        existing.ingredient_ids = _ingredient_ids_string(ingredients)
        existing.nova_group = data.nova_group
        existing.sugar_grams = data.sugar_grams
        existing.sodium_mg = data.sodium_mg
        existing.saturated_fat_grams = data.saturated_fat_grams
        existing.has_artificial_sweeteners = data.has_artificial_sweeteners
        existing.has_preservatives = data.has_preservatives
        existing.is_gluten_free = data.is_gluten_free
        existing.is_lactose_free = data.is_lactose_free
        existing.is_vegan = data.is_vegan
        existing.is_vegetarian = data.is_vegetarian
        existing.is_halal = data.is_halal
        existing.is_kosher = data.is_kosher
        existing.allergens_detected = data.allergens_detected

        if _label_nutrition_is_complete(data, nutrition_known):
            existing.has_verified_nutrition = True
            existing.source = source_label
            existing.source_confidence = None

    existing.is_verified = existing.has_verified_nutrition
    existing.last_verified_at = datetime.now(timezone.utc)
    existing.timestamp = int(time.time() * 1000)


def _new_product_from_label(
    canonical_barcode: str,
    data: AnalyzedProductData,
    ingredients: list[Any],
    nutrition_known: bool,
    source_label: str,
) -> Product:
    is_complete = _label_nutrition_is_complete(data, nutrition_known)
    return _to_product_model(
        canonical_barcode,
        data,
        ingredients,
        source=source_label,
        source_confidence=None,
        is_verified=is_complete,
        has_verified_nutrition=is_complete,
    )


async def _persist_enriched_product(
    db: AsyncSession,
    existing: Product | None,
    canonical_barcode: str,
    data: AnalyzedProductData,
    ingredients: list[Any],
    nutrition_known: bool,
    source_label: str,
) -> tuple[Product, bool]:
    """
    Transactional, concurrency-safe insert-or-merge for one barcode +
    label analysis. Returns `(product, used_label_analysis)` --
    `used_label_analysis` is False only when an already fully-trusted
    row made the fresh label analysis unnecessary (see the section
    docstring's first merge rule).

    Reuses `product_repository.insert_new`'s SAVEPOINT-based race
    handling for the brand-new-row case (same guarantee
    `_persist_discovered_product` relies on above). The existing-row
    merge path mutates the already-fetched ORM instance in place and
    flushes -- the same best-effort (not fully linearizable) guarantee
    `_apply_discovered_fields` already relies on for barcode discovery;
    see README "10.6" for why a single shared SQLite test connection
    cannot exercise true cross-transaction races any more faithfully
    than the existing discovery-merge tests do.
    """
    if existing is None:
        candidate = _new_product_from_label(canonical_barcode, data, ingredients, nutrition_known, source_label)
        inserted = await product_repository.insert_new(db, candidate)
        if inserted is not None:
            await db.commit()
            return inserted, True
        # Lost a race with a concurrent enrichment/discovery of the same barcode.
        existing = await product_repository.get_by_barcode(db, canonical_barcode)
        if existing is None:
            raise AIServiceUnavailableError("Could not persist the enriched product.")

    if existing.is_verified and existing.has_verified_nutrition:
        return existing, False

    _apply_label_enrichment(existing, data, ingredients, nutrition_known, source_label)
    await db.flush()
    await db.commit()
    return existing, True


async def _record_label_provenance(
    db: AsyncSession,
    canonical_barcode: str,
    provider_name: str,
    data: AnalyzedProductData,
    label_result: LabelTextResult,
    *,
    used_for_persisted_product: bool,
) -> None:
    """
    Records label-OCR (and, when applicable, translation) provenance
    through the EXISTING `product_sources` provenance table/repository
    (see `app/models/product_source.py` / `product_source_repository.py`)
    -- no schema migration needed: `language`, `confidence`, and
    `raw_metadata_json` already carry everything the language policy
    needs to record (detected language, whether translation was used,
    translation model/confidence/status). See README section 11.4
    "Provenance" / 11.5 "Why no migration was needed".
    """
    ocr_result = ProviderProductResult(
        provider=provider_name,
        external_id=None,
        product_name=data.product_name,
        brand=None,  # never claim provenance authorship of the brand
        category=None,
        image_url=None,
        raw_ingredient_text=label_result.original_text,
        language=label_result.detected_language if label_result.detected_language in ("en", "bg") else None,
        raw_metadata={
            "detectedLanguage": label_result.detected_language,
            "translationUsed": label_result.translation_used,
            "status": label_result.status,
        },
    )
    await product_source_repository.record_discovery(
        db,
        barcode=canonical_barcode,
        result=ocr_result,
        confidence=1.0,
        is_conflicting=False,
        used_for_persisted_product=used_for_persisted_product,
    )

    if label_result.translation_used:
        translation_result = ProviderProductResult(
            provider="label_translation",
            external_id=None,
            product_name=None,
            brand=None,
            category=None,
            image_url=None,
            raw_ingredient_text=label_result.canonical_text,
            language="en",
            raw_metadata={
                "sourceLanguage": label_result.detected_language,
                "model": label_result.translation_model,
                "status": label_result.status,
            },
        )
        await product_source_repository.record_discovery(
            db,
            barcode=canonical_barcode,
            result=translation_result,
            confidence=label_result.translation_confidence or 0.0,
            is_conflicting=False,
            used_for_persisted_product=used_for_persisted_product,
        )


async def _run_label_image_pipeline(
    image_bytes: bytes, all_db_ingredients: list[Any]
) -> tuple[AnalyzedProductData, list[Any], bool]:
    """
    The same Gemini-then-fallback chain `analyze_label_image` runs
    (kept as a separate function, not a shared refactor, so that
    endpoint's behavior/tests are provably untouched by this feature --
    see the section docstring above), plus the one extra signal the
    enrichment path needs: whether the nutrition figures are genuinely
    known (see `gemini_image_parser.nutrition_fields_present`) rather
    than heuristic fallback guesses.
    """
    data: AnalyzedProductData | None = None
    ingredients: list[Any] | None = None
    nutrition_known = False

    try:
        raw_response = await gemini_service.analyze_image(image_bytes)
    except GeminiUnavailableError as exc:
        logger.info("gemini_image_fallback_triggered", reason=str(exc))
    else:
        parsed = parse_gemini_image_json_result(raw_response, all_db_ingredients)
        if parsed is not None:
            data, ingredients = parsed
            nutrition_known = gemini_image_parser.nutrition_fields_present(raw_response)
        else:
            logger.info("gemini_image_json_invalid_falling_back")

    if data is None:
        try:
            data, ingredients = fallback_local_analysis(
                "Scanned Label Product",
                "Ingredients could not be extracted from the image; AI response was unavailable or invalid.",
                all_db_ingredients,
            )
        except Exception as fallback_exc:  # noqa: BLE001
            raise AIServiceUnavailableError(
                "Both the AI service and the local fallback analysis failed."
            ) from fallback_exc
        nutrition_known = False

    return data, ingredients, nutrition_known


async def _finalize_barcode_enrichment(
    db: AsyncSession,
    user_id: uuid.UUID,
    barcode_raw: str,
    barcode_info: "barcode_validation.BarcodeInfo",
    data: AnalyzedProductData,
    ingredients: list[Any],
    nutrition_known: bool,
    *,
    scan_type: ScanType,
    provenance_provider: str,
) -> dict:
    """Shared tail end of `analyze_label_image_with_barcode` and
    `analyze_ocr_text_with_barcode`: applies the language policy,
    resolves/merges/persists the canonical product, records provenance,
    and returns the same `FullProductAnalysisOut`-shaped dict every
    other `analyze_*` function returns (API Contract compatible -- no
    second response shape)."""
    label_result = await resolve_label_text(data.raw_ingredient_text)
    if label_result.canonical_text.strip() != data.raw_ingredient_text.strip():
        tokens = normalize_and_extract_tokens(label_result.canonical_text)
        # Bulgarian ingredient-list tokens are aliased to their English
        # canonical form for MATCHING purposes only (see
        # `label_language.bulgarian_ingredient_alias`) -- an English and
        # a Bulgarian mention of the same ingredient then resolve to the
        # same matched/synthetic ingredient instead of two, satisfying
        # the "deduplicate equivalent ingredients" language policy. The
        # text actually stored (`data.raw_ingredient_text`, set below)
        # is untouched by this substitution.
        matchable_tokens = [label_language.bulgarian_ingredient_alias(t) or t for t in tokens]
        norm = match_against_database(matchable_tokens, await ingredient_repository.get_all(db))
        rebuilt: list[Any] = list(norm.matched_ingredients)
        for unknown in norm.unknown_ingredients:
            rebuilt.append(create_synthetic_ingredient(unknown))
        if rebuilt:
            ingredients = rebuilt
        data.raw_ingredient_text = label_result.canonical_text

    canonical_barcode = barcode_info.gtin13
    alias_keys = barcode_validation.alias_keys(barcode_info)
    existing = await product_repository.get_by_barcode_or_aliases(db, barcode_raw, alias_keys)

    source_label = "label_scan_translated" if label_result.translation_used else "label_scan"
    product, used_label_analysis = await _persist_enriched_product(
        db, existing, canonical_barcode, data, ingredients, nutrition_known, source_label
    )
    # Provenance must reference the row's ACTUAL persisted primary key
    # (`product.barcode`), not `canonical_barcode`: a pre-existing row
    # found via an alias (see `get_by_barcode_or_aliases`) may still be
    # stored under a non-canonical key (e.g. a legacy 12-digit UPC-A),
    # and `product_sources.barcode` is a foreign key into `products.barcode`
    # -- writing the canonical form here would violate it whenever the
    # two differ.
    await _record_label_provenance(
        db,
        product.barcode,
        provenance_provider,
        data,
        label_result,
        used_for_persisted_product=used_label_analysis,
    )
    await db.commit()

    if not product.has_verified_nutrition:
        raise ProductNotFoundError(
            f"Product {product.barcode} was found but lacks sufficient verified data for a Health Score.",
            details=_label_scan_required_details(product),
        )

    ingredients_out = await fetch_ingredients_for_product(db, product)
    profile = await _get_profile_namespace(db, user_id)
    score, warnings = _score_and_warnings(product, ingredients_out, profile)
    product.health_score = score
    await db.flush()

    await scan_history_repository.insert(
        db,
        ScanHistory(
            user_id=user_id,
            barcode=product.barcode,
            product_name=product.product_name,
            brand=product.brand,
            health_score=score,
            scan_type=scan_type,
        ),
    )
    await db.commit()

    return {
        "product": product,
        "ingredients": ingredients_out,
        "health_score": score,
        "warnings": warnings,
        "is_from_database_cache": not used_label_analysis,
    }


async def analyze_label_image_with_barcode(
    db: AsyncSession, user_id: uuid.UUID, image_bytes: bytes, barcode_raw: str
) -> dict:
    """
    API Contract 6.3, extended (see `app/api/v1/scan.py`): combines a
    supplied barcode with the label image analysis into one canonical,
    persisted product (see the section docstring above for the full
    merge design). Raises `ValidationAppError` for a barcode that
    fails `barcode_validation.validate_and_normalize` -- the same
    structured validation error every other invalid-input case in this
    API uses.
    """
    barcode_info = validate_and_normalize(barcode_raw)
    if barcode_info is None:
        raise ValidationAppError(f"'{barcode_raw}' is not a valid barcode.")

    from io import BytesIO

    from PIL import Image

    try:
        Image.open(BytesIO(image_bytes)).verify()
    except Exception as exc:  # noqa: BLE001
        raise ImageUnreadableError("The uploaded file is not a readable image.") from exc

    all_db_ingredients = await ingredient_repository.get_all(db)
    data, ingredients, nutrition_known = await _run_label_image_pipeline(image_bytes, all_db_ingredients)

    return await _finalize_barcode_enrichment(
        db,
        user_id,
        barcode_raw,
        barcode_info,
        data,
        ingredients,
        nutrition_known,
        scan_type=ScanType.OCR_LABEL,
        provenance_provider="label_ocr",
    )


async def analyze_ocr_text_with_barcode(
    db: AsyncSession, user_id: uuid.UUID, raw_text: str, barcode_raw: str
) -> dict:
    """
    API Contract 6.2, extended (see `app/api/v1/scan.py`): same
    barcode + label-content merge as `analyze_label_image_with_barcode`,
    sourced from free-text OCR content instead of an image.

    Nutrition here is always treated as NOT genuinely known
    (`nutrition_known=False`): `analyze_ocr_text`'s Gemini path
    preserves a documented, tested quirk (see `gemini_result_parser.py`)
    where nutrition figures always come from the deterministic
    keyword-heuristic fallback recomputed against the raw text, never
    from Gemini's own structured numbers -- i.e. they are never
    genuinely-extracted real label data for this endpoint, by
    long-standing, intentional design. A barcode resubmitted through
    this endpoint therefore only reaches `has_verified_nutrition=True`
    if the EXISTING row already had it (or a later `/scan/label-image`
    call for the same barcode provides it).
    """
    barcode_info = validate_and_normalize(barcode_raw)
    if barcode_info is None:
        raise ValidationAppError(f"'{barcode_raw}' is not a valid barcode.")

    all_db_ingredients = await ingredient_repository.get_all(db)
    data, ingredients = await _run_ai_or_fallback("Scanned Product", raw_text, all_db_ingredients)
    nutrition_known = False

    return await _finalize_barcode_enrichment(
        db,
        user_id,
        barcode_raw,
        barcode_info,
        data,
        ingredients,
        nutrition_known,
        scan_type=ScanType.OCR_LABEL,
        provenance_provider="ocr_text",
    )


async def analyze_label_image(db: AsyncSession, user_id: uuid.UUID, image_bytes: bytes) -> dict:
    """
    API Contract 6.3. Fallback chain (unchanged contract, V3 fix only
    changes what happens on a successful Gemini call):
      1. Gemini request.
      2. Gemini returns valid, usable JSON -> USE IT (all fields).
      3. Gemini network error/timeout/missing key/invalid or unusable
         JSON -> deterministic `fallback_local_analysis`.
      4. Fallback itself fails -> AIServiceUnavailableError.
    """
    from PIL import Image
    from io import BytesIO

    try:
        Image.open(BytesIO(image_bytes)).verify()
    except Exception as exc:  # noqa: BLE001
        raise ImageUnreadableError("The uploaded file is not a readable image.") from exc

    all_db_ingredients = await ingredient_repository.get_all(db)

    data: AnalyzedProductData | None = None
    ingredients: list[Any] | None = None

    try:
        raw_response = await gemini_service.analyze_image(image_bytes)
    except GeminiUnavailableError as exc:
        logger.info("gemini_image_fallback_triggered", reason=str(exc))
    else:
        parsed = parse_gemini_image_json_result(raw_response, all_db_ingredients)
        if parsed is not None:
            data, ingredients = parsed
        else:
            logger.info("gemini_image_json_invalid_falling_back")

    if data is None:
        try:
            data, ingredients = fallback_local_analysis(
                "Scanned Label Product",
                "Ingredients could not be extracted from the image; AI response was unavailable or invalid.",
                all_db_ingredients,
            )
        except Exception as fallback_exc:  # noqa: BLE001
            raise AIServiceUnavailableError(
                "Both the AI service and the local fallback analysis failed."
            ) from fallback_exc

    barcode = f"img_{int(time.time() * 1000)}"
    product = _to_product_model(barcode, data, ingredients)
    product = await product_repository.upsert(db, product)

    profile = await _get_profile_namespace(db, user_id)
    score, warnings = _score_and_warnings(product, ingredients, profile)
    product.health_score = score
    await db.flush()

    await scan_history_repository.insert(
        db,
        ScanHistory(
            user_id=user_id,
            barcode=product.barcode,
            product_name=product.product_name,
            brand=product.brand,
            health_score=score,
            scan_type=ScanType.OCR_LABEL,
        ),
    )
    await db.commit()

    return {
        "product": product,
        "ingredients": ingredients,
        "health_score": score,
        "warnings": warnings,
        "is_from_database_cache": False,
    }
