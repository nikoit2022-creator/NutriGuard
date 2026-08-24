package com.example.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.data.model.IngredientEntity
import com.example.data.model.RiskLevel
import com.example.ui.components.IngredientChip
import com.example.ui.components.IngredientDetailBottomSheet
import com.example.ui.theme.EmeraldPrimary
import com.example.ui.theme.NutriGuardRadius
import com.example.ui.theme.NutriGuardSpacing
import com.example.ui.viewmodel.MainViewModel

@Composable
fun ScientificLibraryScreen(
    viewModel: MainViewModel,
    onBack: () -> Unit = {}
) {
    val ingredients by viewModel.allIngredients.collectAsState()
    val searchQuery by viewModel.ingredientSearchQuery.collectAsState()
    val riskFilter by viewModel.selectedRiskFilter.collectAsState()

    var selectedIngredientForDetail by remember { mutableStateOf<IngredientEntity?>(null) }

    val filteredList = ingredients.filter { ing ->
        val matchesQuery = searchQuery.isBlank() ||
                ing.commonName.contains(searchQuery, ignoreCase = true) ||
                ing.scientificName.contains(searchQuery, ignoreCase = true) ||
                ing.eNumber?.contains(searchQuery, ignoreCase = true) == true ||
                ing.category.contains(searchQuery, ignoreCase = true)

        val matchesRisk = riskFilter == null || ing.riskLevel == riskFilter
        matchesQuery && matchesRisk
    }

    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp)
    ) {
        item {
            Spacer(modifier = Modifier.height(NutriGuardSpacing.sm))
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically
            ) {
                IconButton(
                    onClick = onBack,
                    modifier = Modifier
                        .clip(CircleShape)
                        .background(MaterialTheme.colorScheme.surfaceVariant)
                ) {
                    Icon(
                        imageVector = Icons.AutoMirrored.Filled.ArrowBack,
                        contentDescription = "Back",
                        tint = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
                Spacer(modifier = Modifier.width(12.dp))
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = "Ingredient Library",
                        style = MaterialTheme.typography.titleLarge,
                        fontWeight = FontWeight.Bold,
                        color = MaterialTheme.colorScheme.onSurface
                    )
                    Text(
                        text = "EFSA, FDA & WHO safety classifications",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
            }

            Spacer(modifier = Modifier.height(NutriGuardSpacing.md))

            OutlinedTextField(
                value = searchQuery,
                onValueChange = { viewModel.setIngredientSearchQuery(it) },
                placeholder = { Text("Search by E-number, name, or category...", fontSize = 14.sp) },
                leadingIcon = {
                    Icon(
                        imageVector = Icons.Default.Search,
                        contentDescription = "Search",
                        tint = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                },
                trailingIcon = {
                    if (searchQuery.isNotEmpty()) {
                        IconButton(onClick = { viewModel.setIngredientSearchQuery("") }) {
                            Icon(
                                imageVector = Icons.Default.Close,
                                contentDescription = "Clear search",
                                tint = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }
                    }
                },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
                shape = RoundedCornerShape(NutriGuardRadius.medium),
                colors = OutlinedTextFieldDefaults.colors(
                    focusedBorderColor = EmeraldPrimary,
                    unfocusedBorderColor = MaterialTheme.colorScheme.outline
                )
            )

            Spacer(modifier = Modifier.height(10.dp))

            // Risk Filter Pills
            LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                item {
                    FilterPill(
                        label = "All (${ingredients.size})",
                        isSelected = riskFilter == null,
                        onClick = { viewModel.setRiskFilter(null) }
                    )
                }
                item {
                    FilterPill(
                        label = "Safe",
                        isSelected = riskFilter == RiskLevel.SAFE,
                        onClick = { viewModel.setRiskFilter(RiskLevel.SAFE) }
                    )
                }
                item {
                    FilterPill(
                        label = "Moderate",
                        isSelected = riskFilter == RiskLevel.MODERATE,
                        onClick = { viewModel.setRiskFilter(RiskLevel.MODERATE) }
                    )
                }
                item {
                    FilterPill(
                        label = "Potential Concern",
                        isSelected = riskFilter == RiskLevel.POTENTIAL_CONCERN,
                        onClick = { viewModel.setRiskFilter(RiskLevel.POTENTIAL_CONCERN) }
                    )
                }
                item {
                    FilterPill(
                        label = "High Concern",
                        isSelected = riskFilter == RiskLevel.HIGH_CONCERN,
                        onClick = { viewModel.setRiskFilter(RiskLevel.HIGH_CONCERN) }
                    )
                }
            }

            Spacer(modifier = Modifier.height(4.dp))
        }

        if (filteredList.isEmpty()) {
            item {
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(vertical = 40.dp),
                    contentAlignment = Alignment.Center
                ) {
                    Text(
                        text = "No ingredients matching your criteria",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
            }
        } else {
            items(filteredList) { ingredient ->
                IngredientChip(
                    ingredient = ingredient,
                    onClick = { selectedIngredientForDetail = ingredient }
                )
            }
        }

        item {
            Spacer(modifier = Modifier.height(24.dp))
        }
    }

    IngredientDetailBottomSheet(
        ingredient = selectedIngredientForDetail,
        onDismiss = { selectedIngredientForDetail = null }
    )
}

@Composable
private fun FilterPill(
    label: String,
    isSelected: Boolean,
    onClick: () -> Unit
) {
    Box(
        modifier = Modifier
            .clip(RoundedCornerShape(NutriGuardRadius.pill))
            .background(
                if (isSelected) EmeraldPrimary else MaterialTheme.colorScheme.surface
            )
            .border(
                width = 1.dp,
                color = if (isSelected) EmeraldPrimary else MaterialTheme.colorScheme.outline,
                shape = RoundedCornerShape(NutriGuardRadius.pill)
            )
            .clickable { onClick() }
            .padding(horizontal = 14.dp, vertical = 7.dp)
    ) {
        Text(
            text = label,
            fontSize = 12.sp,
            fontWeight = if (isSelected) FontWeight.Bold else FontWeight.Medium,
            color = if (isSelected) MaterialTheme.colorScheme.onPrimary else MaterialTheme.colorScheme.onSurface
        )
    }
}

