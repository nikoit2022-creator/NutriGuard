"""Assemble one ``.i6z`` archive into a single catalogue record.

A dossier archive is *not* one substance: it is one EFSA/IUCLID
publication (a risk assessment conclusion, a re-evaluation opinion,
...) that references a primary substance plus supporting identity,
literature and endpoint records. This module reads the archive's
``manifest.xml`` and every ``*.i6d`` member it lists, and returns one
JSON-serializable dict per archive that groups those documents by role
(identity / endpoint / reference-value / literature / legal entity)
without asserting a 1:1 archive-to-substance mapping.
"""

from __future__ import annotations

from typing import Any

from . import records, safe_io
from .codebook import Codebook


def build_dossier_record(
    archive_path: str,
    archive_rel_path: str,
    codebook: Codebook | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "dossier_file": archive_rel_path,
        "status": "ok",
        "errors": [],
        "manifest": None,
        "documents": [],
        "identity": {},
        "dossier_summary": {},
        "reference_values": [],
        "endpoints": {"human_health": [], "environmental": [], "physicochemical": [],
                      "livestock_animal_health": [], "supporting": [], "unclassified": []},
        "literature": [],
        "legal_entities": [],
        "counts": {},
    }

    try:
        zh = safe_io.open_safe_zip(archive_path)
    except Exception as exc:  # BadZipFile, UnsafeArchiveError, OSError, ...
        result["status"] = "unreadable_archive"
        result["errors"].append(f"{type(exc).__name__}: {exc}")
        return result

    with zh:
        try:
            crc_bad = zh.zf.testzip()
        except Exception as exc:
            crc_bad = "testzip-raised"
            result["errors"].append(f"testzip error: {exc}")
        if crc_bad:
            result["status"] = "crc_failure"
            result["errors"].append(f"CRC check failed at member: {crc_bad}")
            return result

        if "manifest.xml" not in zh.member_names:
            result["status"] = "missing_manifest"
            return result

        try:
            manifest_bytes = zh.read("manifest.xml")
            manifest = records.parse_manifest(manifest_bytes)
        except (safe_io.UnsafeXMLError, safe_io.UnsafeArchiveError) as exc:
            result["status"] = "unsafe_or_malformed_manifest"
            result["errors"].append(str(exc))
            return result
        result["manifest"] = manifest["general"]
        result["base_document_uuid"] = manifest["base_document_uuid"]

        entry_by_href = {}
        for d in manifest["documents"]:
            if d["entry"]:
                entry_by_href[d["entry"]] = d

        doc_type_counts: dict[str, int] = {}
        domain_counts: dict[str, int] = {}
        incomplete_evidence_count = 0

        for entry_name, manifest_doc in entry_by_href.items():
            if entry_name not in zh.member_names:
                result["errors"].append(f"manifest references missing entry: {entry_name}")
                continue
            try:
                xml_bytes = zh.read(entry_name)
                doc = records.build_document_record(archive_rel_path, entry_name, xml_bytes, codebook)
            except (safe_io.UnsafeXMLError, safe_io.UnsafeArchiveError) as exc:
                result["errors"].append(f"{entry_name}: {exc}")
                continue

            doc["manifest_links"] = manifest_doc.get("links", [])
            doc["manifest_uuid"] = manifest_doc.get("uuid")
            result["documents"].append(doc)

            dt = doc["document_type"] or "UNKNOWN"
            doc_type_counts[dt] = doc_type_counts.get(dt, 0) + 1
            domain_counts[doc["domain"]] = domain_counts.get(doc["domain"], 0) + 1
            if not doc["evidence_complete"]:
                incomplete_evidence_count += 1

            if dt == "DOSSIER":
                result["dossier_summary"] = doc["derived"]
                result["title"] = doc["name"]
            elif dt == "SUBSTANCE":
                result["identity"].setdefault("substances", []).append(
                    {"document_key": doc["document_key"], "evidence_complete": doc["evidence_complete"], **doc["derived"], "source": doc["source"]}
                )
            elif dt == "REFERENCE_SUBSTANCE":
                result["identity"].setdefault("reference_substances", []).append(
                    {"document_key": doc["document_key"], "evidence_complete": doc["evidence_complete"], **doc["derived"], "source": doc["source"]}
                )
            elif dt == "LEGAL_ENTITY":
                result["legal_entities"].append(
                    {"document_key": doc["document_key"], "evidence_complete": doc["evidence_complete"], **doc["derived"], "source": doc["source"]}
                )
            elif dt == "LITERATURE":
                result["literature"].append(
                    {"document_key": doc["document_key"], "evidence_complete": doc["evidence_complete"], **doc["derived"], "source": doc["source"]}
                )
            elif dt == "FLEXIBLE_SUMMARY" and doc["document_sub_type"] == "ToxRefValues":
                for rv in doc["derived"].get("reference_values", []):
                    result["reference_values"].append({
                        **rv,
                        "evidence_complete": doc["evidence_complete"],
                        "source": doc["source"],
                        "parse_warnings": doc.get("parse_warnings", []),
                        # Carried through so a consumer can resolve which
                        # identity this reference value actually belongs to
                        # (see scripts/openfoodtox/matcher.py
                        # resolve_subject_links) rather than assuming a
                        # single identity per archive, which does not hold
                        # for every dossier (~3% have more than one
                        # REFERENCE_SUBSTANCE).
                        "manifest_uuid": doc.get("manifest_uuid"),
                        "manifest_links": doc.get("manifest_links", []),
                    })
            elif dt in ("ENDPOINT_SUMMARY", "ENDPOINT_STUDY_RECORD"):
                bucket = result["endpoints"].get(doc["domain"], result["endpoints"]["unclassified"])
                bucket.append(
                    {
                        "document_key": doc["document_key"],
                        "document_type": dt,
                        "document_sub_type": doc["document_sub_type"],
                        "name": doc["name"],
                        "evidence_complete": doc["evidence_complete"],
                        **doc["derived"],
                        "source": doc["source"],
                        "manifest_uuid": doc.get("manifest_uuid"),
                        "manifest_links": doc.get("manifest_links", []),
                    }
                )

        result["counts"] = {
            "documents_by_type": doc_type_counts,
            "documents_by_domain": domain_counts,
            "total_documents": len(result["documents"]),
            "documents_with_incomplete_evidence": incomplete_evidence_count,
        }

    return result
