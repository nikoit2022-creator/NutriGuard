package com.example.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Info
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import com.example.ui.i18n.LocalizedText as Text
import com.example.ui.i18n.LocalAppLanguage
import com.example.ui.i18n.localizeUiText
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.example.ui.theme.NutriGuardRadius
import com.example.ui.theme.RiskGreen
import com.example.ui.theme.RiskOrange
import com.example.ui.theme.RiskRed
import com.example.ui.theme.RiskYellow
import java.util.Locale

enum class HealthFactor {
    SUGAR,
    SODIUM,
    SATURATED_FAT
}

data class HealthFactorUiInfo(
    val title: String,
    val productValue: String,
    val level: String,
    val color: Color,
    val scoreImpact: Int,
    val healthEffects: String,
    val dailyGuidance: String,
    val contextNote: String,
    val sourceLabel: String,
    val sourceUrl: String
)

internal fun sugarScoreDeduction(value: Double): Int = when {
    value > 20.0 -> 25
    value > 12.0 -> 18
    value > 5.0 -> 10
    value > 2.0 -> 4
    else -> 0
}

internal fun sodiumScoreDeduction(value: Double): Int = when {
    value > 900.0 -> 25
    value > 600.0 -> 18
    value > 300.0 -> 10
    value > 120.0 -> 5
    else -> 0
}

internal fun saturatedFatScoreDeduction(value: Double): Int = when {
    value > 8.0 -> 20
    value > 5.0 -> 12
    value > 2.5 -> 6
    else -> 0
}

private fun grams(value: Double): String =
    if (value % 1.0 == 0.0) "${value.toInt()} g" else String.format(Locale.US, "%.1f g", value)

fun healthFactorUiInfo(factor: HealthFactor, value: Double): HealthFactorUiInfo {
    return when (factor) {
        HealthFactor.SUGAR -> {
            val deduction = sugarScoreDeduction(value)
            HealthFactorUiInfo(
                title = "Sugar",
                productValue = "${grams(value)} per 100 g",
                level = when { value > 12.0 -> "High"; value > 5.0 -> "Moderate"; else -> "Lower" },
                color = when { value > 12.0 -> RiskRed; value > 5.0 -> RiskYellow; else -> RiskGreen },
                scoreImpact = deduction,
                healthEffects = "Frequent high intake of free sugars can contribute to tooth decay, unhealthy weight gain and related metabolic risk.",
                dailyGuidance = "WHO recommends keeping free sugars below 10% of daily energy (about 50 g for a 2,000 kcal diet), with below 5% or about 25 g offering additional benefits.",
                contextNote = "The label usually reports total sugars. The WHO limit applies to free sugars, so this value is a comparison guide, not an exact daily allowance for this product.",
                sourceLabel = "WHO — Sugars intake guidance",
                sourceUrl = "https://www.who.int/news-room/detail/04-03-2015-who-calls-on-countries-to-reduce-sugars-intake-among-adults-and-children"
            )
        }

        HealthFactor.SODIUM -> {
            val deduction = sodiumScoreDeduction(value)
            HealthFactorUiInfo(
                title = "Sodium",
                productValue = "${value.toInt()} mg per 100 g",
                level = when { value > 600.0 -> "High"; value > 300.0 -> "Moderate"; else -> "Lower" },
                color = when { value > 600.0 -> RiskRed; value > 300.0 -> RiskYellow; else -> RiskGreen },
                scoreImpact = deduction,
                healthEffects = "Regular high sodium intake can raise blood pressure and increase cardiovascular and kidney health risk.",
                dailyGuidance = "WHO recommends less than 2,000 mg of sodium per day for adults, equivalent to less than 5 g of salt.",
                contextNote = "This product value is shown per 100 g. Your actual intake depends on the portion consumed and sodium from the rest of the day.",
                sourceLabel = "WHO — Sodium reduction",
                sourceUrl = "https://www.who.int/news-room/fact-sheets/detail/sodium-reduction"
            )
        }

        HealthFactor.SATURATED_FAT -> {
            val deduction = saturatedFatScoreDeduction(value)
            HealthFactorUiInfo(
                title = "Saturated fat",
                productValue = "${grams(value)} per 100 g",
                level = when { value > 5.0 -> "High"; value > 2.5 -> "Moderate"; else -> "Lower" },
                color = when { value > 5.0 -> RiskRed; value > 2.5 -> RiskYellow; else -> RiskGreen },
                scoreImpact = deduction,
                healthEffects = "High saturated-fat intake can raise LDL cholesterol and, over time, increase cardiovascular risk.",
                dailyGuidance = "WHO recommends that saturated fat provide no more than 10% of daily energy — about 22 g in a 2,000 kcal diet.",
                contextNote = "The gram estimate changes with individual energy needs. Prefer replacing saturated fat with unsaturated fats rather than adding more calories.",
                sourceLabel = "WHO — Saturated fatty acid guideline",
                sourceUrl = "https://www.who.int/publications/i/item/9789240073630"
            )
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HealthFactorInfoBottomSheet(
    factor: HealthFactor?,
    value: Double,
    onDismiss: () -> Unit
) {
    val language = LocalAppLanguage.current
    if (factor == null) return
    val info = healthFactorUiInfo(factor, value)
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val uriHandler = LocalUriHandler.current

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState,
        containerColor = MaterialTheme.colorScheme.surface
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 20.dp, vertical = 8.dp)
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(16.dp)
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(
                        modifier = Modifier
                            .size(38.dp)
                            .clip(CircleShape)
                            .background(info.color.copy(alpha = 0.14f)),
                        contentAlignment = Alignment.Center
                    ) {
                        Icon(Icons.Default.Info, contentDescription = null, tint = info.color)
                    }
                    Text(
                        text = "Factor details",
                        modifier = Modifier.padding(start = 10.dp),
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold
                    )
                }
                IconButton(onClick = onDismiss) {
                    Icon(
                        Icons.Default.Close,
                        contentDescription = localizeUiText("Close", language)
                    )
                }
            }

            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(NutriGuardRadius.medium))
                    .background(info.color.copy(alpha = 0.10f))
                    .padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp)
            ) {
                Text(info.title, style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
                Text(info.productValue, style = MaterialTheme.typography.titleMedium)
                Text(info.level, color = info.color, fontWeight = FontWeight.SemiBold)
                Text(
                    text = if (info.scoreImpact == 0) "No points deducted from Health Score" else "Health Score impact: −${info.scoreImpact} points",
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.SemiBold
                )
            }

            InfoSection("Possible health effects", info.healthEffects)
            InfoSection("Daily guidance for adults", info.dailyGuidance)
            InfoSection("How to read this value", info.contextNote)

            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text("Source", style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Text(
                    text = info.sourceLabel,
                    color = MaterialTheme.colorScheme.primary,
                    style = MaterialTheme.typography.bodyMedium,
                    modifier = Modifier.clickable { uriHandler.openUri(info.sourceUrl) }
                )
            }
            Spacer(Modifier.height(24.dp))
        }
    }
}

@Composable
private fun InfoSection(title: String, body: String) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(title, style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.primary)
        Text(body, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurface)
    }
}
