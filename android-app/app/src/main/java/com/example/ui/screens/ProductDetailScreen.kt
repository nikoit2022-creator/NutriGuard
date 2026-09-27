package com.example.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Storage
import androidx.compose.material.icons.filled.PhotoCamera
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import com.example.ui.i18n.LocalizedText as Text
import com.example.ui.i18n.LocalAppLanguage
import com.example.ui.i18n.localizeUiText
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.data.model.IngredientEntity
import com.example.data.remote.dto.cleanOrNull
import com.example.ui.components.DietaryBadgesRow
import com.example.ui.components.HealthScoreGauge
import com.example.ui.components.HealthFactor
import com.example.ui.components.HealthFactorInfoBottomSheet
import com.example.ui.components.IngredientChip
import com.example.ui.components.IngredientDetailBottomSheet
import com.example.ui.components.NovaGroupInfoBottomSheet
import com.example.ui.components.hasUsefulIngredientDetails
import com.example.ui.components.PersonalizedWarningCard
import com.example.ui.theme.NutriGuardRadius
import com.example.ui.theme.NutriGuardSpacing
import com.example.ui.theme.ScannerHeroEnd
import com.example.ui.theme.ScannerHeroMiddle
import com.example.ui.theme.ScannerHeroStart
import com.example.ui.theme.ScannerPageBackground
import com.example.ui.theme.ScannerSlateMuted
import com.example.ui.theme.ScannerViolet
import com.example.ui.viewmodel.AnalysisUiState
import com.example.ui.viewmodel.MainViewModel

/** Presentation-only filtering also covers cached responses from older backends. */
internal fun productIdentityText(value: String?): String? = value.cleanOrNull()?.takeUnless {
    it.lowercase() in setOf("scanned label product", "scanned product", "analyzed brand",
        "analyzed food", "general food", "unknown brand", "unknown product")
}

internal fun productIdentitySubtitle(brand: String?, category: String?): String =
    listOfNotNull(productIdentityText(brand), productIdentityText(category))
        .distinct().joinToString(" • ")

@Composable
fun ProductDetailScreen(
    viewModel: MainViewModel,
    onBack: () -> Unit,
    onScanLabelForProduct: (String) -> Unit
) {
    val language = LocalAppLanguage.current
    val uiState by viewModel.analysisState.collectAsState()
    var selectedIngredientForDetail by remember { mutableStateOf<IngredientEntity?>(null) }
    var selectedNovaGroup by remember { mutableStateOf<Int?>(null) }
    var selectedHealthFactor by remember { mutableStateOf<HealthFactor?>(null) }

    when (val state = uiState) {
        is AnalysisUiState.Loading -> {
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .background(ScannerPageBackground),
                contentAlignment = Alignment.Center
            ) {
                Column(
                    horizontalAlignment = Alignment.CenterHorizontally,
                    modifier = Modifier.padding(24.dp)
                ) {
                    CircularProgressIndicator(
                        color = ScannerViolet,
                        modifier = Modifier.size(44.dp)
                    )
                    Spacer(modifier = Modifier.height(20.dp))
                    Text(
                        text = "Analyzing Product Ingredients...",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold,
                        color = MaterialTheme.colorScheme.onSurface
                    )
                    Spacer(modifier = Modifier.height(4.dp))
                    Text(
                        text = "Evaluating additives against EFSA, FDA & WHO safety guidelines",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
            }
        }

        is AnalysisUiState.Error -> {
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .background(ScannerPageBackground)
                    .padding(24.dp),
                contentAlignment = Alignment.Center
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(
                        text = "Analysis Unavailable",
                        style = MaterialTheme.typography.headlineMedium,
                        color = MaterialTheme.colorScheme.error,
                        fontWeight = FontWeight.Bold
                    )
                    Spacer(modifier = Modifier.height(8.dp))
                    Text(
                        text = state.message,
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                    Spacer(modifier = Modifier.height(20.dp))
                    Button(
                        onClick = onBack,
                        shape = RoundedCornerShape(NutriGuardRadius.medium),
                        colors = ButtonDefaults.buttonColors(containerColor = ScannerViolet)
                    ) {
                        Text("Back to Scanner")
                    }
                }
            }
        }

        is AnalysisUiState.Success -> {
            val analysis = state.analysis
            val product = analysis.product

            LazyColumn(
                modifier = Modifier
                    .fillMaxSize()
                    .background(ScannerPageBackground)
                    .padding(horizontal = 16.dp),
                verticalArrangement = Arrangement.spacedBy(NutriGuardSpacing.lg)
            ) {
                // Top Header Bar
                item {
                    Spacer(modifier = Modifier.height(NutriGuardSpacing.sm))
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .clip(RoundedCornerShape(NutriGuardRadius.hero))
                            .background(
                                Brush.horizontalGradient(
                                    listOf(ScannerHeroStart, ScannerHeroMiddle, ScannerHeroEnd)
                                )
                            )
                            .padding(18.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        IconButton(
                            onClick = onBack,
                            modifier = Modifier
                                .clip(CircleShape)
                                .background(MaterialTheme.colorScheme.surface.copy(alpha = 0.82f))
                        ) {
                            Icon(
                                imageVector = Icons.AutoMirrored.Filled.ArrowBack,
                                contentDescription = localizeUiText("Back", language),
                                tint = ScannerViolet
                            )
                        }
                        Spacer(modifier = Modifier.width(12.dp))
                        Column(modifier = Modifier.weight(1f)) {
                            Text(
                                text = productIdentityText(product.productName) ?: "Ingredients recognized",
                                style = MaterialTheme.typography.titleLarge,
                                fontWeight = FontWeight.Bold,
                                color = MaterialTheme.colorScheme.onSurface
                            )
                            val subtitle = productIdentitySubtitle(product.brand, product.category)
                            if (subtitle.isNotEmpty()) Text(
                                text = subtitle,
                                style = MaterialTheme.typography.bodyMedium,
                                color = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }
                    }
                }

                // Health Score Gauge
                item {
                    if (analysis.healthScore != null) {
                        HealthScoreGauge(
                            score = analysis.healthScore,
                            novaGroup = product.novaGroup,
                            sugarGrams = product.sugarGrams,
                            sodiumMg = product.sodiumMg,
                            saturatedFatGrams = product.saturatedFatGrams,
                            onNovaGroupClick = {
                                if (product.novaGroup in 1..4) selectedNovaGroup = product.novaGroup
                            },
                            onSugarClick = { selectedHealthFactor = HealthFactor.SUGAR },
                            onSodiumClick = { selectedHealthFactor = HealthFactor.SODIUM },
                            onSaturatedFatClick = { selectedHealthFactor = HealthFactor.SATURATED_FAT }
                        )
                    }
                }

                // Dietary Suitability Badges
                if (product.isVerified) item {
                    Column {
                        Text(
                            text = "Dietary Suitability",
                            style = MaterialTheme.typography.titleMedium,
                            fontWeight = FontWeight.SemiBold,
                            color = MaterialTheme.colorScheme.onSurface
                        )
                        Spacer(modifier = Modifier.height(8.dp))
                        DietaryBadgesRow(product = product)
                    }
                }

                // Personalized Profile Warning Banners (if any)
                if (analysis.warnings.isNotEmpty()) {
                    item {
                        PersonalizedWarningCard(warnings = analysis.warnings)
                    }
                }

                // Ingredient List Breakdown
                item {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text(
                            text = "Ingredients (${analysis.ingredients.size})",
                            style = MaterialTheme.typography.titleMedium,
                            fontWeight = FontWeight.SemiBold,
                            color = MaterialTheme.colorScheme.onSurface
                        )
                        Text(
                            text = "Tap to view scientific profile",
                            style = MaterialTheme.typography.bodySmall,
                            color = ScannerSlateMuted
                        )
                    }
                }

                if (shouldOfferLabelEnrichment(
                        barcode = product.barcode,
                        hasVerifiedIngredients = product.hasVerifiedIngredients,
                        ingredientCount = analysis.ingredients.size,
                        rawIngredientText = product.rawIngredientText
                    )
                ) {
                    item {
                        Button(
                            onClick = { onScanLabelForProduct(product.barcode) },
                            modifier = Modifier.fillMaxWidth(),
                            shape = RoundedCornerShape(NutriGuardRadius.medium),
                            colors = ButtonDefaults.buttonColors(containerColor = ScannerViolet)
                        ) {
                            Icon(
                                imageVector = Icons.Default.PhotoCamera,
                                contentDescription = null,
                                modifier = Modifier.size(22.dp)
                            )
                            Spacer(Modifier.width(10.dp))
                            Column {
                                Text(
                                    text = "Add more product information",
                                    fontWeight = FontWeight.SemiBold
                                )
                                Text(
                                    text = "Take another photo of ingredients, nutrition facts or product details",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onPrimary.copy(alpha = 0.82f)
                                )
                            }
                        }
                    }
                }

                items(analysis.ingredients) { ingredient ->
                    IngredientChip(
                        ingredient = ingredient,
                        onClick = if (hasUsefulIngredientDetails(ingredient)) {
                            { selectedIngredientForDetail = ingredient }
                        } else {
                            null
                        }
                    )
                }

                if (analysis.healthScore == null) item {
                    PendingHealthScoreCard(
                        hasVerifiedIngredients = analysis.ingredients.isNotEmpty(),
                        hasVerifiedNutrition = product.hasVerifiedNutrition
                    )
                }

                // Raw OCR Text Inspection (Collapsible / Subtle Card)
                if (product.rawIngredientText.isNotBlank()) {
                    item {
                        Card(
                            modifier = Modifier.fillMaxWidth(),
                            shape = RoundedCornerShape(NutriGuardRadius.medium),
                            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                            border = androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.outline)
                        ) {
                            Column(modifier = Modifier.padding(14.dp)) {
                                Text(
                                    text = "Scanned Ingredient Text",
                                    style = MaterialTheme.typography.titleSmall,
                                    fontWeight = FontWeight.SemiBold,
                                    color = MaterialTheme.colorScheme.onSurface
                                )
                                Spacer(modifier = Modifier.height(6.dp))
                                Text(
                                    text = product.rawIngredientText,
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    lineHeight = 18.sp
                                )
                            }
                        }
                    }
                }

                // Source Footnote
                item {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(vertical = 4.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.Center
                    ) {
                        Icon(
                            imageVector = if (analysis.isFromDatabaseCache) Icons.Default.Storage else Icons.Default.Refresh,
                            contentDescription = null,
                            tint = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.6f),
                            modifier = Modifier.size(14.dp)
                        )
                        Spacer(modifier = Modifier.width(6.dp))
                        Text(
                            text = if (analysis.isFromDatabaseCache)
                                "Loaded from saved product data"
                            else
                                "Processed by NutriGuard",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.7f)
                        )
                    }
                }

                item {
                    Spacer(modifier = Modifier.height(24.dp))
                }
            }

            // Scientific Detail Bottom Sheet Modal
            IngredientDetailBottomSheet(
                ingredient = selectedIngredientForDetail,
                onDismiss = { selectedIngredientForDetail = null }
            )
            NovaGroupInfoBottomSheet(
                selectedGroup = selectedNovaGroup,
                onDismiss = { selectedNovaGroup = null }
            )
            HealthFactorInfoBottomSheet(
                factor = selectedHealthFactor,
                value = when (selectedHealthFactor) {
                    HealthFactor.SUGAR -> product.sugarGrams
                    HealthFactor.SODIUM -> product.sodiumMg
                    HealthFactor.SATURATED_FAT -> product.saturatedFatGrams
                    null -> 0.0
                },
                onDismiss = { selectedHealthFactor = null }
            )
        }

        else -> {
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .background(ScannerPageBackground),
                contentAlignment = Alignment.Center
            ) {
                Text("No analysis available.")
            }
        }
    }
}

internal fun shouldOfferLabelEnrichment(
    barcode: String,
    hasVerifiedIngredients: Boolean,
    ingredientCount: Int,
    rawIngredientText: String
): Boolean {
    val isRealBarcode = barcode.matches(Regex("\\d{8,14}"))
    val informationLooksIncomplete =
        !hasVerifiedIngredients || ingredientCount <= 1 || rawIngredientText.isBlank()
    return isRealBarcode && informationLooksIncomplete
}

@Composable
private fun PendingHealthScoreCard(
    hasVerifiedIngredients: Boolean,
    hasVerifiedNutrition: Boolean
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(NutriGuardRadius.large),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        border = androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.outline)
    ) {
        Column(modifier = Modifier.padding(20.dp)) {
            Text(
                text = "Health Score Pending",
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onSurface
            )
            Spacer(modifier = Modifier.height(8.dp))
            Text(
                text = when {
                    hasVerifiedIngredients && !hasVerifiedNutrition ->
                        "A nutrition photo is optional and can help calculate a Health Score"
                    !hasVerifiedIngredients && hasVerifiedNutrition ->
                        "Nutrition data was found, but the ingredient list still needs a clearer scan."
                    else ->
                        "This scan saved useful product data, but more label evidence is needed before a Health Score can be calculated."
                },
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
        }
    }
}
