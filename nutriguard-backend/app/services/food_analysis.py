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
"""
import time
import uuid
from types import SimpleNamespace
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AIServiceUnavailableError, ImageUnreadableError, ProductNotFoundError
from app.integrations.gemini import GeminiUnavailableError, gemini_service
from app.models.enums import ScanType
from app.models.product import Product
from app.models.scan_history import ScanHistory
from app.repositories import (
    health_profile_repository,
    ingredient_repository,
    product_repository,
    scan_history_repository,
)
from app.services import health_score, warning_engine
from app.services.fallback_analysis import AnalyzedProductData, fallback_local_analysis
from app.services.gemini_image_parser import parse_gemini_image_json_result
from app.services.gemini_result_parser import parse_gemini_json_result
from app.services.ocr_normalizer import create_synthetic_ingredient

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


def _to_product_model(barcode: str, data: AnalyzedProductData, ingredients: list[Any]) -> Product:
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
    )


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
    API Contract 6.1 (revised): a barcode is an identifier, looked up
    against the trusted product source only. Unknown barcodes MUST NOT
    fabricate a product, ingredients, nutrition values, or a health
    score — they return PRODUCT_NOT_FOUND. Use `analyze_ocr_text` or
    `analyze_label_image` to analyze actual label content (those
    endpoints are unaffected by this change).
    """
    product = await product_repository.get_by_barcode(db, barcode)
    if product is None:
        raise ProductNotFoundError(f"No product found for barcode {barcode}.")

    ingredients = await fetch_ingredients_for_product(db, product)

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
            scan_type=ScanType.BARCODE,
        ),
    )
    await db.commit()

    return {
        "product": product,
        "ingredients": ingredients,
        "health_score": score,
        "warnings": warnings,
        "is_from_database_cache": True,  # always true here: a match was found via a real lookup
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
