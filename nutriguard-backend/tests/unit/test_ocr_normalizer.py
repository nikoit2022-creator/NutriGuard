from app.models.enums import RiskLevel
from app.services.ocr_normalizer import (
    create_synthetic_ingredient,
    match_against_database,
    normalize_and_extract_tokens,
    reconstruct_synthetic_ingredient,
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


def test_synthetic_ingredient_non_latin_name_gets_a_stable_non_colliding_id():
    """A name with no ASCII-alphanumeric characters at all (e.g. a
    Cyrillic-only Bulgarian ingredient word -- see
    app.services.label_language) must not collapse to the same empty
    "synth_" id as every other such name."""
    water = create_synthetic_ingredient("Вода")
    sugar = create_synthetic_ingredient("Захар")
    assert water.id != "synth_"
    assert sugar.id != "synth_"
    assert water.id != sugar.id
    # Deterministic: the same name always yields the same id.
    assert create_synthetic_ingredient("Вода").id == water.id


def test_reconstruct_synthetic_ingredient_recovers_cyrillic_human_name():
    original = create_synthetic_ingredient("Вода")
    restored = reconstruct_synthetic_ingredient(original.id, "Вода, Захар")
    assert restored.id == original.id
    assert restored.common_name == "Вода"
    assert "synth" not in restored.common_name.lower()


def test_reconstruct_synthetic_ingredient_never_displays_hash_id():
    restored = reconstruct_synthetic_ingredient("synth_5ecbec8146", "")
    assert restored.id == "synth_5ecbec8146"
    assert restored.common_name == "Ingredient detected on label"
