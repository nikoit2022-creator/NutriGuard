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
record's own justification text*, conservatively: it looks for every
number-plus-basis phrase in the text, and only reports a basis when
exactly one distinct basis is attested for the stored value. Any
*other* number/basis pair mentioned in the same text (like the 0.07 mg
nitrite ion/kg bw figure above) is kept as a separate, clearly
distinct observation -- never averaged, converted, or treated as
equivalent to the resolved value.

This is still "raw IUCLID extraction" (reading the dataset's own
text), not external enrichment. Checking the extracted basis against
the actual cited EFSA opinion is a *separate*, explicitly-labeled
external-verification step -- see
``docs/OPENFOODTOX_DATASET_AUDIT.md`` for that check, done separately
from this module and from the raw catalogue.

Safety properties (hardened 2026-10-01 after a code-review finding --
see ``docs/OPENFOODTOX_REVIEW_TASK.md`` "Active follow-up: chemical-
basis ambiguity"):

- **Ambiguity is never silently resolved.** If two or more mentions
  match the stored value but name *different* bases (e.g. the
  synthetic case ``0.1 mg sodium nitrite/kg bw and 0.1 mg potassium
  nitrite/kg bw`` with a stored value of ``0.1``), the result is
  explicitly ``ambiguous_multiple_bases`` with no basis chosen --
  never "first match wins". Repeated mentions of the *same* basis
  (after conservative whitespace/case normalization) still resolve
  normally; this is not treated as a conflict.
- **Exact decimal comparison, not floating-point tolerance.** Matching
  uses :class:`decimal.Decimal` on the literal digit text, which never
  introduces binary floating-point rounding, rather than an epsilon
  comparison that could in principle equate two distinct small values.
  Numbers that are not plain, unambiguous decimal digits (scientific
  notation, decimal-comma, thousands separators) are never partially
  matched -- bounded number-token lookbehind/lookahead ensures a match
  can only start and end on an actual token boundary, so e.g. a
  decimal-comma number like ``0,1`` can never be silently read as the
  substring ``0``.
- **Units are validated, not assumed.** A mention is only usable as
  matching evidence for the stored value when the caller confirms the
  stored value's own unit is in the "mg/kg bw" family this module's
  text pattern understands (``mg/kg bw`` or ``mg/kg bw/day``, case/
  whitespace-insensitive). If the caller does not pass a unit, or
  passes one outside that family (µg/kg bw/day, mg/day, mg/L, ...),
  the result is explicitly ``unresolved_unsupported_unit`` -- numeric
  equality alone is never treated as proof of a match across units.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

# "<number> mg <basis phrase>/kg bw" -- basis phrase is the free text
# between "mg" and "/kg", trimmed. Deliberately narrow: requires the
# literal "mg ... /kg bw" shape actually used in this dataset's
# justification text, not a generic unit-anywhere-in-the-sentence
# search, to avoid matching unrelated numbers in the same paragraph.
#
# The lookbehind/lookahead around the number group enforce a numeric
# token boundary: a match can only start where it is not preceded by
# a digit/period/comma, or by an exponent sign ("e-"/"E+", as in
# scientific notation) -- so it can never start mid-number -- and the
# digit run must not be followed by a digit, comma, or e/E. Together
# this means a decimal-comma number like "0,1" or scientific notation
# like "1e-5" is never partially matched as a shorter, different
# number -- the whole mention is simply not matched, left as "no
# mention found" rather than silently reading out a substring of it.
_BASIS_MENTION_RE = re.compile(
    r"(?<![\d.,])(?<![eE][+-])(?P<value>\d+(?:\.\d+)?)(?![\d,eE.])\s*mg\s+(?P<basis>[A-Za-z][A-Za-z \-]*?)\s*/\s*kg\s*bw",
    re.IGNORECASE,
)

# Unit labels (post-codebook-decoding) this module's text pattern can
# validly be compared against. Anything else (µg/kg bw/day, mg/day,
# mg/L, mg/kg bw/week, ...) is a different physical quantity and must
# never be matched on number alone.
_SUPPORTED_UNIT_RE = re.compile(r"^mg\s*/\s*kg\s*bw(\s*/\s*day)?$", re.IGNORECASE)

# Statuses:
#   resolved                   -- exactly one distinct basis attested for the stored value
#   ambiguous_multiple_bases   -- 2+ distinct bases attested for the stored value; none chosen
#   unresolved_no_mention      -- no "<number> mg <basis>/kg bw" mention found in the text at all
#   unresolved_no_exact_match  -- mentions found, but none numerically equals the stored value
#   unresolved_unsupported_unit -- stored value's unit is missing or not in the mg/kg bw family


@dataclass
class ChemicalBasisResult:
    status: str
    basis: str | None = None
    evidence: str | None = None  # the exact matched substring backing `basis`, when resolved
    matching_value_mentions: list[dict] = field(default_factory=list)
    other_values_mentioned: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "basis": self.basis,
            "evidence": self.evidence,
            "matching_value_mentions": self.matching_value_mentions,
            "other_values_mentioned": self.other_values_mentioned,
        }


def _normalize_basis(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def _exact_decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _values_match(a: str, b: str) -> bool:
    da, db = _exact_decimal(a), _exact_decimal(b)
    if da is None or db is None:
        return a.strip() == b.strip()
    return da == db


def _unit_supported(unit_label: str | None) -> bool:
    if not unit_label:
        return False
    return bool(_SUPPORTED_UNIT_RE.match(unit_label.strip()))


def extract_chemical_basis(
    stored_value: str | None,
    justification_text: str | None,
    stored_unit_label: str | None = None,
) -> ChemicalBasisResult:
    """Determine the chemical basis of ``stored_value`` from the record's
    own justification text. Never guesses when the text doesn't say,
    and never silently picks a basis when more than one is attested.

    ``stored_unit_label`` must be the *decoded* unit label for
    ``stored_value`` (e.g. ``"mg/kg bw/day"``), not a raw IUCLID unit
    code -- callers that have not decoded the unit (no codebook
    available) should leave it as ``None``, which is treated as an
    unsupported/unvalidated unit, not as "assume mg/kg bw".
    """
    if not stored_value or not justification_text:
        return ChemicalBasisResult(status="unresolved_no_mention")

    if not _unit_supported(stored_unit_label):
        return ChemicalBasisResult(status="unresolved_unsupported_unit")

    mentions = [
        {"value": m.group("value"), "basis": m.group("basis").strip(), "evidence": m.group(0)}
        for m in _BASIS_MENTION_RE.finditer(justification_text)
    ]
    if not mentions:
        return ChemicalBasisResult(status="unresolved_no_mention")

    matching = []
    others = []
    for m in mentions:
        (matching if _values_match(m["value"], stored_value) else others).append(m)

    if not matching:
        # The text mentions basis-qualified figures, but none of them
        # is the number actually stored in the structured field --
        # explain the discrepancy rather than silently picking one.
        return ChemicalBasisResult(status="unresolved_no_exact_match", other_values_mentioned=others)

    distinct_bases = {_normalize_basis(m["basis"]) for m in matching}
    if len(distinct_bases) > 1:
        # Two or more different chemical bases both numerically match
        # the stored value -- there is no textual evidence selecting
        # one over the other. Never pick the first one silently.
        return ChemicalBasisResult(
            status="ambiguous_multiple_bases",
            matching_value_mentions=matching,
            other_values_mentioned=others,
        )

    # Exactly one distinct basis (possibly repeated, e.g. the same
    # figure restated with different capitalization/whitespace) --
    # resolve to it, citing the first occurrence as evidence while
    # preserving every matching mention found.
    return ChemicalBasisResult(
        status="resolved",
        basis=matching[0]["basis"],
        evidence=matching[0]["evidence"],
        matching_value_mentions=matching,
        other_values_mentioned=others,
    )
