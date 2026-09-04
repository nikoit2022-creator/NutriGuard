import enum


class RiskLevel(str, enum.Enum):
    """Mirrors com.example.data.model.RiskLevel"""
    SAFE = "SAFE"
    MODERATE = "MODERATE"
    POTENTIAL_CONCERN = "POTENTIAL_CONCERN"
    HIGH_CONCERN = "HIGH_CONCERN"


class ApprovalStatus(str, enum.Enum):
    """Compact, structured regulatory-approval status for a single
    authority (EFSA, FDA, ...), derived from that authority's free-text
    status field -- never fabricated from vague wording. See
    `app.services.ingredient_regulatory.derive_approval_status`."""
    APPROVED = "APPROVED"
    NOT_APPROVED = "NOT_APPROVED"
    NO_INFORMATION = "NO_INFORMATION"


class IngredientVerificationStatus(str, enum.Enum):
    """Whether an `Ingredient` catalog row is real curated/regulatory
    data or just a minimal observation record (see
    `app.services.ingredient_catalog`). Never inferred from keywords --
    set explicitly wherever a row is created or promoted.

    VERIFIED: a curated/seeded or otherwise human/regulatory-confirmed
    record -- its scientific/regulatory fields are real.
    LIMITED_DATA: partially confirmed (e.g. an official identifier or a
    provider-supplied name is known, but the scientific/regulatory
    fields are not yet curated) -- reserved for future use by a real
    external-lookup integration; nothing in this codebase produces it
    yet (see `ingredient_catalog`'s module docstring).
    UNVERIFIED: a bare observation from OCR/Gemini text with no
    scientific-database match at all -- every scientific/regulatory
    field is empty, `riskLevel` is the neutral SAFE placeholder, and it
    must never move the Health Score (see `IngredientOut.risk_assessment_available`,
    which is `True` only for VERIFIED).
    """
    VERIFIED = "VERIFIED"
    LIMITED_DATA = "LIMITED_DATA"
    UNVERIFIED = "UNVERIFIED"


class IngredientSource(str, enum.Enum):
    """Where an `Ingredient`/`IngredientAlias` row's data came from --
    also the priority ranking used to decide whether new data is
    allowed to overwrite what's already stored (see
    `app.services.ingredient_catalog.SOURCE_PRIORITY`/`merge_verified_fields`:
    lower-priority/lower-confidence data must never overwrite
    higher-priority/higher-confidence data)."""
    CURATED_SEED = "CURATED_SEED"
    REGULATORY_LOOKUP = "REGULATORY_LOOKUP"
    GEMINI = "GEMINI"
    OCR_HEURISTIC = "OCR_HEURISTIC"


class WarningSeverity(str, enum.Enum):
    """Mirrors com.example.util.WarningSeverity"""
    HIGH = "HIGH"
    MODERATE = "MODERATE"
    INFO = "INFO"


class ScanType(str, enum.Enum):
    """Mirrors the scanType string values used in ScanHistoryEntity"""
    BARCODE = "BARCODE"
    OCR_LABEL = "OCR_LABEL"
    MANUAL_INPUT = "MANUAL_INPUT"


class Platform(str, enum.Enum):
    ANDROID = "ANDROID"
    IOS = "IOS"
