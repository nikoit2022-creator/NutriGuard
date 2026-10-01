# OpenFoodTox offline matching and review-profile pilot -- report

Baseline reviewed: `56aee7aa613b8b7cc5f98e4082981ac0d898fc92`
(`docs/OPENFOODTOX_PILOT_TASK.md` / `docs/OPENFOODTOX_PROFILE_PRESENTATION.md`).
Worked in the existing isolated worktree on `feat/backend-openfoodtox-dataset-audit`,
fast-forwarded to that commit first (no drift). No merge, no deploy, no live
database access, no Android/API/migration/seed-data/Health-Score change, no
scheduled job, no live external lookup. This is an **offline, dry-run pilot**;
nothing here is visible in the application.

## 1. What was built

| Module | Purpose |
| --- | --- |
| `scripts/openfoodtox/catalogue_snapshot.py` | Minimal NutriGuard catalogue snapshot (id/common name/E-number/CAS) built from the **tracked seed files** (`app/seed/ingredients_seed.json`, `app/seed/e_additives_curated_starter.csv`), reusing the real seed loader's own pure row-transform functions and its "JSON seed row wins over the CSV starter row for the same E-number" merge rule. **No database is opened at all** -- not even a disposable/isolated one -- since those transform functions are pure; this is a stronger guarantee than the task's "isolated test database if required" allowance. Also provides `load_supplied_snapshot()` for a separately supplied, authorized ingredient-only JSON export (not used in this run -- no such export exists yet; no live export was fetched). |
| `scripts/openfoodtox/matcher.py` | Streaming (one `catalogue.jsonl` line at a time, never loaded whole into memory), dry-run identifier matching: exact/normalized CAS and E-number only, agreement required when both are present, conflicting identifiers stay unresolved, missing identifiers are never evidence, E-number ranges can never become an exact single-substance link, multiple assessments of one substance are grouped (not duplicated), dossiers resolving to different underlying identities are reported ambiguous. Also resolves which `REFERENCE_SUBSTANCE` identity a reference value or endpoint actually belongs to within one dossier (`resolve_subject_links`), instead of assuming one identity per archive (see §3). |
| `scripts/openfoodtox/evidence_bundle.py` | Builds the "required operator review bundle per identity" from `docs/OPENFOODTOX_PROFILE_PRESENTATION.md` for an `exact_match` result: re-parses only the matched archives (fast; no full ~1.3 GB catalogue rebuild needed), separates human/feed context, flags review eligibility per reference value, and writes a DRAFT EN/BG text pair. Never computes a numeric consumer guidance figure, never sets a REVIEWED/VERIFIED status anywhere. |
| `scripts/openfoodtox/pilot.py` | CLI orchestrator: builds the catalogue snapshot, matches the requested E-numbers, writes one JSON+Markdown profile per exact match plus a run summary (git SHA, input hashes, extraction schema note). |

## 2. A real extraction defect found and fixed before building any profile

While tracing why aspartame's (E951) ADI reference value looked inconsistent,
found that `records.py::derive_reference_values` treated **any** leaf literally
named `value` under a reference-value container as that container's own
numeric magnitude. `AssessmentBody` -- a sibling field recording *which body*
made the assessment (e.g. code `1342` = "HBGV not from EFSA committees/panels",
self-describing via its own `other` leaf) -- also ends in a leaf named `value`,
and was being captured as if it were the ADI's own number. For aspartame's
2013 re-evaluation this meant `entry["value"]` held the string `"1342"`
alongside the *correct* `entry["lower_value"] == "40"` -- a nonsensical
simultaneous state, and (via `extract_chemical_basis(entry["value"] or
entry["lower_value"], ...)`, which prefers `value` when both are truthy) the
*wrong* number would have been fed into chemical-basis matching too.

**Fix**: `derive_reference_values` now only accepts a `value`/`lowerValue`/
`upperValue` leaf when its immediate parent tag is one of the five confirmed
value-holding wrappers (`Adi`, `Arfd`, `Aoel`, `Aaoel`, `RefValue`) -- an
explicit allowlist, not an ever-growing exclusion list. Verified by grepping
every occurrence across the full 11,613-dossier dataset: zero
`<container>/value` leaves exist unwrapped, so this allowlist drops nothing
real.

**Measured dataset-wide impact of the bug** (grep across `staging/v3/catalogue.jsonl`,
counting raw `AssessmentBody/value` occurrences by container):

| Container | Corrupted-`value` occurrences |
| --- | --- |
| AcceptableDailyIntake (ADI) | 173 |
| AcuteReferenceDose (ARfD) | 31 |
| AcceptableOperatorExposureLevel / AcuteAcceptableOperatorExposureLevel (AOEL/AAOEL) | 4 |
| OtherReferenceValues | 580 |
| **Total** | **788** of 25,973 reference values (~3.0%) |

Two new regression tests added to `tests/unit/test_openfoodtox_records.py`
(`test_assessment_body_code_is_not_mistaken_for_the_reference_value`,
`test_other_reference_value_ref_value_wrapper_still_captured`) pin this fix
and its companion (`OtherReferenceValues`' own `RefValue` wrapper still
works). The previously staged `staging/v3/catalogue.jsonl` (used only for
*identity* matching below, which this bug never touched) was **not**
regenerated -- see §4's note on why that full ~1.3 GB, multi-minute rebuild
was unnecessary for this pilot; every exact match's actual reference-value
evidence was freshly re-extracted with the fix applied (see §3).

## 3. Matching rules implemented (and why each one matters here)

- **Exact, normalized, explicitly sourced identifiers only.** E-numbers are
  normalized via the existing `e_numbers.py` (never collapsing a letter/roman
  suffix -- `E150d` never becomes `E150`); CAS via a strict `NN...-NN-N` regex.
  No fuzzy names, no AI identity guesses, no assignment by list position.
- **Agreement required when both are present; one match does not override a
  conflict.** Reserved for when *both* CAS and E-number are explicitly present
  on both sides -- a single differing identifier with nothing else to
  corroborate a relationship is a different, unrelated substance (`no_match`),
  not a "conflict" (an earlier draft of this matcher over-applied this rule to
  single-sided mismatches and flagged *every* unrelated dossier with its own
  valid-but-different E-number as "conflicting" against every query; caught by
  `test_suffix_distinction_e150d_not_confused_with_sibling` during development
  and fixed before this pilot ran for real).
- **Missing identifiers are not evidence of equivalence.** A catalogue entry
  with only an E-number (true for every pilot substance in the tracked seed
  data -- see §5) is never treated as "confirmed" against a dossier's CAS
  alone, or vice versa; absence just means that identifier type isn't
  comparable.
- **A dossier's own ambiguous identity is never used for matching.** If one
  `REFERENCE_SUBSTANCE` record itself has more than one distinct recognized
  E-number candidate, it is excluded (`source_identity_ambiguous`), never
  guessed.
- **Ranges can never become an exact single-substance link.** An E-number
  range candidate (e.g. `E251-252`) that numerically covers a queried code is
  reported `ambiguous`, never resolved to either endpoint.
- **Multiple assessments of one substance are grouped, not duplicated or
  merged across different substances.** Dossiers whose matched identifier(s)
  resolve to the *same* underlying identity (same CAS, or -- when CAS is
  absent on the dossier side too -- the same reference-substance name) are
  reported together as one `exact_match` with every contributing dossier
  listed; dossiers resolving to *different* identities are reported
  `ambiguous`, never silently picked or merged (`TestMultipleAssessmentsAndAmbiguity`
  in `tests/unit/test_openfoodtox_matcher.py` covers both).
- **Document/subject-link resolution, not "same archive."**
  `matcher.resolve_subject_links` only attaches a reference value or endpoint
  to a specific `REFERENCE_SUBSTANCE` identity when the dossier has exactly
  one such identity, or when the item's own `manifest_links` resolve (via a
  `SUBSTANCE` document's `reference_substance_ref`) to exactly one. Measured
  across the full dataset: **333 of 11,613 dossiers (2.9%) have more than one
  `REFERENCE_SUBSTANCE`** (mostly pesticide-metabolite dossiers), so this is a
  real, not theoretical, risk the matcher now guards against; an item with no
  resolvable link, or links resolving to more than one identity, is
  quarantined under `unresolved`, never guessed. All four of this pilot's
  requested substances happen to live in single-identity dossiers, so this
  path didn't change their own results, but `dossier.py` was extended to
  carry `manifest_uuid`/`manifest_links` through to reference values and
  endpoints (previously dropped) so the general matcher works correctly on
  the 2.9% case too; `TestResolveSubjectLinks` exercises both the
  single-identity and multi-identity (via a synthetic Curdlan/glucose-shaped
  fixture modeled on a real multi-identity dossier) cases.
- **Feed-only / non-food qualifiers are preserved, never silently applied to
  food.** A dossier whose `expert_group_label`/`regulation_label` marks it as
  EFSA FEEDAP / Regulation (EC) No 1831/2003 (animal feed) is flagged
  `feed_or_livestock_context: true` on every reference value it contributes,
  and such a value is never `review_eligible` and carries an explicit
  consumer-facing caveat in the draft text. This is not a hypothetical: see
  §6, E330.

## 4. Pilot run: commands and inputs

```
cd nutriguard-backend
python3 -m scripts.openfoodtox.pilot \
  --dossiers-dir /home/vboxuser/nutriguard-data/openfoodtox/originals/2026-09-30/dossiers \
  --staging-dir  /home/vboxuser/nutriguard-data/openfoodtox/staging/v3 \
  --output-dir   /home/vboxuser/nutriguard-data/openfoodtox/pilot/v1 \
  --e-numbers E250 E150d E330 E951
```

- `--dossiers-dir`: the original, read-only, non-git-tracked IUCLID archives
  (11,613 files) -- never written to.
- `--staging-dir`: the existing `staging/v3/` catalogue (built in the prior
  session, `git diff`-unrelated to this pilot's records.py fix for *identity*
  fields -- see §2). `catalogue.jsonl` SHA-256:
  `b7797ac93f1fdf5202b4906719b0881d898e2fa8fe9fdfa808da949677fa4b50`.
- `--output-dir`: **new**, versioned (`pilot/v1/`) -- does not touch
  `staging/`, `staging/v2/`, or `staging/v3/`'s own `reports/` output from
  prior sessions.
- Extraction schema/version: `MAX_RAW_FIELDS_PER_DOCUMENT = 4000` (unchanged
  this session). Identity matching (CAS/EC/E-number) read the identity fields
  already staged in `catalogue.jsonl` above, which the §2 fix does not affect.
  Every exact match's actual reference-value/endpoint evidence in the written
  profiles was **freshly re-extracted directly from the original archive**
  using this session's corrected code (not read from the stale-for-this-purpose
  staged snapshot) -- cheap here since a pilot only touches a handful of
  matched archives, not all 11,613.
- Ran twice end-to-end and diffed every output byte-for-byte (excluding the
  run timestamp): identical both times -- deterministic and repeatable, no
  implicit network calls anywhere in the matcher or evidence-bundle code.
- Git SHA recorded in `pilot_summary.json`: `56aee7aa613b8b7cc5f98e4082981ac0d898fc92`
  (this pilot's own code/tests are committed in a follow-up commit on the
  same branch -- see the final commit this report references).
- **Coverage label** (explicit in every output file): *seed/supplied-snapshot
  coverage only -- NOT production database coverage.* No live export was
  fetched; the tracked seed data is what NutriGuard currently ships.

## 5. Results

| Query | Catalogue entry? | Result | Dossiers | Notes |
| --- | --- | --- | --- | --- |
| E250 (Sodium Nitrite) | Yes (`tracked_seed_json`, E-number only, no CAS on file) | `exact_match` | 1 | Matched by E-number alone (CAS not comparable: catalogue has none) |
| E150d (caramel colour IV) | **No** -- not in `ingredients_seed.json` or the CSV starter pack | `exact_match` (ad hoc dataset query, explicitly labeled `adhoc_query_not_in_tracked_catalogue`, **not** a NutriGuard catalogue linkage) | 1 | New finding: OpenFoodTox has "Sulphite ammonia caramel", ADI 300 mg/kg bw/day, 2011-02-03 |
| E330 (Citric acid) | Yes (`tracked_seed_csv_starter` -- shadowed by nothing in the JSON seed) | `exact_match` | 3 | 1 human/food (ANS) dossier with an unquantified "ADI not limited" + 2 **animal-feed (FEEDAP)** dossiers -- see below |
| E951 (Aspartame) | Yes (`tracked_seed_json`) | `exact_match` | 6 | ADI 40 mg/kg bw/day, reaffirmed across 5 of 6 assessments (2006-2013); 1 assessment (2011-02-03) carried no reference value at all |

Every catalogue entry in the tracked seed data has **no CAS number at all**
(`app/seed/load_seed.py` hard-codes `cas_number: None` for the JSON rows; the
CSV starter rows never populate one either) -- confirmed by
`test_no_identity_has_a_cas_number_in_current_tracked_seed_data`. This pilot's
matching is therefore necessarily **E-number-only** on the catalogue side; a
genuine CAS-vs-E-number conflict check could only be exercised with synthetic
fixtures (§7), not against the real dataset today. This is a reportable
catalogue-data limitation, not a matcher limitation.

### E330 (citric acid): a concrete feed-vs-food finding

Two of the three matched dossiers ("...for all animal species", `expert_group_label`
= "EFSA FEEDAP", `regulation_label` = "Regulation (EC) No 1831/2003 (amended)")
are **animal-feed additive assessments**, not human food ones -- their
reference values (margin-of-safety judgments for consumers/workers, and a
15,000 mg/kg feedingstuff "safe use" level for poultry/pigs/ruminants/cattle)
must never be shown as human dietary guidance. The third, human/food (EFSA ANS)
dossier's own ADI entry has **no numeric magnitude extracted at all** -- JECFA's
1974 conclusion was "the estimated ADI for man was not limited", a textual
qualifier, not a number -- so citric acid has **zero `review_eligible`
reference values** for human consumer guidance in this dataset. Every
reference value this profile holds is correctly flagged and none reaches
`review_eligible: true`.

## 6. Sample review draft (E250, abbreviated; full text in
   `pilot/v1/e250_sodium_nitrite_profile.md`, not committed -- see §9)

```
# Sodium Nitrite (E250)
Status: DRAFT -- not scientifically reviewed, not translation-reviewed, not publication-ready.

## Effects and conditions
ADI reference value: 0.1 mg/kg bw/day, population: consumers. (assessment date(s): 2017-04-05)
Chemical basis: sodium nitrite.
Source text (quoted verbatim, not paraphrased): "Remarks: The Panel concluded that an
increased methaemoglobin level, observed in human and animals, was a relevant effect
for the derivation of the ADI. Using the lowest BMDL of 9.63 mg/kg bw per day, and
applying the default UF of 100, the Panel derived an ADI of 0.1 mg sodium nitrite/kg
bw per day, corresponding to 0.07 mg nitrite ion/kg bw per day. [...]"

## Sources
- OPENFOODTOX_Re-evaluation of potassium nitrite (E 249) and sodium nitrite (E 250)
  as food additives (2017-04-05) -- doi:10.2903/j.efsa.2017.4786
```

The BG draft mirrors the same structure, certainty and conditions (see the
full file). "Intake guidance" is **deliberately omitted** from every draft in
this pilot -- the presentation spec gates it on "only reviewed applicable
guidance", and `statuses.scientific_review` is honestly `not_reviewed`
everywhere, so no numeric guidance section is shown anywhere, not even for
E250's clean single-value ADI. Direct source quotes are kept in English in
both the EN and BG drafts rather than translated, specifically to avoid
subtly altering a scientific/legal source's exact meaning; this is called out
explicitly as a known limitation (§8), not silently done.

## 7. Tests

New files: `tests/unit/test_openfoodtox_matcher.py` (15),
`tests/unit/test_openfoodtox_catalogue_snapshot.py` (9),
`tests/unit/test_openfoodtox_evidence_bundle.py` (2); 2 new cases added to
`tests/unit/test_openfoodtox_records.py` (the §2 regression pair). All use
small, synthetic, hand-built fixtures -- no bulk dataset redistribution.
Coverage includes: exact/normalized identifiers, suffix distinctions (E150d
vs. its siblings), conflicting CAS+E-number, E-number ranges, missing
identifiers (both directions), multiple assessments of one substance,
ambiguity across distinct underlying identities, subject-linkage resolution
(single- and multi-identity dossiers, including an unresolvable-link
quarantine case), non-`ok` dossier statuses being skipped, and repeatability.

```
cd nutriguard-backend
python3 -m pytest tests/unit/test_openfoodtox_matcher.py \
  tests/unit/test_openfoodtox_catalogue_snapshot.py \
  tests/unit/test_openfoodtox_evidence_bundle.py \
  tests/unit/test_openfoodtox_records.py -q
# 49 passed in 0.30s

python3 -m pytest -q
# 833 passed, 16 skipped, 3 warnings in 22.50s
```

The 16 skips are the pre-existing opt-in `tests/postgres/` suite (needs
`NUTRIGUARD_TEST_POSTGRES_URL`), unrelated to this pilot and unchanged from
every prior session's baseline. The 3 warnings are pre-existing and unrelated
(two SQLAlchemy identity-map warnings in unrelated tests, one Starlette
deprecation notice). Zero failures.

## 8. Limitations (reported honestly, not worked around)

- **No CAS numbers in the tracked catalogue today** (§5) -- every pilot match
  here is E-number-only; the "require agreement when both present" and
  "conflicting identifiers" rules are implemented and tested (§7) but could
  not be exercised against the real dataset for lack of catalogue-side CAS
  data. This is the single most actionable follow-up for making the matcher's
  strongest guarantee (two independently-agreeing identifiers) available in
  practice.
- **E150d has no tracked-catalogue entry at all.** Reported as an explicit ad
  hoc dataset query, not a NutriGuard catalogue linkage -- never silently
  invented or substituted with a sibling caramel colour.
- **Reference-value justification text is quoted verbatim, never translated**
  in either the EN or BG draft, to avoid altering a scientific/legal source's
  meaning -- a deliberate, explicit trade-off (§6), not an oversight.
  Non-quote connective text and structural fields are translated; a human
  BG-language review of the underlying facts (and of whether to commission an
  actual translation of the quotes) is still required before any publication
  decision.
- **Deduplication of repeated reference-value text is exact-string only.**
  Near-identical justification text that differs by a few words across
  assessments (observed for E330's FEEDAP dossiers: "the quality of the
  available data" vs. "the quality of available data") is shown as two
  separate entries rather than merged -- conservative and correct (never
  silently treats two different strings as certainly the same claim), but
  produces a more verbose draft than a human-reviewed near-duplicate
  clustering pass would.
- **`resolve_subject_links`'s multi-identity path is tested only against
  synthetic fixtures**, not against one of the real dataset's 333
  multi-identity dossiers end-to-end (none of the four pilot substances
  needed it) -- a reasonable next-step validation before relying on this path
  for a non-pilot substance drawn from that 2.9%.
- **E150d's own freshness was not separately checked** (§9) -- research time
  was prioritized on the three substances with an actual catalogue entry.

## 9. Reuse and freshness findings

**OpenFoodTox distribution and reuse terms** (checked 2026-10-01):
- Official EFSA page: <https://www.efsa.europa.eu/en/data-report/chemical-hazards-database-openfoodtox>
- Archived dataset release: Zenodo, DOI [10.5281/zenodo.8120114](https://zenodo.org/record/8120114)
  ("OpenFoodTox: EFSA's chemical hazards database").
- License: **Creative Commons Attribution 4.0 International (CC BY 4.0)** --
  reuse is authorized provided EFSA is acknowledged as the source.
- **Third-party exception, explicitly not cleared by this check**: CC BY 4.0
  covers OpenFoodTox's own structured data and summaries. It does **not**, by
  itself, grant reuse rights to the full text of the underlying third-party
  scientific literature cited within individual dossiers (journal articles,
  etc.) -- dataset access must not be read as clearing that separately
  copyrighted material, per the task's own instruction. Not further
  investigated in this session.
- This clears using the *extracted structured data* (identifiers, reference
  values, endpoint summaries) with attribution; it does not by itself make any
  drafted consumer text "publication-ready" -- that remains gated on the
  scientific/translation review statuses in every profile (all `not_reviewed`
  here).

**Per-substance freshness** (official assessment references checked for a
superseding opinion; scope and limits stated plainly rather than claiming
exhaustive currency):

- **E250 (sodium nitrite)**: the dataset's one dossier (2017-04-05,
  doi:10.2903/j.efsa.2017.4786) is EFSA's most recent *toxicological*
  re-evaluation found in this session -- no newer ANS scientific opinion
  located. Commission Regulation (EU) 2023/2108 later revised *permitted
  maximum use levels* in specific food categories (effective 2025-10-09 for
  meat/fishery products) -- a regulatory/use-level change, not a new ADI
  derivation; out of this profile's scope but worth flagging as a related,
  separate regulatory development.
- **E330 (citric acid)**: a genuine gap. The EFSA FAF Panel published a
  dedicated re-evaluation covering citric acid on 2020-03-11
  (doi:10.2903/j.efsa.2020.6032, EFSA Journal 18(3):6032) that **postdates
  every citric-acid dossier in this OpenFoodTox dataset** (newest here:
  2016-09-28) and is **not present in the dataset at all**. This is exactly
  the kind of "dataset is not current for all substances" gap the task asked
  this check to surface, not assume away from archive/export dates alone.
- **E951 (aspartame)**: the dataset's newest assessment is the 2013-11-28
  re-evaluation (doi:10.2903/j.efsa.2013.3496, extracted directly from the
  dossier's own `LinkToPersistentIdentifier` field -- not looked up
  separately). Following IARC's 2023-07-14 classification of aspartame as
  Group 2B and JECFA's same-day reaffirmation of the 40 mg/kg bw/day ADI,
  multiple secondary sources (Food Safety Authority of Ireland, EUFIC,
  Keller & Heckman's regulatory blog) report that EFSA stated it saw no basis
  to revise its 2013 conclusions. This session did **not** locate a dedicated
  EFSA Journal DOI for a formal 2023 superseding opinion -- reported as a
  checked-but-unresolved precise citation, not claimed as either "confirmed
  current" or "superseded."
- **E150d (sulphite ammonia caramel)**: not checked for a superseding opinion
  in this session (§8) -- unresolved freshness, explicitly, rather than
  silently assumed current.

## 10. Integration blockers (explicit, none resolved here)

1. No CAS numbers in the tracked ingredient catalogue (§8) -- blocks the
   matcher's strongest (dual-identifier) confirmation path in practice.
2. Every profile's `scientific_review` and `translation_review` statuses are
   `not_reviewed` by design -- a human/scientific reviewer must review each
   quoted claim and (if a BG translation of the quotes themselves is wanted)
   commission one, before any of this reaches a consumer-facing surface.
3. `reuse_clearance` is `pending_external_check` -- the CC BY 4.0 license
   covers the structured data; a decision on attribution text/placement in
   the product, and on the third-party literature exception above, is still
   needed from a product/legal owner.
4. `source_freshness` is `checked_limited_scope` -- E330 in particular has a
   known, located, not-yet-reviewed-for-incorporation newer EFSA opinion
   (§9); E951's 2023 position is reported via secondary sources only.
5. No code in this pilot writes to the live database, changes any API
   contract, or is wired into any application code path -- integrating any of
   this (even just the corrected `records.py` extraction fix) into a
   production catalogue update is a separate, explicitly-scoped future task.

## Files

New: `scripts/openfoodtox/{catalogue_snapshot,matcher,evidence_bundle,pilot}.py`,
`tests/unit/test_openfoodtox_{matcher,catalogue_snapshot,evidence_bundle}.py`,
this file.
Modified: `scripts/openfoodtox/records.py` (§2 fix), `scripts/openfoodtox/dossier.py`
(carries `manifest_uuid`/`manifest_links` through to reference values and
endpoints), `tests/unit/test_openfoodtox_records.py` (+2 regression tests).
Generated, not committed (bulk/derived, per task scope -- kept outside Git):
`/home/vboxuser/nutriguard-data/openfoodtox/pilot/v1/` (`pilot_summary.json`
plus 4 `*_profile.{json,md}` pairs).
