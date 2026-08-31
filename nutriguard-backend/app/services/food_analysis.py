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
"""
import time
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AIServiceUnavailableError, ImageUnreadableError, ProductNotFoundError
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
from app.services import barcode_discovery, health_score, warning_engine
from app.services.barcode_validation import validate_and_normalize
from app.services.fallback_analysis import AnalyzedProductData, fallback_local_analysis
from app.services.gemini_image_parser import parse_gemini_image_json_result
from app.services.gemini_result_parser import parse_gemini_json_result
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
        discovered_at=now if source != "label_scan" else None,
        last_verified_at=now,
    )


def _apply_discovered_fields(
    existing: Product, data: AnalyzedProductData, ingredients: list[Any], *, source: str, confidence: float
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
    existing.last_verified_at = datetime.now(timezone.utc)
    existing.timestamp = int(time.time() * 1000)


def _to_analyzed_data_from_discovery(
    discovered: "barcode_discovery.DiscoveredProduct", db_ingredients: list[Any]
) -> tuple[AnalyzedProductData, list[Any]]:
    """
    Bridges a validated `DiscoveredProduct` (external, provider-sourced)
    into the same `AnalyzedProductData` shape the OCR/Gemini pipelines
    use, so it goes through the identical ingredient-matching, Health
    Score, and Warning Engine code paths — see module docstring, V5.

    Dietary flags/allergens: OFF's own structured tags (`dietary_flags`/
    `allergens` on `DiscoveredProduct`) are used where present; anything
    OFF didn't state is left at the Product model's neutral "unknown"
    default (True / "None") rather than guessed from a keyword scan —
    deliberately more conservative than the OCR fallback's keyword
    heuristic, since a barcode-only discovery has no label text of its
    own to scan in the first place for whatever OFF left unstated.
    """
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
        # Same heuristic `fallback_local_analysis` uses for OCR text
        # with no explicit NOVA signal — see that module's docstring.
        nova_group = 4 if (len(ingredient_list) > 5 or n.has_artificial_sweeteners or n.has_preservatives) else 3

    flags = discovered.dietary_flags
    allergens_text = ", ".join(discovered.allergens) if discovered.allergens else "None"

    data = AnalyzedProductData(
        barcode=discovered.barcode.raw,
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
        is_gluten_free=flags.get("is_gluten_free", True),
        is_lactose_free=flags.get("is_lactose_free", True),
        is_vegan=flags.get("is_vegan", True),
        is_vegetarian=flags.get("is_vegetarian", True),
        is_halal=flags.get("is_halal", True),
        is_kosher=flags.get("is_kosher", True),
        allergens_detected=allergens_text,
    )
    return data, ingredient_list


def _not_found_details(barcode: str, attempts: list["barcode_discovery.ProviderAttempt"]) -> dict:
    return {
        "labelScanRequired": True,
        "reason": "Barcode not found in the local database or any configured external source.",
        "providersChecked": [{"provider": a.provider, "outcome": a.outcome} for a in attempts],
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
    see the module docstring's V5 entry and README "Deviations".
    """
    all_db_ingredients = await ingredient_repository.get_all(db)
    data, ingredients = _to_analyzed_data_from_discovery(discovered, all_db_ingredients)

    candidate = _to_product_model(
        discovered.barcode.raw,
        data,
        ingredients,
        source=discovered.primary_provider,
        source_confidence=discovered.confidence,
        is_verified=False,
    )

    inserted = await product_repository.insert_new(db, candidate)
    used_for_persisted_product = True
    if inserted is not None:
        product = inserted
    else:
        # Lost a race with a concurrent discovery of the same barcode.
        existing = await product_repository.get_by_barcode(db, discovered.barcode.raw)
        if existing is None:
            raise AIServiceUnavailableError("Could not persist the discovered product.")
        if existing.is_verified or (existing.source_confidence or 0) >= discovered.confidence:
            product = existing
            used_for_persisted_product = False
        else:
            _apply_discovered_fields(
                existing, data, ingredients, source=discovered.primary_provider, confidence=discovered.confidence
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
            barcode=discovered.barcode.raw,
            result=result,
            confidence=confidence,
            is_conflicting=discovered.is_conflicting,
            used_for_persisted_product=is_primary and used_for_persisted_product,
        )
    await db.commit()

    extra_warnings: list[HealthWarning] = []
    if not (discovered.nutrition_known and discovered.ingredients_known):
        extra_warnings.append(
            HealthWarning(
                title="Incomplete Product Data",
                description=(
                    "This product's nutrition and/or ingredient details could not be fully "
                    "verified from external sources. Scan the ingredient label for a fully "
                    "accurate Health Score."
                ),
                condition="Data Completeness",
                trigger_factor=discovered.primary_provider,
                severity=WarningSeverity.INFO,
            )
        )
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
    product = await product_repository.get_by_barcode(db, barcode)
    was_cache_hit = product is not None
    extra_warnings: list[HealthWarning] = []

    if product is None:
        barcode_info = validate_and_normalize(barcode)
        if barcode_info is not None:
            outcome = await barcode_discovery.discover_product(barcode_info)
            if outcome.product is not None:
                product, extra_warnings = await _persist_discovered_product(db, outcome.product)
            else:
                raise ProductNotFoundError(
                    f"No product found for barcode {barcode}.",
                    details=_not_found_details(barcode, outcome.attempts),
                )
        else:
            raise ProductNotFoundError(
                f"No product found for barcode {barcode}.",
                details=_not_found_details(barcode, []),
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
