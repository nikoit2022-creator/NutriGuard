package com.example.data.auth

import android.content.Context
import android.content.SharedPreferences
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.util.UUID

interface AuthTokenStore {
    val isAuthenticated: StateFlow<Boolean>
    fun getDeviceId(): String
    fun getAccessToken(): String?
    fun getRefreshToken(): String?
    fun getUserId(): String?
    fun saveTokens(
        accessToken: String,
        refreshToken: String,
        userId: String? = null,
        expiresInSeconds: Long = 3600
    )
    fun clearTokens()
    fun hasValidToken(): Boolean
}

class SharedPreferencesAuthTokenStore(context: Context) : AuthTokenStore {
    private val prefs: SharedPreferences = context.getSharedPreferences(
        PREFS_NAME,
        Context.MODE_PRIVATE
    )

    private val _isAuthenticated = MutableStateFlow(hasValidToken())
    override val isAuthenticated: StateFlow<Boolean> = _isAuthenticated.asStateFlow()

    @Synchronized
    override fun getDeviceId(): String {
        var deviceId = prefs.getString(KEY_DEVICE_ID, null)
        if (deviceId.isNullOrBlank()) {
            deviceId = UUID.randomUUID().toString()
            prefs.edit().putString(KEY_DEVICE_ID, deviceId).apply()
        }
        return deviceId
    }

    override fun getAccessToken(): String? {
        return prefs.getString(KEY_ACCESS_TOKEN, null)?.takeIf { it.isNotBlank() }
    }

    override fun getRefreshToken(): String? {
        return prefs.getString(KEY_REFRESH_TOKEN, null)?.takeIf { it.isNotBlank() }
    }

    override fun getUserId(): String? {
        return prefs.getString(KEY_USER_ID, null)?.takeIf { it.isNotBlank() }
    }

    @Synchronized
    override fun saveTokens(
        accessToken: String,
        refreshToken: String,
        userId: String?,
        expiresInSeconds: Long
    ) {
        val expiresAt = System.currentTimeMillis() + (expiresInSeconds * 1000)
        prefs.edit()
            .putString(KEY_ACCESS_TOKEN, accessToken)
            .putString(KEY_REFRESH_TOKEN, refreshToken)
            .putString(KEY_USER_ID, userId)
            .putLong(KEY_EXPIRES_AT, expiresAt)
            .apply()
        _isAuthenticated.value = true
    }

    @Synchronized
    override fun clearTokens() {
        prefs.edit()
            .remove(KEY_ACCESS_TOKEN)
            .remove(KEY_REFRESH_TOKEN)
            .remove(KEY_USER_ID)
            .remove(KEY_EXPIRES_AT)
            .apply()
        _isAuthenticated.value = false
    }

    override fun hasValidToken(): Boolean {
        val token = getAccessToken()
        if (token.isNullOrBlank()) return false
        val expiresAt = prefs.getLong(KEY_EXPIRES_AT, 0L)
        // Token is considered valid if expiration is at least 30 seconds in the future
        return expiresAt > (System.currentTimeMillis() + 30_000)
    }

    companion object {
        private const val PREFS_NAME = "nutriguard_secure_auth_prefs"
        private const val KEY_DEVICE_ID = "device_id"
        private const val KEY_ACCESS_TOKEN = "access_token"
        private const val KEY_REFRESH_TOKEN = "refresh_token"
        private const val KEY_USER_ID = "user_id"
        private const val KEY_EXPIRES_AT = "expires_at"
    }
}
