"""
Pure unit tests for `app.services.ingredient_regulatory` -- the
conservative EFSA/FDA approval-status and numeric-ADI derivation used
by `IngredientOut`'s additive data-quality fields (see the
"backend-ingredient-profile-data-quality" task).
"""
import json
from pathlib import Path

import pytest

from app.models.enums import ApprovalStatus, IngredientSource, IngredientVerificationStatus
from app.services.ingredient_regulatory import (
    derive_adi_range_mg_per_kg_bw_per_day,
    derive_approval_status,
    derive_gated_adi_range_mg_per_kg_bw_per_day,
    derive_gated_approval_status,
    is_authoritative_regulatory_source,
)

SEED_FILE = Path(__file__).resolve().parents[2] / "app" / "seed" / "ingredients_seed.json"


# --- derive_approval_status -------------------------------------------------


@pytest.mark.parametrize(
    "status_text,expected",
    [
        (None, ApprovalStatus.NO_INFORMATION),
        ("", ApprovalStatus.NO_INFORMATION),
        ("Authorized (ADI 40 mg/kg bw/day)", ApprovalStatus.APPROVED),
        ("Approved as General Purpose Sweetener", ApprovalStatus.APPROVED),
        ("GRAS (Generally Recognized as Safe)", ApprovalStatus.APPROVED),
        ("Allowed up to 1% by weight", ApprovalStatus.APPROVED),
        ("Banned (No longer considered safe due to genotoxicity)", ApprovalStatus.NOT_APPROVED),
        # Vague wording must never be inferred as an approval -- exactly
        # the failure mode this task removes (a synthetic ingredient's
        # old "Recognized Ingredient"/"Standard Food Additive/Ingredient"
        # placeholders, and real-world ambiguous text like "regulated").
        ("Regulated under Sugars Directive", ApprovalStatus.NO_INFORMATION),
        ("Recognized Ingredient", ApprovalStatus.NO_INFORMATION),
        ("Standard Food Additive/Ingredient", ApprovalStatus.NO_INFORMATION),
    ],
)
def test_derive_approval_status(status_text, expected):
    assert derive_approval_status(status_text) == expected


# --- derive_adi_range_mg_per_kg_bw_per_day ----------------------------------


@pytest.mark.parametrize(
    "adi_text,expected",
    [
        (None, (None, None)),
        ("", (None, None)),
        ("0 - 40 mg/kg bw/day", (0.0, 40.0)),
        ("30 mg/kg bw/day", (30.0, 30.0)),
        ("0 - 0.07 mg/kg bw/day", (0.0, 0.07)),
        ("4 mg/kg bw/day expressed as steviol equivalents", (4.0, 4.0)),
        # Never fabricated when the format isn't an unambiguous mg/kg bw
        # figure -- a percentage-of-calories guideline is real data, but
        # not an ADI number, and a vague qualifier is not a number at all.
        ("<5% total daily calorie intake (WHO guideline)", (None, None)),
        ("No safe ADI established", (None, None)),
        ("Not specified", (None, None)),
        ("Not limited", (None, None)),
        ("No limit", (None, None)),
    ],
)
def test_derive_adi_range_mg_per_kg_bw_per_day(adi_text, expected):
    assert derive_adi_range_mg_per_kg_bw_per_day(adi_text) == expected


# --- Regression pin against the real curated seed data ----------------------


def _seed_rows() -> list[dict]:
    return json.loads(SEED_FILE.read_text(encoding="utf-8"))


def test_no_curated_seed_ingredient_efsa_status_is_fabricated_approved():
    """None of the 12 curated seed entries' EFSA status text should
    resolve to APPROVED through a vague word alone -- pins the current,
    hand-verified classification of every real entry."""
    expected = {
        "e951_aspartame": ApprovalStatus.APPROVED,
        "e171_titanium_dioxide": ApprovalStatus.NOT_APPROVED,
        "e621_msg": ApprovalStatus.APPROVED,
        "e250_sodium_nitrite": ApprovalStatus.APPROVED,
        "high_fructose_corn_syrup": ApprovalStatus.NO_INFORMATION,
        "e102_tartrazine": ApprovalStatus.APPROVED,
        "e320_bha": ApprovalStatus.APPROVED,
        "e471_mono_diglycerides": ApprovalStatus.APPROVED,
        "whole_oat_flour": ApprovalStatus.APPROVED,
        "stevia_extract": ApprovalStatus.APPROVED,
        "e322_soy_lecithin": ApprovalStatus.APPROVED,
        "e415_xanthan_gum": ApprovalStatus.APPROVED,
    }
    for row in _seed_rows():
        assert derive_approval_status(row["efsaStatus"]) == expected[row["id"]], row["id"]


def test_curated_seed_countries_field_never_uses_generic_none_placeholder():
    """'None' is not a verified list of countries -- see task requirement
    7. The seed data was normalized to empty string for every entry
    that had no real country-specific restriction."""
    for row in _seed_rows():
        assert row["countriesRestrictedOrBanned"].strip().lower() != "none"


# --- derive_gated_approval_status / derive_gated_adi_range_mg_per_kg_bw_per_day
# --- (PR #13 review, task requirement 4) ------------------------------------

_APPROVED_TEXT = "Authorized (ADI 40 mg/kg bw/day)"
_ADI_TEXT = "0 - 40 mg/kg bw/day"


@pytest.mark.parametrize(
    "verification_status,source,expected",
    [
        (IngredientVerificationStatus.VERIFIED, IngredientSource.CURATED_SEED, True),
        (IngredientVerificationStatus.VERIFIED, IngredientSource.REGULATORY_LOOKUP, True),
        (IngredientVerificationStatus.VERIFIED, IngredientSource.GEMINI, False),
        (IngredientVerificationStatus.VERIFIED, IngredientSource.OCR_HEURISTIC, False),
        (IngredientVerificationStatus.VERIFIED, None, False),
        (IngredientVerificationStatus.LIMITED_DATA, IngredientSource.CURATED_SEED, False),
        (IngredientVerificationStatus.UNVERIFIED, IngredientSource.CURATED_SEED, False),
        (IngredientVerificationStatus.UNVERIFIED, IngredientSource.GEMINI, False),
    ],
)
def test_is_authoritative_regulatory_source(verification_status, source, expected):
    assert is_authoritative_regulatory_source(verification_status, source) == expected


@pytest.mark.parametrize(
    "source", [IngredientSource.CURATED_SEED, IngredientSource.REGULATORY_LOOKUP]
)
def test_gated_approval_status_passes_through_for_a_trusted_verified_row(source):
    assert (
        derive_gated_approval_status(_APPROVED_TEXT, verification_status=IngredientVerificationStatus.VERIFIED, source=source)
        == ApprovalStatus.APPROVED
    )
    assert derive_gated_adi_range_mg_per_kg_bw_per_day(
        _ADI_TEXT, verification_status=IngredientVerificationStatus.VERIFIED, source=source
    ) == (0.0, 40.0)


def test_gated_approval_status_is_no_information_for_a_gemini_sourced_row_even_with_approved_looking_text():
    """Task requirement 4: Gemini/OCR data must never appear as a
    regulatory approval or an authoritative ADI, no matter how
    confident or well-formatted the text itself looks."""
    assert (
        derive_gated_approval_status(
            _APPROVED_TEXT, verification_status=IngredientVerificationStatus.LIMITED_DATA, source=IngredientSource.GEMINI
        )
        == ApprovalStatus.NO_INFORMATION
    )
    assert derive_gated_adi_range_mg_per_kg_bw_per_day(
        _ADI_TEXT, verification_status=IngredientVerificationStatus.LIMITED_DATA, source=IngredientSource.GEMINI
    ) == (None, None)


def test_gated_approval_status_is_no_information_for_an_ocr_sourced_row():
    assert (
        derive_gated_approval_status(
            _APPROVED_TEXT,
            verification_status=IngredientVerificationStatus.UNVERIFIED,
            source=IngredientSource.OCR_HEURISTIC,
        )
        == ApprovalStatus.NO_INFORMATION
    )
    assert derive_gated_adi_range_mg_per_kg_bw_per_day(
        _ADI_TEXT, verification_status=IngredientVerificationStatus.UNVERIFIED, source=IngredientSource.OCR_HEURISTIC
    ) == (None, None)


def test_gated_approval_status_requires_verified_not_just_a_trusted_source():
    """A trusted `source` alone is not enough -- the row must also
    actually be `VERIFIED` (task requirement 4)."""
    assert (
        derive_gated_approval_status(
            _APPROVED_TEXT,
            verification_status=IngredientVerificationStatus.LIMITED_DATA,
            source=IngredientSource.CURATED_SEED,
        )
        == ApprovalStatus.NO_INFORMATION
    )
