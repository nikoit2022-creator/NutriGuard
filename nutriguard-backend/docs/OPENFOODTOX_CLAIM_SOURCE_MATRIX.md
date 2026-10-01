# OpenFoodTox pilot -- editorial claim/source matrix

Per `docs/OPENFOODTOX_FINAL_PROFILE_REVIEW_TASK.md` item 3: every editorial
claim in `scripts/openfoodtox/editorial_content.py`, with a stable ID, its
source kind, and its exact citation, reviewable through Git (not solely in
an uncommitted VM JSON). Generated directly from `editorial_content.py`
(not hand-transcribed) on 2026-10-01; EN text truncated for table width --
full EN/BG text and the complete, untruncated source citation for every row
are in the module itself, next to the field name given below (e.g. row
`E250-01`'s `identity` field is `EDITORIAL_CONTENT["E250"].identity`).

**Access date for every `external_primary_source`/`openfoodtox_dossier` row
below: 2026-10-01**, unless the source string states a different date.
**`tracked_seed_csv`/`tracked_seed_json` rows** point to NutriGuard's own
already-curated, git-tracked seed data -- an internal content origin, not
independent scientific evidence; never treated as `external_primary_source`
nor as itself establishing a scientific claim (task item 3's own
distinction). **`review_status`**: `not_reviewed` for every row -- an AI
source check is not independent scientific or translation approval;
`internal_only_never_shown_to_consumer` rows never appear in `draft_en`/
`draft_bg` at all (see `evidence_bundle.py`'s `editorial_operator_only_notes`
and the single numeric-gating policy in
`docs/OPENFOODTOX_PILOT_REPORT.md` §12).

| ID | Field | EN claim (truncated -- full text in editorial_content.py) | Source kind | Source (DOI/URL/file) | Review status |
| --- | --- | --- | --- | --- | --- |
| E250-01 | identity | Sodium nitrite is an inorganic salt of nitrous acid (NaNO2), produced industrially; it is… | external_primary_source | Commission Regulation (EU) No 231/2012 of 9 March 2012 laying down sp… | not_reviewed |
| E250-02 | purpose | Used as a curing agent and preservative in meat products: it inhibits the germination and… | tracked_seed_csv | app/seed/e_additives_curated_starter.csv, row E250 (functional_class/… | not_reviewed |
| E250-03 | effects:assessment_conclusion | EFSA's 2017 re-evaluation identified an increase in blood methaemoglobin level as the rel… | openfoodtox_dossier | OpenFoodTox dossier, doi:10.2903/j.efsa.2017.4786, internal evidence … | not_reviewed |
| E250-04 | effects:human | Epidemiological studies linking processed/cured meat consumption to health outcomes are n… | tracked_seed_csv | app/seed/e_additives_curated_starter.csv, row E250 (human_evidence) | not_reviewed |
| E250-05 | effects:assessment_conclusion | The Panel also noted that nitrite can participate in the formation of endogenous N-nitros… | tracked_seed_csv | app/seed/e_additives_curated_starter.csv, row E250 (potential_effects) | not_reviewed |
| E150d-06 | identity | Sulphite ammonia caramel (caramel colour IV) is one of four EU-defined caramel colour cla… | external_primary_source | Commission Regulation (EU) No 231/2012 of 9 March 2012 laying down sp… | not_reviewed |
| E150d-07 | purpose | Used as a brown food colourant, e.g. in cola-type soft drinks, spirits, sauces and baked … | external_primary_source | General food-colour usage, cross-checked against the 2011 EFSA opinio… | not_reviewed |
| E150d-08 | effects:assessment_conclusion | EFSA's 2011 re-evaluation set a GROUP Acceptable Daily Intake that applies to the COMBINE… | external_primary_source | EFSA ANS Panel, Scientific Opinion on the re-evaluation of caramel co… | not_reviewed |
| E150d-09 | effects:assessment_conclusion | A 2012 EFSA follow-up refined the exposure estimate using updated use-level data: actual … | external_primary_source | EFSA, "Refined exposure assessment for caramel colours (E150a, c, d)"… | not_reviewed |
| E150d-10 | operator_only_note | The group ADI is 300 mg/kg bw/day (all four caramel colours combined); E150c's own additi… | openfoodtox_dossier | OpenFoodTox dossier, doi:10.2903/j.efsa.2011.2004, internal evidence … | internal_only_never_shown_to_consumer |
| E330-11 | identity | Citric acid occurs naturally in citrus fruits. Today it is produced industrially at large… | external_primary_source | Commission Regulation (EU) No 231/2012 of 9 March 2012 laying down sp… | not_reviewed |
| E330-12 | purpose | Used as an acidity regulator and sequestrant: it adjusts/stabilises pH (acidification) an… | tracked_seed_csv | app/seed/e_additives_curated_starter.csv, row E330 (functional_class/… | not_reviewed |
| E330-13 | effects:assessment_conclusion | JECFA concluded in 1974 that citric acid and its calcium/potassium/sodium salts did not c… | openfoodtox_dossier | OpenFoodTox dossier, doi:10.2903/j.efsa.2016.4599, internal evidence … | not_reviewed |
| E330-14 | operator_only_note | Separately, EFSA's FEEDAP Panel has assessed citric acid's use as an animal-feed additive… | openfoodtox_dossier | OpenFoodTox FEEDAP dossiers, doi:10.2903/j.efsa.2015.4010 and doi:10.… | internal_only_never_shown_to_consumer |
| E951-15 | identity | Aspartame is a synthetic dipeptide sweetener (L-aspartyl-L-phenylalanine methyl ester), r… | tracked_seed_json | app/seed/ingredients_seed.json, entry e951_aspartame (scientificName/… | not_reviewed |
| E951-16 | purpose | Used as a high-intensity, non-nutritive sweetener in diet beverages, sugar-free confectio… | tracked_seed_json | app/seed/ingredients_seed.json, entry e951_aspartame (purposeInFood) | not_reviewed |
| E951-17 | effects:human | Aspartame is broken down in the gut into its constituent parts -- phenylalanine, aspartic… | tracked_seed_csv | app/seed/e_additives_curated_starter.csv, row E951 (digestion_absorpt… | not_reviewed |
| E951-18 | effects:assessment_conclusion | Across five separate EFSA assessments between 2006 and 2013, EFSA consistently concluded … | openfoodtox_dossier | OpenFoodTox aspartame dossiers, 2006-2013 (five assessments), interna… | not_reviewed |
| E951-19 | effects:assessment_conclusion | In 2023, IARC classified aspartame as 'possibly carcinogenic to humans' (Group 2B, based … | external_primary_source | IARC/WHO press release, 2023-07-14; EFSA plain-language summary, "Re-… | not_reviewed |
| E951-20 | population_exception | The ADI of 40 mg/kg bw/day is NOT applicable to people with phenylketonuria (PKU): they r… | openfoodtox_dossier | OpenFoodTox dossier, doi:10.2903/j.efsa.2013.3496, internal evidence … | not_reviewed |
| E951-21 | editorial_chemical_basis | aspartame itself | external_primary_source | Direct reading of all five OpenFoodTox aspartame dossiers' justificat… | not_reviewed |

## Audit notes (item 3)

- **Vague citation found and fixed**: `E330-01`'s (now `E330-11`) identity
  claim previously cited "Commission Regulation (EU) No 231/2012 ...
  Production method cross-checked against standard food-chemistry
  references" -- the second clause named no actual source. Replaced with
  the Regulation's own Annex entry for E330 CITRIC ACID, quoting its
  "Definition" text directly ("obtained by fermentation of carbohydrate
  solutions ... with the mould Aspergillus niger") so the claim restates a
  specific, named passage rather than an unnamed "standard reference."
- **Regulation citations checked against what they actually define**: Reg.
  (EU) No 231/2012's own per-additive Annex entries (Synonyms/Definition/
  Einecs/Chemical formula, etc.) are the basis for E250/E150d/E330's
  `identity` claims (chemical nature, and for E150a-d specifically the
  sulphite/ammonium manufacturing-process classification that the
  Regulation itself uses to distinguish the four classes) -- not cited for
  any effect/intake claim, which always traces to an OpenFoodTox dossier or
  a named EFSA opinion/press release/plain-language summary instead.
- **`tracked_seed_csv`/`tracked_seed_json` rows are never the sole basis for
  a safety/effect conclusion** -- they supply identity/purpose/mechanism
  background (digestion, metabolism, functional class) only; every
  effects-section *conclusion* about safety/risk traces to an
  `openfoodtox_dossier` or `external_primary_source` row instead.
- **Uncertainty and population limitations preserved**: E150d's own numeric
  claim (`E150d-10`) stays `internal_only_never_shown_to_consumer`,
  gated identically to a structured reference value (see
  `docs/OPENFOODTOX_PILOT_REPORT.md` §12); E951's PKU exception (`E951-20`)
  and its editorially-confirmed basis (`E951-21`, with its own full
  rationale for why the automated check could not resolve it) are both
  carried with their original caveats intact, not stripped for brevity.
- This matrix does **not** establish scientific or translation review --
  every row's `review_status` is `not_reviewed`, truthfully, regardless of
  how carefully its citation was checked in this session.
