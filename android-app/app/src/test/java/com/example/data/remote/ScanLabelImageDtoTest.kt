package com.example.data.remote

import com.example.data.model.RiskLevel
import com.example.data.remote.dto.IngredientDto
import com.example.data.remote.dto.ScanLabelImageResponseDto
import com.example.data.remote.dto.toEntities
import com.example.data.remote.dto.toParsedEntities
import com.example.util.WarningSeverity
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [36])
class ScanLabelImageDtoTest {

    private val fullResponseJson = """
        {
          "product": {
            "barcode": "0123456789012",
            "productName": "Crunchy Oat Bar",
            "brand": "Sunrise Foods",
            "category": "Snack Bars",
            "imageUrl": "https://cdn.nutriguard.example/images/oat-bar.jpg",
            "rawIngredientText": "Oats, Sugar, Palm Oil, Sodium Benzoate, Aspartame",
            "ingredientIds": "sugar,e211_sodium_benzoate,e951_aspartame",
            "healthScore": 42,
            "novaGroup": 4,
            "sugarGrams": 18.5,
            "sodiumMg": 210.0,
            "saturatedFatGrams": 6.25,
            "hasArtificialSweeteners": true,
            "hasPreservatives": true,
            "isGlutenFree": false,
            "isLactoseFree": true,
            "isVegan": true,
            "isVegetarian": true,
            "isHalal": true,
            "isKosher": false,
            "allergensDetected": "Peanuts, Soy"
          },
          "ingredients": [
            {
              "id": "e951_aspartame",
              "commonName": "Aspartame",
              "scientificName": "L-aspartyl-L-phenylalanine methyl ester",
              "eNumber": "E951",
              "category": "Artificial Sweetener",
              "description": "A low-calorie artificial sweetener.",
              "purposeInFood": "Sweetening agent",
              "healthConcerns": "Linked to headaches in sensitive individuals.",
              "evidenceLevel": "Strong Scientific Consensus",
              "countriesRestrictedOrBanned": "None",
              "efsaStatus": "Authorized (ADI 40 mg/kg)",
              "fdaStatus": "Approved with limits",
              "whoIarcClassification": "Group 2B - Possibly Carcinogenic",
              "acceptableDailyIntake": "0 - 40 mg/kg bw/day",
              "sideEffects": "Headaches, GI distress in sensitive individuals",
              "allergens": "Contains Phenylalanine",
              "references": "EFSA Journal 2013;11(12):3496",
              "riskLevel": "HIGH_CONCERN",
              "isGluten": false,
              "isLactose": false,
              "isVegan": true,
              "isVegetarian": true,
              "isHalal": true,
              "isKosher": true,
              "badForDiabetes": false,
              "badForHypertension": false,
              "badForKidneyDisease": true,
              "badForGout": false,
              "badForPregnancy": true,
              "badForChildren": true,
              "badForHighCholesterol": false
            }
          ],
          "healthScore": 42,
          "warnings": [
            {
              "title": "Artificial Sweetener Alert",
              "description": "Contains aspartame, which some sensitive individuals should avoid.",
              "condition": "General",
              "triggerFactor": "Aspartame (E951)",
              "severity": "HIGH"
            }
          ],
          "isFromDatabaseCache": true
        }
    """.trimIndent()

    // TEST 1: complete realistic backend response maps to the correct domain values.
    @Test
    fun `fromJson and toParsedEntities map a complete backend response correctly`() {
        val dto = ScanLabelImageResponseDto.fromJson(JSONObject(fullResponseJson))

        // Top-level fields parsed from the correct locations (catches wrong nesting).
        assertEquals(42, dto.healthScore)
        assertEquals(true, dto.isFromDatabaseCache)

        val parsed = dto.toParsedEntities()
        val product = parsed.product

        // Product fields must come from the nested "product" object, camelCase keys.
        assertEquals("0123456789012", product.barcode)
        assertEquals("Crunchy Oat Bar", product.productName)
        assertEquals("Sunrise Foods", product.brand)
        assertEquals("Snack Bars", product.category)
        assertEquals("https://cdn.nutriguard.example/images/oat-bar.jpg", product.imageUrl)
        assertEquals("Oats, Sugar, Palm Oil, Sodium Benzoate, Aspartame", product.rawIngredientText)
        assertEquals(42, product.healthScore)
        assertEquals(4, product.novaGroup)

        // Nutrition values read directly from product, not a nested "nutrition_per_100g".
        assertEquals(18.5, product.sugarGrams, 0.0001)
        assertEquals(210.0, product.sodiumMg, 0.0001)
        assertEquals(6.25, product.saturatedFatGrams, 0.0001)
        assertTrue(product.hasArtificialSweeteners)
        assertTrue(product.hasPreservatives)

        // Dietary suitability read directly from product, not a nested "dietary_suitability".
        assertFalse(product.isGlutenFree)
        assertTrue(product.isLactoseFree)
        assertTrue(product.isVegan)
        assertTrue(product.isVegetarian)
        assertTrue(product.isHalal)
        assertFalse(product.isKosher)

        // allergensDetected is the backend's string value, not a joined list.
        assertEquals("Peanuts, Soy", product.allergensDetected)

        // Ingredient fields, camelCase, flat badFor* (no nested health_profile_triggers).
        assertEquals(1, parsed.ingredients.size)
        val ingredient = parsed.ingredients[0]
        assertEquals("e951_aspartame", ingredient.id)
        assertEquals("Aspartame", ingredient.commonName)
        assertEquals("L-aspartyl-L-phenylalanine methyl ester", ingredient.scientificName)
        assertEquals("E951", ingredient.eNumber)
        assertEquals(RiskLevel.HIGH_CONCERN, ingredient.riskLevel)
        assertFalse(ingredient.badForDiabetes)
        assertFalse(ingredient.badForHypertension)
        assertTrue(ingredient.badForKidneyDisease)
        assertFalse(ingredient.badForGout)
        assertTrue(ingredient.badForPregnancy)
        assertTrue(ingredient.badForChildren)
        assertFalse(ingredient.badForHighCholesterol)

        // Warning triggerFactor mapped from the correct camelCase key.
        assertEquals(1, parsed.warnings.size)
        val warning = parsed.warnings[0]
        assertEquals("Artificial Sweetener Alert", warning.title)
        assertEquals("Aspartame (E951)", warning.triggerFactor)
        assertEquals(WarningSeverity.HIGH, warning.severity)

        // Top-level healthScore (not the old 50 fallback) drives the mapped product score.
        assertEquals(42, product.healthScore)
    }

    // TEST 2: missing optional fields still fall back to the existing intended defaults.
    @Test
    fun `fromJson and toParsedEntities apply existing fallback defaults when fields are missing`() {
        val minimalJson = """
            {
              "product": {},
              "ingredients": [],
              "warnings": []
            }
        """.trimIndent()

        val dto = ScanLabelImageResponseDto.fromJson(JSONObject(minimalJson))
        val parsed = dto.toParsedEntities()
        val product = parsed.product

        assertTrue(product.barcode.startsWith("SYNTH_IMG_"))
        assertEquals("Scanned Product", product.productName)
        assertEquals("Unknown Brand", product.brand)
        assertEquals("General Food", product.category)
        assertEquals("", product.rawIngredientText)
        assertEquals(50, product.healthScore)
        assertEquals(3, product.novaGroup)
        assertEquals(0.0, product.sugarGrams, 0.0001)
        assertEquals(0.0, product.sodiumMg, 0.0001)
        assertEquals(0.0, product.saturatedFatGrams, 0.0001)
        assertFalse(product.hasArtificialSweeteners)
        assertFalse(product.hasPreservatives)
        assertTrue(product.isGlutenFree)
        assertTrue(product.isLactoseFree)
        assertFalse(product.isVegan)
        assertFalse(product.isVegetarian)
        assertTrue(product.isHalal)
        assertTrue(product.isKosher)
        assertEquals("", product.allergensDetected)
        assertTrue(parsed.ingredients.isEmpty())
        assertTrue(parsed.warnings.isEmpty())
    }

    @Test
    fun `fromJson and toParsedEntities apply existing fallback defaults when product is absent entirely`() {
        val noProductJson = """{ "ingredients": [], "warnings": [] }"""

        val dto = ScanLabelImageResponseDto.fromJson(JSONObject(noProductJson))
        val parsed = dto.toParsedEntities()

        assertTrue(parsed.product.barcode.startsWith("SYNTH_IMG_"))
        assertEquals("Scanned Product", parsed.product.productName)
        assertEquals(50, parsed.product.healthScore)
    }

    // TEST 3: the multipart request part uses field name "image", not "file".
    @Test
    fun `buildLabelImagePart uses field name image`() {
        val part = NutriGuardApiService.buildLabelImagePart(byteArrayOf(1, 2, 3))

        val contentDisposition = part.headers?.get("Content-Disposition") ?: ""
        assertTrue(
            "Expected Content-Disposition to declare name=\"image\", was: $contentDisposition",
            contentDisposition.contains("name=\"image\"")
        )
        assertFalse(
            "Multipart field must not use the old \"file\" name",
            contentDisposition.contains("name=\"file\"")
        )
        assertTrue(contentDisposition.contains("filename=\"food_label.jpg\""))
    }

    @Test
    fun `buildLabelImagePart preserves filename and content type`() {
        val part = NutriGuardApiService.buildLabelImagePart(byteArrayOf(1, 2, 3))

        assertEquals("image/jpeg", part.body.contentType().toString())
    }

    // The shared List<IngredientDto>.toEntities helper -- used both by
    // the full success path (toParsedEntities, above) and by a partial
    // labelScanRequired result's already-verified `ingredients` list
    // (see BackendErrorDetailsDto/LabelScanRequiredException), so both
    // render identically.
    @Test
    fun `toEntities synthesizes a stable id when the backend didn't send one`() {
        val entities = listOf(
            IngredientDto(id = null, commonName = "Sugar"),
            IngredientDto(id = "e211_sodium_benzoate", commonName = "Sodium Benzoate")
        ).toEntities("4006381333931")

        assertEquals("ING_4006381333931_0", entities[0].id)
        assertEquals("e211_sodium_benzoate", entities[1].id)
    }

    // TEST 4: the optional barcode multipart field (review requirement:
    // camera and gallery enrichment both attach a pending barcode via
    // this same request-body builder).

    @Test
    fun `buildLabelImageRequestBody includes a barcode part when a barcode is supplied`() {
        val body = NutriGuardApiService.buildLabelImageRequestBody(byteArrayOf(1, 2, 3), "4006381333931")

        assertEquals(2, body.parts.size)
        val barcodePart = body.parts.first {
            it.headers?.get("Content-Disposition")?.contains("name=\"barcode\"") == true
        }
        val buffer = okio.Buffer()
        barcodePart.body.writeTo(buffer)
        assertEquals("4006381333931", buffer.readUtf8())
    }

    @Test
    fun `buildLabelImageRequestBody omits the barcode part entirely when barcode is null`() {
        val body = NutriGuardApiService.buildLabelImageRequestBody(byteArrayOf(1, 2, 3), null)

        assertEquals(1, body.parts.size)
        assertFalse(
            body.parts.any { it.headers?.get("Content-Disposition")?.contains("name=\"barcode\"") == true }
        )
    }
}
