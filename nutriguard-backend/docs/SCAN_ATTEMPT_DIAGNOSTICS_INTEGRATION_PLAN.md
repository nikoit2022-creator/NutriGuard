# Scan-attempt diagnostics (issue #30): integration plan

Audience: Codex, implementing in a SEPARATE branch/PR off current `origin/main`.
Basis: read-only investigation of `origin/main` (2da53e3) and
`origin/feat/backend-scan-attempt-diagnostics-issue-30` (98b2401, merge-base 65a8e689).
No tests were run (read-only task); anything not read from code is marked UNVERIFIED.

## 1. Summary

- Main already has a bounded, multi-process-safe JSONL journal
  (`app/core/scan_diagnostics.py`, `fcntl.flock`, rotation) and one summary line
  per scan request, keyed only by the server `requestId` (a UUID4 from the
  middleware in `app/main.py`, returned as `X-Request-ID`). Main has NO 16-digit
  attempt id, NO client-event endpoint, NO pipeline-stage tracing, NO owner scope.
- The old branch adds all of those (header contract, pipeline tracing, SQLite
  dedup ledger, HMAC owner scope, read-only CLI), but as one coherent block
  built on a base that predates main's 30 commits. Those 30 commits are almost
  entirely OpenFoodTox and do NOT touch any diagnostics-related file: `scan.py`,
  `main.py`, `food_analysis.py`, `scan_diagnostics.py`, `config.py`,
  `rate_limit.py`, `router.py` are byte-identical between merge-base and main.
  Only `openapi.json` (+5 lines, `ownerApprovedWithoutReview`) and
  `docs/CODEX_HANDOFF.md` differ.
- `git merge-tree --write-tree` shows exactly ONE textual conflict
  (`nutriguard-backend/docs/CODEX_HANDOFF.md`); `openapi.json` auto-merges.
  So reimplementing is cheap, but do NOT cherry-pick wholesale: the old design
  has real gaps against the stated requirement (section 5) that must be fixed.
- Key gaps: (1) client events only cover a small stage/reason vocabulary and
  nothing for camera permission, barcode decode, DNS/TLS/compression, token
  refresh failure; (2) pipeline tracing is coarse (provider discovery is one
  stage, Gemini-to-fallback handled errors are invisible, requests rejected
  before `_diagnostic_base` (auth 401, 429, 422, empty barcode) leave no
  journal line); (3) client events are not bound to a server attempt (any
  authenticated user can write any 16-digit id, scoped only by pseudonymous
  owner), and there is no read API (CLI on the host only); (4) the SQLite ledger
  is safe only for workers sharing one local volume.

## 2. Current contract on main (verified)

Files: `app/core/scan_diagnostics.py`, `app/api/v1/scan.py`, `app/main.py`,
`app/core/config.py`, `tests/unit/test_scan_diagnostics.py`,
`tests/integration/test_scan_diagnostics_endpoints.py`,
`docker-compose.prod.yml`, `docker-compose.yml`, `.env.example`.

- Journal: `record_scan_diagnostic(**fields) -> None`. Disabled unless
  `SCAN_DIAGNOSTICS_ENABLED=true` (default false). One compact JSON line per
  write: `timestamp`, `backendVersion`, `pid` plus caller fields (None dropped).
  Path `SCAN_DIAGNOSTICS_PATH` (default `/var/log/nutriguard/scan-diagnostics.jsonl`),
  `SCAN_DIAGNOSTICS_MAX_BYTES` 1 MiB, `SCAN_DIAGNOSTICS_BACKUP_COUNT` 1.
  Rotation happens inside one critical section under an exclusive `flock` on
  `<journal>.lock`; POSIX-only (lazy `import fcntl`, silently no-ops on Windows
  because the broad `except Exception` swallows the ImportError). Returns None, so
  callers cannot know whether a line was written.
- Writers: only `app/api/v1/scan.py` for `POST /scan/barcode`, `/scan/ocr-text`,
  `/scan/label-image`. Fields: `requestId`, `operation`, `barcode`, coarse `stage`
  (`request_validated` / `content_type_validated` / `image_read` /
  `analysis_complete` / `response_built`), `outcome` (`success|partial|failed`),
  `errorCode`, `dataSource`, `durationMs`, ingredient-language counts, translation
  fields. Excludes images, OCR text, model output, credentials, user ids, health
  profile. Note it DOES record the barcode value (the old branch kept this too).
  `food_analysis` is not instrumented ("do not invent stage information").
- Headers: `X-Request-ID` (UUID4) on every response, set in
  `request_context_middleware` (`app/main.py`); also bound into structlog contextvars.
  No `X-Scan-Attempt-Id`, no `X-Scan-Request-Sequence`.
- Endpoints: no diagnostics router. `app/api/v1/router.py` includes auth, scan,
  products, ingredients, health_profile, scan_history only.
- Auth/rate limit: scan routes use `get_current_user_id` (JWT) and `SCAN_RATE`
  (`30/hour`). `slowapi` limiter keyed by last 32 chars of the Bearer token else
  IP; storage Redis when `REDIS_ENABLED` else `memory://` (`app/core/rate_limit.py`).
- Size limit: `/scan/label-image` does `contents = await image.read()` then compares
  to `MAX_IMAGE_SIZE_BYTES` (read first, check after; there is no streaming bound
  on main). The old branch's "streaming size limit" applies only to the
  client-events body, not to image upload.
- Deployment: `docker-compose.prod.yml` runs `uvicorn ... --workers 4`, mounts the
  named volume `scan_diagnostics` at `/var/log/nutriguard` (single host, single
  container). Dev compose uses `--reload` (one process). Entrypoint
  (`docker/entrypoint.sh`) waits for Postgres, runs `alembic upgrade head`, seeds,
  then `exec "$@"`. Redis 7 and Postgres 16 are already in compose.
- Error envelope: `{"error": {code, message, details, timestamp}}` for AppError,
  422, 429, 500 (`app/main.py`). Wire fields are camelCase (root `AGENTS.md`,
  backend `CLAUDE.md` section 7); `openapi.json` is a checked-in snapshot to
  regenerate on contract change.

## 3. What the old branch adds (by commit)

Range `65a8e689..98b2401`, 5 commits, all verified via `git log --stat`.

1. `6038024` feat: publish scan-attempt diagnostics contract (18 files, +1614).
   Contract/header + client-event endpoint (first version).
   - `app/core/scan_attempt.py`: `X-Scan-Attempt-Id` (regex `^[0-9]{16}$`),
     `X-Scan-Request-Sequence` (`^[1-9][0-9]{0,8}$`); invalid/missing id replaced by
     a server-generated `secrets.randbelow(10**16)` zero-padded id with a
     process-local recent-id cache (`SCAN_ATTEMPT_ID_RECENT_CACHE_SIZE=500`);
     never rejects a request.
   - `app/main.py`: `scan_attempt_context_middleware` limited to the 3 scan paths;
     echoes `X-Scan-Attempt-Id` on every response; the 422 handler strips `ctx`
     (non-serializable ValueError objects).
   - `app/api/v1/scan_diagnostics.py`, `app/schemas/scan_diagnostics.py`:
     `POST /api/v1/scan-diagnostics/client-events` (auth, rate limited via
     `DIAGNOSTICS_RATE`), batch max 20, body max 16 KiB, strict enum/allowlist
     schema (no free text), `origin="android"` lines in the same journal.
   - `exceptions.py` (`PayloadTooLargeError` 413), `rate_limit.py`, `config.py`,
     `router.py`, `openapi.json` (+230), `.env.example`, README, docs
     `SCAN_ATTEMPT_DIAGNOSTICS.md`, tests (`test_scan_attempt*`,
     `test_scan_attempt_headers`, `test_scan_diagnostics_client_events`).
2. `9d4e91e` feat: read-only CLI (6 files). `app/seed/scan_attempt_trace.py`
   (`python -m app.seed.scan_attempt_trace <id> [--human] [--request-id]`),
   completion report, tests. CLI tool + docs.
3. `497a4ab` docs only (completion report SHAs). Skip.
4. `85abdfb` fix: Codex round-2 review (23 files, +2370/-479). The substantive one.
   - Pipeline tracing: `app/core/pipeline_trace.py` (ContextVar bound per
     request, `enter_stage`/`stage_success`/`record_pending_failure`, LIFO stack)
     and ~30 call sites added to `app/services/food_analysis.py`; scan.py binds the
     context in `_diagnostic_base` and calls `record_pending_failure` in each
     except block. Stages: provider_cache_lookup, extraction,
     ingredient_segmentation, identity_language_resolution, catalog_persistence,
     nutrition_scoring_decision, response_construction.
   - Client-event ledger: `app/core/client_event_ledger.py` (SQLite WAL,
     `BEGIN IMMEDIATE` reserve -> journal write -> commit/release; stale
     RESERVED pruned after 30 s; COMMITTED capped at 20000 rows). Response now
     returns `acceptedEventIds` / `duplicateEventIds` / `retryableEventIds`;
     `record_scan_diagnostic` now returns bool.
   - Owner scoping: `app/core/owner_scope.py`: `HMAC-SHA256(JWT_SECRET, user_id)[:32]`
     written as `ownerScope` on every backend and android line; CLI reports
     distinct owner scopes as a definite collision.
   - Streaming body-size enforcement on client events (`request.stream()`,
     then assigns `request._body`; relies on a Starlette private attribute, see
     risks).
   - Schema stage/outcome/reason enums expanded (IMAGE_PREPARATION, PARSING,
     PERSISTENCE, PARTIAL, INTERRUPTED); CLI shared-lock snapshot reads.
   - Docs (CODEX_HANDOFF, DIAGNOSTICS, README), tests: `test_pipeline_trace*`,
     `test_client_event_ledger`, `test_owner_scope`, `test_scan_attempt_trace`.
5. `98b2401` docs only. Skip.

Classification: contract/header = 6038024 (+ main.py part of 85abdfb none);
pipeline tracing = 85abdfb; ledger = 85abdfb; owner scoping = 85abdfb; CLI = 9d4e91e
and 85abdfb; docs = 497a4ab, 98b2401 and doc parts of the others.

## 4. Drift and conflicts

`git merge-tree --write-tree origin/main origin/feat/...issue-30` (tree 6b33d56d...):

- `CONFLICT (content): nutriguard-backend/docs/CODEX_HANDOFF.md` (main added ~1256
  lines of OpenFoodTox history; the branch added 124 lines). Resolve by keeping
  main's file and appending a new entry; never take the branch's version.
- `nutriguard-backend/openapi.json` auto-merged (main's `ownerApprovedWithoutReview`
  hunk vs the branch's new paths/schemas). Textually clean, but the merged file
  must be REGENERATED, not trusted (check with a script that dumps
  `create_app().openapi()` and diff).
- Semantic drift in the diagnostics files: none found. `git diff 65a8e689 origin/main`
  shows zero changes to `scan.py`, `main.py`, `food_analysis.py`,
  `scan_diagnostics.py`, `config.py`, `rate_limit.py`, `router.py`. Main's changes
  are OpenFoodTox scripts/docs/seed, `ingredient_localization` model+migration
  (`...1e2f3_ingredient_localization_owner_approval.py`), `schemas/ingredient.py`,
  `load_seed.py`, and Android pilot files. UNVERIFIED: whether new migrations or
  seed behavior change the test baseline; run the full suite before and after.
- Hazards for hand porting: (a) `food_analysis.py` call sites are positional
  statement-boundary edits: line numbers differ but context is unchanged, so
  patches from `85abdfb` apply, though correctness of enter/success pairing
  must be re-verified (e.g. the `ProductNotFoundError` raised after
  `await db.commit()` in the enrichment path closes catalog_persistence but not the
  stages entered earlier, which is intended); (b) any new `food_analysis` branch
  merged since would silently be uninstrumented (none changed, verified);
  (c) Windows: `fcntl` unavailable, tests that exercise the real journal must
  skip or run under Linux/Docker; (d) alembic head moved; the ledger needs no
  migration if kept in SQLite but WILL if moved to Postgres.

## 5. Requirement gaps

### 5a. Server stages and handled errors carrying the attempt id

Old branch coverage (verified from diff): barcode path covers only
provider_cache_lookup (lookup AND provider discovery are one stage; the
per-provider `outcome.attempts` from `barcode_discovery.discover_product` is not
emitted), catalog_persistence, nutrition_scoring_decision,
response_construction. OCR/label paths cover extraction, ingredient_segmentation,
identity_language_resolution, catalog_persistence, scoring, response. Missing:
- Handled errors that do not propagate: Gemini failure then fallback
  (`GeminiUnavailableError` at `food_analysis.py` ~177/181 and ~1430/1448) stay
  inside one `extraction` stage that reports success; a tester cannot see that
  Gemini failed and the fallback ran. Also the broad handlers at ~1743 and ~2090
  (UNVERIFIED what they absorb; read before designing events).
- Provider-level events: each provider attempt (Open Food Facts, GS1 resolver,
  UPCitemdb: `app/integrations/barcode_providers/*`) with outcome code and
  duration, no payloads.
- Translation: whole-label and per-ingredient translation are inside
  identity_language_resolution; per-pass outcome only appears in the summary line
  (already `translationAttempted`, `ingredient...Count` on main).
- Early rejections before `_diagnostic_base`: empty barcode (`ValidationAppError`),
  `ocr-text` < 3 chars, auth 401, 422 body validation, 429 rate limit, 413. The
  response header is echoed (middleware), but NO journal line is written, so a
  tester cannot correlate "server rejected before the pipeline". Fix: write a
  line from the middleware/exception handlers (attempt id + status + error code
  only), including for auth/rate-limit failures.
- Rate-limited and auth-failed requests carry the header but are also
  invisible to the ownerScope (user unknown); label them `ownerScope: unknown`.
- Attempt id not in structlog contextvars: bind `scan_attempt_id` next to
  `request_id` so ordinary app logs correlate too (not done in the old branch).
- DB persistence failure: covered only through `record_pending_failure` when the
  exception reaches scan.py; rollback/commit exceptions swallowed by handlers
  (UNVERIFIED) would not be seen.

### 5b. Client failures before the request reaches the server

The old branch has an endpoint (`POST /scan-diagnostics/client-events`) and an
allowlisted schema. Stage enum: ATTEMPT_START, CAPTURE_COMPLETE, IMAGE_PREPARATION,
UPLOAD_START, UPLOAD_RETRY, RESPONSE_RECEIVED, PARSING, PERSISTENCE,
TERMINAL_SUCCESS/FAILURE/CANCELLED. Outcome: STARTED, SUCCEEDED, PARTIAL, FAILED,
RETRIED, CANCELLED, INTERRUPTED. Reason codes: NETWORK_TIMEOUT, NETWORK_OFFLINE,
AUTH_EXPIRED, AUTH_RETRY, SERVER_ERROR, RATE_LIMITED, VALIDATION_REJECTED,
USER_CANCELLED, DECODE_ERROR, UNKNOWN. Metrics: 8 allowlisted numeric keys with
bounds (image size, retry count, outbox depth, ...). Privacy: no free text, no
barcode/filename/URI/device id/token; `appVersion` regex-bounded. Deduplicated by
`(ownerScope, eventId)`.

Gaps for the stated requirement:
- Reason codes cannot distinguish camera permission denied, camera unavailable,
  barcode decode failure (only generic DECODE_ERROR), DNS failure vs connect
  failure vs TLS failure (only OFFLINE/TIMEOUT), image compression failure (no
  reason; stage exists), token refresh failure (AUTH_EXPIRED/AUTH_RETRY only, no
  "device re-auth failed"), storage/outbox write failure, HTTP-level non-2xx
  categories. Add enumerated reasons (closed vocabulary, still no text), and
  stages `PERMISSION`/`SCANNER` (or reuse ATTEMPT_START with reasons).
- The endpoint requires JWT. If the failure is auth itself (token missing,
  refresh and device re-auth failed), the event cannot be delivered. Decide:
  keep authenticated-only and queue in a local outbox until auth recovers
  (old design), and state that "auth failed" events are delivered later; or add
  a tightly limited unauthenticated path (NOT recommended: abuse surface). Open
  question 1.
- Offline failures can only be reported after connectivity returns; the
  outbox must persist across process death (Android-side).
- Events are not tied to a server attempt: the server never validates that
  `scanAttemptId` was ever issued to that owner. A user can inject events under an
  arbitrary id, but only into their own `ownerScope`, so cross-owner pollution
  is limited to ids that collide; the CLI flags multiple owner scopes as a
  collision. Acceptable, but document that client lines are untrusted and the
  device clock (`occurredAt`) is untrusted.
- Journal line count: no per-owner/per-attempt cap on stored events other
  than the 20/batch, 16 KiB/body and `DIAGNOSTICS_RATE`; the dedup ledger cap
  (20000) is not a quota on journal lines.

### 5c. Multi-process safety, retention

- Journal: safe across the 4 uvicorn workers on ONE host/volume via `flock`
  (verified on main; same mechanism the old branch reuses). Not safe on NFS or
  across containers on different hosts (UNVERIFIED for network filesystems). Not
  functional on Windows hosts.
- Ledger: SQLite WAL with `BEGIN IMMEDIATE` is correct across worker processes
  sharing the same local volume; same single-host limitation. It lives in
  `/var/log/nutriguard/...sqlite3` on the same named volume. WAL on Docker named
  volumes is fine; on bind-mounted network or Windows/macOS Docker Desktop shares
  WAL can misbehave (UNVERIFIED). Alternatives: Redis (already deployed; atomic
  `SET NX EX` gives bounded reservation with TTL, no cleanup job, multi-host
  safe; but needs `REDIS_ENABLED=true` and the dev/test fallback is memory) or
  Postgres (adds a table + Alembic migration + cleanup job; strongest durability;
  diagnostics would then touch the app DB, which the old design deliberately
  avoided). Recommendation: keep the file journal for the lines (bounded by
  size), move the dedup/reservation ledger to Redis with TTL (retention = TTL,
  no growth), with the SQLite ledger as the fallback when Redis is disabled.
  Needs owner decision (open question 2).
- Retention: journal is bounded to `MAX_BYTES * (1 + BACKUP_COUNT)` = 2 MiB by
  default; at that size and 4 workers writing ~10+ lines per scan (stage events),
  retention could be minutes under load, undermining "tester looks up an id later".
  Raise to a documented value for test builds and add time-based documentation
  (retention is size-based, not time-based). Ledger: reserved rows 30 s, committed
  rows capped by count (no age TTL); with Redis use an explicit TTL (suggest 24-72 h).
- Pipeline tracing writes one journal line per stage enter/success/failure
  (~2 per stage, ~10-14 per scan) each taking the flock; acceptable only because
  diagnostics are opt-in. Consider emitting one `stages` summary line per request
  (list of `{stage,event,durationMs,errorCode}`) at completion to cut lock
  traffic; keep a failure line written immediately.

### 5d. Owner scoping

- `ownerScope = HMAC-SHA256(JWT_SECRET, user_id)[:32]` (old branch). Pros:
  stable and non-reversible without the secret. Cons: rotating `JWT_SECRET`
  silently changes every scope (history un-attributable; document), and the
  same secret signs JWTs (key reuse). Prefer a dedicated
  `DIAGNOSTICS_OWNER_HMAC_KEY` env var with a safe fallback.
- Read side: there is no HTTP read endpoint; the CLI reads the file on the host
  and shows all owners. "Ownership checks on read" therefore means operator-only
  access. If a read endpoint is ever added it MUST filter by the caller's own
  `ownerScope` and return 404 (not 403) for other owners; the old branch has no
  such endpoint and none should be added without explicit approval (open
  question 3).
- Backend lines written before auth resolved (401/429) have no ownerScope.

### 5e. Rate limits, size limits, id generation

- Client events: `DIAGNOSTICS_RATE` (`RATE_LIMIT_DIAGNOSTICS_PER_HOUR`, UNVERIFIED
  default value; read `config.py` on the branch), keyed by the last 32 chars
  of the Bearer token. Batch <= 20, body <= 16 KiB, per-metric numeric
  bounds, 8 metrics max. Verify that the limit is high enough for an outbox flush
  after offline use (a burst of a long outbox could 429).
- The 413 check reads the stream and then sets the private `request._body`;
  this works with the pinned Starlette but is fragile. Add a test that fails
  loudly if Starlette changes (and run on the pinned version in `requirements.txt`).
- 16-digit id: `secrets.randbelow(10**16)`, leading zeros preserved; validation by
  regex, invalid header replaced silently and flagged (`id_header_was_invalid`,
  but check whether that flag is surfaced to the journal: UNVERIFIED). Collision
  space 10^16; the recent-cache only dedups within one process. Accept the
  documented residual collision risk, rely on `(ownerScope, attemptId)` as the
  real key, and never use the id for authorization.
- Image upload on `/scan/label-image` reads the whole body before checking size
  (main). Unchanged by the old branch. Out of scope for #30 but note it; do not
  remove or weaken any existing limit.

### 5f. Wire contract / OpenAPI

- New JSON fields and headers must be camelCase (headers in the existing
  `X-Scan-Attempt-Id` style). The old schemas use `ORMModel` aliasing (verify
  camelCase alias generator in `app/schemas/common.py`).
- Contract change needs: docs section, regression tests, regenerated
  `openapi.json`. The response header is not representable by `response_model`;
  add `responses={...: {"headers": ...}}` or document it in
  `docs/SCAN_ATTEMPT_DIAGNOSTICS.md`.
- The CORS middleware has `allow_headers=["*"]` but does NOT set
  `expose_headers`; irrelevant to native Android, but add
  `X-Scan-Attempt-Id` to `expose_headers` if any browser client is ever used.

## 6. Android contract to implement (out of scope here; Codex implements)

Current Android scan flow (read-only, `android-app/app/src/main/java/com/example/`):
`ui/screens/ScanHomeScreen.kt` (GmsBarcodeScanning code scanner, camera launcher
with a temp file), `data/repository/FoodAnalysisRepository.kt`,
`data/remote/NutriGuardApiService.kt` (`scanBarcode`, `scanOcrText`, `scanLabelImage`;
bitmap JPEG compress quality 80; OkHttp `barcodeHttpClient` with `callTimeout`),
`data/auth/AuthInterceptor.kt` (adds Bearer; on 401 refresh/device re-auth and one
retry with `X-NutriGuard-Retried`), typed `BarcodeScanException`s
(`BarcodeNetworkException`, `BarcodeTimeoutException`, `BarcodeServerException`).
There is no existing Android reference to `X-Scan-Attempt-Id` or `X-Request-ID`
(grep verified).

What Android must emit:
1. At the user action (tap scan / capture), generate one attempt id: 16 decimal
   digits from `SecureRandom`, zero-padded string. Keep it for the whole attempt
   (barcode attempt, the follow-up label-image upload for the same product may be a
   NEW attempt; decide and document). Show it in the UI for the tester.
2. On every scan HTTP request (`/scan/barcode`, `/scan/ocr-text`, `/scan/label-image`)
   send `X-Scan-Attempt-Id: <id>` and `X-Scan-Request-Sequence: <n>` (1-based, +1 for
   each automatic transport retry incl. the AuthInterceptor 401 retry; interceptor
   must preserve the attempt id and increment the sequence).
3. Read `X-Scan-Attempt-Id` from every response, including errors; if it differs from
   the sent value (server replaced an invalid id), display the server value.
4. Emit client events to `POST /api/v1/scan-diagnostics/client-events` (authenticated,
   batch <= 20, body <= 16 KiB, camelCase): `eventId` (UUID), `scanAttemptId`,
   `requestSequence` (null before any request), `sequence` (local monotonically
   increasing per attempt), `occurredAt` (ISO-8601), `stage`, `outcome`, `durationMs`,
   `appVersion`, `reasonCode`, `metrics` (allowlisted numbers). Events at: attempt
   start; camera permission denied/camera unavailable; scanner cancelled by user;
   barcode decoded or decode failure; image capture complete; compression
   success/failure with sizes; upload start; each retry; response received (status
   class only); parse failure; terminal success/failure/cancelled/interrupted.
5. Persist events in a local, size-bounded outbox (survives process death), flush
   after the scan and on next launch, honor `acceptedEventIds`/`duplicateEventIds`
   (drop) and `retryableEventIds` (keep), back off on 429, never block or fail a
   scan on diagnostics errors, drop oldest when the outbox exceeds its cap.
6. Privacy: never send barcode values, raw label text, image bytes/URIs/file names,
   tokens, health profile, device identifiers or exception messages.
Backend prerequisite: items 4-5 need the reason-code/stage expansion in commit 3
below; Android must not invent unlisted enum values (server returns 422).

## 7. Required commits (ordered, backend branch off `origin/main`)

Branch name suggestion: `feat/backend-scan-attempt-diagnostics-v2`. Each commit
independently green (`python -m pytest -q`), updates `docs/CODEX_HANDOFF.md`
(append, do not overwrite).

1. Attempt-id contract + middleware (port from `6038024`: `scan_attempt.py`,
   middleware, config keys, 422 `ctx` fix). Changes: bind `scanAttemptId` and
   `requestSequence` into structlog contextvars; echo the header on every
   response; write a journal line for early rejections (401/422/429/413,
   empty-barcode) with `outcome=failed`, `errorCode`, `ownerScope` unknown when
   unauthenticated. Tests: header present on success, 4xx, 422, 429, 500;
   invalid header replaced and counted; no raw header value logged; id format.
2. Owner scope + journal API (port `owner_scope.py`; change
   `record_scan_diagnostic` to return bool; add `origin`, `scanAttemptId`,
   `ownerScope` to scan.py base). Redesign: dedicated HMAC key setting. Tests:
   scope stable per user, different per user, raw user id never in the journal,
   journal line includes scope.
3. Client-event endpoint and schema (port schema/router from `6038024` + part of
   `85abdfb`). Redesign: expand stage/reason enums for pre-request failures
   (camera permission, camera unavailable, scanner cancelled, barcode decode,
   DNS, connect, TLS, compression, upload, token refresh failed, outbox write);
   keep allowlist, no free text; camelCase; regenerate `openapi.json`. Tests:
   each enum accepted, unknown rejected (422 envelope serializable), size/batch
   limits (including chunked body without Content-Length), privacy test posting
   forbidden fields (`rawText`, `barcode`, `token`, `imageBase64`) -> 422,
   auth required, rate limit.
4. Dedup ledger (port `client_event_ledger.py`; choose backend per the owner's
   answer to open question 2: preferred Redis `SET NX EX` with TTL and SQLite
   fallback when Redis is disabled). Tests: reserve/commit/release; stale reclaim;
   cap/TTL; the multi-process test in section 8; truthful ack when journal
   disabled or write fails.
5. Pipeline tracing (port `pipeline_trace.py` + `food_analysis.py` call sites from
   `85abdfb`). Redesign: split provider discovery into per-provider events
   (provider name, outcome code, duration only), add events for handled
   Gemini-to-fallback and the broad handlers at ~1743/~2090, emit every event
   with `scanAttemptId`, consider one summary-per-request line. Tests: success path
   stage order for each of the 3 endpoints; failure at each stage gives a failure
   event with the id; handled Gemini failure recorded as `fallback` with
   success; tracing disabled/no-context is a no-op; concurrent requests do not
   leak context (asyncio gather test); trace failure never changes the response.
6. Operator CLI (port `scan_attempt_trace.py`). Keep shared-lock snapshot and
   owner-collision reporting; add `--owner-scope` filter; no writes. Skip if Windows
   operators are expected (fcntl) or make it Linux-only and document it.
7. Docs/OpenAPI: `docs/SCAN_ATTEMPT_DIAGNOSTICS.md` (rewrite from the old doc,
   corrected: retention is size-based, residual collision risk, untrusted client
   clock/events, auth-failure delivery limitation), `.env.example`, README,
   compose (`scan_diagnostics` volume already exists; add the ledger/Redis settings),
   regenerated `openapi.json`, `CODEX_HANDOFF.md` entry.
8. (Android PR, strictly after 1-7 are merged and deployed) implement section 6.

Skip entirely: `497a4ab`, `98b2401` (docs-only SHA bookkeeping), the old completion
report, and the old branch's `CODEX_HANDOFF.md`.

Dependency order: 1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7 -> deploy -> Android. Commit 5
only needs 1-2 and can proceed in parallel with 3-4. Android can start against
the OpenAPI after commit 3 merges, but must not ship until the production backend
has deployed 1-5.

Migration/config needs: no Alembic migration if the ledger stays in Redis/SQLite;
new env vars (journal enable, owner HMAC key, ledger path or Redis TTL, rate limit,
body/batch caps) in `.env.example`; production must set
`SCAN_DIAGNOSTICS_ENABLED=true` and a larger `SCAN_DIAGNOSTICS_MAX_BYTES`
deliberately; never commit `.env`.

## 8. Acceptance tests

Must exist and pass (backend `python -m pytest -q`; run Linux-only ones in Docker):
- Header round trip: a request with `X-Scan-Attempt-Id` returns the same id on
  200, 401, 404 (`PRODUCT_NOT_FOUND`), 422, 429, 500; a missing/invalid id yields a
  server-generated 16-digit id; no raw bad value appears in logs or the journal.
- Server correlation: for each of the 3 endpoints, every journal line produced by one
  request (summary + each stage event + provider events + handled-error events)
  contains the same `scanAttemptId`, `requestSequence`, `requestId` and `ownerScope`.
- Handled-error visibility: simulate Gemini unavailable and fallback success; simulate
  one provider timeout then another provider success; simulate DB commit failure;
  simulate translation unreliable. Each emits a distinct, id-tagged event, and the
  response behavior is unchanged.
- Failure-before-server test: submit a client-event batch (id never seen by any scan
  request) containing `camera permission denied`, `decode failure`, `compression
  failure`, `DNS failure`, `TLS failure`, `token refresh failed` events; response lists
  all in `acceptedEventIds`; journal lines are `origin=android`, carry the
  attempt id and owner scope; the CLI shows them for the id with no server events.
  Also a test where the journal is disabled -> all `retryableEventIds`.
- Multi-process ledger test: spawn N >= 4 real OS processes (multiprocessing
  `spawn`) that concurrently submit the same `(owner, eventId)` batch 50 times
  against one shared ledger/journal path (or Redis test container); assert
  exactly one `accepted`, the rest `duplicate` or `retryable`, exactly one journal
  line per event id, no torn/merged lines (every line parses as JSON), rotation
  under concurrent writes loses no committed line beyond the configured
  retention, and a process killed between reserve and commit leaves a reclaimable
  reservation after the timeout. (Existing main tests do not cover multi-process;
  UNVERIFIED whether `tests/unit/test_scan_diagnostics.py` has a concurrent test.)
- Owner scoping: user A cannot cause dedup/ack for user B's same `eventId`; the same
  `scanAttemptId` under two owners is reported as a definite collision by the CLI;
  raw user id never appears in the journal or ledger.
- Privacy: posting `rawText`, `barcode`, `token`, image data, `healthProfile`, unknown
  metric keys, or over-long strings is rejected (422) and nothing is journaled; scan
  summary lines contain no OCR text (existing main tests, keep them).
- Bounds: oversize body with/without `Content-Length`, chunked, > 20 events, metrics
  out of range -> 413/422; journal size never exceeds the configured cap; ledger
  rows/keys expire per TTL/cap.
- Disabled mode: `SCAN_DIAGNOSTICS_ENABLED=false` gives no files created, scans work,
  headers still returned.
- Diagnostics failure never breaks scanning (unwritable path, lock error, tracing
  exception).
- OpenAPI: `openapi.json` equals the generated schema (script check); client-event
  schema fields are camelCase.

## 9. Risks and open questions

Risks:
- Wholesale cherry-pick would import docs that overstate guarantees and an
  unreviewed `CODEX_HANDOFF.md` conflict; port per commit as above.
- Size-based retention can evict evidence quickly under load; tracing multiplies
  lines per scan.
- `fcntl`/SQLite-WAL assumptions fail on Windows hosts or network volumes; a
  single-host deployment is assumed (compose evidence: one backend service, 4
  workers, one named volume). Horizontal scaling breaks both the journal and the
  SQLite ledger.
- `request._body` private-attribute reliance, the broad `except Exception` in the
  journal masks real I/O errors silently (consider a rate-limited warning).
- Diagnostics record barcode numbers (also on main); confirm this is acceptable
  as non-personal, otherwise hash them.
- JWT secret reuse for the owner HMAC and its rotation effect.
- Pre-request auth failure events may never be deliverable.

Open questions for the owner:
1. Accept authenticated-only client events with a local outbox, or allow a limited
   unauthenticated path for token/auth failures?
2. Ledger backend: Redis (preferred, multi-host safe, TTL) vs keep SQLite file vs
   Postgres table (needs migration)? Is single-host deployment guaranteed?
3. Is an HTTP read endpoint wanted (owner-scoped), or is the operator CLI enough?
4. Required retention in hours/days and acceptable disk budget for the journal.
5. Should the id be per user tap, or reused across barcode-then-label-image
   follow-up for the same product?
6. Is recording raw barcode values in diagnostics acceptable?
