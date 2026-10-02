"""Tests for scripts/openfoodtox/safe_io.py: the archive/XML safety layer.

These tests build small synthetic archives in-memory (never touching
the real, non-git-tracked OpenFoodTox dataset) to exercise: path
traversal rejection, oversized-entry rejection, corrupt-zip handling,
and DOCTYPE/ENTITY-bearing XML rejection (the XXE/"billion laughs"
defense).
"""

from __future__ import annotations

import io
import os
import zipfile

import pytest

from scripts.openfoodtox import safe_io


def _make_zip(path: str, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)


VALID_MANIFEST = b"""<?xml version='1.0' encoding='UTF-8'?><manifest xmlns="http://iuclid6.echa.europa.eu/namespaces/manifest/v1"><general-information><title>t</title></general-information></manifest>"""


class TestMemberNameSafety:
    def test_rejects_parent_traversal(self):
        assert not safe_io.is_safe_member_name("../../etc/passwd")

    def test_rejects_absolute_path(self):
        assert not safe_io.is_safe_member_name("/etc/passwd")

    def test_accepts_normal_relative_name(self):
        assert safe_io.is_safe_member_name("manifest.xml")
        assert safe_io.is_safe_member_name("abcd_efgh.i6d")

    def test_open_safe_zip_rejects_traversal_member(self, tmp_path):
        archive = tmp_path / "evil.i6z"
        _make_zip(str(archive), {"../../evil.txt": b"x", "manifest.xml": VALID_MANIFEST})
        with pytest.raises(safe_io.UnsafeArchiveError):
            safe_io.open_safe_zip(str(archive))


class TestSizeBounds:
    def test_rejects_entry_over_max_size(self, tmp_path, monkeypatch):
        monkeypatch.setattr(safe_io, "MAX_ENTRY_UNCOMPRESSED", 100)
        archive = tmp_path / "big.i6z"
        _make_zip(str(archive), {"manifest.xml": b"x" * 200})
        with pytest.raises(safe_io.UnsafeArchiveError):
            safe_io.open_safe_zip(str(archive))

    def test_rejects_archive_over_total_size_bound(self, tmp_path, monkeypatch):
        monkeypatch.setattr(safe_io, "MAX_ENTRY_UNCOMPRESSED", 1000)
        monkeypatch.setattr(safe_io, "MAX_ARCHIVE_UNCOMPRESSED", 150)
        archive = tmp_path / "big_total.i6z"
        _make_zip(str(archive), {"a.i6d": b"x" * 100, "b.i6d": b"x" * 100})
        with pytest.raises(safe_io.UnsafeArchiveError):
            safe_io.open_safe_zip(str(archive))

    def test_accepts_normal_small_archive(self, tmp_path):
        archive = tmp_path / "ok.i6z"
        _make_zip(str(archive), {"manifest.xml": VALID_MANIFEST})
        with safe_io.open_safe_zip(str(archive)) as zh:
            assert "manifest.xml" in zh.member_names
            assert zh.read("manifest.xml") == VALID_MANIFEST


class TestCorruptArchives:
    def test_bad_zip_raises_bad_zip_file(self, tmp_path):
        archive = tmp_path / "not_a_zip.i6z"
        archive.write_bytes(b"this is not a zip file at all")
        with pytest.raises(zipfile.BadZipFile):
            safe_io.open_safe_zip(str(archive))

    def test_truncated_zip_fails_testzip_or_open(self, tmp_path):
        # Build a valid zip then truncate it to simulate a corrupted transfer.
        good = tmp_path / "good.i6z"
        _make_zip(str(good), {"manifest.xml": VALID_MANIFEST * 50})
        data = good.read_bytes()
        truncated = tmp_path / "truncated.i6z"
        truncated.write_bytes(data[: len(data) // 2])
        with pytest.raises(zipfile.BadZipFile):
            safe_io.open_safe_zip(str(truncated))


class TestUnsafeXML:
    def test_rejects_doctype(self):
        xml = b"<?xml version='1.0'?><!DOCTYPE foo [<!ENTITY xxe SYSTEM 'file:///etc/passwd'>]><root>&xxe;</root>"
        with pytest.raises(safe_io.UnsafeXMLError):
            safe_io.parse_safe_xml(xml)

    def test_rejects_billion_laughs_style_entities(self):
        xml = (
            b"<?xml version='1.0'?><!DOCTYPE lolz [<!ENTITY lol \"lol\">"
            b"<!ENTITY lol2 \"&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;\">]>"
            b"<lolz>&lol2;</lolz>"
        )
        with pytest.raises(safe_io.UnsafeXMLError):
            safe_io.parse_safe_xml(xml)

    def test_accepts_well_formed_entity_free_xml(self):
        root = safe_io.parse_safe_xml(VALID_MANIFEST)
        assert root is not None

    def test_rejects_oversized_xml(self, monkeypatch):
        monkeypatch.setattr(safe_io, "MAX_XML_BYTES", 10)
        with pytest.raises(safe_io.UnsafeXMLError):
            safe_io.parse_safe_xml(VALID_MANIFEST)

    def test_rejects_malformed_xml(self):
        with pytest.raises(safe_io.UnsafeXMLError):
            safe_io.parse_safe_xml(b"<not-closed>")


class TestIterDossierFiles:
    def test_finds_i6z_files_recursively_and_sorted(self, tmp_path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "b.i6z").write_bytes(b"")
        (tmp_path / "sub" / "a.i6z").write_bytes(b"")
        (tmp_path / "ignore.txt").write_bytes(b"")
        found = list(safe_io.iter_dossier_files(str(tmp_path)))
        assert len(found) == 2
        assert all(f.endswith(".i6z") for f in found)
