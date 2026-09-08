from app.services.ingredient_normalization import normalize_ingredient_name


def test_case_insensitive():
    assert normalize_ingredient_name("Citric Acid") == normalize_ingredient_name("CITRIC ACID")


def test_collapses_internal_whitespace():
    assert normalize_ingredient_name("Citric   Acid") == normalize_ingredient_name("Citric Acid")


def test_strips_edge_whitespace_and_punctuation():
    assert normalize_ingredient_name("  Citric Acid.  ") == normalize_ingredient_name("Citric Acid")
    assert normalize_ingredient_name("Sugar,") == normalize_ingredient_name("Sugar")


def test_does_not_touch_internal_hyphen_or_meaningful_punctuation():
    assert normalize_ingredient_name("L-alpha-aspartyl") == "l-alpha-aspartyl"


def test_does_not_strip_a_trailing_paren_leaving_its_opening_partner_dangling():
    """Regression: naive edge-punctuation stripping that includes ')'
    would remove only the CLOSING paren of "High Fructose Corn Syrup
    (HFCS)" (nothing follows it), leaving an unbalanced "(hfcs" behind.
    Parens must be either both kept or (never here) both stripped."""
    normalized = normalize_ingredient_name("High Fructose Corn Syrup (HFCS)")
    assert normalized == "high fructose corn syrup (hfcs)"
    assert normalized.count("(") == normalized.count(")")


def test_bulgarian_cyrillic_normalizes_consistently():
    assert normalize_ingredient_name("Лимонена Киселина") == normalize_ingredient_name("лимонена киселина")


def test_empty_and_none_like_input():
    assert normalize_ingredient_name("") == ""


def test_different_ingredients_do_not_collide():
    assert normalize_ingredient_name("Citric Acid") != normalize_ingredient_name("Ascorbic Acid")
