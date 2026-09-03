from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from app.models.enums import WarningSeverity
from app.repositories.health_profile_repository import default_profile_dict
from app.services.warning_engine import generate_warnings


@dataclass
class FakeProduct:
    sugar_grams: float = 0.0
    sodium_mg: float = 0.0
    saturated_fat_grams: float = 0.0
    nutrition_basis: str = "PER_100_G"
    is_gluten_free: bool = True
    is_lactose_free: bool = True
    is_vegan: bool = True
    is_vegetarian: bool = True
    is_halal: bool = True
    is_kosher: bool = True


@dataclass
class FakeIngredient:
    common_name: str = "Test Additive"
    e_number: str | None = "E000"
    bad_for_diabetes: bool = False
    bad_for_hypertension: bool = False
    bad_for_kidney_disease: bool = False
    bad_for_gout: bool = False
    bad_for_pregnancy: bool = False
    bad_for_children: bool = False
    bad_for_high_cholesterol: bool = False


def make_profile(**overrides) -> SimpleNamespace:
    data = default_profile_dict()
    data.update(overrides)
    return SimpleNamespace(**data)


def find(warnings, condition):
    return [w for w in warnings if w.condition == condition]


# 1. Diabetes -----------------------------------------------------------------

def test_diabetes_sugar_moderate_below_15():
    product = FakeProduct(sugar_grams=8.0)
    warnings = generate_warnings(product, [], make_profile(has_diabetes=True))
    matches = find(warnings, "Diabetes")
    assert len(matches) == 1
    assert matches[0].severity == WarningSeverity.MODERATE


def test_diabetes_sugar_high_above_15():
    product = FakeProduct(sugar_grams=20.0)
    warnings = generate_warnings(product, [], make_profile(has_diabetes=True))
    matches = find(warnings, "Diabetes")
    assert matches[0].severity == WarningSeverity.HIGH


def test_nutrition_warning_uses_per_100ml_basis_for_beverage():
    product = FakeProduct(sugar_grams=8.0, nutrition_basis="PER_100_ML")
    warning = find(generate_warnings(product, [], make_profile(has_diabetes=True)), "Diabetes")[0]
    assert "per 100 ml" in warning.description


def test_diabetes_ingredient_flag():
    product = FakeProduct(sugar_grams=0.0)
    ingredient = FakeIngredient(bad_for_diabetes=True)
    warnings = generate_warnings(product, [ingredient], make_profile(has_diabetes=True))
    matches = find(warnings, "Diabetes")
    assert len(matches) == 1
    assert matches[0].severity == WarningSeverity.HIGH


def test_diabetes_no_warning_when_profile_flag_off():
    product = FakeProduct(sugar_grams=50.0)
    warnings = generate_warnings(product, [], make_profile(has_diabetes=False))
    assert find(warnings, "Diabetes") == []


# 2. Hypertension ---------------------------------------------------------------

def test_hypertension_sodium_moderate_and_high():
    moderate = generate_warnings(FakeProduct(sodium_mg=500.0), [], make_profile(has_hypertension=True))
    assert find(moderate, "Hypertension")[0].severity == WarningSeverity.MODERATE

    high = generate_warnings(FakeProduct(sodium_mg=900.0), [], make_profile(has_hypertension=True))
    assert find(high, "Hypertension")[0].severity == WarningSeverity.HIGH


def test_hypertension_ingredient_flag():
    ingredient = FakeIngredient(bad_for_hypertension=True)
    warnings = generate_warnings(FakeProduct(), [ingredient], make_profile(has_hypertension=True))
    assert len(find(warnings, "Hypertension")) == 1


# 3. Kidney disease ---------------------------------------------------------------

def test_kidney_disease_sodium_and_ingredient():
    ingredient = FakeIngredient(bad_for_kidney_disease=True)
    warnings = generate_warnings(
        FakeProduct(sodium_mg=600.0), [ingredient], make_profile(has_kidney_disease=True)
    )
    matches = find(warnings, "Kidney Disease")
    # one from the ingredient flag, one from sodium > 500
    assert len(matches) == 2


# 4. Gout ---------------------------------------------------------------

def test_gout_ingredient_flag():
    ingredient = FakeIngredient(bad_for_gout=True)
    warnings = generate_warnings(FakeProduct(), [ingredient], make_profile(has_gout=True))
    assert len(find(warnings, "Gout")) == 1
    assert find(warnings, "Gout")[0].severity == WarningSeverity.HIGH


# 5. Pregnancy ---------------------------------------------------------------

def test_pregnancy_ingredient_flag():
    ingredient = FakeIngredient(bad_for_pregnancy=True)
    warnings = generate_warnings(FakeProduct(), [ingredient], make_profile(is_pregnant=True))
    assert len(find(warnings, "Pregnancy")) == 1


# 6. Children ---------------------------------------------------------------

def test_children_ingredient_flag():
    ingredient = FakeIngredient(bad_for_children=True)
    warnings = generate_warnings(FakeProduct(), [ingredient], make_profile(for_children=True))
    assert len(find(warnings, "Children")) == 1


# 7. High cholesterol ---------------------------------------------------------------

def test_high_cholesterol_saturated_fat_and_ingredient():
    ingredient = FakeIngredient(bad_for_high_cholesterol=True)
    warnings = generate_warnings(
        FakeProduct(saturated_fat_grams=5.0), [ingredient], make_profile(has_high_cholesterol=True)
    )
    matches = find(warnings, "High Cholesterol")
    assert len(matches) == 2
    severities = {w.severity for w in matches}
    assert WarningSeverity.HIGH in severities  # from saturated fat
    assert WarningSeverity.MODERATE in severities  # from ingredient


# 8. Gluten ---------------------------------------------------------------

def test_gluten_violation():
    warnings = generate_warnings(
        FakeProduct(is_gluten_free=False), [], make_profile(avoid_gluten=True)
    )
    assert len(find(warnings, "Gluten-Free Diet")) == 1


def test_gluten_no_violation_when_free():
    warnings = generate_warnings(
        FakeProduct(is_gluten_free=True), [], make_profile(avoid_gluten=True)
    )
    assert find(warnings, "Gluten-Free Diet") == []


# 9. Lactose ---------------------------------------------------------------

def test_lactose_violation():
    warnings = generate_warnings(
        FakeProduct(is_lactose_free=False), [], make_profile(avoid_lactose=True)
    )
    assert len(find(warnings, "Lactose Intolerance")) == 1


# 10. Vegan ---------------------------------------------------------------

def test_vegan_violation():
    warnings = generate_warnings(FakeProduct(is_vegan=False), [], make_profile(require_vegan=True))
    assert len(find(warnings, "Vegan Lifestyle")) == 1


# 11. Halal ---------------------------------------------------------------

def test_halal_violation():
    warnings = generate_warnings(FakeProduct(is_halal=False), [], make_profile(require_halal=True))
    assert len(find(warnings, "Halal")) == 1


# 12. Kosher ---------------------------------------------------------------

def test_kosher_violation():
    warnings = generate_warnings(FakeProduct(is_kosher=False), [], make_profile(require_kosher=True))
    assert len(find(warnings, "Kosher")) == 1


# Documented quirk preservation --------------------------------------------

def test_unimplemented_flags_produce_no_warnings_by_design():
    """avoid_peanuts / avoid_soy / avoid_tree_nuts / require_vegetarian exist
    on the profile but have NO matching check in the original Kotlin engine
    (API Contract 7.2). This backend preserves that exact behavior."""
    warnings = generate_warnings(
        FakeProduct(is_vegetarian=False),
        [],
        make_profile(
            avoid_peanuts=True,
            avoid_soy=True,
            avoid_tree_nuts=True,
            require_vegetarian=True,
        ),
    )
    assert warnings == []


def test_no_flags_no_warnings():
    warnings = generate_warnings(FakeProduct(), [], make_profile())
    assert warnings == []
