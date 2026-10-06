package com.example.data.diagnostics

import androidx.lifecycle.SavedStateHandle
import com.example.testutil.FakeProductAnalysisSource
import com.example.testutil.sampleFullProductAnalysis
import com.example.ui.viewmodel.MainViewModel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.*
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.Assert.*
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@RunWith(RobolectricTestRunner::class)
@OptIn(ExperimentalCoroutinesApi::class)
class ScanAttemptIntegrationTest {
    @Test fun cancelledExtraPhotoPreservesExistingPartialResult() = runTest(dispatcher) {
        val source = FakeProductAnalysisSource().apply {
            barcodeResult = Result.failure(com.example.data.remote.LabelScanRequiredException(
                reason = "test partial", suggestedAction = null, providersChecked = emptyList(), discoveredIdentity = null))
        }
        val vm = MainViewModel(source)
        vm.scanBarcode("40144474")
        advanceUntilIdle()
        val partial = vm.barcodeLookupState.value
        assertTrue(vm.beginScanAttempt(ScanInput.LABEL_CAMERA))
        vm.finishAcquisition(true)
        advanceUntilIdle()
        assertEquals(partial, vm.barcodeLookupState.value)
        assertEquals("40144474", vm.pendingBarcode.value)
    }
    @Test fun partialResultIsRecordedAsPartialNotFailure() = runTest(dispatcher) {
        val source = FakeProductAnalysisSource().apply {
            ocrResult = Result.failure(com.example.data.remote.LabelScanRequiredException(
                reason = "test partial", suggestedAction = null, providersChecked = emptyList(), discoveredIdentity = null))
        }
        val vm = MainViewModel(source)
        vm.analyzeOcrText("local test")
        advanceUntilIdle()
        assertEquals(ScanAttemptOutcome.PARTIAL, vm.scanAttempt.value!!.outcome)
        assertEquals("PARTIAL", source.scanAttempts.value.single().outcome)
    }
    private val dispatcher = StandardTestDispatcher()
    @Before fun setup() { Dispatchers.setMain(dispatcher) }
    @After fun cleanup() { Dispatchers.resetMain() }

    @Test fun acquisitionAndSuccessfulSubmissionShareIdButNextScanDoesNot() = runTest(dispatcher) {
        val source = FakeProductAnalysisSource().apply { barcodeResult = Result.success(sampleFullProductAnalysis()) }
        val vm = MainViewModel(source)
        assertTrue(vm.beginScanAttempt(ScanInput.BARCODE_CAMERA))
        val id = vm.scanAttempt.value!!.id
        assertFalse(vm.beginScanAttempt(ScanInput.LABEL_CAMERA))
        vm.scanBarcode("40144474")
        advanceUntilIdle()
        assertEquals(id, vm.resultAttemptId.value)
        assertEquals(ScanAttemptOutcome.SUCCESS, vm.scanAttempt.value!!.outcome)
        assertEquals(id.value, source.scanAttempts.value.single().scanAttemptId)
        vm.scanBarcode("40144474")
        advanceUntilIdle()
        assertNotEquals(id, vm.resultAttemptId.value)
    }

    @Test fun cancelKeepsBarcodeEnrichmentAndDoesNotCallBackend() = runTest(dispatcher) {
        val source = FakeProductAnalysisSource()
        val vm = MainViewModel(source)
        vm.requestLabelCameraForProduct("40144474")
        vm.beginScanAttempt(ScanInput.LABEL_CAMERA)
        val id = vm.scanAttempt.value!!.id
        vm.finishAcquisition(true)
        advanceUntilIdle()
        assertEquals("40144474", vm.pendingBarcode.value)
        assertEquals(id, vm.scanAttempt.value!!.id)
        assertEquals(ScanAttemptOutcome.CANCELLED, vm.scanAttempt.value!!.outcome)
        assertEquals(0, source.analyzeImageLabelCallCount)
        assertEquals("CANCELLED", source.scanAttempts.value.single().outcome)
    }

    @Test fun failedTextKeepsItsOwnId() = runTest(dispatcher) {
        val vm = MainViewModel(FakeProductAnalysisSource())
        vm.analyzeOcrText("test input")
        val id = vm.scanAttempt.value!!.id
        advanceUntilIdle()
        assertEquals(id, vm.scanAttempt.value!!.id)
        assertEquals(ScanAttemptOutcome.FAILED, vm.scanAttempt.value!!.outcome)
        assertNull(vm.resultAttemptId.value)
    }

    @Test fun processRestorationMarksInterruptedWithoutReplayingRequest() = runTest(dispatcher) {
        val source = FakeProductAnalysisSource()
        val state = SavedStateHandle(mapOf("scan.id" to "0000123456789012",
            "scan.input" to "LABEL_CAMERA", "scan.started" to 1L))
        val vm = MainViewModel(source, state)
        advanceUntilIdle()
        assertEquals("0000123456789012", vm.scanAttempt.value!!.id.value)
        assertEquals(ScanAttemptOutcome.INTERRUPTED, vm.scanAttempt.value!!.outcome)
        assertEquals(0, source.analyzeImageLabelCallCount)
    }
}
