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


# --- Review finding 4: stronger lexical evidence + corrected Bulgarian
# --- alphabet handling --------------------------------------------------


def test_single_ambiguous_latin_token_is_not_classified_as_english():
    """"in"/"or"/"per" are real words (or near-homographs) in German/
    French/Italian too -- even several of them together must never tip
    the classification to English on their own."""
    assert detect_language("in") == "other"
    assert detect_language("or") == "other"
    assert detect_language("per") == "other"
    assert detect_language("in or per") == "other"


def test_single_weak_english_word_is_not_enough_on_its_own():
    assert detect_language("water") == "other"


def test_two_distinct_weak_english_words_are_enough():
    assert detect_language("Water, Sugar") == "en"


def test_single_strong_english_word_is_enough_on_its_own():
    assert detect_language("Contains soy") == "en"


def test_italian_latin_script_is_not_treated_as_english():
    assert detect_language("Ingredienti: Acqua, Zucchero, Sale, Farina di frumento") == "other"


def test_spanish_latin_script_is_not_treated_as_english():
    assert detect_language("Ingredientes: Agua, Azúcar, Sal, Harina de trigo") == "other"


def test_romanian_latin_script_is_not_treated_as_english():
    assert detect_language("Ingrediente: Apă, Zahăr, Sare, Făină de grâu") == "other"


def test_russian_cyrillic_script_is_not_treated_as_bulgarian_weak_evidence_only():
    """No letter exclusive to another Cyrillic language present (no ы/
    э/ё/і/ї/є/ґ/ў/Serbian letters -- "соль"'s ь no longer excludes it,
    see below), so this specifically exercises the lexical-evidence
    threshold, not the alphabet-exclusion shortcut."""
    assert detect_language("Вода, сахар, соль") == "other"


def test_russian_with_exclusive_cyrillic_letter_is_not_bulgarian():
    assert detect_language("Ингредиенты: Вода, Сахар, Соль, Пшеничная мука") == "other"


def test_ukrainian_is_not_treated_as_bulgarian():
    assert detect_language("Інгредієнти: вода, цукор, сіль") == "other"


def test_bulgarian_text_containing_soft_sign_is_still_classified_as_bulgarian():
    """ь is a valid modern Bulgarian letter (e.g. "шофьор" -- chauffeur)
    and must not, by itself, disqualify Bulgarian classification."""
    assert detect_language("Вода и сол за шофьор") == "bg"
    assert "ь" in "шофьор"


def test_bulgarian_strong_word_is_enough_on_its_own():
    assert detect_language("захар") == "bg"
