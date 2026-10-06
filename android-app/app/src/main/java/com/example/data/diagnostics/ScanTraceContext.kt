package com.example.data.diagnostics

import kotlin.coroutines.AbstractCoroutineContextElement
import kotlin.coroutines.CoroutineContext
import java.util.concurrent.atomic.AtomicInteger

/** Per-coroutine, never a process-global current scan. Shared only by this attempt's retries. */
class ScanTraceContext(
    val id: ScanAttemptId,
    val record: (LocalScanStage, LocalScanOutcome) -> Unit = { _, _ -> }
) : AbstractCoroutineContextElement(Key) {
    companion object Key : CoroutineContext.Key<ScanTraceContext>
    private val requests = AtomicInteger(0)
    @Volatile var recordMetrics: (LocalScanStage, LocalScanOutcome, Map<String, Double>) -> Unit = { stage, outcome, _ ->
        record(stage, outcome)
    }
    @Volatile var serverAttemptId: ScanAttemptId? = null
    @Volatile var reason: ScanReason? = null
    val requestSequence: Int get() = requests.get()
    fun nextRequest(): Int = requests.updateAndGet { check(it < 999_999_999); it + 1 }
}
