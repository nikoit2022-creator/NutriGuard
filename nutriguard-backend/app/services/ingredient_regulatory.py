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
"""
import re

from app.models.enums import ApprovalStatus

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
