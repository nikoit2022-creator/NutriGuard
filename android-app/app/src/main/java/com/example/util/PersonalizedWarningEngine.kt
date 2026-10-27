package com.example.util

import com.example.data.model.IngredientEntity
import com.example.data.model.ProductEntity
import com.example.data.model.UserHealthProfile

enum class WarningSeverity {
    HIGH, MODERATE, INFO
}

data class HealthWarning(
    val title: String,
    val description: String,
    val condition: String,
    val triggerFactor: String,
    val severity: WarningSeverity
)

object PersonalizedWarningEngine {

    fun generateWarnings(
        product: ProductEntity,
        ingredients: List<IngredientEntity>,
        profile: UserHealthProfile
    ): List<HealthWarning> {
        val warnings = mutableListOf<HealthWarning>()

        // 1. Diabetes Profile
        if (profile.hasDiabetes) {
            if (product.sugarGrams > 5.0) {
                warnings.add(
                    HealthWarning(
                        title = "High Glycemic Sugar Alert",
                        description = "Contains ${product.sugarGrams}g sugar per 100g. Can cause rapid blood glucose spikes and insulin surge.",
                        condition = "Diabetes",
                        triggerFactor = "Sugar Content (${product.sugarGrams}g)",
                        severity = if (product.sugarGrams > 15.0) WarningSeverity.HIGH else WarningSeverity.MODERATE
                    )
                )
            }
            ingredients.filter { it.badForDiabetes }.forEach { ing ->
                warnings.add(
                    HealthWarning(
                        title = "Diabetes Sensitivity: ${ing.commonName}",
                        description = "Contains ${ing.commonName} (${ing.eNumber ?: "Additive"}) which affects glycemic index or insulin receptor sensitivity.",
                        condition = "Diabetes",
                        triggerFactor = ing.commonName,
                        severity = WarningSeverity.HIGH
                    )
                )
            }
        }

        // 2. Hypertension Profile
        if (profile.hasHypertension) {
            if (product.sodiumMg > 400.0) {
                warnings.add(
                    HealthWarning(
                        title = "Elevated Sodium Warning",
                        description = "Contains ${product.sodiumMg}mg sodium per 100g. Excessive sodium retention increases arterial blood pressure.",
                        condition = "Hypertension",
                        triggerFactor = "High Sodium (${product.sodiumMg}mg)",
                        severity = if (product.sodiumMg > 800.0) WarningSeverity.HIGH else WarningSeverity.MODERATE
                    )
                )
            }
            ingredients.filter { it.badForHypertension }.forEach { ing ->
                warnings.add(
                    HealthWarning(
                        title = "Hypertension Risk: ${ing.commonName}",
                        description = "${ing.commonName} contributes extra sodium load or alters vascular tone.",
                        condition = "Hypertension",
                        triggerFactor = ing.commonName,
                        severity = WarningSeverity.HIGH
                    )
                )
            }
        }

        // 3. Kidney Disease Profile
        if (profile.hasKidneyDisease) {
            ingredients.filter { it.badForKidneyDisease }.forEach { ing ->
                warnings.add(
                    HealthWarning(
                        title = "Renal Strain: ${ing.commonName}",
                        description = "${ing.commonName} increases renal excretion load or tissue toxicity risk.",
                        condition = "Kidney Disease",
                        triggerFactor = ing.commonName,
                        severity = WarningSeverity.HIGH
                    )
                )
            }
            if (product.sodiumMg > 500.0) {
                warnings.add(
                    HealthWarning(
                        title = "Renal Sodium Overload",
                        description = "High sodium levels (${product.sodiumMg}mg) strain renal filtration capacity.",
                        condition = "Kidney Disease",
                        triggerFactor = "Sodium Content",
                        severity = WarningSeverity.HIGH
                    )
                )
            }
        }

        // 4. Gout Profile
        if (profile.hasGout) {
            ingredients.filter { it.badForGout }.forEach { ing ->
                warnings.add(
                    HealthWarning(
                        title = "Uric Acid Trigger: ${ing.commonName}",
                        description = "${ing.commonName} accelerates hepatic ATP degradation, elevating blood uric acid and triggering gout attacks.",
                        condition = "Gout",
                        triggerFactor = ing.commonName,
                        severity = WarningSeverity.HIGH
                    )
                )
            }
        }

        // 5. Pregnancy Profile
        if (profile.isPregnant) {
            ingredients.filter { it.badForPregnancy }.forEach { ing ->
                warnings.add(
                    HealthWarning(
                        title = "Maternal / Fetal Concern: ${ing.commonName}",
                        description = "${ing.commonName} has evidence of placental transfer, genotoxicity, or fetal growth restriction concerns.",
                        condition = "Pregnancy",
                        triggerFactor = ing.commonName,
                        severity = WarningSeverity.HIGH
                    )
                )
            }
        }

        // 6. Children Profile
        if (profile.forChildren) {
            ingredients.filter { it.badForChildren }.forEach { ing ->
                warnings.add(
                    HealthWarning(
                        title = "Child Safety Warning: ${ing.commonName}",
                        description = "${ing.commonName} is linked to child hyperactivity (ADHD symptoms), neurobehavioral impact, or growth sensitivity.",
                        condition = "Children",
                        triggerFactor = ing.commonName,
                        severity = WarningSeverity.HIGH
                    )
                )
            }
        }

        // 7. High Cholesterol Profile
        if (profile.hasHighCholesterol) {
            if (product.saturatedFatGrams > 4.0) {
                warnings.add(
                    HealthWarning(
                        title = "High Saturated Fat Alert",
                        description = "Contains ${product.saturatedFatGrams}g saturated fat per 100g. Raises serum LDL cholesterol and atherogenic lipo-proteins.",
                        condition = "High Cholesterol",
                        triggerFactor = "Saturated Fat (${product.saturatedFatGrams}g)",
                        severity = WarningSeverity.HIGH
                    )
                )
            }
            ingredients.filter { it.badForHighCholesterol }.forEach { ing ->
                warnings.add(
                    HealthWarning(
                        title = "Cardiovascular Concern: ${ing.commonName}",
                        description = "${ing.commonName} adversely influences serum lipid profiles.",
                        condition = "High Cholesterol",
                        triggerFactor = ing.commonName,
                        severity = WarningSeverity.MODERATE
                    )
                )
            }
        }

        // 8. Custom Allergen & Dietary Rules
        val glutenStatus = product.dietaryDisplayStatus("gluten")
        if (profile.avoidGluten && glutenStatus != "yes") {
            warnings.add(
                HealthWarning(
                    title = if (glutenStatus == "no") "Gluten Violation" else "Gluten Status Unconfirmed",
                    description = if (glutenStatus == "no") "Gluten-containing grain is named in the ingredients." else "Gluten-free status has not been established. Check the label before use.",
                    condition = "Gluten-Free Diet",
                    triggerFactor = "Gluten",
                    severity = if (glutenStatus == "no") WarningSeverity.HIGH else WarningSeverity.INFO
                )
            )
        }

        val lactoseStatus = product.dietaryDisplayStatus("lactose")
        if (profile.avoidLactose && lactoseStatus != "yes") {
            warnings.add(
                HealthWarning(
                    title = if (lactoseStatus == "no") "Lactose Contained" else "Lactose Status Unconfirmed",
                    description = if (lactoseStatus == "no") "Lactose or milk powder is named in the ingredients." else "Lactose-free status has not been established. Check the label before use.",
                    condition = "Lactose Intolerance",
                    triggerFactor = "Lactose",
                    severity = if (lactoseStatus == "no") WarningSeverity.HIGH else WarningSeverity.INFO
                )
            )
        }

        val veganStatus = product.dietaryDisplayStatus("vegan")
        if (profile.requireVegan && veganStatus != "yes") {
            warnings.add(
                HealthWarning(
                    title = if (veganStatus == "no") "Non-Vegan Product" else "Vegan Status Unconfirmed",
                    description = if (veganStatus == "no") "Animal-derived ingredients are named in the ingredients." else "Vegan status has not been established. Check the label before use.",
                    condition = "Vegan Lifestyle",
                    triggerFactor = "Animal Origin",
                    severity = if (veganStatus == "no") WarningSeverity.HIGH else WarningSeverity.INFO
                )
            )
        }

        if (profile.requireHalal && product.dietaryDisplayStatus("halal") != "yes") {
            warnings.add(
                HealthWarning(
                    title = "Halal Status Unconfirmed",
                    description = "Halal compliance has not been established from available evidence.",
                    condition = "Halal",
                    triggerFactor = "Halal Compliance",
                    severity = WarningSeverity.INFO
                )
            )
        }

        if (profile.requireKosher && product.dietaryDisplayStatus("kosher") != "yes") {
            warnings.add(
                HealthWarning(
                    title = "Kosher Status Unconfirmed",
                    description = "Kosher compliance has not been established from available evidence.",
                    condition = "Kosher",
                    triggerFactor = "Kosher Compliance",
                    severity = WarningSeverity.INFO
                )
            )
        }

        val allergens = product.namedAllergens().map { it.lowercase() }.toSet()
        listOf(
            Triple(profile.avoidPeanuts, "Peanuts", "Peanut Allergy"),
            Triple(profile.avoidSoy, "Soybeans", "Soy Allergy"),
            Triple(profile.avoidTreeNuts, "Nuts", "Tree Nut Allergy")
        ).forEach { (enabled, name, condition) ->
            if (enabled && name.lowercase() in allergens) {
                warnings.add(HealthWarning(
                    title = "$name Listed in Ingredients",
                    description = "$name are named as ingredients or declared allergens. Check the package before use.",
                    condition = condition,
                    triggerFactor = name,
                    severity = WarningSeverity.HIGH
                ))
            }
        }

        return warnings
    }
}
