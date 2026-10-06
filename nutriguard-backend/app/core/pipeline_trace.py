"""
Internal food-analysis pipeline tracing (issue #30, Codex review round 2).

Records actually-observed entry/success/failure events for the stages
*inside* `app.services.food_analysis` that the router-level diagnostics
(`app.api.v1.scan`) never see -- see that module's own `_diagnostic_base`
docstring for the coarse, router-only `stage` field this is distinct
from. Never invents an event for a stage that did not run: a stage's
"enter" event is only ever written at the point its own code actually
starts executing, and its "success" event only once that code actually
finished without raising.

Design: request-scoped correlation via a plain `contextvars.ContextVar`,
bound once per HTTP request by `app.api.v1.scan` (see `bind`) and never
explicitly cleared -- each request is handled by its own fresh asyncio
Task (both Starlette's routing and uvicorn's per-request-cycle task
creation guarantee this), so a `ContextVar.set()` made while handling
one request can never be observed by a sibling or later request's task;
`bind()` always installs a brand-new dict, so there is nothing to leak
even within one task across a hypothetical reuse. This lets
`app.services.food_analysis` call `enter_stage`/`stage_success` as
plain, synchronous, best-effort calls at existing statement boundaries
-- no new parameters threaded through its call chain, no `try`/`except`
inserted into that file's own control flow, so business logic and
control flow there are completely unchanged.

Failure is recorded centrally, in `app.api.v1.scan`'s own existing
exception handlers (see `record_pending_failure`): whichever stage(s)
were entered but never reached their own `stage_success` call are
exactly the stages that were still running when the exception
propagated out of `food_analysis` -- so a single call per handler is
enough to attribute the failure correctly, without `food_analysis.py`
needing its own try/except at all. If every stage already succeeded
before the exception (e.g. a later response-serialization error in the
router itself), nothing is pending and no event is fabricated.

Stages nest rather than strictly sequence: this codebase deliberately
commits several stages' work in ONE outer transaction for atomicity
(see e.g. `_finalize_barcode_enrichment`'s docstring), so a
"nutrition/scoring decision" can happen logically *inside* an
outstanding "catalog persistence" unit of work rather than strictly
after it finishes. A plain LIFO stack of pending stages models this
correctly: `stage_success` closes the innermost still-open stage, and
on failure every stage still open on the stack genuinely did not
complete -- each gets its own failure event, innermost first.
"""
from __future__ import annotations

import time
from contextvars import ContextVar
from typing import Any

from app.core.scan_diagnostics import record_scan_diagnostic

# Canonical stage vocabulary -- mirrors issue #30's acceptance list
# ("provider/cache lookup, image/text extraction, ingredient
# segmentation, identity/language resolution, catalog persistence,
# nutrition/scoring decision") plus response construction. Not every
# entry point exercises every stage (e.g. a pure barcode lookup never
# runs EXTRACTION/INGREDIENT_SEGMENTATION/IDENTITY_LANGUAGE_RESOLUTION)
# -- see each call site in `app.services.food_analysis`.
PROVIDER_CACHE_LOOKUP = "provider_cache_lookup"
EXTRACTION = "extraction"
INGREDIENT_SEGMENTATION = "ingredient_segmentation"
IDENTITY_LANGUAGE_RESOLUTION = "identity_language_resolution"
CATALOG_PERSISTENCE = "catalog_persistence"
NUTRITION_SCORING_DECISION = "nutrition_scoring_decision"
RESPONSE_CONSTRUCTION = "response_construction"

_ctx: ContextVar[dict | None] = ContextVar("pipeline_trace_ctx", default=None)


def bind(*, scan_attempt_id: str | None, request_sequence: int | None, request_id: str | None, operation: str) -> None:
    """Called once per request, before `food_analysis` runs (see
    `app.api.v1.scan`). Installs a fresh context -- never reused from a
    previous request, never merged with one."""
    _ctx.set(
        {
            "scanAttemptId": scan_attempt_id,
            "requestSequence": request_sequence,
            "requestId": request_id,
            "operation": operation,
            "stack": [],
        }
    )


def _write(ctx: dict, stage: str, event: str, *, error_code: str | None = None, duration_ms: float | None = None) -> None:
    try:
        record_scan_diagnostic(
            origin="backend",
            scanAttemptId=ctx["scanAttemptId"],
            requestSequence=ctx["requestSequence"],
            requestId=ctx["requestId"],
            operation=ctx["operation"],
            pipelineStage=stage,
            pipelineEvent=event,
            errorCode=error_code,
            durationMs=duration_ms,
        )
    except Exception:  # noqa: BLE001 -- tracing must never break a scan.
        return


def enter_stage(stage: str) -> None:
    """Records that `stage`'s own code is genuinely about to run. A
    no-op (never fabricates anything) when no request context is bound
    -- e.g. a unit test calling a `food_analysis` function directly."""
    ctx = _ctx.get()
    if ctx is None:
        return
    ctx["stack"].append({"stage": stage, "started": time.perf_counter()})
    _write(ctx, stage, "enter")


def stage_success(stage: str) -> None:
    """Records that `stage` actually completed without raising. No-op if
    no context is bound, or if `stage` isn't the innermost still-open
    stage (defensive -- should not happen given each call site pairs its
    own `enter_stage`/`stage_success` correctly)."""
    ctx = _ctx.get()
    if ctx is None or not ctx["stack"] or ctx["stack"][-1]["stage"] != stage:
        return
    entry = ctx["stack"].pop()
    duration_ms = round((time.perf_counter() - entry["started"]) * 1000, 2)
    _write(ctx, stage, "success", duration_ms=duration_ms)


def record_pending_failure(error_code: str) -> None:
    """Called from `app.api.v1.scan`'s existing exception handlers.
    Every stage still open on the stack (entered, never reached its own
    `stage_success`) genuinely did not complete by the time the
    exception propagated -- each gets its own failure event, innermost
    first. A no-op if nothing is open (every internal stage already
    succeeded before the exception occurred, e.g. a later router-level
    serialization error) -- never invents a pipeline-stage failure that
    did not happen."""
    ctx = _ctx.get()
    if ctx is None:
        return
    now = time.perf_counter()
    while ctx["stack"]:
        entry = ctx["stack"].pop()
        duration_ms = round((now - entry["started"]) * 1000, 2)
        _write(ctx, entry["stage"], "failure", error_code=error_code, duration_ms=duration_ms)


def current_context_for_testing() -> dict[str, Any] | None:
    """Test-only introspection helper."""
    return _ctx.get()
