"""
Issue #21 audit reproduction (section 1: unknown-value/API semantics),
committed as an INTENTIONAL reproduction of a confirmed problem, not a
baseline regression test for a fix that has been made. See
nutriguard-backend/docs/BACKEND_DATA_QUALITY_AUDIT.md ("Finding 1") for
the full write-up. Does not modify or weaken any existing test.

`fallback_local_analysis` (app/services/fallback_analysis.py) computes
product-level `is_gluten_free`/`is_lactose_free`/`is_vegan`/
`is_vegetarian`/`is_halal`/`is_kosher` from ENGLISH-ONLY substring
keyword absence over the raw OCR/label text -- "keyword not found"
becomes `True` ("meets this dietary requirement"), not "unknown". This
is the path used whenever Gemini is unavailable/fails, AND (per
`gemini_result_parser.py`'s documented "preserved quirk") for every
`POST /scan/ocr-text` call regardless of Gemini's success. It is NOT
gated on the raw text's declared/detected language the way
`app.services.language_detection`/`label_language` gates other fields
elsewhere in this codebase, and it does NOT consult the actual
per-ingredient scientific catalog's own nullable `is_gluten`/`is_vegan`/
`is_vegetarian`/`is_halal`/`is_kosher` fields (`app/models/ingredient.py`)
for any already-matched ingredient -- the two are entirely disconnected.

This differs from the discovery-path fix already documented in README
section 6, item 6 ("unknown dietary flags now default false, never
true") -- that fix only covers `_to_analyzed_data_from_discovery`
(external barcode-provider data), not this keyword-heuristic path.

Consequence demonstrated below: a Bulgarian-language ingredient list
that genuinely contains milk/wheat/pork is reported as
vegan/gluten-free/halal/kosher-suitable, because none of the literal
English keywords ("milk", "wheat", "gluten", "pork") appear in the
Cyrillic text. `app/services/warning_engine.py` (module docstring:
"Product-like object needs: ... is_gluten_free, is_lactose_free,
is_vegan, is_halal, is_kosher") consumes these flags directly with no
independent per-ingredient cross-check, so `require_vegan`/
`avoid_gluten`/`require_halal`/`require_kosher` users receive an
affirmative false negative -- no warning is generated for a product
that does contain an ingredient their profile asks to avoid.
"""
from app.services.fallback_analysis import fallback_local_analysis
from app.services.warning_engine import generate_warnings
from app.models.enums import WarningSeverity


class _Profile:
    has_diabetes = False
    has_hypertension = False
    has_kidney_disease = False
    has_gout = False
    is_pregnant = False
    for_children = False
    has_high_cholesterol = False
    avoid_gluten = True
    avoid_lactose = True
    require_vegan = True
    require_halal = True
    require_kosher = True
    avoid_peanuts = False
    avoid_soy = False
    avoid_tree_nuts = False
    require_vegetarian = False


def test_bulgarian_milk_and_wheat_text_is_not_detected_by_english_keyword_heuristic():
    """Confirms the gap: literal Bulgarian for 'wheat flour, milk, salt'
    ('Пшенично брашно, мляко, сол') contains real gluten/lactose/vegan-
    relevant ingredients, but none of the English substrings
    ('wheat'/'gluten'/'milk'/'lactose'/'pork') appear in it."""
    raw_text_bg = "Пшенично брашно, мляко, сол"
    product, _ = fallback_local_analysis("Хляб", raw_text_bg, [])

    # This IS the bug: the same real-world ingredients that correctly
    # trip `is_gluten_free=False`/`is_lactose_free=False` in English
    # (see test_fallback_local_analysis_gluten_and_lactose_detection)
    # silently read as safe/suitable in Bulgarian.
    assert product.is_gluten_free is True
    assert product.is_lactose_free is True
    assert product.is_vegan is True


def test_warning_engine_emits_no_dietary_warning_for_the_undetected_bulgarian_case():
    """End-to-end consequence: a user whose profile requires
    vegan/halal/kosher and avoids gluten/lactose gets ZERO warnings for
    a product whose (correctly Bulgarian, genuinely non-compliant)
    ingredient list was only ever run through the English-only
    heuristic -- not because the product is actually compliant, but
    because the detector never looked at non-English text."""
    raw_text_bg = "Пшенично брашно, мляко, сол"
    product, ingredients = fallback_local_analysis("Хляб", raw_text_bg, [])
    product.sugar_grams = 0.0
    product.sodium_mg = 0.0
    product.saturated_fat_grams = 0.0

    warnings = generate_warnings(product, ingredients, _Profile())

    dietary_titles = {w.title for w in warnings}
    assert not any(
        "gluten" in t.lower() or "lactose" in t.lower() or "vegan" in t.lower()
        or "halal" in t.lower() or "kosher" in t.lower()
        for t in dietary_titles
    ), (
        "Expected NO dietary-compliance warning to be generated here -- "
        "that absence, for genuinely non-compliant Bulgarian-language "
        f"content, is exactly the confirmed gap. Got: {dietary_titles}"
    )
