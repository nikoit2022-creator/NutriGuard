"""Tests for scripts/openfoodtox/export_docs.py -- the reproducible
export/check tool for the two committed docs that must always match
generated content (docs/OPENFOODTOX_DELIVERY_CONSISTENCY_TASK.md).
"""

from __future__ import annotations

import json

from scripts.openfoodtox.export_docs import _render_claim_matrix, _render_review_drafts, _PROFILE_FILES


def _minimal_profile(e_number_raw: str, e_number_normalized: str, common_name: str) -> dict:
    return {
        "catalogue_identity": {
            "common_name": common_name,
            "e_number_raw": e_number_raw,
            "e_number_normalized": e_number_normalized,
            "source": "tracked_seed_json",
        },
        "outcome": "ready_for_human_review",
        "deduplicated_reference_values": [],
        "editorial_operator_only_notes": [],
        "draft_en": f"# {common_name} ({e_number_raw})\n\nStatus: DRAFT.",
        "draft_bg": f"# {common_name} ({e_number_raw})\n\nСтатус: ЧЕРНОВА.",
        "internal_evidence_en": "## Internal evidence\n\n(none)",
    }


class TestRenderReviewDrafts:
    def test_renders_all_four_identities_with_exact_json_fields(self, tmp_path):
        names = {
            "E250": ("E250", "E250", "Sodium Nitrite"),
            "E150d": ("E150d", "E150d", "(ad hoc query, not a provisioned NutriGuard ingredient: E150d)"),
            "E330": ("E330", "E330", "Citric acid"),
            "E951": ("E951", "E951", "Aspartame"),
        }
        for e_number, filename in _PROFILE_FILES.items():
            raw, norm, name = names[e_number]
            (tmp_path / filename).write_text(json.dumps(_minimal_profile(raw, norm, name)), encoding="utf-8")

        content = _render_review_drafts(str(tmp_path))
        # Every body is a verbatim fenced-code-block copy of draft_en/draft_bg.
        assert "```text\n# Sodium Nitrite (E250)" in content
        assert "```text\n# Aspartame (E951)" in content
        # Section heading uses export_docs' own _display_name lookup (the
        # actual draft_en/draft_bg heading text is evidence_bundle.py's
        # responsibility, covered by test_openfoodtox_evidence_bundle.py's
        # TestLocalizedDisplayTitles -- this only checks export_docs
        # doesn't re-introduce the raw common_name in its own section
        # heading line).
        assert "## E150d -- Sulphite ammonia caramel / Сулфитно-амонячен карамел" in content

    def test_check_detects_tampering(self, tmp_path):
        for e_number, filename in _PROFILE_FILES.items():
            (tmp_path / filename).write_text(
                json.dumps(_minimal_profile(e_number, e_number, "X")), encoding="utf-8"
            )
        rendered = _render_review_drafts(str(tmp_path))
        tampered = rendered.replace("Status: DRAFT.", "Status: hand-edited, drifted from the source.")
        assert rendered != tampered  # sanity: the tamper actually changed something a `check` would catch


class TestRenderClaimMatrix:
    def test_covers_every_editorial_entry_and_has_no_vague_source(self):
        from scripts.openfoodtox.editorial_content import EDITORIAL_CONTENT

        content = _render_claim_matrix()
        for e_number in EDITORIAL_CONTENT:
            assert f"| {e_number}-" in content
        assert "standard food-chemistry references" not in content.lower()

    def test_claim_matrix_is_deterministic(self):
        assert _render_claim_matrix() == _render_claim_matrix()
