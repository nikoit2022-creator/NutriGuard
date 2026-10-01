"""Conservative recognition of EU "E-number" food-additive identifiers
inside a REFERENCE_SUBSTANCE record's free-text synonym list.

Fixes a confirmed defect in the original extraction: a synonym was only
recognized as an E-number when the text after "E" was entirely
numeric, so explicit letter-suffixed identifiers such as ``E150d``
(caramel colour IV, distinct from E150a/b/c) or roman-numeral-suffixed
identifiers such as ``E 101(i)`` were silently dropped from the
derived view (they still survived in the record's full ``synonyms``
list and in ``raw_fields`` -- this module only affects the *derived*
convenience field, never the raw data).

Survey of every synonym string shaped like an E-number across the full
transferred dataset (276 distinct strings) found these real forms:

- plain, 2-5 digit: ``E 100``, ``E765`` (no space is also real)
- single lowercase letter suffix: ``E 150a`` .. ``E 150d``, ``E 472a``..``f``
- roman-numeral qualifier in parens: ``E 101(i)``, ``E 954(iv)``
- letter *and* roman numeral together: ``E 160a(i)``
- a trailing free-text qualifier: ``E 161(i) (feed)``
- a range spanning two codes: ``E 251-252``

...and confirmed look-alikes that must **not** match: E/Z
stereochemistry descriptors in IUPAC names, e.g. ``E-4-Undecenal``,
``E-5-Decen-1-ol`` (single-digit locant immediately followed by a
hyphen and more text -- rejected by requiring >= 2 digits *and* a full
match against one of the recognized trailing shapes, not a prefix
match).

Design, per the audit's identity-safety rules:

- Never collapse a letter/roman-numeral-suffixed code to its bare
  number (``E150d`` stays ``E150d``, never becomes ``E150``).
- Never silently pick one candidate when a record has more than one
  distinct recognized E-number-shaped synonym -- surface all of them
  and flag the conflict (not observed in the real dataset: checked
  across all 15,705 REFERENCE_SUBSTANCE records, zero had more than
  one; the mechanism exists and is tested for if that ever changes).
- Never invent a split for an ambiguous range -- ``E 251-252`` is kept
  as a single unresolved/ambiguous candidate, not reduced to E251 or
  E252.
- Reject arbitrary words and malformed codes. Recognizing a string as
  E-number-shaped is a syntactic fact about the dataset, not proof of
  current regulatory authorization.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# A single, unambiguous E-number code: 2-5 digits, an optional single
# letter suffix, an optional roman-numeral qualifier in parentheses,
# and an optional trailing free-text qualifier (also in parentheses,
# separated by whitespace, e.g. "E 161(i) (feed)"). Anchored full
# match so a stereodescriptor name like "E-4-Undecenal" (single digit
# immediately followed by a bare hyphen and more text) cannot match.
_SINGLE_RE = re.compile(
    r"""^E[\s-]?
        (?P<code>\d{2,5})
        (?P<suffix>[a-zA-Z])?
        (?:\((?P<roman>i{1,3}|iv)\))?
        (?P<qualifier>\s+\(.+\))?
    $""",
    re.IGNORECASE | re.VERBOSE,
)

# An explicit range, e.g. "E 251-252" -- two codes in one synonym.
# Deliberately not reduced to either endpoint.
_RANGE_RE = re.compile(r"^E[\s-]?(?P<code1>\d{2,5})-(?P<code2>\d{2,5})$", re.IGNORECASE)


@dataclass
class ENumberCandidate:
    raw: str
    recognized: bool
    kind: str  # "single", "range", "unrecognized"
    code: str | None = None
    suffix: str | None = None
    roman: str | None = None
    qualifier: str | None = None
    codes: list[str] = field(default_factory=list)  # populated for kind == "range"
    normalized: str | None = None

    def to_dict(self) -> dict:
        return {
            "raw": self.raw,
            "recognized": self.recognized,
            "kind": self.kind,
            "code": self.code,
            "suffix": self.suffix,
            "roman": self.roman,
            "qualifier": self.qualifier,
            "codes": self.codes,
            "normalized": self.normalized,
        }


def parse_e_number_candidate(text: str) -> ENumberCandidate | None:
    """Return an :class:`ENumberCandidate` if ``text`` is shaped like an
    E-number-ish synonym at all (recognized or not), else ``None`` if it
    plainly isn't (doesn't even start with E/e followed by a digit run
    reachable within the look-alike guard) -- callers should only treat
    something as a "synonym that looked like an E-number but failed
    validation" when this returns a candidate with ``recognized=False``,
    and ignore ordinary chemical-name synonyms entirely (this function
    returns ``None`` for those, e.g. ``"Sodium nitrite"``).
    """
    stripped = text.strip()
    if not re.match(r"^E[\s-]?\d", stripped, re.IGNORECASE):
        return None  # not E-number-shaped at all; an ordinary synonym

    m = _SINGLE_RE.match(stripped)
    if m:
        suffix = m.group("suffix")
        roman = m.group("roman")
        qualifier = m.group("qualifier")
        normalized = "E" + m.group("code") + (suffix.lower() if suffix else "") + (f"({roman.lower()})" if roman else "")
        return ENumberCandidate(
            raw=stripped,
            recognized=True,
            kind="single",
            code=m.group("code"),
            suffix=suffix.lower() if suffix else None,
            roman=f"({roman.lower()})" if roman else None,
            qualifier=qualifier.strip() if qualifier else None,
            normalized=normalized,
        )

    m = _RANGE_RE.match(stripped)
    if m:
        return ENumberCandidate(
            raw=stripped,
            recognized=True,
            kind="range",
            codes=[m.group("code1"), m.group("code2")],
            normalized=f"E{m.group('code1')}-{m.group('code2')}",
        )

    # Looked E-number-shaped (starts "E" + digit) but matched neither
    # the single-code nor the range pattern -- e.g. 1-digit codes, or
    # trailing text beyond a recognized qualifier shape. Surfaced as an
    # explicit unrecognized candidate rather than silently dropped.
    return ENumberCandidate(raw=stripped, recognized=False, kind="unrecognized")


def collect_e_numbers(synonyms: list[str]) -> dict:
    """Scan a REFERENCE_SUBSTANCE record's synonym strings for every
    E-number-shaped candidate and return a structured, conflict-aware
    summary. Never picks a single "best" candidate silently.
    """
    candidates = []
    for s in synonyms:
        c = parse_e_number_candidate(s)
        if c is not None:
            candidates.append(c)

    recognized = [c for c in candidates if c.recognized]
    distinct_normalized = {c.normalized for c in recognized}
    return {
        "candidates": [c.to_dict() for c in candidates],
        "recognized_count": len(recognized),
        "conflict": len(distinct_normalized) > 1,
    }
