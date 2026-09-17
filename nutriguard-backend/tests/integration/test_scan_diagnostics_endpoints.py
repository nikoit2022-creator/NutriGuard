"""
Full HTTP coverage that all three `/scan/*` endpoints (`app.api.v1.scan`,
rewritten by this task) actually call `app.core.scan_diagnostics.
record_scan_diagnostic` -- previously only `/scan/label-image` did.
Diagnostics are captured by monkeypatching `app.api.v1.scan.
record_scan_diagnostic` directly (the name bound into that module at
import time), the same technique `tests/unit/test_scan_diagnostics.py`
uses at the lower `app.core.scan_diagnostics` level -- see that file for
the underlying bounded/multi-process-safe logging mechanism's own tests
(UNCHANGED by this task; not re-tested here).
"""
import io
import json

import pytest
from PIL import Image

import app.api.v1.scan as scan_module
from app.integrations.gemini import gemini_service


async def _register_device(client, device_id: str) -> dict:
    resp = await client.post("/api/v1/auth/device", json={"deviceId": device_id})
    token = resp.json()["accessToken"]
    return {"Authorization": f"Bearer {token}"}


def _capture(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    def _fake(**fields):
        calls.append(fields)

    monkeypatch.setattr(scan_module, "record_scan_diagnostic", _fake)
    return calls


def _small_jpeg_files() -> dict:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), color=(1, 2, 3)).save(buf, format="JPEG")
    return {"image": ("label.jpg", buf.getvalue(), "image/jpeg")}


# --- /scan/barcode -----------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_barcode_failure_records_a_diagnostic(app_client, monkeypatch):
    calls = _capture(monkeypatch)
    headers = await _register_device(app_client, "diag-barcode-fail")

    resp = await app_client.post(
        "/api/v1/scan/barcode", json={"barcode": "9999999999999"}, headers=headers
    )
    assert resp.status_code == 404
    assert len(calls) == 1
    assert calls[0]["outcome"] == "failed"
    assert calls[0]["errorCode"] == "PRODUCT_NOT_FOUND"
    assert calls[0]["operation"] == "scan_barcode"


@pytest.mark.asyncio
async def test_scan_barcode_success_records_a_diagnostic_with_ingredient_counts(app_client, monkeypatch):
    headers = await _register_device(app_client, "diag-barcode-success")

    async def fake_analyze_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        return json.dumps(
            {
                "productName": "Diag Product",
                "rawIngredientText": "Water, Sugar, Salt",
                "ingredients": [],
                "sugarGrams": 2.0,
                "sodiumMg": 80.0,
                "saturatedFatGrams": 0.5,
                "nutritionBasis": "PER_100_G",
            }
        )

    monkeypatch.setattr(gemini_service, "analyze_image", fake_analyze_image)
    seed = await app_client.post(
        "/api/v1/scan/label-image", headers=headers, files=_small_jpeg_files()
    )
    assert seed.status_code == 200
    barcode = seed.json()["product"]["barcode"]

    calls = _capture(monkeypatch)
    resp = await app_client.post("/api/v1/scan/barcode", json={"barcode": barcode}, headers=headers)
    assert resp.status_code == 200
    assert len(calls) == 1
    assert calls[0]["outcome"] == "success"
    assert calls[0]["barcode"] == barcode
    assert "recognizedIngredientCount" in calls[0]


# --- /scan/ocr-text ------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_ocr_text_success_records_a_diagnostic(app_client, monkeypatch):
    calls = _capture(monkeypatch)
    headers = await _register_device(app_client, "diag-ocr-success")

    resp = await app_client.post(
        "/api/v1/scan/ocr-text", json={"rawText": "Water, Sugar, Salt"}, headers=headers
    )
    assert resp.status_code == 200
    assert len(calls) == 1
    assert calls[0]["outcome"] == "success"
    assert calls[0]["operation"] == "scan_ocr_text"
    assert "recognizedIngredientCount" in calls[0]
    assert "unresolvedIngredientCount" in calls[0]
    assert "untranslatedIngredientCount" in calls[0]


# --- /scan/label-image ---------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_label_image_bad_content_type_records_a_diagnostic(app_client, monkeypatch):
    """Task: early validation failures (bad content-type) now ALSO get
    a diagnostic record -- previously they raised before any diagnostic
    was ever built."""
    calls = _capture(monkeypatch)
    headers = await _register_device(app_client, "diag-badtype")

    resp = await app_client.post(
        "/api/v1/scan/label-image",
        headers=headers,
        files={"image": ("label.txt", b"not an image", "text/plain")},
    )
    assert resp.status_code == 422
    assert len(calls) == 1
    assert calls[0]["outcome"] == "failed"
    assert calls[0]["errorCode"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_scan_label_image_oversized_records_a_diagnostic(app_client, monkeypatch):
    """Task: early validation failures (oversized image) now ALSO get a
    diagnostic record."""
    monkeypatch.setattr(scan_module.settings, "MAX_IMAGE_SIZE_BYTES", 10)
    calls = _capture(monkeypatch)
    headers = await _register_device(app_client, "diag-oversized")

    resp = await app_client.post(
        "/api/v1/scan/label-image",
        headers=headers,
        files={"image": ("label.jpg", b"x" * 100, "image/jpeg")},
    )
    assert resp.status_code == 400
    assert len(calls) == 1
    assert calls[0]["outcome"] == "failed"
    assert calls[0]["errorCode"] == "IMAGE_TOO_LARGE"


@pytest.mark.asyncio
async def test_scan_label_image_success_records_a_diagnostic_after_response_build(app_client, monkeypatch):
    calls = _capture(monkeypatch)
    headers = await _register_device(app_client, "diag-label-success")

    async def fake_analyze_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        return json.dumps(
            {
                "productName": "Diag Product",
                "rawIngredientText": "Water, Sugar, Salt",
                "ingredients": [],
                "sugarGrams": 2.0,
                "sodiumMg": 80.0,
                "saturatedFatGrams": 0.5,
                "nutritionBasis": "PER_100_G",
            }
        )

    monkeypatch.setattr(gemini_service, "analyze_image", fake_analyze_image)
    resp = await app_client.post(
        "/api/v1/scan/label-image", headers=headers, files=_small_jpeg_files()
    )
    assert resp.status_code == 200
    assert len(calls) == 1
    assert calls[0]["outcome"] == "success"
    assert calls[0]["operation"] == "scan_label_image"
    assert "nutritionRecognized" in calls[0]
    assert "ingredientsRecognized" in calls[0]


@pytest.mark.asyncio
async def test_scan_label_image_serialization_failure_after_analysis_is_a_failed_diagnostic_not_success(
    app_client, monkeypatch
):
    """Task's key reordering: a `_to_analysis_out` failure occurring
    AFTER `analyze_label_image` already succeeded must fall into the
    generic `except Exception` failure-diagnostic path, never leave a
    false "success" record behind.

    Note: the shared `app_client` fixture's `httpx.ASGITransport` runs
    with its default `raise_app_exceptions=True`, so an exception that
    reaches the ASGI app's outermost error middleware is re-raised into
    the TEST itself rather than turned into an HTTP response (a test-
    harness property, not something this endpoint controls -- in a real
    deployment behind uvicorn it would become a normal JSON 500 via
    `app.main`'s registered `Exception` handler). What actually matters
    here -- that the diagnostic write happened, and recorded "failed"
    rather than a false "success", BEFORE the exception propagates -- is
    still fully observable via the captured `calls` list.
    """
    calls = _capture(monkeypatch)
    headers = await _register_device(app_client, "diag-serialization-fail")

    async def fake_analyze_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        return json.dumps(
            {
                "productName": "Diag Product",
                "rawIngredientText": "Water, Sugar, Salt",
                "ingredients": [],
                "sugarGrams": 2.0,
                "sodiumMg": 80.0,
                "saturatedFatGrams": 0.5,
                "nutritionBasis": "PER_100_G",
            }
        )

    def _broken_to_analysis_out(result):
        raise ValueError("forced serialization failure")

    monkeypatch.setattr(gemini_service, "analyze_image", fake_analyze_image)
    monkeypatch.setattr(scan_module, "_to_analysis_out", _broken_to_analysis_out)

    with pytest.raises(ValueError, match="forced serialization failure"):
        await app_client.post("/api/v1/scan/label-image", headers=headers, files=_small_jpeg_files())

    assert len(calls) == 1
    assert calls[0]["outcome"] == "failed"
    assert calls[0]["errorCode"] == "INTERNAL_ERROR"
    assert all(c["outcome"] != "success" for c in calls)


# --- record_scan_diagnostic raising internally: local defense-in-depth ----
#
# `app.core.scan_diagnostics.record_scan_diagnostic`'s OWN implementation
# already guarantees it never raises (a blanket `except Exception: return`
# -- see `tests/unit/test_scan_diagnostics.py`, unchanged by this task).
# Each of the three endpoints' call sites in `app/api/v1/scan.py` ALSO
# wraps every call through `_safe_record_scan_diagnostic`, a local
# try/except that logs and swallows (task: "diagnostic failures must
# never break scanning") -- so the property holds even if
# `record_scan_diagnostic` itself were ever weakened, not merely because
# of that function's own current internals. The three tests below
# monkeypatch `record_scan_diagnostic` itself to raise and confirm the
# endpoint still returns its normal response/error regardless.


@pytest.mark.asyncio
async def test_diagnostic_write_failure_on_a_failed_scan_does_not_replace_the_real_error(app_client, monkeypatch):
    """`/scan/barcode`'s `except AppError` branch (see
    `app/api/v1/scan.py` around `scan_barcode`) calls
    `_safe_record_scan_diagnostic(...)` before its own `raise` -- even
    when the underlying `record_scan_diagnostic` raises, the original
    `ProductNotFoundError`/404 the caller should see is unaffected."""

    def _raise(**fields):
        raise RuntimeError("diagnostics backend exploded")

    monkeypatch.setattr(scan_module, "record_scan_diagnostic", _raise)
    headers = await _register_device(app_client, "diag-raises-barcode")

    response = await app_client.post(
        "/api/v1/scan/barcode", json={"barcode": "9999999999999"}, headers=headers
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_diagnostic_write_failure_on_a_successful_scan_does_not_prevent_the_response(app_client, monkeypatch):
    """`/scan/ocr-text`'s success path calls
    `_safe_record_scan_diagnostic(...)` AFTER the response was already
    built -- even when the underlying `record_scan_diagnostic` raises,
    the caller still receives the otherwise-valid 200 response."""

    def _raise(**fields):
        raise RuntimeError("diagnostics backend exploded")

    monkeypatch.setattr(scan_module, "record_scan_diagnostic", _raise)
    headers = await _register_device(app_client, "diag-raises-ocr-success")

    response = await app_client.post(
        "/api/v1/scan/ocr-text", json={"rawText": "Water, Sugar, Salt"}, headers=headers
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_label_image_diagnostic_write_failure_on_success_does_not_prevent_the_response(app_client, monkeypatch):
    def _raise(**fields):
        raise RuntimeError("diagnostics backend exploded")

    monkeypatch.setattr(scan_module, "record_scan_diagnostic", _raise)
    headers = await _register_device(app_client, "diag-raises-label-image")

    async def fake_analyze_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        return json.dumps(
            {
                "productName": "Diag Product",
                "rawIngredientText": "Water, Sugar, Salt",
                "ingredients": [],
                "sugarGrams": 2.0,
                "sodiumMg": 80.0,
                "saturatedFatGrams": 0.5,
                "nutritionBasis": "PER_100_G",
            }
        )

    monkeypatch.setattr(gemini_service, "analyze_image", fake_analyze_image)
    response = await app_client.post("/api/v1/scan/label-image", headers=headers, files=_small_jpeg_files())
    assert response.status_code == 200


# --- Code-review follow-up (issue 4): truthful diagnostics -----------------


@pytest.mark.asyncio
async def test_scan_barcode_labelscan_required_partial_result_is_classified_consistently(
    app_client, monkeypatch
):
    """A barcode whose IDENTITY is known (persisted via an earlier
    ingredients-only label scan) but lacks verified nutrition raises
    `labelScanRequired` -- this is a PARTIAL result, not a failure, and
    must be classified the same way `/scan/ocr-text`/`/scan/label-image`
    already classify it (task: "classify partial results consistently
    across scan endpoints"). Distinct from a genuinely unknown barcode
    (see `test_scan_barcode_failure_records_a_diagnostic` above, still
    "failed") -- the discriminator is a real discovered identity, not
    merely the `labelScanRequired` flag both cases set."""
    headers = await _register_device(app_client, "diag-barcode-partial")

    async def fake_analyze_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        return json.dumps(
            {
                "productName": "Partial Diag Product",
                # No ingredient evidence at all (neither a structured
                # array NOR raw text) -- V13's success gate only requires
                # ingredient RECOGNITION, so nutrition alone missing
                # still succeeds; this must fail BOTH groups to reach
                # `labelScanRequired`.
                "rawIngredientText": "",
                "ingredients": [],
                "sugarGrams": None,
                "sodiumMg": None,
                "saturatedFatGrams": None,
                "nutritionBasis": "UNKNOWN",
            }
        )

    monkeypatch.setattr(gemini_service, "analyze_image", fake_analyze_image)
    seed = await app_client.post(
        "/api/v1/scan/label-image",
        headers=headers,
        files=_small_jpeg_files(),
        data={"barcode": "5328710122696"},
    )
    assert seed.status_code == 404  # labelScanRequired -- no ingredients recognized yet
    assert seed.json()["error"]["details"]["labelScanRequired"] is True

    calls = _capture(monkeypatch)
    resp = await app_client.post(
        "/api/v1/scan/barcode", json={"barcode": "5328710122696"}, headers=headers
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["details"]["labelScanRequired"] is True
    assert len(calls) == 1
    assert calls[0]["outcome"] == "partial"
    assert calls[0]["errorCode"] == "PRODUCT_NOT_FOUND"


@pytest.mark.asyncio
async def test_scan_barcode_cache_hit_reports_cache_as_data_source(app_client, monkeypatch):
    """A repeat `/scan/barcode` call for an already-fully-verified
    product must report `dataSource: "cache"` -- real, observed state
    (`is_from_database_cache`), never the endpoint's own operation name
    (task: "dataSource ... propagate actual observed source information,
    without guessing")."""
    headers = await _register_device(app_client, "diag-barcode-cache")

    async def fake_analyze_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        return json.dumps(
            {
                "productName": "Cache Diag Product",
                "rawIngredientText": "Water, Sugar, Salt",
                "ingredients": [],
                "sugarGrams": 2.0,
                "sodiumMg": 80.0,
                "saturatedFatGrams": 0.5,
                "nutritionBasis": "PER_100_G",
            }
        )

    monkeypatch.setattr(gemini_service, "analyze_image", fake_analyze_image)
    seed = await app_client.post(
        "/api/v1/scan/label-image", headers=headers, files=_small_jpeg_files()
    )
    assert seed.status_code == 200
    barcode = seed.json()["product"]["barcode"]
    seeded_source = seed.json()["product"].get("source")

    calls = _capture(monkeypatch)
    resp = await app_client.post("/api/v1/scan/barcode", json={"barcode": barcode}, headers=headers)
    assert resp.status_code == 200
    assert len(calls) == 1
    assert calls[0]["outcome"] == "success"
    # The FIRST (label-scan) request is not a cache hit -- this second,
    # identical-barcode request against the already-persisted row is.
    assert calls[0]["dataSource"] == "cache"
    assert calls[0]["dataSource"] != seeded_source or seeded_source is None


@pytest.mark.asyncio
async def test_scan_label_image_computed_field_serialization_failure_is_a_failed_diagnostic(
    app_client, monkeypatch
):
    """Distinct from `test_scan_label_image_serialization_failure_after_analysis_is_a_failed_diagnostic_not_success`
    (which forces `_to_analysis_out`'s own construction to fail): THIS
    test forces a failure specifically during the LATER full-serialization
    step (`_finish_serializing`'s `model_dump(mode="json")` call, which
    evaluates every nested `@computed_field`) -- the exact gap the task
    identified ("constructing FullProductAnalysisOut does not finish
    JSON serialization; computed-field/serialization failures can still
    occur after success is recorded"). Must still be diagnosed as a
    failure, never a false success."""
    calls = _capture(monkeypatch)
    headers = await _register_device(app_client, "diag-computed-field-fail")

    async def fake_analyze_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        return json.dumps(
            {
                "productName": "Diag Product",
                "rawIngredientText": "Water, Sugar, Salt",
                "ingredients": [],
                "sugarGrams": 2.0,
                "sodiumMg": 80.0,
                "saturatedFatGrams": 0.5,
                "nutritionBasis": "PER_100_G",
            }
        )

    def _broken_finish_serializing(out):
        raise ValueError("forced computed-field serialization failure")

    monkeypatch.setattr(gemini_service, "analyze_image", fake_analyze_image)
    monkeypatch.setattr(scan_module, "_finish_serializing", _broken_finish_serializing)

    with pytest.raises(ValueError, match="forced computed-field serialization failure"):
        await app_client.post("/api/v1/scan/label-image", headers=headers, files=_small_jpeg_files())

    assert len(calls) == 1
    assert calls[0]["outcome"] == "failed"
    assert calls[0]["errorCode"] == "INTERNAL_ERROR"
    assert all(c["outcome"] != "success" for c in calls)
