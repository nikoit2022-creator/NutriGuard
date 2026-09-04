"""
Pure unit tests for `app.services.ingredient_regulatory` -- the
conservative EFSA/FDA approval-status and numeric-ADI derivation used
by `IngredientOut`'s additive data-quality fields (see the
"backend-ingredient-profile-data-quality" task).
"""
import json
from pathlib import Path

import pytest

from app.models.enums import ApprovalStatus
from app.services.ingredient_regulatory import (
    derive_adi_range_mg_per_kg_bw_per_day,
    derive_approval_status,
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
