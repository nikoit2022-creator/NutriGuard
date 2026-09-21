"""
Issue #21 follow-up: evidence-backed TRI-STATE product dietary suitability
(`app.services.dietary_suitability`, `fallback_local_analysis`).

Contract under test: None = unknown, False = SUPPORTED incompatibility,
True = SUPPORTED suitability (explicit source claim only). The absence of
an English keyword is NEVER evidence of suitability.

The two Bulgarian tests are the fixed-behavior counterparts of the audit's
intentional reproductions (`origin/audit/backend-data-quality-issue-21`,
`test_audit_dietary_flag_language_gap_issue21.py`), which asserted the BUG
(`is_gluten_free is True` for a Bulgarian wheat/milk list).
"""
from types import SimpleNamespace

import pytest

from app.models.enums import IngredientSource, IngredientVerificationStatus
from app.services import dietary_suitability as ds
from app.services.fallback_analysis import fallback_local_analysis
from app.services.warning_engine import generate_warnings

_ALL_UNKNOWN = {name: None for name in ds.FLAG_NAMES}


def _flags(product) -> dict:
    return {name: getattr(product, name) for name in ds.FLAG_NAMES}


# --- text-language coverage: absence of a keyword is never suitability ------


@pytest.mark.parametrize(
    "text",
    [
        "Пшенично брашно, мляко, сол",  # Bulgarian, genuinely contains wheat + milk
        "Farine de blé, lait, sel",  # foreign (French)
        "Пшенично брашно, milk powder",  # mixed -- see the dedicated mixed test below
        "",  # empty
        "   ",  # blank
        "water, salt",  # English, but no incompatibility evidence either
    ],
)
def test_no_text_ever_yields_a_positive_suitability_claim(text):
    product, _ = fallback_local_analysis("X", text, [])
    assert all(value is not True for value in _flags(product).values()), _flags(product)


def test_bulgarian_wheat_and_milk_text_is_unknown_not_suitable():
    """Fix for audit Finding 1: previously True/True/True."""
    product, _ = fallback_local_analysis("Хляб", "Пшенично брашно, мляко, сол", [])
    assert _flags(product) == _ALL_UNKNOWN


def test_empty_and_english_text_without_evidence_is_unknown():
    for text in ("", "water, salt, citric acid"):
        product, _ = fallback_local_analysis("X", text, [])
        assert _flags(product) == _ALL_UNKNOWN, text


def test_supported_ingredient_entries_are_still_reported_as_false():
    """Positive incompatibility evidence is preserved (not lost by the fix):
    every entry below IS the ingredient, not a substring of something else."""
    product, _ = fallback_local_analysis("X", "wheat flour, lactose, pork gelatin, salt", [])
    assert product.is_gluten_free is False
    assert product.is_lactose_free is False
    assert product.is_vegan is False
    assert product.is_vegetarian is False
    assert product.is_halal is False
    assert product.is_kosher is False


def test_a_dairy_entry_is_not_vegan_but_does_not_by_itself_prove_lactose():
    """"milk"/"whey" establish the dairy allergen and not-vegan; whether the
    product contains LACTOSE is not inferable from a milk entry (lactose-free
    milk is still milk), so that flag stays unknown."""
    product, _ = fallback_local_analysis("X", "wheat flour, whey powder, skimmed milk powder, salt", [])
    assert product.is_vegan is False
    assert product.allergens_detected == "Milk"
    assert product.is_lactose_free is None
    assert product.is_vegetarian is None  # milk/whey do not make a product non-vegetarian


def test_mixed_language_text_reports_only_the_supported_incompatibilities():
    product, _ = fallback_local_analysis("X", "Пшенично брашно, milk powder, сол", [])
    assert product.is_vegan is False  # the English "milk powder" entry is read
    assert product.is_lactose_free is None  # a milk entry alone does not establish lactose
    # Not contradicted by anything we can read -> unknown, NOT suitable:
    assert product.is_gluten_free is None  # the Bulgarian "Пшенично" is not understood
    assert product.is_halal is None
    assert product.is_kosher is None


@pytest.mark.parametrize(
    "text",
    ["gluten-free oat flour", "Gluten free flour", "milk-free chocolate", "lactose free", "no alcohol", "non-alcoholic"],
)
def test_explicitly_negated_keywords_are_not_incompatibility_evidence(text):
    product, _ = fallback_local_analysis("X", text, [])
    assert product.is_gluten_free is None
    assert product.is_lactose_free is None
    assert product.is_halal is None


# --- explicit true / false / null / malformed input --------------------------


@pytest.mark.parametrize("bad", [None, "true", "false", "yes", 1, 0, 1.0, [], {}, object()])
def test_only_real_booleans_are_explicit_values(bad):
    assert ds.coerce_tri_state(bad) is None


def test_coerce_tri_state_accepts_real_booleans():
    assert ds.coerce_tri_state(True) is True
    assert ds.coerce_tri_state(False) is False


def test_resolve_flags_keeps_explicit_true_false_and_unknown():
    resolved = ds.resolve_flags(
        {"is_vegan": True, "is_halal": False, "is_kosher": None, "is_gluten_free": "true", "is_lactose_free": 1},
        raw_text="",
    )
    assert resolved["is_vegan"] is True
    assert resolved["is_halal"] is False
    assert resolved["is_kosher"] is None
    assert resolved["is_gluten_free"] is None  # malformed -> unknown
    assert resolved["is_lactose_free"] is None  # malformed -> unknown
    assert resolved["is_vegetarian"] is None  # absent -> unknown


def test_explicit_false_is_authoritative_and_derived_evidence_fills_unknowns():
    resolved = ds.resolve_flags({"is_halal": False, "is_vegan": None}, raw_text="pork, milk")
    assert resolved["is_halal"] is False
    assert resolved["is_vegan"] is False  # unknown -> filled from the "pork"/"milk" hit
    assert resolved["is_kosher"] is False  # unknown -> filled from the "pork" hit
    # explicit False with NO contradicting evidence is kept as stated
    assert ds.resolve_flags({"is_vegan": False}, raw_text="water")["is_vegan"] is False


def test_explicit_true_contradicted_by_incompatibility_evidence_becomes_unknown():
    """A positive claim that positive evidence contradicts is not 'supported
    suitability' -- and a conflict is never resolved in favour of the claim.
    (Reviewer finding F1: a model saying vegan/halal/kosher for 'gelatin, pork fat'.)"""
    resolved = ds.resolve_flags(
        {"is_vegan": True, "is_halal": True, "is_kosher": True, "is_gluten_free": True}, raw_text="gelatin, pork fat, sugar"
    )
    assert resolved["is_vegan"] is None
    assert resolved["is_halal"] is None
    assert resolved["is_kosher"] is None
    assert resolved["is_gluten_free"] is True  # nothing contradicts it: explicit claim kept
    # catalog evidence contradicts an explicit true as well, in any language
    assert ds.resolve_flags({"is_gluten_free": True}, "Пшенично брашно, Trusted Test Ingredient", [_ing(is_gluten=True)])["is_gluten_free"] is None


def test_explicit_vegan_beside_explicit_not_vegetarian_is_unknown():
    resolved = ds.resolve_flags({"is_vegan": True, "is_vegetarian": False}, raw_text="")
    assert resolved["is_vegetarian"] is False
    assert resolved["is_vegan"] is None


def test_explicit_true_with_no_contradiction_is_still_honored():
    resolved = ds.resolve_flags({name: True for name in ds.FLAG_NAMES}, raw_text="rice flour, water")
    assert all(value is True for value in resolved.values())


def test_resolve_flags_with_no_arguments_is_all_unknown():
    assert ds.resolve_flags(None, None) == _ALL_UNKNOWN


def test_derivation_never_produces_true():
    derived = ds.derive_incompatibilities("wheat, milk, pork, bacon, alcohol, gelatin, gluten, whey, lactose")
    assert derived and all(value is False for value in derived.values())
    assert ds.derive_incompatibilities("Пшенично брашно") == {}


# --- curated-catalog evidence -------------------------------------------------


def _ing(**flags):
    """A TRUSTED catalog row (verified, curated) unless `flags` say otherwise."""
    base = dict(
        is_gluten=None, is_lactose=None, is_vegan=None, is_vegetarian=None, is_halal=None, is_kosher=None,
        verification_status=IngredientVerificationStatus.VERIFIED, source=IngredientSource.CURATED_SEED,
        identity_uncertain=False, common_name="Trusted Test Ingredient", scientific_name="", e_number=None,
    )
    base.update(flags)
    return SimpleNamespace(**base)


def test_catalog_ingredient_flags_are_evidence_when_an_exact_entry_names_the_row():
    resolved = ds.resolve_flags(None, "Пшенично брашно, Trusted Test Ingredient", [_ing(is_gluten=True, is_vegetarian=False)])
    assert resolved["is_gluten_free"] is False
    assert resolved["is_vegetarian"] is False
    assert resolved["is_vegan"] is False  # not vegetarian implies not vegan
    assert resolved["is_lactose_free"] is None


def test_catalog_unknown_or_favourable_ingredient_flags_prove_nothing():
    """A named ingredient is not enough to infer suitability: an ingredient
    that is itself vegan/gluten-free (or unknown) never makes the PRODUCT so."""
    favourable = _ing(is_gluten=False, is_lactose=False, is_vegan=True, is_vegetarian=True, is_halal=True, is_kosher=True)
    assert ds.resolve_flags(None, "", [favourable, _ing()]) == _ALL_UNKNOWN


# --- allergens: unknown vs known ----------------------------------------------


def test_allergens_are_empty_string_when_nothing_was_found_never_literal_none():
    for text in ("wheat flour, palm oil, salt", "Пшенично брашно, мляко", "", "water"):
        product, _ = fallback_local_analysis("X", text, [])
        assert product.allergens_detected == "", text
        assert product.allergens_detected != "None"


def test_allergens_known_positive_evidence_is_preserved_and_complete():
    only_soy, _ = fallback_local_analysis("X", "soy lecithin, salt", [])
    only_milk, _ = fallback_local_analysis("X", "skimmed milk powder", [])
    both, _ = fallback_local_analysis("X", "milk powder, soy lecithin", [])
    assert only_soy.allergens_detected == "Soy"
    assert only_milk.allergens_detected == "Milk"
    assert both.allergens_detected == "Soy, Milk"  # previously silently dropped "Milk"


def test_negated_allergen_mentions_are_not_positive_evidence():
    product, _ = fallback_local_analysis("X", "soy-free, milk free", [])
    assert product.allergens_detected == ""


@pytest.mark.parametrize(
    "items, expected",
    [
        (["None"], []),
        (["none", "Milk"], ["Milk"]),
        (["No allergens", "N/A", "null", "-", "  ", ""], []),
        (["Allergen-free"], []),
        (["Milk", "milk", "Soy", 3, None], ["Milk", "Soy"]),
        (['"None"'], []),
    ],
)
def test_clean_allergen_names_drops_absence_placeholders_and_keeps_real_allergens(items, expected):
    assert ds.clean_allergen_names(items) == expected


# --- unknown never triggers a confirmed-incompatibility warning ---------------


class _Profile:
    has_diabetes = has_hypertension = has_kidney_disease = has_gout = False
    is_pregnant = for_children = has_high_cholesterol = False
    avoid_gluten = avoid_lactose = require_vegan = require_halal = require_kosher = True
    avoid_peanuts = avoid_soy = avoid_tree_nuts = require_vegetarian = False


_DIETARY_WORDS = ("gluten", "lactose", "vegan", "halal", "kosher")


def _dietary_warning_titles(product, ingredients) -> list[str]:
    warnings = generate_warnings(product, ingredients, _Profile())
    return [w.title for w in warnings if any(word in w.title.lower() for word in _DIETARY_WORDS)]


def test_unknown_flags_emit_no_dietary_warning_for_the_bulgarian_case():
    """Fix for the audit's second reproduction. The audit asserted the
    ABSENCE of a warning as the bug (because flags were falsely True); the
    correct outcome is still no CONFIRMED-incompatibility warning -- but
    now because the flags are UNKNOWN, not because they falsely claim
    compliance."""
    product, ingredients = fallback_local_analysis("Хляб", "Пшенично брашно, мляко, сол", [])
    product.sugar_grams = product.sodium_mg = product.saturated_fat_grams = 0.0
    assert _flags(product) == _ALL_UNKNOWN
    assert _dietary_warning_titles(product, ingredients) == []


def test_supported_incompatibility_still_warns_and_suitability_never_does():
    product, ingredients = fallback_local_analysis("X", "wheat flour, lactose, pork", [])
    product.sugar_grams = product.sodium_mg = product.saturated_fat_grams = 0.0
    assert len(_dietary_warning_titles(product, ingredients)) == 5  # gluten, lactose, vegan, halal, kosher

    suitable = SimpleNamespace(
        sugar_grams=0.0, sodium_mg=0.0, saturated_fat_grams=0.0, has_artificial_sweeteners=False,
        has_preservatives=False, nutrition_basis="PER_100_G", nova_group=1,
        is_gluten_free=True, is_lactose_free=True, is_vegan=True, is_vegetarian=True, is_halal=True, is_kosher=True,
    )
    assert _dietary_warning_titles(suitable, []) == []
