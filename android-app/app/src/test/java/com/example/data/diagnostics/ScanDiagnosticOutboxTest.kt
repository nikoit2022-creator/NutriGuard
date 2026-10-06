package com.example.data.diagnostics

import java.io.File
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@RunWith(RobolectricTestRunner::class)
class ScanDiagnosticOutboxTest {
    @get:Rule val temp = TemporaryFolder()
    private fun event(sequence: Int = 1, time: Long = 1000L) = PendingScanEvent(
        scanAttemptId = ScanAttemptId("0000123456789012"), occurredAt = time,
        sequence = sequence, stage = LocalScanStage.ACQUISITION, outcome = LocalScanOutcome.STARTED
    )

    @Test fun survivesReopenAndAcknowledgesOnlySpecifiedIds() {
        val file = File(temp.root, "outbox.json")
        val first = event()
        val second = event(2)
        val box = ScanDiagnosticOutbox(file, now = { 1000L })
        assertTrue(box.append(first))
        assertTrue(box.append(second))
        val reopened = ScanDiagnosticOutbox(file, now = { 1000L })
        assertEquals(listOf(first, second), reopened.pending())
        assertTrue(reopened.acknowledge(setOf(first.eventId)))
        assertEquals(listOf(second), reopened.pending())
    }

    @Test fun retryDoesNotDuplicateEvent() {
        val box = ScanDiagnosticOutbox(File(temp.root, "outbox.json"), now = { 1000L })
        val e = event()
        assertTrue(box.append(e))
        assertTrue(box.append(e))
        assertEquals(listOf(e), box.pending())
    }

    @Test fun oldestEventsEvictedAndCounted() {
        val box = ScanDiagnosticOutbox(File(temp.root, "outbox.json"), now = { 1000L }, maxEvents = 2)
        val events = (1..3).map { event(it) }
        events.forEach { assertTrue(box.append(it)) }
        assertEquals(events.drop(1), box.pending())
        assertEquals(1L, box.droppedCount())
    }

    @Test fun expirationDoesNotUploadOldEventsAndIsCountedOnAppend() {
        var clock = 1000L
        val box = ScanDiagnosticOutbox(File(temp.root, "outbox.json"), now = { clock }, maxAgeMs = 100)
        assertTrue(box.append(event()))
        clock = 1101L
        assertTrue(box.pending().isEmpty())
        assertTrue(box.append(event(2, clock)))
        assertEquals(1L, box.droppedCount())
    }

    @Test fun serializedPayloadNeverExceedsBudget() {
        val file = File(temp.root, "outbox.json")
        val box = ScanDiagnosticOutbox(file, now = { 1000L }, maxBytes = 800)
        repeat(20) { assertTrue(box.append(event(it + 1))) }
        assertTrue(file.length() <= 800)
        assertTrue(box.pending().isNotEmpty())
        assertTrue(box.droppedCount()!! > 0)
    }

    @Test fun corruptFileIsNotSilentlyOverwritten() {
        val file = temp.newFile("outbox.json")
        file.writeText("broken")
        val box = ScanDiagnosticOutbox(file)
        assertFalse(box.append(event()))
        assertFalse(box.acknowledge(emptySet()))
        assertNull(box.droppedCount())
        assertEquals("broken", file.readText())
    }

    @Test fun persistsAllowlistedMetricsAcrossOutboxReload() {
        val file = File(temp.root, "metrics-outbox.json")
        val withMetrics = event(sequence = 4).copy(metrics = mapOf("uploadBytes" to 12345.0, "retryCount" to 1.0))
        assertTrue(ScanDiagnosticOutbox(file, now = { 1000L }).append(withMetrics))
        val restored = ScanDiagnosticOutbox(file, now = { 1000L }).pending().single()
        assertEquals(withMetrics.metrics, restored.metrics)
    }
}
