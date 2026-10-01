# OpenFoodTox / IUCLID dataset audit

Offline inventory, integrity check, structural audit, and staging
catalogue extraction for the transferred OpenFoodTox IUCLID dossier
archives, done in preparation for a **future** NutriGuard integration.
This audit is read-only against the source data: nothing here imports
into the live database, changes API behavior or the Health Score,
restarts containers, or deploys anything. See §8 for what a real
integration would still need to do.

- Extraction tooling: `nutriguard-backend/scripts/openfoodtox/`
- Tests: `nutriguard-backend/tests/unit/test_openfoodtox_*.py`
- Generated outputs (not in git): `/home/vboxuser/nutriguard-data/openfoodtox/staging/` and `/reports/`
- Source data (not in git, unchanged by this audit): `/home/vboxuser/nutriguard-data/openfoodtox/originals/2026-09-30/dossiers/`

## 1. Source data: what it is and where it came from

Each `*.i6z` file is an **IUCLID 6 dossier export** (a zip archive
containing a `manifest.xml`, IUCLID presentation stylesheets (`*.xsl`,
`*.css`), and one `*.i6d` XML file per contained "document" — a
substance record, a reference-substance identity record, a legal
entity, a literature citation, an endpoint summary, an endpoint study
record, or a reference-value summary). Every dossier's own
`manifest.xml` records, verbatim:

- `submission-type`: `EFSA_CHEMICALS_DATABASE_SUB` (all 11,613 files)
- `archive-type`: `DOSSIER_DATA` (all 11,613 files)
- `application`: `IUCLID6 (8.13.3, build of 26/11/2024 14:54)`
- `legislations-info`: `domain 9.0` and `efsa 2.0` (all files), `oecd 9.0`
  (11,554 files), `ppp 5.0` (11,327 files), `core 9.0` (839 files)

This confirms the dataset is EFSA's **OpenFoodTox** database, exported
via IUCLID 6, under the EFSA Chemicals Database submission type. **No
license or reuse-terms file was included in this transfer** — the
transferred directory contains only the 11,613 `.i6z` archives, no
accompanying README/LICENSE/terms file. Confirming the applicable
reuse terms for OpenFoodTox would require consulting EFSA's own
published data-reuse policy directly; that is out of scope for this
offline, no-external-enrichment audit and is called out as an open
item in §8.

### 1.1 A dossier is not a substance

**One `.i6z` archive is one EFSA publication (a risk-assessment
conclusion, a re-evaluation opinion, ...), not one unique substance.**
A single dossier's `manifest.xml` typically lists a `DOSSIER` record,
one or more `SUBSTANCE`/`REFERENCE_SUBSTANCE` identity records, one or
more `LEGAL_ENTITY` records, `LITERATURE` citations, and several
`ENDPOINT_SUMMARY`/`ENDPOINT_STUDY_RECORD`/`FLEXIBLE_SUMMARY` findings,
all cross-referenced by UUID pairs (`document-uuid/dossier-uuid`). The
same substance (same CAS number) legitimately appears across multiple
separate dossiers when it has been assessed in more than one EFSA
publication (2,567 CAS numbers do this in the transferred set — see
§6). Conversely, a dossier's endpoint records do not always share the
exact identity of that dossier's headline substance: one inspected
dossier (`0001bbd8-...i6z`, "Conclusion on the peer review of the
pesticide risk assessment of the active substance prochloraz") carries
endpoint findings filed against a **metabolite** of prochloraz
(`1-Propyl-1-[2-(2,4,6 trichlorophenoxy)ethyl]urea`, no CAS/EC
populated) rather than prochloraz itself — the discussion text
narrates the parent active substance's regulatory review, but the
`SUBSTANCE`/`REFERENCE_SUBSTANCE` records in that dossier are the
metabolite's. The extractor preserves this distinction (metabolite
records are cataloged under their own identity, not folded into a
parent substance) rather than assuming a section title or dossier
title tells you which substance a finding belongs to.

### 1.2 A section title is not a positive finding

`ENDPOINT_SUMMARY`/`ENDPOINT_STUDY_RECORD` "key information" text is
free-form EFSA panel wording, e.g. `"Genotoxic: Negative"` or
`"Mutagenic: Negative; Carcinogenic: Negative"` — the presence of a
`GeneticToxicity` or `Carcinogenicity_EU_PPP` section does **not** by
itself mean a positive finding; the extractor captures this text
verbatim and never infers a positive/negative/safe verdict from a
section's mere existence.

## 2. Dataset structure survey (full-manifest census)

A full census of `(documentType, documentSubType)` across all 11,613
manifests (not a sample) found 89 distinct subtypes. The dominant
ones:

| type | subtype | count |
|---|---|---|
| LEGAL_ENTITY | — | 23,226 |
| FLEXIBLE_SUMMARY | ToxRefValues | 21,572 |
| ENDPOINT_STUDY_RECORD | BasicToxicokinetics | 18,994 |
| REFERENCE_SUBSTANCE | — | 15,705 |
| SUBSTANCE | — | 14,791 |
| LITERATURE | — | 14,343 |
| ENDPOINT_SUMMARY | Carcinogenicity_EU_PPP | 13,408 |
| ENDPOINT_SUMMARY | GeneticToxicity | 13,406 |
| DOSSIER | EFSA_CHEMICALS_DATABASE | 11,613 |

...down through dozens of low-frequency ecotoxicology/physicochemical
subtypes (full counts are reproducible via the `catalogue` subcommand;
see `reports/catalogue_summary.md`). This full census, done *before*
writing the extractor, is what the evidence-domain classification in
`scripts/openfoodtox/domains.py` is built from (see §5.3).

## 3. Commands

All commands are run from `nutriguard-backend/` and only ever read
from `--dossiers-dir` and write to `--staging-dir`/`--reports-dir`
(both outside git). Point `STAGING`/`REPORTS` at a **new versioned
subfolder** for each rerun that changes extraction logic (e.g.
`staging/v2/`, `reports/v2/`) rather than overwriting the previous
output in place, so prior results stay recoverable for comparison —
this is why `staging/v2/`/`reports/v2/` exist alongside the original
`staging/`/`reports/` from 2026-09-30 (see §8). Run in this order:

```bash
cd nutriguard-backend

DOSSIERS=/home/vboxuser/nutriguard-data/openfoodtox/originals/2026-09-30/dossiers
STAGING=/home/vboxuser/nutriguard-data/openfoodtox/staging/v2
REPORTS=/home/vboxuser/nutriguard-data/openfoodtox/reports/v2

# 1. Hash + integrity-check every archive
python3 -m scripts.openfoodtox.extract inventory \
  --dossiers-dir "$DOSSIERS" --staging-dir "$STAGING" --reports-dir "$REPORTS"

# 2. Harvest the code->label tables shipped inside the dossiers' own .xsl stylesheets
python3 -m scripts.openfoodtox.extract codebook \
  --dossiers-dir "$DOSSIERS" --staging-dir "$STAGING"

# 3. Extract the full searchable JSONL catalogue (uses the codebook if present)
python3 -m scripts.openfoodtox.extract catalogue \
  --dossiers-dir "$DOSSIERS" --staging-dir "$STAGING" --reports-dir "$REPORTS"

# 4. Identity/duplication audit, built from the catalogue
python3 -m scripts.openfoodtox.extract identity-audit \
  --staging-dir "$STAGING" --reports-dir "$REPORTS"

# 5. Sodium nitrite / E250 example profile, built from the catalogue
python3 -m scripts.openfoodtox.extract e250 \
  --staging-dir "$STAGING" --reports-dir "$REPORTS"
```

Tests (no dataset required — all fixtures are small synthetic
archives built in-memory):

```bash
cd nutriguard-backend
python3 -m pytest tests/unit/test_openfoodtox_safe_io.py \
  tests/unit/test_openfoodtox_records.py \
  tests/unit/test_openfoodtox_dossier.py \
  tests/unit/test_openfoodtox_codebook.py \
  tests/unit/test_openfoodtox_domains.py \
  tests/unit/test_openfoodtox_e_numbers.py \
  tests/unit/test_openfoodtox_chemical_basis.py -v

# or the whole backend suite, to confirm no regression:
python3 -m pytest -q
```

## 4. Safety model (archive/XML handling)

Implemented in `scripts/openfoodtox/safe_io.py`, applied uniformly to
every archive and every XML payload read from it:

1. **No external entities / no entity expansion.** Any XML payload
   containing a `<!DOCTYPE` or `<!ENTITY` declaration is refused
   outright before parsing (legitimate IUCLID `manifest.xml`/`*.i6d`
   files never declare one). This defeats XXE and "billion laughs" at
   the source, independent of the parser's own defaults.
2. **Bounded processing.** Per-entry (64 MB) and per-archive (512 MB)
   uncompressed-size caps checked from the zip central directory
   before decompression, plus a compression-ratio check
   (>300x on an entry over 1 MB) to catch zip-bomb-style entries.
3. **No path traversal.** Every member name is validated (no absolute
   path, no `..` segment) before use; archives are never extracted to
   disk — every read goes through an in-memory, size-bounded
   `zf.open(...).read(max_bytes + 1)`.
4. **No stylesheet execution.** The `.xsl` files shipped in each
   dossier are never loaded into an XSLT engine. The only place they
   are read at all is the codebook harvester (§5.2), which
   regex-scans their bytes as plain text and never evaluates them.

Tested in `tests/unit/test_openfoodtox_safe_io.py` and
`test_openfoodtox_dossier.py` (path traversal, oversized entries,
corrupt/truncated zip, DOCTYPE/ENTITY-bearing XML, a dangling
manifest reference).

## 5. Extraction design

### 5.1 Two-layer parsing (`scripts/openfoodtox/records.py`)

Every `*.i6d` document is parsed twice:

1. **`raw_fields`** — a generic, bounded walk of every leaf element in
   the document's content payload, each tagged with its full path
   (e.g.
   `ENDPOINT_STUDY_RECORD.RepeatedDoseToxicityOther/ResultsAndDiscussion/
   EffectLevels/Efflevel/entry/EffectLevel/lowerValue`) and, where
   IUCLID puts a `i6:uuid` on the repeatable `<entry>` wrapper (e.g.
   grouping multiple `Efflevel` entries), the enclosing `entry_uuid` is
   propagated down to every leaf beneath it. The walk always runs to
   completion first and is only *then* capped at a 4,000-leaf coverage
   target (measured with a large safety margin against this dataset's
   true maximum — see §8.2 for how that number was chosen and how
   truncation, on the rare document that exceeds it, is detected and
   quarantined rather than silently included or silently dropped).
   This captures every field actually present with its exact source
   path, even for the ~80 endpoint subtypes this module does not
   hand-model individually.
2. **Derived convenience fields**, layered on top for the record types
   this audit specifically targets: substance/reference-substance
   identity, legal entities, literature citations, dossier-level
   metadata, `FLEXIBLE_SUMMARY.ToxRefValues` reference values
   (ADI/ARfD/AOEL/AAOEL/other), and a subtype-agnostic endpoint view
   (`KeyInformation`, `Discussion`, species/sex/duration,
   `EffectLevels` entries, plus a generic
   `additional_results_text` catch-all for free-text result fields
   outside those patterns — added specifically because
   `BasicToxicokinetics`, the single largest study-record subtype
   (18,994 occurrences), carries most of its content there).

Every derived and raw field carries a `source` provenance dict
(`archive`, `entry`, `document_key`) so any catalogue row traces back
to the exact archive member it came from.

### 5.2 Code decoding from the dataset's own stylesheets (`codebook.py`)

Each dossier ships IUCLID presentation stylesheets that render coded
fields as text, e.g.:

```xslt
<xsl:when test="./unitCode = '2085'"> mg/kg bw/day</xsl:when>
<xsl:when test="./i6:value = '2302'">rat</xsl:when>
```

This is **authoritative, in-dataset reference data** — decoding a code
by reading this table is not external enrichment and not a guess. Only
72 distinct stylesheet filenames exist across all 11,613 archives, and
a full harvest confirmed every same-named stylesheet is byte-identical
across every archive that carries it (sampled 400 archives with zero
variance; the harvester still checks every archive's copy against the
first-seen hash and would flag drift if it existed). The harvested
codebook: **428 unit codes** (a single global vocabulary — cross-
checked as consistent across every stylesheet that renders a unit) and
**16,515 value codes across 71 stylesheets** (scoped **per stylesheet
filename**, not merged globally — see the limitation in §9), with
**zero label conflicts or cross-archive stylesheet variants** detected.

One additional pattern needs no codebook at all: several fields carry
`<value>1342</value>` alongside a sibling `<other>free text</other>` —
this is IUCLID's own "the label is the free-text sibling" convention,
and the extractor prefers that sibling text over any codebook lookup
whenever both are present.

**Codes with no shipped label, and codes outside the shipped
stylesheets' vocabulary, are left as raw numeric codes — never
guessed.** (`test_unresolved_code_stays_unresolved_not_guessed`
pins this.)

### 5.3 Evidence-domain classification (`domains.py`)

A **schema-level heuristic**, not per-record content verification: it
classifies a record by what its `documentSubType` declares itself to
be (built from the full census in §2), not by re-deriving the
classification from the species/route actually recorded inside each
individual study. Domains: `human_health`, `environmental`,
`livestock_animal_health` (farm-animal, kept separate from both human
and wildlife), `physicochemical`, `supporting` (composition/use/
metabolite/test-material context, not itself a finding),
`assessment_summary` (dossier/literature/reference-value containers),
and `identity`. Any subtype not in the table is `unclassified`, never
guessed into a bucket. See §9 for the corresponding limitation.

### 5.4 Never converted, never inferred

Per the audit's explicit constraints, the extractor:

- Never converts a NOAEL/LOAEL/BMD/BMDL value into an ADI or any other
  unit/endpoint (`unit_code`/`unit_label` are reported exactly as
  found; ADI-type reference values are extracted as their own separate
  records, not derived from a POD by this code).
- Never infers regulatory-approval status from the presence of an
  EFSA opinion, a reference value, or a "negative" finding.
- Never treats a missing field (no ARfD in a given dossier, no
  CAS/EC on a metabolite's `REFERENCE_SUBSTANCE` record, ...) as
  evidence of safety, or fabricates a value for it — missing means
  `None` in the catalogue, not omitted-and-silently-assumed.
- Never merges two substances because their names look similar, and
  never auto-resolves a same-name/different-CAS or same-CAS/
  different-name conflict (see §6) — both are surfaced for manual
  review only.

## 6. Results: inventory, integrity, catalogue, identity audit

All figures below are from the first full run against the transferred
set on 2026-09-30 (reproducible via the commands in §3;
machine-readable versions are in `reports/*.json`). **§8 documents
three fixes applied on 2026-10-01 and the corresponding rerun under
`staging/v2/`/`reports/v2/`** — the figures below are superseded where
§8 says so (E-number recognition counts, the "usable" qualifier on the
human-health endpoint count, and the new completeness counters); every
other figure here was re-confirmed unchanged by that rerun.

**Inventory & integrity** (`reports/inventory_summary.json`,
`inventory_report.md`):

- Files found: **11,613** (expected 11,613 — **exact match, no
  discrepancy**)
- Total bytes: **1,100,741,293** (expected 1,100,741,293 — **exact
  match, no discrepancy**)
- Archive status: **11,613/11,613 `ok`** (opened as a valid zip,
  passed `zf.testzip()` CRC validation, `manifest.xml` present) — zero
  corrupt, zero unsupported, zero unsafe (path traversal / oversized /
  zip-bomb-ratio) archives found
- Duplicate archive content (identical sha256 across different
  filenames): **0 groups**

  > **Scope note on "integrity":** the sha256 manifest
  > (`staging/inventory_manifest.jsonl`) is computed from the files
  > under `--dossiers-dir` on this machine. This establishes a **local
  > integrity baseline** (every file is well-formed, uncorrupted, and
  > internally consistent) — it does **not** by itself prove
  > end-to-end transfer fidelity against hashes computed at the
  > original source, which were not available to compare against in
  > this task.

**Catalogue extraction** (`reports/catalogue_summary.{md,json}`,
`staging/catalogue.jsonl`, ~985 MB):

- Archives processed: **11,613**, all with parse status `ok` (zero
  parse failures)
- Documents extracted, by type: `ENDPOINT_STUDY_RECORD` 87,954;
  `ENDPOINT_SUMMARY` 29,139; `FLEXIBLE_SUMMARY` 23,465; `LEGAL_ENTITY`
  23,226; `REFERENCE_SUBSTANCE` 15,705; `SUBSTANCE` 14,791;
  `LITERATURE` 14,343; `DOSSIER` 11,613; `TEST_MATERIAL_INFORMATION`
  866; `FLEXIBLE_RECORD` 275 (221,377 documents total — corrected from an
  arithmetic error in the original version of this document, which summed
  the same per-type counts to 232,377)
- By evidence domain: `human_health` 57,651; `identity` 53,722;
  `assessment_summary` 47,528; `physicochemical` 38,548;
  `environmental` 20,594; `supporting` 3,034; `livestock_animal_health`
  300
- Dossiers with at least one human-health endpoint record:
  **11,394 / 11,613** (confirmed, after the §8.2 fix, that all 11,394 are
  *usable* — zero were truncated)
- Unique CAS numbers observed: **4,352**; unique EC numbers: **3,357**
- Reference substances with a recognized E-number-shaped synonym:
  **619 / 15,705** after the §8.1 fix (495 before it — 124 previously
  missed by the original buggy recognizer)

**Identity & duplication audit** (`reports/identity_audit.{md,json}`):

- Dossiers with parse errors/corrupt archives: **0**
- Dossiers with no CAS or EC identifier on any `REFERENCE_SUBSTANCE`
  record: **1,792** (expected — metabolites, UVCBs, and other
  substances without a registered CAS/EC in this dataset; see §1.1)
- CAS numbers appearing in more than one dossier (re-assessed in more
  than one EFSA publication — expected, not an error): **2,567**
- CAS numbers mapped to more than one distinct substance name (needs
  manual review): **0**
- Substance names mapped to more than one distinct CAS number (needs
  manual review): **0**

No automatic merging was performed anywhere in this pipeline; the
audit found zero ambiguous name/CAS conflicts to flag in this
particular transferred set, but the mechanism exists and is tested
(`test_multiple_occurrences_of_same_container_flags_warning` and the
identity-audit's own conflict tables) for if/when one appears.

## 7. Sodium nitrite / E250

**Match: 1 dossier**, `0f80bae5-4ff4-4aaf-81e1-724e74289211.i6z`,
found by **exact CAS (`7632-00-0`) and EC (`231-555-9`) match** on its
`REFERENCE_SUBSTANCE` record's `Inventory` block, cross-checked
against that substance's own `Synonyms` entry containing the literal
string `"E 250"` — not a name-similarity guess. Full source-linked
profile (current version, with the §8 fixes applied):
`reports/v2/e250_sodium_nitrite_profile.{md,json}`.

Summary of what the dataset actually contains for it (raw evidence,
reproduced from the profile — see the file itself for the complete,
provenance-tagged detail and the explicit raw-vs-explanatory-summary
split):

- **Identity**: Sodium nitrite, CAS `7632-00-0`, EC `231-555-9`,
  synonym `E 250`, formula `NNaO2`.
- **Dossier**: "Re-evaluation of potassium nitrite (E 249) and sodium
  nitrite (E 250) as food additives", EFSA ANS Panel, 2017,
  `doi:10.2903/j.efsa.2017.4786`, adoption date 2017-04-05,
  publication date 2017-06-15, Regulation (EC) No 257/2010.
- **Reference value**: ADI = 0.1 mg/kg bw/day on a **sodium nitrite**
  chemical basis (population code 8521, decoded via the shipped
  codebook as "consumers"; overall uncertainty factor 100), resolved
  and externally verified in §8.3 — the same justification text also
  states a corresponding 0.07 mg nitrite ion/kg bw/day figure, kept as
  a separate, clearly labeled value rather than merged with the
  primary one. Derived from a BMDL of 9.63 mg/kg bw/day with a default
  UF of 100, per the Panel's own justification text, quoted verbatim.
- **Human-health endpoints present**: a repeated-dose (sub-chronic,
  other route) toxicity study record carrying that same BMDL of 9.63
  mg/kg bw/day (methaemoglobin increase, NTP 2001 study, rat,
  male/female, 14 weeks) as its critical effect level; a genetic
  toxicity summary ("Genotoxic: Negative"); a carcinogenicity summary
  ("Mutagenic: Negative; Carcinogenic: Negative").
- **Not present in this dataset**: no ARfD/AOEL/AAOEL record for this
  substance (only the ADI), and no environmental/livestock endpoint
  records under this identity in this dossier. Absence is reported as
  absence — not converted, not inferred, not treated as a safety
  claim.

## 8. Follow-up review and fixes (2026-10-01)

A follow-up review (`docs/OPENFOODTOX_REVIEW_TASK.md`, reviewed
baseline `8dd62355281c147bc84c9266341a47b60a509846`) found three real
issues in the first version of this audit. All three are fixed,
tested, and reflected in a regenerated catalogue/audit/profile under
`staging/v2/` and `reports/v2/` (the original `staging/`/`reports/`
from 2026-09-30 are preserved unchanged for comparison).

### 8.1 E-number suffixes were silently dropped (confirmed defect, fixed)

`derive_reference_substance` only recognized a synonym as an E-number
when the text after "E" was entirely numeric, so letter-suffixed
identifiers (`E150d`, distinct from E150a/b/c) and roman-numeral-
qualified ones (`E 101(i)`) never reached the derived `e_number`
field — only the plain `E 250`-style form worked. Fixed in the new
`scripts/openfoodtox/e_numbers.py`, built from a full survey of every
E-number-shaped synonym string in the dataset (276 distinct strings),
covering: plain 2-5 digit codes (`E 100`, `E765` with no space),
single-letter suffixes (`E150a`..`E150d`, `E472a`..`f`), roman-numeral
qualifiers (`E 101(i)`, `E 954(iv)`), letter+roman combinations
(`E160a(i)`), a trailing free-text qualifier (`E 161(i) (feed)`), and
an explicit two-code range (`E 251-252`, kept as a range, never
reduced to one endpoint). Confirmed rejected: E/Z stereochemistry
descriptors in IUPAC names (`E-4-Undecenal`, `E-5-Decen-1-ol`, ...),
which look superficially similar but are not food-additive codes.

The derived field is now `e_numbers` (plural, structured: a list of
candidates plus a `conflict` flag), not a single scalar — a record
with more than one distinct recognized candidate is flagged, never
silently resolved to the first one found (not observed for real: zero
of 15,705 REFERENCE_SUBSTANCE records have more than one, but the
mechanism is tested). **Measured coverage change**: re-running
recognition across the full dataset moved recognized-E-number records
from 495 (old logic) to 619 (new logic) — 124 previously-missed
records, zero newly-introduced conflicts. See
`reports/v2/catalogue_summary.md` ("E-number recognition") for the
counts and `tests/unit/test_openfoodtox_e_numbers.py` for the
regression suite (numeric, suffixed, spaced, lowercase, range,
qualifier, conflicting, and malformed/look-alike cases).

### 8.2 Bounded extraction vs. completeness (confirmed mismatch, fixed)

Two separate problems existed in the original 400-leaf cap
(`MAX_RAW_FIELDS_PER_DOCUMENT`):

1. **The cap was too low for some real documents.** A full, unbounded
   leaf-count survey of all 221,377 documents in the dataset found a
   true maximum of **1,405** informative leaves in a single document,
   and **467 documents (0.2%)** exceeded the old 400-leaf cap — all of
   them `ENDPOINT_STUDY_RECORD` environmental/physicochemical subtypes
   (`BiodegradationInSoil`, `AdsorptionDesorption`,
   `BiodegradationInWaterAndSedimentSimulationTests`, `Hydrolysis`,
   `PhotoTransformationInSoil`). **None were human-health, identity
   (`SUBSTANCE`/`REFERENCE_SUBSTANCE`), or reference-value
   (`ToxRefValues`, max 32 leaves) documents** — so this never affected
   the E250 profile or any human-health-evidence count, but it was a
   real, silent gap for the affected environmental records.
2. **The truncation flag itself was wrong.** The old code treated "the
   running leaf counter hit exactly the cap" as proof of truncation —
   which is a false positive for any document with *exactly* that many
   leaves and nothing more, and gave no actual guarantee either way.

Fixed in `scripts/openfoodtox/records.py`: the leaf walk now always
runs to completion (up to a separate, much higher hard safety ceiling
of 50,000 leaves, which exists purely to bound memory against a
pathological/adversarial document and is independent of the coverage
question), and `raw_fields_truncated` is set by comparing the *true*
total leaf count against the cap — never inferred from where a counter
stopped. `MAX_RAW_FIELDS_PER_DOCUMENT` is raised to 4,000 (a ~2.8x
margin over the measured 1,405 maximum). **This is not "raising the
cap and claiming completeness" by itself**: every document now also
carries an `evidence_complete` flag (`False` whenever truncated), and
every place that counts something as usable evidence —
`dossiers_with_usable_human_health_endpoint` in the catalogue summary,
the identity audit, and the E250 profile — checks that flag and
excludes/flags incomplete records explicitly rather than silently
including them. **Measured result after the fix: 0 documents in the
full dataset are truncated under the new cap** (confirmed by rerunning
the full catalogue: `documents_with_incomplete_evidence_total: 0`), so
the quarantine mechanism exists and is tested
(`TestTruncationBoundaryAccuracy`,
`TestEvidenceQuarantinePropagation`) but does not currently exclude
anything real. If the dataset grows a larger document in the future,
the mechanism will catch it and say so rather than silently including
or silently truncating it.

### 8.3 E250 reference-value chemical basis (verification request, resolved)

The original E250 profile reported "ADI 0.1 mg/kg bw/day" without
stating what that 0.1 is a mass fraction *of* — the IUCLID
`FLEXIBLE_SUMMARY.ToxRefValues` schema has no dedicated chemical-basis
field at all; `unitCode` only ever encodes the unit (mg/kg bw/day),
not the chemical basis. The basis is only present as free text in
`JustificationAndComments`: *"...the Panel derived an ADI of 0.1 mg
sodium nitrite/kg bw per day, corresponding to 0.07 mg nitrite ion/kg
bw per day."*

Added `scripts/openfoodtox/chemical_basis.py`: a conservative
extractor that looks for a `<number> mg <basis phrase>/kg bw` mention
in the record's own justification text that matches the *stored*
numeric value, and reports that phrase as the basis — tying the
recovered basis to the specific number actually in the structured
field, not just "the first number-like phrase in the paragraph."
*Any other* mg/.../kg bw figure mentioned in the same text is kept as
a separate `other_values_mentioned` entry, never merged, converted, or
treated as equivalent. If no matching mention exists, the basis is
`"unresolved"` — never guessed. For sodium nitrite this resolves to
`basis: "sodium nitrite"` for the stored `0.1`, with `0.07 mg nitrite
ion/kg bw` kept as a separate mention.

**External verification** (explicitly separate from raw IUCLID
extraction, done 2026-10-01 against the actual cited opinion, not a
paraphrase): fetched the EFSA ANS Panel opinion itself
(doi:10.2903/j.efsa.2017.4786, EFSA Journal 2017;15(6):4786) via
<https://efsa.europa.eu/en/efsajournal/pub/4786> and its open-access
mirror <https://pmc.ncbi.nlm.nih.gov/articles/PMC7009987>. The source
opinion's own sentence is a near-verbatim match of the IUCLID text:
*"Using the lowest BMDL of 9.63 mg/kg bw per day for males, and
applying the default factor of 100, an ADI of 0.1 mg sodium
nitrite/kg bw per day was calculated by the Panel, corresponding to
0.07 mg nitrite ion/kg bw per day."* This confirms (a) the IUCLID
export faithfully reproduces the opinion's text, and (b) the Panel
itself states both figures as two chemical-basis expressions of the
*same* BMDL-derived ADI — they are not a discrepancy to resolve, which
is exactly why this extractor keeps both, labeled by basis, rather
than picking one. One secondary note for future readers: some
third-party summaries of this opinion paraphrase the ADI as "0.07 mg
nitrite ion/kg bw/day" alone; this audit's own extraction and the
primary source text both place "0.1 mg sodium nitrite/kg bw per day"
as the figure the Panel calculated, with 0.07 as the stated
corresponding nitrite-ion figure — readers needing more than this
audit's scope should consult the primary opinion, not a paraphrase.
Tests: `tests/unit/test_openfoodtox_chemical_basis.py`.

## 9. Coverage, limitations, and unresolved mappings

- **Not every endpoint subtype has a bespoke field-level parser.**
  `derive_endpoint` is deliberately generic (shared across all
  `ENDPOINT_SUMMARY`/`ENDPOINT_STUDY_RECORD` subtypes) rather than
  hand-modeling all ~80 subtypes individually. `raw_fields` is designed
  to capture everything within the coverage cap (§8.2) — measured as
  complete for every document in this dataset — but a subtype-specific
  consumer (e.g. a future physicochemical-properties integration) would
  still want its own derived view the way `ToxRefValues` has one here.
- **`value`-code decoding is scoped per stylesheet filename, not per
  field**, because the bare `<value>` element name is reused by many
  unrelated picklists (species, sex, population, literature type,
  ...). If a single stylesheet ever renders two distinct `value`
  vocabularies whose numeric codes overlap, a decode could pick the
  wrong label — not observed in this dataset (zero conflicts detected
  across all 72 stylesheets), but not proven impossible either.
  `unitCode` decoding does not have this caveat: it was cross-checked
  as a single consistent global vocabulary.
- **Reference-value extraction (`ToxRefValues`) assumes at most one
  occurrence per container type (ADI/ARfD/AOEL/AAOEL/Other) per
  document.** Confirmed on a large random sample of this dataset; the
  extractor does not silently trust that assumption — if a document
  ever violates it, `parse_warnings` flags that document's reference
  values as needing manual review instead of merging them (tested in
  `test_multiple_occurrences_of_same_container_flags_warning`).
- **Chemical-basis recovery (§8.3) is text-pattern-based**, scoped to
  the literal `mg ... /kg bw` shape actually observed in this dataset's
  justification text. A differently-worded justification (no explicit
  basis phrase, or a different unit shape) will correctly report
  `"unresolved"` rather than guess — which means basis recovery will
  often be unresolved for other substances' reference values, not just
  sodium nitrite. This was verified only for the E250 case end-to-end;
  a broader sweep of how often it resolves across all 21,572
  `ToxRefValues` documents was not performed in this pass.
- **Domain classification is schema-level** (by declared
  `documentSubType`), not a per-record re-verification of the species/
  route actually described in each study's text (see §5.3).
- **No licensing/reuse-terms metadata was transferred with the
  dataset** (§1) — resolving this needs a direct check against EFSA's
  own published OpenFoodTox terms, out of scope for this offline
  audit.
- **`staging/v2/catalogue.jsonl` is large (~1.3 GB, larger than the
  original ~985 MB `staging/catalogue.jsonl`** because every reference
  value now also carries the `chemical_basis` structure and every
  reference substance the expanded `e_numbers` structure). A future
  integration stage would likely want a leaner "derived-fields-only"
  export variant for anything that loads the whole catalogue into
  memory.
- **Transfer-fidelity scope**: see the note in §6 — the sha256
  manifest is a local integrity baseline, not an end-to-end
  transfer-fidelity proof against the original source.

## 10. Recommended next integration stage (not implemented here)

This audit deliberately stops at a read-only staging catalogue. The
next stage, if/when authorized, would need its own explicit scoping
and review — not started here — but based on what this audit found,
it would likely involve: (1) a schema design for how staged
`human_health`-domain findings map onto NutriGuard's existing
ingredient/evidence model, explicitly deciding how to represent
substances that only appear as metabolites (no CAS/EC) without
conflating them with their parent compound; (2) a decision on how (or
whether) to surface `ToxRefValues` reference values in the product
Health Score pipeline, given they are Panel-derived risk-assessment
outputs, not raw study data, and the existing Health Score/ingredient
regulatory logic (`app/services/ingredient_regulatory.py`) has its own
documented, tested conventions this would need to integrate with
rather than bypass; (3) resolving the EFSA reuse-terms question in §9
before any data derived from this set is surfaced to end users; and
(4) a leaner derived-only export path if the full-provenance
`catalogue.jsonl` proves too large for whatever loads it next. None of
this is implemented, wired up, or scheduled by this audit.
