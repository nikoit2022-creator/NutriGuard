# NutriGuard Backend

Production backend for the existing NutriGuard **native Android (Kotlin +
Jetpack Compose)** app, implementing the `NutriGuard_API_Contract.md`
specification. Built with FastAPI, PostgreSQL, SQLAlchemy 2.x (async),
Alembic, Redis and Docker, as a modular monolith.

The Android client is **not modified** by this project. This backend is
designed so the client's existing `FoodAnalysisRepository` abstraction
can later be pointed at this API with minimal, mechanical changes (see
"Remaining Android integration work" at the end of this document).

## Changelog

**V12 (bug fixes, PR #9 review round 5):** V11's field-safety fixes and
cumulative-completeness model were correct, but three further gaps
surfaced in review:

1. **Removed the `always_verified` escape hatch.** `/scan/ocr-text`
   (no barcode) used to force `is_verified=true`/a computed Health
   Score regardless of nutrition trustworthiness. Heuristic/fallback
   nutrition must never set `has_verified_nutrition` -- it now never
   does, for this endpoint or any other. Literal user-submitted OCR
   text still establishes `has_verified_ingredients=true` (it is
   genuine evidence); nutrition stays unverified until a genuinely
   trustworthy source (a barcode provider, or a label-image scan)
   supplies it. **Practical effect**: a standalone `/scan/ocr-text`
   call, whose nutrition can never be genuinely verified through this
   endpoint alone, now CONSISTENTLY returns the same partial/
   `labelScanRequired` response `/scan/label-image` already uses for an
   incomplete result, instead of a `200` with a fabricated score -- see
   item 6 in section 6 and section 11.9.
2. **Fail-safe (not fail-open) verification defaults.**
   `is_verified`/`has_verified_nutrition`/`has_verified_ingredients`
   used to default to `true`, on the theory that "every row not
   explicitly external is locally verified" -- reviewed and found
   unsafe: a future write path that forgot to pass one of these
   explicitly would silently create verified, Health-Score-eligible
   evidence. All three now default `false`, everywhere (the ORM model,
   `_to_product_model`'s own keyword defaults, and -- via an amendment
   to the still-unmerged `a1b2c3d4e5f6` migration -- the database
   `server_default`s themselves, `cf5522508f9a`'s pre-existing two
   columns included). Every real write path already passed all three
   explicitly, so this is a pure safety-net change: a hypothetical
   future insert that omits one now fails toward "unverified", never
   "verified". See section 11.11.
3. **Partial analysis for an incomplete result.** A `404`/
   `labelScanRequired` response used to discard whichever evidence
   group WAS genuinely verified -- e.g. an ingredients-only label photo
   (the Ingredient Label tab's everyday output) got nothing back but a
   bare retry signal, even though its normalized ingredient analysis
   was real and useful. `_label_scan_required_details` now additively
   includes `analysisComplete`/`healthScoreAvailable`/`healthScore`
   (explicitly `null`, never `0`)/`nutritionScanRequired`/
   `ingredientsScanRequired`, plus the actual normalized `ingredients`
   array whenever that group is genuinely verified -- across
   `/scan/barcode`, the barcode-linked label/OCR endpoints, and the
   standalone ones. No OpenAPI schema change (confirmed byte-identical
   against a pinned-dependency regeneration) -- `error.details` was
   already untyped. See section 11.12.

See sections 11.9/11.11/11.12 for the full detail on each.

**V11 (feature + bug fixes, PR #9 review round 4):** The barcode +
label enrichment feature (V8-V10) didn't yet satisfy its own central
requirement: combining a barcode's discovered evidence with a label
scan's evidence into ONE product, when neither alone was complete. Two
substantial changes, both covered by new tests (see 11.13):

1. **Cumulative completeness across independent evidence groups.**
   `has_verified_nutrition` had been asked to mean two things at once:
   "nutrition is genuinely known" AND "the whole product is complete".
   A barcode discovery with real nutrition but no `ingredients_text` (a
   real, common Open Food Facts pattern) could never be completed by a
   later ingredients-only label photo, because the merge logic only
   ever looked at the CURRENT request's nutrition fields. Added a new
   `Product.has_verified_ingredients` column (migration `a1b2c3d4e5f6`,
   parent `cf5522508f9a`) so nutrition and ingredients are tracked,
   merged, and locked-once-complete INDEPENDENTLY -- from a trusted
   barcode provider, a label-image scan, or a mix of both, in any
   order, across any number of requests. `is_verified` (the single
   Health Score gate) is now exactly `has_verified_nutrition AND
   has_verified_ingredients`, applied uniformly to `analyze_barcode`'s
   own discovery path too (previously that path hardcoded
   `is_verified=false` for every external discovery regardless of
   completeness -- a genuinely complete discovery is now correctly
   protected, exactly like a genuinely complete label-scan enrichment
   already was). See section 11.8.
2. **Standalone `/scan/label-image`/`/scan/ocr-text` (no barcode) now
   apply the same language/safety policy.** They previously bypassed
   `resolve_label_text` entirely and always returned `200` with
   `is_verified=true`/`has_verified_nutrition=true`, even when Gemini
   explicitly nulled an unreadable nutrition value (i.e. a Health Score
   computed from placeholder zeros, presented as confident). Both now
   share the exact same pipeline the barcode-linked path uses --
   `/scan/label-image` now correctly returns the EXISTING structured
   `labelScanRequired` response (no new field, no OpenAPI change) when
   its evidence is genuinely incomplete; `/scan/ocr-text` at the time
   kept its own long-standing "always return a best-effort heuristic
   analysis" contract (its nutrition figures are never genuinely
   extracted by design, independent of this change) but applied the
   same English/Bulgarian preference and translation to its text.
   **Superseded by V12** (see below): that contract turned out to be
   unsafe and was removed. See section 11.9 for the current design and
   the Android-facing response note.

See section 11.8/11.9 for the full detail on each.

**V10 (bug fixes, PR #9 review round 3):** Three further correctness/
safety gaps found in a third review of the barcode + label enrichment
feature, fixed before merge:
1. `nutrition_fields_present`/`label_field_validity` correctly rejected
   unsafe nutrition values (NaN/Infinity/negative/out-of-range/boolean/
   placeholder), but the REJECTED value itself still flowed through
   `_as_float` into `AnalyzedProductData` and then into the `Product`
   row -- only `has_verified_nutrition` stayed `false`, while the
   garbage number sat in the column regardless. `parse_gemini_image_json_result`
   now uses `_safe_nutrition_value`, which returns the safe neutral
   placeholder `0.0` for anything that isn't genuinely trustworthy --
   a rejected value can no longer reach a `Product` column in ANY form.
   The merge path (`_apply_label_enrichment`) now also gates each
   nutrition/NOVA field's overwrite INDIVIDUALLY on that field's own
   validity (`gemini_image_parser.LabelFieldValidity`), so a field this
   attempt couldn't trust leaves an existing safe value untouched
   instead of blanking it to the neutral placeholder.
2. Translation-invariant checks (E-numbers, numbers/percentages/units)
   used one-way set subtraction (`source - translated`), which only
   ever caught a REMOVED or CHANGED token -- an ADDED/invented token
   (e.g. a fabricated extra E-number or figure the source never had)
   passed silently, and a REPEATED token collapsing to one occurrence
   (sets dedupe) was invisible too. Now uses `collections.Counter`
   multiset equality, which catches all four: added, removed, changed,
   and duplicate-count loss. Also fixed a real regex bug where `12%`
   silently backtracked to a bare `12` (a trailing `\b` never matches
   right after a non-word character like `%`), and confirmed comma/dot
   decimals (`12,5`/`12.5`) normalize deterministically to the same
   token.
3. `gemini_image_parser.py` hardcoded `allergens_detected="None"`
   unconditionally -- representing "the model said nothing about
   allergens" as a confirmed, factual "no allergens" claim. The image
   prompt now requests an `allergens` array; an explicit, non-empty
   list of named allergens is persisted (normalized, deduplicated), and
   anything else (missing, null, an empty array, a malformed type)
   persists as `""` (unknown), never `"None"`. `novaGroup` is now
   validated strictly as a genuine JSON integer 1-4 (rejecting
   booleans/strings/floats/negative/zero/out-of-range values); an
   invalid value uses the same `0` "unclassified" sentinel
   `barcode_discovery.py` already established, which `health_score.py`'s
   own zero-deduction branch already treats safely.

See section 11.2 and 11.3 for the full detail on each.

**V9 (bug fixes, PR #9 review):** Eight correctness/safety issues found
in review of the V8 barcode + label enrichment feature, fixed before
merge:
1. Gemini image dietary flags (`isGlutenFree`/`isLactoseFree`/`isVegan`/
   `isVegetarian`/`isHalal`/`isKosher`) defaulted to `true` for a
   missing/null/malformed value — same "unknown must never read as a
   positive certification" bug the barcode-discovery bridge already
   fixed for external providers (V6). Now defaults to `false`.
2. `nutrition_fields_present` accepted anything `float()` didn't raise
   on — a JSON boolean, `NaN`/`Infinity`, a negative number, an
   out-of-range number, or a numeric string all passed. Now rejects all
   of those and requires a genuine, finite, non-negative, in-range JSON
   number; the Gemini image prompt was also updated to require `null`
   (never a guessed/defaulted number) for a nutrition value that isn't
   actually legible on the label.
3. `_persist_enriched_product`/`_finalize_barcode_enrichment` committed
   up to four times per request (product, then provenance, then score+
   history) despite being described as transactional — a mid-sequence
   failure could leave a product row with no provenance, or other
   partial state. Restructured to exactly one commit per outcome (see
   11.6); also surfaced and fixed a real SQLite/pysqlite test-harness
   quirk where a SAVEPOINT could silently survive a `rollback()` (see
   11.6's note on `tests/integration/test_label_barcode_enrichment.py`'s
   `strict_db_session` fixture).
4. Bulgarian alphabet handling incorrectly excluded `ь` (a valid modern
   Bulgarian letter) as "non-Bulgarian", and a single ambiguous Latin
   token (`"in"`/`"or"`/`"per"`, coincidentally real words in other
   languages too) could tip a classification to English on its own. See
   11.3 for the corrected two-tier (strong/weak) evidence model.
5. Translation validation trusted Gemini's own self-reported
   `detectedLanguage`/`confidence` and didn't verify the translation
   actually corresponds to the source. Now independently verifies
   (never merely assumes) that the result is actually English, every
   source E-number survived unchanged, and every numeric value/
   percentage/unit survived unchanged — and the response schema now
   forbids unexpected extra fields.
6. Canonical ingredient normalization (the Bulgarian-alias-aware
   re-tokenization pass) only ran when `canonical_text` textually
   differed from the raw OCR text, which silently skipped pure-
   Bulgarian labels (whose canonical text is normally identical to the
   original) — Bulgarian ingredients never got a chance to dedupe
   against an equivalent English mention. Now runs for any real
   language content (English, Bulgarian, mixed, or translated).
7. OCR provenance was recorded with a hardcoded `confidence=1.0`, and
   an incomplete enrichment still stamped `last_verified_at` as if it
   had just been verified. Fixed: OCR provenance now uses the schema's
   own conservative `0.0` default (there is no real per-request
   confidence signal for OCR extraction, unlike translation, which
   already had one); `last_verified_at` is only ever set when
   `has_verified_nutrition` is true.
8. `openapi.json` had been hand-patched rather than generated from the
   repository's pinned dependencies. Regenerated and verified against a
   throwaway container running the exact pinned `requirements.txt` on
   Python 3.12 (matching CI) — byte-for-byte identical to the prior
   hand patch, confirming it was accurate, but now sourced correctly.

See section 11 (11.2–11.7) for the full detail on each.

**V8 (feature): Barcode + label enrichment and multilingual label
normalization.** When barcode discovery (V4/V5) cannot produce a
complete product and returns `labelScanRequired`, the client can now
resubmit a label image (or OCR text) together with the *original*
barcode, via a new optional `barcode` field on `POST
/scan/label-image` and `POST /scan/ocr-text`. The backend combines
both sources into one canonical, barcode-keyed product instead of a
synthetic `img_.../ocr_...` one, persists it through the same
product/history pipeline, and returns the unchanged
`FullProductAnalysisOut` schema — no second response shape. A later
plain barcode scan then resolves entirely from the local database, no
provider discovery or further label request needed. Label text is
normalized against an English/Bulgarian-first language policy: English
and Bulgarian sections are preferred and, when both are present,
merged with equivalent ingredients deduplicated; other-language-only
text is translated to canonical English via the existing Gemini
integration (no new provider/API key), with a controlled, structured
error when translation is unreliable rather than fabricated data. Both
new fields are optional and additive — no existing client behavior
changes when `barcode` is omitted. See section 11 for the full design;
no Alembic migration was needed (see 11.5).

**V6 (bug fixes, PR #7 review):** Six correctness issues in the V4/V5
barcode discovery feature, fixed before merge:
1. Unknown dietary flags (vegan/vegetarian/gluten-free/lactose-free/
   halal/kosher) now default to `false`, never `true` — missing data is
   never shown as a positive certification claim.
2. A discovery whose nutrition/ingredients are materially incomplete
   (identity-only UPCitemdb fallback, or an Open Food Facts entry with
   no `nutriments`/`ingredients_text`) no longer gets a Health Score
   computed from zero-filled placeholders. It's flagged
   (`Product.has_verified_nutrition=False`) and returns the same
   structured `labelScanRequired` response as a true not-found — on
   first discovery AND on every later cache hit of that row.
3. Migration `cf5522508f9a` now backfills existing rows (all
   pre-dating barcode discovery, i.e. local/OCR/label-image products)
   as `is_verified=true`; only an external discovery ever writes
   `false`, and always explicitly.
4. Open Food Facts field selection now enforces English/Bulgarian by
   the record's own *declared* language (`lang`/`lc`), not by script —
   a French/German/Albanian name is Latin script too and no longer
   leaks through as a primary display name.
5. Barcodes are now looked up and stored under one canonical GTIN-13
   key, so whitespace/dash variants and UPC-A/EAN-13-equivalent scans
   of the same product converge on one row instead of duplicating; the
   EAN-8/UPC-E 8-digit ambiguity is resolved by an explicit, tested
   precedence rule.
6. Provenance writes (`product_source_repository.record_discovery`) use
   a SAVEPOINT per provider instead of a full rollback on conflict, so
   one provider's uniqueness race can no longer discard another
   provider's already-flushed row from the same discovery.

See section 6, item 8 and section 10 for the full detail on each.

**V7 (bug fixes, PR #7 review round 2):** Three further correctness
gaps found in a second review of V6, fixed before merge:
1. `nutrition_known` required only ONE of the three core numeric Health
   Score inputs (sugar/sodium/saturated fat) to be present, so a
   discovery with only sugar known still zero-filled the other two and
   passed the completeness gate. Now requires all three.
2. Open Food Facts' `ingredients_text` was correctly language-gated,
   but the structured `ingredients[]` array's `.text` values were used
   unconditionally regardless of the record's declared language — an
   unsupported-language record could still leak untranslated ingredient
   text back into analysis via that fallback. Now only the array's
   always-English `.id` taxonomy identifiers are used when the record's
   declared language isn't English/Bulgarian.
3. Barcode lookup only checked the raw input and the canonical GTIN-13,
   so a pre-existing/legacy row stored under a non-canonical
   representation (e.g. a bare 12-digit UPC-A) was missed by an
   equivalent EAN-13 scan and could be duplicated. Lookup now checks
   every alias representation of a barcode
   (`barcode_validation.alias_keys`), not just one.

See section 6, item 9 and section 10 for the full detail on each.

**V4 (feature):** `POST /api/v1/scan/barcode` now attempts multi-source
product discovery on a local-database miss instead of immediately
returning 404: Open Food Facts, then a GS1 Digital Link resolution for
an official manufacturer resource, then UPCitemdb as an identity-only
fallback. A discovered product is validated, persisted (with full
per-source provenance), and returned through the same
`FullProductAnalysisOut` contract; a barcode no source recognizes still
returns `404 PRODUCT_NOT_FOUND`, now with a structured `details` payload
instead of an uninformative bare 404. OCR/label-image scanning and
Gemini remain the only way actual label content drives an analysis —
barcode discovery never fabricates nutrition/ingredients from the
barcode alone. See section 10 below for the full design, and section 6,
item 7 for the contract-deviation rationale.

**V3 (bug fix):** `POST /api/v1/scan/label-image` now actually uses a
successful Gemini image analysis. Previously it discarded the Gemini
JSON (only extracting `productName`/`rawIngredientText` via regex before
still running the keyword-heuristic fallback), which violated API
Contract 6.3. Root cause, fix, and regression tests are in section 6,
item 7 below. `analyze_barcode` (V2) and `analyze_ocr_text` are
unaffected by this change.

**V2 (contract fix):** `POST /api/v1/scan/barcode` no longer fabricates
a product for an unknown barcode. It now returns `404 PRODUCT_NOT_FOUND`,
consistent with `GET /products/{barcode}`. Root cause, rationale, and
regression tests are in section 6, item 6 below. `analyze_ocr_text` and
`analyze_label_image` are unchanged by V2 — analyzing free-text/image
label content via the Gemini/fallback pipeline is still exactly what
those two endpoints are for.

---

## 1. Quick start (Docker)

> **Verification note:** this development sandbox has no Docker daemon
> available, so `docker compose up` itself could not be executed here.
> Every component the compose stack orchestrates *was* verified for
> real, though: Postgres 16 was installed and run locally, the Alembic
> migration was generated against it and applied with `alembic upgrade
> head`, the seed loader was run against it, and the full FastAPI app
> was exercised through `pytest` (61/61 passing) and manual `uvicorn`
> startup. Please run `docker compose up --build` as a final check in
> an environment with Docker available before deploying.

```bash
cp .env.example .env
# edit .env: set JWT_SECRET, and GEMINI_API_KEY if you want real AI
# analysis (the app works correctly without it — see "Gemini" below).

docker compose up --build
```

This starts three containers: `db` (Postgres 16), `redis` (Redis 7), and
`backend` (FastAPI, auto-reload). On startup the backend automatically:
1. waits for Postgres to accept connections,
2. runs `alembic upgrade head`,
3. seeds the scientific ingredient database (idempotent — safe on every restart).

API docs: `http://localhost:8000/api/v1/docs` (Swagger UI) or
`http://localhost:8000/api/v1/redoc`.
Health check: `http://localhost:8000/health`.

For production, layer the prod compose file on top:
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

## 2. Running locally without Docker

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Point at a real Postgres you control, e.g.:
export DATABASE_URL="postgresql+asyncpg://nutriguard:nutriguard@localhost:5432/nutriguard"
export JWT_SECRET="dev-only-secret"
export REDIS_ENABLED=false   # or point REDIS_URL at a real Redis

alembic upgrade head
python -m app.seed.load_seed     # loads the 12 scientific ingredients
uvicorn app.main:app --reload
```

## 3. Running tests

```bash
source .venv/bin/activate
pytest -q
```

The full suite (**321 tests**: 320 run by default + 1 opt-in) runs
against an in-memory SQLite database via `aiosqlite` — no Docker or
Postgres required, and it runs in a few seconds. This is intentional:
SQLite is good enough to validate all business logic and API behavior,
while the actual deployment always targets Postgres (see
`alembic/versions/` for the Postgres-generated migrations, authored and
applied against a real local PostgreSQL 16 instance during development,
not just SQLite — see section 10.7 for how the barcode-discovery
migration specifically was verified, and section 11.6 for the one
opt-in test, `tests/postgres/test_concurrent_enrichment_postgres.py`,
which needs a real disposable Postgres instance and is skipped by
default).

Every barcode-discovery test mocks its HTTP layer (`httpx.MockTransport`
for provider-adapter tests, in-memory fake `BarcodeProductProvider`
implementations for orchestration/API-level tests) — **no test depends
on live Open Food Facts/GS1/UPCitemdb access**; see section 10.7 for the
full list and how to run just that subset.

Coverage:
- `tests/unit/test_health_score.py` — Health Score Calculator, including boundary/threshold behavior and clamping.
- `tests/unit/test_warning_engine.py` — all 12 Personalized Warning Engine rules, plus a regression test asserting the documented "unimplemented flags" quirk stays unimplemented.
- `tests/unit/test_ocr_normalizer.py` — tokenization, E-number/name matching, synthetic ingredient heuristics.
- `tests/unit/test_fallback_and_gemini_parser.py` — deterministic fallback analysis, and the preserved Gemini-result-parsing quirk (see section 6 below).
- `tests/integration/test_auth.py` — device registration, idempotency, token validation, refresh rotation/revocation.
- `tests/integration/test_scan_flow.py` — full request/response cycles for scan/product/ingredient/health-profile/scan-history endpoints, cache-hit vs cache-miss behavior, per-user isolation, pagination, 404/422 error envelopes.
- `tests/integration/test_barcode_contract_change.py` — V2 regression suite: unknown barcode returns 404 with zero side effects, known barcode returns the real stored product, hand-verified Health Score end-to-end, Warning Engine end-to-end after enabling a profile flag, and a spy-based test proving the synthetic-ingredient/fallback pipeline is never invoked for a barcode lookup.
- `tests/unit/test_gemini_image_parser.py` — V3 regression suite: valid Gemini JSON is parsed field-for-field, DB-matched vs. synthetic ingredient resolution, missing/invalid JSON and missing required fields all correctly return `None` (triggering fallback upstream).
- `tests/integration/test_label_image_gemini_fix.py` — V3 end-to-end: valid Gemini JSON drives the full response (nutrition, flags, NOVA group, ingredients, Health Score); invalid JSON, network timeout, and missing API key all correctly fall back to `fallback_local_analysis`; a simultaneous Gemini+fallback failure returns `503 AI_SERVICE_UNAVAILABLE`.
- `tests/unit/test_barcode_validation.py` — EAN-8/EAN-13/UPC-A/UPC-E checksum validation and GTIN-13/GTIN-14 normalization, including that the backend's own OCR/image synthetic barcode ids are correctly rejected (never sent to a provider); whitespace/dash/UPC-A-vs-EAN-13-equivalent representations share one canonical GTIN-13; the EAN-8/UPC-E ambiguity's explicit precedence rule (with a constructed colliding example).
- `tests/unit/test_barcode_providers.py` — each provider adapter (Open Food Facts, GS1 Digital Link resolver, UPCitemdb) against mocked HTTP responses: success, not-found, malformed body, rate limit, and transient-error retry; Open Food Facts' English/Bulgarian language-gated field selection (French-only record rejected, explicit `_en` field honored even on a non-English record, Bulgarian-declared record accepted).
- `tests/unit/test_barcode_discovery_service.py` — orchestration: provider priority, error isolation (a timeout/rate-limit/error from one provider never blocks the next), the UPCitemdb-fallback-only rule, conflicting-identity flagging, GS1 confidence corroboration, and the English/Bulgarian display-name policy.
- `tests/unit/test_barcode_discovery_migration.py` — the barcode-discovery Alembic migration has a single head, a valid parent revision, and defines both `upgrade`/`downgrade`.
- `tests/integration/test_barcode_discovery_flow.py` — full `POST /scan/barcode` discovery flow: local-hit short-circuit, Open Food Facts success, Open Food Facts miss → UPCitemdb fallback (correctly incomplete, V6), provider timeout/rate-limit/malformed-response isolation, all-providers-unavailable structured 404, invalid-barcode rejection with zero network calls, repeated-discovery dedup, verified-local-data protection, conflicting-source preservation, equivalent-barcode-representation dedup, and provenance-conflict race-safety.
- `tests/unit/test_food_analysis_discovery_bridge.py` — unknown dietary flags default to `false` (never `true`); materially incomplete nutrition/ingredients are flagged `has_verified_nutrition=False` in every combination (missing nutrition, missing ingredients, missing both); complete data is scored normally.
- `tests/unit/test_language_detection.py` — semantic English/Bulgarian/other/unknown classification (not merely script-based): German/French/Russian/Ukrainian text correctly rejected as not-English/not-Bulgarian despite matching script; purely numeric/E-number text is "unknown", not a false positive.
- `tests/unit/test_label_language.py` — `resolve_label_text`'s full language policy: English/Bulgarian preferred over another-language duplicate section, mixed EN+BG retained, other-language-only text translated (mocked Gemini), low-confidence/invalid/malformed translation responses all raise the controlled `TranslationUnreliableError`, E-numbers/percentages/quantities/units untouched by the pipeline.
- `tests/integration/test_label_barcode_enrichment.py` — full `POST /scan/label-image` (+ `/scan/ocr-text`) barcode-enrichment flow: no-barcode/placeholder-barcode backward compatibility, unknown-barcode product creation, incomplete-discovery enrichment in place, a subsequent plain barcode scan resolving locally with discovery never invoked, UPC-A/EAN-13 alias dedup, repeated-enrichment idempotency, verified-data protection, incomplete-nutrition `labelScanRequired`, invalid-barcode validation errors, end-to-end mixed-language dedup and translation, translation-failure persists nothing, and no raw image bytes ever stored.

## 4. Implemented endpoints

All 11 endpoints from the API Contract, plus the two auth endpoints it specifies:

| Method | Path | Contract ref |
|---|---|---|
| POST | `/api/v1/auth/device` | 3.2 |
| POST | `/api/v1/auth/refresh` | 3.2 |
| POST | `/api/v1/scan/barcode` | 6.1 |
| POST | `/api/v1/scan/ocr-text` | 6.2 (+ optional `barcode`, see section 11) |
| POST | `/api/v1/scan/label-image` | 6.3 (+ optional `barcode`, see section 11) |
| GET | `/api/v1/products/{barcode}` | 6.4 |
| GET | `/api/v1/products` | 6.5 |
| GET | `/api/v1/ingredients` | 6.6 |
| GET | `/api/v1/ingredients/{id}` | 6.7 |
| GET | `/api/v1/health-profile` | 6.8 |
| PUT | `/api/v1/health-profile` | 6.9 |
| GET | `/api/v1/scan-history` | 6.10 |
| DELETE | `/api/v1/scan-history` | 6.11 |

Every endpoint: Pydantic request/response schemas, JWT auth dependency
(except `/auth/*`), per-tier rate limiting, and the standard error
envelope. OpenAPI docs are generated automatically by FastAPI (a static
snapshot is included at `openapi.json` for convenience/diffing).

## 5. Database tables

| Table | Purpose |
|---|---|
| `users` | Logical account (1:1 with a device today; supports multiple devices for future real login) |
| `devices` | Registered client devices, keyed by client-generated `device_id` |
| `refresh_tokens` | Persisted refresh-token registry (`jti`), enabling rotation and revocation |
| `ingredients` | Scientific ingredient database — mirrors `IngredientEntity` exactly |
| `products` | Analyzed products — mirrors `ProductEntity` exactly, including a denormalized `ingredient_ids` text column (see section 6), plus additive discovery-provenance columns (see section 10.4) not part of the public API contract |
| `scan_history` | Per-user scan log — mirrors `ScanHistoryEntity` |
| `user_health_profiles` | One row per user — mirrors `UserHealthProfile` |
| `product_sources` | One row per (barcode, provider) external discovery — see section 10.4 |

Indexes: `ingredients(common_name)`, `ingredients(e_number)`,
`products(product_name)`, `products(brand)`, `devices(device_id)`,
`refresh_tokens(jti)`, `scan_history(user_id)`, `scan_history(scanned_at)`,
`product_sources(barcode)`
— covering every lookup path used by the endpoints (barcode PK lookup,
ingredient search/E-number lookup, product search, per-user history
ordered by recency, per-barcode provenance lookup).

## 6. Deviations from the API Contract (documented, not silent)

Per the task's "Critical Rule", these are called out explicitly rather
than silently resolved:

1. **`ProductEntity.ingredientIds` is a plain `TEXT` column, not a
   relational join table.** The original design note in the API
   Contract suggested a junction table internally. In practice, the
   Kotlin client's OCR pipeline creates *synthetic* ingredient IDs
   (`synth_...`) for unmatched label tokens that are never persisted as
   real `IngredientEntity` rows — anywhere, including in the client's
   own Room database. A foreign-key-backed junction table cannot
   reference those IDs without either violating referential integrity
   or persisting throwaway synthetic rows the client never does. Storing
   the comma-separated string directly (exactly like the Room column)
   is the faithful, contract-compliant choice; it was chosen after
   modeling the alternative and finding it incompatible with the
   client's actual (documented) behavior.

2. **Gemini "success" path still uses the deterministic fallback for
   nutrition figures.** While porting `GeminiAnalysisEngine`, the
   original `parseGeminiJsonResult` was found to extract `productName`,
   `brand`, `sugarGrams`, `sodiumMg`, `saturatedFatGrams`, and
   `novaGroup` from the Gemini JSON response via regex — but then call
   `fallbackLocalAnalysis(productName, originalRawText, dbIngredients)`,
   which **only uses `productName`** and recomputes everything else
   (nutrition figures, matched ingredients, dietary flags) from the raw
   text via keyword heuristics, discarding the AI-provided brand,
   nutrition numbers, and full ingredients array entirely. This is
   reproduced verbatim in `app/services/gemini_result_parser.py` (see
   its docstring) and covered by
   `test_gemini_result_parser_uses_only_product_name_from_json`. This
   was **not "fixed"** because the task instructions are explicit that
   behavioral parity with the existing app takes priority over what
   might look like a bug — if the product team wants Gemini's full
   structured output to actually drive nutrition figures, that is a
   deliberate product decision that should update the API Contract
   first.

3. **`avoidPeanuts`, `avoidSoy`, `avoidTreeNuts`, `requireVegetarian`
   accepted but inert.** As documented in API Contract 7.2, these
   `UserHealthProfile` flags exist in the client's data model but have
   no corresponding check in `PersonalizedWarningEngine`. The backend
   stores and returns these fields (so the client's model round-trips
   correctly) but does not generate warnings from them, preserving the
   original behavior. Covered by
   `test_unimplemented_flags_produce_no_warnings_by_design`.

4. **`health_score` is recomputed and persisted on every barcode
   lookup**, not just returned transiently. The API Contract specifies
   the score must reflect the *current* health profile on every request
   (since profile changes should immediately affect previously-scanned
   products), but doesn't mandate whether the DB row itself is updated.
   This implementation writes the recomputed score back to `products`
   on each scan for simplicity and consistency; this has no visible
   effect on any API response and can be changed to a purely transient
   computation if the team prefers not to persist an ephemeral value.

5. **`DELETE /scan-history` has no corresponding UI/ViewModel call in
   the current Android app** (per API Contract 6.11) — the DAO method
   exists in Room but nothing in `MainViewModel` invokes it yet. It is
   still implemented here since the DAO layer defines it and the
   contract lists it, so the endpoint is available whenever the client
   wants to add a "clear history" button.

6. **`POST /scan/barcode` no longer fabricates a product for an unknown
   barcode (V2 fix).** The original implementation (V1) mirrored the
   Kotlin client's `FoodAnalysisRepository.analyzeBarcode()` unknown-
   barcode branch literally: it ran the Gemini/fallback pipeline against
   a fixed sample ingredient string and persisted the result under the
   scanned barcode, exactly reproducing the app's actual behavior at the
   time. Real API testing (V2 task) confirmed this produces misleading,
   non-deterministic output — a barcode is an identifier, not label
   content, and fabricating ingredients/nutrition/health-score/warnings
   for a barcode the trusted product source has never seen is incorrect
   regardless of client parity. This has been changed: an unknown
   barcode now returns `404 PRODUCT_NOT_FOUND` (the same error already
   used by `GET /products/{barcode}`), with zero side effects (no
   product row, no scan-history entry). This is a deliberate,
   product-owner-directed contract change, not a silent fix — see the
   V2 task's root-cause analysis for the full investigation. The OpenAPI
   schema is unchanged (diffed to confirm) since `PRODUCT_NOT_FOUND` was
   already a documented error code; only runtime behavior changed.
   `analyze_ocr_text` and `analyze_label_image` — the endpoints that
   exist specifically to run the Gemini/fallback pipeline against actual
   label content — are unaffected.

   During this investigation, the reported "Health Score = 0" and
   "warnings = []" symptoms were traced and found to be **correct
   outputs of correctly-functioning code** operating on the (now
   removed) fabricated input — not bugs in `HealthScoreCalculator` or
   `PersonalizedWarningEngine`. Both are covered by their existing unit
   test suites (43 passing) plus new end-to-end regression tests in
   `tests/integration/test_barcode_contract_change.py` that hand-verify
   an expected score against a real persisted product. One cosmetic,
   pre-existing artifact was also identified and is intentionally left
   unchanged (out of scope for this fix, and explicitly excluded by the
   V2 task's scope boundaries): `fetch_ingredients_for_product` (used to
   reconstruct ingredients for an already-persisted product) calls
   `create_synthetic_ingredient(id)` with the **id string itself**
   (e.g. `"synth_sodium_nitrite"`) rather than the original OCR token
   (e.g. `"Sodium Nitrite"`) whenever a referenced ingredient isn't a
   real DB row — this was already true of the original Kotlin client
   behavior being mirrored, and produces an odd-looking `commonName`
   (e.g. `"Synth_sodium_nitrite"`) without affecting risk-level
   classification or any downstream Health Score / Warning correctness,
   since the relevant keywords survive substring matching against the
   slug either way (verified in
   `test_health_score_matches_hand_verified_formula_for_real_product`).

7. **`POST /scan/label-image` now uses a successful Gemini response in
   full (V3 fix).** The original implementation called
   `gemini_service.analyze_image(...)`, then extracted only
   `productName` and `rawIngredientText` via ad-hoc regex from the raw
   response text — and, regardless of whether that extraction succeeded,
   unconditionally ran `fallback_local_analysis` afterward, which
   recomputes nutrition figures, dietary flags, and NOVA group from
   keyword heuristics rather than using Gemini's actual structured
   output. This violated API Contract 6.3, which explicitly specifies:
   send image → parse Gemini JSON → normalize ingredients → calculate
   Health Score → generate warnings → save → return. A new parser,
   `app/services/gemini_image_parser.py`, now does this correctly: when
   Gemini returns valid, usable JSON (`productName` and
   `rawIngredientText` present), every field it provides — nutrition
   figures, dietary flags, NOVA group, brand — is used as-is, and its
   `ingredients[]` array is normalized against the scientific database
   using the same deterministic architecture as everywhere else
   (`ocr_normalizer.match_against_database` / `create_synthetic_ingredient`
   for unmatched items), per API Contract 7.3. Gemini's own per-ingredient
   scientific claims (risk level, bad-for-* flags, health concerns) are
   intentionally NOT trusted or persisted — only the seeded scientific
   database or the deterministic synthetic-ingredient heuristic are
   authoritative for those, per the contract's AI-safety rule. The
   fallback chain is otherwise unchanged: Gemini network error/timeout/
   missing key/invalid-or-incomplete JSON → `fallback_local_analysis`;
   fallback failure → `AI_SERVICE_UNAVAILABLE` (503). `analyze_ocr_text`
   is intentionally **not** touched by this fix — it preserves the
   separate, documented quirk in item 2 above, which is out of scope for
   this task. See `tests/unit/test_gemini_image_parser.py` and
   `tests/integration/test_label_image_gemini_fix.py` for the full
   regression coverage (valid JSON used end-to-end; invalid JSON,
   network timeout, and missing API key all correctly fall back; and a
   simultaneous Gemini+fallback failure correctly returns 503).

7. **`POST /scan/barcode` no longer returns an immediate, uninformative
   404 for a barcode the local database has never seen (V4 feature).**
   V2 (item 6 above) correctly stopped fabricating a product for an
   unknown barcode, but left "unknown" meaning only "not yet in our own
   database" — even though a barcode is, unlike free-text OCR content, a
   stable identifier that trusted external product databases can often
   resolve. `analyze_barcode` now attempts multi-source discovery (Open
   Food Facts → GS1 Digital Link resolution → UPCitemdb fallback — full
   design in section 10) before giving up. This is still not a reversal
   of V2: no data is ever fabricated from the barcode alone, every
   provider result is validated before being trusted, UPCitemdb's
   nutrition claims are never used (it has none to offer — see 10.2),
   and a barcode no source recognizes still returns `404
   PRODUCT_NOT_FOUND` — now carrying a structured `error.details`
   payload (`labelScanRequired: true` + which providers were checked)
   instead of an empty body, so the client can steer the user straight
   to a label scan instead of a dead end. The OpenAPI schema is
   unchanged (diffed to confirm): `FullProductAnalysisOut` and
   `ProductOut` gained no new fields, and `PRODUCT_NOT_FOUND` was
   already a documented error code with an untyped `details` field.
   Discovered nutrition/ingredient data that is genuinely incomplete
   (e.g. UPCitemdb-only identity, or Open Food Facts with no
   `ingredients_text`/`nutriments`) still runs through the same
   deterministic Health Score/Warning Engine as everywhere else in this
   codebase (consistent with how the existing OCR fallback path already
   scores thin/heuristic data), but is always paired with an explicit
   `INFO`-severity "Incomplete Product Data" warning in the response —
   the score is never presented as more confident than the data
   backing it actually is. See `tests/integration/test_barcode_discovery_flow.py`
   and `tests/unit/test_barcode_discovery_service.py` for the full
   regression coverage.

8. **Six correctness fixes to the V4/V5 barcode discovery feature,
   found in PR #7 read-only review and fixed before merge (V6).** None
   of these change the public API contract further than V4/V5 already
   did — they correct *internal* logic that was silently wrong:
   - Unknown dietary flags now default `false` (see
     `_to_analyzed_data_from_discovery`), never `true` — a missing
     value must never read as a positive certification.
   - A materially incomplete discovery
     (`Product.has_verified_nutrition=False`) never reaches the Health
     Score Calculator — not on first discovery, not on any later cache
     hit of that row — and returns the same structured
     `labelScanRequired` response a true not-found does (via
     `_label_scan_required_details`), rather than a score computed from
     zero-filled placeholders.
   - Migration `cf5522508f9a` backfills existing (local/OCR/label-image)
     rows as `is_verified=true`/`has_verified_nutrition=true`; the ORM
     model's own Python-side default matches, so only an *external*
     discovery ever needs to explicitly write `false`.
   - Open Food Facts field selection (`_language_gated_field`) now
     checks the record's own declared `lang`/`lc`, not merely
     Latin-vs-other script — a French/German/Albanian name is Latin
     script too and no longer leaks through OFF's language-neutral
     `product_name`/`ingredients_text` fields.
   - Barcode storage/lookup uses one canonical GTIN-13 key
     (`product_repository.get_by_barcode_or_aliases`); the EAN-8/
     UPC-E 8-digit ambiguity is resolved by an explicit, tested
     precedence rule (`BarcodeInfo.ambiguous_upc_e`) instead of an
     implicit accident of control flow.
   - `product_source_repository.record_discovery` uses a SAVEPOINT
     (`db.begin_nested()`) per provider instead of a full
     `db.rollback()` on a uniqueness conflict, verified against both
     SQLite and PostgreSQL — a conflict on one provider's row can no
     longer discard another provider's already-flushed row from the
     same discovery loop.

   See `tests/unit/test_food_analysis_discovery_bridge.py`,
   `tests/unit/test_barcode_validation.py`,
   `tests/unit/test_barcode_providers.py`, and the new tests in
   `tests/integration/test_barcode_discovery_flow.py` for the full
   regression coverage of each.

9. **Three further correctness gaps from a second PR #7 review (V7),
   fixed before merge.** All three were the V6 fixes not being applied
   as strictly/completely as intended, not new contract changes:
   - `barcode_discovery.py`'s `nutrition_known` used `any(...)` over the
     three core numeric Health Score inputs (sugar/sodium/saturated
     fat) — a single field being present was enough to pass the
     completeness gate, silently zero-filling the other two. Changed to
     `all(...)`: every one of the three must be present. Covered by
     `test_partial_nutrition_is_not_considered_known` (unit) and
     `test_partial_nutrition_with_ingredients_still_refuses_a_score`
     (integration).
   - `open_food_facts.py`'s `_ingredient_tokens` consumed the
     structured `ingredients[].text` field unconditionally, regardless
     of the record's declared language — the same leak item 8's
     language gate closed for `ingredients_text`, just reachable
     through the `ingredients[]` array fallback instead. Fixed by
     preferring `ingredients[].text` only when the record's declared
     language is English/Bulgarian, and otherwise using only
     `ingredients[].id` (OFF's own always-English taxonomy identifier,
     e.g. `"en:sodium-benzoate"` — never the literal foreign-language
     label text). Covered by
     `test_off_ingredient_tokens_use_english_taxonomy_id_not_foreign_text`.
   - `get_by_barcode_or_canonical` (now `get_by_barcode_or_aliases`)
     only checked the raw scanned string and the canonical GTIN-13 —
     missing a pre-existing/legacy row stored under a *different* valid
     alias (e.g. a bare 12-digit UPC-A row, found via a later
     EAN-13-zero-padded-equivalent scan), which could then be
     duplicated. Fixed with `barcode_validation.alias_keys`, which
     computes every plausible legacy storage key for a given barcode
     (UPC-A ⇄ EAN-13-zero-padded, specifically); the lookup now checks
     all of them. New rows are still always persisted under the single
     canonical `gtin13` — aliases are for *finding* a pre-existing row,
     never for *choosing* where a new one is written. Covered by
     `test_legacy_upc_a_row_is_found_by_a_later_equivalent_ean13_scan`.

10. **`POST /scan/ocr-text` (no barcode) no longer always returns `200`
    (V12, PR #9 review round 5, finding 1).** Previously this endpoint
    forced `is_verified=true` unconditionally (`always_verified=True`
    in `_finalize_standalone_label_analysis`), on the theory that
    literal user-submitted OCR text is inherently trusted local-
    pipeline content. Review found that unsafe specifically for
    NUTRITION: this endpoint's nutrition figures are ALWAYS the
    deterministic keyword-heuristic guess (item 2 above), never
    genuinely extracted, regardless of the raw text's own quality or
    Gemini's success -- treating them as verified fabricated a
    confident-looking Health Score from numbers that were always a
    guess. Ingredients are unaffected (the raw text is the caller's own
    genuine content, so `has_verified_ingredients` still becomes
    `true`), but since nutrition can now never be genuinely verified
    through this endpoint alone, **every** standalone `/scan/ocr-text`
    call consistently returns the same `404 PRODUCT_NOT_FOUND` /
    `labelScanRequired` response `/scan/label-image` already uses for
    an incomplete result (with its verified ingredient evidence
    attached, see section 11.12), instead of the `200` it always
    returned before. This is a real, intentional behavior change an
    Android client must handle -- not merely an edge case, since it now
    applies to every call to this endpoint when used standalone (no
    `barcode` field). A barcode resubmitted through
    `analyze_ocr_text_with_barcode` is unaffected in spirit: an
    EXISTING row's already-verified nutrition (from a trusted barcode
    provider, or a later `/scan/label-image` call) is preserved and
    still completes the product normally -- see section 11.9. The
    OpenAPI schema is unchanged (confirmed byte-identical against a
    pinned-dependency regeneration): `FullProductAnalysisOut` gained no
    fields, and `error.details` was already untyped `Any`. Covered by
    `tests/integration/test_label_barcode_enrichment.py`'s
    `test_standalone_ocr_text_returns_partial_ingredients_without_health_score`
    and `test_standalone_ocr_text_gemini_success_still_does_not_verify_nutrition`,
    and `tests/integration/test_scan_flow.py`'s
    `test_scan_ocr_text_returns_partial_analysis_without_health_score`.

No other ambiguities were found that required deviating from the
contract; where the contract was silent on an implementation detail
(e.g., exact rate-limit numbers, refresh-token rotation strategy), a
reasonable production default was chosen and documented inline in code.

## 7. Gemini integration

`app/integrations/gemini.py` is the only module that talks to Google's
Gemini API. The key is read from `GEMINI_API_KEY` (environment
variable / secret), **never** hard-coded and **never** returned to the
client. If left empty, `GeminiService.is_configured` is `False` and the
system transparently uses the deterministic local fallback for
everything — the API behaves identically either way from the client's
perspective (aside from the AI actually being consulted), which is why
the test suite runs with `GEMINI_API_KEY=""` and still exercises the
full analysis pipeline.

## 8. Security checklist

- HTTPS-ready (deploy behind a TLS-terminating proxy/load balancer; the app itself is protocol-agnostic).
- JWT access/refresh tokens (HS256), refresh tokens are persisted, single-use (rotated), and revocable.
- Pydantic validates every request body; malformed input → `422 VALIDATION_ERROR`, never a stack trace.
- Upload size is enforced server-side (`MAX_IMAGE_SIZE_BYTES`, default 8MB) independent of any client-side compression.
- Rate limiting via `slowapi` (Redis-backed in production, in-memory in tests), tiered per endpoint group (API Contract section 8).
- CORS origins are configurable via `CORS_ORIGINS`, not wide open by default in production.
- All SQL goes through SQLAlchemy's parameterized query builder — no raw string-interpolated SQL anywhere.
- A catch-all exception handler guarantees stack traces, SQL errors, and secrets are never serialized into an HTTP response; everything is logged server-side instead (`app/core/logging.py` additionally redacts known-sensitive keys from structured logs).
- Secrets are only ever read from environment variables (`app/core/config.py`); `.env` is git-ignored, `.env.example` documents every variable with no real values.

## 9. Remaining Android integration work (out of scope for this task)

Documented for completeness — **not implemented, per the task's explicit
instruction not to modify the mobile app**:

- No Retrofit/network client exists in the app yet; `FoodAnalysisRepository` reads/writes Room directly.
- No token storage (e.g. `EncryptedSharedPreferences`/`DataStore`) for the access/refresh tokens this backend issues.
- `GEMINI_API_KEY` is still embedded in the client's `BuildConfig` and called directly from the device — this backend supersedes that call path, but removing the client-side key requires an app change.

## 10. Multi-source barcode product discovery

`POST /api/v1/scan/barcode` (API Contract 6.1) checks the local
`products` table first, exactly as before V4. What changed is what
happens on a miss: instead of an immediate 404, the backend now attempts
to *discover* the product from external sources before giving up. A
barcode is treated as an identifier the whole time — this never
fabricates ingredients, nutrition, or a health score from the barcode
number itself; every value returned came from a specific, attributed
external source, or the request ends in a structured "not found"
response instead.

### 10.1 Discovery order

```
local products table (barcode PK lookup)
  │  miss
  ▼
validate barcode as EAN-8 / EAN-13 / UPC-A / UPC-E (app/services/barcode_validation.py)
  │  invalid format → straight to "not found" (§10.5), zero external calls
  ▼
Open Food Facts (identity + nutrition + ingredients + allergens + dietary flags)
  │
  ▼
GS1 Digital Link resolution (independent side channel — always attempted
  when enabled, regardless of the Open Food Facts outcome; contributes
  only an official resource URL + a small confidence corroboration,
  never a product identity)
  │
  ▼
Open Food Facts found a name AND a brand?
  │ yes → done, UPCitemdb is not spent
  │ no
  ▼
UPCitemdb (identity/brand/image fallback only)
  │
  ▼
normalize + validate the merged result (app/services/barcode_discovery.py)
  │  nothing usable found anywhere → structured "not found" (§10.5)
  ▼
persist (race-safe, never overwrites verified/higher-confidence data — §10.6)
  │
  ▼
same Health Score / Warning Engine pipeline as every other analysis path
```

### 10.2 Provider responsibilities

| Provider | Module | Contributes | Never contributes |
|---|---|---|---|
| Open Food Facts | `app/integrations/barcode_providers/open_food_facts.py` | Product name, brand, category, image, raw ingredient text + tokens, nutrition (sugar/sodium/saturated fat/NOVA group), allergens, dietary-flag tags | — |
| GS1 Digital Link resolver | `app/integrations/barcode_providers/gs1_resolver.py` | An official manufacturer/GS1-registered resource URL, as corroborating provenance | Product identity, nutrition, ingredients — a resolver links to official resources, it is not a product database |
| UPCitemdb | `app/integrations/barcode_providers/upcitemdb.py` | Product title, brand, category, image — identity/commerce metadata only | Nutrition, ingredients, allergens, dietary flags — never parsed from its response at all (not merely "not trusted": the adapter has no code path that could produce a `NutritionFacts` value from it), consistent with "do not automatically treat UPCitemdb nutrition/health information as authoritative" |

All three implement the shared `BarcodeProductProvider.fetch(barcode) ->
ProviderProductResult | None` abstraction (`app/integrations/barcode_providers/base.py`).
`app/services/barcode_discovery.py` is the orchestration service: it
applies the priority/fallback order above, a per-call timeout
(`asyncio.wait_for`), and error isolation — a timeout, HTTP error,
malformed response, or rate limit from one provider is caught and
logged (provider name + error class only, never a response body,
header, or query string) and never prevents trying the next provider.
Retries (via `tenacity`, an existing pinned dependency) are limited and
apply only to genuinely transient failures (timeout/network
error/5xx) — never to a 4xx, a rate limit, or a malformed body, which
move on immediately instead of burning retry budget.

### 10.3 Configuration

All optional, all with safe, working defaults — the backend starts and
serves every other endpoint normally with none of this configured.
Documented in `.env.example`; see `app/core/config.py` for the
`Settings` fields.

| Variable | Default | Purpose |
|---|---|---|
| `BARCODE_DISCOVERY_ENABLED` | `true` | Master switch; `false` skips straight to "not found" with zero external calls |
| `OPEN_FOOD_FACTS_ENABLED` | `true` | Per-provider enable flag |
| `OPEN_FOOD_FACTS_BASE_URL` | `https://world.openfoodfacts.org` | |
| `OPEN_FOOD_FACTS_USER_AGENT` | `NutriGuard-Backend/1.0 (+https://github.com/nikoit2022-creator/NutriGuard)` | OFF asks integrators to identify their app |
| `GS1_RESOLVER_ENABLED` | `true` | Per-provider enable flag |
| `GS1_RESOLVER_BASE_URL` | `https://id.gs1.org` | GS1's own public global resolver |
| `UPCITEMDB_ENABLED` | `true` | Per-provider enable flag |
| `UPCITEMDB_BASE_URL` | `https://api.upcitemdb.com/prod/trial` | Free, no-credentials tier by default |
| `UPCITEMDB_API_KEY` / `UPCITEMDB_USER_KEY` | *(empty)* | Optional, for a paid UPCitemdb plan; sent as request headers only, never logged, never returned to the client |
| `BARCODE_PROVIDER_TIMEOUT_SECONDS` | `5` | Per-call connect/read timeout |
| `BARCODE_PROVIDER_MAX_RETRIES` | `1` | Extra attempts on a transient failure, per provider |
| `BARCODE_DISCOVERY_CACHE_SECONDS` | `2592000` (30 days) | Intended freshness window for re-verifying a previously-discovered (unverified) product; the discovery/merge logic itself is in place today (§10.6), this setting is reserved for a scheduled re-verification job, not yet implemented |

### 10.4 Provenance and confidence

Every external source that answers for a barcode gets its own row in a
new `product_sources` table (`app/models/product_source.py`,
`app/repositories/product_source_repository.py`) — one row per
`(barcode, provider)`, refreshed in place on a repeat discovery
(dedup), never merged across providers, so two sources that disagree
stay separately reviewable rather than silently blended into one. Each
row keeps: provider name, source URL, a confidence score, the
provider's own `last_modified` (when supplied), `discovered_at` /
`last_verified_at`, the provider's own claimed name/brand/image/raw
ingredients/nutrition (compact JSON)/allergens/language, its upstream
id, small curated metadata, and an `is_conflicting` flag.

`Product` itself gained a handful of additive, backward-compatible
columns (`source`, `source_confidence`, `is_verified`,
`has_verified_nutrition`, `discovered_at`, `last_verified_at`) used only
for internal merge/scoring-gate decisions — **not** part of the public
API contract (`ProductOut` was not changed). `is_verified` defaults to
**true** (every pre-existing/local/OCR/label-image row is verified by
construction); only an external discovery explicitly writes `false`.
`has_verified_nutrition` defaults to true too, and is explicitly set
`false` only for a materially incomplete discovery — see §10.5.

Provider trust weights are fixed constants in code
(`barcode_discovery.PROVIDER_BASE_TRUST`: Open Food Facts 0.75, GS1
0.60, UPCitemdb 0.45), not environment-configurable — they encode a
considered trust ordering, not a deployment knob. A GS1 resolution
corroborating the primary source's answer nudges confidence up slightly
(agreement increases confidence); a conflicting UPCitemdb identity when
supplementing an incomplete Open Food Facts result lowers it and sets
`is_conflicting` on both sources' rows, and the conflicting field is
never used for the persisted product (Open Food Facts' own claim wins,
never a blend of the two).

### 10.5 What happens when no product is found

If no provider recognizes a barcode (or discovery/all providers are
disabled, or the barcode fails EAN/UPC checksum validation), the
endpoint still returns `404 PRODUCT_NOT_FOUND` — but with a structured
`error.details` payload instead of an empty one:

```json
{
  "error": {
    "code": "PRODUCT_NOT_FOUND",
    "message": "No product found for barcode 0000000000000.",
    "details": {
      "labelScanRequired": true,
      "reason": "Barcode not found in the local database or any configured external source.",
      "providersChecked": [
        {"provider": "open_food_facts", "outcome": "not_found"},
        {"provider": "gs1_digital_link", "outcome": "not_found"},
        {"provider": "upcitemdb", "outcome": "skipped"}
      ],
      "suggestedAction": "Use POST /scan/label-image or POST /scan/ocr-text to analyze the product's label directly."
    },
    "timestamp": 1735689600000
  }
}
```

`outcome` is one of `found` / `not_found` / `timeout` / `rate_limited` /
`error` / `skipped` (skipped = the provider was disabled, or UPCitemdb
wasn't needed because Open Food Facts already had a complete identity).
The response shape (`error.code`/`message`/`details`/`timestamp`) is
unchanged from the existing `ErrorResponse` contract — only `details`,
already a documented untyped field, is populated.

A second, related shape covers a barcode whose *identity* WAS found and
persisted, but whose nutrition/ingredient data was too incomplete to
compute a real Health Score for (V6 — see item 8 in section 6): still
`404 PRODUCT_NOT_FOUND`, `labelScanRequired: true`, but with a
`discoveredIdentity` object (barcode/productName/brand/imageUrl) instead
of `providersChecked`, so a client can still show the user *something*
even without a score. This is what a UPCitemdb-only fallback (identity,
no nutrition) always produces, and what a repeat scan of that same
barcode keeps producing — it's cached, not re-discovered every time, but
never scored either.

### 10.6 Validation, deduplication, and safety rules

- Barcodes are validated as EAN-8/EAN-13/UPC-A/UPC-E via the standard
  GS1 mod-10 checksum before any provider is contacted
  (`app/services/barcode_validation.py`); a checksum mismatch or
  unsupported length never reaches a provider. The Android client
  performs no barcode validation of its own today (confirmed by
  inspection), so this only ever *adds* protection — it never rejects
  anything the client currently accepts for the local-database lookup
  path, which is unaffected by this validation. The EAN-8/UPC-E 8-digit
  ambiguity (some digit strings validate as both) is resolved by an
  explicit, tested precedence rule (EAN-8 wins; `BarcodeInfo.ambiguous_upc_e`
  flags when this happened) rather than an implicit accident of control
  flow.
- Barcode storage and lookup use one **canonical GTIN-13 key**
  (`product_repository.get_by_barcode_or_aliases`): the raw string the
  client sent is tried first (matching a legacy row or a non-GTIN
  synthetic `ocr_.../img_...` id verbatim), then the canonical form —
  so "036000291452" (UPC-A), "0036000291452" (its EAN-13-zero-padded
  equivalent), and "036-000-291452" (same digits, dashes) all resolve to
  the same row. Every barcode-discovered product is persisted under its
  canonical form (`discovered.barcode.gtin13`), so the response's
  `product.barcode` may not byte-for-byte equal a UPC-A/dashed input —
  deliberate, and covered by
  `tests/integration/test_barcode_discovery_flow.py::test_equivalent_barcode_representations_resolve_to_one_product`.
- Persisting a discovered product is race-safe:
  `product_repository.insert_new` wraps its INSERT in a SAVEPOINT and,
  on a primary-key conflict (a concurrent discovery of the same barcode
  won the race), re-reads the now-existing row rather than erroring or
  duplicating — verified against both SQLite and PostgreSQL. Provenance
  writes (`product_source_repository.record_discovery`) use the same
  SAVEPOINT pattern, per provider, specifically so a conflict on one
  provider's row can never discard another provider's row already
  flushed earlier in the same discovery loop (a plain `db.rollback()`
  would do exactly that — this was fixed after review; see
  `tests/integration/test_barcode_discovery_flow.py::test_provenance_conflict_does_not_roll_back_earlier_source_in_same_transaction`).
- **Verified local data is never overwritten.** A product created
  through this app's own label-scan pipelines (`analyze_ocr_text` /
  `analyze_label_image`) is marked `is_verified=True` and is never
  touched by a later barcode-discovery attempt for the same barcode —
  in practice this is moot in isolation, since those pipelines mint
  their own non-numeric synthetic barcode ids, but it also protects any
  row a discovery race resolves in favor of an already-persisted,
  verified row.
- A previously-*discovered* (unverified) row is only overwritten by a
  later discovery of **strictly higher** confidence for the same
  barcode; equal-or-lower confidence keeps the existing row.
- Every discovery attempt — winning or not — still gets its own
  `product_sources` row, so a lower-confidence or conflicting claim is
  preserved for review rather than silently dropped.

### 10.7 Running the mocked tests / verifying the migration

```bash
pytest tests/unit/test_barcode_validation.py tests/unit/test_barcode_providers.py \
       tests/unit/test_barcode_discovery_service.py tests/unit/test_barcode_discovery_migration.py \
       tests/unit/test_food_analysis_discovery_bridge.py \
       tests/integration/test_barcode_discovery_flow.py -q
```

Every one of those mocks its HTTP layer — `httpx.MockTransport` for the
provider-adapter tests, in-memory fake `BarcodeProductProvider`
implementations for the orchestration/API-level tests — so this subset,
like the rest of the suite, makes zero live network calls.

The Alembic migration itself (`alembic/versions/cf5522508f9a_barcode_discovery_provenance.py`)
was verified against a real PostgreSQL 16 instance, via
`docker compose run --rm backend alembic <cmd>` against the project's
own `db` service: `upgrade` from the initial schema, `downgrade -1` back
to it, and `upgrade head` again, all applied cleanly; a product row
inserted *before* the upgrade was confirmed to still be readable
afterward with the new columns correctly backfilled by their
`server_default`s (`source='local'`, `is_verified=false`). This is not
re-run by `pytest` itself, consistent with this project's existing
convention (see section 3) that real DDL execution against Postgres is
a manual/CI concern, not something the SQLite-backed test suite
attempts — `tests/unit/test_barcode_discovery_migration.py` instead
checks the migration's structural integrity (single head, valid parent
revision, both `upgrade`/`downgrade` present) on every `pytest` run.

## 11. Barcode + label enrichment and multilingual label normalization

### 11.1 API contract additions

Both additive, optional, backward compatible (an existing client that
never sends the new field behaves byte-for-byte as before):

- `POST /api/v1/scan/label-image` — new optional multipart form field
  `barcode`. Omitted, blank, or a literal placeholder (`"null"`,
  `"N/A"`, whitespace) behaves exactly like today: `analyze_label_image`
  runs unchanged, minting a synthetic `img_...` id.
- `POST /api/v1/scan/ocr-text` — new optional JSON field `barcode` on
  `OcrTextScanRequest`, same semantics.
- Both return the existing `FullProductAnalysisOut` — never a second,
  Android-incompatible response shape. An invalid supplied barcode
  (fails `barcode_validation.validate_and_normalize`) returns the
  standard `422 VALIDATION_ERROR` envelope, the same one `/scan/barcode`
  already uses for an empty barcode.

### 11.2 Merge/upsert design

`app/services/food_analysis.py`'s `analyze_label_image_with_barcode` /
`analyze_ocr_text_with_barcode` (sharing `_finalize_barcode_enrichment`)
implement:

- The supplied barcode is validated/canonicalized and resolved through
  every alias (`barcode_validation.alias_keys` +
  `product_repository.get_by_barcode_or_aliases`) — the same lookup
  `/scan/barcode` uses, so a UPC-A scan finds a row already stored
  under its EAN-13-zero-padded equivalent (or vice versa) and never
  creates a duplicate.
- No existing row → a new one is created under the canonical GTIN-13
  key (`product_repository.insert_new`, SAVEPOINT-based, race-safe —
  the same mechanism V5/V6 barcode discovery already relies on).
- An existing row that is already fully trusted (`is_verified` — V11:
  BOTH `has_verified_nutrition` AND `has_verified_ingredients`, see
  11.8) is never modified — the label analysis still runs (so its
  provenance is recorded) but can't overwrite it.
- Otherwise, fields are merged, not replaced wholesale: identity
  (name/brand/category/image/allergens) is kept when already meaningful
  (not a placeholder string — see `barcode_text_safety.is_placeholder`
  — and not a known discovery placeholder like `"Discovered Product"`/
  `"Unknown Brand"`) and filled from the label analysis only when
  missing.
- Nutrition/NOVA fields are merged at PER-FIELD granularity, not as one
  all-or-nothing blob (`gemini_image_parser.LabelFieldValidity`,
  applied by `_apply_label_enrichment`): an existing row's sugar/
  sodium/saturated-fat/NOVA value is only ever replaced by a fresh
  attempt's value when THAT SPECIFIC field was individually
  trustworthy this time — a field the new attempt couldn't trust
  leaves the existing value untouched rather than blanking it to a
  placeholder. Whatever numeric value actually gets written is always
  safe regardless: a rejected value (see below) is normalized to the
  neutral placeholder `0.0`/`0` at parse time
  (`gemini_image_parser._safe_nutrition_value`/`_safe_nova_group`), so
  it can never reach a `Product` column even on the very first fill-in.
- `has_verified_nutrition` only flips to `true` when the label analysis
  itself produced genuinely known nutrition — all three of
  sugar/sodium/saturated-fat present as a genuine, finite, non-negative,
  physically-defensible-range JSON number in the model's structured
  output (`gemini_image_parser.nutrition_fields_present`) — never a
  boolean, `NaN`/`Infinity`, a negative or out-of-range value, a numeric
  string, or a heuristic fallback guess; the same "all three required"
  gate `barcode_discovery.discover_product` already uses for external
  providers. `has_verified_ingredients` flips to `true` independently
  (V11, see 11.8) — the two are no longer coupled, so nutrition alone
  can complete without ingredients, and vice versa, in either order,
  across any number of requests. The Gemini image prompt itself
  requires `null` (never a guessed number) for a nutrition value that
  isn't actually legible on the label. A row that doesn't clear BOTH
  gates (`is_verified`) stays `labelScanRequired` (`404
  PRODUCT_NOT_FOUND`, the same structured payload `/scan/barcode`
  returns for an incomplete discovery), never a confident-looking score
  from placeholder data.
- `novaGroup` is validated strictly as a genuine JSON integer 1-4
  (rejects booleans, strings, floats, negative values, `0`, and
  anything above `4`); an invalid value uses the same `0`
  "unclassified" sentinel `barcode_discovery.py` already established
  for its own incomplete discoveries, which `health_score.py`'s own
  zero-deduction `else` branch already treats identically to any other
  non-2/3/4 value — never a fabricated processing-level claim, never a
  score skewed either direction.
- `allergens_detected` only persists an explicit, non-empty, named
  allergen list from the model (normalized: trimmed, deduplicated
  case-insensitively, comma-joined); anything else — missing, `null`,
  an empty array, or a malformed type — persists as `""` (unknown),
  never the string `"None"` as a fabricated confirmed-absence claim.
  Neither `allergens_detected` nor `novaGroup` are consumed by the
  Personalized Warning Engine (it acts on the boolean dietary flags and
  the ingredient risk levels respectively) — an unknown/invalid value
  in either field cannot fabricate or suppress a warning.
- `is_verified` is kept equal to `has_verified_nutrition AND
  has_verified_ingredients` (V11): an incomplete row stays open to a
  *later* enrichment attempt for the same barcode — completing either
  group, in either order — and only a row genuinely complete in BOTH
  is protected from being overwritten again. `last_verified_at` is only
  ever stamped when `is_verified` ends up `true` — an enrichment
  attempt that leaves a row still missing either group must not carry
  a timestamp implying it was just fully re-verified. See 11.8 for the
  full cumulative-completeness design.
- `/scan/ocr-text`'s barcode path always treats nutrition as NOT
  genuinely known: that endpoint preserves the pre-existing, documented
  `gemini_result_parser.py` quirk where nutrition figures come from the
  deterministic fallback recomputed against raw text, never from a
  model's structured numbers — i.e. never genuinely-extracted real
  label data, by long-standing design. A barcode resubmitted through
  `/scan/ocr-text` alone only reaches `has_verified_nutrition=true` if
  the existing row already had it — but its (always genuinely
  user-submitted) text DOES count as trustworthy ingredients evidence,
  see 11.8's `ingredients_trustworthy`.
- Everything is persisted through the same `Product`/`Ingredient`
  matching/scan-history pipeline every other analysis path uses — no
  parallel write path, no raw image bytes ever touch storage (only
  `PIL.Image.verify()` reads them, in memory, then they're discarded).

### 11.3 Language policy

`app/services/language_detection.py` (`detect_language`) and
`app/services/label_language.py` (`resolve_label_text`) implement:

- **Semantic, not script-based, detection.** Script (Latin/Cyrillic) is
  only a first filter; English/Bulgarian classification additionally
  requires food-label vocabulary evidence, tiered by ambiguity: a small
  set of STRONG words (`"ingredients"`, `"contains"`, `"захар"`,
  `"съставки"`, ...) distinctive enough to trust on their own, and a
  larger set of WEAK words (common function words like `"water"`/
  `"вода"`) that require **at least two distinct hits together** before
  they're trusted — a single ambiguous token (`"in"`/`"or"`/`"per"`,
  coincidentally real words in German/French/Italian too) can never tip
  the classification by itself. For Cyrillic text, the classification
  additionally requires the *absence* of letters exclusive to other
  Cyrillic-alphabet languages (Russian/Ukrainian/Belarusian/Serbian —
  е.g. `ы`/`э`/`ё`/`і`/`ї`/`є`); modern Bulgarian's own `ь` (e.g.
  `"шофьор"`, chauffeur) is correctly NOT treated as excluding evidence.
  Net effect: a German/French/Italian/Spanish/Romanian label is never
  misread as English, and a Russian/Ukrainian label is never misread as
  Bulgarian, merely because the script (or one coincidental word)
  matches.
- **Section splitting + preference.** Raw label text is split on
  language-section headers (`Ingredients:`/`Съставки:`/`Zutaten:`/...),
  line breaks, and `/`/`|` dividers, and each section is classified
  independently — real multi-market packaging usually separates
  languages this way. Any English and/or Bulgarian sections found are
  used verbatim (English first); other-language sections are ignored
  once English/Bulgarian content exists.
- **Mixed EN+BG dedup.** When both are present, both are retained in
  the stored/displayed text, and equivalent ingredients are
  deduplicated once tokenized: `label_language.bulgarian_ingredient_alias`
  maps common Bulgarian ingredient-list tokens (e.g. `"захар"`) to
  their English canonical form *for matching purposes only* (never
  altering the stored text), so an English and a Bulgarian mention of
  the same ingredient collapse into one matched/synthetic ingredient
  instead of two.
- **Other-language-only → translation.** With no usable English/
  Bulgarian section, the best other-language text is translated to
  canonical English via the existing `GeminiService` (no new provider
  or API key) with a dedicated, injection-hardened prompt (the OCR text
  is explicitly framed as data, never instructions) requiring
  structured JSON (`detectedLanguage`/`confidence`/`translatedText`,
  **extra fields forbidden**), validated with a strict Pydantic model
  before use. Gemini's own `detectedLanguage`/`confidence` self-report
  is never trusted alone — every translation is additionally verified
  **deterministically**, independent of anything the model claims about
  itself: the result must independently detect as English (via the same
  `language_detection.detect_language`, not Gemini's claim), and the
  **multiset** (`collections.Counter`, not a one-way set difference) of
  E-numbers — and separately, of numeric values/percentages/units — in
  the source and in the result must match EXACTLY. Multiset equality
  (not one-way subtraction) is what catches all four failure modes: a
  token REMOVED, one CHANGED, one silently ADDED/invented (a one-way
  "is the source a subset of the translation" check would miss this
  entirely), and a DUPLICATE occurrence lost or gained (plain set
  equality would miss this too, since sets dedupe). Percentages are
  extracted as a single `"12%"` token (a regex fix: a naive trailing
  word-boundary assertion backtracks and silently drops the `%`), and
  comma/dot decimals (`12,5`/`12.5`) normalize to the identical token
  deterministically. A response that fails to parse, fails schema
  validation (including an unexpected extra field), reports non-finite/
  out-of-range confidence, is a placeholder, reports confidence below
  `label_language._MIN_TRANSLATION_CONFIDENCE` (0.55), or fails any of
  those deterministic invariant checks raises
  `TranslationUnreliableError` (`422 LABEL_TRANSLATION_UNRELIABLE`) —
  nothing is persisted for that request. This also means a
  prompt-injection attempt embedded in the OCR source text doesn't need
  to be specifically detected as such: if it causes the model to
  produce content that doesn't actually correspond to the source (a
  changed/added/dropped E-number or number, non-English output), that
  mismatch alone is what gets it rejected.
- **Original text preserved.** The original OCR/label text is always
  kept (see 11.4, `label_ocr` provenance row's `raw_ingredient_text`)
  even when a translated or EN+BG-merged canonical text is what's
  actually stored on `Product.raw_ingredient_text` and analyzed.
  Translation success alone never marks anything "verified" — that
  still requires the nutrition-completeness gate in 11.2.

### 11.4 Provenance

Recorded through the **existing** `product_sources` table/repository
(`product_source_repository.record_discovery`, reused as-is) — no new
table: a `label_ocr` (or `ocr_text`) row records the original OCR text,
detected language, and whether translation was used
(`raw_metadata_json`); when translation ran, a second `label_translation`
row records the source language, the Gemini model, and the confidence
score (`ProductSource.confidence`). Both use the column set every other
provider provenance row already uses.

The OCR/extraction pipeline itself never reports its own confidence
(unlike the separate translation call, which does — self-reported, but
never trusted alone, see 11.3) — so the `label_ocr`/`ocr_text`
provenance row's `confidence` uses the schema's own conservative `0.0`
default (the same "no signal" sentinel `product_source_repository`
already uses elsewhere), never a fabricated `1.0` "full confidence"
claim.

### 11.5 Why no migration was needed (for the V8 feature itself)

`Product.source` / `source_confidence` / `is_verified` /
`has_verified_nutrition` / `discovered_at` / `last_verified_at`
(added by `cf5522508f9a` for barcode discovery) were sufficient to
represent the ORIGINAL (V8) merge/verification state, and
`ProductSource.language` / `.confidence` / `.raw_metadata_json`
(already present on that table) are sufficient to represent the
language/translation provenance in 11.4 — nothing the V8 feature itself
needed was unrepresentable in the schema as it stood then. Section 11.8
below (V11) explains why a migration eventually WAS needed once
nutrition and ingredients had to be tracked as two independent
completeness signals, not one.

### 11.6 Transaction boundaries and concurrency

`_finalize_barcode_enrichment` makes exactly ONE `db.commit()` call on
every path, not several:

- **Incomplete enrichment** (nutrition still not verified after the
  merge): one commit persists the product change and its provenance
  *together*, then `ProductNotFoundError` (`labelScanRequired`) is
  raised — never a product row committed without its provenance.
- **Successful (verified) enrichment**: the product merge/insert,
  provenance write(s), Health Score, and scan-history insert are all
  flushed in-memory first and committed together in one final
  `db.commit()` — a failure at any point before it (a provenance write
  error, a scan-history write error, anything) rolls back the *entire*
  attempt via `app.database.session.get_db`'s
  `except Exception: await session.rollback()`, including the product
  merge/insert from earlier in the same call. Proven directly by
  `tests/integration/test_label_barcode_enrichment.py`'s
  `test_provenance_write_failure_rolls_back_the_whole_enrichment` and
  `test_scan_history_write_failure_rolls_back_product_and_provenance_too`.

**Concurrency guarantee — stated precisely, not overclaimed:**

- The brand-new-row INSERT race is **genuinely safe**: it reuses
  `product_repository.insert_new`'s SAVEPOINT-based primary-key-conflict
  handling, and this is verified against **real, separate PostgreSQL
  connections** (not the SQLite test suite's single shared connection —
  see the note below) in
  `tests/postgres/test_concurrent_enrichment_postgres.py`: two
  concurrent enrichments of the same brand-new barcode always converge
  on exactly one row, never a duplicate, never a crash. This test is
  opt-in (`NUTRIGUARD_TEST_POSTGRES_URL`) against a disposable Postgres
  instance — see the file's own docstring for how to run it; it is not
  part of the default `pytest -q` run, the same convention this project
  already uses for the barcode-discovery migration (section 10.7).
- The EXISTING-row merge path (`_apply_label_enrichment`) is **only
  best effort**: `Product` has no optimistic-concurrency version
  column, so two truly concurrent enrichments of the same
  already-existing (incomplete) row can race, and the later `COMMIT`
  wins ("last write wins"), not a merge of both attempts. This does not
  corrupt data or duplicate rows (SQLAlchemy's identity map plus the
  primary-key `UPDATE` keeps it to one row either way), but it is not
  linearizable. Closing that gap would need a version/`xmin`-based
  optimistic lock on `Product`, deliberately left out of this PR's
  scope as a real schema change.
- The SQLite-backed default test suite's single shared connection
  (`tests/conftest.py`'s `db_engine`) cannot faithfully exercise either
  guarantee directly — see `product_repository.insert_new`'s own
  docstring. Its rollback tests instead use a **dedicated, isolated**
  SQLite engine/session (`test_label_barcode_enrichment.py`'s
  `strict_db_session` fixture) with the standard SQLAlchemy
  pysqlite/aiosqlite workaround applied (disabling the DBAPI's own
  legacy implicit-transaction tracking, which otherwise can silently
  make a SAVEPOINT survive a `session.rollback()` — a SQLite-driver-only
  quirk, never a problem against real PostgreSQL). This fix is
  deliberately scoped to that one fixture, not applied to the shared
  `db_engine` every other test uses, to avoid surfacing unrelated
  pre-existing assumptions elsewhere in the suite that are out of scope
  for this PR.

### 11.8 Cumulative completeness across independent evidence groups (V11)

The central requirement this feature exists for -- combine barcode
discovery and a label scan into one product -- did not actually work
when only ONE side had complete evidence for only ONE of the two
things a Health Score needs (nutrition, ingredients). Fixed by tracking
them as genuinely independent evidence groups everywhere a product's
completeness is decided, not just in the label-enrichment merge path:

- **Data model**: `Product` gained `has_verified_ingredients`
  (migration `a1b2c3d4e5f6`, parent `cf5522508f9a` — the pre-existing
  single head; the chain still has exactly one head afterward). Verified
  upgrade → downgrade → upgrade against a disposable PostgreSQL 16
  instance, including a real backfill check (see below). `is_verified`
  is now, everywhere, exactly `has_verified_nutrition AND
  has_verified_ingredients` — never set independently.
- **Backfill** (conservative, per the review requirement): every row
  that existed before this migration was written under the OLD model,
  in which the app's own code always kept `is_verified ==
  has_verified_nutrition` for the label-enrichment path, and
  `has_verified_nutrition` already encoded FULL completeness
  (`nutrition_known AND ingredients_known`) for the barcode-discovery
  path. So `has_verified_ingredients = has_verified_nutrition` for
  every pre-existing row exactly reproduces what the OLD model already
  implied about it, without inspecting any text content — no risk of
  "upgrading" an incomplete row based on a non-empty placeholder
  string. Verified against real PostgreSQL: a row seeded as
  `is_verified=true, has_verified_nutrition=true` backfills
  `has_verified_ingredients=true`; a row seeded as
  `has_verified_nutrition=false` backfills `has_verified_ingredients=false`
  and stays incomplete until a real future enrichment attempt earns it.
- **`food_analysis._to_analyzed_data_from_discovery`** (the barcode-
  discovery bridge) now returns `nutrition_known`/`ingredients_known`
  as two separate booleans (previously one combined `is_complete`
  flag), and `_persist_discovered_product` stores each on its own
  column. `analyze_barcode`'s Health Score gate is now `if not
  product.is_verified` (previously `if not
  product.has_verified_nutrition`) — a discovery that's complete in
  exactly one dimension correctly still returns `labelScanRequired`.
  One behavior note: `is_verified` for a barcode-discovered row was
  previously ALWAYS `false` regardless of completeness (it meant
  "locally-sourced", not "complete"); a genuinely complete discovery
  now correctly becomes `is_verified=true` and is protected from being
  overwritten by a later, lower-value re-discovery — the same
  protection a genuinely complete label-scan enrichment already had.
- **`food_analysis._apply_label_enrichment`** (the label-enrichment
  merge) now has TWO independent blocks, each gated on its own
  `has_verified_*` flag and locked once complete:
  - **Ingredients group**: `raw_ingredient_text`/`ingredient_ids`, NOVA
    group, dietary flags, and allergens — all physically read off the
    ingredient list, so they're gated and committed together
    (`ingredients_trustworthy`, see below).
  - **Nutrition group**: sugar/sodium/saturated-fat, still gated
    per-field by `LabelFieldValidity` (review round 3, finding 1).

  Both blocks can fire in the SAME request (the common case — one
  photo supplies both) or only one can (a photo of just the nutrition
  panel, or just the ingredients list); whichever group a request
  didn't complete stays exactly as it was, ready for a LATER request to
  complete independently, in any order, any number of requests apart.
- **`ingredients_trustworthy`**: whether THIS request's ingredient
  evidence is trustworthy enough to even consider — `True` only when
  Gemini's structured image extraction succeeded (its rawIngredientText
  is real label content); `False` whenever the deterministic
  keyword-heuristic fallback ran for a label IMAGE (its "raw text" is a
  hardcoded placeholder/error string, not the actual label). For
  `/scan/ocr-text`'s barcode path, always `True` — the raw text there
  is always the caller's own genuine submitted content (validated ≥ 3
  characters by the router), unlike the image fallback's placeholder,
  regardless of whether Gemini or the deterministic fallback tokenized
  it.
- **Efficiency/robustness fix**: when the ingredients group is ALREADY
  verified and locked for a barcode, this request's ingredient text is
  never going to be applied at all — so `resolve_label_text` now runs
  in lenient mode (`strict=False`, see 11.9) for that request, and the
  canonical-ingredient rebuild step is skipped entirely. Demanding a
  successful STRICT translation for text that will be discarded
  regardless was both wasted work and a real spurious-failure mode
  (e.g. a nutrition-only photo whose incidental OCR leftovers don't
  confidently classify as English must never block completing the
  NUTRITION group with an unrelated translation error).

### 11.9 Standalone label analysis reuses the same pipeline (V11, amended V12)

`analyze_label_image`/`analyze_ocr_text` (no barcode) previously had
their own separate copy of the Gemini-then-fallback chain and never
called `resolve_label_text` at all — always returning `200` with
`is_verified=true`/`has_verified_nutrition=true` regardless of what was
actually extracted, including when Gemini explicitly returned `null`
for an unreadable nutrition value (a Health Score computed from
placeholder zeros, presented as confident). Both endpoints now share
the exact same building blocks the barcode-linked path uses
(`_run_label_image_pipeline`, `resolve_label_text`, the canonical
ingredient rebuild, `LabelFieldValidity`/`ingredients_trustworthy`) via
one new shared tail function, `_finalize_standalone_label_analysis` —
review round 4, finding 3 ("avoid duplicated pipelines"): this
duplication is exactly how the two paths drifted apart in the first
place.

- **`/scan/label-image` (no barcode)**: now gates its response on
  `is_verified` (both evidence groups), exactly like the barcode-linked
  path. **API response change Android must handle**: this endpoint can
  now return the EXISTING structured `404 PRODUCT_NOT_FOUND` /
  `labelScanRequired` envelope — byte-for-byte the same shape
  `/scan/barcode` already returns for this situation, with the same
  `discoveredIdentity` sub-object (so the client can still show a
  product name even without a score) — instead of a `200` carrying a
  confident-looking Health Score computed from placeholder-zero
  nutrition. **No new field, no OpenAPI schema change** — confirmed
  byte-for-byte identical against a pinned-dependency regeneration (see
  11.13). This can only happen when the label genuinely couldn't be
  read reliably (Gemini unavailable/invalid response, or an explicit
  `null` nutrition field); a normal, successful scan is completely
  unaffected and returns exactly what it always has.
- **`/scan/ocr-text` (no barcode)**: applies the SAME language policy
  (English/Bulgarian preference, translation, ingredient dedup) to its
  raw text. **UPDATED in V12 (PR #9 review round 5, finding 1)**: this
  endpoint used to be deliberately NOT gated the same way as
  `/scan/label-image` (an `always_verified=True` escape hatch in
  `_finalize_standalone_label_analysis`, preserving what V11 called its
  "local-pipeline content is trusted for display and scoring" contract)
  — reviewed and found unsafe: this endpoint's nutrition figures are
  ALWAYS the deterministic keyword-heuristic guess (the
  `gemini_result_parser.py` quirk: nutrition always comes from the
  fallback recomputed against the raw text, never from Gemini's
  structured numbers), never genuinely extracted, so treating them as
  verified always fabricated a confident-looking score from numbers
  that were always a guess. The escape hatch is REMOVED; this endpoint
  is now gated the same way `/scan/label-image` is. Because its
  nutrition can never be genuinely verified through this endpoint
  alone, this means a standalone `/scan/ocr-text` call now
  CONSISTENTLY returns the `404`/`labelScanRequired` response (with its
  verified INGREDIENTS evidence attached as useful partial data — the
  raw text is the caller's own genuine content, so
  `has_verified_ingredients` still becomes `true` — see section 11.12)
  rather than occasionally, the way `/scan/label-image` does. See item
  10 in section 6 for the full Android-facing contract-change writeup.
  A product created via `/scan/ocr-text` is therefore typically NOT
  `is_verified=true` and NOT immediately resolvable via a later `POST
  /scan/barcode` lookup of the same synthetic id — that lookup now
  correctly returns the same partial response too, consistent
  everywhere a still-incomplete row is read.
- Neither endpoint's translation ever hard-fails the request: both call
  `resolve_label_text(..., strict=False)` — a translation failure
  (infrastructure or a failed invariant check) falls back to the
  original text rather than rejecting a standalone, ad-hoc scan that
  has no barcode-linked canonical row to protect and no continuation
  request a client would ever retry (contrast the barcode-linked path,
  which keeps its original strict behavior, unchanged).
- The original OCR/label text is preserved via `_record_label_provenance`
  — the same existing `product_sources` table the barcode-linked path
  already uses; no new table, no schema change for this either. No raw
  image bytes are ever stored for either endpoint (only
  `PIL.Image.verify()` reads them, in memory). Both guarantees hold on
  the INCOMPLETE (`404` partial) path too, not only the success path —
  `_record_label_provenance` and the image-verify-only handling both
  run before the completeness check, in every `_finalize_*` function.

### 11.11 Fail-safe verification defaults (V12)

`Product.is_verified`/`has_verified_nutrition`/`has_verified_ingredients`
defaulted to `true` (fail-OPEN) since `cf5522508f9a` first introduced
`is_verified`/`has_verified_nutrition`, on the theory that "every row
not explicitly written by an external discovery is a genuinely verified
local one". Reviewed (round 5, finding 2) and found unsafe: it meant
ANY future write path — a new call site, a refactor, a bug — that
simply forgot to pass one of these three arguments would silently
create FULLY VERIFIED, Health-Score-eligible evidence, rather than
failing safe. Every current write path already passes all three
explicitly (`_to_product_model`'s callers, `_apply_discovered_fields`,
`_apply_label_enrichment`) — the default was never actually exercised
by this app's own code, so flipping it is a pure safety-net change with
zero behavior change for any real code path, confirmed by the full
pinned suite still passing unmodified (aside from the tests described
below) and by the real-Postgres verification.

Three layers, each independently defaulting to `false` now:

1. **The ORM model** (`app/models/product.py`): `default=False,
   server_default=false()` for all three columns.
2. **`food_analysis._to_product_model`'s own keyword defaults**: this
   helper explicitly sets these three fields on every `Product(...)` it
   builds, so the ORM column default is never actually consulted for
   any row IT creates — but the helper had its OWN separate
   `is_verified: bool = True` (etc.) parameter defaults, a second,
   independent fail-open gap at the Python call-site level. Flipped to
   `False` too, for the identical reason.
3. **The database `server_default`** — amended into the still-unmerged
   `a1b2c3d4e5f6` migration (never merged, so amending it in place
   rather than adding a new migration keeps the chain minimal):
   - `has_verified_ingredients` is now added with
     `server_default=sa.text('false')` (was `'true'`) — this is both
     the value needed to satisfy `NOT NULL` on the `ADD COLUMN` for
     pre-existing rows AND this column's correct, final, fail-safe
     steady-state default, so no separate "temporary then final
     default" step is needed.
   - The migration additionally `ALTER COLUMN ... SET DEFAULT false`
     for `is_verified` and `has_verified_nutrition` — the two columns
     `cf5522508f9a` added with `server_default=true`. This only changes
     what a FUTURE bare `INSERT` omitting the column receives; it does
     not touch any already-written row's value (the conservative
     backfill, unchanged from V11, still runs first and is unaffected).
     `cf5522508f9a` itself is deliberately left untouched — it already
     shipped/was reviewed in an earlier round; amending the still-open
     `a1b2c3d4e5f6` is the minimal correct fix.
   - `downgrade()` restores the EXACT pre-`a1b2c3d4e5f6` schema: both
     columns' defaults are `ALTER`ed back to `true` (matching
     `cf5522508f9a`'s original values) before `has_verified_ingredients`
     is dropped.

**Verified against a real, disposable PostgreSQL 16 container**:
upgrade to `cf5522508f9a` (confirming both pre-existing columns'
defaults were `true`), seeded a fully-verified legacy row, an
incomplete legacy row, and a row with a non-empty PLACEHOLDER
`raw_ingredient_text` (`"null"`) whose flags were already `false` —
then `upgrade head` and confirmed: both new defaults are `false`; the
backfill reproduced each row's completeness exactly as V11 already
verified (the placeholder-text row's `has_verified_ingredients` stayed
`false`, never "upgraded" based on the non-empty text); and a bare
`INSERT` omitting all three verification columns entirely produced a
fully UNVERIFIED row. Then `downgrade` to `cf5522508f9a` and confirmed
both defaults were restored to `true` exactly, the new column was
dropped, and every row's data was preserved throughout — then
`upgrade head` again, confirming idempotent re-application. The opt-in
concurrent-INSERT-race test
(`tests/postgres/test_concurrent_enrichment_postgres.py`, see 11.6) was
re-run against this final schema and still passes.

`tests/unit/test_nutrition_ingredients_split_migration.py` gained a
structural test
(`test_migration_flips_the_two_pre_existing_verification_columns_to_fail_safe_defaults`)
asserting both the `upgrade()`-side `ALTER COLUMN ... SET DEFAULT
false` calls and the `downgrade()`-side reversal back to `true` are
present in the migration source, without executing DDL (see that
file's own docstring for why — same convention as
`test_barcode_discovery_migration.py`).

### 11.12 Partial analysis for an incomplete result (V12)

Review round 5, finding 3: an ingredients-only label photo is the
NORMAL output of the Android "Ingredient Label" tab whenever only the
ingredients list (not the nutrition panel) was in frame — but the
`404`/`labelScanRequired` response for it discarded the genuinely
useful, already-verified ingredient analysis, giving the client nothing
but a bare "scan again" signal. `_label_scan_required_details` (used by
`analyze_barcode`, `_finalize_barcode_enrichment`, and
`_finalize_standalone_label_analysis` — every place this response is
raised) now additively includes:

```jsonc
{
  "labelScanRequired": true,
  "reason": "...",
  "discoveredIdentity": { "barcode": "...", "productName": "...", "brand": "...", "imageUrl": "..." },
  "suggestedAction": "...",
  // Additive (V12) -- a client that only inspects `labelScanRequired`
  // (the pre-existing contract) is completely unaffected.
  "analysisComplete": false,
  "healthScoreAvailable": false,
  "healthScore": null,           // explicitly null, NEVER 0 -- 0 would read as a real (very poor) score
  "nutritionScanRequired": true, // true iff has_verified_nutrition is false on this row
  "ingredientsScanRequired": false, // true iff has_verified_ingredients is false on this row
  // Present ONLY when has_verified_ingredients is true for this row --
  // omitted (not an empty list) when there is no genuinely trustworthy
  // partial evidence to return at all (e.g. a full Gemini+fallback
  // failure, where even the "ingredients" would be tokens from a
  // hardcoded placeholder/error string, never real label content).
  "ingredients": [ /* same shape as the success response's own `ingredients` array */ ]
}
```

Design notes:

- **Per-group, not assumed-direction**: `nutritionScanRequired`/
  `ingredientsScanRequired` are computed independently from
  `product.has_verified_nutrition`/`has_verified_ingredients` directly
  — a barcode discovery can be missing either group (or both), not only
  nutrition, so the response never assumes which one.
- **Never fabricates**: `ingredients` is only ever attached when the
  CALLER has already established `has_verified_ingredients` is `true`
  for this exact row — this function does not re-derive or guess
  trustworthiness itself, it only surfaces evidence already judged
  trustworthy elsewhere (`_ingredients_group_is_complete`).
- **Same shape, not a new DTO**: each entry is built by a small,
  hand-written `_ingredient_out_dict` helper that mirrors
  `app.schemas.ingredient.IngredientOut`'s exact camelCase field names
  — NOT by importing that Pydantic schema into the service layer
  (services stay schema-free, see section 2 of `CLAUDE.md`) and NOT a
  truncated summary. An Android client can render this list with the
  exact same model/adapter it already uses for the success response's
  `ingredients` array.
- **`analyze_barcode`** additionally now fetches and attaches the
  persisted product's actual ingredients (via
  `fetch_ingredients_for_product`) whenever `has_verified_ingredients`
  is true for a `labelScanRequired` it raises — e.g. a barcode
  discovery that supplied real `ingredients_text` but no `nutriments`
  now hands that back too, not just its identity.
- **No OpenAPI schema change**: `error.details` was already untyped
  `Any` (see `app.core.exceptions.AppError`/`app.main._error_envelope`)
  — none of these routes' error responses are declared via
  `response_model`/`responses=`, so extending this dict moves nothing
  in the generated schema. Confirmed byte-for-byte identical against a
  pinned-dependency regeneration (see 11.13).
- **No fake scan-history entries**: `scan_history_repository.insert`
  is only ever called AFTER the completeness gate in every
  `_finalize_*` function — an incomplete/partial result never leaves
  behind a scan-history row carrying a placeholder score.

### 11.13 Tests

`tests/unit/test_language_detection.py`, `tests/unit/test_label_language.py`,
and `tests/integration/test_label_barcode_enrichment.py` cover: backward
compatibility (no barcode, and a placeholder barcode value); unknown
barcode + label scan creating one canonical product; an incomplete
discovered product being enriched in place (meaningful identity
preserved, placeholders filled); a subsequent plain barcode scan
resolving from the local database with discovery never invoked;
UPC-A/EAN-13 alias enrichment never duplicating; repeated enrichment
being idempotent; verified data never being overwritten by lower-
confidence OCR; incomplete nutrition staying `labelScanRequired`;
invalid-barcode structured errors; English preferred over another-
language duplicate; Bulgarian preferred over another-language
duplicate; mixed English/Bulgarian deduplication (including a
pure-Bulgarian label, which previously skipped the rebuild step — V9
finding 6); other-language-only translation to canonical English;
translation failure/low confidence returning a controlled error with
nothing persisted; E-numbers/percentages/quantities/units surviving
translation; no raw image bytes ever being persisted; a provenance
write failure or a scan-history write failure each rolling back the
*entire* attempt, product row included (V9 finding 3); an incomplete
enrichment never stamping a misleading `last_verified_at` while a
verified one does (V9 finding 7); NaN/Infinity/negative/out-of-range/
boolean/placeholder nutrition values asserted safe at the DATABASE
level (not merely `has_verified_nutrition`), including that an
individually-rejected field never overwrites an existing safe value
from an earlier attempt (V10 finding 1); invalid/boolean NOVA group
values persisting as the safe `0` sentinel without skewing the Health
Score, and a real NOVA 4 classification still taking its full
deduction (V10 finding 3); and explicit allergens persisting while an
unknown/absent answer never persists as `"None"`, including that a
previously-known allergen list survives a later unknown answer (V10
finding 3).

`tests/postgres/test_concurrent_enrichment_postgres.py` (opt-in, see
11.6) proves the concurrent-INSERT guarantee against real, separate
PostgreSQL connections.

`tests/unit/test_ocr_normalizer.py` and `tests/unit/test_gemini_image_parser.py`
gained regression tests for two supporting fixes this feature surfaced:
non-Latin synthetic-ingredient names no longer collide on the same id
(`ocr_normalizer.create_synthetic_ingredient`), and
`gemini_image_parser.nutrition_fields_present` correctly rejects
booleans/NaN/Infinity/negative/out-of-range/non-numeric values, not
merely whatever `float()` doesn't raise on (V9 finding 2) — alongside
dedicated dietary-flag safe-default tests (missing/null/malformed/true/
false, V9 finding 1). `test_language_detection.py` and
`test_label_language.py` gained false-positive fixtures for German,
French, Italian, Spanish, Romanian, Russian, and Ukrainian text, a
Bulgarian-with-`ь` fixture, a single-ambiguous-token fixture, and
adversarial translation tests (changed/omitted E-numbers, altered
percentages/units, non-English output despite a high self-reported
confidence, extra JSON fields, `NaN` confidence, and prompt-injection
phrasing embedded in OCR input) — V9 findings 4–5. `test_label_language.py`
additionally gained multiset-equality regression tests: an invented
extra E-number/number rejected (not just a removed one), a lost
duplicate occurrence rejected, valid reordering with an identical
multiset accepted, a comma/dot decimal pair recognized as the same
value, and the `12%` percentage-token regex-backtracking fix — V10
finding 2. `test_gemini_image_parser.py` gained `label_field_validity`
per-field-independence tests, "rejected value never persisted, even
alongside an individually-valid field" tests, and NOVA-group/allergens
parser tests (valid 1-4, invalid boolean/string/float/negative/zero/
above-4 all reduced to the safe sentinel; missing/null/empty-array/
malformed-type allergens all reduced to `""`, an explicit list
persisted deduplicated) — V10 findings 1 and 3.

**V11 (PR #9 review round 4) new coverage**, all in
`tests/integration/test_label_barcode_enrichment.py` unless noted:
barcode-sourced verified nutrition completed by a LATER ingredients-
only label photo, and the reverse (ingredients-first, nutrition-later);
two separate label-image scans whose complementary evidence (one
supplies only ingredients, the other only nutrition) together complete
a product neither scan alone would have; untrusted/rejected later
evidence (a failed Gemini call) never erasing an already-verified
group nor fabricating the still-missing one; neither evidence group
alone ever producing a Health Score; a subsequent plain barcode lookup
returning the now-completed product without triggering discovery;
UPC-A/EAN-13 alias enrichment still converging cumulative evidence onto
one row; repeated calls staying idempotent once complete — finding 1.
Standalone `/scan/label-image`: English/Bulgarian preference and
other-language translation applied the same as the barcode-linked
path; a `null` nutrition field correctly returning `labelScanRequired`
instead of a fabricated score; the original (pre-translation) OCR text
preserved via provenance; no raw image bytes persisted. Standalone
`/scan/ocr-text`: the same English/Bulgarian preference applied to its
text, while confirming it still always succeeded (never gated) and a
product it created remained resolvable via a later barcode lookup —
finding 2. **Both of these `/scan/ocr-text` tests were REWRITTEN in
V12** (the always-succeeds/never-gated contract they pinned was
removed — see below). `tests/unit/test_food_analysis_discovery_bridge.py` updated
for `_to_analyzed_data_from_discovery`'s new two-boolean return (
`nutrition_known`/`ingredients_known` independently, not one combined
`is_complete`), including that either alone still marks its own field
correctly while the combined gate stays `False`.
`tests/unit/test_nutrition_ingredients_split_migration.py` covers the
new migration's structural properties (parent revision, single head,
upgrade/downgrade present, expected DDL/backfill) the same way
`test_barcode_discovery_migration.py` covers `cf5522508f9a`; the real
PostgreSQL upgrade → downgrade → upgrade cycle (including a backfill
check against rows seeded at the OLD schema) was run manually against
a disposable container — see the PR/final report for the transcript.

**V12 (PR #9 review round 5) new coverage:**

- **Finding 1 (removed `always_verified`)**:
  `test_standalone_ocr_text_returns_partial_ingredients_without_health_score`
  and `test_standalone_ocr_text_gemini_success_still_does_not_verify_nutrition`
  (`test_label_barcode_enrichment.py`) plus
  `test_scan_ocr_text_returns_partial_analysis_without_health_score`
  (`test_scan_flow.py`) pin standalone `/scan/ocr-text`'s new baseline
  contract: `404`/`labelScanRequired` with verified ingredients
  attached but `nutritionScanRequired: true`, even when Gemini's own
  JSON call succeeds. `test_ocr_text_with_barcode_then_label_image_completes_the_product`
  covers the "later trustworthy nutrition evidence completes the same
  barcode-linked product" case explicitly. The three
  `test_barcode_contract_change.py` tests that used to seed a "known
  product" via a single `/scan/ocr-text` call were updated to combine
  `/scan/ocr-text` (ingredients) + `/scan/label-image` (nutrition) for
  the same barcode instead — a `_seed_known_product` helper documents
  why, and reproduces the exact same hand-derived Health Score
  (`fallback_local_analysis`'s deterministic keyword heuristic) the
  original single-call version pinned. `test_scan_flow.py` and
  `test_barcode_discovery_flow.py` gained a similar `_seed_verified_product`
  helper (via `/scan/label-image`) for the same reason, updating six
  further pre-existing tests whose "known product" fixture no longer
  exists as a `200` under the new contract.
- **Finding 2 (fail-safe defaults)**: see 11.11's own test-coverage
  paragraph above (migration structural test, plus the real-Postgres
  bare-INSERT verification).
- **Finding 3 (partial analysis)**:
  `test_standalone_label_image_null_nutrition_field_is_not_fabricated_verified`
  extended with the new `analysisComplete`/`healthScoreAvailable`/
  `healthScore`/`nutritionScanRequired`/`ingredientsScanRequired`/
  `ingredients` assertions (the "ingredients-only photo" case);
  `test_standalone_label_image_incomplete_response_still_preserves_provenance_and_no_image_bytes`
  extends the existing success-path provenance/no-image-bytes
  guarantees to the incomplete path;
  `test_neither_standalone_incomplete_response_creates_a_scan_history_entry`
  proves neither incomplete standalone path leaves a scan-history row
  behind; `test_barcode_nutrition_plus_partial_ingredients_completes_normally`
  proves the "already-verified nutrition + this scan's genuinely
  verified ingredients" case returns the normal full `200`, never the
  partial shape, once both groups are true.

## 12. Project layout

```
app/
├── api/v1/            # FastAPI routers (one file per resource)
├── core/               # config, security (JWT), exceptions, logging, rate limiting
├── models/             # SQLAlchemy ORM models (includes product_source.py)
├── schemas/             # Pydantic request/response schemas (camelCase)
├── repositories/        # DB access layer (no business logic; includes product_source_repository.py)
├── services/            # business logic: health score, warnings, OCR, fallback analysis, orchestration,
│                         #   barcode_validation.py, barcode_discovery.py, barcode_text_safety.py
├── integrations/        # GeminiService, and barcode_providers/ (Open Food Facts, GS1, UPCitemdb adapters)
├── database/             # engine/session/declarative base
├── seed/                 # scientific ingredient seed data + loader
└── main.py               # FastAPI app factory, exception handlers, middleware
alembic/                  # migrations (Postgres-generated and -applied)
tests/
├── unit/                 # pure business-logic tests, no DB/network
├── integration/          # full HTTP request/response tests via httpx + in-memory SQLite
└── fixtures/             # shared test fixtures (incl. barcode_provider_responses.py)
scripts/
└── extract_kotlin_ingredients.py   # one-off tool used to generate app/seed/ingredients_seed.json from the original Kotlin source
docker-compose.yml / docker-compose.prod.yml / Dockerfile / docker/entrypoint.sh
```
