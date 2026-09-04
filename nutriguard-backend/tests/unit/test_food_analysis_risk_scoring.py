"""
Pure unit tests (no DB, no HTTP) for `food_analysis._score_and_warnings`
-- specifically that an ingredient with no real risk assessment
(`risk_assessment_available=False`, the OCR-only "synthetic" case, see
`app.services.ocr_normalizer`) never influences the Health Score, per
the "backend-ingredient-profile-data-quality" task (requirement 5 /
verification checklist item 2). End-to-end coverage of the same
behavior lives in
`tests/integration/test_barcode_contract_change.py::test_health_score_matches_hand_verified_formula_for_real_product`.
"""
from dataclasses import dataclass
from types import SimpleNamespace

from app.models.enums import RiskLevel
from app.services import food_analysis


@dataclass
class _FakeIngredient:
    risk_level: RiskLevel
    risk_assessment_available: bool
    common_name: str = "Fake Ingredient"
    e_number: str | None = None
    bad_for_diabetes: bool = False
    bad_for_hypertension: bool = False
    bad_for_kidney_disease: bool = False
    bad_for_gout: bool = False
    bad_for_pregnancy: bool = False
    bad_for_children: bool = False
    bad_for_high_cholesterol: bool = False


def _product(**overrides) -> SimpleNamespace:
    defaults = dict(
        sugar_grams=0.0,
        sodium_mg=0.0,
        saturated_fat_grams=0.0,
        has_artificial_sweeteners=False,
        has_preservatives=False,
        nova_group=1,
        nutrition_basis="PER_100_G",
        is_gluten_free=True,
        is_lactose_free=True,
        is_vegan=True,
        is_halal=True,
        is_kosher=True,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _inert_profile() -> SimpleNamespace:
    """Every UserHealthProfile flag off -- isolates the test to the
    Health Score, not the Personalized Warning Engine."""
    return SimpleNamespace(
        has_diabetes=False,
        has_hypertension=False,
        has_kidney_disease=False,
        has_gout=False,
        is_pregnant=False,
        for_children=False,
        has_high_cholesterol=False,
        avoid_gluten=False,
        avoid_lactose=False,
        avoid_peanuts=False,
        avoid_soy=False,
        avoid_tree_nuts=False,
        require_vegan=False,
        require_vegetarian=False,
        require_halal=False,
        require_kosher=False,
    )


def test_unassessed_high_concern_ingredient_does_not_deduct_from_health_score():
    """If a `risk_level` somehow carries HIGH_CONCERN despite
    `risk_assessment_available=False` (defense in depth -- today
    `create_synthetic_ingredient` always sets HIGH_CONCERN's neutral
    SAFE sibling, but the exclusion must hold regardless of the actual
    enum value), the Health Score must still be untouched by it."""
    unassessed = _FakeIngredient(risk_level=RiskLevel.HIGH_CONCERN, risk_assessment_available=False)

    score, _warnings = food_analysis._score_and_warnings(_product(), [unassessed], _inert_profile())

    assert score == 100  # no additives_deduction at all


def test_assessed_high_concern_ingredient_still_deducts_from_health_score():
    """A REAL assessment must keep affecting the score exactly as
    before -- only the unconfirmed case is excluded."""
    assessed = _FakeIngredient(risk_level=RiskLevel.HIGH_CONCERN, risk_assessment_available=True)

    score, _warnings = food_analysis._score_and_warnings(_product(), [assessed], _inert_profile())

    assert score == 80  # 100 - 20 (HIGH_CONCERN additives_deduction)


def test_mixed_assessed_and_unassessed_ingredients_only_count_the_assessed_one():
    assessed = _FakeIngredient(risk_level=RiskLevel.POTENTIAL_CONCERN, risk_assessment_available=True)
    unassessed = _FakeIngredient(risk_level=RiskLevel.HIGH_CONCERN, risk_assessment_available=False)

    score, _warnings = food_analysis._score_and_warnings(
        _product(), [assessed, unassessed], _inert_profile()
    )

    assert score == 88  # 100 - 12 (POTENTIAL_CONCERN only)
