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
import androidx.compose.material.icons.filled.Cancel
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.Science
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import com.example.ui.i18n.LocalizedText as Text
import com.example.ui.i18n.AppLanguage
import com.example.ui.i18n.LocalAppLanguage
import com.example.ui.i18n.localizeUiText
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.data.model.IngredientEntity
import com.example.data.model.RiskLevel
import com.example.data.remote.dto.cleanOrNull
import com.example.ui.theme.NutriGuardRadius
import com.example.ui.theme.RiskGreen
import com.example.ui.theme.RiskRed
import com.example.ui.theme.getRiskUiColor
import com.example.ui.theme.getWhoIarcUiColor
import com.example.ui.model.localizedContent
import java.math.BigDecimal

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun IngredientDetailBottomSheet(
    ingredient: IngredientEntity?,
    onDismiss: () -> Unit
) {
    if (ingredient == null) return

    val language = LocalAppLanguage.current
    val localized = ingredient.localizedContent(language)
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val riskUi = getRiskUiColor(ingredient.riskLevel)
    val riskLabel = when (ingredient.riskLevel) {
        RiskLevel.SAFE -> "Low concern"
        RiskLevel.MODERATE -> "Use in moderation"
        RiskLevel.POTENTIAL_CONCERN -> "Potential concern"
        RiskLevel.HIGH_CONCERN -> "High concern"
    }
    val sources = buildSources(ingredient)
    var sourcesExpanded by rememberSaveable(ingredient.id) { mutableStateOf(false) }

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
                            .background(MaterialTheme.colorScheme.primaryContainer),
                        contentAlignment = Alignment.Center
                    ) {
                        Icon(
                            imageVector = Icons.Default.Science,
                            contentDescription = null,
                            tint = MaterialTheme.colorScheme.primary,
                            modifier = Modifier.size(20.dp)
                        )
                    }
                    Spacer(modifier = Modifier.width(10.dp))
                    Text(
                        text = "Ingredient details",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold
                    )
                }

                IconButton(onClick = onDismiss) {
                    Icon(
                        imageVector = Icons.Default.Close,
                        contentDescription = localizeUiText("Close", language)
                    )
                }
            }

            Spacer(modifier = Modifier.height(12.dp))
            Text(
                text = localized.commonName,
                style = MaterialTheme.typography.headlineMedium,
                fontWeight = FontWeight.Bold
            )

            ingredient.scientificName.cleanOrNull()?.let { scientificName ->
                Text(
                    text = scientificName,
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }

            val displayENumber = ingredient.eNumber.cleanOrNull()
            if (ingredient.riskAssessmentAvailable || displayENumber != null) {
                Spacer(modifier = Modifier.height(10.dp))
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    if (ingredient.riskAssessmentAvailable) {
                        StatusPill(riskLabel, riskUi.background, riskUi.text)
                    }
                    displayENumber?.let {
                        StatusPill(
                            it,
                            MaterialTheme.colorScheme.surfaceVariant,
                            MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                }
            }

            Spacer(modifier = Modifier.height(16.dp))
            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
            Spacer(modifier = Modifier.height(10.dp))

            val hasOverview = localized.description.cleanOrNull() != null ||
                localized.purposeInFood.cleanOrNull() != null
            val hasHealthInformation = localized.healthConcerns.cleanOrNull() != null ||
                localized.sideEffects.cleanOrNull() != null ||
                localized.allergens.cleanOrNull() != null ||
                ingredient.whoIarcClassification.cleanOrNull() != null ||
                formatAdi(ingredient.adiMinMgPerKgBwPerDay, ingredient.adiMaxMgPerKgBwPerDay, language) != null ||
                localized.acceptableDailyIntake.cleanOrNull() != null
            val hasRegulatoryInformation = hasKnownRegulatoryStatus(ingredient.efsaApprovalStatus) ||
                hasKnownRegulatoryStatus(ingredient.fdaApprovalStatus) ||
                ingredient.countriesRestrictedOrBanned.cleanOrNull() != null ||
                localized.evidenceLevel.cleanOrNull() != null ||
                (ingredient.riskAssessmentAvailable && localized.riskRationale.cleanOrNull() != null)

            if (hasOverview) DetailGroupTitle("About this ingredient")
            InfoSectionItem("Description", localized.description)
            InfoSectionItem("Purpose in food", localized.purposeInFood)

            if (hasHealthInformation) DetailGroupTitle("Health and safety")
            InfoSectionItem("Health considerations", localized.healthConcerns)
            InfoSectionItem("Known side effects", localized.sideEffects)
            InfoSectionItem("Allergens", localized.allergens, highlightColor = RiskRed)

            ingredient.whoIarcClassification.cleanOrNull()?.let { classification ->
                InfoSectionItem(
                    "WHO / IARC classification",
                    classification,
                    highlightColor = getWhoIarcUiColor(classification).main
                )
            }
            val numericAdi = formatAdi(
                ingredient.adiMinMgPerKgBwPerDay,
                ingredient.adiMaxMgPerKgBwPerDay,
                language
            )
            (numericAdi ?: localized.acceptableDailyIntake.cleanOrNull())?.let { adi ->
                InfoSectionItem("Acceptable daily intake", adi)
            }

            if (hasRegulatoryInformation) DetailGroupTitle("Evidence and regulation")
            RegulatoryStatusRow("EFSA", ingredient.efsaApprovalStatus)
            RegulatoryStatusRow("FDA", ingredient.fdaApprovalStatus)
            if (ingredient.riskAssessmentAvailable) {
                InfoSectionItem("Why this rating", localized.riskRationale.orEmpty())
            }
            InfoSectionItem("Evidence", localized.evidenceLevel)
            InfoSectionItem("Restricted in", localized.countriesRestrictedOrBanned)

            if (sources.isNotEmpty()) {
                Spacer(modifier = Modifier.height(8.dp))
                HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                TextButton(onClick = { sourcesExpanded = !sourcesExpanded }) {
                    Text("Sources (${sources.size})")
                    Spacer(modifier = Modifier.width(4.dp))
                    Icon(
                        imageVector = if (sourcesExpanded) Icons.Default.ExpandLess else Icons.Default.ExpandMore,
                        contentDescription = localizeUiText(
                            if (sourcesExpanded) "Hide sources" else "Show sources",
                            language
                        )
                    )
                }
                if (sourcesExpanded) {
                    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        sources.forEach { source ->
                            Text(
                                text = source,
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }
                    }
                }
            }

            Spacer(modifier = Modifier.height(28.dp))
        }
    }
}

private fun hasKnownRegulatoryStatus(rawStatus: String?): Boolean = when (rawStatus.cleanOrNull()?.uppercase()) {
    "APPROVED", "NOT_APPROVED" -> true
    else -> false
}

@Composable
private fun DetailGroupTitle(title: String) {
    Text(
        text = title,
        modifier = Modifier.padding(top = 10.dp, bottom = 2.dp),
        style = MaterialTheme.typography.titleSmall,
        fontWeight = FontWeight.Bold,
        color = MaterialTheme.colorScheme.onSurface
    )
}

@Composable
private fun StatusPill(label: String, background: Color, foreground: Color) {
    Box(
        modifier = Modifier
            .clip(RoundedCornerShape(NutriGuardRadius.small))
            .background(background)
            .padding(horizontal = 10.dp, vertical = 5.dp)
    ) {
        Text(text = label, fontSize = 12.sp, fontWeight = FontWeight.SemiBold, color = foreground)
    }
}

@Composable
private fun RegulatoryStatusRow(authority: String, rawStatus: String?) {
    val status = rawStatus.cleanOrNull()?.uppercase() ?: return
    val approved = when (status) {
        "APPROVED" -> true
        "NOT_APPROVED" -> false
        else -> return
    }
    val color = if (approved) RiskGreen else RiskRed

    Row(
        modifier = Modifier.padding(vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        Icon(
            imageVector = if (approved) Icons.Default.CheckCircle else Icons.Default.Cancel,
            contentDescription = null,
            tint = color,
            modifier = Modifier.size(20.dp)
        )
        Text(
            text = "$authority ${if (approved) "approved" else "not approved"}",
            style = MaterialTheme.typography.bodyMedium,
            fontWeight = FontWeight.Medium
        )
    }
}

@Composable
private fun InfoSectionItem(title: String, content: String, highlightColor: Color? = null) {
    val displayContent = content.cleanOrNull() ?: return
    Column(modifier = Modifier.padding(vertical = 6.dp)) {
        Text(
            text = title,
            style = MaterialTheme.typography.labelMedium,
            fontWeight = FontWeight.SemiBold,
            color = highlightColor ?: MaterialTheme.colorScheme.primary
        )
        Spacer(modifier = Modifier.height(2.dp))
        Text(text = displayContent, style = MaterialTheme.typography.bodyMedium)
    }
}

private fun formatAdi(minimum: Double?, maximum: Double?, language: AppLanguage): String? {
    if (minimum == null && maximum == null) return null
    val range = when {
        minimum != null && maximum != null -> "${minimum.cleanNumber()}–${maximum.cleanNumber()}"
        maximum != null -> if (language == AppLanguage.BULGARIAN) {
            "До ${maximum.cleanNumber()}"
        } else {
            "Up to ${maximum.cleanNumber()}"
        }
        else -> if (language == AppLanguage.BULGARIAN) {
            "От ${minimum!!.cleanNumber()}"
        } else {
            "From ${minimum!!.cleanNumber()}"
        }
    }
    return if (language == AppLanguage.BULGARIAN) {
        "$range mg/kg телесно тегло/ден"
    } else {
        "$range mg/kg body weight/day"
    }
}

private fun Double.cleanNumber(): String = BigDecimal.valueOf(this).stripTrailingZeros().toPlainString()

private fun buildSources(ingredient: IngredientEntity): List<String> {
    val entries = buildList {
        ingredient.references.cleanOrNull()?.let { references ->
            addAll(references.split('|').mapNotNull { it.cleanOrNull() })
        }
        ingredient.adiSource.cleanOrNull()?.let(::add)
        ingredient.sourceUrl.cleanOrNull()?.let(::add)
    }
    return entries.distinct()
}
