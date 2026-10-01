# OpenFoodTox: bounded source closure

Owner-authorized follow-up, 2026-10-01. Baseline `5cc732a` on
`feat/backend-openfoodtox-dataset-audit`. Read repository instructions and
latest handoff. Use an isolated worktree; fetch/check drift before work and
push. Subagents are permitted if helpful; integrate and review their work.

The automatic document export and localized headings are accepted for this
round. Do not redesign extraction, matching, UI, or the four-profile pilot.
Finish the remaining source work already required by the previous task.

## Exact targets

In `editorial_content.py` and the generated claim/source matrix:

- E250-02 purpose: currently only our seed CSV.
- E330-12 purpose: currently only our seed CSV.
- E951-15 identity and E951-16 purpose: currently only our seed JSON.
- E150d-08: marked `external_primary_source`, but the citation explicitly
  says confirmed through secondary reporting. Read the actual primary
  opinion/official primary summary or keep this claim internal; do not
  relabel a secondary source as primary.
- Check adjacent E150d-07 while here: replace the vague background-section
  reference with the concrete document identity and passage supporting the
  listed uses, or narrow/withhold unsupported details.

For each target either (a) read and record an actual supporting primary
source with DOI/URL, passage locator and access date, narrowing EN/BG prose
to what it supports, or (b) retain the unsupported claim in operator-only
evidence and omit it from both consumer previews. Missing content should
not generate placeholder text. This is an acceptable completion outcome;
do not prolong the task to fill every field. Keep content origin distinct
from supporting evidence and scientific/translation review unapproved.

Extend the existing seed-only provenance regression to identity/purpose
fields that are actually rendered, not only effects. Verify any withheld
claims remain available internally and are absent from both preview bodies.
Tests validate policy and rendering, not scientific truth.

## Delivery and stopping criteria

- All targets have directly checked support or are withheld from previews.
- No consumer editorial claim relies solely on a seed filename; no source
  is described as primary when only a secondary account was inspected.
- Regenerate only the pilot into a new versioned directory; preserve prior
  output. Mechanically export both review documents, run both export/check
  commands and confirm repeatability. Do not hand-edit generated prose.
- Run focused tests and full backend suite; report exact results. Update
  PILOT_REPORT and CODEX_HANDOFF with a target-by-target resolution and
  actual limitations. Do not claim publication/scientific approval.
- Commit and push normally to this same branch; return the full SHA.

No merge, deploy, live database/container access, seed import, API/Android/
Health Score changes, secrets edits, bulk extraction rerun or bulk data
commit. Once these targets pass, stop and hand off for final review.
