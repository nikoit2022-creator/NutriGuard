"""
`app.services.ingredient_segmentation.detect_ambiguous_segmentation` --
pure, deterministic, offline (no DB, no network).

Pins the exact real-world Romanian-label examples from the task
requirement. IMPORTANT: not all seven of those examples are malformed.
Several are entirely legitimate compound ingredient names that merely
happen to emphasize one allergen word in ALL CAPS (a routine EU-label
typographic convention, Regulation (EU) 1169/2011) -- capitalization
alone is no longer treated as evidence of a broken/concatenated OCR
token (see the module's own docstring for why). Only a genuine
structural signal -- a literal un-split `:` (a real clause-header
pattern tokenization couldn't split on), the same significant word
repeated within one token, or an implausibly long word count -- causes
a flag.
"""
import pytest

from app.services.ingredient_segmentation import detect_ambiguous_segmentation


# --- Clean, ordinary compound ingredient names -- must NOT flag ------------


@pytest.mark.parametrize(
    "token",
    [
        "Ulei de rapiță",  # rapeseed oil -- clean 3-word compound name
        "Semințe de mac",  # poppy seeds -- clean
        "Aluat acrisor",  # sourdough -- clean, 2 words
    ],
)
def test_clean_ordinary_examples_are_not_flagged(token):
    assert detect_ambiguous_segmentation(token) is None


def test_secara_agenti_de_crestere_is_not_flagged():
    """"SECARA agenți de creștere" (RYE raising agents) LOOKS like a
    merged fragment at first glance, but under the new, narrower design
    it is intentionally treated as translatable: no literal colon, no
    duplicate significant word, and only 4 words (well under the
    7-word LONG_UNSEGMENTED_CLAUSE threshold). The only thing "unusual"
    about it is that "SECARA" is capitalized -- ordinary EU allergen-
    emphasis phrasing, not a structural indicator like a colon or a
    repeated word. This is a deliberate change from the old
    (over-broad) capitalization-based heuristic, which wrongly flagged
    this as EMBEDDED_ALLERGEN_EMPHASIS_MERGE."""
    assert detect_ambiguous_segmentation("SECARA agenți de creștere") is None


def test_produs_din_grau_is_not_flagged():
    """"Produs din GRAU" ("product from WHEAT") is a coherent,
    grammatically sensible 3-word compound name -- WHEAT is simply the
    label's emphasized allergen. Must not be flagged."""
    assert detect_ambiguous_segmentation("Produs din GRAU") is None


def test_zara_pudra_is_not_flagged():
    """"ZARA pudră" is an unfamiliar-looking (possibly brand-name)
    2-word phrase with one caps word -- structurally no different from
    "MILK powder". Capitalization alone is never sufficient evidence,
    even for a word that looks unusual/unrecognized, so this must not
    be flagged (explicit task requirement)."""
    assert detect_ambiguous_segmentation("ZARA pudră") is None


def test_milk_powder_is_not_flagged():
    """Explicit task example of ordinary EU-label allergen emphasis --
    a single allergen word in caps inline with an otherwise normal
    ingredient description. Must not be flagged."""
    assert detect_ambiguous_segmentation("MILK powder") is None


# --- Genuinely ambiguous examples -- MUST flag ------------------------------


def test_lapte_proteina_din_lapte_flags_duplicate_token_fragment():
    """"LAPTE proteină din LAPTE" ("MILK protein from MILK") contains
    the SAME significant word ("lapte", case-insensitively) twice --
    real evidence that two adjacent list clauses sharing an allergen
    word were merged into one token. Must still flag, via
    DUPLICATE_TOKEN_FRAGMENT (capitalization is irrelevant to this
    check; the duplication itself is the evidence)."""
    reason = detect_ambiguous_segmentation("LAPTE proteină din LAPTE")
    assert reason == "DUPLICATE_TOKEN_FRAGMENT"


def test_duplicate_significant_word_without_all_caps_is_flagged_duplicate_fragment():
    """Two clauses merged around a shared word, with no capitalization
    involved at all, must still be caught -- via DUPLICATE_TOKEN_FRAGMENT."""
    reason = detect_ambiguous_segmentation("ulei ulei de rapiță floarea soarelui")
    assert reason == "DUPLICATE_TOKEN_FRAGMENT"


def test_short_connector_words_repeated_do_not_count_as_duplicates():
    """Connector words under 4 letters ("de", "din", "cu", "and", "or")
    are explicitly excluded from the duplicate-word check -- repeating
    them alone must not falsely flag an otherwise normal ingredient
    clause."""
    assert detect_ambiguous_segmentation("Ulei de floarea de soarelui") is None


# --- COLON_SEPARATED_CLAUSE_MERGE -------------------------------------------


@pytest.mark.parametrize(
    "token",
    [
        # A real, un-split EU-label clause-header pattern: a
        # functional-class header followed by its own sub-list.
        # `ocr_normalizer.normalize_and_extract_tokens` only splits on
        # `,`/`;`/`.`, never `:`, so this survives tokenization as one
        # token -- genuine structural evidence of a merged clause.
        "agenti de crestere: enzime",
        "Contains: milk, soy",
        "agenti de crestere: enzime (SECARA)",
    ],
)
def test_literal_colon_inside_token_flags_colon_separated_clause_merge(token):
    assert detect_ambiguous_segmentation(token) == "COLON_SEPARATED_CLAUSE_MERGE"


def test_colon_check_wins_over_other_checks_when_multiple_signals_present():
    """A colon-bearing token that also happens to be long/duplicated
    should still report the colon reason -- it's checked first because
    it's the most direct, structural signal."""
    token = "Contains: milk, milk, milk, milk, milk, milk, milk"
    assert detect_ambiguous_segmentation(token) == "COLON_SEPARATED_CLAUSE_MERGE"


# --- LONG_UNSEGMENTED_CLAUSE -------------------------------------------------


def test_seven_or_more_words_with_no_dup_or_colon_is_flagged_long_unsegmented_clause():
    token = "zahar sare amidon lecitina aroma naturala citrice"
    assert len(token.split()) == 7
    assert detect_ambiguous_segmentation(token) == "LONG_UNSEGMENTED_CLAUSE"


def test_six_words_with_no_dup_or_colon_is_not_flagged():
    """One word short of the LONG_UNSEGMENTED_CLAUSE threshold -- must
    not be flagged, pinning the exact boundary."""
    token = "zahar sare amidon lecitina aroma naturala"
    assert len(token.split()) == 6
    assert detect_ambiguous_segmentation(token) is None


# --- Capitalization is never, by itself, evidence ---------------------------


def test_whole_token_all_caps_is_not_flagged():
    """A capitalization CONVENTION (the entire token shouting) carries
    no more information than any other capitalization pattern under the
    new design -- it must not be flagged, same as before."""
    assert detect_ambiguous_segmentation("LAPTE PRAF INTEGRAL") is None


def test_mixed_case_single_caps_word_is_not_flagged():
    """A mix of lowercase and one ALL-CAPS word used to be exactly the
    (removed) capitalization-switch heuristic's trigger condition.
    Under the new design this pattern alone is explicitly not
    evidence."""
    assert detect_ambiguous_segmentation("Ulei de PALMIER rafinat") is None


# --- Degenerate inputs -------------------------------------------------------


@pytest.mark.parametrize("token", ["", "   ", None])
def test_empty_or_none_input_is_never_flagged(token):
    assert detect_ambiguous_segmentation(token) is None


def test_single_word_is_never_flagged():
    assert detect_ambiguous_segmentation("LAPTE") is None


def test_pure_number_or_e_number_token_is_never_flagged():
    assert detect_ambiguous_segmentation("E330") is None
    assert detect_ambiguous_segmentation("12%") is None
