# Ingredient-first label scan (issue #25)

Backend fix for GitHub issue #25, "ingredient-first label scan results
independent of product completeness". Branch
`fix/backend-ingredient-first-label-scan`. Not merged, not deployed.

| | |
|---|---|
| Baseline (exact `origin/main`) | `32bd7efc05750470da6f48eab7c4111e45db1d26` |
| Commit 1 (ingredient-first fix) | `b220428006103e7fbedd50f25fc85f7749f79507` |
| Commit 2 (failure-reason contract, section 3.6) | the commit that updates this file to include section 3.6 (a file cannot contain its own SHA; the SHA is posted with the push) |
| Scope | backend only: parser, label/OCR failure reasons, diagnostics, retry messages, tests, docs |
| Wire/schema change | additive only: `error.details.failureReason` on scan failures (commit 2). No field removed or retyped, no status-code change, OpenAPI identical, no migration |

## 1. Reproduced defect vs. unconfirmed live incident

**Reproduced (synthetic, deterministic).** On baseline `32bd7ef`, with the
Gemini image call mocked and the in-memory test database, `POST
/api/v1/scan/label-image` given `rawIngredientText: "Contains: Oats,
Sugar, Citric Acid"`:

| Case | Baseline result |
|---|---|
| A: name + ingredients, no nutrition | `200`, `healthScore: null`, 3 ingredients |
| B: `productName: ""` + ingredients | `404 PRODUCT_NOT_FOUND`, `labelScanRequired: true`, identity "Scanned Label Product", **no ingredients** |
| C: `productName` missing + ingredients | same as B |
| D: `productName: ""` + ingredients + complete nutrition | same as B: ingredients **and** nutrition dropped |
| E: name + ingredients + complete nutrition | `200`, `healthScore: 90`, 3 ingredients |

**Root cause.** `app/services/gemini_image_parser.py`,
`parse_gemini_image_json_result`, returned `None` when `productName`
was empty, missing or not a string. `_run_label_image_pipeline`
(`app/services/food_analysis.py`) treats `None` as a Gemini failure. It
logs `gemini_image_json_invalid_falling_back` and runs
`fallback_local_analysis` on the hardcoded placeholder string
"Ingredients could not be extracted..." with the name "Scanned Label
Product". Those tokens are correctly marked untrustworthy, so
`has_verified_ingredients` stays false and the standalone success gate
raises the `labelScanRequired` 404 with no `ingredients`. In other words,
the ingredients **were extracted**, then **discarded** by an identity
gate. This was not an extraction failure, and nothing was lost in
serialization.

**Live incident: NOT confirmed.** The owner's screenshot shows
`labelScanRequired`, no ingredients, both missing flags and a duplicated
"Scanned Label Product". That matches this defect's exact output, but it
is equally the output of a genuine extraction failure (model
unavailable, invalid JSON, or an unreadable photo). The fallback
produces the same response in every one of those cases. No live logs,
database or traffic were accessed for this work. Whether the production
symptom was this defect is therefore unverified, and this change must
not be described as fixing the production symptom until a live scan
confirms it. On a live system, the public `details.failureReason`
(section 3.6) and the `labelExtraction` diagnostic (section 4)
distinguish these cases.

## 2. Change

1. **Parser** (`gemini_image_parser.py`). `productName` is no longer part
   of the minimum usable response; `rawIngredientText` still is. The new
   `observed_product_name()` returns the trimmed name, or `""` when it is
   missing, JSON null, a non-string, blank, or a literal placeholder
   (`is_placeholder`: "null", "unknown", "n/a", ...). No substitute name
   is ever invented. Every other field (nutrition bounds and validity,
   NOVA, dietary flags, allergens, basis, serving) is validated exactly
   as before. An empty ingredient text without a complete nutrition panel
   is still rejected.
2. **Honest retry text.** When a standalone scan (synthetic `img_`/`ocr_`
   id) recognizes no ingredients, `details.reason` no longer claims "This
   product's identity was found...". It carries text for the observed
   cause instead (section 3.6), and never guesses at photo quality. Same
   key and shape; the barcode-linked and `/scan/barcode` reasons are
   unchanged.
3. **Structured failure reason** (commit 2, section 3.6).
4. **Diagnostics** (section 4).

Not changed, deliberately: `hasVerifiedIngredients` semantics.
Trustworthy, non-empty extracted text sets it, exactly as for a named
label (case A already did this on baseline). It is not set to bypass any
gate. Also unchanged: risk, safety, dietary and translation review
status; Health Score prerequisites; evidence-preserving enrichment; the
catalog, alias and localization lookup; privacy and storage of the
original text.

## 3. JSON contract (exact camelCase, unchanged structure)

Captured from the actual API on this branch. Ingredient arrays are
shortened to one entry.

### 3.1 Ingredients without a product name → `200 FullProductAnalysisOut`

```json
{
  "product": {
    "barcode": "img_1790334974585",
    "productName": "",
    "brand": "Analyzed Brand",
    "category": "Analyzed Food",
    "imageUrl": null,
    "rawIngredientText": "Contains: Oats, Sugar, Citric Acid",
    "originalIngredientText": "Contains: Oats, Sugar, Citric Acid",
    "ingredientTextSourceLanguage": "en",
    "ingredientIds": "synth_oats_4b6cc5f2c3e5,synth_sugar_30eef85dfdd3,synth_citric_acid_d405e014d617",
    "healthScore": 0,
    "novaGroup": 0,
    "sugarGrams": 0.0,
    "sodiumMg": 0.0,
    "saturatedFatGrams": 0.0,
    "nutritionBasis": "UNKNOWN",
    "servingSize": null,
    "servingUnit": null,
    "hasArtificialSweeteners": false,
    "hasPreservatives": false,
    "isGlutenFree": false,
    "isLactoseFree": false,
    "isVegan": false,
    "isVegetarian": false,
    "isHalal": false,
    "isKosher": false,
    "allergensDetected": "",
    "timestamp": 1790334974585,
    "hasVerifiedNutrition": false,
    "hasVerifiedIngredients": true,
    "isVerified": false
  },
  "ingredients": [
    {
      "id": "synth_oats_4b6cc5f2c3e5",
      "commonName": "Oats",
      "description": "",
      "riskLevel": "SAFE",
      "riskAssessmentAvailable": false,
      "verificationStatus": "UNVERIFIED",
      "source": "OCR_HEURISTIC",
      "confidence": 0.2,
      "isGluten": null,
      "isVegan": null,
      "identityUncertain": true,
      "uncertaintyReason": "TRANSLATION_UNRELIABLE",
      "efsaApprovalStatus": "NO_INFORMATION",
      "localizations": { "en": { "commonName": "Oats", "description": "", "translationStatus": null } }
    }
  ],
  "healthScore": null,
  "warnings": [],
  "isFromDatabaseCache": false
}
```

(The ingredient object is shortened to its evidence-relevant keys; the
full `IngredientOut` key set is unchanged.) Contract points:

- `productName: ""` means identity not observed. Clients should hide the
  heading, never show a substitute.
- Standalone results always have a synthetic `img_…` barcode, never a
  fabricated real one.
- The top-level `healthScore` is `null` unless nutrition is complete.
  `product.healthScore: 0` and the `0.0` nutrition values are the
  existing baseline placeholders for a row without verified nutrition
  (identical for a named label on baseline). They are not zero-value
  evidence, and clients must read `hasVerifiedNutrition`/`nutritionBasis`.
  Making `product.healthScore` nullable is open PR #22's scope, not
  this change's.
- `identityUncertain`/`uncertaintyReason` on unmatched tokens are
  the existing uncertainty fields. `TRANSLATION_UNRELIABLE` in this
  sample comes from the test environment having no translation service.
  It is identical for a named label (see the parity test).

### 3.2 Ingredients + complete nutrition, no name → `200`

Same shape as 3.1 with `healthScore: 90` (top level and `product`),
`hasVerifiedNutrition: true`, `isVerified: true` and `productName: ""`.
The Health Score is NutriGuard's own score, not an official Nutri-Score.
It is computed only because its real prerequisites (complete verified
nutrition + ingredients) hold; product identity is not one of them.

### 3.3 Zero useful ingredients (empty extraction) → `404` (unchanged shape + `failureReason`)

```json
{
  "error": {
    "code": "PRODUCT_NOT_FOUND",
    "message": "Product img_1790341390291 could not be read reliably -- no ingredients were recognized.",
    "details": {
      "labelScanRequired": true,
      "reason": "No ingredient list was found in this photo. Make sure the ingredient list is in the frame and try again.",
      "discoveredIdentity": {
        "barcode": "img_1790341390291",
        "productName": "Scanned Label Product",
        "brand": "Scanned Label Product",
        "imageUrl": null
      },
      "suggestedAction": "Use POST /scan/label-image or POST /scan/ocr-text to analyze the product's label directly.",
      "analysisComplete": false,
      "healthScoreAvailable": false,
      "healthScore": null,
      "nutritionScanRequired": true,
      "ingredientsScanRequired": true,
      "dataSource": "label_scan",
      "failureReason": "EXTRACTION_EMPTY"
    },
    "timestamp": 1790341390301
  }
}
```

No `ingredients` key is present (nothing is fabricated). The
`discoveredIdentity` placeholder values are unchanged baseline behavior
and are pinned by existing tests (`test_label_image_gemini_fix.py`). See
section 7.

### 3.4 Nutrition-only panel, no name → `404`, nutrition preserved

Same shape as 3.3 with `nutritionScanRequired: false`,
`ingredientsScanRequired: true`, `discoveredIdentity.productName: ""`,
`brand: "Analyzed Brand"`, `failureReason: "EXTRACTION_EMPTY"` and the
matching `reason`. The verified nutrition
is persisted on the row (`has_verified_nutrition = true`,
`has_verified_ingredients = false`) without claiming ingredient success.

### 3.5 Barcode-linked (`barcode` multipart field)

- New barcode, nameless label: `200`, `product.barcode` is the canonical
  GTIN-13 and `productName: ""`.
- Existing product with trusted identity/nutrition: a later nameless
  capture never erases the name (`_is_meaningful_identity` gate) or the
  verified nutrition. One row per barcode.
- A later named capture fills in the missing name on the same row.
- Older clients: the `200` success and `404` partial (`details.ingredients`)
  forms are both unchanged, so existing partial-error handling keeps working.

### 3.6 Structured failure reason: `error.details.failureReason` (commit 2)

This is an additive, closed-vocabulary, content-free cause, so Android can
tell an empty extraction apart from a provider or processing problem
without parsing message text. It is set where the failure is observed (the
extraction, translation or processing stage), never inferred afterwards
from an empty result.

**Field.** `error.details.failureReason` (string, camelCase). It is present
on every one of these error responses from `POST /api/v1/scan/label-image`
and `POST /api/v1/scan/ocr-text`:

| `error.code` (HTTP) | Unchanged status? | `failureReason` values it can carry |
|---|---|---|
| `PRODUCT_NOT_FOUND` (404) | yes | `EXTRACTION_EMPTY`, `PROVIDER_UNAVAILABLE`, `PROVIDER_RESPONSE_INVALID`, `UNKNOWN` |
| `LABEL_TRANSLATION_UNRELIABLE` (422, barcode-linked label/OCR) | yes | `PROVIDER_UNAVAILABLE`, `PROVIDER_RESPONSE_INVALID`, `TRANSLATION_FAILED` |
| `AI_SERVICE_UNAVAILABLE` (503) | yes | `PROVIDER_UNAVAILABLE`, `PROVIDER_RESPONSE_INVALID`, `EXTRACTION_EMPTY`, `UNKNOWN` |
| `INTERNAL_ERROR` (500) | yes | `UNKNOWN` |

It is not added to input-validation errors (`VALIDATION_ERROR`,
`IMAGE_TOO_LARGE`, `IMAGE_UNREADABLE`, `RATE_LIMIT_EXCEEDED`), whose
`code` already names the cause. It never appears on a `200`, and
`/scan/barcode` is unchanged.

**Enum** (`app.core.exceptions.ScanFailureReason`; the complete set is pinned by a test):

| Value | Meaning (observed, never guessed) | Where it is set |
|---|---|---|
| `EXTRACTION_EMPTY` | The provider answered with a well-formed result that had no ingredient text (an empty list, or a nutrition panel only). Does **not** assert why, e.g. never "blurry". | label-image extraction stage |
| `PROVIDER_UNAVAILABLE` | The AI provider could not be reached: network error, timeout, no configured key, or a provider-side error. Applies to extraction and to barcode-linked translation. | `GeminiUnavailableError` at that stage |
| `PROVIDER_RESPONSE_INVALID` | The provider answered, but not with the required structure (not JSON, wrong shape, no string `rawIngredientText`, invalid translation payload). | parser / translation validation |
| `TRANSLATION_FAILED` | A well-formed translation was rejected: confidence below the minimum, or failed E-number/numeric invariant checks. | `label_language` |
| `UNKNOWN` | Any other or unclassified processing failure. This includes an unexpected exception (500), the local fallback crashing on `/scan/ocr-text`, a persistence race, and any failure where no stage observed a cause. | router fallback / processing sites |

**Mapping to the requested categories.**
- Observed extraction-empty: `EXTRACTION_EMPTY`.
- Provider unavailable: `PROVIDER_UNAVAILABLE`.
- Invalid provider response: `PROVIDER_RESPONSE_INVALID`.
- Translation failure: `TRANSLATION_FAILED`.
- Resolution failure: currently never a failure response (see the proposal in section 7). `RESOLUTION_FAILED` is **reserved** and is not emitted.
- Unknown: `UNKNOWN`.

**Client rules.**
- Treat an absent `failureReason` (older backend, other endpoints) and
  any unrecognized value (future additions) as `UNKNOWN`. Show neutral
  "cause not provided" wording.
- Keep branching on `error.code` and the existing keys as before. Every
  pre-existing key is unchanged; `failureReason` is added alongside them,
  and a `null` `details` becomes an object only where it now carries
  `failureReason`.
- `details.reason` is human-readable text and may change. Branch on
  `failureReason`, never on `reason`.

**Standalone `details.reason` text by cause.** The barcode-linked text is
unchanged.

| `failureReason` | `details.reason` |
|---|---|
| `EXTRACTION_EMPTY` | "No ingredient list was found in this photo. Make sure the ingredient list is in the frame and try again." |
| `PROVIDER_UNAVAILABLE` | "The label could not be analyzed because the analysis service is temporarily unavailable. Please try again later." |
| `PROVIDER_RESPONSE_INVALID` | "The label analysis returned an unusable result. Please try again." |
| `UNKNOWN` | "No ingredients could be read from this label." |

**Guarantees** (each tested on the actual HTTP JSON):
- No raw provider exception text, provider output, OCR/label text, keys
  or stack traces are ever included. Tests plant a marker in each of
  these and assert it is absent from the body.
- No failure is ever turned into a success: every status code is
  unchanged.
- An unexpected exception still returns the same `500` / `INTERNAL_ERROR`
  / "An unexpected error occurred." envelope, now with
  `details: {"failureReason": "UNKNOWN"}`. The stack trace is still
  logged server-side.

**Captured synthetic JSON** (the `details` of the 404 samples are shown
in full in 3.3; only the fields that differ are shown here):

```jsonc
// label-image, provider unreachable (e.g. timeout / no key) -> 404
"details": { "...": "same keys as 3.3",
  "reason": "The label could not be analyzed because the analysis service is temporarily unavailable. Please try again later.",
  "failureReason": "PROVIDER_UNAVAILABLE" }

// label-image, provider answered with non-JSON -> 404
"details": { "...": "same keys as 3.3",
  "reason": "The label analysis returned an unusable result. Please try again.",
  "failureReason": "PROVIDER_RESPONSE_INVALID" }

// label-image + barcode 0012345678905, empty extraction -> 404 (barcode-linked text unchanged)
"details": { "...": "same keys as 3.3",
  "reason": "This product's identity was found, but its nutrition and/or ingredient data is too incomplete for a reliable Health Score.",
  "discoveredIdentity": { "barcode": "0012345678905", "...": "..." },
  "failureReason": "EXTRACTION_EMPTY" }
```

```json
{"error": {"code": "LABEL_TRANSLATION_UNRELIABLE",
  "message": "The label text could not be translated with sufficient confidence. Please rescan a clearer, more complete label.",
  "details": {"confidence": 0.05, "minimumRequired": 0.55, "failureReason": "TRANSLATION_FAILED"},
  "timestamp": 1790341390378}}
```

```json
{"error": {"code": "AI_SERVICE_UNAVAILABLE",
  "message": "Both the AI service and the local fallback analysis failed.",
  "details": {"failureReason": "PROVIDER_UNAVAILABLE"},
  "timestamp": 1790341390388}}
```

```json
{"error": {"code": "INTERNAL_ERROR",
  "message": "An unexpected error occurred.",
  "details": {"failureReason": "UNKNOWN"},
  "timestamp": 1790341390396}}
```

## 4. Diagnostics (bounded, content-free)

The existing opt-in journal (`app/core/scan_diagnostics.py`) is
unchanged: `SCAN_DIAGNOSTICS_MAX_BYTES` = 1 MiB,
`SCAN_DIAGNOSTICS_BACKUP_COUNT` = 1, a cross-process lock, and no
images, OCR text, model output or secrets. The `/scan/label-image`
success, partial and internal-error records gain three fields:

| Field | Values | Distinguishes |
|---|---|---|
| `labelExtraction` | `extracted`, `model_unavailable`, `model_response_invalid`, `model_response_empty` | extraction-empty (and why) vs. extracted |
| `productIdentityObserved` | bool | the model actually read a name (fallback placeholder is never "observed") |
| `labelNutritionComplete` | bool | nutrition-incomplete for this attempt |

Together with the existing fields: `unresolvedIngredientCount`/
`untranslatedIngredientCount` show resolution-unresolved, and `stage` +
`errorCode: INTERNAL_ERROR` show a serialization/response failure. The
`gemini_image_json_invalid_falling_back` log event now carries the same
closed-vocabulary `reason`. These fields are never on the wire (tested).

Commit 2 adds `failureReason` (the same value as the public field) to the
failure records of both `/scan/label-image` and `/scan/ocr-text`. A
failure raised inside the analysis (translation rejection,
provider-and-fallback failure, or an unexpected exception after a
successful extraction) now keeps the `labelExtraction` outcome observed
before it. That outcome is taken from the exception itself, because no
result dict exists at that point.

## 5. Tests

New: `tests/integration/test_ingredient_first_label_scan.py` (26 cases,
all asserting actual HTTP JSON):

- The probe matrix A-E as permanent regressions.
- Null, number, "null", "Unknown" and blank names are treated as
  unobserved.
- Named/nameless ingredient parity (no verification, risk or translation
  promotion; descriptions stay empty).
- Mixed curated + unknown tokens: the curated catalog row
  (`e330_citric_acid`) is reused with its reviewed BG localization, and
  the unknown token stays `UNVERIFIED` with no fabricated BG.
- Bulgarian label via the existing aliases; third-language (German)
  label translated, with the original text retained.
- Empty/blurry label returns honest retry guidance; the nutrition-only
  panel is preserved without ingredient success.
- Barcode-linked: canonical barcode, trusted-evidence preservation,
  enrichment of the same row.
- `/scan/ocr-text` returns ingredients without identity or nutrition.
- Diagnostics for each extraction outcome, no content leak, not on the
  wire.
- Commit 2 (17 more cases, same file): exact enum vocabulary; each
  reachable `failureReason` path on real HTTP JSON, with a planted
  secret marker absent from the body:
  - `PROVIDER_UNAVAILABLE`: timeout and missing key.
  - `PROVIDER_RESPONSE_INVALID`.
  - `EXTRACTION_EMPTY`: empty and nutrition-only; standalone and barcode-linked.
  - The 503 provider-and-fallback failure, with the stage captured.
  - Four barcode-linked translation failures (422), plus the OCR translation failure.
  - The OCR fallback failure (503 `UNKNOWN`).
  - Unexpected 500 `UNKNOWN` on both endpoints, with the extraction outcome captured from the exception.
  - The `UNKNOWN` fallback for an uncaused not-found.
  - Successes carry no `failureReason`.

  Also updated: 3 existing
  `test_scan_diagnostics_endpoints.py` tests that expected the raw
  exception to propagate to the ASGI test transport. They now assert
  the `500` envelope and that the exception text is absent.

Updated: `tests/unit/test_gemini_image_parser.py`.
`test_missing_product_name_returns_none` pinned the defect itself and is
replaced by tests asserting the kept evidence, unchanged nutrition
validation, trimmed real names, the still-rejected empty label, and the
failure-reason vocabulary.

Commands and results (pinned image: `python:3.12-slim` +
`requirements.txt`; Python 3.12.14, fastapi 0.115.6, pydantic 2.10.4,
SQLAlchemy 2.0.36, pytest 8.3.4, pytest-asyncio 0.25.0, aiosqlite 0.20.0,
asyncpg 0.30.0, alembic 1.14.0):

| Check | Baseline `32bd7ef` | Commit 1 `b220428` | Commit 2 |
|---|---|---|---|
| `python -m pytest -q` (full suite) | 590 passed, 10 skipped | 631 passed, 10 skipped | **648 passed, 10 skipped** |
| `app.openapi()` vs tracked `openapi.json` | exact match | exact match | **exact match** (error envelopes are not in the schema) |
| Disposable `postgres:16-alpine`: `alembic heads` | `c6d7e8f9a0b1` (single) | same | same |
| `alembic upgrade head`, then `downgrade -1` / `upgrade head` cycle | OK | OK | OK |
| `pytest tests/postgres` (`NUTRIGUARD_TEST_POSTGRES_URL` set; incl. concurrent enrichment and catalog concurrency) | 10 passed | 10 passed | **10 passed** |

The 10 skipped in the SQLite run are the opt-in Postgres tests, which
were run separately as shown. The disposable Postgres used its own
network and container and published no ports. The live Compose stack
was not touched. No paid or bulk model calls were made; every Gemini call
is mocked.

## 6. Dependencies

- **PR #22** (`feat/backend-truthful-unknowns-diagnostics`, open, head
  `86f94f6`, same base `32bd7ef`): reviewed. **No functional
  dependency.** It does not touch the `productName` gate, and this change
  needs nothing from it. It is not included here. Both branches edit
  `food_analysis.py` and `gemini_image_parser.py` in different
  functions, so whichever merges second may need a small textual
  conflict resolution. Related but separate: PR #22 makes
  `product.healthScore` nullable (removing the `0` placeholder in 3.1)
  and makes dietary flags tri-state.
- No migration, no new dependency, no configuration change.

## 7. Limitations / follow-ups (not done here)

- **Live confirmation outstanding** (section 1). Needs one real
  label-image scan; check `details.failureReason` or `labelExtraction`.
- **Proposed, not implemented: zero-resolved ingredients are a false
  success.** Reproduced on baseline and still present: when the extracted
  ingredient text is non-empty but yields no ingredient tokens (e.g.
  `"---"`, `"..."`, `"; ; ,"`), both `/scan/label-image` and
  `/scan/ocr-text` return **`200` with `ingredients: []`** and
  `hasVerifiedIngredients: true`. On the barcode-linked path, such a scan
  also counts as a complete ingredient group, so it can replace a
  previously verified ingredient list. The honest outcome is the `404`
  `labelScanRequired` result with `failureReason: "RESOLUTION_FAILED"`
  (the reserved value), plus requiring at least one resolved ingredient
  in `_ingredients_group_is_complete`. That changes a status code and an
  evidence gate for an existing (if degenerate) input, so under the
  "coordinate before incompatible changes" rule it needs owner and Codex
  approval before implementation.
- **Placeholder identity strings remain on the wire**:
  `"Scanned Label Product"` (fallback `discoveredIdentity`, 3.3),
  `"Analyzed Brand"`/`"Analyzed Food"` (brand/category defaults), and
  `/scan/ocr-text`'s `"Scanned Product"` title. These are baseline
  values pinned by existing tests. Blanking them is a value change
  for existing clients, so it is proposed rather than done. Android
  (`PlaceholderText.kt`) treats blank as absent but does not list these
  strings.
- **Barcode-linked + short English list**: in the test environment
  (no translation service), a short list such as "Oats, Sugar" can be
  classified as non-English, and the strict barcode-linked path then
  returns `422 LABEL_TRANSLATION_UNRELIABLE`, with or without a product
  name. This is existing language-policy behavior (issues #19/#21), not
  identity gating, and it is untouched here.
- New integration tests run on SQLite (the repo's integration-test
  convention); this change adds no Postgres-specific SQL or locking.

## 8. Android integration notes (Codex)

- `productName: ""` means hide the product heading. Your existing
  `cleanOrNull()` already maps blank to null.
- Show `ingredients` from the `200` immediately, whether `healthScore` is
  null or not. Keep supporting the `404` partial `details.ingredients`
  form for older backends.
- Branch on `error.details.failureReason` (section 3.6), not on
  `reason`. Absent or unrecognized values mean `UNKNOWN`, so use your
  neutral "cause not provided" wording. Suggested user copy:
  - `EXTRACTION_EMPTY`: "no ingredient list found, include it in the frame".
  - `PROVIDER_UNAVAILABLE`: "service temporarily unavailable, try again later". Not the photo's fault.
  - `PROVIDER_RESPONSE_INVALID`: "analysis failed, try again".
  - `TRANSLATION_FAILED`: "label language could not be translated reliably".
  - `UNKNOWN`: neutral wording.
  Never tell the user the photo was blurry: nothing in the contract
  observes that.
- Consider adding "scanned label product", "analyzed brand" and "scanned
  product" to your placeholder list (see section 7).
- The empty-ingredient `404` has no `ingredients` key. The UI cannot and
  must not repair it with fallback ingredients.
