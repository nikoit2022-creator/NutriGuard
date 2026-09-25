# Ingredient-first label scan (issue #25)

Backend fix for GitHub issue #25, "ingredient-first label scan results
independent of product completeness". Branch
`fix/backend-ingredient-first-label-scan`. Not merged, not deployed.

| | |
|---|---|
| Baseline (exact `origin/main`) | `32bd7efc05750470da6f48eab7c4111e45db1d26` |
| Head | the commit that adds this file on `fix/backend-ingredient-first-label-scan` (a file cannot contain its own SHA; the SHA is posted with the push) |
| Scope | backend only: parser, label-image pipeline diagnostics, one retry message, tests, docs |
| Wire/schema change | none: same fields, types, status codes; OpenAPI identical; no migration |

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
confirms it. The new `labelExtraction` diagnostic (section 4) is what
distinguishes these cases on a live system.

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
   id) recognizes no ingredients, `details.reason` now reads "No
   ingredients could be read from this label. Retake the photo with the
   whole ingredient list in frame, in focus and well lit." It no longer
   claims "This product's identity was found...". Same key and shape; the
   barcode-linked and `/scan/barcode` reasons are unchanged.
3. **Diagnostics** (section 4).

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

### 3.3 Zero useful ingredients (empty/blurry) → `404` (unchanged shape)

```json
{
  "error": {
    "code": "PRODUCT_NOT_FOUND",
    "message": "Product img_1790334974640 could not be read reliably -- no ingredients were recognized.",
    "details": {
      "labelScanRequired": true,
      "reason": "No ingredients could be read from this label. Retake the photo with the whole ingredient list in frame, in focus and well lit.",
      "discoveredIdentity": {
        "barcode": "img_1790334974640",
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
      "dataSource": "label_scan"
    },
    "timestamp": 1790334974644
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
`brand: "Analyzed Brand"` and the honest `reason`. The verified nutrition
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

Updated: `tests/unit/test_gemini_image_parser.py`.
`test_missing_product_name_returns_none` pinned the defect itself and is
replaced by tests asserting the kept evidence, unchanged nutrition
validation, trimmed real names, the still-rejected empty label, and the
failure-reason vocabulary.

Commands and results (pinned image: `python:3.12-slim` +
`requirements.txt`; Python 3.12.14, fastapi 0.115.6, pydantic 2.10.4,
SQLAlchemy 2.0.36, pytest 8.3.4, pytest-asyncio 0.25.0, aiosqlite 0.20.0,
asyncpg 0.30.0, alembic 1.14.0):

| Check | Baseline `32bd7ef` | This branch |
|---|---|---|
| `python -m pytest -q` (full suite) | 590 passed, 10 skipped | **631 passed, 10 skipped** |
| `app.openapi()` vs tracked `openapi.json` | exact match | **exact match** (no regeneration needed) |
| Disposable `postgres:16-alpine`: `alembic heads` | `c6d7e8f9a0b1` (single) | `c6d7e8f9a0b1` (single) |
| `alembic upgrade head`, then `downgrade -1` / `upgrade head` cycle | OK | OK |
| `pytest tests/postgres` (`NUTRIGUARD_TEST_POSTGRES_URL` set; incl. concurrent enrichment and catalog concurrency) | 10 passed | **10 passed** |

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
  label-image scan with diagnostics enabled; check `labelExtraction`.
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
- The zero-ingredient `details.reason` now carries honest retry copy.
  Consider adding "scanned label product", "analyzed brand" and "scanned
  product" to your placeholder list (see section 7).
- The empty-ingredient `404` has no `ingredients` key. The UI cannot and
  must not repair it with fallback ingredients.
