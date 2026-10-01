"""Tests for scripts/openfoodtox/chemical_basis.py.

Covers the E250 verification request: IUCLID's ToxRefValues schema has
no dedicated "chemical basis" field, so this module recovers it
conservatively from the record's own justification text, tied to the
specific stored numeric value, and never silently converts or treats
a second mentioned figure (e.g. "0.07 mg nitrite ion/kg bw") as
equivalent to the primary one.
"""

from __future__ import annotations

from scripts.openfoodtox.chemical_basis import extract_chemical_basis

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
        result = extract_chemical_basis("0.1", SODIUM_NITRITE_JUSTIFICATION)
        assert result.status == "resolved"
        assert result.basis == "sodium nitrite"
        assert "0.1 mg sodium nitrite/kg bw" in result.evidence

    def test_other_mentioned_value_preserved_separately_not_merged(self):
        """The 0.07 mg nitrite ion/kg bw figure must never be silently
        treated as equivalent to the primary 0.1 mg sodium nitrite/kg bw
        value -- it is kept as a distinct, clearly separate mention."""
        result = extract_chemical_basis("0.1", SODIUM_NITRITE_JUSTIFICATION)
        assert len(result.other_values_mentioned) == 1
        other = result.other_values_mentioned[0]
        assert other["value"] == "0.07"
        assert other["basis"] == "nitrite ion"
        # The primary resolved basis must stay distinct from the other one.
        assert result.basis != other["basis"]

    def test_case_insensitive_and_whitespace_tolerant(self):
        text = "an ADI of 2.5 MG   Potassium Sorbate / KG BW per day was derived"
        result = extract_chemical_basis("2.5", text)
        assert result.status == "resolved"
        assert result.basis.lower() == "potassium sorbate"


class TestUnresolved:
    def test_no_justification_text_is_unresolved(self):
        result = extract_chemical_basis("0.1", None)
        assert result.status == "unresolved_no_mention"
        assert result.basis is None

    def test_no_value_is_unresolved(self):
        result = extract_chemical_basis(None, SODIUM_NITRITE_JUSTIFICATION)
        assert result.status == "unresolved_no_mention"

    def test_justification_text_with_no_basis_mention_is_unresolved(self):
        text = "Remarks: The Panel derived this value using a standard uncertainty factor."
        result = extract_chemical_basis("0.1", text)
        assert result.status == "unresolved_no_mention"
        assert result.basis is None

    def test_mentions_present_but_none_match_stored_value_is_unresolved_with_explanation(self):
        """If the justification text names other mg/.../kg bw figures but
        none of them equals the structured value, the discrepancy must be
        explained (kept visible), never resolved by guessing."""
        result = extract_chemical_basis("0.1", "corresponding to 0.07 mg nitrite ion/kg bw per day")
        assert result.status == "unresolved_no_exact_match"
        assert result.basis is None
        assert len(result.other_values_mentioned) == 1
        assert result.other_values_mentioned[0]["basis"] == "nitrite ion"

    def test_never_fabricates_a_basis_unsuitable_for_consumer_guidance(self):
        """Resolution requires a textual match in the source record; this
        module must never return a basis it did not actually find."""
        result = extract_chemical_basis("5.0", "No reference to a chemical basis anywhere here.")
        assert result.basis is None
        assert result.status.startswith("unresolved")
