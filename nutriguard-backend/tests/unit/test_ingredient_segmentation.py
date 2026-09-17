"""
`app.services.ingredient_segmentation.detect_ambiguous_segmentation` --
pure, deterministic, offline (no DB, no network). Pins the exact
real-world Romanian-label examples from the task requirement plus the
module's own documented edge cases.
"""
import pytest

from app.services.ingredient_segmentation import detect_ambiguous_segmentation


# --- The exact real-world examples the task requires to be flagged ---------


@pytest.mark.parametrize(
    "token,expected_reason",
    [
        # An embedded ALL-CAPS EU-allergen-emphasis word merged into an
        # otherwise normal-case clause.
        ("SECARA agenți de creștere", "EMBEDDED_ALLERGEN_EMPHASIS_MERGE"),
        ("Produs din GRAU", "EMBEDDED_ALLERGEN_EMPHASIS_MERGE"),
        ("ZARA pudră", "EMBEDDED_ALLERGEN_EMPHASIS_MERGE"),
        # Two clauses merged around a shared allergen word -- the
        # EMBEDDED_ALLERGEN_EMPHASIS_MERGE check (checked first) also
        # fires here, since "LAPTE" is repeated in ALL CAPS alongside
        # lowercase words -- both reason codes are "unreliable", the
        # important thing is that it IS flagged, non-None.
        ("LAPTE proteină din LAPTE", "EMBEDDED_ALLERGEN_EMPHASIS_MERGE"),
    ],
)
def test_real_world_ambiguous_examples_are_flagged(token, expected_reason):
    reason = detect_ambiguous_segmentation(token)
    assert reason is not None, f"{token!r} must be flagged as ambiguous, was not"
    assert reason == expected_reason


@pytest.mark.parametrize("token", ["SECARA agenți de creștere", "Produs din GRAU", "ZARA pudră", "LAPTE proteină din LAPTE"])
def test_real_world_ambiguous_examples_all_return_a_non_none_reason(token):
    """Restates the task requirement directly: every one of the four
    exact examples must be flagged (non-`None`), regardless of which
    specific reason code applies."""
    assert detect_ambiguous_segmentation(token) is not None


# --- The exact real-world examples the task requires to NOT be flagged -----


@pytest.mark.parametrize("token", ["Ulei de rapiță", "Semințe de mac", "Aluat acrisor"])
def test_real_world_clean_examples_are_not_flagged(token):
    assert detect_ambiguous_segmentation(token) is None


# --- Reason-code-specific coverage ------------------------------------------


def test_duplicate_significant_word_without_all_caps_is_flagged_duplicate_fragment():
    """Two clauses merged around a shared word, with NO capitalization
    switch, must still be caught -- via DUPLICATE_TOKEN_FRAGMENT rather
    than EMBEDDED_ALLERGEN_EMPHASIS_MERGE."""
    reason = detect_ambiguous_segmentation("ulei ulei de rapiță floarea soarelui")
    assert reason == "DUPLICATE_TOKEN_FRAGMENT"


def test_short_connector_words_repeated_do_not_count_as_duplicates():
    """Connector words under 4 letters ("de", "din", "cu", "and", "or")
    are explicitly excluded from the duplicate-word check -- repeating
    them alone must not falsely flag an otherwise normal ingredient
    clause."""
    assert detect_ambiguous_segmentation("Ulei de floarea de soarelui") is None


def test_seven_or_more_words_with_no_dup_or_caps_is_flagged_long_unsegmented_clause():
    token = "zahar sare amidon lecitina aroma naturala citrice"
    assert len(token.split()) == 7
    assert detect_ambiguous_segmentation(token) == "LONG_UNSEGMENTED_CLAUSE"


def test_six_words_with_no_dup_or_caps_is_not_flagged():
    """One word short of the LONG_UNSEGMENTED_CLAUSE threshold -- must
    not be flagged, pinning the exact boundary."""
    token = "zahar sare amidon lecitina aroma naturala"
    assert len(token.split()) == 6
    assert detect_ambiguous_segmentation(token) is None


def test_whole_token_all_caps_is_not_flagged():
    """A capitalization CONVENTION (the entire token shouting) is not
    the EU-allergen-emphasis signal this module looks for -- only a
    capitalization SWITCH within one token is. A label whose entire
    ingredient list happens to be printed in caps must not be
    penalized."""
    assert detect_ambiguous_segmentation("LAPTE PRAF INTEGRAL") is None


# --- Degenerate inputs -------------------------------------------------------


@pytest.mark.parametrize("token", ["", "   ", None])
def test_empty_or_none_input_is_never_flagged(token):
    assert detect_ambiguous_segmentation(token) is None


def test_single_word_is_never_flagged():
    """`_has_embedded_allergen_emphasis` requires at least 2 words --
    a bare single ALL-CAPS word (e.g. a whole ingredient name shouted)
    is a capitalization convention, not a merge."""
    assert detect_ambiguous_segmentation("LAPTE") is None


def test_pure_number_or_e_number_token_is_never_flagged():
    assert detect_ambiguous_segmentation("E330") is None
    assert detect_ambiguous_segmentation("12%") is None
