package com.example.data.remote.dto

import android.util.Log
import com.example.data.model.IngredientEntity
import com.example.data.model.ProductEntity
import com.example.data.model.RiskLevel
import com.example.util.HealthWarning
import com.example.util.WarningSeverity
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

data class ScanLabelImageResponseDto(
    val productName: String? = null,
    val brand: String? = null,
    val category: String? = null,
    val barcode: String? = null,
    val imageUrl: String? = null,
    val rawIngredientText: String? = null,
    val healthScore: Int? = null,
    val novaGroup: Int? = null,
    val nutritionPer100g: NutritionDto? = null,
    val dietarySuitability: DietarySuitabilityDto? = null,
    val allergensDetected: List<String>? = null,
    val ingredients: List<IngredientDto>? = null,
    val warnings: List<WarningDto>? = null
) {
    companion object {
        fun fromJson(json: JSONObject): ScanLabelImageResponseDto {
            val nutritionObj = json.optJSONObject("nutrition_per_100g")
            val dietaryObj = json.optJSONObject("dietary_suitability")
            val allergensArray = json.optJSONArray("allergens_detected")
            val ingredientsArray = json.optJSONArray("ingredients")
            val warningsArray = json.optJSONArray("warnings")

            val allergensList = mutableListOf<String>()
            if (allergensArray != null) {
                for (i in 0 until allergensArray.length()) {
                    allergensList.add(allergensArray.optString(i))
                }
            }

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
                productName = json.optString("product_name").takeIf { it.isNotBlank() },
                brand = json.optString("brand").takeIf { it.isNotBlank() },
                category = json.optString("category").takeIf { it.isNotBlank() },
                barcode = json.optString("barcode").takeIf { it.isNotBlank() },
                imageUrl = json.optString("image_url").takeIf { it.isNotBlank() },
                rawIngredientText = json.optString("raw_ingredient_text").takeIf { it.isNotBlank() },
                healthScore = if (json.has("health_score")) json.optInt("health_score") else null,
                novaGroup = if (json.has("nova_group")) json.optInt("nova_group") else null,
                nutritionPer100g = nutritionObj?.let { NutritionDto.fromJson(it) },
                dietarySuitability = dietaryObj?.let { DietarySuitabilityDto.fromJson(it) },
                allergensDetected = allergensList,
                ingredients = ingredientsList,
                warnings = warningsList
            )
        }
    }
}

data class NutritionDto(
    val sugarG: Double? = null,
    val sodiumMg: Double? = null,
    val saturatedFatG: Double? = null,
    val hasArtificialSweeteners: Boolean? = null,
    val hasPreservatives: Boolean? = null
) {
    companion object {
        fun fromJson(json: JSONObject): NutritionDto {
            return NutritionDto(
                sugarG = if (json.has("sugar_g")) json.optDouble("sugar_g") else null,
                sodiumMg = if (json.has("sodium_mg")) json.optDouble("sodium_mg") else null,
                saturatedFatG = if (json.has("saturated_fat_g")) json.optDouble("saturated_fat_g") else null,
                hasArtificialSweeteners = if (json.has("has_artificial_sweeteners")) json.optBoolean("has_artificial_sweeteners") else null,
                hasPreservatives = if (json.has("has_preservatives")) json.optBoolean("has_preservatives") else null
            )
        }
    }
}

data class DietarySuitabilityDto(
    val isGlutenFree: Boolean? = null,
    val isLactoseFree: Boolean? = null,
    val isVegan: Boolean? = null,
    val isVegetarian: Boolean? = null,
    val isHalal: Boolean? = null,
    val isKosher: Boolean? = null
) {
    companion object {
        fun fromJson(json: JSONObject): DietarySuitabilityDto {
            return DietarySuitabilityDto(
                isGlutenFree = if (json.has("is_gluten_free")) json.optBoolean("is_gluten_free") else null,
                isLactoseFree = if (json.has("is_lactose_free")) json.optBoolean("is_lactose_free") else null,
                isVegan = if (json.has("is_vegan")) json.optBoolean("is_vegan") else null,
                isVegetarian = if (json.has("is_vegetarian")) json.optBoolean("is_vegetarian") else null,
                isHalal = if (json.has("is_halal")) json.optBoolean("is_halal") else null,
                isKosher = if (json.has("is_kosher")) json.optBoolean("is_kosher") else null
            )
        }
    }
}

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
    val healthProfileTriggers: HealthProfileTriggersDto? = null
) {
    companion object {
        fun fromJson(json: JSONObject): IngredientDto {
            val triggersObj = json.optJSONObject("health_profile_triggers")
            return IngredientDto(
                id = json.optString("id").takeIf { it.isNotBlank() },
                commonName = json.optString("common_name").takeIf { it.isNotBlank() },
                scientificName = json.optString("scientific_name").takeIf { it.isNotBlank() },
                eNumber = json.optString("e_number").takeIf { it.isNotBlank() },
                category = json.optString("category").takeIf { it.isNotBlank() },
                description = json.optString("description").takeIf { it.isNotBlank() },
                purposeInFood = json.optString("purpose_in_food").takeIf { it.isNotBlank() },
                healthConcerns = json.optString("health_concerns").takeIf { it.isNotBlank() },
                evidenceLevel = json.optString("evidence_level").takeIf { it.isNotBlank() },
                countriesRestrictedOrBanned = json.optString("countries_restricted_or_banned").takeIf { it.isNotBlank() },
                efsaStatus = json.optString("efsa_status").takeIf { it.isNotBlank() },
                fdaStatus = json.optString("fda_status").takeIf { it.isNotBlank() },
                whoIarcClassification = json.optString("who_iarc_classification").takeIf { it.isNotBlank() },
                acceptableDailyIntake = json.optString("acceptable_daily_intake").takeIf { it.isNotBlank() },
                sideEffects = json.optString("side_effects").takeIf { it.isNotBlank() },
                allergens = json.optString("allergens").takeIf { it.isNotBlank() },
                references = json.optString("references").takeIf { it.isNotBlank() },
                riskLevel = json.optString("risk_level").takeIf { it.isNotBlank() },
                isGluten = if (json.has("is_gluten")) json.optBoolean("is_gluten") else null,
                isLactose = if (json.has("is_lactose")) json.optBoolean("is_lactose") else null,
                isVegan = if (json.has("is_vegan")) json.optBoolean("is_vegan") else null,
                isVegetarian = if (json.has("is_vegetarian")) json.optBoolean("is_vegetarian") else null,
                isHalal = if (json.has("is_halal")) json.optBoolean("is_halal") else null,
                isKosher = if (json.has("is_kosher")) json.optBoolean("is_kosher") else null,
                healthProfileTriggers = triggersObj?.let { HealthProfileTriggersDto.fromJson(it) }
            )
        }
    }
}

data class HealthProfileTriggersDto(
    val badForDiabetes: Boolean? = null,
    val badForHypertension: Boolean? = null,
    val badForKidneyDisease: Boolean? = null,
    val badForGout: Boolean? = null,
    val badForPregnancy: Boolean? = null,
    val badForChildren: Boolean? = null,
    val badForHighCholesterol: Boolean? = null
) {
    companion object {
        fun fromJson(json: JSONObject): HealthProfileTriggersDto {
            return HealthProfileTriggersDto(
                badForDiabetes = if (json.has("bad_for_diabetes")) json.optBoolean("bad_for_diabetes") else null,
                badForHypertension = if (json.has("bad_for_hypertension")) json.optBoolean("bad_for_hypertension") else null,
                badForKidneyDisease = if (json.has("bad_for_kidney_disease")) json.optBoolean("bad_for_kidney_disease") else null,
                badForGout = if (json.has("bad_for_gout")) json.optBoolean("bad_for_gout") else null,
                badForPregnancy = if (json.has("bad_for_pregnancy")) json.optBoolean("bad_for_pregnancy") else null,
                badForChildren = if (json.has("bad_for_children")) json.optBoolean("bad_for_children") else null,
                badForHighCholesterol = if (json.has("bad_for_high_cholesterol")) json.optBoolean("bad_for_high_cholesterol") else null
            )
        }
    }
}

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
                triggerFactor = json.optString("trigger_factor").takeIf { it.isNotBlank() },
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
    val warnings: List<HealthWarning>
)

fun ScanLabelImageResponseDto.toParsedEntities(): ParsedScanData {
    val nonNullBarcode = if (!barcode.isNullOrBlank()) {
        barcode
    } else {
        "SYNTH_IMG_" + UUID.randomUUID().toString().replace("-", "").take(12)
    }

    val ingredientEntities = ingredients?.mapIndexed { index, ing ->
        val ingId = if (!ing.id.isNullOrBlank()) ing.id else "ING_${nonNullBarcode}_$index"
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
            badForDiabetes = ing.healthProfileTriggers?.badForDiabetes ?: false,
            badForHypertension = ing.healthProfileTriggers?.badForHypertension ?: false,
            badForKidneyDisease = ing.healthProfileTriggers?.badForKidneyDisease ?: false,
            badForGout = ing.healthProfileTriggers?.badForGout ?: false,
            badForPregnancy = ing.healthProfileTriggers?.badForPregnancy ?: false,
            badForChildren = ing.healthProfileTriggers?.badForChildren ?: false,
            badForHighCholesterol = ing.healthProfileTriggers?.badForHighCholesterol ?: false
        )
    } ?: emptyList()

    val joinedAllergens = allergensDetected?.filter { it.isNotBlank() }?.joinToString(", ") ?: ""
    val joinedIngredientIds = ingredientEntities.map { it.id }.joinToString(",")

    val productEntity = ProductEntity(
        barcode = nonNullBarcode,
        productName = productName ?: "Scanned Product",
        brand = brand ?: "Unknown Brand",
        category = category ?: "General Food",
        rawIngredientText = rawIngredientText ?: "",
        ingredientIds = joinedIngredientIds,
        healthScore = healthScore ?: 50,
        novaGroup = novaGroup ?: 3,
        sugarGrams = nutritionPer100g?.sugarG ?: 0.0,
        sodiumMg = nutritionPer100g?.sodiumMg ?: 0.0,
        saturatedFatGrams = nutritionPer100g?.saturatedFatG ?: 0.0,
        hasArtificialSweeteners = nutritionPer100g?.hasArtificialSweeteners ?: false,
        hasPreservatives = nutritionPer100g?.hasPreservatives ?: false,
        isGlutenFree = dietarySuitability?.isGlutenFree ?: true,
        isLactoseFree = dietarySuitability?.isLactoseFree ?: true,
        isVegan = dietarySuitability?.isVegan ?: false,
        isVegetarian = dietarySuitability?.isVegetarian ?: false,
        isHalal = dietarySuitability?.isHalal ?: true,
        isKosher = dietarySuitability?.isKosher ?: true,
        allergensDetected = joinedAllergens,
        imageUrl = imageUrl,
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
        warnings = domainWarnings
    )
}
