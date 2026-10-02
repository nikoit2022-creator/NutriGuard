"""Tests for scripts/openfoodtox/codebook.py: harvesting IUCLID's own
code->label tables from the shipped .xsl stylesheets (never executing
them -- see the module docstring for the rationale).
"""

from __future__ import annotations

import zipfile

from scripts.openfoodtox.codebook import Codebook, harvest_codebook, _harvest_one_xsl

SAMPLE_XSL = """<?xml version="1.0"?>
<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" xmlns:i6="http://iuclid6.echa.europa.eu/namespaces/platform-fields/v1">
  <xsl:template match="Adi">
    <xsl:choose>
      <xsl:when test="./unitCode = '2085'"> mg/kg bw/day</xsl:when>
      <xsl:when test="./unitCode = '2081'"> mg/kg bw</xsl:when>
    </xsl:choose>
    <xsl:choose>
      <xsl:when test="./i6:value = '8520'">workers</xsl:when>
      <xsl:when test="./i6:value = '8521'">consumers</xsl:when>
    </xsl:choose>
  </xsl:template>
</xsl:stylesheet>"""


class TestHarvestOneXsl:
    def test_extracts_unit_and_value_codes(self):
        cb = Codebook()
        _harvest_one_xsl(cb, "TEST.xsl", SAMPLE_XSL)
        assert cb.unit["2085"] == "mg/kg bw/day"
        assert cb.unit["2081"] == "mg/kg bw"
        assert cb.value_by_xsl["TEST.xsl"]["8520"] == "workers"
        assert cb.value_by_xsl["TEST.xsl"]["8521"] == "consumers"
        assert cb.conflicts == []

    def test_conflicting_label_for_same_unit_code_is_flagged_not_overwritten(self):
        cb = Codebook()
        _harvest_one_xsl(cb, "A.xsl", "<xsl:when test=\"./unitCode = '2085'\"> mg/kg bw/day</xsl:when>")
        _harvest_one_xsl(cb, "B.xsl", "<xsl:when test=\"./unitCode = '2085'\"> a completely different unit</xsl:when>")
        assert cb.unit["2085"] == "mg/kg bw/day"  # first writer wins
        assert len(cb.conflicts) == 1
        assert cb.conflicts[0][0] == "unit"


class TestDecode:
    def test_decode_unit_is_global(self):
        cb = Codebook(unit={"2085": "mg/kg bw/day"})
        assert cb.decode_unit("2085") == "mg/kg bw/day"
        assert cb.decode_unit("999") is None
        assert cb.decode_unit(None) is None

    def test_decode_value_is_scoped_per_stylesheet(self):
        cb = Codebook(value_by_xsl={"A.xsl": {"1": "one"}, "B.xsl": {"1": "uno"}})
        assert cb.decode_value("A.xsl", "1") == "one"
        assert cb.decode_value("B.xsl", "1") == "uno"
        assert cb.decode_value("C.xsl", "1") is None  # unknown stylesheet: unresolved, not guessed

    def test_roundtrip_json(self):
        cb = Codebook(unit={"2085": "mg/kg bw/day"}, value_by_xsl={"A.xsl": {"1": "one"}}, source_xsl_files={"A.xsl"})
        restored = Codebook.from_json_dict(cb.to_json_dict())
        assert restored.unit == cb.unit
        assert restored.value_by_xsl == cb.value_by_xsl
        assert restored.source_xsl_files == cb.source_xsl_files


class TestHarvestCodebookFromArchives:
    def test_harvests_from_synthetic_archives(self, tmp_path):
        for i in range(2):
            archive = tmp_path / f"d{i}.i6z"
            with zipfile.ZipFile(str(archive), "w") as zf:
                zf.writestr("manifest.xml", b"<manifest/>")
                zf.writestr("FLEXIBLE_SUMMARY-ToxRefValues.xsl", SAMPLE_XSL.encode("utf-8"))
        cb = harvest_codebook(str(tmp_path))
        assert cb.unit["2085"] == "mg/kg bw/day"
        assert "FLEXIBLE_SUMMARY-ToxRefValues.xsl" in cb.source_xsl_files

    def test_does_not_crash_on_unreadable_archive(self, tmp_path):
        (tmp_path / "bad.i6z").write_bytes(b"not a zip")
        cb = harvest_codebook(str(tmp_path))
        assert isinstance(cb, Codebook)
