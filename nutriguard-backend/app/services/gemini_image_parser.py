"""
Parser for Gemini's `analyze_image` response (API Contract 6.3).

Unlike `gemini_result_parser.py` (used by `analyze_ocr_text`, which
preserves a documented quirk from the original Kotlin client that
discards most of Gemini's structured output), THIS parser is the
correct, full implementation required by API Contract 6.3: when Gemini
returns valid, usable JSON, every field it provides — nutrition figures,
dietary flags, NOVA group, product name/brand, and the ingredients list
— is actually used, not silently thrown away.

Ingredient normalization still goes through the SAME deterministic
architecture used everywhere else in the app
(`app.services.ocr_normalizer`): each Gemini-reported ingredient is
matched against the scientific database by E-number/name; unmatched
ingredients get a synthetic record via `create_synthetic_ingredient`.
Gemini's own per-ingredient scientific claims (health concerns, risk
level, bad-for-* flags, etc.) are intentionally NOT trusted or persisted
— per the API Contract's safety rule, Gemini is an extraction/analysis
aid, not an authoritative scientific source. Only the seeded scientific
database or the deterministic keyword-heuristic fallback are
authoritative for ingredient-level claims.

Returns `None` (rather than raising) when the JSON is missing, malformed,
or missing the minimum usable fields (`productName` + `rawIngredientText`)
— callers should treat `None` exactly like a Gemini failure and fall
back to `fallback_local_analysis`.
"""
import json
import math
from typing import Any

from app.services.fallback_analysis import AnalyzedProductData
from app.services.ocr_normalizer import (
    create_synthetic_ingredient,
    match_against_database,
    normalize_and_extract_tokens,
)


def _as_float(payload: dict, key: str, default: float) -> float:
    try:
        value = payload.get(key, default)
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _as_bool(payload: dict, key: str, default: bool) -> bool:
    value = payload.get(key, default)
    return value if isinstance(value, bool) else default


def _as_int(payload: dict, key: str, default: int) -> int:
    try:
        value = payload.get(key, default)
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _resolve_ingredients(
    gemini_ingredients: list, raw_text: str, db_ingredients: list[Any]
) -> list[Any]:
    """
    Normalizes ingredients using the existing deterministic architecture
    (API Contract 7.3). Prefers Gemini's structured `ingredients[]` (more
    precise: explicit commonName/eNumber per item); falls back to
    tokenizing `rawIngredientText` if Gemini omitted the array or its
    objects cannot yield any usable ingredients.
    """
    resolved: list[Any] = []
    seen_ids: set[str] = set()

    def _add(candidate: Any) -> None:
        if candidate.id not in seen_ids:
            resolved.append(candidate)
            seen_ids.add(candidate.id)

    valid_items = [item for item in gemini_ingredients if isinstance(item, dict)]

    if valid_items:
        for item in valid_items:
            common_name = str(item.get("commonName") or "").strip()
            e_number = str(item.get("eNumber") or "").strip() or None
            if not common_name and not e_number:
                continue
            token = f"{common_name} ({e_number})" if e_number else common_name
            match = match_against_database([token], db_ingredients)
            if match.matched_ingredients:
                _add(match.matched_ingredients[0])
            else:
                _add(create_synthetic_ingredient(common_name or e_number))
        # A syntactically valid Gemini JSON response is still allowed to
        # contain ingredient objects in an unexpected shape.  Do not let
        # those unusable objects suppress the contract-required
        # rawIngredientText normalization path.
        if resolved:
            return resolved

    # No usable structured ingredients array (or no ingredient could be
    # resolved from the supplied objects) -> tokenize the raw text, using
    # the same mechanism as the text/OCR pipeline.
    tokens = normalize_and_extract_tokens(raw_text)
    match = match_against_database(tokens, db_ingredients)
    for ing in match.matched_ingredients:
        _add(ing)
    for unknown in match.unknown_ingredients:
        _add(create_synthetic_ingredient(unknown))
    return resolved


# Physically/nutritionally defensible upper bounds for a per-100g
# figure (see `NutritionFacts`'/`Product`'s "per-100g" convention).
# Generous on purpose -- these exist only to catch an obviously
# hallucinated/garbled number (e.g. a misplaced decimal or a
# non-nutrition figure echoed into the wrong field), not to second-guess
# a plausible real product. Sugar/saturated fat physically cannot
# exceed the product's own 100g mass; sodium is bounded by the mass of
# pure salt-equivalent compound in 100g (~39% sodium by weight for
# NaCl, so 100,000mg is already far beyond any real food label -- kept
# as a round, generous ceiling rather than a precise salt-chemistry
# figure).
_MAX_SUGAR_GRAMS_PER_100G = 100.0
_MAX_SATURATED_FAT_GRAMS_PER_100G = 100.0
_MAX_SODIUM_MG_PER_100G = 100_000.0

_NUTRITION_FIELD_BOUNDS: dict[str, float] = {
    "sugarGrams": _MAX_SUGAR_GRAMS_PER_100G,
    "sodiumMg": _MAX_SODIUM_MG_PER_100G,
    "saturatedFatGrams": _MAX_SATURATED_FAT_GRAMS_PER_100G,
}


def _is_trustworthy_nutrition_number(value: Any, *, max_value: float) -> bool:
    """
    True only for a genuine, finite, non-negative, in-range JSON
    number -- deliberately stricter than `_as_float` (which is used
    only to fill `AnalyzedProductData` for DISPLAY, including on the
    intentionally-untrusted keyword-heuristic fallback path; it must
    stay lenient there so a malformed value degrades to a placeholder
    number rather than crashing). This function alone gates
    `has_verified_nutrition` and must never be fooled by:

      - a JSON boolean (`bool` is an `int` subclass in Python, so
        `isinstance(True, int)` is True and `float(True) == 1.0` --
        checked and rejected explicitly, before the numeric-type check);
      - `NaN`/`Infinity`/`-Infinity` (Python's `json` module accepts
        these non-standard tokens by default and hands back a real
        `float('nan')`/`float('inf')` -- caught by `math.isnan`/
        `math.isinf`, not merely "did `float()` not raise");
      - a negative value (nutrition per 100g cannot be negative);
      - a value outside a physically defensible per-100g range (see
        `_NUTRITION_FIELD_BOUNDS`);
      - a numeric STRING (e.g. `"12.5"`) or any other non-`int`/`float`
        JSON type -- a well-behaved structured response (per the
        updated `_IMAGE_PROMPT`) emits a real JSON number or `null`,
        never a quoted number; a string is treated as untrustworthy,
        not coerced.

    A missing key or an explicit JSON `null` (`value is None`) is
    exactly the signal the updated `_IMAGE_PROMPT` now asks the model
    to use for "unreadable/absent on the label" -- both correctly fail
    this check rather than being silently treated as zero.
    """
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    numeric = float(value)
    if math.isnan(numeric) or math.isinf(numeric):
        return False
    if numeric < 0 or numeric > max_value:
        return False
    return True


def nutrition_fields_present(json_string: str) -> bool:
    """
    True only if the Gemini image JSON explicitly provided all three
    core numeric Health Score inputs (sugarGrams/sodiumMg/
    saturatedFatGrams) as genuinely trustworthy, extracted values --
    see `_is_trustworthy_nutrition_number` for the full definition
    (rejects booleans, NaN/Infinity, negative values, out-of-range
    values, non-numeric JSON types, and missing/null values). A value
    of exactly `0` is accepted when explicitly present and otherwise
    valid -- a genuinely fat-free/sugar-free product is a real,
    trustworthy answer, indistinguishable at the JSON level from any
    other explicit number.

    Used only by the barcode-enrichment path
    (`food_analysis.analyze_label_image_with_barcode`) to decide
    whether the merged product's nutrition is genuinely verified
    (mirrors `barcode_discovery`'s `nutrition_known` rule for external
    providers). The standalone `/scan/label-image` endpoint (no
    barcode) is unaffected -- it keeps its own unconditional
    `has_verified_nutrition=True` default (see
    `food_analysis._to_product_model`), unchanged by this function.
    """
    try:
        payload = json.loads(json_string)
    except (json.JSONDecodeError, TypeError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    return all(
        _is_trustworthy_nutrition_number(payload.get(key), max_value=max_value)
        for key, max_value in _NUTRITION_FIELD_BOUNDS.items()
    )


def parse_gemini_image_json_result(
    json_string: str, db_ingredients: list[Any]
) -> tuple[AnalyzedProductData, list[Any]] | None:
    try:
        payload = json.loads(json_string)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None

    if not isinstance(payload, dict):
        return None

    product_name = payload.get("productName")
    raw_text = payload.get("rawIngredientText")
    if not isinstance(product_name, str) or not product_name.strip():
        return None
    if not isinstance(raw_text, str) or not raw_text.strip():
        return None

    gemini_ingredients = payload.get("ingredients")
    if not isinstance(gemini_ingredients, list):
        gemini_ingredients = []

    ingredients = _resolve_ingredients(gemini_ingredients, raw_text, db_ingredients)

    data = AnalyzedProductData(
        barcode="",  # overridden by the caller (food_analysis.analyze_label_image)
        product_name=product_name.strip(),
        brand=str(payload.get("brand") or "Analyzed Brand").strip() or "Analyzed Brand",
        category="Analyzed Food",
        image_url=None,
        raw_ingredient_text=raw_text.strip(),
        nova_group=_as_int(payload, "novaGroup", 3),
        sugar_grams=_as_float(payload, "sugarGrams", 0.0),
        sodium_mg=_as_float(payload, "sodiumMg", 0.0),
        saturated_fat_grams=_as_float(payload, "saturatedFatGrams", 0.0),
        has_artificial_sweeteners=_as_bool(payload, "hasArtificialSweeteners", False),
        has_preservatives=_as_bool(payload, "hasPreservatives", False),
        # Safe-default rule: an unknown/missing/null/malformed dietary
        # flag defaults to False, NEVER True. A positive certification
        # claim (gluten-free, vegan, halal, ...) is only ever set when
        # Gemini's response explicitly and reliably states it as a real
        # JSON boolean `true` -- `_as_bool` already falls back to the
        # given default for anything that isn't a genuine bool (missing
        # key, null, a string, a number), so passing `False` here is
        # the entire fix: "unknown" must never read as a positive
        # certification (same asymmetry `_to_analyzed_data_from_discovery`
        # already documents for barcode-discovery dietary flags).
        is_gluten_free=_as_bool(payload, "isGlutenFree", False),
        is_lactose_free=_as_bool(payload, "isLactoseFree", False),
        is_vegan=_as_bool(payload, "isVegan", False),
        is_vegetarian=_as_bool(payload, "isVegetarian", False),
        is_halal=_as_bool(payload, "isHalal", False),
        is_kosher=_as_bool(payload, "isKosher", False),
        allergens_detected="None",
    )
    return data, ingredients
