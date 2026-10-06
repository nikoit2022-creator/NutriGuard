package com.example.data.diagnostics

import android.util.AtomicFile
import java.io.File
import java.util.UUID
import org.json.JSONArray
import org.json.JSONObject

/** Local vocabulary only. Translate to the reviewed #30 wire enums in a separate adapter. */
enum class LocalScanStage { ACQUISITION, IMAGE_PREPARATION, REQUEST, RETRY, RESPONSE, PARSING, PERSISTENCE, RESULT }
enum class LocalScanOutcome { STARTED, SUCCEEDED, PARTIAL, FAILED, CANCELLED, INTERRUPTED, RETRIED }
enum class ScanReason { NETWORK_TIMEOUT, NETWORK_OFFLINE, AUTH_EXPIRED, AUTH_RETRY, SERVER_ERROR, RATE_LIMITED, VALIDATION_REJECTED, USER_CANCELLED, DECODE_ERROR, UNKNOWN }

/** Intentionally no free-form messages, raw text, barcode, URI or exception fields. */
data class PendingScanEvent(
    val eventId: String = UUID.randomUUID().toString(),
    val scanAttemptId: ScanAttemptId,
    val occurredAt: Long,
    val sequence: Int,
    val requestSequence: Int? = null,
    val stage: LocalScanStage,
    val outcome: LocalScanOutcome,
    val durationMs: Long? = null,
    val reasonCode: ScanReason? = null,
    val appVersion: String = "unknown",
    val metrics: Map<String, Double> = emptyMap(),
    // Local-only owner binding. Never sent as client metadata.
    val ownerKey: String? = null
) {
    init {
        require(UUID.fromString(eventId).toString() == eventId)
        require(sequence in 0..100_000 && (requestSequence == null || requestSequence in 1..999_999_999))
        require(durationMs == null || durationMs in 0..86_400_000)
        require(appVersion.matches(Regex("[A-Za-z0-9_.+-]{1,32}")))
        require(metrics.size <= 8)
        metrics.forEach { (key, value) ->
            val bounds = METRIC_BOUNDS[key] ?: error("Unsupported diagnostic metric: $key")
            require(value.isFinite() && value >= bounds.first && value <= bounds.second)
        }
    }

    companion object {
        private val METRIC_BOUNDS = mapOf(
            "imageWidthPx" to (0.0 to 20_000.0),
            "imageHeightPx" to (0.0 to 20_000.0),
            "imageBytes" to (0.0 to 104_857_600.0),
            "uploadBytes" to (0.0 to 104_857_600.0),
            "retryCount" to (0.0 to 1_000.0),
            "ocrConfidencePct" to (0.0 to 100.0),
            "outboxDepth" to (0.0 to 10_000.0),
            "queuedMs" to (0.0 to 86_400_000.0)
        )
    }
}

/**
 * App-private, single-process outbox. Use one application-owned instance. Never put it on
 * external storage. Delivery/acknowledgment is separate; inspection does not discard events.
 * A failed append returns false, never an exception to the scan. No upload is implemented here.
 */
class ScanDiagnosticOutbox(
    private val file: File,
    private val now: () -> Long = System::currentTimeMillis,
    private val maxBytes: Int = 128 * 1024,
    private val maxEvents: Int = 500,
    private val maxAgeMs: Long = 7L * 24 * 60 * 60 * 1000
) {
    init { require(maxBytes >= 256 && maxEvents > 0 && maxAgeMs > 0) }
    private val atomic = AtomicFile(file)
    // AtomicFile may retain a backup during writes: payload cap 128 KiB, disk budget ~256 KiB.
    private data class Snapshot(val events: List<PendingScanEvent>, val dropped: Long)

    @Synchronized fun append(event: PendingScanEvent): Boolean = safely {
        val old = read()
        val currentTime = now()
        val retained = old.events.filter { it.occurredAt >= currentTime - maxAgeMs }
        val events = retained.toMutableList()
        if (events.none { it.eventId == event.eventId }) events.add(event)
        var dropped = old.dropped + old.events.size - retained.size
        while (events.size > maxEvents || encode(Snapshot(events, dropped)).size > maxBytes) {
            if (events.isEmpty()) return@safely false
            events.removeAt(0)
            dropped++
        }
        write(Snapshot(events, dropped))
        true
    }

    @Synchronized fun pending(): List<PendingScanEvent> = try {
        val cutoff = now() - maxAgeMs
        read().events.filter { it.occurredAt >= cutoff }
    } catch (_: Exception) { emptyList() }

    @Synchronized fun droppedCount(): Long? = try { read().dropped } catch (_: Exception) { null }

    @Synchronized fun maintain(additionalDrops: Long = 0): Boolean = safely {
        val old = read()
        val retained = old.events.filter { it.occurredAt >= now() - maxAgeMs }
        write(Snapshot(retained, old.dropped + old.events.size - retained.size + additionalDrops.coerceAtLeast(0)))
        true
    }

    /** Only pass IDs explicitly acknowledged by the server; never clear the whole queue. */
    @Synchronized fun acknowledge(ids: Set<String>): Boolean = safely {
        val old = read()
        write(old.copy(events = old.events.filterNot { it.eventId in ids }))
        true
    }

    private fun read(): Snapshot {
        if (!file.exists() && !File(file.path + ".bak").exists()) return Snapshot(emptyList(), 0)
        val bytes = atomic.openRead().use { input ->
            val result = java.io.ByteArrayOutputStream()
            val buffer = ByteArray(4096)
            while (true) {
                val count = input.read(buffer)
                if (count < 0) break
                check(result.size() + count <= maxBytes) { "Outbox exceeds limit" }
                result.write(buffer, 0, count)
            }
            result.toByteArray()
        }
        val root = JSONObject(bytes.toString(Charsets.UTF_8))
        check(root.getInt("version") == 1)
        val array = root.getJSONArray("events")
        check(array.length() <= maxEvents)
        val events = (0 until array.length()).map { index ->
            val e = array.getJSONObject(index)
            PendingScanEvent(
                eventId = e.getString("eventId"),
                scanAttemptId = ScanAttemptId(e.getString("scanAttemptId")),
                occurredAt = e.getLong("occurredAt"), sequence = e.getInt("sequence"),
                requestSequence = if (e.isNull("requestSequence")) null else e.getInt("requestSequence"),
                stage = LocalScanStage.valueOf(e.getString("stage")),
                outcome = LocalScanOutcome.valueOf(e.getString("outcome")),
                durationMs = if (e.isNull("durationMs")) null else e.getLong("durationMs"),
                reasonCode = if (e.isNull("reasonCode")) null else ScanReason.valueOf(e.getString("reasonCode")),
                appVersion = e.optString("appVersion", "unknown"),
                metrics = e.optJSONObject("metrics")?.let { json ->
                    json.keys().asSequence().associateWith { key -> json.getDouble(key) }
                } ?: emptyMap(),
                ownerKey = if (e.isNull("ownerKey")) null else e.getString("ownerKey")
            )
        }
        return Snapshot(events, root.getLong("dropped").also { check(it >= 0) })
    }

    private fun encode(snapshot: Snapshot): ByteArray {
        val events = JSONArray()
        snapshot.events.forEach { e ->
            events.put(JSONObject().put("eventId", e.eventId).put("scanAttemptId", e.scanAttemptId.value)
                .put("occurredAt", e.occurredAt).put("sequence", e.sequence)
                .put("requestSequence", e.requestSequence ?: JSONObject.NULL)
                .put("stage", e.stage.name).put("outcome", e.outcome.name)
                .put("durationMs", e.durationMs ?: JSONObject.NULL)
                .put("reasonCode", e.reasonCode?.name ?: JSONObject.NULL)
                .put("metrics", JSONObject().apply { e.metrics.forEach { (key, value) -> put(key, value) } })
                .put("appVersion", e.appVersion).put("ownerKey", e.ownerKey ?: JSONObject.NULL))
        }
        return JSONObject().put("version", 1).put("dropped", snapshot.dropped)
            .put("events", events).toString().toByteArray(Charsets.UTF_8)
    }

    private fun write(snapshot: Snapshot) {
        val bytes = encode(snapshot)
        check(bytes.size <= maxBytes)
        val stream = atomic.startWrite()
        try {
            stream.write(bytes)
            atomic.finishWrite(stream)
        } catch (e: Exception) {
            atomic.failWrite(stream)
            throw e
        }
    }

    private inline fun safely(block: () -> Boolean): Boolean = try { block() } catch (_: Exception) { false }
}
