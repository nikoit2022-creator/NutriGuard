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
    is_gluten_free: bool
    is_lactose_free: bool
    is_vegan: bool
    is_vegetarian: bool
    is_halal: bool
    is_kosher: bool
    allergens_detected: str


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
        is_gluten_free=not ("wheat" in lower or "gluten" in lower),
        is_lactose_free=not ("milk" in lower or "whey" in lower or "lactose" in lower),
        is_vegan=not ("pork" in lower or "gelatin" in lower or "milk" in lower),
        is_vegetarian=not ("pork" in lower or "gelatin" in lower or "bacon" in lower),
        is_halal=not ("pork" in lower or "alcohol" in lower),
        is_kosher=not ("pork" in lower),
        allergens_detected="Soy" if "soy" in lower else ("Milk" if "milk" in lower else "None"),
    )

    return product, ingredient_list
