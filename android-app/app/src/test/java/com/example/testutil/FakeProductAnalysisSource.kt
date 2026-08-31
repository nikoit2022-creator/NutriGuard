package com.example.testutil

import android.graphics.Bitmap
import com.example.data.model.IngredientEntity
import com.example.data.model.ProductEntity
import com.example.data.model.ScanHistoryEntity
import com.example.data.model.UserHealthProfile
import com.example.data.repository.FullProductAnalysis
import com.example.data.repository.ProductAnalysisSource
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

/**
 * In-memory [ProductAnalysisSource] test double — no Room, no
 * [com.example.data.remote.NutriGuardApiService], no network of any
 * kind. Lets ViewModel/Compose tests script exactly what a "backend
 * response" looks like (a value, or a specific exception to throw) so
 * they exercise `MainViewModel`'s state machine deterministically,
 * satisfying "Android tests must not call live services."
 */
class FakeProductAnalysisSource(
    initialScanHistory: List<ScanHistoryEntity> = emptyList()
) : ProductAnalysisSource {

    override val allIngredients: StateFlow<List<IngredientEntity>> = MutableStateFlow(emptyList())
    override val allProducts: StateFlow<List<ProductEntity>> = MutableStateFlow(emptyList())
    override val scanHistory: MutableStateFlow<List<ScanHistoryEntity>> = MutableStateFlow(initialScanHistory)
    override val userProfile: StateFlow<UserHealthProfile?> = MutableStateFlow(null)

    /** Set to script the next [analyzeBarcode] outcome. Defaults to throwing if unset. */
    var barcodeResult: Result<FullProductAnalysis> = Result.failure(IllegalStateException("barcodeResult not configured"))

    var ocrResult: Result<FullProductAnalysis> = Result.failure(IllegalStateException("ocrResult not configured"))
    var imageResult: Result<FullProductAnalysis> = Result.failure(IllegalStateException("imageResult not configured"))

    var analyzeBarcodeCallCount: Int = 0
        private set
    var lastBarcodeArgument: String? = null
        private set

    /** Optional artificial suspend delay before resolving [barcodeResult], to observe the Searching window. */
    var barcodeResultDelayMillis: Long = 0

    override suspend fun saveUserProfile(profile: UserHealthProfile) {
        // no-op: not exercised by the barcode-flow tests
    }

    override suspend fun analyzeBarcode(barcode: String): FullProductAnalysis {
        analyzeBarcodeCallCount += 1
        lastBarcodeArgument = barcode
        if (barcodeResultDelayMillis > 0) {
            kotlinx.coroutines.delay(barcodeResultDelayMillis)
        }
        return barcodeResult.getOrThrow()
    }

    override suspend fun analyzeOcrText(rawText: String): FullProductAnalysis = ocrResult.getOrThrow()

    override suspend fun analyzeImageLabel(bitmap: Bitmap): FullProductAnalysis = imageResult.getOrThrow()
}

/** Minimal, obviously-fake [FullProductAnalysis] builder for test scripting. */
fun sampleFullProductAnalysis(
    barcode: String = "4006381333931",
    productName: String = "Test Product",
    brand: String = "Test Brand",
    healthScore: Int = 75,
    isFromDatabaseCache: Boolean = false
): FullProductAnalysis {
    val product = ProductEntity(
        barcode = barcode,
        productName = productName,
        brand = brand,
        category = "Test Category",
        rawIngredientText = "Water, Sugar",
        ingredientIds = "",
        healthScore = healthScore,
        novaGroup = 3,
        sugarGrams = 5.0,
        sodiumMg = 100.0,
        saturatedFatGrams = 1.0,
        hasArtificialSweeteners = false,
        hasPreservatives = false,
        isGlutenFree = true,
        isLactoseFree = true,
        isVegan = false,
        isVegetarian = false,
        isHalal = true,
        isKosher = true,
        allergensDetected = "None"
    )
    return FullProductAnalysis(
        product = product,
        ingredients = emptyList(),
        healthScore = healthScore,
        warnings = emptyList(),
        isFromDatabaseCache = isFromDatabaseCache
    )
}
