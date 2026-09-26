# Catalog baseline audit (issue #23, stage 1)

Read-only inventory of the ingredient catalog: what exists, how good it is,
and what the next stages (identity/candidate queue, content pilot, quality
tooling) should build on. Owner authorization: issue #23 comment
5845208498, stage 1.

| | |
|---|---|
| Baseline (exact `origin/main`) | `32bd7efc05750470da6f48eab7c4111e45db1d26` |
| Branch | `audit/backend-catalog-baseline-issue-23` (from that SHA) |
| Live schema revision seen | `c6d7e8f9a0b1` (same single head as this baseline) |
| Live audit run | 2026-09-26, read-only, aggregate counts |
| Code/config changed | none. New files only: two audit scripts, one guard test, this report |
| Merge / deploy / live change | none |

## 1. Method and limits

**Live audit.** `scripts/audit/catalog_baseline_readonly.sql` was run once
against the running dev-stack PostgreSQL 16.14 through the existing local
container access. The session was `default_transaction_read_only=on` plus a
`BEGIN READ ONLY … ROLLBACK` block and a 15 s statement timeout; credentials
were read from the container's own environment, never placed in a command
or in this report. The script reads only `ingredients`,
`ingredient_aliases`, `ingredient_localizations` and the `ingredient_ids`
column of `products` (ids, not user data). It never touches users, devices,
tokens, scan history, raw OCR text or logs. A guard test pins that. No write,
restart or schema change happened. The raw output was not committed; this
report keeps aggregates and a few catalog-level examples.

**Git audit.** `scripts/audit/seed_sources_inventory.py` reads the three seed
files and prints deterministic counts (no DB, no network).

**The supplied registry.** The 800-row E-additive registry that
`load_seed.py` refers to is **not in the repository and was not found on this
machine**, so nothing was compared against it and nothing here assumes it is
deployed or that every integer code is a valid additive.

**How much to trust the numbers.** The "live" database is the local dev
stack: 84 products, 3 users. Frequencies are indicative of what real scans
produced there, not a statistical sample of production. All counts below are
exact for that database on that day.

## 2. Live catalog inventory

### 2.1 Size and composition

| Table | Rows |
|---|---|
| `ingredients` | 289 |
| `ingredient_aliases` | 301 |
| `ingredient_localizations` | 12 |
| `products` | 84 (81 with at least one ingredient id) |
| `product_sources` | 100 |

| Source | Verification | Rows |
|---|---|---|
| `CURATED_SEED` | `VERIFIED` | 12 |
| `CURATED_SEED` | `LIMITED_DATA` | 35 |
| `OCR_HEURISTIC` (synthetic `synth_…` ids) | `UNVERIFIED` | 242 |

So 47 curated rows and 242 rows that exist only because a label was scanned.

### 2.2 Field population (non-empty)

| Field | Curated (47) | OCR-derived (242) |
|---|---|---|
| `e_number` | 45 | 6 |
| `scientific_name` | 12 | 6 (the E-code) |
| `category` | 47 | 242 (generated "Food Additive (E…)" style) |
| `description` | **17** | 0 |
| `purpose_in_food` | 47 | 0 |
| `health_concerns` | 20 | 0 |
| `side_effects` | 12 | 0 |
| `efsa_status` | 13 | 0 |
| `acceptable_daily_intake` | 31 | 0 |
| `references` (free text) | 47 | 0 |
| `source_url` | **0** | 0 |
| `field_provenance_json` | **0** | 0 |
| `risk_assessment_available` | 12 | 0 |

- **30 of the 45 curated E-coded rows have an empty `description`**: E200,
  E202, E210, E211, E220, E221, E223, E260, E270, E300, E301, E306, E307,
  E321, E331, E338, E407, E410, E412, E420, E433, E440, E460, E466, E500,
  E627, E631, E950, E952, E954.
- **No curated row has a `source_url` or per-field provenance.** A row is
  labelled `CURATED_SEED`/`VERIFIED` or `LIMITED_DATA`, but the label does not
  say which document supports which claim.
- Only 12 curated rows have `risk_assessment_available`, i.e. a risk claim the
  catalog is willing to stand behind.

### 2.3 E-number identity

- 51 distinct E-numbers; 45 on a curated row, **6 only on OCR-derived rows**:
  `E150A`, `E150D`, `E414`, `E476`, `E903`, `E1400`. No E-number is on more
  than one row, and all match `^E[0-9]{3,4}[A-Z]?$`.
- **E150D exists only as an OCR-derived row, with no content**, and its name
  is a tokenization artifact ("Colorant: e150d"). This matches issue #23's
  phase-1 finding (`docs/E_ADDITIVE_PHASE1_VERIFICATION.md`, on
  `feat/backend-e-additive-summaries`): the content is absent, not
  mis-loaded or filtered. The audit shows the row as it is stored; it does
  not by itself prove what the app displayed on the owner's phone.
- 2 curated rows have no E-number (`high_fructose_corn_syrup`,
  `whole_oat_flour`); the second is an ingredient, not an additive.

### 2.4 Identity uncertainty, aliases, localizations

- `identity_uncertain` is true on **38** rows: 30 `TRANSLATION_UNRELIABLE`,
  5 `COLON_SEPARATED_CLAUSE_MERGE`, 3 `DUPLICATE_TOKEN_FRAGMENT`.
- Aliases: 54 EN and 4 BG curated, 1 from `GEMINI`, and 242 `OCR_HEURISTIC`
  aliases with **no language recorded** (one per OCR-derived row). **0**
  alias texts map to more than one ingredient and **0** orphans.
- Localizations: 12 rows, all `bg`, all `translation_status = REVIEWED`
  with `translation_source = MACHINE_TRANSLATED` and `reviewed_at` set. That is
  the loader's deliberate "reviewed machine translation" stamp, not a defect.
  BG covers 12 of 47 curated rows (0 of the 35 starter-CSV-only rows). 0
  orphans.
- Whether a BG row's `source_content_hash` still matches its English source
  was not evaluated here (the hash is computed in Python). It belongs in the
  stage-4 tool.

### 2.5 Products and references

- 675 ingredient references across 81 products; 340 distinct ids.
- **114 distinct referenced ids (234 references, 27 products) have no
  `ingredients` row.** All are `synth_…` ids from `label_scan` (16 products),
  `label_scan_translated` (8) and `open_food_facts` (3) products written
  before the catalog persisted synthetic rows. This is the documented
  read-time reconstruction (`fetch_ingredients_for_product` rebuilds them
  from the product's raw text), so it is not a bug, but those identities
  cannot be counted, aliased or curated until they are materialized.
- 28 OCR-derived rows are referenced by no product.

## 3. Quality findings, most important first

1. **Placeholder error text is stored as catalog ingredients.** Two catalog
   rows are named "Ingredients could not be extracted from the image" and "AI
   response was unavailable or invalid", and **16 products reference them**
   (tied with Salt, behind Sugar). Cause, from the code path:
   `_run_label_image_pipeline` falls back to
   `fallback_local_analysis("Scanned Label Product", "Ingredients could not
   be extracted from the image; AI response was unavailable or invalid.")`,
   and the caller then runs `materialize_ingredients` on the tokens of that
   sentence before the untrusted-result gate applies. **Reproduced on baseline
   code (synthetic, not committed):** with the Gemini image call mocked to
   raise `GeminiUnavailableError`, `POST /scan/label-image` returns `404`
   and the empty test catalog then contains exactly those two
   `OCR_HEURISTIC` rows. Each failed provider call can therefore add or
   reuse catalog rows made of an error message.
   This is a catalog-pollution defect; it is not scientific evidence, but it
   makes the catalog and any frequency count untrustworthy. Fix belongs to
   stage 2 (do not materialize untrusted fallback tokens).
2. **The curated label overstates the evidence.** 0 rows have a source URL or
   per-field provenance, and the starter CSV cites only four generic authority
   landing pages for all 43 rows (section 4), including 23 rows that state a
   numeric ADI with no cited document.
3. **Most curated additives have no description** (30 of 45).
4. **OCR junk becomes identities.** 242 synthetic rows include 4 header
   artifacts ("Съставки: вода"-style), 5 allergen statements ("May contain
   peanuts"), 30 names with unbalanced parentheses, 2 with no letters, 1 with
   a 4+ digit run, and 75 Cyrillic names (their aliases record no language).
   None is merged with a curated identity, which is the safe default.
5. **Uncertain identities are already flagged (38)** but nothing consumes the
   flag to keep them out of frequency counts or curation queues.
6. **No encounter counts, first/last seen, or candidate concept exists.** The
   242 OCR-derived rows are de facto candidates with no bounded metadata.
   There are **0 pending candidates** as a separate structure.

Most-referenced OCR-derived ingredient names, as catalog examples (top of 25;
counts are products in the dev DB): Sugar 24, the two placeholder rows 16
each, Salt 16, Water 10, Wheat Flour 10, "Ароматизант" (flavouring) 10, Dry
whey powder 5, Skimmed milk powder 5, "Colorant: e150d" 4, Cocoa butter 4,
Cocoa mass 4, Palm oil 4, Yeast extract 4, Fructose-glucose syrup 4 (BG
names as printed).

## 4. Git seed sources

| Source | Finding |
|---|---|
| `ingredients_seed.json` | 12 rows, 10 with an E-number. All 12 have the full 11-field content set and a free-text `references` string, **none with a URL**. |
| `e_additives_curated_starter.csv` | 43 rows, 43 distinct codes, all valid format, all "Curated starter", reviewed 2026-09-08, confidence High 27 / Medium 16. |
| Overlap | JSON and CSV share 8 codes (E250, E320, E322, E415, E471, E621, E951, E960); the loader skips those CSV rows. Union = **45 distinct E-codes**; 35 are CSV-only, `E102` and `E171` are JSON-only. |
| CSV content | `functional_class` and `typical_role_or_foods` on all 43; `adi_tdi` on 23; `potential_effects` 11; `digestion_absorption`/`metabolism` 9; `human_evidence` 3; `efsa_assessment`/`jecfa_assessment` 3; `eu_regulatory_note` 1; `animal_evidence` 0. **29 rows have nothing beyond class, role and ADI.** |
| CSV sources | **43 of 43 rows cite only generic authority landing pages** (4 distinct URLs: EFSA topic page, WHO JECFA database home, Codex GSFA, EC additives page). No row cites a specific opinion, specification or regulation. |
| `ingredients_seed_bg.json` | 12 rows, exactly the JSON seed ids; none for the 35 CSV-only rows. |
| Identity specificity | `e322_soy_lecithin` and `high_fructose_corn_syrup` name a specific source under a family. Generic E322 (lecithins) has no row of its own. |

**Git versus live reconciliation.** 12 JSON + 35 CSV-only = **47**, exactly the
live `CURATED_SEED` count, and BG 12 = 12. Nothing in the Git seeds is missing
from live, and live holds no curated row that is not in Git. There is no
"present in Git but not loaded" gap.

## 5. What this means for the next stages

**Stage 2 (identity and candidate queue).**
- Reuse `ingredients` / `ingredient_aliases`; add only bounded observation
  metadata (encounter count, first/last seen, capped name length), not a
  second catalog.
- Stop materializing untrusted fallback tokens (finding 1) and detect
  header/allergen/punctuation junk before it becomes an identity.
- Candidate matching must stay exact-identifier or validated-alias; the 38
  `identity_uncertain` rows and the 0 ambiguous aliases show the current
  state to preserve.
- Legacy dangling synthetic ids (114) need a materialization/keep-as-is
  decision that never rewrites a product reference.

**Stage 3 (content pilot).** Start with **E150D**, which is present only as an
empty OCR-derived row. Frequent missing basic ingredients in the dev data are
Sugar, Salt, Water, Wheat Flour, Palm oil, Yeast extract, Cocoa butter, Dry
whey powder and Skimmed milk powder; they have no curated row, so a
pilot batch can be chosen from them without inventing demand. The 30 curated
additives with an empty description are the other clear gap.

**Stage 4 (quality tooling).** The SQL in this branch is a starting point for
the read-only audit: it already yields counts, coverage, missing sources,
junk flags and reference integrity. Still to add: BG hash staleness, an
affected-product count per finding, a bounded findings list, and an explicit
retention limit.

## 6. Verification

| Check | Result |
|---|---|
| `python -m pytest -q` (pinned image: Python 3.12, fastapi 0.115.6, pydantic 2.10.4, SQLAlchemy 2.0.36, pytest 8.3.4) | **593 passed, 10 skipped** (the 10 are the opt-in Postgres tests; no application code changed, so the Postgres and migration checks are unchanged and were not repeated) |
| Guard test `tests/unit/test_catalog_baseline_audit_scripts.py` | 3 passed: SQL is one `BEGIN READ ONLY`…`ROLLBACK` with no write keyword, reads only catalog tables, and the inventory output is pinned and deterministic |
| Live audit | one run, read-only, output not committed |

## 7. Limits

- One dev database, one day. Not a production statistic.
- BG hash currency, per-product affected counts and the registry comparison
  were not done (reasons above).
- The examples list is catalog names only; no scan text, user or device data
  was read.
- Source quality was judged from what the seeds cite, not by re-verifying the
  claims. Verification of specific claims is stage 3.
