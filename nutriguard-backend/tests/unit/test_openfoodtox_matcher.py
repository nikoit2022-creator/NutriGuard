"""Tests for scripts/openfoodtox/matcher.py: offline, dry-run identifier
matching between a catalogue snapshot and a staged OpenFoodTox
catalogue.jsonl, and subject-link resolution within one dossier.

All fixtures are small, synthetic, hand-built dicts in the exact shape
``dossier.build_dossier_record``/``extract.py``'s ``catalogue`` command
produce -- never the real (non-git-tracked, multi-GB) dataset.
"""

from __future__ import annotations

import json

from scripts.openfoodtox.catalogue_snapshot import CatalogueIdentity
from scripts.openfoodtox.matcher import match_catalogue_against_staging, resolve_subject_links


def _cat(id="cat-1", e_number=None, cas=None, name="Test substance"):
    from scripts.openfoodtox.catalogue_snapshot import normalize_cas, normalize_e_number

    return CatalogueIdentity(
        id=id,
        common_name=name,
        e_number_raw=e_number,
        e_number_normalized=normalize_e_number(e_number),
        cas_number_raw=cas,
        cas_number_normalized=normalize_cas(cas),
        source="tracked_seed_json",
    )


def _e_numbers_block(*raws: str, conflict_override: bool | None = None) -> dict:
    from scripts.openfoodtox.e_numbers import collect_e_numbers

    block = collect_e_numbers(list(raws))
    if conflict_override is not None:
        block["conflict"] = conflict_override
    return block


def _dossier_line(
    dossier_file: str,
    rs_name: str,
    cas: str | None = None,
    ec: str | None = None,
    e_number_synonyms: tuple[str, ...] = (),
    title: str | None = None,
    date_of_evaluation: str | None = None,
    document_key: str | None = None,
    status: str = "ok",
) -> str:
    record = {
        "status": status,
        "dossier_file": dossier_file,
        "title": title or f"Opinion on {rs_name}",
        "dossier_summary": {"date_of_evaluation": date_of_evaluation},
        "identity": {
            "reference_substances": [
                {
                    "document_key": document_key or f"rs-{dossier_file}",
                    "name": rs_name,
                    "cas_number": cas,
                    "ec_number": ec,
                    "e_numbers": _e_numbers_block(*e_number_synonyms),
                }
            ]
        },
    }
    return json.dumps(record)


def _write_jsonl(tmp_path, lines: list[str]) -> str:
    path = tmp_path / "catalogue.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


class TestExactMatch:
    def test_exact_match_by_e_number_only(self, tmp_path):
        cat = _cat(e_number="E951", cas=None)
        path = _write_jsonl(
            tmp_path,
            [_dossier_line("d1.i6z", "Aspartame", cas="22839-47-0", e_number_synonyms=("E 951",))],
        )
        [result] = match_catalogue_against_staging([cat], path)
        assert result.overall_status == "exact_match"
        assert len(result.exact_dossiers) == 1
        assert result.exact_dossiers[0].dossier_file == "d1.i6z"

    def test_exact_match_requires_agreement_when_both_present(self, tmp_path):
        cat = _cat(e_number="E330", cas="77-92-9")
        path = _write_jsonl(
            tmp_path,
            [_dossier_line("d1.i6z", "Citric acid", cas="77-92-9", e_number_synonyms=("E 330",))],
        )
        [result] = match_catalogue_against_staging([cat], path)
        assert result.overall_status == "exact_match"
        assert result.exact_dossiers[0].explanation.startswith("CAS and E-number")


class TestConflictingIdentifiers:
    def test_conflicting_when_both_present_but_disagree(self, tmp_path):
        """E-number agrees but CAS disagrees -- must stay unresolved, not
        be approved because one identifier happened to match."""
        cat = _cat(e_number="E330", cas="99-99-9")
        path = _write_jsonl(
            tmp_path,
            [_dossier_line("d1.i6z", "Citric acid", cas="77-92-9", e_number_synonyms=("E 330",))],
        )
        [result] = match_catalogue_against_staging([cat], path)
        assert result.overall_status == "conflicting"
        assert result.exact_dossiers == []

    def test_source_identity_ambiguous_dossier_side_conflict(self, tmp_path):
        """The dossier's own REFERENCE_SUBSTANCE record has two distinct
        recognized E-numbers on it -- its own identity is ambiguous and
        must never be used for matching, even though one of its
        candidates textually matches the query."""
        cat = _cat(e_number="E150a", cas=None)
        path = _write_jsonl(
            tmp_path,
            [_dossier_line("d1.i6z", "Weird record", e_number_synonyms=("E 150a", "E 150b"))],
        )
        [result] = match_catalogue_against_staging([cat], path)
        assert result.overall_status == "conflicting"
        assert result.conflicting_dossiers[0].verdict == "source_identity_ambiguous"


class TestSuffixAndRangePreservation:
    def test_suffix_distinction_e150d_not_confused_with_sibling(self, tmp_path):
        """E150a/b/c/d are four genuinely distinct substances -- a query
        for E150d must never match an E150a dossier."""
        cat = _cat(e_number="E150d", cas=None)
        path = _write_jsonl(
            tmp_path,
            [_dossier_line("plain-caramel.i6z", "Plain caramel", e_number_synonyms=("E 150a",))],
        )
        [result] = match_catalogue_against_staging([cat], path)
        assert result.overall_status == "no_match"

    def test_range_candidate_cannot_become_exact_link(self, tmp_path):
        cat = _cat(e_number="E252", cas=None)
        path = _write_jsonl(
            tmp_path,
            [_dossier_line("d1.i6z", "Potassium nitrate (ambiguous record)", e_number_synonyms=("E 251-252",))],
        )
        [result] = match_catalogue_against_staging([cat], path)
        assert result.overall_status == "ambiguous"
        assert result.ambiguous_dossiers[0].verdict == "ambiguous_range"


class TestMissingIdentifiers:
    def test_missing_identifiers_are_not_evidence_of_equivalence(self, tmp_path):
        cat = _cat(e_number="E999", cas=None)
        path = _write_jsonl(
            tmp_path,
            [_dossier_line("d1.i6z", "Something else", cas="1-2-3")],  # no e-number synonym at all
        )
        [result] = match_catalogue_against_staging([cat], path)
        assert result.overall_status == "no_match"

    def test_missing_catalogue_cas_does_not_block_e_number_match(self, tmp_path):
        """Catalogue has no CAS on file (as the real tracked seed data
        does not); the dossier having one must not prevent an
        E-number-only match -- it's simply not compared."""
        cat = _cat(e_number="E250", cas=None)
        path = _write_jsonl(
            tmp_path,
            [_dossier_line("d1.i6z", "Sodium nitrite", cas="7632-00-0", e_number_synonyms=("E 250",))],
        )
        [result] = match_catalogue_against_staging([cat], path)
        assert result.overall_status == "exact_match"


class TestMultipleAssessmentsAndAmbiguity:
    def test_multiple_assessments_of_one_substance_are_grouped_not_duplicated(self, tmp_path):
        cat = _cat(e_number="E951", cas="22839-47-0")
        lines = [
            _dossier_line(f"d{i}.i6z", "Aspartame", cas="22839-47-0", e_number_synonyms=("E 951",), document_key=f"rs-{i}")
            for i in range(1, 7)
        ]
        path = _write_jsonl(tmp_path, lines)
        [result] = match_catalogue_against_staging([cat], path)
        assert result.overall_status == "exact_match"
        assert len(result.exact_dossiers) == 6
        assert result.conflicting_dossiers == []

    def test_ambiguous_when_matched_dossiers_disagree_on_underlying_identity(self, tmp_path):
        """Catalogue only has an E-number (no CAS) and it resolves,
        across different dossiers, to reference substances with two
        different CAS numbers -- cannot be linked to one specific
        substance and must not be silently merged or arbitrarily
        picked."""
        cat = _cat(e_number="E500", cas=None)
        lines = [
            _dossier_line("d1.i6z", "Sodium carbonate", cas="497-19-8", e_number_synonyms=("E 500",)),
            _dossier_line("d2.i6z", "Sodium bicarbonate", cas="144-55-8", e_number_synonyms=("E 500",)),
        ]
        path = _write_jsonl(tmp_path, lines)
        [result] = match_catalogue_against_staging([cat], path)
        assert result.overall_status == "ambiguous"
        assert result.exact_dossiers == []
        assert len(result.ambiguous_dossiers) == 2


class TestNonOkStatusExcluded:
    def test_dossiers_with_parse_errors_are_skipped(self, tmp_path):
        cat = _cat(e_number="E951", cas=None)
        path = _write_jsonl(tmp_path, [_dossier_line("d1.i6z", "Aspartame", e_number_synonyms=("E 951",), status="unreadable_archive")])
        [result] = match_catalogue_against_staging([cat], path)
        assert result.overall_status == "no_match"


class TestRepeatability:
    def test_running_the_match_twice_gives_identical_results(self, tmp_path):
        cat = _cat(e_number="E951", cas="22839-47-0")
        path = _write_jsonl(
            tmp_path,
            [_dossier_line("d1.i6z", "Aspartame", cas="22839-47-0", e_number_synonyms=("E 951",))],
        )
        [r1] = match_catalogue_against_staging([cat], path)
        [r2] = match_catalogue_against_staging([cat], path)
        assert r1.to_dict() == r2.to_dict()


class TestResolveSubjectLinks:
    def _single_identity_dossier(self):
        return {
            "identity": {
                "substances": [{"document_key": "sub-1/d1", "reference_substance_ref": "rs-1/d1"}],
                "reference_substances": [{"document_key": "rs-1/d1", "name": "Sodium nitrite", "cas_number": "7632-00-0"}],
            },
            "reference_values": [{"value_type": "ADI", "manifest_links": []}],
            "endpoints": {"human_health": [{"endpoint_code": "X", "manifest_links": []}], "environmental": []},
        }

    def test_single_identity_dossier_attaches_everything(self):
        linkage = resolve_subject_links(self._single_identity_dossier())
        assert len(linkage["by_identity"]["rs-1/d1"]["reference_values"]) == 1
        assert len(linkage["by_identity"]["rs-1/d1"]["endpoints"]["human_health"]) == 1
        assert linkage["unresolved"]["reference_values"] == []
        assert linkage["unresolved"]["endpoints"] == []

    def _multi_identity_dossier(self):
        return {
            "identity": {
                "substances": [{"document_key": "sub-A/d1", "reference_substance_ref": "rs-A/d1"}],
                "reference_substances": [
                    {"document_key": "rs-A/d1", "name": "Curdlan", "cas_number": "54724-00-4"},
                    {"document_key": "rs-B/d1", "name": "Glucose", "cas_number": "50-99-7"},
                ],
            },
            "reference_values": [
                {"value_type": "ADI", "manifest_links": [{"ref_uuid": "sub-A/d1", "ref_type": "CHILD"}]},
                {"value_type": "OTHER", "manifest_links": []},  # no link at all -> unresolved
            ],
            "endpoints": {
                "human_health": [
                    {"endpoint_code": "Y", "manifest_links": [{"ref_uuid": "rs-A/d1", "ref_type": "CHILD"}]},
                ]
            },
        }

    def test_multi_identity_dossier_resolves_via_manifest_links(self):
        linkage = resolve_subject_links(self._multi_identity_dossier())
        assert len(linkage["by_identity"]["rs-A/d1"]["reference_values"]) == 1
        assert linkage["by_identity"]["rs-B/d1"]["reference_values"] == []
        assert len(linkage["unresolved"]["reference_values"]) == 1
        assert linkage["by_identity"]["rs-A/d1"]["endpoints"]["human_health"][0]["endpoint_code"] == "Y"

    def test_link_to_unrelated_uuid_is_quarantined_not_guessed(self):
        dossier = self._multi_identity_dossier()
        dossier["reference_values"][1]["manifest_links"] = [{"ref_uuid": "does-not-exist/d1", "ref_type": "CHILD"}]
        linkage = resolve_subject_links(dossier)
        assert len(linkage["unresolved"]["reference_values"]) == 1
        assert linkage["unresolved"]["reference_values"][0]["subject_linkage_basis"] == "no_resolvable_subject_link"
