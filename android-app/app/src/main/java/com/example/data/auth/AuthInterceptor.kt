package com.example.data.auth

import android.util.Log
import okhttp3.Interceptor
import okhttp3.Response

/**
 * OkHttp Interceptor that automatically attaches the JWT Bearer token
 * to outgoing HTTP requests if a valid access token exists.
 *
 * If a 401 Unauthorized occurs on a protected endpoint, it transparently
 * attempts token refresh (or device re-authentication) and retries the
 * request at most once.
 */
class AuthInterceptor(
    private val tokenStore: AuthTokenStore,
    private val authServiceProvider: () -> NutriGuardAuthService
) : Interceptor {

    override fun intercept(chain: Interceptor.Chain): Response {
        val originalRequest = chain.request()
        val requestUrl = originalRequest.url.toString()

        // Skip adding Authorization or retrying 401 on the auth endpoints themselves
        if (requestUrl.contains("/api/v1/auth/device") || requestUrl.contains("/api/v1/auth/refresh")) {
            return chain.proceed(originalRequest)
        }

        val token = tokenStore.getAccessToken()
        val authenticatedRequest = if (!token.isNullOrBlank()) {
            originalRequest.newBuilder()
                .header("Authorization", "Bearer $token")
                .build()
        } else {
            originalRequest
        }

        val response = chain.proceed(authenticatedRequest)

        // If 401 occurs and this request has not already been retried
        if (response.code == 401 && originalRequest.header(HEADER_RETRY) == null) {
            Log.w(TAG, "Received 401 Unauthorized on $requestUrl. Attempting token refresh/re-auth...")
            originalRequest.tag(com.example.data.diagnostics.ScanTraceContext::class.java)?.let {
                it.reason = com.example.data.diagnostics.ScanReason.AUTH_EXPIRED
                it.record(com.example.data.diagnostics.LocalScanStage.RESPONSE, com.example.data.diagnostics.LocalScanOutcome.FAILED)
            }

            val newAccessToken = synchronized(this) {
                val authService = authServiceProvider()
                // Try refresh token first
                val refreshResult = authService.refreshTokenSync()
                if (refreshResult.isSuccess) {
                    refreshResult.getOrNull()?.accessToken
                } else {
                    // Fall back to device re-authentication
                    val authResult = authService.authenticateDeviceSync()
                    if (authResult.isSuccess) {
                        authResult.getOrNull()?.accessToken
                    } else {
                        tokenStore.clearTokens()
                        null
                    }
                }
            }

            if (!newAccessToken.isNullOrBlank()) {
                response.close()
                val retryRequest = originalRequest.newBuilder()
                    .header("Authorization", "Bearer $newAccessToken")
                    .header(HEADER_RETRY, "1")
                    .apply {
                        originalRequest.tag(com.example.data.diagnostics.ScanTraceContext::class.java)?.let {
                            val sequence = it.nextRequest()
                            header("X-Scan-Request-Sequence", sequence.toString())
                            it.recordMetrics(
                                com.example.data.diagnostics.LocalScanStage.RETRY,
                                com.example.data.diagnostics.LocalScanOutcome.RETRIED,
                                mapOf("retryCount" to (sequence - 1).toDouble())
                            )
                        }
                    }
                    .build()
                return chain.proceed(retryRequest)
            }
        }

        return response
    }

    companion object {
        private const val TAG = "AuthInterceptor"
        private const val HEADER_RETRY = "X-NutriGuard-Retried"
    }
}
