# Ingredient-language code-review follow-up — completion report

Tracked in [GitHub issue #19](https://github.com/nikoit2022-creator/NutriGuard/issues/19).
Committed to the repository per the issue's own documented fallback
("commit a focused report ... reference this issue, and tell the owner
only the pushed SHA") — see the round 2 note below for why, despite
this session having `gh` CLI authenticated access (still true this
round: no token-permission change was requested or made).

## Round 3 (Codex follow-up review, addressing the 2026-09-17 12:34:10Z
comment on pushed head `2cebd4a`)

Codex's static review of round 2 confirmed the multilingual-alias
filtering fix and the mixed-label success-path regression address the
previously central findings, and flagged 2 remaining small diagnostic
requirements (round 2's 585+10 test results were Claude-reported, not
independently rerun by Codex). Both fixed in this round:

| # | Finding | Status | Evidence |
|---|---|---|---|
| 1 | Translation summaries lost on partial/failure paths | **Fixed** | The observed `IngredientTranslationSummary` (and whole-label fields) previously existed only in the SUCCESS return dict from both `_finalize_*` functions -- a `ProductNotFoundError` raised AFTER translation already ran (`food_analysis.py`, both `_finalize_barcode_enrichment` and `_finalize_standalone_label_analysis`'s "not complete" branches) lost it entirely, as did a LATER response-construction/serialization failure in `scan.py`. Fixed via a new `AppError.diagnostic_metadata` side channel (internal-only -- `app.main`'s error-envelope builder never reads it, so it can never leak into the public HTTP error body) populated with the shared `_translation_diagnostic_fields(label_result, ingredient_translation_summary)` helper at both raise sites; `scan.py`'s `except ProductNotFoundError` handlers now fold in `_translation_fields(exc.diagnostic_metadata or {})`, and the generic `except Exception` handlers (ocr-text, label-image) now fold in `_translation_fields(result or {})` using the `result` `food_analysis` already returned before the later failure. The 3 `ProductNotFoundError` sites that run BEFORE any label/translation pipeline (`analyze_barcode`'s pure identity lookup) deliberately never set `diagnostic_metadata` -- no fabrication for a pass that never ran. See `tests/integration/test_scan_diagnostics_endpoints.py::test_scan_ocr_text_partial_outcome_preserves_observed_translation_summary` (partial outcome after a real per-ingredient translation, plus asserts the summary never leaks into the public error body), `::test_scan_label_image_serialization_failure_after_translation_preserves_observed_summary` (later serializer failure), and `::test_barcode_not_found_before_any_translation_never_fabricates_diagnostic_metadata` (the explicit "do not fabricate" case). |
| 2 | Language-code/privacy boundary not enforced | **Fixed** | `IngredientTranslationSummary.detected_languages` copied Gemini's raw `detectedLanguage` string directly (only lowercased/stripped) -- that field is UNRESTRICTED free text by the actual API contract (the prompt asking for "ISO 639-1 code or short language name" is advisory, never enforced), so arbitrary/injected text could reach the diagnostics journal, defeating its bounded/no-raw-content guarantee. Fixed with `_normalize_detected_language_code`: a finite, explicit ISO 639-1 code allowlist (not a regex/shape guess -- a coincidentally 2-letter non-code string also normalizes to `"other"`), a raw-length cap (`_MAX_RAW_LANGUAGE_LENGTH`) applied before any comparison, and a cap on the collected set's size (`_MAX_DETECTED_LANGUAGES`, applied both when a summary is first built and again after `IngredientTranslationSummary.merged_with` unions two summaries). Never relies on the Gemini prompt wording as the boundary. See `tests/integration/test_ingredient_catalog.py::test_normalize_detected_language_code_rejects_non_allowlisted_and_injected_text` (unit-level: real codes pass, non-codes/empty/`None`/a long injected-instruction-shaped string all normalize to `"other"`) and `::test_materialize_ingredients_never_leaks_injected_detected_language_text` (end-to-end: a mocked Gemini response with an injected-content `detectedLanguage` never appears in the persisted summary). |

Verification for this round: **590 passed, 0 failed, 0 skipped** (585 +
5 new tests: 3 diagnostic-preservation regressions, 1 unit-level
allowlist test, 1 end-to-end injected-content test); disposable-
PostgreSQL opt-in suite: **10 passed, 0 failed**, migrated to head
`c6d7e8f9a0b1` first (unchanged this round, no schema edits). No
Pydantic schema was touched, so `openapi.json` is unchanged. Live
deployment: **untouched** (same guarantees as rounds 1-2 -- isolated
worktree, disposable Docker containers/images/networks only).

**Round 3 code-fix commit:** `285fb028184d59932f4f5ffe8e0f187558d1f997`.
This report is committed in one further commit on top of that (this
file only) -- see `git log` on the branch for its exact SHA; the owner
will be told that pushed SHA directly (per the same fallback used in
rounds 1-2). Both pushed to
`origin/feat/backend-ingredient-language-diagnostics`.
Comment/PR posting was not attempted again this round -- no token-
permission change occurred since round 2's documented failure
(`Resource not accessible by personal access token` on both
`addComment` and `createPullRequest`); falling back to this committed
report per the issue's own documented alternative, as Codex's own
comment explicitly anticipated ("no token permission expansion is
needed").

---

## Round 2 (code-review follow-up, addressing the owner's 2026-09-17
12:05:53Z review comment on pushed head `5d852ac3873eca8561d5dd253a12532bdf2eb6f6`)

The owner's independent static review of round 1's pushed head found 2
requirements still incomplete plus a documentation correction. All 3 are
fixed in this round:

| # | Finding | Status | Evidence |
|---|---|---|---|
| 1 | English-alias fallback trusted foreign aliases | **Fixed** | `ingredient_alias_repository.get_all_normalized` (selected EVERY alias regardless of language) replaced by `get_all_normalized_english` (filters `language == "en"`). A foreign-tagged alias (e.g. a French original learned via `_register_original_text_alias`, always tagged with its real `source_language`, never `"en"`) can no longer count as evidence an untranslated foreign RESULT is English. See `tests/integration/test_ingredient_catalog.py::test_get_all_normalized_english_excludes_non_english_and_untagged_aliases` (direct repository coverage: en/bg/fr/untagged) and `tests/integration/test_ingredient_language_identity_e2e.py::test_foreign_alias_of_a_different_ingredient_never_counts_as_english_evidence` (end-to-end regression: a DIFFERENT target than the alias owner, so the pre-existing "already known" alias shortcut can't bypass the check — confirms the target stays honestly `identity_uncertain`/`TRANSLATION_UNRELIABLE` instead of silently inheriting an unrelated ingredient's foreign name). |
| 2 | Diagnostics omitted per-ingredient translation outcome | **Fixed** | New `ingredient_catalog.IngredientTranslationSummary` (bounded: attempted/reliable/unreliable counts + a small set of detected language CODES — never original/translated text) is returned from `materialize_ingredients` (previously discarded down to a single bool) and merged across BOTH `materialize_ingredients` calls each request pipeline makes (`IngredientTranslationSummary.merged_with`) since the first call is normally where real translation work happens and the second, later rebuild pass usually just finds it already alias-resolved — merging avoids silently losing the first pass's real result. Threaded through `food_analysis`'s result dict and surfaced in `app.api.v1.scan._translation_fields` as NEW, clearly separate fields: `ingredientTranslationAttempted`/`ingredientTranslationReliableCount`/`ingredientTranslationUnreliableCount`/`ingredientTranslationLanguages` — never merged into the pre-existing whole-label-blob fields, so a caller can always tell which pass produced which outcome. `translationAttempted` now also becomes `true` when only the per-ingredient pass ran (previously stayed `false`/`"not_needed"` for exactly the mixed-label case the review comment flagged). `detectedLanguage` ( `label_detected_language`, computed but never surfaced before) is now included too. See `tests/integration/test_scan_diagnostics_endpoints.py::test_scan_ocr_text_mixed_label_reports_per_ingredient_translation_separately` — the exact scenario requested: whole-label `status="ok"` (English trigger word glued to Romanian names) with a real mocked per-token translation attempt, one reliable + one unreliable result. |
| 3 | Doc's representative JSON advertised a scientifically verified machine translation | **Fixed** | Section "Representative Android JSON" below regenerated from an actual persisted row (`IngredientOut.model_validate`), not hand-typed — now honestly shows `verificationStatus: "UNVERIFIED"` / `source: "GEMINI"`, never `"VERIFIED"`. |

Verification for this round (see "Exact commands and test counts" below
for the full-suite numbers, re-run against this round's own head):
**585 passed, 0 failed, 0 skipped** (582 + 3 new tests: 1 repository-level,
2 integration-level regressions above); disposable-PostgreSQL opt-in
suite: **10 passed, 0 failed**. No schema/migration change this round, so
no upgrade/downgrade/upgrade re-verification was needed. No Pydantic
schema (`app/schemas/`) was touched this round, so `openapi.json` is
unchanged (confirmed: `app.openapi()` still matches the tracked file
exactly).

Live deployment: **untouched**, same guarantees as round 1 below (isolated
worktree, disposable Docker containers/images/networks only, no
`docker compose` against the live stack, no live/production migration,
repair tool never invoked).

**Round 2 code-fix commit:** `853c05a512eb860b8d625405f4eb23e2e88822b8`.
**Report commit (this file):** `46449c8` (see `git log` on the branch
for the exact full SHA). Both pushed to
`origin/feat/backend-ingredient-language-diagnostics`.

Posting directly as an issue comment and opening a PR were both
attempted this session (this session does have `gh` CLI authenticated
as the repo owner) but both failed:
`GraphQL: Resource not accessible by personal access token` on
`addComment` and `createPullRequest`. The authenticated fine-grained
PAT has `Contents: write` (confirmed -- both commits above pushed
successfully) but not `Issues: write` or `Pull requests: write`, which
fine-grained PATs gate independently of the token owner's actual repo
role/push access. Per the owner's explicit direction this session,
falling back to the committed-report path rather than attempting a
workaround. PR creation link (unchanged from round 1, still not
created):
https://github.com/nikoit2022-creator/NutriGuard/compare/main...feat/backend-ingredient-language-diagnostics?expand=1

---

## Round 1 (original 5 code-review findings)

## 1. Final SHA and PR

- Branch: `feat/backend-ingredient-language-diagnostics`
- Code-fix commit: `dce1472ca410d41b57aa69d0e7bcc9b0cd46bdb0` (all 5
  findings' fixes + tests; pushed to
  `origin/feat/backend-ingredient-language-diagnostics`)
- This report is committed in one further commit on top of that (this
  file only) — see the branch's `git log` for its exact SHA; the owner
  will be told that pushed SHA directly.
- No PR exists yet (this session has no `gh` CLI / API write access to
  create one). Creation link:
  https://github.com/nikoit2022-creator/NutriGuard/compare/main...feat/backend-ingredient-language-diagnostics?expand=1

Reviewed baseline was `8ca0119fac1fbffb47bb64b5cb50a0f5ada83fb7`; this
session's work is two new commits on top of it (`8ca0119` unchanged,
`dce1472` = all code fixes and tests — see `docs/CODEX_HANDOFF.md`'s two
most recent entries for the full technical writeup).

## 2. Each finding

| # | Finding | Status | Evidence |
|---|---|---|---|
| 1 | Short ASCII text does not prove English | **Fixed** | `_is_plain_ascii_text` removed; replaced with a catalog-known-alias check (`ingredient_alias_repository.get_all_normalized`, new). "lait entier" -> "lait entier" (unchanged, no catalog match) now stays `reliable=False`. See `tests/unit/test_ingredient_translation.py::test_short_untranslated_foreign_text_without_a_catalog_match_stays_unreliable` / `test_short_translation_matching_a_known_catalog_alias_is_reliable`. |
| 2 | Translation correspondence + full context | **Fixed** | `_match_responses_to_targets` matches by exact `originalText`, never position; handles reordered/duplicate/missing/extra safely (7 dedicated tests). `translate_ingredient_tokens(targets, *, context=...)` + `GeminiService.translate_ingredient_list(targets, context=...)` now send the full scan ingredient list separately from the specific entries needing translation. |
| 3 | Allergen capitalization is not broken segmentation | **Fixed** | `_has_embedded_allergen_emphasis` removed outright (no milder threshold substituted); replaced with `_has_unsplit_colon_clause` (`COLON_SEPARATED_CLAUSE_MERGE`), a structural (not typographic) signal. "MILK powder", "ZARA pudră", "Produs din GRAU", "SECARA agenți de creștere" no longer flagged; "LAPTE proteină din LAPTE" still correctly flagged via the unrelated, unchanged duplicate-word check. |
| 4 | Truthful diagnostics | **Fixed** | Barcode `labelScanRequired` now classified `partial` (new `_is_partial_result`, keyed on `discoveredIdentity` presence, applied to all 3 endpoints). `dataSource` now reports real `is_from_database_cache`/`Product.source`, not the operation name. `_finish_serializing` forces full `model_dump(mode="json")` inside the try block so computed-field/encoding failures are diagnosed, never a false success. Translation fields now come from real `LabelTextResult.status`/`translation_used`/`detected_language`, not inferred from `Product.source` alone. |
| 5 | Android contract completion | **Fixed** | `ProductOut.originalIngredientText`/`ingredientTextSourceLanguage` added (additive, truthful `""`/`null` defaults). EN/BG contract clarified and covered by new tests (`tests/integration/test_ingredient_bilingual_contract.py`): a translated-only ingredient never fabricates `bg` coverage; the runtime translation path never writes/marks a reviewed `IngredientLocalization` row; a curated ingredient's real reviewed Bulgarian profile is reused via identity resolution (official identifier/alias), not translation. `effectConditions`/`dietaryGuidance` confirmed empty for translated ingredients. |

Nothing in this review round was "not applicable" or "still blocked" —
all five findings were reproducible and are fixed.

## 3. Exact commands and test counts

```
docker build -t nutriguard-review-final:latest .
docker run --rm --entrypoint python -e GEMINI_API_KEY="" nutriguard-review-final:latest -m pytest tests/unit tests/integration -q
```
Result: **582 passed, 0 failed, 0 skipped** (2 pre-existing SQLAlchemy
identity-map `SAWarning`s from unrelated, already-existing race-condition
tests — not failures, not new).

```
docker run --rm --network <disposable> -e NUTRIGUARD_TEST_POSTGRES_URL=... -e GEMINI_API_KEY="" \
  --entrypoint python nutriguard-review-final:latest -m pytest tests/postgres -q
```
Result: **10 passed, 0 failed, 0 skipped** (opt-in concurrency suite,
against a disposable PostgreSQL 16 container, migrated to head
`c6d7e8f9a0b1` first — unchanged this pass, no schema edits were needed).

OpenAPI: `app.openapi()` vs. tracked `openapi.json` (pinned Python 3.12
deps) — **exact match** after regeneration; diff confined to `ProductOut`
(2 new fields), purely additive, no path changes.

No test was skipped in this session's own new/modified files. The 0
skipped count above is the whole suite's.

## 4. Representative Android JSON

### A freshly translated (never-curated) ingredient — `GET` ingredient/product response

Doc-correction (code-review follow-up): the example below is copied
directly from an actual persisted row, materialized through
`ingredient_catalog.materialize_ingredients` with a mocked reliable
Gemini translation ("Ulei de rapiță" -> "Oil Made From Rapeseed") and
serialized through the real `IngredientOut.model_validate(row)` — not
hand-typed. The previous version of this section showed
`verificationStatus: "VERIFIED"` with a footnote explaining that away as
a dataclass default used "for illustration"; that was never something a
real translated row can actually return, and advertised a scientifically
verified machine translation to the Android contract by mistake. A real
row is always `verificationStatus: "UNVERIFIED"` / `source: "GEMINI"` /
`riskAssessmentAvailable: false`, exactly as shown here:

```json
{
  "id": "synth_oil_made_from_rapeseed_5d2fb24560ee",
  "commonName": "Oil Made From Rapeseed",
  "identityUncertain": false,
  "uncertaintyReason": null,
  "effectConditions": "",
  "dietaryGuidance": "",
  "adiPopulationScope": null,
  "verificationStatus": "UNVERIFIED",
  "source": "GEMINI",
  "riskAssessmentAvailable": false,
  "efsaApprovalStatus": "NO_INFORMATION",
  "fdaApprovalStatus": "NO_INFORMATION",
  "localizations": {
    "en": {
      "commonName": "Oil Made From Rapeseed",
      "translationStatus": null,
      "translationSource": null
    }
  }
}
```

Note: **no `bg` key at all** — see EN/BG fallback behavior below. The
existing (unchanged) `merge_verified_fields` gating can never promote a
`source=GEMINI` row to `VERIFIED`/`riskAssessmentAvailable=true` —
translation never promotes scientific/regulatory verification.

### `ProductOut` — new fields

```json
{
  "barcode": "...",
  "rawIngredientText": "Water; Rapeseed Oil; Salt",
  "originalIngredientText": "Apă; Ulei de rapiță; Sare",
  "ingredientTextSourceLanguage": "other",
  "ingredientIds": "..."
}
```

Defaults when no label/OCR text was ever extracted (e.g. a pure barcode
discovery): `originalIngredientText: ""`, `ingredientTextSourceLanguage: null`
— confirmed directly against the schema's own field defaults
(`ProductOut.model_fields`), never omitted, never a placeholder string.

## 5. EN/BG fallback behavior (exact)

- `localizations.en` is **always** present and is always the canonical
  `Ingredient` row's own current fields — genuinely English once the
  language pipeline has processed the row (translated or already-English).
- `localizations.bg` is present **only** when a `REVIEWED`
  `IngredientLocalization` row exists for that exact ingredient id, whose
  `source_content_hash` matches the CURRENT canonical English text
  (`build_localizations`, unchanged production logic).
- The runtime per-ingredient translation pipeline
  (`app/services/ingredient_translation.py`, wired in via
  `ingredient_catalog.materialize_ingredients`) **never** writes an
  `IngredientLocalization` row of any kind, `DRAFT` or `REVIEWED` — it only
  ever sets `Ingredient.common_name`/`source=GEMINI`/etc. directly. So a
  brand-new synthetic ingredient resolved purely through translation will
  **never** have a `bg` key, ever — not until a human curator adds one
  through the existing seed/curation path
  (`app/seed/ingredients_seed_bg.json` + `app/seed/load_seed.py`), exactly
  like the 12 already-curated ingredients.
- When a foreign-language OCR token resolves, via an official identifier
  (E-number/INS/CAS) or a previously-learned alias, to an **already
  curated** ingredient instead of creating a new synthetic row, the full
  curated profile — including any real reviewed Bulgarian localization —
  is served as-is. This is the only path by which a client sees `bg`
  content for a foreign-language-sourced scan; translation itself never
  produces it.
- Android should therefore treat `localizations.bg`'s absence as the
  normal, expected state for most OCR/translated ingredients today, not
  an error — full bilingual coverage exists only for the 12 curated
  ingredients (and will grow only as more are curated, not as more labels
  are scanned).

## 6. Remaining content gaps and diagnostics limitations

- `effectConditions`/`dietaryGuidance` still have no real authored content
  for the 12 curated ingredients — the field/localization machinery is
  implemented and tested, but populating genuine scientific text needs a
  product-owner/scientific review pass, not a backend guess. Left
  deliberately empty per "do not fabricate scientific content."
- The dry-run repair tool (`python -m app.seed.repair_ingredient_language`)
  was not run against any live database in this or the previous session —
  by design; applying repairs to live data was never authorized.
- Diagnostics: bounded rotation (1 MiB / 1 backup) and POSIX `flock`
  multi-process safety in `app/core/scan_diagnostics.py` are unchanged;
  this pass only fixed what gets recorded and when, at the `app/api/v1/scan.py`
  call-site layer. No automatic/periodic reporting exists or was added.
- `stage` in diagnostic records remains coarse (only what the router layer
  itself observes — `request_validated`/`analysis_complete`/`response_built`,
  plus label-image's own early-validation stages) — it does not instrument
  `app/services/food_analysis.py`'s internal steps, by design (never invent
  stage information that wasn't actually observed).

## 7. Live deployment confirmation

**Untouched.** All work in this session happened in the pre-existing
isolated git worktree (`/home/vboxuser/nutriguard-worktrees/feat-backend-ingredient-language-diagnostics`),
using disposable Docker containers/images/networks for every test run, all
removed after use. The live checkout's separately-tracked, uncommitted
`docs/CODEX_HANDOFF.md` edit from the earlier deployment task was never
touched or read for editing purposes. No `docker compose` command was run
against the live dev stack; no migration was applied to any live/production
database; the repair tool was never invoked with `--apply` anywhere.
