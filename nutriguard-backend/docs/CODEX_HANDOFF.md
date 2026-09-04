# CODEX_HANDOFF

## Current work

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
  `riskRationale: string|null` (via new
  `Ingredient.risk_assessment_available` DB column, `true` for every
  curated/seeded row, `false` for every synthetic one), and structured
  `efsaApprovalStatus`/`fdaApprovalStatus`
  (`APPROVED`/`NOT_APPROVED`/`NO_INFORMATION`) and
  `adiMinMgPerKgBwPerDay`/`adiMaxMgPerKgBwPerDay`/`adiSource`, derived
  conservatively and deterministically at read time from the existing
  free-text fields (new pure module
  `app/services/ingredient_regulatory.py` — never infers `APPROVED`
  from vague wording, never parses an ADI number from an ambiguous
  format) so the derived fields can never drift from the text they came
  from.
- `food_analysis._score_and_warnings` now excludes any ingredient with
  `risk_assessment_available=False` from the Health Score's risk-level
  deductions entirely — an unconfirmed ingredient can no longer move
  the score in either direction.
- Normalized the 4 curated seed entries whose
  `countriesRestrictedOrBanned` was the literal placeholder `"None"`
  (`whole_oat_flour`, `stevia_extract`, `e322_soy_lecithin`,
  `e415_xanthan_gum`) to the empty string.
- New Alembic migration `d3e4f5a6b7c8` (parent `b2c3d4e5f6a7`, new
  single head) adds `ingredients.risk_assessment_available` (`NOT NULL
  DEFAULT true` — correct for every existing row, since only
  curated/seeded data is ever persisted to this table).
- `bad_for*`/`allergens` keyword heuristics on a synthetic ingredient
  were intentionally left UNCHANGED — out of this task's scope; they
  feed the separate, pre-existing Personalized Warning Engine, not a
  scientific claim about the ingredient itself.
- `openapi.json` updated for the new `ApprovalStatus` schema and
  `IngredientOut`'s 7 new properties — **hand-patched, not a raw
  regeneration**: this environment's installed FastAPI/Pydantic
  produces two unrelated schema differences versus the currently
  checked-in `openapi.json` that predate this branch entirely
  (`ImageLabelScanRequest.image`: `format: binary` →
  `contentMediaType: application/octet-stream`; `ValidationError`
  gaining `input`/`ctx` properties) — confirmed by diffing this
  environment's live schema against `origin/main`'s `openapi.json`
  *before* any of this branch's changes. Those two hunks are
  environment/dependency-version drift unrelated to this task and were
  deliberately excluded from the diff actually applied, to keep this
  commit scoped to the ingredient data-quality change only. See
  "Unresolved items" below.
- `README.md`: new Changelog entry (V14) and a new numbered deviation
  item (item 12 in section 6) with the full breakdown.

## Files involved

Core areas: `app/services/ocr_normalizer.py` (the fix itself),
`app/services/ingredient_regulatory.py` (new — pure EFSA/FDA/ADI
derivation), `app/services/food_analysis.py` (Health Score exclusion +
`_ingredient_out_dict` parity), `app/schemas/ingredient.py`
(`IngredientOut`/`IngredientCreate`), `app/models/ingredient.py`,
`app/models/enums.py` (new `ApprovalStatus`), `app/seed/ingredients_seed.json`,
migration `d3e4f5a6b7c8`, `openapi.json`, `README.md`, and:
- `tests/unit/test_ocr_normalizer.py` (updated + new)
- `tests/unit/test_ingredient_regulatory.py` (new)
- `tests/unit/test_ingredient_schema_data_quality.py` (new)
- `tests/unit/test_food_analysis_risk_scoring.py` (new)
- `tests/unit/test_ingredient_risk_assessment_migration.py` (new)
- `tests/unit/test_nutrition_ingredients_split_migration.py` (updated:
  a pinned single-head assertion needed to move to the new head)
- `tests/integration/test_barcode_contract_change.py` (updated: the
  hand-verified Health Score regression test's two ingredients were
  always OCR-only synthetic matches in this test's ingredient table —
  its expected score changes from 28 to 60 now that their fabricated
  risk no longer deducts from it; new assertions pin
  `riskAssessmentAvailable`/`riskRationale`)

## Verification

- `python -m pytest -q`: **366 passed, 2 skipped** (0 failed). The 2
  skips are the pre-existing opt-in real-PostgreSQL concurrency test
  and a second pre-existing skip, both unrelated to this branch.
- `python -m alembic heads`: one head, `d3e4f5a6b7c8`.
- `python -m alembic upgrade head --sql`: successful offline SQL
  generation through the new migration.
- Runtime vs. tracked `openapi.json`: **NOT byte-identical** — see the
  "Unresolved items" note below; the only differences are the two
  pre-existing, unrelated environment-drift hunks, confirmed present
  even against `origin/main`'s `openapi.json` before this branch's
  changes. Every change actually made to `openapi.json` in this commit
  (the new `ApprovalStatus` schema and `IngredientOut`'s 7 new
  properties) was hand-verified against the live schema and applied
  precisely, with those two unrelated hunks deliberately excluded.
- Manually re-derived and confirmed the new
  `ingredient_regulatory.derive_approval_status`/
  `derive_adi_range_mg_per_kg_bw_per_day` outputs against all 12 real
  curated seed entries (pinned in
  `tests/unit/test_ingredient_regulatory.py`).

## Unresolved items

- This environment's installed FastAPI/Pydantic versions produce a
  runtime OpenAPI schema that already differs from the checked-in
  `openapi.json` in two places unrelated to any ingredient work
  (`ImageLabelScanRequest.image` `format`/`contentMediaType`, and
  `ValidationError` gaining `input`/`ctx`) — this predates this branch
  (confirmed against `origin/main` directly) and was not touched here,
  to keep this commit scoped to the requested task. A future task
  should pin/reconcile the dependency versions this backend is
  developed against so `openapi.json` can be regenerated wholesale
  again without manual reconciliation.
- No real-PostgreSQL run was performed for the new migration in this
  environment (no Docker/Postgres available here) — only the offline
  `--sql` generation was verified. Run a real
  upgrade → downgrade → upgrade cycle against Postgres before deploying.
- `bad_for*`/`allergens` fields on a synthetic ingredient still use the
  pre-existing keyword heuristics (unchanged, out of this task's
  scope) — if a future task wants the same honesty standard applied to
  the Personalized Warning Engine's inputs, that is a separate,
  explicit product decision.

## Recommended next step

Review the backend diff, run the real PostgreSQL migration cycle for
`d3e4f5a6b7c8` on a disposable Postgres instance, then review/open a PR
for this branch. Do not merge or deploy until review and CI are green.
