# Cyrillic E-number identity & catalogue resolution — fix report

Branch: `fix/backend-cyrillic-e-number`
Scope: backend only (`nutriguard-backend/`). No Android/Kotlin files touched.

This continues prior in-progress work on this branch (the `ocr_normalizer.py`
Cyrillic-E fold + its first unit test already existed, uncommitted, at the
start of this session) and completes/verifies it per the task's 7 sections.

## 1–2. Root causes and the fix

### 1.1 Proven, reproduced root causes

**(a) `ocr_normalizer.py` never recognized Cyrillic "Е" (U+0415/U+0435) as an
E-number prefix** (`app/services/ocr_normalizer.py`). The original regex
`r"e[- ]?(\d{3,4}[a-z]?)"` only matched ASCII `e`/`E`. A Bulgarian label
written with Cyrillic "Е300" degraded to a bare, unidentified text token —
no `e_number`, no catalogue match, no "Food Additive" category. This was
already fixed in the branch's starting state via:
- `_E_NUMBER` widened to `[eе][- ]?(\d{3,4}[a-z]?)` (matches both
  scripts, `re.IGNORECASE` folds case within each script).
- `_CYRILLIC_E_BEFORE_NUMBER` folds a Cyrillic "Е"/"е" immediately before an
  E-number pattern to Latin "E" inside `normalize_and_extract_tokens`, so
  token text, ids, and matching are all consistent going forward.

I verified and extended this:

**(b) The SAME Cyrillic-E gap existed in two more runtime paths** (task:
"inspect all relevant parsing paths, not only `ocr_normalizer.py`"):
- `app/services/label_language.py` (`_E_NUMBER_RE`) — used to verify that a
  Gemini whole-label translation preserved every E-number. A Cyrillic-script
  source (not only Bulgarian — Russian/Ukrainian/Serbian also use Cyrillic
  "Е") would count **zero** source E-numbers while the correctly-translated
  Latin-script output counted one or more, making a **correct** translation
  look like it invented an E-number and get rejected as unreliable.
- `app/services/ingredient_translation.py` (`_E_NUMBER_RE`) — the identical
  per-ingredient-token version of the same check, reached whenever a token's
  detected language is anything other than `en`/`bg`/`unknown` (so: other
  Cyrillic-script languages, not Bulgarian, which is explicitly skipped
  before reaching this module).
- `app/services/gemini_image_parser.py`'s `_resolve_ingredients` builds a
  synthetic ingredient **directly** from Gemini's own structured
  `commonName`/`eNumber` JSON fields, bypassing `normalize_and_extract_tokens`
  entirely — so a Cyrillic `eNumber` Gemini echoes back from a label photo
  reached `create_synthetic_ingredient` unfolded. Fixed by moving the fold
  into `create_synthetic_ingredient` itself (idempotent with the tokenizer's
  own fold), so **every** caller gets a canonical id regardless of path.

Both are fixed with the same `[eе]` pattern, with a unit test each
(`tests/unit/test_gemini_image_parser.py::test_cyrillic_e_number_in_structured_gemini_ingredient_is_recognized`).
`label_language`/`ingredient_translation` fixes are defensive correctness
fixes for non-Bulgarian Cyrillic-script labels; I could not construct an
HTTP-level regression test exercising them without a live/non-deterministic
translation model, so they're covered at the regex-construction level only
— flagged under Remaining limitations below.

**(c) A real, provable identity-loss bug in the Bulgarian-alias dedup path**
(`app/services/food_analysis.py`, both `_finalize_barcode_enrichment` and
`_finalize_standalone_label_analysis`): `label_language.bulgarian_ingredient_alias`
substitutes a handful of common Bulgarian words ("вода"→"Water",
"захар"→"Sugar", etc.) **for catalogue-matching purposes only** — its own
docstring says explicitly "never to alter the stored/displayed text". But
the code built the **synthetic ingredient** (when no catalogue row matched)
from the **substituted** English text, not the original token, while
`Product.raw_ingredient_text` kept the **original** (Cyrillic) text. Since
`reconstruct_synthetic_ingredient` always re-tokenizes the **persisted**
text, it could never regenerate an id that was computed from a different
(substituted) string. I proved this directly (see transcript): for
`"Вода, захар"`, the synthetic ids computed at write time
(`synth_water_...`, from "Water") do **not** match what re-tokenizing the
persisted Cyrillic text at read time produces (`synth_...` from "Вода",
different hash). For these two specific words this is currently masked by
`reconstruct_synthetic_ingredient`'s legacy slug-fallback happening to guess
the right English word back — but it is fragile (silently relies on every
alias value being a plain ASCII word) and is a genuine "canonical alias
resolution" defect per task item 3. **Fixed**: added
`_synthesize_unknown_from_original_text` (`food_analysis.py`), which
recovers the original (pre-alias) token for each unmatched entry and builds
the synthetic ingredient from that — the alias is still used for matching/
dedup, never for identity. Both call sites updated.

### 1.2 "Ingredient detected on label" — traced, not fabricated

That exact string is the final fallback in
`ocr_normalizer.reconstruct_synthetic_ingredient` (line ~364), reached only
when (a) re-tokenizing the persisted `raw_ingredient_text` under **today's**
code never reproduces the stored id, **and** (b) the id itself has no
recoverable slug (i.e. it was minted from a name with zero ASCII-alphanumeric
characters — a pure-Cyrillic word with no digits). I confirmed root cause
(c) above does **not** itself trigger this exact placeholder for the current
alias dictionary (every alias value is ASCII, so the slug fallback always
recovers *something* readable). The one mechanism I could reproduce that
**does** hit the literal placeholder is a **tokenization/hashing change
between when an id was minted and when it's re-read** — i.e., exactly the
"existing damaged records" scenario in section 3 below. I did not have
access to the live product database to prove that is what the specific
live-reported instance was (see "Proven vs. hypothesis" note), but it is the
only mechanism in the code that produces this literal string, and this
branch's own fix (changing how Cyrillic-E tokens hash) is a fresh instance of
exactly that mechanism if deployed without a backfill — which is why section
3's audit tool exists.

**Proven vs. hypothesis, explicitly:** (a), (b), (c), and the mechanism
behind the placeholder are all proven by direct code reading + reproduction
in this session (see the regression tests below). That the **specific**
live-reported "Ingredient detected on label" sighting was caused by a
historical hash change (rather than some other, not-yet-found path) is a
hypothesis, not a proven fact — I had no live-database access to confirm it
against that specific row.

## 2. Regression fixture — fails pre-fix, passes post-fix

New file: `tests/integration/test_cyrillic_e_number_identity_e2e.py`, using
the task's exact label text (and a Latin-E respelling of it), through the
real HTTP API, every Gemini call mocked (no live network):

1. `test_barcode_not_found_then_cyrillic_label_image_preserves_identity` —
   `POST /scan/barcode` (providers all miss → 404 `labelScanRequired`), then
   `POST /scan/label-image` with that same barcode, then a **second**
   `POST /scan/barcode` (subsequent lookup) — identity asserted at both
   points.
2. `test_barcode_not_found_then_latin_e_label_image_preserves_identity` —
   same flow, Latin "E" spelling, proving both scripts behave identically.
3. `test_ocr_text_with_barcode_cyrillic_label_preserves_identity` — the
   `POST /scan/ocr-text` + barcode path, then a reload (this endpoint never
   verifies nutrition by design, so the reload's `404 labelScanRequired`
   carries the real, reconstructed `ingredients` in `error.details` — I
   assert identity survives there, matching the existing documented
   behavior rather than fighting it).

Each test asserts: E300/E202/E211/E955/E950 (the catalogue-seeded additives
this label contains) resolve to their **real, non-synthetic** catalog row
with correct name and a non-empty seeded `purposeInFood`; E414 (not seeded)
resolves to a synthetic ingredient that still carries `eNumber: "E414"`; no
ingredient ever becomes the literal "Ingredient detected on label" string or
a bare "300"/"950"; water/sugar remain recognizable by name. No nutrition
data or health score is required or asserted (task requirement: ingredient
display never needs nutrition).

**Demonstrated fail→pass**: I `git stash`ed the four fixed application files
(keeping the new test file) back to this branch's starting `HEAD`, rebuilt
the test image, and re-ran the new file:

```
FAILED test_barcode_not_found_then_cyrillic_label_image_preserves_identity
       — AssertionError: E300 missing from ingredients: []
FAILED test_ocr_text_with_barcode_cyrillic_label_preserves_identity
       — AssertionError: E300 missing from ingredients: []
2 failed, 1 passed in 1.31s
```
(the Latin-E variant passed even pre-fix — expected, since Latin "E" was
never broken; only the Cyrillic cases regress). After `git stash pop`
(restoring the fix), all 3 pass. This is the exact fail-pre-fix/pass-post-fix
demonstration the task asked for.

## 3. Existing damaged records — read-only audit tool

**Does the fix only affect new scans?** No — it changes what id
`ocr_normalizer._synthetic_id` derives from a Cyrillic-E-bearing name (the
fold now runs *before* hashing). Any product whose `ingredient_ids` were
persisted by **older** code, where a Cyrillic E-number token's hash was
computed on the un-folded Cyrillic text, will **no longer round-trip**
through `reconstruct_synthetic_ingredient` once this fix is deployed — it
falls through to the legacy slug/hash fallback and typically recovers only a
bare, identity-less number (e.g. "950") or, when the id has no slug at all,
the generic placeholder. This is not new to this fix (the exact same
category of risk is already documented inside `reconstruct_synthetic_
ingredient`'s own docstring, describing an earlier, differently-shaped
instance of it) — it is an inherent property of content-hash-based synthetic
ids whenever the hashing input changes.

**New file**: `scripts/audit_synthetic_ingredient_identity.py` — read-only
(only `SELECT`s; its one transaction is explicitly rolled back, never
committed, even on success). For every `Product` with non-empty
`ingredient_ids`, for every id that isn't a real catalogue row, it checks
whether re-tokenizing the **persisted** `raw_ingredient_text` with **today's**
code reproduces that exact id (the same check `reconstruct_synthetic_
ingredient`'s own main loop does). If not, it reports the mismatch,
categorized:
- `PLACEHOLDER_LOST_IDENTITY` — degrades to the generic placeholder.
- `BARE_NUMBER_NO_IDENTITY` — recovers only a bare number, no "E" identity.
- `RECOVERED_BUT_ID_UNSTABLE` — recovers a plausible name via the slug
  fallback, but the id itself would differ from what a fresh scan of
  equivalent text mints today (silent future drift/dedup risk).

It **never** infers an E-number from a bare digit string, never rewrites
anything, and never touches a live database unless explicitly pointed at one
via `--database-url` (and even then, read-only). I did not run it against
the live `nutriguard-backend-db-1` container — per the task's explicit scope,
no live repairs or live inspection were performed. I verified it against a
disposable SQLite database I constructed myself, seeded with one
deliberately-constructed legacy-hash row (reproducing the exact pre-fix
Cyrillic-E-950 scenario) and one fresh-and-consistent row: the tool correctly
flagged only the damaged one (`BARE_NUMBER_NO_IDENTITY`, recovered name
`"950"`) and produced zero false positives. This is also covered by 5 unit-
level tests: `tests/integration/test_audit_synthetic_ingredient_identity.py`
(damaged/Cyrillic-legacy case, placeholder-only case, fresh-consistent case,
real-catalog-id case, and a `--limit` bound check).

**If the user wants to know the live scope**: run (read-only, against the
live DB's connection string, from an operator shell with DB access — I did
not do this myself):
```
python -m scripts.audit_synthetic_ingredient_identity --database-url <live-db-url> --json
```
This produces a list of affected `(barcode, stored_id, category)` rows for a
human to triage. No repair logic exists in this task; a follow-up backfill
migration would need its own explicit review.

## 4–5. Catalogue coverage (E300/E202/E211/E414/E955/E950, water, sugar, CO2)

(Independent investigation run in parallel; verified directly, not taken on
faith — I cross-checked the curated CSV rows and id-generation function
myself before writing the regression test's assertions.)

| Identity | Tracked seed data | Isolated-DB lookup | Note |
|---|---|---|---|
| E300 (ascorbic acid) | `app/seed/e_additives_curated_starter.csv:15` | Resolves by E-number → `e300_ascorbic_acid` | curated, has `purposeInFood` |
| E202 (potassium sorbate) | csv:3 | → `e202_potassium_sorbate` | curated |
| E211 (sodium benzoate) | csv:5 | → `e211_sodium_benzoate` | curated |
| E955 (sucralose) | csv:43 | → `e955_sucralose` | curated, has description too |
| E950 (acesulfame K) | csv:39 | → `e950_acesulfame_k` | curated |
| E414 (gum arabic) | **not present** in any seed file | synthetic fallback | missing content, not a matching bug |
| Water / Sugar / Carbon dioxide | **not present** (additive-only catalogue by design) | synthetic fallback | missing content, not a matching bug — expected, no nutrition required to display |

The regression test seeds the 5 real curated rows directly (mirroring
`load_seed.py`'s own field mapping — the standard integration-test
pytest DB fixture does **not** run the production seed loader, matching
this suite's existing convention of inserting minimal curated rows
directly rather than running the full loader in HTTP tests).

## 6. OpenFoodTox readiness — four-tier status (not blurred)

1. **Original archive**: `~/nutriguard-data/openfoodtox/originals/` — 11,613
   raw EFSA IUCLID dossiers, untracked.
2. **Extracted staging**: `~/nutriguard-data/openfoodtox/staging/v4/catalogue.jsonl`
   — mechanical structural extraction, not reviewed/curated.
3. **Review outputs**: `docs/OPENFOODTOX_PILOT_REVIEW_DRAFTS.md` /
   `OPENFOODTOX_CLAIM_SOURCE_MATRIX.md` + `~/nutriguard-data/openfoodtox/pilot/v1..v8`
   draft profiles.
4. **Actually imported/user-facing**: `app/seed/openfoodtox_pilot_profiles.json`
   — exactly **4** owner-approved identities: **E150d, E250 (sodium
   nitrite), E330 (citric acid), E951 (aspartame)**. Its own metadata states
   `scientific_review_status: "not_reviewed"` even for these 4 — "owner
   approved for publication" is not the same claim as "scientifically
   reviewed", and the seed file says so itself.

**For this task's 6 E-codes (E300, E202, E211, E414, E955, E950): none is in
the Tier-4 allowlist, none has been drafted or claim-checked at Tier 3.**
Tier-2 raw EFSA records exist for all six (counts: 7/7/5/3/2/2 respectively)
but are unreviewed and, for at least E300, found to be feed-additive-context
opinions that would need further filtering before any food-safety use — file
presence is not consumer-guidance eligibility, and none of this was
bulk-imported or summarized as if it already were reviewed content.

## 7. Scan-attempt diagnostics (issue #30) — status, not a merge

**Nothing from `feat/backend-scan-attempt-diagnostics-issue-30` is in `main`.**
It is pushed to `origin` but unmerged (confirmed via `git show main:<path>`
for every file the branch touches — none exist on `main`). `gh` could not
authenticate in this environment (401), so GitHub issue/PR state (open/
closed/merged) could not be independently confirmed via the API — only the
in-repo evidence above. The branch's own completion report states it was
"pushed to origin, not merged, not deployed, per the task's explicit scope."

**Scope**: large — 30 files, +4097/−19 lines across two rounds (contract +
CLI, then 7 Codex review fixes: pipeline tracing, a cross-process SQLite
dedup ledger, pseudonymous owner scoping, streaming body-size enforcement,
etc.) — far more than "give Android a numeric id."

**Current contract on that branch**: two request headers on `/scan/*`
endpoints — `X-Scan-Attempt-Id` (16-digit decimal, client-supplied or
server-generated, diagnostics-only, never rejects a request) and
`X-Scan-Request-Sequence`. Not an auth mechanism, not guaranteed globally
unique.

**Smallest safe integration plan** (not implemented here — out of this
task's scope by its own instruction): cherry-pick/reimplement only
`app/core/scan_attempt.py` + the header wiring in `app/api/v1/scan.py`
(closest to round-1 commit `6038024`), skip the ledger/pipeline-tracing/
owner-scoping infrastructure for a first pass. No DB migration required for
just the header-based id (the SQLite dedup ledger is a separable, optional
piece). This branch is file-disjoint from `fix/backend-cyrillic-e-number` —
no merge-conflict risk — but should land as its own reviewed PR.

## Files changed

```
app/services/ocr_normalizer.py        — Cyrillic E-number fold (pre-existing
                                          + extended into create_synthetic_ingredient)
app/services/label_language.py        — _E_NUMBER_RE accepts Cyrillic Е/е
app/services/ingredient_translation.py — _E_NUMBER_RE accepts Cyrillic Е/е
app/services/food_analysis.py         — _synthesize_unknown_from_original_text:
                                          fixes alias-substitution identity drift
tests/unit/test_ocr_normalizer.py              — +3 tests (letter-suffix variants,
                                                   bare-number non-inference)
tests/unit/test_gemini_image_parser.py         — +1 test (structured-ingredient
                                                   Cyrillic eNumber path)
tests/integration/test_cyrillic_e_number_identity_e2e.py   — new, 3 tests (task's
                                                   regression fixture, full HTTP flow)
tests/integration/test_audit_synthetic_ingredient_identity.py — new, 5 tests
scripts/audit_synthetic_ingredient_identity.py — new, read-only audit CLI
docs/CYRILLIC_E_NUMBER_IDENTITY_FIX_REPORT.md  — this report
```

No `app/schemas/`, `app/models/`, or `openapi.json` changes — verified the
regenerated OpenAPI spec is byte-identical to the checked-in snapshot, so no
API-contract change and no README "Deviations" entry is needed.

## Test results (exact)

Dependency note: the host's only available Python is 3.14, too new for
several of `requirements.txt`'s pinned native wheels (`psycopg2-binary`,
`pydantic-core`'s pinned version has no 3.14 wheel and fails to build via
PyO3/maturin). I ran all tests inside a **disposable** Docker image built
from the backend's own `Dockerfile` (`python:3.12-slim`, exactly
`requirements.txt`'s pinned versions) — never the live `nutriguard-backend-*`
compose stack, never `docker compose` on the project's existing containers.

```
docker build -t nutriguard-backend-task-test:tmp -f Dockerfile .
docker run --rm --entrypoint python nutriguard-backend-task-test:tmp -m pytest -q
```
Final result: **916 passed, 20 skipped**, 0 failed (baseline before any of
this session's changes, with only the pre-existing uncommitted
`ocr_normalizer.py` diff applied, was 905 passed/20 skipped — all additions
are net-new passing tests, nothing regressed).

OpenAPI regenerate-and-diff: `app.main.app.openapi()` output is
byte-identical (sorted-JSON comparison) to the checked-in `openapi.json`.

## Remaining limitations

- `label_language.py`/`ingredient_translation.py`'s Cyrillic-E fixes are
  regex-level correctness fixes; I could not build a deterministic HTTP-level
  regression test for the specific "Cyrillic-script, non-Bulgarian label
  gets machine-translated" path without a live/mocked translation model
  exercising that exact branch — they're currently proven correct by
  inspection + the shared regex pattern, not by an end-to-end test.
- The live-reported "Ingredient detected on label" sighting's root cause is
  a proven *mechanism* (tokenization/hash change between write and read),
  not a proven match to that *specific* historical row — I had no live DB
  access to confirm which.
- The audit script finds affected records; it does not repair them. A
  backfill/migration decision is explicitly out of this task's scope and
  would need its own review (how to safely re-derive/merge ids without
  creating duplicate rows for products already scanned again under the
  fixed code).
- `docs/CODEX_HANDOFF.md` was deliberately **not** updated — per
  `nutriguard-backend/CLAUDE.md` §14, Claude Code is not responsible for
  that file unless explicitly asked; this report is the deliverable instead.

## Android handoff

No Android-visible contract change: same endpoints, same response shapes,
same field names. What changes for Android, purely server-side:
- Bulgarian/Russian/Ukrainian/Serbian labels with Cyrillic "Е" E-numbers now
  resolve ingredient identity (name, `eNumber`, and real catalogue narrative
  fields where seeded) instead of degrading to an unidentified token.
- A handful of common Bulgarian commodity words (water/sugar/salt/milk/...)
  now keep their original-language display text when no catalogue/English
  alias match exists, instead of silently showing the English alias text.

Out of this task's scope (per the task's own instruction, for the other
agent track): the visible scan id, app version/build, empty-ingredient-detail
interaction, local missing-ingredient fallback, and the Bulgarian "Health
Score Pending" mistranslation. The numeric scan-attempt-id contract (section
7 above) is documented but not integrated here — Codex needs the branch
status above before any Android-side wiring.
