# Truthful unknown values and translation diagnostics (issue #21 follow-up)

Backend-only change. Migration `d7e8f9a0b1c2` (single Alembic head; amended in
place by the PR #22 review follow-up, see section 3). Based on
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
  evidence** (PR #22 review follow-up: substring keyword hits are **no longer**
  evidence). Three routes, and only these:
  1. an explicit source value (a provider's structured tag, an explicit JSON
     boolean in a structured label extraction);
  2. a **trusted** catalog row: a matched ingredient that is `VERIFIED` *and*
     from `CURATED_SEED`/`REGULATORY_LOOKUP` (`identity_uncertain` false) *and
     named by an exact ingredient entry of the text* (its common/scientific
     name or E-number -- the upstream catalog matcher links a token to a row by
     bidirectional **substring**, so "coconut milk" reaches a row named "Milk";
     that link is not identity and the row's flags are then ignored), whose
     own flag says `isGluten`/`isLactose` true or `isVegan`/`isVegetarian`/
     `isHalal`/`isKosher` false (not-vegetarian implies not-vegan). Booleans on an
     UNVERIFIED / OCR- or Gemini-observed / `LIMITED_DATA` / provenance-less row
     (including legacy rows written before the flags became nullable) are **not**
     evidence, however definite they look; a synthetic OCR ingredient carries
     none. (Today no curated seed row asserts an incompatibility, so this route
     only fires for future curated/regulatory rows; the gate exists so a stray
     boolean can never become a product claim.);
  3. **exact ingredient-entry identity in the raw text** (`derive_text_evidence`).
     The text is split into ingredient entries (sentence, comma/semicolon/colon/
     slash and parenthesis boundaries; percentages, decoration, case and hyphens
     normalised) and an entry supports a claim only if it is *exactly* one of a
     small closed set: `milk`/`whole|skimmed|skim|semi-skimmed|dried|dry|powdered|
     full-cream|pasteurised|cow's|condensed|evaporated milk`/`… milk powder|solids|
     protein|fat`, `whey`/`sweet whey`/`whey powder|protein` (→ not vegan; allergen Milk), `lactose`/`milk sugar` (→ not lactose-
     free, not vegan; allergen Milk), `pork`/`pork meat|fat|gelatin` (→ not
     vegetarian/vegan/halal/kosher), `bacon`, `gelatin(e)`/`beef|bovine|fish gelatin` (→ not vegetarian/
     vegan), `gluten`/`wheat`/`wheat flour|semolina|bran|germ|gluten` and
     whole/wholemeal/durum variants (→ not gluten-free), and `soy|soya|soja`
     (+ `bean(s)`, `flour|protein|lecithin|sauce|milk|powder`) (→ allergen Soy).
     Everything else is *unknown*: `coconut milk`, `oat milk`, `almond milk`,
     `buttermilk`, `milk chocolate`, `soy milk` (Soy only, never Milk),
     `gluten-free …`, `no milk`, `lactose-free milk`, `bacon flavour`, `vegan
     bacon`, `wheat starch`, `pork sausage`, Bulgarian text. A substring is not an
     identity, so no negation guard or plant-milk blacklist is needed to keep these
     out. Entries are split on `and`/`&`/`or` (`Contains milk and soy`), a leading
     label word (`Contains`, `Ingredients`) is dropped, percentages and case are
     normalised, and a line wrap is whitespace (`Gluten-<newline>free` is
     `gluten-free`; a newline never ends a precautionary scope -- OCR text wraps
     lines).
     **Precautionary/negating headers scope the rest of the sentence** (or the
     parenthesis group they appear in): after `may|might|could contain`, `traces
     of`, `cross contact`, any of `facility|factory|premises|equipment|bakery|
     production line`, `free from|of`, `without`, `contains no|none`, `none of`,
     `neither`, `does not|doesn't contain`, `not containing` (and a `no ...` list
     directly after a `Contains:` / `Allergens:` label), entries are a statement
     *about* the product, not ingredient occurrences. Adjectival
     `gluten-free`/`no artificial colours` inside one entry do **not** open a scope,
     so `Gluten-free oats, wheat flour` still reports not gluten-free and
     `Wheat flour, milk. Free from soy. May contain: eggs` keeps wheat and milk
     while dropping the rest -- a negated phrase never discards genuine
     occurrences globally. Also not ingredient occurrences: the bare nouns before a
     trailing `free` (`Wheat, gluten and dairy free`, `Milk/lactose free`), a
     `key: value` line (`Gluten: none`, `Lactose: free`), and an entry followed by a
     quantity qualifier (`Gluten (<20 ppm)`, `Milk (0%)`). Abbreviation periods
     (`e.g.`, `max.`) and decimals do not end a sentence. The text examined is
     bounded (100,000 characters; evidence beyond it is ignored, never invented) and
     every step is linear -- an earlier draft had a quadratic regex that stalled a
     scan for seconds on a long digit run.

  Deliberately **not** inferred from text: lactose from a `milk`/`whey` entry
  (lactose-free milk is still milk); halal/kosher from anything but pork (alcohol
  as a solvent, gelatin's animal source and certification are not determinable
  from an ingredient name -- the legacy `alcohol` → not-halal hit is dropped);
  precautionary allergen labelling (no "may contain" channel exists on the wire);
  anything from Bulgarian or other non-English text (unknown, not "suitable").
  The absence of a match never means suitability or allergen absence.
* **`null` otherwise** — including Bulgarian, mixed-language, foreign-language,
  empty and truncated text, and any ambiguous, negated, precautionary or
  substring-only mention.
* **Precedence when sources disagree** (`resolve_flags`): an explicit `false` is
  authoritative; derived evidence fills only what the source left unknown; an
  explicit `true` that *real* incompatibility evidence contradicts (e.g. a model
  says `isVegan: true` for "gelatin, pork fat", or a trusted catalog row says it
  contains gluten) becomes `null` — two conflicting claims support neither, and a
  conflict is never resolved in favour of the positive claim; explicit
  `isVegan=true` beside `isVegetarian=false` is likewise `null`. Because an
  uncertain mention ("coconut milk", "gluten-free", an untrusted catalog boolean)
  is no longer evidence, it can no longer fake such a contradiction: a provider's
  explicit `isVegan: true` beside "coconut milk" stays `true`. A milk entry does
  not contradict an explicit `isLactoseFree: true`.
* **Allergens** (`detect_allergens_text`, OCR-text path only): `Soy`/`Milk` are
  listed only when the entry-identity rules above found them (`Contains: milk,
  soy` → `"Soy, Milk"`; `coconut milk` → `""`; `may contain milk` → `""`). `""` is
  *unknown / none detected*, never an allergen-free guarantee -- a product named
  "coconut milk" is neither reported as containing Milk nor cleared of it, and
  only 2 of the 14 regulated allergens are examined at all. A provider's/
  Gemini's *declared* allergen list is independent evidence and is kept as
  declared (placeholders filtered).
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

**Amended in place (PR #22 review, finding 1).** The first version kept a legacy
`false` whenever the row's raw text merely *contained* a keyword
(`LIKE '%gluten%'`), so `isGlutenFree=false` with text `gluten-free` -- and
`coconut milk`, `free from milk`, `without wheat`, everything after `may contain:` --
survived as a "supported incompatibility". The old code that wrote those rows
(`not ("wheat" in text or "gluten" in text)` …) produced exactly these `false`s from
negated and plant-based text, so the keyword being present says nothing about
whether the claim was ever supported. The policy is now evaluated in Python
(a portable SQL `LIKE` cannot apply occurrence-level negation/scope) with a
**frozen copy** of the entry-identity rules (section 2); the migration still
imports no application code, is deterministic and idempotent.

*Applied anywhere beyond disposable databases?* Nothing in the repository or the
operator's records says so: PR #22 is open and unmerged, `main` (`32bd7ef`) does
not contain the revision, the operator's backup directory holds pre-deployment
dumps for earlier deployed PRs (`pre_pr18`, `pre_pr20`) and none for this branch,
and the handoff records every prior run of this revision as CI or a disposable
`--network none` / throwaway-PostgreSQL container. This task was forbidden to
read the live database, so the negative is **not** proven from the live
`alembic_version`. Amending in place is therefore safe only if that holds;
**verify `alembic current` on any environment before deploying** -- if an
environment is already at `d7e8f9a0b1c2` with the old policy, the amended
revision will not re-run there (same revision id). Either `alembic downgrade
c6d7e8f9a0b1` (lossy, documented) and re-upgrade, or add a corrective revision
that re-applies `apply_flag_policy` to the already-migrated rows -- that can only
*remove* unsupported `false`s; it cannot restore a value the old policy already
reset to `NULL`.

Legacy provenance cannot always distinguish evidence from a guess, so the policy
is deliberately conservative (executable, unit-tested on SQLite and round-tripped
on real PostgreSQL 16):

| Legacy value | Kept when | Otherwise |
|---|---|---|
| flag `true` | the row's `source` is a barcode provider (`open_food_facts`, `gs1_digital_link`, `upcitemdb`) **and** the same real evidence (below) does not contradict it (vegan `true` beside not-vegetarian evidence is also unknown) | reset to `NULL` (unsupported positive claim; includes `local`, `label_scan`, `label_scan_translated`, unrecognised sources). Never a `true` from uncertainty. |
| flag `false` | it is **re-supported** by (1) exact ingredient-entry identity in the stored `raw_ingredient_text` (section 2, outside precautionary/negating headers) **or** (2) a linked *trusted* catalog row (`products.ingredient_ids` → `ingredients` that are `VERIFIED` and `CURATED_SEED`/`REGULATORY_LOOKUP`, not `identity_uncertain`) that an exact ingredient entry of the stored text *names* (name or E-number; the stored link itself came from the substring matcher, so it is not identity) | reset to `NULL` (it was a default/"not stated", a negated phrase, a plant-based name or a substring) |
| `health_score` | the product `is_verified` (a genuine `0` survives) | reset to `NULL` (was only a placeholder) |
| `allergens_detected` `"None"`/`"N/A"`/`"null"`/… | never (placeholder set) → `""` | positive allergen names untouched (see limitation 4) |

Worked examples (all legacy `false`): `Gluten-free oat flour` → `NULL`; `Free from
milk`, `without wheat`, `Free from: milk, soy, gluten`, `May contain: milk, wheat`,
`coconut milk`, `oat milk`, `buttermilk`, `lactose-free milk`, Bulgarian and empty
text → `NULL`; `Gluten-free oats, wheat flour` → gluten `false` **kept** (a genuine
`wheat flour` entry); `Contains: Milk, Soy` → not-vegan kept, lactose `NULL`;
`sugar, lactose` → lactose and vegan kept; a label naming (`Barley Malt Extract`) a linked
curated row that says it contains gluten → gluten `false` kept; the same link to an
UNVERIFIED / OCR / Gemini-sourced row, or to a trusted `Milk` row reached only through
`Coconut milk` → `NULL`; line-wrapped `Gluten-<newline>free` and `May contain<newline>milk`
→ `NULL`.

Known limitations (documented, not fixable from stored data):
1. `source` is only rewritten when an enrichment newly *completes* an evidence
   group, so a provider-sourced row whose flags were later overwritten by an
   old-code label scan that completed no group keeps `source = open_food_facts`
   and its guessed `true`s survive as "provider evidence" (unless real evidence
   contradicts them).
2. A verified row's stored score is kept, so a placeholder `0` left by the old code
   between the discovery commit and the first scoring write would survive -- no
   evidence such rows exist, but surviving `0`s are not *proven* genuine.
3. A legacy `false` that a provider/model stated explicitly but that neither the
   stored text nor a trusted catalog row supports cannot be told apart from a
   default `false` and becomes unknown (a rediscovery / label re-scan restores it
   under the new logic). The migration cannot see which model/provider wrote a
   value.
4. **Positive allergen names are not rewritten.** A stored `Milk`/`Soy` on a
   non-provider row may come from the old substring heuristic (`coconut milk` →
   `Milk`) or from a real structured declaration; stored data cannot tell which.
   Erasing a possibly-real allergen warning is the more dangerous error, so they
   stay (over-reporting is never an absence claim) until a re-scan repopulates
   them. **This is a decision for the owner**: a stricter migration could keep a
   non-provider `Milk`/`Soy` only when the stored text re-supports it.
5. The frozen entry rules under-detect by design (`milk chocolate`, `buttermilk`,
   `wheat starch`, Bulgarian, `bacon`-as-halal, …): unknown, not `false`.
6. Offline mode (`alembic upgrade --sql`) is refused for this revision (it raises)
   because the flag policy runs against live rows; it will not silently skip it.

**Downgrade is lossy and documented**: `NULL` flags → `false`, `NULL` score → `0`,
the `"None"` allergen placeholder is *not* restored. Consequently upgrade →
downgrade → upgrade is not an identity on data written after the first upgrade
(non-provider `true`s are reset again, and every backfilled `false` is re-evaluated
with the same evidence rules: an unsupported one returns to `NULL`, one the stored
text/catalog genuinely supports is a supported `false`). **Back up before running
either direction on real data.**

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
* Superseded by the PR #22 review follow-up: raw text no longer over-reports an
  incompatibility for plant products ("coconut milk" is unknown). The cost is
  deliberate under-detection (`milk chocolate`, `buttermilk`, `wheat starch`, non-
  English text stay unknown): for a user who must avoid gluten or dairy, an
  unknown produces **no warning** where a keyword hit used to. That is the
  owner-approved trade-off ("prefer NULL"); an informational "could not confirm"
  warning for unknown flags (above) remains the open product decision that would
  close the gap without asserting anything.
* Catalog evidence is gated on provenance (`VERIFIED` + curated/regulatory), but a
  catalog row is still *matched* to a label token by the existing fuzzy substring
  matcher (`ocr_normalizer.match_against_database`, a verbatim port of the Kotlin
  matcher); a token that merely *contains* a trusted row's name can match it.
  Dormant today (no curated row asserts an incompatibility) but a limitation to
  revisit before curating such rows. Legacy UNVERIFIED ingredient rows created
  before `f5a6b7c8d9e0` may still carry fabricated per-ingredient flags on
  `IngredientOut`; they are ignored for product-level claims and were not rewritten.
  (Catalog evidence is now additionally gated on the row being *named* by an exact
  entry, which closes the substring-link route for `coconut milk` -> a trusted `Milk`
  row; a legitimately fuzzy match such as `Oat flour` -> `Whole Oat Flour` therefore
  yields no catalog evidence either -- unknown, the conservative direction.)
* Known imprecision of the entry rules (all err to *unknown*, none to a claim):
  a precautionary/negating phrase suppresses the rest of its sentence (`salt without
  additives, wheat flour` loses `wheat flour`); a parenthetical qualifier after an
  entry is not interpreted (`Milk (plant-based)` still reads as milk -- rare; the
  qualifier list would be an open-ended blacklist); numbered lists (`1. Milk 2. Wheat
  flour`) are not split; non-English text yields nothing; there is no
  precautionary-allergen ("may contain") channel on the wire.
* `gemini_call_failed` logs `str(exc)` of the transport error in the ordinary
  application log (not in the diagnostics journal); unchanged.
