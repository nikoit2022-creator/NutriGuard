# Backend Efficiency & Maintainability Audit — Issue #24

Owner-authorized, evidence-only backend audit. **AUDIT ONLY**: no
production-code fixes, dependency upgrades, schema changes, API
mutations, merges, or deploys were made. Every finding below is
reported for review; nothing described as "proposed" has been
implemented. This audit is independent of PR #22 and issue #23 and did
not interrupt either.

## 0. Scope, baseline, and environment

- **Base commit**: `32bd7efc05750470da6f48eab7c4111e45db1d26` (`main`,
  merge of PR #20) — confirmed to be `origin/main`'s exact HEAD via
  `git fetch origin main && git rev-parse origin/main` before any work
  started.
- **Branch/worktree**: `audit/backend-optimization-issue-24`, in a
  dedicated `git worktree` at
  `/home/vboxuser/nutriguard-worktrees/audit-backend-optimization-issue-24`,
  separate from the live checkout at `/home/vboxuser/nutrigard` (which
  has its own live, unrelated, uncommitted `docs/CODEX_HANDOFF.md` edit
  in progress — left completely untouched by this audit) and from any
  live Docker Compose stack, container, database, or secret. No live
  container was entered/restarted, no `.env`/secret was read or
  changed, nothing was merged into `main`, nothing was deployed, and no
  paid/live external (Gemini/OpenFoodFacts) call was made.
- **Test/build environment**: this VM's ambient `python3` is 3.14 with
  newer, unpinned versions of every dependency already installed
  (confirmed: `fastapi==0.141.1`, `pytest==9.1.1`, `SQLAlchemy==2.0.52`,
  `pydantic==2.13.5` — vs. `requirements.txt`'s pins of `0.115.6`,
  `8.3.4`, `2.0.36`, `2.10.4`). The suite genuinely runs on that ambient
  interpreter (verified in this session, see §1), but it is **not** the
  pinned environment the project actually ships. For the numbers this
  report treats as authoritative, a disposable, non-committed
  `Dockerfile.audit-test` (repository's own `Dockerfile` base,
  `python:3.12-slim`, unmodified `requirements.txt`) was built and run
  with `docker run --rm --network none` — no network, no volumes beyond
  the worktree bind-mount, no shared state with any live stack. `pip
  freeze` inside the image matches `requirements.txt`'s top-level pins
  exactly; the diff against `requirements.txt` is only expected
  transitive dependencies.

## 1. Baseline verification (actually executed, this session)

```
docker build -f Dockerfile.audit-test -t nutriguard-backend-audit-test:issue24 .
docker run --rm --network none nutriguard-backend-audit-test:issue24 python -m pytest -q
```

- **Before this audit's new test file was added**: `590 passed, 10
  skipped, 2 warnings in 18.00s`.
- **After adding the 2 query-count reproduction tests described in
  §2**: `592 passed, 10 skipped, 2 warnings in 17.11s`. No existing
  test was modified, skipped, or weakened.
- Cross-checked against the ambient (unpinned) `python3 -m pytest -q
  tests/unit tests/integration`: `592 passed, 3 warnings, 13.70s` (no
  `tests/postgres`, hence no skips) — same pass count, confirming the
  new tests are not an artifact of one specific environment.
- No `tests/postgres/` (real-PostgreSQL) tests were run — opt-in via
  `NUTRIGUARD_TEST_POSTGRES_URL`, out of scope (no migration work here).

## 2. Query counts and N+1 patterns

### 2.1 Finding 1 (HIGH impact, MEASURED) — `fetch_ingredients_for_product` issues up to ~3 extra sequential queries per unresolved ingredient, on the common case

`app/services/food_analysis.py:758-782` (`fetch_ingredients_for_product`)
batches its main ingredient fetch correctly
(`ingredient_repository.get_many_by_ids`, one `WHERE id IN (...)` query
for the whole product), but then loops over every resolved ingredient
id and calls `await
ingredient_catalog.resolve_canonical_alias_owner(db, by_id[ingredient_id])`
**sequentially, one at a time** (`food_analysis.py:775-781`).

`resolve_canonical_alias_owner` (`app/services/ingredient_catalog.py:650-662`)
short-circuits with zero extra queries for a curated/verified/strong
row (`_is_provably_weaker_duplicate`, `ingredient_catalog.py:586-607`,
returns `False` immediately for anything not
`UNVERIFIED`+`OCR_HEURISTIC`+no-official-identifier) — but for any row
that **is** that shape, it does an un-batched `get_by_normalized` alias
lookup, and — if the alias was reclaimed by a different canonical row
— an un-batched `get_by_id` fetch of that canonical row.

This is not an edge case: OCR-discovered ingredients are persisted into
the `ingredients` table with exactly this
`UNVERIFIED`/`OCR_HEURISTIC`/no-identifier shape
(`ingredient_catalog.py:559`, `:805`), and the sibling issue #21 audit's
own inventory of a real seeded catalog found **~192 of ~247**
ingredients (about 78%) are OCR-discovered/synthetic rows of exactly
this kind — this is the *typical* ingredient on a real label, not a
rare one.

Compounding this: `Ingredient.localization_rows` is
`lazy="selectin"` (`app/models/ingredient.py:263`), so **every**
`Ingredient` load — including each un-batched `get_by_id` inside the
loop — triggers its own additional `SELECT ... FROM ingredient_localizations`
for just that one row, rather than sharing the one batched selectin
query the initial `get_many_by_ids` call already paid for.

**Measured** (new tests, `tests/integration/test_audit_issue24_query_counts.py`,
via a `sqlalchemy.event.listen(engine, "before_cursor_execute", ...)`
counter around the call, both passing in this session):

| Scenario (n=8 ingredients on one product) | SQL statements | Notes |
|---|---|---|
| All 8 already-verified/curated (control) | **2** | 1 batch `IN` select + 1 batched selectin for localizations, regardless of n |
| All 8 weak OCR-heuristic duplicates whose alias was reclaimed by a curated row | **24** | 12x the control, for the identical ingredient-list *size* |

Reachable from 3 real, hot HTTP paths: `POST /scan/barcode` success
path (`food_analysis.py:899`), `_finalize_barcode_enrichment` (label/OCR
enrichment, `food_analysis.py:1660`), and the plain lookup
`GET /products/{barcode}` (`app/api/v1/products.py:56`).

**Caveat (explicit, not glossed over)**: the 24-statement measurement's
fixture creates each canonical row in the *same* session immediately
before resolving it, so SQLAlchemy's identity map may short-circuit
some of the per-item work that a fresh request-scoped session (reading
rows persisted in an *earlier* request) would not get for free — 24 is
a **plausible under-count**, not an over-count, of the real per-request
cost for this exact shape. The qualitative conclusion (linear per-item
cost that a real, common-case product list pays, vs. a flat cost for
the curated case) is measured and solid; the precise multiplier in
production traffic is not independently confirmed beyond this fixture.

**Expected benefit of fixing (unmeasured, stated honestly)**: replacing
the per-item alias/canonical lookups with one batched alias lookup
(`WHERE alias_normalized IN (...)`) and one batched canonical-id fetch
would plausibly collapse this to a small constant number of statements
regardless of how many weak-duplicate ingredients a product has — but
no wall-clock latency measurement was taken (this audit did not spin up
a real Postgres instance under load), so no percentage speedup is
promised.

**Risk of fixing**: `resolve_canonical_alias_owner`'s per-item call also
reads `ingredient.common_name` per item to normalize it
(`ingredient_catalog.py:657`) — a batched version needs to preserve
exactly which alias/canonical pairing applies to *which* ingredient
id, not just batch the SQL. `_reconcile_official_identifier_conflict`
(`ingredient_catalog.py:665-720`, called elsewhere, not from this loop)
already contains delicate alias-ownership logic that a naive batching
rewrite could accidentally interact with if reused carelessly — a fix
here should stay scoped to `fetch_ingredients_for_product`'s read path
and not touch the write-side conflict resolution.

**Proposed verification** (not performed here): re-run this audit's two
query-count tests after any fix and confirm the "all weak duplicates"
case drops to a small constant (e.g. 2–4) while the control stays at 2;
additionally add a third case at n=16 to confirm the fix is O(1)-ish in
n, not just a smaller constant multiplier.

### 2.2 Finding 2 (MEDIUM impact, PROVEN, narrower) — every scan response is fully serialized twice

`app/api/v1/scan.py:53-73` (`_finish_serializing`) deliberately calls
`out.model_dump(mode="json", by_alias=True)` on the constructed
`FullProductAnalysisOut` inside the endpoint's own try block, then
FastAPI's `response_model` machinery serializes the **same object a
second time** when actually building the HTTP response — this is
explicitly documented in the function's own docstring as intentional
("Forces the SAME serialization FastAPI's response_model machinery
performs later... so a computed-field/encoding failure surfaces INSIDE
this function's try block... never a guessed/deferred success").

`IngredientOut` (`app/schemas/ingredient.py`) has **11**
`@computed_field`s (confirmed by count, not the 9 estimated during
in-flight review), including `localizations`
(`ingredient_localization.build_localizations`) — so for a product with
N ingredients, all 11 computed fields run **2×N** times per successful
scan response (`scan_barcode` at `scan.py:270`, `scan_ocr_text` at
`:349`, `scan_label_image` at `:480`), not N times.

This is a genuine, deliberate trade-off (correctness-of-diagnostics vs.
CPU), not a bug — reported here because the issue asked specifically
about payload/serialization cost, not because it's wrong. **Unmeasured**:
no timing was taken of `model_dump(mode="json")` on a realistic
20–30-ingredient response; the actual CPU cost of the duplicate pass is
not quantified in this report.

**Proposed verification if ever revisited**: time
`FullProductAnalysisOut.model_dump(mode="json", by_alias=True)` for a
synthetic ~25-ingredient product, then evaluate whether the diagnostic
dump's *result* could be reused as the actual response body (e.g. via a
custom `Response` returning the already-serialized JSON) instead of
re-deriving it — that would preserve the "catch computed-field failures
before claiming success" property this code explicitly wants, without
paying for it twice. Not proposed as a ready fix here; a change to a
router's response-construction path touches the API contract's
transport mechanics and would need its own review.

## 3. Ingredient-processing duplication and complexity

### 3.1 Finding 3 (MEDIUM impact, PROVEN, byte-identical duplication) — translation-invariant primitives copy-pasted between two modules

`app/services/ingredient_translation.py` and
`app/services/label_language.py` each independently define:

- `_MIN_TRANSLATION_CONFIDENCE = 0.55` (`ingredient_translation.py:36`,
  `label_language.py:53`)
- `_E_NUMBER_RE = re.compile(r"\bE[- ]?(\d{3,4}[A-Za-z]?)\b", re.IGNORECASE)`
  (`ingredient_translation.py:68`, `label_language.py:158`) — character-for-character identical
- `_NUMBER_WITH_UNIT_RE` (`ingredient_translation.py:69-72`,
  `label_language.py:170-173`) — character-for-character identical
- `_normalize_number` (`ingredient_translation.py:103-107`,
  `label_language.py:180-189`) — identical logic (comma→dot, trailing-zero strip)
- `_extract_e_numbers` (`ingredient_translation.py:110-111`,
  `label_language.py:176-177`) — identical one-liner
- `_extract_numeric_tokens` (`ingredient_translation.py:114-120`,
  `label_language.py:192-206`) — identical loop body

Verified directly (both files read side-by-side in this session) — the
regexes and three whole functions are byte-identical, not merely
similar. `ingredient_translation.py`'s own module docstring
(lines 130-141) even says its `_translation_is_reliable` "mirrors
`label_language._verify_translation_invariants`" — the duplication was
noticed by whoever wrote the comment and left unresolved anyway.

Both are reachable from every scan endpoint: `translate_ingredient_tokens`
← `ingredient_catalog._resolve_ingredient_languages`
(`ingredient_catalog.py:1174`) ← `materialize_ingredients`
(`ingredient_catalog.py:1270`) ← 5 call sites in
`food_analysis.py` ← `app/api/v1/scan.py`'s barcode/OCR/label-image
endpoints; `label_language.resolve_label_text` ← `food_analysis.py:1544,1919`,
also from the same endpoints.

**Safe to unify**: the primitives only (regexes, `_normalize_number`,
`_extract_e_numbers`, `_extract_numeric_tokens`, the confidence
constant) — pure, stateless, and identical today; a shared module
(e.g. `app/services/translation_invariants.py`) could hold them with no
behavior change.

**Do NOT unify** the higher-level decision functions built on top:
`ingredient_translation._translation_is_reliable` additionally falls
back to a catalog-alias check for short translated ingredient *names*
(`ingredient_translation.py:146-149`) that
`label_language._verify_translation_invariants` has no equivalent for
(it instead produces a human-readable mismatch reason via
`_describe_counter_mismatch`, `label_language.py:209-223`, absent from
`ingredient_translation.py`). These operate on genuinely different
inputs (one short name vs. one whole label-text blob) and have
diverged for real reasons — collapsing them would either regress
short-name translation accuracy or add unused complexity to the
label-blob path.

**Expected benefit**: maintainability only (one place to fix a
numeric-invariant bug instead of two) — this is not a performance
finding, and no runtime benefit is claimed.

### 3.2 Finding 4 (LOW impact, PROVEN, low priority) — same swallow-and-log alias-registration pattern duplicated, not the code itself

`ingredient_catalog._register_original_text_alias`
(`ingredient_catalog.py:1219-1251`) and
`repair_ingredient_language._register_alias`
(`app/seed/repair_ingredient_language.py:289-319`) share the same
try/except-and-log *shape* around
`ingredient_alias_repository.get_or_create`, but are not byte-identical
(different parameters, different guard conditions, different log
event names). `repair_ingredient_language.py` is a manually-invoked CLI
tool (confirmed: no caller anywhere outside itself/tests), not a
per-request path — unifying this would touch a rarely-run script for a
purely cosmetic benefit. **Not worth prioritizing.**

### 3.3 Complexity hotspots — hot vs. not hot (important distinction the issue asked for)

**Hot (genuinely per-request, worth attention)**:
- `ingredient_catalog.get_or_create_catalog_ingredient`
  (`ingredient_catalog.py:827-945`) — ~8-10 branches, up to 4 sequential
  `await` DB round-trips in the race-losing/new-insert path, called once
  per recognized ingredient token via `materialize_ingredients`, which
  itself runs up to twice per scan request (a first matching pass, then
  a Bulgarian-alias-aware rebuild pass — `IngredientTranslationSummary.merged_with`'s
  own docstring, `ingredient_catalog.py:1049-1060`).
- `ingredient_catalog._resolve_ingredient_languages`
  (`ingredient_catalog.py:1071-1216`) — sequential per-item `await`s
  (alias lookup, conditional e-number lookup) before the one batched
  translation call, i.e. its own smaller N+1-shaped cost independent of
  §2.1's finding, on the same hot path.

**Not hot / effectively dead in production today (do not prioritize for
efficiency)**:
- `ingredient_catalog.merge_verified_fields` (`ingredient_catalog.py:321-500`)
  is the single most heavily-branched function in the file (source-rank
  gating, confidence tie-break, stale-trusted-revalidation bypass,
  per-field provenance backfill, verification-status promotion), but a
  repo-wide grep found **zero callers anywhere in `app/`** outside its
  own definition and comments — it is exercised exclusively by tests.
  Its own module docstring (`ingredient_catalog.py:42-48`) confirms this
  is intentional: a "ready seam for a FUTURE external ingredient-lookup
  integration" that doesn't exist yet. Reducing its complexity would not
  speed up any current request.
- `app/seed/repair_ingredient_language.py` (`run_repair`,
  lines 387-479) and `app/seed/load_seed.py` — one-off, manually-invoked
  CLI/startup tools (confirmed via grep: no router/service imports
  either), irrelevant to per-request scan latency regardless of their
  own internal complexity.

### 3.4 Frozen-migration safety constraint (issue-specified check)

`grep -rln "from app.services\|from app.seed\|import app.services\|import app.seed" alembic/versions/`
returned **no matches** — no Alembic migration imports from
`app/services/*` or `app/seed/*` today. The anti-pattern the issue
warned about (frozen historical migration logic deduplicated into a
mutable runtime import) does not currently exist in this codebase; no
finding to report and no such change is proposed anywhere in this
report. (`app/seed/load_seed.py` and `repair_ingredient_language.py` do
import from `app/services/*`, but those are idempotent, currently-run
seed/repair scripts, not frozen migrations — correct, intentional
layering, not the anti-pattern.)

## 4. Catalog/cache reuse and repeated external calls/translations

### 4.1 What already works (verified, not re-litigated as a problem)

- **No retry/backoff near Gemini** that could silently multiply a real
  paid call: `grep -rn tenacity app/` shows `tenacity` used only in the
  non-Gemini barcode-provider HTTP client
  (`app/integrations/barcode_providers/_http.py:16,89`).
  `GeminiService._call` (`app/integrations/gemini.py:199-231`) makes
  exactly one `httpx` POST per invocation.
- **Per-ingredient translation is durably cached across requests**: a
  once-translated foreign ingredient name is persisted as a
  language-tagged `IngredientAlias` row
  (`ingredient_catalog._register_original_text_alias`,
  `ingredient_catalog.py:1219-1251`), so the same foreign name is never
  re-sent to Gemini once resolved — confirmed by the existing, passing
  `tests/unit/test_ingredient_translation.py` /
  `tests/integration/test_ingredient_catalog.py` /
  `tests/integration/test_ingredient_language_identity_e2e.py`. This is
  a real, working, Postgres-backed cache — just not Redis.
- **No row lock held across a Gemini network call**:
  `_finalize_barcode_enrichment` only takes its `FOR UPDATE` fetch
  (`food_analysis.py:1522-1524`) *after* the Gemini call already
  completed in the caller — confirmed by call-order tracing.

### 4.2 Finding 5 (MEDIUM impact, PROVEN via a passing test) — an already-fully-verified barcode still pays a fresh Gemini call on a repeat label/OCR scan

`analyze_label_image_with_barcode`/`analyze_ocr_text_with_barcode`
(`food_analysis.py:1711-1739`, `:1766-1799`) call Gemini
(`gemini_service.analyze_image`/`analyze_text`) **before** loading the
existing `Product` row inside `_finalize_barcode_enrichment`
(`:1522-1524`) — there is no "this barcode is already
`is_verified=True`/`has_verified_nutrition`/`has_verified_ingredients`,
skip the Gemini call" short-circuit anywhere on this path.

Confirmed as real, current behavior — not a guess — via the existing,
already-passing test
`tests/integration/test_label_barcode_enrichment.py::test_verified_product_is_refreshed_by_complete_user_label_scan`
(line 292): it seeds a fully-verified row, mocks Gemini to return
*different* nutrition data, resubmits the same barcode with a label
image, and asserts the response reflects the **new** Gemini-derived
value — i.e. Gemini genuinely runs, and its output overwrites the
existing verified data, even though nothing was missing to justify it.

This may be an intentional "always allow a fresher photo to correct
stale/wrong data" product decision (not documented as such in
`README.md`'s deviations section, but plausible) — this report does
**not** recommend silently adding a short-circuit, since that would be
exactly the kind of undocumented contract change §8 of
`CLAUDE.md` prohibits without a product-owner decision. Flagged here
because it is the single largest **real external cost** (a paid/metered
Gemini Vision or text call) with zero caching found anywhere in this
audit — every other finding in this report is a local CPU/DB cost.

**Unmeasured**: actual Gemini call volume/cost in production, and what
fraction of repeat scans hit an already-fully-verified barcode. A
mock-based call-count test against a verified fixture (counting
`gemini_service.analyze_image` invocations across repeat submissions of
the same barcode) would quantify how often this fires, without needing
production log/cost data (which this audit was not authorized to read
in any case).

### 4.3 Finding 6 (LOW-MEDIUM impact, PROVEN, narrower gap) — whole-label-text translation has no cache at all

`label_language.resolve_label_text` (`label_language.py:313-418`) calls
`gemini_service.translate_label_text` fresh on every invocation, with
no lookup against any prior translation of the same raw label text (no
DB table, no Redis — confirmed, see §4.4). Unlike per-ingredient-name
translation (§4.1), there is no persistence layer here at all. Same
caveat as Finding 5: this is a real, unmeasured gap, not a proven
production cost, since typical label text for the *same* product is
unlikely to repeat verbatim across different users' photos (OCR
variance) — reported for completeness per the issue's explicit ask
about "repeated external calls/translations," not prioritized as
urgent.

### 4.4 Redis: present but never used for caching

`grep -rn -i redis app/` returns exactly 3 lines, all rate-limiting
configuration: `app/core/config.py:25-27` (`REDIS_URL`, `REDIS_ENABLED`)
and `app/core/rate_limit.py:24` (feeds `slowapi.Limiter`'s
`storage_uri`). There is no `import redis`/`aioredis` anywhere else in
`app/`, and no `functools.lru_cache` or manual in-process memoization
anywhere in `ingredient_catalog.py`/`ingredient_repository.py`/
`ingredient_alias_repository.py`. Redis infrastructure is present
(already a configured dependency) but used for exactly one narrow
purpose today.

**Unmeasured hypothesis (explicitly not promised as a win)**: a small
in-process or Redis-backed cache keyed on normalized ingredient
name/e-number could plausibly reduce §2.1's and §3.3's per-item DB
round-trips for very common additives (e.g. citric acid, potassium
sorbate) that recur across thousands of different products — but fixing
§2.1's batching first would already remove most of the redundant
per-request cost for the specific pattern measured in this audit, so a
cache layer's *incremental* benefit on top of that fix is unclear
without first measuring query counts post-fix. **Proposed verification
if ever pursued**: re-run this audit's query-count tests (§2.1) before
and after a batching fix, then decide whether a cache is still worth
its invalidation complexity — do not add caching before the simpler
batching fix is measured.

## 5. Transaction boundaries

### 5.1 Already correct and already documented (verified, not re-litigated)

`_finalize_barcode_enrichment` (`food_analysis.py:1477-1708`) and
`_finalize_standalone_label_analysis` both commit **exactly once** per
request path (failure-path commit XOR success-path commit, never
both) — this is explicitly documented in the function's own docstring
(`food_analysis.py:1499-1518`, "Transaction boundaries (review finding
3)") and directly tested by
`tests/integration/test_label_barcode_enrichment.py`'s
`test_*_failure_rolls_back_*` tests, confirmed still passing in this
session's baseline run (§1).

### 5.2 Finding 7 (LOW-MEDIUM impact, PROVEN, already-justified inconsistency) — barcode discovery is 3 separate commits, not atomic end-to-end

Inside one `POST /scan/barcode` call for a genuinely new barcode:

1. `_persist_discovered_product` (`food_analysis.py:634-755`):
   `db.flush()` at `:717` (concurrent-race/update branch only), then
   `db.commit()` at `:723` — its own comment explains this is a
   deliberate "checkpoint the product row on its own before touching
   provenance rows" to avoid a later, unrelated provenance-write race
   unwinding the just-inserted product.
2. Same function: a second `db.commit()` at `:738`, after writing
   `product_source` provenance rows in a loop.
3. Back in `analyze_barcode` (`:839-926`): `db.flush()` at `:905`
   (health score) then final `db.commit()` at `:918` (after inserting
   `ScanHistory`).

This is a deliberate, commented trade-off, not an oversight — but it
means a fresh-barcode discovery is **not** atomic the way the
label/OCR enrichment path (§5.1) is: if the final commit at `:918`
fails, the product + provenance rows from steps 1–2 remain committed
with `health_score` still at its unverified placeholder and no
scan-history row. **Unmeasured**: whether this is ever actually
observed in production, or whether the placeholder-`health_score`
semantics (already the documented, correct behavior for an unverified
row — see the sibling issue #21 audit's Finding 2, and note PR #22
currently pending on `main` proposes making this field nullable
instead of a `0` placeholder, see §7) make this gap harmless in
practice. **Proposed verification**: a fault-injection test that fails
the `ScanHistory` insert between the second and third commit
(analogous to the existing rollback tests for the enrichment path) to
observe the actual persisted state.

### 5.3 Finding 8 (LOW impact, PROVEN pattern, unmeasured severity) — a DB read/session is open across the ~60s Gemini call

In every Gemini-touching flow, a DB read on the request's `AsyncSession`
happens immediately before the Gemini call — e.g.
`ingredient_repository.get_all(db)` at `food_analysis.py:1736`
immediately followed by the Gemini image-analysis call at `:1737-1739`.
SQLAlchemy's async session auto-begins a transaction on first use and
holds a checked-out pool connection until commit/rollback;
`GEMINI_TIMEOUT_SECONDS` defaults to 60s
(`app/core/config.py:42`), and the async engine uses SQLAlchemy's
default pool sizing (no explicit `pool_size`/`max_overflow` override in
`app/database/session.py`). A burst of concurrent label/OCR scans, each
waiting up to 60s on Gemini, could each hold a pool connection for that
whole window despite having done only a read so far. No row lock is
held (plain `SELECT`, not `FOR UPDATE`), so this is a pool-exhaustion/
latency risk under concurrent load, not a correctness/deadlock risk.
**Unmeasured**: no concurrency/load test was run to confirm this
actually exhausts the pool in practice; reported as a plausible,
code-level pattern only.

## 6. Test-suite redundancy and runtime

- **592 tests, 17.11s** (pinned Docker environment, §1) / **13.70–13.85s**
  on the ambient interpreter — fast by any reasonable standard for a
  592-test suite with `httpx` + in-memory SQLite integration coverage.
  Slowest individual test measured at **0.50s**
  (`test_scan_diagnostic_is_safe_across_concurrent_processes`); nothing
  else exceeds 0.42s (`pytest --durations=25`, full output captured in
  this session).
- **No meaningful name-level redundancy found**: a repo-wide scan for
  duplicate test function names across all 51 test files in
  `tests/unit`/`tests/integration` found only 3 identical names, each
  appearing in exactly 2 files — consistent with intentional
  unit-vs-integration-layer re-coverage of the same behavior (a
  legitimate pattern this codebase already uses elsewhere, e.g. the
  sibling issue #21 audit's own two independent test files both
  covering the health-score-zero-collision finding), not accidental
  duplication.
- **Conclusion: nothing here is worth restructuring.** The suite is
  already fast and does not show the kind of copy-pasted, redundant
  test bloat that would justify a consolidation effort. This section is
  reported explicitly as "not worth doing" per the issue's own request
  to identify low-value optimizations, not just problems.

## 7. Pending PR #22 / issue #23 — noted separately, not treated as deployed behavior

Per the issue's explicit instruction, PR #22 (open, unmerged,
`+7580/-185` across 30+ files, `baseRefName: main`) and issue #23 (a
separate, unrelated content-authoring workstream) were identified but
**not** treated as current `main` behavior anywhere in this report, and
this audit did not modify, rebase onto, or interrupt either.

Relevant overlap found (informational only): PR #22's diff
(`gh pr diff 22`, read-only) touches
`app/services/ingredient_catalog.py`, `food_analysis.py`,
`warning_engine.py`, and adds new files
(`app/services/dietary_suitability.py`, `translation_rejection.py`) —
none of which change the specific query shapes measured in §2.1 or
§3.3 of this report (its `ingredient_catalog.py` changes are additive
translation-rejection-reason counters on `IngredientTranslationSummary`,
not a change to `resolve_canonical_alias_owner`'s or
`_resolve_ingredient_languages`'s query pattern). Separately, PR #22's
`food_analysis.py` diff changes `_to_product_model`'s
`health_score=0` placeholder to `health_score=None`
(migration `d7e8f9a0b1c2`) — this directly addresses the sibling issue
#21 audit's Finding 2 (`ProductOut.health_score` overloading "unknown"
and "zero"), but **only once PR #22 merges**; on current `main` (this
audit's actual base), that placeholder is still the literal `0` value
§5.2 references. This is noted for context, not re-analyzed as a new
finding of this audit.

## 8. Evidence limitations (explicit)

- No live Postgres, Redis, or Docker Compose stack was used — all
  measurements are against the in-memory SQLite test database via the
  project's own `db_session`/`db_engine` fixtures
  (`tests/conftest.py`), consistent with how this project's own test
  suite already runs. Absolute query latency (not count) was not
  measured, since SQLite-in-memory latency is not representative of
  production Postgres latency; only **query counts** are claimed as
  measured, never wall-clock speedup percentages.
- No production Gemini call volume, cost, or log data was read (not
  authorized, and would require live/paid access this audit explicitly
  may not use) — §4.2/§4.3's real-world cost impact is stated as
  unmeasured for that reason, not because it couldn't in principle be
  measured with the right access.
- §5.2's discovery-flow non-atomicity gap was traced by reading code
  and existing tests, not reproduced by a new fault-injection test in
  this pass — flagged as a proposed follow-up, not a confirmed
  production incident.
- §2.1's 24-vs-2 measurement's exact multiplier is fixture-dependent
  (see the caveat in that section) — the qualitative direction (linear
  per-weak-ingredient cost vs. flat cost for curated ingredients) is
  solid; the precise number in a real request is not independently
  reproduced against a live database.

## 9. Prioritized findings (3–5 actionable, plus explicitly-skip items)

**Actionable, in priority order:**

1. **§2.1 — Batch `resolve_canonical_alias_owner`'s per-item alias/
   canonical lookups inside `fetch_ingredients_for_product`.** Highest
   priority: measured 12x query-count increase (2 → 24 for n=8) on the
   *common* case (OCR-heuristic ingredients are ~78% of a real catalog
   per the #21 audit), reachable from 3 hot endpoints. Proposed
   verification already specified in §2.1.
2. **§4.2 — Decide, explicitly, whether an already-fully-verified
   barcode should short-circuit a repeat label/OCR Gemini call.** This
   is the only finding touching a real external/paid cost rather than
   local CPU/DB — but it may be intentional ("always allow a fresher
   photo"), so this is flagged as a **product-owner decision needed**,
   not a ready fix. If the owner confirms it's unintentional, a
   `is_verified`/`has_verified_nutrition`/`has_verified_ingredients`
   check before the Gemini call would need its own regression test per
   `CLAUDE.md` §8's contract-deviation rule.
3. **§3.1 — Extract the byte-identical translation-invariant
   primitives** (regexes, `_normalize_number`, `_extract_e_numbers`,
   `_extract_numeric_tokens`, `_MIN_TRANSLATION_CONFIDENCE`) from
   `ingredient_translation.py`/`label_language.py` into one shared
   module. Pure maintainability win (one place to fix a numeric-
   invariant bug), safe because the primitives are provably identical
   and stateless — explicitly do NOT merge the two higher-level
   `_translation_is_reliable`/`_verify_translation_invariants`
   functions built on top of them (§3.1 explains why).
4. **§2.2 — Revisit `_finish_serializing`'s double full-response
   serialization**, but only as a "measure first" item: time
   `model_dump(mode="json")` on a realistic ~25-ingredient response
   before deciding whether reusing that dump as the actual response
   body is worth touching the response-construction path (a change
   with API-transport implications, not a pure internal refactor).
5. **§5.2 — Add a fault-injection test for the barcode-discovery
   flow's 3-commit sequence** to determine whether the already-
   documented non-atomicity is actually observable, before deciding if
   it needs a fix at all.

**Explicitly not worth doing (per the issue's own request to flag
these, not just problems):**

- Reducing `merge_verified_fields`'s complexity (§3.3) — proven
  unreachable from any current HTTP endpoint; zero request-time
  benefit today.
- Restructuring `repair_ingredient_language.py`/`load_seed.py` for
  complexity (§3.3) — one-off CLI/startup tools, not per-request.
- Adding a Redis/in-process cache for ingredient catalog lookups (§4.4)
  — before measuring whether §2.1's simpler batching fix already
  removes most of the redundant cost this would target; caching adds
  invalidation complexity that isn't justified without that
  measurement.
- Restructuring the test suite for redundancy (§6) — genuinely fast
  already (592 tests / ~14-17s), no meaningful duplication found.
- Unifying `_register_original_text_alias`/`_register_alias`'s shared
  pattern (§3.2) — real but low-value; one side is a rarely-run CLI
  tool.

## 10. New files added by this audit (all new, nothing modified)

Per the issue's explicit "documentation-only commit/push... no
implementation/test edits pushed" instruction, only this report is
committed and pushed to `audit/backend-optimization-issue-24`. Two
supporting files exist locally in this audit's worktree (created and
used during the session to produce §1's and §2.1's numbers) but are
**not** committed or pushed, and can be reconstructed by the owner from
this report if reproduction is wanted:

- `tests/integration/test_audit_issue24_query_counts.py` — the 2
  query-count reproduction tests behind §2.1's measured numbers (local
  only, not part of this branch's history).
- `Dockerfile.audit-test` — the disposable pinned-dependency test image
  definition used for §1's baseline (local only; would have had no
  effect on any live stack or `docker-compose.yml` even if committed).
- `nutriguard-backend/docs/BACKEND_OPTIMIZATION_AUDIT.md` (this file)
  — the only file actually committed/pushed by this audit.

## 11. Process note (primary-agent disclosure)

This audit was run as one primary agent plus two bounded, independent,
non-overlapping subagent passes (ingredient-processing
duplication/complexity/catalog-reuse; external calls/caching/
transactions/serialization), all pointed at this same dedicated
worktree/branch, explicitly instructed not to run any `git` command and
not to touch existing files. Both reported findings back to the primary
agent as instructed, without touching git. The primary agent
independently re-read the source at every file:line citation used in
this report from both subagents before including it (spot-checked, not
taken on faith): confirmed the byte-identical regex/function
duplication in §3.1 by direct side-by-side read, confirmed
`merge_verified_fields` has zero callers via an independent grep,
confirmed the double-serialization docstring and the 11-computed-field
count in §2.2 by direct read, confirmed the 3-commit discovery-flow
sequence in §5.2 independently (before either subagent's report
arrived, by direct code reading, which cross-confirmed one subagent's
independent finding of the same sequence), and independently ran and
verified the ambient-Python test-count claim in §1 by re-running it
itself. The §2.1 query-count measurement was produced directly by the
primary agent (not a subagent), via a new instrumented test, run in
both the pinned Docker environment and cross-checked conceptually
against the ambient interpreter. No inaccuracy was found in either
subagent's report during this verification pass; nothing was dropped.
