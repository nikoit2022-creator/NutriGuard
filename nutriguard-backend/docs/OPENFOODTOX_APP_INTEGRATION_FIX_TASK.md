# Application pilot: reproduced integration regressions

2026-10-02, Codex review of `78cbd5b572fc8b2ae29bc2133d1ce6dea72fa2c7`.
Continue backend only on `feat/backend-openfoodtox-dataset-audit`, isolated
worktree. Subagents permitted. Read instructions and latest handoff; fetch
first and preserve concurrent Android work. No merge/deploy/live operations.

## Reproduced defects (disposable in-memory SQLite)

Codex ran the existing import/response/localization tests: **21 passed**.
Then three temporary tests asserting expected behavior each failed:

1. **Restart overwrites imported profiles.** Run real `load_seed()` with
   its session factory patched to the disposable engine, apply pilot,
   snapshot E951.description, then run `load_seed()` again. Description
   changes back from the pilot's dipeptide text to the old seed's
   `High-intensity artificial sweetener approximately 200 times sweeter
   than sucrose.` Production entrypoint defaults SEED_ON_START=true and
   invokes this same loader. Fix persistence through the normal startup
   path, preserving scoring and unrelated seed behavior. Do not merely
   document manual reimport after each restart or auto-publish all drafts.
2. **EN-only content update makes approved BG disappear.** After initial
   seed+pilot import, change only E951 EN description in the artifact (BG
   stays identical), apply again, fetch via repository to eager-load rows,
   call build_localizations: `bg` is absent. `_plan_bg` sees unchanged BG
   prose and returns no_op, so `_apply_bg` skips refreshing the source hash
   despite an explicitly approved artifact update. Planning must include
   publication/hash metadata, not only text differences. Ordinary unapproved
   EN edits must still invalidate BG. Preserve HUMAN_CURATED translations.
3. **BG names are English.** After import,
   build_localizations(E150d)["bg"]["commonName"] is
   `Sulphite ammonia caramel`, not `Сулфитно-амонячен карамел`; E330 fixture
   also shows `Citric acid`. `_apply_bg` initializes new rows from canonical
   English common_name. Carry all four approved localized names explicitly
   from the artifact; do not rely on Android guessing translations.

The temporary review probe was removed after reproduction; add permanent
regressions in the normal test suite. No live DB was used.

## Close existing delivery requirements

- Regenerate OpenAPI with the branch's pinned dependencies (disposable
  image if necessary). Remove unrelated environment-generated schema drift;
  compare exact runtime output under deployment dependencies.
- The prior assignment required disposable Postgres import tests and real
  barcode/label-image paths, not just a migration on an empty Postgres DB or
  reliance on older generic response tests. Verify the new pilot publication
  flags/conditions on those paths (external AI/network mocked). Test migration
  with pre-existing rows and preserve false default for non-pilot content.
- Fix misleading report claims that the four profiles need no Android
  integration: effectConditions contains E951's essential PKU exception.
  Codex is wiring these fields now; deployment must wait for both halves.
- Remove broad load_seed() as a rollback recommendation: it changes unrelated
  data and is itself the overwrite path. Document targeted rollback or a
  backup restore with explicit scope and data-loss implications.

Regenerate API fixtures, run focused/full suites and document exact results.
Update CODEX_HANDOFF and integration report. Commit and push backend-only
changes normally on the same branch after checking drift. Return full SHA.
Do not edit Android, merge, deploy, touch secrets, or access live services.
