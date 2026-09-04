package com.example.data.remote.dto

import android.util.Log
import com.example.data.model.IngredientEntity
import com.example.data.model.ProductEntity
import com.example.data.model.RiskLevel
import com.example.util.HealthWarning
import com.example.util.WarningSeverity
import org.json.JSONObject
import java.util.UUID

/**
 * Top-level response DTO for POST /api/v1/scan/label-image.
 * Mirrors the backend's FullProductAnalysisOut (all fields camelCase on the wire).
 */
data class ScanLabelImageResponseDto(
    val product: ProductDto? = null,
    val ingredients: List<IngredientDto>? = null,
    val healthScore: Int? = null,
    val warnings: List<WarningDto>? = null,
    val isFromDatabaseCache: Boolean? = null
) {
    companion object {
        private fun JSONObject.optNullableInt(key: String): Int? =
            if (!has(key) || isNull(key)) null else optInt(key)

        fun fromJson(json: JSONObject): ScanLabelImageResponseDto {
            val productObj = json.optJSONObject("product")
            val ingredientsArray = json.optJSONArray("ingredients")
            val warningsArray = json.optJSONArray("warnings")

            val ingredientsList = mutableListOf<IngredientDto>()
            if (ingredientsArray != null) {
                for (i in 0 until ingredientsArray.length()) {
                    val ingObj = ingredientsArray.optJSONObject(i)
                    if (ingObj != null) {
                        ingredientsList.add(IngredientDto.fromJson(ingObj))
                    }
                }
            }

            val warningsList = mutableListOf<WarningDto>()
            if (warningsArray != null) {
                for (i in 0 until warningsArray.length()) {
                    val warnObj = warningsArray.optJSONObject(i)
                    if (warnObj != null) {
                        warningsList.add(WarningDto.fromJson(warnObj))
                    }
                }
            }

            return ScanLabelImageResponseDto(
                product = productObj?.let { ProductDto.fromJson(it) },
                ingredients = ingredientsList,
                healthScore = json.optNullableInt("healthScore"),
                warnings = warningsList,
                isFromDatabaseCache = if (json.has("isFromDatabaseCache")) json.optBoolean("isFromDatabaseCache") else null
            )
        }
    }
}

/**
 * Mirrors the backend's ProductOut, nested under "product" in FullProductAnalysisOut.
 * Nutrition and dietary-suitability fields live directly on this object (no nested
 * "nutrition_per_100g" / "dietary_suitability" wrapper on the wire).
 */
data class ProductDto(
    val barcode: String? = null,
    val productName: String? = null,
    val brand: String? = null,
    val category: String? = null,
    val imageUrl: String? = null,
    val rawIngredientText: String? = null,
    val ingredientIds: String? = null,
    val healthScore: Int? = null,
    val novaGroup: Int? = null,
    val sugarGrams: Double? = null,
    val sodiumMg: Double? = null,
    val saturatedFatGrams: Double? = null,
    val hasArtificialSweeteners: Boolean? = null,
    val hasPreservatives: Boolean? = null,
    val isGlutenFree: Boolean? = null,
    val isLactoseFree: Boolean? = null,
    val isVegan: Boolean? = null,
    val isVegetarian: Boolean? = null,
    val isHalal: Boolean? = null,
    val isKosher: Boolean? = null,
    val allergensDetected: String? = null,
    val hasVerifiedNutrition: Boolean? = null,
    val hasVerifiedIngredients: Boolean? = null,
    val isVerified: Boolean? = null
) {
    companion object {
        private fun JSONObject.optNullableInt(key: String): Int? =
            if (!has(key) || isNull(key)) null else optInt(key)

        fun fromJson(json: JSONObject): ProductDto {
            return ProductDto(
                barcode = json.optString("barcode").takeIf { it.isNotBlank() },
                productName = json.optString("productName").takeIf { it.isNotBlank() },
                brand = json.optString("brand").takeIf { it.isNotBlank() },
                category = json.optString("category").takeIf { it.isNotBlank() },
                imageUrl = json.optString("imageUrl").takeIf { it.isNotBlank() },
                rawIngredientText = json.optString("rawIngredientText").takeIf { it.isNotBlank() },
                ingredientIds = json.optString("ingredientIds").takeIf { it.isNotBlank() },
                healthScore = json.optNullableInt("healthScore"),
                novaGroup = if (json.has("novaGroup")) json.optInt("novaGroup") else null,
                sugarGrams = if (json.has("sugarGrams")) json.optDouble("sugarGrams") else null,
                sodiumMg = if (json.has("sodiumMg")) json.optDouble("sodiumMg") else null,
                saturatedFatGrams = if (json.has("saturatedFatGrams")) json.optDouble("saturatedFatGrams") else null,
                hasArtificialSweeteners = if (json.has("hasArtificialSweeteners")) json.optBoolean("hasArtificialSweeteners") else null,
                hasPreservatives = if (json.has("hasPreservatives")) json.optBoolean("hasPreservatives") else null,
                isGlutenFree = if (json.has("isGlutenFree")) json.optBoolean("isGlutenFree") else null,
                isLactoseFree = if (json.has("isLactoseFree")) json.optBoolean("isLactoseFree") else null,
                isVegan = if (json.has("isVegan")) json.optBoolean("isVegan") else null,
                isVegetarian = if (json.has("isVegetarian")) json.optBoolean("isVegetarian") else null,
                isHalal = if (json.has("isHalal")) json.optBoolean("isHalal") else null,
                isKosher = if (json.has("isKosher")) json.optBoolean("isKosher") else null,
                allergensDetected = json.optString("allergensDetected").takeIf { it.isNotBlank() },
                hasVerifiedNutrition = if (json.has("hasVerifiedNutrition")) json.optBoolean("hasVerifiedNutrition") else null,
                hasVerifiedIngredients = if (json.has("hasVerifiedIngredients")) json.optBoolean("hasVerifiedIngredients") else null,
                isVerified = if (json.has("isVerified")) json.optBoolean("isVerified") else null
            )
        }
    }
}

/**
 * Mirrors the backend's IngredientOut. The seven "bad for X" health-profile
 * trigger flags live directly on this object (no nested "health_profile_triggers").
 */
data class IngredientDto(
    val id: String? = null,
    val commonName: String? = null,
    val scientificName: String? = null,
    val eNumber: String? = null,
    val category: String? = null,
    val description: String? = null,
    val purposeInFood: String? = null,
    val healthConcerns: String? = null,
    val evidenceLevel: String? = null,
    val countriesRestrictedOrBanned: String? = null,
    val efsaStatus: String? = null,
    val fdaStatus: String? = null,
    val whoIarcClassification: String? = null,
    val acceptableDailyIntake: String? = null,
    val sideEffects: String? = null,
    val allergens: String? = null,
    val references: String? = null,
    val riskLevel: String? = null,
    val isGluten: Boolean? = null,
    val isLactose: Boolean? = null,
    val isVegan: Boolean? = null,
    val isVegetarian: Boolean? = null,
    val isHalal: Boolean? = null,
    val isKosher: Boolean? = null,
    val badForDiabetes: Boolean? = null,
    val badForHypertension: Boolean? = null,
    val badForKidneyDisease: Boolean? = null,
    val badForGout: Boolean? = null,
    val badForPregnancy: Boolean? = null,
    val badForChildren: Boolean? = null,
    val badForHighCholesterol: Boolean? = null
) {
    companion object {
        fun fromJson(json: JSONObject): IngredientDto {
            return IngredientDto(
                id = json.optString("id").takeIf { it.isNotBlank() },
                commonName = json.optString("commonName").takeIf { it.isNotBlank() },
                scientificName = json.optString("scientificName").takeIf { it.isNotBlank() },
                eNumber = json.optString("eNumber").takeIf { it.isNotBlank() },
                category = json.optString("category").takeIf { it.isNotBlank() },
                description = json.optString("description").takeIf { it.isNotBlank() },
                purposeInFood = json.optString("purposeInFood").takeIf { it.isNotBlank() },
                healthConcerns = json.optString("healthConcerns").takeIf { it.isNotBlank() },
                evidenceLevel = json.optString("evidenceLevel").takeIf { it.isNotBlank() },
                countriesRestrictedOrBanned = json.optString("countriesRestrictedOrBanned").takeIf { it.isNotBlank() },
                efsaStatus = json.optString("efsaStatus").takeIf { it.isNotBlank() },
                fdaStatus = json.optString("fdaStatus").takeIf { it.isNotBlank() },
                whoIarcClassification = json.optString("whoIarcClassification").takeIf { it.isNotBlank() },
                acceptableDailyIntake = json.optString("acceptableDailyIntake").takeIf { it.isNotBlank() },
                sideEffects = json.optString("sideEffects").takeIf { it.isNotBlank() },
                allergens = json.optString("allergens").takeIf { it.isNotBlank() },
                references = json.optString("references").takeIf { it.isNotBlank() },
                riskLevel = json.optString("riskLevel").takeIf { it.isNotBlank() },
                isGluten = if (json.has("isGluten")) json.optBoolean("isGluten") else null,
                isLactose = if (json.has("isLactose")) json.optBoolean("isLactose") else null,
                isVegan = if (json.has("isVegan")) json.optBoolean("isVegan") else null,
                isVegetarian = if (json.has("isVegetarian")) json.optBoolean("isVegetarian") else null,
                isHalal = if (json.has("isHalal")) json.optBoolean("isHalal") else null,
                isKosher = if (json.has("isKosher")) json.optBoolean("isKosher") else null,
                badForDiabetes = if (json.has("badForDiabetes")) json.optBoolean("badForDiabetes") else null,
                badForHypertension = if (json.has("badForHypertension")) json.optBoolean("badForHypertension") else null,
                badForKidneyDisease = if (json.has("badForKidneyDisease")) json.optBoolean("badForKidneyDisease") else null,
                badForGout = if (json.has("badForGout")) json.optBoolean("badForGout") else null,
                badForPregnancy = if (json.has("badForPregnancy")) json.optBoolean("badForPregnancy") else null,
                badForChildren = if (json.has("badForChildren")) json.optBoolean("badForChildren") else null,
                badForHighCholesterol = if (json.has("badForHighCholesterol")) json.optBoolean("badForHighCholesterol") else null
            )
        }
    }
}

/**
 * Mirrors the backend's HealthWarningOut.
 */
data class WarningDto(
    val title: String? = null,
    val description: String? = null,
    val condition: String? = null,
    val triggerFactor: String? = null,
    val severity: String? = null
) {
    companion object {
        fun fromJson(json: JSONObject): WarningDto {
            return WarningDto(
                title = json.optString("title").takeIf { it.isNotBlank() },
                description = json.optString("description").takeIf { it.isNotBlank() },
                condition = json.optString("condition").takeIf { it.isNotBlank() },
                triggerFactor = json.optString("triggerFactor").takeIf { it.isNotBlank() },
                severity = json.optString("severity").takeIf { it.isNotBlank() }
            )
        }
    }
}

// Safe Enum Parsers
fun parseRiskLevel(value: String?): RiskLevel {
    if (value.isNullOrBlank()) return RiskLevel.SAFE
    val normalized = value.trim().uppercase()
    return try {
        RiskLevel.valueOf(normalized)
    } catch (e: IllegalArgumentException) {
        Log.w("ScanDtoMapper", "Unexpected RiskLevel '$value', falling back to MODERATE")
        when {
            normalized.contains("HIGH") -> RiskLevel.HIGH_CONCERN
            normalized.contains("POTENTIAL") || normalized.contains("CONCERN") -> RiskLevel.POTENTIAL_CONCERN
            normalized.contains("MODERATE") -> RiskLevel.MODERATE
            normalized.contains("SAFE") || normalized.contains("LOW") -> RiskLevel.SAFE
            else -> RiskLevel.MODERATE
        }
    }
}

fun parseWarningSeverity(value: String?): WarningSeverity {
    if (value.isNullOrBlank()) return WarningSeverity.INFO
    val normalized = value.trim().uppercase()
    return try {
        WarningSeverity.valueOf(normalized)
    } catch (e: IllegalArgumentException) {
        Log.w("ScanDtoMapper", "Unexpected WarningSeverity '$value', falling back to INFO")
        when {
            normalized.contains("HIGH") || normalized.contains("CRITICAL") -> WarningSeverity.HIGH
            normalized.contains("MODERATE") || normalized.contains("MEDIUM") -> WarningSeverity.MODERATE
            else -> WarningSeverity.INFO
        }
    }
}

data class ParsedScanData(
    val product: ProductEntity,
    val ingredients: List<IngredientEntity>,
    val warnings: List<HealthWarning>,
    val isFromDatabaseCache: Boolean
)

/**
 * Converts backend [IngredientDto]s into domain [IngredientEntity]s,
 * synthesizing a stable id (`ING_<idPrefix>_<index>`) for any entry the
 * backend didn't give an id — shared by the full-success parsing below
 * AND by a partial/`labelScanRequired` result's already-verified
 * `ingredients` list (see [com.example.data.remote.BackendErrorDetailsDto]),
 * so both render identically instead of two divergent mappings.
 */
fun List<IngredientDto>.toEntities(idPrefix: String): List<IngredientEntity> =
    mapIndexed { index, ing ->
        val ingId = ing.id?.takeIf { it.isNotBlank() } ?: "ING_${idPrefix}_$index"
        IngredientEntity(
            id = ingId,
            commonName = ing.commonName ?: "Unknown Ingredient",
            scientificName = ing.scientificName ?: "",
            eNumber = ing.eNumber,
            category = ing.category ?: "Food Additive",
            description = ing.description ?: "",
            purposeInFood = ing.purposeInFood ?: "",
            healthConcerns = ing.healthConcerns ?: "",
            evidenceLevel = ing.evidenceLevel ?: "Scientific Studies",
            countriesRestrictedOrBanned = ing.countriesRestrictedOrBanned ?: "None reported",
            efsaStatus = ing.efsaStatus ?: "Approved",
            fdaStatus = ing.fdaStatus ?: "GRAS",
            whoIarcClassification = ing.whoIarcClassification,
            acceptableDailyIntake = ing.acceptableDailyIntake ?: "Not specified",
            sideEffects = ing.sideEffects ?: "None reported",
            allergens = ing.allergens ?: "None",
            references = ing.references ?: "",
            riskLevel = parseRiskLevel(ing.riskLevel),
            isGluten = ing.isGluten ?: false,
            isLactose = ing.isLactose ?: false,
            isVegan = ing.isVegan ?: true,
            isVegetarian = ing.isVegetarian ?: true,
            isHalal = ing.isHalal ?: true,
            isKosher = ing.isKosher ?: true,
            badForDiabetes = ing.badForDiabetes ?: false,
            badForHypertension = ing.badForHypertension ?: false,
            badForKidneyDisease = ing.badForKidneyDisease ?: false,
            badForGout = ing.badForGout ?: false,
            badForPregnancy = ing.badForPregnancy ?: false,
            badForChildren = ing.badForChildren ?: false,
            badForHighCholesterol = ing.badForHighCholesterol ?: false
        )
    }

fun ScanLabelImageResponseDto.toParsedEntities(): ParsedScanData {
    val productDto = product

    val nonNullBarcode = productDto?.barcode?.takeIf { it.isNotBlank() }
        ?: ("SYNTH_IMG_" + UUID.randomUUID().toString().replace("-", "").take(12))

    val ingredientEntities = (ingredients ?: emptyList()).toEntities(nonNullBarcode)

    val joinedIngredientIds = ingredientEntities.map { it.id }.joinToString(",")

    val productEntity = ProductEntity(
        barcode = nonNullBarcode,
        productName = productDto?.productName ?: "Scanned Product",
        brand = productDto?.brand ?: "Unknown Brand",
        category = productDto?.category ?: "General Food",
        rawIngredientText = productDto?.rawIngredientText ?: "",
        ingredientIds = joinedIngredientIds,
        healthScore = healthScore,
        novaGroup = productDto?.novaGroup ?: 3,
        sugarGrams = productDto?.sugarGrams ?: 0.0,
        sodiumMg = productDto?.sodiumMg ?: 0.0,
        saturatedFatGrams = productDto?.saturatedFatGrams ?: 0.0,
        hasArtificialSweeteners = productDto?.hasArtificialSweeteners ?: false,
        hasPreservatives = productDto?.hasPreservatives ?: false,
        isGlutenFree = productDto?.isGlutenFree ?: true,
        isLactoseFree = productDto?.isLactoseFree ?: true,
        isVegan = productDto?.isVegan ?: false,
        isVegetarian = productDto?.isVegetarian ?: false,
        isHalal = productDto?.isHalal ?: true,
        isKosher = productDto?.isKosher ?: true,
        allergensDetected = productDto?.allergensDetected ?: "",
        hasVerifiedNutrition = productDto?.hasVerifiedNutrition ?: false,
        hasVerifiedIngredients = productDto?.hasVerifiedIngredients ?: false,
        isVerified = productDto?.isVerified ?: false,
        imageUrl = productDto?.imageUrl,
        timestamp = System.currentTimeMillis()
    )

    val domainWarnings = warnings?.map { w ->
        HealthWarning(
            title = w.title ?: "Ingredient Notice",
            description = w.description ?: "",
            condition = w.condition ?: "General",
            triggerFactor = w.triggerFactor ?: "",
            severity = parseWarningSeverity(w.severity)
        )
    } ?: emptyList()

    return ParsedScanData(
        product = productEntity,
        ingredients = ingredientEntities,
        warnings = domainWarnings,
        isFromDatabaseCache = isFromDatabaseCache ?: false
    )
}
