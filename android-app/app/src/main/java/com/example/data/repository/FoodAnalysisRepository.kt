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

/**
 * The subset of [FoodAnalysisRepository] `MainViewModel` actually
 * depends on. Extracted purely so ViewModel/UI tests can swap in an
 * in-memory fake (no Room, no network — see `FakeProductAnalysisSource`
 * in the test sources) without constructing real DAOs or an
 * [NutriGuardApiService]; the DI container still wires the real
 * [FoodAnalysisRepository] here in production, unchanged.
 */
interface ProductAnalysisSource {
    val allIngredients: Flow<List<IngredientEntity>>
    val allProducts: Flow<List<ProductEntity>>
    val scanHistory: Flow<List<ScanHistoryEntity>>
    val userProfile: Flow<UserHealthProfile?>

    suspend fun saveUserProfile(profile: UserHealthProfile)
    suspend fun analyzeBarcode(barcode: String): FullProductAnalysis
    suspend fun analyzeOcrText(rawText: String): FullProductAnalysis

    /**
     * [barcode], when non-null, is forwarded to the backend's optional
     * multipart `barcode` field (see [com.example.data.remote.NutriGuardApiService.scanLabelImage])
     * so this label photo combines with an already-known barcode
     * identity into ONE canonical product instead of a synthetic
     * `img_...` row. Must always be a real barcode -- never a synthetic
     * id from a previous standalone response.
     */
    suspend fun analyzeImageLabel(bitmap: Bitmap, barcode: String? = null): FullProductAnalysis
}

class FoodAnalysisRepository(
    private val ingredientDao: IngredientDao,
    private val productDao: ProductDao,
    private val userProfileDao: UserHealthProfileDao,
    private val scanHistoryDao: ScanHistoryDao,
    private val apiService: NutriGuardApiService
) : ProductAnalysisSource {

    override val allIngredients: Flow<List<IngredientEntity>> = ingredientDao.getAllIngredients()
    override val allProducts: Flow<List<ProductEntity>> = productDao.getAllProducts()
    override val scanHistory: Flow<List<ScanHistoryEntity>> = scanHistoryDao.getAllHistory()
    override val userProfile: Flow<UserHealthProfile?> = userProfileDao.getProfile()

    override suspend fun saveUserProfile(profile: UserHealthProfile) = withContext(Dispatchers.IO) {
        userProfileDao.saveProfile(profile)
    }

    suspend fun searchIngredients(query: String): Flow<List<IngredientEntity>> {
        return ingredientDao.searchIngredients(query)
    }

    /**
     * Barcode -> product analysis. Local Room cache is checked first
     * (fast path, matches prior behavior for a barcode this device has
     * already resolved). On a local miss, this now calls the backend's
     * `POST /api/v1/scan/barcode` (local DB + multi-source discovery
     * server-side) via [NutriGuardApiService.scanBarcode] instead of
     * fabricating a sample product for an unrecognized barcode — see
     * the removed `GeminiAnalysisEngine`-backed synthetic-mock branch
     * this replaces. Exceptions from `scanBarcode` (including
     * [com.example.data.remote.LabelScanRequiredException]) are
     * intentionally NOT caught here; they propagate to the caller
     * (`MainViewModel`), exactly like [analyzeImageLabel] already does
     * for its own backend call.
     */
    override suspend fun analyzeBarcode(barcode: String): FullProductAnalysis = withContext(Dispatchers.IO) {
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
        }

        // Not cached locally: ask the backend. May throw
        // LabelScanRequiredException / BarcodeNetworkException /
        // BarcodeTimeoutException / BarcodeServerException /
        // BarcodeParseException -- all handled by MainViewModel, never
        // silently swallowed into a fabricated product here.
        val parsedData = apiService.scanBarcode(barcode)
        val analyzedProd = parsedData.product
        val ingList = parsedData.ingredients

        if (ingList.isNotEmpty()) {
            ingredientDao.insertAll(ingList)
        }

        val scoreBreakdown = HealthScoreCalculator.calculate(
            ingredients = ingList,
            sugarGrams = analyzedProd.sugarGrams,
            sodiumMg = analyzedProd.sodiumMg,
            saturatedFatGrams = analyzedProd.saturatedFatGrams,
            hasArtificialSweeteners = analyzedProd.hasArtificialSweeteners,
            hasPreservatives = analyzedProd.hasPreservatives,
            novaGroup = analyzedProd.novaGroup
        )

        val personalizedWarnings = PersonalizedWarningEngine.generateWarnings(analyzedProd, ingList, profile)
        val allWarnings = (parsedData.warnings + personalizedWarnings).distinctBy { "${it.title}_${it.condition}" }

        val finalProd = analyzedProd.copy(healthScore = scoreBreakdown.totalScore)
        productDao.insertProduct(finalProd)

        scanHistoryDao.insertHistory(
            ScanHistoryEntity(
                barcode = finalProd.barcode,
                productName = finalProd.productName,
                brand = finalProd.brand,
                healthScore = scoreBreakdown.totalScore,
                scanType = "BARCODE"
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

    override suspend fun analyzeOcrText(rawText: String): FullProductAnalysis = withContext(Dispatchers.IO) {
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

    override suspend fun analyzeImageLabel(bitmap: Bitmap, barcode: String?): FullProductAnalysis = withContext(Dispatchers.IO) {
        // Step 1: Call NutriGuard FastAPI Backend (POST /api/v1/scan/label-image)
        // No silent fallback to local analysis in strict integration mode.
        // May throw LabelScanRequiredException (a genuine partial result,
        // not a generic error -- see NutriGuardApiService.scanLabelImage's
        // docs) / BarcodeNetworkException / BarcodeTimeoutException /
        // BarcodeServerException / BarcodeAuthException / BarcodeParseException
        // -- all handled by MainViewModel, never silently swallowed here.
        val parsedData = apiService.scanLabelImage(bitmap, barcode)
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
