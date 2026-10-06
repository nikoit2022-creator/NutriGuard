# Integration report: scan-attempt diagnostics (issue #30) + ingredient identity follow-up

Branch: `integration/scan-diagnostics-ingredient-identity` (task-specific; not merged, not deployed).
Nothing in this task touched the live checkout, containers, migrations, secrets or live data.
Read-only live inspection was limited to `docker inspect`/`ls`/`printenv` of non-secret settings.

## 1. Commit availability and ancestry (verified, not assumed)

| Checkpoint | SHA | Where it exists |
|---|---|---|
| `origin/main` (base, re-verified with `git ls-remote` on 2026-10-06) | `2da53e349d1830fd37920562e5c91440d5e3e47f` | origin |
| Diagnostics branch tip `feat/backend-scan-attempt-diagnostics-issue-30` | `98b24015ee8c93c4315ba78fe7f4b8148279cfec` (matches the previously inspected SHA) | origin; merge-base with main `65a8e689` |
| Ingredient base fix `fix/backend-cyrillic-e-number` | `a915243dd4243f0468bdc25d35e9482aed751be6` | origin |
| Ingredient follow-up 1 | `39fac43d467f6803e7e929d12882199e7ade1493` | **Windows-local only** (not on origin, not on the VM) |
| Ingredient follow-up 2 | `e69ebf0441d074d0c16ddddc886d9e2b98b59de1` (parent `39fac43`) | **Windows-local only** |

Both follow-up commits were found in the local Windows worktree (`...\scratch-2026-10-05-5a5cf2\nutriguard-wt`,
branch `fix/backend-cyrillic-e-number-followup`) and integrated from there unchanged; nothing was recreated or substituted.
No Android diagnostics code exists on `origin` (checked every remote branch for `X-Scan-Attempt-Id`) or in any VM worktree
(15 searched, read-only), so Android compatibility below is verified against the specified Android behaviour, not Android source.
The live VM checkout `/home/vboxuser/nutrigard` is still on `fix/backend-cyrillic-e-number` at `a915243` (re-checked, unchanged).
It bind-mounts `./app` and `./alembic` and runs `uvicorn --reload` (single process, dev compose command).

## 2. Integration (exact SHAs)

Built in an isolated worktree from `origin/main`:

1. `9220ba2` merge of `fix/backend-cyrillic-e-number-followup` (`a915243`, `39fac43`, `e69ebf0`). No conflicts.
2. `662e58d` merge of `origin/feat/backend-scan-attempt-diagnostics-issue-30` (`98b2401`, 5 commits). One textual conflict.
3. Final integrated head: the commit that adds this report (see the hand-back message for its full SHA).

Conflict resolution: only `nutriguard-backend/docs/CODEX_HANDOFF.md` conflicted (both sides prepend entries). Resolved by keeping **both**
sides in order; verified no line from `origin/main`'s handoff was removed. `openapi.json`, `food_analysis.py`, `scan.py`, `main.py` auto-merged
(the diagnostics branch's `main.py`/`scan.py`/`scan_diagnostics.py`/`config.py` are byte-identical to the merge-base on main, so there is no semantic drift;
`food_analysis.py` combines the diagnostics branch's `pipeline_trace` calls with the identity read path in separate hunks and was reviewed hunk by hunk).
No unrelated branch was merged; OpenFoodTox profiles, localization serving rules and ingredient-first partial results are untouched
(covered by the existing suites, which pass, and the new combined test below).

Files (52 changed vs `origin/main`, +8097/-42): 19 under `app/` (diagnostics core: `scan_attempt`, `pipeline_trace`, `client_event_ledger`, `owner_scope`,
`scan_diagnostics`, `schemas/scan_diagnostics`, `api/v1/scan_diagnostics`, `seed/scan_attempt_trace`, wiring in `main.py`/`scan.py`/`router.py`/`rate_limit.py`/`config.py`/`exceptions.py`;
ingredient identity: `ocr_normalizer`, `food_analysis`, `ingredient_repository`, `label_language`, `ingredient_translation`), `.env.example`, `README.md`, `openapi.json`,
new scripts (`audit_*`, `plan_e_number_remediation`, `inventory_error_text_ingredients`), docs and tests. **No migration, requirements, Dockerfile or compose change.**

## 3. Android compatibility (runtime-verified against the committed schema)

Specified Android behaviour vs backend, each exercised through the real HTTP API with a real journal file
(`tests/integration/test_android_scan_diagnostics_compat.py`, 4 tests; plus the diagnostics branch's own suites):

| Android behaviour | Result |
|---|---|
| 16-digit ASCII `scanAttemptId` as a string, leading zeros kept (`0123456789012345`) | Echoed byte-for-byte as `X-Scan-Attempt-Id` on all three scan endpoints (`ocr-text`, `label-image`, `barcode`), also on a 401 |
| `X-Scan-Request-Sequence` 1, then 2 on an automatic auth retry, same id | Same id echoed; the authenticated retry is journaled with `requestSequence=2`. **The pre-auth 401 writes no journal line** (header still echoed): a known limitation |
| `POST /api/v1/scan-diagnostics/client-events`, authenticated, <= 20 events and 16 KiB | 20 accepted; 21 rejected with 422; worst-case batch of 20 maximal events measured under 16 KiB |
| Remove only `acceptedEventIds` + `duplicateEventIds`, keep `retryableEventIds` | Every event id appears in exactly one list; with diagnostics disabled all ids are `retryable`, none falsely accepted; a replay after a lost response returns only `duplicateEventIds` and writes nothing |
| Round 2 stages and `PARTIAL` / `INTERRUPTED` | `IMAGE_PREPARATION`, `PARSING`, `PERSISTENCE`, `UPLOAD_RETRY`, `TERMINAL_*` and both outcomes accepted and journaled verbatim; a client `PARTIAL` is never stored as `FAILED`. Backend-side `partial` vs `failed` outcomes are distinct (existing `test_partial_discovery_records_success_with_no_fabricated_failure`) |
| No photos, OCR text, barcodes, credentials, exception messages | Schema has no free-text fields; unknown keys (`barcode`, `ocrText`, `exceptionMessage`, `imageBase64`) are ignored by Pydantic and never reach the journal (asserted by searching the journal for sentinels). Metrics are allowlisted numeric keys only |
| Owner isolation | Dedup key is (HMAC owner scope, eventId); a second user reusing an eventId is `accepted`, and the journal then holds two lines with different `ownerScope` |
| Internal pipeline correlation | Backend journal lines and `pipelineEvent` stage events carry the same `scanAttemptId` and `requestSequence` as the client's events (`origin` `backend` vs `android`) |
| Durable acknowledgment | `accepted` only after the journal append succeeded (reserve, write, commit protocol, SQLite ledger); I/O or disabled-journal failures are `retryable` |

Findings the Android side must know:
- `X-Scan-Attempt-Id` / `X-Scan-Request-Sequence` are implemented in middleware and are **not in `openapi.json`**; the contract is `docs/SCAN_ATTEMPT_DIAGNOSTICS.md`. Malformed headers are replaced, never rejected.
- `reasonCode` is a closed enum (`NETWORK_TIMEOUT`, `NETWORK_OFFLINE`, `AUTH_EXPIRED`, `AUTH_RETRY`, `SERVER_ERROR`, `RATE_LIMITED`, `VALIDATION_REJECTED`, `USER_CANCELLED`, `DECODE_ERROR`, `UNKNOWN`); there are no values for camera permission, DNS/TLS or compression failures (use the closest or `UNKNOWN`).
- Client events are not bound to a server-side attempt: any authenticated user can write any 16-digit id within their own owner scope.
- Auth-failure events cannot be sent while unauthenticated (the endpoint requires a token).
- The Android source was not available; header/body shapes above are the specification, so Android must be re-run against this backend before release.

## 4. Verification

Disposable infrastructure only (own Docker network, own PostgreSQL 16, image built from the backend `Dockerfile`, `python:3.12-slim`, pinned `requirements.txt`) on the deployment VM; the live database and `nutriguard-backend-*` containers were never contacted by any test.
The sources are copied to `~/nutriguard-claude-task/int-src` on the VM (kept as evidence).

- Offline default run (`--network none`, SQLite): `python -m pytest -q -p no:cacheprovider` -> **1029 passed, 22 skipped, 2 warnings in 33.30s**.
  The 22 skips are PostgreSQL opt-in tests (`NUTRIGUARD_TEST_POSTGRES_URL` unset).
- Same suite with the disposable database migrated by `alembic upgrade head` (all migrations apply on PostgreSQL 16) and `NUTRIGUARD_TEST_POSTGRES_URL` set:
  **1050 passed, 1 skipped, 2 warnings in 38.79s**. The one skip is `tests/postgres/test_openfoodtox_pilot_import_postgres.py`, which needs a separate EMPTY instance (`NUTRIGUARD_TEST_POSTGRES_MIGRATION_URL`) because it runs a real `alembic downgrade`; it was not run. No migration changed in this integration.
- OpenAPI: checked-in `openapi.json` vs `app.openapi()` -> **identical**. Versus `origin/main` the only change is additive and intended: path `/api/v1/scan-diagnostics/client-events` and schemas `ClientDiagnosticEvent`, `ClientDiagnosticOutcome`, `ClientDiagnosticReasonCode`, `ClientDiagnosticStage`, `ClientEventBatchRequest`, `ClientEventBatchResponse`. No existing path or schema changed, nothing removed; no dependency drift (requirements unchanged).
- Identity regression coverage included in those runs: Cyrillic Е-codes, current and legacy ids (generations 0-3), ambiguous ids, bare numbers, current-format ids reaching catalogue content, case-insensitive E-number lookup, and the PostgreSQL id-length/INSERT tests. New combined test
  `tests/integration/test_integration_identity_and_diagnostics_together.py`: a product with a legacy Cyrillic-E id reloaded with attempt headers and a real journal -> canonical `e330_citric_acid`, description/health text/BG reviewed localization/risk and `riskAssessmentAvailable` exactly as stored, bare `330` and the collapsed id fail closed, the catalogue row is **byte-for-byte unchanged** (all columns compared) and the product's stored ids are not rewritten.

## 5. Remaining limitations

- Android source unavailable: compatibility is runtime-verified against the specified behaviour only.
- Diagnostics gaps carried over (see `docs/SCAN_ATTEMPT_DIAGNOSTICS_INTEGRATION_PLAN.md`): coarse provider stage, no journal line for early 401/422/429 rejections, client events not bound to a server attempt, closed reason vocabulary, SQLite ledger and `flock` journal safe only for workers sharing one volume (production overlay runs `--workers 4` on the named volume `scan_diagnostics`, which is the same filesystem, so this holds; multi-host would not).
- Journal and ledger are bounded (1 MiB x 1 backup by default; 20 000 dedup rows) so old events are lost by design; the diagnostics journal records the barcode value.
- The E150D/E150d lookup change alters resolution for new scans and needs owner review; the remediation plan is a dry run only. 38 live references remain ambiguous by construction.
- Behaviour of `--reload` on the live bind mount: any change to the live checkout hot-reloads the backend.

## 6. Proposed deployment and rollback (NOT executed)

Preconditions: owner review of this branch; live checkout still at `a915243` (re-verify with `git -C /home/vboxuser/nutrigard status -sb` and `git log -1`); no uncommitted files there.
1. Backup first: `pg_dump -Fc` of the live database to `~/nutriguard-backups/<date>-integration/` plus `pg_restore --list`; copy the `scan_diagnostics` volume files (journal and ledger) aside. The integration contains **no migration**, so the schema is unchanged; confirm with `alembic current` before and after.
2. Settings: `SCAN_DIAGNOSTICS_ENABLED=true` already on live; keep the default paths on the `scan_diagnostics` named volume (writable by `appuser`, verified). No new secret is needed (`JWT_SECRET` also keys the owner scope; rotating it changes owner scopes, not authentication of old events).
3. Rollout: fast-forward the live checkout to the reviewed integration commit with `git fetch` + `git merge --ff-only` (the bind mount makes `--reload` pick it up; a partial update can briefly serve mixed code, so use a single fast-forward, ideally from a maintenance window); do not edit files in place. Alternative with no mixed-state risk: build and start the reviewed image under a different port first.
4. Smoke checks: `/health`; `POST /scan/barcode` with `X-Scan-Attempt-Id` returns the same header; `POST /scan-diagnostics/client-events` returns `acceptedEventIds`; reload a product known to hold legacy ids and confirm canonical identity; re-run `scripts.audit_catalogue_readonly` (read-only) and compare with 2026-10-06 counts.
5. Rollback: record the current live SHA (`a915243`) before step 3 and, if needed, return to it with `git checkout fix/backend-cyrillic-e-number` (or `git reset --hard a915243` only with explicit owner approval); the backend reloads, no data to undo because nothing is written to the catalogue by this change and diagnostics journals are append-only logs. If the journal volume misbehaves, set `SCAN_DIAGNOSTICS_ENABLED=false` (events then return `retryable`, scans are unaffected).
6. Not part of the rollout: E150D/E150d remediation, error-text row cleanup, any data repair, and the `upper(e_number)` unique index; each needs its own reviewed task.
