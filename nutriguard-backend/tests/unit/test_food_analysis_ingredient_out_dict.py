"""
Unit tests for `food_analysis._ingredient_out_dict` -- the "plain-dict
mirror of `IngredientOut`" used for partial-analysis error payloads
(see that function's own docstring). PR #13 review fix ("scientific/
regulatory provenance truthful"): this path duplicates `IngredientOut`'s
own EFSA/FDA/ADI gating logic by hand, so it must independently resolve
each field's TRUE source (`ingredient_catalog.resolve_field_source`)
exactly like the schema does, not the row's bare record-level `source`.
"""
import json

from app.models.enums import IngredientSource, IngredientVerificationStatus, RiskLevel
from app.models.ingredient import Ingredient
from app.services.food_analysis import _ingredient_out_dict


def _curated_row(**overrides) -> Ingredient:
    defaults = dict(
        id="e951_aspartame",
        common_name="Aspartame",
        scientific_name="L-alpha-aspartyl-L-phenylalanine methyl ester",
        e_number="E951",
        category="Artificial Sweetener",
        description="High-intensity artificial sweetener.",
        purpose_in_food="Non-nutritive intense sweetener.",
        health_concerns="IARC classified as possibly carcinogenic (Group 2B).",
        evidence_level="Moderate Evidence",
        countries_restricted_or_banned="",
        efsa_status="Authorized (ADI 40 mg/kg bw/day)",
        fda_status="Approved as General Purpose Sweetener",
        acceptable_daily_intake="0 - 40 mg/kg bw/day",
        side_effects="",
        allergens="Contains Phenylalanine",
        references="WHO IARC Monograph Vol 134 (2023)",
        risk_level=RiskLevel.POTENTIAL_CONCERN,
        risk_assessment_available=True,
        verification_status=IngredientVerificationStatus.VERIFIED,
        source=IngredientSource.REGULATORY_LOOKUP,
        is_gluten=False,
        is_lactose=False,
        is_vegan=True,
        is_vegetarian=True,
        is_halal=True,
        is_kosher=True,
        bad_for_diabetes=False,
        bad_for_hypertension=False,
        bad_for_kidney_disease=False,
        bad_for_gout=False,
        bad_for_pregnancy=False,
        bad_for_children=False,
        bad_for_high_cholesterol=False,
    )
    defaults.update(overrides)
    return Ingredient(**defaults)


def test_untouched_field_with_explicit_lower_trust_provenance_is_never_gated_authoritative():
    """Mirrors the equivalent `IngredientOut` schema-level test: a row
    whose record-level `source`/`verificationStatus` read
    REGULATORY_LOOKUP/VERIFIED (because `description` was genuinely
    regulatory-confirmed) must not make `efsaStatus`/
    `acceptableDailyIntake` -- still, in truth, GEMINI content -- look
    authoritative in this dict-mirror path either."""
    field_provenance_json = json.dumps(
        {
            "description": {
                "source": "REGULATORY_LOOKUP", "confidence": 0.9, "retrievedAt": "2026-01-01T00:00:00+00:00"
            },
            "efsa_status": {"source": "GEMINI", "confidence": 0.8, "retrievedAt": "2025-01-01T00:00:00+00:00"},
            "acceptable_daily_intake": {
                "source": "GEMINI", "confidence": 0.8, "retrievedAt": "2025-01-01T00:00:00+00:00"
            },
        }
    )
    row = _curated_row(field_provenance_json=field_provenance_json)

    result = _ingredient_out_dict(row)

    assert result["efsaApprovalStatus"] == "NO_INFORMATION"
    assert result["adiMinMgPerKgBwPerDay"] is None
    assert result["adiMaxMgPerKgBwPerDay"] is None
    assert result["adiSource"] is None


def test_a_row_that_never_went_through_a_partial_merge_is_unaffected():
    """No `field_provenance_json` at all (a curated/seeded row, or any
    row that predates per-field tracking) -- every field falls back to
    the row's own record-level `source`, exactly as before this fix."""
    row = _curated_row(field_provenance_json=None)

    result = _ingredient_out_dict(row)

    assert result["efsaApprovalStatus"] == "APPROVED"
    assert result["adiMinMgPerKgBwPerDay"] == 0.0
    assert result["adiMaxMgPerKgBwPerDay"] == 40.0
