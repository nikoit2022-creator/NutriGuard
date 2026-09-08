"""
Pure, deterministic derivation of structured regulatory fields from the
existing free-text `Ingredient`/`SyntheticIngredient` fields
(`efsa_status`, `fda_status`, `acceptable_daily_intake`).

These are read-time derivations, not stored columns: the free-text
fields remain the single source of truth, so a derived value can never
drift out of sync with the text it was computed from. Every rule here
is intentionally conservative -- see the data-quality task this module
was added for (honest EFSA/FDA/ADI reporting, no fabricated approvals):

  * `derive_approval_status` never infers APPROVED from vague wording
    such as "recognized" or "regulated" -- only from an authority
    actually saying so (authorized/approved/allowed/GRAS), and never
    infers NOT_APPROVED from anything but an explicit ban/prohibition.
    Empty/unrecognized text is NO_INFORMATION, never a guess.
  * `derive_adi_range_mg_per_kg_bw_per_day` only ever returns a numeric
    range when the source text is an unambiguous "<number> mg/kg
    (bw|body weight) ..." or "<number> - <number> mg/kg (bw|body
    weight) ..." expression. Anything else (a percentage-of-calories
    guideline, "No safe ADI established", "Not specified", "Not
    limited", missing text, ...) returns `(None, None)` rather than
    guessing.

`derive_approval_status`/`derive_adi_range_mg_per_kg_bw_per_day` above
are pure TEXT parsing -- they say nothing about whether the row that
text came from is trustworthy enough to present as a real regulatory
fact in the first place. `derive_gated_approval_status`/
`derive_gated_adi_range_mg_per_kg_bw_per_day` add that second gate
(task: "Gate derived EFSA/FDA approval states and numeric ADI fields"):
an EFSA/FDA approval status or a numeric ADI must never be presented on
the wire unless the row is both `VERIFIED` AND its `source` is one of
`app.models.enums.TRUSTED_INGREDIENT_SOURCES` (`CURATED_SEED` or
`REGULATORY_LOOKUP`) -- a Gemini-parsed label or a bare OCR token must
NEVER surface as an "authoritative" approval/ADI, no matter how
confident or well-formatted its text happens to look, since neither is
a regulatory authority. `IngredientOut`/`food_analysis._ingredient_out_dict`
call ONLY the gated versions; the ungated pure functions above remain
for their own direct text-parsing tests and for callers (none in this
codebase yet) that have already established trust some other way.
"""
import re

from app.models.enums import (
    TRUSTED_INGREDIENT_SOURCES,
    ApprovalStatus,
    IngredientSource,
    IngredientVerificationStatus,
)

_NOT_APPROVED_KEYWORDS = ("banned", "prohibited", "not approved", "not permitted")
_APPROVED_KEYWORDS = ("authorized", "authorised", "approved", "allowed", "gras")

_ADI_RANGE_RE = re.compile(
    r"(?P<min>\d+(?:\.\d+)?)\s*-\s*(?P<max>\d+(?:\.\d+)?)\s*mg/kg\s*(?:bw|body\s*weight)",
    re.IGNORECASE,
)
_ADI_SINGLE_RE = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*mg/kg\s*(?:bw|body\s*weight)",
    re.IGNORECASE,
)


def derive_approval_status(status_text: str | None) -> ApprovalStatus:
    """Map a free-text regulatory-status string to a compact status.

    Conservative by design: an explicit ban wins over anything else; an
    explicit authorization/approval/GRAS statement is APPROVED; every
    other case -- including empty text and ambiguous wording like
    "Regulated under Sugars Directive" -- is NO_INFORMATION rather than
    an inferred APPROVED.
    """
    if not status_text:
        return ApprovalStatus.NO_INFORMATION
    lowered = status_text.lower()
    if any(keyword in lowered for keyword in _NOT_APPROVED_KEYWORDS):
        return ApprovalStatus.NOT_APPROVED
    if any(keyword in lowered for keyword in _APPROVED_KEYWORDS):
        return ApprovalStatus.APPROVED
    return ApprovalStatus.NO_INFORMATION


def derive_adi_range_mg_per_kg_bw_per_day(adi_text: str | None) -> tuple[float | None, float | None]:
    """Extract an official numeric ADI range from free text, only when
    the format is an unambiguous "mg/kg bw" (or "mg/kg body weight")
    expression. Never parses a percentage-of-intake guideline or a
    vague qualifier ("Not specified", "No limit", ...) into a number.
    """
    if not adi_text:
        return None, None

    range_match = _ADI_RANGE_RE.search(adi_text)
    if range_match:
        return float(range_match.group("min")), float(range_match.group("max"))

    single_match = _ADI_SINGLE_RE.search(adi_text)
    if single_match:
        value = float(single_match.group("value"))
        return value, value

    return None, None


def is_authoritative_regulatory_source(
    verification_status: IngredientVerificationStatus, source: IngredientSource | None
) -> bool:
    """Task requirement 4's gate, factored out so both derivation
    wrappers below (and any future caller) apply it identically: an
    ingredient row backs a real regulatory claim only when it is
    actually `VERIFIED` -- not `LIMITED_DATA`, not `UNVERIFIED` -- AND
    its `source` is one of `TRUSTED_INGREDIENT_SOURCES`. Both
    conditions matter independently: `VERIFIED` alone isn't enough (a
    row's `verification_status` could, in principle, be misreported by
    a caller that doesn't go through the real DB row -- see
    `IngredientOut`'s own `verification_status` field default), and a
    trusted `source` alone isn't enough either (a row can be freshly
    inserted, not yet actually confirmed).
    """
    return verification_status == IngredientVerificationStatus.VERIFIED and source in TRUSTED_INGREDIENT_SOURCES


def derive_gated_approval_status(
    status_text: str | None,
    *,
    verification_status: IngredientVerificationStatus,
    source: IngredientSource | None,
) -> ApprovalStatus:
    """`derive_approval_status`, gated on the row's own trustworthiness
    (task requirement 4). `NO_INFORMATION` -- never a value parsed from
    `status_text` -- whenever the row isn't `VERIFIED` and
    `CURATED_SEED`/`REGULATORY_LOOKUP`-sourced, even if `status_text`
    itself reads like a real, well-formatted approval statement (e.g. a
    Gemini-parsed label that happens to quote "EU Approved" verbatim
    must never surface as an authoritative EFSA/FDA approval status)."""
    if not is_authoritative_regulatory_source(verification_status, source):
        return ApprovalStatus.NO_INFORMATION
    return derive_approval_status(status_text)


def derive_gated_adi_range_mg_per_kg_bw_per_day(
    adi_text: str | None,
    *,
    verification_status: IngredientVerificationStatus,
    source: IngredientSource | None,
) -> tuple[float | None, float | None]:
    """`derive_adi_range_mg_per_kg_bw_per_day`, gated exactly like
    `derive_gated_approval_status` above (task requirement 4): `(None,
    None)` whenever the row isn't `VERIFIED` and
    `CURATED_SEED`/`REGULATORY_LOOKUP`-sourced, regardless of whether
    `adi_text` itself parses as a clean numeric figure."""
    if not is_authoritative_regulatory_source(verification_status, source):
        return None, None
    return derive_adi_range_mg_per_kg_bw_per_day(adi_text)
