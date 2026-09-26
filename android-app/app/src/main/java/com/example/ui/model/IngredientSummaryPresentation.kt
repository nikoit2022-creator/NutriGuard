package com.example.ui.model

import com.example.data.remote.dto.cleanOrNull
import com.example.ui.i18n.AppLanguage
import org.json.JSONObject

data class IngredientSummarySectionUi(
    val kind: String,
    val text: String,
    val jurisdiction: String?
)

data class IngredientSummaryCitationUi(val label: String, val url: String)

data class IngredientSummaryUi(
    val humanReviewed: Boolean,
    val sections: List<IngredientSummarySectionUi>,
    val citations: List<IngredientSummaryCitationUi>
)

/** Fail closed: malformed or empty summary data is simply not presented. */
fun parseIngredientSummary(raw: String, language: AppLanguage): IngredientSummaryUi? = runCatching {
    val root = JSONObject(raw)
    val canonical = root.optJSONArray("sections") ?: return@runCatching null
    val localized = if (language == AppLanguage.BULGARIAN) {
        root.optJSONObject("localizations")
            ?.optJSONObject("bg")
            ?.takeIf { it.optString("translationStatus") == "REVIEWED" }
            ?.optJSONArray("sections")
    } else null
    val selected = localized ?: canonical
    val sections = buildList {
        for (index in 0 until selected.length()) {
            val item = selected.optJSONObject(index) ?: continue
            val text = item.optString("text").cleanOrNull() ?: continue
            add(IngredientSummarySectionUi(
                kind = item.optString("kind").cleanOrNull() ?: "OVERVIEW",
                text = text,
                jurisdiction = item.optString("jurisdiction").cleanOrNull()
            ))
        }
    }
    val citationsArray = root.optJSONArray("citations")
    val citations = buildList {
        if (citationsArray != null) for (index in 0 until citationsArray.length()) {
            val item = citationsArray.optJSONObject(index) ?: continue
            val url = item.optString("url").cleanOrNull() ?: continue
            val label = item.optString("label").cleanOrNull() ?: url
            add(IngredientSummaryCitationUi(label, url))
        }
    }.distinctBy { it.url }
    if (sections.isEmpty()) null else IngredientSummaryUi(
        humanReviewed = root.optBoolean("humanReviewed", false),
        sections = sections,
        citations = citations
    )
}.getOrNull()

fun summarySectionTitle(kind: String, jurisdiction: String?): String {
    val base = when (kind.uppercase()) {
        "ORIGIN" -> "Origin"
        "FUNCTION" -> "Function in food"
        "EFFECTS" -> "What studies report"
        "JURISDICTION" -> "Regulatory context"
        else -> "Overview"
    }
    return jurisdiction.cleanOrNull()?.let { "$base · $it" } ?: base
}
