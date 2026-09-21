# CODEX_HANDOFF

## 2026-09-21 (latest): PR #22 owner follow-up -- parenthetical qualifiers stay attached to their parent (Claude Code, isolated worktree, NOT merged/deployed)

Owner comment: https://github.com/nikoit2022-creator/NutriGuard/pull/22#issuecomment-5761194261. Same PR branch `feat/backend-truthful-unknowns-diagnostics`; **fast-forward push, no force push**. Backend only; Android, live checkout, live database, live containers, secrets: untouched (worktree `nutriguard-worktrees/feat-backend-truthful-unknowns-diagnostics`; tests only in `--network none` containers and a disposable PostgreSQL 16.15 on an `--internal` Docker network, removed afterwards; the live `nutriguard-backend-*` containers were never touched).

- Drift check before starting: local HEAD == `origin/<branch>` == reviewed head `eaad86ff0df3f4fae11c6401b4d16da2a02bf971`; `origin/main` still `32bd7efc05750470da6f48eab7c4111e45db1d26` (PR base unchanged); worktree clean; the live checkout's own uncommitted handoff edit was left as found.
- **Code head of this fix: `7ee33ce705160f9d62836e59f3fdd4fe458eab8e`** (parent `eaad86f`). Backend CI on it: **success**, run https://github.com/nikoit2022-creator/NutriGuard/actions/runs/35608237912. (This handoff entry is a docs-only commit on top; its own CI result is in the PR checks.)

### What was wrong (reproduced, then fixed)
`resolve_flags(None, "Milk (plant-based)")` -> `is_vegan=False`; `detect_allergens_text` -> `"Milk"`; same for `Milk (coconut)` and `Ingredients: sugar, milk (plant-based), salt`; `resolve_legacy_flags(label_scan, "Milk (plant-based)", all six False)` preserved `is_vegan=False`. Root cause as the owner said: the tokenizer made `plant-based` a separate entry and left the bare parent `milk` behind as a definitive identity. The catalog route (`_is_named_by_entries`) had the same defect for a trusted `Milk` row.

### What changed (`app/services/dietary_suitability.py`, and the identical frozen copy in `alembic/versions/d7e8f9a0b1c2_*.py`)
A parenthesis group directly after an entry (also `[...]`, and every adjacent group: `milk (3%) (plant-based)`) **qualifies** that entry. The parent keeps its own identity only for a **closed list of identity-preserving forms**: (1) a chunk that is *only* a quantity (a number, optionally a unit or fat/protein word: `Milk (3.5% fat)`, `(250 ml)`; a percentage inside `(100% plant-based)` / `(3% coconut)` is **not** a quantity); (2) a precautionary statement (`may contain traces of ...`, facility words); (3) a `with|including|plus|added|containing|fortified/enriched with` additive list (`Wheat Flour (with Calcium, Iron, Niacin)`); (4) an E-number synonym (`Cochineal (E120)`); (5) a qualifier that composes with the parent into a known identity **of the same family** (`Milk (skimmed)` -> `skimmed milk`, `Gelatin (bovine)`; `milk (sugar)` is *not* `milk sugar`), whereupon the consumed qualifier is not read again (`soy (milk)` is soy milk: `Soy`, not `Milk`). Everything else -- `(plant-based)`, `(coconut)`, `(dairy-free)`, several qualifiers, nested groups, unclosed groups -- leaves the identity uncertain: the bare parent is **not** an occurrence (no flag, no allergen, no trusted-row match) and only the opaque joined form (`milk (plant based)`) is kept. **No plant-word blacklist.** The group's own entries are still read, so genuine sublists keep their evidence (`Chocolate (sugar, cocoa mass, whole milk powder)`, `Emulsifier (soy lecithin)`, `Margarine (vegetable oils, water, milk proteins)`). Consequence for the owner's other requirement: an explicit supported claim is no longer contradicted (and erased) by an unsupported interpretation (`resolve_flags({"is_vegan": True}, "Milk (plant-based)")` stays `True`; a legacy provider `true` survives; a genuinely contradicting entry elsewhere -- `gelatin` -- still makes it unknown). A private-use quantity marker is stripped from input first so it cannot be forged; full-width `%` is normalized.

### Independent review (subagent, read-only, adversarial, ran code) and what it changed
It confirmed the three exact repros fail-safe, runtime/migration parity (60,000-string fuzz, 0 divergences), linear time on 100k-character adversarial input, and 42 of the first 156 new tests failing against the pre-fix code. It found **2 majors** (a percentage *inside* a qualifier counted as a quantity, so `Milk (100% plant-based)` still asserted milk; only the first of several adjacent groups was attached) and minors (`milk (sugar)` composing to lactose; a composed qualifier re-read as an entry, `soy (milk)`; E-number synonyms; catalog naming). **All fixed and pinned** (203 tests in the new file). Residual limitations, documented in `docs/TRUTHFUL_UNKNOWN_VALUES.md` section 5, all in the *unknown* direction except the first:
- `oat (milk)` / `sugar (milk)` (a food name qualified by `milk`): the child `milk` is still read, because it is structurally identical to `Emulsifier (soy lecithin)`; separating them needs a lexicon. **Owner decision welcome** (options: keep; or read a lone child only under a regulatory functional-class name).
- `milk (with coconut)` is read as milk *plus* coconut (an additive list).
- Over-suppression (unknown): `Wheat Flour (Calcium Carbonate, Iron, Niacin)` without a `with` introducer, `Milk (organic)`, `Milk (3%, organic)`, `Whey (from milk)`, `Lactose (Milk)` (lactose flag; vegan/Milk still come from the child).

### Files
Code: `app/services/dietary_suitability.py`, `alembic/versions/d7e8f9a0b1c2_tristate_product_flags_nullable_score.py` (docstring + frozen rules; **amended in place again** -- same "verify `alembic current` on the target before deploy; an already-applied old policy will not re-run" caveat as the previous entry). Docs: `docs/TRUTHFUL_UNKNOWN_VALUES.md`, `README.md` (deviation 17), this file. Tests: new `tests/unit/test_ingredient_qualifier_identity.py` (owner's exact examples, nested/ambiguous qualifiers, explicit claims, ordinary quantities, genuine sublists, runtime<->migration parity on entries/flags/legacy policy, SQLite legacy rows, adversarial time, forged marker); `tests/postgres/test_tristate_product_flags_migration_postgres.py` (5 qualifier rows incl. a trusted-`Milk`-row link; row-count assertions 15->20, 16->21); `tests/integration/test_evidence_semantics_e2e.py` (3 cases through the real endpoints).

### Verification (executed this session; pinned deps in `ng-truthful-deps:issue21` built from the unmodified `requirements.txt`, Python 3.12)
- Default suite (`docker run --network none ... python -m pytest -q`): **1220 passed, 12 skipped** (skipped = the opt-in PostgreSQL tests).
- Full suite with every opt-in PostgreSQL test against a disposable PostgreSQL 16.15 (`NUTRIGUARD_TEST_POSTGRES_URL`, base DB migrated to head first): **1232 passed, 0 skipped**. The migration round trip (`upgrade c6d7e8f9a0b1` -> seed representative legacy rows incl. the qualifier rows -> `upgrade head` -> `downgrade` -> `upgrade head`, fresh database per test, product<->ingredient/source/history links and text preserved) passes. `alembic heads`: single head `d7e8f9a0b1c2`.
- `app.openapi()` == tracked `openapi.json` (no wire change). CI: see above.

### API / migration / rollout implications
No wire or schema change. Behavioural: fewer confirmed flags/allergens/warnings for qualified ingredient text (unknown instead of a claim). Migration policy reads the same rules (frozen). Android impact unchanged from the previous entry. Nothing merged, deployed or repaired.

## 2026-09-21: PR #22 owner review follow-up -- unsupported negative claims and substring heuristics (Claude Code, isolated worktree, NOT merged/deployed)

Branch `feat/backend-truthful-unknowns-diagnostics`, continued from reviewed snapshot `11ddaca48376b66c1e582ea0a0d0a5566a28e4a4` (local HEAD == `origin/<branch>` at start and again immediately before this commit; no drift, no newer work to preserve; the only PR comment is the owner's review). Implements the owner-approved comment `issuecomment-5757681001` (two blockers). Backend only; Android, live checkout, live database, live containers, secrets: untouched (the live checkout's own uncommitted handoff edit was left as found; all work in this worktree; tests only in `--network none` containers and a disposable PostgreSQL 16 on a throwaway Docker network, removed afterwards). PR #22 left open and unmerged; nothing deployed.

### What was wrong and what changed
1. **Legacy migration preserved unsupported negative claims** (`alembic/versions/d7e8f9a0b1c2_*.py`). The old policy kept a legacy `false` whenever the stored text merely *contained* a keyword (`LIKE '%gluten%'`), so `isGlutenFree=false` + `gluten-free` survived as a "supported incompatibility" (the old writer produced exactly those `false`s from negated/plant text). **Amended in place** (see "Was the revision applied?" below). The policy is now a deterministic, frozen, self-contained Python evaluator (no `app` imports): a legacy `false` is kept only when **re-supported** by (1) exact ingredient-entry identity in the stored `raw_ingredient_text` (outside precautionary/negating headers) or (2) a linked *trusted* catalog row (`VERIFIED` + `CURATED_SEED`/`REGULATORY_LOOKUP`, not identity-uncertain) that an exact entry of the text *names*; otherwise `NULL`. A provider `true` is kept only if real evidence does not contradict it; every other `true` is `NULL` (never a `true` from uncertainty). `health_score` and allergen-placeholder handling unchanged. Offline `--sql` mode now raises (the flag policy needs live rows). Exact rules, worked examples and provenance limitations: migration docstring and `docs/TRUTHFUL_UNKNOWN_VALUES.md` section 3.
2. **Substring heuristics masqueraded as confirmed evidence** (`app/services/dietary_suitability.py`). `keyword_present`/`_INCOMPATIBILITY_KEYWORDS`/`_ALLERGEN_KEYWORDS` are gone. Raw text supports a claim/allergen only via **exact ingredient-entry identity** from a small closed set (`milk`, `skimmed milk powder`, `whey`, `lactose`, `pork`, `gelatin`, `bacon`, `wheat flour`, `gluten`, `soy lecithin`, ...), outside precautionary/negating scopes (`may contain`, `free from`, `without`, `contains no`, facility words; scoped to the sentence/parenthesis group so genuine occurrences elsewhere survive), with line wraps as whitespace, `X, Y and Z free` back-negation, `key: value`/quantity lines excluded, and `and`/`&`/`or` splitting. `coconut milk`, `oat milk`, `gluten-free`, `buttermilk`, Bulgarian text: **unknown** (`null` / no allergen), never suitability or allergen-free. A milk entry no longer implies lactose; halal/kosher only from pork (the `alcohol` -> not-halal hit is dropped); catalog rows count only when trusted **and** named by an exact entry (the upstream matcher links by substring). An uncertain mention can no longer fake a contradiction: a provider's explicit `isVegan: true` beside `coconut milk` stays `true`; real conflicting evidence still turns it `null`. Heuristic "suspicions" are not retained. No wire change (OpenAPI byte-identical).

### Independent review (subagent, read-only, adversarial) and what it changed
A fresh reviewer, given only the owner's comment and the diff, reproduced by running code: **1 blocker** (a newline reset the negation scope, so `Gluten-<newline>free` / `May contain<newline>milk, soy` became asserted claims, in the resolver *and* the migration), **3 majors** (a quadratic percent regex: 20 KB of digits stalled a scan ~15 s, migration copy too; trusted-catalog routing still promoted `coconut milk` via the substring matcher; free-lists/nutrient lines such as `Wheat, gluten and dairy free`, `Gluten: none`, `doesn't contain: ...` were asserted), minors (abbreviation periods ending scope, common shapes like `Contains milk and soy` dropped) and two surviving mutants. **All fixed and pinned by tests** (regressions listed below); the reviewer's "checked, not a problem" list (migration/runtime regex parity, SQL/enum portability, idempotency, unicode, other regex linear at 1 MB) held. Deliberately *not* changed (documented): parenthetical qualifiers (`Milk (plant-based)`) are not interpreted; a scope marker suppresses the rest of its sentence (over-suppression -> unknown); numbered lists are not split; legacy positive allergens are not rewritten (decision below).

### Was revision `d7e8f9a0b1c2` applied anywhere beyond disposable tests?
No evidence that it was: PR #22 is open/unmerged, `main` (`32bd7ef`) does not contain it, the operator's backup directory has pre-deployment dumps only for earlier PRs (`pre_pr18`, `pre_pr20`), and every recorded run of it was CI or a disposable container. **Not proven**: reading the live `alembic_version` was forbidden. So it was amended in place rather than adding a corrective revision (a corrective one cannot restore values the first policy already reset, and adds a permanent flawed revision). **Before any deploy run `alembic current` on the target; if it is already `d7e8f9a0b1c2` with the old policy the amended file will not re-run** (see doc section 3 for the two safe options).

### Files
Code: `app/services/dietary_suitability.py` (rewritten evidence layer), `alembic/versions/d7e8f9a0b1c2_tristate_product_flags_nullable_score.py` (policy + docstring), comments in `app/services/fallback_analysis.py`, `app/services/food_analysis.py`. Docs: `docs/TRUTHFUL_UNKNOWN_VALUES.md` (sections 2, 3, 5), `README.md` (deviation 17 "Review follow-up"), this file. Tests (new): `tests/unit/test_text_evidence_identity.py` (frozen copy of the old heuristic pins the old failures + corrected semantics, catalog provenance/identity, wrapped text, free-lists, adversarial performance), `tests/integration/test_evidence_semantics_e2e.py` (real endpoints: unknown -> no confirmed warning and no positive claim, plant milk, genuine+negated, explicit vs uncertain/conflicting, provider allergen kept, untrusted/trusted/substring-linked catalog rows, Bulgarian/empty, wrapped OCR). Tests (rewritten/updated): `tests/unit/test_tristate_product_flags_migration.py` (runs the real policy on representative legacy rows in SQLite; old-policy failures pinned per row; idempotency; downgrade backfill; parity tripwire; linear time), `tests/postgres/test_tristate_product_flags_migration_postgres.py` (real PostgreSQL round trip incl. trusted/untrusted/GEMINI-sourced/substring-linked catalog rows and product<->ingredient/source/history link + text preservation), `tests/unit/test_dietary_suitability.py`, `test_fallback_and_gemini_parser.py`, `tests/integration/test_truthful_unknown_values_e2e.py` (intent kept; `milk` no longer implies lactose, catalog fixtures carry provenance/names).

### Verification (all executed this session; pinned deps in the disposable image `ng-truthful-deps:issue21`, built from the unmodified `requirements.txt`, `docker run --rm --network none`)
- Baseline at `11ddaca`: `814 passed, 12 skipped, 2 warnings`.
- Final default suite: `1014 passed, 12 skipped, 2 warnings` (12 skipped = opt-in PostgreSQL tests).
- Full suite with every opt-in PostgreSQL test against disposable PostgreSQL 16.15 (`NUTRIGUARD_TEST_POSTGRES_URL`, base DB migrated to head first): `1026 passed, 0 skipped`. The migration round trip (`upgrade c6d7e8f9a0b1 -> seed legacy rows -> upgrade head -> downgrade -> upgrade head`, per-test fresh database) passes.
- `alembic heads`: single head `d7e8f9a0b1c2`. `app.openapi()` == committed `openapi.json` (identical; no wire change). `git diff --check`: clean.
- Mutation testing (scratch copies): 8 mutants of the first draft, all killed. Then 22 mutants of the reworked code (15 runtime, 7 migration: newline scope, percent-regex lookbehind, length bound, catalog trust/name gating, free back-walk, key:value, quantity, leading filler, and-split, abbreviation, contains-no header, milk->lactose, keep-every-false, trust filter, source widening, name check, provider-true contradiction). Everything was killed except 3 *equivalent* mutants: each of the two redundant newline guards mutated alone, and the hyphen-wrap join, which was dead code (a dash already becomes a space) and was deleted. The combined newline mutant and a corrected and-split mutant (the first attempt was a syntax error, not a mutant) are killed.
- The first draft of this change passed 948 tests and was still wrong (reviewer blocker/majors above): green tests were not treated as sufficient.
- NOT run: any live Gemini/OFF call, any live DB, `alembic upgrade --sql` (now refused), Android tests.

### Unresolved / decisions for the owner
- **Legacy positive allergens are not rewritten** by the migration (`allergens_detected` `Milk`/`Soy` on a non-provider row may come from the old substring heuristic or a real declaration; stored data cannot tell). Kept because erasing a real allergen warning is the worse error. A stricter rule (keep a non-provider `Milk`/`Soy` only if the stored text re-supports it) is a one-function change if you prefer it.
- **Deliberate under-detection**: `milk chocolate`, `buttermilk`, `wheat starch`, `pork sausage`, non-English labels are now *unknown*, so a user avoiding gluten/dairy gets **no warning** where a keyword hit used to. Closing that without asserting anything needs the still-open informational "could not confirm" warning (product decision, not added).
- Catalog evidence is dormant today (no curated seed row asserts an incompatibility) and now name-gated; a legitimately fuzzy match (`Oat flour` -> `Whole Oat Flour`) yields none. Legacy UNVERIFIED ingredient rows may still expose fabricated per-ingredient flags on `IngredientOut` (ignored for product claims, not rewritten).
- Prior open items unchanged: Android nullable-flag handling must ship with/before this backend; `whole_oat_flour` cross-contact cannot be expressed by a boolean; Gemini's answer to the revised prompt is unverified.

### Exact recommended next step
Owner reviews PR #22. Before any deployment: run `alembic current` on the target (see above), take a backup (the migration rewrites `products` flag/score/allergen data), and ship Codex's Android nullable-flag handling first or together. Decide the two owner decisions above. Then merge and deploy by the normal procedure.

## 2026-09-21 (earlier, keyword semantics superseded by the entry above): issue #21 follow-up -- truthful unknown values + actionable translation diagnostics (Claude Code, isolated worktree, NOT merged/deployed)

Branch `feat/backend-truthful-unknowns-diagnostics`, based on `origin/main` = `32bd7efc05750470da6f48eab7c4111e45db1d26` (PR #20; verified no drift from the previously deployed reviewed head). Implements the owner's approved follow-up comment on issue #21. Backend only; Android untouched. Live checkout, containers, database, secrets: untouched (worked in a separate worktree, tested only in disposable `--network none` containers and a disposable PostgreSQL 16 on an isolated Docker network). Full design/contract: `docs/TRUTHFUL_UNKNOWN_VALUES.md`; deviations: README section 6 items 17-18. Two subagents used: one for the translation diagnostics (isolated worktree, diff reviewed and cherry-picked), one independent read-only reviewer (its confirmed findings F1/F2 fixed, F3-F5 documented).

### What changed
1. **Tri-state product dietary flags** (`isGlutenFree`/`isLactoseFree`/`isVegan`/`isVegetarian`/`isHalal`/`isKosher`: `null` unknown, `false` supported incompatibility, `true` explicit supported suitability). New `app/services/dietary_suitability.py`; keyword absence never yields `true`; `false` only from curated-catalog flags or legacy English keyword hits (negation-guarded); an explicit `true` contradicted by evidence becomes `null`. Wired through `fallback_analysis`, `gemini_image_parser` (strict JSON bool; prompt now asks for `null` when unknown), discovery bridge, ORM defaults, `warning_engine` (`is False`). Enrichment/rediscovery never overwrite supported flags/allergens with an unknown.
2. **`healthScore` nullable** on `ProductOut`/`Product`; `null` whenever the product is not `isVerified` (serializer gate, so nested `product.healthScore` matches the top level); genuine 0 preserved; no stored 0 placeholder.
3. **Allergens**: `allergensDetected` `""` = none detected or unknown, literal `"None"` never produced (heuristic, Gemini and OFF placeholders filtered). E471 seed flags (`isVegan`/`isVegetarian`/`isHalal`/`isKosher`) -> `null`; seed reload clears legacy `true`s.
4. **Migration `d7e8f9a0b1c2`** (single head): nullable columns + documented conservative legacy-data policy + lossy documented downgrade (`NULL` flags -> `false`, `NULL` score -> 0, `"None"` not restored). See its docstring.
5. **Translation diagnostics** (internal closed-vocabulary rejection reasons, provider-failure categories from a label never a message; additive repair-report aggregates `translationFailureReasons`/`translationProviderFailureCategories`/`translationAttempt`; bounded scan-line counters incl. `translationOutcome`; fail-open; journal budget unchanged). No root cause for the owner's 150 rows is asserted -- no evidence exists.

### Files
Code: `app/services/{dietary_suitability,translation_rejection}.py` (new), `fallback_analysis.py`, `food_analysis.py`, `gemini_image_parser.py`, `warning_engine.py`, `ingredient_translation.py`, `label_language.py`, `ingredient_catalog.py`, `app/models/product.py`, `app/schemas/product.py`, `app/integrations/gemini.py`, `app/integrations/barcode_providers/open_food_facts.py`, `app/api/v1/scan.py`, `app/seed/{ingredients_seed.json,repair_ingredient_language.py}`, `alembic/versions/d7e8f9a0b1c2_*.py`, `openapi.json`. Docs: `README.md`, `docs/TRUTHFUL_UNKNOWN_VALUES.md`, this file. Tests: ~20 new files (dietary suitability, merge rules, e2e through real endpoints, seed idempotency, migration policy on SQLite + opt-in PostgreSQL round trip, wire-contract, translation reasons/diagnostics, review-finding regressions). Six existing tests updated (intent kept): 2 in `test_food_analysis_discovery_bridge.py`, 4 in `test_gemini_image_parser.py` (unknown `False` -> `None`); `test_ingredient_language_provenance_migration.py` relaxed from "head == c6d7e8f9a0b1" to "single head containing it".

### Verification (all actually executed this session; pinned deps in a disposable image built from the unmodified `requirements.txt`, `docker run --rm --network none`)
- Baseline on unmodified `origin/main`: `590 passed, 10 skipped, 2 warnings`.
- Final default suite on this branch: `814 passed, 12 skipped, 2 warnings` (12 skipped = opt-in PostgreSQL tests).
- All opt-in PostgreSQL tests against disposable PostgreSQL 16.15 (`NUTRIGUARD_TEST_POSTGRES_URL`): full suite `826 passed, 0 skipped`; includes upgrade -> downgrade -> upgrade with representative products/ingredients/aliases/history/product-source links on a real database, and a from-scratch `alembic upgrade head` ending at `d7e8f9a0b1c2`.
- Alembic: single head `d7e8f9a0b1c2`. `openapi.json` regenerated with pinned deps and verified identical to runtime; exactly 7 widening changes on `ProductOut` (`healthScore` + six flags become `T | null`), `required` lists and paths unchanged.
- Independent review: 801 passed at the reviewed commit; reviewer mutated the code 16 ways, every mutant caught.
- NOT run: any live Gemini/OFF call, any live DB, any `--apply`, Android tests, multi-process `flock` stress beyond the existing suite.

### Unresolved / decisions for the owner
- **Android (Codex):** committed `main` `ProductDto.fromJson` parses a JSON `null` flag as `false` (`has()` true + `optBoolean`). Release Codex's nullable-flag handling before/with deploying this backend. `false` now means only "supported incompatibility"; `allergensDetected` `""` must not render as "allergen free"; client-side `!product.isGlutenFree` must become "is explicitly false".
- Migration policy caveats (doc section 3): `source` not rewritten on partial enrichment; a verified placeholder `0` from a request that died between discovery commit and scoring would survive. Take a backup before running either direction on real data; downgrade is lossy.
- Open product decisions: informational "could not confirm" warning for unknown flags (not added); resetting old `true`s when a re-scan replaces ingredient text (not done -- owner's "never overwrite evidence with unknown" rule); `whole_oat_flour` gluten cross-contact cannot be expressed by a boolean; plant-milk keyword over-report.
- Gemini's answer to the revised dietary-flag prompt is unverified (no live calls); parser is safe either way.

### Exact recommended next step
Review and merge the PR after Codex confirms the Android nullable-flag build; then deploy by the normal procedure (backup the DB first -- the migration rewrites `products` flag/score/allergen data; seed reload will clear the E471 `true` flags). After deploy, run the repair dry-run once and read `translationFailureReasons`/`translationProviderFailureCategories` before assuming a root cause for the 150 rows.

## 2026-09-17 (later): code-review follow-up -- translation correctness, EN/BG contract, diagnostic accuracy (Claude Code, isolated worktree)

Continuation of the entry immediately below, addressing a code review of commit `8ca0119fac1fbffb47bb64b5cb50a0f5ada83fb7` on the same branch (`feat/backend-ingredient-language-diagnostics`). Also tracked as GitHub issue #19 (shared Claude/Codex task record); a copy of this report was intended to be posted there, with a fallback to committing it into this repo when API/comment access isn't available in this session -- see `docs/INGREDIENT_LANGUAGE_REVIEW.md` if present. Worked in the same isolated worktree as before; live checkout/database/containers untouched throughout. One subagent used (issue 3, non-overlapping file ownership: `app/services/ingredient_segmentation.py` + its test file only).

### 1. Short ASCII text is not proof of English -- FIXED

`ingredient_translation._is_plain_ascii_text` accepted ANY <=2-word ASCII result as reliable, which let genuinely untranslated foreign text through whenever it happened to contain no diacritics (e.g. French "lait entier" -> "lait entier" unchanged, confidently accepted). Removed entirely -- replaced with a narrow, evidence-based fallback: a short (<=2-word... no, ANY-length) translated result is now accepted, when `detect_language` alone doesn't confirm "en", ONLY if its normalized text matches a name ALREADY established in the ingredient catalog (`ingredient_alias_repository.get_all_normalized`, a new bulk-fetch repository function -- real, pre-existing evidence, never a guess). A short, genuinely novel, correct translation with no catalog match now stays honestly `reliable=False` (identity_uncertain, never silently accepted or silently rejected as "definitely wrong") -- this is the "preserve unresolved status when uncertain" requirement, a deliberate and correct trade-off, not a regression.

Tests: `tests/unit/test_ingredient_translation.py` -- new cases for a known-alias short match (accepted), an unchanged short foreign phrase with no catalog match (rejected), and a novel correct short translation with no catalog match (honestly unresolved, not guessed either way).

### 2. Translation-to-input correspondence + full-list context -- FIXED

`translate_ingredient_tokens` previously paired Gemini's response entries to input tokens by `zip()` position only, never validating the parsed `originalText` field against anything -- a reordered/duplicated/mismatched response could silently attach a translation (and, downstream, a persisted alias) to the WRONG original ingredient. Replaced with `_match_responses_to_targets`: builds a `{text -> [positions]}` map of the input `targets` list, then matches each response entry to its target by EXACT `originalText`, consuming duplicate-text occurrences FIFO; any response entry whose text doesn't match a (remaining) target is discarded, never force-attached elsewhere; any target with no matching response entry becomes an honest `_unreliable` result. The previous blanket `len(payload) != len(tokens)` whole-batch rejection is gone -- every irregular shape (reordered, duplicate input, missing, extra) is now handled per-target instead of failing the whole call.

Full-list-context fix: `translate_ingredient_tokens(targets, *, context=None, known_normalized_names=...)` now takes `targets` (what needs a translation returned) SEPARATE from `context` (the whole scan's ingredient list, for disambiguation). `GeminiService.translate_ingredient_list` and its prompt template were updated to send both explicitly (`fullIngredientList` vs `targetEntries`), asking the model to use the former as context but return translations ONLY for the latter, keyed by exact original text. The caller (`ingredient_catalog._resolve_ingredient_languages`) now builds `full_context` from every ingredient this scan actually saw (curated + synthetic), not just the subset still needing translation.

Also fixed while verifying this (found via a new test, not a code-review item): `_resolve_ingredient_languages` only checked for an existing alias by EXACT normalized text before deciding to translate -- a foreign-language OCR reading that still carries a genuine E-number (definitive identity on its own) was being wastefully sent for translation even though `get_or_create_catalog_ingredient` would resolve it via the E-number regardless. Added an official-identifier pre-check (mirrors task 1's "local-catalog resolution first, using established identifiers") alongside the alias check.

Tests: `tests/unit/test_ingredient_translation.py` -- reordered, response-entry-not-in-targets (discarded), duplicate-target (both matched / one matched one missing), missing target, extra response entries, and a near-miss (non-exact) `originalText` match. `tests/integration/test_ingredient_bilingual_contract.py` -- E-number pre-check reuse test.

### 3. Ordinary allergen emphasis is not broken segmentation -- FIXED (subagent, reviewed)

`ingredient_segmentation._has_embedded_allergen_emphasis` flagged ANY token mixing a lowercase word with an ALL-CAPS word of 3+ letters -- over-broad: EU labels routinely emphasize a single allergen word in caps within an otherwise ordinary compound name ("MILK powder", "ZARA pudră"). Removed entirely, with no milder capitalization-threshold variant substituted (matches the task's explicit instruction not to replace it with another blanket script rule). Replaced with a narrower, STRUCTURALLY-grounded check: a literal, un-split `:` still inside the token (`_has_unsplit_colon_clause`, new reason code `COLON_SEPARATED_CLAUSE_MERGE`) -- real evidence tokenization's own colon-exclusion (`ocr_normalizer.normalize_and_extract_tokens` never splits on `:`) left a genuine clause-header pattern merged into one token. `_has_duplicate_word` (repeated significant word) and the 7+-word length check are unchanged.

Of the task's 7 original examples: "Ulei de rapiță"/"Semințe de mac"/"Aluat acrisor" were never flagged (unchanged). "LAPTE proteină din LAPTE" is STILL flagged (`DUPLICATE_TOKEN_FRAGMENT` -- the same word repeated is real evidence). "SECARA agenți de creștere"/"Produs din GRAU"/"ZARA pudră" are NO LONGER flagged -- a deliberate, correct behavior change (they were false positives from the old heuristic, not genuinely malformed) -- confirmed and re-tested end-to-end (they now reach translation instead of being blocked).

Tests: `tests/unit/test_ingredient_segmentation.py` rewritten (23 tests) with all 7 original examples plus "MILK powder", colon-clause positive cases, and edge cases. Downstream fallout from the intentional behavior change fixed in 3 files (outside the subagent's scope, fixed by this session): `tests/integration/test_ingredient_language_identity_e2e.py` (split the old 4-way parametrized test into a genuinely-ambiguous-only case plus a new "previously over-flagged examples now translate" case), `tests/integration/test_repair_ingredient_language.py` (fixture changed from a caps-based example to a colon-based one), `tests/integration/test_scan_language_e2e.py` (same split, at the HTTP level).

### 4. Truthful diagnostics -- FIXED

- **Barcode partial results**: `scan_barcode` had no `ProductNotFoundError`-specific branch at all (only generic `AppError`/`Exception`), so a `labelScanRequired` partial result was recorded `outcome="failed"`. Added the same partial/failed branch the other two endpoints already had -- but discovered `labelScanRequired` alone is set by BOTH `_label_scan_required_details` (a real partial identity) AND `_not_found_details` (nothing found at all, same suggested next action), so it can't distinguish them. Added `_is_partial_result`, keyed on `discoveredIdentity` presence (only ever set by the genuine partial case) -- applied consistently to all three endpoints, not just barcode.
- **`dataSource`**: previously a hardcoded literal naming the operation ("barcode"/"ocr_text"/"label_image"). Now `_observed_data_source` reports the REAL origin: `"cache"` when `is_from_database_cache` is true, else the row's own persisted `Product.source` (a real provider name, "local", "label_scan", etc.) once a product is known, `exc.details.get("dataSource")` for a partial result (new field added to `_label_scan_required_details`, sourced from the same real `product.source`), or `None` when genuinely unknown (no product ever existed for this request). `operation` (which endpoint) is kept as a separate field.
- **Serialization/computed-field failures**: `_to_analysis_out(result)` only runs Pydantic field validation, not full JSON encoding -- nested `@computed_field`s (e.g. `adiPopulationScope`, `localizations`) evaluate later, inside FastAPI's own response rendering, OUTSIDE any endpoint try/except. Added `_finish_serializing`, which calls `out.model_dump(mode="json", by_alias=True)` (discarding the result) INSIDE the same try block right after construction -- any failure there now correctly falls into the generic failure-diagnostic path instead of leaving a false "success" record.
- **Translation attempt/result/reason**: previously inferred from `product.source == "label_scan_translated"` only (a boolean proxy). `_finalize_barcode_enrichment`/`_finalize_standalone_label_analysis` now return the real `label_language_status`/`label_translation_used`/`label_detected_language` (straight from `LabelTextResult`), and `scan.py`'s new `_translation_fields` maps `status` ("ok"/"translated"/"translation_unreliable_fallback") to `translationAttempted`/`translationResult`/`translationReason` -- absent (not guessed) on the barcode-only path, which never computes a `label_result` at all.
- **One summary record per operation**: audited -- every code path writes exactly one diagnostic record then either re-raises or returns; no duplication introduced.
- Bounded rotation, multi-process `flock` safety, and the existing privacy exclusions in `app/core/scan_diagnostics.py` are UNCHANGED by this pass.

Tests added to `tests/integration/test_scan_diagnostics_endpoints.py`: barcode `labelScanRequired` partial classification (distinct from a genuinely-unknown-barcode failure), cache-vs-fresh `dataSource` attribution, and a computed-field/full-serialization failure distinct from the pre-existing `_to_analysis_out`-construction-failure test (forces `_finish_serializing` itself to raise).

### 5. Android contract completion -- FIXED

- `ProductOut` gained `originalIngredientText: str = ""` and `ingredientTextSourceLanguage: str | None = None` (the `Product` columns already existed from the previous pass; they were just never exposed on the response schema). Additive, backward-compatible, truthful empty/null defaults matching the underlying column defaults exactly. Covered via the real `/scan/ocr-text` -> `GET /products/{barcode}` round trip.
- **EN/BG display-name contract, clarified and tested** (`tests/integration/test_ingredient_bilingual_contract.py`, new file, entirely documentation-via-tests -- no production behavior changed here, this codifies and proves EXISTING `build_localizations` behavior which was previously untested for the translation-pipeline interaction): a freshly-translated (never-curated) synthetic ingredient's `localizations` has `en` only (genuinely English, since translated text always becomes the canonical `common_name`) and NEVER a `bg` key -- translating a foreign name into English does not fabricate or claim any Bulgarian coverage. The runtime translation path (`ingredient_translation.py`) never writes an `IngredientLocalization` row at all (verified directly against the DB) -- so nothing from it could ever be marked REVIEWED, automatically or otherwise; only the curated seed loader (`app/seed/load_seed.py`) ever writes a reviewed localization. When a foreign-language OCR token resolves via an official identifier or alias to an EXISTING CURATED ingredient instead, the full curated profile -- including any real reviewed Bulgarian localization -- is served as-is (identity resolution, not translation, is what connects foreign-language input to already-curated bilingual content). `effectConditions`/`dietaryGuidance` confirmed empty (never fabricated) on a translated ingredient, both at the top level and inside `localizations.en`.

### Verification (this pass)

- `pytest tests/unit tests/integration -q` (pinned Python 3.12, this branch's own Docker image): **582 passed, 0 failed, 0 skipped** (2 pre-existing, unrelated SAWarnings).
- `pytest tests/postgres -q` (disposable PostgreSQL 16, `NUTRIGUARD_TEST_POSTGRES_URL`): **10 passed**.
- No schema/model changes in this pass -- migration head unchanged at `c6d7e8f9a0b1`; no upgrade/downgrade cycle needed (confirmed by re-running `alembic upgrade head` cleanly against a fresh disposable Postgres before the above).
- `app.openapi()` vs. tracked `openapi.json` (pinned deps): **exact match** -- diff confined to `ProductOut` (2 new fields), purely additive.
- Live dev stack/database: **never touched** -- disposable Docker containers/network, removed at the end of this session; live checkout's uncommitted handoff edit from the earlier deployment task left exactly as found.

### Files touched this pass

Modified: `app/api/v1/scan.py`, `app/integrations/gemini.py`, `app/repositories/ingredient_alias_repository.py`, `app/schemas/product.py`, `app/services/food_analysis.py`, `app/services/ingredient_catalog.py`, `app/services/ingredient_segmentation.py` (subagent), `app/services/ingredient_translation.py`, `openapi.json`, and 6 existing test files updated for new signatures/intentional behavior changes.

New: `tests/integration/test_ingredient_bilingual_contract.py`.

### Remaining limitations (unchanged from the previous entry, still accurate)

Real curated `effectConditions`/`dietaryGuidance` content for the 12 seed ingredients is still not authored (deliberately -- needs product-owner/scientific review, not a backend guess). `Product.original_ingredient_text`/`ingredient_text_source_language` are now exposed on `ProductOut` (this pass closes that gap). The dry-run repair tool (`python -m app.seed.repair_ingredient_language`) was still not run against any live database.

---

## 2026-09-17: ingredient-language identity, info contract, and scan diagnostics (Claude Code, isolated worktree)

- Branch `feat/backend-ingredient-language-diagnostics`, based on `origin/main` at `8d742eb773ebe015b7fcfcefd19869ab129f7644` (PR #18 merge), worked entirely in a separate `git worktree`
  (`/home/vboxuser/nutriguard-worktrees/feat-backend-ingredient-language-diagnostics`) so the live dev stack/checkout/database was never touched. Used two background subagents for the dry-run repair script and the verification test suite (both reported back, both reviewed/integrated by this session; see "Findings from delegated work" below).

### Task 1: consistent ingredient identity and language

**Root cause (traced, not assumed OCR-only)**: `app/services/ingredient_catalog.materialize_ingredients` persisted every OCR/Gemini-observed ingredient name into the shared catalog BEFORE any language/translation policy ever ran (the existing `label_language.resolve_label_text` translator only ran later, on the whole raw-text blob, inside the barcode/standalone "rebuild" step). Two further compounding bugs: `label_language._split_segments` never splits on commas, so ONE English trigger word (e.g. "Ingredients:", "Contains") anywhere in a large mixed-language block caused the WHOLE block -- including embedded foreign-language ingredient names -- to be accepted as `"en"` verbatim, skipping translation entirely; and `ocr_normalizer.normalize_and_extract_tokens` never splits on `:`, so an EU-style allergen-emphasis clause (e.g. "SECARA: agenti de crestere") can survive as one merged token. Confirmed the barcode-discovery path (Open Food Facts) was already correctly language-gated (V7) -- not a source of this bug.

**Fix** -- language resolution now runs ONCE, at the single choke point all 5 ingredient-assembly entry points funnel through:
- `app/services/language_detection.py` (pre-existing, untouched) -- per-token `detect_language`.
- NEW `app/services/ingredient_segmentation.py` -- `detect_ambiguous_segmentation(token)`: flags suspected OCR concatenation (embedded ALL-CAPS allergen-emphasis word, duplicated significant word, 7+-word unsegmented clause) with a stable reason code; NEVER attempts to re-segment or translate a flagged token ("translation alone must not certify identity"). Verified against all 7 examples from the reported bug: the 3 clean Romanian names translate cleanly; the 4 concatenated fragments ("SECARA agenți de creștere", "Produs din GRAU", "ZARA pudră", "LAPTE proteină din LAPTE") all correctly flag.
- NEW `app/services/ingredient_translation.py` -- `translate_ingredient_tokens(tokens)`: batches an ENTIRE ingredient list's untranslated tokens into ONE Gemini call (full-list context, task requirement), via a new `GeminiService.translate_ingredient_list` prompt (`app/integrations/gemini.py`). Per-entry structured validation + independent invariant checks (confidence >= 0.55, non-placeholder, E-number/numeric-token multiset preservation, and an English-plausibility check) -- one bad entry never invalidates the batch. The English-plausibility check needed a real fix mid-task (see "Findings" below).
- `app/services/ingredient_catalog.py`: `materialize_ingredients` now returns `(materialized, translation_occurred)` and runs a new `_resolve_ingredient_languages` pass FIRST: already-en/bg/unknown -> pass through; an alias already exists for the exact original text -> skip translation, reuse (no re-translation cost -- "don't translate again for every product/request"); ambiguous -> flag `identity_uncertain`, never translate; otherwise -> batch-translate. A reliable translation is rebuilt via `create_synthetic_ingredient` on the NOW-English text (correctly recomputing id/category AND allergen-keyword detection -- a real incidental fix, since the keyword list is English-only and previously never fired on untranslated foreign text). `_build_minimal_row` sets `source=GEMINI`+capped confidence for a translated row -- the PRE-EXISTING `SOURCE_PRIORITY`/`merge_verified_fields` gating (untouched) already guarantees GEMINI can never reach `VERIFIED`, so translation can never promote scientific verification. After resolving, the pre-translation text is registered as its own language-tagged `IngredientAlias` (reuses the ALREADY-EXISTING but previously-unpopulated `IngredientAlias.language` column).
- `Product` gained `original_ingredient_text`/`ingredient_text_source_language` (new columns, migration below) -- the TRUE pre-translation label text is now always preserved separately from `raw_ingredient_text` (which stays the canonical/identity-bearing text `ocr_normalizer.reconstruct_synthetic_ingredient` re-tokenizes on every later read -- keeping these two in sync, only overriding when translation actually changed something, was the single most important correctness property verified here; see the migration/Postgres cycle below).
- API: `IngredientOut` gained `identityUncertain`/`uncertaintyReason` (structured signal for Android to offer another label photo, per task requirement).

**Dry-run repair for old bad records** (delegated, reviewed): NEW `app/seed/repair_ingredient_language.py`, invoked as `python -m app.seed.repair_ingredient_language [--apply]` (mirrors `load_seed`'s convention). Dry-run by default; `--apply` required to write. Per-row SAVEPOINT, one commit at the end (or a `rollback()` in dry-run -- zero writes, confirmed by test); idempotent by construction (a repaired row is `ALREADY_FINE` on the next run). Never touches `CURATED_SEED`/`REGULATORY_LOOKUP` rows or ambiguous-segmentation rows; preserves the original text as an alias before overwriting `common_name`. `Product.raw_ingredient_text` is report-only (never rewritten -- rewriting it has correctness implications for id reconstruction that are out of scope for a repair tool). NOT run against any live database in this session.

### Task 2: ingredient information contract

Audited existing fields first: `description`/`purposeInFood`/`healthConcerns`+`sideEffects`/`references` already existed and already satisfy 4 of the 6 requested bullets. Genuinely missing: a structured "conditions under which effects apply" and "intake guidance distinct from ADI". Added, minimally:
- `Ingredient`/`IngredientLocalization` gained `effect_conditions`/`dietary_guidance` (new columns, wired through the EXISTING `LOCALIZED_FIELDS`/`build_localizations` EN/BG machinery) -- camelCase `effectConditions`/`dietaryGuidance` on the wire, `""` (never a placeholder) when the curated source states none.
- NEW `IngredientOut.adiPopulationScope` (`app/services/ingredient_regulatory.derive_gated_adi_population_scope`) -- `"PER_KG_BODY_WEIGHT"` exactly when a gated numeric ADI was parsed, else `null`. Makes explicit, on the wire, that the ADI is per-bodyweight, never a universal daily amount, and never a fabricated male/female split (the source text draws no such distinction, so none is invented).
- Deliberately did NOT author real `effectConditions`/`dietaryGuidance` curated content for the 12 seed ingredients in this pass -- the machinery is implemented and tested, but populating real scientific content needs a product-owner/scientific review pass, not a backend-engineering guess under this task's own "report missing scientific content honestly" instruction. Left as an explicit follow-up.
- Confirmed (structurally, via a test) that no field anywhere in the API claims a product "exceeds" an ingredient's limit -- no such field exists, and none was added; the backend has no per-product ingredient-quantity data to support such a claim in the first place.

### Task 3: scan diagnostics

Audited `app/core/scan_diagnostics.py` (bounded rotation, POSIX `flock` multi-process safety) -- UNCHANGED, already correct. Gaps were entirely at the call-site layer (`app/api/v1/scan.py`, rewritten):
- Previously only `/scan/label-image` wrote diagnostics; `/scan/barcode` and `/scan/ocr-text` had zero coverage. All three now call a `record_scan_diagnostic` wrapper.
- `/scan/label-image`'s early validation failures (bad content-type, oversized image) previously raised before `diagnostic_base` was even built, so they were silently uncovered -- now diagnosed too.
- The "success" diagnostic write previously happened BEFORE `_to_analysis_out(result)` (response construction); a hypothetical serialization failure there would have left a false "success" record. Moved inside the same try block -- a serialization failure now correctly falls into the generic failure-diagnostic path.
- New fields: `operation`/`dataSource` (barcode/ocr_text/label_image), coarse honest `stage` (only what this router layer actually observes -- never a guess at `food_analysis`'s internal steps, which aren't instrumented), `detectedLanguage` (from the new `Product.ingredient_text_source_language`), `translationUsed` (kept its original name from the pre-existing label-image-only diagnostic, not renamed), and new `recognizedIngredientCount`/`unresolvedIngredientCount`/`untranslatedIngredientCount` (computed from the real `identity_uncertain`/`uncertaintyReason` values on the returned ingredients, never guessed).
- Reuses the EXISTING `request.state.request_id` correlation id (already bound into structlog contextvars app-wide) -- no second id scheme introduced.
- Added local defense-in-depth: every call site now goes through `_safe_record_scan_diagnostic`, which locally catches+logs rather than relying solely on `record_scan_diagnostic`'s own internal guarantee never regressing (task: "diagnostic failures must never break scanning" -- verified by monkeypatching `record_scan_diagnostic` to raise and confirming all three endpoints still return their normal response/error).
- Default storage budget (`SCAN_DIAGNOSTICS_MAX_BYTES`=1 MiB, `SCAN_DIAGNOSTICS_BACKUP_COUNT`=1) left unchanged, per task instruction. No periodic/automatic reporting added.

### Migration

`alembic/versions/c6d7e8f9a0b1_ingredient_language_provenance.py`, `down_revision="b5c6d7e8f9a0"`. Adds: `products.original_ingredient_text`/`ingredient_text_source_language`; `ingredients.identity_uncertain`/`uncertainty_reason`/`effect_conditions`/`dietary_guidance`; `ingredient_localizations.effect_conditions`/`dietary_guidance`. Verified upgrade -> downgrade -> upgrade against a disposable PostgreSQL 16 instance with representative pre-existing data (a curated `Ingredient` with a REVIEWED Bulgarian localization, a `Product` referencing it, and a language-tagged `IngredientAlias`) -- all data and all 8 new columns survived every step correctly; `alembic heads` -> single head, `c6d7e8f9a0b1`. All 10 opt-in `tests/postgres/` concurrency tests re-run against the migrated schema: 10/10 passed.

### Findings from delegated work (both reviewed and fixed by this session before finalizing)

1. **Real bug, fixed**: `ingredient_translation._translation_is_reliable`'s English-plausibility check (`detect_language(translated_text) == "en"`) used the SAME word-frequency threshold `language_detection` uses for a whole label-text blob (needs one strong or two distinct weak recognized words) -- which a short, correct, 1-2 word ingredient translation (e.g. "MILK" -> "Milk") can never reach, so it was being wrongly rejected as "unreliable" 100% of the time. This directly undermined the task's own "Lapte"/"MILK" canonical-convergence example. Fixed with a narrow, bounded fallback: a <=2-word, plain-ASCII translated result is also accepted (independent script-level evidence it isn't still-foreign text), while longer text still goes through the stricter word-based check only (verified this doesn't let a longer ASCII-only foreign phrase, e.g. French "Lait Entier Complet", slip through).
2. **Defense-in-depth gap, fixed**: `app/api/v1/scan.py`'s diagnostic-write call sites had no LOCAL protection against `record_scan_diagnostic` raising, relying entirely on that function's own (already-correct) internal guarantee. Added `_safe_record_scan_diagnostic`; see Task 3 above.
3. **Deferred, not a bug**: `Product.original_ingredient_text`/`ingredient_text_source_language` are not exposed on `ProductOut` (only used internally for diagnostics) -- intentional for this pass; exposing them to Android (e.g. so the app can show "as printed" vs. "translated") is a reasonable follow-up if wanted.

### Verification

- `pytest tests/unit tests/integration -q` (pinned Python 3.12 image, built from this branch's own `Dockerfile`/`requirements.txt`): **562 passed, 0 failed, 0 skipped** (2 pre-existing, unrelated SAWarnings).
- `pytest tests/postgres -q` (disposable PostgreSQL 16, `NUTRIGUARD_TEST_POSTGRES_URL`): **10 passed**.
- Alembic upgrade -> downgrade -> upgrade cycle: see "Migration" above.
- `app.openapi()` vs. tracked `openapi.json` (pinned deps): **exact match** -- diff confined to the two changed schemas (`IngredientOut`, `IngredientLocalizedTextOut`), purely additive, no path changes.
- Live dev stack/database: **never touched** -- all verification used disposable Docker containers/networks and a separate git worktree, removed at the end of this session.

### Files involved

New: `app/services/ingredient_segmentation.py`, `app/services/ingredient_translation.py`, `app/seed/repair_ingredient_language.py`, migration `c6d7e8f9a0b1`, and 8 new test files (`tests/unit/test_ingredient_segmentation.py`, `tests/unit/test_ingredient_translation.py`, `tests/unit/test_ingredient_language_provenance_migration.py`, `tests/integration/test_ingredient_language_identity_e2e.py`, `tests/integration/test_scan_language_e2e.py`, `tests/integration/test_scan_diagnostics_endpoints.py`, `tests/integration/test_repair_ingredient_language.py`).

Modified: `app/models/ingredient.py`, `app/models/ingredient_localization.py`, `app/models/product.py`, `app/schemas/ingredient.py`, `app/services/ingredient_catalog.py`, `app/services/ingredient_localization.py`, `app/services/ingredient_regulatory.py`, `app/services/ocr_normalizer.py`, `app/services/fallback_analysis.py`, `app/services/food_analysis.py`, `app/integrations/gemini.py`, `app/api/v1/scan.py`, `openapi.json`, plus 3 existing test files updated for the new `materialize_ingredients` signature and the two additive field-set pin tests.

### Recommended next step

Review the branch diff and open PR, then merge only after CI/human review is green. Two deliberate follow-ups flagged for a future task, not blockers: (1) real curated `effectConditions`/`dietaryGuidance` content for the 12 seed ingredients, pending product-owner/scientific review; (2) whether `Product.original_ingredient_text`/`ingredient_text_source_language` should be exposed on `ProductOut` for Android. The dry-run repair tool (`python -m app.seed.repair_ingredient_language`) should be run for review (dry-run first) against the live database by a human before ever passing `--apply` there -- this task deliberately never did so.

---

## 2026-09-16: PR #18 pre-merge verification (Claude Code, isolated worktree)

Full independent verification of PR #18 (`feat/app-bilingual-enrichment`,
commit `eceaaff017737f82815fc630fd2b1f28b7b3b820`) requested ahead of
merge. Performed entirely in a separate `git worktree` (detached HEAD at
that exact SHA) and disposable Docker containers/network -- the live
dev stack (`nutriguard-backend-db-1`/`-redis-1`/`-backend-1`) and the
main checkout were never touched, switched, or stopped.

**1. Target verification**: `origin/feat/app-bilingual-enrichment` ==
`eceaaff017737f82815fc630fd2b1f28b7b3b820`, matching the requested SHA
exactly, re-checked again after finishing (no remote drift). GitHub
Actions for this SHA: both `tests` and `unit-tests` checks
`completed`/`success` (checked via the public REST API).

**2. Full backend suite** (pinned deps, Python 3.12, built from this
branch's own `Dockerfile`/`requirements.txt`, env isolated from any
`.env`): `python -m pytest -q` -> **486 passed, 10 skipped, 0 failed**
before this pass's additions; **491 passed, 10 skipped, 0 failed**
after (see item 6). All 10 skips are the pre-existing opt-in
`tests/postgres/` suite (needs `NUTRIGUARD_TEST_POSTGRES_URL`), not
failures.

**3. Disposable PostgreSQL 16 migration cycle** (unique container/
network, removed after use; live DB never touched, never downgraded):
- `alembic upgrade` to the new migration's actual parent
  (`a4b5c6d7e8f9`, NOT `e4f5a6b7c8d9` -- `main` has advanced two more
  migrations, `f5a6b7c8d9e0` and `a4b5c6d7e8f9`, since the last
  handoff entry below was written), inserted a pre-existing
  Ingredient(E951 Aspartame)/Product row pair using the OLD column
  set, then `upgrade -> b5c6d7e8f9a0 -> verify -> downgrade ->
  a4b5c6d7e8f9 -> verify -> upgrade -> b5c6d7e8f9a0 -> verify`: **the
  pre-existing ingredient row, its product's `ingredient_ids`
  reference, the `ingredient_localizations` table, its composite PK
  (`ingredient_id`,`language`), its `ON DELETE CASCADE` FK, and its
  `language IN ('en','bg')` CHECK constraint all survived/reappeared
  correctly at every step.** Also directly verified: a duplicate
  `(ingredient_id, language)` insert is rejected (PK uniqueness);
  `language='fr'` is rejected (CHECK constraint); deleting the parent
  Ingredient cascades and removes its localization row.
- Seed loader run twice against the same disposable instance:
  **idempotent** -- identical `(ingredients=47, aliases=58,
  bg_localizations=12)` after both runs, no duplicate
  `(ingredient_id, language)` rows. All 12 reviewed Bulgarian rows
  verified attached to the correct canonical curated ingredient id
  (e.g. `e621_msg` -> "Monosodium Glutamate" / "Мононатриев глутамат").
- `alembic heads`: **exactly one head**, `b5c6d7e8f9a0`.
- All 10 opt-in `tests/postgres/` tests (unrelated pre-existing
  concurrency/identifier-precedence suites) also re-run against this
  same disposable instance after migrating to head: **10/10 passed**
  (one transient failure was traced to leftover data from this
  session's own earlier ad-hoc script runs against the same
  container, not a product bug -- resolved by resetting the schema;
  10/10 passed again from a clean slate).

**4. OpenAPI**: `app.openapi()` regenerated inside the pinned-dependency
image and compared to the tracked `openapi.json` -- **exact match, 0
differences**. No changes needed.

**5. Localization behavior**: read through `Ingredient.
loaded_localization_rows`, `IngredientOut.localizations`, and
`ingredient_localization.build_localizations` (used by both the
Pydantic schema path and the hand-built `_ingredient_out_dict` partial-
response path). Confirmed by inspection and by the new tests in item 6:
existing English top-level fields are untouched; only a `REVIEWED` +
content-hash-matching Bulgarian row is ever surfaced (a `DRAFT` row or
one whose hash no longer matches the current English text falls back
to English); identifiers/E-numbers/ADI/enums/citations/URLs are never
duplicated into the localized profile; translation review status lives
on a wholly separate table/enum from `Ingredient.verification_status`
and is never written together with it anywhere in `load_seed.py` or
`ingredient_catalog.py`, so a translation being `REVIEWED` cannot
promote the underlying scientific/regulatory evidence.

**6. Regression coverage added** (task requirement: exercise the
MissingGreenlet fix with actual SQLAlchemy objects, not mocks -- the
existing unit tests in `test_ingredient_schema_data_quality.py`/
`test_ingredient_localization*.py` only used `SimpleNamespace`/a hand-
written fake class for the "unloaded relationship" case). New file
`tests/integration/test_ingredient_localization_response_paths.py`
(+5 tests, all passing):
- Forced a REAL, persistent `Ingredient` row's `localization_rows` to
  come back genuinely unloaded (via an explicit `lazyload()` query
  option -- confirmed no normal repository query anywhere in `app/`
  does this itself; `lazy="selectin"` already protects every default
  query path). Proved touching the raw relationship synchronously
  reproduces the exact `MissingGreenlet` error CI hit, and that both
  serializers (`IngredientOut.model_validate` and
  `_ingredient_out_dict`) correctly fall back to English-only instead
  of raising.
- End-to-end, through the real FastAPI app with a real reviewed
  Bulgarian row attached to a real Ingredient: `GET /products/
  {barcode}` (normal product response), `GET /ingredients` (product
  listing), a hand-built `labelScanRequired` partial response
  (`_label_scan_required_details`), and `POST /scan/label-image` with
  a Gemini-mocked payload resolved via official E-number match to the
  same curated row (barcode/label scan) -- all four correctly surface
  the reviewed Bulgarian localization alongside the unchanged English
  fields, eNumber, and citations.
- Full suite after adding these: **491 passed, 10 skipped, 0 failed**
  (pinned image). OpenAPI re-confirmed exact match after the rebuild
  (test-only change, no schema/route touched).

**Files involved this pass**: only
`tests/integration/test_ingredient_localization_response_paths.py`
added (new file). No `app/` code changed -- no reproducible product
bug was found; the only gap was test coverage, now closed.

**Unresolved / carried forward**: none new. Everything listed as
unresolved in the "app-wide EN/BG" entry immediately below this one
remains accurate context but is otherwise unaffected by this pass.

**Recommended next step**: commit and push this one new test file to
`feat/app-bilingual-enrichment` (no `app/` changes, so no re-review of
production behavior is needed beyond this file) and merge PR #18 once
CI is green on the resulting commit. Live deployment was not touched
at any point in this session.

---

## 2026-09-16: CI regression fix for unloaded ingredient localizations

- PR #18's first backend CI run failed with 20 `MissingGreenlet`
  errors while Pydantic serialized `Ingredient.localization_rows` from
  legacy product-query paths that had not preloaded the new async
  relationship. Product data itself was valid; synchronous response
  serialization was accidentally attempting hidden database I/O.
- Added `Ingredient.loaded_localization_rows`, a SQLAlchemy-state-aware
  view that returns reviewed translations only when the relationship is
  already loaded. `IngredientOut` now validates its internal translation
  rows through that safe view, and `build_localizations` follows the same
  rule. An unloaded relationship therefore produces the canonical
  English profile instead of HTTP 500; normally preloaded reviewed
  Bulgarian profiles remain unchanged.
- Added a regression test whose unsafe relationship accessor raises if
  touched, proving schema validation uses the no-I/O path and returns an
  English localization.
- Files involved: `app/models/ingredient.py`,
  `app/schemas/ingredient.py`, `app/services/ingredient_localization.py`,
  and `tests/unit/test_ingredient_schema_data_quality.py`.
- Local verification: `git diff --check` passed. The Windows workspace
  has no backend Python/Docker runtime, so the full backend suite is to
  be verified by the new GitHub Actions run after this fix is pushed.
- Unresolved issue: none in the diagnosed serialization path; CI remains
  the required full-suite confirmation.
- Recommended next step: push the focused fix to PR #18 and require both
  Android and backend checks to be green before considering merge.

## 2026-09-11: app-wide EN/BG and reviewed ingredient localizations

- Android and backend were updated together on the existing
  `fix/ingredient-details-nova-ui` branch; no commit, push, merge or
  deployment was performed in this task.
- Backend: added `ingredient_localizations`, keyed by ingredient and
  language, with review/source provenance and canonical-content hashes.
  Existing English fields remain canonical/backward compatible. The
  API additively returns `localizations.en` and a current reviewed
  `localizations.bg`; identifiers, numeric ADI, enums and URLs remain
  unchanged. Twelve rich seed ingredients have Bulgarian profiles
  marked `MACHINE_TRANSLATED` + `REVIEWED`.
- Android: added persistent localization JSON to the Room ingredient
  cache (schema 3→4), resolves Bulgarian with per-field English
  fallback, and applies it to ingredient cards, details and library
  search. A persistent EN/BG control now switches app-owned UI text,
  dates, accessibility labels and available scientific profiles without
  rescanning. Original product names, brands and OCR label text remain
  unchanged.
- Files involved: new backend localization model/service/seed/migration
  and related schema/seed/serializer/tests; Android i18n components,
  DTO/Room mapping, localized ingredient presentation and tests.
- Verification: Android `:app:testDebugUnitTest :app:lintDebug
  --rerun-tasks` passed after all changes: **116 tests, 0 failed, 0
  skipped**, and lint succeeded. The Windows host has no Python or
  Docker runtime, so the complete backend pytest suite, pinned OpenAPI
  regeneration and disposable-PostgreSQL migration cycle still need to
  run on the backend VM/CI. The tracked OpenAPI snapshot was updated to
  the additive localization shape and parses as valid JSON, but must be
  compared byte-for-byte with the pinned runtime generator there.
- Recommended next step: run both full suites, regenerate and review
  `openapi.json` using the pinned backend dependencies, verify the new
  Alembic migration against disposable PostgreSQL, then commit and open
  a PR only after review.

## 2026-09-09: E-number starter knowledge and compact Android presentation

- Branch: `feat/e-number-knowledge-ui`, based on GitHub `origin/main`
  at `c94c20193602d9b90cf60d090dff7f50f383ba95`.
- Added `app/seed/e_additives_curated_starter.csv`: exactly the 43
  populated rows from the provided NutriGuard E-number starter pack.
  The companion E200–E999 registry was deliberately not imported: 757
  of its 800 rows are coverage placeholders, not confirmed additives.
- `app.seed.load_seed` now adds 35 previously absent E-number identities
  (8 overlap richer existing seed rows and are never overwritten), for
  47 total catalog ingredients. New starter rows are deliberately
  `LIMITED_DATA`, `risk_assessment_available=False`, with nullable
  dietary flags. Their identity, function, available health text and
  informational source URLs are reusable; they do not affect Health
  Score or claim regulatory approval/ADI without stronger per-field
  evidence.
- Android now parses the backend's structured ingredient data-quality
  fields (`riskAssessmentAvailable`, gated EFSA/FDA status, numeric ADI,
  rationale and sources) and preserves explicit JSON `null` dietary
  flags. It no longer fabricates `Approved`, `GRAS`, `Safe`, `None
  reported`, or generic OCR profile text.
- Ingredient cards and the detail sheet omit absent information.
  EFSA/FDA appear only as compact approved/not-approved icon rows;
  numeric ADI appears only when supplied; references/URLs are collapsed
  in a final Sources section. Room migration 2→3 preserves product,
  history and profile data while updating the ingredient cache schema.
- Verification: Android `:app:testDebugUnitTest` passed. Backend loader
  files pass `py_compile`; the backend pytest environment is not
  installed on this Windows host, so `test_load_seed.py` still requires
  execution in backend CI or the VM's pinned Docker environment.

## Current work

Date: 2026-09-04

- Branch: `feat/backend-ingredient-profile-data-quality`, based on
  GitHub `origin/main` at `4b44b8f04e95bc1fa58fe27546f17dcab605d562`.
  Fourth task on this same (still unmerged) branch — see "V16", "V15"
  and "V14" below for the first three tasks' own handoff summaries,
  preserved as-is.
- Backend-only changes (`nutriguard-backend/` only); `android-app/**`,
  `main`, the live database/Docker volumes, `.env`, and credentials
  were not touched.

### V17: resolved the documented deterministic PostgreSQL test failure

The V16 entry below reported
`test_concurrent_enrichment_of_the_same_existing_row_preserves_both_groups`
as "a pre-existing, unrelated test issue, found and reported (not
fixed)". This task's own instruction pushed back correctly: it IS in
the same product/ingredient enrichment and concurrency area, so it
needed to actually be resolved and proven, not left as "unrelated"
by assertion alone. Root-caused and fixed here.

**Reproduction (task requirement 1) — all three scenarios, against a
fresh disposable PostgreSQL 16 instance, before any fix:**

- *Alone*, 10 repeated fresh-DB runs: **8 passed, 2 failed** — already
  disproves last task's working theory ("passes alone, fails with its
  sibling"); that theory was drawn from a single alone-run and was
  itself wrong. It's flaky alone too.
- *Together with its sibling test* (`test_concurrent_enrichment_of_the_same_new_barcode_converges_on_one_row`),
  5 repeated fresh-DB runs, both orders: **failed 5/5 in normal order**
  in this batch, **passed 3/3 in reversed order** in a separate batch
  — inconsistent with either file/order being the cause; consistent
  with pure timing-based nondeterminism.
- *As part of the complete opt-in `tests/postgres/` directory*: same
  pattern — pass/fail tracked the individual test's own race outcome,
  not directory composition.

**Determination (requirement 2) — a 15-iteration diagnostic script**
(`asyncio.gather`-ing the exact two concurrent calls the test makes,
printed per-call, not part of the repo) against real Postgres nailed
the exact mechanism:

- **15/15 iterations**: the FINAL persisted row was IDENTICAL and
  fully correct — `hasVerifiedIngredients=True`,
  `hasVerifiedNutrition=True`, `isVerified=True`, `healthScore=85`.
  The `for_update=True` row-lock concurrency guarantee never once
  failed. This rules out *transaction isolation/session reuse* and
  *a real production concurrency bug* outright — nothing was ever lost
  or corrupted.
- **12/15 iterations**: the INGREDIENTS-photo call's transaction
  committed first → it got `SUCCESS(healthScore=None)` (a normal `200`,
  not `labelScanRequired`) → the NUTRITION-photo call committed second,
  saw both groups, got `SUCCESS(healthScore=85)`. Two successes, zero
  `labelScanRequired` — exactly the pattern the OLD assertion rejected.
- **3/15 iterations**: the NUTRITION-photo call committed first → it
  got `labelScanRequired` (nutrition alone was never enough) → the
  INGREDIENTS-photo call committed second, saw both groups, got
  `SUCCESS(healthScore=85)`. One success, one `labelScanRequired` —
  the ONLY pattern the OLD assertion accepted.
- Full DB resets between every iteration (fresh migrate each time)
  rule out *test-state leakage/order dependence* as well — the same
  two outcomes occur from a clean slate, in either order, with no
  prior test having run at all.

**Root cause: a stale assertion, citing the exact code that supersedes
it** (requirement 3) — `app/services/food_analysis.py`,
`_finalize_barcode_enrichment`'s own "SUCCESS GATE (V13...)" comment
block (search that file for `SUCCESS GATE`): "label-driven product
enrichment succeeds once ingredient RECOGNITION succeeds... Missing/
incomplete NUTRITION alone never fails this endpoint any more." Also
`README.md`'s "V13" Changelog entry and section 11.14
("Ingredient-recognition success vs. health-score readiness"). V13
predates this branch entirely (already on `origin/main`) and is
otherwise fully, correctly implemented and already covered
deterministically elsewhere
(`tests/integration/test_label_barcode_enrichment.py::test_barcode_nutrition_plus_later_ingredients_only_photo_completes_product`
and `::test_barcode_ingredients_plus_later_nutrition_only_photo_completes_product`
cover both sequential orderings already) — only this ONE concurrency
test's assertion never accounted for it.

**Fix applied (requirement 3 — assertion + description only, nothing
in `app/` touched, no guarantee weakened):**
`tests/postgres/test_concurrent_enrichment_postgres.py`:
- Docstring rewritten to state the ACTUAL invariant (exactly one call
  always sees the real Health Score; the other's own response
  legitimately depends on race timing) and cites the V13 contract
  above plus this task's own 15-iteration finding.
- Assertion rewritten to check the REAL invariant directly: exactly
  one of the two results has a non-null `health_score` (never both —
  duplicated evidence — never neither — lost evidence); the other
  result is then EITHER `labelScanRequired` (nutrition-only committed
  first) OR a success with `health_score is None` (ingredients-only
  committed first, V13) — both explicitly checked, neither silently
  accepted by omission. No `pytest.mark.skip`, no loosened row-state
  assertions (the final-row checks are byte-for-byte unchanged).

**Verification (requirement 5), all against the same disposable
Postgres 16 + pinned-dependency Docker image used in the V16 pass:**
- Fixed test alone: **15/15 passed** (both legitimate orderings
  occurred naturally across the 15 runs, both correctly accepted).
- Fixed test + sibling, normal order: **5/5 passed** (`2 passed` each).
- Fixed test + sibling, reversed order: **3/3 passed**.
- Complete `tests/postgres/` directory (all 4 tests): **3/3 runs, 4/4
  tests passed each time**.
- `python -m pytest -q`: **410 passed, 4 skipped, 0 failed** — both on
  the host (Python 3.14) and inside the pinned-dependency image
  (Python 3.12) — identical, and identical to the V16 pass's count
  (this task changed a test file that's skipped in the default local
  run, so the count is unaffected).
- `python -m alembic heads`: one head, `e4f5a6b7c8d9` — unchanged (no
  migration touched by this task).
- Runtime `app.openapi()` vs. tracked `openapi.json`, pinned
  dependencies (Python 3.12, `requirements.txt` exactly): **EXACT
  MATCH** — unchanged (no schema/route touched by this task).
- Diff review: **one file changed**
  (`tests/postgres/test_concurrent_enrichment_postgres.py`, +61/-12),
  fully scoped to `nutriguard-backend/`, no secrets
  (`api_key`/`secret`/`password`/PEM patterns grepped across both this
  diff and the full `4b44b8f..HEAD` branch range — none found beyond
  expected config field names).

## Verification (V17 summary)

- Reproduction: alone (8/10 pass pre-fix, confirming flakiness),
  with sibling (both orders, pre-fix), full `tests/postgres/`
  directory (pre-fix) — all three reproduced the SAME nondeterministic
  pattern, never a distinct order-dependent or leakage-dependent one.
- 15-iteration diagnostic: 15/15 final-row correctness, 12/15 +
  3/15 = the exact two legitimate V13 orderings, 0 unexpected outcomes.
- Post-fix: 15/15 (alone) + 5/5 (with sibling, normal order) + 3/3
  (reversed order) + 3/3 runs × 4/4 tests (full directory) = **26/26
  postgres-test executions passed** across every combination requested.
- `pytest -q` (host + pinned image): 410 passed, 4 skipped, 0 failed,
  both environments identical.
- `alembic heads`: single head, `e4f5a6b7c8d9`.
- OpenAPI (pinned deps): exact match.
- Secrets/unrelated files: none.

## Unresolved risks (after V17)

- None new. Everything listed as unresolved in the V16 entry below
  still applies (no real external ingredient-lookup/regulatory-database
  integration exists yet; `cas_number` not populated for curated seed
  data; `_EXTRA_ALIASES` intentionally small/hand-curated;
  `_fill_missing_identity_fields`'s lack of its own confidence gate,
  safe today, flagged for a future non-OCR_HEURISTIC source) — this
  task did not change any of that. The item this task existed to
  resolve (the deterministic test failure) is now closed: fixed, not
  skipped, not weakened, with the root cause proven and cited.

## Recommended next step

This branch (backend-only, `android-app/**`/`main`/live
stack/credentials untouched throughout) has now had ALL of its
previously-flagged verification gaps closed: migration up/down/up
cycle proven against real Postgres (V16), both opt-in concurrency test
files proven against real concurrent Postgres sessions with 0
failures across 26 executions (V16 + V17), the complete backend suite
green in both the host and pinned-dependency environments, a single
Alembic head, an exact-match OpenAPI regeneration under the pinned
dependency set, and no outstanding "reported but not fixed" items in
this area. The branch is ready for PR review. Opening the actual PR
(and any merge/deploy) still requires the explicit go-ahead this task
series has consistently deferred to a human/CI review step.

---

### V16: final pre-PR verification (found and fixed 2 real bugs)

Real, disposable infrastructure was used for this pass — NOT the
already-running live dev stack (`nutriguard-backend-db-1` etc., left
completely untouched): a separate `postgres:16-alpine` container
(`nutriguard-pr-verify-pg`, its own Docker network, no persistent
volume, removed at the end of the session) plus a throwaway image
(`nutriguard-pr-verify:*`) built FROM THIS BRANCH's own `Dockerfile`
+ `requirements.txt` (Python 3.12-slim, the repo's actual pinned
dependency set — the host shell's ambient Python 3.14 was NOT used for
any of this pass's checks, precisely to eliminate the environment-drift
question raised in the V15/V14 entries below).

**1-2. Migration upgrade → downgrade → upgrade through `e4f5a6b7c8d9`,
with pre-existing data surviving correctly.** Script: start at the
immediately-prior revision (`d3e4f5a6b7c8`), insert a product +
ingredient row using the OLD (pre-migration) column set via raw SQL,
then upgrade → verify → downgrade → verify → upgrade again → verify.
**Result: PASSED.** After the first upgrade, the pre-existing
`Citric Acid`/`E330` row was correctly backfilled
(`normalizedName="citric acid"`, `insNumber="330"` derived from the
E-number, `verificationStatus=VERIFIED`, `source=CURATED_SEED`,
`confidence=1.000`) with the product's `ingredientIds` link intact;
after downgrade, the new columns/table were gone but both rows and
their link survived unchanged; the re-upgrade deterministically
reproduced the identical backfilled values. (An `asyncpg` "cached
statement plan is invalid" error appeared on the first attempt — a
verification-script artifact from reusing one engine's connection pool
across DDL changes made by a separate `alembic` subprocess, fixed by
disposing the pool after each migration step; not a product bug.)

**3-4. Opt-in ingredient-catalog PostgreSQL concurrency tests, proving
simultaneous creation of the same alias/E-number converges on one row.**
Found and fixed **two real bugs** in the process (both now covered by
regression tests, both verified fixed against real concurrent
PostgreSQL sessions, 5 repeated runs each with 0 failures):

- **Test-harness deadlock** (not a production bug, but the concurrency
  test itself was unusable): the original test deferred both sessions'
  `commit()` until after `asyncio.gather` returned. Under SQLite (this
  suite's fast default DB) that's harmless; under real Postgres, the
  second session's INSERT genuinely blocks waiting for the first
  session's transaction to resolve (a `transactionid` lock wait,
  confirmed via `pg_stat_activity`) — but that first session's commit
  was scheduled to run only AFTER `gather` returned, which never
  happens while the second call is still blocked. Neither coroutine
  could make progress: a real hang, not a flaky race (reproduced
  identically 3/3 times before the fix). Fixed by having each
  concurrent "request" commit its own work internally before
  returning — exactly how `food_analysis.py`'s real request-scoped
  functions already behave (each with its own internal
  `await db.commit()`), which is what the SIBLING file's existing,
  working concurrency test actually gathers (full request functions,
  not bare repository calls).
- **Real production bug: `get_or_create_catalog_ingredient` couldn't
  recover from an E-number conflict.** Two different display names
  sharing the same genuine, never-before-seen E-number (e.g.
  "Vitamin C (E300)" vs. "Ascorbic Acid (E300)") produce DIFFERENT
  deterministic ids/normalized names, so a concurrent race between them
  conflicts on the UNIQUE `e_number` column, not the `id` primary key.
  The loser's recovery path only re-fetched by `id` and then by its OWN
  alias — neither of which the WINNER's row is reachable through (it
  has a different id and a different alias) — so it fell through to
  the "this should be unreachable" `RuntimeError` instead of
  converging. **Fixed**: the recovery path now also re-checks by every
  official identifier the row carries (E-number, INS, CAS) before
  concluding the conflict is unrecoverable. New regression test:
  `test_concurrent_resolution_of_the_same_new_e_number_from_different_display_names_converges`.
  See README section 13.4.

**5. Complete backend test suite**: `python -m pytest -q` →
**410 passed, 4 skipped, 0 failed** (up from 407/4 in the V15 entry —
6 new tests from this pass's 2 fixes, minus 2 renamed/consolidated;
net +3 test functions, +1 skip from the new E-number concurrency
test), run BOTH on the host (Python 3.14) and inside the pinned
`nutriguard-pr-verify` image (Python 3.12, the repo's actual pinned
deps) — identical result both ways.

**6. OpenAPI regenerated with the repository's pinned dependencies —
EXACT MATCH, no manual reconciliation.** Running
`app.openapi() == json.load(open("openapi.json"))` INSIDE the pinned
`nutriguard-pr-verify` image (Python 3.12-slim, `fastapi==0.115.6`,
`pydantic==2.10.4`, exactly as pinned in `requirements.txt`) returned
`True` with **zero** differences — including the two hunks
(`ImageLabelScanRequest.image`'s `format`/`contentMediaType`,
`ValidationError`'s `input`/`ctx`) that the V15/V14 entries below
documented as "pre-existing, unrelated environment drift" and
hand-excluded from what they applied. This CONFIRMS that drift was
purely an artifact of the earlier sessions' host Python (3.14, too new
for the pinned `pydantic-core` wheel) rather than anything about the
tracked `openapi.json` itself — with the actual pinned dependency set,
there is no drift left to explain or exclude. No changes to
`openapi.json` were needed in this pass.

**7. Commit review** (`1d8c3d9`, `fdb025f`, plus this pass's own fixes)
against each requested risk category:
- *Fabricated scientific/regulatory claims*: none found —
  `_build_minimal_row` only ever copies already-empty fields from
  `SyntheticIngredient` (verified by re-reading every field assignment
  in `ingredient_catalog.py`/`ocr_normalizer.py`).
- *Incorrect verification promotion*: **found and fixed** —
  `merge_verified_fields` was promoting a row all the way to `VERIFIED`
  (and setting `riskAssessmentAvailable=True`, which is what lets
  `riskLevel` influence the Health Score) for ANY non-OCR source,
  including a future `GEMINI`-sourced merge. An AI-generated claim is
  real content worth storing but is NOT a human/regulatory
  confirmation. Fixed: only `REGULATORY_LOOKUP`/`CURATED_SEED`-ranked
  sources promote to `VERIFIED`; a `GEMINI`-sourced merge now promotes
  only to `LIMITED_DATA` and leaves `riskAssessmentAvailable` alone.
  New tests: `test_regulatory_lookup_source_promotes_all_the_way_to_verified`,
  `test_gemini_source_promotes_only_to_limited_data_never_verified`.
- *Alias collisions/ambiguous aliases*: none found in the actual seed
  data (programmatically verified: all 12 curated self-aliases + the 5
  curated `_EXTRA_ALIASES` normalize to 17 distinct, non-colliding
  keys). Found and fixed a related **completeness gap**: resolving via
  E-number (step 1) returned early without registering the observed
  display text as a new alias, so a later non-E-number-annotated
  mention of the same name would have fallen through to creating a
  duplicate stub instead of reaching the alias hit in step 2. Fixed —
  see item 3-4 above and README 13.1. The known, accepted, narrow
  residual risk from before (a curated seed entry added AFTER an
  OCR-only stub already claimed its exact normalized name — the seed
  loader logs and skips rather than repointing) is unchanged and still
  intentionally out of automatic-repair scope; see the V15 entry.
- *Lower-confidence overwrite of curated data*: re-verified via the
  existing + new `merge_verified_fields` tests — a lower-rank OR
  equal-rank-lower-confidence source can never touch a higher-rank
  row's fields. No issue found beyond the verification-promotion bug
  above.
- *Unsafe TTL handling*: the SQLite naive/aware `datetime` bug fixed in
  V15 was re-verified fixed (`_as_utc` applied consistently in
  `ingredient_catalog.py`, `schemas/ingredient.py`, `food_analysis.py`)
  — the migration/concurrency verification pass above exercises real
  `TIMESTAMP WITH TIME ZONE` values end-to-end against genuine
  Postgres, where this class of bug cannot occur in the first place
  (Postgres always returns tz-aware values), so this pass adds
  confirmation on top of the SQLite-side unit tests. No new TTL issue
  found.
- *Transaction/race problems*: **found and fixed two** — see item 3-4
  above (test-harness deadlock + the E-number conflict-recovery bug).
  Also noted (not fixed, low severity/likelihood, documented instead):
  `_fill_missing_identity_fields` fills a null `e_number`/`ins_number`
  on ANY row (curated or not) whenever the E-number-hit path or an
  exact alias match supplies one, with no separate confidence gate of
  its own — safe in practice because it can only ever fire when the
  FULL normalized display text exactly matches an existing alias (or
  the E-number itself already resolves), which structurally prevents a
  stray OCR digit sequence from attaching to an unrelated curated row;
  flagged here for visibility rather than engineered around, since
  every synthetic ingredient's source is `OCR_HEURISTIC` regardless
  (there is no different-source case to gate against yet).
- *Secrets and unrelated files*: none found. `git diff --stat`
  confined to `nutriguard-backend/` for both commits and this pass's
  fixes; greeted for `api_key`/`secret`/`password`/PEM-block patterns
  across the full `4b44b8f..fdb025f` range plus this pass's own diff —
  no matches outside already-expected config field NAMES (e.g.
  `JWT_SECRET: str`, `GEMINI_API_KEY: str = ""`); no `.env`/credential/
  keystore-shaped file touched by any commit.

Also incidentally discovered while running `tests/postgres/` as a
whole (NOT part of this task, NOT fixed, reported for visibility): the
PRE-EXISTING, unrelated
`test_concurrent_enrichment_postgres.py::test_concurrent_enrichment_of_the_same_existing_row_preserves_both_groups`
test (predates this branch entirely — inherited from `origin/main`,
never previously run against real Postgres per every prior session's
own "no Docker/Postgres available" note) fails deterministically (3/3
runs) when run in the same pytest session as the file's other test, but
passes in isolation. Root cause appears to be a stale assumption in the
test itself, not a real application bug: V13 (already on `origin/main`
before this branch existed) changed `_finalize_barcode_enrichment` so
that completing ONLY the ingredients evidence group returns `200` with
a null Health Score rather than `404`/`labelScanRequired` — this
test's hardcoded "exactly one of the two concurrent calls succeeds, the
other gets `ProductNotFoundError`" assertion only holds when the
NUTRITION-only side happens to win the row-lock race, not when the
INGREDIENTS-only side does (which now legitimately succeeds too, per
V13). This is unrelated to the ingredient-knowledge-cache task and was
deliberately NOT touched here (out of scope, a different file/feature);
flagging it for a separate, dedicated fix.

## Verification (this pass)

- `python -m pytest -q` (host, Python 3.14): **410 passed, 4 skipped,
  0 failed**.
- `python -m pytest -q` (pinned image, Python 3.12): **410 passed, 4
  skipped, 0 failed** — identical.
- `alembic upgrade e4f5a6b7c8d9` → verify → `alembic downgrade
  d3e4f5a6b7c8` → verify → `alembic upgrade e4f5a6b7c8d9` → verify,
  against real disposable PostgreSQL 16: **PASSED** (see item 1-2
  above for the exact assertions).
- `tests/postgres/test_ingredient_catalog_concurrency_postgres.py`
  (both tests, real disposable PostgreSQL 16, 5 repeated runs after the
  fixes): **PASSED, 5/5, 0 failures**.
- Runtime `app.openapi()` vs. tracked `openapi.json`, using the
  repository's pinned dependencies (Python 3.12, `requirements.txt`
  exactly): **EXACT MATCH — 0 differences.**
- Commit review of `1d8c3d9`/`fdb025f` against the 7 requested risk
  categories: 2 real issues found and fixed (see item 7 above), 1
  unrelated pre-existing test issue found and reported (not fixed), no
  secrets/unrelated files.

## Unresolved risks (carried forward / new)

- The pre-existing, unrelated `test_concurrent_enrichment_of_the_same_existing_row_preserves_both_groups`
  test failure (see above) needs a separate, dedicated fix/task — its
  assertion needs updating for V13's actual (correct) asymmetric
  completion-gate behavior. Out of scope here.
- `_fill_missing_identity_fields`'s lack of its own confidence gate
  (see item 7 above) — safe today, flagged for whoever eventually adds
  a second, non-OCR_HEURISTIC source that could reach that function.
- Everything already listed as unresolved in the V15 entry below still
  applies (no real external ingredient-lookup/regulatory-database
  integration exists yet; `cas_number` not populated for curated seed
  data; `_EXTRA_ALIASES` intentionally small/hand-curated) — this pass
  did not change any of that.

## Recommended next step

Review the diff (including this pass's 2 bug fixes), then merge only
after CI/human review confirms green — this handoff documents that a
disposable Postgres 16 instance, a pinned-dependency Docker build, a
real concurrent-session migration cycle, and the full opt-in
concurrency suite all passed cleanly as of this pass's commit (see the
git log / PR for the exact SHA). Do not merge or deploy without that
separate review, per the task's own instruction.

---

### V15: persistent ingredient knowledge cache

Full design writeup: README section 13. Summary:

- The `ingredients` table is now the ONE persistent, reusable catalog
  for BOTH curated/seeded data AND any OCR/Gemini-observed ingredient
  with no curated match — previously the latter was recreated from
  scratch, in memory only, on every scan (`ocr_normalizer.SyntheticIngredient`,
  never persisted). New table `ingredient_aliases` is the reverse-
  lookup index (any known name/spelling/Bulgarian/OCR-variant → its one
  canonical `ingredients` row), globally unique on `alias_normalized`
  — deliberately a separate table (not a JSON/array column) so that
  uniqueness can be enforced at the DB level, per-alias, across ALL
  ingredients (see `IngredientAlias`'s own class docstring for the
  full "why not a column" justification the task asked for).
- New pure module `app/services/ingredient_normalization.py`
  (`normalize_ingredient_name`) and new service module
  `app/services/ingredient_catalog.py` (local-first canonical-identity
  resolution: official identifier → alias → minimal-record
  get-or-create, race-safe; `is_stale`/`is_within_negative_cache_window`
  TTL predicates; `merge_verified_fields` confidence/source-priority-
  gated merge — see its module docstring for exactly what's live today
  vs. a ready seam for a future real external-lookup integration, since
  none exists in this codebase currently).
- Wired into `food_analysis.py` at all 6 places an ingredient list is
  assembled (`_persist_discovered_product`, `analyze_ocr_text`,
  `analyze_ocr_text_with_barcode`, both label-image entry points via
  `_run_label_image_pipeline`, and both Bulgarian-alias-aware "rebuild"
  blocks in `_finalize_barcode_enrichment`/`_finalize_standalone_label_analysis`)
  via one new `ingredient_catalog.materialize_ingredients(db, ingredients)`
  call each — every pure OCR/Gemini-parsing function itself
  (`ocr_normalizer`, `fallback_analysis`, `gemini_image_parser`, the
  discovery bridge) is UNCHANGED, keeping their own existing pure-unit-
  test suites completely untouched.
- Additive `IngredientEntity` fields: `insNumber`, `casNumber`
  (structural only, not populated for curated seed data — see below),
  `verificationStatus`, `source`, `sourceRecordId`, `sourceUrl`,
  `retrievedAt`/`lastVerifiedAt` (epoch millis), `confidence`,
  `schemaVersion`, `needsRefresh` (computed). Old fields/shape
  unchanged.
- `app/seed/load_seed.py`: every curated row now gets real provenance
  (`VERIFIED`/`CURATED_SEED`/confidence `1.0`/`normalizedName`/derived
  `insNumber`) instead of the fail-safe column defaults, and registers
  its own name plus a small hand-verified extra-alias list
  (`_EXTRA_ALIASES` — migrates the pre-existing, UNCHANGED
  `label_language._BULGARIAN_INGREDIENT_ALIASES` dict's curated-ingredient
  entries into persistent rows) as `IngredientAlias` rows.
- Migration `e4f5a6b7c8d9` (parent `d3e4f5a6b7c8`, new single head):
  adds the 8 provenance/identity columns to `ingredients` (backfilling
  every EXISTING row — all curated, at this point in the chain — to
  VERIFIED/CURATED_SEED/full confidence, and deriving `ins_number` from
  any existing `e_number`) and creates `ingredient_aliases`.
- New config: `INGREDIENT_VERIFIED_DATA_TTL_SECONDS` (~6 months),
  `INGREDIENT_NEGATIVE_CACHE_TTL_SECONDS` (24h).
- **Real bug fixed along the way, not part of the original ask**: the
  first draft of `normalize_ingredient_name` stripped brackets/parens
  as "edge punctuation", which for a name like "High Fructose Corn
  Syrup (HFCS)" removed only the trailing `)` (nothing follows it)
  while leaving the matching `(` in place — an inconsistent, lopsided
  normalized form. Fixed by excluding brackets/parens from the
  edge-stripping set entirely (see the function's own docstring for the
  reasoning); caught by `tests/unit/test_ingredient_normalization.py`'s
  own regression test before it ever reached seeded data.
- **Real bug fixed along the way** (SQLite-only, would not have
  affected production Postgres): a `DateTime(timezone=True)` value read
  back from SQLite (this test suite's DB) loses its tzinfo, so both
  `datetime` subtraction and `.timestamp()` on it were crashing/silently
  wrong (`.timestamp()` on a naive value assumes the LOCAL system
  timezone). Fixed with the same `.replace(tzinfo=timezone.utc)` pattern
  `app/services/auth_service.py` already uses for the identical issue —
  applied in `ingredient_catalog._as_utc`, `schemas/ingredient.py`'s own
  `_as_utc`, and `food_analysis._as_utc`.
- `openapi.json`: hand-patched (not a raw regeneration) for the exact
  same reason as the V14 handoff below — this environment's installed
  FastAPI/Pydantic produce two unrelated schema differences vs. the
  tracked file that predate this branch entirely
  (`ImageLabelScanRequest.image` `format`/`contentMediaType`,
  `ValidationError` gaining `input`/`ctx`) — confirmed still present and
  unrelated; excluded from what was actually applied.
- `README.md`: new Changelog entry (V15), new section 13 (full design
  writeup), section 5's table/index list updated, section 12's project
  layout updated, and a new deviation item (13) in section 6 for the
  one additive behavior change this causes (`GET /ingredients/{id}` for
  a since-observed `synth_...` id now `200`s instead of `404`ing).

## Files involved

New: `app/services/ingredient_catalog.py`,
`app/services/ingredient_normalization.py`,
`app/models/ingredient_alias.py`,
`app/repositories/ingredient_alias_repository.py`, migration
`e4f5a6b7c8d9`, and the test files listed under Verification below.

Modified: `app/models/ingredient.py`, `app/models/enums.py`,
`app/models/__init__.py`, `app/repositories/ingredient_repository.py`,
`app/schemas/ingredient.py`, `app/services/food_analysis.py`,
`app/seed/load_seed.py`, `app/core/config.py`, `openapi.json`,
`README.md`, `tests/unit/test_ingredient_schema_data_quality.py`
(extended its "purely additive field set" pin with this round's new
fields — the correct evolution of that check, not a weakening of it).

## Verification

- `python -m pytest -q`: **407 passed, 3 skipped** (0 failed). Skips:
  the two pre-existing (opt-in real-PostgreSQL concurrency test from
  V14's predecessor branch, and one other pre-existing skip) plus this
  round's own new opt-in real-PostgreSQL concurrency test (see below).
- New/updated test files: `tests/unit/test_ingredient_normalization.py`,
  `tests/unit/test_ingredient_catalog_pure.py`,
  `tests/unit/test_ingredient_knowledge_cache_migration.py`,
  `tests/integration/test_ingredient_catalog.py`,
  `tests/integration/test_load_seed.py`,
  `tests/integration/test_ingredient_knowledge_cache_end_to_end.py`,
  `tests/postgres/test_ingredient_catalog_concurrency_postgres.py`
  (opt-in), `tests/unit/test_ingredient_schema_data_quality.py` (field-set
  pin extended).
- `python -m alembic heads`: one head, `e4f5a6b7c8d9`.
- `python -m alembic upgrade head --sql`: clean. Also manually checked
  `alembic downgrade e4f5a6b7c8d9:d3e4f5a6b7c8 --sql` (offline) for the
  new migration specifically — clean, symmetric with its own upgrade.
- Runtime vs. tracked `openapi.json`: **NOT byte-identical** — the only
  differences are the same two pre-existing, environment-only hunks
  documented in the V14 entry below (reconfirmed present and unrelated
  to this change). Every actual diff applied to `openapi.json` in this
  round (the two new `IngredientSource`/`IngredientVerificationStatus`
  schemas and `IngredientOut`'s 11 new properties) was hand-verified
  against the live schema, including exact property declaration order.

## Unresolved items

- **No real-PostgreSQL run was performed** for the new migration or the
  new opt-in concurrency test in this environment (no Docker/Postgres
  available here) — only offline `--sql` generation was verified for
  the migration. Run a real upgrade → downgrade → upgrade cycle AND
  `pytest tests/postgres/ -v` (with `NUTRIGUARD_TEST_POSTGRES_URL` set)
  against a disposable Postgres instance before deploying.
- **No real external ingredient-lookup/regulatory-database integration
  exists in this codebase.** `IngredientSource.REGULATORY_LOOKUP`,
  `IngredientVerificationStatus.LIMITED_DATA`, and
  `ingredient_catalog.merge_verified_fields`/`is_within_negative_cache_window`
  are real, fully unit-tested, and ready to be called by one, but
  nothing currently calls them with non-empty/non-trivial data (Gemini
  today only does whole-label extraction, never per-ingredient
  regulatory lookup, by design — see the V14 entry below on why OCR/
  Gemini never produce scientific claims). If a specific external
  regulatory data source was actually intended by "call an external
  source or model", that needs a concrete API/credentials spec from
  the product owner before it can be built — flagging this explicitly
  rather than guessing at one.
- A future task should pin/reconcile this environment's dependency
  versions against the ones `openapi.json` was originally generated
  with, so the environment-drift differences noted above stop requiring
  manual reconciliation on every hand-patch (unrelated to this task,
  carried over unresolved from the V14 handoff).
- `cas_number` is a real column on `Ingredient` but is not populated for
  any of the 12 curated seed entries in this change — deliberately, to
  avoid fabricating an identifier without a verified source for each
  one. A follow-up task with a trustworthy per-ingredient CAS reference
  could backfill it via a proper Alembic data migration.
- The `_EXTRA_ALIASES` list in `load_seed.py` is small and hand-curated
  by design (never auto-generated/fuzzy-matched) — extending it to
  cover more curated ingredients' Bulgarian/spelling variants is
  straightforward but was intentionally scoped to what already existed,
  reviewed, in `label_language._BULGARIAN_INGREDIENT_ALIASES`.

## Recommended next step

Review the backend diff, run the real PostgreSQL migration cycle for
`e4f5a6b7c8d9` AND the new opt-in concurrency test
(`tests/postgres/test_ingredient_catalog_concurrency_postgres.py`) on a
disposable Postgres instance, then review/open a PR for this branch
covering both tasks (V14 + V15). Do not merge or deploy until review
and CI are green.

---

## Previous work (V14 — preserved as this task's own handoff)

Date: 2026-09-04

- Branch: `feat/backend-ingredient-profile-data-quality`, based on
  GitHub `origin/main` at `4b44b8f04e95bc1fa58fe27546f17dcab605d562`.
- Backend-only changes (`nutriguard-backend/` only); `android-app/**`
  was not touched.
- Removed every fabricated generic scientific/regulatory placeholder an
  OCR-only ("synthetic") ingredient used to report ("Normalized Food
  Component", "Ingredient extracted via OCR label scan.", "Standard
  ingredient.", "Extracted via OCR", "Subject to standard local food
  safety regulations.", "Standard Food Additive/Ingredient",
  "Recognized Ingredient", "Standard dietary intake", "See individual
  sensitivity profile", "NutriGuard OCR & Scientific Pipeline") and the
  keyword-inferred `riskLevel` (SAFE/POTENTIAL_CONCERN/HIGH_CONCERN
  guessed from the OCR name alone). `app/services/ocr_normalizer.py`
  now leaves every field that would require real curated/verified data
  honestly empty (never `null` for an already-required-non-null string
  field, per the existing contract) and always reports the neutral
  `SAFE` risk placeholder.
- Added additive, backward-compatible `IngredientEntity` fields (old
  fields unchanged): `riskAssessmentAvailable: boolean`,
  `riskRationale: string|null`, structured `efsaApprovalStatus`/
  `fdaApprovalStatus`, and `adiMinMgPerKgBwPerDay`/`adiMaxMgPerKgBwPerDay`/
  `adiSource`, derived conservatively and deterministically at read
  time from the existing free-text fields (`app/services/ingredient_regulatory.py`).
- `food_analysis._score_and_warnings` excludes any ingredient with
  `risk_assessment_available=False` from the Health Score's risk-level
  deductions entirely.
- Normalized the 4 curated seed entries' `countriesRestrictedOrBanned`
  placeholder `"None"` to the empty string.
- Migration `d3e4f5a6b7c8` (single head at the time).
- `openapi.json` hand-patched, excluding the same two pre-existing,
  unrelated environment-drift hunks re-confirmed in the V15 entry above.

Verification at the time: `python -m pytest -q` → 366 passed, 2
skipped. `alembic heads` → one head (`d3e4f5a6b7c8`, since superseded
by `e4f5a6b7c8d9` above). `alembic upgrade head --sql` → clean.
