package com.example.ui.viewmodel

import android.graphics.Bitmap
import com.example.data.remote.BarcodeAuthException
import com.example.data.remote.BarcodeNetworkException
import com.example.data.remote.BarcodeServerException
import com.example.data.remote.BarcodeTimeoutException
import com.example.data.remote.DiscoveredIdentity
import com.example.data.remote.LabelScanRequiredException
import com.example.data.remote.dto.IngredientDto
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
    fun `authentication failure is distinct from a generic server error`() = runTest(testDispatcher) {
        val fake = FakeProductAnalysisSource()
        fake.barcodeResult = Result.failure(
            BarcodeAuthException(401, "Your session could not be verified. Please restart the app and try again.")
        )
        val viewModel = MainViewModel(fake)

        viewModel.scanBarcode("4006381333931")
        testDispatcher.scheduler.advanceUntilIdle()

        val state = viewModel.barcodeLookupState.value
        assertTrue(state is BarcodeLookupUiState.Failed)
        assertEquals(
            "Your session could not be verified. Please restart the app and try again.",
            (state as BarcodeLookupUiState.Failed).message
        )
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

    // --- pendingBarcode / analyzeLabelImage (review requirement: keep
    // the scanned barcode across the labelScanRequired -> camera/gallery
    // -> analyzeLabelImage flow, attach it to the submission, and only
    // clear it on genuine success or an explicit whole-flow cancel).
    // `analyzeLabelImage` is the single function BOTH the camera capture
    // callback and the gallery-pick callback call in ScanHomeScreen, so
    // these tests cover "camera and gallery enrichment" identically --
    // neither UI entry point does anything barcode-specific itself, the
    // ViewModel is what attaches pendingBarcode. ------------------------

    /** A minimal, decodable [Bitmap] -- Robolectric provides a real (shadowed) implementation, no mocking library needed. */
    private fun fakeBitmap(): Bitmap = Bitmap.createBitmap(4, 4, Bitmap.Config.ARGB_8888)

    @Test
    fun `a labelScanRequired barcode result sets pendingBarcode`() = runTest(testDispatcher) {
        val fake = FakeProductAnalysisSource()
        fake.barcodeResult = Result.failure(
            LabelScanRequiredException(
                reason = "Identity found but nutrition data is incomplete.",
                suggestedAction = null,
                providersChecked = emptyList(),
                discoveredIdentity = null
            )
        )
        val viewModel = MainViewModel(fake)

        assertNull(viewModel.pendingBarcode.value)
        viewModel.scanBarcode("4006381333931")
        testDispatcher.scheduler.advanceUntilIdle()

        assertEquals("4006381333931", viewModel.pendingBarcode.value)
    }

    @Test
    fun `analyzeLabelImage attaches pendingBarcode to the submission (camera or gallery)`() = runTest(testDispatcher) {
        val fake = FakeProductAnalysisSource()
        fake.barcodeResult = Result.failure(
            LabelScanRequiredException("incomplete", null, emptyList(), null)
        )
        val viewModel = MainViewModel(fake)
        viewModel.scanBarcode("4006381333931")
        testDispatcher.scheduler.advanceUntilIdle()

        fake.imageResult = Result.success(sampleFullProductAnalysis(barcode = "4006381333931"))
        viewModel.analyzeLabelImage(fakeBitmap())
        testDispatcher.scheduler.advanceUntilIdle()

        assertEquals(1, fake.analyzeImageLabelCallCount)
        assertEquals("4006381333931", fake.lastImageLabelBarcodeArgument)
    }

    @Test
    fun `analyzeLabelImage submits no barcode for a standalone (no pending) capture`() = runTest(testDispatcher) {
        val fake = FakeProductAnalysisSource()
        fake.imageResult = Result.success(sampleFullProductAnalysis())
        val viewModel = MainViewModel(fake)

        assertNull(viewModel.pendingBarcode.value)
        viewModel.analyzeLabelImage(fakeBitmap())
        testDispatcher.scheduler.advanceUntilIdle()

        assertNull(
            "A standalone submission (no prior labelScanRequired barcode) must never invent one",
            fake.lastImageLabelBarcodeArgument
        )
    }

    @Test
    fun `a fully successful analyzeLabelImage clears pendingBarcode and navigates`() = runTest(testDispatcher) {
        val fake = FakeProductAnalysisSource()
        fake.barcodeResult = Result.failure(LabelScanRequiredException("incomplete", null, emptyList(), null))
        val viewModel = MainViewModel(fake)
        viewModel.scanBarcode("4006381333931")
        testDispatcher.scheduler.advanceUntilIdle()
        assertEquals("4006381333931", viewModel.pendingBarcode.value)

        fake.imageResult = Result.success(sampleFullProductAnalysis(barcode = "4006381333931"))
        var navigated = false
        val collectJob = collectNavigation(viewModel) { navigated = true }

        viewModel.analyzeLabelImage(fakeBitmap())
        testDispatcher.scheduler.advanceUntilIdle()

        assertTrue(navigated)
        assertNull("pendingBarcode must be cleared after a genuine full success", viewModel.pendingBarcode.value)
        assertEquals(BarcodeLookupUiState.Idle, viewModel.barcodeLookupState.value)
        collectJob.cancel()
    }

    @Test
    fun `a label photo that is still incomplete keeps pendingBarcode for the next photo`() = runTest(testDispatcher) {
        val fake = FakeProductAnalysisSource()
        fake.barcodeResult = Result.failure(LabelScanRequiredException("nutrition still needed", null, emptyList(), null))
        val viewModel = MainViewModel(fake)
        viewModel.scanBarcode("4006381333931")
        testDispatcher.scheduler.advanceUntilIdle()

        // First photo (e.g. the ingredients list) is submitted but the
        // backend says nutrition is STILL missing -- a genuine partial
        // result, never a generic error, and pendingBarcode must survive
        // it so the NEXT photo (e.g. the nutrition panel) keeps
        // combining into the same product.
        fake.imageResult = Result.failure(
            LabelScanRequiredException(
                reason = "Ingredients verified, nutrition still needed.",
                suggestedAction = null,
                providersChecked = emptyList(),
                discoveredIdentity = DiscoveredIdentity("4006381333931", "Diagnostic Product", null, null),
                analysisComplete = false,
                healthScoreAvailable = false,
                healthScore = null,
                nutritionScanRequired = true,
                ingredientsScanRequired = false,
                ingredients = listOf(IngredientDto(id = "sugar", commonName = "Sugar"))
            )
        )
        var navigated = false
        val collectJob = collectNavigation(viewModel) { navigated = true }

        viewModel.analyzeLabelImage(fakeBitmap())
        testDispatcher.scheduler.advanceUntilIdle()

        assertFalse("a still-incomplete result must never navigate", navigated)
        assertEquals("4006381333931", viewModel.pendingBarcode.value)
        val state = viewModel.barcodeLookupState.value
        assertTrue(state is BarcodeLookupUiState.LabelScanRequired)
        state as BarcodeLookupUiState.LabelScanRequired
        // Never a fabricated/zero score for an incomplete result.
        assertEquals(false, state.healthScoreAvailable)
        assertNull(state.healthScore)
        assertEquals(true, state.nutritionScanRequired)
        assertEquals(false, state.ingredientsScanRequired)
        assertEquals(1, state.ingredients.size)
        assertEquals("Sugar", state.ingredients[0].commonName)

        // Second photo (nutrition panel) still attaches the SAME pending barcode.
        fake.imageResult = Result.success(sampleFullProductAnalysis(barcode = "4006381333931"))
        viewModel.analyzeLabelImage(fakeBitmap())
        testDispatcher.scheduler.advanceUntilIdle()

        assertEquals(2, fake.analyzeImageLabelCallCount)
        assertEquals("4006381333931", fake.lastImageLabelBarcodeArgument)
        assertNull("cleared only once the SECOND photo genuinely completes it", viewModel.pendingBarcode.value)
        collectJob.cancel()
    }

    @Test
    fun `cancelPendingScanFlow clears both the card state and pendingBarcode`() = runTest(testDispatcher) {
        val fake = FakeProductAnalysisSource()
        fake.barcodeResult = Result.failure(LabelScanRequiredException("incomplete", null, emptyList(), null))
        val viewModel = MainViewModel(fake)
        viewModel.scanBarcode("4006381333931")
        testDispatcher.scheduler.advanceUntilIdle()
        assertEquals("4006381333931", viewModel.pendingBarcode.value)

        viewModel.cancelPendingScanFlow()

        assertEquals(BarcodeLookupUiState.Idle, viewModel.barcodeLookupState.value)
        assertNull("an explicit whole-flow cancel must clear pendingBarcode", viewModel.pendingBarcode.value)
    }

    @Test
    fun `dismissBarcodeLookupState hides the card but preserves pendingBarcode`() = runTest(testDispatcher) {
        val fake = FakeProductAnalysisSource()
        fake.barcodeResult = Result.failure(LabelScanRequiredException("incomplete", null, emptyList(), null))
        val viewModel = MainViewModel(fake)
        viewModel.scanBarcode("4006381333931")
        testDispatcher.scheduler.advanceUntilIdle()

        // This is what "tap Scan label for more information" does before
        // opening the camera -- it must NOT lose the pending barcode.
        viewModel.dismissBarcodeLookupState()

        assertEquals(BarcodeLookupUiState.Idle, viewModel.barcodeLookupState.value)
        assertEquals(
            "dismissing the CARD (not the whole flow) must never clear pendingBarcode -- requirement: never clear it before the follow-up request",
            "4006381333931",
            viewModel.pendingBarcode.value
        )
    }

    @Test
    fun `analyzeLabelImage network failure preserves pendingBarcode for a retry`() = runTest(testDispatcher) {
        val fake = FakeProductAnalysisSource()
        fake.barcodeResult = Result.failure(LabelScanRequiredException("incomplete", null, emptyList(), null))
        val viewModel = MainViewModel(fake)
        viewModel.scanBarcode("4006381333931")
        testDispatcher.scheduler.advanceUntilIdle()

        fake.imageResult = Result.failure(BarcodeNetworkException("offline"))
        viewModel.analyzeLabelImage(fakeBitmap())
        testDispatcher.scheduler.advanceUntilIdle()

        assertTrue(viewModel.barcodeLookupState.value is BarcodeLookupUiState.Failed)
        assertEquals(
            "a transient network failure is not an explicit cancel -- pendingBarcode must survive it",
            "4006381333931",
            viewModel.pendingBarcode.value
        )
    }

    @Test
    fun `analyzeLabelImage ignores a repeated call while already in flight`() = runTest(testDispatcher) {
        val fake = FakeProductAnalysisSource()
        fake.imageResult = Result.success(sampleFullProductAnalysis())
        fake.imageResultDelayMillis = 1000
        val viewModel = MainViewModel(fake)

        viewModel.analyzeLabelImage(fakeBitmap())
        viewModel.analyzeLabelImage(fakeBitmap())

        testDispatcher.scheduler.advanceUntilIdle()

        assertEquals(1, fake.analyzeImageLabelCallCount)
    }
}
