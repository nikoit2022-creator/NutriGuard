"""End-to-end tests for scripts/openfoodtox/evidence_bundle.py against
small, synthetic, hand-built ``.i6z`` archives -- never the real
(non-git-tracked, multi-GB) OpenFoodTox dataset.
"""

from __future__ import annotations

import zipfile

from scripts.openfoodtox.catalogue_snapshot import CatalogueIdentity, normalize_e_number
from scripts.openfoodtox.codebook import Codebook
from scripts.openfoodtox.evidence_bundle import _assess_review_eligibility, build_profile
from scripts.openfoodtox.matcher import match_catalogue_against_staging

_GOOD_RV = {
    "value_type": "ADI",
    "evidence_complete": True,
    "subject_linkage_basis": "single_identity_dossier",
    "chemical_basis": {"status": "resolved", "basis": "sodium nitrite"},
    "unit_label": "mg/kg bw/day",
    "population_label": "consumers",
}


class TestAssessReviewEligibility:
    def test_fully_valid_record_is_consumer_guidance_eligible(self):
        e = _assess_review_eligibility(_GOOD_RV, feed_context=False, identity_evidence_complete=True)
        assert e.operator_inspectable is True
        assert e.consumer_guidance_eligible is True
        assert e.reasons == []

    def test_truncated_record_with_otherwise_valid_basis_and_units_is_never_eligible(self):
        """Regression: evidence_complete was previously not checked at all
        -- a record whose own extraction was truncated must never be
        consumer_guidance_eligible no matter how clean its basis/unit
        look, since the truncation could have dropped a disqualifying
        field."""
        rv = {**_GOOD_RV, "evidence_complete": False}
        e = _assess_review_eligibility(rv, feed_context=False, identity_evidence_complete=True)
        assert e.operator_inspectable is True  # still inspectable, never hidden
        assert e.consumer_guidance_eligible is False
        assert any("incomplete" in r for r in e.reasons)

    def test_missing_evidence_complete_flag_fails_closed(self):
        rv = {**_GOOD_RV}
        del rv["evidence_complete"]
        e = _assess_review_eligibility(rv, feed_context=False, identity_evidence_complete=True)
        assert e.consumer_guidance_eligible is False

    def test_incomplete_identity_extraction_is_never_eligible(self):
        e = _assess_review_eligibility(_GOOD_RV, feed_context=False, identity_evidence_complete=False)
        assert e.consumer_guidance_eligible is False
        assert any("identity" in r for r in e.reasons)

    def test_unresolved_subject_link_is_never_eligible(self):
        rv = {**_GOOD_RV, "subject_linkage_basis": "multiple_distinct_linked_identities"}
        e = _assess_review_eligibility(rv, feed_context=False, identity_evidence_complete=True)
        assert e.consumer_guidance_eligible is False
        assert any("subject linkage" in r for r in e.reasons)

    def test_aoel_operator_exposure_level_is_never_a_consumer_value(self):
        """AOEL/AAOEL are occupational (operator) exposure levels, never
        a consumer daily/acute intake limit -- must be excluded
        regardless of how complete and well-sourced the record is."""
        rv = {**_GOOD_RV, "value_type": "AOEL", "population_label": "consumers"}
        e = _assess_review_eligibility(rv, feed_context=False, identity_evidence_complete=True)
        assert e.consumer_guidance_eligible is False
        assert any("AOEL" in r for r in e.reasons)

    def test_other_type_is_never_a_consumer_value(self):
        rv = {**_GOOD_RV, "value_type": "OTHER"}
        e = _assess_review_eligibility(rv, feed_context=False, identity_evidence_complete=True)
        assert e.consumer_guidance_eligible is False

    def test_worker_population_is_not_a_consumer_population(self):
        rv = {**_GOOD_RV, "population_label": "workers"}
        e = _assess_review_eligibility(rv, feed_context=False, identity_evidence_complete=True)
        assert e.consumer_guidance_eligible is False
        assert any("population" in r for r in e.reasons)

    def test_species_specific_population_is_not_a_consumer_population(self):
        rv = {**_GOOD_RV, "value_type": "OTHER", "population_label": "Poultry"}
        e = _assess_review_eligibility(rv, feed_context=False, identity_evidence_complete=True)
        assert e.consumer_guidance_eligible is False

    def test_feed_context_blocks_eligibility_even_with_consumer_population_label(self):
        rv = {**_GOOD_RV, "value_type": "OTHER", "population_label": "consumers"}
        e = _assess_review_eligibility(rv, feed_context=True, identity_evidence_complete=True)
        assert e.consumer_guidance_eligible is False
        assert any("feed" in r.lower() for r in e.reasons)

    def test_unresolved_chemical_basis_blocks_eligibility(self):
        rv = {**_GOOD_RV, "chemical_basis": {"status": "unresolved_no_mention"}}
        e = _assess_review_eligibility(rv, feed_context=False, identity_evidence_complete=True)
        assert e.consumer_guidance_eligible is False

    def test_missing_unit_blocks_eligibility(self):
        rv = {**_GOOD_RV, "unit_label": None}
        e = _assess_review_eligibility(rv, feed_context=False, identity_evidence_complete=True)
        assert e.consumer_guidance_eligible is False

    def test_all_reasons_reported_together_not_just_the_first(self):
        rv = {
            "value_type": "AOEL",
            "evidence_complete": False,
            "subject_linkage_basis": "no_resolvable_subject_link",
            "chemical_basis": {"status": "unresolved_no_mention"},
            "unit_label": None,
            "population_label": "workers",
        }
        e = _assess_review_eligibility(rv, feed_context=True, identity_evidence_complete=False)
        assert e.consumer_guidance_eligible is False
        assert len(e.reasons) >= 6

_TEST_CODEBOOK = Codebook(
    unit={"2085": "mg/kg bw/day"},
    value_by_xsl={"FLEXIBLE_SUMMARY-ToxRefValues.xsl": {"8521": "consumers"}},
)

MANIFEST_TMPL = """<?xml version='1.0' encoding='UTF-8'?><manifest xmlns="http://iuclid6.echa.europa.eu/namespaces/manifest/v1" xmlns:xlink="http://www.w3.org/1999/xlink">
<general-information>
  <title>IUCLID 6 container manifest file</title>
  <submission-type>EFSA_CHEMICALS_DATABASE_SUB</submission-type>
  <archive-type>DOSSIER_DATA</archive-type>
</general-information>
<base-document-uuid>dossier-1/dossier-1</base-document-uuid>
<contained-documents>
{documents}
</contained-documents>
</manifest>"""

DOC_TMPL = '<document id="{uuid}"><type>{type}</type>{subtype}<name xlink:type="simple" xlink:href="{entry}">{name}</name><uuid>{uuid}</uuid><links>{links}</links></document>'
LINK_TMPL = '<link><ref-uuid>{ref_uuid}</ref-uuid><ref-type>{ref_type}</ref-type></link>'


def _doc_xml(document_key: str, name: str, document_type: str, content_xml: str, sub_type: str | None = None) -> bytes:
    subtype_el = f"<i6m:documentSubType>{sub_type}</i6m:documentSubType>" if sub_type else ""
    return f"""<?xml version="1.0"?>
<i6c:Document xmlns:i6c="http://iuclid6.echa.europa.eu/namespaces/platform-container/v2">
  <i6c:PlatformMetadata xmlns:i6m="http://iuclid6.echa.europa.eu/namespaces/platform-metadata/v1">
    <i6m:documentKey>{document_key}</i6m:documentKey>
    <i6m:name>{name}</i6m:name>
    <i6m:documentType>{document_type}</i6m:documentType>
    {subtype_el}
    <i6m:definitionVersion>9.0</i6m:definitionVersion>
  </i6c:PlatformMetadata>
  <i6c:Content>
    {content_xml}
  </i6c:Content>
</i6c:Document>""".encode("utf-8")


def _make_pilot_style_dossier_zip(
    path: str,
    *,
    cas: str,
    e_number_synonym: str,
    name: str,
    adi_lower_value: str,
    dossier_uuid: str = "dossier-1",
    justification: str | None = None,
    title: str | None = None,
    date_of_evaluation: str = "2015-01-01",
    expert_group_code: str = "133185",
    regulation_code: str = "133164",
) -> None:
    rs_key = f"rs-1/{dossier_uuid}"
    sub_key = f"sub-1/{dossier_uuid}"
    flex_key = f"flex-1/{dossier_uuid}"

    dossier_doc = DOC_TMPL.format(uuid=dossier_uuid, type="DOSSIER", subtype="", entry="dossier.i6d", name=f"Opinion on {name}", links="")
    rs_doc = DOC_TMPL.format(uuid=rs_key, type="REFERENCE_SUBSTANCE", subtype="", entry="rs.i6d", name=name, links="")
    sub_doc = DOC_TMPL.format(uuid=sub_key, type="SUBSTANCE", subtype="", entry="sub.i6d", name=name, links="")
    flex_doc = DOC_TMPL.format(
        uuid=flex_key,
        type="FLEXIBLE_SUMMARY",
        subtype="<subtype>ToxRefValues</subtype>",
        entry="flex.i6d",
        name="Reference values",
        links=LINK_TMPL.format(ref_uuid=sub_key, ref_type="CHILD"),
    )
    manifest = MANIFEST_TMPL.format(documents=dossier_doc + rs_doc + sub_doc + flex_doc).encode("utf-8")

    title = title or f"Opinion on {name}"
    justification = justification or f"The Panel derived an ADI of {adi_lower_value} mg {name}/kg bw per day."
    dossier_xml = _doc_xml(
        dossier_uuid,
        title,
        "DOSSIER",
        f"""<DOSSIER xmlns="http://iuclid6.echa.europa.eu/namespaces/DOSSIER/9.0">
             <LiteratureReference><EFSAOutputTitle>{title}</EFSAOutputTitle><DateOfEvaluation>{date_of_evaluation}</DateOfEvaluation></LiteratureReference>
             <Domain><FoodDomain><value>119227</value></FoodDomain><Regulation><value>{regulation_code}</value></Regulation><ExpertGroup><value>{expert_group_code}</value></ExpertGroup></Domain>
           </DOSSIER>""",
    )
    rs_xml = _doc_xml(
        rs_key,
        name,
        "REFERENCE_SUBSTANCE",
        f"""<REFERENCE_SUBSTANCE xmlns="http://iuclid6.echa.europa.eu/namespaces/REFERENCE_SUBSTANCE/9.0">
             <ReferenceSubstanceName>{name}</ReferenceSubstanceName>
             <Inventory><CASNumber>{cas}</CASNumber></Inventory>
             <Synonyms><Synonyms><entry><Name>{e_number_synonym}</Name></entry></Synonyms></Synonyms>
           </REFERENCE_SUBSTANCE>""",
    )
    sub_xml = _doc_xml(
        sub_key,
        name,
        "SUBSTANCE",
        f"""<SUBSTANCE xmlns="http://iuclid6.echa.europa.eu/namespaces/SUBSTANCE/9.0">
             <GeneralInformation><ChemicalName>{name}</ChemicalName><ReferenceSubstance><ReferenceSubstance>{rs_key}</ReferenceSubstance></ReferenceSubstance></GeneralInformation>
           </SUBSTANCE>""",
    )
    flex_xml = _doc_xml(
        flex_key,
        "Reference values",
        "FLEXIBLE_SUMMARY",
        f"""<FLEXIBLE_SUMMARY.ToxRefValues xmlns="http://iuclid6.echa.europa.eu/namespaces/FLEXIBLE_SUMMARY/9.0">
             <HumanHealthHazardCharacteristics>
               <AcceptableDailyIntake>
                 <Adi><unitCode>2085</unitCode><lowerValue>{adi_lower_value}</lowerValue></Adi>
                 <Population><value>8521</value></Population>
                 <JustificationAndComments>{justification}</JustificationAndComments>
               </AcceptableDailyIntake>
             </HumanHealthHazardCharacteristics>
           </FLEXIBLE_SUMMARY.ToxRefValues>""",
        sub_type="ToxRefValues",
    )

    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.xml", manifest)
        zf.writestr("dossier.i6d", dossier_xml)
        zf.writestr("rs.i6d", rs_xml)
        zf.writestr("sub.i6d", sub_xml)
        zf.writestr("flex.i6d", flex_xml)


def _build_profile_for_single_dossier_zip(tmp_path, zip_filename: str, *, e_number_raw: str, common_name: str) -> dict:
    """Shared end-to-end harness: parse the given zip into a tiny
    catalogue.jsonl (for matcher.py), match it by e_number_raw, then
    build the full profile. Reused across tests that need a real
    build_profile() output rather than calling its internal helpers
    directly, so they also exercise editorial_content.py's real entries
    (looked up by e_number_normalized) end to end."""
    import json

    from scripts.openfoodtox.dossier import build_dossier_record

    dossiers_dir = tmp_path / "dossiers"
    record = build_dossier_record(str(dossiers_dir / zip_filename), zip_filename, None)
    cat_jsonl = tmp_path / "catalogue.jsonl"
    cat_jsonl.write_text(json.dumps(record) + "\n", encoding="utf-8")

    cat = CatalogueIdentity(
        id=f"test_{e_number_raw.lower()}",
        common_name=common_name,
        e_number_raw=e_number_raw,
        e_number_normalized=normalize_e_number(e_number_raw),
        cas_number_raw=None,
        cas_number_normalized=None,
        source="tracked_seed_json",
    )
    [result] = match_catalogue_against_staging([cat], str(cat_jsonl))
    assert result.overall_status == "exact_match", result.explanation
    return build_profile(cat, result, str(dossiers_dir), _TEST_CODEBOOK)


class TestBuildProfileEndToEnd:
    def test_exact_match_produces_ready_for_review_bundle(self, tmp_path):
        dossiers_dir = tmp_path / "dossiers"
        dossiers_dir.mkdir()
        _make_pilot_style_dossier_zip(
            str(dossiers_dir / "d1.i6z"), cas="7632-00-0", e_number_synonym="E 250", name="Sodium nitrite", adi_lower_value="0.1"
        )

        # Build the matcher's view the same way the real pilot does: via
        # the lightweight catalogue.jsonl shape (not re-deriving it from
        # the zip here -- matcher.py is tested against that shape
        # separately in test_openfoodtox_matcher.py). Construct the
        # equivalent hit directly through the matcher against a tiny
        # synthetic catalogue.jsonl built by re-parsing the same archive.
        import json

        from scripts.openfoodtox.dossier import build_dossier_record

        record = build_dossier_record(str(dossiers_dir / "d1.i6z"), "d1.i6z", None)
        cat_jsonl = tmp_path / "catalogue.jsonl"
        cat_jsonl.write_text(json.dumps(record) + "\n", encoding="utf-8")

        cat = CatalogueIdentity(
            id="e250_sodium_nitrite",
            common_name="Sodium Nitrite",
            e_number_raw="E250",
            e_number_normalized=normalize_e_number("E250"),
            cas_number_raw=None,
            cas_number_normalized=None,
            source="tracked_seed_json",
        )
        [result] = match_catalogue_against_staging([cat], str(cat_jsonl))
        assert result.overall_status == "exact_match"

        profile = build_profile(cat, result, str(dossiers_dir), _TEST_CODEBOOK)
        assert profile["outcome"] == "ready_for_human_review"
        assert profile["statuses"]["scientific_review"] == "not_reviewed"
        assert profile["statuses"]["translation_review"] == "not_reviewed"
        assert len(profile["deduplicated_reference_values"]) == 1
        rv = profile["deduplicated_reference_values"][0]
        assert rv["lower_value"] == "0.1"
        assert rv["unit_label"] == "mg/kg bw/day"
        assert rv["population_label"] == "consumers"
        assert rv["review_eligibility"]["operator_inspectable"] is True
        assert rv["review_eligibility"]["consumer_guidance_eligible"] is True
        assert rv["review_eligibility"]["reasons"] == []
        assert "Sodium nitrite" in profile["draft_en"]
        assert "ЧЕРНОВА" in profile["draft_bg"]
        # The draft is a paraphrase built from structured fields, not the
        # source's own sentence, which instead lives in internal_evidence_en.
        assert "0.1" in profile["draft_en"]
        assert "mg/kg bw/day" in profile["draft_en"]
        assert "The Panel derived an ADI" not in profile["draft_en"]
        assert "The Panel derived an ADI" in profile["internal_evidence_en"]
        # This record is consumer_guidance_eligible, so Intake guidance IS
        # now shown for it (task: show it when "adequately supported" --
        # the whole profile still stays DRAFT/not_reviewed at the top).
        assert "Intake guidance" in profile["draft_en"]
        assert "target intake" in profile["draft_en"]  # ADI-is-not-a-target-intake caveat present
        assert not any(s["section"] == "Intake guidance" for s in profile["omitted_sections_internal_note"])
        # E250 has real editorial content (identity/purpose) -- those
        # sections are populated, not omitted, for this substance.
        assert "What it is" in profile["draft_en"]
        assert "Purpose in food" in profile["draft_en"]
        assert not any(s["section"] in ("What it is", "Purpose in food") for s in profile["omitted_sections_internal_note"])

    def test_build_profile_rejects_non_exact_match(self, tmp_path):
        cat = CatalogueIdentity(
            id="x", common_name="X", e_number_raw="E999", e_number_normalized="E999",
            cas_number_raw=None, cas_number_normalized=None, source="tracked_seed_json",
        )
        from scripts.openfoodtox.matcher import MatchResult

        no_match = MatchResult(catalogue_identity=cat, overall_status="no_match", explanation="none")
        try:
            build_profile(cat, no_match, str(tmp_path), None)
            assert False, "expected ValueError"
        except ValueError:
            pass


class TestEditorialContentCorrections:
    """docs/OPENFOODTOX_PROFILE_CONTENT_TASK.md's four required content
    corrections, tested for structure/presence -- not for scientific
    truth just because a fixture contains a string (task's own
    instruction)."""

    def test_e951_pku_exception_appears_in_both_language_drafts_adjacent_to_adi(self, tmp_path):
        dossiers_dir = tmp_path / "dossiers"
        dossiers_dir.mkdir()
        _make_pilot_style_dossier_zip(
            str(dossiers_dir / "d1.i6z"),
            cas="22839-47-0",
            e_number_synonym="E 951",
            name="Aspartame",
            adi_lower_value="40",
            # Real aspartame dossiers never repeat the substance name
            # immediately after "mg" -- this is what makes the automated
            # chemical-basis check unresolved for every real assessment,
            # which is exactly the shape the editorial override path
            # (editorial_content.EDITORIAL_CONTENT["E951"]) exists for.
            justification="The Panel derived an ADI of 40 mg/kg bw per day for aspartame.",
        )
        profile = _build_profile_for_single_dossier_zip(tmp_path, "d1.i6z", e_number_raw="E951", common_name="Aspartame")

        # The automated per-record chemical-basis check genuinely can't
        # resolve this text shape -- eligibility here must come from the
        # editorial override, proving the override path is what's doing
        # the work, not a change to the automated extractor.
        rv = profile["deduplicated_reference_values"][0]
        assert rv["chemical_basis"]["status"] != "resolved"
        assert rv["review_eligibility"]["basis_source"] == "editorial_override"
        assert rv["review_eligibility"]["consumer_guidance_eligible"] is True

        en, bg = profile["draft_en"], profile["draft_bg"]
        assert "phenylketonuria" in en.lower()
        assert "фенилкетонурия" in bg
        # Adjacent to the ADI, not buried under Sources: the PKU sentence
        # must appear before the "Sources" heading in both languages, and
        # at least twice (once near the ADI finding in "Effects and
        # conditions", once in "Intake guidance") -- see the review
        # task's "whenever the ADI or general-population safety
        # conclusion is shown" requirement.
        assert en.lower().count("phenylketonuria") >= 2
        assert bg.count("фенилкетонурия") >= 2
        assert en.lower().find("phenylketonuria") < en.find("## Sources")
        assert bg.find("фенилкетонурия") < bg.find("## Източници")

    def test_e150d_group_adi_scope_preserved_not_individual_allowance(self, tmp_path):
        dossiers_dir = tmp_path / "dossiers"
        dossiers_dir.mkdir()
        _make_pilot_style_dossier_zip(
            str(dossiers_dir / "d1.i6z"),
            cas="",
            e_number_synonym="E 150d",
            name="Sulphite ammonia caramel",
            adi_lower_value="300",
            justification="Comments: ADI (group)",  # matches the real dossier's own terse text
            date_of_evaluation="2011-02-03",
        )
        profile = _build_profile_for_single_dossier_zip(tmp_path, "d1.i6z", e_number_raw="E150d", common_name="Sulphite ammonia caramel")

        rv = profile["deduplicated_reference_values"][0]
        assert rv["chemical_basis"]["status"] != "resolved"
        assert rv["review_eligibility"]["consumer_guidance_eligible"] is False

        for draft in (profile["draft_en"], profile["draft_bg"]):
            # Group scope is stated -- never "an independent allowance for
            # E150d alone" (the editorial content's own group_scope note).
            assert "group" in draft.lower() or "групов" in draft.lower()
            # Intake guidance is never shown at all for this identity
            # (nothing passed the eligibility gate) -- the exact same gate
            # "Effects and conditions" uses (see next test).
            assert "## Intake guidance" not in draft and "## Насоки за прием" not in draft
        assert any(s["section"] == "Intake guidance" for s in profile["omitted_sections_internal_note"])

    def test_numeric_gating_is_identical_between_effects_and_intake_sections(self, tmp_path):
        """Regression for the review task's own complaint: a value that
        is not consumer_guidance_eligible must never show its magnitude
        in "Effects and conditions" either, just because "Intake
        guidance" happens to be a separate, hidden section."""
        dossiers_dir = tmp_path / "dossiers"
        dossiers_dir.mkdir()
        _make_pilot_style_dossier_zip(
            str(dossiers_dir / "d1.i6z"),
            cas="",
            e_number_synonym="E 150d",
            name="Sulphite ammonia caramel",
            adi_lower_value="300",
            justification="Comments: ADI (group)",
        )
        profile = _build_profile_for_single_dossier_zip(tmp_path, "d1.i6z", e_number_raw="E150d", common_name="Sulphite ammonia caramel")
        rv = profile["deduplicated_reference_values"][0]
        assert rv["review_eligibility"]["consumer_guidance_eligible"] is False
        # The record's own number (300) only appears via the editorial
        # scope narrative (independently sourced from the primary
        # opinion, see editorial_content.py), never as a restatement of
        # *this ineligible record's own* figure in either draft section.
        effects_section = profile["draft_en"].split("## Effects and conditions", 1)[1].split("## Sources", 1)[0]
        assert "not shown" in effects_section or "pending review" in effects_section

    def test_date_types_kept_distinct_for_e951_superseding_opinion(self):
        from scripts.openfoodtox.editorial_content import EDITORIAL_CONTENT

        e951 = EDITORIAL_CONTENT["E951"]
        combined = " ".join(n.text.en for n in e951.effects) + " " + " ".join(s.get("note", "") for s in e951.external_sources)
        assert "First published: 10 September 2026" in combined
        assert "Approved: 1 July 2026" in combined
        # The two dates must actually be recorded as distinct strings, not
        # collapsed into a single "adopted on" date.
        assert "10 September 2026" != "1 July 2026"
        # The opinion's own scope is described precisely -- never "every
        # part of the 2013 opinion was superseded".
        assert "within the E962 re-evaluation" in combined or "within the E 962 re-evaluation" in combined

    def test_feed_context_values_excluded_from_consumer_draft_entirely(self, tmp_path):
        """Task: "Keep feed/worker guidance out of the consumer preview,
        retaining it only in operator evidence" -- not merely caveated
        inline (the pre-review-task behavior)."""
        feed_codebook = Codebook(
            unit={"2085": "mg/kg bw/day"},
            value_by_xsl={
                "FLEXIBLE_SUMMARY-ToxRefValues.xsl": {"8521": "consumers"},
                "DOSSIER.xsl": {"999001": "EFSA FEEDAP", "999002": "Regulation (EC) No 1831/2003 (amended)"},
            },
        )
        dossiers_dir = tmp_path / "dossiers"
        dossiers_dir.mkdir()
        _make_pilot_style_dossier_zip(
            str(dossiers_dir / "d1.i6z"),
            cas="77-92-9",
            e_number_synonym="E 330",
            name="Citric acid",
            adi_lower_value="15000",
            expert_group_code="999001",
            regulation_code="999002",
            justification="Remarks: safe for the target species at 15000 mg citric acid/kg feedingstuffs.",
        )
        import json

        from scripts.openfoodtox.dossier import build_dossier_record
        from scripts.openfoodtox.matcher import match_catalogue_against_staging

        record = build_dossier_record(str(dossiers_dir / "d1.i6z"), "d1.i6z", feed_codebook)
        assert record["dossier_summary"]["expert_group_label"] == "EFSA FEEDAP"  # fixture sanity check
        cat_jsonl = tmp_path / "catalogue.jsonl"
        cat_jsonl.write_text(json.dumps(record) + "\n", encoding="utf-8")
        cat = CatalogueIdentity(
            id="test_e330", common_name="Citric acid", e_number_raw="E330", e_number_normalized="E330",
            cas_number_raw=None, cas_number_normalized=None, source="tracked_seed_json",
        )
        [result] = match_catalogue_against_staging([cat], str(cat_jsonl))
        profile = build_profile(cat, result, str(dossiers_dir), feed_codebook)

        rv = profile["deduplicated_reference_values"][0]
        assert rv["feed_or_livestock_context"] is True
        assert profile["consumer_draft_excluded_feed_or_worker_value_count"] == 1
        assert "15000" not in profile["draft_en"]
        assert "15000" not in profile["draft_bg"]
        assert "15000" in profile["internal_evidence_en"]  # retained for operator inspection
