# Ingredient-first label results

Coordination: https://github.com/nikoit2022-creator/NutriGuard/issues/25

Baseline: main 32bd7efc05750470da6f48eab7c4111e45db1d26.
Branch: feat/android-ingredient-first, isolated from the unfinished integrity/optimization worktree.

## Implemented presentation slice

Phone feedback follow-up: extracted LanguageTopBar now applies safeDrawing top/horizontal insets; a Compose test injects a 100px system inset and checks visible EN/BG controls and actual language switching. Empty label results on the old contract use neutral cause-not-provided text, not an unsupported diagnosis of an unclear photograph. Backend API reason-code follow-up posted to issue #25 (comment 5832579379); E150d/content verification follow-up to #23 (5832581010). E-additive summaries remain pending, not shipped. User/Android Studio changes to gradle.properties and untracked gradle-daemon-jvm.properties are preserved and are not part of these UI edits.

- Partial label-image and OCR-text responses retain their submission origin, separately from barcode misses.
- Returned ingredients remain clickable regardless of missing nutrition/ingredient flags or null score.
- Partial ingredient results do not show the generic product-analysis failure reason or repeated missing-group demands.
- Additional photography is optional; empty label results offer a clear ingredient-list retry suggestion without claiming OCR itself failed.
- Generic Scanned Label Product headings and duplicate brand/name are suppressed.
- New copy has English and Bulgarian variants.
- No wire, database, scientific verification, risk calculation or scoring changes.

## Remaining integration boundary

2026-09-25: read Claude's pushed backend b220428006103e7fbedd50f25fc85f7749f79507 and report. Its compatible 200 response has blank productName, top-level null healthScore and a legacy nested product.healthScore=0. Added a DTO regression proving Android keeps the authoritative top-level null and the ingredient list. Successful Product Details now filters placeholder identity text, places the pending-score explanation after ingredients and hides dietary badges on unverified products (temporary conservative presentation, not a replacement for the separate tri-state work). Source footer no longer implies scientific verification just because a result is cached. No backend tests independently rerun here.

An empty backend ingredients array cannot be repaired by presentation. Claude must reproduce the extraction/resolution/response path and publish the exact contract in issue 25. Android currently supports existing 200 FullProductAnalysisOut and 404 partial details.ingredients. Do not assume any new API field is agreed.

This slice is not a claim that the owner's production scan now returns ingredients. Full success-screen refinements, unknown-value integration and end-to-end verification must be reconciled with the settled backend contract and separate existing work. No merge/deployment has occurred.

## Verification

2026-09-25 final files: testDebugUnitTest BUILD SUCCESSFUL in 51s, 122 tests, 0 failures/errors/skips. diff --check passed. Still local/uncommitted/unpushed; no deployment or phone verification.

2026-09-24: testDebugUnitTest BUILD SUCCESSFUL, 120 tests, 0 failures/errors/skips (XML totals). git diff --check passed. No phone test or production API call was performed. Existing SDK-version/deprecation/native-access warnings remain.

Run testDebugUnitTest. IngredientFirstResultTest mounts the isolated result card (not the Google Play scanner) and checks clickable ingredients with both missing flags, no score/failure heading, optional camera callback, Bulgarian empty guidance, and unchanged barcode-miss guidance. ViewModel regression tests check both photo and OCR origins.
## 2026-09-26 — Testable Android integration

The Android work now presents ingredient-first label results without requiring a
Health Score, keeps the EN/BG switch visible inside safe screen insets, and parses the
backend's optional structured `summary` for ingredient/E-number details. Summary
sections use canonical English or a current `REVIEWED` Bulgarian localization; draft
Bulgarian text falls back to English. Citations remain visible at the bottom. A
`humanReviewed=false` summary is clearly labelled as awaiting human review and is not
presented as expert approval.

Persistence: `IngredientEntity.summaryJson` and Room migration 4→5 preserve the
optional summary for cached/history products. Missing/malformed summaries remain
hidden; legacy responses continue to work. No client-side scientific claims, E-number
lookups, risk grades, or translations are invented.

Verification: `:app:testDebugUnitTest` completed successfully, 127 tests, 0 failures.
The first full run exposed three tests using Android `JSONObject` without Robolectric;
the harness was corrected and the complete suite was rerun green. The existing SDK XML
version and deprecated Android API warnings remain non-blocking.

Deployment dependency: visible source-backed summaries require the backend summaries
branch/API to be reviewed, integrated, migrated and deployed. The Android client is
backward compatible before that happens, but it cannot display data the live backend
does not yet send. The separate catalog audit is operational and has no UI.
