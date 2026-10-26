package com.example.util

import com.example.data.model.ProductEntity

/** Legacy Boolean false may mean either 'no' or 'not stated by provider'. */
internal fun ProductEntity.dietaryDisplayStatus(kind: String): String {
    val ingredients = confirmedIngredientText(rawIngredientText)
    fun mentions(pattern: String) = Regex(pattern, RegexOption.IGNORE_CASE).containsMatchIn(ingredients)
    val key = when (kind) {
        "gluten" -> "glutenFree"
        "lactose" -> "lactoseFree"
        else -> kind
    }
    val explicit = Regex("\"${Regex.escape(key)}\"\\s*:\\s*\"(yes|no|unknown)\"")
        .find(dietaryStatusesJson)?.groupValues?.get(1).orEmpty()
    if (explicit in setOf("yes", "no", "unknown")) return explicit
    // Old Room rows had only Boolean flags, including unsupported defaults.
    // A named ingredient can establish a contradiction; positive status
    // requires the new backend evidence field above.
    return when (kind) {
        "gluten" -> if (mentions("\\b(wheat|barley|rye|gluten)\\b|пшениц|ечемик|ръж")) "no" else "unknown"
        "lactose" -> if (mentions("\\b(lactose|milk powder)\\b|лактоза")) "no" else "unknown"
        "vegan" -> if (mentions("\\b(milk|egg|eggs|meat|beef|pork|chicken|fish|gelatin)\\b|мляко|яйц")) "no" else "unknown"
        "vegetarian" -> if (mentions("\\b(meat|beef|pork|chicken|fish|gelatin)\\b")) "no" else "unknown"
        "halal", "kosher" -> "unknown"
        else -> "unknown"
    }
}

internal fun confirmedIngredientText(raw: String): String = raw.split(
    Regex("(?i)(?:[.,;]\\s*|\\n\\s*)(?:may\\s+(?:also\\s+)?contain|може\\s+да\\s+съдържа|възможни\\s+следи\\s+от)"), limit = 2
).first()

internal fun ProductEntity.namedAllergens(): Set<String> {
    val text = confirmedIngredientText(rawIngredientText)
    val stored = allergensDetected.split(",").map { it.trim() }.filter { it.isNotBlank() && !it.equals("none", true) }.toSet()
    val explicit = listOf(
        "Gluten" to "\\b(wheat|barley|rye)\\b|пшениц|ечемик|ръж",
        "Milk" to "\\b(milk|whey)\\b|мляко|млеч|суроват",
        "Peanuts" to "\\bpeanuts?\\b|фъстъц",
        "Eggs" to "\\beggs?\\b|яйц|яйчен",
        "Soybeans" to "\\b(soy|soya|soybeans)\\b|соев|соя",
        "Nuts" to "\\b(hazelnut|hazelnuts|almond|almonds)\\b|лешниц|бадем",
        "Sesame" to "\\bsesame\\b|сусам"
    ).filter { (_, pattern) -> Regex(pattern, RegexOption.IGNORE_CASE).containsMatchIn(text) }
        .map { it.first }
    return stored + explicit
}
