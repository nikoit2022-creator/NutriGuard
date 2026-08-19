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
