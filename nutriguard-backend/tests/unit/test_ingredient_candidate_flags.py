"""Issue #23 stage 2: pure token/identity classification. No DB, no network."""
import pytest

from app.services.ingredient_candidate_flags import (
    KEY_MAX_LENGTH,
    NAME_MAX_LENGTH,
    CandidateFlag as F,
    bounded_display_name,
    candidate_key,
    classify_resolution,
    classify_token,
    is_junk,
    parse_flags,
    serialize_flags,
)


@pytest.mark.parametrize(
    "token, expected",
    [
        ("Ingredients could not be extracted from the image", {F.PLACEHOLDER_TEXT}),
        ("AI response was unavailable or invalid", {F.PLACEHOLDER_TEXT}),
        ("1234", {F.NO_LETTERS}),
        ("%%", {F.NO_LETTERS}),
        ("", {F.NO_LETTERS}),
        ("Съставки: вода", {F.HEADER_ARTIFACT}),
        ("Ingredients: water", {F.HEADER_ARTIFACT}),
        ("Colorant: e150d", {F.CLASS_PREFIXED}),
        ("Emulsifier: soy lecithin", {F.CLASS_PREFIXED}),
        ("Оцветител: карамел", {F.CLASS_PREFIXED}),
        ("May contain peanuts", {F.ALLERGEN_STATEMENT}),
        ("Може да съдържа фъстъци", {F.ALLERGEN_STATEMENT}),
        ("Colour", {F.GENERIC_FUNCTION_TERM}),
        ("Ароматизант", {F.GENERIC_FUNCTION_TERM}),
        ("Raising agents (ammonium bicarbonate", {F.UNBALANCED_PARENTHESIS}),
        ("Sugar", set()),
        ("Xylofrobinate", set()),
        ("Dry whey powder", set()),
        ("Фруктозо-глюкозен сироп", set()),
    ],
)
def test_classify_token(token, expected):
    assert set(classify_token(token)) == expected


def test_only_placeholder_and_no_letters_are_junk():
    assert is_junk(classify_token("1234"))
    assert is_junk(classify_token("AI response was unavailable or invalid"))
    for anomaly in ("May contain peanuts", "Colour", "Colorant: e150d", "Съставки: вода", "Sugar ("):
        assert not is_junk(classify_token(anomaly)), anomaly


def test_a_valid_but_unknown_name_carries_no_flag():
    """Unknown != flagged: an ingredient with no catalog entry is not an anomaly."""
    assert classify_token("Xylofrobinate") == frozenset()


def test_too_long_names_are_flagged_and_bounded():
    long_name = "x" * 300
    assert F.TOO_LONG in classify_token(long_name)
    display, truncated = bounded_display_name(long_name)
    assert len(display) == NAME_MAX_LENGTH and truncated
    assert bounded_display_name("Sugar") == ("Sugar", False)


def test_key_is_the_normalized_name_and_is_exact_only():
    assert candidate_key("Sugar") == candidate_key("  sugar,  ") == "sugar"
    # No stemming, no fuzzy match, no translation: each is its own key.
    assert len({candidate_key(t) for t in ("Sugar", "Sugars", "Suger", "Захар", "Sucrose")}) == 5


def test_long_keys_are_bounded_stable_and_distinct():
    a = "a" * 200 + "1"
    b = "a" * 200 + "2"
    assert len(candidate_key(a)) <= KEY_MAX_LENGTH and len(candidate_key(b)) <= KEY_MAX_LENGTH
    assert candidate_key(a) == candidate_key(a)
    assert candidate_key(a) != candidate_key(b)  # same 128-char prefix, different content


def _resolution(**overrides):
    base = dict(
        token_name="Widget gum", token_e_number=None, resolved_name="Widget gum",
        resolved_e_number=None, resolved_identity_uncertain=False,
    )
    return classify_resolution(**{**base, **overrides})


def test_a_plain_exact_resolution_has_no_flag():
    assert _resolution() == frozenset()


def test_conflicting_identifiers_are_flagged_not_resolved():
    assert F.CONFLICTING_IDENTIFIER in _resolution(token_e_number="E300", resolved_e_number="E301")
    assert F.CONFLICTING_IDENTIFIER not in _resolution(token_e_number="e300", resolved_e_number="E300")
    assert F.CONFLICTING_IDENTIFIER not in _resolution(token_e_number=None, resolved_e_number="E301")


@pytest.mark.parametrize(
    "token, resolved, flagged",
    [
        ("Lecithin", "Soy Lecithin", True),            # generic token, specific identity
        ("Sunflower lecithin", "Soy Lecithin", True),  # two different specific sources
        ("Soy lecithin", "Soy Lecithin", False),
        ("Соев лецитин", "Soy Lecithin", False),        # same source, other language
        ("Лецитин", "Soy Lecithin", True),
        ("Palm oil", "Palm oil", False),
        ("Oil", "Palm oil", True),
        ("Sugar", "Sugar", False),
    ],
)
def test_generic_versus_specific_source(token, resolved, flagged):
    result = _resolution(token_name=token, resolved_name=resolved)
    assert (F.GENERIC_VS_SPECIFIC in result) is flagged


def test_uncertain_identity_of_the_resolved_row_is_carried():
    assert F.IDENTITY_UNCERTAIN in _resolution(resolved_identity_uncertain=True)


def test_flag_serialization_is_sorted_closed_and_round_trips():
    flags = {F.NO_LETTERS, F.CLASS_PREFIXED, F.TOO_LONG}
    raw = serialize_flags(flags)
    assert raw == ",".join(sorted(f.value for f in flags))
    assert parse_flags(raw) == frozenset(flags)
    assert parse_flags("NOT_A_FLAG,NO_LETTERS,") == frozenset({F.NO_LETTERS})
    assert parse_flags(None) == frozenset()
    assert len(serialize_flags(set(F))) <= 255  # the whole vocabulary fits the column
