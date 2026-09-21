"""
Review follow-up (PR #22, finding 2): substring heuristics must not
masquerade as confirmed dietary / allergen evidence.

`app.services.dietary_suitability` used to promote ANY substring hit of
"milk"/"wheat"/"gluten"/... in raw label text into a definitive product
flag (`isVegan=false`, `isLactoseFree=false`, ...) and a positively
"detected" allergen. A substring is not an ingredient identity, and a
mention is not proof of a dietary property. It now derives a claim from raw
text only from EXACT ingredient-entry identity, outside any precautionary /
negating header, and otherwise leaves the value unknown (`None` / no
allergen entry) -- which never implies suitability or allergen absence.

This file first PINS the old failure (a frozen, verbatim copy of the
previous heuristic, `_legacy_*`, so the regression is demonstrable rather
than asserted from memory) and then the corrected semantics.
"""
import re
import time
from types import SimpleNamespace

import pytest

from app.models.enums import IngredientSource, IngredientVerificationStatus
from app.services import dietary_suitability as ds
from app.services.fallback_analysis import fallback_local_analysis
from app.services.ocr_normalizer import create_synthetic_ingredient

_ALL_UNKNOWN = {name: None for name in ds.FLAG_NAMES}

# --- the PREVIOUS heuristic, frozen verbatim from 11ddaca (do not "fix") ------

_LEGACY_KEYWORDS = {
    "is_gluten_free": ("wheat", "gluten"),
    "is_lactose_free": ("milk", "whey", "lactose"),
    "is_vegan": ("pork", "gelatin", "milk"),
    "is_vegetarian": ("pork", "gelatin", "bacon"),
    "is_halal": ("pork", "alcohol"),
    "is_kosher": ("pork",),
}
_LEGACY_NEGATED_SUFFIX_RE = re.compile(r"^[\s\-]*free\b")
_LEGACY_NEGATED_PREFIX_RE = re.compile(r"(?:free\s+(?:from|of)|without|\bno|\bnon)[\s\-]*$")


def _legacy_keyword_present(text: str, keyword: str) -> bool:
    start = 0
    while True:
        index = text.find(keyword, start)
        if index == -1:
            return False
        end = index + len(keyword)
        if not _LEGACY_NEGATED_SUFFIX_RE.match(text[end:]) and not _LEGACY_NEGATED_PREFIX_RE.search(text[:index]):
            return True
        start = end


def _legacy_false_flags(raw_text: str) -> set[str]:
    lower = raw_text.lower()
    return {flag for flag, kws in _LEGACY_KEYWORDS.items() if any(_legacy_keyword_present(lower, k) for k in kws)}


def _legacy_allergens(raw_text: str) -> list[str]:
    lower = raw_text.lower()
    return [name for name, kw in (("Soy", "soy"), ("Milk", "milk")) if _legacy_keyword_present(lower, kw)]


def _flags(raw_text, explicit=None, ingredients=()):
    return ds.resolve_flags(explicit, raw_text, ingredients)


# --- the old failures, demonstrated, and the corrected semantics ---------------


@pytest.mark.parametrize("text", ["coconut milk", "oat milk", "Almond milk, water, salt", "rice milk", "coconut milk (58%), water"])
def test_plant_milk_was_a_false_dairy_claim_and_is_now_unknown(text):
    # OLD: not vegan, not lactose-free, and Milk reported as a positively detected allergen.
    assert {"is_vegan", "is_lactose_free"} <= _legacy_false_flags(text)
    assert _legacy_allergens(text) == ["Milk"]

    # NEW: no claim at all -- and, equally important, no claim of suitability either.
    assert _flags(text) == _ALL_UNKNOWN
    assert ds.detect_allergens_text(text) == ""
    product, _ = fallback_local_analysis("X", text, [])
    assert {name: getattr(product, name) for name in ds.FLAG_NAMES} == _ALL_UNKNOWN
    assert product.allergens_detected == ""
    assert product.allergens_detected != "None"  # "" = unknown / none detected, never an absence claim


def test_soy_milk_is_a_soy_allergen_but_never_a_dairy_one():
    """The token-level view could not tell these apart; entry identity can:
    soy milk really is soy, and really is not dairy."""
    assert _legacy_allergens("soy milk") == ["Soy", "Milk"]  # OLD: also (wrongly) dairy
    assert ds.detect_allergens_text("soy milk") == "Soy"
    assert _flags("soy milk") == _ALL_UNKNOWN


@pytest.mark.parametrize(
    "text",
    [
        "gluten-free",
        "Gluten free flour",
        "free from milk",
        "Free from: milk, soy, gluten",
        "without wheat",
        "contains no milk or gluten",
        "does not contain wheat",
        "lactose-free milk",
        "milk-free chocolate",
        "May contain: milk, soy, wheat",
        "May contain traces of milk and wheat",
        "Produced in a facility that also handles: milk, soy, wheat",
        "sugar-free wheat starch",
    ],
)
def test_negated_and_precautionary_text_is_not_evidence(text):
    assert _flags(text) == _ALL_UNKNOWN
    assert ds.detect_allergens_text(text) == ""


def test_legacy_negation_guard_alone_was_not_enough_for_these_shapes():
    """Documents WHY the fix is entry identity and not "more negation words":
    the previous guard let a header-scoped or precautionary list through."""
    assert "is_vegan" in _legacy_false_flags("May contain: milk")
    assert "is_gluten_free" in _legacy_false_flags("Free from: milk, soy, gluten")
    assert _legacy_allergens("May contain: milk") == ["Milk"]
    assert _flags("May contain: milk") == _ALL_UNKNOWN
    assert _flags("Free from: milk, soy, gluten") == _ALL_UNKNOWN


@pytest.mark.parametrize(
    "text",
    ["buttermilk", "milk chocolate", "milkshake flavour", "wheatgrass juice", "buckwheat flour", "bacon flavour", "vegan bacon",
     "alcohol", "pork-free", "gelatin-free", "soy-free", "ham"],
)
def test_substrings_partial_words_and_derived_names_are_unknown_not_matches(text):
    """Prefer unknown to a guess: none of these is an ingredient entry of the
    closed identity set (some, like "milk chocolate", genuinely contain dairy;
    a conservative unknown is the documented direction)."""
    assert _flags(text) == _ALL_UNKNOWN
    assert ds.detect_allergens_text(text) == ""


# --- genuine occurrences are kept, also next to negated phrases ------------------


@pytest.mark.parametrize(
    "text, expected_false, allergens",
    [
        ("gluten-free oats, wheat flour", {"is_gluten_free"}, ""),
        ("Ingredients: wheat flour, milk. Free from soy.", {"is_gluten_free", "is_vegan"}, "Milk"),
        ("flavouring (may contain milk), wheat flour", {"is_gluten_free"}, ""),
        ("no artificial colours, skimmed milk powder", {"is_vegan"}, "Milk"),
        ("Ingredients: sugar, milk. May contain: soy, wheat.", {"is_vegan"}, "Milk"),
        ("lactose free milk, pork", {"is_vegetarian", "is_vegan", "is_halal", "is_kosher"}, ""),
        ("Contains: Milk, Soy", {"is_vegan"}, "Soy, Milk"),
        ("Free from gluten. Wheat flour, sugar", {"is_gluten_free"}, ""),
    ],
)
def test_genuine_ingredient_occurrences_survive_a_negated_or_precautionary_phrase(text, expected_false, allergens):
    resolved = _flags(text)
    assert {name for name, value in resolved.items() if value is False} == expected_false
    assert all(value is not True for value in resolved.values())
    assert ds.detect_allergens_text(text) == allergens


@pytest.mark.parametrize(
    "text, expected_false, allergens",
    [
        ("skimmed milk powder", {"is_vegan"}, "Milk"),
        ("Chocolate (sugar, cocoa mass, whole milk powder), palm oil", {"is_vegan"}, "Milk"),
        ("MILK 3%, SUGAR", {"is_vegan"}, "Milk"),
        ("Milk*, sugar", {"is_vegan"}, "Milk"),
        ("Whey powder", {"is_vegan"}, "Milk"),
        ("sugar, lactose", {"is_lactose_free", "is_vegan"}, "Milk"),
        ("Wheat Flour (with Calcium, Iron, Niacin), Water", {"is_gluten_free"}, ""),
        ("Wholemeal wheat flour, wheat gluten", {"is_gluten_free"}, ""),
        ("pork, salt", {"is_vegetarian", "is_vegan", "is_halal", "is_kosher"}, ""),
        ("gelatin", {"is_vegetarian", "is_vegan"}, ""),
        ("Bacon, salt", {"is_vegetarian", "is_vegan"}, ""),
        ("soy lecithin, salt", set(), "Soy"),
        ("Emulsifier (soy lecithin)", set(), "Soy"),
    ],
)
def test_actual_dairy_animal_and_gluten_declarations_are_retained(text, expected_false, allergens):
    resolved = _flags(text)
    assert {name for name, value in resolved.items() if value is False} == expected_false
    assert ds.detect_allergens_text(text) == allergens


def test_a_milk_entry_never_establishes_lactose_or_vegetarian_status():
    resolved = _flags("skimmed milk powder, whey powder, whole milk")
    assert resolved["is_vegan"] is False
    assert resolved["is_lactose_free"] is None
    assert resolved["is_vegetarian"] is None
    assert resolved["is_halal"] is None and resolved["is_kosher"] is None


def test_alcohol_and_gelatin_do_not_establish_halal_or_kosher_status():
    """Halal/kosher status is a certification-like property that an ambiguous
    ingredient name (alcohol as a solvent, gelatin of unknown animal) cannot
    establish. Only pork does."""
    resolved = _flags("alcohol, gelatin, ethanol")
    assert resolved["is_halal"] is None and resolved["is_kosher"] is None
    assert resolved["is_vegetarian"] is False  # gelatin is animal-derived


# --- English / Bulgarian / mixed / empty ---------------------------------------


def test_bulgarian_text_yields_only_unknown():
    text = "Пшенично брашно, мляко, сол, свинска мазнина"
    assert _flags(text) == _ALL_UNKNOWN
    assert ds.detect_allergens_text(text) == ""


def test_mixed_language_text_reads_only_the_english_entries():
    text = "Пшенично брашно, wheat flour, мляко, pork, сол"
    resolved = _flags(text)
    assert resolved["is_gluten_free"] is False  # the English entry, not the Bulgarian one
    assert resolved["is_vegetarian"] is False and resolved["is_halal"] is False
    assert resolved["is_lactose_free"] is None
    assert all(value is not True for value in resolved.values())


@pytest.mark.parametrize("text", [None, "", "   ", "\n", ",,,;;;", "...", "()", "100%"])
def test_empty_or_punctuation_only_text_is_unknown(text):
    assert _flags(text) == _ALL_UNKNOWN
    assert ds.detect_allergens_text(text) == ""
    assert ds.derive_text_evidence(text) == ds.TextEvidence()


def test_text_alone_never_produces_true_or_a_suitability_claim():
    for text in ("rice flour, water", "vegan gluten-free lactose-free halal kosher", "oat milk", "Пшенично брашно"):
        assert all(value is not True for value in _flags(text).values()), text


# --- an uncertain match must not fake a contradiction; real evidence still does ---


def test_uncertain_keyword_match_does_not_erase_an_explicit_supported_claim():
    explicit = {"is_vegan": True, "is_lactose_free": True, "is_vegetarian": True}
    # OLD: "milk" substring contradicted the explicit vegan / lactose-free claims and erased them.
    assert {"is_vegan", "is_lactose_free"} <= _legacy_false_flags("coconut milk, water")
    resolved = _flags("coconut milk, water", explicit)
    assert resolved["is_vegan"] is True and resolved["is_lactose_free"] is True and resolved["is_vegetarian"] is True


def test_a_negated_phrase_does_not_erase_an_explicit_supported_claim():
    assert _flags("gluten-free, free from milk", {"is_gluten_free": True, "is_vegan": True}) == {
        **_ALL_UNKNOWN, "is_gluten_free": True, "is_vegan": True,
    }


def test_conflicting_real_evidence_turns_an_explicit_true_into_unknown():
    resolved = _flags("wheat flour, gelatin, milk", {"is_gluten_free": True, "is_vegan": True, "is_vegetarian": True})
    assert resolved["is_gluten_free"] is None
    assert resolved["is_vegan"] is None
    assert resolved["is_vegetarian"] is None


def test_a_milk_entry_does_not_contradict_an_explicit_lactose_free_claim():
    resolved = _flags("skimmed milk powder", {"is_lactose_free": True, "is_vegan": True})
    assert resolved["is_lactose_free"] is True  # not inferred from the token "milk"
    assert resolved["is_vegan"] is None  # a dairy entry genuinely contradicts "vegan"


def test_explicit_false_is_kept_without_text_evidence_and_wins_over_unknown_text():
    assert _flags("oat milk", {"is_vegan": False})["is_vegan"] is False
    assert _flags("", {"is_gluten_free": False})["is_gluten_free"] is False


# --- allergens: declared evidence survives, heuristics do not fabricate ---------


def test_positive_allergen_declarations_are_kept_in_stable_order():
    assert ds.detect_allergens_text("Contains: milk, soy") == "Soy, Milk"
    assert ds.detect_allergens_text("soy flour, skimmed milk powder") == "Soy, Milk"
    assert ds.detect_allergens_text("whey, lactose") == "Milk"


def test_only_soy_and_milk_are_ever_looked_for_so_empty_means_unknown():
    text = "peanuts, wheat flour, eggs, fish"
    assert ds.detect_allergens_text(text) == ""
    product, _ = fallback_local_analysis("X", text, [])
    assert product.allergens_detected == ""  # never "None": the other allergens are simply not examined


# --- catalog evidence: only trusted rows, whatever booleans an untrusted row holds ---


def _catalog_row(**overrides):
    row = dict(
        is_gluten=None, is_lactose=None, is_vegan=None, is_vegetarian=None, is_halal=None, is_kosher=None,
        verification_status=IngredientVerificationStatus.VERIFIED, source=IngredientSource.CURATED_SEED,
        identity_uncertain=False, common_name="Trusted Test Ingredient", scientific_name="", e_number=None,
    )
    row.update(overrides)
    return SimpleNamespace(**row)


NAMED = "sugar, Trusted Test Ingredient"  # text whose exact entry names the default catalog row


@pytest.mark.parametrize("source", [IngredientSource.CURATED_SEED, IngredientSource.REGULATORY_LOOKUP])
def test_trusted_verified_catalog_rows_are_evidence(source):
    resolved = _flags(NAMED, None, [_catalog_row(source=source, is_gluten=True, is_vegetarian=False)])
    assert resolved["is_gluten_free"] is False
    assert resolved["is_vegetarian"] is False and resolved["is_vegan"] is False
    assert resolved["is_lactose_free"] is None


@pytest.mark.parametrize(
    "overrides",
    [
        dict(verification_status=IngredientVerificationStatus.UNVERIFIED, source=IngredientSource.OCR_HEURISTIC),
        dict(verification_status=IngredientVerificationStatus.UNVERIFIED, source=IngredientSource.GEMINI),
        dict(verification_status=IngredientVerificationStatus.UNVERIFIED, source=IngredientSource.CURATED_SEED),
        dict(verification_status=IngredientVerificationStatus.LIMITED_DATA, source=IngredientSource.CURATED_SEED),
        dict(verification_status=IngredientVerificationStatus.VERIFIED, source=IngredientSource.OCR_HEURISTIC),
        dict(verification_status=IngredientVerificationStatus.VERIFIED, source=IngredientSource.GEMINI),
        dict(identity_uncertain=True),
    ],
)
def test_untrusted_catalog_rows_never_become_product_level_claims(overrides):
    row = _catalog_row(is_gluten=True, is_lactose=True, is_vegan=False, is_vegetarian=False, is_halal=False, is_kosher=False, **overrides)
    assert _flags(NAMED, None, [row]) == _ALL_UNKNOWN


def test_rows_without_provenance_and_synthetic_ingredients_are_not_evidence():
    bare = SimpleNamespace(is_gluten=True, is_lactose=True, is_vegan=False, is_vegetarian=False, is_halal=False, is_kosher=False)
    assert _flags("Trusted Test Ingredient", None, [bare]) == _ALL_UNKNOWN  # a bool alone is not provenance
    assert _flags("", None, [create_synthetic_ingredient("Xylotol")]) == _ALL_UNKNOWN


def test_an_untrusted_row_neither_contributes_nor_hides_a_trusted_one_and_never_contradicts_a_claim():
    untrusted = _catalog_row(
        verification_status=IngredientVerificationStatus.UNVERIFIED, source=IngredientSource.OCR_HEURISTIC, is_gluten=True
    )
    trusted = _catalog_row(is_lactose=True)
    resolved = _flags(NAMED, None, [untrusted, trusted])
    assert resolved["is_lactose_free"] is False and resolved["is_gluten_free"] is None
    # an explicit supported claim is not erased by an untrusted row's boolean
    assert _flags(NAMED, {"is_gluten_free": True}, [untrusted])["is_gluten_free"] is True
    # ...but IS turned unknown by a trusted row that contradicts it
    assert _flags(NAMED, {"is_gluten_free": True}, [_catalog_row(is_gluten=True)])["is_gluten_free"] is None


def test_favourable_or_unknown_catalog_flags_prove_nothing_even_when_trusted():
    favourable = _catalog_row(is_gluten=False, is_lactose=False, is_vegan=True, is_vegetarian=True, is_halal=True, is_kosher=True)
    assert _flags(NAMED, None, [favourable, _catalog_row()]) == _ALL_UNKNOWN


# --- catalog evidence needs an exact NAME match, not the matcher's substring link ---------


def _milk_row(**overrides):
    return _catalog_row(common_name="Milk", is_lactose=True, is_vegan=False, **overrides)


@pytest.mark.parametrize("text", ["coconut milk, sugar", "Oat milk", "lactose-free milk", "milk chocolate", "buttermilk"])
def test_a_trusted_row_is_not_evidence_when_the_text_only_contains_its_name_as_a_substring(text):
    """`match_against_database` links a token to a row by bidirectional substring
    ("coconut milk" -> a row named "Milk"). That link is not identity, so the trusted
    row's flags must not become a claim about this product."""
    assert _flags(text, None, [_milk_row()]) == _ALL_UNKNOWN
    product, _ = fallback_local_analysis("X", text, [_milk_row()])
    assert {name: getattr(product, name) for name in ds.FLAG_NAMES} == _ALL_UNKNOWN


def test_a_trusted_row_named_by_an_exact_entry_or_e_number_is_evidence():
    assert _flags("water, Milk", None, [_milk_row()])["is_lactose_free"] is False
    assert _flags("Wheat (E1234)", None, [_catalog_row(e_number="E1234", is_gluten=True)])["is_gluten_free"] is False
    assert _flags("e 1234", None, [_catalog_row(e_number="E1234", is_gluten=True)])["is_gluten_free"] is False
    named_in_parens = _catalog_row(common_name="Tartrazine (Yellow 5)", is_halal=False)
    assert _flags("colour: tartrazine", None, [named_in_parens])["is_halal"] is False


def test_a_trusted_row_named_only_inside_a_negated_or_precautionary_scope_is_not_evidence():
    row = _milk_row()
    assert _flags("May contain: Milk", None, [row]) == _ALL_UNKNOWN
    assert _flags("Free from milk, milk", None, [row]) == _ALL_UNKNOWN  # everything after the header is a statement
    assert _flags("Milk free", None, [row]) == _ALL_UNKNOWN


# --- line-wrapped OCR text: a newline is whitespace, never a scope boundary ----------------


@pytest.mark.parametrize(
    "text",
    [
        "Gluten-\nfree oat flour",
        "Lactose-\nfree milk",
        "Ingredients: sugar. May contain\nmilk, soy",
        "Free from:\nmilk, gluten",
        "Contains no\nmilk",
        "Produced in a factory that handles\nmilk",
        "May contain traces of\nnuts, milk, wheat flour",
        "gluten\nfree",
        "Wheat\nfree",
        "without\nwheat",
        "Sugar, rice flour.\r\nMay contain\r\nmilk, soy",
    ],
)
def test_line_wrapped_negated_or_precautionary_text_is_not_evidence(text):
    assert _flags(text) == _ALL_UNKNOWN
    assert ds.detect_allergens_text(text) == ""


def test_line_wraps_do_not_hide_or_invent_genuine_entries():
    assert _flags("Wheat flour, sugar,\nmilk powder,\nsalt")["is_gluten_free"] is False
    assert ds.detect_allergens_text("Wheat flour,\nskimmed\nmilk powder") == "Milk"  # a wrap inside an entry is a space
    assert _flags("Oat flour, Gluten-\nfree, wheat flour")["is_gluten_free"] is False


# --- "X, Y and Z free" lists, negating phrases and nutrient lines -----------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Wheat, gluten and dairy free", "Gluten, wheat & dairy free", "Wheat/gluten free", "Gluten, lactose free",
        "Milk, egg and nut free", "Milk/lactose free", "Milk or soy free",
        "Contains: no wheat, milk", "Doesn't contain: milk, wheat flour", "Contains none of the following: milk, gluten",
        "Contains neither milk nor gluten",
        "Gluten: none", "Gluten (<20 ppm)", "gluten: <20mg/kg", "Lactose: free", "Lactose: 0.0 g",
        "Lactose (<0.01 g/100 g)", "Milk (0%)", "Milk: 0 g",
    ],
)
def test_free_from_lists_and_nutrient_lines_are_not_ingredient_occurrences(text):
    assert _flags(text) == _ALL_UNKNOWN
    assert ds.detect_allergens_text(text) == ""


def test_a_free_list_does_not_discard_genuine_multiword_entries_before_it():
    resolved = _flags("Wheat flour, skimmed milk powder, salt, gluten free")
    assert resolved["is_gluten_free"] is False and resolved["is_vegan"] is False  # genuine, kept
    assert _flags("Wheat flour, sugar, wheat, gluten and dairy free")["is_gluten_free"] is False  # "wheat flour" kept


@pytest.mark.parametrize(
    "text, allergens, false_flags",
    [
        ("Contains milk and soy", "Soy, Milk", {"is_vegan"}),
        ("Contains: milk and soy.", "Soy, Milk", {"is_vegan"}),
        ("Contains milk", "Milk", {"is_vegan"}),  # no colon: the leading label word is filler, not part of the name
        ("Wheat and rye flour", "", {"is_gluten_free"}),
        ("cow's milk, pasteurised milk, sweet whey powder", "Milk", {"is_vegan"}),
        ("beef gelatin", "", {"is_vegetarian", "is_vegan"}),
        ("Milk (3%), sugar", "Milk", {"is_vegan"}),
    ],
)
def test_and_lists_and_common_qualified_forms_are_read(text, allergens, false_flags):
    resolved = _flags(text)
    assert {name for name, value in resolved.items() if value is False} == false_flags
    assert ds.detect_allergens_text(text) == allergens


@pytest.mark.parametrize(
    "text",
    ["May contain traces of nuts, e.g. milk, soy", "May contain traces of nuts (max. 5 mg), milk", "May contain: milk. Ingredients: 1.5 g sugar"],
)
def test_abbreviation_periods_and_decimals_do_not_end_a_precautionary_scope(text):
    assert _flags(text) == _ALL_UNKNOWN
    assert ds.detect_allergens_text(text) == ""


def test_country_of_origin_and_inline_phrases_do_not_hide_genuine_entries():
    assert _flags("Made in Italy, milk")["is_vegan"] is False
    assert _flags("no artificial colours, wheat flour")["is_gluten_free"] is False


# --- adversarial input must stay linear (the reviewer measured 15 s for 20 KB of digits) ------


@pytest.mark.parametrize(
    "text",
    [
        "1" * 30_000, "1" * 15_000 + " " * 15_000, ", " * 200_000, "milk, " * 60_000, "a, " * 60_000 + "free, " * 30_000,
        "(" * 300_000, "may " * 100_000, "-\n" * 150_000, "1.5 " * 100_000, "0%" * 100_000, "gluten and " * 50_000,
    ],
)
def test_adversarial_text_is_processed_in_linear_time(text):
    started = time.perf_counter()
    ds.derive_text_evidence(text)
    ds.detect_allergens_text(text)
    assert time.perf_counter() - started < 3.0  # linear code takes milliseconds; the quadratic one took many seconds


def test_evidence_beyond_the_length_bound_is_ignored_never_invented():
    late_milk = "sugar, " * 40_000 + "milk"
    assert _flags(late_milk) == _ALL_UNKNOWN  # past the bound: dropped, unknown
