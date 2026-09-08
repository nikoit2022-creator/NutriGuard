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


def test_regulatory_lookup_source_promotes_all_the_way_to_verified():
    stub = _MergeableIngredient(source=IngredientSource.OCR_HEURISTIC, confidence=0.2)
    merge_verified_fields(
        stub,
        fields={"description": "A regulatory-lookup-confirmed description."},
        source=IngredientSource.REGULATORY_LOOKUP,
        confidence=0.9,
    )
    assert stub.verification_status == IngredientVerificationStatus.VERIFIED
    assert stub.risk_assessment_available is True


def test_gemini_source_promotes_only_to_limited_data_never_verified():
    """Incorrect-verification-promotion regression: an AI-generated
    (GEMINI) claim is real content worth storing -- it outranks a bare
    OCR guess -- but it is NOT a human/regulatory confirmation. It must
    never promote a row to VERIFIED or flip `risk_assessment_available`,
    which is what actually lets `riskLevel` start influencing the
    Health Score (see food_analysis._score_and_warnings) -- letting an
    AI-suggested risk assessment move the score is exactly what the
    data-quality task (commit 1d8c3d9) exists to prevent."""
    stub = _MergeableIngredient(source=IngredientSource.OCR_HEURISTIC, confidence=0.2)
    changed = merge_verified_fields(
        stub,
        fields={"description": "An AI-suggested description."},
        source=IngredientSource.GEMINI,
        confidence=0.8,
    )
    assert changed is True
    assert stub.description == "An AI-suggested description."
    assert stub.verification_status == IngredientVerificationStatus.LIMITED_DATA
    assert stub.risk_assessment_available is False


def test_merge_ignores_fields_outside_the_mergeable_allowlist():
    row = _MergeableIngredient(source=IngredientSource.OCR_HEURISTIC, confidence=0.1)
    changed = merge_verified_fields(
        row,
        fields={"id": "should-never-be-touched"},
        source=IngredientSource.CURATED_SEED,
        confidence=1.0,
    )
    assert changed is False


# --- PR #13 review fixes: stale-revalidation + blank-value protection -------


def _stale_curated(**overrides) -> _MergeableIngredient:
    old = NOW - timedelta(seconds=settings.INGREDIENT_VERIFIED_DATA_TTL_SECONDS + 1)
    base = dict(
        description="Real curated description.",
        source=IngredientSource.CURATED_SEED,
        confidence=1.0,
        verification_status=IngredientVerificationStatus.VERIFIED,
        last_verified_at=old,
    )
    base.update(overrides)
    return _MergeableIngredient(**base)


def test_same_rank_same_confidence_revalidation_of_stale_data_succeeds():
    """Task requirement 5a: a trusted provider may revalidate STALE data
    even with confidence unchanged and field values identical -- the OLD
    rule (same rank requires a STRICTLY higher confidence) would reject
    this outright, even though it's the exact same trusted source
    simply confirming its own earlier answer is still correct."""
    curated = _stale_curated()
    changed = merge_verified_fields(
        curated,
        fields={"description": "Real curated description."},  # identical value
        source=IngredientSource.CURATED_SEED,
        confidence=1.0,  # identical confidence
        now=NOW,
    )
    assert changed is True
    assert curated.description == "Real curated description."
    assert curated.source == IngredientSource.CURATED_SEED
    assert curated.confidence == 1.0


def test_stale_revalidation_advances_verification_timestamps():
    """Task requirement 5b: a successful unchanged revalidation still
    advances `last_verified_at`/`retrieved_at` -- "confirmed still
    current as of now" is real provenance even with nothing to change."""
    curated = _stale_curated()
    old_last_verified_at = curated.last_verified_at
    merge_verified_fields(
        curated,
        fields={"description": "Real curated description."},
        source=IngredientSource.CURATED_SEED,
        confidence=1.0,
        now=NOW,
    )
    assert curated.last_verified_at == NOW
    assert curated.last_verified_at != old_last_verified_at
    assert curated.retrieved_at == NOW


def test_regulatory_lookup_may_revalidate_a_stale_curated_row():
    """Task requirement 5d: resolves the contradiction where a curated
    row IS allowed to go stale, yet a REGULATORY_LOOKUP source (row-level
    rank 90) could never revalidate a CURATED_SEED row (rank 100) even
    though both are equally-trusted regulatory-grade sources -- the OLD
    rank gate rejected this unconditionally. `source`/`confidence` stay
    CURATED_SEED/1.0 (the row-level provenance is honest: nothing about
    the CONTENT actually changed, so it must not claim REGULATORY_LOOKUP
    now supplied it)."""
    curated = _stale_curated()
    changed = merge_verified_fields(
        curated,
        fields={"description": "Real curated description."},
        source=IngredientSource.REGULATORY_LOOKUP,
        confidence=0.8,  # lower confidence than the existing 1.0
        now=NOW,
    )
    assert changed is True
    assert curated.source == IngredientSource.CURATED_SEED  # unchanged -- honest provenance
    assert curated.confidence == 1.0  # unchanged
    assert curated.last_verified_at == NOW  # still advances


def test_regulatory_lookup_revalidation_with_genuinely_new_content_updates_provenance():
    """When a stale-revalidation source ALSO brings genuinely different
    content, the row's provenance must honestly reflect that the new
    values actually came from that (possibly lower-ranked) source --
    never keep claiming the old, higher-ranked source for content it
    didn't supply (task requirement 5e: honest per-field provenance)."""
    curated = _stale_curated()
    changed = merge_verified_fields(
        curated,
        fields={"description": "Updated by a fresh regulatory lookup."},
        source=IngredientSource.REGULATORY_LOOKUP,
        confidence=0.8,
        now=NOW,
    )
    assert changed is True
    assert curated.description == "Updated by a fresh regulatory lookup."
    assert curated.source == IngredientSource.REGULATORY_LOOKUP
    assert curated.confidence == 0.8


def test_non_trusted_source_cannot_revalidate_a_stale_curated_row():
    """Staleness only ever opens the door BETWEEN the two regulatory-
    grade sources -- never down to GEMINI/OCR_HEURISTIC, no matter how
    old the existing data is."""
    curated = _stale_curated()
    changed = merge_verified_fields(
        curated,
        fields={"description": "An AI guess."},
        source=IngredientSource.GEMINI,
        confidence=0.99,
        now=NOW,
    )
    assert changed is False
    assert curated.description == "Real curated description."


def test_fresh_not_stale_data_still_requires_the_normal_rank_and_confidence_gate():
    """The new stale-revalidation bypass must never apply to FRESH
    (not-yet-stale) VERIFIED data -- the original protection (task
    requirement 3) is completely unaffected for the common case."""
    fresh_curated = _stale_curated(last_verified_at=NOW)  # NOT stale relative to `now=NOW`
    changed = merge_verified_fields(
        fresh_curated,
        fields={"description": "Real curated description."},  # identical value
        source=IngredientSource.CURATED_SEED,
        confidence=1.0,  # identical confidence
        now=NOW,
    )
    assert changed is False
    assert fresh_curated.last_verified_at == NOW  # untouched, not re-set by this call


def test_blank_incoming_value_never_erases_a_meaningful_existing_value():
    """Task requirement 5c: a partial response's blank/null/placeholder
    field must never erase a meaningful existing value."""
    stub = _MergeableIngredient(
        description="Meaningful curated description.",
        health_concerns="Known real concern.",
        source=IngredientSource.OCR_HEURISTIC,
        confidence=0.2,
    )
    changed = merge_verified_fields(
        stub,
        fields={
            "description": "",  # blank
            "health_concerns": "N/A",  # placeholder
            "purpose_in_food": "A genuinely new value.",  # real -- should still apply
        },
        source=IngredientSource.REGULATORY_LOOKUP,
        confidence=0.9,
    )
    assert changed is True
    assert stub.description == "Meaningful curated description."  # untouched
    assert stub.health_concerns == "Known real concern."  # untouched
    assert stub.purpose_in_food == "A genuinely new value."  # applied


def test_none_incoming_value_never_erases_an_existing_optional_field():
    stub = _MergeableIngredient(
        who_iarc_classification="Group 2B - Possibly Carcinogenic",
        source=IngredientSource.OCR_HEURISTIC,
        confidence=0.2,
    )
    changed = merge_verified_fields(
        stub,
        fields={"who_iarc_classification": None, "description": "A genuinely new value."},
        source=IngredientSource.REGULATORY_LOOKUP,
        confidence=0.9,
    )
    assert changed is True
    assert stub.who_iarc_classification == "Group 2B - Possibly Carcinogenic"
