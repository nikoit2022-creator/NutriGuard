package com.example.data.diagnostics

import java.security.SecureRandom
import org.junit.Assert.*
import org.junit.Test

class ScanAttemptTest {
    @Test fun requestSequenceStaysWithinBackendNineDigitLimit() {
        val attempt = ScanAttempt(ScanAttemptId("0000123456789012"), ScanInput.TEXT, 1L,
            requestSequence = 999_999_998)
        assertEquals(999_999_999, attempt.nextRequest().requestSequence)
        assertThrows(IllegalStateException::class.java) { attempt.nextRequest().nextRequest() }
        assertThrows(IllegalArgumentException::class.java) {
            attempt.copy(requestSequence = 1_000_000_000)
        }
    }
    @Test fun preservesLeadingZerosAndFormatsWithoutChangingCopyValue() {
        val id = ScanAttemptId("0000123456789012")
        assertEquals("0000 1234 5678 9012", id.display)
        assertEquals("0000123456789012", id.value)
    }

    @Test fun rejectsMalformedAndNonAsciiIds() {
        listOf("", "123", "12345678901234567", "0000 1234 5678 9012",
            "abcdefghijklmnop", "１２３４５６７８９０１２３４５６").forEach {
            assertThrows(IllegalArgumentException::class.java) { ScanAttemptId(it) }
        }
    }

    @Test fun generatorAvoidsRetainedCollision() {
        var draws = 0
        val generator = ScanAttemptIdGenerator(object : SecureRandom() {
            override fun nextInt(bound: Int): Int = if (draws++ < 16) 0 else 1
        })
        assertEquals("1111111111111111", generator.next(setOf("0000000000000000")).value)
    }

    @Test fun pathologicalRandomSourceCannotLoopForever() {
        val generator = ScanAttemptIdGenerator(object : SecureRandom() {
            override fun nextInt(bound: Int) = 0
        })
        assertThrows(IllegalStateException::class.java) {
            generator.next(setOf("0000000000000000"))
        }
    }

    @Test fun automaticRetryKeepsIdentityAndIncrementsRequest() {
        val original = ScanAttempt(ScanAttemptId("0000123456789012"), ScanInput.TEXT, 1L)
        val retry = original.nextRequest().nextRequest()
        assertEquals(original.id, retry.id)
        assertEquals(2, retry.requestSequence)
        assertEquals(0, original.requestSequence)
    }

    @Test fun terminalOutcomeCannotBeOverwrittenOrRetried() {
        ScanAttemptOutcome.entries.forEach { outcome ->
            val done = ScanAttempt(ScanAttemptId("0000123456789012"), ScanInput.LABEL_CAMERA, 1L)
                .finish(outcome)
            assertEquals(outcome, done.outcome)
            assertThrows(IllegalStateException::class.java) { done.nextRequest() }
            assertThrows(IllegalStateException::class.java) { done.finish(ScanAttemptOutcome.SUCCESS) }
        }
    }
}
