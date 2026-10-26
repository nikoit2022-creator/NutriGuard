"""Conservative parsing of ingredient text and cross-contact statements."""

import json
import re

_TRACE_START = re.compile(
    r"(?i)(?:^|[.,;]\s*|\n\s*)(?:may\s+(?:also\s+)?contain|"
    r"може\s+да\s+съдържа|възможни\s+следи\s+от)\s*[:\-]?\s*"
)

_ALLERGEN_WORDS = {
    "Gluten": (r"\bwheat\b", r"\bпшениц\w*", r"\bbarley\b", r"\brye\b", r"\bечемик\b", r"\bръж\w*"),
    "Milk": (r"\bmilk\b", r"\bwhey\b", r"\bмляко\b", r"\bмлеч\w*", r"\bсуроват\w*"),
    "Peanuts": (r"\bpeanuts?\b", r"\bфъстъц\w*"),
    "Eggs": (r"\beggs?\b", r"\bяйц\w*", r"\bяйчен\w*"),
    "Soybeans": (r"\bsoy(?:a|beans?)?\b", r"\bсоев\w*", r"\bсоя\b"),
    "Nuts": (r"\bhazelnuts?\b", r"\balmonds?\b", r"\bлешниц\w*", r"\bбадем\w*"),
    "Sesame": (r"\bsesame\b", r"\bсусам\w*"),
}


def split_ingredients_and_traces(text: str) -> tuple[str, str]:
    """Keep the original ingredient segment; separate an explicit trace clause.

    A sentence without a trace marker is preserved verbatim. Never infer
    cross-contact from a mere ingredient name.
    """
    match = _TRACE_START.search(text)
    if not match:
        return text.strip(), ""
    return text[: match.start()].strip().rstrip(".; "), text[match.end() :].strip().rstrip(". ")


def stated_allergens(ingredient_text: str) -> set[str]:
    """Only explicit named ingredients, never a separated 'may contain' clause."""
    ingredients, _ = split_ingredients_and_traces(ingredient_text)
    return {
        group for group, patterns in _ALLERGEN_WORDS.items()
        if any(re.search(pattern, ingredients, re.IGNORECASE) for pattern in patterns)
    }


def dietary_statuses(product: object) -> dict[str, str]:
    """Three-state evidence for display and warnings, including legacy rows.

    Boolean false in the old schema also means 'provider did not say'. Only
    explicit ingredient contradictions become `no`; absent evidence stays
    `unknown`. Certification cannot be inferred from ingredient names.
    """
    ingredients, _ = split_ingredients_and_traces(getattr(product, "raw_ingredient_text", "") or "")
    allergens = stated_allergens(ingredients)
    # OFF booleans are set from structured tags. First-party/legacy Boolean
    # defaults can be positive without explicit suitability evidence.
    positive_is_documented = getattr(product, "source", None) in (None, "open_food_facts")
    try:
        explicit_flags = json.loads(getattr(product, "dietary_evidence_json", "") or "{}")
        if not isinstance(explicit_flags, dict):
            explicit_flags = {}
    except (TypeError, ValueError):
        explicit_flags = {}
    meat = bool(re.search(r"\b(meat|beef|pork|chicken|fish|gelatin)\b", ingredients, re.I))
    animal = bool(allergens & {"Milk", "Eggs"}) or meat
    inferred = {
        "glutenFree": "no" if "Gluten" in allergens else "yes" if positive_is_documented and getattr(product, "is_gluten_free", False) else "unknown",
        "lactoseFree": "no" if re.search(r"\b(lactose|milk powder|лактоза)\b", ingredients, re.I) else "yes" if positive_is_documented and getattr(product, "is_lactose_free", False) else "unknown",
        "vegan": "no" if animal else "yes" if positive_is_documented and getattr(product, "is_vegan", False) else "unknown",
        "vegetarian": "no" if meat else "yes" if positive_is_documented and getattr(product, "is_vegetarian", False) else "unknown",
        "halal": "yes" if positive_is_documented and getattr(product, "is_halal", False) else "unknown",
        "kosher": "yes" if positive_is_documented and getattr(product, "is_kosher", False) else "unknown",
    }
    for api_key, old_key in (
        ("glutenFree", "is_gluten_free"), ("lactoseFree", "is_lactose_free"),
        ("vegan", "is_vegan"), ("vegetarian", "is_vegetarian"),
        ("halal", "is_halal"), ("kosher", "is_kosher"),
    ):
        if isinstance(explicit_flags.get(old_key), bool):
            # Explicitly named contradictory ingredients outrank a stale tag.
            if inferred[api_key] != "no":
                inferred[api_key] = "yes" if explicit_flags[old_key] else "no"
    return inferred
