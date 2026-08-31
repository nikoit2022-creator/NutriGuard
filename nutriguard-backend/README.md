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

The full suite (**159 tests**) runs against an in-memory SQLite database
via `aiosqlite` — no Docker or Postgres required, and it runs in a few
seconds. This is intentional: SQLite is good enough to validate all
business logic and API behavior, while the actual deployment always
targets Postgres (see `alembic/versions/` for the Postgres-generated
migrations, authored and applied against a real local PostgreSQL 16
instance during development, not just SQLite — see section 10.7 for how
the barcode-discovery migration specifically was verified).

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

## 4. Implemented endpoints

All 11 endpoints from the API Contract, plus the two auth endpoints it specifies:

| Method | Path | Contract ref |
|---|---|---|
| POST | `/api/v1/auth/device` | 3.2 |
| POST | `/api/v1/auth/refresh` | 3.2 |
| POST | `/api/v1/scan/barcode` | 6.1 |
| POST | `/api/v1/scan/ocr-text` | 6.2 |
| POST | `/api/v1/scan/label-image` | 6.3 |
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

## 11. Project layout

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
