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
from typing import Any

from app.services.fallback_analysis import AnalyzedProductData
from app.services.ocr_normalizer import (
    create_synthetic_ingredient,
    match_against_database,
    normalize_and_extract_tokens,
)
from app.services.text_localization import (
    clean_optional,
    clean_required,
    to_bilingual_display_text,
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
            common_name = clean_optional(str(item.get("commonName") or "")) or ""
            e_number = clean_optional(str(item.get("eNumber") or ""))
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


def parse_gemini_image_json_result(
    json_string: str, db_ingredients: list[Any]
) -> tuple[AnalyzedProductData, list[Any]] | None:
    try:
        payload = json.loads(json_string)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None

    if not isinstance(payload, dict):
        return None

    # A literal "null"/"None" placeholder string is exactly as unusable
    # as a genuinely missing field -- treat both the same way so a
    # misbehaving Gemini response falls back to deterministic analysis
    # instead of persisting placeholder junk as the product name/text.
    product_name = clean_optional(payload.get("productName")) if isinstance(
        payload.get("productName"), str
    ) else None
    raw_text = clean_optional(payload.get("rawIngredientText")) if isinstance(
        payload.get("rawIngredientText"), str
    ) else None
    if not product_name:
        return None
    if not raw_text:
        return None

    # productName is a required, already-present field at this point --
    # still run it through the bilingual guard so an untranslated
    # third-language name never reaches the client (API Contract
    # bilingual-output requirement; brand is handled separately below
    # and is never translated).
    product_name = to_bilingual_display_text(product_name, fallback="Scanned Product")

    gemini_ingredients = payload.get("ingredients")
    if not isinstance(gemini_ingredients, list):
        gemini_ingredients = []

    ingredients = _resolve_ingredients(gemini_ingredients, raw_text, db_ingredients)

    data = AnalyzedProductData(
        barcode="",  # overridden by the caller (food_analysis.analyze_label_image)
        product_name=product_name,
        # Brand names are never translated (API Contract bilingual-output
        # requirement) -- only placeholder junk is scrubbed here.
        brand=clean_required(
            payload.get("brand") if isinstance(payload.get("brand"), str) else None,
            fallback="Analyzed Brand",
        ),
        category="Analyzed Food",
        image_url=None,
        raw_ingredient_text=raw_text,
        nova_group=_as_int(payload, "novaGroup", 3),
        sugar_grams=_as_float(payload, "sugarGrams", 0.0),
        sodium_mg=_as_float(payload, "sodiumMg", 0.0),
        saturated_fat_grams=_as_float(payload, "saturatedFatGrams", 0.0),
        has_artificial_sweeteners=_as_bool(payload, "hasArtificialSweeteners", False),
        has_preservatives=_as_bool(payload, "hasPreservatives", False),
        is_gluten_free=_as_bool(payload, "isGlutenFree", True),
        is_lactose_free=_as_bool(payload, "isLactoseFree", True),
        is_vegan=_as_bool(payload, "isVegan", True),
        is_vegetarian=_as_bool(payload, "isVegetarian", True),
        is_halal=_as_bool(payload, "isHalal", True),
        is_kosher=_as_bool(payload, "isKosher", True),
        allergens_detected="None",
    )
    return data, ingredients
