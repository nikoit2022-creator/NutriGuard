package com.example.ui.viewmodel

import com.example.data.remote.BarcodeNetworkException
import com.example.data.remote.BarcodeServerException
import com.example.data.remote.BarcodeTimeoutException
import com.example.data.remote.DiscoveredIdentity
import com.example.data.remote.LabelScanRequiredException
import com.example.testutil.FakeProductAnalysisSource
import com.example.testutil.sampleFullProductAnalysis
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Covers the required "barcode loading state" / "structured error
 * parsing" / "label-scan-required flow" / "distinct failure states"
 * behavior in `MainViewModel.scanBarcode`, entirely against
 * [FakeProductAnalysisSource] — no Room, no real
 * [com.example.data.remote.NutriGuardApiService], no network.
 *
 * `Dispatchers.setMain(testDispatcher)` and `runTest(testDispatcher)`
 * deliberately share the SAME [StandardTestDispatcher] instance (and
 * therefore the same virtual-time scheduler) in every test — otherwise
 * `viewModelScope.launch` (which runs on `Dispatchers.Main`) and this
 * test's `advanceUntilIdle()` would be driving two unrelated
 * schedulers and the coroutine would never actually be observed to
 * complete.
 */
@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [36])
class MainViewModelBarcodeTest {

    private val testDispatcher = StandardTestDispatcher()

    @Before
    fun setUp() {
        Dispatchers.setMain(testDispatcher)
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    private fun CoroutineScope.collectNavigation(viewModel: MainViewModel, onEvent: () -> Unit) =
        launch { viewModel.navigateToResultEvent.collect { onEvent() } }

    @Test
    fun `scanBarcode shows Searching immediately then Idle plus navigation on success`() = runTest(testDispatcher) {
        val fake = FakeProductAnalysisSource()
        fake.barcodeResult = Result.success(sampleFullProductAnalysis(productName = "Real Product"))
        val viewModel = MainViewModel(fake)

        var navigated = false
        val collectJob = collectNavigation(viewModel) { navigated = true }

        viewModel.scanBarcode("4006381333931")
        assertTrue(
            "Expected Searching immediately after scanBarcode() is called",
            viewModel.barcodeLookupState.value is BarcodeLookupUiState.Searching
        )

        testDispatcher.scheduler.advanceUntilIdle()

        assertEquals(BarcodeLookupUiState.Idle, viewModel.barcodeLookupState.value)
        assertTrue(navigated)
        val analysis = viewModel.analysisState.value
        assertTrue(analysis is AnalysisUiState.Success)
        assertEquals("Real Product", (analysis as AnalysisUiState.Success).analysis.product.productName)
        collectJob.cancel()
    }

    @Test
    fun `scanBarcode ignores a repeated call while already searching`() = runTest(testDispatcher) {
        val fake = FakeProductAnalysisSource()
        fake.barcodeResult = Result.success(sampleFullProductAnalysis())
        fake.barcodeResultDelayMillis = 1000
        val viewModel = MainViewModel(fake)

        viewModel.scanBarcode("4006381333931")
        viewModel.scanBarcode("4006381333931") // must be ignored: a lookup is already in flight
        viewModel.scanBarcode("0000000000000") // must also be ignored, even for a different barcode

        testDispatcher.scheduler.advanceUntilIdle()

        assertEquals(1, fake.analyzeBarcodeCallCount)
        assertEquals("4006381333931", fake.lastBarcodeArgument)
    }

    @Test
    fun `labelScanRequired error does not navigate and carries parsed fields`() = runTest(testDispatcher) {
        val fake = FakeProductAnalysisSource()
        fake.barcodeResult = Result.failure(
            LabelScanRequiredException(
                reason = "This barcode was not found in our database or trusted sources.",
                suggestedAction = "Scan the ingredient label instead",
                providersChecked = listOf("open_food_facts", "upcitemdb"),
                discoveredIdentity = null
            )
        )
        val viewModel = MainViewModel(fake)
        var navigated = false
        val collectJob = collectNavigation(viewModel) { navigated = true }

        viewModel.scanBarcode("0000000000000")
        testDispatcher.scheduler.advanceUntilIdle()

        assertFalse("A structured labelScanRequired result must never navigate", navigated)
        val state = viewModel.barcodeLookupState.value
        assertTrue(state is BarcodeLookupUiState.LabelScanRequired)
        state as BarcodeLookupUiState.LabelScanRequired
        assertEquals("This barcode was not found in our database or trusted sources.", state.reason)
        assertEquals("Scan the ingredient label instead", state.suggestedAction)
        assertNull(state.discoveredIdentity)
        // analysisState must not have been touched into a fabricated Success.
        assertTrue(viewModel.analysisState.value !is AnalysisUiState.Success)
        collectJob.cancel()
    }

    @Test
    fun `labelScanRequired with a discovered identity is preserved`() = runTest(testDispatcher) {
        val fake = FakeProductAnalysisSource()
        val identity = DiscoveredIdentity(
            barcode = "5000112548167",
            productName = "Partially Known Product",
            brand = "Some Brand",
            imageUrl = null
        )
        fake.barcodeResult = Result.failure(
            LabelScanRequiredException(
                reason = "Identity found but nutrition data is incomplete.",
                suggestedAction = null,
                providersChecked = listOf("open_food_facts"),
                discoveredIdentity = identity
            )
        )
        val viewModel = MainViewModel(fake)
        viewModel.scanBarcode("5000112548167")
        testDispatcher.scheduler.advanceUntilIdle()

        val state = viewModel.barcodeLookupState.value as BarcodeLookupUiState.LabelScanRequired
        assertEquals("Partially Known Product", state.discoveredIdentity?.productName)
        assertEquals("Some Brand", state.discoveredIdentity?.brand)
    }

    @Test
    fun `network failure is retryable and distinct from labelScanRequired`() = runTest(testDispatcher) {
        val fake = FakeProductAnalysisSource()
        fake.barcodeResult = Result.failure(BarcodeNetworkException("Unable to reach the server."))
        val viewModel = MainViewModel(fake)

        viewModel.scanBarcode("4006381333931")
        testDispatcher.scheduler.advanceUntilIdle()

        val state = viewModel.barcodeLookupState.value
        assertTrue(state is BarcodeLookupUiState.Failed)
        assertEquals("Unable to reach the server.", (state as BarcodeLookupUiState.Failed).message)
    }

    @Test
    fun `timeout failure is retryable and distinct from labelScanRequired`() = runTest(testDispatcher) {
        val fake = FakeProductAnalysisSource()
        fake.barcodeResult = Result.failure(BarcodeTimeoutException("The request timed out."))
        val viewModel = MainViewModel(fake)

        viewModel.scanBarcode("4006381333931")
        testDispatcher.scheduler.advanceUntilIdle()

        assertTrue(viewModel.barcodeLookupState.value is BarcodeLookupUiState.Failed)
    }

    @Test
    fun `unexpected server error is retryable and does not crash`() = runTest(testDispatcher) {
        val fake = FakeProductAnalysisSource()
        fake.barcodeResult = Result.failure(BarcodeServerException(500, "Server exploded"))
        val viewModel = MainViewModel(fake)

        viewModel.scanBarcode("4006381333931")
        testDispatcher.scheduler.advanceUntilIdle()

        val state = viewModel.barcodeLookupState.value
        assertTrue(state is BarcodeLookupUiState.Failed)
    }

    @Test
    fun `a completely unexpected exception is handled without crashing`() = runTest(testDispatcher) {
        val fake = FakeProductAnalysisSource()
        fake.barcodeResult = Result.failure(RuntimeException()) // no message at all
        val viewModel = MainViewModel(fake)

        viewModel.scanBarcode("4006381333931")
        testDispatcher.scheduler.advanceUntilIdle()

        val state = viewModel.barcodeLookupState.value
        assertTrue(state is BarcodeLookupUiState.Failed)
        assertTrue((state as BarcodeLookupUiState.Failed).message.isNotBlank())
    }

    @Test
    fun `dismissBarcodeLookupState resets to Idle without navigating`() = runTest(testDispatcher) {
        val fake = FakeProductAnalysisSource()
        fake.barcodeResult = Result.failure(BarcodeNetworkException("offline"))
        val viewModel = MainViewModel(fake)

        viewModel.scanBarcode("4006381333931")
        testDispatcher.scheduler.advanceUntilIdle()
        assertTrue(viewModel.barcodeLookupState.value is BarcodeLookupUiState.Failed)

        viewModel.dismissBarcodeLookupState()
        assertEquals(BarcodeLookupUiState.Idle, viewModel.barcodeLookupState.value)
    }

    @Test
    fun `a new scan after dismissing a failure can succeed`() = runTest(testDispatcher) {
        val fake = FakeProductAnalysisSource()
        fake.barcodeResult = Result.failure(BarcodeNetworkException("offline"))
        val viewModel = MainViewModel(fake)

        viewModel.scanBarcode("4006381333931")
        testDispatcher.scheduler.advanceUntilIdle()
        viewModel.dismissBarcodeLookupState()

        fake.barcodeResult = Result.success(sampleFullProductAnalysis())
        var navigated = false
        val collectJob = collectNavigation(viewModel) { navigated = true }

        viewModel.scanBarcode("4006381333931")
        testDispatcher.scheduler.advanceUntilIdle()

        assertTrue(navigated)
        assertEquals(2, fake.analyzeBarcodeCallCount)
        collectJob.cancel()
    }
}
