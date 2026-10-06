package com.example.data.diagnostics

import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant

/** Explicit allowlist; internal fields (including owner binding) cannot leak into the API. */
object ScanEventWire {
    fun event(e: PendingScanEvent): JSONObject {
        val stage = when (e.stage) {
            LocalScanStage.ACQUISITION -> if (e.outcome == LocalScanOutcome.STARTED) "ATTEMPT_START" else "CAPTURE_COMPLETE"
            LocalScanStage.REQUEST -> "UPLOAD_START"
            LocalScanStage.RETRY -> "UPLOAD_RETRY"
            LocalScanStage.RESPONSE -> "RESPONSE_RECEIVED"
            LocalScanStage.RESULT -> when (e.outcome) {
                LocalScanOutcome.SUCCEEDED, LocalScanOutcome.PARTIAL -> "TERMINAL_SUCCESS"
                LocalScanOutcome.CANCELLED -> "TERMINAL_CANCELLED"
                else -> "TERMINAL_FAILURE"
            }
            else -> e.stage.name
        }
        return JSONObject().put("eventId", e.eventId).put("scanAttemptId", e.scanAttemptId.value)
            .put("requestSequence", e.requestSequence ?: JSONObject.NULL).put("sequence", e.sequence)
            .put("occurredAt", Instant.ofEpochMilli(e.occurredAt).toString())
            .put("stage", stage).put("outcome", e.outcome.name)
            .put("durationMs", e.durationMs ?: JSONObject.NULL)
            .put("reasonCode", e.reasonCode?.name ?: JSONObject.NULL).put("appVersion", e.appVersion)
            .put("metrics", JSONObject().apply { e.metrics.forEach { (key, value) -> put(key, value) } })
    }

    fun body(events: List<PendingScanEvent>): String = JSONObject()
        .put("events", JSONArray(events.map(::event))).toString()

    fun batch(pending: List<PendingScanEvent>): List<PendingScanEvent> {
        val result = mutableListOf<PendingScanEvent>()
        for (event in pending.take(20)) {
            if (body(result + event).toByteArray(Charsets.UTF_8).size > 16 * 1024) break
            result.add(event)
        }
        return result
    }

    /** Fail closed on malformed, overlapping, missing or foreign acknowledgments. */
    fun acknowledged(body: String, submitted: Set<String>): Set<String> {
        val json = JSONObject(body)
        fun ids(key: String): List<String> = json.getJSONArray(key).let { a ->
            (0 until a.length()).map { a.getString(it) }
        }
        val accepted = ids("acceptedEventIds")
        val duplicate = ids("duplicateEventIds")
        val retryable = ids("retryableEventIds")
        val all = accepted + duplicate + retryable
        require(all.size == all.toSet().size && all.toSet() == submitted)
        return (accepted + duplicate).toSet()
    }
}
