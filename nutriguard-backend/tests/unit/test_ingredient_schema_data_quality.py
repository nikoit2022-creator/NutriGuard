"""
Unit tests for `IngredientOut`'s additive data-quality fields (task:
"backend-ingredient-profile-data-quality") -- serialization shape,
camelCase naming, and that the new fields never fabricate a value for
data that was never actually confirmed. Complements
`test_ingredient_regulatory.py` (the pure derivation logic) and
`test_ocr_normalizer.py` (the synthetic-ingredient construction).
"""
from app.models.enums import IngredientSource, IngredientVerificationStatus, RiskLevel
from app.schemas.ingredient import IngredientOut
from app.services.ocr_normalizer import create_synthetic_ingredient


def _curated_kwargs(**overrides) -> dict:
    """A complete, curated (Ingredient-row-shaped) IngredientOut payload
    -- mirrors a real seeded scientific entry, e.g. aspartame. Explicit
    `source`/`verificationStatus` (task requirement 4: EFSA/FDA
    approval and ADI are only ever gated TRUE for a `VERIFIED`,
    `CURATED_SEED`/`REGULATORY_LOOKUP`-sourced row -- a real curated
    seed row states both explicitly, never relying on the schema's own
    defaults)."""
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
        verification_status=IngredientVerificationStatus.VERIFIED,
        source=IngredientSource.CURATED_SEED,
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
    unchanged in shape -- only new keys were added, across BOTH the
    "data quality" task and the later "persistent ingredient knowledge
    cache" task."""
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
    data_quality_task_fields = {
        "riskAssessmentAvailable", "riskRationale", "efsaApprovalStatus",
        "fdaApprovalStatus", "adiMinMgPerKgBwPerDay", "adiMaxMgPerKgBwPerDay", "adiSource",
    }
    knowledge_cache_task_fields = {
        "insNumber", "casNumber", "verificationStatus", "source", "sourceRecordId",
        "sourceUrl", "retrievedAt", "lastVerifiedAt", "confidence", "schemaVersion",
        "needsRefresh",
    }
    # PR #13 review fixes: `insNumberVerified` (see `ingredient_regulatory`
    # module docstring -- INS is only ever mechanically derived, never an
    # independently-verified identifier).
    review_fix_fields = {"insNumberVerified"}
    assert (
        dumped.keys()
        == original_camel_fields | data_quality_task_fields | knowledge_cache_task_fields | review_fix_fields
    )


# --- Gated EFSA/FDA/ADI (PR #13 review: requirement 4) ----------------------


def test_gemini_sourced_data_never_surfaces_as_an_authoritative_approval_or_adi():
    """A Gemini-parsed label can quote real-looking regulatory text
    ("Authorized", "0 - 40 mg/kg bw/day") verbatim from a label or its
    own training data -- that must never be presented as an actual
    EFSA/FDA approval or an authoritative ADI, since Gemini is not a
    regulatory authority (task requirement 4)."""
    out = IngredientOut(
        **_curated_kwargs(
            verification_status=IngredientVerificationStatus.LIMITED_DATA,
            source=IngredientSource.GEMINI,
        )
    )
    dumped = out.model_dump(by_alias=True)
    assert dumped["efsaApprovalStatus"] == "NO_INFORMATION"
    assert dumped["fdaApprovalStatus"] == "NO_INFORMATION"
    assert dumped["adiMinMgPerKgBwPerDay"] is None
    assert dumped["adiMaxMgPerKgBwPerDay"] is None
    assert dumped["adiSource"] is None


def test_unverified_curated_source_never_surfaces_as_an_authoritative_approval():
    """`source=CURATED_SEED` alone is not enough -- the row must ALSO be
    `VERIFIED` (task requirement 4); a not-yet-verified row of an
    otherwise-trusted source still gets `NO_INFORMATION`/`None`."""
    out = IngredientOut(
        **_curated_kwargs(
            verification_status=IngredientVerificationStatus.UNVERIFIED,
            source=IngredientSource.CURATED_SEED,
        )
    )
    dumped = out.model_dump(by_alias=True)
    assert dumped["efsaApprovalStatus"] == "NO_INFORMATION"
    assert dumped["adiMinMgPerKgBwPerDay"] is None


def test_untouched_field_with_explicit_lower_trust_provenance_is_never_gated_authoritative():
    """The scenario `merge_verified_fields` actually produces once a row
    has been through at least one partial merge: an untouched field
    keeps an EXPLICIT provenance entry naming its own real (non-trusted)
    source, even though the record-level `source`/`verificationStatus`
    now read REGULATORY_LOOKUP/VERIFIED. That explicit entry must win --
    `efsaApprovalStatus`/`adiMin...` must stay ungated for THIS field."""
    import json

    field_provenance_json = json.dumps(
        {
            "description": {"source": "REGULATORY_LOOKUP", "confidence": 0.9, "retrievedAt": "2026-01-01T00:00:00+00:00"},
            "efsa_status": {"source": "GEMINI", "confidence": 0.8, "retrievedAt": "2025-01-01T00:00:00+00:00"},
            "acceptable_daily_intake": {
                "source": "GEMINI", "confidence": 0.8, "retrievedAt": "2025-01-01T00:00:00+00:00"
            },
        }
    )
    out = IngredientOut(
        **_curated_kwargs(
            verification_status=IngredientVerificationStatus.VERIFIED,
            source=IngredientSource.REGULATORY_LOOKUP,
            field_provenance_json=field_provenance_json,
        )
    )
    dumped = out.model_dump(by_alias=True)

    # The one field a REGULATORY_LOOKUP call genuinely touched IS gated.
    # (`description` isn't itself gated -- only EFSA/FDA/ADI are -- this
    # just documents the setup is realistic.)
    assert dumped["description"] == "High-intensity artificial sweetener."

    # `efsaStatus`/`acceptableDailyIntake` are still, in truth, GEMINI
    # content -- never gated as an authoritative regulatory claim, no
    # matter what the record-level `source`/`verificationStatus` say.
    assert dumped["efsaApprovalStatus"] == "NO_INFORMATION"
    assert dumped["adiMinMgPerKgBwPerDay"] is None
    assert dumped["adiMaxMgPerKgBwPerDay"] is None
    assert dumped["adiSource"] is None


def test_regulatory_lookup_sourced_verified_data_is_authoritative():
    """The other trusted source (task requirement 4): a VERIFIED,
    REGULATORY_LOOKUP-sourced row is just as authoritative as a
    CURATED_SEED one."""
    out = IngredientOut(
        **_curated_kwargs(
            verification_status=IngredientVerificationStatus.VERIFIED,
            source=IngredientSource.REGULATORY_LOOKUP,
        )
    )
    dumped = out.model_dump(by_alias=True)
    assert dumped["efsaApprovalStatus"] == "APPROVED"
    assert dumped["adiMinMgPerKgBwPerDay"] == 0.0


def test_ins_number_is_never_presented_as_independently_verified():
    """See `ingredient_regulatory` module docstring / `IngredientOut.
    ins_number_verified`: always `False` today, for a curated row
    exactly like an OCR one -- INS is always mechanically derived from
    `eNumber`, never independently confirmed."""
    curated_out = IngredientOut(**_curated_kwargs(ins_number="951"))
    assert curated_out.model_dump(by_alias=True)["insNumberVerified"] is False


# --- Nullable dietary-identity flags (PR #13 review: requirement 2) --------


def test_unassessed_synthetic_ingredient_never_claims_a_dietary_identity():
    """`isGluten`/`isLactose`/`isVegan`/`isVegetarian`/`isHalal`/
    `isKosher` must be `null`, never a fabricated True/False, for an
    OCR-only ingredient with no scientific-database match."""
    syn = create_synthetic_ingredient("Palm Oil")
    out = IngredientOut.model_validate(syn)
    dumped = out.model_dump(by_alias=True)
    for field in ("isGluten", "isLactose", "isVegan", "isVegetarian", "isHalal", "isKosher"):
        assert dumped[field] is None, field


def test_unassessed_synthetic_ingredient_never_claims_no_allergens():
    """The literal string 'None' must never be persisted/returned as
    proof of "no allergens" -- absence of a keyword match is unknown,
    not a verified clean bill of health (task requirement 2)."""
    syn = create_synthetic_ingredient("Plain Water")
    assert syn.allergens == ""
    out = IngredientOut.model_validate(syn)
    assert out.model_dump(by_alias=True)["allergens"] == ""


def test_unassessed_synthetic_ingredient_never_infers_bad_for_flags_from_keywords():
    """`bad_for_*` must never be guessed from an ingredient-name keyword
    (task requirement 2) -- a name containing "sugar"/"sodium"/"palm"
    used to flip `badForDiabetes`/`badForHypertension`/
    `badForHighCholesterol` to `True` with no real clinical assessment
    behind it."""
    for name in ("Cane Sugar", "Sodium Benzoate", "Palm Oil", "Corn Syrup", "Hydrogenated Palm Fat"):
        syn = create_synthetic_ingredient(name)
        assert syn.bad_for_diabetes is False, name
        assert syn.bad_for_hypertension is False, name
        assert syn.bad_for_high_cholesterol is False, name
