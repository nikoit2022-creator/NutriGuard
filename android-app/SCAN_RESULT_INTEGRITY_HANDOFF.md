# Android scan result integrity — 2026-10-06

Branch: fix/android-scan-result-integrity. Base: 2da53e349d1830fd37920562e5c91440d5e3e47f.

## Implemented first slice

- Profile shows versionName, versionCode and compile-time Git revision; debug Scan shows the same label. Local Android changes append `-dirty`. Missing Git metadata falls back to `unknown`.
- Recognized cards now use the detail-availability gate, as product cards already did.
- Raw localization JSON is no longer evidence of useful details. Resolved localized narrative is checked instead, using the selected language. Numeric ADI, narrative intake and IARC fields remain supported.
- Bulgarian Health Score Pending no longer claims calculation is underway.
- Added two tests for name-only localization and BG-only narrative.

## Verification

- Initial build hit a Gradle configuration-cache error from git exit 128 under the execution account. Fixed by a command-local safe.directory for this exact repository and ignored nonzero exit with explicit unknown fallback.
- Generated BuildConfig verified: SOURCE_REVISION = 2da53e349d18-dirty.
- Final `:app:testDebugUnitTest :app:assembleDebug --console=plain`: BUILD SUCCESSFUL in 55s; XML totals 125 tests, 0 failures, 0 errors, 0 skipped. `git diff --check` clean. This verifies the first slice only, not the pending diagnostics/recovery work.

## Scan attempt foundation port — earlier checkpoint, 2026-10-06

- Ported only ScanAttempt.kt, ScanAttemptFooter.kt and their tests from the preserved diagnostics worktree. Existing work there remains untouched.
- Kept the ID as 16 ASCII digits including leading zeros, cryptographic generation, bounded collision retries, immutable terminal state, EN/BG display and exact-value copying.
- Corrected the request sequence bound to 999,999,999 to match the backend header contract; added a boundary regression.
- `:app:testDebugUnitTest :app:assembleDebug --console=plain`: BUILD SUCCESSFUL in 1m17s; XML totals 135 tests, 0 failures, 0 errors, 0 skipped. Components are not wired to acquisition, HTTP requests, history or upload yet; no claim of visible end-to-end IDs.
- Current Room schema remains v5. The old diagnostics migration also used v5 and must not be copied over the shipped OpenFoodTox migration. A new v5-to-v6 preservation migration is needed when persistence is integrated.

## Backend dependency review — 2026-10-06

- Reviewed Claude's local commit e69ebf0441d074d0c16ddddc886d9e2b98b59de1 in a separate detached worktree, without switching the Android checkout or touching the live server.
- Independently ran `tests/integration/test_legacy_synthetic_identity_reload.py` and `tests/integration/test_inventory_error_text_ingredients.py`: 7 passed in 1.27s. The first sandboxed run stalled and was interrupted; the isolated rerun outside that restriction completed. These tests use local SQLite, not the live database.
- Current-format IDs now reach catalogue content as well as legacy IDs. Ambiguous identities and bare numbers remain unresolved. Reload lookup is covered by a SELECT-only regression.
- Claude's reported 970-pass PostgreSQL run was not independently repeated here. The skipped OpenFoodTox test is the separate empty-database migration case, not the whole import/response suite.
- No backend merge, push, deployment or historical-data repair performed. This does not complete Android scan-attempt integration.

## Integrated Android diagnostics — 2026-10-06 (supersedes foundation checkpoint)

- The preserved diagnostics worktree was read as an implementation input only; no changes were made there. Integration was applied as scoped patches onto this newer Android branch.
- Camera barcode, manual barcode, camera label, gallery and text now allocate a 16-digit ID. Automatic auth retry preserves that ID and increments the bounded request sequence. Implicit OkHttp reconnect/redirect retries are disabled for scan calls so they cannot silently reuse a sequence.
- Terminal success, partial result, failure, cancellation and process-restored interruption show an EN/BG copyable footer. A differing valid server echo is shown separately, never silently substituted. Invalid/missing echo remains compatible with older backends. Stale capture callbacks check the acquisition's ID before submitting.
- Room v6 adds nullable scan_history.scanAttemptId and a bounded 200-row scan_attempts table, including optional serverAttemptId. MIGRATION_5_6 preserves existing rows and invents no IDs for old history. The original OpenFoodTox v4-to-v5 migration is retained and its test now verifies the chain to v6.
- New ScanDiagnostics is application-owned, not ViewModel-owned. An IO consumer persists bounded events to noBackupFilesDir/scan-diagnostic-outbox.json (128 KiB payload, 500 events, 7 days, AtomicFile temporary/backup overhead). A 256-event memory queue has a persisted drop counter; corruption/write failure does not fail a scan.
- Delivery starts after authentication initializes the app container dependency. It runs every 30 seconds while the process lives, backing off to 30 minutes on failure; pending disk events are resumed on the next launch. This is NOT an OS-scheduled background job and is NOT guaranteed delivery after force-stop. Abrupt process death can lose still-buffered memory events. Journal rotation and outbox expiry can leave gaps.
- Events are locally bound to a SHA-256 owner key captured at attempt start. Only the current matching owner's events are sent; the key itself is never included in the wire payload. Delivery uses a fixed token snapshot, no auth interceptor, no recursive diagnostics and no redirects; it cannot retry another owner's queued events under a newly authenticated account. Ownerless/old-owner entries remain bounded and expire.
- Exact endpoint: POST /api/v1/scan-diagnostics/client-events. Batches are at most 20 events and 16 KiB. Only disjoint, exhaustive acceptedEventIds + duplicateEventIds acknowledgments remove submitted IDs; retryableEventIds stay queued. Unknown/missing/duplicate acknowledgments, offline, 401/404/429/5xx all preserve the queue.
- Allowlisted event stages cover acquisition, compression, request, explicit auth retry, response, parsing, ingredient/product persistence and terminal outcome. durationMs is elapsed client attempt time, not a server-stage duration. Controlled reasons are sent; no photo, barcode, OCR text, URI, token or exception message enters this event schema. These are client observations, not proof of backend processing. Cache hits need not have server request stages.
- Client events now include only backend-allowlisted numeric metrics: compressed image dimensions/bytes, explicit auth retry count, and delivery-time outbox depth/queue age. Metrics are bounded and persisted with the event; no free text or image content is added.
- AuthInterceptor's failed-refresh path now leaves the original 401 body readable; it closes that response only when actually retrying. Regression tests cover both paths.
- Read-only VM Git check: live checkout remains a915243dd4243f0468bdc25d35e9482aed751be6; #30 diagnostics contract branch is 98b24015ee8c93c4315ba78fe7f4b8148279cfec. The current app integration targets that committed contract, read directly from Git (including Round 2 retryable acknowledgments and new enums). No claim that this backend is deployed.

### Verification / release gate

- Automated regression coverage includes ID format/bounds/collisions, lifecycle, partial results, cancellation preserving enrichment, process restoration without request replay, history migration, OpenFoodTox migration chain, EN/BG copy, mismatched echo, auth retry, concurrent request isolation, outbox limits, offline/old-backend behavior, owner isolation and selective durable acknowledgment.
- Final unchanged-source run: `:app:testDebugUnitTest :app:assembleDebug --console=plain` — BUILD SUCCESSFUL in 54s; XML totals 162 tests, 0 failures, 0 errors, 0 skipped. `git diff --check` clean. Existing SDK/native/deprecation warnings remain. No live scan or diagnostic submission was made by these tests; HTTP tests use local interceptors and Room tests use disposable local databases.
- APK: `app/build/outputs/apk/debug/app-debug.apk` (generated locally, not committed or installed). Changes remain uncommitted in this branch; no push, merge or deploy.
- Before a device end-to-end test: review this Android diff; separately integrate/review/deploy the backend diagnostics branch with explicit authorization; install the APK and verify camera/gallery cancellation/rotation, timeout/offline recovery, result/history footers and CLI correlation. Do not claim a complete server trace based only on a visible ID.

## Separate unresolved work

- Repository local missing-ingredient fallback still synthesizes from IDs; requires a separately tested repair.
- Backend content fixes and missing scientific descriptions remain separate from scan diagnostics; historical live data was not repaired.
- No device visual validation, commit, push, merge or deployment.

## Diagnostic metrics follow-up — 2026-10-06

- Added only numeric, backend-allowlisted metrics to client events: image dimensions and compressed byte size after label-image preparation; explicit auth retry count; and delivery-time outbox depth/queue age. Values are validated locally against the backend bounds and survive the bounded disk outbox. No image, barcode, OCR text, URI, token or free text is added.
- Verification after this follow-up: `:app:testDebugUnitTest :app:assembleDebug` — BUILD SUCCESSFUL, 165 tests, 0 failures/errors/skips. `git diff --check` reported no whitespace errors (Git emitted a line-ending normalization warning for an existing test file).
- Fresh debug APK: `app/build/outputs/apk/debug/app-debug.apk` (24,233,242 bytes), not installed. These are Android-local tests/builds; no live Android-to-backend submission, push, merge or deployment was performed.
