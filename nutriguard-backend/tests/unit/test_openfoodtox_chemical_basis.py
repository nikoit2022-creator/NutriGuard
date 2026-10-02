"""Tests for scripts/openfoodtox/chemical_basis.py.

Covers the E250 verification request (basis recovery from the record's
own justification text) and the follow-up code-review hardening
(``docs/OPENFOODTOX_REVIEW_TASK.md`` "Active follow-up: chemical-basis
ambiguity"): ambiguity must never be silently resolved by picking the
first matching mention, numeric matching must use exact decimal
comparison with real token boundaries (no decimal-comma/scientific-
notation partial matches), and a mention is only usable evidence when
the stored value's own unit is confirmed to be in the "mg/kg bw"
family this module understands.
"""

from __future__ import annotations

from scripts.openfoodtox.chemical_basis import extract_chemical_basis

MG_KG_BW_DAY = "mg/kg bw/day"

SODIUM_NITRITE_JUSTIFICATION = (
    "Remarks: The Panel concluded that an increased methaemoglobin level, observed in "
    "human and animals, was a relevant effect for the derivation of the ADI. Using the "
    "lowest BMDL of 9.63 mg/kg bw per day, and applying the default UF of 100, the Panel "
    "derived an ADI of 0.1 mg sodium nitrite/kg bw per day, corresponding to 0.07 mg "
    "nitrite ion/kg bw per day. The exposure to nitrite resulting from its use as food "
    "additive did not exceed this ADI for the general population, except for a slight "
    "exceedance in children at the highest percentile."
)


class TestResolvedBasis:
    def test_sodium_nitrite_adi_resolves_to_sodium_nitrite_basis(self):
        result = extract_chemical_basis("0.1", SODIUM_NITRITE_JUSTIFICATION, MG_KG_BW_DAY)
        assert result.status == "resolved"
        assert result.basis == "sodium nitrite"
        assert "0.1 mg sodium nitrite/kg bw" in result.evidence

    def test_other_mentioned_value_preserved_separately_not_merged(self):
        """The 0.07 mg nitrite ion/kg bw figure must never be silently
        treated as equivalent to the primary 0.1 mg sodium nitrite/kg bw
        value -- it is kept as a distinct, clearly separate mention."""
        result = extract_chemical_basis("0.1", SODIUM_NITRITE_JUSTIFICATION, MG_KG_BW_DAY)
        assert len(result.other_values_mentioned) == 1
        other = result.other_values_mentioned[0]
        assert other["value"] == "0.07"
        assert other["basis"] == "nitrite ion"
        assert result.basis != other["basis"]

    def test_case_insensitive_and_whitespace_tolerant(self):
        text = "an ADI of 2.5 MG   Potassium Sorbate / KG BW per day was derived"
        result = extract_chemical_basis("2.5", text, MG_KG_BW_DAY)
        assert result.status == "resolved"
        assert result.basis.lower() == "potassium sorbate"

    def test_unit_label_whitespace_and_day_suffix_tolerant(self):
        assert extract_chemical_basis("0.1", "0.1 mg X/kg bw", " mg/kg bw ").status == "resolved"
        assert extract_chemical_basis("0.1", "0.1 mg X/kg bw", "MG/KG BW/DAY").status == "resolved"


class TestDuplicateSameBasisMentions:
    def test_repeated_identical_mentions_still_resolve(self):
        text = "0.1 mg sodium nitrite/kg bw was found. Elsewhere: 0.1 mg sodium nitrite/kg bw again."
        result = extract_chemical_basis("0.1", text, MG_KG_BW_DAY)
        assert result.status == "resolved"
        assert result.basis == "sodium nitrite"
        assert len(result.matching_value_mentions) == 2

    def test_same_basis_different_case_and_whitespace_still_resolves(self):
        text = "0.1 mg Sodium   Nitrite/kg bw and also 0.1 mg sodium nitrite/kg bw"
        result = extract_chemical_basis("0.1", text, MG_KG_BW_DAY)
        assert result.status == "resolved"
        assert len(result.matching_value_mentions) == 2


class TestAmbiguousMultipleBases:
    """Regression test for the confirmed review finding: two distinct
    bases both matching the stored value must never resolve to the
    first one found."""

    def test_distinct_bases_same_value_is_ambiguous_not_resolved(self):
        text = "0.1 mg sodium nitrite/kg bw and 0.1 mg potassium nitrite/kg bw"
        result = extract_chemical_basis("0.1", text, MG_KG_BW_DAY)
        assert result.status == "ambiguous_multiple_bases"
        assert result.basis is None
        assert result.evidence is None
        bases = {m["basis"] for m in result.matching_value_mentions}
        assert bases == {"sodium nitrite", "potassium nitrite"}

    def test_ambiguous_case_preserves_all_original_evidence(self):
        text = "0.1 mg sodium nitrite/kg bw and 0.1 mg potassium nitrite/kg bw"
        result = extract_chemical_basis("0.1", text, MG_KG_BW_DAY)
        evidences = {m["evidence"] for m in result.matching_value_mentions}
        assert "0.1 mg sodium nitrite/kg bw" in evidences
        assert "0.1 mg potassium nitrite/kg bw" in evidences

    def test_three_distinct_bases_all_preserved(self):
        text = "0.2 mg A/kg bw, 0.2 mg B/kg bw, 0.2 mg C/kg bw"
        result = extract_chemical_basis("0.2", text, MG_KG_BW_DAY)
        assert result.status == "ambiguous_multiple_bases"
        assert len(result.matching_value_mentions) == 3


class TestUnresolved:
    def test_no_justification_text_is_unresolved(self):
        result = extract_chemical_basis("0.1", None, MG_KG_BW_DAY)
        assert result.status == "unresolved_no_mention"
        assert result.basis is None

    def test_no_value_is_unresolved(self):
        result = extract_chemical_basis(None, SODIUM_NITRITE_JUSTIFICATION, MG_KG_BW_DAY)
        assert result.status == "unresolved_no_mention"

    def test_justification_text_with_no_basis_mention_is_unresolved(self):
        text = "Remarks: The Panel derived this value using a standard uncertainty factor."
        result = extract_chemical_basis("0.1", text, MG_KG_BW_DAY)
        assert result.status == "unresolved_no_mention"
        assert result.basis is None

    def test_mentions_present_but_none_match_stored_value_is_unresolved_with_explanation(self):
        result = extract_chemical_basis("0.1", "corresponding to 0.07 mg nitrite ion/kg bw per day", MG_KG_BW_DAY)
        assert result.status == "unresolved_no_exact_match"
        assert result.basis is None
        assert len(result.other_values_mentioned) == 1
        assert result.other_values_mentioned[0]["basis"] == "nitrite ion"

    def test_never_fabricates_a_basis_unsuitable_for_consumer_guidance(self):
        result = extract_chemical_basis("5.0", "No reference to a chemical basis anywhere here.", MG_KG_BW_DAY)
        assert result.basis is None
        assert result.status.startswith(("unresolved", "ambiguous"))


class TestUnitValidation:
    """A numeric match alone must never prove equivalence across
    incompatible units."""

    def test_no_unit_provided_is_unresolved_even_with_a_textual_match(self):
        result = extract_chemical_basis("0.1", SODIUM_NITRITE_JUSTIFICATION, None)
        assert result.status == "unresolved_unsupported_unit"
        assert result.basis is None

    def test_incompatible_unit_is_unresolved_even_with_a_textual_match(self):
        """E.g. a stored value actually in µg/kg bw/day must not be
        matched against a "mg ... /kg bw" text mention just because the
        bare numbers happen to coincide."""
        result = extract_chemical_basis("0.1", SODIUM_NITRITE_JUSTIFICATION, "µg/kg bw/day")
        assert result.status == "unresolved_unsupported_unit"
        assert result.basis is None

    def test_other_incompatible_units_rejected(self):
        for bad_unit in ["mg/day", "mg/L", "g/kg bw", "mg/kg bw/week", "mg/person/day", ""]:
            result = extract_chemical_basis("0.1", SODIUM_NITRITE_JUSTIFICATION, bad_unit)
            assert result.status == "unresolved_unsupported_unit", bad_unit

    def test_supported_units_accepted(self):
        for ok_unit in ["mg/kg bw", "mg/kg bw/day", "mg / kg bw", "MG/KG BW/DAY"]:
            result = extract_chemical_basis("0.1", SODIUM_NITRITE_JUSTIFICATION, ok_unit)
            assert result.status == "resolved", ok_unit


class TestNumericTokenBoundaries:
    """Exact decimal comparison with real token boundaries: no partial
    matches on decimal-comma, scientific notation, or digit runs that
    are actually part of a larger number."""

    def test_decimal_comma_number_is_not_partially_matched(self):
        """"0,1 mg X/kg bw" (European decimal-comma style, unsupported
        by this parser) must never be read as the substring "0"."""
        text = "a value of 0,1 mg sodium nitrite/kg bw was reported"
        result = extract_chemical_basis("0", text, MG_KG_BW_DAY)
        assert result.status == "unresolved_no_mention"

    def test_scientific_notation_is_not_partially_matched(self):
        text = "a value of 1e-5 mg sodium nitrite/kg bw was reported"
        result = extract_chemical_basis("1", text, MG_KG_BW_DAY)
        assert result.status == "unresolved_no_mention"

    def test_larger_number_is_not_truncated_to_a_trailing_substring(self):
        """"210.1 mg X/kg bw" must be read as the single number 210.1,
        never as "0.1" or "10.1"."""
        text = "a dose of 210.1 mg sodium nitrite/kg bw was administered"
        result_full = extract_chemical_basis("210.1", text, MG_KG_BW_DAY)
        assert result_full.status == "resolved"
        result_substring = extract_chemical_basis("0.1", text, MG_KG_BW_DAY)
        assert result_substring.status == "unresolved_no_exact_match"
        assert all(m["value"] != "0.1" for m in result_substring.other_values_mentioned)

    def test_exact_decimal_equality_not_float_tolerance(self):
        """0.1 and 0.10 are the same decimal value and should match;
        this must hold via exact decimal comparison, not an epsilon
        that could also conflate genuinely distinct small numbers."""
        result = extract_chemical_basis("0.10", "0.1 mg sodium nitrite/kg bw", MG_KG_BW_DAY)
        assert result.status == "resolved"

    def test_genuinely_distinct_small_values_do_not_match(self):
        result = extract_chemical_basis("0.07", "0.1 mg sodium nitrite/kg bw", MG_KG_BW_DAY)
        assert result.status == "unresolved_no_exact_match"
