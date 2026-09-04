"""
Unit tests for `IngredientOut`'s additive data-quality fields (task:
"backend-ingredient-profile-data-quality") -- serialization shape,
camelCase naming, and that the new fields never fabricate a value for
data that was never actually confirmed. Complements
`test_ingredient_regulatory.py` (the pure derivation logic) and
`test_ocr_normalizer.py` (the synthetic-ingredient construction).
"""
from app.models.enums import RiskLevel
from app.schemas.ingredient import IngredientOut
from app.services.ocr_normalizer import create_synthetic_ingredient


def _curated_kwargs(**overrides) -> dict:
    """A complete, curated (Ingredient-row-shaped) IngredientOut payload
    -- mirrors a real seeded scientific entry, e.g. aspartame."""
    base = dict(
        id="e951_aspartame",
        common_name="Aspartame",
        scientific_name="L-alpha-aspartyl-L-phenylalanine methyl ester",
        e_number="E951",
        category="Artificial Sweetener",
        description="High-intensity artificial sweetener.",
        purpose_in_food="Non-nutritive intense sweetener.",
        health_concerns="IARC classified as possibly carcinogenic (Group 2B).",
        evidence_level="Moderate Evidence",
        countries_restricted_or_banned="Warning labels required in EU & USA for PKU.",
        efsa_status="Authorized (ADI 40 mg/kg bw/day)",
        fda_status="Approved as General Purpose Sweetener",
        who_iarc_classification="Group 2B - Possibly Carcinogenic",
        acceptable_daily_intake="0 - 40 mg/kg bw/day",
        side_effects="Headaches in sensitive individuals",
        allergens="Contains Phenylalanine",
        references="WHO IARC Monograph Vol 134 (2023)",
        risk_level=RiskLevel.POTENTIAL_CONCERN,
        risk_assessment_available=True,
        is_gluten=False,
        is_lactose=False,
        is_vegan=True,
        is_vegetarian=True,
        is_halal=True,
        is_kosher=True,
        bad_for_diabetes=True,
        bad_for_hypertension=False,
        bad_for_kidney_disease=False,
        bad_for_gout=False,
        bad_for_pregnancy=True,
        bad_for_children=True,
        bad_for_high_cholesterol=False,
    )
    base.update(overrides)
    return base


def test_curated_ingredient_out_reports_a_real_available_assessment():
    out = IngredientOut(**_curated_kwargs())
    dumped = out.model_dump(by_alias=True)

    assert dumped["riskAssessmentAvailable"] is True
    assert dumped["riskRationale"] == "Moderate Evidence"
    assert dumped["efsaApprovalStatus"] == "APPROVED"
    assert dumped["fdaApprovalStatus"] == "APPROVED"
    assert dumped["adiMinMgPerKgBwPerDay"] == 0.0
    assert dumped["adiMaxMgPerKgBwPerDay"] == 40.0
    assert dumped["adiSource"] == "WHO IARC Monograph Vol 134 (2023)"

    # Old fields untouched -- backward compatible for existing clients.
    assert dumped["riskLevel"] == "POTENTIAL_CONCERN"
    assert dumped["efsaStatus"] == "Authorized (ADI 40 mg/kg bw/day)"
    assert dumped["acceptableDailyIntake"] == "0 - 40 mg/kg bw/day"


def test_ingredient_with_no_verified_regulatory_data_reports_no_information_not_approved():
    """EFSA/FDA 'unknown' must never silently become 'approved' -- task
    requirement 6."""
    out = IngredientOut(**_curated_kwargs(efsa_status="", fda_status="", acceptable_daily_intake=""))
    dumped = out.model_dump(by_alias=True)

    assert dumped["efsaApprovalStatus"] == "NO_INFORMATION"
    assert dumped["fdaApprovalStatus"] == "NO_INFORMATION"
    assert dumped["adiMinMgPerKgBwPerDay"] is None
    assert dumped["adiMaxMgPerKgBwPerDay"] is None
    assert dumped["adiSource"] is None


def test_unassessed_synthetic_ingredient_out_never_fabricates_a_rationale():
    syn = create_synthetic_ingredient("Sodium Nitrite")
    out = IngredientOut.model_validate(syn)
    dumped = out.model_dump(by_alias=True)

    assert dumped["riskLevel"] == "SAFE"
    assert dumped["riskAssessmentAvailable"] is False
    assert dumped["riskRationale"] is None
    assert dumped["efsaApprovalStatus"] == "NO_INFORMATION"
    assert dumped["fdaApprovalStatus"] == "NO_INFORMATION"
    assert dumped["adiMinMgPerKgBwPerDay"] is None
    assert dumped["adiMaxMgPerKgBwPerDay"] is None
    assert dumped["adiSource"] is None


def test_ingredient_out_field_set_is_purely_additive():
    """Every field an old client already parses is still present and
    unchanged in shape -- only new keys were added."""
    original_camel_fields = {
        "id", "commonName", "scientificName", "eNumber", "category", "description",
        "purposeInFood", "healthConcerns", "evidenceLevel", "countriesRestrictedOrBanned",
        "efsaStatus", "fdaStatus", "whoIarcClassification", "acceptableDailyIntake",
        "sideEffects", "allergens", "references", "riskLevel",
        "isGluten", "isLactose", "isVegan", "isVegetarian", "isHalal", "isKosher",
        "badForDiabetes", "badForHypertension", "badForKidneyDisease", "badForGout",
        "badForPregnancy", "badForChildren", "badForHighCholesterol",
    }
    dumped = IngredientOut(**_curated_kwargs()).model_dump(by_alias=True)
    assert original_camel_fields.issubset(dumped.keys())
    new_fields = {
        "riskAssessmentAvailable", "riskRationale", "efsaApprovalStatus",
        "fdaApprovalStatus", "adiMinMgPerKgBwPerDay", "adiMaxMgPerKgBwPerDay", "adiSource",
    }
    assert dumped.keys() == original_camel_fields | new_fields
