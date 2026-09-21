# Truthful unknown values and translation diagnostics (issue #21 follow-up)

Backend-only change. Migration `d7e8f9a0b1c2` (single Alembic head). Based on
`origin/main` = `32bd7efc05750470da6f48eab7c4111e45db1d26` (PR #20); no drift from
the previously deployed reviewed head was found at the start of this work.
Nothing here is merged or deployed, and no live data was touched.

The principle: **"we do not know" must never be encoded as a value that means
something else** (`true`, `false`, `0`, `"None"`).

## 1. Android-facing contract (exact camelCase wire shapes)

All fields below are on `ProductOut` (`product` in `POST /scan/*`, `GET
/products/{barcode}`, items of `GET /products`). Every key is **always present**;
only the value may be `null`. No field was added or renamed.

| Wire field | Type | `null` | `false` | `true` |
|---|---|---|---|---|
| `isGlutenFree`, `isLactoseFree`, `isVegan`, `isVegetarian`, `isHalal`, `isKosher` | `boolean \| null` | **Unknown / insufficient evidence.** Show no badge, no claim, no warning. | **Supported incompatibility** (positive evidence the product does *not* meet the requirement). | **Supported suitability** (an explicit provider tag or an explicit structured label claim). Never derived from the absence of a keyword. |
| `healthScore` | `integer \| null` | **No Health Score available**: the product is not `isVerified` (nutrition and/or ingredient evidence incomplete). | n/a | n/a: `0` is a *genuine computed score* (worst possible) and is preserved as `0`. |
| `allergensDetected` | `string` (unchanged type) | never `null` | n/a | `""` = **none detected OR unknown — not an allergen-free guarantee**. Otherwise a comma-separated list of allergens *positively* found/declared (`"Milk"`, `"Soy, Milk"`). The literal `"None"` is no longer ever produced. |

Related, unchanged fields that already had the right semantics:
`FullProductAnalysisOut.healthScore` (`int | null`), `IngredientOut.isGluten/
isLactose/isVegan/isVegetarian/isHalal/isKosher` (`boolean | null`, and
`isGluten`/`isLactose` mean "*contains*"), `riskAssessmentAvailable`,
`identityUncertain`, `hasVerifiedNutrition`, `hasVerifiedIngredients`,
`isVerified`.

**Health Score availability** is decided by the verified-data contract
(`isVerified` = verified nutrition **and** verified ingredients), not by the
number: the serializer forces `null` for any unverified product, so the *nested*
`product.healthScore` always agrees with the top-level `healthScore` on every
path (plain lookup, list, every scan success/partial path). A verified product's
real score — including `0` — is untouched.

**Warnings.** The dietary/religious warnings (`Gluten Violation`, `Lactose
Contained`, `Non-Vegan Product`, `Halal Compliance Alert`, `Kosher Compliance
Alert`) now fire **only on an explicit `false`**. An unknown (`null`) flag produces
no "confirmed incompatibility" warning, and `true` never does. (Reminder, existing
V13 behaviour: warnings are computed only for verified products; a standalone
`/scan/ocr-text` always returns `warnings: []`.)

### Compatibility implications for Android

* **Explicit `false` changed meaning.** It used to mean both "unknown" (provider/
  model defaults) and "incompatible". It now means only "supported incompatibility".
  Treat `false` as a confirmed "not suitable" badge and `null` as "hide".
* **The committed `main` Android parser turns `null` into `false`.** `ProductDto.
  fromJson` (`android-app/.../ScanLabelImageDtos.kt`) uses `if (json.has(k))
  json.optBoolean(k) else null`; with `org.json`, `has()` is `true` for a JSON
  `null` and `optBoolean` returns `false`. Against an Android build without the
  nullable-flag handling, a backend `null` therefore renders as "not compliant".
  This is the same parse-layer issue already documented for the ingredient flags
  (README §6 "Android nullable-dietary-flags contract check"). It is *not* worse
  than what that build showed for barcode-discovery products before (unknown was
  `false`), but for label/OCR products it replaces a false "compliant" with a false
  "not compliant". **Recommendation: release the Android nullable-flag handling
  (Codex's local changes) before or together with deploying this backend.** No
  Android file was touched here.
* `healthScore` on `ProductDto` is already `Int?` and parsed null-safely
  (`optNullableInt`); `allergensDetected` `""` is already mapped to `null`.
  Do not render `""` as "allergen free".
* Client-side warning code that reimplements `!product.isGlutenFree` (as
  `PersonalizedWarningEngine.kt` does on `main`) must use "is explicitly false".

## 2. Where each value comes from (evidence rules)

Implemented in `app/services/dietary_suitability.py` (pure, unit-tested).

* **`true` only from an explicit source value**: a barcode provider's structured
  tag (Open Food Facts `vegan`/`vegetarian`/labels `gluten-free`, `lactose-free`,
  `halal`, `kosher`), or an explicit JSON boolean in a structured label extraction.
  Never from text, and never from a matched ingredient name (lactose status,
  certification and cross-contact are not inferable from a name).
* **`false` from an explicit source value or from positive incompatibility
  evidence**: (a) a matched *curated* ingredient's own flag (`isGluten`/`isLactose`
  true, or `isVegan`/`isVegetarian`/`isHalal`/`isKosher` false; not-vegetarian
  implies not-vegan), works in any language; (b) the *legacy English keyword hits*
  (unchanged sets: `wheat|gluten`, `milk|whey|lactose`, `pork|gelatin|milk`,
  `pork|gelatin|bacon`, `pork|alcohol`, `pork`) with an explicit-negation guard so
  `gluten-free`, `milk free`, `no alcohol`, `non-alcoholic` are not read as
  presence. The keyword hit is a heuristic that can over-report (e.g. plant
  "coconut milk" → not vegan): that is why it can only ever produce the
  conservative direction.
* **`null` otherwise** — including Bulgarian, mixed-language, foreign-language,
  empty and truncated text.
* **Precedence when sources disagree** (`resolve_flags`): an explicit `false` is
  authoritative; derived evidence fills only what the source left unknown; an
  explicit `true` that positive incompatibility evidence *contradicts* (e.g. a
  model says `isVegan: true` for "gelatin, pork fat", or a curated ingredient
  says it contains gluten) becomes `null` — two conflicting claims support
  neither, and a conflict is never resolved in favour of the positive claim;
  explicit `isVegan=true` beside `isVegetarian=false` is likewise `null`. (Cost:
  a plant product whose text trips the over-reporting keyword heuristic, e.g.
  "coconut milk", loses its explicit `true` and reads as unknown.)
* **Merges never erase evidence with an unknown**: barcode+label enrichment and
  higher-confidence rediscovery overwrite a flag/allergen list only when the
  incoming value is explicit/known; an incoming `null`/`""` leaves the supported
  existing value alone. A newer explicit value does replace an older one.
  *Known trade-off (review F3):* a label re-scan replaces the stored ingredient
  text, but an unknown incoming flag leaves an older supported flag (e.g. a
  provider's `isVegan: true`) untouched, so a flag can describe evidence that is
  no longer the stored text. This follows the owner's rule ("do not overwrite
  supported existing evidence with an unknown incoming value"); any *explicit*
  contradicting value from the new scan does replace it. Resetting `true`s on
  every text replacement is a possible follow-up if the owner prefers it.
* **Allergen placeholders**: Gemini's and Open Food Facts' allergen lists are
  filtered (`clean_allergen_names`) so "None", "N/A", "null", "No allergens",
  `en:none` etc. are never stored as allergens.
* **Gemini label extraction**: only real JSON booleans count (`"true"`, `1`,
  missing, `null` → unknown). The prompt now asks for `null` when unknown and for
  `false` only on explicit contradiction (previously it asked for `false` on
  "unknown", which made `false` ambiguous). *Not verified live* (no Gemini calls
  were made); the parser is safe either way because a model that still answers
  `false` for unknown only reproduces the pre-change behaviour.
* **Curated seed**: `e471_mono_diglycerides` (whose own text says it is "derived
  from animal or plant fats" and that "source verification [is] required for
  Halal/Kosher") now has `isVegan`/`isVegetarian`/`isHalal`/`isKosher` = `null`
  (`isGluten`/`isLactose` stay `false`). The seed loader `merge`s explicit values,
  so a reload also clears the old `true`s on an already-seeded database
  (tested, including idempotency). A guard test fails if any curated row's own
  text says its animal/plant/halal/kosher status is source-dependent while a flag
  is hardcoded `true`.

## 3. Migration `d7e8f9a0b1c2` and the legacy-data policy

Schema: the six product flags and `health_score` become nullable;
`allergens_detected` stays `NOT NULL` text; ORM defaults for the flags become
`NULL` (they used to be `True`), for `allergens_detected` `""` (was `"None"`).

Legacy provenance cannot always distinguish evidence from a guess, so the policy
is deliberately conservative (executable SQL, unit-tested on SQLite and
round-tripped on real PostgreSQL 16):

| Legacy value | Kept when | Otherwise |
|---|---|---|
| flag `true` | the row's `source` is a barcode provider (`open_food_facts`, `gs1_digital_link`, `upcitemdb`) — explicit provider tag | reset to `NULL` (unsupported positive claim; includes `local`, `label_scan`, `label_scan_translated`, unrecognised sources) |
| flag `false` | the stored `raw_ingredient_text` still contains a legacy incompatibility keyword for *that* flag (case-insensitive) | reset to `NULL` (it was a default/"not stated") |
| `health_score` | the product `is_verified` (a genuine `0` survives) | reset to `NULL` (was only a placeholder) |
| `allergens_detected` `"None"`/`"N/A"`/`"null"`/… | never (placeholder set) → `""` | positive allergen names untouched |

Known limitations (documented, not fixable from stored data): (a) `source` is
only rewritten when an enrichment newly *completes* an evidence group, so a
provider-sourced row whose flags were later overwritten by an old-code label
scan that completed no group keeps `source = open_food_facts` and its guessed
`true`s survive as "provider evidence"; (b) a verified row's stored score is kept,
so a placeholder `0` left by the old code between the discovery commit and the
first scoring write (a request that died in that window) would survive — there is
no evidence such rows exist, but the surviving `0`s are not *proven* genuine.
Also: the SQL keyword re-check does not replicate the runtime negation
guard, so a legacy `false` whose only hit is a negated phrase is preserved rather
than destroyed; a provider's explicit `false` with no surviving keyword is reset
to unknown (a rediscovery/label re-scan restores it). Trustworthy data is not
indiscriminately destroyed: provider `true`s, keyword-supported `false`s, verified
scores and known allergens all survive.

**Downgrade is lossy and documented**: `NULL` flags → `false`, `NULL` score → `0`,
the `"None"` allergen placeholder is *not* restored. Consequently upgrade →
downgrade → upgrade is not an identity on data written after the first upgrade
(non-provider `true`s are reset again). **Back up before running either
direction on real data.** The migration imports no application code (frozen SQL
copies of the keyword and placeholder sets).

## 4. Translation-rejection diagnostics (owner's section 4)

Evidence limits first: the count of 150 "translation-unreliable" repair rows is an
aggregate; **`detectedLanguage="other"` is not proof of a missing API key or
transport failure**, and no root cause is asserted. The tooling now records the
*reason* for each rejection so a future dry run can say why.

Internal, closed-vocabulary reasons (`app/services/translation_rejection.py`;
never persisted, never in a public response, never change which translation is
accepted — check order is unchanged, the public signal is still
`identityUncertain=true` with `uncertaintyReason="TRANSLATION_UNRELIABLE"`):

`providerUnavailable` · `malformedResponse` · `noMatchingEntry` · `lowConfidence` ·
`emptyTranslation` · `languageRejected` · `eNumberMismatch` · `numericMismatch` ·
`unspecified` (legacy/injected translator without a reason).

`providerUnavailable` is sub-classified from a label carried by
`GeminiUnavailableError.category` (never its message): `notConfigured`, `timeout`,
`transport`, `httpAuth`, `httpRateLimited`, `httpClientError`, `httpServerError`,
`httpOther`, `unparsableResponse`, `unknown`. A schema-invalid per-entry response
is `malformedResponse` when its `originalText` is readable and matches a target,
`noMatchingEntry` otherwise.

**Repair dry run** (`python -m app.seed.repair_ingredient_language`, dry-run) adds,
additively (existing keys unchanged, per-entry `reason` still
`TRANSLATION_UNRELIABLE`): `translationFailureReasons` (all keys zero-filled; sums to
`ingredientCounts.flaggedUnresolvedTranslationFailed`),
`translationProviderFailureCategories` (sums to the `providerUnavailable` count),
`translationAttempt` (`batches`, `targets`, `reliable`, `rejected`, `outcome`), and
per-entry `translationFailureReason` / `translationProviderFailureCategory`.
No text, response, exception message or secret is included.

**Scan diagnostics** (one JSON line per scan; same `SCAN_DIAGNOSTICS_*` settings:
1 MiB per file + one backup, `flock`-serialised, fail-open — unchanged) gains
bounded counters: `ingredientTranslationBatches` (provider calls),
`ingredientTranslationPartialBatches`, `ingredientTranslationFailedBatches`,
`ingredientTranslationReasons` and `ingredientTranslationProviderFailures`
(non-zero keys only), `labelTranslationFailureReason` /
`labelTranslationProviderFailureCategory` (whole-label pass only) and
`translationOutcome` (`not_attempted|succeeded|partial|failed`, the *final*
translation outcome, distinct from the request `outcome`). Entry counts satisfy
`reliable + unreliable == attempted` and reason counts sum to the unreliable
count, so attempts, partial outcomes and the final outcome are never conflated or
double-counted. Building the fields can never change a scan response (the builder
is fail-open even for the 404/failed-request paths).

How to read a future dry run before blaming the provider: (1) a uniform
`providerUnavailable` with `notConfigured`/`httpAuth` points at configuration;
(2) `malformedResponse`/`noMatchingEntry` in a very large batch may be provider
truncation (the repair tool sends every candidate in one batch); (3) the repair
tool calls the translator without `known_normalized_names`, so a short, correct
English translation that a live scan would accept via catalog evidence can show
up as `languageRejected` in a dry run — an existing limitation, not changed here.

## 5. Observations that were **not** changed (audit-only)

* `whole_oat_flour`: `isGluten=false` while its `allergens` text warns of gluten
  cross-contact; a tri-state boolean cannot express "gluten-free but
  cross-contact risk". Needs a product decision (e.g. an explicit cross-contact
  field), not a boolean flip.
* Curated ingredient-level `allergens` text such as `"None reported"` is reviewed
  curated content, not heuristic detection; left as is.
* No "could not confirm" INFO warning is emitted for unknown flags (silent by
  design, per "unknown must not produce confirmed-incompatibility warnings").
  Whether an unknown should produce an informational warning for users with a
  matching restriction is an open product decision.
* Nutrition columns of an *unverified* product still serialise as numbers
  (`0.0`/heuristic values) gated only by `hasVerifiedNutrition`; not changed.
* `requireVegetarian`/`avoidPeanuts`/`avoidSoy`/`avoidTreeNuts` remain inert
  (documented parity gap, README §6 item 3).
* The keyword heuristic can over-report an incompatibility for plant products
  ("coconut milk"); it never over-reports suitability.
* `gemini_call_failed` logs `str(exc)` of the transport error in the ordinary
  application log (not in the diagnostics journal); unchanged.
