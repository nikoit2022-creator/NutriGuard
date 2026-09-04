package com.example.data.model

import androidx.room.Entity
import androidx.room.PrimaryKey

@Entity(tableName = "products")
data class ProductEntity(
    @PrimaryKey
    val barcode: String,
    val productName: String,
    val brand: String,
    val category: String,
    val imageUrl: String? = null,
    val rawIngredientText: String,
    val ingredientIds: String, // Comma separated IDs or JSON string
    val healthScore: Int?, // 0 to 100 when verified; null while pending
    val novaGroup: Int, // 1 (Unprocessed), 2 (Processed Culinary), 3 (Processed), 4 (Ultra-Processed)
    // Nutrition Per 100g
    val sugarGrams: Double,
    val sodiumMg: Double,
    val saturatedFatGrams: Double,
    val hasArtificialSweeteners: Boolean,
    val hasPreservatives: Boolean,
    // Dietary Suitability Flags
    val isGlutenFree: Boolean,
    val isLactoseFree: Boolean,
    val isVegan: Boolean,
    val isVegetarian: Boolean,
    val isHalal: Boolean,
    val isKosher: Boolean,
    val allergensDetected: String, // e.g., "Peanuts, Soy, Milk"
    val hasVerifiedNutrition: Boolean = true,
    val hasVerifiedIngredients: Boolean = true,
    val isVerified: Boolean = true,
    val timestamp: Long = System.currentTimeMillis()
)
