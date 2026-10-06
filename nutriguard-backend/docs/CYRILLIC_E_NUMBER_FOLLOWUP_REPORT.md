# Cyrillic E-number follow-up: legacy identities, live catalogue audit, E-number duplicates, completeness, diagnostics handoff

Branch: `fix/backend-cyrillic-e-number-followup`, based on reviewed commit
`a915243dd4243f0468bdc25d35e9482aed751be6` (`fix/backend-cyrillic-e-number`).
Scope: backend only. Nothing was merged, deployed, restarted or repaired on the
live system; the live database was only read, inside `READ ONLY` transactions
that were rolled back.

Live observations below are dated 2026-10-06 and are not acceptance counts.

## 1. Backward-compatible ingredient identity

### Finding

The live data showed the earlier report's concern is real, but the dominant
legacy shape is older than the Cyrillic-E fold. Synthetic ids have had four
generations (see `git log` of `app/services/ocr_normalizer.py`):

| Gen | Commit | Id shape |
|---|---|---|
| 0 | `3add7a1` baseline | `synth_<slug>`, no hash; slug = `[a-z0-9_]` of the lower-cased name with spaces as `_`; **no underscore collapsing**, so Cyrillic names collapse to `synth_`, `synth__`, `synth___`, ... |
| 1 | `09f6136`..`1d8c3d9` | as gen 0, but an empty slug becomes `synth_` + `sha1(name)[:10]` (case-sensitive) |
| 2 | `47b6462`..`a915243^` | `synth_<slug>_<sha1(lower name)[:12]>`; hashed **unfolded** text, and alias-substituted text for Bulgarian words |
| 3 | `a915243` | as gen 2, hashed after folding Cyrillic Е before an E-number |

Every live "unresolved" product reference is gen 0 or gen 1.

### Change

`resolve_synthetic_identity(id, raw_text)` in `app/services/ocr_normalizer.py`
matches a stored id to a token of the **stored original text**:

1. `CURRENT`: today's id (unchanged first priority, so current resolution is preserved).
2. `LEGACY_CYRILLIC_E`: gen-2 id of the unfolded Cyrillic-E token.
3. `LEGACY_ALIAS`: gen-2 id of the Bulgarian alias text (`Вода` stored as `Water`).
4. `LEGACY_BARE_SLUG` (gen 0) and `LEGACY_HASH10` (gen 1), also through the alias text.

A stage whose tokens yield different identities returns `AMBIGUOUS` and recovers
nothing (the old readable-slug fallback is kept). Recovered ingredients keep
the **stored id** and are built from the original token, as a fresh scan builds
them. An E-number is only recovered when the stored token literally carries
Cyrillic `Е` + digits; a bare number is never promoted (tests below).

On read, `fetch_ingredients_for_product` (`food_analysis._resolve_unmatched_reference`)
additionally resolves **every unambiguous, text-backed identity match that carries an
explicit E-number, current-format ids included** (review finding 1), to the catalogue
row that owns that official identifier (same E-number-first rule as a fresh scan), so
curated narrative added after the product was saved is reached. Fail-closed cases are
unchanged: an ambiguous or unmatched id, and a bare number, never reach a catalogue row.
The lookup is SELECT-only (tested with a statement spy); `Product.ingredient_ids` is
never rewritten.

`scripts/audit_synthetic_ingredient_identity.py` now treats recoverable legacy ids
as non-damage, and sets `SET TRANSACTION READ ONLY` on PostgreSQL.

### Tests (all new unless noted)

- `tests/unit/test_ocr_normalizer.py` (+17): legacy Cyrillic-E id, Cyrillic words with no readable slug, current id wins, bare number never an E-number, unrelated text does not recover, alias substitution, ambiguity (including the shared `synth_` id of several Cyrillic tokens, duplicate tokens of one identity, a simulated hash collision), gen-0 and gen-1 recovery, gen-1 case sensitivity.
- `tests/integration/test_legacy_synthetic_identity_reload.py` (6): persists a `Product` with legacy ids and reloads it through `POST /scan/barcode`; asserts E414/alias identity recovered, stored row untouched, a bare `414` never becomes an E-number, and legacy `Е300` ids of both generations reach the curated E300 row. Review finding 1 adds: **save, then curated E300 appears, then reload** with a current-format id (before: empty synthetic profile; after: canonical id `e300_ascorbic_acid`, name, description and purpose); ambiguous/unmatched/bare-number ids fail closed even when a catalogue row exists; and the reload lookup issues only SELECT statements.
- `tests/integration/test_audit_synthetic_ingredient_identity.py` (changed 1, added 1): the former "damaged" Cyrillic-E case is now recoverable; without supporting Cyrillic text it is still `BARE_NUMBER_NO_IDENTITY`.

### Before / after (regression demonstration)

Persistence + reload test file run against the pre-change `ocr_normalizer.py`
(commit `a915243` version bind-mounted into the same image). The finding-1 tests were also
run against the previous `food_analysis.py` (commit `39fac43`, legacy-only lookup):
`2 failed, 4 passed` (`assert 'synth_e300_4d18679422d6' == 'e300_ascorbic_acid'`); with the fix all 6 pass.
First comparison:

```
FAILED test_product_persisted_with_legacy_ids_reloads_with_identity
  assert e414["eNumber"] == "E414"  ->  AssertionError: assert None == 'E414'
1 failed, 1 passed        (the bare-number test passes on both, as it should)
```
With the change: all three reload tests pass.

Live effect (same 234 references, re-evaluated read-only, section 2): before,
all 234 degraded (28 to a bare number, 76 to the generic placeholder, 130 to a
lossy slug); after, 196 are recovered from stored text and 38 remain honestly
ambiguous.

## 2. Read-only live catalogue audit (2026-10-06)

Tool: `scripts/audit_catalogue_readonly.py` (SELECT-only, `READ ONLY`
transaction, rolled back; reads `ingredients`, `ingredient_aliases`,
`ingredient_localizations` and from `products` only barcode, raw ingredient
text, ingredient ids, a flag; never users, health profiles, history, secrets or
logs). Run from a disposable container on the live Docker network; credentials
were read inside the shell and not printed.

Totals reproduce Codex's 2026-10-05 observation exactly: 100 products, 523
ingredients, 545 aliases, 14 localizations, 254 `identity_uncertain`; empty
fields: description 505, purpose 475, health_concerns 501, references 475
(side_effects 511, effect_conditions 522, dietary_guidance 522). Sources:
472 `OCR_HEURISTIC`, 48 `CURATED_SEED`, 3 `GEMINI`; 475 `UNVERIFIED`, 36
`LIMITED_DATA`, 12 `VERIFIED`.

Unresolved product to ingredient references: **234 references, 114 distinct ids,
27 products** (matches). Classification with the new resolver:

| Class | References | Products | Meaning |
|---|---|---|---|
| `RECOVERABLE_LEGACY_BARE_SLUG_GEN0` | 184 | 27 | valid gen-0 synthetic id; a stored token reproduces it |
| `RECOVERABLE_LEGACY_HASH10_GEN1` | 12 | 9 | valid gen-1 id (pure-Cyrillic name); stored token reproduces it |
| `AMBIGUOUS` | 38 | 9 | gen-0 id collapsed by Cyrillic text (`synth_`, `synth__`, `synth___`, `synth_____`: 4 ids); several tokens share it, so it cannot be told apart from the id |
| genuinely missing / corrupt | 0 | 0 | no non-synthetic dangling ids and no id lacking supporting text were found |

So none of the 234 is corruption: they are valid legacy synthetic references.
The 38 ambiguous ones are not recoverable by id and need a re-scan (or a human-reviewed
positional mapping); they were not guessed. Per-id counts overlap between classes
because the same id can be supported by different products' text.

**21 of the recovered references (8 distinct ids, e.g. `synth__300`, `synth__202`,
`synth_211`, `synth_950`, `synth__955`, `synth__330`, `synth__471`)** carry a literal
E-number whose curated catalogue row exists with narrative; before this change they
never reached it, after it they resolve to that row.

### Other live findings worth an owner decision

- **Historical error-text ingredient rows (not proven to be current).** Two catalogue rows are the
  provider-failure sentences "AI response was unavailable or invalid" and "Ingredients could not be
  extracted from the image" (`OCR_HEURISTIC`, `UNVERIFIED`, `retrieved_at` 2026-09-09), referenced by
  17 products (34 references). The code already prevents new ones: `_run_label_image_pipeline` clears the
  failed-image fallback text and ingredients (commit `d372985`, issue #23 stage 2), covered by
  `test_a_failed_label_scan_no_longer_puts_error_text_into_the_catalog`; both entry points
  (`analyze_label_image` and `analyze_label_image_with_barcode`) go through that function, and it is the only
  place the placeholder sentence is built. All 17 referencing products were created between
  2026-09-09 and 2026-09-26 (none later), which is consistent with historical data but does **not** prove no
  current scan can create such a row. The dry-run inventory below only lists them.
  `scripts/inventory_error_text_ingredients.py` (read-only, SELECT-only, tested) reports rows, aliases,
  localizations, candidate-queue rows and referencing products (with `created_at`/`source`); live result:
  2 rows, 17 products, 34 references, 0 candidate rows, 0 localizations. No deletion or repair is proposed here.
- **Seven legacy rows with `e_number` NULL whose text is a Cyrillic E-number**
  (`synth_300_...` "Киселина: Е300", `synth_202_...`, `synth_211_...`, `synth_414_...`,
  `synth_955_...`, `synth_950_...`, `synth_150d_...` "Оцветител (Е 150d"), each referenced by one
  product. They exist as rows, so the read-side fix does not apply; see the dry-run plan in section 3.
- `CURATED_SEED` E-numbers come from migrations as well as the seed loader (a freshly migrated database already holds E150d, E211, ...).

## 3. E-number uniqueness and the E150D / E150d duplicate

### Root cause (verified in code and live data)

- `ingredients.e_number` has a **case-sensitive** unique index (`ix_ingredients_e_number`),
  as does `ins_number`. OCR upper-cases a suffix (`create_synthetic_ingredient` produces
  `E150D`, ins `150D`); the pilot importer wrote `E150d` / `150d`. The previous
  `get_by_official_identifier` compared with `==`, so `E150D` never found `E150d`, and the
  index happily allowed both. The OpenFoodTox pilot import (2026-10-02) then created `E150d`
  while the OCR `E150D` row already existed.
- Live rows: OCR `synth_colorant_e150d_0495700507e9` (`E150D`, `OCR_HEURISTIC`,
  `UNVERIFIED`, empty narrative, 6 product references, 1 alias) and curated
  `e150d_sulphite_ammonia_caramel` (`E150d`, `CURATED_SEED`, `LIMITED_DATA`, populated profile,
  0 references, 2 aliases, 1 BG localization `DRAFT`/owner-approved, current hash). A third row
  `synth_150d_...` ("Оцветител (Е 150d", `e_number` NULL, 1 reference) is a pre-fold legacy row.
- It is the only case collision in the catalogue (also one `ins_number` collision: `150D`/`150d`).
  No `e_number` contains a non-ASCII character; no value has an unexpected shape.
  The loaders also differ on `ins_number` case (`load_seed` upper, pilot lower).

### Change (new scans only; no live row is touched)

`ingredient_repository.get_by_official_identifier` and `get_by_id_or_e_number` now match
E-/INS-numbers case-insensitively (`upper(col) = upper(value)`) and break ties
deterministically: trusted source first, then id (exact id first for the API lookup). The suffix letter is
still significant (E150a, E150b, E150c, E150d stay distinct). Behaviour change to review: a
new OCR `E150D` observation now resolves to the curated `E150d` row instead of minting a twin.
`_reconcile_official_identifier_conflict` cannot delete the OCR row in that case (it carries an
`e_number`, so it is not a provably weaker duplicate, and it is referenced). `upper()` on the
column does not use the existing index; negligible at this size (see proposal below).

Tests: `tests/integration/test_ingredient_catalog.py` (+4: suffix-case insensitivity, subtype
distinctions, OCR `E150D` resolves to curated without a new row, deterministic result when both
rows already exist); Postgres: `tests/postgres/test_e_number_case_postgres.py` (real enums, the
case-sensitive index, and that `READ ONLY` rejects a write).

### Dry-run remediation plan (not applied)

Tool: `scripts/plan_e_number_remediation.py` (read-only, prints a JSON plan; tested to issue
only SELECTs and change nothing). Live output for E150D:

- Canonical target: `e150d_sulphite_ammonia_caramel` (trusted source, higher confidence,
  populated profile). `needs_manual_decision: false`; `fields_modified_on_target: []`.
- Affected: OCR row `synth_colorant_e150d_0495700507e9`: 6 product references, alias
  `Colorant: e150d`, no narrative (so `requires_manual_review: false`).
- Proposed steps for a LATER reviewed task: repoint that alias (alias_normalized is globally
  unique, only `ingredient_id` changes); rewrite `Product.ingredient_ids` old to new for the 6 products
  (dropping a duplicate id); keep the source row until zero references remain; delete only as a separate approved step.
- Preserved/unchanged: target narrative, provenance, `LIMITED_DATA` status, BG localization
  (`DRAFT`, owner-approved, not promoted), risk/scoring/regulatory fields, product references. Nothing is promoted to verified.
- Legacy Cyrillic-E rows (7): E150D, E300, E202, E211, E955 and E950 point at curated targets;
  **E414 has no curated row**, its target is another OCR stub (`synth_e414_...`), a content gap and
  not a duplicate. The plan lists them separately from case collisions.
- Schema enforcement (proposal only, not applied): after `remaining_case_collisions` is 0 and
  the repoint step is applied, `CREATE UNIQUE INDEX CONCURRENTLY ix_ingredients_e_number_upper ON
  ingredients (upper(e_number)) WHERE e_number IS NOT NULL;` and the same for `ins_number`.
  Creating it before the E150D/E150d collision (and the `150D`/`150d` ins pair) is resolved would fail.
  Also decide a canonical stored casing (EU style `E150d` vs OCR `E150D`); this change does not alter emitted `eNumber`.

## 4. Content completeness report

Evidence flags, not review verdicts: non-empty fields are never treated as scientifically complete.
Data: `completeness` and `priority_ingredients` sections of `scripts/audit_catalogue_readonly.py`.

Among the 100 products' referenced ingredients (23 with an E-number): 403 have identity but
no description/purpose/health text, 400 are unverified OCR stubs, 208 are `identity_uncertain`,
414 lack a BG localization, 2 BG localizations are draft/not human-reviewed (none stale), 17 have a
seed candidate. "Not applicable vs not researched" cannot be decided from data: E-numbered rows are
reported `NOT_YET_RESEARCHED`; plain foods `APPLICABILITY_UNDECIDED_NOT_APPLICABLE_CANDIDATE` (397),
an owner decision.

| Priority | Catalogue state (live) | Why content is thin / not reached | Source candidates (not imported) |
|---|---|---|---|
| E300 ascorbic acid | row `e300_ascorbic_acid`, `CURATED_SEED`/`LIMITED_DATA`, purpose set; description, health_concerns, side_effects, effect_conditions, dietary_guidance empty; 0 references; no BG localization | legacy references (`synth__300`, 2) did not reach it; Cyrillic row `synth_300_...` (e_number NULL) | curated CSV row (ADI present, no effects text); OpenFoodTox raw: 7 dossiers, feed-additive context, needs filtering, not drafted |
| E202 potassium sorbate | same shape; 1 reference | `synth__202` x5 and `synth_202_...` row not reaching it | curated CSV (ADI, no effects); OFT raw 7, not drafted |
| E211 sodium benzoate | same shape; 1 reference | `synth_211` x3, `synth_211_...` row | curated CSV; OFT raw 5, not drafted |
| E414 gum arabic | **no curated row**; two OCR stubs: `synth_e414_...` (E414) and `synth_gum_arabic_...` (`identity_uncertain`, e_number NULL, link unproven) | missing identity; refs `synth__414` x3 | none in curated CSV or seed JSON; OFT raw 3, not drafted |
| E950 acesulfame K | same shape as E300; 1 reference | `synth_950` x3, `synth_950_...` row | curated CSV; OFT raw 2, not drafted |
| E955 sucralose | row has description + purpose; health_concerns, side_effects empty; 0 references | `synth__955` x3, `synth_955_...` row | curated CSV (has effects text); OFT raw 2, not drafted |
| water | `synth_water_...`, OCR stub, 13 references, all narrative empty | plus 6 legacy references (`synth_water`) | none (additive-only catalogue); applicability undecided |
| sugar | OCR stub, 27 references (the most referenced), empty | plus 3 legacy | none; applicability undecided |
| carbon dioxide | OCR stub, 1 reference | plus 1 legacy | none in the seed files; applicability undecided |

OpenFoodTox pilot allowlist is exactly E150d, E250, E330, E951 (profiles carry
`scientific_review_status: not_reviewed`). None of the priority E-codes is drafted or imported.
Raw staging counts are from the earlier report and were not re-derived here.
Nothing was imported or invented; no live content changed.

Next by product reach (all stubs): salt 20, wheat flour 12, "Ароматизант" 11, skimmed milk powder 7,
E330 citric acid 6 (curated, draft BG), E150D 6, HFCS 5, E322 soy lecithin 4 (curated). Also the two
historical error-text rows (17 products each, section 2).

## 5. Scan-attempt diagnostics handoff (issue #30)

No code was integrated. The reviewed plan and contract for Codex is
`docs/SCAN_ATTEMPT_DIAGNOSTICS_INTEGRATION_PLAN.md` (read-only investigation of `origin/main` and
`origin/feat/backend-scan-attempt-diagnostics-issue-30`; items it could not verify are marked
UNVERIFIED). Headlines:

- Main has a bounded, `flock`-protected journal keyed by a UUID `requestId` only; no 16-digit id,
  no client-event endpoint, no pipeline tracing, no owner scope.
- The old branch (5 commits, merge-base `65a8e689`) adds the header contract, client events, a SQLite
  dedup ledger, HMAC owner scope, pipeline tracing and a CLI. `git merge-tree` against current main
  shows one textual conflict (`docs/CODEX_HANDOFF.md`); `openapi.json` merges but must be regenerated.
  The 30 commits on main since then do not touch the diagnostics files, so porting is cheap, but wholesale cherry-picking is not recommended.
- Gaps to close: client reasons for failures before the request (camera permission, decode, DNS/TLS,
  compression, token refresh), coarse server stages, early 401/422/429 rejections leaving no journal line, client events not bound
  to a server attempt, and a single-host ledger while production runs `--workers 4` on one volume.
- The plan lists 8 ordered commits (Android last), the Android contract, acceptance tests including a multi-process ledger test and a
  failure-before-server test, and open questions for the owner.

## 6. Verification

Environment: disposable image built from the backend `Dockerfile` (`python:3.12-slim`, pinned
`requirements.txt`) on the deployment VM, in its own Docker network with its own PostgreSQL 16;
the `nutriguard-backend-*` containers and the bind-mounted checkout were not touched.
The local host has no Docker.

- Default run, offline (`--network none`, SQLite, Postgres opt-in tests skipped):
  `python -m pytest -q -p no:cacheprovider` -> **949 passed, 22 skipped, 2 warnings in 34.84s**
  (previous report's baseline: 916 passed, 20 skipped; the extra skips are the two new opt-in Postgres tests).
- Same suite with `NUTRIGUARD_TEST_POSTGRES_URL` set to the disposable database after
  `alembic upgrade head` (all migrations apply on PostgreSQL 16): **970 passed, 1 skipped, 2 warnings in 40.70s**.
  The skip is `tests/postgres/test_openfoodtox_pilot_import_postgres.py` (needs a separate EMPTY database via
  `NUTRIGUARD_TEST_POSTGRES_MIGRATION_URL` because it runs real `alembic downgrade`); it was not run.
- Focused regressions (reload, inventory, candidate queue, both id/E-number Postgres files): 43 passed.
- **PostgreSQL id-length test (review finding 2).** The earlier failure of
  `test_synthetic_ingredient_id_postgres` came from its punctuation-heavy fixture name containing `E211`: with a
  seeded/migrated `E211` the resolver correctly reuses the catalogue row, so no synthetic row exists to assert on.
  Insertion/length testing is now separated from official-identifier reuse: the fixture name has no E-number
  (heavy punctuation kept; the test asserts no fixture name carries one), and a new self-contained test
  `test_a_punctuation_heavy_name_with_an_e_number_reuses_the_catalogue_row_instead_of_inserting` creates its own curated
  row under an unseeded E-number (`E997z`), resolves `E997Z`, asserts reuse and that no synthetic row was inserted, and removes
  only its own rows. Real PostgreSQL INSERT coverage (long ASCII, Unicode, punctuation-heavy, colliding prefixes) and
  the 64-character length assertions are unchanged; no curated rows were deleted.
- OpenAPI: `app.main.app.openapi()` compared with the checked-in `openapi.json` as sorted JSON: **identical**
  (no wire-contract change; no README deviation entry needed).

## 7. Limitations and decisions needed

- 38 live references are ambiguous by construction (gen-0 collapsed Cyrillic ids). Recovery needs a re-scan or a reviewed mapping; none was guessed.
- The E150D to E150d lookup change alters resolution for new scans; it needs review before deploy. The remediation plan and any unique index are not applied.
- Legacy rows with a NULL `e_number` (7) and the 2 historical error-text rows need a decision and a separate task; current contamination is not asserted.
- The read path now resolves any text-backed E-number reference that is not a catalogue row to the catalogue row, with up to two extra SELECTs per such reference per product load.
- Raw OpenFoodTox counts for the priority codes were not re-derived (1.4 GB staging file); they come from the earlier report.
- No live data, container, secret or checkout was modified. The disposable test containers and the temporary directory `~/nutriguard-claude-task` on the VM are not cleaned up yet; remove them when the owner confirms.

## 8. Android handoff

No Android-visible contract change in this task: same endpoints, same camelCase fields. Visible effect for
existing products: legacy Cyrillic/Latin ingredient names now read back with their original names, legacy
`Е300`-style references show the curated E-number row's content, and the ambiguous collapsed ones keep the generic
placeholder until rescanned. For scan diagnostics, use the integration plan above.
