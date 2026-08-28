package com.example.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.TabRowDefaults
import androidx.compose.material3.TabRowDefaults.tabIndicatorOffset
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.ui.theme.EmeraldPrimary
import com.example.ui.theme.NutriGuardRadius
import com.example.ui.theme.NutriGuardSpacing
import com.example.ui.viewmodel.MainViewModel

@Composable
fun ArchitectureAdminScreen(
    viewModel: MainViewModel,
    onBack: () -> Unit = {}
) {
    val ingredients by viewModel.allIngredients.collectAsState()
    val products by viewModel.allProducts.collectAsState()
    val history by viewModel.scanHistory.collectAsState()

    var selectedTab by remember { mutableIntStateOf(0) }

    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp)
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
                        text = "System Diagnostics",
                        style = MaterialTheme.typography.titleLarge,
                        fontWeight = FontWeight.Bold,
                        color = MaterialTheme.colorScheme.onSurface
                    )
                    Text(
                        text = "Architecture & Production Specifications",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
            }

            Spacer(modifier = Modifier.height(NutriGuardSpacing.md))

            TabRow(
                selectedTabIndex = selectedTab,
                containerColor = MaterialTheme.colorScheme.surface,
                indicator = { tabPositions ->
                    TabRowDefaults.SecondaryIndicator(
                        Modifier.tabIndicatorOffset(tabPositions[selectedTab]),
                        color = EmeraldPrimary
                    )
                }
            ) {
                Tab(
                    selected = selectedTab == 0,
                    onClick = { selectedTab = 0 },
                    text = { Text("System Arch", fontSize = 12.sp, fontWeight = if (selectedTab == 0) FontWeight.Bold else FontWeight.Normal) }
                )
                Tab(
                    selected = selectedTab == 1,
                    onClick = { selectedTab = 1 },
                    text = { Text("Schema", fontSize = 12.sp, fontWeight = if (selectedTab == 1) FontWeight.Bold else FontWeight.Normal) }
                )
                Tab(
                    selected = selectedTab == 2,
                    onClick = { selectedTab = 2 },
                    text = { Text("API Spec", fontSize = 12.sp, fontWeight = if (selectedTab == 2) FontWeight.Bold else FontWeight.Normal) }
                )
                Tab(
                    selected = selectedTab == 3,
                    onClick = { selectedTab = 3 },
                    text = { Text("Docker & Ops", fontSize = 12.sp, fontWeight = if (selectedTab == 3) FontWeight.Bold else FontWeight.Normal) }
                )
            }
        }

        when (selectedTab) {
            0 -> {
                // System Architecture & Folder Structure
                item {
                    Card(
                        modifier = Modifier.fillMaxWidth(),
                        shape = RoundedCornerShape(NutriGuardRadius.large),
                        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                        border = androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.outline)
                    ) {
                        Column(modifier = Modifier.padding(16.dp)) {
                            Text(
                                text = "Modular Full-Stack Architecture",
                                style = MaterialTheme.typography.titleMedium,
                                fontWeight = FontWeight.SemiBold,
                                color = MaterialTheme.colorScheme.onSurface
                            )
                            Spacer(modifier = Modifier.height(8.dp))
                            Text(
                                text = "• Backend: Python FastAPI (Asynchronous ASGI)\n" +
                                        "• Database: PostgreSQL 16 + SQLAlchemy ORM\n" +
                                        "• OCR Pipeline: Tesseract v5 + OpenCV Pre-processing\n" +
                                        "• Cache & Message Queue: Redis\n" +
                                        "• Object Storage: MinIO (S3 compatible for label photos)\n" +
                                        "• Reverse Proxy: Nginx SSL Termination\n" +
                                        "• Mobile Client: Jetpack Compose Native Engine\n" +
                                        "• Authentication: JWT (JSON Web Tokens)",
                                style = MaterialTheme.typography.bodyMedium,
                                color = MaterialTheme.colorScheme.onSurfaceVariant
                            )

                            Spacer(modifier = Modifier.height(14.dp))
                            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                            Spacer(modifier = Modifier.height(14.dp))

                            Text(
                                text = "Production Project Folder Structure",
                                style = MaterialTheme.typography.titleMedium,
                                fontWeight = FontWeight.SemiBold,
                                color = MaterialTheme.colorScheme.onSurface
                            )
                            Spacer(modifier = Modifier.height(8.dp))

                            CodeSnippetBox(
                                code = """
                                nutriguard_platform/
                                ├── backend/
                                │   ├── app/
                                │   │   ├── api/v1/          # Endpoints (auth, scan, ingredients)
                                │   │   ├── core/            # Security, config, JWT
                                │   │   ├── db/              # Postgres sessions, migrations
                                │   │   ├── models/          # SQLAlchemy ORM schemas
                                │   │   ├── services/        # OCR pipeline, AI analyzer
                                │   │   └── schemas/         # Pydantic request/response
                                │   ├── Dockerfile
                                │   └── requirements.txt
                                ├── nginx/
                                │   └── nginx.conf
                                ├── docker-compose.yml
                                └── mobile_android/          # Android Jetpack Compose app
                                """.trimIndent()
                            )
                        }
                    }
                }
            }

            1 -> {
                // PostgreSQL Database Schema Specification
                item {
                    Card(
                        modifier = Modifier.fillMaxWidth(),
                        shape = RoundedCornerShape(NutriGuardRadius.large),
                        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                        border = androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.outline)
                    ) {
                        Column(modifier = Modifier.padding(16.dp)) {
                            Text(
                                text = "PostgreSQL Database Schema",
                                style = MaterialTheme.typography.titleMedium,
                                fontWeight = FontWeight.SemiBold,
                                color = MaterialTheme.colorScheme.onSurface
                            )

                            Spacer(modifier = Modifier.height(8.dp))

                            CodeSnippetBox(
                                code = """
                                CREATE TABLE scientific_ingredients (
                                    id VARCHAR(64) PRIMARY KEY,
                                    common_name VARCHAR(255) NOT NULL,
                                    scientific_name VARCHAR(255),
                                    e_number VARCHAR(16) UNIQUE,
                                    category VARCHAR(64),
                                    description TEXT,
                                    purpose_in_food TEXT,
                                    health_concerns TEXT,
                                    evidence_level VARCHAR(64),
                                    countries_restricted TEXT,
                                    efsa_status VARCHAR(128),
                                    fda_status VARCHAR(128),
                                    who_iarc_classification VARCHAR(64),
                                    acceptable_daily_intake VARCHAR(64),
                                    side_effects TEXT,
                                    allergens TEXT,
                                    references TEXT,
                                    risk_level VARCHAR(32) NOT NULL,
                                    bad_for_diabetes BOOLEAN DEFAULT FALSE,
                                    bad_for_hypertension BOOLEAN DEFAULT FALSE,
                                    bad_for_kidney_disease BOOLEAN DEFAULT FALSE,
                                    bad_for_gout BOOLEAN DEFAULT FALSE,
                                    bad_for_pregnancy BOOLEAN DEFAULT FALSE,
                                    bad_for_children BOOLEAN DEFAULT FALSE,
                                    bad_for_high_cholesterol BOOLEAN DEFAULT FALSE
                                );

                                CREATE TABLE products (
                                    barcode VARCHAR(32) PRIMARY KEY,
                                    product_name VARCHAR(255) NOT NULL,
                                    brand VARCHAR(128),
                                    category VARCHAR(64),
                                    raw_ingredient_text TEXT,
                                    health_score INT NOT NULL,
                                    nova_group INT NOT NULL,
                                    sugar_grams NUMERIC(6,2),
                                    sodium_mg NUMERIC(8,2),
                                    saturated_fat_grams NUMERIC(6,2),
                                    has_artificial_sweeteners BOOLEAN,
                                    has_preservatives BOOLEAN,
                                    is_gluten_free BOOLEAN,
                                    is_lactose_free BOOLEAN,
                                    is_vegan BOOLEAN,
                                    is_vegetarian BOOLEAN,
                                    is_halal BOOLEAN,
                                    is_kosher BOOLEAN,
                                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                                );
                                """.trimIndent()
                            )
                        }
                    }
                }
            }

            2 -> {
                // REST API Specification
                item {
                    Card(
                        modifier = Modifier.fillMaxWidth(),
                        shape = RoundedCornerShape(NutriGuardRadius.large),
                        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                        border = androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.outline)
                    ) {
                        Column(modifier = Modifier.padding(16.dp)) {
                            Text(
                                text = "REST API Specification (FastAPI / OpenAPI)",
                                style = MaterialTheme.typography.titleMedium,
                                fontWeight = FontWeight.SemiBold,
                                color = MaterialTheme.colorScheme.onSurface
                            )

                            Spacer(modifier = Modifier.height(8.dp))

                            CodeSnippetBox(
                                code = """
                                POST /api/v1/auth/token
                                -> Returns JWT OAuth2 access token

                                GET /api/v1/products/{barcode}
                                -> Returns stored product analysis & scientific ingredients

                                POST /api/v1/ocr/scan-label
                                Body: Multipart form with photo label or raw text
                                -> Runs OpenCV pre-processing + Tesseract OCR
                                -> Normalizes E-numbers against DB
                                -> Returns Health Score 0-100 & personalized profile warnings

                                GET /api/v1/ingredients?search={query}&risk_level={level}
                                -> Returns paginated list of scientific ingredient records

                                PUT /api/v1/users/me/health-profile
                                -> Updates user health toggles (diabetes, hypertension, pregnancy, etc.)
                                """.trimIndent()
                            )
                        }
                    }
                }
            }

            3 -> {
                // Docker Deployment & Live Admin Metrics
                item {
                    Card(
                        modifier = Modifier.fillMaxWidth(),
                        shape = RoundedCornerShape(NutriGuardRadius.large),
                        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                        border = androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.outline)
                    ) {
                        Column(modifier = Modifier.padding(16.dp)) {
                            Text(
                                text = "Database & Cache Metrics",
                                style = MaterialTheme.typography.titleMedium,
                                fontWeight = FontWeight.SemiBold,
                                color = MaterialTheme.colorScheme.onSurface
                            )

                            Spacer(modifier = Modifier.height(10.dp))

                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.spacedBy(8.dp)
                            ) {
                                Box(modifier = Modifier.weight(1f)) {
                                    MetricBadge(title = "Ingredients", count = "${ingredients.size}")
                                }
                                Box(modifier = Modifier.weight(1f)) {
                                    MetricBadge(title = "Cached Products", count = "${products.size}")
                                }
                                Box(modifier = Modifier.weight(1f)) {
                                    MetricBadge(title = "Scans Logged", count = "${history.size}")
                                }
                            }

                            Spacer(modifier = Modifier.height(16.dp))
                            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                            Spacer(modifier = Modifier.height(16.dp))

                            Text(
                                text = "Docker Deployment Configuration",
                                style = MaterialTheme.typography.titleMedium,
                                fontWeight = FontWeight.SemiBold,
                                color = MaterialTheme.colorScheme.onSurface
                            )

                            Spacer(modifier = Modifier.height(8.dp))

                            CodeSnippetBox(
                                code = """
                                version: '3.8'

                                services:
                                  postgres:
                                    image: postgres:16-alpine
                                    environment:
                                      POSTGRES_DB: nutriguard
                                      POSTGRES_USER: admin
                                      POSTGRES_PASSWORD: ${'$'}{POSTGRES_PASSWORD}
                                    volumes:
                                      - postgres_data:/var/lib/postgresql/data

                                  redis:
                                    image: redis:7-alpine

                                  backend:
                                    build: ./backend
                                    environment:
                                      DATABASE_URL: postgresql://admin:${'$'}{POSTGRES_PASSWORD}@postgres:5432/nutriguard
                                      REDIS_URL: redis://redis:6379/0
                                    depends_on:
                                      - postgres
                                      - redis

                                  nginx:
                                    image: nginx:alpine
                                    ports:
                                      - "80:80"
                                      - "443:443"
                                    volumes:
                                      - ./nginx/nginx.conf:/etc/nginx/nginx.conf
                                """.trimIndent()
                            )
                        }
                    }
                }
            }
        }

        item {
            Spacer(modifier = Modifier.height(24.dp))
        }
    }
}

@Composable
private fun CodeSnippetBox(code: String) {
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(NutriGuardRadius.medium))
            .background(MaterialTheme.colorScheme.background)
            .border(
                1.dp,
                MaterialTheme.colorScheme.outline,
                RoundedCornerShape(NutriGuardRadius.medium)
            )
            .padding(12.dp)
            .horizontalScroll(rememberScrollState())
    ) {
        Text(
            text = code,
            fontFamily = FontFamily.Monospace,
            fontSize = 11.sp,
            color = MaterialTheme.colorScheme.onSurface,
            lineHeight = 16.sp
        )
    }
}

@Composable
private fun MetricBadge(title: String, count: String) {
    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(NutriGuardRadius.medium))
            .background(EmeraldPrimary.copy(alpha = 0.08f))
            .border(
                1.dp,
                EmeraldPrimary.copy(alpha = 0.2f),
                RoundedCornerShape(NutriGuardRadius.medium)
            )
            .padding(horizontal = 12.dp, vertical = 12.dp)
    ) {
        Text(
            text = count,
            fontSize = 20.sp,
            fontWeight = FontWeight.Bold,
            color = EmeraldPrimary
        )
        Spacer(modifier = Modifier.height(2.dp))
        Text(
            text = title,
            fontSize = 11.sp,
            fontWeight = FontWeight.Medium,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
    }
}

