# OpenFoodTox app pilot integration -- backend report

Implements `docs/OPENFOODTOX_APP_PILOT_INTEGRATION_TASK.md` (owner
authorization dated 2026-10-01, baseline `037b3ccbeb91b91e539aa24474674e6b9687dc1e`).
Backend-only: no Android code was touched. This document is the
field-contract handoff Codex needs before wiring the Android side.

## 1. What this delivers

A small, tracked, versioned content artifact plus an explicit,
transactional, idempotent dry-run/apply import that publishes the four
owner-approved OpenFoodTox pilot identities (E250, E150d, E330, E951) to
the real ingredient catalog and serves their EN/BG content through every
existing response path -- without touching Health Scores, regulatory
approval badges, numeric ADI figures, or dietary/scoring flags for any
of the three identities that already existed.

## 2. New/changed files

| File | Purpose |
| --- | --- |
| `scripts/openfoodtox/build_app_pilot_content.py` | Builds `app/seed/openfoodtox_pilot_profiles.json` directly from `editorial_content.py`'s structured dataclasses -- never parses Markdown, never needs a pilot output directory or the bulk VM dataset at runtime. |
| `app/seed/openfoodtox_pilot_profiles.json` | The tracked, versioned (`content_version`) EN/BG content artifact the import reads. Regenerate with `python -m scripts.openfoodtox.build_app_pilot_content` (`--check` to verify it's current). |
| `app/seed/load_openfoodtox_pilot_content.py` | The import itself -- exact four-identity allowlist, dry-run by default, `--apply` to write+commit in one transaction. See its own module docstring for the complete, authoritative list of fields it is and isn't allowed to touch. |
| `scripts/openfoodtox/generate_app_pilot_fixtures.py` | Regenerates the real response fixtures in `docs/openfoodtox_app_pilot_fixtures/` by actually running the seed+import+API against a throwaway in-memory DB. |
| `docs/openfoodtox_app_pilot_fixtures/{e250,e150d,e330,e951}_ingredient_out.json` | Real `GET /api/v1/ingredients/{id}` response bodies (English top-level fields + `localizations.en`/`localizations.bg`) -- not hand-authored, byte-exact output of the code in this branch. |
| `alembic/versions/a8b9c0d1e2f3_ingredient_localization_owner_approval.py` | One additive column: `ingredient_localizations.owner_approved_without_review` (boolean, `NOT NULL DEFAULT false`). |
| `app/models/ingredient_localization.py`, `app/services/ingredient_localization.py`, `app/schemas/ingredient.py` | The owner-approved-publication mechanism (see section 4). |
| `openapi.json` | Regenerated; see section 6 for the actual contract diff. |

## 3. What is and isn't touched for each identity

| Identity | Before | After this import |
| --- | --- | --- |
| E250 (`e250_sodium_nitrite`) | Existing, `VERIFIED`/`CURATED_SEED` | Same id, same `riskLevel` (`HIGH_CONCERN`), same `riskAssessmentAvailable` (`true`), same `efsaStatus`/`fdaStatus`/`acceptableDailyIntake`/dietary flags. Only `description`, `purposeInFood`, `healthConcerns`, `effectConditions`, `dietaryGuidance`, `references` change, to the OpenFoodTox-pilot content. |
| E951 (`e951_aspartame`) | Existing, `VERIFIED`/`CURATED_SEED` | Same as E250's row above. |
| E330 (`e330_citric_acid`) | Existing, `LIMITED_DATA`/`CURATED_SEED` (CSV starter) | Stays `LIMITED_DATA` -- **never promoted to `VERIFIED`** by this import. Same narrative-field-only update as above. |
| E150d | Did not exist | **New** row `e150d_sulphite_ammonia_caramel`, `LIMITED_DATA`, `riskAssessmentAvailable=false`, `riskLevel=SAFE` placeholder -- excluded from `app.services.food_analysis._score_and_warnings`'s Health Score input by construction, so it can never move any product's score in either direction. `acceptableDailyIntake`/`efsaStatus`/`fdaStatus` left `""` -- the pilot's own numeric-eligibility check (`pilot/v8/adhoc_query_E150d_profile.json`) withheld E150d's group ADI as `consumer_guidance_eligible=false` ("chemical basis is not resolved"); owner publication permission for the narrative content does not resolve that separate numeric-eligibility question, so no number is asserted. `dietaryGuidance` instead explains in prose that a shared, four-colour group limit exists without stating a figure. |

Verified directly (not just by design): `tests/integration/test_openfoodtox_pilot_import.py::test_apply_preserves_scoring_and_regulatory_fields_on_existing_rows` snapshots every scoring/regulatory field on E250/E951 before and after `--apply` and asserts byte-for-byte equality; `test_existing_product_health_score_is_unchanged_by_the_import` computes a real product's Health Score before and after and asserts it is identical.

## 4. Owner-approved-publication mechanism (honest provenance)

The existing rule (`REVIEWED` status + current content hash) is
unchanged. A new, narrowly scoped escape hatch was added alongside it:
an `IngredientLocalization` row may ALSO be served while honestly
`DRAFT` when `owner_approved_without_review=True` (new column, default
`false` for every row from every other writer). `translation_source`
stays `MACHINE_TRANSLATED` and `translation_status` stays `DRAFT` --
never relabeled `REVIEWED`/`HUMAN_CURATED`, because nobody but the
owner's publication-permission decision reviewed this content (the
task's own words: "NOT a claim that an independent scientist or human
translator reviewed it"). The content hash check still applies in both
branches -- approval covers the content as approved, never a later,
unapproved edit. `IngredientLocalizedTextOut.ownerApprovedWithoutReview`
surfaces this on the wire so a client can honestly label the content if
it chooses to. An existing `HUMAN_CURATED` translation (a genuinely
human-authored one, as opposed to this codebase's own
REVIEWED+MACHINE_TRANSLATED seed-loader convention) is never overwritten
by this import, regardless of status.

## 5. Field contract for Android (Codex)

| Backend field | Values here | Intended UI destination |
| --- | --- | --- |
| `commonName` + `eNumber` | e.g. `"Sulphite ammonia caramel"` + `"E150d"` | Heading; E-code shown once, distinct from the name |
| `description` / `localizations.bg.description` | What it is / origin | Description |
| `purposeInFood` / `localizations.bg.purposeInFood` | Technological role | Purpose in food |
| `healthConcerns` / `localizations.bg.healthConcerns` | Human-relevant effects + evidence limitations, already narrowed to what the cited source supports | Health considerations |
| `effectConditions` / `localizations.bg.effectConditions` | Susceptible populations / conditions (e.g. aspartame + PKU) | Adjacent to the relevant effect or intake section |
| `acceptableDailyIntake`, `adiMinMgPerKgBwPerDay`, `adiMaxMgPerKgBwPerDay`, `adiPopulationScope`, `adiSource` | **Unchanged** for E250/E330/E951; all `null`/`""` for E150d | Intake section, never serving-size advice |
| `dietaryGuidance` / `localizations.bg.dietaryGuidance` | General, non-ADI guidance; for E150d, the prose explanation of the withheld group-limit number | Intake section, alongside the ADI fields |
| `references` (EN only -- citations are not per-language) | DOI/URL list | Sources, at the bottom |
| `localizations.bg.ownerApprovedWithoutReview` | `true` for every pilot BG row | Not in the task's table, but present so Android can honestly flag "not independently reviewed" content if it chooses to; `false` everywhere else, including every `en` entry |

**Correction (2026-10-02, OPENFOODTOX_APP_INTEGRATION_FIX_TASK.md)**: an
earlier version of this section claimed Android needed no integration
work for this pilot's own four identities "because the ADI/approval
fields were left alone." That claim was misleading and has been
retracted -- it is NOT true that nothing here needs Android wiring.
`effectConditions` is not cosmetic for this very pilot: E951's
`effectConditions` carries the PKU (phenylketonuria) exception, a
safety-relevant caveat on a sweetener already in the catalog today. An
Android build that doesn't read `effectConditions` silently drops that
warning for every existing E951-containing product the moment this
pilot's content ships, not just for some future identity. Current
Android main does not consume `effectConditions`/`dietaryGuidance`/
`adiPopulationScope`, and its numeric ADI display takes precedence over
text; for E250/E330/E951 this is a pre-existing gap, but THIS pilot is
what first makes it consumer-visible and safety-relevant (via E951),
not a hypothetical "future identity." Codex has since wired
`effectConditions`/`dietaryGuidance`/`adiPopulationScope` end-to-end
(DTO fields, Room entity/migration, localization lookup, view binding --
see `android-app/OPENFOODTOX_PILOT_HANDOFF.md`); deployment of this
pilot content must wait for both halves (this backend content AND that
Android wiring) to ship together, never the backend content alone.

Representative, real response bodies: `docs/openfoodtox_app_pilot_fixtures/{e250,e150d,e330,e951}_ingredient_out.json`
(EN fields at top level, BG under `localizations.bg`). Regenerate with:

```
python -m scripts.openfoodtox.generate_app_pilot_fixtures
```

## 6. OpenAPI contract diff

Regenerated via `python -c "import json; from app.main import app; json.dump(app.openapi(), open('openapi.json','w'), indent=2); "`
(trailing newline added to match the committed file's convention) and
diffed against the previously committed snapshot. The diff has two
unrelated parts:

- **This task's actual contract change** (additive, non-breaking): a new
  `ownerApprovedWithoutReview: boolean = false` field on
  `IngredientLocalizedTextOut` (i.e. inside `localizations.en`/`.bg`).
  No field was removed, renamed, or narrowed.
- **Pre-existing, unrelated environment drift**: confirmed by
  regenerating `openapi.json` against the UNMODIFIED tree (stashed this
  round's changes first) -- the same ~129 diff lines already exist with
  zero code changes from this task (a FastAPI/Pydantic version
  difference: `image`'s `format: binary` became `contentMediaType`, and
  `ValidationError` gained `input`/`ctx` fields). Included in the
  regenerated snapshot since that is what the installed library versions
  actually generate today, but not attributable to this task.

## 7. Verification

```
cd nutriguard-backend

# Focused
python3 -m pytest tests/unit/test_openfoodtox_build_app_pilot_content.py \
  tests/unit/test_ingredient_localization.py \
  tests/unit/test_ingredient_localization_owner_approval_migration.py \
  tests/unit/test_ingredient_candidate_queue_migration.py \
  tests/integration/test_openfoodtox_pilot_import.py \
  tests/integration/test_openfoodtox_app_pilot_response_paths.py -q
# 35 passed

# Full suite
python3 -m pytest -q
# 895 passed, 16 skipped (pre-existing), 3 warnings (pre-existing)
```

Migration cycle, verified against a **disposable** Postgres 16 container
(`docker run --name openfoodtox-pilot-migration-check ...`, port 55432,
isolated from the live `nutriguard-backend-db-1` stack, destroyed
immediately after):

```
alembic upgrade head        # full chain, from empty -- succeeds
alembic downgrade -1        # drops owner_approved_without_review -- succeeds
alembic upgrade head        # re-adds it -- succeeds
```

Import dry-run/apply/rollback, documented and tested
(`tests/integration/test_openfoodtox_pilot_import.py`):

```
# Preview only -- writes nothing, prints every proposed field change:
python -m app.seed.load_openfoodtox_pilot_content

# Write + commit, one transaction (all four identities together):
python -m app.seed.load_openfoodtox_pilot_content --apply
```

Rollback approach: `run(apply=True)` performs every write inside one
`AsyncSession`, calls `session.rollback()` and re-raises on any
exception during `apply_plan`, and only calls `session.commit()` after
every identity has been applied successfully -- a failure partway
through (e.g. E150d's creation) leaves ALL four identities, including
ones already touched earlier in the same call (E250), exactly as they
were before (`test_rollback_on_failure_leaves_no_partial_writes`).
Idempotency: re-running `--apply` with no further content changes
produces an all-`no_op` plan and writes nothing new
(`test_apply_is_idempotent`).

**Rollback, corrected (2026-10-02, OPENFOODTOX_APP_INTEGRATION_FIX_TASK.md)**:
an earlier version of this section recommended "re-running `load_seed()`'s
own data" as this import's undo mechanism. That recommendation is
withdrawn -- `load_seed()` is not a rollback tool here: it is the very
unconditional-reseed path that defect 1 identified (production's
`SEED_ON_START` re-invoking it on every restart used to silently revert
this import's own content back to the old curated-seed text). The fix
for that defect makes `load_seed()` correctly LEAVE already-applied
pilot content alone; it does not, and was never meant to, give
`load_seed()` a second job of restoring PRE-pilot values on request --
it has no record of what those were. Using it to "roll back" would
therefore do nothing today (by design, post-fix) while still being the
wrong tool conceptually, and recommending it was itself a symptom of
conflating "the bulk reseed" with "a scoped import's own undo."

An honest targeted rollback, by identity:
- **E150d** (a new, isolated row, created only by this import, with
  `risk_assessment_available=False` so no existing product's Health
  Score or warnings ever depended on it): delete the single `ingredients`
  row `e150d_sulphite_ammonia_caramel`, its one `ingredient_localizations`
  `bg` row, and the two aliases `register_curated_alias` registered for
  it (its own common name, and the bare `E150d` alias). Scoped and safe
  specifically because nothing else in the catalog references this id.
- **E250/E330/E951** (updates to pre-existing rows): this import keeps
  no per-field "before" snapshot, so there is no automated targeted
  undo for these three -- only two real options, and both have explicit
  data-loss implications: (a) a full database restore from a backup
  taken immediately before `--apply` (loses ANY other writes made to the
  database between that backup and the restore, not just this import's
  changes -- back up immediately before every `--apply` specifically so
  this stays available and recent); or (b) a new, explicit corrective
  profile/import restoring the specific prior field values by hand,
  reviewed with the same owner-approval rigor as this one, since it is
  itself a content change to a live, existing ingredient.

## 8. Explicitly not done (per the task's own stop condition)

No merge, no deploy, no live database/container access, no secrets
changes, no bulk extraction rerun, no bulk dataset commit, no Android
code changes. The pilot import was never run against the live
`nutriguard-backend-db-1` container -- only against disposable
in-memory SQLite (tests, fixture generation) and one disposable,
destroyed-after-use Postgres container (migration-cycle check only, no
app data).
