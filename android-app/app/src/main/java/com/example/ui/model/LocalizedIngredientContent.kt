package com.example.ui.model

import com.example.data.model.IngredientEntity
import com.example.data.remote.dto.IngredientLocalizationDto
import com.example.data.remote.dto.cleanOrNull
import com.example.ui.i18n.AppLanguage
import com.squareup.moshi.Moshi
import com.squareup.moshi.Types
import com.squareup.moshi.kotlin.reflect.KotlinJsonAdapterFactory

data class LocalizedIngredientContent(
    val commonName: String,
    val category: String,
    val description: String,
    val purposeInFood: String,
    val healthConcerns: String,
    val evidenceLevel: String,
    val countriesRestrictedOrBanned: String,
    val acceptableDailyIntake: String,
    val sideEffects: String,
    val allergens: String,
    val riskRationale: String?
)

private object IngredientLocalizationCacheJson {
    private val mapType = Types.newParameterizedType(
        Map::class.java,
        String::class.java,
        IngredientLocalizationDto::class.java
    )
    private val adapter = Moshi.Builder()
        .addLast(KotlinJsonAdapterFactory())
        .build()
        .adapter<Map<String, IngredientLocalizationDto>>(mapType)

    fun parse(raw: String): Map<String, IngredientLocalizationDto> =
        runCatching { adapter.fromJson(raw).orEmpty() }.getOrDefault(emptyMap())
}

/**
 * Resolves verified backend copy per field. Bulgarian falls back to the
 * backend's English localization, then to the legacy canonical field. Raw OCR
 * and product identity are deliberately outside this model and stay unchanged.
 */
fun IngredientEntity.localizedContent(language: AppLanguage): LocalizedIngredientContent {
    val root = localizationsJson.cleanOrNull()
        ?.let(IngredientLocalizationCacheJson::parse)
        .orEmpty()
    val requested = root[language.code]
    val english = root[AppLanguage.ENGLISH.code]

    fun resolve(
        requestedValue: String?,
        englishValue: String?,
        fallback: String
    ): String = requestedValue.cleanOrNull() ?: englishValue.cleanOrNull() ?: fallback

    return LocalizedIngredientContent(
        commonName = resolve(requested?.commonName, english?.commonName, commonName),
        category = resolve(requested?.category, english?.category, category),
        description = resolve(requested?.description, english?.description, description),
        purposeInFood = resolve(requested?.purposeInFood, english?.purposeInFood, purposeInFood),
        healthConcerns = resolve(requested?.healthConcerns, english?.healthConcerns, healthConcerns),
        evidenceLevel = resolve(requested?.evidenceLevel, english?.evidenceLevel, evidenceLevel),
        countriesRestrictedOrBanned = resolve(
            requested?.countriesRestrictedOrBanned,
            english?.countriesRestrictedOrBanned,
            countriesRestrictedOrBanned
        ),
        acceptableDailyIntake = resolve(
            requested?.acceptableDailyIntake,
            english?.acceptableDailyIntake,
            acceptableDailyIntake
        ),
        sideEffects = resolve(requested?.sideEffects, english?.sideEffects, sideEffects),
        allergens = resolve(requested?.allergens, english?.allergens, allergens),
        riskRationale = resolve(
            requested?.riskRationale,
            english?.riskRationale,
            riskRationale.orEmpty()
        ).cleanOrNull()
    )
}
