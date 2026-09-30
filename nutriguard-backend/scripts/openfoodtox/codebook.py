"""Harvest IUCLID picklist code -> label mappings from the dossiers' own
shipped presentation stylesheets.

Each ``.i6z`` archive bundles a set of ``.xsl`` stylesheets that IUCLID
uses to render its coded fields (``unitCode``, various ``value``
picklists) as human-readable text, e.g.::

    <xsl:when test="./unitCode = '2085'"> mg/kg bw/day</xsl:when>
    <xsl:when test="./i6:value = '2302'">rat</xsl:when>

This is authoritative, *in-dataset* reference data shipped by EFSA/
IUCLID alongside the records themselves — decoding a code by looking
it up here is not external enrichment and not a guess, it is reading a
lookup table that came with the dataset. We only ever regex-scan the
``.xsl`` bytes as text; we never load an XSLT engine or execute them
(see :mod:`scripts.openfoodtox.safe_io` for why that matters).

Scoping and a known limitation
-------------------------------
``unitCode`` is confirmed (by cross-checking many stylesheets) to be a
single global IUCLID unit vocabulary: the same code maps to the same
unit everywhere it appears, so unit codes are merged into one global
table and decoded with high confidence.

``value`` is heavily overloaded: the bare element name ``<value>`` is
reused by many unrelated picklists (species, sex, population,
literature type, GLP compliance, deviation, endpoint type, basis,
type-of-substance, ...). We therefore scope the ``value`` table
*per source stylesheet filename* rather than merging it globally, and
still flag it as best-effort: if a single stylesheet renders two
distinct ``value`` picklists whose numeric codes happen to overlap
(not observed in this dataset, but not disproven either), a decode
could pick the wrong label. Decoded ``value`` labels should be treated
as an aid, not ground truth — the raw numeric code is always preserved
alongside the decoded label so this can be checked.

One further self-describing pattern is handled *without* a codebook
lookup at all: several fields carry ``<value>1342</value>`` alongside
a sibling ``<other>free text</other>`` — code 1342 marks "the label is
the free-text sibling", so callers should prefer a sibling ``<other>``
element over any codebook lookup when both are present.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import safe_io

_WHEN_RE = re.compile(
    r"""xsl:when\s+test\s*=\s*"\./(?:i6:)?(unitCode|value)\s*=\s*'(\d+)'"\s*>([^<]*)""",
)

MAX_XSL_BYTES = 2 * 1024 * 1024  # stylesheets observed are all under ~50 KB


@dataclass
class Codebook:
    unit: dict = field(default_factory=dict)  # code -> label
    value_by_xsl: dict = field(default_factory=dict)  # xsl_filename -> {code: label}
    conflicts: list = field(default_factory=list)  # (kind, key, code, old_label, new_label)
    source_xsl_files: set = field(default_factory=set)

    def decode_unit(self, code: str | None) -> str | None:
        if code is None:
            return None
        return self.unit.get(str(code))

    def decode_value(self, xsl_filename: str | None, code: str | None) -> str | None:
        if code is None or xsl_filename is None:
            return None
        return self.value_by_xsl.get(xsl_filename, {}).get(str(code))

    def to_json_dict(self) -> dict:
        return {
            "unit": self.unit,
            "value_by_xsl": self.value_by_xsl,
            "conflicts": self.conflicts,
            "source_xsl_files": sorted(self.source_xsl_files),
        }

    @classmethod
    def from_json_dict(cls, d: dict) -> "Codebook":
        return cls(
            unit=d.get("unit", {}),
            value_by_xsl=d.get("value_by_xsl", {}),
            conflicts=d.get("conflicts", []),
            source_xsl_files=set(d.get("source_xsl_files", [])),
        )


def _harvest_one_xsl(cb: Codebook, xsl_filename: str, text: str) -> None:
    for field_name, code, label in _WHEN_RE.findall(text):
        label = label.strip()
        if not label:
            continue
        if field_name == "unitCode":
            existing = cb.unit.get(code)
            if existing is not None and existing != label:
                cb.conflicts.append(("unit", xsl_filename, code, existing, label))
                continue
            cb.unit[code] = label
        else:  # "value"
            table = cb.value_by_xsl.setdefault(xsl_filename, {})
            existing = table.get(code)
            if existing is not None and existing != label:
                cb.conflicts.append(("value", xsl_filename, code, existing, label))
                continue
            table[code] = label


def harvest_codebook(dossiers_dir: str, max_archives: int | None = None, progress_every: int = 2000) -> Codebook:
    """Scan ``.i6z`` archives under ``dossiers_dir`` for distinct ``.xsl``
    stylesheets and harvest their code tables.

    Stops re-parsing a given stylesheet filename once one instance has
    been harvested *and* records whether any later instance of the
    same filename differs in content, so silent drift across archives
    (e.g. a mid-dataset IUCLID version bump) is not swallowed.
    """
    import hashlib

    cb = Codebook()
    seen_hash_by_name: dict[str, str] = {}
    variant_warnings: list[str] = []
    n = 0
    for path in safe_io.iter_dossier_files(dossiers_dir):
        if max_archives is not None and n >= max_archives:
            break
        try:
            with safe_io.open_safe_zip(path) as zh:
                for name in zh.member_names:
                    if not name.endswith(".xsl"):
                        continue
                    try:
                        data = zh.read(name, max_bytes=MAX_XSL_BYTES)
                    except safe_io.UnsafeArchiveError:
                        continue
                    digest = hashlib.sha256(data).hexdigest()
                    if name in seen_hash_by_name:
                        if seen_hash_by_name[name] != digest:
                            variant_warnings.append(
                                f"{name}: content differs across archives (first seen hash "
                                f"{seen_hash_by_name[name][:12]}, now {digest[:12]} in {path})"
                            )
                        continue
                    seen_hash_by_name[name] = digest
                    cb.source_xsl_files.add(name)
                    text = data.decode("utf-8", errors="replace")
                    _harvest_one_xsl(cb, name, text)
        except Exception:
            continue
        n += 1
        if progress_every and n % progress_every == 0:
            print(f"  codebook harvest: scanned {n} archives, {len(seen_hash_by_name)} distinct stylesheets so far")
    cb.conflicts.extend(("variant", w, "", "", "") for w in variant_warnings)
    return cb
