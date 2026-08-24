package com.example

import android.app.Application
import android.content.Context
import com.example.data.auth.AuthInterceptor
import com.example.data.auth.AuthTokenStore
import com.example.data.auth.NutriGuardAuthService
import com.example.data.auth.SharedPreferencesAuthTokenStore
import com.example.data.db.AppDatabase
import com.example.data.remote.NutriGuardApiService
import com.example.data.repository.FoodAnalysisRepository
import okhttp3.OkHttpClient
import java.util.concurrent.TimeUnit

/**
 * Dependency container interface holding Application-scoped dependencies.
 */
interface AppContainer {
    val authTokenStore: AuthTokenStore
    val authService: NutriGuardAuthService
    val apiService: NutriGuardApiService
    val foodAnalysisRepository: FoodAnalysisRepository
}

/**
 * Default implementation of AppContainer that initializes and holds
 * application-scoped repositories, token store, and authenticated network client.
 */
class DefaultAppContainer(private val context: Context) : AppContainer {
    private val database: AppDatabase by lazy {
        AppDatabase.getDatabase(context)
    }

    override val authTokenStore: AuthTokenStore by lazy {
        SharedPreferencesAuthTokenStore(context)
    }

    private val authInterceptor: AuthInterceptor by lazy {
        AuthInterceptor(
            tokenStore = authTokenStore,
            authServiceProvider = { authService }
        )
    }

    // Base unauthenticated client for auth endpoint calls (/auth/device, /auth/refresh)
    private val unauthenticatedHttpClient: OkHttpClient by lazy {
        OkHttpClient.Builder()
            .connectTimeout(30, TimeUnit.SECONDS)
            .readTimeout(60, TimeUnit.SECONDS)
            .writeTimeout(60, TimeUnit.SECONDS)
            .build()
    }

    // Shared authenticated client with AuthInterceptor (used for protected API requests)
    private val authenticatedHttpClient: OkHttpClient by lazy {
        OkHttpClient.Builder()
            .connectTimeout(30, TimeUnit.SECONDS)
            .readTimeout(60, TimeUnit.SECONDS)
            .writeTimeout(60, TimeUnit.SECONDS)
            .addInterceptor(authInterceptor)
            .build()
    }

    override val authService: NutriGuardAuthService by lazy {
        NutriGuardAuthService(
            httpClient = unauthenticatedHttpClient,
            tokenStore = authTokenStore
        )
    }

    override val apiService: NutriGuardApiService by lazy {
        NutriGuardApiService(
            httpClient = authenticatedHttpClient
        )
    }

    override val foodAnalysisRepository: FoodAnalysisRepository by lazy {
        FoodAnalysisRepository(
            ingredientDao = database.ingredientDao(),
            productDao = database.productDao(),
            userProfileDao = database.userHealthProfileDao(),
            scanHistoryDao = database.scanHistoryDao(),
            apiService = apiService
        )
    }
}

/**
 * Application class for NutriGuard managing application-level state and DI container.
 */
class NutriGuardApplication : Application() {
    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        container = DefaultAppContainer(applicationContext)
    }
}
