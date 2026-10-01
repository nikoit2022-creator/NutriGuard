"""Tests for scripts/openfoodtox/records.py: manifest/.i6d parsing and the
derived convenience views built on top of the generic raw-field walk.

Fixture XML below mirrors the real IUCLID document shapes confirmed by
manual inspection of the transferred dataset (namespaces, element
nesting, the ``value``/``other`` self-describing code pattern, and the
``i6:uuid`` attribute IUCLID puts on repeatable ``entry`` wrappers).
None of it is drawn from or resembles any specific real substance
record -- it is deliberately synthetic/minimal.
"""

from __future__ import annotations

from scripts.openfoodtox import records
from scripts.openfoodtox.codebook import Codebook


def _i6d(document_type: str, document_sub_type: str | None, content_inner: str, definition_version="9.0") -> bytes:
    sub_attr = f"<i6m:documentSubType>{document_sub_type}</i6m:documentSubType>" if document_sub_type else ""
    payload_tag = f"{document_type}.{document_sub_type}" if document_sub_type else document_type
    content_ns = f"http://iuclid6.echa.europa.eu/namespaces/{document_type}"
    if document_sub_type:
        content_ns += f"-{document_sub_type}"
    content_ns += f"/{definition_version}"
    xml = f"""<?xml version="1.0"?>
<i6c:Document xmlns:i6c="http://iuclid6.echa.europa.eu/namespaces/platform-container/v2">
  <i6c:PlatformMetadata xmlns:i6m="http://iuclid6.echa.europa.eu/namespaces/platform-metadata/v1">
    <i6m:documentKey>doc-key-1/dossier-1</i6m:documentKey>
    <i6m:name>Test document</i6m:name>
    <i6m:documentType>{document_type}</i6m:documentType>
    {sub_attr}
    <i6m:definitionVersion>{definition_version}</i6m:definitionVersion>
    <i6m:creationDate>2024-01-01T00:00:00Z</i6m:creationDate>
    <i6m:lastModificationDate>2024-01-01T00:00:00Z</i6m:lastModificationDate>
  </i6c:PlatformMetadata>
  <i6c:Content>
    <{payload_tag} xmlns="{content_ns}" xmlns:i6="http://iuclid6.echa.europa.eu/namespaces/platform-fields/v1">
      {content_inner}
    </{payload_tag}>
  </i6c:Content>
</i6c:Document>"""
    return xml.encode("utf-8")


class TestParseManifest:
    def test_parses_general_info_and_documents(self):
        xml = b"""<?xml version='1.0'?><manifest xmlns="http://iuclid6.echa.europa.eu/namespaces/manifest/v1" xmlns:xlink="http://www.w3.org/1999/xlink">
        <general-information>
          <title>IUCLID 6 container manifest file</title>
          <created>now</created>
          <application>IUCLID6</application>
          <submission-type>EFSA_CHEMICALS_DATABASE_SUB</submission-type>
          <archive-type>DOSSIER_DATA</archive-type>
          <legislations-info><legislation><id>efsa</id><version>2.0</version></legislation></legislations-info>
        </general-information>
        <base-document-uuid>u1/u1</base-document-uuid>
        <contained-documents>
          <document id="u1/u1">
            <type>DOSSIER</type><subtype>EFSA_CHEMICALS_DATABASE</subtype>
            <name xlink:type="simple" xlink:href="u1_u1.i6d">Some title</name>
            <uuid>u1/u1</uuid>
            <first-modification-date>2024-01-01T00:00:00Z</first-modification-date>
            <last-modification-date>2024-01-01T00:00:00Z</last-modification-date>
            <links><link><ref-uuid>u2/u1</ref-uuid><ref-type>DOSSIER_SUBJECT</ref-type></link></links>
          </document>
        </contained-documents>
        </manifest>"""
        parsed = records.parse_manifest(xml)
        assert parsed["general"]["submission_type"] == "EFSA_CHEMICALS_DATABASE_SUB"
        assert parsed["general"]["legislations"] == [{"id": "efsa", "version": "2.0"}]
        assert parsed["base_document_uuid"] == "u1/u1"
        assert len(parsed["documents"]) == 1
        doc = parsed["documents"][0]
        assert doc["type"] == "DOSSIER"
        assert doc["entry"] == "u1_u1.i6d"
        assert doc["links"] == [{"ref_uuid": "u2/u1", "ref_type": "DOSSIER_SUBJECT"}]


class TestSubstanceAndReferenceSubstance:
    def test_derive_substance(self):
        xml = _i6d(
            "SUBSTANCE",
            None,
            """
            <ChemicalName>Test Chemical</ChemicalName>
            <OwnerLegalEntity>le-1/dossier-1</OwnerLegalEntity>
            <ReferenceSubstance><ReferenceSubstance>rs-1/dossier-1</ReferenceSubstance></ReferenceSubstance>
            <TypeOfSubstance><Composition><value>2915</value></Composition></TypeOfSubstance>
            """,
        )
        doc = records.build_document_record("a.i6z", "e.i6d", xml, None)
        assert doc["document_type"] == "SUBSTANCE"
        assert doc["derived"]["chemical_name"] == "Test Chemical"
        assert doc["derived"]["owner_legal_entity_ref"] == "le-1/dossier-1"
        assert doc["derived"]["reference_substance_ref"] == "rs-1/dossier-1"
        assert doc["derived"]["type_of_substance_code"] == "2915"

    def test_derive_reference_substance_full_identity(self):
        xml = _i6d(
            "REFERENCE_SUBSTANCE",
            None,
            """
            <ReferenceSubstanceName>Sodium nitrite</ReferenceSubstanceName>
            <IupacName>sodium nitrite</IupacName>
            <Inventory>
              <InventoryEntry><entry><i6:inventoryCode>EC</i6:inventoryCode><i6:numberInInventory>231-555-9</i6:numberInInventory></entry></InventoryEntry>
              <CASNumber>7632-00-0</CASNumber>
            </Inventory>
            <Synonyms><Synonyms>
              <entry i6:uuid="u-1"><Identifier><value>1342</value><other>EFSA PARAM name</other></Identifier><Name>Sodium nitrite</Name></entry>
              <entry i6:uuid="u-2"><Identifier><value>4176</value></Identifier><Name>E 250</Name></entry>
            </Synonyms></Synonyms>
            <MolecularStructuralInfo><MolecularFormula>NNaO2</MolecularFormula><SmilesNotation>O=N[O-].[Na+]</SmilesNotation></MolecularStructuralInfo>
            """,
        )
        doc = records.build_document_record("a.i6z", "e.i6d", xml, None)
        d = doc["derived"]
        assert d["name"] == "Sodium nitrite"
        assert d["cas_number"] == "7632-00-0"
        assert d["ec_number"] == "231-555-9"
        assert d["e_numbers"]["recognized_count"] == 1
        assert d["e_numbers"]["candidates"][0]["normalized"] == "E250"
        assert "Sodium nitrite" in d["synonyms"]
        assert d["molecular_formula"] == "NNaO2"

    def test_missing_identifier_fields_are_none_not_fabricated(self):
        """A metabolite-style REFERENCE_SUBSTANCE with no CAS/EC populated
        (observed for real in the dataset) must surface None, never a
        guessed or empty-string placeholder presented as real data."""
        xml = _i6d(
            "REFERENCE_SUBSTANCE",
            None,
            """
            <ReferenceSubstanceName>Unregistered metabolite</ReferenceSubstanceName>
            <Inventory><CASNumber></CASNumber></Inventory>
            """,
        )
        doc = records.build_document_record("a.i6z", "e.i6d", xml, None)
        assert doc["derived"]["cas_number"] is None
        assert doc["derived"]["ec_number"] is None
        assert doc["derived"]["e_numbers"]["recognized_count"] == 0


class TestLegalEntityAndLiterature:
    def test_derive_legal_entity(self):
        xml = _i6d(
            "LEGAL_ENTITY",
            None,
            """<GeneralInfo><LegalEntityName>EFSA OpenFoodTox</LegalEntityName>
               <LegalEntityType><value>2912</value></LegalEntityType></GeneralInfo>""",
        )
        doc = records.build_document_record("a.i6z", "e.i6d", xml, None)
        assert doc["derived"]["name"] == "EFSA OpenFoodTox"
        assert doc["derived"]["legal_entity_type_code"] == "2912"

    def test_derive_literature_citation_source_not_overwritten_by_provenance(self):
        xml = _i6d(
            "LITERATURE",
            None,
            """<GeneralInfo><LiteratureType><value>1342</value><other>Regulatory Document</other></LiteratureType>
               <Name>Some opinion</Name><Author>EFSA ANS</Author><ReferenceYear>2017</ReferenceYear>
               <Source>doi:10.2903/example</Source><Remarks>Adoption date: X</Remarks></GeneralInfo>""",
        )
        doc = records.build_document_record("a.i6z", "e.i6d", xml, None)
        d = doc["derived"]
        assert d["citation_source"] == "doi:10.2903/example"
        assert d["author"] == "EFSA ANS"
        # The provenance envelope's own "source" key must remain the
        # archive/entry dict, never clobbered by/clobbering the citation.
        assert doc["source"]["archive"] == "a.i6z"
        assert isinstance(doc["source"], dict)


class TestReferenceValues:
    def test_adi_block_with_missing_optional_fields(self):
        xml = _i6d(
            "FLEXIBLE_SUMMARY",
            "ToxRefValues",
            """<HumanHealthHazardCharacteristics>
                 <AcceptableDailyIntake>
                   <Adi><unitCode>2085</unitCode><lowerValue>0.1</lowerValue></Adi>
                   <Population><value>8521</value></Population>
                   <OverallUncertainty>100.0</OverallUncertainty>
                   <CriticalEndpoint>ep-1/dossier-1</CriticalEndpoint>
                   <JustificationAndComments>Some justification.</JustificationAndComments>
                 </AcceptableDailyIntake>
               </HumanHealthHazardCharacteristics>""",
        )
        doc = records.build_document_record("a.i6z", "e.i6d", xml, None)
        rv = doc["derived"]["reference_values"]
        assert len(rv) == 1
        assert rv[0]["value_type"] == "ADI"
        assert rv[0]["lower_value"] == "0.1"
        assert rv[0]["upper_value"] is None  # not present in source -> None, not fabricated
        assert rv[0]["unit_code"] == "2085"
        assert rv[0]["critical_endpoint_ref"] == "ep-1/dossier-1"
        assert doc["parse_warnings"] == []

    def test_multiple_occurrences_of_same_container_flags_warning(self):
        """If a document ever contains more than one AcceptableDailyIntake
        block (not observed in the real dataset but not schema-forbidden
        either), the flattening extractor cannot safely disambiguate them
        -- it must flag this rather than silently merging two distinct
        reference values into one record."""
        xml = _i6d(
            "FLEXIBLE_SUMMARY",
            "ToxRefValues",
            """<HumanHealthHazardCharacteristics>
                 <AcceptableDailyIntake><Adi><unitCode>2085</unitCode><lowerValue>0.1</lowerValue></Adi></AcceptableDailyIntake>
                 <AcceptableDailyIntake><Adi><unitCode>2085</unitCode><lowerValue>0.2</lowerValue></Adi></AcceptableDailyIntake>
               </HumanHealthHazardCharacteristics>""",
        )
        doc = records.build_document_record("a.i6z", "e.i6d", xml, None)
        assert any("AcceptableDailyIntake" in w for w in doc["parse_warnings"])

    def test_codebook_decodes_unit_and_population(self):
        cb = Codebook(unit={"2085": "mg/kg bw/day"}, value_by_xsl={"FLEXIBLE_SUMMARY-ToxRefValues.xsl": {"8521": "consumers"}})
        xml = _i6d(
            "FLEXIBLE_SUMMARY",
            "ToxRefValues",
            """<HumanHealthHazardCharacteristics>
                 <AcceptableDailyIntake>
                   <Adi><unitCode>2085</unitCode><lowerValue>0.1</lowerValue></Adi>
                   <Population><value>8521</value></Population>
                 </AcceptableDailyIntake>
               </HumanHealthHazardCharacteristics>""",
        )
        doc = records.build_document_record("a.i6z", "e.i6d", xml, cb)
        rv = doc["derived"]["reference_values"][0]
        assert rv["unit_label"] == "mg/kg bw/day"
        assert rv["population_label"] == "consumers"
        assert rv["unit_label_source"] == "shipped_xsl_stylesheet"

    def test_unresolved_code_stays_unresolved_not_guessed(self):
        cb = Codebook(unit={}, value_by_xsl={})  # empty codebook: nothing to decode
        xml = _i6d(
            "FLEXIBLE_SUMMARY",
            "ToxRefValues",
            """<HumanHealthHazardCharacteristics>
                 <AcceptableDailyIntake><Adi><unitCode>999999</unitCode><lowerValue>1</lowerValue></Adi></AcceptableDailyIntake>
               </HumanHealthHazardCharacteristics>""",
        )
        doc = records.build_document_record("a.i6z", "e.i6d", xml, cb)
        rv = doc["derived"]["reference_values"][0]
        assert rv["unit_code"] == "999999"
        assert "unit_label" not in rv  # left unresolved, not guessed


class TestEndpointEffectLevels:
    def test_effect_levels_grouped_by_entry_uuid(self):
        """Regression test: the uuid identifying a repeatable Efflevel
        entry lives on the non-leaf <entry> wrapper element, not on any
        leaf. Grouping must not silently collapse to an empty list."""
        xml = _i6d(
            "ENDPOINT_STUDY_RECORD",
            "RepeatedDoseToxicityOther",
            """<ResultsAndDiscussion><EffectLevels>
                 <Efflevel>
                   <entry i6:uuid="uuid-1">
                     <Endpoint><value>1342</value><other>BMDL</other></Endpoint>
                     <EffectLevel><unitCode>2085</unitCode><lowerValue>9.63</lowerValue></EffectLevel>
                     <Basis><value>61018</value></Basis>
                   </entry>
                   <entry i6:uuid="uuid-2">
                     <Endpoint><value>1342</value><other>NOAEL</other></Endpoint>
                     <EffectLevel><unitCode>2085</unitCode><lowerValue>5.0</lowerValue></EffectLevel>
                   </entry>
                 </Efflevel>
               </EffectLevels></ResultsAndDiscussion>""",
        )
        doc = records.build_document_record("a.i6z", "e.i6d", xml, None)
        levels = doc["derived"]["effect_levels"]
        assert len(levels) == 2
        assert levels[0]["uuid"] == "uuid-1"
        assert levels[0]["endpoint_type_label"] == "BMDL"
        assert levels[0]["lower_value"] == "9.63"
        assert levels[1]["uuid"] == "uuid-2"
        assert levels[1]["endpoint_type_label"] == "NOAEL"
        assert levels[1]["lower_value"] == "5.0"

    def test_key_information_and_discussion_captured(self):
        xml = _i6d(
            "ENDPOINT_SUMMARY",
            "GeneticToxicity",
            """<KeyInformation><KeyInformation>Genotoxic: Negative</KeyInformation></KeyInformation>
               <Discussion><Discussion>Some discussion text.</Discussion></Discussion>""",
        )
        doc = records.build_document_record("a.i6z", "e.i6d", xml, None)
        assert doc["derived"]["key_information"] == "Genotoxic: Negative"
        assert doc["derived"]["discussion"] == "Some discussion text."
        assert doc["domain"] == "human_health"

    def test_additional_results_text_captured_generically(self):
        """Free-text results outside the EffectLevels/KeyInformation/
        Discussion patterns (e.g. BasicToxicokinetics's narrative result
        fields) must still surface in the derived view, not only in
        raw_fields."""
        xml = _i6d(
            "ENDPOINT_STUDY_RECORD",
            "BasicToxicokinetics",
            """<ResultsAndDiscussion>
                 <PharmacokineticStudies><DetailsOnDistribution>distribution site: liver</DetailsOnDistribution></PharmacokineticStudies>
               </ResultsAndDiscussion>""",
        )
        doc = records.build_document_record("a.i6z", "e.i6d", xml, None)
        texts = doc["derived"]["additional_results_text"]
        assert any(t["field"] == "DetailsOnDistribution" and "liver" in t["text"] for t in texts)


class TestDossierDocument:
    def test_derive_dossier(self):
        xml = _i6d(
            "DOSSIER",
            "EFSA_CHEMICALS_DATABASE",
            """<remarks>Publication date: 2011-07-22</remarks>
               <LiteratureReference>
                 <EFSAOutputTitle>Conclusion on X</EFSAOutputTitle>
                 <DateOfEvaluation>2011-07-13</DateOfEvaluation>
                 <LinkToPersistentIdentifier>doi:10.2903/j.efsa.2011.2323</LinkToPersistentIdentifier>
               </LiteratureReference>
               <DataSource><EFSAQuestionNumber>EFSA-Q-2010-01466</EFSAQuestionNumber></DataSource>""",
            definition_version="2.0",
        )
        doc = records.build_document_record("a.i6z", "e.i6d", xml, None)
        d = doc["derived"]
        assert d["output_title"] == "Conclusion on X"
        assert d["persistent_identifier"] == "doi:10.2903/j.efsa.2011.2323"
        assert d["efsa_question_number"] == "EFSA-Q-2010-01466"
        assert doc["domain"] == "assessment_summary"


class TestRawFieldsProvenance:
    def test_every_raw_field_has_a_path_and_source_is_attached_by_caller(self):
        xml = _i6d("SUBSTANCE", None, "<ChemicalName>X</ChemicalName>")
        doc = records.build_document_record("archive.i6z", "entry.i6d", xml, None)
        assert doc["source"] == {
            "archive": "archive.i6z",
            "entry": "entry.i6d",
            "document_key": "doc-key-1/dossier-1",
        }
        assert all("path" in f for f in doc["raw_fields"])

    def test_raw_fields_bounded_and_flags_truncation(self, monkeypatch):
        monkeypatch.setattr(records, "MAX_RAW_FIELDS_PER_DOCUMENT", 2)
        xml = _i6d("SUBSTANCE", None, "<ChemicalName>X</ChemicalName><OtherNames>Y</OtherNames><OwnerLegalEntity>Z</OwnerLegalEntity>")
        doc = records.build_document_record("a.i6z", "e.i6d", xml, None)
        assert doc["raw_fields_truncated"] is True
        assert len(doc["raw_fields"]) <= 2
        assert doc["evidence_complete"] is False
        assert any("may be incomplete" in w for w in doc["parse_warnings"])


class TestTruncationBoundaryAccuracy:
    """Regression coverage for the confirmed defect: the old
    implementation stopped the leaf walk exactly at the cap and treated
    "the running counter hit zero" as proof of truncation, which is
    wrong when a document has *exactly* that many leaves and nothing
    more (false positive) -- and separately gave no way to tell whether
    omitted leaves included fields derived views actually depend on.
    The fix measures the true, complete leaf count first and only then
    decides whether the cap had to cut anything."""

    def _xml_with_n_leaves(self, n: int) -> bytes:
        inner = "".join(f"<F{i}>v{i}</F{i}>" for i in range(n))
        return _i6d("SUBSTANCE", None, inner)

    def test_below_cap_not_truncated(self, monkeypatch):
        monkeypatch.setattr(records, "MAX_RAW_FIELDS_PER_DOCUMENT", 10)
        xml = self._xml_with_n_leaves(5)
        doc = records.build_document_record("a.i6z", "e.i6d", xml, None)
        assert doc["raw_fields_truncated"] is False
        assert doc["evidence_complete"] is True
        assert len(doc["raw_fields"]) == 5

    def test_exactly_at_cap_is_not_a_false_positive(self, monkeypatch):
        """The core regression: a document with exactly cap-many leaves
        and nothing beyond must NOT be flagged as truncated."""
        monkeypatch.setattr(records, "MAX_RAW_FIELDS_PER_DOCUMENT", 10)
        xml = self._xml_with_n_leaves(10)
        doc = records.build_document_record("a.i6z", "e.i6d", xml, None)
        assert doc["raw_fields_truncated"] is False
        assert doc["evidence_complete"] is True
        assert len(doc["raw_fields"]) == 10

    def test_one_above_cap_is_truncated_and_quarantined(self, monkeypatch):
        monkeypatch.setattr(records, "MAX_RAW_FIELDS_PER_DOCUMENT", 10)
        xml = self._xml_with_n_leaves(11)
        doc = records.build_document_record("a.i6z", "e.i6d", xml, None)
        assert doc["raw_fields_truncated"] is True
        assert doc["evidence_complete"] is False
        assert len(doc["raw_fields"]) == 10

    def test_critical_field_beyond_cap_is_what_truncation_actually_means(self, monkeypatch):
        """Demonstrates concretely what "incomplete" means: a field
        placed after the cap's worth of filler leaves is dropped from
        raw_fields, and the document is correctly flagged so that drop
        is never silently treated as "this substance has no such
        field"."""
        monkeypatch.setattr(records, "MAX_RAW_FIELDS_PER_DOCUMENT", 3)
        inner = "<Filler1>a</Filler1><Filler2>b</Filler2><Filler3>c</Filler3><ChemicalName>Critical</ChemicalName>"
        xml = _i6d("SUBSTANCE", None, inner)
        doc = records.build_document_record("a.i6z", "e.i6d", xml, None)
        assert doc["raw_fields_truncated"] is True
        assert doc["derived"]["chemical_name"] is None  # dropped by the cap
        assert doc["evidence_complete"] is False  # ...and this says so

    def test_safety_ceiling_is_independent_of_coverage_cap(self, monkeypatch):
        monkeypatch.setattr(records, "_SAFETY_CEILING_LEAVES", 5)
        monkeypatch.setattr(records, "MAX_RAW_FIELDS_PER_DOCUMENT", 1000)
        xml = self._xml_with_n_leaves(20)
        doc = records.build_document_record("a.i6z", "e.i6d", xml, None)
        assert doc["raw_fields_safety_ceiling_hit"] is True
        assert len(doc["raw_fields"]) <= 5
        assert any("safety ceiling" in w for w in doc["parse_warnings"])
