package com.example

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ErrorOutline
import androidx.compose.material.icons.filled.History
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.QrCodeScanner
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material.icons.outlined.History
import androidx.compose.material.icons.outlined.Person
import androidx.compose.material.icons.outlined.QrCodeScanner
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.example.ui.screens.ArchitectureAdminScreen
import com.example.ui.screens.HealthProfileScreen
import com.example.ui.screens.ProductDetailScreen
import com.example.ui.screens.ScanHomeScreen
import com.example.ui.screens.ScanHistoryScreen
import com.example.ui.screens.ScientificLibraryScreen
import com.example.ui.theme.EmeraldPrimary
import com.example.ui.theme.NutriGuardRadius
import com.example.ui.theme.NutriGuardTheme
import com.example.ui.theme.ScannerPageBackground
import com.example.ui.theme.ScannerSlateMuted
import com.example.ui.theme.ScannerSoftBorder
import com.example.ui.theme.ScannerViolet
import com.example.ui.viewmodel.MainViewModel
import kotlinx.coroutines.launch

sealed class Screen(val route: String) {
    object ScanHome : Screen("scan_home")
    object ScanHistory : Screen("scan_history")
    object HealthProfiles : Screen("health_profiles")
    object ProductDetail : Screen("product_detail")
    object ScientificLibrary : Screen("scientific_library")
    object ArchitectureAdmin : Screen("architecture_admin")
}

data class BottomNavTab(
    val route: String,
    val title: String,
    val selectedIcon: ImageVector,
    val unselectedIcon: ImageVector
)

private sealed class DeviceAuthState {
    object Initializing : DeviceAuthState()
    object Authenticated : DeviceAuthState()
    data class Error(val title: String, val message: String) : DeviceAuthState()
}

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val appContainer = (application as NutriGuardApplication).container
        setContent {
            NutriGuardTheme {
                var authState by remember {
                    mutableStateOf<DeviceAuthState>(
                        if (appContainer.authTokenStore.hasValidToken()) {
                            DeviceAuthState.Authenticated
                        } else {
                            DeviceAuthState.Initializing
                        }
                    )
                }

                val coroutineScope = rememberCoroutineScope()

                val triggerDeviceAuth: () -> Unit = {
                    authState = DeviceAuthState.Initializing
                    coroutineScope.launch {
                        val result = appContainer.authService.authenticateDevice()
                        result.fold(
                            onSuccess = {
                                authState = DeviceAuthState.Authenticated
                            },
                            onFailure = { err ->
                                val (title, message) = err.toBootstrapError()
                                authState = DeviceAuthState.Error(
                                    title = title,
                                    message = message
                                )
                            }
                        )
                    }
                }

                LaunchedEffect(Unit) {
                    if (!appContainer.authTokenStore.hasValidToken()) {
                        triggerDeviceAuth()
                    }
                }

                when (val state = authState) {
                    is DeviceAuthState.Initializing -> {
                        AuthBootstrapScreen(
                            isConnecting = true,
                            errorMessage = null,
                            onRetry = triggerDeviceAuth
                        )
                    }
                    is DeviceAuthState.Error -> {
                        AuthBootstrapScreen(
                            isConnecting = false,
                            errorTitle = state.title,
                            errorMessage = state.message,
                            onRetry = triggerDeviceAuth
                        )
                    }
                    is DeviceAuthState.Authenticated -> {
                        val viewModel: MainViewModel = viewModel(
                            factory = MainViewModel.Factory(appContainer.foodAnalysisRepository)
                        )
                        NutriGuardApp(viewModel = viewModel)
                    }
                }
            }
        }
    }
}

@Composable
private fun AuthBootstrapScreen(
    isConnecting: Boolean,
    errorTitle: String? = null,
    errorMessage: String?,
    onRetry: () -> Unit,
    modifier: Modifier = Modifier
) {
    Box(
        modifier = modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.background)
            .padding(24.dp),
        contentAlignment = Alignment.Center
    ) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center
        ) {
            Box(
                modifier = Modifier
                    .size(80.dp)
                    .background(EmeraldPrimary.copy(alpha = 0.12f), shape = RoundedCornerShape(20.dp)),
                contentAlignment = Alignment.Center
            ) {
                Icon(
                    imageVector = Icons.Default.Shield,
                    contentDescription = "NutriGuard Shield",
                    tint = EmeraldPrimary,
                    modifier = Modifier.size(44.dp)
                )
            }

            Spacer(modifier = Modifier.height(16.dp))

            Text(
                text = "NutriGuard",
                style = MaterialTheme.typography.headlineMedium,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.onBackground
            )

            Spacer(modifier = Modifier.height(24.dp))

            if (isConnecting) {
                CircularProgressIndicator(
                    modifier = Modifier.size(32.dp),
                    color = EmeraldPrimary,
                    strokeWidth = 3.dp
                )
                Spacer(modifier = Modifier.height(16.dp))
                Text(
                    text = "Connecting to NutriGuard backend...",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            } else if (errorMessage != null) {
                Card(
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(NutriGuardRadius.medium),
                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                    border = androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.error.copy(alpha = 0.5f))
                ) {
                    Column(
                        modifier = Modifier.padding(20.dp),
                        horizontalAlignment = Alignment.CenterHorizontally
                    ) {
                        Icon(
                            imageVector = Icons.Default.ErrorOutline,
                            contentDescription = "Error",
                            tint = MaterialTheme.colorScheme.error,
                            modifier = Modifier.size(36.dp)
                        )
                        Spacer(modifier = Modifier.height(12.dp))
                        Text(
                            text = errorTitle ?: "Backend Connection Failed",
                            style = MaterialTheme.typography.titleMedium,
                            fontWeight = FontWeight.SemiBold,
                            color = MaterialTheme.colorScheme.onSurface
                        )
                        Spacer(modifier = Modifier.height(8.dp))
                        Text(
                            text = errorMessage,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            textAlign = TextAlign.Center
                        )
                        Spacer(modifier = Modifier.height(16.dp))
                        Button(
                            onClick = onRetry,
                            shape = RoundedCornerShape(NutriGuardRadius.small),
                            colors = ButtonDefaults.buttonColors(containerColor = EmeraldPrimary),
                            modifier = Modifier.testTag("retry_auth_button")
                        ) {
                            Icon(
                                imageVector = Icons.Default.Refresh,
                                contentDescription = "Retry",
                                modifier = Modifier.size(18.dp)
                            )
                            Spacer(modifier = Modifier.size(8.dp))
                            Text("Retry Connection")
                        }
                    }
                }
            }
        }
    }
}

private fun Throwable.toBootstrapError(): Pair<String, String> {
    return when (this) {
        is com.example.data.auth.DeviceAuthNetworkException -> {
            "Network Connection Failed" to (message
                ?: "Unable to reach the NutriGuard backend.")
        }
        is com.example.data.auth.DeviceAuthHttpException -> {
            "Backend HTTP Error" to (message
                ?: "NutriGuard backend returned HTTP ${statusCode}.")
        }
        is com.example.data.auth.DeviceAuthParseException -> {
            "Response Parsing Failed" to (message
                ?: "NutriGuard backend returned an unexpected auth response.")
        }
        is com.example.data.auth.DeviceAuthenticationException -> {
            "Authentication Failed" to (message
                ?: "NutriGuard device authentication failed.")
        }
        else -> "Backend Connection Failed" to (message ?: "Failed to connect to NutriGuard backend.")
    }
}

@Composable
fun NutriGuardApp(
    viewModel: MainViewModel
) {
    val navController = rememberNavController()
    val navBackStackEntry by navController.currentBackStackEntryAsState()
    val currentRoute = navBackStackEntry?.destination?.route

    val tabs = listOf(
        BottomNavTab(
            route = Screen.ScanHome.route,
            title = "Scan",
            selectedIcon = Icons.Filled.QrCodeScanner,
            unselectedIcon = Icons.Outlined.QrCodeScanner
        ),
        BottomNavTab(
            route = Screen.ScanHistory.route,
            title = "History",
            selectedIcon = Icons.Filled.History,
            unselectedIcon = Icons.Outlined.History
        ),
        BottomNavTab(
            route = Screen.HealthProfiles.route,
            title = "Profile",
            selectedIcon = Icons.Filled.Person,
            unselectedIcon = Icons.Outlined.Person
        )
    )

    val showBottomBar = currentRoute in listOf(
        Screen.ScanHome.route,
        Screen.ScanHistory.route,
        Screen.HealthProfiles.route
    )

    Scaffold(
        modifier = Modifier.fillMaxSize(),
        bottomBar = {
            if (showBottomBar) {
                NavigationBar(
                    containerColor = ScannerPageBackground,
                    tonalElevation = 0.dp
                ) {
                    tabs.forEach { tab ->
                        val isSelected = currentRoute == tab.route
                        NavigationBarItem(
                            selected = isSelected,
                            onClick = {
                                if (currentRoute != tab.route) {
                                    navController.navigate(tab.route) {
                                        popUpTo(navController.graph.findStartDestination().id) {
                                            saveState = true
                                        }
                                        launchSingleTop = true
                                        restoreState = true
                                    }
                                }
                            },
                            icon = {
                                Icon(
                                    imageVector = if (isSelected) tab.selectedIcon else tab.unselectedIcon,
                                    contentDescription = tab.title
                                )
                            },
                            label = {
                                Text(
                                    text = tab.title,
                                    fontSize = 12.sp,
                                    fontWeight = if (isSelected) FontWeight.SemiBold else FontWeight.Normal
                                )
                            },
                            colors = NavigationBarItemDefaults.colors(
                                selectedIconColor = ScannerViolet,
                                selectedTextColor = ScannerViolet,
                                unselectedIconColor = ScannerSlateMuted,
                                unselectedTextColor = ScannerSlateMuted,
                                indicatorColor = ScannerViolet.copy(alpha = 0.13f),
                                disabledIconColor = ScannerSoftBorder,
                                disabledTextColor = ScannerSoftBorder
                            )
                        )
                    }
                }
            }
        }
    ) { innerPadding ->
        NavHost(
            navController = navController,
            startDestination = Screen.ScanHome.route,
            modifier = Modifier.padding(innerPadding)
        ) {
            composable(Screen.ScanHome.route) {
                ScanHomeScreen(
                    viewModel = viewModel,
                    onNavigateToResult = {
                        navController.navigate(Screen.ProductDetail.route)
                    },
                    onNavigateToLibrary = {
                        navController.navigate(Screen.ScientificLibrary.route)
                    }
                )
            }

            composable(Screen.ProductDetail.route) {
                ProductDetailScreen(
                    viewModel = viewModel,
                    onBack = {
                        navController.popBackStack()
                    }
                )
            }

            composable(Screen.ScientificLibrary.route) {
                ScientificLibraryScreen(
                    viewModel = viewModel,
                    onBack = {
                        navController.popBackStack()
                    }
                )
            }

            composable(Screen.HealthProfiles.route) {
                HealthProfileScreen(
                    viewModel = viewModel,
                    onNavigateToLibrary = {
                        navController.navigate(Screen.ScientificLibrary.route)
                    },
                    onNavigateToAdmin = {
                        navController.navigate(Screen.ArchitectureAdmin.route)
                    }
                )
            }

            composable(Screen.ScanHistory.route) {
                ScanHistoryScreen(
                    viewModel = viewModel,
                    onSelectHistoryItem = {
                        navController.navigate(Screen.ProductDetail.route)
                    },
                    onNavigateToScan = {
                        navController.navigate(Screen.ScanHome.route) {
                            popUpTo(navController.graph.findStartDestination().id) {
                                saveState = true
                            }
                            launchSingleTop = true
                        }
                    }
                )
            }

            composable(Screen.ArchitectureAdmin.route) {
                ArchitectureAdminScreen(
                    viewModel = viewModel,
                    onBack = {
                        navController.popBackStack()
                    }
                )
            }
        }
    }
}
