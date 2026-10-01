# OpenFoodTox offline matching and review-profile pilot -- report

Baseline reviewed: `56aee7aa613b8b7cc5f98e4082981ac0d898fc92` (first implementation,
commit `4db31ecbaaae5c113398004577748ae7046080f6`), then
`docs/OPENFOODTOX_PILOT_REVIEW_TASK.md` reviewed at commit
`63c4d4e66de03a18a4dcc0b41b3d82b634bc2f35` (corrections implemented at commit
`0beb2717491ec8e4e8a5c89b1e63b8b433c3ce33`), then
`docs/OPENFOODTOX_PROFILE_CONTENT_TASK.md` reviewed at commit
`40d063444358086c2b47dbe1a2a7386b9509349d` (content/editorial corrections
implemented at commit `631939a120ad7bf79f1d7f95ab9d821caf741916`; see §11).
Worked throughout in the same isolated worktree on
`feat/backend-openfoodtox-dataset-audit`, fast-forwarded first each time (no
drift). No merge, no deploy, no live database access, no
Android/API/migration/seed-data/Health-Score change, no scheduled job, no
live external lookup except read-only web research for §9 and §11. This is an
**offline, dry-run pilot**; nothing here is visible in the application.

## Correction notice (preserving the trail, not erasing it)

This revision corrects the first revision's §9 freshness claim about E330
(see the old §9, which wrongly cited DOI 10.2903/j.efsa.2020.6032 as a
dedicated E330 re-evaluation). Codex caught this on 2026-10-01 (see
`docs/CODEX_HANDOFF.md`, "pilot review corrections assigned"); independently
re-verified here before correcting (§9). §11 carries a second, separate
correction: the date of EFSA opinion `10.2903/j.efsa.2026.10259` was
previously described as "adopted on September 10" -- the opinion's own
"First published" (10 September 2026) and "Approved" (1 July 2026) dates are
distinct, and §9/§11/`editorial_content.py` now keep them so. The wrong sentence is not reproduced
below -- `docs/CODEX_HANDOFF.md`'s append-only log and this branch's git
history (the first revision is commit `4db31ec`'s
`docs/OPENFOODTOX_PILOT_REPORT.md`) are the durable record of exactly what
the wrong claim said and when it was corrected. This revision also corrects
two implementation defects caught by the same review (§3b's matcher truth
table, §3c's `review_eligible` completeness gate) and redesigns the bilingual
drafts (§6).

## 1. What was built

| Module | Purpose |
| --- | --- |
| `scripts/openfoodtox/catalogue_snapshot.py` | Minimal NutriGuard catalogue snapshot (id/common name/E-number/CAS) built from the **tracked seed files**, reusing the real seed loader's own pure row-transform functions. **No database is opened at all.** Also provides `load_supplied_snapshot()` for a separately supplied, authorized ingredient-only JSON export (not used in this run). |
| `scripts/openfoodtox/matcher.py` | Streaming, dry-run CAS/E-number identifier matching (truth table in §3b) and `resolve_subject_links` (which `REFERENCE_SUBSTANCE` identity a reference value/endpoint actually belongs to within one dossier). |
| `scripts/openfoodtox/evidence_bundle.py` | Builds the "required operator review bundle per identity" for an `exact_match`: re-parses only the matched archives, separates human/feed context, assesses review eligibility (§3c), and writes paraphrase DRAFT EN/BG text plus a separate verbatim internal-evidence section (§6). |
| `scripts/openfoodtox/provenance.py` | **New this revision.** Shared git fingerprinting (SHA + dirty-tree detection + diff fingerprint when dirty) so no output is ever labeled with a clean SHA while its producing code was actually dirty (§4). |
| `scripts/openfoodtox/pilot.py` | CLI orchestrator: builds the catalogue snapshot, matches the requested E-numbers, writes one JSON+Markdown profile per exact match plus a run summary with full provenance. |

## 2. A real extraction defect found and fixed (AssessmentBody numeric-value bug)

While tracing why aspartame's (E951) ADI reference value looked inconsistent,
found that `records.py::derive_reference_values` treated **any** leaf literally
named `value` under a reference-value container as that container's own
numeric magnitude. `AssessmentBody` -- a sibling field recording *which body*
made the assessment, self-describing via its own `other` leaf -- also ends in
a leaf named `value`, and was being captured as if it were the ADI's own
number.

**Fix**: `derive_reference_values` now only accepts a `value`/`lowerValue`/
`upperValue` leaf when its immediate parent tag is one of the five confirmed
value-holding wrappers (`Adi`, `Arfd`, `Aoel`, `Aaoel`, `RefValue`) -- an
explicit allowlist. Verified by grepping every occurrence across the full
11,613-dossier dataset: zero `<container>/value` leaves exist unwrapped, so
this allowlist drops nothing real. `records.py` also gained an explicit
`EXTRACTION_LOGIC_VERSION` marker (now `2`) for exactly this kind of change
-- see §4.

**Measured dataset-wide impact, now with a real before/after regeneration**
(§4's `staging/v3` vs `staging/v4`, 25,973 reference values in both --
identical total, only reclassified, confirming this was a pure correctness
fix, not an extraction-completeness change):

| Metric | v3 (stale, pre-fix) | v4 (corrected) | Change |
| --- | --- | --- | --- |
| `chemical_basis.status = resolved` | 42 | 47 | **+5** (genuinely new correct resolutions -- the fix let the *real* magnitude reach the text-matching step instead of a corrupted code) |
| `status = unresolved_no_exact_match` | 21 | 15 | -6 |
| `status = unresolved_unsupported_unit` | 11,479 | 11,268 | -211 |
| `status = unresolved_no_mention` | 14,431 | 14,643 | +212 |
| `status = ambiguous_multiple_bases` | 0 | 0 | unchanged (confirms, again, this is not an actual ambiguity in the real dataset) |
| Records with the literal corruption signature (`value` *and* `lower_value`/`upper_value` simultaneously populated -- never legitimate) | 576 | **0** | fixed |

The `unsupported_unit` → `no_mention` shift (211/212 records) has a precise
mechanical explanation, traced in `chemical_basis.py::extract_chemical_basis`:
it checks `if not stored_value: return unresolved_no_mention` *before*
checking the unit. Pre-fix, a record with no real numeric magnitude at all
but a corrupted, truthy `AssessmentBody`-derived `value` (e.g. `"1342"`)
skipped past that first check and fell through to the unit check instead --
so a record that genuinely had *no number to validate a unit against* was
mislabeled `unresolved_unsupported_unit` (implying a real number with a bad
unit) instead of the more honest `unresolved_no_mention` (no number at all).
Post-fix, these records correctly short-circuit on the first check. The
+5/-6 resolved/no-exact-match shift is the direct, intended effect: the real
stored magnitude (not a corrupted code) is now what gets compared against
the justification text's own mentions.

Two regression tests pin this in `tests/unit/test_openfoodtox_records.py`
(`test_assessment_body_code_is_not_mistaken_for_the_reference_value`,
`test_other_reference_value_ref_value_wrapper_still_captured`).

## 3. Review-task corrections implemented

### 3a. Source-identity audit (item 1) -- see §9 for the full table

The claim that EFSA opinion `10.2903/j.efsa.2020.6032` is a dedicated E330
re-evaluation was **wrong** and has been removed; independently re-verified
(not just trusting the correction) by fetching the actual EFSA/Wiley page and
cross-checking with independent secondary sources. Its real title is
"Re-evaluation of acetic acid, lactic acid, citric acid, tartaric acid,
mono- and diacetyltartaric acid, mixed acetic and tartaric acid esters of
mono- and diglycerides of fatty acids (E 472a-f) as food additives" --
citric acid appears only as a *component name inside the ester description*,
never as the opinion's own assessed subject. All four pilot references are
now audited the same way in §9's table (DOI/title, actual assessed
identifiers, relevance, date, superseding-status evidence) -- not just the
one Codex flagged.

### 3b. Matcher truth table (item 3)

`matcher._compare_one` returned `"conflicting"` whenever both CAS and
E-number were present and **not both** agreed -- including the case where
**neither** agreed, which would have flagged every fully-identified,
unrelated dossier in the entire dataset as "conflicting" against every
query. Fixed to the correct truth table:

| Both present? | CAS agrees | E-number agrees | Result |
| --- | --- | --- | --- |
| yes | yes | yes | `exact` |
| yes | yes | no | `conflicting` |
| yes | no | yes | `conflicting` |
| yes | no | no | **no hit (unrelated)** -- was wrongly `conflicting` before this fix |
| only one comparable | -- | -- | `exact` if it agrees, else no hit (ranges/ambiguity still protected) |

New tests: `test_neither_agreeing_is_unrelated_not_conflicting` and
`test_full_truth_table_in_one_stream` (the latter streams the exact identity,
a real one-identifier conflict, and 50 unrelated fully-identified records
through one query, asserting exactly 1 exact hit, exactly 1 conflicting hit,
and zero false conflicts from the 50 unrelated records).

### 3c. `review_eligible` completeness and applicability gate (item 2)

The single, ambiguous `review_eligible` boolean checked basis/population/unit
but never `evidence_complete`, identity completeness, or subject-linkage
resolution -- an incomplete record could still read as "eligible." Replaced
with a `ReviewEligibility` struct that separates two different questions:

- **`operator_inspectable`** (always `True`): a record is never hidden from
  a human reviewer for being incomplete or inapplicable -- they should see
  it and judge for themselves. (This is why `dossier_bundles` in every
  profile JSON still lists every extracted reference value regardless of
  eligibility.)
- **`consumer_guidance_eligible`** (strict, fail-closed): requires *all* of
  -- the reference value's own `evidence_complete`; the matched identity's
  own `evidence_complete`; a cleanly resolved subject link (`basis` in
  `single_identity_dossier`/`manifest_child_link`, never an unresolved or
  multiply-linked one); a `chemical_basis.status == "resolved"`; a resolved
  unit; a `value_type` of `ADI` or `ARfD` (never `AOEL`/`AAOEL`, which are
  **occupational operator exposure levels, not consumer intake limits**,
  excluded unconditionally regardless of completeness; never `OTHER`, which
  has no defined consumer meaning in this dataset's own schema); and a
  population explicitly in `{"consumers", "general population"}` (fail-closed
  allowlist, not a blocklist of "bad" populations -- an unrecognized future
  population defaults to ineligible). Every failing check appends its own
  reason string; none are silently dropped.
- **Route and period**: the dataset has no separate "route" field -- route is
  implicit in `value_type` (ADI/ARfD are dietary; AOEL/AAOEL are
  dermal/inhalation/occupational), which is exactly why AOEL/AAOEL exclusion
  is unconditional rather than population-dependent. Period (chronic vs.
  acute) is likewise carried by `value_type` (ADI vs. ARfD) rather than a
  separate field -- both are reported, neither conflated with the other.

12 new unit tests in `TestAssessReviewEligibility` exercise every blocking
condition individually and together (truncated-but-otherwise-valid-basis,
missing completeness flag, incomplete identity, unresolved subject link,
AOEL/OTHER type exclusion, worker/species population exclusion, feed
context, unresolved basis, missing unit, and all-reasons-reported-together).

**Real-data consequence, measured in the §4/§5 rerun**: with this stricter,
corrected gate, **only E250's reference value is `consumer_guidance_eligible`
among all four pilot substances** -- E951's and E150d's ADI entries have a
`chemical_basis.status` of `unresolved_no_mention` (their source text states
the ADI as e.g. "40 mg/kg bw/day" without repeating the substance name
immediately after "mg", which is the specific textual shape
`chemical_basis.py` requires to resolve a basis -- a real, honestly-reported
dataset limitation, not a code defect), and every one of E330's reference
values is blocked by multiple reasons at once (unresolved basis, and
for the FEEDAP ones, wrong type/population/feed-context too). This is a
materially more conservative (and more correct) result than the first
revision reported.

## 4. Supersede stale derived data reproducibly (item 4)

`staging/v3/catalogue.jsonl` (and `reports/v3/`) were generated with the
pre-fix code and therefore contain the corrupted reference-value data from
§2. **They are not deleted** (preserved per every prior session's
convention) but are now marked unsuitable: a plain-text
`UNSUITABLE_FOR_EVIDENCE.txt` note was added to both `staging/v3/` and
`reports/v3/` (outside Git, alongside the data itself) pointing to this
report and to `staging/v4/`.

A new `staging/v4/`/`reports/v4/` was generated reproducibly:

```
cd nutriguard-backend
python3 -m scripts.openfoodtox.extract codebook \
  --dossiers-dir /home/vboxuser/nutriguard-data/openfoodtox/originals/2026-09-30/dossiers \
  --staging-dir  /home/vboxuser/nutriguard-data/openfoodtox/staging/v4
# codebook written: 428 unit codes, 16,515 value codes across 71 stylesheets, 0 conflicts, 23.3s

python3 -m scripts.openfoodtox.extract catalogue \
  --dossiers-dir /home/vboxuser/nutriguard-data/openfoodtox/originals/2026-09-30/dossiers \
  --staging-dir  /home/vboxuser/nutriguard-data/openfoodtox/staging/v4 \
  --reports-dir  /home/vboxuser/nutriguard-data/openfoodtox/reports/v4 \
  --progress-every 4000
# 11,613/11,613 archives status=ok, 0 incomplete-evidence documents, 75.4s

python3 -m scripts.openfoodtox.extract identity-audit \
  --staging-dir /home/vboxuser/nutriguard-data/openfoodtox/staging/v4 \
  --reports-dir /home/vboxuser/nutriguard-data/openfoodtox/reports/v4
```

Both the `codebook` and `catalogue` commands were run from a **clean**
working tree at commit `0beb2717491ec8e4e8a5c89b1e63b8b433c3ce33` -- recorded
in `reports/v4/catalogue_summary.json` via the new
`scripts/openfoodtox/provenance.py::git_fingerprint()`:

```json
"extraction_logic_version": 2,
"producing_code": {
  "git_sha": "0beb2717491ec8e4e8a5c89b1e63b8b433c3ce33",
  "git_tree_dirty": false,
  "dirty_diff_sha256": null,
  "note": "git_sha alone fully reproduces this output -- working tree was clean."
}
```

`extract.py`'s `catalogue` command (and `pilot.py`) now always record this
structure (not just the `MAX_RAW_FIELDS_PER_DOCUMENT` leaf-cap constant,
which is a coverage threshold, not a schema-version marker, and was
previously the only thing recorded) -- `git_tree_dirty: false` plus the SHA
is what actually proves reproducibility; had the tree been dirty, the
`dirty_diff_sha256`/`dirty_files` fields would also be populated and
required for exact reproduction, never silently omitted.

Dataset-wide identity totals are unchanged between v3 and v4 (confirming the
fix touched only reference-value extraction, never identity extraction):
11,613/11,613 archives `ok`, 15,705 `REFERENCE_SUBSTANCE` records, 619 with a
recognized E-number, 0 with an E-number conflict, 4,352 unique CAS numbers,
3,357 unique EC numbers -- identical in both versions. The reference-value
numeric comparison itself is in §2.

The pilot was then rerun against `staging/v4` into a **new** output directory
(`pilot/v2/`, `pilot/v1/` preserved unchanged) -- see §5.

## 5. Pilot run: commands, inputs and results

```
cd nutriguard-backend
python3 -m scripts.openfoodtox.pilot \
  --dossiers-dir /home/vboxuser/nutriguard-data/openfoodtox/originals/2026-09-30/dossiers \
  --staging-dir  /home/vboxuser/nutriguard-data/openfoodtox/staging/v4 \
  --output-dir   /home/vboxuser/nutriguard-data/openfoodtox/pilot/v2 \
  --e-numbers E250 E150d E330 E951
```

- Ran twice end-to-end and diffed every output byte-for-byte (excluding the
  run timestamp): identical both times.
- `pilot_summary.json` now records `pilot_producing_code` (this run's own
  fingerprint -- clean, `0beb271...`), `staging_catalogue_producing_code`
  (what built `staging/v4` -- same clean SHA here, confirming code and data
  are from the same revision for this run), and
  `pilot_code_extraction_logic_version: 2`.
- **Coverage label** (explicit in every output file): *seed/supplied-snapshot
  coverage only -- NOT production database coverage.*

| Query | Catalogue entry? | Result | Dossiers | `consumer_guidance_eligible` values |
| --- | --- | --- | --- | --- |
| E250 (Sodium Nitrite) | Yes (E-number only, no CAS on file) | `exact_match` | 1 | **1 of 1** -- ADI 0.1 mg/kg bw/day, consumers, basis resolved (sodium nitrite) |
| E150d (caramel colour IV) | No -- ad hoc dataset query, not a catalogue linkage | `exact_match` | 1 | 0 of 1 -- ADI 300 mg/kg bw/day found, but basis unresolved from text |
| E330 (Citric acid) | Yes (CSV starter) | `exact_match` | 3 | 0 of 5 -- see §3c and the FEEDAP finding below |
| E951 (Aspartame) | Yes (JSON seed) | `exact_match` | 6 (5 with a reference value) | 0 of 5 -- basis unresolved from text (different phrasing than E250's, see §3c) |

Every catalogue entry in the tracked seed data has **no CAS number at all**
(`app/seed/load_seed.py` hard-codes `cas_number: None`) -- confirmed by
`test_no_identity_has_a_cas_number_in_current_tracked_seed_data`. Matching is
therefore necessarily E-number-only on the catalogue side; the CAS-vs-E-number
agreement/conflict rules are implemented and tested (§3b) but only
exercisable against the real dataset once the catalogue gains CAS data.

### E330 (citric acid): the feed-vs-food finding stands, now more precisely gated

Two of the three matched dossiers (`expert_group_label` = "EFSA FEEDAP",
`regulation_label` = "Regulation (EC) No 1831/2003 (amended)") are
animal-feed additive assessments. Under the corrected §3c gate, their
reference values are now blocked for *multiple, independently-listed*
reasons (type, population, feed-context, and unresolved basis all at once --
see §3c), not just the feed flag alone. The one human/food (EFSA ANS) dossier's
ADI entry still has no numeric magnitude extracted at all (JECFA 1974: "the
estimated ADI for man was not limited").

## 6. Bilingual drafts (item 5) -- redesigned

**Superseded by §11** (2026-10-01, third revision): the BG example below is
historical -- it predates translating exact-equivalent terms
(`натриев нитрит`, `мг/кг телесно тегло дневно`) and predates "Intake
guidance" actually being shown for an eligible value. Left as-is here to
preserve the record of what the second revision actually produced; §11 is
current.

The first revision quoted the EFSA source text verbatim under a Bulgarian
heading and called that the "BG draft" -- which is exactly what this review
correctly flagged as presenting an English quotation as a completed BG
description. Redesigned:

- **Draft EN/BG text is now a genuine, readable paraphrase**, built *only*
  from already-extracted structured fields (value type, magnitude, unit,
  population, chemical basis, dates, feed-context flag) -- e.g. "EFSA set
  Acceptable Daily Intake (ADI) of 0.1 mg/kg bw/day for the general consumers
  population, with a chemical basis of sodium nitrite. (Source: ..., 2017-04-05.)"
  and its independent BG rendering "ЕФСА определя допустима дневна доза (ADI)
  от 0.1 mg/kg bw/day за общата популация от потребители, с химична основа
  sodium nitrite. (Източник: ..., 2017-04-05.)" -- never an algorithmic
  rewrite of the source's own free-text justification (which would risk
  subtly changing a scientific/legal source's meaning).
- **The verbatim source text moved to a separate `internal_evidence_en`
  section** (and its own block in the `.md` file, after both drafts, clearly
  headed "not for direct publication") -- so a reviewer can check the
  paraphrase against the original, but the two are never conflated.
  Chemical/unit/population terms (e.g. "sodium nitrite", "mg/kg bw/day") are
  deliberately left untranslated in the BG paraphrase rather than guessed at
  -- a stated limitation (§8), not silently done.
- **Omitted-field notes are now retained internally** even for sections
  hidden from both drafts: `omitted_sections_internal_note` in every profile
  lists "What it is" and "Purpose in food" (no sourced identity/function text
  exists in OpenFoodTox's own reference values) and "Intake guidance" (gated
  on `scientific_review`, which stays `not_reviewed` by design in this
  pilot -- never shown even for E250's one fully eligible value).
- Both languages remain explicitly `DRAFT`/`not_reviewed`; no runtime
  localization writes occur anywhere in this pilot (it never opens a
  database connection at all); no claim of publication readiness.

**Concise sample drafts for all four pilot identities are committed** in
`docs/OPENFOODTOX_PILOT_REVIEW_DRAFTS.md` (git-tracked, so Codex can review
through Git without the VM's bulk `pilot/v2/` output directory) -- full
per-dossier detail remains in the (uncommitted, bulk) `*_profile.json`/`.md`
files per §4's convention.

## 7. Tests

```
cd nutriguard-backend
python3 -m pytest tests/unit/test_openfoodtox_matcher.py \
  tests/unit/test_openfoodtox_catalogue_snapshot.py \
  tests/unit/test_openfoodtox_evidence_bundle.py \
  tests/unit/test_openfoodtox_records.py \
  tests/unit/test_openfoodtox_dossier.py -q
# 72 passed in 0.28s

python3 -m pytest -q
# 848 passed, 16 skipped, 3 warnings in 18.09s
```

`test_openfoodtox_matcher.py`: 17 (was 15; +2 for the §3b truth-table fix:
`test_neither_agreeing_is_unrelated_not_conflicting`,
`test_full_truth_table_in_one_stream`). `test_openfoodtox_evidence_bundle.py`:
15 (was 2; +12 for `TestAssessReviewEligibility`, +1 extended end-to-end
assertion set). `test_openfoodtox_catalogue_snapshot.py`: unchanged at 9.
The 16 skips remain the pre-existing opt-in `tests/postgres/` suite. The 3
warnings are pre-existing and unrelated. Zero failures.

## 8. Limitations (reported honestly, not worked around)

- **No CAS numbers in the tracked catalogue today** -- matching is
  E-number-only in practice; the dual-identifier agreement/conflict rules
  (§3b) are implemented and tested but not yet exercisable against real data.
- **E150d has no tracked-catalogue entry at all** -- reported as an explicit
  ad hoc dataset query, never invented or substituted.
- **Updated by §11**: two of four pilot substances now have a
  `consumer_guidance_eligible` value -- E250 (automated) and E951 (via a
  directly-verified editorial chemical-basis override, §11d item 2's
  sibling discussion in §3c). E150d and E330 remain ineligible: this is the
  gate working as intended (fail-closed), not a shortfall to fix by
  loosening it; closing the gap for them requires either richer source text
  or (for E330) a completed EFSA re-evaluation that doesn't yet exist (§9).
- **Updated by §11**: exact-equivalent controlled-vocabulary terms (sodium
  nitrite, aspartame, mg/kg bw/day, etc.) are now translated in the BG text
  via a small curated allowlist; anything *not* in that allowlist is still
  left as-is rather than guessed at. Still needs a reviewed BG terminology
  decision before publication for any term not already in the allowlist.
- **Deduplication of repeated reference-value claims is exact-structured-field
  only** (value/unit/population/basis/justification-text/feed-context tuple)
  -- near-identical justification text differing by a few words across
  assessments is still shown as separate claims rather than merged
  (conservative; unchanged from the first revision).
- **`resolve_subject_links`'s multi-identity path is tested only against
  synthetic fixtures**, not one of the real dataset's 333 multi-identity
  dossiers end-to-end (none of the four pilot substances needed it).
- **E150d's superseding-opinion check (§9) found a 2012 exposure refinement**
  but this session did not attempt a from-scratch re-evaluation search beyond
  what surfaced in that check.

## 9. Reuse and freshness findings (corrected and completed)

**OpenFoodTox distribution and reuse terms** (checked 2026-10-01, unchanged
from the first revision -- not implicated in Codex's correction):
Official EFSA page
<https://www.efsa.europa.eu/en/data-report/chemical-hazards-database-openfoodtox>;
archived release Zenodo DOI [10.5281/zenodo.8120114](https://zenodo.org/record/8120114);
license **CC BY 4.0** (reuse authorized with EFSA attribution). **Third-party
exception, still not cleared**: CC BY 4.0 covers OpenFoodTox's own structured
data, not the full text of third-party scientific literature cited within
individual dossiers -- not further investigated.

**Source-check table** (every DOI below was independently fetched/searched
on 2026-10-01 in this session, not assumed from the dataset's own title text
alone):

| Substance | Dataset DOI / title / date | Actual assessed identifiers (independently verified) | Relevance | Superseding-status evidence |
| --- | --- | --- | --- | --- |
| E250 | `10.2903/j.efsa.2017.4786`, "Re-evaluation of potassium nitrite (E 249) and sodium nitrite (E 250) as food additives", adopted 2017-04-05 | E249, E250 -- directly named and assessed (confirmed via EFSA/secondary sources: ADI 0.07 mg nitrite ion/kg bw/day via BMD approach) | Direct, exact | No newer EFSA *toxicological* re-evaluation located. Commission Regulation (EU) 2023/2108 later revised permitted **maximum use levels** (not the ADI) for some food categories, effective 2025-10-09 -- a separate regulatory development, not a new hazard opinion. |
| E330 | No standalone opinion in the dataset -- 3 dossiers: 2016-09-28 TMDC (ANS, food, citric acid as a comparator substance), 2× 2015-01-27 FEEDAP (animal feed) | Citric acid is a cited component/comparator, never the sole subject of a dedicated dataset dossier | Indirect | **Corrected this revision**: `10.2903/j.efsa.2020.6032` does **not** cover E330 (it assesses E472a-f esters; independently confirmed via EFSA/Wiley + secondary sources). Per Commission Regulation (EU) No 1419/2020's own text, E330's dedicated re-evaluation remains explicitly **pending/low-priority**; an EFSA open data call for E330 analytical/use-level data has a 2026-06-30 deadline (ongoing). **No standalone re-evaluation exists yet** to supersede or confirm the 1990 SCF "ADI not specified" conclusion -- freshness is genuinely **unresolved**, not a confirmed gap against a named newer opinion (the first revision's claim of a confirmed gap is withdrawn). |
| E951 | `10.2903/j.efsa.2013.3496`, "Scientific Opinion on the re-evaluation of aspartame (E 951) as a food additive", Dec 2013 | E951 -- directly named and assessed | Direct, exact | **Updated within a later, broader opinion (corrected this revision -- see §11 item 3)**: `10.2903/j.efsa.2026.10259`, "Re-evaluation of salt of aspartame-acesulfame (E 962) as food additive" -- **Approved: 1 July 2026; First published: 10 September 2026** (these are two distinct dates, kept distinct; the opinion was *not* "adopted on September 10"). EFSA's own plain-language summary (fetched directly) states this opinion's E951 work is *"an updated assessment within the E 962 re-evaluation"* -- an updated toxicological and dietary-exposure assessment of E951 conducted within the scope of re-assessing E962, not a standalone full re-evaluation of E951 and not a claim that the entire 2013 opinion was superseded. ADI for E951 **reaffirmed unchanged at 40 mg/kg bw/day** ("The existing acceptable daily intakes (ADIs) for E 951 ... remain valid"). This opinion is **not present in the OpenFoodTox dataset used here** (newest E951 dossier: 2013-11-28) -- a real, precisely-dated freshness gap. |
| E150d | `10.2903/j.efsa.2011.2004`, "Scientific Opinion on the re-evaluation of caramel colours (E 150 a,b,c,d) as food additives", March 2011 | E150a/b/c/d -- directly named and assessed (group ADI 300 mg/kg bw/day, sub-ADI 100 mg/kg bw/day for E150c) | Direct, exact | **Partially superseded (newly checked this revision)**: `10.2903/j.efsa.2012.3030`, "Refined exposure assessment for caramel colours (E 150a, c, d)", Dec 2012 -- revises **exposure estimates only** (not the hazard/ADI figures) for E150a/c/d, concluding actual exposure is lower than 2011 estimates except E150c for high-consuming toddlers/adults, who may still exceed its 100 mg/kg bw/day sub-ADI. Not present in the dataset used here. The underlying ADI for E150d itself is not superseded by this -- only the exposure context around the group's E150c member is. |

This table replaces the first revision's unaudited, partially-wrong §9 in
full; all four substances are now checked, not just the one Codex flagged.

## 10. Integration blockers (explicit, none resolved here)

1. No CAS numbers in the tracked ingredient catalogue (§8) -- blocks the
   matcher's dual-identifier confirmation path in practice.
2. `scientific_review`/`translation_review` remain `not_reviewed` by design
   for every profile -- a human/scientific reviewer must check each
   paraphrase against its internal evidence quote (and decide on BG
   terminology for the untranslated terms, §6/§8) before any of this reaches
   a consumer-facing surface.
3. `reuse_clearance` is `pending_external_check` -- CC BY 4.0 covers the
   structured data; attribution placement and the third-party literature
   exception (§9) still need a product/legal decision.
4. `source_freshness` is `checked_limited_scope` -- E951 has a confirmed,
   not-yet-incorporated later opinion updating its assessment within the
   E962 re-evaluation (Approved 1 July 2026, First published 10 September
   2026, §9); E330's freshness is unresolved (no completed re-evaluation
   exists yet); E150d has a partial 2012 exposure-only update not yet
   incorporated.
5. Under the corrected eligibility gate (§3c), only E250 currently has a
   `consumer_guidance_eligible` reference value at all -- any future
   integration work should not assume the other three substances are close
   to ready.
6. No code in this pilot writes to the live database, changes any API
   contract, or is wired into any application code path.

## 11. Substantive EN/BG content and content corrections (2026-10-01, third revision)

`docs/OPENFOODTOX_PROFILE_CONTENT_TASK.md` (reviewed at commit
`40d063444358086c2b47dbe1a2a7386b9509349d`) asked for four *substantive*
profiles -- identity/origin, purpose in food, and effects narrative, not
bare number restatements -- plus four specific content corrections and a
fix to a real gating inconsistency. Implemented at commit
`631939a120ad7bf79f1d7f95ab9d821caf741916`.

### 11a. New editorial content layer

`scripts/openfoodtox/editorial_content.py` -- a separately versioned
(`EDITORIAL_CONTENT_VERSION = 1`), fixed, reviewed content module, never a
live model call at runtime, holding "What it is"/"Purpose in food"/"Relevant
effects" text per substance. Every sentence carries its own `source_kind`
(`openfoodtox_dossier` / `tracked_seed_csv` / `external_primary_source`) and
citation, so it is never confused with OpenFoodTox's own extracted evidence.
`evidence_bundle.py`'s draft builder now populates "What it is" and "Purpose
in food" from this layer (previously always omitted -- the task's own
"they do not yet explain what the ingredient is" complaint) and weaves
editorial "Relevant effects" narrative (tagged human / animal-in-vitro /
assessment-conclusion) in with the existing structured reference-value
findings.

### 11b. Numeric-gating consistency fixed

The task's own finding: "the current template can display a numeric value
in 'Effects and conditions' even while hiding 'Intake guidance'." Fixed --
`_finding_sentence_en/bg` (Effects) and `_intake_sentence_en/bg` (Intake
guidance) now use the *exact same* `consumer_guidance_eligible` test; an
ineligible value's magnitude is withheld from both sections, replaced with
an explanation that points to internal evidence, never shown in one place
and hidden in the other. "Intake guidance" is now actually populated (not
unconditionally omitted as in the previous revision) for every eligible
value, with the whole profile still explicitly `DRAFT`/`not_reviewed` at
the top -- showing substantive, well-supported content and staying
unreviewed are not the same thing.

### 11c. Feed/worker findings fully excluded from the consumer draft

Previously caveated inline ("NOTE: ... must not be presented as human
dietary guidance") but still shown. Now `build_profile` filters
`feed_or_livestock_context` values out of the consumer-facing draft
entirely -- confirmed on E330 (its 4 FEEDAP-sourced findings, including the
15,000 mg/kg feed level, no longer appear in either draft at all) and
covered by a new regression test
(`test_feed_context_values_excluded_from_consumer_draft_entirely`). They
remain fully visible in `deduplicated_reference_values`/
`internal_evidence_en` for operator inspection, per the task's "retaining
it only in operator evidence."

### 11d. The four required content corrections

1. **E951 PKU exclusion**: carried into both language drafts, adjacent to
   every shown ADI/general-population-safety-conclusion claim (twice: once
   after the assessment-conclusion narrative in "Effects and conditions",
   once in "Intake guidance"), never left buried under Sources. Phrased as
   the existing medical dietary restriction it is, not personalised advice.
2. **E150d group ADI scope**: preserved precisely. Verified directly (not
   trusting a prior summary) that the 2011 opinion's group ADI of 300 mg/kg
   bw/day covers all four caramel colours (E150a-d) *combined*, and that
   only E150c carries an additional 100 mg/kg bw/day sub-ADI (due to
   uncertainty about an immune-system effect of one of its constituents,
   THI) -- E150d itself has no separate sub-ADI. The record's own chemical
   basis is kept unresolved rather than guessed (OpenFoodTox's own
   extracted text for it is the two-word comment `"ADI (group)"`); the
   group-scope figure itself is stated as independently-sourced editorial
   content with its own citation, a different claim from "this record's
   own eligible number," which stays withheld (§11b).
3. **E962 opinion dates**: corrected. The opinion's own page states
   **"First published: 10 September 2026"** and **"Approved: 1 July
   2026"** -- two distinct dates; the previous revision's "adopted
   2026-09-10" framing conflated them and has been removed everywhere
   (§9's table, the integration-blockers list, `editorial_content.py`).
   Its scope is now described precisely as an updated E951 assessment
   *within* the E962 re-evaluation (EFSA's own plain-language summary:
   "an updated assessment within the E 962 re-evaluation"), not a
   standalone E951 re-evaluation and not a claim that the entire 2013
   opinion was superseded.
4. **BG terminology**: exact, high-confidence equivalents are now
   translated in the consumer-facing BG text (e.g. `натриев нитрит`,
   `аспартам`, `мг/кг телесно тегло дневно`) via small, curated allowlists
   in `evidence_bundle.py` (`_bg_population`/`_bg_unit`/`_bg_basis`) --
   anything not in the allowlist is left as-is rather than guessed. The
   original machine-readable English fields are untouched in
   `deduplicated_reference_values`. A real regression was found and fixed
   while building this: the ineligible-value fallback sentence was
   interpolating the raw English `review_eligibility.reasons` strings
   (e.g. `status='unresolved_no_mention'`) directly into the Bulgarian
   sentence -- exactly the "English population labels or placeholders in
   the Bulgarian consumer-preview body" the task warned against. Replaced
   with a generic, fully-Bulgarian phrase pointing to the internal review
   notes instead.

### 11e. A verbosity bug found and fixed while building these drafts

`_dedupe_reference_values`'s exact-match grouping (by value/unit/population
*and* the literal justification text) meant E951's five real assessments,
whose justification wording differs slightly each time, never merged for
display -- so the (now much more substantial) per-value sentences were
repeated five times each in both "Effects and conditions" and "Intake
guidance" (ten repetitions of a multi-sentence editorial basis explanation
in the first draft of this round). Fixed with a second, coarser,
*display-only* grouping (`_group_for_consumer_display`, by value/unit/
population alone) used solely for rendering the draft text -- the full,
unmerged per-assessment detail (every distinct quote) remains in
`deduplicated_reference_values`/`internal_evidence_en`. Also shortened the
E951 editorial chemical-basis text itself: the short label (`"aspartame
itself"`) is what appears inline per claim now; the longer rationale
appears once, in `remaining_uncertainties`, not repeated per sentence.

### 11f. Source verification for this round

Fetched/read directly (not search snippets) on 2026-10-01: the EFSA
plain-language summary for the E962 re-evaluation (confirming its E951
scope and the ADI-reaffirmation wording); Commission Regulation (EU) No
231/2012 (via search-engine-indexed excerpts of its own text, for
E150a-d's manufacturing-process definitions and sodium nitrite/citric
acid's specifications); and secondary EFSA-sourced reporting confirming the
2011 caramel-colours group-ADI/sub-ADI figures and the 2012 exposure-update
scope precisely (group ADI 300 mg/kg bw/day for all four colours; E150c's
own 100 mg/kg bw/day sub-ADI due to THI). The Wiley-hosted DOI landing pages
themselves returned HTTP 403 to automated fetches in this session (as in
the previous round) -- the exact "First published"/"Approved" dates for
`10.2903/j.efsa.2026.10259` were supplied directly by the task document
(stated as read from that page) and used as given, with the correction
applied to every place this session had previously stated them incorrectly.

### 11g. Tests and verification

5 new tests in `tests/unit/test_openfoodtox_evidence_bundle.py`
(`TestEditorialContentCorrections`): PKU adjacency (both languages, twice,
before Sources), E150d group-ADI scope preservation, numeric-gating
consistency between sections, the E962 date-type distinctness (read
directly from `editorial_content.py`'s own data, not re-derived), and
feed-value exclusion from the consumer draft. All verify structure and the
presence of required qualifiers, not scientific truth. Per the task's own
instruction ("do not rerun the whole dataset merely for prose changes
unless extraction changed"), `staging/v4`/`reports/v4` were **not**
regenerated this round (no extraction code changed) -- only the pilot was
rerun, against the unchanged `staging/v4`, into a new `pilot/v4/`
(`pilot/v1`-`v3` preserved unchanged).

```
cd nutriguard-backend
python3 -m pytest tests/unit/test_openfoodtox_matcher.py \
  tests/unit/test_openfoodtox_catalogue_snapshot.py \
  tests/unit/test_openfoodtox_evidence_bundle.py \
  tests/unit/test_openfoodtox_records.py \
  tests/unit/test_openfoodtox_dossier.py -q
# 77 passed in 0.36s

python3 -m pytest -q
# 853 passed, 16 skipped, 3 warnings in 22.17s

python3 -m scripts.openfoodtox.pilot \
  --dossiers-dir /home/vboxuser/nutriguard-data/openfoodtox/originals/2026-09-30/dossiers \
  --staging-dir  /home/vboxuser/nutriguard-data/openfoodtox/staging/v4 \
  --output-dir   /home/vboxuser/nutriguard-data/openfoodtox/pilot/v4 \
  --e-numbers E250 E150d E330 E951
```

Ran twice end-to-end and diffed every output byte-for-byte (excluding the
timestamp): identical. `pilot_summary.json`'s `pilot_producing_code`
confirms a clean tree at `631939a120ad7bf79f1d7f95ab9d821caf741916`. Full,
complete EN/BG drafts for all four identities (not abbreviated) plus a
summary of each profile's claim-source matrix and remaining uncertainties
are committed in `docs/OPENFOODTOX_PILOT_REVIEW_DRAFTS.md` (revision 2).

### Updated results snapshot (supersedes §5's table for `consumer_guidance_eligible`)

| Identity | `consumer_guidance_eligible` values | Basis source |
| --- | --- | --- |
| E250 | 1 of 1 | automated |
| E150d | 0 of 1 | unresolved (no override applied -- uncertain, not guessed) |
| E330 | 0 of 2 distinct findings (4 feed values excluded entirely) | unresolved |
| E951 | 1 of 1 (after display-grouping five assessments into one) | **editorial_override**, directly verified this round |

## 12. Targeted corrections (2026-10-01, fourth revision)

`docs/OPENFOODTOX_FINAL_PROFILE_REVIEW_TASK.md` (reviewed baseline
`b915ccdabc23729110d41e32df15876adb7863ad`) found a real, specific gap §11b's
"numeric gating consistency" fix had not actually closed, plus two smaller
corrections. Implemented at commits `704d5b7e2b60a5934fa2dbf777176baf185b9b28`
and `b051e2a2242b14b635fa3ca6bae6c4b2a94b2ca8` (the second a one-line follow-up removing a dangling
cross-reference the first introduced).

### 12a. The gating fix only covered structured reference values, not editorial text

§11b's fix made `_finding_sentence_en/bg` (Effects) and
`_intake_sentence_en/bg` (Intake guidance) share one eligibility test for a
reference value's own magnitude -- but `editorial_content.py`'s E150d
`effects` notes separately stated the group ADI (300 mg/kg bw/day) and
E150c's own sub-limit (100 mg/kg bw/day) as **plain narrative text**,
entirely outside that gate, then the very next paragraph said the figure
"is not shown... pending review" -- directly contradicting itself. Fixed:
both numbers moved to a new `operator_only_notes` field on `EditorialEntry`
(never rendered in `draft_en`/`draft_bg`, exposed instead as
`editorial_operator_only_notes` in the profile JSON for operator review).
The consumer-facing effects note now states the group-scope qualifier (it's
a joint limit, not an individual E150d allowance; only E150c has its own
separate limit) without either figure. This is a single policy, not a
per-substance patch: a new test,
`test_no_unconfirmed_numeric_intake_claims_in_consumer_facing_editorial_content`,
mechanically scans *every* consumer-facing editorial field (`identity`,
`purpose`, `effects`, `population_exceptions`, `group_scope_note`) for a
bare dose-shaped number, for every identity without an
`editorial_chemical_basis` override -- a future addition can't bypass this
by choosing a different field, evidence_type, or source_kind, since the
scan covers all of them uniformly. Two further tests confirm the fix
end-to-end (no leaked numbers in either language) and that unrelated
numbers (assessment years, E-code digits) are never caught by the same
filter.

Also removed two technical/debug-sounding fallback sentences from both
language drafts ("pending review of this preview's eligibility criteria...
see the internal review notes for exactly which criterion") -- replaced
with plain language ("the specific figure is not included in this preview
yet"); the actual reasons remain fully available in
`review_eligibility.reasons` for operator review, never lost, just no
longer phrased as internal tooling jargon in consumer text.

### 12b. Localized, non-technical titles

Added `evidence_bundle._display_name`, a small, explicit EN/BG map for the
four pilot identities (Sodium nitrite/Натриев нитрит, Sulphite ammonia
caramel/Сулфитно-амонячен карамел, Citric acid/Лимонена киселина,
Aspartame/Аспартам), used only for the draft's own heading. E150d's
previous heading literally read "(ad hoc query, not a provisioned
NutriGuard ingredient: E150d) (E150d)" -- the ad hoc status is real and
important *operator* metadata (still present verbatim in the profile's own
`catalogue_identity.common_name`), but never appropriate as the
consumer-facing ingredient name. No live catalogue row was invented for
this -- `_display_name` falls back to `common_name` for any identity not
in its small map. Also fixed a BG grammar error ("каква точно количество"
-- feminine agreement on a neuter noun -- corrected to "какво точно
количество"). Two new tests
(`TestLocalizedDisplayTitles`) check both languages and confirm no
technical placeholder leaks into either heading.

### 12c. Citation audit

Found one genuinely vague citation: E330's identity claim cited "Commission
Regulation (EU) No 231/2012 ... cross-checked against standard
food-chemistry references" -- the second clause named no actual source.
Replaced with the Regulation's own Annex entry for E330 CITRIC ACID,
quoting its "Definition" text directly ("obtained by fermentation of
carbohydrate solutions ... with the mould Aspergillus niger"). Audited
every other citation against what its named source actually supports (no
regulation cited for an effect/intake claim it doesn't cover; every
safety/effect conclusion traces to an OpenFoodTox dossier or a named EFSA
opinion/press release, never to the internal seed CSV/JSON alone). New
`docs/OPENFOODTOX_CLAIM_SOURCE_MATRIX.md`: every one of the 21 editorial
claims with a stable ID, source kind, and citation, generated directly from
`editorial_content.py` (not hand-transcribed), reviewable via Git. Two new
structural tests (`TestEditorialCitationCoverage`) check every claim has a
non-trivial, non-vague source and every `external_sources` entry has a URL
and access date -- never asserting a claim's scientific truth.

### 12d. Test-count reconciliation

The task asked to reconcile "858 passed" (stated in this session's own
end-of-round chat summary after the previous round) against
`docs/CODEX_HANDOFF.md`'s committed "853 passed" for the same commit
(`b915ccd`). Checked directly: **no committed document ever stated "858"**
-- `docs/CODEX_HANDOFF.md` and this report both correctly recorded the
actual `pytest -q` output of 853 passed at that commit. "858" was this
session's own arithmetic in conversation (853 + the 5 tests just added,
double-counting since 853 already included them) -- a verbal slip, not a
documentation error, and nothing needed correcting in either file for that
reason. This round's own actual count, run directly:

```
cd nutriguard-backend
python3 -m pytest -q
# 860 passed, 16 skipped, 3 warnings in 19.32s
```

853 (previous) + 7 new this round (3 numeric-gating-policy regressions, 2
title-localization, 2 citation-coverage) = 860, matching exactly.

### 12e. Delivery

Per the task's own instruction, extraction code was not touched this round,
so `staging/v4`/`reports/v4` were **not** regenerated -- only the pilot
previews were rerun (against the unchanged `staging/v4`) into a new
`pilot/v5/` (`pilot/v1`-`v4` preserved). Ran twice, diffed byte-for-byte
(excluding timestamp): identical. `docs/OPENFOODTOX_PILOT_REVIEW_DRAFTS.md`
updated to revision 3 (E150d section replaced with the corrected,
number-free consumer preview; E250/E330/E951 headings updated to the
localized names).

## 13. Delivery-consistency closure (2026-10-01, fifth revision)

`docs/OPENFOODTOX_DELIVERY_CONSISTENCY_TASK.md` (reviewed baseline
`8ceba0531082e2c63cbe62598af663c9f852ece9`) found the previous round's own
delivery had drifted from what it claimed, and that several consumer claims
still cited only internal seed data as if that were independent evidence.
Implemented at commit `d573d821770dcc5f147b7c5c4b502ddd0d580320`. The owner
authorized subagent use for this round; one general-purpose subagent did
the source-tracing research in item 13b (reading primary documents, not
snippets), while this session did the mechanical/integration work in 13a
and reviewed and independently spot-checked the subagent's findings before
using them (the EFSA-2017-nitrite PMC mirror and the EUR-Lex-blocked
pattern were both independently reproduced here, not merely trusted).

### 13a. Committed previews had drifted from actual generated output

`docs/OPENFOODTOX_PILOT_REVIEW_DRAFTS.md` still said "revision 3,
superseding... reproduce `pilot/v5`" while containing the pre-fix
`каква точно количество` grammar error, English headings inside Bulgarian
bodies, and other residue from earlier hand-copy-paste export rounds --
the committed file had not actually been re-derived from a real pilot run
since an early revision, despite each round's commit message claiming
otherwise. Root cause: every previous round exported the EN/BG bodies by
hand (reading a profile's `.md` file and retyping/reformatting it into the
review-drafts document's blockquote structure), which is exactly the kind
of manual step that silently drifts.

Fixed structurally, not just re-synced once: new
**`scripts/openfoodtox/export_docs.py`** mechanically generates both
committed docs with no manual step in between:

```
python -m scripts.openfoodtox.pilot \
  --dossiers-dir /home/vboxuser/nutriguard-data/openfoodtox/originals/2026-09-30/dossiers \
  --staging-dir  /home/vboxuser/nutriguard-data/openfoodtox/staging/v4 \
  --output-dir   /home/vboxuser/nutriguard-data/openfoodtox/pilot/v7 \
  --e-numbers E250 E150d E330 E951

python -m scripts.openfoodtox.export_docs review-drafts --output-dir /home/vboxuser/nutriguard-data/openfoodtox/pilot/v7
python -m scripts.openfoodtox.export_docs claim-matrix

# reproducible check -- exits non-zero and prints a line-level diff on any mismatch:
python -m scripts.openfoodtox.export_docs review-drafts --output-dir /home/vboxuser/nutriguard-data/openfoodtox/pilot/v7 --check
python -m scripts.openfoodtox.export_docs claim-matrix --check
```

Both `--check` runs reported `CHECK OK` immediately after `export` (tested
in this session). The review-drafts format itself changed from
blockquote-prefixed text (error-prone to reproduce by hand, which is
exactly what drifted) to **fenced code blocks** (` ```text ... ``` `) around
each of the 8 verbatim bodies (4 identities x EN+BG) plus each identity's
`internal_evidence_en` -- a deliberate, documented wrapper change, not
content drift. The file's own header now states this and tells a future
editor never to hand-edit inside a fenced block, only re-run `export`.
Internal evidence/operator-only notes remain in clearly separate sections,
never merged into the consumer preview bodies, per the task's explicit
instruction to keep them apart. Two new tests
(`tests/unit/test_openfoodtox_export_docs.py`) cover rendering all four
identities from synthetic profile JSON and confirm the renderer is
deterministic (byte-identical across two calls).

Per the task's own instruction, extraction code was not touched this round
-- `staging/v4`/`reports/v4` were **not** regenerated; the pilot was rerun
(against the unchanged `staging/v4`) into a new `pilot/v7/` (`pilot/v1`-`v6`
preserved), verified repeatable (ran twice, diffed every profile JSON
byte-for-byte, identical).

### 13b. Primary-source traceability, not just seed attribution

The claim/source matrix's own audit note claimed "every effects-section
conclusion traces to an openfoodtox_dossier or external_primary_source"
while three rows contradicted it outright
(E250 human-evidence/nitrosation claims, E951 digestion/metabolism claim --
all `tracked_seed_csv` only). Fixed by upgrading all three to a directly-read
primary source (not a search snippet, not a secondary report mislabeled as
primary):

- **E250 human-evidence claim**: now cites the actual EFSA 2017 nitrite
  re-evaluation's own text (Section 3.6.8 and its exposure discussion),
  read via an open-access PMC mirror (efsa.europa.eu/Wiley both block
  automated fetching in this environment, as in every prior round) --
  quoting the Panel's own conclusion that nitrosamines from added nitrite
  could not be clearly separated from those already in the food matrix,
  and that its exposure estimates "do not relate only to the use of
  nitrite as food additive."
- **E250 nitrosation claim -- corrected, not just re-sourced**: the
  primary text revealed the previous claim had the relationship backwards.
  It said nitrosation "fed into the overall risk characterisation rather
  than into the ADI figure itself"; the opinion's own Sections 3.6.1/3.7.1/3.7.2
  show the Panel chose the benchmark-response magnitude used to derive the
  ADI *itself* partly to keep the resulting nitrosamine margin of exposure
  above 10,000 (calculated at ~420,000) -- nitrosation shaped the ADI
  derivation, not only a separate downstream step. Reworded accordingly.
- **E951 digestion/metabolism claim**: now cites the EFSA 2013 aspartame
  opinion's own Abstract directly (near-verbatim match: "Aspartame is
  rapidly and completely hydrolysed in the gastrointestinal tract to
  phenylalanine, aspartic acid and methanol"), corroborated by the
  JECFA/WHO Food Additives Series 15 monograph (via IPCS INCHEM).
- **E330 identity claim -- re-verified per the task's explicit instruction
  not to trust the prior round's quote, and found wrong**: the previously
  cited "Definition" text ("obtained by fermentation of carbohydrate
  solutions (e.g., glucose syrups) with the mould Aspergillus niger") was
  itself an inaccurate paraphrase, not read from the actual regulation.
  The real text (read via an archived EUR-Lex snapshot -- live eur-lex.europa.eu
  blocks automated fetching -- and independently cross-checked against the
  UK's official statutory-text mirror, both matching exactly): "Citric
  acid is produced from lemon or pineapple juice, by fermentation of
  carbohydrate solutions or other suitable media using Candida spp. or
  non-toxicogenic strains of Aspergillus niger." The regulation allows
  *either* direct juice extraction *or* fermentation (the prior claim
  implied fermentation only), names Candida spp. as an alternative
  organism the prior claim omitted, includes a "non-toxicogenic strains"
  qualifier the prior claim dropped, and never says "glucose syrups" at
  all (an invented illustrative example, now removed). The claim and its
  citation were corrected to the verified text, with the correction
  itself documented in the source string (not silently swapped).

**Scope boundary, stated honestly rather than claimed complete**: the task
flagged these three as *examples*; a full audit found several more
`tracked_seed_csv`/`tracked_seed_json`-only claims remain --
E250/E330/E951's `purpose` (functional-classification) fields and E951's
`identity` field. These are lower-stakes (functional/identity description,
not a safety or effect conclusion) and were **not** re-sourced this round:
closing the explicitly-named gaps completely (including the E330
correction, which required discovering the prior citation was itself
wrong) was judged higher priority than a shallower pass across every
remaining claim. A new regression test
(`test_no_consumer_facing_effect_claim_relies_solely_on_tracked_seed_data`)
enforces the boundary actually reached: every `effects` note with
evidence_type `human` or `assessment_conclusion` must now trace to
`openfoodtox_dossier` or `external_primary_source`; `identity`/`purpose`
fields are explicitly outside that check's scope (documented in the test's
own docstring, not silently narrowed).

### 13c. Verification

```
cd nutriguard-backend
python3 -m pytest tests/unit/test_openfoodtox_evidence_bundle.py \
  tests/unit/test_openfoodtox_export_docs.py -q
# 32 passed in 0.19s

python3 -m pytest -q
# 865 passed, 16 skipped, 3 warnings in 17.21s
```

865 = 860 (previous) + 1 new provenance regression test + 4 new
`export_docs` tests. `docs/OPENFOODTOX_PILOT_REVIEW_DRAFTS.md` (fenced-block
format, regenerated from `pilot/v7`) and `docs/OPENFOODTOX_CLAIM_SOURCE_MATRIX.md`
(regenerated from `editorial_content.py`) both pass their own `--check`
immediately after `export`, and the pilot itself was confirmed repeatable
(two full runs, every profile JSON byte-identical).

## 14. Source closure: remaining identity/purpose claims (2026-10-01, sixth revision)

`docs/OPENFOODTOX_SOURCE_CLOSURE_TASK.md` (reviewed baseline `5cc732a`)
named the 6 remaining targets the fifth revision had explicitly deferred
or left under-sourced: E250 purpose, E330 purpose, E951 identity and
purpose, and two E150d claims (purpose and the group-ADI effect note, the
latter flagged because its own citation admitted only secondary
confirmation despite being labeled `external_primary_source`). Implemented
at commit `40a2d174845cc2a990bfec76ea9a284311af3e3b`. A subagent did the
primary-source research (reading actual documents, never a search-result
summary); this session independently reviewed every quote/locator before
using it and did not take any finding on trust.

### Target-by-target resolution

- **E250 purpose** (curing/preservative, C. botulinum inhibition,
  colour/flavour): now cites the EFSA 2017 nitrite opinion's own Section
  3.1.6 "Technological function" (doi:10.2903/j.efsa.2017.4786, read via
  the same open-access PMC mirror used in the fifth revision). All three
  claim elements confirmed directly in the primary text.
- **E330 purpose** (acidity regulator/sequestrant/flavour support): cites
  the JECFA "CITRIC ACID" monograph (INS 330, FNP 52 Add 7, 1999),
  "Functional uses" field -- "Acidulant; sequestrant; antioxidant
  synergist; flavouring agent" -- fetched directly from the FAO archive.
  **Honestly flagged, not silently accepted**: this is a genuine primary
  document but a JECFA/FAO-WHO one, not an EU-specific regulatory text;
  Commission Regulation (EU) No 231/2012's own E330 specification entry
  (also read directly via the UK's statutory mirror) turns out to have no
  functional-class field at all -- EU specification regulations list
  identity/purity criteria, not function. An EFSA FEEDAP opinion
  (doi:10.2903/j.efsa.2015.4010) does call citric acid "an acidity
  regulator" but in a feed-additive context, so it's cited only as
  secondary EU corroboration, not as the primary source for this claim.
- **E951 identity** (dipeptide structure, ~200x sweeter than sucrose) and
  **purpose** (sweetener in diet beverages/sugar-free confectionery): both
  now cite the EFSA 2013 aspartame opinion itself, read as a genuine
  full-text PDF (not a JS-shell snapshot, confirmed via `file`) via a
  Wayback Machine raw snapshot of the Wiley-hosted PDF -- Section 2.1 for
  the dipeptide structure and CAS number, the opinion's own reproduction
  of the Reg. 231/2012 specification table for the sweetness figure, and
  its Abstract plus reproduced Annex II use-table for the purpose claim.
- **E150d-08, group ADI -- the most important target**: this claim's own
  citation previously admitted "independently confirmed via EFSA-sourced
  secondary reporting" while being labeled `external_primary_source` -- a
  mislabeling bug on its own, independent of whether the claim was
  correct (it was). Fixed by actually reading the genuine 2011 EFSA
  caramel-colours opinion in full. Confirms: group ADI of 300 mg/kg bw/day
  across all four classes; an additional 100 mg/kg bw/day sub-ADI for
  Class III (E150c) only, due to THI
  (2-acetyl-4(5)-tetrahydroxybutylimidazole) immunotoxicity; E150d (Class
  IV, this profile) carries no separate limit of its own. The claim text
  needed no change -- only its citation was upgraded from
  secondary-admitted to genuinely primary.
- **E150d-07, purpose**: the previous citation ("cross-checked against the
  2011 EFSA opinion's background section") named no specific passage.
  Replaced with an exact locator (same 2011 opinion, p.19/p.21) and quote
  naming cola-type drinks, spirits (whisky/rum/brandy), sauces and baked
  goods directly -- not a generic, unsourced fact.

No claim was withdrawn to operator-only this round: all 6 targets reached
a directly-read, genuine primary source (one, E330 purpose, with an
explicit scope caveat rather than a clean match -- the task's own
stopping criteria accept this as resolution, not as a gap requiring
withdrawal, since the claim is supported, just not by an EU-specific
text).

### Test and verification

Extended the fifth revision's seed-only provenance regression
(`test_no_consumer_facing_claim_relies_solely_on_tracked_seed_data`,
renamed) to also cover `identity`/`purpose` fields, per the task's
explicit instruction ("not only effects"). Added
`test_no_external_primary_source_admits_only_secondary_confirmation` to
catch the E150d-08 mislabeling pattern as a standing regression going
forward.

```
cd nutriguard-backend
python3 -m pytest tests/unit/test_openfoodtox_evidence_bundle.py \
  tests/unit/test_openfoodtox_export_docs.py -q
# 33 passed in 0.22s

python3 -m pytest -q
# 866 passed, 16 skipped, 3 warnings in 21.34s
```

866 = 865 (fifth revision) + 1 new mislabeling-regression test. Per the
task's explicit instruction, extraction code was not touched -- the pilot
was rerun against the unchanged `staging/v4` into a new `pilot/v8/`
(`pilot/v1`-`v7` preserved), all four identities `exact_match`, producing
commit clean (`git_tree_dirty: false`). Repeatability re-verified: two
full pilot runs, every profile JSON byte-identical. Both committed docs
regenerated via `export_docs` and pass `--check` immediately after export.

## Files

New this revision (sixth): none (code/test/doc changes only, no new files).
New this revision (fifth): `scripts/openfoodtox/export_docs.py`,
`tests/unit/test_openfoodtox_export_docs.py`.
New this revision (fourth): `docs/OPENFOODTOX_CLAIM_SOURCE_MATRIX.md`.
New this revision (third): `scripts/openfoodtox/editorial_content.py`.
New in the review-corrections revision: `scripts/openfoodtox/provenance.py`,
`docs/OPENFOODTOX_PILOT_REVIEW_DRAFTS.md`.
Modified this revision: `scripts/openfoodtox/editorial_content.py` (§14),
`tests/unit/test_openfoodtox_evidence_bundle.py` (+1 test, +1 renamed/extended),
this file, `docs/OPENFOODTOX_PILOT_REVIEW_DRAFTS.md` (regenerated from
`pilot/v8`), `docs/OPENFOODTOX_CLAIM_SOURCE_MATRIX.md` (regenerated),
`docs/CODEX_HANDOFF.md`.
Generated, not committed (bulk/derived, kept outside Git):
`pilot/v8/` (`pilot/v1`-`v7` preserved unchanged). `staging/v4`/`reports/v4`
unchanged this round (extraction code untouched).
