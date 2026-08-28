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
