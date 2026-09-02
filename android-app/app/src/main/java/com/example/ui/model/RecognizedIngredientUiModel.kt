package com.example.ui.model

import com.example.data.model.IngredientEntity
import com.example.data.model.RiskLevel
import com.example.data.remote.dto.cleanOrNull

enum class IngredientSafetyRating {
    LOW_CONCERN,
    MODERATE,
    POTENTIAL_CONCERN,
    HIGH_CONCERN,
    LIMITED_DATA
}

data class RecognizedIngredientUiModel(
    val ingredient: IngredientEntity,
    val displayName: String,
    val safetyScore: Int?,
    val rating: IngredientSafetyRating,
    val purpose: String?,
    val explanation: String,
    val eNumber: String?,
    val evidenceLevel: String?,
    val allergens: String?
)

private val GENERATED_INGREDIENT_ID = Regex("^ING_.+_\\d+$", RegexOption.IGNORE_CASE)
private val SYNTHETIC_PREFIX = Regex("^(?:synth[_\\s-]+)+", RegexOption.IGNORE_CASE)
private val REPEATED_WHITESPACE = Regex("\\s+")

private val GENERIC_PROFILE_VALUES = setOf(
    "food additive",
    "scientific studies",
    "none reported",
    "not specified",
    "approved",
    "gras"
)

/**
 * Maps the current ingredient entity to presentation data without inventing a
 * numeric safety score. [safetyScore] exists for the future additive backend
 * field and for boundary testing; production call sites intentionally omit it
 * until the API supplies a real score.
 */
fun IngredientEntity.toRecognizedIngredientUiModel(
    safetyScore: Int? = null
): RecognizedIngredientUiModel {
    val normalizedScore = safetyScore?.takeIf { it in 0..100 }
    val synthetic = isSyntheticOrGenerated()
    val hasScientificProfile = !synthetic && listOf(
        description,
        purposeInFood,
        healthConcerns,
        evidenceLevel,
        references
    ).any { value -> value.meaningfulProfileText() != null }

    val rating = when {
        normalizedScore != null -> ratingForSafetyScore(normalizedScore)
        !hasScientificProfile -> IngredientSafetyRating.LIMITED_DATA
        else -> riskLevel.toSafetyRating()
    }

    val cleanedPurpose = purposeInFood.meaningfulProfileText()
        ?: category.meaningfulProfileText()
    val cleanedExplanation = description.meaningfulProfileText()
        ?: healthConcerns.meaningfulProfileText()
        ?: "No verified scientific profile is available for this ingredient yet."

    return RecognizedIngredientUiModel(
        ingredient = this,
        displayName = ingredientDisplayName(),
        safetyScore = normalizedScore,
        rating = rating,
        purpose = cleanedPurpose,
        explanation = cleanedExplanation,
        eNumber = eNumber.cleanOrNull(),
        evidenceLevel = evidenceLevel.meaningfulProfileText(),
        allergens = allergens.meaningfulAllergenText()
    )
}

fun ratingForSafetyScore(score: Int): IngredientSafetyRating = when (score.coerceIn(0, 100)) {
    in 80..100 -> IngredientSafetyRating.LOW_CONCERN
    in 55..79 -> IngredientSafetyRating.MODERATE
    in 30..54 -> IngredientSafetyRating.POTENTIAL_CONCERN
    else -> IngredientSafetyRating.HIGH_CONCERN
}

private fun RiskLevel.toSafetyRating(): IngredientSafetyRating = when (this) {
    RiskLevel.SAFE -> IngredientSafetyRating.LOW_CONCERN
    RiskLevel.MODERATE -> IngredientSafetyRating.MODERATE
    RiskLevel.POTENTIAL_CONCERN -> IngredientSafetyRating.POTENTIAL_CONCERN
    RiskLevel.HIGH_CONCERN -> IngredientSafetyRating.HIGH_CONCERN
}

private fun IngredientEntity.isSyntheticOrGenerated(): Boolean {
    val normalizedName = commonName.cleanOrNull().orEmpty()
    return id.startsWith("synth_", ignoreCase = true) ||
        normalizedName.startsWith("synth_", ignoreCase = true) ||
        GENERATED_INGREDIENT_ID.matches(id)
}

private fun IngredientEntity.ingredientDisplayName(): String {
    val source = commonName.cleanOrNull()
        ?.takeUnless { it.equals("Unknown Ingredient", ignoreCase = true) }
        ?: id.cleanOrNull()
        ?: return "Recognized ingredient"

    val cleaned = source
        .replace(SYNTHETIC_PREFIX, "")
        .replace('_', ' ')
        .replace(REPEATED_WHITESPACE, " ")
        .trim()

    if (cleaned.isEmpty()) return "Recognized ingredient"
    return cleaned.replaceFirstChar { first ->
        if (first.isLowerCase()) first.titlecase() else first.toString()
    }
}

private fun String?.meaningfulProfileText(): String? = cleanOrNull()
    ?.takeUnless { it.lowercase() in GENERIC_PROFILE_VALUES }

private fun String?.meaningfulAllergenText(): String? = cleanOrNull()
    ?.takeUnless { value ->
        value.equals("no", ignoreCase = true) ||
            value.equals("no allergens", ignoreCase = true) ||
            value.equals("not detected", ignoreCase = true)
    }
