package com.example.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.example.data.model.RiskLevel

object NutriGuardSpacing {
    val xs: Dp = 4.dp
    val sm: Dp = 8.dp
    val md: Dp = 12.dp
    val lg: Dp = 16.dp
    val xl: Dp = 20.dp
    val xxl: Dp = 24.dp
    val section: Dp = 28.dp
}

object NutriGuardRadius {
    val small: Dp = 8.dp
    val medium: Dp = 14.dp
    val large: Dp = 20.dp
    val editorialCard: Dp = 22.dp
    val hero: Dp = 28.dp
    val pill: Dp = 50.dp
}

data class RiskUiColor(
    val main: Color,
    val background: Color,
    val text: Color
)

@Composable
fun getRiskUiColor(riskLevel: RiskLevel): RiskUiColor {
    val isDark = isSystemInDarkTheme()
    return when (riskLevel) {
        RiskLevel.SAFE -> RiskUiColor(
            main = RiskGreen,
            background = if (isDark) RiskGreenBgDark else RiskGreenBgLight,
            text = if (isDark) Color(0xFF86EFAC) else Color(0xFF15803D)
        )
        RiskLevel.MODERATE -> RiskUiColor(
            main = RiskYellow,
            background = if (isDark) RiskYellowBgDark else RiskYellowBgLight,
            text = if (isDark) Color(0xFFFDE68A) else Color(0xFFB45309)
        )
        RiskLevel.POTENTIAL_CONCERN -> RiskUiColor(
            main = RiskOrange,
            background = if (isDark) RiskOrangeBgDark else RiskOrangeBgLight,
            text = if (isDark) Color(0xFFFDBA74) else Color(0xFFC2410C)
        )
        RiskLevel.HIGH_CONCERN -> RiskUiColor(
            main = RiskRed,
            background = if (isDark) RiskRedBgDark else RiskRedBgLight,
            text = if (isDark) Color(0xFFFCA5A5) else Color(0xFFB91C1C)
        )
    }
}

/**
 * IARC's own five-tier carcinogenicity classification scheme -- NOT the
 * same axis as [RiskLevel] (which reflects this app's own overall food-
 * additive risk scoring). [UNKNOWN] covers both "no classification
 * string at all" (filtered out entirely before this is ever consulted
 * -- see `cleanOrNull` call sites in `IngredientChip`/
 * `IngredientDetailBottomSheet`) and "a classification string that
 * doesn't match any recognized IARC group" -- an unparseable value must
 * never be *assumed* hazardous.
 */
enum class IarcGroup { GROUP_1, GROUP_2A, GROUP_2B, GROUP_3, UNKNOWN }

private val GROUP_2A_PATTERN = Regex("""\bGROUP\s*2\s*A\b""")
private val GROUP_2B_PATTERN = Regex("""\bGROUP\s*2\s*B\b""")
private val GROUP_1_PATTERN = Regex("""\bGROUP\s*1\b""")
private val GROUP_3_PATTERN = Regex("""\bGROUP\s*3\b""")

/**
 * Parses a free-text WHO/IARC classification string (e.g.
 * `"Group 2B - Possibly Carcinogenic to Humans"`,
 * `IngredientEntity.whoIarcClassification`'s documented shape) into its
 * [IarcGroup] tier. Pure and Compose-free so it's directly unit-testable.
 * 2A/2B are checked before the bare "1"/"3" patterns so e.g. "Group 2A"
 * is never mis-tiered by a naive "contains '1'"-style check; an
 * unrecognized string (typo, a future IARC group, empty after
 * placeholder-filtering) safely falls back to [IarcGroup.UNKNOWN] rather
 * than guessing.
 */
fun parseIarcGroup(classification: String): IarcGroup {
    val normalized = classification.uppercase()
    return when {
        GROUP_2A_PATTERN.containsMatchIn(normalized) -> IarcGroup.GROUP_2A
        GROUP_2B_PATTERN.containsMatchIn(normalized) -> IarcGroup.GROUP_2B
        GROUP_1_PATTERN.containsMatchIn(normalized) -> IarcGroup.GROUP_1
        GROUP_3_PATTERN.containsMatchIn(normalized) -> IarcGroup.GROUP_3
        else -> IarcGroup.UNKNOWN
    }
}

/**
 * UI color for a WHO/IARC classification, tiered by actual hazard
 * (review requirement: not every classification is red, Group 3 --
 * "not classifiable as to carcinogenicity" -- is neutral, not a
 * hazard). [IarcGroup.UNKNOWN] (unparseable/unrecognized text) is ALSO
 * neutral -- an unparseable value must never be presented as a safety
 * conclusion in either direction.
 *
 *   Group 1  (carcinogenic to humans)          -> red    (confirmed, most severe)
 *   Group 2A (probably carcinogenic to humans)  -> orange (probable)
 *   Group 2B (possibly carcinogenic to humans)  -> yellow (possible, least severe hazard tier)
 *   Group 3 / unrecognized                      -> neutral (surface/outline tones, no hazard color)
 */
@Composable
fun getWhoIarcUiColor(classification: String): RiskUiColor {
    val isDark = isSystemInDarkTheme()
    return when (parseIarcGroup(classification)) {
        IarcGroup.GROUP_1 -> RiskUiColor(
            main = RiskRed,
            background = if (isDark) RiskRedBgDark else RiskRedBgLight,
            text = if (isDark) Color(0xFFFCA5A5) else Color(0xFFB91C1C)
        )
        IarcGroup.GROUP_2A -> RiskUiColor(
            main = RiskOrange,
            background = if (isDark) RiskOrangeBgDark else RiskOrangeBgLight,
            text = if (isDark) Color(0xFFFDBA74) else Color(0xFFC2410C)
        )
        IarcGroup.GROUP_2B -> RiskUiColor(
            main = RiskYellow,
            background = if (isDark) RiskYellowBgDark else RiskYellowBgLight,
            text = if (isDark) Color(0xFFFDE68A) else Color(0xFFB45309)
        )
        IarcGroup.GROUP_3, IarcGroup.UNKNOWN -> RiskUiColor(
            main = SlateTextSecondary,
            background = if (isDark) DarkForestSurfaceVariant else LightSurfaceVariant,
            text = if (isDark) SlateDarkTextSecondary else SlateTextSecondary
        )
    }
}
