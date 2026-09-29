# Issue #30 completion report: scan-attempt diagnostics

Branch: `feat/backend-scan-attempt-diagnostics-issue-30`.
Worktree: `/home/vboxuser/nutriguard-worktrees/feat-backend-scan-attempt-diagnostics-issue-30`
(isolated `git worktree`, based on `origin/main` at `65a8e68`; the live
bind-mounted checkout at `/home/vboxuser/nutrigard` was never modified).
Not pushed, not merged, not deployed — local commits only, per the
task's explicit scope.

Full contract: `docs/SCAN_ATTEMPT_DIAGNOSTICS.md`. This report covers
what was built, how it was verified, and what remains.

## Commits on this branch

1. `6038024` — contract publication: header middleware/generation,
   ingestion endpoint, schemas, README/`.env.example`/`openapi.json`
   updates, 35 tests, a fix to a latent validation-error-handler bug.
2. (this commit) — operator CLI, its tests, and this report.

Run `git log origin/main..HEAD` on the branch for the exact set.

## What was delivered

- **Header contract** (`app.core.scan_attempt`, wired into
  `app.main`): `X-Scan-Attempt-Id` / `X-Scan-Request-Sequence` on all
  three `/scan/*` endpoints. Validated, never blocks/delays a scan,
  server-generates a compatible id for a missing/malformed header
  (with best-effort recent-id self-collision avoidance), echoed on
  success and every handled failure path (validation/auth/rate-limit/
  any `AppError`).
- **Client diagnostic-event ingestion**
  (`POST /api/v1/scan-diagnostics/client-events`,
  `app.api.v1.scan_diagnostics` + `app.schemas.scan_diagnostics`):
  authenticated, bounded (≤20 events, ≤16 KiB), strictly-enumerated
  (no free text anywhere), per-user replay dedup (bounded, process-
  local LRU), written into the existing bounded/multi-process-safe
  scan-diagnostics journal, origin-tagged, sharing the existing
  storage cap rather than adding an unbounded new store.
- **Existing journal extended, not replaced**: backend-authored lines
  from `app.api.v1.scan` gained `origin`/`scanAttemptId`/
  `requestSequence`, purely additively — no existing field renamed,
  no existing `stage` value changed.
- **Read-only operator CLI**
  (`python -m app.seed.scan_attempt_trace <id>`): looks up one attempt
  across the current journal file and its retained rotated backup(s)
  under a shared lock (safe against a concurrent writer), chronological
  by server receipt time, distinguishes `origin`, flags an
  `ambiguousCollision` instead of silently merging records that might
  belong to different attempts/owners (this journal deliberately does
  not store a user id — see the contract doc §5), reports malformed
  retained lines and the rotation/retention caveat explicitly. JSON by
  default, `--human` for plain text.
- **Docs**: `docs/SCAN_ATTEMPT_DIAGNOSTICS.md` (full contract), README
  §4 (new endpoint row) and new §15, `.env.example` (every new
  setting, documented).
- **`openapi.json`**: regenerated inside a throwaway
  `python:3.12-slim` container against the pinned `requirements.txt`
  (matching CI, per README's own stated method for this exact step —
  avoids the environment-drift noise a locally-installed newer
  FastAPI/Pydantic would otherwise introduce into the diff). Diff is
  purely additive: the new path, the new request/response schemas,
  nothing else changed or removed.
- **One incidental bug fix**: `app.main`'s `RequestValidationError`
  handler crashed (500, not 422) whenever a custom `@field_validator`
  raised a plain `ValueError`, because pydantic's default
  `errors()` embeds the raw exception object in each error's `ctx`,
  which is not JSON-serializable. This was latent before (nothing in
  the codebase exercised that path over real HTTP) and is now fixed
  and regression-tested by the new metrics-allowlist validator.

## Verification

Full suite, Python 3.14 (globally installed packages — newer than the
pinned `requirements.txt`, but no functional incompatibility observed;
`openapi.json` was still regenerated on pinned Python 3.12 specifically
to avoid this affecting the committed contract snapshot):

```
python -m pytest -q
744 passed, 16 skipped, 7 warnings in 18.50s
```

The 16 skipped and the pre-existing 3 warnings are unchanged from the
`origin/main` baseline (also captured before this task started: 701
passed, 16 skipped). 43 new tests added, zero regressions:

- `tests/unit/test_scan_attempt.py` (10): header parsing/validation,
  id generation format, self-collision avoidance, leading-zero
  round-trip, malformed/oversized input handling.
- `tests/unit/test_scan_diagnostics.py` (+4): client-event dedup cache
  (first-submission, resubmission, per-user scoping, bounded eviction).
- `tests/unit/test_scan_attempt_trace.py` (7): the operator CLI —
  bad-id rejection, no-journal-file, cross-file matching with correct
  chronological ordering, request-id filtering, malformed-line
  handling, ambiguous-collision flagging, human-readable rendering.
- `tests/integration/test_scan_attempt_headers.py` (10): header echo
  on success/validation-failure/auth-failure/rate-limit-failure,
  leading-zero round-trip over real HTTP, missing/malformed header
  handling, journal correlation fields, scoping to only the three scan
  routes.
- `tests/integration/test_scan_diagnostics_client_events.py` (12):
  auth required, happy path, per-user dedup (including cross-user
  non-interference), batch/body size bounds, malformed
  `scanAttemptId`, metrics allowlist (unknown key, out-of-bounds
  value), journal storage content (including the privacy assertion
  that no user id is ever written), disabled-diagnostics
  acknowledge-without-persist behavior, and the endpoint's own rate
  limit.

**No database/schema change was made** (this feature is entirely
log-file-based — see the contract doc §6 for why), so the acceptance
checklist's "real disposable PostgreSQL tests if DB/schema changes" and
"migration upgrade/downgrade/upgrade if any" do not apply; this is
stated explicitly rather than silently skipped. Docker was available
and used, but only to regenerate `openapi.json` on pinned dependencies
— the live `nutriguard-backend-db-1`/`nutriguard-backend-backend-1`
containers were never touched, restarted, or connected to.

## Explicitly not done (scope decision, not an oversight)

Issue #30's acceptance list asks for entry/exit/failure instrumentation
of the stages *inside* `app.services.food_analysis` (provider/cache
lookup, image/text extraction, ingredient segmentation, identity/
language resolution, catalog persistence, nutrition/scoring decision)
as individually-observed events, beyond the router's existing single
coarse `stage` marker per request.

This was deliberately not attempted in this delivery. `food_analysis.py`
is a 2100+ line, heavily-reviewed, correctness-critical file — its own
existing code already documents a deliberate boundary ("Coarse, honest
stages this router layer actually observes -- never a guess at what
happened deeper inside `app.services.food_analysis`'s own pipeline,
which this endpoint layer does not instrument", `app/api/v1/scan.py`).
Threading attempt-id-scoped diagnostic calls through its five top-level
orchestration functions (and, further, their internal helpers) without
altering any control flow or return value is possible but is a
materially separate, higher-risk change from the rest of this delivery,
and was not worth destabilizing that file to rush within this task.

This is recorded here — not silently dropped — as the one remaining
item from the issue's acceptance list, for the product owner to
explicitly authorize (and scope precisely) as a follow-up, per
CLAUDE.md §8's rule against silently resolving a contract/acceptance
gap.

## Files changed (both commits combined)

```
.env.example
README.md
app/api/v1/router.py
app/api/v1/scan.py
app/api/v1/scan_diagnostics.py          (new)
app/core/config.py
app/core/exceptions.py
app/core/rate_limit.py
app/core/scan_attempt.py                (new)
app/core/scan_diagnostics.py
app/main.py
app/schemas/scan_diagnostics.py         (new)
app/seed/scan_attempt_trace.py          (new)
docs/SCAN_ATTEMPT_DIAGNOSTICS.md        (new)
docs/SCAN_ATTEMPT_DIAGNOSTICS_COMPLETION_REPORT.md  (new, this file)
openapi.json
tests/integration/test_scan_attempt_headers.py       (new)
tests/integration/test_scan_diagnostics_client_events.py  (new)
tests/unit/test_scan_attempt.py         (new)
tests/unit/test_scan_attempt_trace.py   (new)
tests/unit/test_scan_diagnostics.py
```

## Recommended next step

1. Product owner reviews the published contract
   (`docs/SCAN_ATTEMPT_DIAGNOSTICS.md`) and this branch/SHA.
2. Codex integrates Android's diagnostic-event uploader against the
   verified request/response schemas here (the local outbox and header
   sending described as already implemented on
   `feat/android-scan-attempt-diagnostics` should not need to change
   shape — the header names, id format, and event schema match what
   that work already assumed, cross-checked against the issue's shared
   contract text).
3. If deeper `food_analysis.py` stage instrumentation is still wanted,
   scope it as its own explicitly-authorized follow-up task, separate
   from this contract/ingestion delivery.
4. No push, merge, or deploy happens until separately authorized.
