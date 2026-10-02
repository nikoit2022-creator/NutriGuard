# CODEX_HANDOFF

## 2026-10-02 (latest): PR #32 (OpenFoodTox pilot) deployed and verified (Claude Code)

- Owner authorized deployment per issue #33, gated on PR #32 merged with
  green Backend/Android CI. Verified via `gh api`: PR #32
  (`feat/backend-openfoodtox-dataset-audit`, head `61ac7be42058ede0ebd1cc4060d124f207e50be2`)
  `merged=true`, `merge_commit_sha=f8379c2109e377866f93ace8ff7ed0029312c540`;
  fetched `origin/main` and confirmed that SHA was its exact tip (no
  unreviewed later commits). Pre-deploy local `main` was
  `65a8e689ebf7ba40ac4bdcb43ab2821b60c8916c` (PR #29).
- Preserved the sole pre-existing working-tree change (the 332-line PR #29
  handoff entry) across the fast-forward: both it and the incoming PR #32
  handoff entries insert at the top of this file, so a plain merge would
  conflict. Backed up `handoff.before.md` and `local-changes.patch` under
  `/home/vboxuser/nutriguard-backups/pr32-deploy/`, took a path-scoped
  `git stash push -- nutriguard-backend/docs/CODEX_HANDOFF.md` (kept, not
  dropped; SHA in `stash-sha.txt`), fast-forwarded, then reinserted the
  332 preserved lines verbatim immediately after the incoming PR #32
  entries and before the next pre-existing entry. No other file had
  working-tree changes.
- Backup created BEFORE stopping/updating anything:
  `/home/vboxuser/nutriguard-backups/pr32-deploy/nutriguard_pre_pr32.dump`.
  `docker exec nutriguard-backend-db-1 pg_dump -U nutriguard -d nutriguard -Fc`
  succeeded; archive is 147,881 bytes, created 2026-10-02 18:43:47 UTC.
  `pg_restore --list` (run inside the `db` container, host has no client)
  succeeded (exit 0), CUSTOM format, 88 TOC entries; listing retained as
  `backup.toc`. No live restore performed.
- `docker compose stop backend` completed at 18:43:57 UTC, BEFORE any
  fast-forward or source update. PostgreSQL and Redis stayed running and
  healthy throughout; no Compose down, volume operation, secret edit,
  Tailscale/firewall change, or unrelated service restart.
- Pre-migration database revision was exactly `d7e8f9a0b1c2`; the target
  migration `a8b9c0d1e2f3_ingredient_localization_owner_approval.py` has
  `down_revision = "d7e8f9a0b1c2"` -- an exact match, confirmed by reading
  the migration chain at `origin/main` before building.
- Rebuilt with `docker compose build backend` (pinned `Dockerfile`). Final
  image ID: `sha256:4bd250febfd0c6025ba96e441f29d2e7964ace7d17b62efa5add432a728a5735`.
  Full suite in that image, `--network none`, entrypoint bypassed
  (`--entrypoint ""`), isolated SQLite config: `python -m pytest -q
  -p no:cacheprovider`: **904 passed, 20 skipped, 2 warnings in 30.24s**.
  Skips are opt-in PostgreSQL-only tests. No suite was pointed at the
  live database.
- `docker compose up -d --no-deps backend` at 18:46:26 UTC used the
  documented entrypoint: DB-readiness wait, `alembic upgrade head`,
  startup `load_seed()` (`SEED_ON_START=true`, refreshed `retrieved_at`/
  `last_verified_at` on the 12 pre-existing curated ingredients -- existing,
  unrelated behavior, not part of this import). `alembic current` read
  `a8b9c0d1e2f3 (head)` immediately after start and again after the later
  restart below; single head throughout.
- Explicit dry-run of `python -m app.seed.load_openfoodtox_pilot_content`
  (no `--apply`) printed its proposed-changes plan: exactly identities
  E250 (update), E150d (create), E330 (update), E951 (update) -- no other
  ingredient, and no `risk_level`/`risk_assessment_available`/
  `verification_status`/dietary-flag field appeared in the plan, matching
  the task's constraint. Re-ran with `--apply`: one transaction, `Applied
  and committed.`
- Verified directly in Postgres after apply: `E150d` row has
  `risk_level=SAFE`, `risk_assessment_available=f`, `verification_status=
  LIMITED_DATA`, `acceptable_daily_intake`/`efsa_status`/`fda_status` all
  empty -- no invented ADI or risk badge. `E951.effect_conditions` is
  populated (372 chars, the PKU caveat). All four identities' new/updated
  BG localization rows: `translation_status=DRAFT`,
  `translation_source=MACHINE_TRANSLATED`, `owner_approved_without_review=t`
  -- never `REVIEWED`/`HUMAN_CURATED`. EN `description`/`purpose_in_food`/
  `health_concerns`/`references` all non-empty for all four identities.
- Row counts, immediately before vs. after the import (read-only `psql`
  queries, no product/user content printed): `products` 93->93,
  `ingredients` 390->391 (+1, the new E150d row), `ingredient_aliases`
  403->405 (+2, E150d's curated aliases), `ingredient_localizations`
  12->14 (+2, new BG rows for E150d and E330; E250/E951's existing BG
  rows were replaced in place, not inserted), `product_sources` 111->111,
  `scan_history` 55->55, `users`/`devices`/`user_health_profiles` 24/24/24
  unchanged, `refresh_tokens` 57->57. Only the four-identity import added
  rows; no product, user, or scan data was touched.
- `http://127.0.0.1:8000/health` and `https://ubuntu.taileaa26d.ts.net/health`
  both returned `{"status":"ok","version":"1.0.0"}` immediately after the
  import, AND again after a controlled `docker compose restart backend`
  (db/redis untouched) -- the restart re-ran `alembic upgrade head` and
  `load_seed()` a second time, and the pilot content and
  `ingredients`/`ingredient_localizations` counts were unchanged
  afterward, confirming PR #32's own fixes for its Defects 1-3 (startup
  reseed no longer reverts pilot content) hold on this exact deployment.
- Post-restart backend logs: 1,267 lines in the 10 minutes covering both
  starts, zero matches for traceback/exception/critical/error/HTTP 5xx
  (private OCR/image/credential content was never printed). No automatic
  monitoring was set up.
- **Rollback instructions (not executed -- no failure, no data loss):**
  stop `backend`; restore with `pg_restore` from
  `/home/vboxuser/nutriguard-backups/pr32-deploy/nutriguard_pre_pr32.dump`
  only with explicit owner approval (this would also discard the 10 live
  rows/users/scans created since the backup, independent of this
  deployment); for a source-only rollback, `git reset` is unnecessary --
  checking out `65a8e689ebf7ba40ac4bdcb43ab2821b60c8916c` and rebuilding
  returns to the pre-PR#32 image (this does not revert the DB migration
  or the four-identity content, which would need the backup restore
  above). No restore was needed or performed.
- Only this handoff file was modified in Git (still uncommitted, as this
  repo's convention has been for every prior deployment entry above). No
  commits/pushes, secret edits, Android changes, or unrelated branch
  changes. `.env`/`client_secret.json` contents were never read, printed,
  or modified.

**Recommended next step:** deployment complete and verified; no further
backend action required. Per issue #33, Codex will provide Android
Studio update/install steps next.

---

## 2026-10-02: application pilot integration regressions fixed (Claude)

Implemented `docs/OPENFOODTOX_APP_INTEGRATION_FIX_TASK.md` (Codex's
review of `78cbd5b`, baseline fast-forwarded to `68c76ee` first --
Android's pilot mapping, preserved verbatim, no Android files touched).
Same isolated worktree. Full SHA after this commit: see the commit this
entry ships in (`git log -1` on this branch).

**Defect 1 -- restart overwrote imported profiles.** `load_seed()`'s
unconditional `session.merge` (base `ingredients_seed.json` loop) and
`_load_bg_localizations`'s unconditional BG upsert both used to blindly
revert the four pilot identities' EN/BG content back to the old
curated-seed text on every container restart (`SEED_ON_START=true`
re-invokes `load_seed()`). Fixed by protection, not reapplication:
`load_seed.py` now checks each row's OWN `field_provenance_json` (the
pilot importer's own per-field provenance -- nothing else in this
codebase writes it) before reseeding, and carries the pilot-supplied
value (and the provenance blob itself, so the protection survives a
SECOND restart too) through the merge untouched; `_load_bg_localizations`
now also skips any BG row with `owner_approved_without_review=True`, the
same way it already skipped `HUMAN_CURATED` ones. Also gave `references`
its own field-provenance entry in `load_openfoodtox_pilot_content.py`
(`_apply_update`) -- it was the one updatable field the importer never
recorded, so it alone would have kept reverting. Every OTHER ingredient
and field reseeds exactly as before (verified directly).

**Defect 2 -- EN-only update made approved BG disappear.** `_apply_bg`
used to skip entirely on a `no_op` BG-text plan, including the hash
refresh -- so an EN-only approved artifact update (still calls
`_apply_bg`, since `en_changes` is non-empty) left the BG row's
`source_content_hash` pointing at the ingredient's PREVIOUS English
content, and `build_localizations` correctly (but unintentionally)
started treating the still-approved, unchanged BG prose as stale. Fixed
by only ever skipping entirely on `HUMAN_CURATED`; a true `no_op` now
still refreshes the hash/metadata (text fields stay untouched, since
`bg_values` is all `None` in that case). An ordinary, UNGOVERNED direct
edit to the EN column (never going through this importer) still
correctly invalidates BG -- verified directly, so this isn't a widened
gate.

**Defect 3 -- BG names were English.** `_apply_bg` initialized a new BG
row's `common_name` from the canonical English name. The artifact
(`app/seed/openfoodtox_pilot_profiles.json`, rebuilt via
`scripts/openfoodtox/build_app_pilot_content.py`) now carries an
explicit, owner-approved `common_name.bg` for all FOUR identities (not
just E150d) -- E250/E951 match this repo's own already-reviewed
`ingredients_seed_bg.json` entries exactly (no behavior change for
those two), E330/E150d match the headings already published in
`docs/OPENFOODTOX_PILOT_REVIEW_DRAFTS.md`. `_plan_bg`/`_apply_bg` now
carry and assert this name explicitly, for both a brand-new BG row and
an existing one (self-healing, not just at creation).

**Permanent regressions**: 9 new SQLite tests in
`tests/integration/test_openfoodtox_pilot_import.py` (3 classes, one
per defect) -- verified they fail against the pre-fix code (6 of 9;
the other 3 cover already-correct behavior the fix must not regress)
before restoring the fix. Focused suite: `python3 -m pytest -q
tests/integration/test_openfoodtox_pilot_import.py
tests/integration/test_openfoodtox_app_pilot_response_paths.py
tests/unit/test_ingredient_localization.py
tests/integration/test_load_seed.py` -- **35 passed**. Full suite:
`python3 -m pytest -q` -- **904 passed, 20 skipped** (16 pre-existing
opt-in Postgres + 4 new, see below), 3 warnings (pre-existing).

**Close existing delivery requirements**:
- OpenAPI regenerated inside a disposable `docker build` of this
  branch (pinned `requirements.txt`, Python 3.12-slim, matching
  `Dockerfile` exactly) -- confirmed the previously committed
  `openapi.json` actually had NEWER-environment drift (`format:
  binary` vs `contentMediaType`; `ValidationError`'s extra `input`/
  `ctx` fields), not the pinned deployment output. Replaced with the
  disposable image's exact output; zero other diff (no schema change
  from this round's fixes).
- Added `tests/postgres/test_openfoodtox_pilot_import_postgres.py`
  (opt-in, same convention as the rest of that directory): (a)
  `TestMigrationWithPreexistingRows` -- real `alembic` downgrade/upgrade
  around the `a8b9c0d1e2f3` migration with a raw pre-existing
  `ingredient_localizations` row inserted before it runs, asserting
  `owner_approved_without_review` defaults `false` for that row (not
  just for rows created after the column existed) -- separate,
  explicitly-named env var (`NUTRIGUARD_TEST_POSTGRES_MIGRATION_URL`)
  since this one mutates schema state; (b) the real import against a
  real `asyncpg` connection, plus real `POST /api/v1/scan/barcode` and
  `POST /api/v1/scan/label-image` response-path tests (external
  provider/Gemini mocked, no network) serving the pilot's
  owner-approved content -- the prior assignment only ever verified an
  empty-Postgres migration cycle plus SQLite-backed direct/listing/
  OCR-text/partial paths. Ran all of this against disposable,
  destroyed-after-use containers (never `nutriguard-backend-db-1`):
  migration test passed (pre-existing row's flag confirmed `false`
  post-migration); import + barcode + label-image tests passed (3/3,
  BG common names correctly localized through both real scan paths).
- `docs/OPENFOODTOX_APP_PILOT_INTEGRATION_REPORT.md` corrected in two
  places the task flagged: (1) withdrew the claim that this pilot
  needs no Android integration work -- E951's `effectConditions`
  carries the PKU exception, a safety-relevant caveat Android main
  didn't consume before Codex's `68c76ee` wiring; the report now says
  deployment must wait for both halves. (2) withdrew "re-running
  `load_seed()`'s own data" as a rollback recommendation (it was the
  very overwrite path defect 1 fixed, and was never actually able to
  restore pre-pilot values); replaced with a precise targeted-rollback
  description (delete the one isolated E150d row; for the three
  updates, an explicit backup-restore or a new reviewed corrective
  import -- no automated undo exists for those three).
- Regenerated `docs/openfoodtox_app_pilot_fixtures/*.json` (real bytes,
  `scripts/openfoodtox/generate_app_pilot_fixtures.py`) -- the only
  real diff per file is the corrected BG `commonName` (defect 3) plus
  this run's fresh timestamps. Re-ran Android's full unit suite against
  the regenerated fixtures (`android-app`'s own `build.gradle.kts`
  sources them directly as test resources): **123 passed, 0
  failures/errors/skips** -- unchanged from Codex's count, confirming
  the regeneration didn't disturb the Android mapping.

No merge, no deploy, no live database/container access (disposable
Postgres 16 + a disposable pinned-deps image this round, both destroyed
after use; `docker ps` confirmed only the pre-existing
`nutriguard-backend-*`/`ng-stage3-pg-dev` containers were ever running),
no secrets changes, no Android code changes (fixtures only, which
Android's own build reads as data).

## 2026-10-02: Android pilot mapping completed (Codex)

- Android DTO/entity/localization and Room v4->v5 migration now retain the
  pilot conditions, guidance and publication metadata; details keep caveats
  next to numeric intake rather than replacing narrative with bare numbers.
- Fixture-based Android suite: 123 tests, 0 failures/errors/skips; debug APK
  assembled. See android-app/OPENFOODTOX_PILOT_HANDOFF.md for scope and
  manual acceptance checklist. No physical-device test yet.
- Independently ran 35 focused existing backend tests (21 response/import/
  localization plus 14 artifact/migration tests): all passed. Three separate
  temporary review probes reproduced the regressions assigned below.
- Next: Claude fixes OPENFOODTOX_APP_INTEGRATION_FIX_TASK.md and regenerates
  fixtures; rerun Android suite before combined release. No merge/deploy.

## 2026-10-02: application integration review (Codex)

- Reviewed `78cbd5b`; existing targeted backend tests independently run:
  `pytest tests/integration/test_openfoodtox_pilot_import.py
  tests/integration/test_openfoodtox_app_pilot_response_paths.py
  tests/unit/test_ingredient_localization.py -q`: **21 passed** (local venv).
- Three temporary SQLite regression probes: **3 failed** as expected,
  proving seed-on-start overwrites pilot descriptions, EN-only artifact
  update loses BG via stale hash, and new BG names use English. Temporary
  probe file removed after execution; no production data touched.
- Added OPENFOODTOX_APP_INTEGRATION_FIX_TASK.md with exact reproduction,
  permanent-test requirements, pinned OpenAPI and remaining verification.
- Android work is in progress separately in this branch; preserve it.
  Next: Claude fixes backend and updates fixtures; Codex finishes Android.
  No merge/deploy; this update is not yet ready for device testing.

## 2026-10-01: owner-approved four-profile application integration implemented (Claude)

- Implemented `docs/OPENFOODTOX_APP_PILOT_INTEGRATION_TASK.md` (baseline
  `037b3ccbeb91b91e539aa24474674e6b9687dc1e`), same isolated worktree,
  fast-forwarded first. Full field contract, fixtures and verification in
  `docs/OPENFOODTOX_APP_PILOT_INTEGRATION_REPORT.md` -- summary only here.
  Backend-only; no Android code touched.
- New tracked content artifact `app/seed/openfoodtox_pilot_profiles.json`
  (built mechanically by `scripts/openfoodtox/build_app_pilot_content.py`
  directly from `editorial_content.py`'s structured dataclasses -- no
  Markdown parsing, no bulk VM dataset needed at runtime), and a new,
  separate, bounded import --
  `app/seed/load_openfoodtox_pilot_content.py` -- explicit four-identity
  allowlist, dry-run by default, `--apply` writes+commits one
  transaction, rollback-on-failure and idempotent re-run both tested.
  Deliberately NOT `load_seed.py`'s broad merge, which would have
  overwritten unrelated scientific fields.
- E250/E951 (existing `VERIFIED`) and E330 (existing `LIMITED_DATA`):
  only `description`/`purposeInFood`/`healthConcerns`/`effectConditions`/
  `dietaryGuidance`/`references` updated to the OpenFoodTox pilot
  content. `riskLevel`, `riskAssessmentAvailable`, `verificationStatus`,
  `efsaStatus`/`fdaStatus`, `acceptableDailyIntake`, every dietary/
  scoring flag -- all byte-for-byte unchanged (tested directly, plus a
  real product's Health Score computed before/after the import and
  asserted identical). E330 is never promoted to `VERIFIED`.
- E150d provisioned as a new, distinct ingredient (`LIMITED_DATA`,
  `riskAssessmentAvailable=false`, `riskLevel=SAFE` placeholder --
  excluded from Health Score scoring by construction). Its group ADI
  figure stays withheld (`acceptableDailyIntake=""`) because the
  pilot's own numeric-eligibility check never resolved its chemical
  basis -- owner publication permission for the narrative content does
  not resolve that separately; `dietaryGuidance` explains the shared
  group limit in prose instead of asserting a number.
- New, narrowly scoped owner-approved-publication mechanism: one
  additive migration (`a8b9c0d1e2f3`, boolean
  `ingredient_localizations.owner_approved_without_review`, default
  `false`) lets a row be served while honestly `DRAFT` (never relabeled
  `REVIEWED`/`HUMAN_CURATED`) when an owner has explicitly approved that
  specific content. Verified reversible against a disposable,
  isolated Postgres 16 container (destroyed after use, never the live
  `nutriguard-backend-db-1` stack).
- Verified consistently across direct ingredient, product, OCR-text, and
  partial-result response paths (label-image's general mechanism was
  already covered by existing tests); real, regeneratable response
  fixtures for Codex in `docs/openfoodtox_app_pilot_fixtures/`
  (`scripts/openfoodtox/generate_app_pilot_fixtures.py`).
- `openapi.json` regenerated: the only contract-relevant change is an
  additive `ownerApprovedWithoutReview: boolean = false` field on
  `IngredientLocalizedTextOut`; the rest of the diff is pre-existing,
  unrelated FastAPI/Pydantic version drift (confirmed by regenerating
  against the unmodified tree first -- same drift, zero code changes).
- Tests: 30 new, 1 updated (a migration-head assertion loosened to
  accommodate the new migration on top) this pass. Full backend suite
  `python -m pytest -q`: **895 passed, 16 skipped** (pre-existing), 0
  failed.
- No merge, no deploy, no live database/container access, no secrets
  changes, no bulk extraction rerun, no bulk dataset commit, no Android
  changes. Stopping per the task's own instruction -- ready for Codex to
  wire/verify Android against the published fixtures.

## 2026-10-01: owner-approved four-profile application integration assigned

- Owner accepts source-backed pilot content for display without an outside
  expert; do not misrepresent this as independent scientific review.
- Compared main `65a8e68` to pilot `037b3cc`: changes remain offline scripts,
  tests and docs; no runtime import or app content delivery yet.
- Inspected localization publication gate, seed merge and Android detail
  rendering. Android main does not consume effectConditions/dietaryGuidance/
  adiPopulationScope; numeric ADI replaces textual guidance, risking hidden
  qualifications if used without UI integration.
- Added OPENFOODTOX_APP_PILOT_INTEGRATION_TASK.md for Claude: scoped explicit
  import, honest display-publication permission, preserved scoring, exact
  response fixtures and tests. Codex owns subsequent Android mapping.
- Docs-only; `git diff --check` validation. No backend suite run by Codex,
  no live changes, merge or deploy. Next: Claude implements/pushes contract
  and backend; Codex verifies Android before device-test release.

## 2026-10-01: bounded source closure resolved (Claude)

- Implemented `docs/OPENFOODTOX_SOURCE_CLOSURE_TASK.md` (reviewed baseline
  `5cc732a`), same isolated worktree, fast-forwarded first. Committed at
  `40a2d174845cc2a990bfec76ea9a284311af3e3b`. Full account in
  `docs/OPENFOODTOX_PILOT_REPORT.md` §14 -- summary only here. A subagent
  did the primary-source research (reading actual documents, never a
  search-result summary); this session independently reviewed every
  quote/locator before using it.
- All 6 named targets closed with a directly-read primary source, none
  withdrawn to operator-only:
  - E250 purpose -> EFSA 2017 nitrite opinion, Section 3.1.6.
  - E330 purpose -> JECFA "CITRIC ACID" monograph (FNP 52 Add 7, 1999),
    "Functional uses" field -- honestly flagged as a JECFA/FAO-WHO
    reference rather than EU-specific, since Reg. (EU) 231/2012's own
    E330 entry has no functional-class field at all.
  - E951 identity + purpose -> both from the EFSA 2013 aspartame opinion
    itself (genuine full-text PDF via Wayback, not a snippet).
  - **E150d-08 (group ADI), the most important target**: was labeled
    `external_primary_source` while its own citation admitted only
    "secondary reporting" -- a mislabeling bug, independent of whether the
    claim was correct (it was). Fixed by actually reading the full 2011
    EFSA caramel-colours opinion, confirming the 300 mg/kg bw/day group
    ADI, the 100 mg/kg bw/day Class-III-only (E150c) sub-ADI for THI
    immunotoxicity, and that E150d itself has no separate limit.
  - E150d-07 purpose -> replaced a vague "background section" reference
    with an exact page locator and quote naming cola-type drinks, spirits,
    sauces and baked goods.
- Extended the existing seed-only provenance regression to
  `identity`/`purpose` fields (previously effects-only), and added a new
  test (`test_no_external_primary_source_admits_only_secondary_confirmation`)
  to catch the E150d-08 mislabeling pattern going forward.
- Tests: 2 new/extended this pass. Full backend suite `python -m pytest -q`:
  **866 passed, 16 skipped** (pre-existing), 0 failed.
- Pilot rerun (extraction untouched) into a new `pilot/v8/` (`pilot/v1`-`v7`
  preserved), all four identities `exact_match`, clean-tree provenance,
  repeatability re-verified. Both committed docs regenerated via
  `export_docs` and pass `--check`.
- No live database access, no API/Health Score change, no container
  restart, no deploy, no merge, no seed import.

## 2026-10-01: bounded source closure assigned (Codex)

- Reviewed `5cc732a`: generated EN/BG headings and export/check mechanism
  address previous document drift. Four identity/purpose entries remain
  seed-only; E150d-08 still labels secondary verification as primary.
- Added `docs/OPENFOODTOX_SOURCE_CLOSURE_TASK.md` with exact targets and
  support-or-withhold completion criteria; adjacent vague E150d-07 citation
  included. Subagents permitted. No new product scope.
- Documentation-only change; read repo instructions/latest handoff, checked
  clean worktree and remote, fast-forwarded this isolated detached checkout.
  Validation: `git diff --check`; backend suite not rerun by Codex.
- Next: Claude implements this task, regenerates/checks documents, tests,
  commits and pushes on the same branch. No merge/deploy authorized.

## 2026-10-01: delivery consistency gaps closed (Claude)

- Implemented `docs/OPENFOODTOX_DELIVERY_CONSISTENCY_TASK.md` (reviewed
  baseline `8ceba0531082e2c63cbe62598af663c9f852ece9`) in the same isolated
  worktree, fast-forwarded first (no drift). Committed at
  `d573d821770dcc5f147b7c5c4b502ddd0d580320`. Full account in
  `docs/OPENFOODTOX_PILOT_REPORT.md` §13; regenerated, check-verified
  complete EN/BG drafts in `docs/OPENFOODTOX_PILOT_REVIEW_DRAFTS.md` and the
  claim matrix in `docs/OPENFOODTOX_CLAIM_SOURCE_MATRIX.md` -- summary only
  here. Owner authorized subagent use; one general-purpose subagent did the
  primary-source research (reading actual documents, not snippets), which
  this session independently spot-checked before using (reproduced the
  EFSA-nitrite PMC mirror and the EUR-Lex-blocked pattern directly).
- **Item 1 root cause found**: every previous round had exported the
  committed EN/BG previews by hand (read a profile's own `.md` output,
  retype/reformat into the review-drafts document), which is exactly what
  let the committed file drift -- it still said "reproduces pilot/v5" while
  containing the pre-fix `каква точно количество` grammar error and other
  stale text. Fixed structurally: new `scripts/openfoodtox/export_docs.py`
  mechanically generates both committed docs with no manual step, and its
  `--check` mode exits non-zero with a line-level diff on any mismatch
  (including whitespace) -- verified `CHECK OK` for both docs against a
  freshly-rerun `pilot/v7/` in this session. Review-drafts format changed
  from hand-prefixed blockquotes to fenced code blocks (more reliable for
  verbatim reproduction), documented as a deliberate wrapper change.
- **Item 2**: audited the claim matrix against its own "never seed-only for
  effect conclusions" claim and found 3 contradicting rows (not just the
  2 named examples). Upgraded all 3 to directly-read primary sources: E250's
  human-evidence and nitrosation claims now cite the actual EFSA 2017
  nitrite opinion (read via an open-access PMC mirror, since efsa.europa.eu/
  Wiley both block automated fetching as in every prior round) -- and the
  nitrosation claim was *corrected*, not just re-sourced: the primary text
  showed nitrosation considerations actually shaped the ADI-derivation
  benchmark-response choice itself, the reverse of what was previously
  claimed. E951's digestion/metabolism claim now cites EFSA's 2013 aspartame
  opinion abstract directly (near-verbatim match) plus a JECFA/WHO
  corroboration. **E330's regulation quote, re-verified per the task's
  explicit "don't trust the prior report" instruction, was found to be
  wrong** -- the real Reg. (EU) 231/2012 "Definition" text (read via an
  archived EUR-Lex snapshot, cross-checked against the UK's official
  statutory-text mirror) allows citrus-juice extraction as an alternative to
  fermentation, names Candida spp. as an alternative organism, and includes
  a "non-toxicogenic strains" qualifier the prior paraphrase dropped while
  inventing a "glucose syrups" detail not in the source at all -- corrected.
  New regression test enforces going forward that no consumer-facing
  effect/human claim may rely solely on seed data. **Scope boundary stated
  honestly**: several `purpose`/`identity` fields (lower-stakes,
  non-safety) remain seed-only -- not claimed fixed, explicitly flagged as
  a remaining gap in the report rather than silently left.
- Tests: 6 new/updated this pass. Full backend suite `python -m pytest -q`:
  **865 passed, 16 skipped** (pre-existing), 0 failed.
- Per the task's own instruction, extraction code was untouched -- only the
  pilot previews were rerun into a new `pilot/v7/` (`pilot/v1`-`v6`
  preserved), verified repeatable.
- No import into the live database, no API/Health Score change, no container
  restart, no deploy, no merge, no seed import. Originals and the live
  stack were not touched.

## 2026-10-01: delivery consistency follow-up assigned (Codex)

- Reviewed remote `8ceba0531082e2c63cbe62598af663c9f852ece9` read-only:
  rendering fixes are present, but committed previews retain old text and
  v4 pointers; the source matrix still uses seed-only attribution for some
  consumer claims while its audit note says otherwise.
- Added `docs/OPENFOODTOX_DELIVERY_CONSISTENCY_TASK.md`: two bounded fixes,
  reproducible preview export/equality and claim-level primary evidence.
  Owner permits Claude to use subagents as needed.
- Documentation-only handoff; inspected Git diff/status and handoff;
  `git diff --check` is the relevant validation. Backend tests not rerun by
  Codex; Claude's 860 passed / 16 skipped remains a reported result.
- Next: Claude reads the new task from this branch, implements and verifies,
  commits/pushes a report. No merge/deploy or live data operations authorized.

## 2026-10-01: targeted final profile corrections implemented (Claude)

- Implemented `docs/OPENFOODTOX_FINAL_PROFILE_REVIEW_TASK.md` (reviewed
  baseline `b915ccdabc23729110d41e32df15876adb7863ad`) in the same isolated
  worktree, fast-forwarded first (no drift). Committed at
  `704d5b7e2b60a5934fa2dbf777176baf185b9b28` (code/tests/matrix) and
  `b051e2a2242b14b635fa3ca6bae6c4b2a94b2ca8` (one-line follow-up fixing a
  dangling cross-reference the first commit introduced). Full account in
  `docs/OPENFOODTOX_PILOT_REPORT.md` §12; corrected complete EN/BG drafts in
  `docs/OPENFOODTOX_PILOT_REVIEW_DRAFTS.md` (revision 3) -- summary only
  here.
- **Item 1 confirmed and fixed**: the previous round's numeric-gating fix
  only covered structured reference-value rendering -- `editorial_content.py`'s
  E150d effects text separately stated the group ADI (300 mg/kg bw/day) and
  E150c's sub-limit (100 mg/kg bw/day) as plain narrative, bypassing the
  gate entirely, then the next paragraph said the figure "is not shown...
  pending review" -- a direct self-contradiction. Both numbers moved to a
  new `operator_only_notes` field (never rendered in either draft, exposed
  as `editorial_operator_only_notes` in the profile JSON). A new mechanical
  test scans every consumer-facing editorial field for a bare dose-shaped
  number, for every identity without an independently-confirmed basis
  override -- covers all fields/evidence_types/source_kinds uniformly, so
  relabeling can't bypass it. Confirmed unrelated numbers (years, E-code
  digits) are untouched by the same check. Also removed two
  technical/debug-sounding fallback sentences from consumer text
  ("pending review of this preview's eligibility criteria... see internal
  review notes") -- replaced with plain language; full reasons remain in
  `review_eligibility.reasons`.
- **Item 2**: added explicit EN/BG display names for all four identities,
  used only for the draft heading -- E150d's heading no longer reads "(ad
  hoc query, not a provisioned NutriGuard ingredient: E150d)" (still present
  verbatim as real operator metadata in `catalogue_identity.common_name`,
  just never in the consumer-facing title). Fixed a BG grammar error
  ("каква точно количество" -> "какво точно количество" -- количество is
  neuter).
- **Item 3**: found and fixed one genuinely vague citation (E330's identity
  claim cited "standard food-chemistry references" -- no actual source
  named); replaced with Commission Regulation (EU) No 231/2012's own E330
  Definition text, quoted directly. Audited every other citation against
  what it actually supports. New `docs/OPENFOODTOX_CLAIM_SOURCE_MATRIX.md`:
  all 21 editorial claims with a stable ID, source kind, and citation,
  generated directly from `editorial_content.py`, reviewable via Git.
- **Test-count reconciliation**: checked directly -- no committed document
  ever stated "858 passed"; `docs/CODEX_HANDOFF.md` and
  `docs/OPENFOODTOX_PILOT_REPORT.md` both correctly recorded the actual
  `pytest -q` output of 853 at `b915ccd`. "858" was this session's own
  conversational arithmetic (853 + 5 new, double-counting since 853 already
  included them) -- nothing to correct in either committed file. This
  round's own count, run directly: `python -m pytest -q` -> **860 passed,
  16 skipped** (pre-existing), 0 failed (853 + 7 new this round).
- Per the task's own instruction, extraction code was untouched this round
  -- `staging/v4`/`reports/v4` were **not** regenerated; only the pilot
  previews were rerun (against the unchanged `staging/v4`) into a new
  `pilot/v5/` (`pilot/v1`-`v4` preserved), verified repeatable.
- No import into the live database, no API/Health Score change, no container
  restart, no deploy, no merge. Originals and the live stack were not
  touched.

## 2026-10-01: targeted final profile review assigned (Codex, docs only)

- Added docs/OPENFOODTOX_FINAL_PROFILE_REVIEW_TASK.md against b915ccd: close
  editorial numeric-gating bypass, localize headings, and commit precise primary
  source mappings instead of relying on seed files/vague references as evidence.
- Requested reconciliation of summary test count (858) versus committed handoff
  (853); neither was independently rerun by Codex. Docs-only git diff --check.
- No implementation/live changes, main merge or deployment. Next: Claude fetches
  this task, applies only scoped corrections and returns regenerated drafts/tests.

## 2026-10-01: four-profile content completion assigned (Codex, docs only)

- Reviewed 40d063444358086c2b47dbe1a2a7386b9509349d drafts and official EFSA
  publication 10259. Added docs/OPENFOODTOX_PROFILE_CONTENT_TASK.md for Claude.
- Required: preserve E951 PKU applicability exception and E150d group-ADI scope in
  EN/BG consumer previews, substantive sourced identity/function/effects text,
  readable Bulgarian and consistent numeric gating across headings.
- Date correction to previous entries: publication 10259 was published 2026-09-10;
  its page lists approval on 2026-07-01. Prior "adopted 2026-09-10" wording is incorrect.
- Documentation-only task; git diff --check used. No runtime tests rerun, live
  access, content import, merge or deployment. Previous test counts are Claude's.
- Next: Claude fetches the content task, pushes four complete source-linked DRAFT
  profiles and tests/report as applicable; Codex reviews before integration.

## 2026-10-01: substantive EN/BG profile content implemented (Claude)

- Implemented `docs/OPENFOODTOX_PROFILE_CONTENT_TASK.md` (reviewed baseline
  `40d063444358086c2b47dbe1a2a7386b9509349d`) in the same isolated worktree,
  fast-forwarded first (no drift). Code committed at
  `631939a120ad7bf79f1d7f95ab9d821caf741916`. Full account in
  `docs/OPENFOODTOX_PILOT_REPORT.md` §11; complete EN/BG drafts for all four
  identities (not abbreviated) in `docs/OPENFOODTOX_PILOT_REVIEW_DRAFTS.md`
  (revision 2) -- summary only here.
- New `scripts/openfoodtox/editorial_content.py`: a separately versioned,
  fixed/reviewed (never live-model-generated) content layer providing
  source-cited "What it is"/"Purpose in food"/"Relevant effects" text per
  substance -- now actually populated in the drafts (previously always
  omitted). Includes one `editorial_chemical_basis` override, applied only
  to E951 after directly reading all five of its dossiers' own text and
  confirming aspartame has no salt/ion basis ambiguity (unlike sodium
  nitrite) -- kept explicitly distinct from the automated field, and
  deliberately *not* applied to E150d (basis kept unresolved, never
  guessed, per the task's own instruction).
- **Fixed the numeric-gating inconsistency the task flagged**: a value's
  magnitude could previously appear in "Effects and conditions" while being
  hidden from "Intake guidance". Both sections now use the identical
  `consumer_guidance_eligible` test; "Intake guidance" is now actually shown
  for eligible values (E250 automated, E951 via the editorial override)
  rather than unconditionally omitted. Feed/worker (FEEDAP) findings are now
  excluded from the consumer draft entirely (confirmed on E330's 4 feed
  values), not merely caveated inline.
- **Four content corrections applied**: E951's PKU exclusion now appears in
  both drafts, twice, adjacent to every shown ADI/safety-conclusion claim.
  E150d's group ADI (300 mg/kg bw/day for all four caramel colours
  combined; E150c's own separate 100 mg/kg bw/day sub-ADI given as context;
  E150d's own basis kept unresolved) is precisely scoped. The E962 opinion's
  First-published (10 September 2026) and Approved (1 July 2026) dates are
  now kept distinct (previously conflated as "adopted September 10"), and
  its E951 work is now described as an update *within* the E962
  re-evaluation, not a standalone E951 re-evaluation or a claim the 2013
  opinion was wholly superseded. BG text now translates exact-equivalent
  terms (натриев нитрит, мг/кг телесно тегло дневно, etc.) instead of
  leaving them in English.
- **Found and fixed two real bugs while building this**: (1) a verbosity
  bug -- E951's five real assessments never merged for display (their
  justification wording differs slightly each time), so a now much longer
  per-value sentence was repeated 5x in each of two sections; fixed with a
  display-only coarser grouping, full per-assessment detail preserved
  internally. (2) a genuine "English placeholder in the Bulgarian body"
  bug -- the ineligible-value fallback sentence was interpolating raw
  English `review_eligibility.reasons` strings directly into Bulgarian
  text; replaced with a fully-Bulgarian generic phrase.
- Tests: 5 new (`TestEditorialContentCorrections`: PKU adjacency, E150d
  group-scope preservation, numeric-gating consistency, E962 date-type
  distinctness, feed-value exclusion). Full backend suite
  `python -m pytest -q`: **853 passed, 16 skipped** (pre-existing), 0
  failed. Per the task's own instruction, `staging/v4`/`reports/v4` were
  **not** regenerated this round (no extraction code changed) -- only the
  pilot was rerun (against the unchanged `staging/v4`) into a new
  `pilot/v4/`, verified repeatable (diffed twice, identical).
- No import into the live database, no API/Health Score change, no container
  restart, no deploy, no merge. Originals and the live stack were not
  touched. Remaining blockers in `docs/OPENFOODTOX_PILOT_REPORT.md` §10/§11
  (catalogue CAS gap; every profile still `not_reviewed`; reuse-clearance
  decision pending; E951's located newer opinion and E330's unresolved
  freshness not yet incorporated; E150d/E330 still have no eligible numeric
  value at all).

## 2026-10-01: pilot review corrections implemented (Claude)

- Implemented `docs/OPENFOODTOX_PILOT_REVIEW_TASK.md` (reviewed baseline
  `63c4d4e66de03a18a4dcc0b41b3d82b634bc2f35`) in the same isolated worktree,
  fast-forwarded first (no drift). Code/tests committed at
  `0beb2717491ec8e4e8a5c89b1e63b8b433c3ce33`; full account in
  `docs/OPENFOODTOX_PILOT_REPORT.md` (now its own revision, with a
  correction notice at the top preserving what was wrong and why --
  summary only here).
- **Item 1 (source identity)**: independently re-verified Codex's finding
  (fetched the actual EFSA/Wiley page myself rather than trusting the
  correction) -- `10.2903/j.efsa.2020.6032` assesses E472a-f esters, not
  E330. Removed the wrong claim. Audited all four pilot references, not
  just the flagged one: found a genuinely newer, precisely-dated superseding
  opinion for **E951** (`10.2903/j.efsa.2026.10259`, adopted 2026-09-10,
  confirmed via EFSA's own plain-language summary to update E951's
  toxicological/exposure assessment post-IARC-2023, ADI reaffirmed
  unchanged at 40 mg/kg bw/day -- not in this dataset at all) and a partial
  2012 exposure-only update for **E150d**
  (`10.2903/j.efsa.2012.3030`); confirmed **E330** has no completed
  standalone re-evaluation yet (explicitly pending/low-priority per
  Commission Regulation (EU) No 1419/2020's own text) -- freshness
  genuinely unresolved there, not a confirmed gap as previously claimed.
  Full source-check table (DOI/title/actual assessed identifiers/relevance/
  superseding-status evidence) in the report §9.
- **Item 2 (review_eligible)**: replaced the single bool with
  `ReviewEligibility` (`operator_inspectable` always true; strict,
  fail-closed `consumer_guidance_eligible` now also checking
  `evidence_complete`, identity completeness, resolved subject linkage,
  and excluding AOEL/AAOEL unconditionally as occupational-not-consumer
  levels). Real-data consequence: only E250 now has any
  `consumer_guidance_eligible` reference value among all four pilot
  substances -- a materially more conservative, more correct result. 12 new
  tests.
- **Item 3 (matcher truth table)**: fixed `_compare_one` -- both identifiers
  present but *neither* agreeing was wrongly reported `conflicting` (would
  have flagged every unrelated, fully-identified dossier in the dataset
  against every query); now correctly reported as no hit. 2 new tests,
  including one streaming the exact identity + a real conflict + 50
  unrelated records through one query.
- **Item 4 (stale data)**: regenerated the full catalogue/identity-audit
  (`staging/v4`, `reports/v4`, 11,613/11,613 ok, 75.4s) from the clean
  `0beb271` commit, recorded via a new shared
  `scripts/openfoodtox/provenance.py` (git SHA + dirty-tree + diff
  fingerprint) and a new `records.EXTRACTION_LOGIC_VERSION` marker (now 2).
  `staging/v3`/`reports/v3` marked unsuitable-for-evidence (plain-text note,
  outside Git) rather than deleted. Measured dataset-wide: `resolved`
  chemical-basis count rose 42->47 (+5 genuinely new correct resolutions),
  the corruption signature (simultaneous `value`+`lower_value`/`upper_value`)
  dropped 576->0, with the mechanism for a 211/212-record
  `unsupported_unit`<->`no_mention` reclassification traced and explained
  in the report. Pilot rerun against `staging/v4` into `pilot/v2/`
  (`pilot/v1/` preserved); repeatable (diffed twice, identical).
- **Item 5 (bilingual drafts)**: redesigned -- EN/BG drafts are now genuine
  readable paraphrases built only from structured fields, never the source's
  own text presented under a BG heading (what the first revision did,
  correctly flagged by this review). Verbatim source quotes moved to a
  separate internal-evidence section. Omitted-field notes retained
  internally even when the section itself is hidden from the drafts.
  Concise samples for all four pilot identities committed in the new
  `docs/OPENFOODTOX_PILOT_REVIEW_DRAFTS.md` for Git-based review.
- Tests: 17 new/updated this pass (matcher +2, evidence_bundle +13 net).
  Full backend suite `python -m pytest -q`: **848 passed, 16 skipped**
  (pre-existing), 0 failed.
- No import into the live database, no API/Health Score change, no container
  restart, no deploy, no merge. Originals and the live stack were not
  touched. Remaining blockers unchanged in kind, updated in detail, in
  `docs/OPENFOODTOX_PILOT_REPORT.md` §10 (catalogue CAS gap; every profile
  still `not_reviewed`; reuse-clearance decision pending; E951's located
  newer opinion and E330's unresolved freshness not yet incorporated; only
  one of four substances currently has any consumer_guidance_eligible
  value at all).

## 2026-10-01: pilot review corrections assigned (Codex, docs only)

- Reviewed 4db31ecbaaae5c113398004577748ae7046080f6. Follow-up instructions are in
  docs/OPENFOODTOX_PILOT_REVIEW_TASK.md: source identity, completeness gating,
  unrelated identifier pairs, stale catalogue replacement and substantive EN/BG drafts.
- Correction to the preceding Claude report: official EFSA publication 6032 covers
  E472a-f, not a dedicated E330 re-evaluation. Its citation does not establish the
  claimed E330 freshness gap. Checked official page on 2026-10-01; follow-up must
  correct the report and reassess freshness without assuming a replacement source.
- Tests not rerun by Codex; documentation-only diff checked with git diff --check.
  No live access, implementation changes, merge or deployment in this handoff.
- Next: Claude fetches the new review task, implements/tests the offline corrections
  and returns source-linked bilingual review samples through Git.

## 2026-10-01: offline matching/profile pilot implemented (Claude)

- Implemented `docs/OPENFOODTOX_PILOT_TASK.md` (reviewed baseline
  `56aee7aa613b8b7cc5f98e4082981ac0d898fc92`) in the same isolated worktree,
  fast-forwarded first (no drift). Full detail, measured results, sample
  drafts, limitations, source links and reuse/freshness findings in
  `docs/OPENFOODTOX_PILOT_REPORT.md` -- summary only here.
- New: `scripts/openfoodtox/catalogue_snapshot.py` (offline NutriGuard
  catalogue snapshot from the tracked seed files -- zero database access, not
  even disposable), `matcher.py` (streaming dry-run CAS/E-number matching +
  document/subject-link resolution), `evidence_bundle.py` (per-identity
  review bundle + DRAFT EN/BG text), `pilot.py` (CLI orchestrator).
- **Found and fixed a real extraction defect** while building the first
  profile: `records.py::derive_reference_values` was capturing
  `AssessmentBody/value` (a sibling classification code, self-describing via
  its own `other` leaf) as if it were the reference value's own numeric
  magnitude whenever a leaf happened to be named `value`. For aspartame's
  2013 ADI this produced a nonsensical `value: "1342"` alongside the correct
  `lower_value: "40"`, and would have fed the wrong number into
  chemical-basis matching too (`entry["value"] or entry["lower_value"]`
  prefers the former). Fixed with an explicit allowlist of the five
  confirmed value-holding wrapper tags (`Adi`/`Arfd`/`Aoel`/`Aaoel`/
  `RefValue`); measured impact: 788 of 25,973 reference values dataset-wide
  (~3.0%) had this corruption. 2 new regression tests in
  `tests/unit/test_openfoodtox_records.py`.
- `dossier.py` extended to carry `manifest_uuid`/`manifest_links` through to
  reference values and endpoints (previously dropped), needed for
  `matcher.resolve_subject_links` to correctly handle the 333/11,613 (2.9%)
  dossiers with more than one `REFERENCE_SUBSTANCE` -- none of this pilot's
  four substances needed that path, but the general matcher now guards
  against it instead of assuming one identity per archive.
- Pilot run (`E250`, `E150d`, `E330`, `E951`): all four `exact_match`.
  E250/E951/E330 matched tracked NutriGuard catalogue entries (E-number
  only -- **the tracked catalogue has no CAS numbers at all today**, a
  reported limitation, not a matcher one); E150d has no catalogue entry and
  was run as an explicit ad hoc dataset query, clearly labeled as not a
  catalogue linkage. Concrete finding: 2 of E330's 3 matched dossiers are
  EFSA FEEDAP (animal-feed) opinions, correctly flagged and excluded from
  `review_eligible`; the dataset's one human-food ADI entry for citric acid
  has no extractable numeric magnitude at all ("not limited", JECFA 1974).
  Reuse/freshness check found OpenFoodTox is CC BY 4.0 (attribution
  required; third-party literature full text not cleared by this) and a
  concrete freshness gap for E330 (a 2020-03-11 EFSA re-evaluation postdates
  everything in this dataset for that substance and is not present in it).
- Tests: 28 new (`test_openfoodtox_{matcher,catalogue_snapshot,
  evidence_bundle}.py` + 2 in `test_openfoodtox_records.py`), all synthetic
  fixtures. Full backend suite `python -m pytest -q`: **833 passed, 16
  skipped** (pre-existing), 0 failed. Ran the pilot twice end-to-end and
  diffed every output byte-for-byte (minus the timestamp): identical --
  deterministic, no implicit network calls.
- Generated pilot output (`pilot_summary.json` + 4 profile JSON/MD pairs)
  lives outside Git at `/home/vboxuser/nutriguard-data/openfoodtox/pilot/v1/`
  -- bulk/generated, per task scope, same convention as prior rounds'
  `staging/`/`reports/` output.
- No import into the live database, no API/Health Score change, no container
  restart, no deploy, no merge. Originals and the live stack were not
  touched. Integration blockers are listed in full in
  `docs/OPENFOODTOX_PILOT_REPORT.md` §10 (catalogue CAS gap, pending
  scientific/translation review on every drafted claim, pending reuse-clearance
  decision, E330's located-but-unincorporated newer opinion).

## 2026-10-01: offline matching/profile pilot assigned (Codex, docs only)

- Added docs/OPENFOODTOX_PILOT_TASK.md for Claude: deterministic dry-run matching
  against seed/supplied catalogue snapshots, exact identifiers, evidence linkage,
  four requested pilot identities, source/reuse checks and tests. No live DB access.
- Added docs/OPENFOODTOX_PROFILE_PRESENTATION.md: EN/BG section order, per-claim
  provenance and display gates; draft review specification, not runtime UI/API changes.
- Baseline af195fd2a63b35ebd9496e0081f66f30893ed5bd; docs-only verification:
  git diff --check. No runtime tests needed for this handoff; no merge/deploy/import.
- Next: Claude fetches the pilot task, implements offline tooling and returns committed
  report; Codex reviews actual pilot drafts before application integration. Scientific
  content, reuse/freshness clearance and live integration remain pending.

## 2026-10-01: chemical-basis ambiguity follow-up implemented (Claude)

- Implemented the "Active follow-up: chemical-basis ambiguity" section of
  `docs/OPENFOODTOX_REVIEW_TASK.md` (reviewed baseline
  `f997bb7741cb858434163a935d30a1bde69c941d`) in the same isolated worktree,
  fast-forwarded to the handoff commit `6f06a77` first (no drift).
- Confirmed the reported defect in `chemical_basis.py::extract_chemical_basis`:
  the first numerically-matching mention became `primary` (resolved) while later
  matching mentions with a *different* basis were filed under `other_values_mentioned`
  — i.e. ambiguity was silently resolved by picking the first candidate.
- Fixed: all matching candidates are now collected before resolution; two or more
  distinct normalized bases for the same stored value now produce a new
  `ambiguous_multiple_bases` status with `basis: null` and every candidate preserved
  in `matching_value_mentions` (never a silent pick). Repeated mentions of the same
  basis (case/whitespace-normalized) still resolve normally.
- Also fixed, per the task's explicit requirements: exact `decimal.Decimal` comparison
  (not float tolerance); numeric-token-boundary guards so decimal-comma (`0,1`) and
  scientific notation (`1e-5`) can never be partially matched as a different number
  (found and fixed a real gap here during testing: the original lookbehind didn't
  exclude a preceding exponent sign, so `1e-5` let the `5` alone match); and unit
  validation — `extract_chemical_basis` now requires the stored value's *decoded* unit
  to be in the `mg/kg bw` family before attempting any match, producing
  `unresolved_unsupported_unit` otherwise (moved the call site in `records.py` to run
  after unit-code decoding so this is possible).
- Tests: 15 new cases (`TestAmbiguousMultipleBases`, `TestUnitValidation`,
  `TestNumericTokenBoundaries`) added to `tests/unit/test_openfoodtox_chemical_basis.py`
  (23 tests total in that file). `python -m pytest -q` (full backend suite, from
  `nutriguard-backend/`): **805 passed, 16 skipped** (pre-existing), 0 failed.
- Reran the full offline extraction/audit into a new versioned output folder
  (`staging/v3/`, `reports/v3/`); `staging/`/`reports/` (2026-09-30) and
  `staging/v2/`/`reports/v2/` (first review round) preserved unchanged.
- **Measured real-dataset impact** (25,973 reference values across all 11,613
  dossiers): `resolved` unchanged at 42 (same 41 unique documents, identical basis
  and value in both versions — zero regressions); `unresolved_no_mention` dropped
  from 25,842 to 14,431 and a new `unresolved_unsupported_unit` bucket holds 11,479
  (records whose unit isn't mg/kg-bw-family, now correctly classified instead of
  silently falling into "no mention"); `unresolved_no_exact_match` dropped from 89 to
  21 for the same reason; **`ambiguous_multiple_bases`: 0** — confirms the reviewer's
  own framing that this was a code-review finding, not an actual ambiguity in the real
  dataset. The E250 profile (`reports/v3/e250_sodium_nitrite_profile.md`) is
  byte-identical to `reports/v2/`'s.
- `docs/OPENFOODTOX_DATASET_AUDIT.md` updated: new §8.4 with the full before/after
  table and explanation, §3/§6/§7/§9 pointers updated to `v3` as current.
- No import into the live database, no API/Health Score change, no container
  restart, no deploy, no merge. Originals and the live stack were not touched.

## 2026-10-01: chemical-basis ambiguity follow-up (Codex, docs only)

- Reviewed `f997bb7741cb858434163a935d30a1bde69c941d`; added the active follow-up
  section in `docs/OPENFOODTOX_REVIEW_TASK.md` for Claude.
- Source review found first-match selection when equal numeric mentions refer to
  different chemical bases. Requested conservative ambiguity handling, numeric/unit
  guards and regression tests. This does not assert a defect in the actual E250 data.
- No code changes or live access. Python probe unavailable (`python` not on PATH);
  tests not rerun. Documentation whitespace checked with `git diff --check`.
- Next: Claude fetches this branch, completes the active follow-up and pushes results.
  No merge into main, deployment or live import authorized by this handoff.

## 2026-10-01: OpenFoodTox audit review fixes implemented (Claude)

- Implemented all three fixes requested in `docs/OPENFOODTOX_REVIEW_TASK.md`
  (reviewed baseline `8dd62355281c147bc84c9266341a47b60a509846`) in an isolated
  worktree on `feat/backend-openfoodtox-dataset-audit`, fast-forwarded to the
  handoff commit `48981ae` first (no drift/conflicts).
- **E-number suffixes**: added `scripts/openfoodtox/e_numbers.py` — conservative,
  survey-driven recognition (plain, letter-suffixed, roman-numeral-qualified,
  trailing-qualifier, and range forms; rejects E/Z stereodescriptor look-alikes).
  Never collapses E150a/b/c/d; flags (does not silently resolve) multi-candidate
  conflicts. Measured impact: 495 -> 619 recognized records (124 previously missed),
  0 conflicts, across all 15,705 REFERENCE_SUBSTANCE records.
- **Bounded extraction vs. completeness**: measured the true per-document leaf
  count across all 221,377 documents (max 1,405; 467 docs/0.2% exceeded the old
  400-leaf cap, all environmental/physicochemical, none human-health/identity/
  reference-value). Fixed the truncation flag (was a false-positive-prone
  "counter hit zero" check; now compares the true complete count against the
  cap). Raised the cap to 4,000 (~2.8x margin) and added an independent 50,000-leaf
  hard safety ceiling. Added an `evidence_complete` quarantine flag propagated from
  document -> dossier -> catalogue/identity-audit/E250 profile, excluding any
  truncated record from "usable evidence" counts rather than silently including it.
  Measured result after the fix: 0 documents truncated in the full dataset.
- **E250 chemical basis**: added `scripts/openfoodtox/chemical_basis.py` — recovers
  the ADI's chemical basis from the record's own justification text (ties the
  stored 0.1 value to "sodium nitrite", keeps the 0.07 mg nitrite ion/kg bw figure
  as a separate, non-merged mention). Externally verified (separately from raw
  IUCLID extraction) against the actual cited EFSA opinion via
  efsa.europa.eu/en/efsajournal/pub/4786 and pmc.ncbi.nlm.nih.gov/articles/PMC7009987
  (fetched 2026-10-01): the IUCLID text is a near-verbatim match of the opinion's
  own sentence, and the Panel itself states both figures as two bases of one ADI —
  not a discrepancy to resolve.
- Also fixed an arithmetic error in the prior version of `docs/OPENFOODTOX_DATASET_AUDIT.md`
  (documents-by-type summed to 232,377; correct total is 221,377).
- Tests: added `test_openfoodtox_e_numbers.py` (24 tests), `test_openfoodtox_chemical_basis.py`
  (8 tests), plus new truncation-boundary and quarantine-propagation cases in the
  existing records/dossier test files. `python -m pytest -q` (full backend suite,
  from `nutriguard-backend/`): **790 passed, 16 skipped** (pre-existing skips), 0 failed.
- Reran the full offline extraction/audit into a **new versioned output folder**
  (`staging/v2/`, `reports/v2/`) rather than overwriting the 2026-09-30 outputs,
  which remain unchanged at their original location for comparison.
- `docs/OPENFOODTOX_DATASET_AUDIT.md` updated with a new §8 (the three fixes,
  measured impact, external-verification sources/dates) and corrected §6/§7/§9.
- No import into the live database, no API/Health Score change, no container
  restart, no deploy, no merge. Originals and the live stack were not touched.

## 2026-10-01: Codex review handoff for Claude (documentation only)

- Reviewed audit baseline `8dd62355281c147bc84c9266341a47b60a509846`.
- Added `docs/OPENFOODTOX_REVIEW_TASK.md`: fix suffix-bearing E-number extraction,
  measure/resolve the 400-leaf completeness limitation, and verify E250 ADI chemical basis.
- No application or extractor code changed; tests not rerun for this documentation task.
  Whitespace checked with `git diff --check`. No live access, import, merge or deploy.
- Next: Claude reads the task from this branch and implements/tests the scoped fixes
  in an isolated worktree, updates audit results and pushes for review.

## 2026-09-30: OpenFoodTox IUCLID dataset audit (offline, not integrated)

- Scope: offline inventory/integrity/structural audit and staging
  catalogue extraction of the transferred OpenFoodTox IUCLID dossier
  archives (`/home/vboxuser/nutriguard-data/openfoodtox/originals/2026-09-30/dossiers/`,
  11,613 files), in an isolated worktree on branch
  `feat/backend-openfoodtox-dataset-audit`. No import into the live
  database, no API/Health Score change, no container restart, no
  deploy, no merge. Originals and the live stack were not touched.
- Added `nutriguard-backend/scripts/openfoodtox/` (safe bounded
  zip/XML handling with no external entities/no path traversal/no
  stylesheet execution; manifest and `.i6d` parsing; a code->label
  registry harvested from the dossiers' own shipped `.xsl` stylesheets;
  domain classification; a repeatable `extract` CLI with
  `inventory`/`codebook`/`catalogue`/`identity-audit`/`e250`
  subcommands) and 52 new unit tests in
  `tests/unit/test_openfoodtox_*.py` (all synthetic fixtures, no
  dependency on the real dataset).
- Full run against the transferred set: file count and total bytes
  matched the expected 11,613 files / 1,100,741,293 bytes exactly;
  11,613/11,613 archives valid (zero corrupt/unsafe/duplicate); 11,613
  dossiers cataloged with zero parse failures; identity audit found
  zero name/CAS conflicts needing manual review. Sodium nitrite/E250
  matched exactly one dossier by CAS `7632-00-0`/EC `231-555-9`; full
  profile in `reports/e250_sodium_nitrite_profile.md`.
- Full detail, output schemas, and known limitations (generic vs.
  per-subtype endpoint parsing, value-code decoding scoped per
  stylesheet, no license/reuse-terms metadata transferred with the
  dataset, ~985 MB catalogue size) in
  `docs/OPENFOODTOX_DATASET_AUDIT.md`.
- Tests: `python -m pytest -q` (full backend suite) → **753 passed, 16
  skipped** (skips pre-exist this change), 0 failed — no regression in
  existing app/backend behavior; nothing under `app/` was touched.
- Generated staging/reports data lives outside git under
  `/home/vboxuser/nutriguard-data/openfoodtox/{staging,reports}/` —
  not committed (per task scope: no dataset, no bulk generated output,
  no secrets in the commit).
- Next: see `docs/OPENFOODTOX_DATASET_AUDIT.md` §9 (recommended next
  integration stage) — not started here; needs its own explicit
  scoping/authorization before any of this data reaches the live
  product.

## 2026-09-27: PR #29 deployed and verified (Codex)

- Owner authorized deployment of exactly merged PR #29:
  `65a8e689ebf7ba40ac4bdcb43ab2821b60c8916c`. Fetched origin; target
  matched `origin/main`, merge title and parent `32bd7efc05750470da6f48eab7c4111e45db1d26`.
  Fast-forwarded local `main` only to that SHA. No extra commits deployed.
- Read repository/backend AGENTS.md, this handoff, README Docker startup
  procedure, candidate queue and review exporter documentation before deployment.
  Live bind mounts were confirmed to use this checkout's `app` and `alembic`.
- Preserved the sole pre-existing working-tree change (247 added handoff lines)
  verbatim alongside all incoming handoff entries. Original document and binary
  patch saved under `/home/vboxuser/nutriguard-backups/pr29-deploy/` as
  `handoff.before.md` and `local-changes.patch`; retained the path-scoped stash
  `pr29-deploy-preserved-handoff` (SHA recorded in `stash-sha.txt`).
- Backup created BEFORE stopping/updating anything:
  `/home/vboxuser/nutriguard-backups/pr29-deploy/nutriguard_pre_pr29.dump`.
  `docker exec nutriguard-backend-db-1 pg_dump -U nutriguard -d nutriguard -Fc`
  succeeded; archive is 120,992 bytes, created 2026-09-27 17:21:14 UTC.
  `pg_restore --list` succeeded (exit 0), CUSTOM PostgreSQL 16 archive,
  75 TOC entries; listing retained as `backup.toc`. No live restore performed.
- `docker compose stop backend` completed at approximately 17:24:16 UTC,
  BEFORE stash/fast-forward or any mounted-source update. PostgreSQL and Redis
  remained running; their container start timestamps were verified unchanged.
  No Compose down, data-volume operation or unrelated service restart.
- Pre-migration database revision was exactly `c6d7e8f9a0b1` and the candidate
  table was absent, matching this migration's expected parent state. Target
  migration is the additive candidate table version; no other branch integrated.
- Rebuilt with `docker compose build backend`. Verified all 105 tracked app
  and Alembic files inside the final image match the target checkout. Sole
  image head: `d7e8f9a0b1c2`. Final image ID:
  `sha256:4692c6ce95bcef6382f37b2ee50289297178014aca1cf54c5520448fb387adbc`.
- Full tests in that image, network disabled, entrypoint bypassed and isolated
  SQLite configuration: `python -m pytest -q -p no:cacheprovider`:
  **701 passed, 16 skipped, 2 warnings in 20.20s**. Skips are opt-in PostgreSQL
  tests; the earlier disposable PostgreSQL verification is recorded below.
  No test suite was pointed at the live database.
- `docker compose up -d --no-deps backend` at 17:26:12 UTC used the documented
  entrypoint: database readiness, `alembic upgrade head`, existing startup
  seed loader, application startup. Both live `alembic heads` and
  `alembic current` confirmed `d7e8f9a0b1c2 (head)`; no stamp or downgrade.
- Local `http://127.0.0.1:8000/health` and Tailscale
  `https://ubuntu.taileaa26d.ts.net/health` both returned HTTP 200 with
  `{"status":"ok","version":"1.0.0"}`. Results saved as `health.json`.
- Data preservation checked using pre/post full-row fingerprints and a
  column-by-column comparison of backup COPY data with live data in memory
  (no ingredient contents printed). Counts unchanged: products=88,
  ingredients=296, ingredient_aliases=308, ingredient_localizations=12,
  product_sources=104, scan_history=51, users=24, devices=24,
  user_health_profiles=24, refresh_tokens=54. All row identities preserved.
  All values unchanged EXCEPT existing startup-seed metadata: `retrieved_at`
  and `last_verified_at` on 12 ingredients, `reviewed_at` on 12 localizations.
  Scientific content, aliases and product references are unchanged. These
  timestamp refreshes are existing seed behavior, not migration data changes.
  Evidence: `data.before.txt`, `data.after.txt`, `data-column-audit.json`.
- Ran `docker compose exec -T backend python -m app.seed.ingredient_review_list`
  successfully, using its PostgreSQL READ ONLY transaction and rollback.
  Report processed in memory; only count/flag summary saved to
  `export-summary.json`: entryCount=0, junkObservationRowsExcluded=0,
  tokenObservationCount=0, flags={}; candidate table rows=0. This is expected:
  old catalog rows are not automatically backfilled. Every table fingerprint
  was identical immediately before/after the exporter. No backfill, repair,
  prune, synthetic scan or content mutation was performed by verification.
- Post-start backend logs: 631 lines through 17:27:26 UTC, zero matches for
  traceback/exception/critical/error/HTTP 5xx; startup and migration succeeded.
  PostgreSQL and Redis remained healthy. Existing Compose obsolete-version
  warning and test warnings were left unchanged.
- Operational checks initially caught two issues, both resolved: the first
  handoff combination assertion failed before editing files; all local additions
  were then preserved verbatim at the top. A build started before that checkout
  update completed; it was never started as a service and was superseded by a
  second build from the verified target. The strict full-row equality check
  flagged seed timestamp refreshes; the backup column audit above established
  their exact scope. No recovery, restore or live-data deletion was needed.
- Only this handoff remains modified in Git. No commits/pushes, secret edits,
  scientific content edits, Android changes or unrelated branch changes.

**Recommended next step:** deployment complete; no further deployment action
required. Future normal scans can populate the candidate queue. A later manual
read-only exporter run can report those observations; legacy backfill remains
subject to a separate owner decision.

---



## 2026-09-17: PR #20 deployed to the live dev backend (Claude Code)

**[DOCUMENTATION UPDATE — uncommitted, per task instruction]**

- Deployed the merged `main` (PR #20, `feat/backend-ingredient-language-diagnostics`
  -- the ingredient-language identity/diagnostics work documented in the
  entries immediately below, its 3 code-review rounds, and issue #19)
  to the live `nutriguard-backend` Docker Compose stack. Expected merge
  commit `32bd7efc05750470da6f48eab7c4111e45db1d26` confirmed to be
  `origin/main`'s exact HEAD before touching anything -- no unreviewed
  drift beyond it.
- **Working-tree conflict handled, nothing discarded**: this file itself
  had an uncommitted local entry (the PR #18 deployment record just
  below) at the same insertion point PR #20's own commits also touched.
  `git merge --ff-only` correctly refused rather than risk overwriting
  it. Resolved safely: `git stash push -u` (recoverable, not a discard)
  on just this file, fast-forwarded `main` (`8d742eb` -> `32bd7ef`,
  clean fast-forward, confirmed via `git merge-base --is-ancestor`),
  then `git stash apply <sha>` (not `pop`) produced a real (expected)
  merge conflict at the same spot; resolved by hand, keeping BOTH
  entries in full -- nothing from either side was dropped. The stash
  entry itself was intentionally left in place afterward as a redundant
  safety net (harmless to leave; the PR #18 entry's content is already
  fully present in the working tree either way).
- **Process deviation, disclosed**: the task asked to stop the backend
  *before* updating the mounted source. Because updating the checkout
  (the fast-forward) is what the git-conflict resolution above was
  itself resolving, the backend was still running (dev-mode
  `--reload`) for the ~15 seconds between the fast-forward landing on
  disk and this session's own `docker compose stop backend`. `WatchFiles`
  did hot-reload the app against the still-unmigrated schema in that
  window (`Application startup complete` logged at 13:01:46Z). Checked
  the exact log window (13:01:46Z-13:03:25Z when `stop` completed):
  **zero requests were served** in that interval (no access-log lines
  at all between the reload and the stop) -- no client ever observed
  the mismatched-schema state. Flagging the ordering miss for
  visibility; the actual exposure was nil per the logs.
- Backup: `pg_dump -Fc` via the running `db` container ->
  `/home/vboxuser/nutriguard-backups/nutriguard_pre_pr20_20260917T130301Z.dump`
  (103,058 bytes, exit code 0). `pg_restore --list` against it (via a
  throwaway `postgres:16-alpine` container, read-only mount) succeeded:
  exit 0, 75 TOC entries, `dbname: nutriguard`, `Format: CUSTOM`.
  Baseline counts recorded before any change: products=55,
  ingredients=247, ingredient_aliases=258, scan_history=39, users=21,
  ingredient_localizations=12 (identical to the post-PR18 baseline
  recorded in the entry below -- nothing changed between that
  deployment and this one).
- Maintenance window: `docker compose stop backend` (db/redis left
  running/untouched throughout -- never `docker compose down`, no
  volume touched, no reset/reseed, no `.env`/secret edit).
- Rebuilt the `backend` image from the repo's own `Dockerfile`/
  `requirements.txt` (`docker compose build backend`), then
  `docker compose up -d --no-deps backend`. The existing entrypoint
  ran its supported startup path unmodified: waited for Postgres,
  `alembic upgrade head` (`b5c6d7e8f9a0 -> c6d7e8f9a0b1, ingredient
  language provenance, identity-uncertainty flags, and additive
  intake-guidance fields`), then `python -m app.seed.load_seed`
  (`seed_loaded count=12`, idempotent -- no new rows), then `uvicorn`
  started. No manual migration/seed invocation was needed.
- Post-deploy verification: `alembic heads`/`alembic current` in the
  running container both == `c6d7e8f9a0b1 (head)` -- exactly one head.
  Local `GET /health` and the private Tailscale
  `https://ubuntu.taileaa26d.ts.net/health` both returned
  `200 {"status":"ok","version":"1.0.0"}`.
- **Data preservation confirmed post-deploy, byte-for-byte**:
  products=55, ingredients=247, ingredient_aliases=258, scan_history=39,
  users=21, ingredient_localizations=12 -- every single count identical
  to the pre-deploy baseline. Expected: this migration only adds
  columns to existing tables (no new rows), and the seed loader is
  idempotent against already-seeded data.
- API field verification, reusing the EXISTING `deploy-verify-20260917`
  device (re-authenticating an already-registered device is
  get-or-create -- confirmed `users` count stayed at 21 across the
  call, no new user created) -- no disposable product/user created:
  `GET /ingredients/e951_aspartame` (curated, reviewed Bulgarian) --
  new additive fields present and correct: `effectConditions`/
  `dietaryGuidance` (`""`, honestly not yet authored),
  `identityUncertain=false`/`uncertaintyReason=null`,
  `adiPopulationScope="PER_KG_BODY_WEIGHT"`; `localizations.en` and
  `.bg` both present, `bg.translationStatus="REVIEWED"`/
  `translationSource="MACHINE_TRANSLATED"` -- unchanged existing
  behavior. `GET /ingredients/e200_sorbic_acid` (curated, no reviewed
  Bulgarian row) -- `localizations` has only `en`, confirming no
  fabricated Bulgarian coverage. `GET /products/4006381333931`
  (pre-existing fixture, not created by this session) -- new
  `originalIngredientText`/`ingredientTextSourceLanguage` fields
  present with honest defaults (`""`/`null`, since this product predates
  the original-vs-canonical-text distinction); `rawIngredientText`
  unchanged.
- Logs: `docker compose logs backend` since the rebuild (834 lines)
  grepped for `traceback|exception|MissingGreenlet|CRITICAL|error|5xx`
  -- zero matches. All access-log lines since deploy: 7 requests, all
  `200` (this session's own health/verification calls only -- 2x
  `/health`, 1x `POST /auth/device`, 2x `/products/4006381333931`, 1x
  each ingredient lookup).
- Diagnostics: config unchanged by this deploy (`SCAN_DIAGNOSTICS_ENABLED=true`,
  `SCAN_DIAGNOSTICS_MAX_BYTES=1048576`, `SCAN_DIAGNOSTICS_BACKUP_COUNT=1`,
  `SCAN_DIAGNOSTICS_PATH=/var/log/nutriguard/scan-diagnostics.jsonl`,
  storage budget untouched, no automatic/periodic monitoring added).
  Diagnostics file: 14,426 bytes / 39 lines, well under the 1 MiB
  rotation threshold, no rotation backup file beyond the configured
  `.1`. Inspected only structural info (file size, line count, the
  field NAMES of the most recent line) -- never printed raw OCR/scan
  content in this session; the diagnostics format itself is
  content-free by design (images/OCR text/model responses/credentials/
  user IDs/health-profile data are structurally excluded at the
  recording layer, unchanged by this deploy).
- **Ingredient-language repair tool -- dry-run only, confirmed
  non-mutating first** (`app/seed/repair_ingredient_language.py`:
  `--apply` defaults to `False`; the write path (`_apply_ingredient_translation`)
  is only ever called when `apply=True`, and the function ends with
  `await db.rollback()` whenever `apply=False` -- confirmed by reading
  the source before running). Ran without `--apply` against the live
  database: exit code 0, `"mode": "dry_run"`, **0 entries with
  `applied=true`**, and DB row counts re-verified identical
  before/after the run. Summary of the 247 existing ingredient rows
  examined (category counts only -- no raw ingredient/OCR text
  reproduced here, per task instruction):
  - `ALREADY_FINE` (already en/bg/unknown): 20
  - `SKIPPED_TRUSTED_SOURCE` (curated/regulatory, never auto-repaired): 47
  - `FLAGGED_UNRESOLVED_AMBIGUOUS` (suspected OCR concatenation, never translated): 26
  - `TRANSLATED` (would become a proposed repair under `--apply`): 4
  - `FLAGGED_UNRESOLVED_TRANSLATION_FAILED` (translation attempted, failed the strict reliability check): 150
  - 10 additional PRODUCT-level flags (`Product.raw_ingredient_text` in
    a non-EN/BG language, report-only -- this tool never rewrites
    `Product` rows regardless of `--apply`).
  - The 150-count `FLAGGED_UNRESOLVED_TRANSLATION_FAILED` figure is
    reported as observed, without a confirmed root cause: Gemini
    responses varied (not a uniform "unavailable" fallback value),
    so this reflects the translation-reliability check's own strict
    bar being applied to old/legacy catalog rows, not a Gemini outage
    -- but a definitive cause would need further investigation, not
    performed in this session (out of scope for a deployment task).
  - **No repair was applied.** This summary is for owner review/approval
    only -- a decision to run `--apply` (only 4 rows would actually
    change) is explicitly deferred to a human, per task instruction.
- Minor pre-existing operational observations (not caused by or fixed
  in this session, mentioned for visibility only, no values printed):
  the `backend` service's `JWT_SECRET` environment value in
  `docker-compose.yml` appears to still be the literal placeholder
  string from `.env.example`, not a generated secret -- worth the
  owner's attention for a non-dev environment, but out of scope to
  change here (`.env`/secret edits are explicitly forbidden by this
  task and by this repo's own security rules). `GEMINI_API_KEY`
  appears to be set to a real-looking value.
- No push performed (none authorized/needed -- `origin/main` was
  already the deploy target). No live/production database reset,
  reseed, or `--apply` repair performed. `docker compose down` never
  run; no volume touched.

**Recommended next step**: none required for this deployment -- it is
complete and verified. The repair-tool dry-run summary above (150
flagged-unresolved rows, 4 proposed translations) is worth a human
decision on whether/when to run `--apply`, but is not a blocker. If
the leftover `deploy-verify-20260917` device/user row (see the PR #18
entry below) should still be removed, that remains an available
follow-up, unrelated to this deployment.

---

---

## 2026-09-17: PR #18 deployed to the live dev backend (Claude Code)

**[DOCUMENTATION UPDATE — uncommitted, per task instruction]**

- Deployed the already-merged `main` (PR #18, `feat/app-bilingual-enrichment`)
  to the live `nutriguard-backend` Docker Compose stack
  (`nutriguard-backend-backend-1`/`-db-1`/`-redis-1`), which was already
  running dev-mode (`docker-compose.yml`, bind-mounted `./app`/`./alembic`,
  `uvicorn --reload`) before this task started.
- Pre-deploy state: local `main` was one merge behind `origin/main`
  (`f35322f`, PR #16). Fetched, confirmed `origin/main` had advanced to
  exactly the expected `8d742eb773ebe015b7fcfcefd19869ab129f7644` (no
  further unexpected drift), fast-forward merged. Working tree was clean
  throughout; no local changes to preserve.
- Backup: `pg_dump -Fc` via the running `db` container ->
  `/home/vboxuser/nutriguard-backups/nutriguard_pre_pr18_20260917T071115Z.dump`
  (93,402 bytes, 68 TOC entries). `pg_restore --list` against it (via a
  throwaway `postgres:16-alpine` container) succeeded. Baseline counts
  recorded before any change: products=55, ingredients=247,
  scan_history=39, ingredient_aliases=252, users=20.
- Maintenance window: `docker compose stop backend` (db/redis left
  running/untouched) *before* the fast-forward, specifically because the
  dev compose file bind-mounts source into the container with
  `--reload` -- updating the checkout files live would otherwise have
  hot-reloaded new code against the still-unmigrated schema.
- Rebuilt the `backend` image from the repo's own `Dockerfile`/
  `requirements.txt` (`docker compose build backend`), then
  `docker compose up -d --no-deps backend`. The existing entrypoint
  (`docker/entrypoint.sh`) ran its supported startup path unmodified:
  waited for Postgres, `alembic upgrade head` (`a4b5c6d7e8f9 ->
  b5c6d7e8f9a0, reviewed ingredient display localizations`), then
  `python -m app.seed.load_seed` (`seed_loaded count=12`, "Seeded 12
  ingredients."), then started `uvicorn`. No manual migration/seed
  invocation was needed.
- Post-deploy verification: `alembic current` in the running container
  == `b5c6d7e8f9a0 (head)`. `ingredient_localizations` has 12 rows,
  all `language='bg'`, matching the 12 curated ids in
  `app/seed/ingredients_seed_bg.json`. Local `GET /health` and the
  private Tailscale `https://ubuntu.taileaa26d.ts.net/health` both
  returned `200 {"status":"ok","version":"1.0.0"}`.
- Data preservation confirmed post-deploy: products=55, ingredients=247,
  scan_history=39, users=20 (all unchanged); `ingredient_aliases`
  went 252 -> 258 (+6), which is the seed loader's own expected
  additive alias registration for this change, not data loss.
- API field verification (via a throwaway `deploy-verify-20260917`
  device-auth token, never printed): `GET /ingredients/e951_aspartame`
  -- canonical English fields (`commonName="Aspartame"`, `eNumber=E951`,
  `insNumber=951`, `adiMinMgPerKgBwPerDay=0.0`/`adiMaxMgPerKgBwPerDay=40.0`,
  `references`/citations, `sourceUrl`) unchanged; `localizations.bg`
  present with `translationStatus="REVIEWED"`,
  `translationSource="MACHINE_TRANSLATED"`, full reviewed Bulgarian
  text. Product-context check via `POST /scan/ocr-text` against the
  pre-existing `4006381333931` ("Diagnostic Test Product DELETE ME")
  fixture (created 2026-08-31, not created by this session) with raw
  text containing "Aspartame (E951)": resolved via existing alias
  matching straight to canonical `e951_aspartame`, and
  `GET /products/4006381333931` surfaced the same unchanged English +
  reviewed Bulgarian profile inside the product response.
  `GET /ingredients/e200_sorbic_acid` (curated, no reviewed Bulgarian
  row) returned normally with only `localizations.en` populated --
  confirms non-translated ingredients still serve valid English
  content. `GET /ingredients/synth_fd99c9498840` (OCR-only ingredient)
  also returned normally.
- Logs: `docker compose logs backend` since deploy start (1135 lines)
  grepped for `traceback|exception|MissingGreenlet|CRITICAL|500|Internal
  Server Error` -- zero matches. All request/response status lines were
  200 except one intentional 422 from an earlier invalid-barcode probe
  during verification (expected validation behavior, not a server error).
- Minor, harmless side effect from verification: the throwaway
  `deploy-verify-20260917` device + its user row remain in the `devices`
  and `users` tables (users 20 -> 21) -- an attempt to delete them was
  blocked by this session's own destructive-action safety guard
  (mass-delete classifier); left in place rather than working around
  it. No product, ingredient, or scan-history data was created or
  altered by verification.
- No push performed (none authorized). No test suite run against the
  live database (explicitly out of scope; PR #18 was already verified
  in disposable infrastructure per the entry immediately below).

**Recommended next step**: none required for this deployment -- it is
complete and verified. If the leftover `deploy-verify-20260917`
device/user row should be removed, grant Bash permission for that
specific delete (or run it manually) and re-run the two `DELETE`
statements this session attempted.

## 2026-09-27: VM verification of ingredient review list

- Verified fetched `c3f2f4eefa8c7ef839c95945496471ee2871fbe9` in isolated
  worktree `feat-backend-ingredient-review-list`; live bind-mounted checkout and
  its existing handoff edit were left untouched. Live schema was inspected only
  in a read-only transaction: `c6d7e8f9a0b1`, no candidate table.
- Full Linux suite on Python 3.12.14/pinned dependencies:
  `python -m pytest -q -p no:cacheprovider`: **701 passed, 15 skipped**;
  disposable PostgreSQL suite: **16 passed**, including new real CLI export,
  read-only transaction, bilingual deduplication and failure-preservation coverage.
- Final complete suite with disposable PostgreSQL enabled: **717 passed, zero
  skipped, 2 existing warnings**, 22.05 seconds. Disposable resources removed.
- Disposable PostgreSQL upgrade/downgrade/re-upgrade preserved existing seeded
  ingredients/localizations, OCR identity/alias and product references. Maintenance
  dry runs made no changes. OpenAPI exact match; one Alembic head.
- Added `tests/postgres/test_ingredient_review_list_postgres.py` and full report
  `docs/INGREDIENT_REVIEW_VERIFICATION.md`; updated the review-list verification
  guidance. No application code, sources or descriptions changed; no EFSA work.
- Integration gates remain: truthful-unknowns uses the same `d7e8f9a0b1c2`
  revision for different DDL; summaries introduces a sibling head; ingredient-first
  label scanning conflicts in `food_analysis.py`. No optional branches integrated.
- Next: review the candidate dependency and verification report, resolve conflicts
  only on the explicitly chosen integration tree, and re-test that tree before
  separately authorizing merge/deployment. This task authorizes committing and
  pushing verification only; collection is still inactive on the live stack.

## 2026-09-27: authorized Git handoff to the VM

- Owner explicitly authorized committing and pushing `feat/backend-ingredient-review-list`,
  without merging or changing the server. This entry accompanies that delivery.
- Focused review-list suite rerun: 15 passed. Full-suite platform limitations
  and unmerged candidate dependency documented below remain applicable.
- VM agent: fetch this branch without switching the live bind-mounted checkout.
  Read `AGENTS.md`, `docs/INGREDIENT_REVIEW_LIST.md`, and
  `docs/INGREDIENT_CANDIDATE_QUEUE.md` in an isolated worktree. Inspect current
  deployed schema/branch and resolve integration conflicts there, not on live data.
- Retain current scientific information sources; no EFSA/OpenFoodTox integration,
  generated descriptions, evidence promotion, or scheduled monitoring is requested.
- Run Linux full suite and disposable PostgreSQL migration/concurrency/export
  checks; document results and remaining deployment gates. Do not automatically
  merge or deploy this handoff branch. Collection is not activated by a Git push.

## 2026-09-27: activation requested; remote authentication blocked

- User requested applying the names-only collection, retaining existing information sources.
- Re-read repository instructions and checked the local review-list changes are intact.
- Refreshed origin/main: still `32bd7efc05750470da6f48eab7c4111e45db1d26`.
- Read-only SSH connection test to the previously configured VM address with
  BatchMode and strict host-key checking reached SSH but returned
  `Permission denied (publickey,password)`. No remote command executed.
- No production files/database/services changed; collection is NOT activated.
  No push/merge performed. Prior integration and Linux/Postgres gates remain.
- Next: obtain approved SSH authentication through the normal local key setup,
  or hand execution to the existing VM agent. Inspect deployed state first,
  reconcile dependencies, test and back up before any production migration.

## 2026-09-26: owner EN/BG ingredient review export (local, not deployed)

- Inspected main (`32bd7ef`), catalog audit and existing candidate collection.
  Reused candidate branch `d372985` in isolated task branch
  `feat/backend-ingredient-review-list`; main and other worktrees unchanged.
- Added `app/services/ingredient_review_list.py` (read-only canonical grouping,
  explicit-language names, collision flags, observation metadata) and
  `app/seed/ingredient_review_list.py` (manual JSON view/atomic file snapshot).
  See `docs/INGREDIENT_REVIEW_LIST.md` for operator commands and limitations.
- Added `tests/integration/test_ingredient_review_list.py`: 15 tests passed
  using isolated Python 3.12.14 with repository requirements. Final full suite:
  698 passed, 15 skipped, 3 failed. Failures are
  unchanged POSIX-only scan diagnostics tests on Windows (`fcntl`/`fork`),
  not suppressed or edited (confirmed unchanged against `d372985`). PostgreSQL
  opt-in tests not executed here. `git diff --check` passed; `alembic heads`
  reports the existing single head `d7e8f9a0b1c2`.
- No live connection, migration, content update, API change, push, commit,
  merge, deployment, or automatic monitoring performed. Existing collection
  remains the source: known curated identities are not candidates, queue limits
  still apply. Export does not import hand-edited scientific content.
- Important limitation: untagged OCR names stay unnamed pending language review;
  internal originals remain available by candidate ID. This avoids guessing EN/BG.
  Same-name different identities are flagged rather than unsafe auto-merged.
- Next: independently review this extension and candidate dependency; reconcile
  migration ID collisions across unmerged branches, run Linux/Postgres checks
  on the combined branch, then separately authorize push/review/deployment.

## 2026-09-17 (later): code-review follow-up -- translation correctness, EN/BG contract, diagnostic accuracy (Claude Code, isolated worktree)

Continuation of the entry immediately below, addressing a code review of commit `8ca0119fac1fbffb47bb64b5cb50a0f5ada83fb7` on the same branch (`feat/backend-ingredient-language-diagnostics`). Also tracked as GitHub issue #19 (shared Claude/Codex task record); a copy of this report was intended to be posted there, with a fallback to committing it into this repo when API/comment access isn't available in this session -- see `docs/INGREDIENT_LANGUAGE_REVIEW.md` if present. Worked in the same isolated worktree as before; live checkout/database/containers untouched throughout. One subagent used (issue 3, non-overlapping file ownership: `app/services/ingredient_segmentation.py` + its test file only).

### 1. Short ASCII text is not proof of English -- FIXED

`ingredient_translation._is_plain_ascii_text` accepted ANY <=2-word ASCII result as reliable, which let genuinely untranslated foreign text through whenever it happened to contain no diacritics (e.g. French "lait entier" -> "lait entier" unchanged, confidently accepted). Removed entirely -- replaced with a narrow, evidence-based fallback: a short (<=2-word... no, ANY-length) translated result is now accepted, when `detect_language` alone doesn't confirm "en", ONLY if its normalized text matches a name ALREADY established in the ingredient catalog (`ingredient_alias_repository.get_all_normalized`, a new bulk-fetch repository function -- real, pre-existing evidence, never a guess). A short, genuinely novel, correct translation with no catalog match now stays honestly `reliable=False` (identity_uncertain, never silently accepted or silently rejected as "definitely wrong") -- this is the "preserve unresolved status when uncertain" requirement, a deliberate and correct trade-off, not a regression.

Tests: `tests/unit/test_ingredient_translation.py` -- new cases for a known-alias short match (accepted), an unchanged short foreign phrase with no catalog match (rejected), and a novel correct short translation with no catalog match (honestly unresolved, not guessed either way).

### 2. Translation-to-input correspondence + full-list context -- FIXED

`translate_ingredient_tokens` previously paired Gemini's response entries to input tokens by `zip()` position only, never validating the parsed `originalText` field against anything -- a reordered/duplicated/mismatched response could silently attach a translation (and, downstream, a persisted alias) to the WRONG original ingredient. Replaced with `_match_responses_to_targets`: builds a `{text -> [positions]}` map of the input `targets` list, then matches each response entry to its target by EXACT `originalText`, consuming duplicate-text occurrences FIFO; any response entry whose text doesn't match a (remaining) target is discarded, never force-attached elsewhere; any target with no matching response entry becomes an honest `_unreliable` result. The previous blanket `len(payload) != len(tokens)` whole-batch rejection is gone -- every irregular shape (reordered, duplicate input, missing, extra) is now handled per-target instead of failing the whole call.

Full-list-context fix: `translate_ingredient_tokens(targets, *, context=None, known_normalized_names=...)` now takes `targets` (what needs a translation returned) SEPARATE from `context` (the whole scan's ingredient list, for disambiguation). `GeminiService.translate_ingredient_list` and its prompt template were updated to send both explicitly (`fullIngredientList` vs `targetEntries`), asking the model to use the former as context but return translations ONLY for the latter, keyed by exact original text. The caller (`ingredient_catalog._resolve_ingredient_languages`) now builds `full_context` from every ingredient this scan actually saw (curated + synthetic), not just the subset still needing translation.

Also fixed while verifying this (found via a new test, not a code-review item): `_resolve_ingredient_languages` only checked for an existing alias by EXACT normalized text before deciding to translate -- a foreign-language OCR reading that still carries a genuine E-number (definitive identity on its own) was being wastefully sent for translation even though `get_or_create_catalog_ingredient` would resolve it via the E-number regardless. Added an official-identifier pre-check (mirrors task 1's "local-catalog resolution first, using established identifiers") alongside the alias check.

Tests: `tests/unit/test_ingredient_translation.py` -- reordered, response-entry-not-in-targets (discarded), duplicate-target (both matched / one matched one missing), missing target, extra response entries, and a near-miss (non-exact) `originalText` match. `tests/integration/test_ingredient_bilingual_contract.py` -- E-number pre-check reuse test.

### 3. Ordinary allergen emphasis is not broken segmentation -- FIXED (subagent, reviewed)

`ingredient_segmentation._has_embedded_allergen_emphasis` flagged ANY token mixing a lowercase word with an ALL-CAPS word of 3+ letters -- over-broad: EU labels routinely emphasize a single allergen word in caps within an otherwise ordinary compound name ("MILK powder", "ZARA pudră"). Removed entirely, with no milder capitalization-threshold variant substituted (matches the task's explicit instruction not to replace it with another blanket script rule). Replaced with a narrower, STRUCTURALLY-grounded check: a literal, un-split `:` still inside the token (`_has_unsplit_colon_clause`, new reason code `COLON_SEPARATED_CLAUSE_MERGE`) -- real evidence tokenization's own colon-exclusion (`ocr_normalizer.normalize_and_extract_tokens` never splits on `:`) left a genuine clause-header pattern merged into one token. `_has_duplicate_word` (repeated significant word) and the 7+-word length check are unchanged.

Of the task's 7 original examples: "Ulei de rapiță"/"Semințe de mac"/"Aluat acrisor" were never flagged (unchanged). "LAPTE proteină din LAPTE" is STILL flagged (`DUPLICATE_TOKEN_FRAGMENT` -- the same word repeated is real evidence). "SECARA agenți de creștere"/"Produs din GRAU"/"ZARA pudră" are NO LONGER flagged -- a deliberate, correct behavior change (they were false positives from the old heuristic, not genuinely malformed) -- confirmed and re-tested end-to-end (they now reach translation instead of being blocked).

Tests: `tests/unit/test_ingredient_segmentation.py` rewritten (23 tests) with all 7 original examples plus "MILK powder", colon-clause positive cases, and edge cases. Downstream fallout from the intentional behavior change fixed in 3 files (outside the subagent's scope, fixed by this session): `tests/integration/test_ingredient_language_identity_e2e.py` (split the old 4-way parametrized test into a genuinely-ambiguous-only case plus a new "previously over-flagged examples now translate" case), `tests/integration/test_repair_ingredient_language.py` (fixture changed from a caps-based example to a colon-based one), `tests/integration/test_scan_language_e2e.py` (same split, at the HTTP level).

### 4. Truthful diagnostics -- FIXED

- **Barcode partial results**: `scan_barcode` had no `ProductNotFoundError`-specific branch at all (only generic `AppError`/`Exception`), so a `labelScanRequired` partial result was recorded `outcome="failed"`. Added the same partial/failed branch the other two endpoints already had -- but discovered `labelScanRequired` alone is set by BOTH `_label_scan_required_details` (a real partial identity) AND `_not_found_details` (nothing found at all, same suggested next action), so it can't distinguish them. Added `_is_partial_result`, keyed on `discoveredIdentity` presence (only ever set by the genuine partial case) -- applied consistently to all three endpoints, not just barcode.
- **`dataSource`**: previously a hardcoded literal naming the operation ("barcode"/"ocr_text"/"label_image"). Now `_observed_data_source` reports the REAL origin: `"cache"` when `is_from_database_cache` is true, else the row's own persisted `Product.source` (a real provider name, "local", "label_scan", etc.) once a product is known, `exc.details.get("dataSource")` for a partial result (new field added to `_label_scan_required_details`, sourced from the same real `product.source`), or `None` when genuinely unknown (no product ever existed for this request). `operation` (which endpoint) is kept as a separate field.
- **Serialization/computed-field failures**: `_to_analysis_out(result)` only runs Pydantic field validation, not full JSON encoding -- nested `@computed_field`s (e.g. `adiPopulationScope`, `localizations`) evaluate later, inside FastAPI's own response rendering, OUTSIDE any endpoint try/except. Added `_finish_serializing`, which calls `out.model_dump(mode="json", by_alias=True)` (discarding the result) INSIDE the same try block right after construction -- any failure there now correctly falls into the generic failure-diagnostic path instead of leaving a false "success" record.
- **Translation attempt/result/reason**: previously inferred from `product.source == "label_scan_translated"` only (a boolean proxy). `_finalize_barcode_enrichment`/`_finalize_standalone_label_analysis` now return the real `label_language_status`/`label_translation_used`/`label_detected_language` (straight from `LabelTextResult`), and `scan.py`'s new `_translation_fields` maps `status` ("ok"/"translated"/"translation_unreliable_fallback") to `translationAttempted`/`translationResult`/`translationReason` -- absent (not guessed) on the barcode-only path, which never computes a `label_result` at all.
- **One summary record per operation**: audited -- every code path writes exactly one diagnostic record then either re-raises or returns; no duplication introduced.
- Bounded rotation, multi-process `flock` safety, and the existing privacy exclusions in `app/core/scan_diagnostics.py` are UNCHANGED by this pass.

Tests added to `tests/integration/test_scan_diagnostics_endpoints.py`: barcode `labelScanRequired` partial classification (distinct from a genuinely-unknown-barcode failure), cache-vs-fresh `dataSource` attribution, and a computed-field/full-serialization failure distinct from the pre-existing `_to_analysis_out`-construction-failure test (forces `_finish_serializing` itself to raise).

### 5. Android contract completion -- FIXED

- `ProductOut` gained `originalIngredientText: str = ""` and `ingredientTextSourceLanguage: str | None = None` (the `Product` columns already existed from the previous pass; they were just never exposed on the response schema). Additive, backward-compatible, truthful empty/null defaults matching the underlying column defaults exactly. Covered via the real `/scan/ocr-text` -> `GET /products/{barcode}` round trip.
- **EN/BG display-name contract, clarified and tested** (`tests/integration/test_ingredient_bilingual_contract.py`, new file, entirely documentation-via-tests -- no production behavior changed here, this codifies and proves EXISTING `build_localizations` behavior which was previously untested for the translation-pipeline interaction): a freshly-translated (never-curated) synthetic ingredient's `localizations` has `en` only (genuinely English, since translated text always becomes the canonical `common_name`) and NEVER a `bg` key -- translating a foreign name into English does not fabricate or claim any Bulgarian coverage. The runtime translation path (`ingredient_translation.py`) never writes an `IngredientLocalization` row at all (verified directly against the DB) -- so nothing from it could ever be marked REVIEWED, automatically or otherwise; only the curated seed loader (`app/seed/load_seed.py`) ever writes a reviewed localization. When a foreign-language OCR token resolves via an official identifier or alias to an EXISTING CURATED ingredient instead, the full curated profile -- including any real reviewed Bulgarian localization -- is served as-is (identity resolution, not translation, is what connects foreign-language input to already-curated bilingual content). `effectConditions`/`dietaryGuidance` confirmed empty (never fabricated) on a translated ingredient, both at the top level and inside `localizations.en`.

### Verification (this pass)

- `pytest tests/unit tests/integration -q` (pinned Python 3.12, this branch's own Docker image): **582 passed, 0 failed, 0 skipped** (2 pre-existing, unrelated SAWarnings).
- `pytest tests/postgres -q` (disposable PostgreSQL 16, `NUTRIGUARD_TEST_POSTGRES_URL`): **10 passed**.
- No schema/model changes in this pass -- migration head unchanged at `c6d7e8f9a0b1`; no upgrade/downgrade cycle needed (confirmed by re-running `alembic upgrade head` cleanly against a fresh disposable Postgres before the above).
- `app.openapi()` vs. tracked `openapi.json` (pinned deps): **exact match** -- diff confined to `ProductOut` (2 new fields), purely additive.
- Live dev stack/database: **never touched** -- disposable Docker containers/network, removed at the end of this session; live checkout's uncommitted handoff edit from the earlier deployment task left exactly as found.

### Files touched this pass

Modified: `app/api/v1/scan.py`, `app/integrations/gemini.py`, `app/repositories/ingredient_alias_repository.py`, `app/schemas/product.py`, `app/services/food_analysis.py`, `app/services/ingredient_catalog.py`, `app/services/ingredient_segmentation.py` (subagent), `app/services/ingredient_translation.py`, `openapi.json`, and 6 existing test files updated for new signatures/intentional behavior changes.

New: `tests/integration/test_ingredient_bilingual_contract.py`.

### Remaining limitations (unchanged from the previous entry, still accurate)

Real curated `effectConditions`/`dietaryGuidance` content for the 12 seed ingredients is still not authored (deliberately -- needs product-owner/scientific review, not a backend guess). `Product.original_ingredient_text`/`ingredient_text_source_language` are now exposed on `ProductOut` (this pass closes that gap). The dry-run repair tool (`python -m app.seed.repair_ingredient_language`) was still not run against any live database.

---

## 2026-09-17: ingredient-language identity, info contract, and scan diagnostics (Claude Code, isolated worktree)

- Branch `feat/backend-ingredient-language-diagnostics`, based on `origin/main` at `8d742eb773ebe015b7fcfcefd19869ab129f7644` (PR #18 merge), worked entirely in a separate `git worktree`
  (`/home/vboxuser/nutriguard-worktrees/feat-backend-ingredient-language-diagnostics`) so the live dev stack/checkout/database was never touched. Used two background subagents for the dry-run repair script and the verification test suite (both reported back, both reviewed/integrated by this session; see "Findings from delegated work" below).

### Task 1: consistent ingredient identity and language

**Root cause (traced, not assumed OCR-only)**: `app/services/ingredient_catalog.materialize_ingredients` persisted every OCR/Gemini-observed ingredient name into the shared catalog BEFORE any language/translation policy ever ran (the existing `label_language.resolve_label_text` translator only ran later, on the whole raw-text blob, inside the barcode/standalone "rebuild" step). Two further compounding bugs: `label_language._split_segments` never splits on commas, so ONE English trigger word (e.g. "Ingredients:", "Contains") anywhere in a large mixed-language block caused the WHOLE block -- including embedded foreign-language ingredient names -- to be accepted as `"en"` verbatim, skipping translation entirely; and `ocr_normalizer.normalize_and_extract_tokens` never splits on `:`, so an EU-style allergen-emphasis clause (e.g. "SECARA: agenti de crestere") can survive as one merged token. Confirmed the barcode-discovery path (Open Food Facts) was already correctly language-gated (V7) -- not a source of this bug.

**Fix** -- language resolution now runs ONCE, at the single choke point all 5 ingredient-assembly entry points funnel through:
- `app/services/language_detection.py` (pre-existing, untouched) -- per-token `detect_language`.
- NEW `app/services/ingredient_segmentation.py` -- `detect_ambiguous_segmentation(token)`: flags suspected OCR concatenation (embedded ALL-CAPS allergen-emphasis word, duplicated significant word, 7+-word unsegmented clause) with a stable reason code; NEVER attempts to re-segment or translate a flagged token ("translation alone must not certify identity"). Verified against all 7 examples from the reported bug: the 3 clean Romanian names translate cleanly; the 4 concatenated fragments ("SECARA agenți de creștere", "Produs din GRAU", "ZARA pudră", "LAPTE proteină din LAPTE") all correctly flag.
- NEW `app/services/ingredient_translation.py` -- `translate_ingredient_tokens(tokens)`: batches an ENTIRE ingredient list's untranslated tokens into ONE Gemini call (full-list context, task requirement), via a new `GeminiService.translate_ingredient_list` prompt (`app/integrations/gemini.py`). Per-entry structured validation + independent invariant checks (confidence >= 0.55, non-placeholder, E-number/numeric-token multiset preservation, and an English-plausibility check) -- one bad entry never invalidates the batch. The English-plausibility check needed a real fix mid-task (see "Findings" below).
- `app/services/ingredient_catalog.py`: `materialize_ingredients` now returns `(materialized, translation_occurred)` and runs a new `_resolve_ingredient_languages` pass FIRST: already-en/bg/unknown -> pass through; an alias already exists for the exact original text -> skip translation, reuse (no re-translation cost -- "don't translate again for every product/request"); ambiguous -> flag `identity_uncertain`, never translate; otherwise -> batch-translate. A reliable translation is rebuilt via `create_synthetic_ingredient` on the NOW-English text (correctly recomputing id/category AND allergen-keyword detection -- a real incidental fix, since the keyword list is English-only and previously never fired on untranslated foreign text). `_build_minimal_row` sets `source=GEMINI`+capped confidence for a translated row -- the PRE-EXISTING `SOURCE_PRIORITY`/`merge_verified_fields` gating (untouched) already guarantees GEMINI can never reach `VERIFIED`, so translation can never promote scientific verification. After resolving, the pre-translation text is registered as its own language-tagged `IngredientAlias` (reuses the ALREADY-EXISTING but previously-unpopulated `IngredientAlias.language` column).
- `Product` gained `original_ingredient_text`/`ingredient_text_source_language` (new columns, migration below) -- the TRUE pre-translation label text is now always preserved separately from `raw_ingredient_text` (which stays the canonical/identity-bearing text `ocr_normalizer.reconstruct_synthetic_ingredient` re-tokenizes on every later read -- keeping these two in sync, only overriding when translation actually changed something, was the single most important correctness property verified here; see the migration/Postgres cycle below).
- API: `IngredientOut` gained `identityUncertain`/`uncertaintyReason` (structured signal for Android to offer another label photo, per task requirement).

**Dry-run repair for old bad records** (delegated, reviewed): NEW `app/seed/repair_ingredient_language.py`, invoked as `python -m app.seed.repair_ingredient_language [--apply]` (mirrors `load_seed`'s convention). Dry-run by default; `--apply` required to write. Per-row SAVEPOINT, one commit at the end (or a `rollback()` in dry-run -- zero writes, confirmed by test); idempotent by construction (a repaired row is `ALREADY_FINE` on the next run). Never touches `CURATED_SEED`/`REGULATORY_LOOKUP` rows or ambiguous-segmentation rows; preserves the original text as an alias before overwriting `common_name`. `Product.raw_ingredient_text` is report-only (never rewritten -- rewriting it has correctness implications for id reconstruction that are out of scope for a repair tool). NOT run against any live database in this session.

### Task 2: ingredient information contract

Audited existing fields first: `description`/`purposeInFood`/`healthConcerns`+`sideEffects`/`references` already existed and already satisfy 4 of the 6 requested bullets. Genuinely missing: a structured "conditions under which effects apply" and "intake guidance distinct from ADI". Added, minimally:
- `Ingredient`/`IngredientLocalization` gained `effect_conditions`/`dietary_guidance` (new columns, wired through the EXISTING `LOCALIZED_FIELDS`/`build_localizations` EN/BG machinery) -- camelCase `effectConditions`/`dietaryGuidance` on the wire, `""` (never a placeholder) when the curated source states none.
- NEW `IngredientOut.adiPopulationScope` (`app/services/ingredient_regulatory.derive_gated_adi_population_scope`) -- `"PER_KG_BODY_WEIGHT"` exactly when a gated numeric ADI was parsed, else `null`. Makes explicit, on the wire, that the ADI is per-bodyweight, never a universal daily amount, and never a fabricated male/female split (the source text draws no such distinction, so none is invented).
- Deliberately did NOT author real `effectConditions`/`dietaryGuidance` curated content for the 12 seed ingredients in this pass -- the machinery is implemented and tested, but populating real scientific content needs a product-owner/scientific review pass, not a backend-engineering guess under this task's own "report missing scientific content honestly" instruction. Left as an explicit follow-up.
- Confirmed (structurally, via a test) that no field anywhere in the API claims a product "exceeds" an ingredient's limit -- no such field exists, and none was added; the backend has no per-product ingredient-quantity data to support such a claim in the first place.

### Task 3: scan diagnostics

Audited `app/core/scan_diagnostics.py` (bounded rotation, POSIX `flock` multi-process safety) -- UNCHANGED, already correct. Gaps were entirely at the call-site layer (`app/api/v1/scan.py`, rewritten):
- Previously only `/scan/label-image` wrote diagnostics; `/scan/barcode` and `/scan/ocr-text` had zero coverage. All three now call a `record_scan_diagnostic` wrapper.
- `/scan/label-image`'s early validation failures (bad content-type, oversized image) previously raised before `diagnostic_base` was even built, so they were silently uncovered -- now diagnosed too.
- The "success" diagnostic write previously happened BEFORE `_to_analysis_out(result)` (response construction); a hypothetical serialization failure there would have left a false "success" record. Moved inside the same try block -- a serialization failure now correctly falls into the generic failure-diagnostic path.
- New fields: `operation`/`dataSource` (barcode/ocr_text/label_image), coarse honest `stage` (only what this router layer actually observes -- never a guess at `food_analysis`'s internal steps, which aren't instrumented), `detectedLanguage` (from the new `Product.ingredient_text_source_language`), `translationUsed` (kept its original name from the pre-existing label-image-only diagnostic, not renamed), and new `recognizedIngredientCount`/`unresolvedIngredientCount`/`untranslatedIngredientCount` (computed from the real `identity_uncertain`/`uncertaintyReason` values on the returned ingredients, never guessed).
- Reuses the EXISTING `request.state.request_id` correlation id (already bound into structlog contextvars app-wide) -- no second id scheme introduced.
- Added local defense-in-depth: every call site now goes through `_safe_record_scan_diagnostic`, which locally catches+logs rather than relying solely on `record_scan_diagnostic`'s own internal guarantee never regressing (task: "diagnostic failures must never break scanning" -- verified by monkeypatching `record_scan_diagnostic` to raise and confirming all three endpoints still return their normal response/error).
- Default storage budget (`SCAN_DIAGNOSTICS_MAX_BYTES`=1 MiB, `SCAN_DIAGNOSTICS_BACKUP_COUNT`=1) left unchanged, per task instruction. No periodic/automatic reporting added.

### Migration

`alembic/versions/c6d7e8f9a0b1_ingredient_language_provenance.py`, `down_revision="b5c6d7e8f9a0"`. Adds: `products.original_ingredient_text`/`ingredient_text_source_language`; `ingredients.identity_uncertain`/`uncertainty_reason`/`effect_conditions`/`dietary_guidance`; `ingredient_localizations.effect_conditions`/`dietary_guidance`. Verified upgrade -> downgrade -> upgrade against a disposable PostgreSQL 16 instance with representative pre-existing data (a curated `Ingredient` with a REVIEWED Bulgarian localization, a `Product` referencing it, and a language-tagged `IngredientAlias`) -- all data and all 8 new columns survived every step correctly; `alembic heads` -> single head, `c6d7e8f9a0b1`. All 10 opt-in `tests/postgres/` concurrency tests re-run against the migrated schema: 10/10 passed.

### Findings from delegated work (both reviewed and fixed by this session before finalizing)

1. **Real bug, fixed**: `ingredient_translation._translation_is_reliable`'s English-plausibility check (`detect_language(translated_text) == "en"`) used the SAME word-frequency threshold `language_detection` uses for a whole label-text blob (needs one strong or two distinct weak recognized words) -- which a short, correct, 1-2 word ingredient translation (e.g. "MILK" -> "Milk") can never reach, so it was being wrongly rejected as "unreliable" 100% of the time. This directly undermined the task's own "Lapte"/"MILK" canonical-convergence example. Fixed with a narrow, bounded fallback: a <=2-word, plain-ASCII translated result is also accepted (independent script-level evidence it isn't still-foreign text), while longer text still goes through the stricter word-based check only (verified this doesn't let a longer ASCII-only foreign phrase, e.g. French "Lait Entier Complet", slip through).
2. **Defense-in-depth gap, fixed**: `app/api/v1/scan.py`'s diagnostic-write call sites had no LOCAL protection against `record_scan_diagnostic` raising, relying entirely on that function's own (already-correct) internal guarantee. Added `_safe_record_scan_diagnostic`; see Task 3 above.
3. **Deferred, not a bug**: `Product.original_ingredient_text`/`ingredient_text_source_language` are not exposed on `ProductOut` (only used internally for diagnostics) -- intentional for this pass; exposing them to Android (e.g. so the app can show "as printed" vs. "translated") is a reasonable follow-up if wanted.

### Verification

- `pytest tests/unit tests/integration -q` (pinned Python 3.12 image, built from this branch's own `Dockerfile`/`requirements.txt`): **562 passed, 0 failed, 0 skipped** (2 pre-existing, unrelated SAWarnings).
- `pytest tests/postgres -q` (disposable PostgreSQL 16, `NUTRIGUARD_TEST_POSTGRES_URL`): **10 passed**.
- Alembic upgrade -> downgrade -> upgrade cycle: see "Migration" above.
- `app.openapi()` vs. tracked `openapi.json` (pinned deps): **exact match** -- diff confined to the two changed schemas (`IngredientOut`, `IngredientLocalizedTextOut`), purely additive, no path changes.
- Live dev stack/database: **never touched** -- all verification used disposable Docker containers/networks and a separate git worktree, removed at the end of this session.

### Files involved

New: `app/services/ingredient_segmentation.py`, `app/services/ingredient_translation.py`, `app/seed/repair_ingredient_language.py`, migration `c6d7e8f9a0b1`, and 8 new test files (`tests/unit/test_ingredient_segmentation.py`, `tests/unit/test_ingredient_translation.py`, `tests/unit/test_ingredient_language_provenance_migration.py`, `tests/integration/test_ingredient_language_identity_e2e.py`, `tests/integration/test_scan_language_e2e.py`, `tests/integration/test_scan_diagnostics_endpoints.py`, `tests/integration/test_repair_ingredient_language.py`).

Modified: `app/models/ingredient.py`, `app/models/ingredient_localization.py`, `app/models/product.py`, `app/schemas/ingredient.py`, `app/services/ingredient_catalog.py`, `app/services/ingredient_localization.py`, `app/services/ingredient_regulatory.py`, `app/services/ocr_normalizer.py`, `app/services/fallback_analysis.py`, `app/services/food_analysis.py`, `app/integrations/gemini.py`, `app/api/v1/scan.py`, `openapi.json`, plus 3 existing test files updated for the new `materialize_ingredients` signature and the two additive field-set pin tests.

### Recommended next step

Review the branch diff and open PR, then merge only after CI/human review is green. Two deliberate follow-ups flagged for a future task, not blockers: (1) real curated `effectConditions`/`dietaryGuidance` content for the 12 seed ingredients, pending product-owner/scientific review; (2) whether `Product.original_ingredient_text`/`ingredient_text_source_language` should be exposed on `ProductOut` for Android. The dry-run repair tool (`python -m app.seed.repair_ingredient_language`) should be run for review (dry-run first) against the live database by a human before ever passing `--apply` there -- this task deliberately never did so.

---

## 2026-09-16: PR #18 pre-merge verification (Claude Code, isolated worktree)

Full independent verification of PR #18 (`feat/app-bilingual-enrichment`,
commit `eceaaff017737f82815fc630fd2b1f28b7b3b820`) requested ahead of
merge. Performed entirely in a separate `git worktree` (detached HEAD at
that exact SHA) and disposable Docker containers/network -- the live
dev stack (`nutriguard-backend-db-1`/`-redis-1`/`-backend-1`) and the
main checkout were never touched, switched, or stopped.

**1. Target verification**: `origin/feat/app-bilingual-enrichment` ==
`eceaaff017737f82815fc630fd2b1f28b7b3b820`, matching the requested SHA
exactly, re-checked again after finishing (no remote drift). GitHub
Actions for this SHA: both `tests` and `unit-tests` checks
`completed`/`success` (checked via the public REST API).

**2. Full backend suite** (pinned deps, Python 3.12, built from this
branch's own `Dockerfile`/`requirements.txt`, env isolated from any
`.env`): `python -m pytest -q` -> **486 passed, 10 skipped, 0 failed**
before this pass's additions; **491 passed, 10 skipped, 0 failed**
after (see item 6). All 10 skips are the pre-existing opt-in
`tests/postgres/` suite (needs `NUTRIGUARD_TEST_POSTGRES_URL`), not
failures.

**3. Disposable PostgreSQL 16 migration cycle** (unique container/
network, removed after use; live DB never touched, never downgraded):
- `alembic upgrade` to the new migration's actual parent
  (`a4b5c6d7e8f9`, NOT `e4f5a6b7c8d9` -- `main` has advanced two more
  migrations, `f5a6b7c8d9e0` and `a4b5c6d7e8f9`, since the last
  handoff entry below was written), inserted a pre-existing
  Ingredient(E951 Aspartame)/Product row pair using the OLD column
  set, then `upgrade -> b5c6d7e8f9a0 -> verify -> downgrade ->
  a4b5c6d7e8f9 -> verify -> upgrade -> b5c6d7e8f9a0 -> verify`: **the
  pre-existing ingredient row, its product's `ingredient_ids`
  reference, the `ingredient_localizations` table, its composite PK
  (`ingredient_id`,`language`), its `ON DELETE CASCADE` FK, and its
  `language IN ('en','bg')` CHECK constraint all survived/reappeared
  correctly at every step.** Also directly verified: a duplicate
  `(ingredient_id, language)` insert is rejected (PK uniqueness);
  `language='fr'` is rejected (CHECK constraint); deleting the parent
  Ingredient cascades and removes its localization row.
- Seed loader run twice against the same disposable instance:
  **idempotent** -- identical `(ingredients=47, aliases=58,
  bg_localizations=12)` after both runs, no duplicate
  `(ingredient_id, language)` rows. All 12 reviewed Bulgarian rows
  verified attached to the correct canonical curated ingredient id
  (e.g. `e621_msg` -> "Monosodium Glutamate" / "Мононатриев глутамат").
- `alembic heads`: **exactly one head**, `b5c6d7e8f9a0`.
- All 10 opt-in `tests/postgres/` tests (unrelated pre-existing
  concurrency/identifier-precedence suites) also re-run against this
  same disposable instance after migrating to head: **10/10 passed**
  (one transient failure was traced to leftover data from this
  session's own earlier ad-hoc script runs against the same
  container, not a product bug -- resolved by resetting the schema;
  10/10 passed again from a clean slate).

**4. OpenAPI**: `app.openapi()` regenerated inside the pinned-dependency
image and compared to the tracked `openapi.json` -- **exact match, 0
differences**. No changes needed.

**5. Localization behavior**: read through `Ingredient.
loaded_localization_rows`, `IngredientOut.localizations`, and
`ingredient_localization.build_localizations` (used by both the
Pydantic schema path and the hand-built `_ingredient_out_dict` partial-
response path). Confirmed by inspection and by the new tests in item 6:
existing English top-level fields are untouched; only a `REVIEWED` +
content-hash-matching Bulgarian row is ever surfaced (a `DRAFT` row or
one whose hash no longer matches the current English text falls back
to English); identifiers/E-numbers/ADI/enums/citations/URLs are never
duplicated into the localized profile; translation review status lives
on a wholly separate table/enum from `Ingredient.verification_status`
and is never written together with it anywhere in `load_seed.py` or
`ingredient_catalog.py`, so a translation being `REVIEWED` cannot
promote the underlying scientific/regulatory evidence.

**6. Regression coverage added** (task requirement: exercise the
MissingGreenlet fix with actual SQLAlchemy objects, not mocks -- the
existing unit tests in `test_ingredient_schema_data_quality.py`/
`test_ingredient_localization*.py` only used `SimpleNamespace`/a hand-
written fake class for the "unloaded relationship" case). New file
`tests/integration/test_ingredient_localization_response_paths.py`
(+5 tests, all passing):
- Forced a REAL, persistent `Ingredient` row's `localization_rows` to
  come back genuinely unloaded (via an explicit `lazyload()` query
  option -- confirmed no normal repository query anywhere in `app/`
  does this itself; `lazy="selectin"` already protects every default
  query path). Proved touching the raw relationship synchronously
  reproduces the exact `MissingGreenlet` error CI hit, and that both
  serializers (`IngredientOut.model_validate` and
  `_ingredient_out_dict`) correctly fall back to English-only instead
  of raising.
- End-to-end, through the real FastAPI app with a real reviewed
  Bulgarian row attached to a real Ingredient: `GET /products/
  {barcode}` (normal product response), `GET /ingredients` (product
  listing), a hand-built `labelScanRequired` partial response
  (`_label_scan_required_details`), and `POST /scan/label-image` with
  a Gemini-mocked payload resolved via official E-number match to the
  same curated row (barcode/label scan) -- all four correctly surface
  the reviewed Bulgarian localization alongside the unchanged English
  fields, eNumber, and citations.
- Full suite after adding these: **491 passed, 10 skipped, 0 failed**
  (pinned image). OpenAPI re-confirmed exact match after the rebuild
  (test-only change, no schema/route touched).

**Files involved this pass**: only
`tests/integration/test_ingredient_localization_response_paths.py`
added (new file). No `app/` code changed -- no reproducible product
bug was found; the only gap was test coverage, now closed.

**Unresolved / carried forward**: none new. Everything listed as
unresolved in the "app-wide EN/BG" entry immediately below this one
remains accurate context but is otherwise unaffected by this pass.

**Recommended next step**: commit and push this one new test file to
`feat/app-bilingual-enrichment` (no `app/` changes, so no re-review of
production behavior is needed beyond this file) and merge PR #18 once
CI is green on the resulting commit. Live deployment was not touched
at any point in this session.

---

## 2026-09-16: CI regression fix for unloaded ingredient localizations

- PR #18's first backend CI run failed with 20 `MissingGreenlet`
  errors while Pydantic serialized `Ingredient.localization_rows` from
  legacy product-query paths that had not preloaded the new async
  relationship. Product data itself was valid; synchronous response
  serialization was accidentally attempting hidden database I/O.
- Added `Ingredient.loaded_localization_rows`, a SQLAlchemy-state-aware
  view that returns reviewed translations only when the relationship is
  already loaded. `IngredientOut` now validates its internal translation
  rows through that safe view, and `build_localizations` follows the same
  rule. An unloaded relationship therefore produces the canonical
  English profile instead of HTTP 500; normally preloaded reviewed
  Bulgarian profiles remain unchanged.
- Added a regression test whose unsafe relationship accessor raises if
  touched, proving schema validation uses the no-I/O path and returns an
  English localization.
- Files involved: `app/models/ingredient.py`,
  `app/schemas/ingredient.py`, `app/services/ingredient_localization.py`,
  and `tests/unit/test_ingredient_schema_data_quality.py`.
- Local verification: `git diff --check` passed. The Windows workspace
  has no backend Python/Docker runtime, so the full backend suite is to
  be verified by the new GitHub Actions run after this fix is pushed.
- Unresolved issue: none in the diagnosed serialization path; CI remains
  the required full-suite confirmation.
- Recommended next step: push the focused fix to PR #18 and require both
  Android and backend checks to be green before considering merge.

## 2026-09-11: app-wide EN/BG and reviewed ingredient localizations

- Android and backend were updated together on the existing
  `fix/ingredient-details-nova-ui` branch; no commit, push, merge or
  deployment was performed in this task.
- Backend: added `ingredient_localizations`, keyed by ingredient and
  language, with review/source provenance and canonical-content hashes.
  Existing English fields remain canonical/backward compatible. The
  API additively returns `localizations.en` and a current reviewed
  `localizations.bg`; identifiers, numeric ADI, enums and URLs remain
  unchanged. Twelve rich seed ingredients have Bulgarian profiles
  marked `MACHINE_TRANSLATED` + `REVIEWED`.
- Android: added persistent localization JSON to the Room ingredient
  cache (schema 3→4), resolves Bulgarian with per-field English
  fallback, and applies it to ingredient cards, details and library
  search. A persistent EN/BG control now switches app-owned UI text,
  dates, accessibility labels and available scientific profiles without
  rescanning. Original product names, brands and OCR label text remain
  unchanged.
- Files involved: new backend localization model/service/seed/migration
  and related schema/seed/serializer/tests; Android i18n components,
  DTO/Room mapping, localized ingredient presentation and tests.
- Verification: Android `:app:testDebugUnitTest :app:lintDebug
  --rerun-tasks` passed after all changes: **116 tests, 0 failed, 0
  skipped**, and lint succeeded. The Windows host has no Python or
  Docker runtime, so the complete backend pytest suite, pinned OpenAPI
  regeneration and disposable-PostgreSQL migration cycle still need to
  run on the backend VM/CI. The tracked OpenAPI snapshot was updated to
  the additive localization shape and parses as valid JSON, but must be
  compared byte-for-byte with the pinned runtime generator there.
- Recommended next step: run both full suites, regenerate and review
  `openapi.json` using the pinned backend dependencies, verify the new
  Alembic migration against disposable PostgreSQL, then commit and open
  a PR only after review.

## 2026-09-09: E-number starter knowledge and compact Android presentation

- Branch: `feat/e-number-knowledge-ui`, based on GitHub `origin/main`
  at `c94c20193602d9b90cf60d090dff7f50f383ba95`.
- Added `app/seed/e_additives_curated_starter.csv`: exactly the 43
  populated rows from the provided NutriGuard E-number starter pack.
  The companion E200–E999 registry was deliberately not imported: 757
  of its 800 rows are coverage placeholders, not confirmed additives.
- `app.seed.load_seed` now adds 35 previously absent E-number identities
  (8 overlap richer existing seed rows and are never overwritten), for
  47 total catalog ingredients. New starter rows are deliberately
  `LIMITED_DATA`, `risk_assessment_available=False`, with nullable
  dietary flags. Their identity, function, available health text and
  informational source URLs are reusable; they do not affect Health
  Score or claim regulatory approval/ADI without stronger per-field
  evidence.
- Android now parses the backend's structured ingredient data-quality
  fields (`riskAssessmentAvailable`, gated EFSA/FDA status, numeric ADI,
  rationale and sources) and preserves explicit JSON `null` dietary
  flags. It no longer fabricates `Approved`, `GRAS`, `Safe`, `None
  reported`, or generic OCR profile text.
- Ingredient cards and the detail sheet omit absent information.
  EFSA/FDA appear only as compact approved/not-approved icon rows;
  numeric ADI appears only when supplied; references/URLs are collapsed
  in a final Sources section. Room migration 2→3 preserves product,
  history and profile data while updating the ingredient cache schema.
- Verification: Android `:app:testDebugUnitTest` passed. Backend loader
  files pass `py_compile`; the backend pytest environment is not
  installed on this Windows host, so `test_load_seed.py` still requires
  execution in backend CI or the VM's pinned Docker environment.

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
