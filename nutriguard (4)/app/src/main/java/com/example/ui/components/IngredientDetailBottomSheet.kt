package com.example.ui.components

import androidx.compose.foundation.background
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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Science
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.data.model.IngredientEntity
import com.example.data.model.RiskLevel
import com.example.ui.theme.NutriGuardRadius
import com.example.ui.theme.RiskRed
import com.example.ui.theme.getRiskUiColor

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun IngredientDetailBottomSheet(
    ingredient: IngredientEntity?,
    onDismiss: () -> Unit
) {
    if (ingredient == null) return

    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val riskUi = getRiskUiColor(ingredient.riskLevel)
    val riskLabel = when (ingredient.riskLevel) {
        RiskLevel.SAFE -> "Safe"
        RiskLevel.MODERATE -> "Consume in moderation"
        RiskLevel.POTENTIAL_CONCERN -> "Potential concern"
        RiskLevel.HIGH_CONCERN -> "High concern / Restricted"
    }

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState,
        containerColor = MaterialTheme.colorScheme.surface
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 20.dp, vertical = 8.dp)
                .verticalScroll(rememberScrollState())
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(
                        modifier = Modifier
                            .size(36.dp)
                            .clip(CircleShape)
                            .background(riskUi.background),
                        contentAlignment = Alignment.Center
                    ) {
                        Icon(
                            imageVector = Icons.Default.Science,
                            contentDescription = "Scientific Profile",
                            tint = riskUi.main,
                            modifier = Modifier.size(20.dp)
                        )
                    }
                    Spacer(modifier = Modifier.width(10.dp))
                    Text(
                        text = "Scientific Ingredient Profile",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold,
                        color = MaterialTheme.colorScheme.onSurface
                    )
                }

                IconButton(onClick = onDismiss) {
                    Icon(imageVector = Icons.Default.Close, contentDescription = "Close")
                }
            }

            Spacer(modifier = Modifier.height(12.dp))

            Text(
                text = ingredient.commonName,
                style = MaterialTheme.typography.headlineMedium,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.onSurface
            )

            if (ingredient.scientificName.isNotBlank()) {
                Text(
                    text = ingredient.scientificName,
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }

            Spacer(modifier = Modifier.height(10.dp))

            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                Box(
                    modifier = Modifier
                        .clip(RoundedCornerShape(NutriGuardRadius.small))
                        .background(riskUi.background)
                        .padding(horizontal = 10.dp, vertical = 5.dp)
                ) {
                    Text(
                        text = riskLabel,
                        fontSize = 12.sp,
                        fontWeight = FontWeight.SemiBold,
                        color = riskUi.text
                    )
                }

                if (!ingredient.eNumber.isNullOrBlank()) {
                    Box(
                        modifier = Modifier
                            .clip(RoundedCornerShape(NutriGuardRadius.small))
                            .background(MaterialTheme.colorScheme.surfaceVariant)
                            .padding(horizontal = 10.dp, vertical = 5.dp)
                    ) {
                        Text(
                            text = "E-Number: ${ingredient.eNumber}",
                            fontSize = 12.sp,
                            fontWeight = FontWeight.Medium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                }
            }

            Spacer(modifier = Modifier.height(16.dp))
            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
            Spacer(modifier = Modifier.height(12.dp))

            // Structured Scientific Sections
            InfoSectionItem("Description", ingredient.description)
            InfoSectionItem("Purpose in Food", ingredient.purposeInFood)
            InfoSectionItem("Evidence-Based Health Concerns", ingredient.healthConcerns)
            InfoSectionItem("Scientific Evidence Level", ingredient.evidenceLevel)
            InfoSectionItem("EFSA (European Food Safety) Status", ingredient.efsaStatus)
            InfoSectionItem("FDA (US Food & Drug) Status", ingredient.fdaStatus)

            if (ingredient.whoIarcClassification != null) {
                InfoSectionItem("WHO / IARC Classification", ingredient.whoIarcClassification, highlightColor = RiskRed)
            }

            InfoSectionItem("Countries Banned or Restricted", ingredient.countriesRestrictedOrBanned)
            InfoSectionItem("Acceptable Daily Intake (ADI)", ingredient.acceptableDailyIntake)
            InfoSectionItem("Known Side Effects & Toxicity", ingredient.sideEffects)
            InfoSectionItem("Allergen Information", ingredient.allergens)
            InfoSectionItem("Scientific References", ingredient.references)

            Spacer(modifier = Modifier.height(28.dp))
        }
    }
}

@Composable
private fun InfoSectionItem(
    title: String,
    content: String,
    highlightColor: Color? = null
) {
    if (content.isBlank()) return

    Column(modifier = Modifier.padding(vertical = 6.dp)) {
        Text(
            text = title,
            style = MaterialTheme.typography.labelMedium,
            fontWeight = FontWeight.SemiBold,
            color = highlightColor ?: MaterialTheme.colorScheme.primary
        )
        Spacer(modifier = Modifier.height(2.dp))
        Text(
            text = content,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurface
        )
    }
}

