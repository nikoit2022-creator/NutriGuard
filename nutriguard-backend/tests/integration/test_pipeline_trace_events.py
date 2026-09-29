"""
End-to-end coverage of the internal food-analysis pipeline tracing
(`app.core.pipeline_trace`, issue #30, Codex review round 2, finding 1)
through the real HTTP endpoints -- proves actual entry/success/failure
events land in the journal for a genuine success, a genuine partial
result, and a genuine failure, and that concurrent requests' events are
never cross-attributed.
"""
import asyncio
import io
import json

import pytest
from PIL import Image

from app.core import pipeline_trace, scan_diagnostics
from app.integrations.barcode_providers.base import NutritionFacts, ProviderProductResult
from app.integrations.gemini import GeminiUnavailableError, gemini_service
from app.services import barcode_discovery
from tests.integration.test_barcode_discovery_flow import FakeProvider, _off_result, _patch_providers


async def _register_device(client, device_id: str) -> dict:
    resp = await client.post("/api/v1/auth/device", json={"deviceId": device_id})
    token = resp.json()["accessToken"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _enable_diagnostics(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_ENABLED", True)
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_PATH", str(tmp_path / "scan.jsonl"))
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_MAX_BYTES", 16 * 1024 * 1024)
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_BACKUP_COUNT", 1)
    yield tmp_path / "scan.jsonl"


def _pipeline_events(path, scan_attempt_id: str) -> list[dict]:
    if not path.exists():
        return []
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return [
        r for r in records
        if "pipelineEvent" in r and r.get("scanAttemptId") == scan_attempt_id
    ]


@pytest.mark.asyncio
async def test_successful_cache_hit_scan_records_real_stage_events(app_client, monkeypatch, _enable_diagnostics):
    headers = await _register_device(app_client, "pipeline-success-device")

    async def fake_analyze_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        return json.dumps(
            {
                "productName": "Pipeline Trace Product",
                "rawIngredientText": "Water, Sugar",
                "ingredients": [],
                "sugarGrams": 2.0,
                "sodiumMg": 80.0,
                "saturatedFatGrams": 0.5,
                "nutritionBasis": "PER_100_G",
            }
        )

    monkeypatch.setattr(gemini_service, "analyze_image", fake_analyze_image)
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), color=(1, 2, 3)).save(buf, format="JPEG")
    seed_resp = await app_client.post(
        "/api/v1/scan/label-image",
        headers=headers,
        files={"image": ("label.jpg", buf.getvalue(), "image/jpeg")},
    )
    assert seed_resp.status_code == 200
    barcode = seed_resp.json()["product"]["barcode"]

    attempt_id = "1111111111110001"
    resp = await app_client.post(
        "/api/v1/scan/barcode",
        json={"barcode": barcode},
        headers={**headers, "X-Scan-Attempt-Id": attempt_id},
    )
    assert resp.status_code == 200

    events = _pipeline_events(_enable_diagnostics, attempt_id)
    stage_event_pairs = [(e["pipelineStage"], e["pipelineEvent"]) for e in events]

    assert (pipeline_trace.PROVIDER_CACHE_LOOKUP, "enter") in stage_event_pairs
    assert (pipeline_trace.PROVIDER_CACHE_LOOKUP, "success") in stage_event_pairs
    assert (pipeline_trace.NUTRITION_SCORING_DECISION, "enter") in stage_event_pairs
    assert (pipeline_trace.NUTRITION_SCORING_DECISION, "success") in stage_event_pairs
    assert (pipeline_trace.CATALOG_PERSISTENCE, "enter") in stage_event_pairs
    assert (pipeline_trace.CATALOG_PERSISTENCE, "success") in stage_event_pairs
    assert (pipeline_trace.RESPONSE_CONSTRUCTION, "enter") in stage_event_pairs
    assert (pipeline_trace.RESPONSE_CONSTRUCTION, "success") in stage_event_pairs
    # A pure cache-hit barcode lookup never runs OCR/extraction stages --
    # never fabricated for a stage that did not run.
    assert not any(stage == pipeline_trace.EXTRACTION for stage, _ in stage_event_pairs)
    assert not any(event == "failure" for _, event in stage_event_pairs)


@pytest.mark.asyncio
async def test_partial_discovery_records_success_with_no_fabricated_failure(
    app_client, monkeypatch, _enable_diagnostics
):
    """A `labelScanRequired` partial result is a genuine BUSINESS
    outcome, not a pipeline malfunction: lookup and persistence both
    genuinely succeeded (a real, if incomplete, product was found and
    persisted) before the router turns that into a 404. No pipeline
    stage failure event may be fabricated for it."""
    off = FakeProvider(
        "open_food_facts",
        0.75,
        _off_result(
            name="Partial Nutrition Item",
            raw_ingredient_text="Water, Sugar, Citric Acid",
            nutrition=NutritionFacts(sugar_grams=12.0, sodium_mg=None, saturated_fat_grams=None),
        ),
    )
    _patch_providers(monkeypatch, off=off)

    headers = await _register_device(app_client, "pipeline-partial-device")
    attempt_id = "1111111111110002"
    resp = await app_client.post(
        "/api/v1/scan/barcode",
        json={"barcode": "8901058851397"},
        headers={**headers, "X-Scan-Attempt-Id": attempt_id},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["details"]["labelScanRequired"] is True

    events = _pipeline_events(_enable_diagnostics, attempt_id)
    stage_event_pairs = [(e["pipelineStage"], e["pipelineEvent"]) for e in events]

    assert (pipeline_trace.PROVIDER_CACHE_LOOKUP, "success") in stage_event_pairs
    assert (pipeline_trace.CATALOG_PERSISTENCE, "success") in stage_event_pairs
    # Never reached (the function raises before it) -- must not appear at all.
    assert not any(stage == pipeline_trace.NUTRITION_SCORING_DECISION for stage, _ in stage_event_pairs)
    # No genuine stage malfunction happened -- no failure event at all.
    assert not any(event == "failure" for _, event in stage_event_pairs)


@pytest.mark.asyncio
async def test_extraction_failure_records_a_real_pipeline_failure_event(
    app_client, monkeypatch, _enable_diagnostics
):
    import app.services.food_analysis as food_analysis_module

    async def fake_analyze_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        raise GeminiUnavailableError("simulated failure")

    def fake_fallback(*args, **kwargs):
        raise RuntimeError("simulated fallback failure")

    monkeypatch.setattr(gemini_service, "analyze_image", fake_analyze_image)
    monkeypatch.setattr(food_analysis_module, "fallback_local_analysis", fake_fallback)

    headers = await _register_device(app_client, "pipeline-failure-device")
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), color=(4, 5, 6)).save(buf, format="JPEG")
    attempt_id = "1111111111110003"
    resp = await app_client.post(
        "/api/v1/scan/label-image",
        headers={**headers, "X-Scan-Attempt-Id": attempt_id},
        files={"image": ("label.jpg", buf.getvalue(), "image/jpeg")},
    )
    assert resp.status_code == 503

    events = _pipeline_events(_enable_diagnostics, attempt_id)
    stage_event_pairs = [(e["pipelineStage"], e["pipelineEvent"]) for e in events]

    assert (pipeline_trace.EXTRACTION, "enter") in stage_event_pairs
    assert (pipeline_trace.EXTRACTION, "failure") in stage_event_pairs
    failure_events = [e for e in events if e["pipelineEvent"] == "failure"]
    assert all(e["errorCode"] == "AI_SERVICE_UNAVAILABLE" for e in failure_events)
    # No stage ever falsely reports success after genuinely failing.
    assert (pipeline_trace.EXTRACTION, "success") not in stage_event_pairs


@pytest.mark.asyncio
async def test_concurrent_scans_never_cross_attribute_pipeline_events(
    app_client, monkeypatch, _enable_diagnostics
):
    from app.core.config import settings

    # No external providers involved -- purely hermetic, fast not-found.
    monkeypatch.setattr(settings, "BARCODE_DISCOVERY_ENABLED", False)

    headers = await _register_device(app_client, "pipeline-concurrency-device")
    attempt_a = "1111111111110004"
    attempt_b = "1111111111110005"

    resp_a, resp_b = await asyncio.gather(
        app_client.post(
            "/api/v1/scan/barcode",
            json={"barcode": "9999999999998"},
            headers={**headers, "X-Scan-Attempt-Id": attempt_a},
        ),
        app_client.post(
            "/api/v1/scan/barcode",
            json={"barcode": "9999999999997"},
            headers={**headers, "X-Scan-Attempt-Id": attempt_b},
        ),
    )
    assert resp_a.status_code == 404
    assert resp_b.status_code == 404

    events_a = _pipeline_events(_enable_diagnostics, attempt_a)
    events_b = _pipeline_events(_enable_diagnostics, attempt_b)
    assert events_a, "expected at least one pipeline event for attempt A"
    assert events_b, "expected at least one pipeline event for attempt B"
    assert all(e["scanAttemptId"] == attempt_a for e in events_a)
    assert all(e["scanAttemptId"] == attempt_b for e in events_b)
