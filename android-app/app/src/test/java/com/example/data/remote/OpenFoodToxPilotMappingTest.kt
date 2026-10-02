package com.example.data.remote

import com.example.data.remote.dto.IngredientDto
import com.example.data.remote.dto.toEntities
import com.example.ui.i18n.AppLanguage
import com.example.ui.model.localizedContent
import com.example.ui.components.intakeText
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [36])
class OpenFoodToxPilotMappingTest {
    @Test fun `numeric intake retains narrative exclusions and deduplicates only equivalent text`() {
        val combined = intakeText("0–40 mg/kg body weight/day", "Not applicable to people with PKU")
        assertTrue(combined.contains("0–40"))
        assertTrue(combined.contains("PKU"))
        assertEquals("0–40 mg/kg body weight/day", intakeText("0–40 mg/kg body weight/day", "0 - 40 mg/kg bw/day"))
    }
    private fun fixture(code: String): JSONObject = JSONObject(
        requireNotNull(javaClass.classLoader?.getResourceAsStream("${code}_ingredient_out.json"))
            .bufferedReader().use { it.readText() }
    )

    @Test fun `all four published profiles survive dto entity and offline localization`() {
        listOf("e250", "e150d", "e330", "e951").forEach { code ->
            val json = fixture(code)
            val dto = IngredientDto.fromJson(json)
            val entity = listOf(dto).toEntities("pilot").single()
            val bg = json.getJSONObject("localizations").getJSONObject("bg")
            assertEquals(json.optString("effectConditions"), entity.effectConditions.orEmpty())
            assertEquals(json.optString("dietaryGuidance"), entity.dietaryGuidance.orEmpty())
            assertEquals(bg.getString("commonName"), entity.localizedContent(AppLanguage.BULGARIAN).commonName)
            assertEquals(bg.optString("effectConditions"), entity.localizedContent(AppLanguage.BULGARIAN).effectConditions)
            assertEquals(json.getBoolean("riskAssessmentAvailable"), entity.riskAssessmentAvailable)
            val cachedBg = JSONObject(entity.localizationsJson).getJSONObject("bg")
            assertEquals("DRAFT", cachedBg.getString("translationStatus"))
            assertTrue(cachedBg.getBoolean("ownerApprovedWithoutReview"))
        }
    }

    @Test fun `aspartame numeric ADI does not discard the PKU exclusion`() {
        val entity = listOf(IngredientDto.fromJson(fixture("e951"))).toEntities("pilot").single()
        assertEquals(40.0, entity.adiMaxMgPerKgBwPerDay!!, 0.0)
        assertEquals("PER_KG_BODY_WEIGHT", entity.adiPopulationScope)
        assertTrue(entity.localizedContent(AppLanguage.ENGLISH).effectConditions.contains("PKU"))
        assertTrue(entity.localizedContent(AppLanguage.BULGARIAN).effectConditions.contains("ФКУ"))
    }

    @Test fun `E150d has neither invented numeric ADI nor a risk assessment`() {
        val entity = listOf(IngredientDto.fromJson(fixture("e150d"))).toEntities("pilot").single()
        assertNull(entity.adiMaxMgPerKgBwPerDay)
        assertFalse(entity.riskAssessmentAvailable)
    }

    @Test fun `missing or null new fields keep old responses compatible and fallback per field`() {
        val json = fixture("e951")
        json.put("effectConditions", "Canonical caveat")
        json.put("dietaryGuidance", JSONObject.NULL)
        json.remove("adiPopulationScope")
        json.getJSONObject("localizations").getJSONObject("bg").remove("effectConditions")
        json.getJSONObject("localizations").getJSONObject("en").remove("effectConditions")
        val entity = listOf(IngredientDto.fromJson(json)).toEntities("pilot").single()
        assertEquals("Canonical caveat", entity.localizedContent(AppLanguage.BULGARIAN).effectConditions)
        assertNull(entity.dietaryGuidance)
        assertNull(entity.adiPopulationScope)
    }
}
