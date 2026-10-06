package com.example.data.diagnostics

import java.security.SecureRandom

/** Display correlation only: never a product ID, authentication token or idempotency key. */
data class ScanAttemptId(val value: String) {
    init {
        require(value.length == 16 && value.all { it in '0'..'9' })
    }

    val display: String get() = value.chunked(4).joinToString(" ")
}

class ScanAttemptIdGenerator(private val random: SecureRandom = SecureRandom()) {
    /** Retained IDs must include persisted attempts/outbox entries when wired into the app. */
    fun next(retainedIds: Set<String> = emptySet()): ScanAttemptId {
        repeat(32) {
            val value = buildString(16) { repeat(16) { append(random.nextInt(10)) } }
            if (value !in retainedIds) return ScanAttemptId(value)
        }
        error("Unable to allocate scan attempt ID")
    }
}

enum class ScanInput { BARCODE_CAMERA, BARCODE_MANUAL, LABEL_CAMERA, LABEL_GALLERY, TEXT }
enum class ScanAttemptOutcome { SUCCESS, PARTIAL, FAILED, CANCELLED, INTERRUPTED }

/** Immutable state; save/restore this per attempt, not on the product row. */
data class ScanAttempt(
    val id: ScanAttemptId,
    val input: ScanInput,
    val startedAt: Long,
    val requestSequence: Int = 0,
    val outcome: ScanAttemptOutcome? = null,
    val serverId: ScanAttemptId? = null
) {
    init { require(requestSequence in 0..999_999_999) }

    fun nextRequest(): ScanAttempt {
        check(outcome == null) { "Attempt is already terminal" }
        check(requestSequence < 999_999_999) { "Scan request sequence exhausted" }
        return copy(requestSequence = requestSequence + 1)
    }

    fun finish(result: ScanAttemptOutcome): ScanAttempt {
        check(outcome == null) { "Attempt is already terminal" }
        return copy(outcome = result)
    }
}
