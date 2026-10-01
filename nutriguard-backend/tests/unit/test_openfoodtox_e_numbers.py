"""Tests for scripts/openfoodtox/e_numbers.py.

Regression coverage for the confirmed defect: the original extractor
only recognized an E-number when the part after "E" was entirely
numeric, so letter-suffixed codes like E150d were silently dropped
from the derived view. These tests pin the fix using the real shapes
found across the full transferred dataset (plain, letter-suffixed,
roman-numeral-qualified, trailing-qualifier, and range forms), plus
the confirmed look-alikes that must be rejected (E/Z stereochemistry
descriptors in IUPAC names).
"""

from __future__ import annotations

from scripts.openfoodtox.e_numbers import collect_e_numbers, parse_e_number_candidate


class TestPlainNumeric:
    def test_spaced(self):
        c = parse_e_number_candidate("E 250")
        assert c.recognized
        assert c.code == "250"
        assert c.suffix is None
        assert c.normalized == "E250"

    def test_no_space(self):
        c = parse_e_number_candidate("E765")
        assert c.recognized
        assert c.code == "765"

    def test_hyphen_separator(self):
        c = parse_e_number_candidate("E-250")
        assert c.recognized
        assert c.code == "250"

    def test_four_digit(self):
        c = parse_e_number_candidate("E 1201")
        assert c.recognized
        assert c.code == "1201"

    def test_lowercase_e_prefix(self):
        c = parse_e_number_candidate("e 250")
        assert c.recognized
        assert c.normalized == "E250"


class TestLetterSuffixes:
    """The core regression: E150a..d must never collapse to E150."""

    def test_e150_suffixes_are_distinct(self):
        results = {s: parse_e_number_candidate(f"E 150{s}") for s in "abcd"}
        for s, c in results.items():
            assert c.recognized, f"E150{s} should be recognized"
            assert c.code == "150"
            assert c.suffix == s
            assert c.normalized == f"E150{s}"
        normalized = {c.normalized for c in results.values()}
        assert len(normalized) == 4  # all four stay distinct, never collapsed to "E150"

    def test_uppercase_suffix_normalized_to_lowercase(self):
        c = parse_e_number_candidate("E 150D")
        assert c.recognized
        assert c.suffix == "d"
        assert c.normalized == "E150d"

    def test_e472_six_distinct_suffixes(self):
        letters = "abcdef"
        codes = {parse_e_number_candidate(f"E 472{s}").normalized for s in letters}
        assert codes == {f"E472{s}" for s in letters}


class TestRomanNumeralQualifiers:
    def test_simple_roman(self):
        c = parse_e_number_candidate("E 101(i)")
        assert c.recognized
        assert c.code == "101"
        assert c.roman == "(i)"
        assert c.suffix is None

    def test_roman_up_to_iv(self):
        for roman in ["i", "ii", "iii", "iv"]:
            c = parse_e_number_candidate(f"E 954({roman})")
            assert c.recognized, roman
            assert c.roman == f"({roman})"

    def test_letter_suffix_and_roman_numeral_together(self):
        c = parse_e_number_candidate("E 160a(i)")
        assert c.recognized
        assert c.code == "160"
        assert c.suffix == "a"
        assert c.roman == "(i)"
        assert c.normalized == "E160a(i)"
        # Must stay distinct from the bare letter-suffixed form.
        assert c.normalized != parse_e_number_candidate("E 160a").normalized


class TestTrailingQualifier:
    def test_feed_qualifier_recognized_and_preserved(self):
        c = parse_e_number_candidate("E 161(i) (feed)")
        assert c.recognized
        assert c.code == "161"
        assert c.roman == "(i)"
        assert c.qualifier == "(feed)"
        # The original raw string is never discarded.
        assert c.raw == "E 161(i) (feed)"


class TestRangeNotCollapsed:
    def test_range_is_recognized_but_not_reduced_to_one_endpoint(self):
        c = parse_e_number_candidate("E 251-252")
        assert c.recognized
        assert c.kind == "range"
        assert c.codes == ["251", "252"]
        assert c.code is None  # no single code chosen
        assert c.normalized == "E251-252"


class TestMalformedAndLookalikesRejected:
    def test_single_digit_code_is_unrecognized_not_guessed(self):
        c = parse_e_number_candidate("E5")
        assert c is not None
        assert c.recognized is False

    def test_stereodescriptor_names_never_match(self):
        """E/Z stereochemistry descriptors in IUPAC names look
        superficially like E-numbers (start with "E" + digit) but must
        never be recognized as food-additive identifiers."""
        lookalikes = [
            "E-4-Undecenal",
            "E-5-Decen-1-ol",
            "E-5-Decen-1-yl Acetate",
            "E-5-Decen-1-yl acetate and E-5-Decen-1-ol mixture",
            "E-3-benzo[1,3]dioxol-5-yl-N,N-diphenyl-2-propenamide",
        ]
        for name in lookalikes:
            c = parse_e_number_candidate(name)
            assert c is None or c.recognized is False, name

    def test_ordinary_chemical_name_returns_none(self):
        assert parse_e_number_candidate("Sodium nitrite") is None
        assert parse_e_number_candidate("1-Propyl-1-[2-(2,4,6 trichlorophenoxy)ethyl]urea") is None

    def test_arbitrary_word_starting_with_e_but_no_digit_returns_none(self):
        assert parse_e_number_candidate("Ethanol") is None

    def test_multi_letter_suffix_rejected(self):
        c = parse_e_number_candidate("E150xyz")
        assert c is not None
        assert c.recognized is False


class TestCollectENumbers:
    def test_single_recognized_candidate_no_conflict(self):
        result = collect_e_numbers(["Sodium nitrite", "E 250", "RF-00000294-ADD"])
        assert result["recognized_count"] == 1
        assert result["conflict"] is False
        assert result["candidates"][0]["normalized"] == "E250"

    def test_no_candidates(self):
        result = collect_e_numbers(["Sodium nitrite", "Some other synonym"])
        assert result["recognized_count"] == 0
        assert result["conflict"] is False
        assert result["candidates"] == []

    def test_distinct_suffixed_candidates_flagged_as_conflict(self):
        """If a record ever carries two different recognized E-number
        candidates (not observed for real in this dataset -- checked
        across all 15,705 REFERENCE_SUBSTANCE records -- but not
        schema-forbidden), the conflict must be flagged, never silently
        resolved by picking the first one."""
        result = collect_e_numbers(["E 150a", "E 150b"])
        assert result["recognized_count"] == 2
        assert result["conflict"] is True

    def test_same_candidate_repeated_is_not_a_conflict(self):
        result = collect_e_numbers(["E 250", "E 250"])
        assert result["recognized_count"] == 2
        assert result["conflict"] is False

    def test_unrecognized_candidate_included_but_does_not_trigger_conflict(self):
        result = collect_e_numbers(["E 250", "E5"])
        assert result["recognized_count"] == 1
        assert result["conflict"] is False
        kinds = {c["recognized"] for c in result["candidates"]}
        assert kinds == {True, False}
