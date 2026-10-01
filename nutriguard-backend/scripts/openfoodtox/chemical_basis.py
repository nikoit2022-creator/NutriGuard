"""Conservative, text-grounded chemical-basis extraction for reference
values (ADI/ARfD/AOEL/AAOEL/other).

Why this exists: a IUCLID ``FLEXIBLE_SUMMARY.ToxRefValues`` reference
value stores a bare number and a ``unitCode`` (e.g. ``2085`` ->
"mg/kg bw/day"), but the unit alone does not say *what substance basis*
that mg figure is expressed on. For sodium nitrite's ADI, the EFSA
Panel's own justification text makes this explicit and, in that case,
gives *two* figures on two different bases in the same sentence::

    "...the Panel derived an ADI of 0.1 mg sodium nitrite/kg bw per
    day, corresponding to 0.07 mg nitrite ion/kg bw per day."

The structured ``lowerValue``/``value`` field only ever stores one of
these numbers (0.1 here) -- the schema has no separate "chemical
basis" field at all. This module recovers the basis *from the
record's own justification text*, conservatively: it looks for a
number-plus-basis phrase in the text that matches the stored numeric
value, and reports that phrase as the basis. Any *other* number/basis
pair mentioned in the same text (like the 0.07 mg nitrite ion/kg bw
figure above) is kept as a separate, clearly distinct observation --
never averaged, converted, or treated as equivalent to the primary
value.

This is still "raw IUCLID extraction" (reading the dataset's own
text), not external enrichment. Checking the extracted basis against
the actual cited EFSA opinion is a *separate*, explicitly-labeled
external-verification step -- see
``docs/OPENFOODTOX_DATASET_AUDIT.md`` for that check, done separately
from this module and from the raw catalogue.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# "<number> mg <basis phrase>/kg bw" -- basis phrase is the free text
# between "mg" and "/kg", trimmed. Deliberately narrow: requires the
# literal "mg ... /kg bw" shape actually used in this dataset's
# justification text, not a generic unit-anywhere-in-the-sentence
# search, to avoid matching unrelated numbers in the same paragraph.
_BASIS_MENTION_RE = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*mg\s+(?P<basis>[A-Za-z][A-Za-z \-]*?)\s*/\s*kg\s*bw",
    re.IGNORECASE,
)


@dataclass
class ChemicalBasisResult:
    status: str  # "resolved", "unresolved_no_mention", "unresolved_no_exact_match"
    basis: str | None = None
    evidence: str | None = None  # the exact matched substring
    other_values_mentioned: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "basis": self.basis,
            "evidence": self.evidence,
            "other_values_mentioned": self.other_values_mentioned,
        }


def _values_match(a: str, b: str) -> bool:
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return a.strip() == b.strip()


def extract_chemical_basis(stored_value: str | None, justification_text: str | None) -> ChemicalBasisResult:
    """Determine the chemical basis of ``stored_value`` from the record's
    own justification text. Never guesses when the text doesn't say.
    """
    if not stored_value or not justification_text:
        return ChemicalBasisResult(status="unresolved_no_mention")

    mentions = [
        {"value": m.group("value"), "basis": m.group("basis").strip(), "evidence": m.group(0)}
        for m in _BASIS_MENTION_RE.finditer(justification_text)
    ]
    if not mentions:
        return ChemicalBasisResult(status="unresolved_no_mention")

    primary = None
    others = []
    for m in mentions:
        if primary is None and _values_match(m["value"], stored_value):
            primary = m
        else:
            others.append(m)

    if primary is None:
        # The text mentions basis-qualified figures, but none of them
        # is the number actually stored in the structured field --
        # explain the discrepancy rather than silently picking one.
        return ChemicalBasisResult(
            status="unresolved_no_exact_match",
            other_values_mentioned=others,
        )

    return ChemicalBasisResult(
        status="resolved",
        basis=primary["basis"],
        evidence=primary["evidence"],
        other_values_mentioned=others,
    )
