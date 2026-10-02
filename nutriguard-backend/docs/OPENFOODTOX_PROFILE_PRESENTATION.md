# Ingredient evidence profile: EN/BG review specification

Status: proposed presentation for the offline OpenFoodTox pilot (2026-10-01).
Not an API contract, implemented Android screen or scientific approval.

## User-facing order

| EN heading | BG heading | Content rule |
| --- | --- | --- |
| Ingredient name + E-code | Име на съставката + E-код | One canonical identity; no synthetic IDs or repeated code badges. |
| What it is | Какво представлява | Plain-language identity and origin only where supported. |
| Purpose in food | Роля в храната | Sourced technological function, not an inference from toxicity. |
| Effects and conditions | Ефекти и условия | Findings together with dose/exposure context and limits of evidence. |
| Intake guidance | Насоки за прием | Only reviewed applicable guidance with exact units, chemical basis and population. |
| Sources | Източници | Informational references and URLs at the bottom, with publication dates. |

Hide sections with no substantive information rather than adding placeholder text.
Do not hide qualifications required to understand a shown claim. No arbitrary text
length cap in the pilot: use readable paragraphs and avoid repetitive sections.
In the operator report retain missingness, uncertainty and rejection reasons even
when their empty counterparts are hidden from the proposed consumer view.

## Evidence wording and display gates

- Separate human findings, animal findings, in-vitro findings and assessment conclusions.
  Animal findings must not read as established effects in people.
- Present effects with their conditions; avoid unsupported categorical safe/dangerous
  labels. Presence of an E-code, a study section, or an EFSA opinion implies neither
  danger nor regulatory approval. Dataset absence implies neither safety nor danger.
- No new risk color/category is computed by this pilot. Future UI colors require
  separately reviewed risk logic; incomplete/unknown evidence must not become green.
- No ADI calculation from experimental NOAEL/LOAEL/BMD. An ADI is not a recommended
  target intake. Keep chemical basis and applicable exposure period beside any number.
- Keep raw source values separate from explanations; never silently convert salt/ion
  bases or mg/g/µg. Unresolved basis, conflicting references or unsupported population
  block numeric consumer guidance, not the internal evidence report.
- Do not invent female/male-specific limits. Retain the source's actual population.
- No portion/exceedance claims without product concentration and relevant consumption
  data. No changes to product Health Score or NOVA classification.
- EN/BG drafts must express the same certainty and conditions. Translation is not
  scientific validation. Missing reviewed BG content remains an explicit internal
  publication blocker; do not label machine text REVIEWED.
- Sources remain at the bottom; internal claim-to-source mapping remains precise.

## Required operator review bundle per identity

1. Canonical identity, original names, identifiers and match evidence/conflicts.
2. Source archive/document/field references and relevant literal excerpts.
3. Structured findings, study type/species, exposure conditions and assessment dates.
4. Each reference value with basis, unit, period, population and review eligibility.
5. EN draft and BG draft, each claim linked to source evidence; omitted fields listed
   only in the internal review notes.
6. Separate statuses for identity match, extraction completeness, scientific review,
   translation review, reuse clearance and source freshness. One must not promote another.
7. Outcome: ready for human review, missing evidence, ambiguous identity or blocked;
   none of these automatically authorizes publication/import.

Codex will review the real pilot bundles before implementing any UI binding. No
example health descriptions are fabricated in this specification to fill gaps.
