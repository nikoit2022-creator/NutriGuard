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

## Round 2 (Codex review): what changed

A first review of this contract found seven blockers before Android
integration could proceed. All seven are addressed in this revision;
the sections below are updated in place rather than kept as a separate
changelog, but the headline changes are:

1. **Internal pipeline tracing** (§8, new): `app.services.food_analysis`'s
   internal stages (provider/cache lookup, extraction, ingredient
   segmentation, identity/language resolution, catalog persistence,
   nutrition/scoring decision, response construction) now emit real
   entry/success/failure events, not just the router's own coarse
   per-request `stage`.
2. **Truthful acknowledgment** (§4.3): `acceptedEventIds` now means
   "durably written to the journal", never "we said yes before trying".
   A NEW `retryableEventIds` list carries anything not (yet) durable.
3. **Cross-process dedup** (§4.3, §6): replay dedup and the
   accept/duplicate decision are now coordinated through a bounded,
   cross-process-safe SQLite ledger (`app.core.client_event_ledger`),
   not a single worker process's in-memory cache.
4. **Pseudonymous owner scoping** (§5): every journal line now carries
   a stable, one-way `ownerScope` (never the raw user id), so the
   operator CLI can tell two different owners' attempts apart.
5. **Operator CLI fixes** (§7): one shared lock across the current file
   and every rotated backup (no more per-rotated-file `.N.lock` files),
   one consistent snapshot per invocation, and safe handling of
   non-object JSON / malformed records / invalid field types /
   unreadable files.
6. **Streaming request-size enforcement** (§4): the byte limit is now
   enforced against a bounded stream, not a first-fully-buffer-then-check.
7. **Contract reconciliation** (§4.2): new stage/outcome enum values
   for partial results, interrupted attempts, image preparation,
   parsing and client-side persistence — see below for exactly which
   values are new.

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
  that runs on the deployment host, not over HTTP (§7).

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

**Round 2 update:** deeper entry/exit instrumentation of the stages
*inside* `app.services.food_analysis` (provider/cache lookup,
image/text extraction, ingredient segmentation, identity/language
resolution, catalog persistence, nutrition/scoring decision,
response construction) **is now implemented** — see §8. The router's
own existing coarse stage marker above is unchanged and still real;
§8's events are a separate, finer-grained layer, never a replacement.

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
  (default 16 KiB), enforced by `app.core.body_size_limit.BodySizeLimitMiddleware`
  — a pure ASGI middleware that counts bytes off the raw ASGI `receive`
  channel for exactly this route, **before Starlette's routing (and
  therefore FastAPI's own body buffering) ever runs**. It aborts the
  instant the running total exceeds the limit, so a missing/lying
  `Content-Length` or a chunked-transfer body is bounded exactly the
  same way as an honestly-declared oversized one. Exceeding it → 413
  `PAYLOAD_TOO_LARGE`.

  (Round 2's fix enforced this from a FastAPI route dependency instead
  — Round 3 found that was too late to matter: FastAPI's own request
  handler calls `await request.body()` to parse this route's declared
  Pydantic body parameter *before* resolving any `Depends()` on that
  same route, and Starlette's `Request.body()` has no size bound of its
  own, so the entire oversized body was already fully buffered by the
  time that dependency ran. Moving the check to ASGI middleware, ahead
  of routing entirely, is the only way to bound it before FastAPI ever
  sees the request.)

### 4.1 Request: `{"events": [ClientDiagnosticEvent, ...]}`

| Field | Type | Constraint |
|---|---|---|
| `eventId` | string | canonical UUID string (any version); the replay-dedup key (§4.3) |
| `scanAttemptId` | string | exactly 16 ASCII digits, same format as §2 |
| `requestSequence` | int \| null | `null` before any network attempt; else `1..999999999` |
| `sequence` | int | `0..100000`; this event's position in the client's own local outbox for this attempt (a different axis from `requestSequence`) |
| `occurredAt` | ISO-8601 datetime string | the **client's own clock** — untrusted for cross-system ordering (§5), used only for display/relative ordering within one device |
| `stage` | enum, see §4.2 | client-observable lifecycle point |
| `outcome` | enum: `STARTED`, `SUCCEEDED`, `PARTIAL`, `FAILED`, `RETRIED`, `CANCELLED`, `INTERRUPTED` | this event's own outcome — a different, per-*event* axis from the pre-existing per-*request* `outcome` string (`success`/`partial`/`failed`) `app.api.v1.scan` already writes; both can appear in the same journal file, disambiguated by `origin` |
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
ATTEMPT_START       CAPTURE_COMPLETE     IMAGE_PREPARATION
UPLOAD_START         UPLOAD_RETRY         RESPONSE_RECEIVED
PARSING              PERSISTENCE          TERMINAL_SUCCESS
TERMINAL_FAILURE     TERMINAL_CANCELLED
```

`IMAGE_PREPARATION`/`PARSING`/`PERSISTENCE` are new in Round 2 (Codex
review, finding 7): the original set had no way to represent client-side
image compression/resize before upload, client-side response parsing
after upload, or the client's own local outbox write — each is now its
own explicit stage instead of being silently folded into
`CAPTURE_COMPLETE` or `RESPONSE_RECEIVED`.

`outcome` also gained `PARTIAL` (a `labelScanRequired`-style partial
result — never report this as `FAILED`, and never invent a `SUCCEEDED`
to avoid using it) and `INTERRUPTED` (the attempt was genuinely cut off
— app killed/backgrounded, process death — as distinct from a
user-initiated `CANCELLED` or a network/server `FAILED`).

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

### 4.3 Response, acknowledgment and dedup (Round 2: truthful acknowledgment)

`{"acceptedEventIds": [...], "duplicateEventIds": [...], "retryableEventIds": [...]}`
— every `eventId` from the request appears in **exactly one** of the
three lists (never in more than one, never in none). A hard error
(auth/validation/rate-limit) uses the standard error envelope instead,
exactly like every other endpoint.

- **`accepted`**: durably written to the journal by the time this
  response was sent. Safe for the client to drop from its local outbox.
- **`duplicate`**: already durably written by an EARLIER accepted
  submission of the same `(owner, eventId)`. Also safe to drop.
- **`retryable`** (Round 2, new): **not** durably written — diagnostics
  are currently disabled, the journal write itself failed (I/O error),
  or a concurrent in-flight retry of the same event is being processed
  elsewhere right now. The client **must** keep it in its outbox and
  retry later; it was never falsely acknowledged. This is the fix for
  the Round 1 bug: `acceptedEventIds` used to be decided BEFORE the
  journal write was even attempted, so a disabled/failed write was
  still reported as accepted — a client following that ack would
  delete an event that was never actually stored.

Dedup and truthful acknowledgment are now coordinated through a single
atomic reserve-then-commit-or-release protocol
(`app.core.client_event_ledger`, backed by a small, bounded, **cross-process-safe
SQLite ledger** — see §6):

1. `reserve(ownerScope, eventId)` atomically claims the pair, or reports
   it as already `duplicate` (durably committed earlier) or `in_flight`
   (another concurrent attempt is mid-write right now — reported
   `retryable`, never treated as a duplicate, never allowed to also
   write).
2. Only the caller that WON the reservation attempts the real journal
   write. On success, the reservation is committed (NOW it becomes a
   recognized duplicate for any future resubmission). On failure — the
   write raised, or `SCAN_DIAGNOSTICS_ENABLED` is `false` — the
   reservation is released so a future retry can win it again; the
   event is reported `retryable`, never `accepted`.

This replaces the Round 1 in-memory, single-worker-process-only dedup
cache, which could never coordinate across the multiple Uvicorn worker
*processes* production actually runs (`--workers 4`) — two retries of
the same event landing on different workers could both have been
treated as "not yet seen" and both journaled.

Dedup is scoped by `(ownerScope, eventId)` (§5) — one owner's event ids
can never be reported as duplicates of another owner's, and vice versa.
The ledger is bounded (`SCAN_DIAGNOSTICS_CLIENT_EVENTS_DEDUP_MAX_ROWS`,
default 20000 rows, oldest pruned first) and self-healing (a reservation
never committed or released within
`SCAN_DIAGNOSTICS_CLIENT_EVENTS_RESERVATION_TIMEOUT_SECONDS`, default
30s — its owning worker crashed mid-write — is reclaimed by a future
retry rather than permanently blocking the event id). Once a committed
row is pruned, that event id is no longer recognized as a duplicate if
resubmitted — a documented, bounded-storage limitation, not a
correctness bug: nothing here is authoritative business data.

A duplicate event is acknowledged (`duplicateEventIds`) but **not**
re-written to the journal, so a client's retry-until-acked outbox
strategy cannot inflate reported event counts within the dedup window.

### 4.4 Enablement (Round 2: no false acknowledgment while disabled)

Persistence to the journal is gated by the same
`SCAN_DIAGNOSTICS_ENABLED` flag the pre-existing backend journal
already uses (see §6 for enablement details). When it is `false`
(the default): the endpoint still authenticates, validates, and
rate-limits normally, and still runs dedup (a previously-`accepted`
event, from back when diagnostics WERE enabled, is still correctly
reported `duplicate`) — but every event that would otherwise be a fresh
write is now reported **`retryable`, never `acceptedEventIds`** (Round
1 bug, fixed — see §4.3). The client's outbox-retry logic still gets a
real, immediate answer every time; it just correctly reflects that
nothing was persisted, so the client knows to keep retrying rather than
silently losing the event.

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
- **Pseudonymous owner scoping (Round 2, new — `app.core.owner_scope`).**
  The journal still never carries the raw authenticated user id (see
  `app.core.scan_diagnostics`'s module docstring), but EVERY journal
  line (both `origin: backend` and `origin: android`) now carries a
  stable `ownerScope`: an HMAC-SHA256 of the user id keyed by the
  server's own `JWT_SECRET`, truncated to 32 hex characters. Two events
  from the SAME authenticated user always carry the SAME scope; two
  different users carry different scopes; nobody holding only the
  journal file can recover the original user id from it (the secret
  never leaves the server). A record with no `ownerScope` at all (an
  older retained line from before this field existed) is treated
  explicitly as `UNKNOWN_OWNER_SCOPE` — never silently attributed to,
  or excluded from, any real owner.
- **Ambiguous collisions — now partly DEFINITIVE, not just heuristic.**
  Two different owners sharing one `scanAttemptId` used to be only a
  suspicion (a heuristic: more than one backend request with
  `requestSequence=1`). With `ownerScope` now on every line, the
  operator CLI (§7) can tell the two cases apart precisely: more than
  one DISTINCT, KNOWN `ownerScope` sharing a `scanAttemptId` is
  conclusive proof of a collision (or a shared device/token) — never
  "the same owner". The old heuristic still applies, unchanged, when
  every matched record shares one scope or carries none at all (nothing
  in that case can rule out two independent attempts by the SAME
  owner).
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

**A second, separate bounded store (Round 2, new): the client-event
ledger.** `app.core.client_event_ledger` — a small SQLite database
(WAL mode), at `CLIENT_EVENT_LEDGER_PATH`, deliberately **not** the
JSONL journal above and **not** the application's own Postgres database
(never touched by this feature at all). It exists purely to make
dedup/acknowledgment (§4.3) atomic and cross-process-safe — the journal
remains the one place an operator actually reads event *content* from.
Bounded the same way: `SCAN_DIAGNOSTICS_CLIENT_EVENTS_DEDUP_MAX_ROWS`
rows total (oldest pruned first), and a `RESERVED` row (a persistence
attempt that started but never committed or released) is reclaimed
after `SCAN_DIAGNOSTICS_CLIENT_EVENTS_RESERVATION_TIMEOUT_SECONDS`.
Deleting this file is safe at any time (worst case: a few genuinely
duplicate lines get journaled again before the ledger rebuilds its
state) — it is coordination state, not the record of what happened.

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
# Round 2: the cross-process-safe ledger (replaces Round 1's
# SCAN_DIAGNOSTICS_CLIENT_EVENTS_DEDUP_CACHE_SIZE, an in-memory-only
# setting that no longer exists).
CLIENT_EVENT_LEDGER_PATH=/var/log/nutriguard/scan-diagnostics-client-events.sqlite3
SCAN_DIAGNOSTICS_CLIENT_EVENTS_RESERVATION_TIMEOUT_SECONDS=30
SCAN_DIAGNOSTICS_CLIENT_EVENTS_DEDUP_MAX_ROWS=20000
```

## 7. Operator CLI (`app.seed.scan_attempt_trace`) — Round 2 fixes

Given a user reports "scan attempt 0042135790246813 failed" (the id
Android displayed/copied for them):

1. Confirm diagnostics are enabled on the target environment and
   locate `SCAN_DIAGNOSTICS_PATH` (plus its `.1` backup, if present).
2. Run the read-only operator CLI:
   `python -m app.seed.scan_attempt_trace 0042135790246813` (add
   `--human` for a plain-text timeline instead of JSON, or
   `--request-id <uuid>` to narrow to one internal request).
3. Read the timeline as: client-observed lifecycle (`origin: android`)
   interleaved with backend-observed stages (`origin: backend`),
   correlated only by `scanAttemptId`/`requestSequence` — never treat a
   gap as proof a step did not happen (rotation, below), and use the
   `ownerScopes`/`ambiguousCollision` fields (§5) rather than assuming a
   shared id across seemingly different sessions is the same owner.
4. This is read-only, on-request investigation — never an automated
   trigger for a fix, a score change, or any other side effect.

**Locking, corrected (Round 1 had three bugs here, all fixed):**

- **One shared lock for every file, not one per file.** The CLI now
  takes exactly the SAME sibling `<journal>.lock` file the writer
  itself uses (`app.core.scan_diagnostics`'s `_append_locked`), in
  `LOCK_SH` (shared) mode, for the current file AND every retained
  rotated backup. Round 1 computed a DIFFERENT lock path per file
  (`<journal>.N.lock` for a rotated backup) — a lock the writer never
  takes at all, so reading a rotated file was never actually
  synchronized with a concurrent rotation, and running the CLI left
  behind a stray, writer-irrelevant `.N.lock` file on disk. Neither
  happens any more.
- **One consistent snapshot per invocation, not one lock-acquire per
  file.** The lock is held across the ENTIRE read of every candidate
  file in one invocation, so a rotation can no longer happen in between
  reading the current file and reading a backup (which could previously
  double-count or silently drop a record that moved between them
  mid-read).
- **The lock's effect on a writer, corrected.** A SHARED lock never
  blocks another concurrent reader, and never blocks a concurrent
  writer for longer than THIS invocation's own read of every candidate
  file — bounded by the journal's total configured size
  (`SCAN_DIAGNOSTICS_MAX_BYTES * (1 + SCAN_DIAGNOSTICS_BACKUP_COUNT)`),
  **not** "at most one append" as Round 1's docstring claimed (an
  overstatement once the CLI can hold the lock across multiple files —
  see the module docstring for the precise, corrected claim).
- If the lock file cannot be opened at all, the CLI still reads
  best-effort WITHOUT it — but the report says so explicitly via
  `lockAcquired: false` (and a `--human` warning) rather than silently
  claiming a consistent read it did not get.

**Robustness (Round 2, new):** a syntactically valid JSON line that
isn't a JSON *object* (a bare array/string/number) now counts as
malformed rather than crashing the CLI; a record with an unexpected
field type (e.g. a non-string `timestamp`/`origin`/`requestId`) is
handled defensively rather than crashing sort/grouping; a file that
exists but can't be read (permissions, a race) is reported per-file
(`filesChecked[].unreadable`) instead of aborting the whole lookup.

**Owner isolation (Round 2, new — see §5):** the report now includes
`ownerScopes` (every distinct known `ownerScope` among the matched
records) and `unknownOwnerScopePresent`. More than one distinct known
scope is a DEFINITE collision (`ambiguousCollision: true`, a precise
note naming it as conclusive) — a strictly stronger signal than the
old `requestSequence == 1`-counting heuristic, which still applies as a
fallback when every matched record shares one scope or carries none.

## 8. Internal pipeline tracing (Round 2, new — `app.core.pipeline_trace`)

Codex review round 2, finding 1: the router-level `stage` field (§3)
only ever observes what `app.api.v1.scan` itself does (validate,
call `food_analysis`, build the response) — it never sees what happens
*inside* `app.services.food_analysis`'s own pipeline. `app.core.pipeline_trace`
adds that finer-grained layer: real entry/success/failure events for
the stages `food_analysis` actually executes, journaled the same way
(`origin: "backend"`, correlated by `scanAttemptId`/`requestId`), with
two new fields — `pipelineStage` (one of `provider_cache_lookup`,
`extraction`, `ingredient_segmentation`, `identity_language_resolution`,
`catalog_persistence`, `nutrition_scoring_decision`,
`response_construction`) and `pipelineEvent` (`enter` / `success` /
`failure`, the last carrying `errorCode`).

- **Never fabricated.** A stage's `enter` event is written only at the
  exact point its own code starts running; not every entry point
  exercises every stage (e.g. a pure barcode cache-hit lookup never
  runs `extraction`/`ingredient_segmentation`/`identity_language_resolution`
  at all — no event for those stages appears for that request).
- **`success` vs `failure` is determined centrally**, in
  `app.api.v1.scan`'s own existing exception handlers, not inside
  `food_analysis.py` itself: whichever stage(s) were entered but never
  reached their own success call are exactly the ones still running
  when an exception propagated out — each gets a `failure` event
  (innermost first, since this codebase deliberately commits several
  stages' work in one outer transaction, so stages can nest rather than
  strictly sequence). If every internal stage already succeeded before
  a LATER failure (e.g. the router's own response serialization), no
  pipeline-stage failure is invented for it.
- **A business outcome is not a pipeline failure.** A `labelScanRequired`
  partial result (§ — see `_label_scan_required_details`) is raised
  only AFTER the stages that actually ran (lookup, persistence) already
  recorded their own `success` — so it produces zero `pipelineEvent:
  "failure"` records, only genuine `success` ones. A pipeline `failure`
  event means a stage's own code genuinely did not complete (e.g. both
  Gemini and the deterministic fallback raised) — see
  `tests/integration/test_pipeline_trace_events.py` for both scenarios
  proven against the real endpoints.
- **Correlation is request-scoped**, via a plain `contextvars.ContextVar`
  bound once per request — never explicitly cleared, since each HTTP
  request is handled by its own fresh asyncio Task (both Starlette's
  routing and uvicorn's per-request-cycle task creation guarantee this),
  so two concurrent requests' events can never cross-attribute.
- Business logic and control flow inside `app.services.food_analysis`
  are completely unchanged by this: every call site is a plain,
  synchronous, best-effort `enter_stage(...)`/`stage_success(...)` call
  inserted between existing statements — no new `try`/`except`, no
  reordering, no altered return values.
