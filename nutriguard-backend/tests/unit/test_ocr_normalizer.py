from app.models.enums import RiskLevel
from app.services.ocr_normalizer import (
    create_synthetic_ingredient,
    match_against_database,
    normalize_and_extract_tokens,
)


def test_tokenizes_comma_separated_ingredients():
    tokens = normalize_and_extract_tokens("Water, Sugar, Citric Acid")
    assert tokens == ["Water", "Sugar", "Citric Acid"]


def test_strips_brackets_and_percent_parens():
    tokens = normalize_and_extract_tokens("Palm Oil [sustainably sourced], Cocoa (10%)")
    assert tokens == ["Palm Oil", "Cocoa"]


def test_strips_ingredients_prefix_case_insensitive():
    tokens = normalize_and_extract_tokens("INGREDIENTS: Water, Salt")
    assert tokens == ["Water", "Salt"]


def test_filters_single_character_tokens():
    tokens = normalize_and_extract_tokens("Water, A, Salt")
    assert "A" not in tokens


def test_match_against_database_by_e_number():
    class FakeIngredient:
        id = "e951_aspartame"
        common_name = "Aspartame"
        scientific_name = "L-alpha-aspartyl..."
        e_number = "E951"

    result = match_against_database(["Aspartame (E951)"], [FakeIngredient()])
    assert len(result.matched_ingredients) == 1
    assert result.matched_ingredients[0].id == "e951_aspartame"


def test_match_against_database_by_partial_name():
    class FakeIngredient:
        id = "sugar"
        common_name = "Sugar"
        scientific_name = "Sucrose"
        e_number = None

    result = match_against_database(["Cane Sugar"], [FakeIngredient()])
    assert len(result.matched_ingredients) == 1


def test_unknown_tokens_go_to_unknown_list():
    result = match_against_database(["Unobtainium"], [])
    assert result.unknown_ingredients == ["Unobtainium"]
    assert result.matched_ingredients == []


def test_synthetic_ingredient_high_risk_keywords():
    syn = create_synthetic_ingredient("Sodium Nitrite")
    assert syn.risk_level == RiskLevel.HIGH_CONCERN
    assert syn.id.startswith("synth_")


def test_synthetic_ingredient_moderate_risk_keywords():
    syn = create_synthetic_ingredient("Corn Syrup")
    assert syn.risk_level == RiskLevel.POTENTIAL_CONCERN


def test_synthetic_ingredient_safe_default():
    syn = create_synthetic_ingredient("Water")
    assert syn.risk_level == RiskLevel.SAFE


def test_synthetic_ingredient_diabetes_flag_from_sugar_keyword():
    syn = create_synthetic_ingredient("Dextrose")
    assert syn.bad_for_diabetes is True


def test_synthetic_ingredient_hypertension_flag_from_sodium_keyword():
    syn = create_synthetic_ingredient("Sodium Chloride")
    assert syn.bad_for_hypertension is True


def test_synthetic_ingredient_extracts_e_number():
    syn = create_synthetic_ingredient("some e621 flavor enhancer")
    assert syn.e_number == "E621"


# -- bilingual output requirements -------------------------------------------

def test_synthetic_ingredient_preserves_bulgarian_display_name():
    syn = create_synthetic_ingredient("Натриев бензоат")
    assert syn.common_name == "Натриев бензоат"
    assert syn.id.startswith("synth_")


def test_synthetic_ingredient_translates_known_german_term():
    syn = create_synthetic_ingredient("Zucker")
    assert syn.common_name == "Sugar"


def test_synthetic_ingredient_never_leaks_unsupported_script_name():
    # A third-language ingredient name (Greek here) written in a script
    # that cannot be deterministically translated must never reach the
    # client as-is -- it falls back to the E-number, or a safe English
    # label when there is no E-number either.
    syn_with_e_number = create_synthetic_ingredient("ζάχαρη E950")
    assert syn_with_e_number.common_name == "E950"

    syn_without_e_number = create_synthetic_ingredient("ζάχαρη")
    assert syn_without_e_number.common_name == "Unidentified Ingredient"


def test_synthetic_ingredient_null_and_none_placeholders_never_become_the_name():
    assert create_synthetic_ingredient("null").common_name == "Unidentified Ingredient"
    assert create_synthetic_ingredient("None").common_name == "Unidentified Ingredient"


def test_synthetic_ingredient_ids_do_not_collide_for_different_non_ascii_names():
    # Two different non-Latin-script names with no E-number must not both
    # collapse to the same synthetic id (a plain ASCII-strip of the name
    # would produce "synth_" for both, silently merging two ingredients).
    syn_a = create_synthetic_ingredient("糖")
    syn_b = create_synthetic_ingredient("塩")
    assert syn_a.id != syn_b.id
    assert syn_a.id != "synth_"


def test_match_against_database_by_bulgarian_search_alias():
    """API Contract requirement: a Bulgarian label's ingredient name is
    matched against the (English) scientific database via its English
    canonical name/E-number, without needing the OCR token itself to be
    in English."""

    class FakeIngredient:
        id = "e951_aspartame"
        common_name = "Aspartame"
        scientific_name = "L-alpha-aspartyl..."
        e_number = "E951"

    result = match_against_database(["Аспартам"], [FakeIngredient()])
    assert len(result.matched_ingredients) == 1
    assert result.matched_ingredients[0].id == "e951_aspartame"


def test_match_against_database_by_e_number_after_bulgarian_alias():
    class FakeIngredient:
        id = "e211_sodium_benzoate"
        common_name = "Sodium Benzoate"
        scientific_name = "Sodium salt of benzoic acid"
        e_number = "E211"

    result = match_against_database(["Натриев бензоат"], [FakeIngredient()])
    assert len(result.matched_ingredients) == 1
    assert result.matched_ingredients[0].e_number == "E211"
