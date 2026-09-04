"""
Pure unit tests (no DB) for `app.services.ingredient_catalog`'s
standalone functions: INS derivation, verified-data staleness,
negative-cache TTL, and the confidence/source-priority-gated merge
("lower-quality OCR/Gemini data must never overwrite curated or
regulatory information" -- task requirement 3/5). The DB-backed
get-or-create/materialize flow is covered in
`tests/integration/test_ingredient_catalog.py`.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.models.enums import IngredientSource, IngredientVerificationStatus, RiskLevel
from app.services.ingredient_catalog import (
    derive_ins_number_from_e_number,
    is_stale,
    is_within_negative_cache_window,
    merge_verified_fields,
)

NOW = datetime(2026, 9, 4, tzinfo=timezone.utc)


# --- derive_ins_number_from_e_number ---------------------------------------


def test_derive_ins_number_from_plain_e_number():
    assert derive_ins_number_from_e_number("E951") == "951"


def test_derive_ins_number_from_e_number_with_letter_suffix():
    assert derive_ins_number_from_e_number("E160a") == "160A"


def test_derive_ins_number_none_when_no_e_number():
    assert derive_ins_number_from_e_number(None) is None
    assert derive_ins_number_from_e_number("") is None


# --- is_stale ----------------------------------------------------------------


@dataclass
class _FakeIngredient:
    verification_status: IngredientVerificationStatus
    last_verified_at: datetime | None
    retrieved_at: datetime | None = None
    source: IngredientSource = IngredientSource.CURATED_SEED
    confidence: float = 1.0


def test_is_stale_false_for_a_freshly_verified_row():
    row = _FakeIngredient(verification_status=IngredientVerificationStatus.VERIFIED, last_verified_at=NOW)
    assert is_stale(row, now=NOW) is False


def test_is_stale_true_once_past_the_ttl():
    old = NOW - timedelta(seconds=settings.INGREDIENT_VERIFIED_DATA_TTL_SECONDS + 1)
    row = _FakeIngredient(verification_status=IngredientVerificationStatus.VERIFIED, last_verified_at=old)
    assert is_stale(row, now=NOW) is True


def test_is_stale_false_for_an_unverified_row_regardless_of_age():
    old = NOW - timedelta(seconds=settings.INGREDIENT_VERIFIED_DATA_TTL_SECONDS * 10)
    row = _FakeIngredient(verification_status=IngredientVerificationStatus.UNVERIFIED, last_verified_at=old)
    assert is_stale(row, now=NOW) is False


def test_is_stale_false_when_never_verified():
    row = _FakeIngredient(verification_status=IngredientVerificationStatus.VERIFIED, last_verified_at=None)
    assert is_stale(row, now=NOW) is False


# --- is_within_negative_cache_window ----------------------------------------


def test_negative_cache_true_for_a_fresh_unverified_row():
    row = _FakeIngredient(
        verification_status=IngredientVerificationStatus.UNVERIFIED, last_verified_at=None, retrieved_at=NOW
    )
    assert is_within_negative_cache_window(row, now=NOW) is True


def test_negative_cache_false_once_past_the_ttl():
    old = NOW - timedelta(seconds=settings.INGREDIENT_NEGATIVE_CACHE_TTL_SECONDS + 1)
    row = _FakeIngredient(
        verification_status=IngredientVerificationStatus.UNVERIFIED, last_verified_at=None, retrieved_at=old
    )
    assert is_within_negative_cache_window(row, now=NOW) is False


def test_negative_cache_false_for_a_verified_row():
    row = _FakeIngredient(
        verification_status=IngredientVerificationStatus.VERIFIED, last_verified_at=NOW, retrieved_at=NOW
    )
    assert is_within_negative_cache_window(row, now=NOW) is False


# --- merge_verified_fields ---------------------------------------------------


@dataclass
class _MergeableIngredient:
    scientific_name: str = ""
    category: str = ""
    description: str = ""
    purpose_in_food: str = ""
    health_concerns: str = ""
    evidence_level: str = ""
    countries_restricted_or_banned: str = ""
    efsa_status: str = ""
    fda_status: str = ""
    who_iarc_classification: str | None = None
    acceptable_daily_intake: str = ""
    side_effects: str = ""
    references: str = ""
    risk_level: RiskLevel = RiskLevel.SAFE
    source: IngredientSource = IngredientSource.OCR_HEURISTIC
    confidence: float = 0.2
    retrieved_at: datetime | None = None
    verification_status: IngredientVerificationStatus = IngredientVerificationStatus.UNVERIFIED
    last_verified_at: datetime | None = None
    risk_assessment_available: bool = False


def test_lower_priority_source_cannot_overwrite_curated_data():
    """Task requirement 3/5, verification checklist item 4: lower-
    quality OCR/Gemini data must never overwrite curated/regulatory
    information."""
    curated = _MergeableIngredient(
        description="Real curated description.",
        source=IngredientSource.CURATED_SEED,
        confidence=1.0,
    )
    changed = merge_verified_fields(
        curated,
        fields={"description": "some OCR guess"},
        source=IngredientSource.OCR_HEURISTIC,
        confidence=0.9,  # even a HIGH confidence at a lower source rank must not win
    )
    assert changed is False
    assert curated.description == "Real curated description."


def test_higher_priority_source_may_overwrite_lower_priority_data():
    stub = _MergeableIngredient(description="", source=IngredientSource.OCR_HEURISTIC, confidence=0.2)
    changed = merge_verified_fields(
        stub,
        fields={"description": "A regulatory-lookup-confirmed description."},
        source=IngredientSource.REGULATORY_LOOKUP,
        confidence=0.8,
    )
    assert changed is True
    assert stub.description == "A regulatory-lookup-confirmed description."
    assert stub.verification_status == IngredientVerificationStatus.VERIFIED
    assert stub.risk_assessment_available is True
    assert stub.last_verified_at is not None


def test_same_source_lower_confidence_cannot_overwrite():
    row = _MergeableIngredient(
        description="High-confidence answer.", source=IngredientSource.GEMINI, confidence=0.9
    )
    changed = merge_verified_fields(
        row, fields={"description": "Lower-confidence answer."}, source=IngredientSource.GEMINI, confidence=0.5
    )
    assert changed is False
    assert row.description == "High-confidence answer."


def test_same_source_higher_confidence_may_refresh():
    row = _MergeableIngredient(
        description="Old answer.", source=IngredientSource.GEMINI, confidence=0.5
    )
    changed = merge_verified_fields(
        row, fields={"description": "Refreshed, more confident answer."}, source=IngredientSource.GEMINI, confidence=0.95
    )
    assert changed is True
    assert row.description == "Refreshed, more confident answer."
    assert row.confidence == 0.95


def test_merge_ignores_fields_outside_the_mergeable_allowlist():
    row = _MergeableIngredient(source=IngredientSource.OCR_HEURISTIC, confidence=0.1)
    changed = merge_verified_fields(
        row,
        fields={"id": "should-never-be-touched"},
        source=IngredientSource.CURATED_SEED,
        confidence=1.0,
    )
    assert changed is False
