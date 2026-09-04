# CODEX_HANDOFF

## Current work

Date: 2026-09-04

- Branch: `feat/backend-ingredient-profile-data-quality`, based on
  GitHub `origin/main` at `4b44b8f04e95bc1fa58fe27546f17dcab605d562`.
  Fourth task on this same (still unmerged) branch — see "V16", "V15"
  and "V14" below for the first three tasks' own handoff summaries,
  preserved as-is.
- Backend-only changes (`nutriguard-backend/` only); `android-app/**`,
  `main`, the live database/Docker volumes, `.env`, and credentials
  were not touched.

### V17: resolved the documented deterministic PostgreSQL test failure

The V16 entry below reported
`test_concurrent_enrichment_of_the_same_existing_row_preserves_both_groups`
as "a pre-existing, unrelated test issue, found and reported (not
fixed)". This task's own instruction pushed back correctly: it IS in
the same product/ingredient enrichment and concurrency area, so it
needed to actually be resolved and proven, not left as "unrelated"
by assertion alone. Root-caused and fixed here.

**Reproduction (task requirement 1) — all three scenarios, against a
fresh disposable PostgreSQL 16 instance, before any fix:**

- *Alone*, 10 repeated fresh-DB runs: **8 passed, 2 failed** — already
  disproves last task's working theory ("passes alone, fails with its
  sibling"); that theory was drawn from a single alone-run and was
  itself wrong. It's flaky alone too.
- *Together with its sibling test* (`test_concurrent_enrichment_of_the_same_new_barcode_converges_on_one_row`),
  5 repeated fresh-DB runs, both orders: **failed 5/5 in normal order**
  in this batch, **passed 3/3 in reversed order** in a separate batch
  — inconsistent with either file/order being the cause; consistent
  with pure timing-based nondeterminism.
- *As part of the complete opt-in `tests/postgres/` directory*: same
  pattern — pass/fail tracked the individual test's own race outcome,
  not directory composition.

**Determination (requirement 2) — a 15-iteration diagnostic script**
(`asyncio.gather`-ing the exact two concurrent calls the test makes,
printed per-call, not part of the repo) against real Postgres nailed
the exact mechanism:

- **15/15 iterations**: the FINAL persisted row was IDENTICAL and
  fully correct — `hasVerifiedIngredients=True`,
  `hasVerifiedNutrition=True`, `isVerified=True`, `healthScore=85`.
  The `for_update=True` row-lock concurrency guarantee never once
  failed. This rules out *transaction isolation/session reuse* and
  *a real production concurrency bug* outright — nothing was ever lost
  or corrupted.
- **12/15 iterations**: the INGREDIENTS-photo call's transaction
  committed first → it got `SUCCESS(healthScore=None)` (a normal `200`,
  not `labelScanRequired`) → the NUTRITION-photo call committed second,
  saw both groups, got `SUCCESS(healthScore=85)`. Two successes, zero
  `labelScanRequired` — exactly the pattern the OLD assertion rejected.
- **3/15 iterations**: the NUTRITION-photo call committed first → it
  got `labelScanRequired` (nutrition alone was never enough) → the
  INGREDIENTS-photo call committed second, saw both groups, got
  `SUCCESS(healthScore=85)`. One success, one `labelScanRequired` —
  the ONLY pattern the OLD assertion accepted.
- Full DB resets between every iteration (fresh migrate each time)
  rule out *test-state leakage/order dependence* as well — the same
  two outcomes occur from a clean slate, in either order, with no
  prior test having run at all.

**Root cause: a stale assertion, citing the exact code that supersedes
it** (requirement 3) — `app/services/food_analysis.py`,
`_finalize_barcode_enrichment`'s own "SUCCESS GATE (V13...)" comment
block (search that file for `SUCCESS GATE`): "label-driven product
enrichment succeeds once ingredient RECOGNITION succeeds... Missing/
incomplete NUTRITION alone never fails this endpoint any more." Also
`README.md`'s "V13" Changelog entry and section 11.14
("Ingredient-recognition success vs. health-score readiness"). V13
predates this branch entirely (already on `origin/main`) and is
otherwise fully, correctly implemented and already covered
deterministically elsewhere
(`tests/integration/test_label_barcode_enrichment.py::test_barcode_nutrition_plus_later_ingredients_only_photo_completes_product`
and `::test_barcode_ingredients_plus_later_nutrition_only_photo_completes_product`
cover both sequential orderings already) — only this ONE concurrency
test's assertion never accounted for it.

**Fix applied (requirement 3 — assertion + description only, nothing
in `app/` touched, no guarantee weakened):**
`tests/postgres/test_concurrent_enrichment_postgres.py`:
- Docstring rewritten to state the ACTUAL invariant (exactly one call
  always sees the real Health Score; the other's own response
  legitimately depends on race timing) and cites the V13 contract
  above plus this task's own 15-iteration finding.
- Assertion rewritten to check the REAL invariant directly: exactly
  one of the two results has a non-null `health_score` (never both —
  duplicated evidence — never neither — lost evidence); the other
  result is then EITHER `labelScanRequired` (nutrition-only committed
  first) OR a success with `health_score is None` (ingredients-only
  committed first, V13) — both explicitly checked, neither silently
  accepted by omission. No `pytest.mark.skip`, no loosened row-state
  assertions (the final-row checks are byte-for-byte unchanged).

**Verification (requirement 5), all against the same disposable
Postgres 16 + pinned-dependency Docker image used in the V16 pass:**
- Fixed test alone: **15/15 passed** (both legitimate orderings
  occurred naturally across the 15 runs, both correctly accepted).
- Fixed test + sibling, normal order: **5/5 passed** (`2 passed` each).
- Fixed test + sibling, reversed order: **3/3 passed**.
- Complete `tests/postgres/` directory (all 4 tests): **3/3 runs, 4/4
  tests passed each time**.
- `python -m pytest -q`: **410 passed, 4 skipped, 0 failed** — both on
  the host (Python 3.14) and inside the pinned-dependency image
  (Python 3.12) — identical, and identical to the V16 pass's count
  (this task changed a test file that's skipped in the default local
  run, so the count is unaffected).
- `python -m alembic heads`: one head, `e4f5a6b7c8d9` — unchanged (no
  migration touched by this task).
- Runtime `app.openapi()` vs. tracked `openapi.json`, pinned
  dependencies (Python 3.12, `requirements.txt` exactly): **EXACT
  MATCH** — unchanged (no schema/route touched by this task).
- Diff review: **one file changed**
  (`tests/postgres/test_concurrent_enrichment_postgres.py`, +61/-12),
  fully scoped to `nutriguard-backend/`, no secrets
  (`api_key`/`secret`/`password`/PEM patterns grepped across both this
  diff and the full `4b44b8f..HEAD` branch range — none found beyond
  expected config field names).

## Verification (V17 summary)

- Reproduction: alone (8/10 pass pre-fix, confirming flakiness),
  with sibling (both orders, pre-fix), full `tests/postgres/`
  directory (pre-fix) — all three reproduced the SAME nondeterministic
  pattern, never a distinct order-dependent or leakage-dependent one.
- 15-iteration diagnostic: 15/15 final-row correctness, 12/15 +
  3/15 = the exact two legitimate V13 orderings, 0 unexpected outcomes.
- Post-fix: 15/15 (alone) + 5/5 (with sibling, normal order) + 3/3
  (reversed order) + 3/3 runs × 4/4 tests (full directory) = **26/26
  postgres-test executions passed** across every combination requested.
- `pytest -q` (host + pinned image): 410 passed, 4 skipped, 0 failed,
  both environments identical.
- `alembic heads`: single head, `e4f5a6b7c8d9`.
- OpenAPI (pinned deps): exact match.
- Secrets/unrelated files: none.

## Unresolved risks (after V17)

- None new. Everything listed as unresolved in the V16 entry below
  still applies (no real external ingredient-lookup/regulatory-database
  integration exists yet; `cas_number` not populated for curated seed
  data; `_EXTRA_ALIASES` intentionally small/hand-curated;
  `_fill_missing_identity_fields`'s lack of its own confidence gate,
  safe today, flagged for a future non-OCR_HEURISTIC source) — this
  task did not change any of that. The item this task existed to
  resolve (the deterministic test failure) is now closed: fixed, not
  skipped, not weakened, with the root cause proven and cited.

## Recommended next step

This branch (backend-only, `android-app/**`/`main`/live
stack/credentials untouched throughout) has now had ALL of its
previously-flagged verification gaps closed: migration up/down/up
cycle proven against real Postgres (V16), both opt-in concurrency test
files proven against real concurrent Postgres sessions with 0
failures across 26 executions (V16 + V17), the complete backend suite
green in both the host and pinned-dependency environments, a single
Alembic head, an exact-match OpenAPI regeneration under the pinned
dependency set, and no outstanding "reported but not fixed" items in
this area. The branch is ready for PR review. Opening the actual PR
(and any merge/deploy) still requires the explicit go-ahead this task
series has consistently deferred to a human/CI review step.

---

### V16: final pre-PR verification (found and fixed 2 real bugs)

Real, disposable infrastructure was used for this pass — NOT the
already-running live dev stack (`nutriguard-backend-db-1` etc., left
completely untouched): a separate `postgres:16-alpine` container
(`nutriguard-pr-verify-pg`, its own Docker network, no persistent
volume, removed at the end of the session) plus a throwaway image
(`nutriguard-pr-verify:*`) built FROM THIS BRANCH's own `Dockerfile`
+ `requirements.txt` (Python 3.12-slim, the repo's actual pinned
dependency set — the host shell's ambient Python 3.14 was NOT used for
any of this pass's checks, precisely to eliminate the environment-drift
question raised in the V15/V14 entries below).

**1-2. Migration upgrade → downgrade → upgrade through `e4f5a6b7c8d9`,
with pre-existing data surviving correctly.** Script: start at the
immediately-prior revision (`d3e4f5a6b7c8`), insert a product +
ingredient row using the OLD (pre-migration) column set via raw SQL,
then upgrade → verify → downgrade → verify → upgrade again → verify.
**Result: PASSED.** After the first upgrade, the pre-existing
`Citric Acid`/`E330` row was correctly backfilled
(`normalizedName="citric acid"`, `insNumber="330"` derived from the
E-number, `verificationStatus=VERIFIED`, `source=CURATED_SEED`,
`confidence=1.000`) with the product's `ingredientIds` link intact;
after downgrade, the new columns/table were gone but both rows and
their link survived unchanged; the re-upgrade deterministically
reproduced the identical backfilled values. (An `asyncpg` "cached
statement plan is invalid" error appeared on the first attempt — a
verification-script artifact from reusing one engine's connection pool
across DDL changes made by a separate `alembic` subprocess, fixed by
disposing the pool after each migration step; not a product bug.)

**3-4. Opt-in ingredient-catalog PostgreSQL concurrency tests, proving
simultaneous creation of the same alias/E-number converges on one row.**
Found and fixed **two real bugs** in the process (both now covered by
regression tests, both verified fixed against real concurrent
PostgreSQL sessions, 5 repeated runs each with 0 failures):

- **Test-harness deadlock** (not a production bug, but the concurrency
  test itself was unusable): the original test deferred both sessions'
  `commit()` until after `asyncio.gather` returned. Under SQLite (this
  suite's fast default DB) that's harmless; under real Postgres, the
  second session's INSERT genuinely blocks waiting for the first
  session's transaction to resolve (a `transactionid` lock wait,
  confirmed via `pg_stat_activity`) — but that first session's commit
  was scheduled to run only AFTER `gather` returned, which never
  happens while the second call is still blocked. Neither coroutine
  could make progress: a real hang, not a flaky race (reproduced
  identically 3/3 times before the fix). Fixed by having each
  concurrent "request" commit its own work internally before
  returning — exactly how `food_analysis.py`'s real request-scoped
  functions already behave (each with its own internal
  `await db.commit()`), which is what the SIBLING file's existing,
  working concurrency test actually gathers (full request functions,
  not bare repository calls).
- **Real production bug: `get_or_create_catalog_ingredient` couldn't
  recover from an E-number conflict.** Two different display names
  sharing the same genuine, never-before-seen E-number (e.g.
  "Vitamin C (E300)" vs. "Ascorbic Acid (E300)") produce DIFFERENT
  deterministic ids/normalized names, so a concurrent race between them
  conflicts on the UNIQUE `e_number` column, not the `id` primary key.
  The loser's recovery path only re-fetched by `id` and then by its OWN
  alias — neither of which the WINNER's row is reachable through (it
  has a different id and a different alias) — so it fell through to
  the "this should be unreachable" `RuntimeError` instead of
  converging. **Fixed**: the recovery path now also re-checks by every
  official identifier the row carries (E-number, INS, CAS) before
  concluding the conflict is unrecoverable. New regression test:
  `test_concurrent_resolution_of_the_same_new_e_number_from_different_display_names_converges`.
  See README section 13.4.

**5. Complete backend test suite**: `python -m pytest -q` →
**410 passed, 4 skipped, 0 failed** (up from 407/4 in the V15 entry —
6 new tests from this pass's 2 fixes, minus 2 renamed/consolidated;
net +3 test functions, +1 skip from the new E-number concurrency
test), run BOTH on the host (Python 3.14) and inside the pinned
`nutriguard-pr-verify` image (Python 3.12, the repo's actual pinned
deps) — identical result both ways.

**6. OpenAPI regenerated with the repository's pinned dependencies —
EXACT MATCH, no manual reconciliation.** Running
`app.openapi() == json.load(open("openapi.json"))` INSIDE the pinned
`nutriguard-pr-verify` image (Python 3.12-slim, `fastapi==0.115.6`,
`pydantic==2.10.4`, exactly as pinned in `requirements.txt`) returned
`True` with **zero** differences — including the two hunks
(`ImageLabelScanRequest.image`'s `format`/`contentMediaType`,
`ValidationError`'s `input`/`ctx`) that the V15/V14 entries below
documented as "pre-existing, unrelated environment drift" and
hand-excluded from what they applied. This CONFIRMS that drift was
purely an artifact of the earlier sessions' host Python (3.14, too new
for the pinned `pydantic-core` wheel) rather than anything about the
tracked `openapi.json` itself — with the actual pinned dependency set,
there is no drift left to explain or exclude. No changes to
`openapi.json` were needed in this pass.

**7. Commit review** (`1d8c3d9`, `fdb025f`, plus this pass's own fixes)
against each requested risk category:
- *Fabricated scientific/regulatory claims*: none found —
  `_build_minimal_row` only ever copies already-empty fields from
  `SyntheticIngredient` (verified by re-reading every field assignment
  in `ingredient_catalog.py`/`ocr_normalizer.py`).
- *Incorrect verification promotion*: **found and fixed** —
  `merge_verified_fields` was promoting a row all the way to `VERIFIED`
  (and setting `riskAssessmentAvailable=True`, which is what lets
  `riskLevel` influence the Health Score) for ANY non-OCR source,
  including a future `GEMINI`-sourced merge. An AI-generated claim is
  real content worth storing but is NOT a human/regulatory
  confirmation. Fixed: only `REGULATORY_LOOKUP`/`CURATED_SEED`-ranked
  sources promote to `VERIFIED`; a `GEMINI`-sourced merge now promotes
  only to `LIMITED_DATA` and leaves `riskAssessmentAvailable` alone.
  New tests: `test_regulatory_lookup_source_promotes_all_the_way_to_verified`,
  `test_gemini_source_promotes_only_to_limited_data_never_verified`.
- *Alias collisions/ambiguous aliases*: none found in the actual seed
  data (programmatically verified: all 12 curated self-aliases + the 5
  curated `_EXTRA_ALIASES` normalize to 17 distinct, non-colliding
  keys). Found and fixed a related **completeness gap**: resolving via
  E-number (step 1) returned early without registering the observed
  display text as a new alias, so a later non-E-number-annotated
  mention of the same name would have fallen through to creating a
  duplicate stub instead of reaching the alias hit in step 2. Fixed —
  see item 3-4 above and README 13.1. The known, accepted, narrow
  residual risk from before (a curated seed entry added AFTER an
  OCR-only stub already claimed its exact normalized name — the seed
  loader logs and skips rather than repointing) is unchanged and still
  intentionally out of automatic-repair scope; see the V15 entry.
- *Lower-confidence overwrite of curated data*: re-verified via the
  existing + new `merge_verified_fields` tests — a lower-rank OR
  equal-rank-lower-confidence source can never touch a higher-rank
  row's fields. No issue found beyond the verification-promotion bug
  above.
- *Unsafe TTL handling*: the SQLite naive/aware `datetime` bug fixed in
  V15 was re-verified fixed (`_as_utc` applied consistently in
  `ingredient_catalog.py`, `schemas/ingredient.py`, `food_analysis.py`)
  — the migration/concurrency verification pass above exercises real
  `TIMESTAMP WITH TIME ZONE` values end-to-end against genuine
  Postgres, where this class of bug cannot occur in the first place
  (Postgres always returns tz-aware values), so this pass adds
  confirmation on top of the SQLite-side unit tests. No new TTL issue
  found.
- *Transaction/race problems*: **found and fixed two** — see item 3-4
  above (test-harness deadlock + the E-number conflict-recovery bug).
  Also noted (not fixed, low severity/likelihood, documented instead):
  `_fill_missing_identity_fields` fills a null `e_number`/`ins_number`
  on ANY row (curated or not) whenever the E-number-hit path or an
  exact alias match supplies one, with no separate confidence gate of
  its own — safe in practice because it can only ever fire when the
  FULL normalized display text exactly matches an existing alias (or
  the E-number itself already resolves), which structurally prevents a
  stray OCR digit sequence from attaching to an unrelated curated row;
  flagged here for visibility rather than engineered around, since
  every synthetic ingredient's source is `OCR_HEURISTIC` regardless
  (there is no different-source case to gate against yet).
- *Secrets and unrelated files*: none found. `git diff --stat`
  confined to `nutriguard-backend/` for both commits and this pass's
  fixes; greeted for `api_key`/`secret`/`password`/PEM-block patterns
  across the full `4b44b8f..fdb025f` range plus this pass's own diff —
  no matches outside already-expected config field NAMES (e.g.
  `JWT_SECRET: str`, `GEMINI_API_KEY: str = ""`); no `.env`/credential/
  keystore-shaped file touched by any commit.

Also incidentally discovered while running `tests/postgres/` as a
whole (NOT part of this task, NOT fixed, reported for visibility): the
PRE-EXISTING, unrelated
`test_concurrent_enrichment_postgres.py::test_concurrent_enrichment_of_the_same_existing_row_preserves_both_groups`
test (predates this branch entirely — inherited from `origin/main`,
never previously run against real Postgres per every prior session's
own "no Docker/Postgres available" note) fails deterministically (3/3
runs) when run in the same pytest session as the file's other test, but
passes in isolation. Root cause appears to be a stale assumption in the
test itself, not a real application bug: V13 (already on `origin/main`
before this branch existed) changed `_finalize_barcode_enrichment` so
that completing ONLY the ingredients evidence group returns `200` with
a null Health Score rather than `404`/`labelScanRequired` — this
test's hardcoded "exactly one of the two concurrent calls succeeds, the
other gets `ProductNotFoundError`" assertion only holds when the
NUTRITION-only side happens to win the row-lock race, not when the
INGREDIENTS-only side does (which now legitimately succeeds too, per
V13). This is unrelated to the ingredient-knowledge-cache task and was
deliberately NOT touched here (out of scope, a different file/feature);
flagging it for a separate, dedicated fix.

## Verification (this pass)

- `python -m pytest -q` (host, Python 3.14): **410 passed, 4 skipped,
  0 failed**.
- `python -m pytest -q` (pinned image, Python 3.12): **410 passed, 4
  skipped, 0 failed** — identical.
- `alembic upgrade e4f5a6b7c8d9` → verify → `alembic downgrade
  d3e4f5a6b7c8` → verify → `alembic upgrade e4f5a6b7c8d9` → verify,
  against real disposable PostgreSQL 16: **PASSED** (see item 1-2
  above for the exact assertions).
- `tests/postgres/test_ingredient_catalog_concurrency_postgres.py`
  (both tests, real disposable PostgreSQL 16, 5 repeated runs after the
  fixes): **PASSED, 5/5, 0 failures**.
- Runtime `app.openapi()` vs. tracked `openapi.json`, using the
  repository's pinned dependencies (Python 3.12, `requirements.txt`
  exactly): **EXACT MATCH — 0 differences.**
- Commit review of `1d8c3d9`/`fdb025f` against the 7 requested risk
  categories: 2 real issues found and fixed (see item 7 above), 1
  unrelated pre-existing test issue found and reported (not fixed), no
  secrets/unrelated files.

## Unresolved risks (carried forward / new)

- The pre-existing, unrelated `test_concurrent_enrichment_of_the_same_existing_row_preserves_both_groups`
  test failure (see above) needs a separate, dedicated fix/task — its
  assertion needs updating for V13's actual (correct) asymmetric
  completion-gate behavior. Out of scope here.
- `_fill_missing_identity_fields`'s lack of its own confidence gate
  (see item 7 above) — safe today, flagged for whoever eventually adds
  a second, non-OCR_HEURISTIC source that could reach that function.
- Everything already listed as unresolved in the V15 entry below still
  applies (no real external ingredient-lookup/regulatory-database
  integration exists yet; `cas_number` not populated for curated seed
  data; `_EXTRA_ALIASES` intentionally small/hand-curated) — this pass
  did not change any of that.

## Recommended next step

Review the diff (including this pass's 2 bug fixes), then merge only
after CI/human review confirms green — this handoff documents that a
disposable Postgres 16 instance, a pinned-dependency Docker build, a
real concurrent-session migration cycle, and the full opt-in
concurrency suite all passed cleanly as of this pass's commit (see the
git log / PR for the exact SHA). Do not merge or deploy without that
separate review, per the task's own instruction.

---

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
