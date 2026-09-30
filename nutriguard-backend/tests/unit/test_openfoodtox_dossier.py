"""Tests for scripts/openfoodtox/dossier.py: assembling one archive into
a catalogue record, including corrupt-archive and unsafe-input handling.

Builds small synthetic ``.i6z``-shaped zip archives in-memory; never
touches the real (non-git-tracked, multi-GB) OpenFoodTox dataset.
"""

from __future__ import annotations

import zipfile

from scripts.openfoodtox.dossier import build_dossier_record

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

DOC_TMPL = """<document id="{uuid}"><type>{type}</type>{subtype}<name xlink:type="simple" xlink:href="{entry}">{name}</name><uuid>{uuid}</uuid></document>"""


def _substance_i6d() -> bytes:
    return b"""<?xml version="1.0"?>
<i6c:Document xmlns:i6c="http://iuclid6.echa.europa.eu/namespaces/platform-container/v2">
  <i6c:PlatformMetadata xmlns:i6m="http://iuclid6.echa.europa.eu/namespaces/platform-metadata/v1">
    <i6m:documentKey>rs-1/dossier-1</i6m:documentKey>
    <i6m:name>Test Substance</i6m:name>
    <i6m:documentType>REFERENCE_SUBSTANCE</i6m:documentType>
    <i6m:definitionVersion>9.0</i6m:definitionVersion>
  </i6c:PlatformMetadata>
  <i6c:Content>
    <REFERENCE_SUBSTANCE xmlns="http://iuclid6.echa.europa.eu/namespaces/REFERENCE_SUBSTANCE/9.0">
      <ReferenceSubstanceName>Test Substance</ReferenceSubstanceName>
      <Inventory><CASNumber>123-45-6</CASNumber></Inventory>
    </REFERENCE_SUBSTANCE>
  </i6c:Content>
</i6c:Document>"""


def _make_simple_dossier_zip(path: str, cas: str = "123-45-6", name: str = "Test Substance") -> None:
    manifest_doc = DOC_TMPL.format(uuid="rs-1/dossier-1", type="REFERENCE_SUBSTANCE", subtype="", entry="rs.i6d", name=name)
    manifest = MANIFEST_TMPL.format(documents=manifest_doc).encode("utf-8")
    i6d = f"""<?xml version="1.0"?>
<i6c:Document xmlns:i6c="http://iuclid6.echa.europa.eu/namespaces/platform-container/v2">
  <i6c:PlatformMetadata xmlns:i6m="http://iuclid6.echa.europa.eu/namespaces/platform-metadata/v1">
    <i6m:documentKey>rs-1/dossier-1</i6m:documentKey>
    <i6m:name>{name}</i6m:name>
    <i6m:documentType>REFERENCE_SUBSTANCE</i6m:documentType>
    <i6m:definitionVersion>9.0</i6m:definitionVersion>
  </i6c:PlatformMetadata>
  <i6c:Content>
    <REFERENCE_SUBSTANCE xmlns="http://iuclid6.echa.europa.eu/namespaces/REFERENCE_SUBSTANCE/9.0">
      <ReferenceSubstanceName>{name}</ReferenceSubstanceName>
      <Inventory><CASNumber>{cas}</CASNumber></Inventory>
    </REFERENCE_SUBSTANCE>
  </i6c:Content>
</i6c:Document>""".encode("utf-8")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.xml", manifest)
        zf.writestr("rs.i6d", i6d)


class TestValidDossier:
    def test_builds_record_with_identity(self, tmp_path):
        archive = tmp_path / "d1.i6z"
        _make_simple_dossier_zip(str(archive), cas="7632-00-0", name="Sodium nitrite")
        rec = build_dossier_record(str(archive), "d1.i6z", None)
        assert rec["status"] == "ok"
        assert rec["errors"] == []
        assert len(rec["identity"]["reference_substances"]) == 1
        assert rec["identity"]["reference_substances"][0]["cas_number"] == "7632-00-0"
        assert rec["counts"]["total_documents"] == 1


class TestCorruptAndUnsafeArchives:
    def test_not_a_zip_file(self, tmp_path):
        archive = tmp_path / "bad.i6z"
        archive.write_bytes(b"garbage, not a zip")
        rec = build_dossier_record(str(archive), "bad.i6z", None)
        assert rec["status"] == "unreadable_archive"
        assert rec["errors"]

    def test_missing_manifest(self, tmp_path):
        archive = tmp_path / "no_manifest.i6z"
        with zipfile.ZipFile(str(archive), "w") as zf:
            zf.writestr("something.i6d", b"<x/>")
        rec = build_dossier_record(str(archive), "no_manifest.i6z", None)
        assert rec["status"] == "missing_manifest"

    def test_path_traversal_member_rejected(self, tmp_path):
        archive = tmp_path / "evil.i6z"
        with zipfile.ZipFile(str(archive), "w") as zf:
            zf.writestr("manifest.xml", MANIFEST_TMPL.format(documents=""))
            zf.writestr("../../evil.txt", b"x")
        rec = build_dossier_record(str(archive), "evil.i6z", None)
        assert rec["status"] == "unreadable_archive"

    def test_manifest_with_doctype_rejected(self, tmp_path):
        archive = tmp_path / "xxe.i6z"
        evil_manifest = (
            b"<?xml version='1.0'?><!DOCTYPE manifest [<!ENTITY xxe SYSTEM 'file:///etc/passwd'>]>"
            b"<manifest xmlns='http://iuclid6.echa.europa.eu/namespaces/manifest/v1'>&xxe;</manifest>"
        )
        with zipfile.ZipFile(str(archive), "w") as zf:
            zf.writestr("manifest.xml", evil_manifest)
        rec = build_dossier_record(str(archive), "xxe.i6z", None)
        assert rec["status"] == "unsafe_or_malformed_manifest"

    def test_manifest_references_missing_entry_is_reported_not_crashed(self, tmp_path):
        archive = tmp_path / "dangling.i6z"
        doc = DOC_TMPL.format(uuid="rs-1/d1", type="REFERENCE_SUBSTANCE", subtype="", entry="missing.i6d", name="X")
        manifest = MANIFEST_TMPL.format(documents=doc).encode("utf-8")
        with zipfile.ZipFile(str(archive), "w") as zf:
            zf.writestr("manifest.xml", manifest)
        rec = build_dossier_record(str(archive), "dangling.i6z", None)
        assert rec["status"] == "ok"  # archive itself is fine, just missing one referenced entry
        assert any("missing entry" in e for e in rec["errors"])


class TestDuplicateIdentityAcrossDossiers:
    def test_same_cas_in_two_dossiers_is_not_merged(self, tmp_path):
        """Two separate archives assessing the same substance (same CAS)
        must remain two separate dossier records -- this module never
        merges across archives; that is the identity-audit's job, and it
        must not merge on name similarity either."""
        a1 = tmp_path / "a1.i6z"
        a2 = tmp_path / "a2.i6z"
        _make_simple_dossier_zip(str(a1), cas="7632-00-0", name="Sodium nitrite")
        _make_simple_dossier_zip(str(a2), cas="7632-00-0", name="Sodium nitrite")
        rec1 = build_dossier_record(str(a1), "a1.i6z", None)
        rec2 = build_dossier_record(str(a2), "a2.i6z", None)
        assert rec1["dossier_file"] != rec2["dossier_file"]
        assert rec1["identity"]["reference_substances"][0]["cas_number"] == rec2["identity"]["reference_substances"][0]["cas_number"]
        # Each record independently traces back to its own archive.
        assert rec1["identity"]["reference_substances"][0]["source"]["archive"] == "a1.i6z"
        assert rec2["identity"]["reference_substances"][0]["source"]["archive"] == "a2.i6z"
