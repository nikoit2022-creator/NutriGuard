"""
PR #22 owner follow-up: a parenthetical qualifier stays ATTACHED to its parent
ingredient.

Reproduced by the owner at eaad86f, on the pure runtime functions and on the
frozen migration evaluator:

    resolve_flags(None, "Milk (plant-based)")        -> is_vegan=False
    detect_allergens_text("Milk (plant-based)")      -> "Milk"
    resolve_legacy_flags(label_scan, "Milk (plant-based)", all six False)
                                                     -> is_vegan=False preserved

Root cause: the tokenizer turned "(plant-based)" into a separate entry and
left the unqualified parent "milk" behind as a definitive identity. A
qualifier that alters -- or leaves uncertain -- the identity must not let the
bare parent count. Identity-PRESERVING qualifiers are a small closed list
(quantity, precautionary statement, "with ..." additive list, a qualifier that
composes with the parent into a known identity); there is no plant-word
blacklist, so whatever is not recognised is unknown. Genuine compound
ingredient sublists keep their own evidence.

Every runtime expectation is checked against the frozen migration evaluator
too (`MIG`), because the migration ships its own copy of the rules.
"""
import time
from types import SimpleNamespace

import pytest

from app.models.enums import IngredientSource, IngredientVerificationStatus
from app.services import dietary_suitability as ds
from app.services.fallback_analysis import fallback_local_analysis

from tests.unit.test_tristate_product_flags_migration import (  # noqa: F401  (engine is a fixture)
    ALL_FALSE,
    FLAGS,
    MIG,
    _known,
    _product,
    _row,
    _run_policy,
    engine,
)

_ALL_UNKNOWN = {name: None for name in ds.FLAG_NAMES}


def _flags(raw_text, explicit=None, ingredients=()):
    return ds.resolve_flags(explicit, raw_text, ingredients)


def _false_flags(raw_text) -> set[str]:
    return {name for name, value in _flags(raw_text).items() if value is False}


def _migration_false_flags(raw_text) -> set[str]:
    return set(MIG.text_supported_false_flags(raw_text))


def _catalog_row(**overrides):
    row = dict(
        is_gluten=None, is_lactose=None, is_vegan=None, is_vegetarian=None, is_halal=None, is_kosher=None,
        verification_status=IngredientVerificationStatus.VERIFIED, source=IngredientSource.CURATED_SEED,
        identity_uncertain=False, common_name="Milk", scientific_name="", e_number=None,
    )
    row.update(overrides)
    return SimpleNamespace(**row)


# --- the owner's exact reproductions ------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Milk (plant-based)",
        "Milk (coconut)",
        "Ingredients: sugar, milk (plant-based), salt",
    ],
)
def test_the_reproduced_examples_no_longer_assert_dairy_identity(text):
    assert _flags(text) == _ALL_UNKNOWN
    assert ds.detect_allergens_text(text) == ""
    assert ds.derive_incompatibilities(text) == {}
    assert _migration_false_flags(text) == set()
    product, _ = fallback_local_analysis("X", text, [])
    assert {name: getattr(product, name) for name in ds.FLAG_NAMES} == _ALL_UNKNOWN
    assert product.allergens_detected == ""


@pytest.mark.parametrize("text", ["Milk (plant-based)", "Milk (coconut)", "Ingredients: sugar, milk (plant-based), salt"])
def test_legacy_migration_policy_no_longer_preserves_false_or_erases_true_for_a_qualified_parent(text):
    all_false = MIG.resolve_legacy_flags(source="label_scan", raw_text=text, stored=dict(ALL_FALSE))
    assert all_false == {flag: None for flag in FLAGS}  # the unsupported is_vegan=False is no longer preserved
    # ...and a provider's explicit `true` is not contradicted (and erased) by an unsupported interpretation
    provider_true = MIG.resolve_legacy_flags(source="open_food_facts", raw_text=text, stored={**{f: None for f in FLAGS}, "is_vegan": True})
    assert provider_true["is_vegan"] is True


# --- qualifiers that alter or leave the identity uncertain stay attached ---------------


@pytest.mark.parametrize(
    "text",
    [
        "Milk (plant-based)",
        "Milk [plant-based]",
        "MILK (Plant Based)",
        "Milk (vegan)",
        "Milk (dairy-free)",
        "Milk (lactose-free)",
        "Milk (oat)",
        "Milk (almond)",
        "Milk (from almonds)",
        "Milk (coconut or almond)",
        "Milk (coconut, almond)",  # several qualifiers: ambiguous, not a sublist we can trust
        "Milk (plant-based, 3%)",  # a quantity next to an uncertain qualifier does not resolve it
        "Milk (plant-based (oat))",  # nested qualifier
        "Milk ((oat))",
        "Milk (substitute)",
        "Whey (plant-based)",
        "Skimmed milk powder (soy-based)",
        "Wheat flour (gluten free)",
        "Wheat (gluten-free variety)",
        "Pork (halal)",
        "Gelatin (vegetable)",
        "Gelatin (agar)",
        "Bacon (plant-based)",
        "Milk and wheat flour (plant-based)",  # the qualifier may attach to either part: neither is asserted
        # a percentage INSIDE a qualifier is not "a quantity": something other than a number remains (independent review)
        "Milk (100% plant-based)",
        "milk (3% coconut)",
        "milk (coconut 3%)",
        "milk (plant-based 100%)",
        "milk (approx. 3% plant-based)",
        "milk (100 % plant-based)",
        "milk (100\uff05 plant-based)",  # full-width percent sign
        "milk (1 coconut)",
        "milk (3% coconut, 2% oat)",
        # several adjacent groups qualify the same entry; any uncertain one leaves the identity uncertain
        "milk (skimmed) (coconut)",
        "milk (skimmed)(coconut)",
        "milk (3%) (plant-based)",
        "milk (3%) [plant-based]",
        "milk [3%] (plant-based)",
        "milk (plant-based) (3%)",
        "milk (coconut) (3%)",
        "milk (may contain traces of nuts) (plant-based)",
        # composition stays inside the parent's own identity family: "milk sugar" is lactose, not milk
        "milk (sugar)",
    ],
)
def test_an_identity_altering_or_uncertain_qualifier_keeps_the_parent_unasserted(text):
    resolved = _flags(text)
    assert all(value is None for value in resolved.values()), (text, resolved)
    assert ds.detect_allergens_text(text) in ("", "Soy")  # only an explicit soy entry could ever appear
    assert ds.derive_text_evidence(text).flags == frozenset()
    assert _migration_false_flags(text) == set()


def test_a_qualifier_never_promotes_the_parent_and_only_a_named_soy_entry_can_yield_soy():
    # "(soy)" composes to "soy milk" -- a genuine soy identity, so Soy is declared; nothing about dairy.
    assert ds.detect_allergens_text("Milk (soy)") == "Soy"
    assert _flags("Milk (soy)") == _ALL_UNKNOWN
    assert ds.detect_allergens_text("Milk (plant-based)") == ""


@pytest.mark.parametrize(
    "text",
    ["Chocolate (milk (plant-based), sugar)", "Biscuit (wheat flour substitute (rice), milk (oat), sugar)"],
)
def test_a_qualified_parent_nested_inside_a_compound_ingredient_stays_unasserted(text):
    assert "is_vegan" not in _false_flags(text)
    assert ds.detect_allergens_text(text) == ""


# --- explicit / independently supported claims are not erased ---------------------------


def test_an_explicit_true_claim_is_not_contradicted_by_an_unsupported_interpretation():
    for text in ("Milk (plant-based)", "Milk (coconut)", "Ingredients: sugar, milk (plant-based), salt"):
        assert _flags(text, {"is_vegan": True})["is_vegan"] is True, text
        assert _flags(text, {"is_vegan": True, "is_vegetarian": True, "is_lactose_free": True}) == {
            **_ALL_UNKNOWN, "is_vegan": True, "is_vegetarian": True, "is_lactose_free": True,
        }, text


def test_an_explicit_false_claim_is_never_overwritten():
    assert _flags("Milk (plant-based)", {"is_vegan": False})["is_vegan"] is False


def test_independently_supported_evidence_still_contradicts_a_positive_claim():
    text = "sugar, milk (plant-based), gelatin"
    resolved = _flags(text, {"is_vegan": True})
    assert resolved["is_vegan"] is None  # a genuine gelatin entry contradicts it, the qualified milk plays no part
    assert resolved["is_vegetarian"] is False
    assert _flags("Milk (plant-based), skimmed milk powder", {"is_vegan": True})["is_vegan"] is None
    assert ds.detect_allergens_text("Milk (plant-based), skimmed milk powder") == "Milk"


def test_a_qualified_parent_does_not_hide_a_separate_genuine_entry_or_a_negated_scope():
    assert _false_flags("Milk (plant-based), pork") == {"is_vegetarian", "is_vegan", "is_halal", "is_kosher"}
    assert _false_flags("Milk (plant-based), wheat flour, sugar") == {"is_gluten_free"}
    assert _false_flags("Milk (plant-based). May contain: wheat") == set()
    assert _false_flags("Free from milk (plant-based), wheat flour") == set()


# --- trusted catalog rows: same rule ------------------------------------------------------


@pytest.mark.parametrize("text", ["Milk (plant-based)", "Milk (coconut)", "sugar, milk (plant-based), salt"])
def test_a_trusted_catalog_row_named_milk_is_not_evidence_for_a_qualified_milk(text):
    row = _catalog_row(is_lactose=True, is_vegan=False)
    assert _flags(text, None, [row]) == _ALL_UNKNOWN
    product, _ = fallback_local_analysis("X", text, [row])
    assert {name: getattr(product, name) for name in ds.FLAG_NAMES} == _ALL_UNKNOWN
    # the same row is evidence when the entry genuinely IS milk
    assert _flags("sugar, milk", None, [row])["is_lactose_free"] is False
    assert _flags("sugar, milk (3%)", None, [row])["is_lactose_free"] is False


def test_a_catalog_row_may_still_be_named_by_a_child_entry_or_by_its_exact_qualified_name():
    assert _flags("Wheat (E1234)", None, [_catalog_row(common_name="Wheat", e_number="E1234", is_gluten=True)])["is_gluten_free"] is False
    named = _catalog_row(common_name="Milk (plant-based)", is_vegan=False)
    assert _flags("Milk (plant-based)", None, [named])["is_vegan"] is False  # the row's own name includes the qualifier


# --- ordinary quantity qualifiers keep the identity ----------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Milk (3%), sugar", {"is_vegan"}),
        ("MILK (3.5 %)", {"is_vegan"}),
        ("Whole milk (3.5% fat)", {"is_vegan"}),
        ("Milk (min. 20%)", {"is_vegan"}),
        ("Milk (250 ml)", {"is_vegan"}),
        ("Milk (approx. 3 g)", {"is_vegan"}),
        ("Wheat flour (30%), water", {"is_gluten_free"}),
        ("Wheat flour [30%]", {"is_gluten_free"}),
        ("Pork (12%), salt", {"is_vegetarian", "is_vegan", "is_halal", "is_kosher"}),
        ("Whey powder (5%)", {"is_vegan"}),
        ("Milk ( )", {"is_vegan"}),
        ("Milk ()", {"is_vegan"}),
        ("Milk (3%) (3.5% fat)", {"is_vegan"}),  # several adjacent quantity groups
        ("Milk (3%) (skimmed)", {"is_vegan"}),  # quantity + an identity-composing qualifier
        ("Milk [3%] [250 ml]", {"is_vegan"}),
        ("Milk (3,5 %)", {"is_vegan"}),
        ("Milk (3\uff05)", {"is_vegan"}),
    ],
)
def test_quantity_qualifiers_keep_the_parent_identity(text, expected):
    assert _false_flags(text) == expected
    assert _migration_false_flags(text) == expected


@pytest.mark.parametrize("text", ["Milk (0%)", "Gluten (<20 ppm)", "Lactose (<0.01 g/100 g)", "Milk (0 g)"])
def test_zero_and_threshold_quantities_stay_nutrient_lines_not_occurrences(text):
    assert _flags(text) == _ALL_UNKNOWN
    assert ds.detect_allergens_text(text) == ""


# --- other identity-preserving qualifiers ------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected, allergens",
    [
        ("Milk (skimmed)", {"is_vegan"}, "Milk"),
        ("Milk (powder)", {"is_vegan"}, "Milk"),
        ("Milk (cow's)", {"is_vegan"}, "Milk"),
        ("Milk (pasteurised)", {"is_vegan"}, "Milk"),
        ("Whey (sweet)", {"is_vegan"}, "Milk"),
        ("Gelatin (bovine)", {"is_vegetarian", "is_vegan"}, ""),
        ("Pork (fat)", {"is_vegetarian", "is_vegan", "is_halal", "is_kosher"}, ""),
        ("Wheat (flour)", {"is_gluten_free"}, ""),
        ("Wheat Flour (with Calcium, Iron, Niacin), Water", {"is_gluten_free"}, ""),
        ("Wheat flour (fortified with iron and calcium)", {"is_gluten_free"}, ""),
        ("Milk (with vitamin D)", {"is_vegan"}, "Milk"),
        ("Milk (including cream)", {"is_vegan"}, "Milk"),
        ("Wheat flour (may contain traces of milk)", {"is_gluten_free"}, ""),
        ("Wheat flour (produced in a facility that handles nuts)", {"is_gluten_free"}, ""),
        ("Milk powder (may contain soy)", {"is_vegan"}, "Milk"),
    ],
)
def test_identity_preserving_qualifiers_keep_the_identity(text, expected, allergens):
    assert _false_flags(text) == expected
    assert ds.detect_allergens_text(text) == allergens
    assert _migration_false_flags(text) == expected


def test_an_e_number_after_a_name_is_a_synonym_not_a_qualifier_of_its_identity():
    assert _false_flags("Wheat (E1234)") == {"is_gluten_free"}
    assert list(ds._evidential_entries("Cochineal (E120), sugar")) == ["cochineal", "e120", "sugar"]
    row = _catalog_row(common_name="Cochineal", is_vegan=False, is_vegetarian=False)  # a trusted row with no E-number of its own
    resolved = _flags("sugar, cochineal (colour)", None, [row])
    assert resolved["is_vegan"] is None  # "(colour)" is not an E-number and not recognised: unknown
    resolved = _flags("Colour: cochineal (E120)", None, [row])
    assert resolved["is_vegan"] is False and resolved["is_vegetarian"] is False


def test_a_composed_qualifier_is_consumed_and_not_read_again_as_a_separate_entry():
    assert list(ds._evidential_entries("soy (milk)")) == ["soy milk"]  # soy milk is soy, not milk
    assert _false_flags("soy (milk)") == set()
    assert ds.detect_allergens_text("soy (milk)") == "Soy"
    assert list(ds._evidential_entries("Milk (skimmed)")) == ["skimmed milk"]


def test_a_negating_qualifier_is_not_an_additive_list_or_a_precautionary_statement():
    # "with no ..." / "without ..." negate rather than add, so they leave the parent's identity uncertain
    assert _false_flags("Wheat flour (with no gluten)") == set()
    assert _false_flags("Milk (without lactose)") == set()
    assert _false_flags("Milk (free from dairy)") == set()


# --- genuine compound-ingredient sublists keep their evidence ---------------------------------------


@pytest.mark.parametrize(
    "text, expected, allergens",
    [
        ("Chocolate (sugar, cocoa mass, whole milk powder), palm oil", {"is_vegan"}, "Milk"),
        ("Biscuit (wheat flour, sugar, palm oil), salt", {"is_gluten_free"}, ""),
        ("Margarine (vegetable oils, water, milk proteins)", {"is_vegan"}, "Milk"),
        ("Sauce (soy sauce, sugar), gelatin", {"is_vegetarian", "is_vegan"}, "Soy"),
        ("Emulsifier (soy lecithin)", set(), "Soy"),
        ("Flavouring (natural flavouring, whey powder)", {"is_vegan"}, "Milk"),
        ("Sweets (sugar, gelatin, pork gelatin)", {"is_vegetarian", "is_vegan", "is_halal", "is_kosher"}, ""),
        # a sublist whose own entry is qualified: only that entry is withheld
        ("Chocolate (sugar, milk (plant-based), whole milk powder)", {"is_vegan"}, "Milk"),
        ("Wheat flour (wheat flour, calcium, iron), sugar", {"is_gluten_free"}, ""),
    ],
)
def test_compound_ingredient_sublists_keep_their_own_evidence(text, expected, allergens):
    assert _false_flags(text) == expected
    assert ds.detect_allergens_text(text) == allergens
    assert _migration_false_flags(text) == expected


# --- adversarial / robustness -------------------------------------------------------------------------


def test_a_forged_quantity_marker_in_the_input_cannot_turn_a_qualifier_into_a_quantity():
    assert _flags("Milk (plant-based)") == _ALL_UNKNOWN
    assert _flags("Milk (plant-based)") == _ALL_UNKNOWN
    assert _migration_false_flags("Milk (plant-based)") == set()
    assert _false_flags("Milk, sugar") == {"is_vegan"}  # the stray character is just dropped


@pytest.mark.parametrize(
    "text",
    ["a(" * 100_000, "milk (x)" * 100_000, "milk " + "(x)" * 100_000, "milk (" * 100_000, "milk (plant-based) " * 30_000, "milk (3%) " * 30_000, "(milk (" * 100_000 + ")" * 100_000],
)
def test_nested_qualifier_groups_are_processed_in_linear_time(text):
    for evaluate in (ds.derive_text_evidence, MIG.text_supported_false_flags):
        started = time.perf_counter()
        evaluate(text)
        assert time.perf_counter() - started < 3.0


def test_unbalanced_brackets_do_not_crash_and_do_not_assert_a_qualified_parent():
    for text in ("Milk (plant-based", "Milk plant-based)", "Milk ((plant-based)", "Milk [plant-based)"):
        assert _flags(text) == _ALL_UNKNOWN, text
        assert _migration_false_flags(text) == set(), text
    for text in ("Milk )(plant-based(", ")))(((", "((("):
        assert list(ds._evidential_entries(text)) == list(MIG._evidential_entries(text))  # no crash, same reading


# --- runtime <-> frozen migration parity ------------------------------------------------------------------

PARITY_CORPUS = [
    "Milk (plant-based)", "Milk (coconut)", "Ingredients: sugar, milk (plant-based), salt", "Milk (skimmed)", "Milk (3%)",
    "Whole milk (3.5% fat)", "Wheat Flour (with Calcium, Iron, Niacin), Water", "Wheat flour (may contain traces of milk)",
    "Milk (plant-based (oat))", "Milk ((oat))", "Milk (coconut or almond)", "Milk (plant-based, 3%)", "Milk [plant-based]",
    "Chocolate (sugar, cocoa mass, whole milk powder), palm oil", "Chocolate (milk (plant-based), sugar), gelatin",
    "Emulsifier (soy lecithin)", "Milk (dairy-free)", "Milk (0%)", "Gelatin (bovine)", "Pork (halal)", "Wheat (E1234)",
    "Milk (soy)", "Milk and wheat flour (plant-based)", "Milk (with oat)", "Wheat flour (gluten free)", "Milk (plant-based)",
    "Milk (plant-based", "Milk plant-based)", "Milk ((plant-based)", "flavouring (may contain milk), wheat flour",
    "Milk (100% plant-based)", "milk (3% coconut, 2% oat)", "milk (skimmed) (coconut)", "milk (3%) (plant-based)", "milk (sugar)",
    "soy (milk)", "Cochineal (E120), sugar", "Milk (3%) (skimmed)", "milk (100\uff05 plant-based)", "milk [3%] [250 ml]",
    "Gluten (<20 ppm), wheat flour (12%)", "Contains: milk (plant-based), soy (lecithin)", "milk (plant-based)\nwheat flour (30%)",
]


@pytest.mark.parametrize("text", PARITY_CORPUS)
def test_runtime_and_frozen_migration_agree_on_qualified_entries(text):
    assert list(MIG._evidential_entries(text)) == list(ds._evidential_entries(text)), text
    assert MIG.text_supported_false_flags(text) == set(ds.derive_text_evidence(text).flags), text


@pytest.mark.parametrize("text", PARITY_CORPUS)
def test_migration_policy_agrees_with_runtime_resolution_for_a_provider_row(text):
    """For a row whose stored flags are all `false` the migration keeps exactly the flags the runtime supports."""
    kept = MIG.resolve_legacy_flags(source="label_scan", raw_text=text, stored=dict(ALL_FALSE))
    assert {flag for flag, value in kept.items() if value is False} == set(ds.derive_incompatibilities(text))


# --- the migration policy end to end (SQLite rows) -------------------------------------------------------------


def test_migration_policy_on_rows_with_qualified_parents(engine):
    with engine.begin() as conn:
        _product(conn, "plant-label", source="label_scan", text="Milk (plant-based)", **ALL_FALSE)
        _product(conn, "plant-mixed", source="label_scan", text="sugar, milk (coconut), salt", **ALL_FALSE)
        _product(conn, "plant-provider", source="open_food_facts", text="Milk (plant-based)", is_vegan=True)
        _product(conn, "dairy-quantity", source="label_scan", text="Milk (3%), sugar", **ALL_FALSE)
        _product(conn, "dairy-skimmed", source="label_scan", text="Milk (skimmed), sugar", **ALL_FALSE)
        _product(conn, "sublist", source="label_scan", text="Chocolate (sugar, whole milk powder)", **ALL_FALSE)
    _run_policy(engine)
    assert _known(_row(engine, "plant-label")) == {}
    assert _known(_row(engine, "plant-mixed")) == {}
    assert _known(_row(engine, "plant-provider")) == {"is_vegan": True}
    assert _known(_row(engine, "dairy-quantity")) == {"is_vegan": False}
    assert _known(_row(engine, "dairy-skimmed")) == {"is_vegan": False}
    assert _known(_row(engine, "sublist")) == {"is_vegan": False}
    assert _run_policy(engine) == 0  # idempotent
