"""End-to-end tests for scripts/openfoodtox/evidence_bundle.py against
small, synthetic, hand-built ``.i6z`` archives -- never the real
(non-git-tracked, multi-GB) OpenFoodTox dataset.
"""

from __future__ import annotations

import zipfile

from scripts.openfoodtox.catalogue_snapshot import CatalogueIdentity, normalize_e_number
from scripts.openfoodtox.evidence_bundle import build_profile
from scripts.openfoodtox.matcher import match_catalogue_against_staging

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
                 <JustificationAndComments>The Panel derived an ADI of {adi_lower_value} mg/kg bw per day for {name}.</JustificationAndComments>
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

        profile = build_profile(cat, result, str(dossiers_dir), None)
        assert profile["outcome"] == "ready_for_human_review"
        assert profile["statuses"]["scientific_review"] == "not_reviewed"
        assert profile["statuses"]["translation_review"] == "not_reviewed"
        assert len(profile["deduplicated_reference_values"]) == 1
        rv = profile["deduplicated_reference_values"][0]
        assert rv["lower_value"] == "0.1"
        assert "Sodium nitrite" in profile["draft_en"]
        assert "ЧЕРНОВА" in profile["draft_bg"]
        assert "0.1 mg sodium nitrite/kg bw per day" not in profile["draft_en"]  # never inventing text not in the source
        assert "0.1" in profile["draft_en"]
        # Intake guidance is never populated by this pilot (nothing has
        # been scientifically reviewed yet).
        assert "Intake guidance" not in profile["draft_en"]

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
