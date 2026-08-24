package com.example.data.auth

import android.util.Log
import com.example.BuildConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.IOException

sealed class DeviceAuthException(message: String, cause: Throwable? = null) : IOException(message, cause)

class DeviceAuthNetworkException(message: String, cause: Throwable? = null) : DeviceAuthException(message, cause)

class DeviceAuthHttpException(val statusCode: Int, message: String) : DeviceAuthException(message)

class DeviceAuthParseException(message: String, cause: Throwable? = null) : DeviceAuthException(message, cause)

class DeviceAuthenticationException(message: String, cause: Throwable? = null) : DeviceAuthException(message, cause)

data class DeviceTokenResponse(
    val accessToken: String,
    val refreshToken: String,
    val tokenType: String = "Bearer",
    val expiresIn: Long = 3600,
    val userId: String? = null
)

class NutriGuardAuthService(
    private val httpClient: OkHttpClient,
    private val tokenStore: AuthTokenStore
) {
    private val baseUrl: String = BuildConfig.BACKEND_BASE_URL.trimEnd('/')

    /**
     * Authenticates the device using anonymous device authentication:
     * POST /api/v1/auth/device
     */
    suspend fun authenticateDevice(appVersion: String = "1.0.0"): Result<DeviceTokenResponse> = withContext(Dispatchers.IO) {
        val deviceId = tokenStore.getDeviceId()
        val endpointUrl = "$baseUrl/api/v1/auth/device"

        val jsonPayload = JSONObject().apply {
            put("deviceId", deviceId)
            put("appVersion", appVersion)
            put("platform", "ANDROID")
        }

        Log.d(TAG, "Executing device authentication to $endpointUrl")

        val request = Request.Builder()
            .url(endpointUrl)
            .post(jsonPayload.toString().toRequestBody("application/json".toMediaType()))
            .header("Accept", "application/json")
            .build()

        try {
            val response = httpClient.newCall(request).execute()
            response.use { resp ->
                val bodyString = resp.body?.string() ?: ""
                if (!resp.isSuccessful) {
                    val errorDetail = parseErrorDetail(bodyString, resp.code)
                    Log.e(TAG, "Device auth failed with HTTP ${resp.code}: $errorDetail")
                    return@withContext Result.failure(DeviceAuthHttpException(resp.code, errorDetail))
                }

                val tokenResponse = parseTokenResponse(bodyString)
                tokenStore.saveTokens(
                    accessToken = tokenResponse.accessToken,
                    refreshToken = tokenResponse.refreshToken,
                    userId = tokenResponse.userId,
                    expiresInSeconds = tokenResponse.expiresIn
                )
                Log.d(TAG, "Device authentication successful. Tokens saved.")
                return@withContext Result.success(tokenResponse)
            }
        } catch (e: DeviceAuthException) {
            Log.e(TAG, "Device auth failed: ${e.message}", e)
            return@withContext Result.failure(e)
        } catch (e: IOException) {
            Log.e(TAG, "Network failure during device auth: ${e.localizedMessage}", e)
            return@withContext Result.failure(
                DeviceAuthNetworkException(
                    "Unable to reach NutriGuard backend at $baseUrl. Check network connection.",
                    e
                )
            )
        } catch (e: Exception) {
            Log.e(TAG, "Unexpected error during device auth: ${e.localizedMessage}", e)
            return@withContext Result.failure(
                DeviceAuthenticationException("Device authentication failed unexpectedly.", e)
            )
        }
    }

    /**
     * Refreshes access token using refresh token:
     * POST /api/v1/auth/refresh
     */
    suspend fun refreshToken(): Result<DeviceTokenResponse> = withContext(Dispatchers.IO) {
        val refreshToken = tokenStore.getRefreshToken()
        if (refreshToken.isNullOrBlank()) {
            return@withContext Result.failure(IOException("No refresh token available"))
        }

        val endpointUrl = "$baseUrl/api/v1/auth/refresh"
        val jsonPayload = JSONObject().apply {
            put("refreshToken", refreshToken)
        }

        Log.d(TAG, "Executing token refresh to $endpointUrl")

        val request = Request.Builder()
            .url(endpointUrl)
            .post(jsonPayload.toString().toRequestBody("application/json".toMediaType()))
            .header("Accept", "application/json")
            .build()

        try {
            val response = httpClient.newCall(request).execute()
            response.use { resp ->
                val bodyString = resp.body?.string() ?: ""
                if (!resp.isSuccessful) {
                    val errorDetail = parseErrorDetail(bodyString, resp.code)
                    Log.e(TAG, "Token refresh failed with HTTP ${resp.code}: $errorDetail")
                    return@withContext Result.failure(DeviceAuthHttpException(resp.code, errorDetail))
                }

                val tokenResponse = parseTokenResponse(bodyString)
                tokenStore.saveTokens(
                    accessToken = tokenResponse.accessToken,
                    refreshToken = tokenResponse.refreshToken,
                    userId = tokenResponse.userId,
                    expiresInSeconds = tokenResponse.expiresIn
                )
                Log.d(TAG, "Token refresh successful. New tokens saved.")
                return@withContext Result.success(tokenResponse)
            }
        } catch (e: DeviceAuthException) {
            Log.e(TAG, "Token refresh failed: ${e.message}", e)
            return@withContext Result.failure(e)
        } catch (e: IOException) {
            Log.e(TAG, "Network failure during token refresh: ${e.localizedMessage}", e)
            return@withContext Result.failure(
                DeviceAuthNetworkException(
                    "Unable to reach NutriGuard backend at $baseUrl while refreshing the device session.",
                    e
                )
            )
        } catch (e: Exception) {
            Log.e(TAG, "Error during token refresh: ${e.localizedMessage}", e)
            return@withContext Result.failure(
                DeviceAuthenticationException("Device session refresh failed unexpectedly.", e)
            )
        }
    }

    /**
     * Synchronous version of refresh for use inside OkHttp Interceptor.
     */
    fun refreshTokenSync(): Result<DeviceTokenResponse> {
        val refreshToken = tokenStore.getRefreshToken()
        if (refreshToken.isNullOrBlank()) {
            return Result.failure(IOException("No refresh token available"))
        }

        val endpointUrl = "$baseUrl/api/v1/auth/refresh"
        val jsonPayload = JSONObject().apply {
            put("refreshToken", refreshToken)
        }

        val request = Request.Builder()
            .url(endpointUrl)
            .post(jsonPayload.toString().toRequestBody("application/json".toMediaType()))
            .header("Accept", "application/json")
            .build()

        return try {
            val response = httpClient.newCall(request).execute()
            response.use { resp ->
                val bodyString = resp.body?.string() ?: ""
                if (!resp.isSuccessful) {
                    val errorDetail = parseErrorDetail(bodyString, resp.code)
                    return Result.failure(DeviceAuthHttpException(resp.code, errorDetail))
                }

                val tokenResponse = parseTokenResponse(bodyString)
                tokenStore.saveTokens(
                    accessToken = tokenResponse.accessToken,
                    refreshToken = tokenResponse.refreshToken,
                    userId = tokenResponse.userId,
                    expiresInSeconds = tokenResponse.expiresIn
                )
                Result.success(tokenResponse)
            }
        } catch (e: DeviceAuthException) {
            Result.failure(e)
        } catch (e: IOException) {
            Result.failure(
                DeviceAuthNetworkException(
                    "Unable to reach NutriGuard backend at $baseUrl while refreshing the device session.",
                    e
                )
            )
        } catch (e: Exception) {
            Result.failure(DeviceAuthenticationException("Device session refresh failed unexpectedly.", e))
        }
    }

    /**
     * Synchronous version of device authentication for fallback inside OkHttp Interceptor.
     */
    fun authenticateDeviceSync(appVersion: String = "1.0.0"): Result<DeviceTokenResponse> {
        val deviceId = tokenStore.getDeviceId()
        val endpointUrl = "$baseUrl/api/v1/auth/device"

        val jsonPayload = JSONObject().apply {
            put("deviceId", deviceId)
            put("appVersion", appVersion)
            put("platform", "ANDROID")
        }

        val request = Request.Builder()
            .url(endpointUrl)
            .post(jsonPayload.toString().toRequestBody("application/json".toMediaType()))
            .header("Accept", "application/json")
            .build()

        return try {
            val response = httpClient.newCall(request).execute()
            response.use { resp ->
                val bodyString = resp.body?.string() ?: ""
                if (!resp.isSuccessful) {
                    val errorDetail = parseErrorDetail(bodyString, resp.code)
                    return Result.failure(DeviceAuthHttpException(resp.code, errorDetail))
                }

                val tokenResponse = parseTokenResponse(bodyString)
                tokenStore.saveTokens(
                    accessToken = tokenResponse.accessToken,
                    refreshToken = tokenResponse.refreshToken,
                    userId = tokenResponse.userId,
                    expiresInSeconds = tokenResponse.expiresIn
                )
                Result.success(tokenResponse)
            }
        } catch (e: DeviceAuthException) {
            Result.failure(e)
        } catch (e: IOException) {
            Result.failure(
                DeviceAuthNetworkException(
                    "Unable to reach NutriGuard backend at $baseUrl. Check network connection.",
                    e
                )
            )
        } catch (e: Exception) {
            Result.failure(DeviceAuthenticationException("Device authentication failed unexpectedly.", e))
        }
    }

    private fun parseTokenResponse(responseBody: String): DeviceTokenResponse {
        return parseDeviceTokenResponse(responseBody)
    }

    private fun parseErrorDetail(responseBody: String, httpCode: Int): String {
        return try {
            val json = JSONObject(responseBody)
            val detail = json.optString("detail").takeIf { it.isNotBlank() }
            val errorObject = json.optJSONObject("error")
            val errorMessage = errorObject?.optString("message")?.takeIf { it.isNotBlank() }
            val errorCode = errorObject?.optString("code")?.takeIf { it.isNotBlank() }
            when {
                detail != null -> detail
                errorMessage != null && errorCode != null -> "$errorMessage ($errorCode)"
                errorMessage != null -> errorMessage
                else -> "Authentication error (HTTP $httpCode)"
            }
        } catch (e: Exception) {
            "Authentication failed with HTTP $httpCode"
        }
    }

    companion object {
        private const val TAG = "NutriGuardAuthService"
    }
}

internal fun parseDeviceTokenResponse(responseBody: String): DeviceTokenResponse {
    val json = try {
        JSONObject(responseBody)
    } catch (e: Exception) {
        throw DeviceAuthParseException("Backend auth response is not valid JSON.", e)
    }

    val accessToken = json.optRequiredString("accessToken", "access_token")
    val refreshToken = json.optRequiredString("refreshToken", "refresh_token")
    val tokenType = json.optOptionalString("tokenType", "token_type") ?: "Bearer"
    val expiresIn = json.optOptionalLong("expiresIn", "expires_in") ?: 3600L
    val userId = json.optOptionalString("userId", "user_id")

    return DeviceTokenResponse(
        accessToken = accessToken,
        refreshToken = refreshToken,
        tokenType = tokenType,
        expiresIn = expiresIn,
        userId = userId
    )
}

private fun JSONObject.optRequiredString(vararg keys: String): String {
    return optOptionalString(*keys)
        ?: throw DeviceAuthParseException(
            "Backend auth response is missing required token field: ${keys.joinToString(" / ")}."
        )
}

private fun JSONObject.optOptionalString(vararg keys: String): String? {
    for (key in keys) {
        val value = optString(key).takeIf { it.isNotBlank() }
        if (value != null) {
            return value
        }
    }
    return null
}

private fun JSONObject.optOptionalLong(vararg keys: String): Long? {
    for (key in keys) {
        if (has(key) && !isNull(key)) {
            return optLong(key)
        }
    }
    return null
}
