package com.example.data.diagnostics

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@RunWith(RobolectricTestRunner::class)
class ScanEventWireTest {
    private fun event(outcome: LocalScanOutcome = LocalScanOutcome.PARTIAL) = PendingScanEvent(
        scanAttemptId = ScanAttemptId("0000123456789012"), occurredAt = 0,
        sequence = 1, stage = LocalScanStage.RESULT, outcome = outcome, ownerKey = "private-owner")

    @Test fun preservesPartialAndDoesNotSerializeLocalOwner() {
        val json = ScanEventWire.event(event())
        assertEquals("PARTIAL", json.getString("outcome"))
        assertEquals("TERMINAL_SUCCESS", json.getString("stage"))
        assertEquals("0000123456789012", json.getString("scanAttemptId"))
        assertEquals("1970-01-01T00:00:00Z", json.getString("occurredAt"))
        assertFalse(json.has("ownerKey"))
    }

    @Test fun interruptedIsNotUserCancellation() {
        val json = ScanEventWire.event(event(LocalScanOutcome.INTERRUPTED))
        assertEquals("INTERRUPTED", json.getString("outcome"))
        assertEquals("TERMINAL_FAILURE", json.getString("stage"))
    }

    @Test fun serializesOnlyAllowlistedImageMetrics() {
        val imageEvent = event().copy(metrics = mapOf(
            "imageWidthPx" to 1920.0,
            "imageHeightPx" to 1080.0,
            "imageBytes" to 350_000.0,
            "uploadBytes" to 350_000.0
        ))
        val json = ScanEventWire.event(imageEvent)
        assertEquals(1920, json.getJSONObject("metrics").getInt("imageWidthPx"))
        assertEquals(350_000, json.getJSONObject("metrics").getInt("uploadBytes"))
        assertFalse(json.has("ownerKey"))
        assertTrue(runCatching { imageEvent.copy(metrics = mapOf("barcode" to 123.0)) }.isFailure)
    }

    @Test fun batchHonorsCountAndByteBounds() {
        val batch = ScanEventWire.batch(List(100) { event() })
        assertEquals(20, batch.size)
        assertTrue(ScanEventWire.body(batch).toByteArray().size <= 16384)
    }

    @Test fun batchRemainsWithinByteBoundWithMaximumMetrics() {
        val maxMetrics = mapOf(
            "imageWidthPx" to 20000.0, "imageHeightPx" to 20000.0,
            "imageBytes" to 104857600.0, "uploadBytes" to 104857600.0,
            "retryCount" to 1000.0, "ocrConfidencePct" to 100.0,
            "outboxDepth" to 10000.0, "queuedMs" to 86400000.0
        )
        val batch = ScanEventWire.batch(List(20) { event().copy(metrics = maxMetrics) })
        assertEquals(20, batch.size)
        assertTrue(ScanEventWire.body(batch).toByteArray().size <= 16384)
    }

    @Test fun retryableEventsAreNeverAcknowledged() {
        val json = JSONObject().put("acceptedEventIds", JSONArray(listOf("a")))
            .put("duplicateEventIds", JSONArray(listOf("b")))
            .put("retryableEventIds", JSONArray(listOf("c")))
        assertEquals(setOf("a", "b"), ScanEventWire.acknowledged(json.toString(), setOf("a", "b", "c")))
    }

    @Test fun malformedAcknowledgmentsCannotLoseEvents() {
        listOf(
            "{}",
            "{\"acceptedEventIds\":[\"a\"],\"duplicateEventIds\":[\"a\"],\"retryableEventIds\":[]}",
            "{\"acceptedEventIds\":[\"other\"],\"duplicateEventIds\":[],\"retryableEventIds\":[]}",
            "{\"acceptedEventIds\":[],\"duplicateEventIds\":[],\"retryableEventIds\":[]}"
        ).forEach { raw -> assertTrue(runCatching { ScanEventWire.acknowledged(raw, setOf("a")) }.isFailure) }
    }
}
