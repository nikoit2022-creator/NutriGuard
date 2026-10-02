# OpenFoodTox Android pilot integration

2026-10-02. Backend contract baseline: `78cbd5b`.

## Implemented

- Map effectConditions, dietaryGuidance and adiPopulationScope from the API
  into Room; additive database migration 4 -> 5 preserves existing rows.
- Preserve ownerApprovedWithoutReview and localized conditions/guidance in
  cached EN/BG JSON without promoting translation or scientific status.
- Show conditions/limitations and intake guidance beside ADI. Keep meaningful
  narrative ADI alongside numeric values, deduplicating equivalent range text.
- Enable ingredient details when these new fields are the useful content;
  keep sources at the bottom. No scoring, risk or approval calculation changed.
- Tests read the four committed backend fixtures directly as test resources,
  not duplicated production content. No hardcoded ingredient translations.

## Verification

Windows Android Studio JBR, installed Android SDK:
`gradlew.bat :app:testDebugUnitTest :app:assembleDebug --console=plain`.
Final XML totals: **123 tests, 0 failures, 0 errors, 0 skipped**.
Debug APK assembled. Migration tests exercise additive SQLite preservation
and Room full-schema validation when opening a v4-shaped database as v5.
Initial test-only issues (BG acronym assertion and native SQLite Windows
path length) were fixed before the successful final run.

No physical-device visual test has been performed. Build output is not
committed; this APK alone does not import server content.

## Release blocked on backend fixes

See `nutriguard-backend/docs/OPENFOODTOX_APP_INTEGRATION_FIX_TASK.md`:
seed-on-start overwrites imported content; EN-only reimport can invalidate
BG; E150d/E330 BG names remain English in the current backend fixtures.
Re-run these Android tests after Claude regenerates the response fixtures.
No merge/deployment has been performed.

## Device acceptance after backend release

1. Fresh lookup/scan for each pilot identity, not only an old history entry.
2. EN/BG descriptions and names match the server response; flip language
   while viewing details, then reopen from history/offline.
3. E951 conditions remain visible next to numeric ADI; E150d displays no
   invented numeric limit or SAFE badge from its placeholder risk enum.
4. Sources remain at the bottom; absent fields create no empty headings.
5. Updating an existing install preserves history and cached ingredients.
6. Backend restart leaves published profiles unchanged (backend regression).
