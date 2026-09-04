# CODEX_HANDOFF

## Current work

Date: 2026-09-04

- Branch: `feat/backend-ingredient-profile-data-quality`, based on
  GitHub `origin/main` at `4b44b8f04e95bc1fa58fe27546f17dcab605d562`.
  Second task on this same (still unmerged/unreviewed) branch — see
  the "V14" entry below for the first task's own handoff summary,
  preserved as-is.
- Backend-only changes (`nutriguard-backend/` only); `android-app/**`
  was not touched.

### V15: persistent ingredient knowledge cache

Full design writeup: README section 13. Summary:

- The `ingredients` table is now the ONE persistent, reusable catalog
  for BOTH curated/seeded data AND any OCR/Gemini-observed ingredient
  with no curated match — previously the latter was recreated from
  scratch, in memory only, on every scan (`ocr_normalizer.SyntheticIngredient`,
  never persisted). New table `ingredient_aliases` is the reverse-
  lookup index (any known name/spelling/Bulgarian/OCR-variant → its one
  canonical `ingredients` row), globally unique on `alias_normalized`
  — deliberately a separate table (not a JSON/array column) so that
  uniqueness can be enforced at the DB level, per-alias, across ALL
  ingredients (see `IngredientAlias`'s own class docstring for the
  full "why not a column" justification the task asked for).
- New pure module `app/services/ingredient_normalization.py`
  (`normalize_ingredient_name`) and new service module
  `app/services/ingredient_catalog.py` (local-first canonical-identity
  resolution: official identifier → alias → minimal-record
  get-or-create, race-safe; `is_stale`/`is_within_negative_cache_window`
  TTL predicates; `merge_verified_fields` confidence/source-priority-
  gated merge — see its module docstring for exactly what's live today
  vs. a ready seam for a future real external-lookup integration, since
  none exists in this codebase currently).
- Wired into `food_analysis.py` at all 6 places an ingredient list is
  assembled (`_persist_discovered_product`, `analyze_ocr_text`,
  `analyze_ocr_text_with_barcode`, both label-image entry points via
  `_run_label_image_pipeline`, and both Bulgarian-alias-aware "rebuild"
  blocks in `_finalize_barcode_enrichment`/`_finalize_standalone_label_analysis`)
  via one new `ingredient_catalog.materialize_ingredients(db, ingredients)`
  call each — every pure OCR/Gemini-parsing function itself
  (`ocr_normalizer`, `fallback_analysis`, `gemini_image_parser`, the
  discovery bridge) is UNCHANGED, keeping their own existing pure-unit-
  test suites completely untouched.
- Additive `IngredientEntity` fields: `insNumber`, `casNumber`
  (structural only, not populated for curated seed data — see below),
  `verificationStatus`, `source`, `sourceRecordId`, `sourceUrl`,
  `retrievedAt`/`lastVerifiedAt` (epoch millis), `confidence`,
  `schemaVersion`, `needsRefresh` (computed). Old fields/shape
  unchanged.
- `app/seed/load_seed.py`: every curated row now gets real provenance
  (`VERIFIED`/`CURATED_SEED`/confidence `1.0`/`normalizedName`/derived
  `insNumber`) instead of the fail-safe column defaults, and registers
  its own name plus a small hand-verified extra-alias list
  (`_EXTRA_ALIASES` — migrates the pre-existing, UNCHANGED
  `label_language._BULGARIAN_INGREDIENT_ALIASES` dict's curated-ingredient
  entries into persistent rows) as `IngredientAlias` rows.
- Migration `e4f5a6b7c8d9` (parent `d3e4f5a6b7c8`, new single head):
  adds the 8 provenance/identity columns to `ingredients` (backfilling
  every EXISTING row — all curated, at this point in the chain — to
  VERIFIED/CURATED_SEED/full confidence, and deriving `ins_number` from
  any existing `e_number`) and creates `ingredient_aliases`.
- New config: `INGREDIENT_VERIFIED_DATA_TTL_SECONDS` (~6 months),
  `INGREDIENT_NEGATIVE_CACHE_TTL_SECONDS` (24h).
- **Real bug fixed along the way, not part of the original ask**: the
  first draft of `normalize_ingredient_name` stripped brackets/parens
  as "edge punctuation", which for a name like "High Fructose Corn
  Syrup (HFCS)" removed only the trailing `)` (nothing follows it)
  while leaving the matching `(` in place — an inconsistent, lopsided
  normalized form. Fixed by excluding brackets/parens from the
  edge-stripping set entirely (see the function's own docstring for the
  reasoning); caught by `tests/unit/test_ingredient_normalization.py`'s
  own regression test before it ever reached seeded data.
- **Real bug fixed along the way** (SQLite-only, would not have
  affected production Postgres): a `DateTime(timezone=True)` value read
  back from SQLite (this test suite's DB) loses its tzinfo, so both
  `datetime` subtraction and `.timestamp()` on it were crashing/silently
  wrong (`.timestamp()` on a naive value assumes the LOCAL system
  timezone). Fixed with the same `.replace(tzinfo=timezone.utc)` pattern
  `app/services/auth_service.py` already uses for the identical issue —
  applied in `ingredient_catalog._as_utc`, `schemas/ingredient.py`'s own
  `_as_utc`, and `food_analysis._as_utc`.
- `openapi.json`: hand-patched (not a raw regeneration) for the exact
  same reason as the V14 handoff below — this environment's installed
  FastAPI/Pydantic produce two unrelated schema differences vs. the
  tracked file that predate this branch entirely
  (`ImageLabelScanRequest.image` `format`/`contentMediaType`,
  `ValidationError` gaining `input`/`ctx`) — confirmed still present and
  unrelated; excluded from what was actually applied.
- `README.md`: new Changelog entry (V15), new section 13 (full design
  writeup), section 5's table/index list updated, section 12's project
  layout updated, and a new deviation item (13) in section 6 for the
  one additive behavior change this causes (`GET /ingredients/{id}` for
  a since-observed `synth_...` id now `200`s instead of `404`ing).

## Files involved

New: `app/services/ingredient_catalog.py`,
`app/services/ingredient_normalization.py`,
`app/models/ingredient_alias.py`,
`app/repositories/ingredient_alias_repository.py`, migration
`e4f5a6b7c8d9`, and the test files listed under Verification below.

Modified: `app/models/ingredient.py`, `app/models/enums.py`,
`app/models/__init__.py`, `app/repositories/ingredient_repository.py`,
`app/schemas/ingredient.py`, `app/services/food_analysis.py`,
`app/seed/load_seed.py`, `app/core/config.py`, `openapi.json`,
`README.md`, `tests/unit/test_ingredient_schema_data_quality.py`
(extended its "purely additive field set" pin with this round's new
fields — the correct evolution of that check, not a weakening of it).

## Verification

- `python -m pytest -q`: **407 passed, 3 skipped** (0 failed). Skips:
  the two pre-existing (opt-in real-PostgreSQL concurrency test from
  V14's predecessor branch, and one other pre-existing skip) plus this
  round's own new opt-in real-PostgreSQL concurrency test (see below).
- New/updated test files: `tests/unit/test_ingredient_normalization.py`,
  `tests/unit/test_ingredient_catalog_pure.py`,
  `tests/unit/test_ingredient_knowledge_cache_migration.py`,
  `tests/integration/test_ingredient_catalog.py`,
  `tests/integration/test_load_seed.py`,
  `tests/integration/test_ingredient_knowledge_cache_end_to_end.py`,
  `tests/postgres/test_ingredient_catalog_concurrency_postgres.py`
  (opt-in), `tests/unit/test_ingredient_schema_data_quality.py` (field-set
  pin extended).
- `python -m alembic heads`: one head, `e4f5a6b7c8d9`.
- `python -m alembic upgrade head --sql`: clean. Also manually checked
  `alembic downgrade e4f5a6b7c8d9:d3e4f5a6b7c8 --sql` (offline) for the
  new migration specifically — clean, symmetric with its own upgrade.
- Runtime vs. tracked `openapi.json`: **NOT byte-identical** — the only
  differences are the same two pre-existing, environment-only hunks
  documented in the V14 entry below (reconfirmed present and unrelated
  to this change). Every actual diff applied to `openapi.json` in this
  round (the two new `IngredientSource`/`IngredientVerificationStatus`
  schemas and `IngredientOut`'s 11 new properties) was hand-verified
  against the live schema, including exact property declaration order.

## Unresolved items

- **No real-PostgreSQL run was performed** for the new migration or the
  new opt-in concurrency test in this environment (no Docker/Postgres
  available here) — only offline `--sql` generation was verified for
  the migration. Run a real upgrade → downgrade → upgrade cycle AND
  `pytest tests/postgres/ -v` (with `NUTRIGUARD_TEST_POSTGRES_URL` set)
  against a disposable Postgres instance before deploying.
- **No real external ingredient-lookup/regulatory-database integration
  exists in this codebase.** `IngredientSource.REGULATORY_LOOKUP`,
  `IngredientVerificationStatus.LIMITED_DATA`, and
  `ingredient_catalog.merge_verified_fields`/`is_within_negative_cache_window`
  are real, fully unit-tested, and ready to be called by one, but
  nothing currently calls them with non-empty/non-trivial data (Gemini
  today only does whole-label extraction, never per-ingredient
  regulatory lookup, by design — see the V14 entry below on why OCR/
  Gemini never produce scientific claims). If a specific external
  regulatory data source was actually intended by "call an external
  source or model", that needs a concrete API/credentials spec from
  the product owner before it can be built — flagging this explicitly
  rather than guessing at one.
- A future task should pin/reconcile this environment's dependency
  versions against the ones `openapi.json` was originally generated
  with, so the environment-drift differences noted above stop requiring
  manual reconciliation on every hand-patch (unrelated to this task,
  carried over unresolved from the V14 handoff).
- `cas_number` is a real column on `Ingredient` but is not populated for
  any of the 12 curated seed entries in this change — deliberately, to
  avoid fabricating an identifier without a verified source for each
  one. A follow-up task with a trustworthy per-ingredient CAS reference
  could backfill it via a proper Alembic data migration.
- The `_EXTRA_ALIASES` list in `load_seed.py` is small and hand-curated
  by design (never auto-generated/fuzzy-matched) — extending it to
  cover more curated ingredients' Bulgarian/spelling variants is
  straightforward but was intentionally scoped to what already existed,
  reviewed, in `label_language._BULGARIAN_INGREDIENT_ALIASES`.

## Recommended next step

Review the backend diff, run the real PostgreSQL migration cycle for
`e4f5a6b7c8d9` AND the new opt-in concurrency test
(`tests/postgres/test_ingredient_catalog_concurrency_postgres.py`) on a
disposable Postgres instance, then review/open a PR for this branch
covering both tasks (V14 + V15). Do not merge or deploy until review
and CI are green.

---

## Previous work (V14 — preserved as this task's own handoff)

Date: 2026-09-04

- Branch: `feat/backend-ingredient-profile-data-quality`, based on
  GitHub `origin/main` at `4b44b8f04e95bc1fa58fe27546f17dcab605d562`.
- Backend-only changes (`nutriguard-backend/` only); `android-app/**`
  was not touched.
- Removed every fabricated generic scientific/regulatory placeholder an
  OCR-only ("synthetic") ingredient used to report ("Normalized Food
  Component", "Ingredient extracted via OCR label scan.", "Standard
  ingredient.", "Extracted via OCR", "Subject to standard local food
  safety regulations.", "Standard Food Additive/Ingredient",
  "Recognized Ingredient", "Standard dietary intake", "See individual
  sensitivity profile", "NutriGuard OCR & Scientific Pipeline") and the
  keyword-inferred `riskLevel` (SAFE/POTENTIAL_CONCERN/HIGH_CONCERN
  guessed from the OCR name alone). `app/services/ocr_normalizer.py`
  now leaves every field that would require real curated/verified data
  honestly empty (never `null` for an already-required-non-null string
  field, per the existing contract) and always reports the neutral
  `SAFE` risk placeholder.
- Added additive, backward-compatible `IngredientEntity` fields (old
  fields unchanged): `riskAssessmentAvailable: boolean`,
  `riskRationale: string|null`, structured `efsaApprovalStatus`/
  `fdaApprovalStatus`, and `adiMinMgPerKgBwPerDay`/`adiMaxMgPerKgBwPerDay`/
  `adiSource`, derived conservatively and deterministically at read
  time from the existing free-text fields (`app/services/ingredient_regulatory.py`).
- `food_analysis._score_and_warnings` excludes any ingredient with
  `risk_assessment_available=False` from the Health Score's risk-level
  deductions entirely.
- Normalized the 4 curated seed entries' `countriesRestrictedOrBanned`
  placeholder `"None"` to the empty string.
- Migration `d3e4f5a6b7c8` (single head at the time).
- `openapi.json` hand-patched, excluding the same two pre-existing,
  unrelated environment-drift hunks re-confirmed in the V15 entry above.

Verification at the time: `python -m pytest -q` → 366 passed, 2
skipped. `alembic heads` → one head (`d3e4f5a6b7c8`, since superseded
by `e4f5a6b7c8d9` above). `alembic upgrade head --sql` → clean.
