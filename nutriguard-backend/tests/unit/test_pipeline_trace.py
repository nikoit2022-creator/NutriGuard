import asyncio
import json

import pytest

from app.core import pipeline_trace, scan_diagnostics


@pytest.fixture(autouse=True)
def _enable_diagnostics(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_ENABLED", True)
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_PATH", str(tmp_path / "scan.jsonl"))
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_MAX_BYTES", 10 * 1024 * 1024)
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_BACKUP_COUNT", 1)
    yield tmp_path / "scan.jsonl"


def _read_events(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_unbound_calls_are_no_ops_and_write_nothing(_enable_diagnostics):
    pipeline_trace.enter_stage(pipeline_trace.EXTRACTION)
    pipeline_trace.stage_success(pipeline_trace.EXTRACTION)
    pipeline_trace.record_pending_failure("INTERNAL_ERROR")
    assert _read_events(_enable_diagnostics) == []


def test_enter_and_success_are_recorded_in_order(_enable_diagnostics):
    pipeline_trace.bind(scan_attempt_id="a1", request_sequence=1, request_id="r1", operation="scan_barcode")
    pipeline_trace.enter_stage(pipeline_trace.PROVIDER_CACHE_LOOKUP)
    pipeline_trace.stage_success(pipeline_trace.PROVIDER_CACHE_LOOKUP)

    events = _read_events(_enable_diagnostics)
    assert [e["pipelineEvent"] for e in events] == ["enter", "success"]
    assert all(e["pipelineStage"] == pipeline_trace.PROVIDER_CACHE_LOOKUP for e in events)
    assert all(e["scanAttemptId"] == "a1" and e["requestId"] == "r1" for e in events)


def test_a_stage_that_never_succeeds_is_reported_as_a_failure_by_the_router(_enable_diagnostics):
    pipeline_trace.bind(scan_attempt_id="a1", request_sequence=1, request_id="r1", operation="scan_barcode")
    pipeline_trace.enter_stage(pipeline_trace.EXTRACTION)
    # Simulates an exception propagating out of food_analysis before its
    # own stage_success call ever runs -- the router's exception handler
    # calls this.
    pipeline_trace.record_pending_failure("AI_SERVICE_UNAVAILABLE")

    events = _read_events(_enable_diagnostics)
    assert [e["pipelineEvent"] for e in events] == ["enter", "failure"]
    assert events[-1]["errorCode"] == "AI_SERVICE_UNAVAILABLE"


def test_a_stage_that_already_succeeded_is_never_reported_as_failed(_enable_diagnostics):
    pipeline_trace.bind(scan_attempt_id="a1", request_sequence=1, request_id="r1", operation="scan_barcode")
    pipeline_trace.enter_stage(pipeline_trace.EXTRACTION)
    pipeline_trace.stage_success(pipeline_trace.EXTRACTION)
    # A LATER failure outside any traced stage (e.g. router-level
    # response serialization) must not fabricate a failure for a stage
    # that already genuinely completed.
    pipeline_trace.record_pending_failure("INTERNAL_ERROR")

    events = _read_events(_enable_diagnostics)
    assert [e["pipelineEvent"] for e in events] == ["enter", "success"]


def test_nested_stages_both_fail_when_the_inner_one_raises(_enable_diagnostics):
    pipeline_trace.bind(scan_attempt_id="a1", request_sequence=1, request_id="r1", operation="scan_barcode")
    pipeline_trace.enter_stage(pipeline_trace.CATALOG_PERSISTENCE)
    pipeline_trace.enter_stage(pipeline_trace.NUTRITION_SCORING_DECISION)
    pipeline_trace.record_pending_failure("INTERNAL_ERROR")

    events = _read_events(_enable_diagnostics)
    failures = {e["pipelineStage"] for e in events if e["pipelineEvent"] == "failure"}
    assert failures == {pipeline_trace.CATALOG_PERSISTENCE, pipeline_trace.NUTRITION_SCORING_DECISION}


def test_nested_stages_success_closes_only_the_innermost(_enable_diagnostics):
    pipeline_trace.bind(scan_attempt_id="a1", request_sequence=1, request_id="r1", operation="scan_barcode")
    pipeline_trace.enter_stage(pipeline_trace.CATALOG_PERSISTENCE)
    pipeline_trace.enter_stage(pipeline_trace.NUTRITION_SCORING_DECISION)
    pipeline_trace.stage_success(pipeline_trace.NUTRITION_SCORING_DECISION)
    pipeline_trace.stage_success(pipeline_trace.CATALOG_PERSISTENCE)

    events = _read_events(_enable_diagnostics)
    assert [e["pipelineEvent"] for e in events] == ["enter", "enter", "success", "success"]


def test_correlation_is_isolated_across_concurrent_requests(_enable_diagnostics):
    # Each request is handled by its own asyncio Task in the real app;
    # simulated here directly -- two "requests" running concurrently
    # must never see or fail each other's stages.
    async def one_request(attempt_id: str):
        pipeline_trace.bind(
            scan_attempt_id=attempt_id, request_sequence=1, request_id=attempt_id, operation="scan_barcode"
        )
        pipeline_trace.enter_stage(pipeline_trace.EXTRACTION)
        await asyncio.sleep(0)
        pipeline_trace.stage_success(pipeline_trace.EXTRACTION)

    async def run_both():
        await asyncio.gather(
            asyncio.create_task(one_request("attempt-A")),
            asyncio.create_task(one_request("attempt-B")),
        )

    asyncio.run(run_both())

    events = _read_events(_enable_diagnostics)
    by_attempt = {}
    for e in events:
        by_attempt.setdefault(e["scanAttemptId"], []).append(e["pipelineEvent"])
    assert by_attempt["attempt-A"] == ["enter", "success"]
    assert by_attempt["attempt-B"] == ["enter", "success"]
