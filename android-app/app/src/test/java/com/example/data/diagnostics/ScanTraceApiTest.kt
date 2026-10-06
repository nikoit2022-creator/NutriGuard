package com.example.data.diagnostics

import com.example.data.remote.NutriGuardApiService
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.Dispatchers
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@RunWith(RobolectricTestRunner::class)
class ScanTraceApiTest {
    @Test fun concurrentRequestsKeepTheirOwnCorrelation() = runBlocking {
        val seen = java.util.Collections.synchronizedList(mutableListOf<Pair<String?, String?>>())
        val api = NutriGuardApiService(OkHttpClient.Builder().addInterceptor { chain ->
            val id = chain.request().header("X-Scan-Attempt-Id")!!
            seen.add(id to chain.request().header("X-Scan-Request-Sequence"))
            Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1).code(500).message("test")
                .header("X-Scan-Attempt-Id", id).body("{}".toResponseBody()).build()
        }.build())
        val ids = listOf("0000123456789012", "1111222233334444")
        ids.map { id -> async(Dispatchers.IO) {
            val trace = ScanTraceContext(ScanAttemptId(id))
            withContext(trace) { runCatching { api.scanOcrText("local test") } }
            assertEquals(id, trace.serverAttemptId?.value)
        } }.awaitAll()
        assertEquals(ids.map { it to "1" }.toSet(), seen.toSet())
    }
    @Test fun authRetryKeepsIdIncrementsSequenceAndRecordsRetry() = runBlocking {
        val tokens = com.example.data.auth.SharedPreferencesAuthTokenStore(org.robolectric.RuntimeEnvironment.getApplication())
        tokens.saveTokens("old-test-token", "test-refresh", "test-owner")
        val authHttp = OkHttpClient.Builder().addInterceptor { chain ->
            Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1).code(200).message("OK")
                .body("""{"accessToken":"new-test-token","refreshToken":"test-refresh","userId":"test-owner","expiresIn":3600}""".toResponseBody()).build()
        }.build()
        val auth = com.example.data.auth.NutriGuardAuthService(authHttp, tokens)
        val requests = mutableListOf<Pair<String?, String?>>()
        val events = mutableListOf<Pair<LocalScanStage, LocalScanOutcome>>()
        val trace = ScanTraceContext(ScanAttemptId("0000123456789012")) { s, o -> events.add(s to o) }
        val client = OkHttpClient.Builder()
            .addInterceptor(com.example.data.auth.AuthInterceptor(tokens) { auth })
            .addInterceptor { chain ->
                requests.add(chain.request().header("X-Scan-Attempt-Id") to chain.request().header("X-Scan-Request-Sequence"))
                Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1)
                    .code(if (requests.size == 1) 401 else 500).message("test").body("{}".toResponseBody()).build()
            }.build()
        withContext(trace) { runCatching { NutriGuardApiService(client).scanBarcode("40144474") } }
        assertEquals(listOf(trace.id.value to "1", trace.id.value to "2"), requests)
        assertTrue(events.contains(LocalScanStage.RETRY to LocalScanOutcome.RETRIED))
        assertEquals(2, trace.requestSequence)
    }

    @Test fun failedRefreshLeavesReadable401ResponseAndNoSecondScanRequest() = runBlocking {
        val tokens = com.example.data.auth.SharedPreferencesAuthTokenStore(org.robolectric.RuntimeEnvironment.getApplication())
        tokens.saveTokens("old-test-token", "test-refresh", "test-owner")
        val authHttp = OkHttpClient.Builder().addInterceptor { chain ->
            Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1).code(401).message("No")
                .body("{}".toResponseBody()).build()
        }.build()
        val auth = com.example.data.auth.NutriGuardAuthService(authHttp, tokens)
        var requests = 0
        val client = OkHttpClient.Builder().addInterceptor(com.example.data.auth.AuthInterceptor(tokens) { auth })
            .addInterceptor { chain ->
                requests++
                Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1).code(401).message("No")
                    .body("{}".toResponseBody()).build()
            }.build()
        val trace = ScanTraceContext(ScanAttemptId("0000123456789012"))
        val result = withContext(trace) { runCatching { NutriGuardApiService(client).scanOcrText("local test") } }
        assertTrue(result.exceptionOrNull() is com.example.data.remote.BarcodeAuthException)
        assertEquals(1, requests)
    }
    @Test fun failureResponseStillCarriesAttemptAndAcceptsDifferentValidEcho() = runBlocking {
        val trace = ScanTraceContext(ScanAttemptId("0000123456789012"))
        val api = NutriGuardApiService(OkHttpClient.Builder().addInterceptor { chain ->
            assertEquals(trace.id.value, chain.request().header("X-Scan-Attempt-Id"))
            assertEquals("1", chain.request().header("X-Scan-Request-Sequence"))
            Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1)
                .code(500).message("Test failure").header("X-Scan-Attempt-Id", "1111222233334444")
                .body("{}".toResponseBody()).build()
        }.build())
        val result = withContext(trace) { runCatching { api.scanBarcode("40144474") } }
        assertTrue(result.isFailure)
        assertEquals("1111222233334444", trace.serverAttemptId?.value)
        assertEquals("0000123456789012", trace.id.value)
    }

    @Test fun independentAttemptsAndLegacyResponsesDoNotLeakCorrelation() = runBlocking {
        val observed = mutableListOf<String?>()
        val api = NutriGuardApiService(OkHttpClient.Builder().addInterceptor { chain ->
            observed.add(chain.request().header("X-Scan-Attempt-Id"))
            Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1)
                .code(500).message("Test failure").body("{}".toResponseBody()).build()
        }.build())
        for (id in listOf("0000123456789012", "1111222233334444")) {
            val trace = ScanTraceContext(ScanAttemptId(id))
            withContext(trace) { runCatching { api.scanOcrText("local test") } }
            assertNull(trace.serverAttemptId)
        }
        runCatching { api.scanBarcode("40144474") }
        assertEquals(listOf("0000123456789012", "1111222233334444", null), observed)
    }
}
