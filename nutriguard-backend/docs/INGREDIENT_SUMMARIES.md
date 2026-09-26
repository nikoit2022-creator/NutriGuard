# Source-backed ingredient summaries: content pilot (issue #23, stage 3)

Branch `feat/backend-ingredient-summaries-issue-23`. Baseline `origin/main`
= `32bd7efc05750470da6f48eab7c4111e45db1d26`. The head SHA is the commit
that adds this file (a file cannot contain its own SHA); it is posted with
the push. Backend only. No live database, container, log or secret was
read, written or restarted. Nothing was merged or deployed.

This branch is **not** stacked on PR #22, on the issue #25 branch or on the
stage 2 branch. Dependencies are in section 9.

**Source checking here was done by the authoring agent. It is not a human
scientific review, and the Bulgarian text is not a human translation
review.** Every English claim is recorded with the document and place that
supports it (`claims` ledger in the content file), and every Bulgarian
text is a machine-translated DRAFT that is never served (section 6).

## 1. What ships

| Piece | Where |
|---|---|
| Two tables, additive, reversible, migration `e8f9a0b1c2d3` on top of `c6d7e8f9a0b1` | `alembic/versions/e8f9a0b1c2d3_ingredient_summaries.py`, `app/models/ingredient_summary.py` |
| Serving rules, exact-identity matching, structural validation | `app/services/ingredient_summaries.py` |
| Idempotent loader (also run by `load_seed`) | `app/seed/load_summaries.py` |
| Pilot content, 5 subjects, EN sources + BG drafts | `app/seed/ingredient_summaries.json` |
| Bulgarian review pack generator and explicit approval action | `app/seed/summary_review.py`, `docs/INGREDIENT_SUMMARY_BG_REVIEW.md` |
| One additive nullable `summary` on `IngredientOut` | `app/schemas/ingredient.py`; `openapi.json` regenerated |
| Kill switch `INGREDIENT_SUMMARIES_SERVE` (default true) | `app/core/config.py` |
| Real API JSON samples for Android | `docs/ingredient_summary_samples/` |

Summaries are a **separate layer beside `ingredients`**, not columns on it.
A reusable E-number family summary is served next to an ingredient's own
fields and is never merged into or over them. A test compares every other
field of `GET /ingredients/E322` (the soy-specific row) with the summary on
and off and requires them to be identical.

## 2. Contract (approved in the 2026-09-26 program comment)

`summary` appears on every ingredient object: `GET /ingredients`,
`GET /ingredients/{id}`, `ingredients[]` of every scan response and
`error.details.ingredients[]` of the partial `404` envelope. It is `null`
when nothing servable matches. No status code changes and no existing field
changes.

```jsonc
"summary": {
  "scope": "E_NUMBER_GENERIC",            // or "INGREDIENT_NAME"
  "evidenceState": "SOURCE_VERIFIED",     // only value ever served
  "humanReviewed": false,                 // scientific sign-off, separate from translation review
  "verifiedAt": 1790380800000,            // epoch ms: when the sources were last checked
  "sections": [                           // ordered; a section without verified text is ABSENT
    { "kind": "ORIGIN",       "text": "...\n\n...", "citationIds": ["eu-231-2012-e150d"], "jurisdiction": null },
    { "kind": "JURISDICTION", "text": "...",        "citationIds": ["fda-4mei-qa"],       "jurisdiction": "US" }
  ],
  "citations": [                          // language-independent; shown at the bottom
    { "id": "fda-4mei-qa", "label": "US FDA, Questions & Answers About 4-MEI",
      "url": "https://www.fda.gov/...", "documentDate": "2020-03-27",
      "accessType": "OFFICIAL_PAGE", "supports": ["ORIGIN", "EFFECTS", "JURISDICTION"] }
  ],
  "localizations": {                      // {} unless a REVIEWED, current translation exists
    "bg": { "sections": [ { "kind": "ORIGIN", "text": "...", "jurisdiction": null } ],
            "translationStatus": "REVIEWED", "translationSource": "MACHINE_TRANSLATED" }
  }
}
```

Semantics for the client (Codex):

- `sections[].kind` is closed: `ORIGIN | FUNCTION | EFFECTS | JURISDICTION`.
  Ignore unknown kinds. Text may contain `\n\n` paragraph breaks. There is no
  length cap. `JURISDICTION` may appear more than once, one per
  `jurisdiction` (`EU`, `US`, `US-CA` in the pilot); other kinds carry
  `jurisdiction: null`. Show the jurisdiction label with every such section.
- Show sections in the given order, hide absent ones, and never synthesize
  filler for a missing one.
- Show `citations` at the bottom; `supports` says which sections each backs.
  `accessType` (`FULL_TEXT`, `ABSTRACT`, `OFFICIAL_PAGE`) is honest about how
  much of the source was read; consider showing it.
- Language: use `localizations.bg` when the selected language is Bulgarian
  and the key exists; otherwise show the English `sections` (the fallback for
  every language). A BG section list has the same `kind`/`jurisdiction`
  sequence as the English one and never carries its own citations.
- `summary` never carries a risk level, score or colour. Colour must follow
  an independently supported risk assessment, never the presence of a summary
  or the E-code.
- A client that does not know `summary` is unaffected (additive).

Real JSON, generated from a scratch database (never live) in
`docs/ingredient_summary_samples/`:

| File | Shows |
|---|---|
| `scan_ingredient_e150d.json` | the ingredient object from `POST /scan/ocr-text` for the owner's case ("Colour (E150d)"): a scan-created row with an empty description and the full E150d summary beside it |
| `ingredient_e322_english_only.json` | a curated, soy-specific row with the generic lecithin summary beside it; BG absent (draft) |
| `ingredient_e322_bulgarian_reviewed_illustration.json` | the same after a **synthetic** approval in a scratch database, to show `localizations.bg`; no BG text is reviewed in this branch |
| `ingredient_without_summary_excerpt.json` | `summary: null` |
| `scan_summary_scopes.json` | which ingredients of one scan got which scope |

## 3. Identity rules and what they mean in practice

1. **Exact subject only.** A summary attaches by the ingredient's official
   E-number (`E150D`), or by its exact normalized name (`name:sugar`,
   the same `normalize_ingredient_name` the catalog uses). No stemming, no
   fuzzy match, no translation-based match, no family prefix (`E150` never
   gets the `E150D` summary; `Cane sugar` and `Sugars` do not get
   `name:sugar`).
2. **Uncertainty only narrows the match.**
   - identity-certain row: E-number, then exact name;
   - `TRANSLATION_UNRELIABLE` row that carries an official E-number: the
     E-number summary only, because the code is extracted deterministically
     and is the catalog's own definitive identifier; the unverified name gets
     no name summary. **This is a decision worth a look (section 10, D2).**
   - any other uncertain row (`COLON_SEPARATED_CLAUSE_MERGE`,
     `DUPLICATE_TOKEN_FRAGMENT`, an unknown reason): none, because the token
     itself may be several merged clauses.
3. **Generic family text never asserts the scanned product's origin.** The
   E322 summary says the raw material "is not identified by the E-number
   alone" and attaches to the soy-specific row without saying "soy".
4. Summaries never change `verificationStatus`, `riskLevel`, `description`,
   allergens, dietary flags, the score or any warning (tests pin this).

### Findings from testing that the owner should know (not fixed here)

- **A fresh catalog does not serve name summaries for bare words.** In the
  current pipeline a single word such as "Sugar" is classified `other` by
  `detect_language`, sent for translation, and stays `TRANSLATION_UNRELIABLE`
  unless the translation is verified, which for a short word also needs an
  existing English alias. So a new "Sugar" row is identity-uncertain and
  correctly gets no `name:sugar` summary until a certain row/alias exists.
  Whether the live rows for Sugar, Salt and Palm oil are identity-certain was
  **not checked** (no live access in this stage). If they are not, the three
  name-scoped pilot summaries will not show for them; the E-number ones do.
  Changing that is a language-policy decision and is out of scope here.
- E150d matching gaps from `docs/E_ADDITIVE_PHASE1_VERIFICATION.md` are
  unchanged and pinned by a test: `E150 d` reads as `E150`, and Cyrillic `Е150d`
  reads as no code, so neither gets the E150d summary. The display name of a
  scan-created row is still whatever the first OCR token was (`Colour (E150d`).
  These need the separate matching issue (phase-1 decision D7).
- On `main`, a mention such as "Cane sugar" can converge on a row named
  "Sugar" (fragment matching). The stage 2 branch fixes that. A summary always
  follows the row's own name, so it is never attached to a row whose name does
  not match, but merge stage 2 before relying on name summaries.

## 4. Pilot coverage

Chosen from the stage 1 audit (`CATALOG_BASELINE_AUDIT.md` on
`audit/backend-catalog-baseline-issue-23`): E150D exists only as an empty
OCR-derived row, and Sugar, Salt, Palm oil are among the most-referenced
names with no curated content. E322 is a well-described existing code kept to
show the generic-versus-specific split.

| Subject | Scope | Sections present | Sources | Notes |
|---|---|---|---|---|
| `E150D` | E-number | ORIGIN, FUNCTION, EFFECTS, JURISDICTION x3 (EU, US, US-CA) | 9, 17 ledger claims | section 5 |
| `E322` | E-number | ORIGIN, FUNCTION, EFFECTS, JURISDICTION (EU) | 5, 6 claims | carried from the phase-1 verification, EN unchanged in substance |
| `name:sugar` | name | EFFECTS | 1 (WHO 2015) | free-sugars guideline; no origin/function because no source was read for them |
| `name:salt` | name | EFFECTS | 1 (WHO 2026 fact sheet) | sodium guideline |
| `name:palm oil` | name | ORIGIN | 1 (Codex CXS 210-1999) | sparse on purpose: no source for effects or function was read |

Coverage is **5 subjects**. Everything else is uncovered and stays absent; no
text was written to raise the number. The 30 curated additives with an empty
description, Water, Wheat flour, Yeast extract, Cocoa butter and the milk
powders remain for later batches. E951, E171, E220 and E410 have verified
phase-1 drafts but were left out of this batch to keep it small; they can be
added as data with no code change.

## 5. E150d specifically

- **Content was absent, not filtered or unloaded** (phase-1 finding, still
  true). It is now delivered through the summary layer; the row's own fields
  stay empty and unpromoted.
- **The colour mixture and 4-MEI are kept apart.** ORIGIN says E 150d is a
  complex mixture and that 4-MEI is a separate by-product of ammonia-processed
  caramels, "not the colour itself".
- **Animal findings and human evidence are kept apart.** EFFECTS states that
  the cancer concern comes from feeding studies of the pure substance in mice
  and rats (NTP TR-535, doses in mg/kg bw/day, per sex and species, with the
  NTP's own levels of evidence), that IARC lists 4-MEI as Group 2B, and that
  the summary cites no human study of E 150d or 4-MEI and cancer.
- **Hazard is kept apart from exposure-dependent risk.** The text quotes the
  NTP's own statement that human risk needs a wider analysis, the FDA's
  statement that the NTP doses far exceed estimated dietary exposure, EFSA's
  group ADI and its 2012 exposure finding, and the two 2025 papers.
- **Conflicting and emerging evidence is stated as such**: EFSA (not
  genotoxic, NOAEL 80 mg/kg/day) versus California OEHHA (testing not
  comprehensive enough to rule out a genotoxic mode of action), reported by a
  2016 review; a 2025 risk assessment (margins of exposure 735 to 1,489, US
  diet, reproductive endpoints) versus a 2025 narrative review of experimental
  models that calls for further research. Both 2025 items were read as
  **abstracts only**, so only claims stated in the abstract are used, and the
  text says so.
- **Jurisdictions are separate sections**, each with its date or version
  caveat: EU (specification limits, original 2012 text), US (FDA, page dated
  2020-03-27), US-CA (Proposition 65 level as reported by a 2016 review).
- **Not included, on purpose:** the JECFA class IV ADI (0-200 mg/kg bw, 1985)
  because the current WHO JECFA database entry is not readable here (phase-1
  decision D5); any "safe amount"; any risk colour or score.

### What could not be read, and therefore was not cited for any claim

| Source | Why it matters | What happened |
|---|---|---|
| EFSA Journal 2011;9(3):2004, the caramel colours re-evaluation | it set the group ADI and the 4-MEI conclusions | EFSA and Wiley pages return a bot challenge or 403; no text obtained. Its content is used only as reported by the 2012 EFSA abstract and the 2016 review |
| IARC Monographs Volume 101 | the rationale for Group 2B | NCBI Bookshelf returns a reCAPTCHA. Only the classification in IARC's own list is cited |
| OEHHA primary page | the Proposition 65 level | page returned no content; used only as reported by the 2016 review, and its current status is not checked |
| WHO JECFA database | current caramel class IV ADI | client-rendered, unreadable |
| Later amendments of Regulation (EU) 231/2012 and 1169/2011 | currentness | originals were read; consolidated versions on EUR-Lex return a bot challenge. Every affected sentence says "text as originally published" |

The JECFA specification cited is the 74th meeting (2011) version, which
supersedes the 2000 version used in the phase-1 verification.

Reading method: documents were fetched to a local scratchpad and read as text
(NTP TR-535 full PDF, JECFA PDFs, the OJ texts, PMC pages, the FDA and WHO
pages). A few pages were read only through the fetch tool's summary (EFSA news
2012-12-19, the Morita 2016 full text) and are flagged in the ledger.

## 6. Bulgarian: review process and gates

- Every BG text is `DRAFT` / `MACHINE_TRANSLATED` and is **never served**. The
  served rule is `translationStatus == REVIEWED` and
  `source_content_hash == the summary's current content_hash`.
- The loader never writes `REVIEWED`. Changed English content makes any earlier
  translation stale, and stale text is replaced by the file's new draft and
  never served in between.
- `python -m app.seed.summary_review export` prints a side-by-side pack; the
  committed copy is `docs/INGREDIENT_SUMMARY_BG_REVIEW.md`, and a test fails if
  it drifts from the content file. It prints, per subject, the exact approval
  command including the English content hash the reviewer must quote.
- `python -m app.seed.summary_review approve --subject KEY --language bg
  --content-hash HASH --reviewer LABEL` is a deliberate human action. It
  refuses a wrong hash, an empty reviewer, an unknown subject or language, sets
  only status, time and reviewer, keeps `translation_source` as
  `MACHINE_TRANSLATED` (origin and review are separate facts) and does not set
  `humanReviewed` (the scientific sign-off is another decision).
- English fallback is implemented by omission: a missing `localizations.bg`
  means "show the English `sections`".
- **Decision needed:** who is the named Bulgarian reviewer, and who signs off
  scientific content (`humanReviewed`)? Until then BG is absent for every
  code.

## 7. Loader semantics (safe fill-missing, idempotent)

- The whole file is validated before anything is written; one malformed
  subject rejects the file (tests: each rule, plus "nothing written").
- Same content: no change (three repeated loads, ids, hashes and timestamps
  identical). Changed English: sections replaced, `human_reviewed` reset,
  translation made stale. A REVIEWED translation that still matches is never
  touched. Concurrent loaders converge on one row per subject (savepoint and
  re-read; proven on real PostgreSQL, 8 at once).
- It runs only against the summary tables; it does not read or write
  `ingredients`, so it cannot overwrite a better-supported ingredient field.
- The seed run by the container entrypoint calls it. Nothing is run against
  the live database by this work.

## 8. Verification

Pinned image (Python 3.12.14, fastapi 0.115.6, pydantic 2.10.4, SQLAlchemy
2.0.36, pytest 8.3.4, pytest-asyncio 0.25.0, asyncpg 0.30.0, alembic 1.14.0,
aiosqlite 0.20.0), no network for the SQLite runs.

| Check | Result |
|---|---|
| `python -m pytest -q` (SQLite), whole suite | **653 passed, 12 skipped** (baseline `32bd7ef`: 590 passed, 10 skipped; the 12 skips are the opt-in Postgres tests) |
| New tests | 39 unit (rules, gates, pilot file, review-pack drift), 4 migration, 20 real-HTTP integration (loader, review, read paths, scan paths, kill switch), 2 PostgreSQL |
| `tests/postgres` on a disposable `postgres:16-alpine` (own network, no published ports) | **12 passed** (10 existing + 2 new: 8 concurrent loaders converge; CHECK/unique/cascade enforced) |
| Migration on PostgreSQL | 11 migrations upgrade to a single head `e8f9a0b1c2d3`; downgrade `-1` removes both tables and leaves ingredients 50, aliases 64, ingredient localizations 12 unchanged; re-upgrade and reload restore 5 summaries and 5 DRAFT translations |
| Seed twice on PostgreSQL | first run creates 5 summaries and 5 drafts; second run `unchanged=5`, `translations_kept=5`, nothing created or updated |
| OpenAPI | `app.openapi()` equals the committed `openapi.json` after regeneration; the diff is 200 added lines, 0 removed: `IngredientOut.summary` and five new schemas |
| Alembic heads | `['e8f9a0b1c2d3']` |

Two existing tests were touched only to allow a later migration on top
(`test_ingredient_language_provenance_migration`: single head becomes "in the
one chain") and to pin the new additive field
(`test_ingredient_out_field_set_is_purely_additive`).

Note for whoever runs `tests/postgres`: the existing
`test_synthetic_ingredient_id_postgres.py` assumes an **unseeded** catalog and
fails if `load_seed` has already loaded the curated rows (its `E211` name
resolves to the curated row). That is unrelated to this change; run the
Postgres tests before seeding.

Not done: an independent subagent review of source support and identity
safety (none was spawned); a check against the live catalog; the weekly audit
(stage 4).

## 9. Dependencies and integration

Nothing here was stacked on unmerged work.

- **Stage 2** (`feat/backend-ingredient-candidate-queue-issue-23`, migration
  `d7e8f9a0b1c2`) has the same parent `c6d7e8f9a0b1`. **Whichever migration
  merges second must change its `down_revision` to the other's revision**;
  the single-head test fails until that one-line edit is made. Both branches
  also touch `README.md`, `app/core/config.py`, `app/models/__init__.py`,
  `app/services/food_analysis.py` and
  `tests/unit/test_ingredient_language_provenance_migration.py`; the edits are
  additive and in different places.
- **Recommended order:** stage 2 first (it fixes fragment-name convergence that
  name summaries depend on), then this branch with its `down_revision` set to
  `d7e8f9a0b1c2`.
- **PR #22 and issue #25** do not overlap in files with this change.
- **Rollback:** set `INGREDIENT_SUMMARIES_SERVE=false` (responses are exactly
  as before, `summary: null`) or downgrade one revision; no ingredient row is
  ever changed by this feature.

## 10. Open decisions

| # | Decision | Recommendation |
|---|---|---|
| D1 | Name a Bulgarian reviewer and a scientific sign-off owner | needed before any BG is served |
| D2 | Serve the E-number summary on a `TRANSLATION_UNRELIABLE` row that carries an official E-number (implemented, section 3) | keep: the code is deterministic and the alternative leaves the owner's E150d case blank in a fresh catalog |
| D3 | Language policy for bare English words (section 3 finding) so name summaries reach fresh rows | separate issue; not a content change |
| D4 | Include the JECFA class IV ADI for E150d once the current database entry can be read | yes, attributed and dated, never as a "safe amount" |
| D5 | Add E951, E171, E220, E410 (already verified in phase 1) and the remaining frequent names as the next tested batch | yes |
| D6 | Refresh the original-OJ regulatory sentences against consolidated texts when EUR-Lex is readable | yes, before promoting any of them to `humanReviewed` |

## 11. Files

Added: `alembic/versions/e8f9a0b1c2d3_ingredient_summaries.py`,
`app/models/ingredient_summary.py`, `app/services/ingredient_summaries.py`,
`app/seed/load_summaries.py`, `app/seed/summary_review.py`,
`app/seed/ingredient_summaries.json`, `docs/INGREDIENT_SUMMARIES.md`,
`docs/INGREDIENT_SUMMARY_BG_REVIEW.md`, `docs/ingredient_summary_samples/*`,
`tests/unit/test_ingredient_summaries_service.py`,
`tests/unit/test_ingredient_summaries_migration.py`,
`tests/integration/test_ingredient_summaries.py`,
`tests/postgres/test_ingredient_summaries_postgres.py`.
Changed: `app/api/v1/ingredients.py`, `app/api/v1/scan.py`,
`app/core/config.py`, `app/models/__init__.py`, `app/models/ingredient.py`,
`app/schemas/ingredient.py`, `app/seed/load_seed.py`,
`app/services/food_analysis.py`, `openapi.json`, `README.md`, and the two
tests named in section 8.
