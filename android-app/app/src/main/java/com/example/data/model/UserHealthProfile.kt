package com.example.data.model

import androidx.room.Entity
import androidx.room.PrimaryKey

@Entity(tableName = "user_health_profile")
data class UserHealthProfile(
    @PrimaryKey val id: Int = 1,
    val hasDiabetes: Boolean = false,
    val hasHypertension: Boolean = false,
    val hasKidneyDisease: Boolean = false,
    val hasGout: Boolean = false,
    val isPregnant: Boolean = false,
    val forChildren: Boolean = false,
    val hasHighCholesterol: Boolean = false,
    // Custom Allergen Exclusions
    val avoidGluten: Boolean = false,
    val avoidLactose: Boolean = false,
    val avoidPeanuts: Boolean = false,
    val avoidSoy: Boolean = false,
    val avoidTreeNuts: Boolean = false,
    val requireVegan: Boolean = false,
    val requireVegetarian: Boolean = false,
    val requireHalal: Boolean = false,
    val requireKosher: Boolean = false
)
