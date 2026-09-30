"""Safe, bounded archive and XML handling for untrusted IUCLID (.i6z) input.

Threat model: the OpenFoodTox dossier archives originate from a third
party and are treated as untrusted input. This module applies four
concrete defenses, all enforced *before* any content is trusted or
parsed:

1. **No external entities / no entity expansion.** ``parse_safe_xml``
   refuses to parse any XML payload that contains a ``<!DOCTYPE`` or
   ``<!ENTITY`` declaration at all, rather than relying on a parser
   flag. Legitimate IUCLID ``manifest.xml`` / ``*.i6d`` documents never
   declare a DOCTYPE or custom entities (confirmed by inspection of the
   dataset), so this defeats both XXE (external file/network entity
   resolution) and "billion laughs" (internal entity expansion) at the
   source, independent of the underlying parser's own defaults.
2. **Bounded processing.** Every archive member has a per-entry size
   cap and every archive has a total-uncompressed-size cap, both
   checked from the zip *central directory* (``ZipInfo.file_size``)
   before any bytes are decompressed, plus a compression-ratio check
   to catch zip-bomb-style entries. Reads are additionally bounded at
   the point of use as a second check against a mismatched/forged
   central directory.
3. **No path traversal.** Every member name is validated to be a
   relative, non-parent-escaping path before it is ever used to build
   an output path. This module never calls ``ZipFile.extractall`` or
   ``ZipFile.extract`` on untrusted archives; callers read member
   bytes into memory via :func:`read_member_bytes` instead.
4. **No stylesheet execution.** The ``.xsl`` files shipped inside each
   dossier are IUCLID's presentation stylesheets. Nothing in this
   package (or the wider extraction toolkit built on it) loads an
   XSLT engine or executes them. ``.xsl`` bytes are only ever read as
   opaque text for the offline codebook-harvesting step
   (:mod:`scripts.openfoodtox.codebook`), which regex-scans them for
   literal ``xsl:when test="... = 'CODE'">Label`` reference-table
   entries and never evaluates the stylesheet.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterator

MAX_ENTRY_UNCOMPRESSED = 64 * 1024 * 1024        # 64 MB per zip entry
MAX_ARCHIVE_UNCOMPRESSED = 512 * 1024 * 1024     # 512 MB total per archive
MAX_COMPRESSION_RATIO = 300                      # flag suspicious zip-bomb-style ratios
MIN_SIZE_FOR_RATIO_CHECK = 1_000_000             # only ratio-check entries above 1 MB
MAX_XML_BYTES = MAX_ENTRY_UNCOMPRESSED

_DOCTYPE_RE = re.compile(rb"<!DOCTYPE", re.IGNORECASE)
_ENTITY_RE = re.compile(rb"<!ENTITY", re.IGNORECASE)


class UnsafeArchiveError(Exception):
    """Raised when an archive fails a safety check (not necessarily corrupt)."""


class UnsafeXMLError(Exception):
    """Raised when an XML payload fails a safety check or fails to parse."""


def is_safe_member_name(name: str) -> bool:
    """Reject absolute paths, drive letters, and any ``..`` path segment."""
    if not name or name.startswith("/") or name.startswith("\\"):
        return False
    if ":" in name.split("/")[0] and len(name.split("/")[0]) == 2:
        return False  # e.g. "C:\..."
    parts = PurePosixPath(name.replace("\\", "/")).parts
    if ".." in parts:
        return False
    return True


@dataclass
class SafeZipHandle:
    zf: zipfile.ZipFile
    path: str
    total_uncompressed: int
    member_names: list

    def read(self, name: str, max_bytes: int = MAX_ENTRY_UNCOMPRESSED) -> bytes:
        return read_member_bytes(self.zf, name, max_bytes=max_bytes)

    def close(self) -> None:
        self.zf.close()

    def __enter__(self) -> "SafeZipHandle":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def open_safe_zip(path: str) -> SafeZipHandle:
    """Open ``path`` as a zip archive and validate it before returning a handle.

    Validates every member's declared path and declared uncompressed
    size from the central directory, and the archive's total declared
    uncompressed size, *before* any member is decompressed. Raises
    :class:`UnsafeArchiveError` (validation failure) or
    ``zipfile.BadZipFile`` (structurally corrupt/not a zip) — callers
    should treat both as "this archive could not be processed" and
    keep going with the rest of the dataset.
    """
    zf = zipfile.ZipFile(path)
    try:
        total_uncompressed = 0
        names = []
        for info in zf.infolist():
            if info.is_dir():
                continue
            if not is_safe_member_name(info.filename):
                raise UnsafeArchiveError(f"unsafe member path: {info.filename!r}")
            if info.file_size > MAX_ENTRY_UNCOMPRESSED:
                raise UnsafeArchiveError(
                    f"entry too large: {info.filename!r} ({info.file_size} bytes)"
                )
            if info.file_size > MIN_SIZE_FOR_RATIO_CHECK and info.compress_size > 0:
                ratio = info.file_size / info.compress_size
                if ratio > MAX_COMPRESSION_RATIO:
                    raise UnsafeArchiveError(
                        f"suspicious compression ratio for {info.filename!r}: {ratio:.0f}x"
                    )
            total_uncompressed += info.file_size
            names.append(info.filename)
        if total_uncompressed > MAX_ARCHIVE_UNCOMPRESSED:
            raise UnsafeArchiveError(
                f"archive too large uncompressed: {total_uncompressed} bytes"
            )
    except Exception:
        zf.close()
        raise
    return SafeZipHandle(zf=zf, path=path, total_uncompressed=total_uncompressed, member_names=names)


def read_member_bytes(zf: zipfile.ZipFile, name: str, max_bytes: int = MAX_ENTRY_UNCOMPRESSED) -> bytes:
    """Read one member's bytes, re-checking its declared size and bounding the read."""
    info = zf.getinfo(name)
    if info.file_size > max_bytes:
        raise UnsafeArchiveError(f"entry too large to read: {name!r}")
    with zf.open(name) as f:
        data = f.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise UnsafeArchiveError(f"entry exceeded its declared size while reading: {name!r}")
    return data


def parse_safe_xml(data: bytes):
    """Parse XML bytes with :mod:`xml.etree.ElementTree`, refusing DOCTYPE/ENTITY.

    See the module docstring for the rationale. Raises
    :class:`UnsafeXMLError` for oversized input, a refused
    DOCTYPE/ENTITY declaration, or a parse failure.
    """
    import xml.etree.ElementTree as ET

    if len(data) > MAX_XML_BYTES:
        raise UnsafeXMLError(f"XML payload exceeds {MAX_XML_BYTES} byte bound")
    if _DOCTYPE_RE.search(data) or _ENTITY_RE.search(data):
        raise UnsafeXMLError("DOCTYPE/ENTITY declaration present; refusing to parse")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise UnsafeXMLError(f"XML parse error: {exc}") from exc


def iter_dossier_files(root: str) -> Iterator[str]:
    """Yield ``.i6z`` file paths under ``root``, recursively, in sorted order.

    Sorted, deterministic traversal so re-runs are reproducible and
    diffable. Does not follow symlinks.
    """
    import os

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for fn in sorted(filenames):
            if fn.lower().endswith(".i6z"):
                yield os.path.join(dirpath, fn)
