package com.example.data.model

import androidx.room.Entity
import androidx.room.PrimaryKey

enum class RiskLevel {
    SAFE,             // 🟢 Safe
    MODERATE,         // 🟡 Consume in moderation
    POTENTIAL_CONCERN,// 🟠 Potential concern
    HIGH_CONCERN      // 🔴 High concern or restricted
}

@Entity(tableName = "ingredients")
data class IngredientEntity(
    @PrimaryKey
    val id: String, // e.g., "e951_aspartame" or "sugar"
    val commonName: String,
    val scientificName: String,
    val eNumber: String? = null, // e.g., "E951"
    val category: String, // e.g., "Artificial Sweetener", "Preservative", "Colorant"
    val description: String,
    val purposeInFood: String,
    val healthConcerns: String,
    val evidenceLevel: String, // e.g., "Strong Scientific Consensus", "Moderate Evidence", "Inconclusive"
    val countriesRestrictedOrBanned: String, // e.g., "EU, Japan, Norway" or "None"
    val efsaStatus: String, // e.g., "Authorized (ADI 40 mg/kg)", "Under Review", "Banned in EU"
    val fdaStatus: String, // e.g., "GRAS (Generally Recognized as Safe)", "Approved with limits"
    val whoIarcClassification: String? = null, // e.g., "Group 2B - Possibly Carcinogenic"
    val acceptableDailyIntake: String, // e.g., "0 - 40 mg/kg bw/day"
    val sideEffects: String, // e.g., "Headaches, GI distress in sensitive individuals"
    val allergens: String, // e.g., "Contains Phenylalanine"
    val references: String, // e.g., "EFSA Journal 2013;11(12):3496; WHO IARC Monograph 2023"
    val riskLevel: RiskLevel,
    val isGluten: Boolean = false,
    val isLactose: Boolean = false,
    val isVegan: Boolean = true,
    val isVegetarian: Boolean = true,
    val isHalal: Boolean = true,
    val isKosher: Boolean = true,
    // Health Profile Triggers
    val badForDiabetes: Boolean = false,
    val badForHypertension: Boolean = false,
    val badForKidneyDisease: Boolean = false,
    val badForGout: Boolean = false,
    val badForPregnancy: Boolean = false,
    val badForChildren: Boolean = false,
    val badForHighCholesterol: Boolean = false
)
