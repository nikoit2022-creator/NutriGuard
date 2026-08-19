"""
Deterministic port of `com.example.util.PersonalizedWarningEngine`.

All thresholds, titles, descriptions and severities are copied verbatim
from the Kotlin source (API Contract section 7.2). This module accepts
plain duck-typed objects (ORM instances or dataclasses) exposing the
snake_case attribute names listed below, so it can be unit tested with
lightweight fixtures instead of full ORM sessions.

Product-like object needs: sugar_grams, sodium_mg, saturated_fat_grams,
    is_gluten_free, is_lactose_free, is_vegan, is_halal, is_kosher
Ingredient-like object needs: common_name, e_number, bad_for_diabetes,
    bad_for_hypertension, bad_for_kidney_disease, bad_for_gout,
    bad_for_pregnancy, bad_for_children, bad_for_high_cholesterol
Profile-like object needs: all UserHealthProfile boolean flags

NOTE (documented deviation, not a bug in this port): in the original
Kotlin engine, `avoid_peanuts`, `avoid_soy`, `avoid_tree_nuts` and
`require_vegetarian` exist on the profile model but have NO matching
check implemented. This port preserves that exact (documented)
inconsistency rather than silently "fixing" it — see API Contract 7.2.
"""
from dataclasses import dataclass, field
from typing import Any

from app.models.enums import WarningSeverity


@dataclass(frozen=True)
class HealthWarning:
    title: str
    description: str
    condition: str
    trigger_factor: str
    severity: WarningSeverity


def generate_warnings(product: Any, ingredients: list[Any], profile: Any) -> list[HealthWarning]:
    warnings: list[HealthWarning] = []

    # 1. Diabetes
    if profile.has_diabetes:
        if product.sugar_grams > 5.0:
            warnings.append(
                HealthWarning(
                    title="High Glycemic Sugar Alert",
                    description=(
                        f"Contains {product.sugar_grams}g sugar per 100g. "
                        "Can cause rapid blood glucose spikes and insulin surge."
                    ),
                    condition="Diabetes",
                    trigger_factor=f"Sugar Content ({product.sugar_grams}g)",
                    severity=WarningSeverity.HIGH if product.sugar_grams > 15.0 else WarningSeverity.MODERATE,
                )
            )
        for ing in ingredients:
            if ing.bad_for_diabetes:
                warnings.append(
                    HealthWarning(
                        title=f"Diabetes Sensitivity: {ing.common_name}",
                        description=(
                            f"Contains {ing.common_name} ({ing.e_number or 'Additive'}) which affects "
                            "glycemic index or insulin receptor sensitivity."
                        ),
                        condition="Diabetes",
                        trigger_factor=ing.common_name,
                        severity=WarningSeverity.HIGH,
                    )
                )

    # 2. Hypertension
    if profile.has_hypertension:
        if product.sodium_mg > 400.0:
            warnings.append(
                HealthWarning(
                    title="Elevated Sodium Warning",
                    description=(
                        f"Contains {product.sodium_mg}mg sodium per 100g. Excessive sodium retention "
                        "increases arterial blood pressure."
                    ),
                    condition="Hypertension",
                    trigger_factor=f"High Sodium ({product.sodium_mg}mg)",
                    severity=WarningSeverity.HIGH if product.sodium_mg > 800.0 else WarningSeverity.MODERATE,
                )
            )
        for ing in ingredients:
            if ing.bad_for_hypertension:
                warnings.append(
                    HealthWarning(
                        title=f"Hypertension Risk: {ing.common_name}",
                        description=f"{ing.common_name} contributes extra sodium load or alters vascular tone.",
                        condition="Hypertension",
                        trigger_factor=ing.common_name,
                        severity=WarningSeverity.HIGH,
                    )
                )

    # 3. Kidney disease
    if profile.has_kidney_disease:
        for ing in ingredients:
            if ing.bad_for_kidney_disease:
                warnings.append(
                    HealthWarning(
                        title=f"Renal Strain: {ing.common_name}",
                        description=f"{ing.common_name} increases renal excretion load or tissue toxicity risk.",
                        condition="Kidney Disease",
                        trigger_factor=ing.common_name,
                        severity=WarningSeverity.HIGH,
                    )
                )
        if product.sodium_mg > 500.0:
            warnings.append(
                HealthWarning(
                    title="Renal Sodium Overload",
                    description=(
                        f"High sodium levels ({product.sodium_mg}mg) strain renal filtration capacity."
                    ),
                    condition="Kidney Disease",
                    trigger_factor="Sodium Content",
                    severity=WarningSeverity.HIGH,
                )
            )

    # 4. Gout
    if profile.has_gout:
        for ing in ingredients:
            if ing.bad_for_gout:
                warnings.append(
                    HealthWarning(
                        title=f"Uric Acid Trigger: {ing.common_name}",
                        description=(
                            f"{ing.common_name} accelerates hepatic ATP degradation, elevating blood uric "
                            "acid and triggering gout attacks."
                        ),
                        condition="Gout",
                        trigger_factor=ing.common_name,
                        severity=WarningSeverity.HIGH,
                    )
                )

    # 5. Pregnancy
    if profile.is_pregnant:
        for ing in ingredients:
            if ing.bad_for_pregnancy:
                warnings.append(
                    HealthWarning(
                        title=f"Maternal / Fetal Concern: {ing.common_name}",
                        description=(
                            f"{ing.common_name} has evidence of placental transfer, genotoxicity, or fetal "
                            "growth restriction concerns."
                        ),
                        condition="Pregnancy",
                        trigger_factor=ing.common_name,
                        severity=WarningSeverity.HIGH,
                    )
                )

    # 6. Children
    if profile.for_children:
        for ing in ingredients:
            if ing.bad_for_children:
                warnings.append(
                    HealthWarning(
                        title=f"Child Safety Warning: {ing.common_name}",
                        description=(
                            f"{ing.common_name} is linked to child hyperactivity (ADHD symptoms), "
                            "neurobehavioral impact, or growth sensitivity."
                        ),
                        condition="Children",
                        trigger_factor=ing.common_name,
                        severity=WarningSeverity.HIGH,
                    )
                )

    # 7. High cholesterol
    if profile.has_high_cholesterol:
        if product.saturated_fat_grams > 4.0:
            warnings.append(
                HealthWarning(
                    title="High Saturated Fat Alert",
                    description=(
                        f"Contains {product.saturated_fat_grams}g saturated fat per 100g. Raises serum LDL "
                        "cholesterol and atherogenic lipo-proteins."
                    ),
                    condition="High Cholesterol",
                    trigger_factor=f"Saturated Fat ({product.saturated_fat_grams}g)",
                    severity=WarningSeverity.HIGH,
                )
            )
        for ing in ingredients:
            if ing.bad_for_high_cholesterol:
                warnings.append(
                    HealthWarning(
                        title=f"Cardiovascular Concern: {ing.common_name}",
                        description=f"{ing.common_name} adversely influences serum lipid profiles.",
                        condition="High Cholesterol",
                        trigger_factor=ing.common_name,
                        severity=WarningSeverity.MODERATE,
                    )
                )

    # 8. Custom allergen & dietary rules
    if profile.avoid_gluten and not product.is_gluten_free:
        warnings.append(
            HealthWarning(
                title="Gluten Violation",
                description="This product contains or is processed with Gluten sources.",
                condition="Gluten-Free Diet",
                trigger_factor="Gluten",
                severity=WarningSeverity.HIGH,
            )
        )

    if profile.avoid_lactose and not product.is_lactose_free:
        warnings.append(
            HealthWarning(
                title="Lactose Contained",
                description="Product contains dairy or milk derivatives with lactose.",
                condition="Lactose Intolerance",
                trigger_factor="Lactose",
                severity=WarningSeverity.HIGH,
            )
        )

    if profile.require_vegan and not product.is_vegan:
        warnings.append(
            HealthWarning(
                title="Non-Vegan Product",
                description="Contains animal-derived ingredients or processing agents.",
                condition="Vegan Lifestyle",
                trigger_factor="Animal Origin",
                severity=WarningSeverity.HIGH,
            )
        )

    if profile.require_halal and not product.is_halal:
        warnings.append(
            HealthWarning(
                title="Halal Compliance Alert",
                description="Contains non-Halal ingredients or unverified animal derivatives.",
                condition="Halal",
                trigger_factor="Halal Compliance",
                severity=WarningSeverity.HIGH,
            )
        )

    if profile.require_kosher and not product.is_kosher:
        warnings.append(
            HealthWarning(
                title="Kosher Compliance Alert",
                description="Product does not meet strict Kosher certification standards.",
                condition="Kosher",
                trigger_factor="Kosher Compliance",
                severity=WarningSeverity.HIGH,
            )
        )

    return warnings
