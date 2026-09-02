package com.example.ui.components

import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.Science
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.data.model.IngredientEntity
import com.example.ui.model.IngredientSafetyRating
import com.example.ui.model.RecognizedIngredientUiModel
import com.example.ui.model.toRecognizedIngredientUiModel
import com.example.ui.theme.NutriGuardRadius
import com.example.ui.theme.NutriGuardSpacing
import com.example.ui.theme.PastelButter
import com.example.ui.theme.PastelInk
import com.example.ui.theme.PastelLavender
import com.example.ui.theme.PastelMint
import com.example.ui.theme.PastelPeach
import com.example.ui.theme.PastelRose
import com.example.ui.theme.RiskGreen
import com.example.ui.theme.RiskOrange
import com.example.ui.theme.RiskRed
import com.example.ui.theme.RiskYellow

private const val COLLAPSED_INGREDIENT_COUNT = 4

internal data class IngredientResultSection(
    val rating: IngredientSafetyRating,
    val title: String,
    val models: List<RecognizedIngredientUiModel>
)

private val RESULT_ORDER = listOf(
    IngredientSafetyRating.HIGH_CONCERN,
    IngredientSafetyRating.POTENTIAL_CONCERN,
    IngredientSafetyRating.MODERATE,
    IngredientSafetyRating.LOW_CONCERN,
    IngredientSafetyRating.LIMITED_DATA
)

internal fun categorizeIngredientResults(
    models: List<RecognizedIngredientUiModel>
): List<IngredientResultSection> = RESULT_ORDER.mapNotNull { rating ->
    models.filter { it.rating == rating }
        .takeIf { it.isNotEmpty() }
        ?.let { matches ->
            IngredientResultSection(
                rating = rating,
                title = categoryTitle(rating),
                models = matches
            )
        }
}

@Composable
fun RecognizedIngredientsSection(
    ingredients: List<IngredientEntity>,
    onIngredientClick: (IngredientEntity) -> Unit,
    modifier: Modifier = Modifier,
    initiallyExpanded: Boolean = false
) {
    if (ingredients.isEmpty()) return

    var expanded by rememberSaveable { mutableStateOf(initiallyExpanded) }
    val models = ingredients.map { it.toRecognizedIngredientUiModel() }
    val orderedModels = categorizeIngredientResults(models).flatMap { it.models }
    val visibleModels = if (expanded) orderedModels else orderedModels.take(COLLAPSED_INGREDIENT_COUNT)
    val visibleSections = categorizeIngredientResults(visibleModels)

    Column(
        modifier = modifier
            .fillMaxWidth()
            .animateContentSize(),
        verticalArrangement = Arrangement.spacedBy(NutriGuardSpacing.sm)
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Box(
                modifier = Modifier
                    .size(42.dp)
                    .clip(CircleShape)
                    .background(PastelLavender),
                contentAlignment = Alignment.Center
            ) {
                Icon(
                    imageVector = Icons.Default.Science,
                    contentDescription = null,
                    tint = PastelInk,
                    modifier = Modifier.size(21.dp)
                )
            }
            Spacer(modifier = Modifier.width(NutriGuardSpacing.sm))
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = "Recognized ingredients (${models.size})",
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.SemiBold,
                    color = PastelInk
                )
                Text(
                    text = "Verified cards open a detailed scientific profile",
                    style = MaterialTheme.typography.bodySmall,
                    color = PastelInk.copy(alpha = 0.66f)
                )
            }
        }

        visibleSections.forEach { section ->
            CategoryHeader(section)
            section.models.forEach { model ->
                RecognizedIngredientCard(
                    model = model,
                    onClick = if (model.rating == IngredientSafetyRating.LIMITED_DATA) {
                        null
                    } else {
                        { onIngredientClick(model.ingredient) }
                    }
                )
            }
        }

        if (models.size > COLLAPSED_INGREDIENT_COUNT) {
            TextButton(
                onClick = { expanded = !expanded },
                modifier = Modifier
                    .align(Alignment.CenterHorizontally)
                    .clip(RoundedCornerShape(NutriGuardRadius.pill))
                    .background(PastelLavender)
            ) {
                Icon(
                    imageVector = if (expanded) Icons.Default.ExpandLess else Icons.Default.ExpandMore,
                    contentDescription = null,
                    tint = PastelInk,
                    modifier = Modifier.size(18.dp)
                )
                Spacer(modifier = Modifier.width(NutriGuardSpacing.xs))
                Text(
                    text = if (expanded) "Show less" else "Show all (${models.size})",
                    color = PastelInk
                )
            }
        }
    }
}

@Composable
private fun CategoryHeader(section: IngredientResultSection) {
    val colors = ratingColors(section.rating)
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(top = NutriGuardSpacing.xs),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Box(
            modifier = Modifier
                .size(9.dp)
                .clip(CircleShape)
                .background(colors.accent)
        )
        Spacer(modifier = Modifier.width(NutriGuardSpacing.sm))
        Text(
            text = section.title,
            style = MaterialTheme.typography.labelLarge,
            fontWeight = FontWeight.Bold,
            color = PastelInk
        )
        Spacer(modifier = Modifier.width(NutriGuardSpacing.xs))
        Box(
            modifier = Modifier
                .clip(RoundedCornerShape(NutriGuardRadius.pill))
                .background(Color.White.copy(alpha = 0.62f))
                .padding(horizontal = 7.dp, vertical = 2.dp)
        ) {
            Text(
                text = section.models.size.toString(),
                style = MaterialTheme.typography.labelSmall,
                fontWeight = FontWeight.Bold,
                color = PastelInk
            )
        }
    }
}

@Composable
fun RecognizedIngredientCard(
    model: RecognizedIngredientUiModel,
    onClick: (() -> Unit)?,
    modifier: Modifier = Modifier
) {
    val ratingColors = ratingColors(model.rating)
    val ratingLabel = ratingLabel(model.rating)
    val scoreText = model.safetyScore?.let { "$it/100" }
    val semanticRating = if (scoreText == null) ratingLabel else "$ratingLabel, $scoreText"

    val interactionModifier = if (onClick != null) {
        Modifier
            .semantics(mergeDescendants = true) {
                contentDescription = "${model.displayName}. $semanticRating. Open scientific profile."
                role = Role.Button
            }
            .clickable(onClick = onClick)
    } else {
        Modifier.semantics(mergeDescendants = true) {
            contentDescription = "${model.displayName}. $semanticRating. No verified scientific profile available."
        }
    }

    Card(
        modifier = modifier
            .fillMaxWidth()
            .then(interactionModifier),
        shape = RoundedCornerShape(NutriGuardRadius.editorialCard),
        colors = CardDefaults.cardColors(containerColor = ratingColors.cardBackground),
        elevation = CardDefaults.cardElevation(defaultElevation = 3.dp),
        border = BorderStroke(1.dp, ratingColors.accent.copy(alpha = 0.18f))
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .height(IntrinsicSize.Min)
        ) {
            Box(
                modifier = Modifier
                    .width(6.dp)
                    .fillMaxHeight()
                    .background(ratingColors.accent)
            )

            Column(
                modifier = Modifier
                    .weight(1f)
                    .padding(NutriGuardSpacing.md)
            ) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.Top
                ) {
                    Text(
                        text = model.displayName,
                        modifier = Modifier.weight(1f),
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.SemiBold,
                        color = PastelInk
                    )
                    Spacer(modifier = Modifier.width(NutriGuardSpacing.sm))
                    RatingBadge(label = ratingLabel, score = scoreText, colors = ratingColors)
                }

                model.purpose?.let { purpose ->
                    Spacer(modifier = Modifier.height(NutriGuardSpacing.xs))
                    Text(
                        text = purpose,
                        style = MaterialTheme.typography.labelMedium,
                        color = PastelInk.copy(alpha = 0.76f),
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis
                    )
                }

                Spacer(modifier = Modifier.height(NutriGuardSpacing.sm))
                Text(
                    text = model.explanation,
                    style = MaterialTheme.typography.bodySmall,
                    color = PastelInk.copy(alpha = 0.72f),
                    maxLines = 3,
                    overflow = TextOverflow.Ellipsis
                )

                if (model.eNumber != null || model.evidenceLevel != null || model.allergens != null) {
                    Spacer(modifier = Modifier.height(NutriGuardSpacing.sm))
                    Column(verticalArrangement = Arrangement.spacedBy(NutriGuardSpacing.xs)) {
                        model.eNumber?.let { MetadataChip("E-number", it) }
                        model.evidenceLevel?.let { MetadataChip("Evidence", it) }
                        model.allergens?.let { MetadataChip("Allergen", it, RiskRed) }
                    }
                }
            }

            if (onClick != null) {
                Icon(
                    imageVector = Icons.Default.ChevronRight,
                    contentDescription = null,
                    tint = PastelInk.copy(alpha = 0.58f),
                    modifier = Modifier
                        .align(Alignment.CenterVertically)
                        .padding(end = NutriGuardSpacing.sm)
                        .size(20.dp)
                )
            } else {
                Spacer(modifier = Modifier.width(NutriGuardSpacing.sm))
            }
        }
    }
}

@Composable
private fun RatingBadge(label: String, score: String?, colors: RatingColors) {
    Column(horizontalAlignment = Alignment.End) {
        Box(
            modifier = Modifier
                .clip(RoundedCornerShape(NutriGuardRadius.pill))
                .background(colors.background)
                .padding(horizontal = 9.dp, vertical = 5.dp)
        ) {
            Text(
                text = label,
                fontSize = 11.sp,
                fontWeight = FontWeight.SemiBold,
                color = colors.text
            )
        }
        score?.let {
            Spacer(modifier = Modifier.height(2.dp))
            Text(
                text = it,
                style = MaterialTheme.typography.labelSmall,
                fontWeight = FontWeight.Bold,
                color = colors.accent
            )
        }
    }
}

@Composable
private fun MetadataChip(label: String, value: String, color: Color = PastelInk.copy(alpha = 0.72f)) {
    Row(
        modifier = Modifier
            .clip(RoundedCornerShape(NutriGuardRadius.pill))
            .background(Color.White.copy(alpha = 0.66f))
            .padding(horizontal = 9.dp, vertical = 5.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Box(
            modifier = Modifier
                .size(5.dp)
                .clip(CircleShape)
                .background(color)
        )
        Spacer(modifier = Modifier.width(6.dp))
        Text(
            text = "$label: $value",
            style = MaterialTheme.typography.labelSmall,
            color = color,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis
        )
    }
}

private data class RatingColors(
    val accent: Color,
    val background: Color,
    val text: Color,
    val cardBackground: Color
)

@Composable
private fun ratingColors(rating: IngredientSafetyRating): RatingColors {
    return when (rating) {
        IngredientSafetyRating.LOW_CONCERN -> RatingColors(
            RiskGreen,
            RiskGreen.copy(alpha = 0.14f),
            RiskGreen,
            PastelMint
        )
        IngredientSafetyRating.MODERATE -> RatingColors(
            RiskYellow,
            RiskYellow.copy(alpha = 0.14f),
            RiskYellow,
            PastelButter
        )
        IngredientSafetyRating.POTENTIAL_CONCERN -> RatingColors(
            RiskOrange,
            RiskOrange.copy(alpha = 0.14f),
            RiskOrange,
            PastelPeach
        )
        IngredientSafetyRating.HIGH_CONCERN -> RatingColors(
            RiskRed,
            RiskRed.copy(alpha = 0.14f),
            RiskRed,
            PastelRose
        )
        IngredientSafetyRating.LIMITED_DATA -> RatingColors(
            PastelInk.copy(alpha = 0.58f),
            Color.White.copy(alpha = 0.64f),
            PastelInk.copy(alpha = 0.68f),
            PastelLavender
        )
    }
}

private fun categoryTitle(rating: IngredientSafetyRating): String = when (rating) {
    IngredientSafetyRating.HIGH_CONCERN -> "High concern"
    IngredientSafetyRating.POTENTIAL_CONCERN -> "Potential concern"
    IngredientSafetyRating.MODERATE -> "Use in moderation"
    IngredientSafetyRating.LOW_CONCERN -> "Low concern"
    IngredientSafetyRating.LIMITED_DATA -> "Limited data"
}

private fun ratingLabel(rating: IngredientSafetyRating): String = when (rating) {
    IngredientSafetyRating.LOW_CONCERN -> "Low concern"
    IngredientSafetyRating.MODERATE -> "Moderate"
    IngredientSafetyRating.POTENTIAL_CONCERN -> "Potential concern"
    IngredientSafetyRating.HIGH_CONCERN -> "High concern"
    IngredientSafetyRating.LIMITED_DATA -> "Limited data"
}
