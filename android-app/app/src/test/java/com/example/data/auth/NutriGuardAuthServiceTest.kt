package com.example.data.auth

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [36])
class NutriGuardAuthServiceTest {

    @Test
    fun `parseDeviceTokenResponse accepts camelCase backend fields`() {
        val response = """
            {
              "accessToken": "access-token",
              "refreshToken": "refresh-token",
              "tokenType": "Bearer",
              "expiresIn": 3600,
              "userId": "user-123"
            }
        """.trimIndent()

        val parsed = parseDeviceTokenResponse(response)

        assertEquals("access-token", parsed.accessToken)
        assertEquals("refresh-token", parsed.refreshToken)
        assertEquals("Bearer", parsed.tokenType)
        assertEquals(3600L, parsed.expiresIn)
        assertEquals("user-123", parsed.userId)
    }

    @Test
    fun `parseDeviceTokenResponse still accepts snake_case fields`() {
        val response = """
            {
              "access_token": "access-token",
              "refresh_token": "refresh-token",
              "token_type": "Bearer",
              "expires_in": 1800,
              "user_id": "user-456"
            }
        """.trimIndent()

        val parsed = parseDeviceTokenResponse(response)

        assertEquals("access-token", parsed.accessToken)
        assertEquals("refresh-token", parsed.refreshToken)
        assertEquals("Bearer", parsed.tokenType)
        assertEquals(1800L, parsed.expiresIn)
        assertEquals("user-456", parsed.userId)
    }

    @Test
    fun `parseDeviceTokenResponse throws parse exception when access token missing`() {
        val response = """
            {
              "refreshToken": "refresh-token",
              "expiresIn": 3600
            }
        """.trimIndent()

        val error = runCatching { parseDeviceTokenResponse(response) }.exceptionOrNull()

        assertTrue(error is DeviceAuthParseException)
        assertEquals(
            "Backend auth response is missing required token field: accessToken / access_token.",
            error?.message
        )
    }
}
