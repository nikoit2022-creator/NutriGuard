# Ingredient identity and candidate queue (issue #23, stage 2)

Owner authorization: issue #23 comment 5845208498, stage 2. Backend only.
Not merged, not deployed; no live database was touched in this stage.

| | |
|---|---|
| Baseline (exact `origin/main`) | `32bd7efc05750470da6f48eab7c4111e45db1d26` |
| Branch | `feat/backend-ingredient-candidate-queue-issue-23` (from that SHA) |
| Migration | `d7e8f9a0b1c2`, parent `c6d7e8f9a0b1`, single head |
| Head SHA | the commit that adds this file (a file cannot contain its own SHA; it is posted with the push) |
| API / OpenAPI | no change (`app.openapi()` identical to `openapi.json`) |
| Depends on | nothing unmerged. Stage 1 (`audit/backend-catalog-baseline-issue-23`) is documentation and read-only scripts only |

## 1. What this stage fixes and adds

Stage 1 (`docs/CATALOG_BASELINE_AUDIT.md`, on its own branch) found 242
OCR-derived catalog rows with no observation metadata, error text stored as
ingredients, and no way to tell an unknown but valid name from junk. This
stage adds the queue and fixes two identity defects that the work
uncovered, both reproduced with synthetic fixtures before any change.

| # | Finding | Evidence | Change |
|---|---|---|---|
| 1 | A failed label-image scan stored its own placeholder sentence as two catalog rows (16 products reference them in the dev DB) | Baseline `32bd7ef`: provider mocked to raise, `404`, catalog then holds "Ingredients could not be extracted from the image" and "AI response was unavailable or invalid" | The fallback no longer produces text or ingredients from that sentence; placeholder text is also recognized and never materialized |
| 2 | **Fragment matching merged different ingredients.** `match_against_database` treated a token that is a substring of any catalog name, including uncurated OCR-derived rows, as that row | Baseline: after one scan of "Carbonated Water, Sugar-free sweetener, Salted butter", a scan of "Water, Sugar, Salt, Butter" returned those same three rows (Water→Carbonated Water, Sugar→Sugar-free sweetener, Salt and Butter→Salted butter) | Matching is by identity evidence only (section 3) |
| 3 | No record of how often or when an unknown token was seen | Stage 1: 0 candidate metadata | `ingredient_candidates` (section 2) |
| 4 | Junk tokens (digits only) became identities | Stage 1 quality flags | Junk is counted, not stored as an identity, not returned |

## 2. Design: observation, identity and evidence stay apart

```
ingredient_candidates  --(nullable pointer)-->  ingredients / ingredient_aliases   (identity, reused as is)
  observation only                                  scientific evidence and verification live here
```

- **Observation** (`ingredient_candidates`): `normalized_key` (unique, exact
  normalized text, <= 128 chars), `display_name` (<= 128), `e_number` (an
  identifier actually present in the token), `ingredient_id` (the identity it
  resolved to; NULL for junk; `ON DELETE SET NULL`), `status`
  (`PENDING | FLAGGED | JUNK`, CHECK-enforced), `flags` (closed vocabulary),
  `encounter_count` (>= 1, CHECK-enforced), `first_seen_at`, `last_seen_at`.
- **Identity**: `ingredients` and `ingredient_aliases`, unchanged. The queue
  is not a parallel catalog: no alias, no scientific field, no verification
  state, no second identity.
- **Evidence**: untouched. Seeing a token again raises `encounter_count`
  only; a test repeats 25 scans and asserts every `ingredients` column is
  identical afterwards.
- **No stored image or label text**: only the one token, capped at 128
  characters.

**Which tokens are queued.** Every token that resolved to an identity that
is not curated/regulatory/`VERIFIED`, and every junk token. A token that
resolves to a curated row is a known ingredient and is not queued.

**Once per request.** `materialize_ingredients` runs twice per scan; a
token is counted once per request through a per-session set (sessions are per
request). A failed write is rolled back with its SAVEPOINT and the token may
be counted on a retry.

**Best effort.** The whole queue write runs in a SAVEPOINT; an exception is
logged and swallowed, so it can never fail a scan or undo catalog work
(tested by forcing the repository to raise).

**Closed flag vocabulary** (`app/services/ingredient_candidate_flags.py`;
small explicit word lists, no similarity, no model):

| Flag | Meaning | Effect on the scan |
|---|---|---|
| `PLACEHOLDER_TEXT` | the system's own fallback/error sentence | junk: not stored, not returned |
| `NO_LETTERS` | digits/punctuation only | junk: not stored, not returned |
| `HEADER_ARTIFACT` | section header fused in ("Съставки: вода") | flagged only |
| `CLASS_PREFIXED` | functional class fused to a name ("Colorant: e150d") | flagged only |
| `ALLERGEN_STATEMENT` | "May contain ..." advisory | flagged only |
| `GENERIC_FUNCTION_TERM` | a class word alone ("Colour", "Ароматизант") | flagged only |
| `UNBALANCED_PARENTHESIS` | tokenization artifact | flagged only |
| `IDENTITY_UNCERTAIN` | the catalog row is already `identity_uncertain` | flagged only |
| `CONFLICTING_IDENTIFIER` | the token's E-number differs from that of the identity its NAME resolved to | flagged only; the row's identifier is never overwritten |
| `GENERIC_VS_SPECIFIC` | the resolved identity names a source (soy, palm, ...) the token does not, or a different one | flagged only |
| `TOO_LONG` | name cut to 128 | flagged only |

Only the first two are junk. Everything else still resolves exactly as
before and is only flagged, because dropping a real-looking ingredient card
is a product decision (see section 7). `status` is `JUNK` if any junk flag,
else `FLAGGED` if any flag, else `PENDING`; flags only accumulate.

**Ambiguous aliases.** `ingredient_aliases.alias_normalized` is already
unique, so two identities cannot share an alias (stage 1: 0 ambiguous). The
ambiguous case that CAN happen is a contested alias, where an official
identifier picks one row and the name alias points to another; that is the
`CONFLICTING_IDENTIFIER` flag.

## 3. Identity rules (what may merge, what may not)

Identity is decided, in this order and nowhere else, by:

1. **Exact official identifier** (E-number).
2. **Exact normalized name** of a catalog row (`common_name` or
   `scientific_name`), against any row.
3. **A curated/regulatory row's full name appearing as whole words** in a
   more specific token ("Cane Sugar" contains "Sugar").
4. Downstream, in `ingredient_catalog`: an exact **alias** (curated aliases,
   including reviewed BG ones), then a new minimal `UNVERIFIED` row.

Never a basis for merging: a token that is only a **fragment** of a name
("Water" vs "Carbonated Water", "Lecithin" vs "Soy Lecithin"), a near
spelling, a stem, or a translation. The queue keys by exact text, so
"Xylofrobinate", "Xylofrobinates" and "Ксилофробинат" are three candidates
and three identities, each pointing at its own row. An uncurated row is only
ever matched exactly and never absorbs a more specific token ("Refined palm
oil" does not become "Palm oil"). Tests: `tests/unit/test_ocr_normalizer_identity_matching.py`
(9 tests, 24 cases with parameters; Latin and Cyrillic, curated and uncurated) and three HTTP
regressions including the reproduction above.

**Generic vs specific.** A generic token no longer resolves to a specific
curated row by name ("Lecithin" gets its own unverified identity); only the
official identifier links them ("Lecithin (E322)" resolves to
`e322_soy_lecithin`, as before). When an identifier does link a token to a
row naming a specific source, the observation carries
`GENERIC_VS_SPECIFIC` for review; the resolution itself is not changed here
(stage 3 content must not let generic E-number text overwrite the
source-specific row).

**Unchanged and worth knowing.** `ingredient_catalog` still absorbs a
provably weaker duplicate (an unverified, OCR-sourced row with no identifier)
when an official identifier claims its alias, and deletes it only if no
product references it. That is existing, tested behavior of the merged catalog
and is not touched here; this stage adds no automatic deletion or merging.

## 4. Existing product references survive

- The migration adds one table; no existing table, column, alias or product
  reference is touched, and nothing is backfilled at migration time.
- A PostgreSQL round-trip (section 8) with a curated row, an OCR-derived row,
  its alias and a product referencing both showed identical rows and
  references after upgrade, downgrade and re-upgrade.
- A failed or empty-text label scan no longer overwrites an existing
  **unverified** product's ingredient text, ids and flags (test: a product
  with `Water, Sugar` and two ids keeps both after a provider-down barcode
  scan). Before, such an attempt replaced them with the fallback's placeholder
  result.
- A verified product is untouched, as before.

## 5. Bounds and retention

| Bound | Value | Enforced by |
|---|---|---|
| name and key length | 128 | code + column type; long names keep a prefix + SHA-256 suffix so two long names never collide |
| flags | closed vocabulary, <= 255 chars | code + column |
| rows | `INGREDIENT_CANDIDATE_MAX_ROWS`, default 5000 | at the cap a NEW candidate is refused (warning logged), existing ones keep counting, **nothing is evicted** |
| retention | `INGREDIENT_CANDIDATE_RETENTION_DAYS` 180, `..._PRUNE_MAX_ENCOUNTERS` 2 | manual `prune` only |

`prune` considers only rows unseen for the retention period, seen at most 2
times, and never `FLAGGED` (those await review). It removes queue rows only:
never an `ingredients` row, an alias, or a product reference.

Manual commands, **dry run by default**, never scheduled or run at startup:

```
python -m app.seed.ingredient_candidate_maintenance prune              # report only
python -m app.seed.ingredient_candidate_maintenance prune --apply
python -m app.seed.ingredient_candidate_maintenance backfill           # report only
python -m app.seed.ingredient_candidate_maintenance backfill --apply
```

`backfill` seeds rows for existing uncurated identities so the queue is
usable on data scanned before it existed. `encounter_count` is the measured
number of products referencing the identity (at least 1) and both timestamps
are the row's own `retrieved_at`: **lower bounds, not history**. It changes
nothing outside the queue and is idempotent. The report prints counts and
row ids/flags only, never ingredient text. **Neither command was run against
the live database.** The existing live rows are not in the queue until the
owner runs `backfill` after deployment.

## 6. Behavior changes visible to clients (no wire-format change)

1. Tokens with no letter (`1234`) and system placeholder text are no longer
   returned as ingredients. Example: `Sugar, 1234, Salt` now returns two
   ingredients, not three.
2. A short token that is a fragment of a stored name no longer resolves to
   that stored name (the reproduced Water/Sugar/Salt case). Scans of the same
   text after other scans now return their own ingredients.
3. A failed label-image scan stores no placeholder ingredient rows, and its
   product row's `raw_ingredient_text` and `ingredient_ids` are empty instead
   of the placeholder sentence.
4. A failed/empty attempt on an existing unverified product keeps that
   product's ingredient text and ids.

Old clients: the response schema and status codes are unchanged. Effects 1
and 3 make results shorter or emptier where they were wrong.

## 7. Decisions left to the owner (not made here)

- **Dropping other anomalies** (`ALLERGEN_STATEMENT` "May contain peanuts",
  `HEADER_ARTIFACT`, `GENERIC_FUNCTION_TERM`) from the ingredient list rather
  than only flagging them. They are flagged today because removing a card is
  a product call and "May contain" is useful information; it could instead be
  shown as an allergen advisory.
- **Curated rows and whole-phrase containment.** A token that contains a
  curated row's full name as whole words still resolves to it (long-standing,
  Kotlin-parity behavior). With today's curated rows this cannot collapse a
  specific token into a broader one, but adding a broad curated name such as
  "Milk" would absorb "Skimmed milk powder". Stage 3 should add such rows with
  an alias-only rule or accept the containment.
- **The two polluted live catalog rows** (stage 1) and the 114 legacy
  read-time-reconstructed ids are not removed or rewritten; nothing here
  deletes or merges live rows. A read-only report is in the stage-1 SQL.

## 8. Verification

Pinned dependencies (Python 3.12 image: fastapi 0.115.6, pydantic 2.10.4,
SQLAlchemy 2.0.36, pytest 8.3.4, pytest-asyncio 0.25.0, asyncpg 0.30.0,
alembic 1.14.0):

| Check | Result |
|---|---|
| Full suite, `python -m pytest -q` (SQLite) | **686 passed, 15 skipped** (baseline `32bd7ef`: 590 passed, 10 skipped; the 15 skips are the opt-in Postgres tests) |
| `tests/postgres` on a disposable `postgres:16-alpine` (own network, no published ports, removed afterwards), incl. the new concurrency tests | **15 passed** |
| `alembic heads` | single head `d7e8f9a0b1c2` |
| Migration round-trip on PostgreSQL with representative data | upgrade, downgrade `c6d7e8f9a0b1`, re-upgrade all OK; curated row, OCR row, alias and product with two ids identical after upgrade; table and 4 indexes gone after downgrade; `ON DELETE SET NULL` observed; an unknown status, a zero count and a duplicate key are each rejected |
| `app.openapi()` vs `openapi.json` | **identical** |
| Maintenance CLI on the disposable DB | `backfill` and `prune` dry runs report and roll back |

PostgreSQL concurrency (`tests/postgres/test_ingredient_candidate_queue_postgres.py`):
12 concurrent scans of a new token converge on one row with
`encounter_count = 12`; 10 concurrent scans of an existing token add exactly
10; 8 scans touching six keys in opposite orders finish without deadlock (keys
are locked in sorted order, the row is locked with `SELECT ... FOR UPDATE`);
deleting an identity keeps the observation; the CHECK constraints reject bad
rows.

New tests: `tests/unit/test_ingredient_candidate_flags.py` (36),
`tests/unit/test_ocr_normalizer_identity_matching.py` (24 cases),
`tests/unit/test_ingredient_candidate_queue_migration.py` (3),
`tests/integration/test_ingredient_candidate_queue.py` (33, deterministic
synthetic fixtures, idempotent writes). One existing test
(`test_ingredient_language_provenance_migration`) pinned `c6d7e8f9a0b1` as the
head; it now asserts a single head and that the revision is part of the chain.

## 9. Dependencies and integration steps

- **Not stacked** on any unmerged branch. It applies to `origin/main`.
- **Issue #25 branch** (`fix/backend-ingredient-first-label-scan`): both edit
  `food_analysis.py`. Textual conflicts are small, two spots need care:
  `_apply_label_enrichment`'s ingredient-group condition (this stage adds
  `and ingredients_trustworthy`; #25 adds `and not resolution_failed`; keep
  both) and `_run_label_image_pipeline`'s fallback block. #25's
  `_ingredients_group_is_complete` also requires a usable ingredient, which
  agrees with this stage's junk rule.
- **PR #22** (`feat/backend-truthful-unknowns-diagnostics`): no functional
  dependency.
- After merge: deploy runs `alembic upgrade head` (one added table), then the
  owner may run `backfill`.

## 10. Limits

- The row cap is soft under concurrency: the count check and the insert are
  not one atomic step, so simultaneous new tokens can overshoot by at most the
  number of concurrent writers. It bounds growth; it is not a quota.
- `encounter_count` is per request, not per user or device; a user rescanning
  the same product counts again.
- Detection lists (headers, class prefixes, allergen phrases, class words,
  source words) are English and Bulgarian only and deliberately short; a
  token in another language is simply not flagged.
- `GENERIC_VS_SPECIFIC` and `CONFLICTING_IDENTIFIER` are review signals, not
  verdicts, and use text only.
- Source or scientific content is not touched here; that is stage 3.
