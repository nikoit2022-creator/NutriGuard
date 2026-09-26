package com.example.ui.model

import com.example.ui.i18n.AppLanguage
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [36])
class IngredientSummaryPresentationTest {
    private val summary = """
        {
          "humanReviewed": false,
          "sections": [
            {"kind":"ORIGIN","text":"Made by controlled heating.","citationIds":["fda"]},
            {"kind":"EFFECTS","text":"Studies discuss exposure-dependent effects.","citationIds":["study"]}
          ],
          "citations": [
            {"id":"fda","label":"FDA","url":"https://example.test/fda","documentDate":"2025","accessType":"OFFICIAL_PAGE"}
          ],
          "localizations": {
            "bg": {
              "translationStatus":"REVIEWED",
              "translationSource":"HUMAN_CURATED",
              "sections":[
                {"kind":"ORIGIN","text":"Получава се чрез контролирано нагряване."},
                {"kind":"EFFECTS","text":"Проучванията разглеждат ефекти според експозицията."}
              ]
            }
          }
        }
    """.trimIndent()

    @Test
    fun englishUsesCanonicalSectionsAndCitations() {
        val parsed = parseIngredientSummary(summary, AppLanguage.ENGLISH)!!
        assertFalse(parsed.humanReviewed)
        assertEquals("Made by controlled heating.", parsed.sections.first().text)
        assertEquals("https://example.test/fda", parsed.citations.single().url)
    }

    @Test
    fun bulgarianUsesOnlyReviewedLocalization() {
        val parsed = parseIngredientSummary(summary, AppLanguage.BULGARIAN)!!
        assertEquals("Получава се чрез контролирано нагряване.", parsed.sections.first().text)
    }

    @Test
    fun draftBulgarianFallsBackToEnglish() {
        val draft = summary.replace("REVIEWED", "DRAFT")
        assertEquals(
            "Made by controlled heating.",
            parseIngredientSummary(draft, AppLanguage.BULGARIAN)!!.sections.first().text
        )
    }

    @Test
    fun malformedOrEmptySummaryIsHidden() {
        assertNull(parseIngredientSummary("not-json", AppLanguage.ENGLISH))
        assertNull(parseIngredientSummary("{\"sections\":[]}", AppLanguage.ENGLISH))
    }
}
