package com.example.data.repository

import android.graphics.Bitmap
import com.example.data.dao.IngredientDao
import com.example.data.dao.ProductDao
import com.example.data.dao.ScanHistoryDao
import com.example.data.dao.UserHealthProfileDao
import com.example.data.model.IngredientEntity
import com.example.data.model.ProductEntity
import com.example.data.model.ScanHistoryEntity
import com.example.data.model.UserHealthProfile
import com.example.data.remote.NutriGuardApiService
import com.example.service.ai.GeminiAnalysisEngine
import com.example.util.HealthScoreCalculator
import com.example.util.PersonalizedWarningEngine
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.firstOrNull
import kotlinx.coroutines.withContext

data class FullProductAnalysis(
    val product: ProductEntity,
    val ingredients: List<IngredientEntity>,
    val healthScore: Int,
    val warnings: List<com.example.util.HealthWarning>,
    val isFromDatabaseCache: Boolean
)

class FoodAnalysisRepository(
    private val ingredientDao: IngredientDao,
    private val productDao: ProductDao,
    private val userProfileDao: UserHealthProfileDao,
    private val scanHistoryDao: ScanHistoryDao,
    private val apiService: NutriGuardApiService
) {

    val allIngredients: Flow<List<IngredientEntity>> = ingredientDao.getAllIngredients()
    val allProducts: Flow<List<ProductEntity>> = productDao.getAllProducts()
    val scanHistory: Flow<List<ScanHistoryEntity>> = scanHistoryDao.getAllHistory()
    val userProfile: Flow<UserHealthProfile?> = userProfileDao.getProfile()

    suspend fun saveUserProfile(profile: UserHealthProfile) = withContext(Dispatchers.IO) {
        userProfileDao.saveProfile(profile)
    }

    suspend fun searchIngredients(query: String): Flow<List<IngredientEntity>> {
        return ingredientDao.searchIngredients(query)
    }

    suspend fun analyzeBarcode(barcode: String): FullProductAnalysis = withContext(Dispatchers.IO) {
        val existing = productDao.getProductByBarcode(barcode)
        val profile = userProfileDao.getProfileSync() ?: UserHealthProfile()

        if (existing != null) {
            val ingList = fetchIngredientsForProduct(existing)
            val scoreBreakdown = HealthScoreCalculator.calculate(
                ingredients = ingList,
                sugarGrams = existing.sugarGrams,
                sodiumMg = existing.sodiumMg,
                saturatedFatGrams = existing.saturatedFatGrams,
                hasArtificialSweeteners = existing.hasArtificialSweeteners,
                hasPreservatives = existing.hasPreservatives,
                novaGroup = existing.novaGroup
            )
            val warnings = PersonalizedWarningEngine.generateWarnings(existing, ingList, profile)

            scanHistoryDao.insertHistory(
                ScanHistoryEntity(
                    barcode = barcode,
                    productName = existing.productName,
                    brand = existing.brand,
                    healthScore = scoreBreakdown.totalScore,
                    scanType = "BARCODE"
                )
            )

            return@withContext FullProductAnalysis(
                product = existing.copy(healthScore = scoreBreakdown.totalScore),
                ingredients = ingList,
                healthScore = scoreBreakdown.totalScore,
                warnings = warnings,
                isFromDatabaseCache = true
            )
        } else {
            // Unknown Barcode: trigger default analysis or synthetic mock for new barcode
            val sampleRawText = "Water, High Fructose Corn Syrup, Aspartame (E951), Tartrazine (E102), Sodium Nitrite (E250), Monosodium Glutamate (E621)"
            val (analyzedProd, ingList) = GeminiAnalysisEngine.analyzeIngredientText(
                rawIngredientText = sampleRawText,
                dbIngredients = ingredientDao.getAllIngredients().first()
            )

            val finalProduct = analyzedProd.copy(
                barcode = barcode,
                productName = "Scanned Item ($barcode)",
                brand = "Newly Identified Product"
            )

            val scoreBreakdown = HealthScoreCalculator.calculate(
                ingredients = ingList,
                sugarGrams = finalProduct.sugarGrams,
                sodiumMg = finalProduct.sodiumMg,
                saturatedFatGrams = finalProduct.saturatedFatGrams,
                hasArtificialSweeteners = finalProduct.hasArtificialSweeteners,
                hasPreservatives = finalProduct.hasPreservatives,
                novaGroup = finalProduct.novaGroup
            )

            val finalSavedProduct = finalProduct.copy(healthScore = scoreBreakdown.totalScore)
            productDao.insertProduct(finalSavedProduct)

            scanHistoryDao.insertHistory(
                ScanHistoryEntity(
                    barcode = barcode,
                    productName = finalSavedProduct.productName,
                    brand = finalSavedProduct.brand,
                    healthScore = scoreBreakdown.totalScore,
                    scanType = "BARCODE"
                )
            )

            val warnings = PersonalizedWarningEngine.generateWarnings(finalSavedProduct, ingList, profile)

            return@withContext FullProductAnalysis(
                product = finalSavedProduct,
                ingredients = ingList,
                healthScore = scoreBreakdown.totalScore,
                warnings = warnings,
                isFromDatabaseCache = false
            )
        }
    }

    suspend fun analyzeOcrText(rawText: String): FullProductAnalysis = withContext(Dispatchers.IO) {
        val dbIngs = ingredientDao.getAllIngredients().first()
        val (analyzedProd, ingList) = GeminiAnalysisEngine.analyzeIngredientText(rawText, dbIngs)
        val profile = userProfileDao.getProfileSync() ?: UserHealthProfile()

        val scoreBreakdown = HealthScoreCalculator.calculate(
            ingredients = ingList,
            sugarGrams = analyzedProd.sugarGrams,
            sodiumMg = analyzedProd.sodiumMg,
            saturatedFatGrams = analyzedProd.saturatedFatGrams,
            hasArtificialSweeteners = analyzedProd.hasArtificialSweeteners,
            hasPreservatives = analyzedProd.hasPreservatives,
            novaGroup = analyzedProd.novaGroup
        )

        val finalProd = analyzedProd.copy(healthScore = scoreBreakdown.totalScore)
        productDao.insertProduct(finalProd)

        scanHistoryDao.insertHistory(
            ScanHistoryEntity(
                barcode = finalProd.barcode,
                productName = finalProd.productName,
                brand = finalProd.brand,
                healthScore = scoreBreakdown.totalScore,
                scanType = "OCR_LABEL"
            )
        )

        val warnings = PersonalizedWarningEngine.generateWarnings(finalProd, ingList, profile)

        return@withContext FullProductAnalysis(
            product = finalProd,
            ingredients = ingList,
            healthScore = scoreBreakdown.totalScore,
            warnings = warnings,
            isFromDatabaseCache = false
        )
    }

    suspend fun analyzeImageLabel(bitmap: Bitmap): FullProductAnalysis = withContext(Dispatchers.IO) {
        // Step 1: Call NutriGuard FastAPI Backend (POST /api/v1/scan/label-image)
        // No silent fallback to local analysis in strict integration mode
        val parsedData = apiService.scanLabelImage(bitmap)
        val analyzedProd = parsedData.product
        val ingList = parsedData.ingredients

        // Step 2: Persist detected ingredients to Room
        if (ingList.isNotEmpty()) {
            ingredientDao.insertAll(ingList)
        }

        // Step 3: Calculate score & warnings aligned with user profile
        val profile = userProfileDao.getProfileSync() ?: UserHealthProfile()
        val scoreBreakdown = HealthScoreCalculator.calculate(
            ingredients = ingList,
            sugarGrams = analyzedProd.sugarGrams,
            sodiumMg = analyzedProd.sodiumMg,
            saturatedFatGrams = analyzedProd.saturatedFatGrams,
            hasArtificialSweeteners = analyzedProd.hasArtificialSweeteners,
            hasPreservatives = analyzedProd.hasPreservatives,
            novaGroup = analyzedProd.novaGroup
        )

        // Combine backend warnings with personalized user profile rules
        val personalizedWarnings = PersonalizedWarningEngine.generateWarnings(analyzedProd, ingList, profile)
        val allWarnings = (parsedData.warnings + personalizedWarnings).distinctBy { "${it.title}_${it.condition}" }

        val finalProd = analyzedProd.copy(healthScore = scoreBreakdown.totalScore)

        // Step 4: Persist product & scan history to Room
        productDao.insertProduct(finalProd)
        scanHistoryDao.insertHistory(
            ScanHistoryEntity(
                barcode = finalProd.barcode,
                productName = finalProd.productName,
                brand = finalProd.brand,
                healthScore = scoreBreakdown.totalScore,
                scanType = "LABEL_IMAGE"
            )
        )

        return@withContext FullProductAnalysis(
            product = finalProd,
            ingredients = ingList,
            healthScore = scoreBreakdown.totalScore,
            warnings = allWarnings,
            isFromDatabaseCache = false
        )
    }

    private suspend fun fetchIngredientsForProduct(product: ProductEntity): List<IngredientEntity> {
        val ids = product.ingredientIds.split(",").map { it.trim() }.filter { it.isNotBlank() }
        val result = mutableListOf<IngredientEntity>()
        ids.forEach { id ->
            val entity = ingredientDao.getIngredientByIdOrEnum(id)
            if (entity != null) {
                result.add(entity)
            } else {
                result.add(com.example.service.ocr.OcrNormalizer.createSyntheticIngredient(id))
            }
        }
        return result
    }
}
