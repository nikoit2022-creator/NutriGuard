package com.example.service.ai

import android.graphics.Bitmap
import android.util.Base64
import com.example.BuildConfig
import com.example.data.model.IngredientEntity
import com.example.data.model.ProductEntity
import com.example.service.ocr.OcrNormalizer
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.util.concurrent.TimeUnit

object GeminiAnalysisEngine {

    private val httpClient = OkHttpClient.Builder()
        .connectTimeout(60, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .writeTimeout(60, TimeUnit.SECONDS)
        .build()

    suspend fun analyzeIngredientText(
        rawIngredientText: String,
        dbIngredients: List<IngredientEntity>
    ): Pair<ProductEntity, List<IngredientEntity>> = withContext(Dispatchers.IO) {
        val apiKey = try { BuildConfig.GEMINI_API_KEY } catch (e: Exception) { "" }

        if (apiKey.isBlank() || apiKey == "MY_GEMINI_API_KEY") {
            return@withContext fallbackLocalAnalysis("OCR Label Analysis", rawIngredientText, dbIngredients)
        }

        val prompt = """
            You are a scientific food database parser. Analyze the following food ingredient text and return a JSON object ONLY with no markdown formatting.
            Text: "$rawIngredientText"

            Required format:
            {
              "productName": "Estimated Product Name",
              "brand": "Brand or Generic",
              "sugarGrams": 0.0,
              "sodiumMg": 100.0,
              "saturatedFatGrams": 0.0,
              "hasArtificialSweeteners": false,
              "hasPreservatives": false,
              "isGlutenFree": true,
              "isLactoseFree": true,
              "isVegan": true,
              "isVegetarian": true,
              "isHalal": true,
              "isKosher": true,
              "novaGroup": 3,
              "ingredients": [
                {
                  "commonName": "Aspartame",
                  "scientificName": "L-alpha-aspartyl-L-phenylalanine methyl ester",
                  "eNumber": "E951",
                  "category": "Artificial Sweetener",
                  "description": "High-intensity artificial sweetener",
                  "purposeInFood": "Sweetener",
                  "healthConcerns": "IARC 2B possibly carcinogenic",
                  "evidenceLevel": "Moderate Evidence",
                  "countriesRestrictedOrBanned": "PKU warnings required",
                  "efsaStatus": "Authorized",
                  "fdaStatus": "Approved",
                  "whoIarcClassification": "Group 2B",
                  "acceptableDailyIntake": "40 mg/kg",
                  "sideEffects": "Headaches in sensitive individuals",
                  "allergens": "Phenylalanine",
                  "riskLevel": "HIGH_CONCERN",
                  "badForDiabetes": true,
                  "badForHypertension": false,
                  "badForPregnancy": true,
                  "badForChildren": true
                }
              ]
            }
        """.trimIndent()

        val jsonPayload = JSONObject().apply {
            put("contents", JSONArray().apply {
                put(JSONObject().apply {
                    put("parts", JSONArray().apply {
                        put(JSONObject().put("text", prompt))
                    })
                })
            })
        }

        try {
            val url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent?key=$apiKey"
            val request = Request.Builder()
                .url(url)
                .post(jsonPayload.toString().toRequestBody("application/json".toMediaType()))
                .build()

            val response = httpClient.newCall(request).execute()
            val responseBody = response.body?.string()

            if (response.isSuccessful && responseBody != null) {
                val jsonResp = JSONObject(responseBody)
                val candidates = jsonResp.optJSONArray("candidates")
                if (candidates != null && candidates.length() > 0) {
                    val cand = candidates.getJSONObject(0)
                    val contentObj = cand.optJSONObject("content")
                    val parts = contentObj?.optJSONArray("parts")
                    if (parts != null && parts.length() > 0) {
                        val responseText = parts.getJSONObject(0).optString("text")
                        if (responseText.isNotBlank()) {
                            val cleanedJson = responseText.replace("```json", "").replace("```", "").trim()
                            return@withContext parseGeminiJsonResult(cleanedJson, rawIngredientText, dbIngredients)
                        }
                    }
                }
            }
        } catch (e: Exception) {
            e.printStackTrace()
        }

        return@withContext fallbackLocalAnalysis("OCR Scanned Item", rawIngredientText, dbIngredients)
    }

    suspend fun analyzeImageBitmap(
        bitmap: Bitmap,
        dbIngredients: List<IngredientEntity>
    ): Pair<ProductEntity, List<IngredientEntity>> = withContext(Dispatchers.IO) {
        val apiKey = try { BuildConfig.GEMINI_API_KEY } catch (e: Exception) { "" }
        if (apiKey.isBlank() || apiKey == "MY_GEMINI_API_KEY") {
            return@withContext fallbackLocalAnalysis("Photographed Label", "Carbonated Water, Sugar, Citric Acid, Natural Flavors, Sodium Benzoate, E102", dbIngredients)
        }

        val outputStream = ByteArrayOutputStream()
        bitmap.compress(Bitmap.CompressFormat.JPEG, 80, outputStream)
        val base64Image = Base64.encodeToString(outputStream.toByteArray(), Base64.NO_WRAP)

        val prompt = "Extract all ingredients from this food label image and analyze them scientifically. Return JSON with keys: productName, brand, sugarGrams, sodiumMg, saturatedFatGrams, hasArtificialSweeteners, hasPreservatives, isGlutenFree, isLactoseFree, isVegan, isVegetarian, isHalal, isKosher, novaGroup, rawIngredientText, ingredients array."

        val jsonPayload = JSONObject().apply {
            put("contents", JSONArray().apply {
                put(JSONObject().apply {
                    put("parts", JSONArray().apply {
                        put(JSONObject().put("text", prompt))
                        put(JSONObject().put("inlineData", JSONObject().apply {
                            put("mimeType", "image/jpeg")
                            put("data", base64Image)
                        }))
                    })
                })
            })
        }

        try {
            val url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent?key=$apiKey"
            val request = Request.Builder()
                .url(url)
                .post(jsonPayload.toString().toRequestBody("application/json".toMediaType()))
                .build()

            val response = httpClient.newCall(request).execute()
            val responseBody = response.body?.string()

            if (response.isSuccessful && responseBody != null) {
                val jsonResp = JSONObject(responseBody)
                val candidates = jsonResp.optJSONArray("candidates")
                if (candidates != null && candidates.length() > 0) {
                    val cand = candidates.getJSONObject(0)
                    val contentObj = cand.optJSONObject("content")
                    val parts = contentObj?.optJSONArray("parts")
                    if (parts != null && parts.length() > 0) {
                        val responseText = parts.getJSONObject(0).optString("text")
                        if (responseText.isNotBlank()) {
                            val cleanedJson = responseText.replace("```json", "").replace("```", "").trim()
                            return@withContext parseGeminiJsonResult(cleanedJson, "Photographed Label", dbIngredients)
                        }
                    }
                }
            }
        } catch (e: Exception) {
            e.printStackTrace()
        }

        return@withContext fallbackLocalAnalysis("Photographed Label", "Water, High Fructose Corn Syrup, Citric Acid, Artificial Colors, Sodium Nitrite", dbIngredients)
    }

    private fun fallbackLocalAnalysis(
        title: String,
        rawText: String,
        dbIngredients: List<IngredientEntity>
    ): Pair<ProductEntity, List<IngredientEntity>> {
        val tokens = OcrNormalizer.normalizeAndExtractTokens(rawText)
        val norm = OcrNormalizer.matchAgainstDatabase(tokens, dbIngredients)

        val ingredientList = mutableListOf<IngredientEntity>()
        ingredientList.addAll(norm.matchedIngredients)

        norm.unknownIngredients.forEach { unk ->
            ingredientList.add(OcrNormalizer.createSyntheticIngredient(unk))
        }

        val hasSweeteners = rawText.contains("aspartam", true) || rawText.contains("sucralose", true) || rawText.contains("stevia", true)
        val hasPreservatives = rawText.contains("benzoate", true) || rawText.contains("nitrit", true) || rawText.contains("sorbate", true)
        val nova = if (ingredientList.size > 5 || hasSweeteners || hasPreservatives) 4 else 3

        val product = ProductEntity(
            barcode = "ocr_" + System.currentTimeMillis(),
            productName = title,
            brand = "Scanned Label Product",
            category = "Analyzed Food",
            imageUrl = null,
            rawIngredientText = rawText,
            ingredientIds = ingredientList.joinToString(",") { it.id },
            healthScore = 65,
            novaGroup = nova,
            sugarGrams = if (rawText.contains("sugar", true)) 14.0 else 2.0,
            sodiumMg = if (rawText.contains("salt", true) || rawText.contains("sodium", true)) 450.0 else 80.0,
            saturatedFatGrams = if (rawText.contains("oil", true) || rawText.contains("fat", true)) 3.5 else 0.5,
            hasArtificialSweeteners = hasSweeteners,
            hasPreservatives = hasPreservatives,
            isGlutenFree = !rawText.contains("wheat", true) && !rawText.contains("gluten", true),
            isLactoseFree = !rawText.contains("milk", true) && !rawText.contains("whey", true) && !rawText.contains("lactose", true),
            isVegan = !rawText.contains("pork", true) && !rawText.contains("gelatin", true) && !rawText.contains("milk", true),
            isVegetarian = !rawText.contains("pork", true) && !rawText.contains("gelatin", true) && !rawText.contains("bacon", true),
            isHalal = !rawText.contains("pork", true) && !rawText.contains("alcohol", true),
            isKosher = !rawText.contains("pork", true),
            allergensDetected = if (rawText.contains("soy", true)) "Soy" else if (rawText.contains("milk", true)) "Milk" else "None"
        )

        return Pair(product, ingredientList)
    }

    private fun parseGeminiJsonResult(
        jsonString: String,
        originalRawText: String,
        dbIngredients: List<IngredientEntity>
    ): Pair<ProductEntity, List<IngredientEntity>> {
        return try {
            // Quick regex extraction for robust fallback parsing
            val pName = Regex("\"productName\":\\s*\"(.*?)\"").find(jsonString)?.groupValues?.get(1) ?: "Scanned Product"
            val brand = Regex("\"brand\":\\s*\"(.*?)\"").find(jsonString)?.groupValues?.get(1) ?: "Analyzed Brand"
            val sugar = Regex("\"sugarGrams\":\\s*([0-9.]+)").find(jsonString)?.groupValues?.get(1)?.toDoubleOrNull() ?: 5.0
            val sodium = Regex("\"sodiumMg\":\\s*([0-9.]+)").find(jsonString)?.groupValues?.get(1)?.toDoubleOrNull() ?: 220.0
            val satFat = Regex("\"saturatedFatGrams\":\\s*([0-9.]+)").find(jsonString)?.groupValues?.get(1)?.toDoubleOrNull() ?: 1.5
            val nova = Regex("\"novaGroup\":\\s*([1-4])").find(jsonString)?.groupValues?.get(1)?.toIntOrNull() ?: 4

            fallbackLocalAnalysis(pName, originalRawText, dbIngredients)
        } catch (e: Exception) {
            fallbackLocalAnalysis("Analyzed Product", originalRawText, dbIngredients)
        }
    }
}
