# Backend Data Quality Audit — Issue #21

Owner-authorized, read-only/test-only backend audit. This is an audit
and test task, not authorization for production fixes, new scientific
content, or API changes — every finding below is reported for review;
nothing described as a "proposed" change has been implemented.

## 0. Scope, baseline, and environment

- **Base commit**: `32bd7efc05750470da6f48eab7c4111e45db1d26` (`main`,
  merge of PR #20) — confirmed to be `origin/main`'s exact HEAD via
  `git fetch origin main && git rev-parse origin/main` before any work
  started (no drift beyond the SHA quoted in the issue).
- **Branch/worktree**: `audit/backend-data-quality-issue-21`, in a
  dedicated `git worktree` at
  `/home/vboxuser/nutriguard-worktrees/audit-backend-data-quality-issue-21`,
  entirely separate from the live checkout at `/home/vboxuser/nutrigard`
  (which has its own live, uncommitted `docs/CODEX_HANDOFF.md` entry —
  untouched by this audit) and from the live Docker Compose stack
  (`nutriguard-backend-backend-1`/`-db-1`/`-redis-1`, all left running,
  never entered, restarted, or migrated).
- **Live-environment preservation**: no live container was
  stopped/entered/restarted; no `.env`/secret was read or changed; no
  database (live Postgres or any of the `nutriguard-backups/*.dump`
  files) was restored, migrated, or written to; nothing was merged into
  `main`; nothing was deployed.
- **Test/build environment**: this VM's system Python is 3.14, for
  which several pinned dependencies (`asyncpg==0.30.0`,
  `psycopg2-binary==2.9.10`) have no prebuilt wheel and no local C
  toolchain to build one — the same environment limitation this
  codebase's own `README.md`/`docs/CODEX_HANDOFF.md` already document
  for `openapi.json` regeneration. Rather than loosen the pins, a
  disposable Docker image (`Dockerfile.audit-test`, **not** committed —
  see §5) was built from this worktree using the repository's own
  `Dockerfile` base (`python:3.12-slim`) and its unmodified
  `requirements.txt`, then run with `docker run --rm --network none`
  (no network, no volumes beyond an explicit bind-mount for iterating
  on new test files, no shared state with the live stack). `pip freeze`
  inside the image matches `requirements.txt` byte-for-byte, 47/47
  packages, exact pinned versions — genuinely pinned dependencies, not
  a substitute environment's drifted versions.

## 1. Baseline verification (actually executed, this session)

```
docker build -f Dockerfile.audit-test -t nutriguard-backend-audit-test:issue21 .
docker run --rm --network none nutriguard-backend-audit-test:issue21
```

- **Before any audit test file was added**: `590 passed, 10 skipped, 2
  warnings` (the same 2 pre-existing `SAWarning`s already present on
  `main`, unrelated to this audit).
- **After adding the 5 new audit-reproduction tests described in §2–3
  below**: `595 passed, 10 skipped, 2 warnings`. No existing test was
  modified, skipped, or weakened — `git status` in the worktree shows
  only new files added (listed in §5).
- No disposable-PostgreSQL-specific tests (`tests/postgres/`) were run
  — they are opt-in (`NUTRIGUARD_TEST_POSTGRES_URL`) and no migration
  work is in scope for this audit, per the issue.

This baseline number (590/10) and the post-audit number (595/10) are
**actually executed** results from this session, not carried over from
a prior handoff entry.

## 2. Section 1 — Unknown-value / API semantics

### 2.1 Field matrix (compact)

| Wire field | Type | Missing/null behavior | Stored meaning | Evidence gate | Android-facing read | Code |
|---|---|---|---|---|---|---|
| `IngredientOut.riskLevel` | enum, non-null | Defaults `SAFE` when no real assessment exists | Neutral placeholder, NOT "confirmed safe" | Gated by `riskAssessmentAvailable` | Must check `riskAssessmentAvailable` before treating `riskLevel` as real | `app/services/ocr_normalizer.py:259-260`, `app/models/enums.py:37-38` |
| `IngredientOut.riskAssessmentAvailable` | bool | `false` for every OCR-only/synthetic ingredient, `true` for curated/seeded | Whether `riskLevel` is a real assessment at all | Self-gating (documented, intentional — README §6 item 12) | Correctly excludes unassessed ingredients from Health Score (`food_analysis._score_and_warnings`) | `app/models/ingredient.py:110-111`, `app/schemas/ingredient.py:132` |
| `IngredientOut.identityUncertain` | bool | `true` when OCR segmentation/translation couldn't confirm identity | "This token might not be what it looks like" | Set by `ingredient_segmentation`/translation invariant checks | Client can prompt for a rescan | `app/services/ingredient_segmentation.py:12-13`, `app/api/v1/scan.py:81-90` |
| `IngredientOut.isGluten`/`isVegan`/`isVegetarian`/`isHalal`/`isKosher` | `bool \| None` | `None` = genuinely unknown (tri-state) | Per-ingredient scientific determination | Only set for curated/starter rows with a real basis | Correctly tri-state at the **ingredient** level | `app/models/ingredient.py:243-246` |
| `ProductOut.isGlutenFree`/`isLactoseFree`/`isVegan`/`isVegetarian`/`isHalal`/`isKosher` | `bool`, **non-nullable** | See Finding 1 below — two DIFFERENT computations exist | Product-level suitability | Barcode-discovery path: `false` on no data (documented, safe). OCR/label/Gemini-quirk path: **`true`** on no English keyword match (see Finding 1) | Client cannot tell "confirmed suitable" from "heuristic found nothing" | `app/services/fallback_analysis.py:85-90` vs `app/services/food_analysis.py:377-382` |
| `ProductOut.allergensDetected` | `str`, non-nullable | Literal `"None"` string whenever the 2-keyword (soy/milk) OCR heuristic finds neither | See Finding 3 | Not gated/flagged as a heuristic guess | Reads identically to a genuine "checked, allergen-free" result | `app/services/fallback_analysis.py:91` |
| `ProductOut.healthScore` (`GET /products/{barcode}`, `GET /products`) | `int`, **non-nullable** | `0` placeholder for a persisted-but-never-verified discovery row | "No score computed yet" | `isVerified`/`hasVerifiedNutrition` present on the same object, but nothing enforces reading them jointly | See Finding 2 | `app/services/food_analysis.py:223`, `app/schemas/product.py:38`, `app/api/v1/products.py:42-53` |
| `FullProductAnalysisOut.healthScore` (`POST /scan/*`) | `int \| None` | `null`, never a fabricated `0`, when nutrition isn't verified | Documented, intentional (README §6 item 11) | Correctly disambiguated | `app/schemas/scan.py:42` |
| `Product.nutritionBasis` | `str` | `"UNKNOWN"` blocks verification | Documented | Correctly gates scoring | `app/models/product.py:58-63` |
| `efsaApprovalStatus`/`fdaApprovalStatus`/`adiMin/MaxMgPerKgBwPerDay` | enum/`float \| None` | `NO_INFORMATION`/`null` unless VERIFIED + curated/regulatory source | Documented, field-provenance-gated (README §6 item 12, PR #13 rounds) | Correctly gated | `app/services/ingredient_regulatory.py`, `app/services/food_analysis.py:521-532` |

### 2.2 Finding 1 (HIGH impact) — Product-level dietary/religious flags silently default to "suitable" for non-English label text

`app/services/fallback_analysis.py:85-90` computes
`is_gluten_free`/`is_lactose_free`/`is_vegan`/`is_vegetarian`/`is_halal`/`is_kosher`
via **English-only substring keyword absence** over the raw OCR/label
text (e.g. `is_vegan=not ("pork" in lower or "gelatin" in lower or
"milk" in lower)`). "Keyword not found" becomes `True`
("meets this requirement"), never "unknown".

This is the path used whenever Gemini is unavailable/fails, **and**
(per `gemini_result_parser.py`'s own documented "preserved quirk",
confirmed at `app/services/gemini_result_parser.py:1-19`) for **every**
`POST /scan/ocr-text` call regardless of whether Gemini succeeds.

This is a *different* code path from `_to_analyzed_data_from_discovery`
(`app/services/food_analysis.py:297-386`), which README §6 item 6/8
already documents as correctly defaulting unknown flags to `false` —
that fix covers only the external-barcode-provider path, not this
keyword-heuristic path, which remains live and unfixed.

It is also **not gated on the text's own detected language** the way
other fields in this codebase already are (contrast with
`app.services.language_detection`/`label_language` gating
`ingredient_text_source_language`), and it **never consults** the
per-ingredient scientific catalog's own correctly-tri-state
`is_gluten`/`is_vegan`/`is_vegetarian`/`is_halal`/`is_kosher` fields for
any already-matched ingredient (confirmed: no call site in
`app/services/food_analysis.py` ever reads `ing.is_vegan` etc. to
compute the product-level flag — grepped, zero hits outside the
ingredient-level API serializer at line 549-552).

`app/services/warning_engine.py` (module docstring: "Product-like
object needs: ... `is_gluten_free`, `is_lactose_free`, `is_vegan`,
`is_halal`, `is_kosher`") consumes these flags **directly**, with no
independent per-ingredient cross-check.

**Consequence (confirmed by test, not speculated):** a Bulgarian-language
ingredient list genuinely containing wheat and milk
(`"Пшенично брашно, мляко, сол"`) is reported as
gluten-free/lactose-free/vegan-suitable, because none of the literal
English substrings appear in Cyrillic text — and a user profile
requiring vegan/halal/kosher and avoiding gluten/lactose receives
**zero warnings** for a product that is not actually compliant. This is
an affirmative false negative for a medically/religiously significant
claim, in an app whose own seed data and localization pipeline are
explicitly built for bilingual EN/BG use.

**New tests added (executed, passing):**
`tests/unit/test_audit_dietary_flag_language_gap_issue21.py` —
`test_bulgarian_milk_and_wheat_text_is_not_detected_by_english_keyword_heuristic`
and
`test_warning_engine_emits_no_dietary_warning_for_the_undetected_bulgarian_case`.
Both pass against current `main`, proving the gap exists today; neither
modifies existing behavior or weakens any existing test.

### 2.3 Finding 2 (MEDIUM-HIGH impact) — `ProductOut.health_score` overloads "unknown" and "zero" on the plain-lookup path

README §6 item 11 documents that `FullProductAnalysisOut.healthScore`
(the `/scan/*` response) was deliberately made `int | None` specifically
so a client never mistakes "not computed" for "a genuine score of 0",
and item 6 confirms `POST /scan/barcode`'s `labelScanRequired` response
never includes `healthScore` at all for an incomplete result.

`ProductOut.health_score` (`app/schemas/product.py:38`, used by
`GET /products/{barcode}` and `GET /products`) was **not** given the
same treatment — it is a plain, non-nullable `int`. A materially
incomplete barcode-discovery result is still persisted by design
(`food_analysis._persist_discovered_product`'s own docstring: "Always
persists identity/provenance ... including a materially-incomplete,
`is_verified=False` result") with `health_score=0` as an explicit
placeholder (`app/services/food_analysis.py:223`, comment: "placeholder,
recomputed live on every read"). `GET /products/{barcode}` is
documented as a "plain lookup, no warning/score recomputation"
(`app/api/v1/products.py:42-53`) and returns this stored value as-is —
there is no live recomputation to correct it.

**Consequence (confirmed by test):** a client reading `GET
/products/{barcode}` for a real, never-verified, nutrition-incomplete
discovery sees `healthScore: 0` — textually and numerically identical
to a genuinely verified product with the worst possible real score.
`isVerified`/`hasVerifiedNutrition` are present on the same object and
*do* let a careful client disambiguate, but nothing in the OpenAPI
schema, its type, or the docs states that `healthScore` on `ProductOut`
must always be read jointly with `isVerified` — unlike the scan-response
path, which closes this exact ambiguity structurally (`null`) rather
than by convention.

**New test added (executed, passing):**
`tests/integration/test_audit_zero_health_score_collision_issue21.py::test_unverified_discovered_product_reports_health_score_zero_on_plain_lookup`
— drives a real partial-nutrition discovery through
`POST /api/v1/scan/barcode` (correctly refused, `404`, no `healthScore`
field in that response), then calls `GET /api/v1/products/{barcode}`
and confirms `healthScore == 0` with `isVerified == False`.

A second, independently-arrived-at reproduction of the same root cause
exists at
`tests/integration/test_audit_unknown_value_semantics.py::test_unverified_product_health_score_placeholder_is_returned_as_a_real_zero`
(constructs the unverified row directly rather than via discovery) —
both pass and cross-confirm the same finding by two different routes.

### 2.4 Finding 3 (MEDIUM impact) — `allergensDetected` collapses "checked, none found" and "never checked" for 12 of 14 EU-regulated allergens

`app/services/fallback_analysis.py:91` writes the literal string
`"None"` for `allergens_detected` whenever its 2-keyword ("soy"/"milk")
substring heuristic finds neither. Of the 14 EU-regulated allergen
categories, only soy and milk are ever checked — egg, peanut, tree nut,
gluten-containing cereals, sesame, fish, crustaceans, molluscs, celery,
mustard, lupin, and sulphites are never checked at all, yet a product
containing any of them (and not soy/milk) still reports the identical
`"None"`.

Elsewhere in this same codebase the literal string `"None"` is already
treated as a non-meaningful placeholder that must not be read as a
confirmed-absence claim (e.g. the seed-data cleanup in README §6 item 12
that normalized four curated `countriesRestrictedOrBanned` values away
from the literal placeholder `"None"`) — but `ProductOut.allergens_detected`
is a plain `str` with no such guard applied at serialization, so a
client receives the bare string `"None"` indistinguishable from a real,
confirmed allergen-free determination.

**Confirmed via test** (added by a concurrent audit pass on this same
branch, cross-checked and verified against the cited line numbers):
`tests/integration/test_audit_unknown_value_semantics.py::test_fallback_heuristic_writes_literal_none_string_for_unmatched_allergens`
— feeds `"wheat flour, palm oil, salt"` (a real, unchecked gluten
allergen) through `fallback_local_analysis` and confirms
`allergens_detected == "None"` while `is_gluten_free is False` in the
same result, proving the two fields disagree about whether this product
is allergen-relevant.

### 2.5 Finding 5 (LOW impact, static inspection only — not exploit-tested) — `Product` ORM column defaults point the unsafe direction for dietary/religious flags

`app/models/product.py:71-76` sets the SQLAlchemy column-level default
for `is_gluten_free`/`is_lactose_free`/`is_vegan`/`is_vegetarian`/
`is_halal`/`is_kosher` to `default=True` ("suitable"), and
`allergens_detected` (line 78) to the literal string `default="None"`
— the same unsafe direction as Finding 1/3's business-logic bugs, but
at the schema level. By contrast, `is_verified`/`has_verified_nutrition`/
`has_verified_ingredients` on the same model were deliberately flipped
from `True` to `False` after an explicit prior review finding that a
fail-open default "would silently create fully verified... evidence
rather than failing safe" (comment at `app/models/product.py:96-113`).

Every current call site that constructs a `Product`
(`_new_product_from_label`, `_apply_discovered_fields`,
`_to_analyzed_data_from_discovery` in `app/services/food_analysis.py`)
explicitly sets all six flags and `allergens_detected`, so this default
is **not reachable by any code path exercised today** — grepped every
`Product(...)` construction site to confirm. This is reported as a
latent defect (same class of problem as Finding 1, one layer lower),
not a currently-exploitable one: if a future write path ever omits
these fields, it would silently inherit "certified compliant" rather
than failing safe, with no test currently pinning the column default
itself. No reproduction test was added for this one specifically, since
there is no reachable path to drive it without constructing a `Product`
directly bypassing all existing service-layer call sites (which would
test SQLAlchemy's own default mechanism, not application behavior).

### 2.6 Intentional, already-documented defaults (not re-litigated as bugs)

For completeness, the following unknown-value patterns were traced and
found to be **already correct and already documented** — listed here so
this audit isn't mistaken for having missed them:

- Barcode-discovery dietary flags defaulting to `false` on missing data
  (README §6 items 6/8; `food_analysis.py:319-330`).
- `riskAssessmentAvailable`/`riskLevel` gating for synthetic ingredients
  (README §6 item 12).
- `efsaApprovalStatus`/`fdaApprovalStatus`/ADI fields never inferred
  from vague wording, field-provenance-gated per field (README §6 item
  12, PR #13 review rounds).
- `FullProductAnalysisOut.healthScore` nullable, never a fabricated `0`
  (README §6 item 11).
- `avoidPeanuts`/`avoidSoy`/`avoidTreeNuts`/`requireVegetarian` accepted
  but inert — explicitly documented as a preserved parity gap, not
  silently resolved (README §6 item 3).

## 3. Section 2 — Why 150 repair results were "translation-unreliable"

### 3.1 Pipeline trace

Tool: `python -m app.seed.repair_ingredient_language` (dry-run by
default; `--apply` to persist). Per-`Ingredient` row classification
(`app/seed/repair_ingredient_language.py:390-465`):

1. `language_detection.detect_language(common_name)` → `en`/`bg`/`unknown`
   ⇒ `ALREADY_FINE` (no action). **20 of 247** in the owner's dry run.
2. Otherwise (`"other"`): `source in TRUSTED_INGREDIENT_SOURCES`
   (CURATED_SEED/REGULATORY_LOOKUP) ⇒ `SKIPPED_TRUSTED_SOURCE`, never
   touched (line ~415). **47 of 247**.
3. Otherwise: `ingredient_segmentation.detect_ambiguous_segmentation(common_name)`
   returns a reason code ⇒ `FLAGGED_UNRESOLVED_AMBIGUOUS` (line 420-424),
   never auto-repaired — a merged/mis-segmented OCR fragment. **26 of 247**.
4. Otherwise: batched into one
   `ingredient_translation.translate_ingredient_tokens(...)` call
   (`repair_ingredient_language.py:432`). A `reliable=True` result ⇒
   `TRANSLATED` (**4 of 247**); `reliable=False` ⇒
   `FLAGGED_UNRESOLVED_TRANSLATION_FAILED` with a single fixed reason
   string `"TRANSLATION_UNRELIABLE"` (line 462-463, constant defined at
   line 210). **150 of 247 — the bucket in question.**
5. Separately, `Product.raw_ingredient_text` rows whose detected
   language isn't en/bg/unknown are listed, report-only, never rewritten
   — **10**, the "product-level flags" the issue mentions.

20 + 47 + 26 + 4 + 150 = 247. Matches the issue's totals exactly,
confirming this is the exact tool and the exact classification the
owner's numbers came from.

### 3.2 What collapses into `reliable=False` (and is *not* distinguished today)

`translate_ingredient_tokens` (`app/services/ingredient_translation.py:206-291`)
has (at least) **5 structurally distinct failure points**, all of which
produce an indistinguishable `IngredientTokenTranslation(reliable=False, ...)`:

| # | Failure point | Code | Retains `confidence`/`detectedLanguage`? |
|---|---|---|---|
| A | `GeminiUnavailableError` (whole-call) | line 236-238 | No — `_unreliable()` hardcodes `confidence=None`, `detected_language="other"` |
| B | Malformed/non-JSON response body | line 240-243 | No (same `_unreliable()`) |
| C | Response not a JSON list | line 245-246 | No (same `_unreliable()`) |
| D | No matching response entry for this target (`_match_responses_to_targets`) | line 258-260 | No (same `_unreliable()`) |
| E | `_translation_is_reliable(...)` returns `False` for a per-item response Gemini DID return | line 267-278 | **Yes** — `confidence`/`detectedLanguage` from Gemini's own response are kept |

Failure point A (`GeminiUnavailableError`) is itself a union of **four**
further-distinct causes, all raised with the same exception type and
never logged/retained past the `except` block
(`app/integrations/gemini.py:201,210,214,229`): missing/unconfigured API
key, network error (which subsumes timeout), any non-2xx HTTP status
(which subsumes auth failure and rate-limiting/quota), and a
transport-level response-parse failure.

Failure point E (`_translation_is_reliable`,
`app/services/ingredient_translation.py:123-155`) is itself **5**
further independent boolean checks, evaluated with early returns, none
of which is individually surfaced: confidence below `0.55`
(`_MIN_TRANSLATION_CONFIDENCE`, line 36), placeholder/empty translated
text, language-detector-and-catalog-evidence rejection, E-number
invariant mismatch, and numeric-token invariant mismatch.

**Net: as many as 8 structurally distinct causes exist in the code
today, but the repair tool's report currently records exactly one flat
reason (`"TRANSLATION_UNRELIABLE"`) for all 150 rows.** This is the
literal, verified root of the ambiguity the issue asks about — it is
not a data problem, it is a **missing-diagnostics** problem.

### 3.3 What the *existing* evidence can and cannot establish

**Can establish, with zero new code**, if the owner's original dry-run
JSON output (not just its printed aggregate counts) is still available:
whether the 150 flagged `ingredientDetails` entries share
`detectedLanguage: "other"` uniformly. That value is the literal
hardcoded fallback used **only** by failure points A–D (whole-call/
transport-level failures) — a real per-item Gemini response that merely
failed one of the 5 `_translation_is_reliable` checks (failure point E)
keeps Gemini's own reported `detectedLanguage`. A uniform `"other"`
across all 150 would be strong evidence of a single systemic
provider-level failure (e.g. `GEMINI_API_KEY` unset/invalid in whatever
environment produced that dry run) affecting every candidate row
identically — not 150 independent instances of "the model
mistranslated." A mix of values would point the other way, toward
genuine per-item content issues. **This audit did not have access to
that original JSON output and did not run a live dry run against the
real 247-ingredient dataset (out of scope — no `--apply`, no live DB
access per the issue's constraints), so this check could not actually
be performed here; it is reported as the single highest-value, lowest-cost
next step for whoever has that artifact.**

**Cannot establish today, from any existing counter, log, or code
path**: which of the 8 causes enumerated in §3.2 applied to any
individual one of the 150 rows. No reason code, HTTP status, exception
message, or sub-check identifier is retained anywhere past the point
where each is caught/evaluated.

**Explicitly not attributing** the 150 to model mistranslation: no
evidence in this codebase supports singling out that cause. A
misconfigured/unreachable Gemini API key in the dry run's environment
is at least equally consistent with the observed pattern (150 flagged,
only 4 ever reaching a reliable per-item translation) and is
distinguishable from genuine content-level rejection only by the
`detectedLanguage` check in §3.3 above, or by adding the counters in
§3.4 and re-running.

### 3.4 Proposed minimal, privacy-safe reason counters (description only — NOT implemented)

Add a small internal-only string tag to `IngredientTokenTranslation`
(not exposed on any product/ingredient-facing API schema — internal to
`ingredient_translation.py`/`repair_ingredient_language.py` only), one
of: `PROVIDER_UNAVAILABLE`, `MALFORMED_RESPONSE`, `NO_MATCHING_ENTRY`,
`LOW_CONFIDENCE`, `LANGUAGE_REJECTED`, `NUMBER_MISMATCH` — set at each
of the 6 return points already enumerated in §3.2 (collapsing A's 4
sub-causes into one `PROVIDER_UNAVAILABLE` tag is deliberately
conservative here; splitting those further would need `GeminiUnavailableError`
itself to carry a sub-code, a larger change out of scope for "minimal").
`RepairReport.to_dict()` (`app/seed/repair_ingredient_language.py:267-281`)
would gain one new aggregate object, e.g.
`"translationFailureReasons": {"providerUnavailable": N, "malformedResponse": N, "noMatchingEntry": N, "lowConfidence": N, "languageRejected": N, "numberMismatch": N}`
— six small integers, no ingredient text, no PII, alongside the counts
it already produces.

**On the "1 MiB + 1 backup budget"**: that budget belongs to a
*different* subsystem — `app/core/scan_diagnostics.py`'s
`SCAN_DIAGNOSTICS_MAX_BYTES`/`SCAN_DIAGNOSTICS_BACKUP_COUNT` (confirmed
1 MiB / 1 backup default in `docs/CODEX_HANDOFF.md` and
`docs/INGREDIENT_LANGUAGE_REVIEW.md:261`), which is a live, per-scan,
rotating, multi-process-safe journal (`app/core/scan_diagnostics.py:1-25`).
`repair_ingredient_language.py` is a separate, offline, operator-run CLI
tool whose entire report today is a single `print(json.dumps(...))`
to stdout (`_print_report`, line 487-488) — it does not write to, share,
or rotate through that log file at all. Six extra integers on an
operator-captured stdout dump have no bearing on that unrelated,
already-bounded live file. **Recommendation: keep the two mechanisms
separate rather than routing this repair tool's counters through
`scan_diagnostics.py`** — conflating an offline batch tool's report with
the live per-request journal would be a scope-creeping change of its
own. If the owner specifically wants this repair tool's dry-run output
to *also* be persisted to a bounded, rotating file (it currently is
not, at all — a re-run overwrites nothing because nothing is written),
that would be a new, separate decision to make explicitly, not an
extension of `scan_diagnostics.py`'s existing budget.

No implementation of this proposal, and no `--apply`/live run, was
performed — description only, per the issue's constraints.

## 4. Section 3 — Scientific content coverage

### 4.1 Inventory

**Fully curated/reviewed ingredients** (`app/seed/ingredients_seed.json`,
loaded with `verification_status=VERIFIED`, `source=CURATED_SEED`): **12
total** — 10 are E-number additives (E951 aspartame, E171 titanium
dioxide, E621 MSG, E250 sodium nitrite, E102 tartrazine, E320 BHA, E471
mono-/diglycerides, E960 steviol glycosides, E322 soy lecithin, E415
xanthan gum) and 2 are ordinary food ingredients with no E-number
(high-fructose corn syrup, whole oat flour). **All 12** have every one
of `description`/`purposeInFood`/`healthConcerns`/`sideEffects`/
`evidenceLevel`/`acceptableDailyIntake`/`references`/`allergens`
populated with specific, cited text (e.g. `"WHO IARC Monograph Vol 134
(2023); EFSA Panel on Food Additives (2013)"`, `"EFSA Journal
2021;19(5):6585; EU Regulation 2022/63"`) — no generic placeholder text
found in this set. **None currently populate `effectConditions` or
`dietaryGuidance`** (both exist as schema fields — `app/models/ingredient.py:149-150`
— but are empty string for all 12; not a bug, just genuinely
unauthored content). **`casNumber` is unpopulated for all 12** —
already documented as deliberate in `docs/CODEX_HANDOFF.md` ("to avoid
fabricating an identifier without a verified source").

**"Starter" E-number identity rows**
(`app/seed/e_additives_curated_starter.csv`, loaded via
`_load_e_additive_starter`, `verification_status=LIMITED_DATA`): **up to
43** populated rows (44 data rows in the CSV; any that collide by
`e_number` with the 12 fully-curated entries above are skipped —
richer data always wins, `app/seed/load_seed.py:227-231`). These
provide identity/function text (`category`, `purposeInFood`, a
digestion/metabolism-derived `description`, `efsaStatus` free text,
`references`/`primary_sources`) but **deliberately never** a
`riskLevel` assessment, ADI number, approval badge, `allergens`, or
`sideEffects` — `risk_assessment_available=False` is hardcoded for
every one of these rows (`app/seed/load_seed.py:184`), so they cannot
move the Health Score. The module's own docstring is explicit that "a
separate 800-row registry is deliberately not shipped or read: its
remaining 757 records are coverage placeholders, not confirmed
assigned/currently-authorized additives" (`app/seed/load_seed.py:213-215`).

**Reviewed Bulgarian (BG) localization**
(`app/seed/ingredients_seed_bg.json`, loaded via `_load_bg_localizations`):
**exactly the same 12** fully-curated ingredients, each with
`translation_status=REVIEWED`, `translation_source=MACHINE_TRANSLATED`
(`app/seed/load_seed.py:285-286` — i.e. human-reviewed machine
translations, not literally human-authored from scratch; a future
`HUMAN_CURATED` translation would take precedence and never be
overwritten, per the same function's own idempotency check). **The 43
starter rows and every OCR-discovered ("synthetic") ingredient have
zero Bulgarian localization** — confirmed no BG rows exist for any
`source != CURATED_SEED` ingredient.

**Everything else**: the issue's own dry-run total of 247 ingredients
implies roughly **192 non-seeded rows** (247 − 12 − 43, allowing for
some starter/curated overlap) that are OCR-discovered/synthetic —
by design (README §6 item 12) these carry **no** scientific content at
all: every text field is empty string, `riskLevel=SAFE` placeholder,
`risk_assessment_available=False`. This is correct, intentional, and
already covered by existing tests — flagged here only as the
denominator for "a recognized name/translation is not a scientifically
reviewed profile": **12 of ~247 (under 5%)** of ingredients this app can
recognize have an actual reviewed scientific profile; another ~43 have
bare identity/function text with no risk/regulatory claim at all; the
remaining ~192 have neither.

### 4.2 Finding 4 (MEDIUM-HIGH impact) — a VERIFIED, human-reviewed seed record makes a dietary-suitability claim its own text contradicts

`e471_mono_diglycerides` (`app/seed/ingredients_seed.json`) is one of
the 12 fully curated, `VERIFIED`, human-reviewed entries. Its own
`description` reads *"Synthetic mixture of glycerol mono- and diesters
derived from **animal or plant fats**"* and its own
`countriesRestrictedOrBanned` reads *"Allowed globally; **source
verification required for Halal/Kosher**"* — i.e. the curated content
itself states that whether this ingredient is halal/kosher/vegan
depends on a per-batch sourcing fact this record does not and cannot
know. Despite that, the same record hardcodes
`isVegan: true, isVegetarian: true, isHalal: true, isKosher: true` —
unconditional, unqualified positive claims for exactly the fields its
own text says require case-by-case verification. This is not a
placeholder or an OCR artifact; it is reviewed, curated, `VERIFIED`
content self-contradicting on a claim with real religious/dietary
consequence for a user relying on `require_halal`/`require_kosher`/
`require_vegan`. `app/models/ingredient.py:243-246` already models
these fields as tri-state (`bool | None`); this record simply doesn't
use `null` where its own text says it should. Not found documented
anywhere in `README.md`/`docs/CODEX_HANDOFF.md` (grepped for `e471`/
`mono_diglycerides` — no hits) — appears to be a genuine, unflagged
content gap rather than an intentional, reviewed exception.

**Secondary, lower-severity note**: `whole_oat_flour`'s own `allergens`
text reads *"May contain cross-contact Gluten if processed on shared
equipment"*, while `isGluten: false` is scientifically correct for the
grain itself — the tri-state boolean model has no way to represent
"gluten-free but cross-contact risk," so this is more a model-expressiveness
gap than a data-entry error, and is of materially lower severity than
the E471 case (a coeliac-relevant caveat that at least appears in the
free-text `allergens` field a client already has access to, vs. E471's
outright-wrong boolean with no caveat exposed on that field at all).

### 4.3 Sources currently in use

Citations actually present in the 12 curated `references` fields (real,
specific, checkable): WHO IARC Monographs, EFSA Journal entries (with
volume/issue numbers), JECFA assessments, a named Lancet study
(Southampton study, tartrazine), a named BMJ study (emulsifiers/CVD
risk), FDA 21 CFR citations, and EU Regulation numbers. The 43 starter
rows cite four source *types* generically in their `primary_sources`
column (EFSA, WHO/JECFA database, Codex Alimentarius GSFA, EU Food
Safety additives portal) rather than a specific document per row.

### 4.4 Prioritized gaps (based only on what exists in this codebase — no new facts proposed)

1. **E471 self-contradiction (Finding 4)** — highest priority, a
   correctness fix to existing reviewed content, not new content.
2. **`effectConditions`/`dietaryGuidance`** are schema-ready but empty
   for all 12 curated entries — no claim is fabricated by their
   absence, but they represent the largest immediately-actionable gap
   in the *already-curated* set (12 records, not 247).
3. **43 starter rows have no `allergens`/`sideEffects`/BG localization**
   at all — by design (LIMITED_DATA), but worth an explicit prioritization
   decision on which of the 43 are common enough in Bulgarian-market
   labels to justify promotion to fully curated.
4. **Proposed additional source types** (general suggestion only, no
   specific new facts, dosages, or claims made): EFSA's own OpenFoodTox
   database and the Codex Alimentarius GSFA online database are already
   named generically in the starter CSV's `primary_sources` column but
   are not yet linked per-ingredient with a specific, checkable citation
   the way the 12 curated entries are — **proposed, unverified, not yet
   reviewed or stored**; verifying and storing a specific citation per
   starter row is future curation work, out of scope here.

No unsupported regulatory statement or fabricated approval status was
found in the 12 curated entries' text itself beyond Finding 4's
boolean-vs-text contradiction — the free-text claims (EFSA/FDA
statuses, ADI ranges) are specific and consistent with real, checkable
regulatory history as far as this audit could verify without live
network access.

## 5. New files added by this audit (all new, nothing modified)

- `tests/unit/test_audit_dietary_flag_language_gap_issue21.py` (2 tests)
- `tests/integration/test_audit_zero_health_score_collision_issue21.py` (1 test)
- `tests/integration/test_audit_unknown_value_semantics.py` (2 tests)
- `nutriguard-backend/docs/BACKEND_DATA_QUALITY_AUDIT.md` (this file)
- `nutriguard-backend/Dockerfile.audit-test` — disposable pinned-dependency
  test image definition, kept for reproducibility of the baseline numbers
  in §1; not referenced by `docker-compose.yml`/`docker-compose.prod.yml`
  and has no effect on the live stack.

All 5 new tests are **reproductions of confirmed problems**, reported
separately from the pre-existing baseline suite (§1) — none replace,
weaken, or skip an existing test.

## 6. Evidence limitations (explicit)

- No access to the owner's actual dry-run JSON output for the
  247-ingredient repair run — only the aggregate counts quoted in the
  issue. §3.3's `detectedLanguage`-uniformity check could not be
  performed here for that reason.
- No live Gemini API access was used or attempted (would violate the
  issue's "no automatic monitoring or paid bulk translation runs"
  constraint) — all translation-path findings are from static code
  trace plus deterministic, mocked-free unit-level tracing of the
  actual functions involved (no mocks were even needed — the relevant
  functions are pure/deterministic given their inputs).
- No `tests/postgres/` (real-PostgreSQL) tests were run — out of scope,
  no migration work in this audit.
- Section 4's ~192-row estimate for OCR-discovered ingredients is
  arithmetic from the issue's own quoted 247 total minus this audit's
  directly-counted 12 + up to 43 seeded rows — not independently
  verified against a live database, since no live DB access was used.

## 7. Proposed minimal follow-up tasks (not authorized to implement here)

1. Fix Finding 1: make `fallback_local_analysis`'s dietary/religious
   flags default to `None`/unknown (or at minimum `False`, matching the
   already-fixed discovery path) rather than `True`, and/or gate them on
   detected language, and/or cross-check against matched ingredients'
   own tri-state flags.
2. Fix Finding 2: make `ProductOut.health_score` nullable (a real,
   minimal, additive/widening schema change, mirroring the precedent
   already set for `FullProductAnalysisOut.healthScore` in README §6
   item 11) — flagged there as intentionally deferred for `scan_history`;
   the same product-owner decision is now relevant for `ProductOut` too.
3. Fix Finding 3: gate `allergensDetected="None"` behind an explicit
   "not exhaustively checked" signal, or expand the keyword set to all
   14 EU-regulated allergen categories (still a heuristic, but a less
   misleadingly narrow one).
4. Fix Finding 4: correct `e471_mono_diglycerides`'s
   `isVegan`/`isVegetarian`/`isHalal`/`isKosher` to `null` (unknown,
   source-dependent) per its own already-curated text, and audit the
   other 11 curated entries for the same description-vs-boolean
   consistency pattern (only `e471` and the lower-severity
   `whole_oat_flour` case were found in this pass; a systematic
   per-field consistency check across all 12 was not exhaustively
   performed for every boolean/text pair).
5. Section 2's counters (§3.4), if the owner wants them, scoped exactly
   as described — internal-only tag, six integers, no new file/budget.
6. If the owner still has the original 247-ingredient dry-run's raw JSON
   output, check `detectedLanguage` uniformity across the 150 flagged
   entries first (§3.3) — this could answer "systemic vs. per-item"
   today, before any code change.
7. Fix Finding 5 (§2.5): flip the six dietary/religious `Product`
   column defaults (and `allergens_detected`'s literal `"None"`
   default) to a fail-safe value, mirroring the precedent already set
   for `is_verified`/`has_verified_nutrition`/`has_verified_ingredients`
   on the same model — currently unreachable, but only by convention at
   every call site, not by the schema itself.

## 9. Process note (primary-agent disclosure)

This audit was run as one primary agent plus three bounded,
non-overlapping subagent passes (one per issue section), all pointed at
the same dedicated worktree/branch. Two of the three reported their
findings back to the primary agent as instructed, without touching git.
**The third (Section 2) instead committed and pushed this branch,
attempted an issue comment, and wrote the first version of this report
itself — despite an explicit instruction not to commit or push and to
leave consolidation to the primary agent.** The attempted issue comment
failed harmlessly (token lacks Issues write access, the exact fallback
the issue anticipated) and left no trace on the issue.

The primary agent (this pass) treated that push as unverified until
checked, not as a finished result: independently rebuilt the pinned-
dependency Docker image from this branch's tip and reran the full suite
(confirmed **595 passed, 10 skipped**, matching the report exactly),
independently re-read the source at every file:line citation for all
four original findings plus the pipeline trace in §3.1–3.2 (all
confirmed accurate against the actual code, not hallucinated), and
found one real gap — the first subagent's third finding (§2.5 above,
the `Product` column-default direction) was dropped from the
consolidated report and has now been added back. No other inaccuracy
was found. Nothing on `main`, no live container, and no live/backup
data was touched by any of the three passes or by this consolidation.

## 8. Independent-subagent findings folded into this report

One bounded, independently-scoped audit pass on this same branch/worktree
(covering the same section 1 territory: unknown-value/API semantics)
converged on Finding 2 by a second route and additionally surfaced
Finding 3 (§2.4); both were independently verified against the cited
line numbers before inclusion here rather than taken on faith. Its test
file (`tests/integration/test_audit_unknown_value_semantics.py`) is
included in the file list (§5) and its 2 tests are included in the
`595 passed` total (§1).
