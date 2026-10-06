package com.example.data.diagnostics

import com.example.data.auth.SharedPreferencesAuthTokenStore
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import java.io.File
import java.io.IOException

@RunWith(RobolectricTestRunner::class)
class ScanDeliveryTest {
    @get:Rule val temp = TemporaryFolder()
    private fun event(owner: String?) = PendingScanEvent(scanAttemptId = ScanAttemptId("0000123456789012"),
        occurredAt = System.currentTimeMillis(), sequence = 1, stage = LocalScanStage.RESULT,
        outcome = LocalScanOutcome.FAILED, ownerKey = owner)

    @Test fun acknowledgesOnlyDurableEventsAndReplaysStableIds() {
        val context = RuntimeEnvironment.getApplication()
        val tokens = SharedPreferencesAuthTokenStore(context).apply { saveTokens("test", "test-refresh", "owner-a") }
        val box = ScanDiagnosticOutbox(File(temp.root, "q.json"))
        var calls = 0
        val seen = mutableListOf<List<String>>()
        val client = OkHttpClient.Builder().addInterceptor { chain ->
            assertNull(chain.request().header("X-Scan-Attempt-Id"))
            assertEquals("/api/v1/scan-diagnostics/client-events", chain.request().url.encodedPath)
            val buffer = okio.Buffer(); chain.request().body!!.writeTo(buffer)
            val array = JSONObject(buffer.readUtf8()).getJSONArray("events")
            val ids = (0 until array.length()).map { array.getJSONObject(it).getString("eventId") }
            seen.add(ids)
            val response = JSONObject().put("acceptedEventIds", JSONArray(if (calls++ == 0) ids.take(1) else ids))
                .put("duplicateEventIds", JSONArray()).put("retryableEventIds", JSONArray(if (calls == 1) ids.drop(1) else emptyList<String>()))
            Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1).code(200).message("OK")
                .body(response.toString().toResponseBody()).build()
        }.build()
        val delivery = ScanDiagnostics(context, tokens, box, client, startDelivery = false)
        val a = event(delivery.ownerKey()); val b = event(delivery.ownerKey())
        box.append(a); box.append(b)
        assertFalse(delivery.deliver())
        assertEquals(listOf(b.eventId), box.pending().map { it.eventId })
        assertTrue(delivery.deliver())
        assertEquals(listOf(a.eventId, b.eventId), seen.first())
        assertEquals(listOf(b.eventId), seen.last())
        assertTrue(box.pending().isEmpty())
    }

    @Test fun ownerChangeNeverUploadsPreviousOwnersEvents() {
        val context = RuntimeEnvironment.getApplication()
        val tokens = SharedPreferencesAuthTokenStore(context).apply { saveTokens("test", "refresh", "owner-a") }
        val box = ScanDiagnosticOutbox(File(temp.root, "q.json"))
        val client = OkHttpClient.Builder().addInterceptor { error("Must not upload another owner's events") }.build()
        val delivery = ScanDiagnostics(context, tokens, box, client, startDelivery = false)
        box.append(event(delivery.ownerKey()))
        tokens.saveTokens("second", "refresh", "owner-b")
        assertTrue(delivery.deliver())
        assertEquals(1, box.pending().size)
    }

    @Test fun offlineOldBackendAndInvalidAcknowledgmentKeepQueue() {
        val context = RuntimeEnvironment.getApplication()
        val tokens = SharedPreferencesAuthTokenStore(context).apply { saveTokens("test", "refresh", "owner-a") }
        val box = ScanDiagnosticOutbox(File(temp.root, "q.json"))
        var code = 404
        val client = OkHttpClient.Builder().addInterceptor { chain ->
            if (code == 0) throw IOException("simulated offline")
            Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1).code(code).message("test")
                .body("{}".toResponseBody()).build()
        }.build()
        val delivery = ScanDiagnostics(context, tokens, box, client, startDelivery = false)
        box.append(event(delivery.ownerKey()))
        for (status in listOf(0, 401, 404, 429, 500, 200)) {
            code = status
            assertFalse(delivery.deliver())
            assertEquals(1, box.pending().size)
        }
    }
}
