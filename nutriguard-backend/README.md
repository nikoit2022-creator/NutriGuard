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

**V4 (bug fix):** AI-derived product/ingredient text (from Gemini OCR/
image analysis) is now guarded to always be English or Bulgarian:
strengthened Gemini prompts plus a deterministic backend safety net that
scrubs "null"/"None" placeholders, blocks non-Latin/non-Cyrillic script
leakage, translates common German/French/Albanian food-label terms, and
lets Bulgarian ingredient names still match the English scientific
database by canonical name/E-number. Brand names are never translated.
Root cause, fix, and regression tests are in section 6, item 8 below.

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

The full suite (**82 tests**: 52 unit + 30 integration) runs against an
in-memory SQLite database via `aiosqlite` — no Docker or Postgres
required, and it runs in under 2 seconds. This is intentional: SQLite is
good enough to validate all business logic and API behavior, while the
actual deployment always targets Postgres (see `alembic/versions/` for
the Postgres-generated migration, which was authored and applied against
a real local PostgreSQL 16 instance during development, not just SQLite).

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
| `products` | Analyzed products — mirrors `ProductEntity` exactly, including a denormalized `ingredient_ids` text column (see section 6) |
| `scan_history` | Per-user scan log — mirrors `ScanHistoryEntity` |
| `user_health_profiles` | One row per user — mirrors `UserHealthProfile` |

Indexes: `ingredients(common_name)`, `ingredients(e_number)`,
`products(product_name)`, `products(brand)`, `devices(device_id)`,
`refresh_tokens(jti)`, `scan_history(user_id)`, `scan_history(scanned_at)`
— covering every lookup path used by the endpoints (barcode PK lookup,
ingredient search/E-number lookup, product search, per-user history
ordered by recency).

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

8. **AI-derived product/ingredient text is now guarded to be English or
   Bulgarian only (bilingual-output fix).** The Gemini prompts
   (`app/integrations/gemini.py`) now explicitly require English-or-
   Bulgarian-only output, translation of any other source language,
   preservation of the canonical English ingredient name (for scientific-
   database matching) and the E-number, and forbid placeholder text
   ("null"/"None") in place of a real value or JSON `null`. Because a
   prompt is not a guarantee, a deterministic backend safety net was
   added in `app/services/text_localization.py` and wired into
   `fallback_analysis.fallback_local_analysis`,
   `gemini_image_parser.parse_gemini_image_json_result`, and
   `ocr_normalizer.create_synthetic_ingredient` (every place an AI- or
   OCR-derived string becomes a `productName`/`brand`/ingredient
   `commonName` in the API response):
     - literal `"null"`/`"None"`/`"N/A"`/empty-string placeholders are
       replaced with `null`s (Pydantic optionals) or an appropriate
       English fallback (required fields) — never returned as the
       literal placeholder text;
     - text in a script that is neither Latin nor Cyrillic (Greek,
       Arabic, CJK, Thai, Hebrew, ...) is replaced with a safe English
       fallback rather than ever reaching the client untranslated;
     - a small, explicitly non-exhaustive glossary of common German/
       French/Albanian food-label words is translated to English;
     - Bulgarian text is preserved as-is for display, while a separate
       Bulgarian→English alias table (`search_aliases_for`) lets
       `ocr_normalizer.match_against_database` still resolve a
       Bulgarian ingredient word against the (English) scientific
       database by its canonical name/E-number;
     - `brand` is never translated (only placeholder-scrubbed), per the
       requirement that brand names are preserved verbatim.
   **Known, accepted limitation:** an unrecognised Latin-script word in
   a language this module has no glossary entry for (e.g. an unlisted
   German/French/Albanian/Italian/Spanish term) is passed through
   unchanged — a full offline machine translator is out of scope for a
   deterministic backend layer; correctness for that general case
   depends on the Gemini prompt being followed. `rawIngredientText`
   (the verbatim OCR/Gemini transcript) is intentionally **not**
   translated or filtered — it exists to show what was actually read
   from the label, not as a "name" or "descriptive value", and every
   existing test that asserts it verbatim continues to do so unchanged.
   See `tests/unit/test_text_localization.py`,
   `tests/unit/test_ocr_normalizer.py`,
   `tests/unit/test_fallback_and_gemini_parser.py`,
   `tests/unit/test_gemini_image_parser.py`, and
   `tests/integration/test_bilingual_analysis.py`.

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

## 10. Project layout

```
app/
├── api/v1/            # FastAPI routers (one file per resource)
├── core/               # config, security (JWT), exceptions, logging, rate limiting
├── models/             # SQLAlchemy ORM models
├── schemas/             # Pydantic request/response schemas (camelCase)
├── repositories/        # DB access layer (no business logic)
├── services/            # business logic: health score, warnings, OCR, fallback analysis, orchestration
├── integrations/        # GeminiService (the only Gemini-aware code)
├── database/             # engine/session/declarative base
├── seed/                 # scientific ingredient seed data + loader
└── main.py               # FastAPI app factory, exception handlers, middleware
alembic/                  # migrations (Postgres-generated and -applied)
tests/
├── unit/                 # pure business-logic tests, no DB/network
└── integration/          # full HTTP request/response tests via httpx + in-memory SQLite
scripts/
└── extract_kotlin_ingredients.py   # one-off tool used to generate app/seed/ingredients_seed.json from the original Kotlin source
docker-compose.yml / docker-compose.prod.yml / Dockerfile / docker/entrypoint.sh
```
