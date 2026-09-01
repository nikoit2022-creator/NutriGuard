"""
`app.services.language_detection.detect_language` -- semantic (not
script-only) English/Bulgarian/other/unknown classification. See
README "Language policy".
"""
from app.services.language_detection import detect_language


def test_english_ingredient_text_detected_as_en():
    assert detect_language("Ingredients: Water, Sugar, Salt, Citric Acid") == "en"


def test_bulgarian_ingredient_text_detected_as_bg():
    assert detect_language("Съставки: Вода, Захар, Сол, Лимонена киселина") == "bg"


def test_latin_script_non_english_is_not_treated_as_english():
    """German -- Latin script, but not English: must not be misdetected
    as "en" merely because the script matches."""
    assert detect_language("Zutaten: Wasser, Zucker, Salz, Weizenmehl") == "other"


def test_french_latin_script_is_not_treated_as_english():
    assert detect_language("Ingrédients: Eau, Sucre, Sel, Farine de blé") != "en"


def test_cyrillic_script_non_bulgarian_is_not_treated_as_bulgarian():
    """Russian -- Cyrillic script, but not Bulgarian: must not be
    misdetected as "bg" merely because the script matches."""
    assert detect_language("Ингредиенты: сахар, соль, мука") == "other"


def test_ukrainian_specific_letters_reject_bulgarian_classification():
    assert detect_language("Інгредієнти: вода, цукор, сіль") == "other"


def test_empty_text_is_unknown():
    assert detect_language("") == "unknown"
    assert detect_language("   ") == "unknown"
    assert detect_language(None) == "unknown"


def test_purely_numeric_or_e_number_text_is_unknown_not_a_false_other():
    assert detect_language("E202, E330, 12%, 250g") == "unknown"


def test_brand_only_text_with_no_vocabulary_signal_is_other_not_english():
    """A bare Latin-script brand/proper-noun string has no stopword
    evidence either way -- must not default to "en"."""
    assert detect_language("Bolt Beverages Xtreme") == "other"
