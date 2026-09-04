package com.example.ui.components

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.ui.theme.NutriGuardRadius
import com.example.ui.theme.RiskGreen
import com.example.ui.theme.RiskOrange
import com.example.ui.theme.RiskRed
import com.example.ui.theme.RiskYellow

@Composable
fun HealthScoreGauge(
    score: Int,
    novaGroup: Int,
    sugarGrams: Double,
    sodiumMg: Double,
    saturatedFatGrams: Double,
    modifier: Modifier = Modifier
) {
    var startAnimation by remember { mutableStateOf(false) }
    val animatedProgress by animateFloatAsState(
        targetValue = if (startAnimation) (score / 100f).coerceIn(0f, 1f) else 0f,
        animationSpec = tween(durationMillis = 800),
        label = "scoreAnimation"
    )

    LaunchedEffect(score) {
        startAnimation = true
    }

    val (scoreColor, scoreLabel, scoreDescription) = when {
        score >= 80 -> Triple(RiskGreen, "Excellent", "Clean formulation & safe ingredients")
        score >= 60 -> Triple(RiskYellow, "Good", "Balanced with minor processing")
        score >= 40 -> Triple(RiskOrange, "Moderate", "Consume occasionally")
        else -> Triple(RiskRed, "Poor", "Highly processed or high-concern additives")
    }

    val isDark = isSystemInDarkTheme()
    val badgeBg = if (isDark) scoreColor.copy(alpha = 0.2f) else scoreColor.copy(alpha = 0.12f)

    Column(
        modifier = modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        // Main Hero Score Card
        Card(
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(NutriGuardRadius.large),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
            border = androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.outline)
        ) {
            Row(
                modifier = Modifier.padding(20.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = "Health Score",
                        style = MaterialTheme.typography.titleMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )

                    Spacer(modifier = Modifier.height(4.dp))

                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Box(
                            modifier = Modifier
                                .clip(RoundedCornerShape(8.dp))
                                .background(badgeBg)
                                .padding(horizontal = 10.dp, vertical = 4.dp)
                        ) {
                            Text(
                                text = scoreLabel,
                                style = MaterialTheme.typography.titleSmall,
                                fontWeight = FontWeight.SemiBold,
                                color = scoreColor
                            )
                        }
                    }

                    Spacer(modifier = Modifier.height(6.dp))

                    Text(
                        text = scoreDescription,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }

                Spacer(modifier = Modifier.width(16.dp))

                Box(
                    contentAlignment = Alignment.Center,
                    modifier = Modifier.size(100.dp)
                ) {
                    val trackColor = if (isDark) Color(0xFF1E2D42) else Color(0xFFE2E8F0)
                    Canvas(modifier = Modifier.size(92.dp)) {
                        val strokeWidth = 9.dp.toPx()
                        drawArc(
                            color = trackColor,
                            startAngle = 135f,
                            sweepAngle = 270f,
                            useCenter = false,
                            style = Stroke(width = strokeWidth, cap = StrokeCap.Round)
                        )
                        drawArc(
                            color = scoreColor,
                            startAngle = 135f,
                            sweepAngle = 270f * animatedProgress,
                            useCenter = false,
                            style = Stroke(width = strokeWidth, cap = StrokeCap.Round)
                        )
                    }

                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text(
                            text = "$score",
                            fontSize = 28.sp,
                            fontWeight = FontWeight.Bold,
                            color = scoreColor
                        )
                        Text(
                            text = "out of 100",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                }
            }
        }

        // Key Nutritional Factors (2x2 Grid)
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(10.dp)
        ) {
            NutrientTile(
                title = "NOVA Group",
                value = "Group $novaGroup",
                subtitle = if (novaGroup >= 4) "Ultra-processed" else if (novaGroup == 3) "Processed" else "Unprocessed",
                progress = (novaGroup / 4f).coerceIn(0f, 1f),
                accentColor = if (novaGroup >= 4) RiskRed else if (novaGroup >= 3) RiskOrange else RiskGreen,
                modifier = Modifier.weight(1f)
            )

            NutrientTile(
                title = "Sugar",
                value = "${sugarGrams}g",
                subtitle = "per 100g",
                progress = (sugarGrams / 25.0).toFloat().coerceIn(0f, 1f),
                accentColor = if (sugarGrams > 12.0) RiskRed else if (sugarGrams > 5.0) RiskYellow else RiskGreen,
                modifier = Modifier.weight(1f)
            )
        }

        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(10.dp)
        ) {
            NutrientTile(
                title = "Sodium",
                value = "${sodiumMg.toInt()} mg",
                subtitle = "per 100g",
                progress = (sodiumMg / 1000.0).toFloat().coerceIn(0f, 1f),
                accentColor = if (sodiumMg > 600.0) RiskRed else if (sodiumMg > 300.0) RiskYellow else RiskGreen,
                modifier = Modifier.weight(1f)
            )

            NutrientTile(
                title = "Saturated Fat",
                value = "${saturatedFatGrams}g",
                subtitle = "per 100g",
                progress = (saturatedFatGrams / 10.0).toFloat().coerceIn(0f, 1f),
                accentColor = if (saturatedFatGrams > 5.0) RiskRed else if (saturatedFatGrams > 2.5) RiskYellow else RiskGreen,
                modifier = Modifier.weight(1f)
            )
        }
    }
}

@Composable
private fun NutrientTile(
    title: String,
    value: String,
    subtitle: String,
    progress: Float,
    accentColor: Color,
    modifier: Modifier = Modifier
) {
    Card(
        modifier = modifier,
        shape = RoundedCornerShape(NutriGuardRadius.medium),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        border = androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.outline)
    ) {
        Column(modifier = Modifier.padding(14.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = title,
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )

                Box(
                    modifier = Modifier
                        .size(7.dp)
                        .clip(CircleShape)
                        .background(accentColor)
                )
            }

            Spacer(modifier = Modifier.height(4.dp))

            Text(
                text = value,
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onSurface
            )

            Text(
                text = subtitle,
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )

            Spacer(modifier = Modifier.height(8.dp))

            LinearProgressIndicator(
                progress = { progress },
                modifier = Modifier
                    .fillMaxWidth()
                    .height(4.dp)
                    .clip(RoundedCornerShape(2.dp)),
                color = accentColor,
                trackColor = accentColor.copy(alpha = 0.15f)
            )
        }
    }
}

