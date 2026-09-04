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

V13 (documented, see README "Deviations" section 6 item 11 / section
11.14): INGREDIENT-RECOGNITION SUCCESS VS. HEALTH-SCORE READINESS.
`/scan/label-image`, `/scan/ocr-text`, and their barcode-linked
variants used to treat "sufficient verified data for a Health Score"
(`is_complete`/`is_verified` — BOTH the nutrition and ingredients
evidence groups) as the one gate for returning `200` at all. That
conflated two different goals: ingredient recognition/categorization
(this scan's PRIMARY purpose — see `_run_label_image_pipeline`/
`_run_ai_or_fallback`, the ingredient-matching pipeline, and
`app/services/ocr_normalizer.py`) and full nutrition verification for
Health Score readiness (a separate, optional bonus). A label photo
that clearly shows the ingredients list but not the nutrition panel —
by far the most common single-photo "Ingredient Label" scan — was
returning the SAME `404`/`labelScanRequired` "please rescan" response
as a genuine extraction failure, even though the ingredients had just
been read and categorized correctly.

The success/failure gate for these four functions is now
`has_verified_ingredients`/`ingredients_complete` alone (see
`_finalize_barcode_enrichment` and `_finalize_standalone_label_analysis`
below) — a `200` is returned whenever ingredient recognition succeeded,
regardless of nutrition completeness. `is_verified`/`is_complete`
(BOTH groups) is unchanged and continues to gate ONLY the Health Score:
`health_score` in the response is `int | None` (see
`app/schemas/scan.py`'s docstring) — a genuine score when nutrition is
also verified, `None` (never a fabricated `0`) otherwise; no
scan-history entry is written when there is no real score to log.
`labelScanRequired`/404 is now reserved for what it should always have
meant: no trustworthy ingredient evidence was extracted at all — Gemini
AND the deterministic fallback both failed to produce real, usable
label content. `POST /scan/barcode` (`analyze_barcode`, no image/OCR
text — a pure identity lookup) is INTENTIONALLY UNCHANGED: it has no
ingredient-recognition step of its own to succeed at, so it keeps
gating on `is_verified` exactly as before.
"""
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    AIServiceUnavailableError,
    ImageUnreadableError,
    ProductNotFoundError,
    ValidationAppError,
)
from app.integrations.barcode_providers.base import ProviderProductResult
from app.integrations.gemini import GeminiUnavailableError, gemini_service
from app.models.enums import IngredientVerificationStatus, ScanType, WarningSeverity
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
from app.services import ingredient_catalog
from app.services import ingredient_regulatory
from app.services.barcode_text_safety import is_placeholder
from app.services.barcode_validation import validate_and_normalize
from app.services.fallback_analysis import AnalyzedProductData, fallback_local_analysis
from app.services.gemini_image_parser import parse_gemini_image_json_result
from app.services.gemini_result_parser import parse_gemini_json_result
from app.services import label_language
from app.services.label_language import LabelTextResult, resolve_label_text
from app.services.ocr_normalizer import (
    create_synthetic_ingredient,
    match_against_database,
    normalize_and_extract_tokens,
    reconstruct_synthetic_ingredient,
)
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
    # V12 (PR #9 review round 5, finding 2): these three used to default
    # to True -- fail-OPEN, matching the (also since-reversed) `Product`
    # column defaults. Every real call site already passes all three
    # explicitly (see `_persist_discovered_product`, `_new_product_from_label`,
    # `_finalize_standalone_label_analysis`); the default is deliberately
    # never exercised by this app's own code and exists only as a last-
    # resort safety net, so it must fail SAFE (unverified) rather than
    # silently fabricate verified, Health-Score-eligible evidence for a
    # future call site that forgets to pass one.
    is_verified: bool = False,
    has_verified_nutrition: bool = False,
    has_verified_ingredients: bool = False,
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
        nutrition_basis=data.nutrition_basis,
        serving_size=data.serving_size,
        serving_unit=data.serving_unit,
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
        has_verified_ingredients=has_verified_ingredients,
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
    has_verified_ingredients: bool,
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
    existing.nutrition_basis = data.nutrition_basis
    existing.serving_size = data.serving_size
    existing.serving_unit = data.serving_unit
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
    existing.has_verified_ingredients = has_verified_ingredients
    existing.is_verified = has_verified_nutrition and has_verified_ingredients
    existing.last_verified_at = datetime.now(timezone.utc)
    existing.timestamp = int(time.time() * 1000)


def _to_analyzed_data_from_discovery(
    discovered: "barcode_discovery.DiscoveredProduct", db_ingredients: list[Any]
) -> tuple[AnalyzedProductData, list[Any], bool, bool]:
    """
    Bridges a validated `DiscoveredProduct` (external, provider-sourced)
    into the same `AnalyzedProductData` shape the OCR/Gemini pipelines
    use, so it goes through the identical ingredient-matching, Health
    Score, and Warning Engine code paths — see module docstring, V5/V6.

    Returns `(data, ingredients, nutrition_known, ingredients_known)`.
    Deliberately TWO independent booleans (V11, PR #9 review round 4;
    previously a single combined `is_complete` flag): a trusted provider
    frequently states real per-100g nutrition with no `ingredients_text`
    (or vice versa), and the caller (`_persist_discovered_product`) now
    tracks each as its own `Product.has_verified_nutrition`/
    `has_verified_ingredients` column so a LATER label scan for the same
    barcode can independently complete whichever group the barcode
    discovery didn't provide -- see the "Barcode + label enrichment"
    section below and README section 11.8. `analyze_barcode` gates the
    Health Score on `product.is_verified` (both true), never on either
    flag alone, so neither is scored in isolation.

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
    nutrition_known = discovered.nutrition_known
    ingredients_known = discovered.ingredients_known
    is_complete = nutrition_known and ingredients_known

    ingredient_list: list[Any] = []
    if ingredients_known:
        tokens = normalize_and_extract_tokens(discovered.raw_ingredient_text)
        norm = match_against_database(tokens, db_ingredients)
        ingredient_list = list(norm.matched_ingredients)
        for unk in norm.unknown_ingredients:
            ingredient_list.append(create_synthetic_ingredient(unk))

    n = discovered.nutrition
    nova_group = n.nova_group
    if nova_group is None:
        # Only guessed when there's real ingredient data to base the
        # guess on (same heuristic `fallback_local_analysis` uses for
        # OCR text with no explicit NOVA signal); a discovery with no
        # ingredients at all gets an explicit "unclassified" 0 instead
        # of a guess dressed up as a real NOVA group -- NOVA
        # classification is inferred from the ingredient list (additive
        # count/presence), not the nutrition panel, so it tracks
        # `ingredients_known` specifically, not overall completeness.
        nova_group = (
            (4 if (len(ingredient_list) > 5 or n.has_artificial_sweeteners or n.has_preservatives) else 3)
            if ingredients_known
            else 0
        )

    flags = discovered.dietary_flags
    allergens_text = ", ".join(discovered.allergens) if discovered.allergens else ""

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
        nutrition_basis="PER_100_G" if nutrition_known else "UNKNOWN",
    )
    return data, ingredient_list, nutrition_known, ingredients_known


def _not_found_details(barcode: str, attempts: list["barcode_discovery.ProviderAttempt"]) -> dict:
    return {
        "labelScanRequired": True,
        "reason": "Barcode not found in the local database or any configured external source.",
        "providersChecked": [{"provider": a.provider, "outcome": a.outcome} for a in attempts],
        "suggestedAction": "Use POST /scan/label-image or POST /scan/ocr-text to analyze the product's label directly.",
    }


def _as_utc(value: datetime) -> datetime:
    """SQLite doesn't preserve timezone info on a `DateTime(timezone=True)`
    column -- a value written as UTC (every timestamp this module ever
    writes, see `ingredient_catalog._utcnow`) comes back naive, and both
    `datetime` subtraction and `.timestamp()` silently misbehave for a
    naive value (the latter assumes the LOCAL system timezone) unless
    corrected first. Same pattern as `auth_service`'s
    `expires_at.replace(tzinfo=timezone.utc)`."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _to_epoch_millis(value: datetime | None) -> int | None:
    """Matches `IngredientOut`'s own `_serialize_epoch_millis` field
    serializer -- kept as a tiny standalone helper (not imported from
    `app/schemas/`, per this function's own schema-free-services rule)
    so `_ingredient_out_dict` renders `retrievedAt`/`lastVerifiedAt`
    identically to the normal response path."""
    return None if value is None else int(_as_utc(value).timestamp() * 1000)


def _ingredient_out_dict(ing: Any) -> dict:
    """Plain-dict mirror of `app.schemas.ingredient.IngredientOut`'s
    camelCase shape, built BY HAND rather than by importing that schema
    -- services stay schema-free (see CLAUDE.md section 2: routers own
    `app/schemas/`); this is a raw `dict` placed straight into an
    `AppError.details` payload, which bypasses `response_model`
    serialization entirely (see `app.main._error_envelope`) -- so it
    must already be plain, JSON-serializable data. Works identically for
    a real `Ingredient` ORM row and a `SyntheticIngredient` (see
    `ocr_normalizer.create_synthetic_ingredient`) -- both expose the
    exact same attribute names. Field names/casing -- including the
    additive `riskAssessmentAvailable`/`riskRationale`/
    `efsaApprovalStatus`/`fdaApprovalStatus`/`adiMinMgPerKgBwPerDay`/
    `adiMaxMgPerKgBwPerDay`/`adiSource` data-quality fields, derived via
    `app.services.ingredient_regulatory` exactly like the schema's own
    `@computed_field`s -- are kept in exact sync with `IngredientOut` so
    a partial-analysis `ingredients` list
    (see `_label_scan_required_details`) can be rendered by the SAME
    Android model/adapter the normal success response's `ingredients`
    array already uses -- no new client-side type.
    """
    risk_level = ing.risk_level
    risk_assessment_available = getattr(ing, "risk_assessment_available", True)
    adi_min, adi_max = ingredient_regulatory.derive_adi_range_mg_per_kg_bw_per_day(
        ing.acceptable_daily_intake
    )
    verification_status = getattr(ing, "verification_status", IngredientVerificationStatus.UNVERIFIED)
    source = getattr(ing, "source", None)
    last_verified_at = getattr(ing, "last_verified_at", None)
    needs_refresh = False
    if verification_status == IngredientVerificationStatus.VERIFIED and last_verified_at is not None:
        age = datetime.now(timezone.utc) - _as_utc(last_verified_at)
        needs_refresh = age > timedelta(seconds=settings.INGREDIENT_VERIFIED_DATA_TTL_SECONDS)
    return {
        "id": ing.id,
        "commonName": ing.common_name,
        "scientificName": ing.scientific_name,
        "eNumber": ing.e_number,
        "insNumber": getattr(ing, "ins_number", None),
        "casNumber": getattr(ing, "cas_number", None),
        "category": ing.category,
        "description": ing.description,
        "purposeInFood": ing.purpose_in_food,
        "healthConcerns": ing.health_concerns,
        "evidenceLevel": ing.evidence_level,
        "countriesRestrictedOrBanned": ing.countries_restricted_or_banned,
        "efsaStatus": ing.efsa_status,
        "fdaStatus": ing.fda_status,
        "whoIarcClassification": ing.who_iarc_classification,
        "acceptableDailyIntake": ing.acceptable_daily_intake,
        "sideEffects": ing.side_effects,
        "allergens": ing.allergens,
        "references": ing.references,
        "riskLevel": risk_level.value if hasattr(risk_level, "value") else risk_level,
        "riskAssessmentAvailable": risk_assessment_available,
        "riskRationale": (ing.evidence_level or None) if risk_assessment_available else None,
        "efsaApprovalStatus": ingredient_regulatory.derive_approval_status(ing.efsa_status).value,
        "fdaApprovalStatus": ingredient_regulatory.derive_approval_status(ing.fda_status).value,
        "adiMinMgPerKgBwPerDay": adi_min,
        "adiMaxMgPerKgBwPerDay": adi_max,
        "adiSource": (ing.references or None) if adi_min is not None else None,
        "verificationStatus": (
            verification_status.value if hasattr(verification_status, "value") else verification_status
        ),
        "source": (source.value if hasattr(source, "value") else source),
        "sourceRecordId": getattr(ing, "source_record_id", None),
        "sourceUrl": getattr(ing, "source_url", None),
        "retrievedAt": _to_epoch_millis(getattr(ing, "retrieved_at", None)),
        "lastVerifiedAt": _to_epoch_millis(last_verified_at),
        "confidence": (float(c) if (c := getattr(ing, "confidence", None)) is not None else None),
        "schemaVersion": getattr(ing, "schema_version", None),
        "needsRefresh": needs_refresh,
        "isGluten": ing.is_gluten,
        "isLactose": ing.is_lactose,
        "isVegan": ing.is_vegan,
        "isVegetarian": ing.is_vegetarian,
        "isHalal": ing.is_halal,
        "isKosher": ing.is_kosher,
        "badForDiabetes": ing.bad_for_diabetes,
        "badForHypertension": ing.bad_for_hypertension,
        "badForKidneyDisease": ing.bad_for_kidney_disease,
        "badForGout": ing.bad_for_gout,
        "badForPregnancy": ing.bad_for_pregnancy,
        "badForChildren": ing.bad_for_children,
        "badForHighCholesterol": ing.bad_for_high_cholesterol,
    }


def _label_scan_required_details(product: Product, ingredients: list[Any] | None = None) -> dict:
    """Structured response for a barcode whose *identity* is known (was
    discovered and persisted — see `_persist_discovered_product`) but
    whose nutrition/ingredient data is too incomplete to compute a real
    Health Score. Deliberately shaped like `_not_found_details` (same
    `labelScanRequired`/`suggestedAction` keys) so a client can handle
    both with one code path, plus the identity fields worth showing the
    user even without a score.

    V12 (PR #9 review round 5, finding 3): additive partial-analysis
    fields, so an incomplete result doesn't have to discard a genuinely
    USEFUL half of the analysis just because the OTHER evidence group is
    still missing -- e.g. an ingredients-only label photo (the normal
    output of the Android "Ingredient Label" tab when only the
    ingredients list, not the nutrition panel, was in frame) should
    still hand back its normalized/scientific ingredient analysis, not
    just a bare "scan again" signal. See README section 11.11.

    `ingredients` is the CALLER's job to pass, and ONLY when it is
    genuinely trustworthy -- i.e. only when `product.
    has_verified_ingredients` is True (see every caller of this
    function). Left as `None` (nothing added to the response) whenever
    there is no genuinely trustworthy partial data to return at all
    (e.g. a full Gemini-and-fallback failure, where even the
    "ingredients" are tokens from a hardcoded placeholder/error string,
    never real label content) -- this function never fabricates
    evidence, it only surfaces evidence that's already been judged
    trustworthy elsewhere.

    `nutritionScanRequired`/`ingredientsScanRequired` are reported
    per-group (from `product.has_verified_nutrition`/
    `has_verified_ingredients` directly) rather than assuming which one
    is missing -- a barcode discovery can be missing either group (or
    both), not only nutrition.
    """
    details = {
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
        # Additive (V12) -- a client that only inspects `labelScanRequired`
        # (the pre-existing contract) is completely unaffected.
        "analysisComplete": False,
        "healthScoreAvailable": False,
        # Explicitly null, never 0 or omitted -- 0 would read as a real
        # (very poor) score rather than "not computed at all".
        "healthScore": None,
        "nutritionScanRequired": not product.has_verified_nutrition,
        "ingredientsScanRequired": not product.has_verified_ingredients,
    }
    if ingredients is not None:
        details["ingredients"] = [_ingredient_out_dict(ing) for ing in ingredients]
    return details


async def _persist_discovered_product(
    db: AsyncSession, discovered: "barcode_discovery.DiscoveredProduct"
) -> tuple[Product, list[HealthWarning]]:
    """
    Normalizes + validates + persists a multi-source discovery result.
    Concurrency-safe (see `product_repository.insert_new`) and never
    overwrites an existing verified (`is_verified` -- both evidence
    groups, see V11 below) row, nor an existing externally-discovered
    row of equal-or-higher confidence — see the module docstring's
    V5/V6 entries and README "Deviations".

    Always persists identity/provenance when a provider found *something*
    — including a materially-incomplete, `is_verified=False` result —
    so a repeat scan doesn't re-hit external providers every time and
    so the identity is available for `_label_scan_required_details`. It
    is the caller's (`analyze_barcode`'s) job to check `product.
    is_verified` and refuse to compute/return a Health Score for such a
    row.

    V11 (PR #9 review round 4): `nutrition_known`/`ingredients_known`
    are tracked as the two independent `has_verified_nutrition`/
    `has_verified_ingredients` columns (previously combined into a
    single `has_verified_nutrition` flag standing in for overall
    completeness). `is_verified` is `nutrition_known AND
    ingredients_known` -- a genuinely complete discovery (both groups
    known from a trusted provider) is protected exactly like a
    genuinely complete label-scan enrichment is, and a discovery that
    only supplied one group stays open for a LATER barcode+label-image
    scan (see `_apply_label_enrichment`) to independently complete the
    other, without needing to re-supply what's already trusted.
    """
    all_db_ingredients = await ingredient_repository.get_all(db)
    data, ingredients, nutrition_known, ingredients_known = _to_analyzed_data_from_discovery(
        discovered, all_db_ingredients
    )
    # Persistent ingredient knowledge cache: any ingredient with no
    # curated match becomes a real, reusable catalog row (get-or-create,
    # race-safe) instead of an unpersisted in-memory stub -- see
    # `ingredient_catalog`'s module docstring.
    ingredients = await ingredient_catalog.materialize_ingredients(db, ingredients)
    is_complete = nutrition_known and ingredients_known
    # Canonical GTIN-13 storage key (see product_repository.get_by_barcode_or_aliases)
    # — every equivalent representation of this barcode converges on one row.
    canonical_barcode = discovered.barcode.gtin13

    candidate = _to_product_model(
        canonical_barcode,
        data,
        ingredients,
        source=discovered.primary_provider,
        source_confidence=discovered.confidence,
        is_verified=is_complete,
        has_verified_nutrition=nutrition_known,
        has_verified_ingredients=ingredients_known,
    )
    if not is_complete:
        # Mirrors `_new_product_from_label`: an incomplete row must not
        # carry a misleading verification timestamp (review round 3,
        # finding 7's principle, applied here too).
        candidate.last_verified_at = None

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
                has_verified_nutrition=nutrition_known,
                has_verified_ingredients=ingredients_known,
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
    found there. Reconstruction uses the persisted raw label text so an
    internal `synth_*` key is never exposed as the human-facing name.
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
            resolved.append(reconstruct_synthetic_ingredient(ingredient_id, product.raw_ingredient_text))
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
    # An ingredient with no real risk assessment (an OCR-only match with
    # no scientific-database row -- see `ocr_normalizer.SyntheticIngredient`,
    # `risk_assessment_available=False`) carries a neutral SAFE
    # `risk_level` placeholder, never a real one -- it must never move
    # the Health Score in either direction, so it is excluded here
    # rather than counted as SAFE (which the loop below would otherwise
    # do silently, since SAFE contributes no deduction anyway -- this
    # exclusion is what actually matters once a future HIGH_CONCERN/
    # POTENTIAL_CONCERN default is ever considered for unassessed rows).
    risk_levels = [
        ing.risk_level
        for ing in ingredients
        if getattr(ing, "risk_assessment_available", True)
    ]
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
    # gets a fabricated/placeholder-derived Health Score (V6). `is_verified`
    # (V11: both `has_verified_nutrition` AND `has_verified_ingredients`)
    # is the single completeness gate — see `Product`'s docstring — so a
    # row missing EITHER evidence group still correctly returns
    # `labelScanRequired` here, exactly like a row missing both.
    if not product.is_verified:
        # V12 (review round 5, finding 3): a discovery that already
        # established one evidence group (e.g. a trusted provider's real
        # `ingredients_text` but no nutriments) hands that group back as
        # genuinely useful partial data, not just a bare retry signal.
        partial_ingredients = (
            await fetch_ingredients_for_product(db, product) if product.has_verified_ingredients else None
        )
        raise ProductNotFoundError(
            f"Product {product.barcode} was found but lacks sufficient verified data for a Health Score.",
            details=_label_scan_required_details(product, partial_ingredients),
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
    """
    API Contract 6.2 (no barcode). V11 (PR #9 review round 4, finding
    2): now applies the SAME language policy (English/Bulgarian
    preference, other-language translation, ingredient normalization/
    dedup) as the barcode-linked path, via the shared
    `_finalize_standalone_label_analysis`.

    CONTRACT CHANGE (V12, PR #9 review round 5, finding 1 -- see README
    "Deviations" and section 11.11): this endpoint previously ALWAYS
    returned `200` with `is_verified=True` (the now-removed
    `always_verified` escape hatch), on the theory that literal
    user-submitted OCR text is inherently trusted local-pipeline content.
    Review round 5 found that unsafe specifically for NUTRITION: this
    endpoint's nutrition figures are ALWAYS the deterministic keyword-
    heuristic guess (the documented `gemini_result_parser.py` quirk --
    see `analyze_ocr_text_with_barcode`'s docstring for the same point),
    never genuinely extracted, regardless of Gemini's success or the
    raw text's own quality -- so treating them as verified fabricated a
    confident-looking Health Score from numbers that were always a
    guess. Ingredients are different: `raw_text` here is the caller's
    own genuine, literal text (the router rejects anything under 3
    characters), so `has_verified_ingredients` DOES still become True,
    same as before.

    V13 AMENDMENT (see module docstring "V13" entry and README section
    11.14 -- supersedes the paragraph above): `_finalize_standalone_label_analysis`
    now gates success on ingredient recognition alone, not on full
    verification. Since `ingredients_trustworthy=True` unconditionally
    here, a standalone `/scan/ocr-text` call now ALWAYS returns `200`
    (the router already rejects anything under 3 characters before this
    function even runs) -- carrying the caller's own genuinely
    recognized/categorized ingredients, but with `healthScore: null` and
    `hasVerifiedNutrition: false` on the product, since nutrition still
    can never be genuinely verified through this endpoint alone. A
    barcode resubmitted through `analyze_ocr_text_with_barcode` is
    unaffected by this scoping: an EXISTING row's already-verified
    nutrition (from a trusted barcode provider, or a later
    `/scan/label-image` call) is preserved and still completes the
    product normally -- see that function's docstring.
    """
    all_db_ingredients = await ingredient_repository.get_all(db)
    data, ingredients = await _run_ai_or_fallback("Scanned Product", raw_text, all_db_ingredients)
    ingredients = await ingredient_catalog.materialize_ingredients(db, ingredients)
    validity = gemini_image_parser.LabelFieldValidity()
    ingredients_trustworthy = True

    return await _finalize_standalone_label_analysis(
        db,
        user_id,
        data,
        ingredients,
        validity,
        ingredients_trustworthy,
        barcode_prefix="ocr",
        scan_type=ScanType.OCR_LABEL,
        provenance_provider="ocr_text",
    )


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
#   - A row that is already fully trusted (`is_verified` -- BOTH
#     `has_verified_nutrition` AND `has_verified_ingredients`) is only
#     modified by a NEW attempt that itself supplies a complete group
#     (see `_apply_label_enrichment`'s own docstring below for the
#     current per-group refresh rules); an incomplete/invalid attempt
#     never touches it. The label analysis is always run (so its
#     provenance is recorded, see `_record_label_provenance`) but only a
#     complete group is ever allowed to overwrite the corresponding
#     already-verified group.
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
#
# V13 AMENDMENT: everything above (the merge rules themselves --
# per-field identity/nutrition/ingredients merging, the `has_verified_*`
# completeness gates, `is_verified`) is UNCHANGED. What changed is what
# `_finalize_barcode_enrichment` does with the result: it now returns
# `200` whenever `has_verified_ingredients` is True, independent of
# `has_verified_nutrition`/`is_verified` -- see the module docstring's
# "V13" entry and that function's own gate for the full rationale.
# ---------------------------------------------------------------------------

# Identity values considered "not meaningful" -- either a generic
# placeholder string (see `barcode_text_safety.is_placeholder` -- "",
# "null", "none", "n/a", "unknown", ...; review finding 3 surfaced that
# a legacy row's `allergens_detected="None"` must NOT be treated as a
# real, protected value) or one of the explicit placeholder fallbacks
# `barcode_discovery.py` / `_to_analyzed_data_from_discovery` writes
# when a source didn't state a real value. An existing row holding one
# of these is treated as missing that field for enrichment purposes,
# not as a value to protect.
_PLACEHOLDER_IDENTITY_VALUES = {"discovered product", "unknown brand"}


def _is_meaningful_identity(value: str | None) -> bool:
    if is_placeholder(value):
        return False
    return value.strip().lower() not in _PLACEHOLDER_IDENTITY_VALUES


def _nutrition_group_is_complete(validity: "gemini_image_parser.LabelFieldValidity") -> bool:
    """The NUTRITION evidence group: all three core numeric Health
    Score inputs genuinely known. Independent of the ingredients group
    -- see the section docstring above and README section 11.8."""
    return validity.all_valid


def _ingredients_group_is_complete(data: AnalyzedProductData, ingredients_trustworthy: bool) -> bool:
    """The INGREDIENTS evidence group: real, trustworthy ingredient-list
    text was extracted (not a heuristic guess from a placeholder/error
    string -- see `ingredients_trustworthy`'s callers) AND it's actually
    non-empty. Independent of the nutrition group."""
    return ingredients_trustworthy and bool(data.raw_ingredient_text.strip())


def _apply_label_enrichment(
    existing: Product,
    data: AnalyzedProductData,
    ingredients: list[Any],
    validity: "gemini_image_parser.LabelFieldValidity",
    ingredients_trustworthy: bool,
    source_label: str,
) -> None:
    """
    Mutates `existing` IN PLACE. Complete first-party label evidence may
    refresh the corresponding group; incomplete evidence only fills missing
    fields and never erases a verified group. Does NOT commit -- the caller
    controls the single outer-transaction commit point (review round 3
    finding 3; see `_finalize_barcode_enrichment`).

    V11 (PR #9 review round 4) -- CUMULATIVE completeness across TWO
    INDEPENDENT evidence groups, each with its own gate/commit
    condition, so neither request ever needs to supply both at once:

      - INGREDIENTS group (`existing.has_verified_ingredients`):
        raw_ingredient_text/ingredient_ids, NOVA group, dietary flags,
        and allergens -- all physically read off the ingredient list,
        so they're gated and committed together. Only touched while
        `not existing.has_verified_ingredients`; once genuinely
        complete (`_ingredients_group_is_complete`), it's locked and
        this attempt's ingredient data is never consulted again.
      - NUTRITION group (`existing.has_verified_nutrition`):
        sugar/sodium/saturated-fat, each still gated individually by
        `validity` (review round 3 finding 1: a field this attempt
        couldn't trust leaves an already-stored value from an EARLIER
        attempt untouched, rather than blanking it). Only touched while
        `not existing.has_verified_nutrition`; locked the same way once
        `_nutrition_group_is_complete`.

    Both blocks can fire in the SAME call (the common case: one label
    photo supplies both), or only one can (a photo of just the
    nutrition panel, or just the ingredients list) -- whichever group(s)
    this attempt didn't complete simply stay exactly as they were,
    ready for a LATER attempt to complete independently. `data.
    sugar_grams`/etc. are already guaranteed safe regardless of
    `validity` (never a boolean/NaN/Infinity/negative/out-of-range
    value -- see `gemini_image_parser._safe_nutrition_value`/
    `_safe_nova_group`), so even the very first fill-in can never write
    garbage.
    """
    if not _is_meaningful_identity(existing.product_name):
        existing.product_name = data.product_name
    if not _is_meaningful_identity(existing.brand):
        existing.brand = data.brand
    if not _is_meaningful_identity(existing.category):
        existing.category = data.category
    if existing.image_url is None:
        existing.image_url = data.image_url

    newly_completed_group = False

    ingredients_complete_this_scan = _ingredients_group_is_complete(data, ingredients_trustworthy)
    nutrition_complete_this_scan = _nutrition_group_is_complete(validity)

    # A user-requested label scan is newer first-party evidence. A complete
    # group replaces that group even when a provider previously marked the
    # product complete; an incomplete/invalid group never erases verified
    # evidence. Ingredient lists are replaced (not unioned) so removed or
    # corrected label items do not linger forever.
    if ingredients_complete_this_scan or not existing.has_verified_ingredients:
        existing.raw_ingredient_text = data.raw_ingredient_text
        existing.ingredient_ids = _ingredient_ids_string(ingredients)
        if validity.nova_valid:
            existing.nova_group = data.nova_group
        existing.has_artificial_sweeteners = data.has_artificial_sweeteners
        existing.has_preservatives = data.has_preservatives
        existing.is_gluten_free = data.is_gluten_free
        existing.is_lactose_free = data.is_lactose_free
        existing.is_vegan = data.is_vegan
        existing.is_vegetarian = data.is_vegetarian
        existing.is_halal = data.is_halal
        existing.is_kosher = data.is_kosher
        # Allergen text: only overwritten when this attempt actually
        # names something -- an unknown/unreadable answer ("", see
        # `gemini_image_parser._extract_allergens_text`) must never
        # erase an allergen list a previous attempt already established
        # (review round 3 finding 3).
        if _is_meaningful_identity(data.allergens_detected):
            existing.allergens_detected = data.allergens_detected

        if ingredients_complete_this_scan:
            existing.has_verified_ingredients = True
            newly_completed_group = True

    if nutrition_complete_this_scan or not existing.has_verified_nutrition:
        if validity.sugar_valid:
            existing.sugar_grams = data.sugar_grams
        if validity.sodium_valid:
            existing.sodium_mg = data.sodium_mg
        if validity.saturated_fat_valid:
            existing.saturated_fat_grams = data.saturated_fat_grams

        if nutrition_complete_this_scan:
            existing.nutrition_basis = data.nutrition_basis
            existing.serving_size = data.serving_size
            existing.serving_unit = data.serving_unit
            existing.has_verified_nutrition = True
            newly_completed_group = True

    if newly_completed_group:
        existing.source = source_label
        existing.source_confidence = None

    existing.is_verified = existing.has_verified_nutrition and existing.has_verified_ingredients
    # Review round 3 finding 7: `last_verified_at` is a claim that
    # verification actually happened -- only ever stamp it when the
    # product is now (or still) FULLY verified (both groups). A row
    # that's still missing one group after this attempt must not
    # receive a timestamp implying it was just fully re-verified.
    if existing.is_verified:
        existing.last_verified_at = datetime.now(timezone.utc)
    existing.timestamp = int(time.time() * 1000)


def _new_product_from_label(
    canonical_barcode: str,
    data: AnalyzedProductData,
    ingredients: list[Any],
    validity: "gemini_image_parser.LabelFieldValidity",
    ingredients_trustworthy: bool,
    source_label: str,
) -> Product:
    nutrition_complete = _nutrition_group_is_complete(validity)
    ingredients_complete = _ingredients_group_is_complete(data, ingredients_trustworthy)
    is_complete = nutrition_complete and ingredients_complete
    product = _to_product_model(
        canonical_barcode,
        data,
        ingredients,
        source=source_label,
        source_confidence=None,
        is_verified=is_complete,
        has_verified_nutrition=nutrition_complete,
        has_verified_ingredients=ingredients_complete,
    )
    if not is_complete:
        # `_to_product_model` unconditionally stamps `last_verified_at`
        # (correct for its OTHER callers, which only ever create
        # fully-verified rows) -- an incomplete enrichment must not
        # receive a misleading verification timestamp (review round 3
        # finding 7).
        product.last_verified_at = None
    return product


async def _persist_enriched_product(
    db: AsyncSession,
    existing: Product | None,
    canonical_barcode: str,
    data: AnalyzedProductData,
    ingredients: list[Any],
    validity: "gemini_image_parser.LabelFieldValidity",
    ingredients_trustworthy: bool,
    source_label: str,
) -> tuple[Product, bool]:
    """
    Insert-or-merge for one barcode + label analysis. Returns
    `(product, used_label_analysis)`. A caller explicitly submitted fresh
    label evidence, so the second value is True whenever persistence succeeds;
    complete groups may refresh an already-verified provider product.

    Deliberately does NOT commit: the caller
    (`_finalize_barcode_enrichment`) owns the single outer-transaction
    commit point so product changes, provenance, score and scan history
    all land atomically together or not at all (review round 3 finding 3).

    Concurrency guarantee -- stated precisely, not overclaimed (review
    round 3 finding 3, updated for the `for_update` row lock below): the
    brand-new-row case reuses `product_repository.insert_new`'s
    SAVEPOINT-based primary-key-conflict handling, which IS genuinely
    safe under real concurrent transactions (verified against real,
    separate PostgreSQL connections -- see
    `tests/postgres/test_concurrent_enrichment_postgres.py`): two
    concurrent enrichments of the same brand-new barcode always converge
    on exactly one row, never a duplicate, never a crash. The
    EXISTING-row merge path (`_apply_label_enrichment` above) is
    protected the same way on PostgreSQL: `_finalize_barcode_enrichment`
    fetches `existing` via `product_repository.
    get_by_barcode_or_aliases(..., for_update=True)`, a `SELECT ... FOR
    UPDATE` that holds a real row lock for the rest of the attempt. A
    second concurrent attempt against the same row blocks on that lock
    until the first commits, then reads the first's already-persisted
    result before applying its own -- so two attempts that each
    complete a DIFFERENT evidence group (one ingredients, one nutrition)
    both survive, never "last write wins" silently discarding one
    (verified against real, separate PostgreSQL connections -- see
    `test_concurrent_enrichment_of_the_same_existing_row_preserves_both_groups`
    in the same Postgres test file). This is a real row lock, not an
    application-level merge: it serializes concurrent attempts against
    the same existing row rather than running them in parallel, and (like
    any `FOR UPDATE`) is only as strong as the database session actually
    holding it -- SQLite (the default `pytest -q` suite) accepts
    `with_for_update()` without enforcing it, so this guarantee is
    PostgreSQL-only, exactly like the brand-new-row guarantee above.
    """
    if existing is None:
        candidate = _new_product_from_label(
            canonical_barcode, data, ingredients, validity, ingredients_trustworthy, source_label
        )
        inserted = await product_repository.insert_new(db, candidate)
        if inserted is not None:
            return inserted, True
        # Lost a race with a concurrent enrichment/discovery of the same barcode.
        existing = await product_repository.get_by_barcode(db, canonical_barcode)
        if existing is None:
            raise AIServiceUnavailableError("Could not persist the enriched product.")

    _apply_label_enrichment(existing, data, ingredients, validity, ingredients_trustworthy, source_label)
    await db.flush()
    return existing, True


# Review finding 7: the OCR-extraction pipeline (Gemini image analysis
# for `label_ocr`, the text-analysis chain for `ocr_text`) never
# reports its own extraction-quality/confidence figure -- unlike the
# SEPARATE translation call, which does (self-reported, but never
# trusted alone -- see `label_language._verify_translation_invariants`).
# There is therefore no real per-request confidence value available for
# the OCR provenance row, and `1.0` was a fabricated, unjustified "full
# confidence" claim. `0.0` is not "zero confidence this is right" --
# it's the conservative, explicitly-unknown sentinel already built into
# the schema (`ProductSource.confidence`'s own `default=0`, the same
# value `product_source_repository` already uses for "no signal"),
# reused here rather than inventing a new nullable column.
_OCR_PROVENANCE_CONFIDENCE_UNKNOWN = 0.0


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
    "Provenance" / 11.5 "Why no migration was needed". Does NOT commit
    -- see `_finalize_barcode_enrichment`'s single outer-transaction
    commit point (review finding 3).
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
            "confidenceBasis": "not reported by the OCR/extraction pipeline",
        },
    )
    await product_source_repository.record_discovery(
        db,
        barcode=canonical_barcode,
        result=ocr_result,
        confidence=_OCR_PROVENANCE_CONFIDENCE_UNKNOWN,
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
) -> tuple[AnalyzedProductData, list[Any], "gemini_image_parser.LabelFieldValidity", bool]:
    """
    The Gemini-then-fallback chain for label-image analysis: try
    Gemini's structured extraction; on any failure/invalid response,
    fall back to the deterministic keyword heuristic. Shared by BOTH
    `analyze_label_image` (standalone, no barcode) and
    `analyze_label_image_with_barcode` (review round 4, finding 3:
    "avoid duplicated pipelines" -- previously the standalone endpoint
    had its own copy of this exact chain, which is how the two paths
    drifted apart in the first place; see README section 11.9).

    Returns `(data, ingredients, validity, ingredients_trustworthy)`:
    `validity` is which nutrition/NOVA fields are genuinely,
    individually trustworthy (see `gemini_image_parser.
    label_field_validity`); `ingredients_trustworthy` is True only when
    Gemini's structured extraction succeeded (`parsed is not None`) --
    False whenever the deterministic fallback ran, since ITS "raw text"
    for the label-image case is a hardcoded placeholder/error string
    ("Ingredients could not be extracted...", see below), never real
    label content, so ingredients tokenized from it are never
    trustworthy evidence (contrast `analyze_ocr_text_with_barcode`,
    where the fallback tokenizes the user's own genuine OCR text).
    """
    data: AnalyzedProductData | None = None
    ingredients: list[Any] | None = None
    validity = gemini_image_parser.LabelFieldValidity()
    ingredients_trustworthy = False

    try:
        raw_response = await gemini_service.analyze_image(image_bytes)
    except GeminiUnavailableError as exc:
        logger.info("gemini_image_fallback_triggered", reason=str(exc))
    else:
        parsed = parse_gemini_image_json_result(raw_response, all_db_ingredients)
        if parsed is not None:
            data, ingredients = parsed
            validity = gemini_image_parser.label_field_validity(raw_response)
            ingredients_trustworthy = bool(data.raw_ingredient_text.strip())
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
        # The keyword-heuristic fallback never extracts anything -- it
        # guesses, from a placeholder string, not the actual label.
        validity = gemini_image_parser.LabelFieldValidity()
        ingredients_trustworthy = False

    return data, ingredients, validity, ingredients_trustworthy


async def _finalize_barcode_enrichment(
    db: AsyncSession,
    user_id: uuid.UUID,
    barcode_raw: str,
    barcode_info: "barcode_validation.BarcodeInfo",
    data: AnalyzedProductData,
    ingredients: list[Any],
    validity: "gemini_image_parser.LabelFieldValidity",
    ingredients_trustworthy: bool,
    *,
    scan_type: ScanType,
    provenance_provider: str,
) -> dict:
    """
    Shared tail end of `analyze_label_image_with_barcode` and
    `analyze_ocr_text_with_barcode`: applies the language policy,
    resolves/merges/persists the canonical product, records provenance,
    and returns the same `FullProductAnalysisOut`-shaped dict every
    other `analyze_*` function returns (API Contract compatible -- no
    second response shape).

    Transaction boundaries (review finding 3): exactly ONE `db.commit()`
    call on every path through this function, not several --
      - no ingredients recognized (V13: the actual failure case, see
        this function's own success-gate comment below -- no trustworthy
        ingredient evidence at all, ever, for this barcode): ONE commit
        persists the product change and its provenance together, then
        `ProductNotFoundError` is raised (`labelScanRequired`) -- never a
        product row committed without its provenance, and never a
        partial write left behind by a later failure, because nothing
        before that commit is persisted either.
      - successful (ingredients recognized) enrichment: product change +
        provenance + (when nutrition is ALSO verified) Health Score +
        scan history are all flushed in-memory first and committed
        together in the ONE final `db.commit()` below -- a failure at
        ANY point before it (a provenance write error, a scan-history
        write error, anything) rolls back the ENTIRE attempt, including
        the product merge/insert, via `get_db`'s `except Exception:
        await session.rollback()` (see `app/database/session.py`). See
        `tests/integration/test_label_barcode_enrichment.py`'s
        `test_*_failure_rolls_back_*` tests for direct proof.
    """
    canonical_barcode = barcode_info.gtin13
    alias_keys = barcode_validation.alias_keys(barcode_info)
    existing = await product_repository.get_by_barcode_or_aliases(
        db, barcode_raw, alias_keys, for_update=True
    )

    # Empty raw ingredient text means this is a nutrition-panel-only photo:
    # there is nothing to translate or replace. Real ingredient text always
    # receives the strict EN/BG/translation policy, including when it refreshes
    # a previously verified provider list.
    has_label_ingredients = bool(data.raw_ingredient_text.strip())
    label_result = await resolve_label_text(data.raw_ingredient_text, strict=has_label_ingredients)

    # Review finding 6: canonical ingredient normalization runs for ANY
    # real language content -- English, Bulgarian, or mixed EN/BG, and
    # of course a translated result -- not only when `canonical_text`
    # happens to differ textually from the original. The previous
    # `if canonical_text != raw_ingredient_text` gate silently skipped
    # this for pure-Bulgarian content (a single Bulgarian segment's
    # `canonical_text` is normally byte-identical to the original), so
    # its tokens never got Bulgarian-alias-substituted (see
    # `label_language.bulgarian_ingredient_alias`) and could never
    # dedupe against an equivalent English mention. Only a genuinely
    # empty/"unknown" result (nothing tokenizable at all) is skipped.
    if has_label_ingredients and label_result.detected_language != "unknown" and label_result.canonical_text.strip():
        tokens = normalize_and_extract_tokens(label_result.canonical_text)
        # Bulgarian ingredient-list tokens are aliased to their English
        # canonical form for MATCHING purposes only -- an English and a
        # Bulgarian mention of the same ingredient then resolve to the
        # same matched/synthetic ingredient instead of two. The text
        # actually stored (`data.raw_ingredient_text`, set below) is
        # untouched by this substitution -- English AND Bulgarian
        # content are both preserved verbatim in what's displayed.
        matchable_tokens = [label_language.bulgarian_ingredient_alias(t) or t for t in tokens]
        norm = match_against_database(matchable_tokens, await ingredient_repository.get_all(db))
        rebuilt: list[Any] = list(norm.matched_ingredients)
        for unknown in norm.unknown_ingredients:
            rebuilt.append(create_synthetic_ingredient(unknown))
        if rebuilt:
            # Persistent ingredient knowledge cache -- see
            # `ingredient_catalog`'s module docstring.
            ingredients = await ingredient_catalog.materialize_ingredients(db, rebuilt)
    data.raw_ingredient_text = label_result.canonical_text

    source_label = "label_scan_translated" if label_result.translation_used else "label_scan"
    product, used_label_analysis = await _persist_enriched_product(
        db, existing, canonical_barcode, data, ingredients, validity, ingredients_trustworthy, source_label
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

    # SUCCESS GATE (V13, see module docstring "Ingredient-recognition
    # success vs. health-score readiness" and README section 11.14):
    # label-driven product enrichment succeeds once ingredient
    # RECOGNITION succeeds -- not once the whole product is fully
    # verified. `has_verified_ingredients` is cumulative for this
    # barcode (see `_apply_label_enrichment`), so this is True whenever
    # THIS attempt or any EARLIER one for the same barcode ever produced
    # trustworthy ingredient evidence. Missing/incomplete NUTRITION
    # alone never fails this endpoint any more -- only a genuine
    # extraction failure (no trustworthy ingredients at all, ever, for
    # this barcode) still returns the structured `labelScanRequired`
    # response, exactly as before.
    if not product.has_verified_ingredients:
        # Atomically persist ONLY the permitted incomplete identity +
        # provenance (one commit, nothing else -- no Health Score, no
        # scan history for a row with no recognized ingredients) before
        # surfacing the same structured `labelScanRequired` response
        # `/scan/barcode` already uses for this situation.
        #
        # `has_verified_ingredients` is False here by construction, so
        # there is never genuinely trustworthy partial-ingredient
        # evidence to hand back (contrast `analyze_barcode`, which can
        # legitimately reach this same helper function with ingredients
        # verified but nutrition missing -- that path's own partial-
        # ingredients handling is unaffected by this change).
        await db.commit()
        raise ProductNotFoundError(
            f"Product {product.barcode} could not be read reliably -- no ingredients were recognized.",
            details=_label_scan_required_details(product, None),
        )

    ingredients_out = await fetch_ingredients_for_product(db, product)

    # Health Score stays a SEPARATE axis (V13): computed only when
    # nutrition is ALSO reliably known -- ingredient-recognition success
    # above must never depend on it. A successful ingredients-only
    # enrichment still returns `200`, just with `healthScore: null` and
    # no scan-history entry (there is no real score to log).
    health_score_value: int | None = None
    warnings: list[HealthWarning] = []
    if product.has_verified_nutrition:
        profile = await _get_profile_namespace(db, user_id)
        health_score_value, warnings = _score_and_warnings(product, ingredients_out, profile)
        product.health_score = health_score_value

        await scan_history_repository.insert(
            db,
            ScanHistory(
                user_id=user_id,
                barcode=product.barcode,
                product_name=product.product_name,
                brand=product.brand,
                health_score=health_score_value,
                scan_type=scan_type,
            ),
        )
    # The single outer-transaction commit: product changes, provenance,
    # and (when computed) Health Score + scan history all land together,
    # or (on any exception above) none of them do.
    await db.commit()

    return {
        "product": product,
        "ingredients": ingredients_out,
        "health_score": health_score_value,
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
    data, ingredients, validity, ingredients_trustworthy = await _run_label_image_pipeline(
        image_bytes, all_db_ingredients
    )
    # Persistent ingredient knowledge cache -- see `ingredient_catalog`'s
    # module docstring. The Bulgarian-alias-aware rebuild further down
    # (see `_finalize_barcode_enrichment`/`_finalize_standalone_label_analysis`)
    # re-materializes again when it replaces this list -- cheap (a local
    # get-or-create against an already-existing row after the first
    # time) and keeps every code path honestly covered rather than
    # relying on the rebuild always running.
    ingredients = await ingredient_catalog.materialize_ingredients(db, ingredients)

    return await _finalize_barcode_enrichment(
        db,
        user_id,
        barcode_raw,
        barcode_info,
        data,
        ingredients,
        validity,
        ingredients_trustworthy,
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

    Nutrition here is always treated as NOT genuinely known (an
    all-`False` `LabelFieldValidity()`): `analyze_ocr_text`'s Gemini
    path preserves a documented, tested quirk (see
    `gemini_result_parser.py`) where nutrition figures always come from
    the deterministic keyword-heuristic fallback recomputed against the
    raw text, never from Gemini's own structured numbers -- i.e. they
    are never genuinely-extracted real label data for this endpoint, by
    long-standing, intentional design. A barcode resubmitted through
    this endpoint therefore only reaches `has_verified_nutrition=True`
    if the EXISTING row already had it (or a later `/scan/label-image`
    call for the same barcode provides it).

    Ingredients are different: `raw_text` here is always the caller's
    OWN genuine, literal OCR/label text (the router already rejects
    anything under 3 characters) -- unlike the label-image fallback's
    placeholder error string, tokenizing it (via Gemini structured
    output OR the deterministic fallback -- both operate on this SAME
    real text) is genuinely trustworthy evidence, so
    `ingredients_trustworthy=True` unconditionally here.
    """
    barcode_info = validate_and_normalize(barcode_raw)
    if barcode_info is None:
        raise ValidationAppError(f"'{barcode_raw}' is not a valid barcode.")

    all_db_ingredients = await ingredient_repository.get_all(db)
    data, ingredients = await _run_ai_or_fallback("Scanned Product", raw_text, all_db_ingredients)
    ingredients = await ingredient_catalog.materialize_ingredients(db, ingredients)
    validity = gemini_image_parser.LabelFieldValidity()
    ingredients_trustworthy = True

    return await _finalize_barcode_enrichment(
        db,
        user_id,
        barcode_raw,
        barcode_info,
        data,
        ingredients,
        validity,
        ingredients_trustworthy,
        scan_type=ScanType.OCR_LABEL,
        provenance_provider="ocr_text",
    )


# ---------------------------------------------------------------------------
# Standalone label analysis (V11, PR #9 review round 4, finding 2:
# "apply the label language/safety policy to standalone Ingredient
# Label scans"). `analyze_label_image`/`analyze_ocr_text` (no barcode)
# now share this exact tail end with the barcode-linked path (finding
# 3: "avoid duplicated pipelines") -- same language resolution
# (`resolve_label_text`), same canonical ingredient normalization/
# dedup, same `LabelFieldValidity`-gated nutrition/NOVA safety, same
# real-JSON-null/no-fabricated-allergen handling (all already enforced
# upstream, in `gemini_image_parser.py`/`_run_ai_or_fallback`, which
# both paths already call) -- and the SAME real, honest
# `has_verified_nutrition`/`has_verified_ingredients` flags, computed
# the identical way `_new_product_from_label` computes them for the
# barcode-linked path. No raw image bytes are ever stored (only
# `PIL.Image.verify()` reads them, in memory); the original OCR/label
# text is preserved via `_record_label_provenance`, the same existing
# `product_sources` table the barcode-linked path already uses -- no
# new table, no schema change for this either.
#
# API response change Android must handle (see README section 11.9 and
# 11.11): `POST /scan/label-image` can return the EXISTING structured
# `404 PRODUCT_NOT_FOUND` / `labelScanRequired` envelope (byte-for-byte
# the same shape `/scan/barcode` already returns for this situation --
# no new REQUIRED field, no OpenAPI schema change) instead of a `200`
# carrying a confident-looking Health Score computed from placeholder-
# zero nutrition. This can only happen when the label genuinely
# couldn't be read reliably (Gemini unavailable/invalid response, or
# explicit `null` nutrition fields) -- a normal, successful scan is
# completely unaffected and returns exactly what it always has.
#
# CONTRACT CHANGE (V12, PR #9 review round 5, finding 1): `POST
# /scan/ocr-text` (no barcode) is now gated the SAME way -- the
# `always_verified` escape hatch that used to force every OCR-text
# submission to `200` regardless of nutrition trustworthiness has been
# REMOVED (see `analyze_ocr_text`'s docstring for the full rationale).
# Because this endpoint's nutrition is ALWAYS a heuristic guess, never
# genuinely extracted, a standalone OCR-text submission now
# CONSISTENTLY returns this `404`/`labelScanRequired` response (with
# its verified ingredient evidence attached as useful partial data --
# see `_label_scan_required_details`) rather than occasionally, the way
# `/scan/label-image` does. This is a real, intentional behavior change
# for `/scan/ocr-text` specifically -- an Android client that assumed
# this endpoint always returns `200` MUST be updated to handle this
# response the same way it already has to handle `/scan/label-image`'s.
#
# V13 AMENDMENT (supersedes the V11/V12 paragraphs above -- see the
# module docstring's "V13" entry and README section 11.14): the gate
# below is now `ingredients_complete` alone, not `is_complete`
# (BOTH groups). A `200` is returned whenever ingredient recognition
# succeeded -- for `/scan/label-image` that is every case where Gemini
# or the fallback produced real ingredient text (which, per
# `_run_label_image_pipeline`, is every case EXCEPT a full Gemini+
# fallback failure); for `/scan/ocr-text` that is now EVERY call, since
# `analyze_ocr_text` always sets `ingredients_trustworthy=True` for the
# caller's own genuine literal text. `healthScore` in that `200` is
# `null` whenever nutrition isn't ALSO verified (always true for
# standalone `/scan/ocr-text`, per its docstring) -- see each function's
# own "Health Score stays a SEPARATE axis" comment below. The
# `404`/`labelScanRequired` response itself (shape, `_label_scan_required_details`)
# is unchanged; it is simply reached far less often now.
# ---------------------------------------------------------------------------


async def _finalize_standalone_label_analysis(
    db: AsyncSession,
    user_id: uuid.UUID,
    data: AnalyzedProductData,
    ingredients: list[Any],
    validity: "gemini_image_parser.LabelFieldValidity",
    ingredients_trustworthy: bool,
    *,
    barcode_prefix: str,
    scan_type: ScanType,
    provenance_provider: str,
) -> dict:
    """
    Shared tail end of `analyze_label_image`/`analyze_ocr_text` (no
    barcode) -- see the section docstring above. Always creates a fresh
    row under a new synthetic `{barcode_prefix}_<timestamp>` id (there
    is no existing-row alias lookup here, unlike the barcode-linked
    `_finalize_barcode_enrichment`: a synthetic id is never resubmitted
    by a client, so there is nothing to merge into).

    `resolve_label_text(..., strict=False)`: a translation failure here
    degrades gracefully (falls back to the original text) instead of
    failing the whole request -- see that parameter's docstring for why
    this matters specifically for a standalone, ad-hoc scan.

    V12 (review round 5, finding 1): completeness is now UNCONDITIONALLY
    the real, honest `_nutrition_group_is_complete`/
    `_ingredients_group_is_complete` result for BOTH callers -- the
    `always_verified` escape hatch that used to force `analyze_ocr_text`
    straight to `is_verified=True` regardless of actual nutrition
    trustworthiness has been removed entirely. Heuristic/fallback
    nutrition must never set `has_verified_nutrition`; only genuinely
    trustworthy evidence does, exactly like the barcode-linked path.
    """
    label_result = await resolve_label_text(data.raw_ingredient_text, strict=False)

    # Same canonical (Bulgarian-alias-aware) ingredient rebuild the
    # barcode-linked path uses -- see `_finalize_barcode_enrichment`'s
    # identical block for the full rationale (review round 3 finding 6).
    if label_result.detected_language != "unknown" and label_result.canonical_text.strip():
        tokens = normalize_and_extract_tokens(label_result.canonical_text)
        matchable_tokens = [label_language.bulgarian_ingredient_alias(t) or t for t in tokens]
        norm = match_against_database(matchable_tokens, await ingredient_repository.get_all(db))
        rebuilt: list[Any] = list(norm.matched_ingredients)
        for unknown in norm.unknown_ingredients:
            rebuilt.append(create_synthetic_ingredient(unknown))
        if rebuilt:
            # Persistent ingredient knowledge cache -- see
            # `ingredient_catalog`'s module docstring.
            ingredients = await ingredient_catalog.materialize_ingredients(db, rebuilt)
    data.raw_ingredient_text = label_result.canonical_text

    nutrition_complete = _nutrition_group_is_complete(validity)
    ingredients_complete = _ingredients_group_is_complete(data, ingredients_trustworthy)
    # `is_complete` still tracks full verification / Health-Score
    # readiness for `Product.is_verified` -- kept exactly as before.
    # V13: it is deliberately NOT the success/failure gate below any
    # more -- see that gate's own comment.
    is_complete = nutrition_complete and ingredients_complete

    barcode = f"{barcode_prefix}_{int(time.time() * 1000)}"
    source_label = "label_scan_translated" if label_result.translation_used else "label_scan"
    product = _to_product_model(
        barcode,
        data,
        ingredients,
        source=source_label,
        source_confidence=None,
        is_verified=is_complete,
        has_verified_nutrition=nutrition_complete,
        has_verified_ingredients=ingredients_complete,
    )
    if not is_complete:
        product.last_verified_at = None
    product = await product_repository.upsert(db, product)

    await _record_label_provenance(
        db,
        product.barcode,
        provenance_provider,
        data,
        label_result,
        used_for_persisted_product=True,
    )

    # SUCCESS GATE (V13, see module docstring "Ingredient-recognition
    # success vs. health-score readiness" and README section 11.14):
    # ingredient RECOGNITION, not full verification, is the primary
    # success condition for a label-driven scan. Only a genuine
    # extraction failure -- no trustworthy ingredient evidence at all
    # (Gemini AND the deterministic fallback both produced nothing
    # usable for `/scan/label-image`; an unreadable/too-short submission
    # for `/scan/ocr-text`) -- still returns the structured
    # `labelScanRequired` response; missing/incomplete nutrition alone
    # never does any more.
    if not ingredients_complete:
        # `ingredients_complete` is False here by construction, so there
        # is no genuinely trustworthy partial evidence to hand back.
        await db.commit()
        raise ProductNotFoundError(
            f"Product {product.barcode} could not be read reliably -- no ingredients were recognized.",
            details=_label_scan_required_details(product, None),
        )

    # Health Score stays a SEPARATE axis (V13): computed only when
    # nutrition is ALSO reliably known -- ingredient-recognition success
    # above must never depend on it. An ingredients-only scan still
    # returns `200`, just with `healthScore: null` and no scan-history
    # entry (there is no real score to log). `/scan/ocr-text` in
    # particular can now NEVER complete the nutrition group on its own
    # (its nutrition is always a heuristic guess -- see
    # `analyze_ocr_text`'s docstring), so a standalone OCR-text call
    # always takes this branch.
    health_score_value: int | None = None
    warnings: list[HealthWarning] = []
    if nutrition_complete:
        profile = await _get_profile_namespace(db, user_id)
        health_score_value, warnings = _score_and_warnings(product, ingredients, profile)
        product.health_score = health_score_value

        await scan_history_repository.insert(
            db,
            ScanHistory(
                user_id=user_id,
                barcode=product.barcode,
                product_name=product.product_name,
                brand=product.brand,
                health_score=health_score_value,
                scan_type=scan_type,
            ),
        )
    await db.commit()

    return {
        "product": product,
        "ingredients": ingredients,
        "health_score": health_score_value,
        "warnings": warnings,
        "is_from_database_cache": False,
    }


async def analyze_label_image(db: AsyncSession, user_id: uuid.UUID, image_bytes: bytes) -> dict:
    """
    API Contract 6.3 (no barcode). Fallback chain (V3 fix: use a
    successful Gemini call's full structured output, never discard it):
      1. Gemini request.
      2. Gemini returns valid, usable JSON -> USE IT (all fields).
      3. Gemini network error/timeout/missing key/invalid or unusable
         JSON -> deterministic `fallback_local_analysis`.
      4. Fallback itself fails -> AIServiceUnavailableError.

    V11 (PR #9 review round 4, finding 2): now applies the full label
    language/safety policy via the shared `_finalize_standalone_label_analysis`
    -- see that function's and the section docstring above for the
    resulting (additive-only, no OpenAPI change) response-shape note.
    """
    from PIL import Image
    from io import BytesIO

    try:
        Image.open(BytesIO(image_bytes)).verify()
    except Exception as exc:  # noqa: BLE001
        raise ImageUnreadableError("The uploaded file is not a readable image.") from exc

    all_db_ingredients = await ingredient_repository.get_all(db)
    data, ingredients, validity, ingredients_trustworthy = await _run_label_image_pipeline(
        image_bytes, all_db_ingredients
    )
    # Persistent ingredient knowledge cache -- see `ingredient_catalog`'s
    # module docstring. The Bulgarian-alias-aware rebuild further down
    # (see `_finalize_barcode_enrichment`/`_finalize_standalone_label_analysis`)
    # re-materializes again when it replaces this list -- cheap (a local
    # get-or-create against an already-existing row after the first
    # time) and keeps every code path honestly covered rather than
    # relying on the rebuild always running.
    ingredients = await ingredient_catalog.materialize_ingredients(db, ingredients)

    return await _finalize_standalone_label_analysis(
        db,
        user_id,
        data,
        ingredients,
        validity,
        ingredients_trustworthy,
        barcode_prefix="img",
        scan_type=ScanType.OCR_LABEL,
        provenance_provider="label_ocr",
    )
