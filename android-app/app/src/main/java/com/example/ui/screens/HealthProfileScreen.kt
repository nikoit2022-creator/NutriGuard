package com.example.ui.screens

import androidx.compose.foundation.background
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
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material.icons.filled.DeveloperMode
import androidx.compose.material.icons.filled.Science
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.data.model.UserHealthProfile
import com.example.ui.theme.NutriGuardScannerTheme
import com.example.ui.theme.NutriGuardRadius
import com.example.ui.theme.NutriGuardSpacing
import com.example.ui.viewmodel.MainViewModel

@Composable
fun HealthProfileScreen(
    viewModel: MainViewModel,
    onNavigateToLibrary: () -> Unit = {},
    onNavigateToAdmin: () -> Unit = {}
) {
    val profileState by viewModel.userProfile.collectAsState()
    val profile = profileState ?: UserHealthProfile()

    // Calculate active count
    val activeCount = listOf(
        profile.hasDiabetes, profile.hasHypertension, profile.hasKidneyDisease,
        profile.hasGout, profile.isPregnant, profile.forChildren,
        profile.hasHighCholesterol, profile.avoidGluten, profile.avoidLactose,
        profile.requireVegan, profile.requireHalal, profile.requireKosher
    ).count { it }

    NutriGuardScannerTheme {
        LazyColumn(
            modifier = Modifier
                .fillMaxSize()
                .padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(NutriGuardSpacing.lg)
        ) {
        item {
            Spacer(modifier = Modifier.height(NutriGuardSpacing.md))
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Column {
                    Text(
                        text = "Health Profile",
                        style = MaterialTheme.typography.headlineLarge,
                        fontWeight = FontWeight.Bold,
                        color = MaterialTheme.colorScheme.onSurface
                    )
                    Text(
                        text = "Customize alerts for your health needs and dietary preferences",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
            }

            if (activeCount > 0) {
                Spacer(modifier = Modifier.height(10.dp))
                Box(
                    modifier = Modifier
                        .clip(RoundedCornerShape(NutriGuardRadius.pill))
                        .background(MaterialTheme.colorScheme.primary.copy(alpha = 0.12f))
                        .padding(horizontal = 12.dp, vertical = 5.dp)
                ) {
                    Text(
                        text = "$activeCount active filter${if (activeCount > 1) "s" else ""} applied to scans",
                        fontSize = 12.sp,
                        fontWeight = FontWeight.SemiBold,
                        color = MaterialTheme.colorScheme.primary
                    )
                }
            }
        }

        // Medical Health Conditions Section
        item {
            Card(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(NutriGuardRadius.large),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                border = androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.outline)
            ) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text(
                        text = "Health Conditions & Life Stages",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold,
                        color = MaterialTheme.colorScheme.onSurface
                    )

                    Spacer(modifier = Modifier.height(12.dp))

                    ProfileToggleRow(
                        title = "Diabetes & Glycemic Control",
                        subtitle = "Alerts high sugar (>5g), maltodextrin, dextrose, & high GI sweeteners",
                        checked = profile.hasDiabetes,
                        onCheckedChange = { viewModel.updateProfile(profile.copy(hasDiabetes = it)) }
                    )

                    HorizontalDivider(modifier = Modifier.padding(vertical = 10.dp), color = MaterialTheme.colorScheme.outlineVariant)

                    ProfileToggleRow(
                        title = "Hypertension (High Blood Pressure)",
                        subtitle = "Alerts high sodium (>400mg), MSG (E621), & sodium preservers",
                        checked = profile.hasHypertension,
                        onCheckedChange = { viewModel.updateProfile(profile.copy(hasHypertension = it)) }
                    )

                    HorizontalDivider(modifier = Modifier.padding(vertical = 10.dp), color = MaterialTheme.colorScheme.outlineVariant)

                    ProfileToggleRow(
                        title = "Kidney & Renal Health",
                        subtitle = "Alerts high sodium, potassium, & phosphate additives (E338-E343)",
                        checked = profile.hasKidneyDisease,
                        onCheckedChange = { viewModel.updateProfile(profile.copy(hasKidneyDisease = it)) }
                    )

                    HorizontalDivider(modifier = Modifier.padding(vertical = 10.dp), color = MaterialTheme.colorScheme.outlineVariant)

                    ProfileToggleRow(
                        title = "Gout / High Uric Acid",
                        subtitle = "Alerts high fructose corn syrup, purine boosters, & yeast extracts",
                        checked = profile.hasGout,
                        onCheckedChange = { viewModel.updateProfile(profile.copy(hasGout = it)) }
                    )

                    HorizontalDivider(modifier = Modifier.padding(vertical = 10.dp), color = MaterialTheme.colorScheme.outlineVariant)

                    ProfileToggleRow(
                        title = "Pregnancy & Lactation",
                        subtitle = "Alerts nitrates, nitrites, saccharin, titanium dioxide, & unpasteurized additives",
                        checked = profile.isPregnant,
                        onCheckedChange = { viewModel.updateProfile(profile.copy(isPregnant = it)) }
                    )

                    HorizontalDivider(modifier = Modifier.padding(vertical = 10.dp), color = MaterialTheme.colorScheme.outlineVariant)

                    ProfileToggleRow(
                        title = "Children & Toddlers",
                        subtitle = "Alerts Southampton Six hyperactivity dyes (E102, E110, E129) & high sugar",
                        checked = profile.forChildren,
                        onCheckedChange = { viewModel.updateProfile(profile.copy(forChildren = it)) }
                    )

                    HorizontalDivider(modifier = Modifier.padding(vertical = 10.dp), color = MaterialTheme.colorScheme.outlineVariant)

                    ProfileToggleRow(
                        title = "Cardiovascular & High Cholesterol",
                        subtitle = "Alerts saturated fats (>4g), palm oil, trans fats, & hydrogenated oils",
                        checked = profile.hasHighCholesterol,
                        onCheckedChange = { viewModel.updateProfile(profile.copy(hasHighCholesterol = it)) }
                    )
                }
            }
        }

        // Dietary & Allergen Exclusions
        item {
            Card(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(NutriGuardRadius.large),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                border = androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.outline)
            ) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text(
                        text = "Dietary Preferences & Allergens",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold,
                        color = MaterialTheme.colorScheme.onSurface
                    )

                    Spacer(modifier = Modifier.height(12.dp))

                    ProfileToggleRow(
                        title = "Gluten-Free",
                        subtitle = "Alerts wheat, barley, rye, or gluten cross-contact",
                        checked = profile.avoidGluten,
                        onCheckedChange = { viewModel.updateProfile(profile.copy(avoidGluten = it)) }
                    )

                    HorizontalDivider(modifier = Modifier.padding(vertical = 10.dp), color = MaterialTheme.colorScheme.outlineVariant)

                    ProfileToggleRow(
                        title = "Lactose-Free",
                        subtitle = "Alerts milk, whey, butter, or lactose derived ingredients",
                        checked = profile.avoidLactose,
                        onCheckedChange = { viewModel.updateProfile(profile.copy(avoidLactose = it)) }
                    )

                    HorizontalDivider(modifier = Modifier.padding(vertical = 10.dp), color = MaterialTheme.colorScheme.outlineVariant)

                    ProfileToggleRow(
                        title = "Vegan",
                        subtitle = "Alerts animal-derived ingredients, gelatin, & carmine (E120)",
                        checked = profile.requireVegan,
                        onCheckedChange = { viewModel.updateProfile(profile.copy(requireVegan = it)) }
                    )

                    HorizontalDivider(modifier = Modifier.padding(vertical = 10.dp), color = MaterialTheme.colorScheme.outlineVariant)

                    ProfileToggleRow(
                        title = "Halal Compliance",
                        subtitle = "Alerts pork derivatives, alcohol carriers, & non-halal additives",
                        checked = profile.requireHalal,
                        onCheckedChange = { viewModel.updateProfile(profile.copy(requireHalal = it)) }
                    )

                    HorizontalDivider(modifier = Modifier.padding(vertical = 10.dp), color = MaterialTheme.colorScheme.outlineVariant)

                    ProfileToggleRow(
                        title = "Kosher Compliance",
                        subtitle = "Alerts non-kosher processing agents or uncertified origins",
                        checked = profile.requireKosher,
                        onCheckedChange = { viewModel.updateProfile(profile.copy(requireKosher = it)) }
                    )
                }
            }
        }

        // Knowledge Base & Resources
        item {
            Card(
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(NutriGuardRadius.large))
                    .clickable { onNavigateToLibrary() },
                shape = RoundedCornerShape(NutriGuardRadius.large),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                border = androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.outline)
            ) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(16.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        modifier = Modifier.weight(1f)
                    ) {
                        Box(
                            modifier = Modifier
                                .size(40.dp)
                                .clip(CircleShape)
                                .background(MaterialTheme.colorScheme.primary.copy(alpha = 0.12f)),
                            contentAlignment = Alignment.Center
                        ) {
                            Icon(
                                imageVector = Icons.Default.Science,
                                contentDescription = null,
                                tint = MaterialTheme.colorScheme.primary,
                                modifier = Modifier.size(20.dp)
                            )
                        }

                        Spacer(modifier = Modifier.width(12.dp))

                        Column {
                            Text(
                                text = "Scientific Ingredient Library",
                                style = MaterialTheme.typography.titleMedium,
                                fontWeight = FontWeight.SemiBold,
                                color = MaterialTheme.colorScheme.onSurface
                            )
                            Text(
                                text = "Browse additive encyclopedia & safety studies",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }
                    }

                    Icon(
                        imageVector = Icons.Default.ChevronRight,
                        contentDescription = "Navigate",
                        tint = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.5f)
                    )
                }
            }
        }

        // Developer & System Diagnostics Link (Subtle, professional)
        item {
            Card(
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(NutriGuardRadius.medium))
                    .clickable { onNavigateToAdmin() },
                shape = RoundedCornerShape(NutriGuardRadius.medium),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                border = androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.outline)
            ) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(14.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(
                            imageVector = Icons.Default.DeveloperMode,
                            contentDescription = null,
                            tint = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.6f),
                            modifier = Modifier.size(18.dp)
                        )
                        Spacer(modifier = Modifier.width(10.dp))
                        Text(
                            text = "Developer & Architecture Diagnostics",
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }

                    Icon(
                        imageVector = Icons.Default.ChevronRight,
                        contentDescription = null,
                        tint = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.4f),
                        modifier = Modifier.size(18.dp)
                    )
                }
            }
        }

            item {
                Spacer(modifier = Modifier.height(24.dp))
            }
        }
    }
}

@Composable
private fun ProfileToggleRow(
    title: String,
    subtitle: String,
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = title,
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onSurface
            )
            Spacer(modifier = Modifier.height(2.dp))
            Text(
                text = subtitle,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                lineHeight = 16.sp
            )
        }

        Spacer(modifier = Modifier.width(12.dp))

        Switch(
            checked = checked,
            onCheckedChange = onCheckedChange,
            colors = SwitchDefaults.colors(
                checkedThumbColor = MaterialTheme.colorScheme.surface,
                checkedTrackColor = MaterialTheme.colorScheme.primary
            )
        )
    }
}
