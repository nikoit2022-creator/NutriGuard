# Scan-attempt tracing and client diagnostic event ingestion (issue #30)

Backend contract for numeric scan-attempt correlation and bounded,
client-submitted scan diagnostics. This is the authoritative, verified
contract Android/Codex should integrate against — every schema, enum,
limit and behavior below is backed by real code and passing tests on
`feat/backend-scan-attempt-diagnostics-issue-30`, not a design proposal.

Not part of `NutriGuard_API_Contract.md` — this is new, additive
surface for issue #30, documented here and in `openapi.json` per
CLAUDE.md §8 (contract-deviation rule) even though it does not change
any *existing* contract endpoint.

## 1. Scope and non-goals

- One user-initiated scan attempt (barcode scan, camera capture,
  gallery image, or typed/pasted OCR text) gets exactly one
  `scanAttemptId`, generated before acquisition starts. A new
  capture or a manual retry is a **new** attempt (new id). An
  *automatic* transport retry of the same capture keeps the same
  attempt id and increments `requestSequence`. An additional product
  photo for the same attempt is a new attempt, not a new product
  identity.
- The id is a correlation aid for debugging, **not** authentication
  and **not** a guaranteed globally-unique key (see §3.4). It must
  never gate access to anything, never be treated as an idempotency
  key for business data (products, scan history, etc.), and a
  collision must never silently mix two different attempts/owners.
- No automatic analysis, alerting or "auto-fixing" is built on top of
  this data. It exists to be read by an operator/AI on request, only
  when investigating a reported problem.
- No public retrieval endpoint exists or is planned — only an
  authenticated write path (§4) and an operator-only, read-only CLI
  that runs on the deployment host, not over HTTP (§6).

## 2. Header contract (all three scan endpoints)

Applies to exactly `POST /api/v1/scan/barcode`, `POST
/api/v1/scan/ocr-text`, and `POST /api/v1/scan/label-image` — no other
endpoint, including the ingestion endpoint in §4. Implemented in
`app.core.scan_attempt` and wired in via `app.main`'s
`scan_attempt_context_middleware`.

| Header | Direction | Format |
|---|---|---|
| `X-Scan-Attempt-Id` | request (optional) + response (always present on the three scan endpoints) | exactly 16 ASCII decimal digits (`^[0-9]{16}$`), e.g. `0042135790246813` |
| `X-Scan-Request-Sequence` | request (optional) | a positive integer as ASCII digits, no leading zero, at most 9 digits (`^[1-9][0-9]{0,8}$`) |

### 2.1 Request side

- `X-Scan-Attempt-Id`: generated client-side, cryptographically
  random, exactly 16 decimal digits, **leading zeros are significant
  and must be preserved** (it is a string, never encoded as a JSON
  number, timestamp, or device identifier). Display as four groups of
  four digits; copy all 16 digits without spaces.
- `X-Scan-Request-Sequence`: starts at `1` for the first request of an
  attempt; the client increments it by one on each automatic transport
  retry of that same attempt (not on a new attempt). Omitted on a
  legacy client.

### 2.2 Server-side validation (never blocks or fails a scan)

These headers are diagnostics-only. A missing or malformed value is
**always** silently substituted with a safe default — it never causes
a 4xx, never delays the request, and the raw invalid value is **never
logged or echoed back**.

- `X-Scan-Attempt-Id` missing, wrong length, or containing any
  non-digit character → the server generates a fresh compatible id
  (§2.3) and uses/echoes that instead. This is also how a legacy
  client (one that never sends the header at all) gets a valid id.
- `X-Scan-Request-Sequence` missing or not matching the pattern above
  → treated as `1`.

### 2.3 Server-side generation and collision handling

`app.core.scan_attempt.generate_scan_attempt_id()`:

- Draws a cryptographically random 16-digit decimal string
  (`secrets.randbelow`, zero-padded — leading zeros preserved).
- Checks it against a bounded, process-local, in-memory set of the
  last `SCAN_ATTEMPT_ID_RECENT_CACHE_SIZE` (default 500) ids this
  *same process* has itself generated, retrying up to 5 times on a
  hit before accepting the last draw regardless.

**This is a best-effort reduction of immediate self-collision, not a
uniqueness guarantee.** It does not check across worker processes, does
not check against ids clients supplied, and does not persist across a
restart. The 16-digit space (10^16) makes an accidental collision
already very unlikely; this cache only guards against the pathological
case of two consecutive generations in the same process drawing the
same value. Residual collision risk (two different attempts, possibly
different owners, ending up with the same id) is real and explicitly
not treated as impossible anywhere in this system — see §5's
ambiguous-collision handling.

### 2.4 Response side

The accepted-or-generated id is echoed back as `X-Scan-Attempt-Id` on:

- a normal success response;
- a validation failure (422);
- an authentication failure (401);
- a rate-limit rejection (429);
- any other `AppError`-derived failure (404, 500, etc.).

All of these are produced by an exception handler running *inside* the
same middleware `call_next` call that sets the header, so the header
is present on every response FastAPI itself renders. It is **not**
guaranteed on a failure that never reaches this backend at all (a
client-side network error, a proxy timeout, an LB-level rejection) —
those cannot echo anything, by construction.

## 3. What changed in the existing scan-diagnostics journal

`app.core.scan_diagnostics` (the pre-existing, opt-in, bounded,
multi-process-safe JSONL journal — see its own module docstring) is
extended, not replaced. Every diagnostic line `app.api.v1.scan` already
wrote now additionally carries:

```jsonc
{
  "origin": "backend",
  "scanAttemptId": "0042135790246813",
  "requestSequence": 1,
  // ...all pre-existing fields, unchanged: requestId, operation,
  // barcode, stage, outcome, errorCode, dataSource, durationMs, ...
}
```

No existing field name, value, or the coarse `stage` vocabulary the
router already wrote (`request_validated`, `content_type_validated`,
`image_read`, `analysis_complete`, `response_built`, etc.) was renamed
or reinterpreted — this is a purely additive change, verified by the
full existing test suite passing unchanged (see the completion report).

**Scope note:** deeper entry/exit instrumentation of the stages
*inside* `app.services.food_analysis` (provider/cache lookup,
image/text extraction, ingredient segmentation, identity/language
resolution, catalog persistence, nutrition/scoring decision as
individually-observed events, distinct from the router's existing
coarse stage marker) is **not implemented in this delivery** — see the
completion report's "remaining work" section. The router's own
existing stage marker is real, is preserved, and now also carries the
attempt id; nothing here fabricates instrumentation that does not
exist.

## 4. `POST /api/v1/scan-diagnostics/client-events`

Additive, authenticated endpoint for Android to report bounded,
already-locally-queued diagnostic events. Implemented in
`app.api.v1.scan_diagnostics`; schemas in
`app.schemas.scan_diagnostics`. Never touches the database. See
`openapi.json` for the generated, authoritative schema — this section
explains it.

- **Auth**: same `Authorization: Bearer <access token>` as every other
  authenticated endpoint (`app.api.deps.get_current_user_id`). 401
  `INVALID_TOKEN` if missing/invalid, identical to every other
  endpoint's error shape.
- **Rate limit**: `RATE_LIMIT_DIAGNOSTICS_PER_HOUR` (default 120/hour),
  keyed the same way as every other endpoint (per-token, else
  per-IP). 429 `RATE_LIMIT_EXCEEDED` on breach, standard envelope.
- **Batch bound**: `1..SCAN_DIAGNOSTICS_CLIENT_EVENTS_MAX_BATCH`
  events per request (default 20). More → 422 `VALIDATION_ERROR`.
- **Body size bound**: `SCAN_DIAGNOSTICS_CLIENT_EVENTS_MAX_BODY_BYTES`
  (default 16 KiB), enforced on the raw request body **before**
  Pydantic parses it (checked via `Content-Length` first, then the
  actual byte count read). Exceeding it → 413 `PAYLOAD_TOO_LARGE`.

### 4.1 Request: `{"events": [ClientDiagnosticEvent, ...]}`

| Field | Type | Constraint |
|---|---|---|
| `eventId` | string | canonical UUID string (any version); the replay-dedup key (§4.3) |
| `scanAttemptId` | string | exactly 16 ASCII digits, same format as §2 |
| `requestSequence` | int \| null | `null` before any network attempt; else `1..999999999` |
| `sequence` | int | `0..100000`; this event's position in the client's own local outbox for this attempt (a different axis from `requestSequence`) |
| `occurredAt` | ISO-8601 datetime string | the **client's own clock** — untrusted for cross-system ordering (§5), used only for display/relative ordering within one device |
| `stage` | enum, see §4.2 | client-observable lifecycle point |
| `outcome` | enum: `STARTED`, `SUCCEEDED`, `FAILED`, `RETRIED`, `CANCELLED` | this event's own outcome — a different, per-*event* axis from the pre-existing per-*request* `outcome` string (`success`/`partial`/`failed`) `app.api.v1.scan` already writes; both can appear in the same journal file, disambiguated by `origin` |
| `durationMs` | int \| null | `0..86400000` (24h) |
| `appVersion` | string | `1..32` chars, `^[A-Za-z0-9_.+-]+$` |
| `reasonCode` | enum \| null, see §4.2 | |
| `metrics` | object \| null | allowlisted numeric keys only (§4.2) |

No free-text field exists anywhere in this schema — see §5 for the
enforced privacy allowlist.

### 4.2 Enums

`ClientDiagnosticStage` (`app.schemas.scan_diagnostics.ClientDiagnosticStage`)
— a strict subset of the full stage vocabulary: Android may only ever
report what it itself observed.

```
ATTEMPT_START       CAPTURE_COMPLETE     UPLOAD_START
UPLOAD_RETRY         RESPONSE_RECEIVED    TERMINAL_SUCCESS
TERMINAL_FAILURE     TERMINAL_CANCELLED
```

`ClientDiagnosticReasonCode` — closed, allowlisted, never arbitrary
text:

```
NETWORK_TIMEOUT   NETWORK_OFFLINE   AUTH_EXPIRED     AUTH_RETRY
SERVER_ERROR      RATE_LIMITED      VALIDATION_REJECTED
USER_CANCELLED    DECODE_ERROR      UNKNOWN
```

`metrics` allowlist (key → inclusive bound; any other key, or a value
outside its bound, is rejected with 422):

| Key | Bounds |
|---|---|
| `imageWidthPx` / `imageHeightPx` | 0–20000 |
| `imageBytes` / `uploadBytes` | 0–104857600 (100 MiB) |
| `retryCount` | 0–1000 |
| `ocrConfidencePct` | 0–100 |
| `outboxDepth` | 0–10000 |
| `queuedMs` | 0–86400000 |

At most 8 metric entries per event.

### 4.3 Response, acknowledgment and dedup

`{"acceptedEventIds": [...], "duplicateEventIds": [...]}` — every
`eventId` from the request appears in exactly one of the two lists
(never both, never neither). A hard error (auth/validation/rate-limit)
uses the standard error envelope instead, exactly like every other
endpoint.

Dedup is by `(authenticated user, eventId)`, so one user's event ids
can never be reported as duplicates of another user's, and vice versa
— never silently mixed. It is a **bounded, process-local, in-memory
LRU** (`SCAN_DIAGNOSTICS_CLIENT_EVENTS_DEDUP_CACHE_SIZE`, default
2000 entries, oldest evicted first once full) — **not** persisted, and
**not** shared across worker processes. A resubmission of the same
event handled by a different worker process, or after a process
restart, is not recognized as a duplicate and will be accepted (and
journaled) again. This is a documented, accepted limitation of a
diagnostics-only system, not a correctness bug — nothing here is
authoritative business data.

A duplicate event is acknowledged (`duplicateEventIds`) but **not**
re-written to the journal, so a client's retry-until-acked outbox
strategy cannot inflate reported event counts within the dedup window.

### 4.4 Enablement (mirrors the existing diagnostics flag)

Persistence to the journal is gated by the same
`SCAN_DIAGNOSTICS_ENABLED` flag the pre-existing backend journal
already uses (see §6 for enablement details). When it is `false`
(the default): the endpoint still authenticates, validates, rate-limits
and deduplicates normally, and still acknowledges well-formed events in
`acceptedEventIds` — it just does not write anything to disk. This is
deliberate: it keeps a client's outbox-retry logic simple (it always
gets a real ack/duplicate answer) without silently growing storage when
diagnostics are off, and mirrors exactly how
`record_scan_diagnostic` already no-ops when disabled.

## 5. Privacy allowlist and untrusted-input handling

Enforced entirely by the strict, enum/bounded-numeric-only schema in
§4.1 — there is no free-text field to police at write time. Explicitly
**forbidden** anywhere in this system, by construction: images,
OCR/ingredient text, barcode values, filenames/URIs, credentials,
device identifiers, raw request/response bodies, raw exception
strings.

- **Client events are untrusted.** Every journal line written by §4
  carries `"origin": "android"`. `occurredAt` is the *device's* clock
  and must never be used for cross-system ordering or to establish
  causality with a backend-side event — only the backend's own
  `timestamp` (server clock, already written by
  `record_scan_diagnostic`) is authoritative for that.
- **Backend-authored events** (§3) carry `"origin": "backend"`, a
  server timestamp, and the deployed `backendVersion` — already true
  of the pre-existing journal, unchanged.
- **A client can only ever report its own observations, never
  authoritative backend facts.** Nothing here treats a client-submitted
  event as proof that a backend-side step did or did not run.
- **Ambiguous collisions.** The journal does not (and, per the
  pre-existing module's own documented privacy design, must not) carry
  the authenticated user id (see `app.core.scan_diagnostics`'s module
  docstring — user ids were already excluded before this task, for the
  backend-authored lines too). This means a lookup by `scanAttemptId`
  alone cannot, on its own, distinguish two different owners whose
  attempt ids happen to collide. The operator CLI (§6) must flag this
  possibility explicitly in its output rather than silently merging
  records — it has no way to rule it out, so it must say so.
- **Rotation means history may be incomplete.** The shared journal
  (§6) is size-bounded; a missing event or stage for a given
  `scanAttemptId` may simply mean it rotated out, never proof that the
  step did not happen.

## 6. Storage, retention, and enablement

Client events are written into the **same** bounded, multi-process-safe
JSONL journal the pre-existing backend scan diagnostics already use —
`app.core.scan_diagnostics.record_scan_diagnostic`, same file
(`SCAN_DIAGNOSTICS_PATH`), same cap
(`SCAN_DIAGNOSTICS_MAX_BYTES`, default 1 MiB) and the same rotation
policy (`SCAN_DIAGNOSTICS_BACKUP_COUNT`, default 1 backup generation),
protected by the same cross-process `fcntl.flock`-based locking. This
is a deliberate reuse, not a new, separate, unbounded store: **client
events count against the existing total budget** rather than adding an
unbounded second store, exactly as required. `origin` (`"backend"` /
`"android"`) disambiguates the two kinds of line sharing that one
budget.

A practical consequence, stated plainly rather than left implicit: a
burst of client-submitted events can now evict older backend-authored
lines sooner than before (and vice versa), because they share one
capped file. Given the existing 1 MiB default and the ≤16 KiB/≤20-event
per-request bound in §4, this requires a sustained high submission rate
to matter in practice; it is called out here so an operator changing
the size defaults understands the trade-off.

**Enablement** (development/testing only, matching the pre-existing
journal's own posture — off by default in production):

```bash
SCAN_DIAGNOSTICS_ENABLED=true
SCAN_DIAGNOSTICS_PATH=/var/log/nutriguard/scan-diagnostics.jsonl
SCAN_DIAGNOSTICS_MAX_BYTES=1048576
SCAN_DIAGNOSTICS_BACKUP_COUNT=1
```

New settings this task adds (`app/core/config.py`, all with defaults,
see `.env.example`):

```bash
RATE_LIMIT_DIAGNOSTICS_PER_HOUR=120
SCAN_ATTEMPT_ID_RECENT_CACHE_SIZE=500
SCAN_DIAGNOSTICS_CLIENT_EVENTS_MAX_BATCH=20
SCAN_DIAGNOSTICS_CLIENT_EVENTS_MAX_BODY_BYTES=16384
SCAN_DIAGNOSTICS_CLIENT_EVENTS_DEDUP_CACHE_SIZE=2000
```

## 7. Example: an agent/operator debugging one reported attempt

Given a user reports "scan attempt 0042135790246813 failed" (the id
Android displayed/copied for them):

1. Confirm diagnostics are enabled on the target environment and
   locate `SCAN_DIAGNOSTICS_PATH` (plus its `.1` backup, if present).
2. Use the read-only operator CLI (once delivered — see the completion
   report's remaining-work section for its current status) to filter
   both the current file and the one backup for that exact 16-digit id,
   across both `origin` values, and print a chronological timeline.
3. Read the timeline as: client-observed lifecycle (`origin: android`)
   interleaved with backend-observed stages (`origin: backend`),
   correlated only by `scanAttemptId`/`requestSequence` — never treat a
   gap as proof a step did not happen (rotation, §5), and never treat a
   shared id across seemingly different sessions as proof of the same
   owner without independent confirmation (no user id is stored here by
   design).
4. This is read-only, on-request investigation — never an automated
   trigger for a fix, a score change, or any other side effect.
