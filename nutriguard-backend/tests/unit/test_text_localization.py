from app.services.text_localization import (
    clean_optional,
    clean_required,
    is_placeholder,
    safe_slug,
    search_aliases_for,
    to_bilingual_display_text,
)


# -- placeholder handling (requirement: no "null"/"None"/empty strings) ----

def test_is_placeholder_detects_common_junk_values():
    for junk in ("null", "None", "NULL", "  none  ", "N/A", "", "  ", "-", "undefined"):
        assert is_placeholder(junk) is True


def test_is_placeholder_false_for_real_values():
    assert is_placeholder("Aspartame") is False
    assert is_placeholder("Захар") is False


def test_clean_optional_returns_none_for_placeholder_junk():
    assert clean_optional("null") is None
    assert clean_optional("None") is None
    assert clean_optional(None) is None
    assert clean_optional("") is None


def test_clean_optional_returns_trimmed_value_for_real_text():
    assert clean_optional("  Aspartame  ") == "Aspartame"


def test_clean_required_falls_back_on_placeholder_junk():
    assert clean_required("null", fallback="Analyzed Brand") == "Analyzed Brand"
    assert clean_required(None, fallback="Analyzed Brand") == "Analyzed Brand"


def test_clean_required_never_translates_brand_names():
    # Brand names must be preserved verbatim, even if they happen to be a
    # word that also appears in the ingredient-name glossary.
    assert clean_required("Zucker GmbH", fallback="Analyzed Brand") == "Zucker GmbH"


# -- English label -----------------------------------------------------------

def test_english_text_passes_through_unchanged():
    assert to_bilingual_display_text("Aspartame", fallback="X") == "Aspartame"
    assert to_bilingual_display_text("Sodium Benzoate", fallback="X") == "Sodium Benzoate"


# -- Bulgarian label: preserved, not mangled ---------------------------------

def test_bulgarian_text_is_preserved_as_is():
    assert to_bilingual_display_text("Захар", fallback="X") == "Захар"
    assert to_bilingual_display_text("Натриев бензоат", fallback="X") == "Натриев бензоат"


# -- other-language label: translated, never leaked untranslated ------------

def test_german_glossary_terms_are_translated_to_english():
    assert to_bilingual_display_text("Zucker", fallback="X") == "Sugar"
    assert to_bilingual_display_text("Vollmilch Zucker", fallback="X") == "Vollmilch Sugar"


def test_french_glossary_terms_are_translated_to_english():
    assert to_bilingual_display_text("Sucre", fallback="X") == "Sugar"


def test_albanian_glossary_terms_are_translated_to_english():
    assert to_bilingual_display_text("Sheqer", fallback="X") == "Sugar"


def test_unsupported_script_never_leaks_untranslated():
    # Greek, Arabic, and Chinese script names must never reach the client
    # as-is -- there is no deterministic way to translate them, so the
    # safe fallback is used instead of leaking foreign text.
    assert to_bilingual_display_text("ζάχαρη", fallback="Unidentified Ingredient") == "Unidentified Ingredient"
    assert to_bilingual_display_text("سكر", fallback="Unidentified Ingredient") == "Unidentified Ingredient"
    assert to_bilingual_display_text("糖", fallback="Unidentified Ingredient") == "Unidentified Ingredient"


# -- "null"/"None" placeholders never become the displayed value ------------

def test_null_and_none_strings_use_the_fallback_not_the_literal_text():
    assert to_bilingual_display_text("null", fallback="Unidentified Ingredient") == "Unidentified Ingredient"
    assert to_bilingual_display_text("None", fallback="Unidentified Ingredient") == "Unidentified Ingredient"
    assert to_bilingual_display_text("", fallback="Unidentified Ingredient") == "Unidentified Ingredient"
    assert to_bilingual_display_text(None, fallback="Unidentified Ingredient") == "Unidentified Ingredient"


# -- E-number search aliases (Bulgarian -> English canonical search term) ---

def test_bulgarian_ingredient_word_resolves_to_english_search_alias():
    assert "Aspartame" in search_aliases_for("аспартам")
    assert "Sodium Benzoate" in search_aliases_for("натриев бензоат")


def test_german_ingredient_word_resolves_to_english_search_alias():
    assert "Sugar" in search_aliases_for("Zucker")


def test_english_token_has_no_extra_aliases():
    assert search_aliases_for("Aspartame") == []


# -- synthetic ingredient id slugs stay unique for non-Latin names ----------

def test_safe_slug_ascii_name_unchanged_behavior():
    assert safe_slug("Sodium Nitrite", fallback_prefix=None) == "sodium_nitrite"


def test_safe_slug_falls_back_to_e_number_for_non_ascii_name():
    assert safe_slug("ζάχαρη", fallback_prefix="E951") == "e951"


def test_safe_slug_falls_back_to_stable_hash_when_nothing_else_available():
    slug_a = safe_slug("糖", fallback_prefix=None)
    slug_b = safe_slug("糖", fallback_prefix=None)
    slug_other = safe_slug("塩", fallback_prefix=None)
    assert slug_a == slug_b  # deterministic
    assert slug_a != slug_other  # distinct inputs don't collide
    assert slug_a
