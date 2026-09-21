"""
Deterministic port of `GeminiAnalysisEngine.fallbackLocalAnalysis`.

This is the keyword-heuristic analysis used whenever the Gemini call is
unavailable, misconfigured, or fails to parse (API Contract 7.4). It is
also, due to a preserved quirk in the original app (see
`gemini_result_parser.py`), effectively what determines nutrition figures
even on a *successful* Gemini call for text analysis.
"""
import time
from dataclasses import dataclass, field
from typing import Any

from app.services import dietary_suitability
from app.services.ocr_normalizer import (
    create_synthetic_ingredient,
    match_against_database,
    normalize_and_extract_tokens,
)


@dataclass
class AnalyzedProductData:
    """Transient, not-yet-persisted mirror of ProductEntity's mutable fields."""

    barcode: str
    product_name: str
    brand: str
    category: str
    image_url: str | None
    raw_ingredient_text: str
    nova_group: int
    sugar_grams: float
    sodium_mg: float
    saturated_fat_grams: float
    has_artificial_sweeteners: bool
    has_preservatives: bool
    # Tri-state (see `app.services.dietary_suitability`): `None` =
    # unknown/insufficient evidence, `False` = supported incompatibility,
    # `True` = supported suitability (only ever from an explicit source
    # value, never from keyword absence).
    is_gluten_free: bool | None
    is_lactose_free: bool | None
    is_vegan: bool | None
    is_vegetarian: bool | None
    is_halal: bool | None
    is_kosher: bool | None
    # Comma-separated allergens positively found; "" = unknown (never
    # the literal "None" -- see `dietary_suitability.detect_allergens_text`).
    allergens_detected: str
    nutrition_basis: str = "UNKNOWN"
    serving_size: float | None = None
    serving_unit: str | None = None
    # See `app.models.product.Product.original_ingredient_text`/
    # `ingredient_text_source_language` -- set only by the label/OCR
    # finalize paths that actually run the language policy
    # (`app.services.food_analysis`'s two `_finalize_*` functions); ""/
    # `None` (the honest "no label/OCR text was ever extracted, or the
    # language policy hasn't run yet") for every other path.
    original_ingredient_text: str = ""
    ingredient_text_source_language: str | None = None


def fallback_local_analysis(
    title: str, raw_text: str, db_ingredients: list[Any]
) -> tuple[AnalyzedProductData, list[Any]]:
    tokens = normalize_and_extract_tokens(raw_text)
    norm = match_against_database(tokens, db_ingredients)

    ingredient_list: list[Any] = list(norm.matched_ingredients)
    for unk in norm.unknown_ingredients:
        ingredient_list.append(create_synthetic_ingredient(unk))

    lower = raw_text.lower()
    has_sweeteners = any(kw in lower for kw in ("aspartam", "sucralose", "stevia"))
    has_preservatives = any(kw in lower for kw in ("benzoate", "nitrit", "sorbate"))
    nova = 4 if (len(ingredient_list) > 5 or has_sweeteners or has_preservatives) else 3

    # Tri-state product dietary flags: `False` only from positive
    # incompatibility evidence (a TRUSTED catalog ingredient's own flag, or
    # an ingredient entry that IS an unambiguous identity such as "pork" /
    # "skimmed milk powder" -- never a substring like "coconut milk");
    # NEVER `True` from the absence of a keyword -- a Bulgarian/mixed/empty
    # label must not read as gluten-free/vegan/halal. See
    # `app.services.dietary_suitability`.
    flags = dietary_suitability.resolve_flags(None, raw_text, ingredient_list)

    product = AnalyzedProductData(
        barcode=f"ocr_{int(time.time() * 1000)}",
        product_name=title,
        brand="Scanned Label Product",
        category="Analyzed Food",
        image_url=None,
        raw_ingredient_text=raw_text,
        nova_group=nova,
        sugar_grams=14.0 if "sugar" in lower else 2.0,
        sodium_mg=450.0 if ("salt" in lower or "sodium" in lower) else 80.0,
        saturated_fat_grams=3.5 if ("oil" in lower or "fat" in lower) else 0.5,
        has_artificial_sweeteners=has_sweeteners,
        has_preservatives=has_preservatives,
        allergens_detected=dietary_suitability.detect_allergens_text(raw_text),
        **flags,
    )

    return product, ingredient_list
