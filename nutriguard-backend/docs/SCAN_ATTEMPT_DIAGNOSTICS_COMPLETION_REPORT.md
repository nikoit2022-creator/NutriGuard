# Issue #30 completion report: scan-attempt diagnostics (round 2)

Branch: `feat/backend-scan-attempt-diagnostics-issue-30`.
Worktree: `/home/vboxuser/nutriguard-worktrees/feat-backend-scan-attempt-diagnostics-issue-30`
(isolated `git worktree`; the live bind-mounted checkout at
`/home/vboxuser/nutrigard` and its running containers were never
modified, restarted, or connected to). Pushed to `origin`, not merged,
not deployed — per the task's explicit scope.

Full contract: `docs/SCAN_ATTEMPT_DIAGNOSTICS.md` (see its own "Round 2"
section for a concise summary of what changed and why). This report
covers what was built across both rounds, how it was verified, and
what remains.

## Round 2: Codex review findings addressed

Codex reviewed round 1's published code (commit `9d4e91e`) and returned
7 blockers before Android integration could proceed. All seven are
addressed in this round — see `docs/CODEX_HANDOFF.md`'s
"2026-09-29: issue #30 round 2" entry for the full per-finding writeup,
and `docs/SCAN_ATTEMPT_DIAGNOSTICS.md`'s "Round 2" section for the
contract-level detail. Summary:

1. **Internal pipeline tracing** (`app.core.pipeline_trace`, new) —
   real entry/success/failure events for `food_analysis`'s own internal
   stages, request-scoped, never fabricated, proven against the real
   endpoints (success/partial/failure/concurrency-isolation).
2. **Truthful client-event acknowledgment** — `acceptedEventIds` now
   means "durably written"; a new `retryableEventIds` field carries
   anything not durable (disabled/failed/in-flight). Partial-batch
   failure is safe by construction.
3. **Cross-process-safe dedup ledger** (`app.core.client_event_ledger`,
   new) — a small, separate, bounded SQLite database replacing the
   round-1 in-memory-only cache; proven safe across genuinely separate
   OS processes.
4. **Pseudonymous owner scoping** (`app.core.owner_scope`, new) — a
   stable, one-way `ownerScope` on every journal line; lets the
   operator CLI definitively distinguish two owners sharing a
   `scanAttemptId`.
5. **Operator CLI fixes** — one shared lock across all files, one
   consistent snapshot per invocation, robust handling of malformed/
   non-object/invalid-typed/unreadable input, corrected locking claim.
6. **Streaming request-size enforcement** — the byte bound now holds
   under a missing/lying `Content-Length` or chunked transfer.
7. **Contract reconciliation** — additive stage/outcome enum values for
   partial results, interrupted attempts, image preparation, parsing,
   and client-side persistence.

## Commits on this branch

Round 1: `6038024` (contract publication), `9d4e91e` (operator CLI +
round-1 report), `497a4ab` (round-1 SHA correction).
Round 2: `85abdfbbc44fc9d01f032e53e7059831c74eddf3` (all 7 Codex
review findings — pipeline tracing, truthful ack, cross-process
ledger, owner scoping, CLI fixes, streaming enforcement, contract
reconciliation).

`HEAD` at the time of this report is
`85abdfbbc44fc9d01f032e53e7059831c74eddf3`. Run
`git log origin/main..HEAD` on the branch for the exact, current set.

## What was delivered (cumulative, both rounds)

Round 1 (unchanged, see round-1 history in `docs/CODEX_HANDOFF.md` and
the contract doc): the `X-Scan-Attempt-Id`/`X-Scan-Request-Sequence`
header contract on the three `/scan/*` endpoints; the client
diagnostic-event ingestion endpoint; the extended scan-diagnostics
journal; the operator CLI (now further fixed in round 2); the
`docs/SCAN_ATTEMPT_DIAGNOSTICS.md` contract; `openapi.json`.

Round 2 additions (this report's own focus — see above for the
per-finding summary and the contract doc / handoff entry for full
detail): `app/core/pipeline_trace.py`, `app/core/client_event_ledger.py`,
`app/core/owner_scope.py`; instrumentation added to
`app/services/food_analysis.py` and `app/api/v1/scan.py` (additive
calls only — no control-flow change); `app/api/v1/scan_diagnostics.py`
rewritten for truthful acknowledgment and streaming body-size
enforcement; `app/schemas/scan_diagnostics.py` gained new enum values
and the `retryableEventIds` response field; `app/seed/scan_attempt_trace.py`
rewritten for locking/robustness/owner-scope collision detection;
`app/core/scan_diagnostics.py`'s `record_scan_diagnostic` now returns
whether a line was actually written; `app/core/config.py` gained the
ledger's settings (and dropped the now-superseded in-memory dedup-cache
setting).

## Verification

Full suite, Python 3.14 (host — no functional incompatibility observed;
`openapi.json` regenerated on pinned Python 3.12 specifically to avoid
this affecting the committed contract snapshot, exactly as round 1
did):

```
python3 -m pytest -q
776 passed, 16 skipped, 7 warnings in 27.75s
```

The 16 skipped and 7 warnings are unchanged from both the `origin/main`
baseline and round 1's own count. Up from round 1's 744 passed — +32
new/rewritten tests, zero regressions:

- `tests/unit/test_pipeline_trace.py` (new, 6 tests): enter/success
  ordering, failure-only-for-still-pending-stages, nested-stage stack
  semantics, no-op when unbound, correlation isolation across
  concurrent asyncio tasks.
- `tests/integration/test_pipeline_trace_events.py` (new, 4 tests):
  real success/partial/failure pipeline events through the actual HTTP
  endpoints, plus concurrent-request correlation isolation.
- `tests/unit/test_client_event_ledger.py` (new, 9 tests): reserve/
  commit/release state machine, per-owner scoping, stale-reservation
  reclaim, bounded pruning, and a genuine multi-process concurrency
  test (`multiprocessing`, not threads) proving exactly one of 8
  concurrent processes ever wins the same reservation.
- `tests/unit/test_owner_scope.py` (new, 5 tests): stability, per-user
  distinctness, non-reversibility without the secret, the explicit
  unknown-scope sentinel.
- `tests/unit/test_scan_attempt_trace.py` (+8 tests): non-object JSON
  lines, invalid field types, unreadable files, no per-rotated-file
  lock created, `lockAcquired` reporting, definite owner-scope
  collision detection, same-owner non-collision, unknown-scope
  reporting, and a genuine multi-process concurrent-rotation-during-read
  test.
- `tests/unit/test_scan_diagnostics.py` (updated): `record_scan_diagnostic`'s
  new boolean return value asserted on the existing bounded/disabled/
  write-error tests; the now-superseded in-memory dedup tests removed
  (replaced by the ledger's own tests above).
- `tests/integration/test_scan_diagnostics_client_events.py`
  (substantially rewritten): truthful accept/duplicate/retryable
  three-way classification, partial-batch-failure isolation,
  retry-after-failure recovery, disabled-diagnostics correctly
  reporting retryable (not accepted), and a streaming-body-limit test
  that bypasses the `Content-Length` pre-check to exercise the
  streaming enforcement itself.

**No database/schema change was made** (this feature remains entirely
log-file/SQLite-file-based — see the contract doc §6), so the
acceptance checklist's "real disposable PostgreSQL tests if DB/schema
changes" and "migration upgrade/downgrade/upgrade if any" do not apply.
Docker was available and used, but only to regenerate `openapi.json` on
pinned dependencies inside a throwaway `python:3.12-slim` container —
the live `nutriguard-backend-db-1`/`-backend-1`/`-redis-1` containers
were never touched, restarted, or connected to. No live scans were
performed against them; no credentials were read, written, or changed.

`openapi.json` diff (pinned deps, regenerated inside the throwaway
container): confined to the new additive enum values
(`ClientDiagnosticStage.IMAGE_PREPARATION`/`PARSING`/`PERSISTENCE`,
`ClientDiagnosticOutcome.PARTIAL`/`INTERRUPTED`), the new
`retryableEventIds` response field, and updated docstrings — no
existing field, type, or path changed shape.

## Explicitly not done (scope decision, not an oversight)

Unchanged from round 1: deeper instrumentation of stages *inside*
`app.services.food_analysis`'s own internal helper functions beyond
what round 2 now adds is complete per the review's finding 1 — nothing
further is flagged as outstanding there.

Genuinely still outstanding, carried forward and not silently resolved:

1. **Android integration itself.** This delivery is the verified,
   revised backend contract; Android's diagnostic-event uploader still
   needs to be built/updated against it (§4 of the contract doc,
   including the new `retryableEventIds` handling and the reconciled
   enum values).
2. **Merge/deploy.** No push beyond this branch, no PR, no merge, no
   deployment happens until separately authorized.
3. **The client-event ledger's own operational posture** (retention
   window, whether `CLIENT_EVENT_LEDGER_PATH` needs its own backup/
   monitoring in production) has not been reviewed by an operator —
   it is new infrastructure, bounded and self-healing by design (see
   the contract doc §6), but has no production track record yet.

## Files changed (round 2, in addition to round 1's files)

```
app/api/v1/scan.py
app/api/v1/scan_diagnostics.py
app/core/client_event_ledger.py          (new)
app/core/config.py
app/core/owner_scope.py                  (new)
app/core/pipeline_trace.py               (new)
app/core/scan_diagnostics.py
app/schemas/scan_diagnostics.py
app/seed/scan_attempt_trace.py
app/services/food_analysis.py
.env.example
README.md
openapi.json
docs/SCAN_ATTEMPT_DIAGNOSTICS.md
docs/SCAN_ATTEMPT_DIAGNOSTICS_COMPLETION_REPORT.md  (this file)
docs/CODEX_HANDOFF.md
tests/integration/test_pipeline_trace_events.py     (new)
tests/integration/test_scan_diagnostics_client_events.py
tests/unit/test_client_event_ledger.py              (new)
tests/unit/test_owner_scope.py                      (new)
tests/unit/test_pipeline_trace.py                   (new)
tests/unit/test_scan_attempt_trace.py
tests/unit/test_scan_diagnostics.py
```

## Recommended next step

1. Product owner / Codex reviews this round-2 revision
   (`docs/SCAN_ATTEMPT_DIAGNOSTICS.md`) and this branch/SHA.
2. Codex integrates Android's diagnostic-event uploader against the
   now-revised, verified contract (new `retryableEventIds` handling,
   reconciled enum values — §4/§4.2/§4.3 of the contract doc).
3. No push (beyond this one branch), merge, or deploy happens until
   separately authorized.
