#!/usr/bin/env python3
"""Repeatable offline extraction/audit CLI for the transferred OpenFoodTox
IUCLID dossier archives.

This script only reads from ``--dossiers-dir`` (the transferred
originals) and writes to ``--staging-dir`` (machine-readable) and
``--reports-dir`` (human-readable). It never imports the live
NutriGuard application, never opens a database connection, and never
extracts archive contents to disk (all archive/XML reading goes
through :mod:`scripts.openfoodtox.safe_io`, in memory, with bounded
sizes). See ``docs/OPENFOODTOX_DATASET_AUDIT.md`` for full usage.

Subcommands (run in this order for a full audit)::

    python -m scripts.openfoodtox.extract inventory  --dossiers-dir ... --staging-dir ... --reports-dir ...
    python -m scripts.openfoodtox.extract codebook    --dossiers-dir ... --staging-dir ...
    python -m scripts.openfoodtox.extract catalogue   --dossiers-dir ... --staging-dir ... --reports-dir ...
    python -m scripts.openfoodtox.extract identity-audit --staging-dir ... --reports-dir ...
    python -m scripts.openfoodtox.extract e250        --staging-dir ... --reports-dir ...
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import zipfile
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.openfoodtox import safe_io  # noqa: E402
from scripts.openfoodtox.codebook import Codebook, harvest_codebook  # noqa: E402
from scripts.openfoodtox.dossier import build_dossier_record  # noqa: E402
from scripts.openfoodtox.records import MAX_RAW_FIELDS_PER_DOCUMENT  # noqa: E402

EXPECTED_FILE_COUNT = 11613
EXPECTED_TOTAL_BYTES = 1_100_741_293
HASH_CHUNK = 1024 * 1024


def _sha256_file(path: str) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        while True:
            chunk = f.read(HASH_CHUNK)
            if not chunk:
                break
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def cmd_inventory(args: argparse.Namespace) -> int:
    os.makedirs(args.staging_dir, exist_ok=True)
    os.makedirs(args.reports_dir, exist_ok=True)

    manifest_path = os.path.join(args.staging_dir, "inventory_manifest.jsonl")
    files = list(safe_io.iter_dossier_files(args.dossiers_dir))

    by_hash: dict[str, list[str]] = defaultdict(list)
    status_counts: Counter = Counter()
    total_bytes = 0
    t0 = time.time()

    with open(manifest_path, "w", encoding="utf-8") as out:
        for i, path in enumerate(files):
            rel = os.path.relpath(path, args.dossiers_dir)
            row: dict = {"relative_path": rel}
            try:
                digest, size = _sha256_file(path)
            except OSError as exc:
                row.update({"status": "unreadable", "error": str(exc)})
                status_counts["unreadable"] += 1
                out.write(json.dumps(row) + "\n")
                continue

            row["sha256"] = digest
            row["size_bytes"] = size
            total_bytes += size
            by_hash[digest].append(rel)

            status = "ok"
            error = None
            try:
                with safe_io.open_safe_zip(path) as zh:
                    bad = zh.zf.testzip()
                    if bad:
                        status = "crc_failure"
                        error = f"CRC check failed at member: {bad}"
                    elif "manifest.xml" not in zh.member_names:
                        status = "missing_manifest"
            except safe_io.UnsafeArchiveError as exc:
                status = "unsafe_archive"
                error = str(exc)
            except zipfile.BadZipFile as exc:
                status = "corrupt_or_unsupported"
                error = str(exc)
            except Exception as exc:  # defensive: never let one bad file kill the run
                status = "error"
                error = f"{type(exc).__name__}: {exc}"

            row["status"] = status
            if error:
                row["error"] = error
            status_counts[status] += 1
            out.write(json.dumps(row) + "\n")

            if args.progress_every and (i + 1) % args.progress_every == 0:
                print(f"  inventory: {i + 1}/{len(files)} files hashed, elapsed={time.time() - t0:.1f}s")

    duplicate_groups = {h: paths for h, paths in by_hash.items() if len(paths) > 1}

    summary = {
        "dossiers_dir": args.dossiers_dir,
        "files_found": len(files),
        "expected_file_count": EXPECTED_FILE_COUNT,
        "file_count_discrepancy": len(files) - EXPECTED_FILE_COUNT,
        "total_bytes": total_bytes,
        "expected_total_bytes": EXPECTED_TOTAL_BYTES,
        "byte_count_discrepancy": total_bytes - EXPECTED_TOTAL_BYTES,
        "status_counts": dict(status_counts),
        "duplicate_content_groups": len(duplicate_groups),
        "duplicate_files_total": sum(len(v) for v in duplicate_groups.values()),
        "elapsed_seconds": round(time.time() - t0, 1),
        "note": (
            "sha256 values here are computed locally, from the transferred copy under "
            "--dossiers-dir. This establishes a local integrity baseline only; it does not "
            "by itself prove end-to-end transfer fidelity against the original source unless "
            "compared against hashes computed at the source."
        ),
    }
    with open(os.path.join(args.reports_dir, "inventory_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    dup_path = os.path.join(args.reports_dir, "duplicate_archives.json")
    with open(dup_path, "w", encoding="utf-8") as f:
        json.dump(duplicate_groups, f, indent=2)

    report_lines = [
        "# OpenFoodTox dossier inventory & integrity report",
        "",
        f"- Dossiers directory: `{args.dossiers_dir}`",
        f"- Files found: {len(files)} (expected {EXPECTED_FILE_COUNT}, discrepancy {summary['file_count_discrepancy']:+d})",
        f"- Total bytes: {total_bytes} (expected {EXPECTED_TOTAL_BYTES}, discrepancy {summary['byte_count_discrepancy']:+d})",
        "",
        "## Status counts",
        "",
    ]
    for status, count in sorted(status_counts.items(), key=lambda kv: -kv[1]):
        report_lines.append(f"- {status}: {count}")
    report_lines += [
        "",
        f"## Duplicate content (exact sha256 match across different filenames): {len(duplicate_groups)} group(s)",
        "",
        "See `duplicate_archives.json` for the full listing." if duplicate_groups else "None found.",
        "",
        "## Verification scope",
        "",
        summary["note"],
    ]
    with open(os.path.join(args.reports_dir, "inventory_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines) + "\n")

    print(json.dumps(summary, indent=2))
    return 0


def cmd_codebook(args: argparse.Namespace) -> int:
    os.makedirs(args.staging_dir, exist_ok=True)
    cb = harvest_codebook(args.dossiers_dir, max_archives=args.max_archives)
    out_path = os.path.join(args.staging_dir, "codebook.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(cb.to_json_dict(), f, indent=2, sort_keys=True)
    print(f"codebook written to {out_path}")
    print(f"  unit codes: {len(cb.unit)}")
    print(f"  value codes: {sum(len(v) for v in cb.value_by_xsl.values())} across {len(cb.value_by_xsl)} stylesheets")
    print(f"  conflicts/variants flagged: {len(cb.conflicts)}")
    return 0


def _load_codebook(staging_dir: str) -> Codebook | None:
    path = os.path.join(staging_dir, "codebook.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return Codebook.from_json_dict(json.load(f))


def cmd_catalogue(args: argparse.Namespace) -> int:
    os.makedirs(args.staging_dir, exist_ok=True)
    os.makedirs(args.reports_dir, exist_ok=True)
    codebook = _load_codebook(args.staging_dir)
    if codebook is None:
        print("WARNING: no codebook.json in --staging-dir; run the 'codebook' subcommand first "
              "for decoded unit/value labels. Continuing with raw codes only.", file=sys.stderr)

    files = list(safe_io.iter_dossier_files(args.dossiers_dir))
    cat_path = os.path.join(args.staging_dir, "catalogue.jsonl")

    status_counts: Counter = Counter()
    doc_type_counts: Counter = Counter()
    domain_counts: Counter = Counter()
    cas_to_dossiers: dict[str, set] = defaultdict(set)
    ec_to_dossiers: dict[str, set] = defaultdict(set)
    name_to_cas: dict[str, set] = defaultdict(set)
    dossiers_with_usable_human_health = 0
    dossiers_with_any_incomplete_evidence = 0
    documents_with_incomplete_evidence_total = 0
    reference_substances_total = 0
    reference_substances_with_recognized_e_number = 0
    reference_substances_with_e_number_conflict = 0
    t0 = time.time()

    with open(cat_path, "w", encoding="utf-8") as out:
        for i, path in enumerate(files):
            rel = os.path.relpath(path, args.dossiers_dir)
            record = build_dossier_record(path, rel, codebook)
            status_counts[record["status"]] += 1

            for dt, c in record.get("counts", {}).get("documents_by_type", {}).items():
                doc_type_counts[dt] += c
            for dm, c in record.get("counts", {}).get("documents_by_domain", {}).items():
                domain_counts[dm] += c
            # Quarantine rule: a human-health endpoint only counts as
            # "usable" evidence if its own raw-field extraction was not
            # truncated (evidence_complete=True). A dossier with only
            # incomplete human-health records is not counted here.
            if any(e.get("evidence_complete") for e in record["endpoints"].get("human_health", [])):
                dossiers_with_usable_human_health += 1
            incomplete_here = record.get("counts", {}).get("documents_with_incomplete_evidence", 0)
            documents_with_incomplete_evidence_total += incomplete_here
            if incomplete_here:
                dossiers_with_any_incomplete_evidence += 1

            for rs in record.get("identity", {}).get("reference_substances", []):
                cas = rs.get("cas_number")
                ec = rs.get("ec_number")
                name = rs.get("name")
                if cas:
                    cas_to_dossiers[cas].add(rel)
                if ec:
                    ec_to_dossiers[ec].add(rel)
                if name and cas:
                    name_to_cas[name].add(cas)
                reference_substances_total += 1
                e_numbers = rs.get("e_numbers") or {}
                if e_numbers.get("recognized_count"):
                    reference_substances_with_recognized_e_number += 1
                if e_numbers.get("conflict"):
                    reference_substances_with_e_number_conflict += 1

            out.write(json.dumps(record, ensure_ascii=False) + "\n")

            if args.progress_every and (i + 1) % args.progress_every == 0:
                print(f"  catalogue: {i + 1}/{len(files)} archives processed, elapsed={time.time() - t0:.1f}s")

    conflicting_names = {n: sorted(c) for n, c in name_to_cas.items() if len(c) > 1}
    summary = {
        "archives_total": len(files),
        "status_counts": dict(status_counts),
        "documents_by_type": dict(doc_type_counts),
        "documents_by_domain": dict(domain_counts),
        "dossiers_with_usable_human_health_endpoint": dossiers_with_usable_human_health,
        "documents_with_incomplete_evidence_total": documents_with_incomplete_evidence_total,
        "dossiers_with_any_incomplete_evidence": dossiers_with_any_incomplete_evidence,
        "reference_substances_total": reference_substances_total,
        "reference_substances_with_recognized_e_number": reference_substances_with_recognized_e_number,
        "reference_substances_with_e_number_conflict": reference_substances_with_e_number_conflict,
        "unique_cas_numbers": len(cas_to_dossiers),
        "unique_ec_numbers": len(ec_to_dossiers),
        "cas_numbers_used_across_multiple_dossiers": {
            cas: sorted(dossiers) for cas, dossiers in cas_to_dossiers.items() if len(dossiers) > 1
        },
        "substance_names_mapped_to_multiple_cas_numbers": conflicting_names,
        "elapsed_seconds": round(time.time() - t0, 1),
    }
    with open(os.path.join(args.reports_dir, "catalogue_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, sort_keys=True)

    lines = [
        "# OpenFoodTox staging catalogue — summary",
        "",
        f"- Archives processed: {len(files)}",
        "",
        "## Archive parse status",
        "",
    ]
    for status, count in sorted(status_counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"- {status}: {count}")
    lines += [
        "",
        "## Documents by type",
        "",
    ]
    for dt, count in sorted(doc_type_counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"- {dt}: {count}")
    lines += [
        "",
        "## Documents by evidence domain",
        "",
    ]
    for dm, count in sorted(domain_counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"- {dm}: {count}")
    lines += [
        "",
        f"## Dossiers with at least one *usable* (non-truncated) human-health endpoint record: {dossiers_with_usable_human_health} / {len(files)}",
        "(a human-health endpoint record only counts here if its own raw-field extraction was not truncated -- see Coverage/completeness below)",
        "",
        "## Coverage / completeness (bounded extraction)",
        "",
        f"- Documents with truncated raw-field extraction (exceeded the {MAX_RAW_FIELDS_PER_DOCUMENT}-leaf coverage cap): {documents_with_incomplete_evidence_total}",
        f"- Dossiers containing at least one such truncated document: {dossiers_with_any_incomplete_evidence}",
        "- Truncated documents are excluded from the \"usable human-health endpoint\" count above and from every",
        "  derived summary that depends on completeness; they remain in the catalogue with their partial",
        "  raw_fields and an explicit `evidence_complete: false` flag for manual review.",
        "",
        "## E-number recognition (REFERENCE_SUBSTANCE synonym parsing)",
        "",
        f"- REFERENCE_SUBSTANCE records examined: {reference_substances_total}",
        f"- With at least one recognized E-number-shaped synonym: {reference_substances_with_recognized_e_number}",
        f"- With a flagged E-number conflict (multiple distinct candidates on one record): {reference_substances_with_e_number_conflict}",
        "",
        f"## Unique CAS numbers observed: {len(cas_to_dossiers)}",
        f"## Unique EC numbers observed: {len(ec_to_dossiers)}",
        "",
        f"## CAS numbers shared across multiple dossiers: {len(summary['cas_numbers_used_across_multiple_dossiers'])}",
        "(same substance assessed/re-assessed in more than one publication — not a data error; see identity-audit)",
        "",
        f"## Substance names mapped to more than one CAS number: {len(conflicting_names)}",
        "(potential naming collisions — flagged for manual review, not auto-merged; see identity-audit)",
    ]
    with open(os.path.join(args.reports_dir, "catalogue_summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def cmd_identity_audit(args: argparse.Namespace) -> int:
    os.makedirs(args.reports_dir, exist_ok=True)
    cat_path = os.path.join(args.staging_dir, "catalogue.jsonl")
    if not os.path.exists(cat_path):
        print(f"ERROR: {cat_path} not found; run the 'catalogue' subcommand first.", file=sys.stderr)
        return 1

    cas_to_dossiers: dict[str, set] = defaultdict(set)
    ec_to_dossiers: dict[str, set] = defaultdict(set)
    cas_to_names: dict[str, set] = defaultdict(set)
    name_to_cas: dict[str, set] = defaultdict(set)
    no_identifier_dossiers = []
    parse_error_dossiers = []

    with open(cat_path, encoding="utf-8") as f:
        for line in f:
            record = json.loads(line)
            if record["status"] != "ok":
                parse_error_dossiers.append({"dossier_file": record["dossier_file"], "status": record["status"], "errors": record.get("errors", [])})
                continue
            ref_subs = record.get("identity", {}).get("reference_substances", [])
            had_identifier = False
            for rs in ref_subs:
                cas, ec, name = rs.get("cas_number"), rs.get("ec_number"), rs.get("name")
                if cas:
                    cas_to_dossiers[cas].add(record["dossier_file"])
                    if name:
                        cas_to_names[cas].add(name)
                    had_identifier = True
                if ec:
                    ec_to_dossiers[ec].add(record["dossier_file"])
                    had_identifier = True
                if name and cas:
                    name_to_cas[name].add(cas)
            if not had_identifier:
                no_identifier_dossiers.append(record["dossier_file"])

    cas_multi_name = {cas: sorted(names) for cas, names in cas_to_names.items() if len(names) > 1}
    name_multi_cas = {name: sorted(cases) for name, cases in name_to_cas.items() if len(cases) > 1}
    cas_multi_dossier = {cas: sorted(d) for cas, d in cas_to_dossiers.items() if len(d) > 1}

    out = {
        "total_dossiers_with_parse_errors": len(parse_error_dossiers),
        "parse_error_dossiers": parse_error_dossiers,
        "dossiers_with_no_cas_or_ec_identifier": len(no_identifier_dossiers),
        "dossiers_with_no_cas_or_ec_identifier_sample": no_identifier_dossiers[:50],
        "cas_numbers_with_multiple_distinct_names": cas_multi_name,
        "names_mapped_to_multiple_cas_numbers": name_multi_cas,
        "cas_numbers_spanning_multiple_dossiers": cas_multi_dossier,
        "note": (
            "No automatic merging is performed here: same-CAS-different-dossier is treated as "
            "the same substance being assessed in more than one publication (expected); "
            "same-name-different-CAS or same-CAS-different-name are flagged for manual review "
            "only, never auto-resolved, per the audit's identity-safety requirement (never merge "
            "on name similarity, never conflate salts/mixtures/isomers)."
        ),
    }
    with open(os.path.join(args.reports_dir, "identity_audit.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, sort_keys=True)

    lines = [
        "# OpenFoodTox identity & duplication audit",
        "",
        f"- Dossiers with parse errors/corrupt archives: {len(parse_error_dossiers)}",
        f"- Dossiers with no CAS or EC identifier on any REFERENCE_SUBSTANCE record: {len(no_identifier_dossiers)}",
        f"- Distinct CAS numbers appearing in more than one dossier (re-assessment across publications, not a duplicate error): {len(cas_multi_dossier)}",
        f"- CAS numbers mapped to more than one distinct substance name (needs manual review): {len(cas_multi_name)}",
        f"- Substance names mapped to more than one CAS number (needs manual review): {len(name_multi_cas)}",
        "",
        "Full detail in `identity_audit.json`. No automatic merging was performed; ambiguous",
        "cases are listed for manual review, not resolved.",
    ]
    with open(os.path.join(args.reports_dir, "identity_audit.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(json.dumps({k: v for k, v in out.items() if not isinstance(v, list) or len(v) < 5}, indent=2, default=str))
    return 0


def cmd_e250(args: argparse.Namespace) -> int:
    os.makedirs(args.reports_dir, exist_ok=True)
    cat_path = os.path.join(args.staging_dir, "catalogue.jsonl")
    if not os.path.exists(cat_path):
        print(f"ERROR: {cat_path} not found; run the 'catalogue' subcommand first.", file=sys.stderr)
        return 1

    TARGET_CAS = "7632-00-0"
    TARGET_EC = "231-555-9"
    matches = []
    with open(cat_path, encoding="utf-8") as f:
        for line in f:
            record = json.loads(line)
            if record["status"] != "ok":
                continue
            ref_subs = record.get("identity", {}).get("reference_substances", [])
            hit = None
            for rs in ref_subs:
                if rs.get("cas_number") == TARGET_CAS or rs.get("ec_number") == TARGET_EC:
                    hit = rs
                    break
            if hit:
                matches.append((record, hit))

    profile = {
        "query": {
            "target_name": "sodium nitrite",
            "target_e_number": "E 250",
            "target_cas": TARGET_CAS,
            "target_ec": TARGET_EC,
        },
        "matched_dossiers": len(matches),
        "evidence_basis": (
            "Matched by exact CAS number (7632-00-0) and/or EC number (231-555-9) on a "
            "REFERENCE_SUBSTANCE record's Inventory block, cross-checked against the "
            "substance's own Synonyms list containing the literal string 'E 250'. "
            "This is an exact-identifier match, not a name-similarity guess."
        ),
        "dossiers": [],
    }

    for record, ref_sub in matches:
        human_health_endpoints = record["endpoints"].get("human_health", [])
        reference_values = record.get("reference_values") or []
        incomplete_parts = []
        if not ref_sub.get("evidence_complete", True):
            incomplete_parts.append("identity")
        if any(not rv.get("evidence_complete", True) for rv in reference_values):
            incomplete_parts.append("reference_values")
        if any(not ep.get("evidence_complete", True) for ep in human_health_endpoints):
            incomplete_parts.append("human_health_endpoints")
        dossier_entry = {
            "dossier_file": record["dossier_file"],
            "title": record.get("title"),
            "dossier_summary": record.get("dossier_summary"),
            "identity": ref_sub,
            "legal_entities": record.get("legal_entities"),
            "literature": record.get("literature"),
            "reference_values": reference_values,
            "human_health_endpoints": human_health_endpoints,
            "other_domain_endpoint_counts": {
                k: len(v) for k, v in record["endpoints"].items() if k != "human_health"
            },
            "incomplete_evidence_in": incomplete_parts,
        }
        profile["dossiers"].append(dossier_entry)

    json_path = os.path.join(args.reports_dir, "e250_sodium_nitrite_profile.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2, sort_keys=False)

    md = _render_e250_markdown(profile)
    md_path = os.path.join(args.reports_dir, "e250_sodium_nitrite_profile.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)

    print(f"E250/sodium nitrite: {len(matches)} matching dossier(s) found by exact CAS/EC match.")
    print(f"  {json_path}")
    print(f"  {md_path}")
    return 0


def _render_e250_markdown(profile: dict) -> str:
    lines = [
        "# E250 / sodium nitrite — source-linked example profile",
        "",
        "**Raw extracted evidence below is quoted/reproduced verbatim from the dataset.",
        "Any explanatory text is explicitly labeled as such — do not read the two as",
        "equivalent.**",
        "",
        f"Matched dossiers (exact CAS `{profile['query']['target_cas']}` / EC "
        f"`{profile['query']['target_ec']}` match): **{profile['matched_dossiers']}**",
        "",
        f"Evidence basis: {profile['evidence_basis']}",
        "",
    ]
    if not profile["dossiers"]:
        lines.append("No dossier in this dataset matched sodium nitrite / E250 by exact CAS/EC identifier.")
        return "\n".join(lines) + "\n"

    for d in profile["dossiers"]:
        lines.append(f"## Dossier: `{d['dossier_file']}`")
        lines.append("")
        if d.get("incomplete_evidence_in"):
            lines.append(f"**⚠ INCOMPLETE EVIDENCE in: {', '.join(d['incomplete_evidence_in'])} — see each item below, do not treat as fully usable without manual review.**")
            lines.append("")
        lines.append(f"- Title: {d.get('title')}")
        ds = d.get("dossier_summary") or {}
        if ds.get("persistent_identifier"):
            lines.append(f"- Reference/DOI: {ds['persistent_identifier']}")
        if ds.get("date_of_evaluation"):
            lines.append(f"- Date of evaluation: {ds['date_of_evaluation']}")
        if ds.get("remarks"):
            lines.append(f"- Remarks (raw): {ds['remarks']}")
        lines.append("")
        lines.append("### Identity (raw, from REFERENCE_SUBSTANCE)")
        idn = d["identity"]
        lines.append(f"- Name: {idn.get('name')}")
        lines.append(f"- IUPAC name: {idn.get('iupac_name')}")
        lines.append(f"- CAS number: {idn.get('cas_number')}")
        lines.append(f"- EC number: {idn.get('ec_number')}")
        e_numbers = idn.get("e_numbers") or {}
        recognized = [c for c in e_numbers.get("candidates", []) if c.get("recognized")]
        if recognized:
            shown = "; ".join(f"{c['raw']} (normalized: {c['normalized']})" for c in recognized)
            lines.append(f"- E-number synonym (recognized, raw + normalized): {shown}")
            if e_numbers.get("conflict"):
                lines.append("  - ⚠ multiple distinct E-number candidates found on this record — not auto-resolved")
        else:
            lines.append("- E-number synonym (recognized): none")
        lines.append(f"- Molecular formula: {idn.get('molecular_formula')}")
        lines.append(f"- SMILES: {idn.get('smiles')}")
        lines.append(f"- Source: `{idn['source']['archive']}` / `{idn['source']['entry']}`")
        lines.append("")

        if d.get("literature"):
            lines.append("### Literature reference (raw)")
            for lit in d["literature"]:
                lines.append(
                    f"- {lit.get('author')} ({lit.get('reference_year')}): {lit.get('name')} "
                    f"— {lit.get('citation_source')}"
                )
                if lit.get("remarks"):
                    lines.append(f"  - Remarks (raw): {lit['remarks']}")
            lines.append("")

        if d.get("reference_values"):
            lines.append("### Reference values / points of departure (raw, unconverted)")
            for rv in d["reference_values"]:
                unit = rv.get("unit_label") or f"unit code {rv.get('unit_code')}"
                val = rv.get("value") or rv.get("lower_value")
                lines.append(
                    f"- **{rv['value_type']}**: {val} {unit} "
                    f"(population: {rv.get('population_label') or rv.get('population_code')}, "
                    f"overall uncertainty factor: {rv.get('overall_uncertainty')})"
                )
                if not rv.get("evidence_complete", True):
                    lines.append("  - ⚠ INCOMPLETE EVIDENCE: this record's raw-field extraction was truncated; do not treat as usable evidence without manual review.")
                cb = rv.get("chemical_basis") or {}
                if cb.get("status") == "resolved":
                    lines.append(
                        f"  - Chemical basis (from this record's own justification text): **{cb['basis']}** "
                        f"(matched text: \"{cb['evidence']}\")"
                    )
                    for other in cb.get("other_values_mentioned", []):
                        lines.append(
                            f"    - Also mentioned in the same text, on a **different** basis -- kept separate, "
                            f"not merged/converted: {other['value']} mg {other['basis']}/kg bw "
                            f"(\"{other['evidence']}\")"
                        )
                elif cb.get("status", "").startswith("unresolved"):
                    lines.append(f"  - Chemical basis: **unresolved** ({cb.get('status')}) — not suitable for consumer intake guidance without checking the cited opinion directly.")
                    for other in cb.get("other_values_mentioned", []):
                        lines.append(f"    - Text mentions a different value/basis that did not match the stored figure: {other['value']} mg {other['basis']}/kg bw")
                if rv.get("justification_and_comments"):
                    lines.append(f"  - Justification (raw): {rv['justification_and_comments']}")
                if rv.get("parse_warnings"):
                    lines.append(f"  - ⚠ {'; '.join(rv['parse_warnings'])}")
            lines.append("")
        else:
            lines.append("### Reference values / points of departure")
            lines.append("None extracted for this dossier.")
            lines.append("")

        if d.get("human_health_endpoints"):
            lines.append("### Human-health endpoint findings (raw)")
            for ep in d["human_health_endpoints"]:
                lines.append(f"- **{ep['document_sub_type']}** — {ep.get('name')}")
                if not ep.get("evidence_complete", True):
                    lines.append("  - ⚠ INCOMPLETE EVIDENCE: this record's raw-field extraction was truncated; do not treat as usable evidence without manual review.")
                if ep.get("key_information"):
                    lines.append(f"  - Key information (raw): {ep['key_information']}")
                if ep.get("discussion"):
                    lines.append(f"  - Discussion (raw): {ep['discussion']}")
                for el in ep.get("effect_levels", []) or []:
                    unit = el.get("unit_label") or f"unit code {el.get('unit_code')}"
                    lines.append(
                        f"  - Effect level: {el.get('endpoint_type_label') or el.get('endpoint_type_code')} = "
                        f"{el.get('value') or el.get('lower_value')} {unit}"
                        + (f" (basis: {el.get('basis_label') or el.get('basis_code')})" if el.get("basis_code") else "")
                    )
                    if el.get("remarks_text"):
                        lines.append(f"    - Remarks (raw): {el['remarks_text']}")
            lines.append("")
        else:
            lines.append("### Human-health endpoint findings")
            lines.append("None extracted for this dossier.")
            lines.append("")

        other_counts = d.get("other_domain_endpoint_counts") or {}
        non_zero = {k: v for k, v in other_counts.items() if v}
        if non_zero:
            lines.append(f"### Other-domain records present but kept separate from human-health evidence: {non_zero}")
            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "**Explanatory summary (interpretation, not raw data):** the dataset represents "
        "sodium nitrite / E250 as a food-additive substance with an EFSA ANS Panel "
        "re-evaluation opinion (2017, doi above) that derives toxicological reference "
        "values (e.g. an ADI) from animal repeated-dose toxicity data using a benchmark-dose "
        "lower-confidence-limit (BMDL) approach and a default uncertainty factor. This "
        "profile does not convert any BMDL/reference value into a different unit or "
        "endpoint, does not infer regulatory approval status, and does not treat the "
        "absence of a field (e.g. a missing ARfD in this dataset) as evidence that no such "
        "value exists in the source EFSA opinion — only that this transferred IUCLID export "
        "does not carry it."
    )
    lines.append("")
    lines.append(
        "**External verification (NOT raw IUCLID extraction — a separate check against the "
        "actual cited opinion, done 2026-10-01):** the IUCLID `JustificationAndComments` text "
        "quoted above is a near-verbatim match of the source opinion's own sentence: "
        "\"Using the lowest BMDL of 9.63 mg/kg bw per day for males, and applying the default "
        "factor of 100, an ADI of 0.1 mg sodium nitrite/kg bw per day was calculated by the "
        "Panel, corresponding to 0.07 mg nitrite ion/kg bw per day\" (EFSA Journal 2017;15(6):4786, "
        "doi:10.2903/j.efsa.2017.4786). The Panel calculates and states *both* figures "
        "explicitly as two chemical-basis expressions of the one BMDL-derived ADI -- they are "
        "not conflicting values and this audit does not treat them as such; this extractor "
        "resolves `chemical_basis` to \"sodium nitrite\" for the stored 0.1 figure and keeps "
        "the 0.07 mg nitrite ion/kg bw figure as a separate, clearly labeled mention for "
        "exactly that reason, never merging or converting between them. Some third-party "
        "summaries of this opinion paraphrase the ADI as \"0.07 mg nitrite ion/kg bw/day\" "
        "alone (likely because that basis is what aggregate nitrite-exposure assessments "
        "compare against); readers relying on this profile for anything beyond this audit's "
        "own scope should consult the primary opinion directly rather than either paraphrase. "
        "Sources consulted: https://efsa.europa.eu/en/efsajournal/pub/4786 (official EFSA "
        "Journal record) and https://pmc.ncbi.nlm.nih.gov/articles/PMC7009987 (open-access "
        "full text, fetched 2026-10-01 to confirm the exact sentence quoted above)."
    )
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    common_out = argparse.ArgumentParser(add_help=False)
    common_out.add_argument("--staging-dir", required=True, help="Machine-readable output directory (outside git)")

    inv = sub.add_parser("inventory", parents=[common_out], help="Hash + integrity-check every archive")
    inv.add_argument("--dossiers-dir", required=True)
    inv.add_argument("--reports-dir", required=True)
    inv.add_argument("--progress-every", type=int, default=2000)
    inv.set_defaults(func=cmd_inventory)

    cb = sub.add_parser("codebook", parents=[common_out], help="Harvest code->label tables from shipped .xsl stylesheets")
    cb.add_argument("--dossiers-dir", required=True)
    cb.add_argument("--max-archives", type=int, default=None)
    cb.set_defaults(func=cmd_codebook)

    cat = sub.add_parser("catalogue", parents=[common_out], help="Extract the full searchable JSONL catalogue")
    cat.add_argument("--dossiers-dir", required=True)
    cat.add_argument("--reports-dir", required=True)
    cat.add_argument("--progress-every", type=int, default=1000)
    cat.set_defaults(func=cmd_catalogue)

    ida = sub.add_parser("identity-audit", parents=[common_out], help="Identity/duplication audit from the catalogue")
    ida.add_argument("--reports-dir", required=True)
    ida.set_defaults(func=cmd_identity_audit)

    e250 = sub.add_parser("e250", parents=[common_out], help="Build the sodium nitrite / E250 example profile")
    e250.add_argument("--reports-dir", required=True)
    e250.set_defaults(func=cmd_e250)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
