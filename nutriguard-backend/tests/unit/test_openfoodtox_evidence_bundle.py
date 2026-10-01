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


def _make_pilot_style_dossier_zip(path: str, *, cas: str, e_number_synonym: str, name: str, adi_lower_value: str, dossier_uuid: str = "dossier-1") -> None:
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

    dossier_xml = _doc_xml(
        dossier_uuid,
        f"Opinion on {name}",
        "DOSSIER",
        f"""<DOSSIER xmlns="http://iuclid6.echa.europa.eu/namespaces/DOSSIER/9.0">
             <LiteratureReference><EFSAOutputTitle>Opinion on {name}</EFSAOutputTitle><DateOfEvaluation>2015-01-01</DateOfEvaluation></LiteratureReference>
             <Domain><FoodDomain><value other="food additives">119227</value></FoodDomain><Regulation><value other="Regulation (EC) No 1331/2008">133164</value></Regulation><ExpertGroup><value other="EFSA ANS">133185</value></ExpertGroup></Domain>
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
                 <JustificationAndComments>The Panel derived an ADI of {adi_lower_value} mg {name}/kg bw per day.</JustificationAndComments>
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
        # Intake guidance is never populated by this pilot (nothing has
        # been scientifically reviewed yet), even though this value is
        # consumer_guidance_eligible in shape.
        assert "Intake guidance" not in profile["draft_en"]
        assert any(s["section"] == "Intake guidance" for s in profile["omitted_sections_internal_note"])

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
