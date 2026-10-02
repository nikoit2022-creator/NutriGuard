#!/usr/bin/env python3
"""Offline, dry-run pilot: match a NutriGuard catalogue snapshot against
the already-staged OpenFoodTox catalogue and build evidence-bundle
drafts for any exact matches. See docs/OPENFOODTOX_PILOT_TASK.md.

Reads ``<staging-dir>/catalogue.jsonl`` (already built by
``scripts.openfoodtox.extract catalogue``) for identity matching, and
the original ``--dossiers-dir`` archives (read-only) to freshly
re-extract full evidence for any exact match -- so exact-match evidence
always reflects the current extraction code, never a possibly-stale
staged snapshot. Writes a machine-readable summary JSON plus one
JSON+Markdown evidence-bundle pair per exact match into
``--output-dir``. Never writes outside ``--output-dir``, never imports
the live NutriGuard application, never opens a database connection,
never fetches anything over the network.

Usage::

    python -m scripts.openfoodtox.pilot \\
        --dossiers-dir /path/to/dossiers \\
        --staging-dir /path/to/staging/v3 \\
        --output-dir /path/to/pilot/v1 \\
        --e-numbers E250 E150d E330 E951
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.openfoodtox.catalogue_snapshot import (  # noqa: E402
    CatalogueIdentity,
    build_snapshot_from_tracked_seed,
    load_supplied_snapshot,
    normalize_e_number,
)
from scripts.openfoodtox.codebook import Codebook  # noqa: E402
from scripts.openfoodtox.evidence_bundle import build_profile  # noqa: E402
from scripts.openfoodtox.matcher import match_catalogue_against_staging  # noqa: E402
from scripts.openfoodtox.provenance import git_fingerprint  # noqa: E402
from scripts.openfoodtox.records import EXTRACTION_LOGIC_VERSION, MAX_RAW_FIELDS_PER_DOCUMENT  # noqa: E402

_HASH_CHUNK = 1024 * 1024


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_HASH_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _staging_producing_code(staging_dir: str) -> dict | None:
    """The producing-code fingerprint the staged catalogue.jsonl itself
    was generated with (see extract.py cmd_catalogue's own
    catalogue_summary.json), so a pilot run can show whether its own
    code matches what built the staging it reads identity fields from,
    not just its own current state. Follows this dataset's own
    established convention of versioned sibling directories
    (``staging/vN/`` / ``reports/vN/`` under the same root) rather than
    assuming any fixed relative path -- returns ``None`` (not a guess)
    when that convention doesn't resolve to an existing summary file."""
    staging_dir = os.path.normpath(staging_dir)
    version = os.path.basename(staging_dir)
    root = os.path.dirname(os.path.dirname(staging_dir))
    path = os.path.join(root, "reports", version, "catalogue_summary.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {
        "extraction_logic_version": data.get("extraction_logic_version"),
        "producing_code": data.get("producing_code"),
        "source_file": path,
    }


def _load_codebook(staging_dir: str) -> Codebook | None:
    path = os.path.join(staging_dir, "codebook.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return Codebook.from_json_dict(json.load(f))


def run_pilot(
    *, dossiers_dir: str, staging_dir: str, output_dir: str, e_numbers: list[str], supplied_snapshot: str | None
) -> dict:
    os.makedirs(output_dir, exist_ok=True)
    catalogue_jsonl = os.path.join(staging_dir, "catalogue.jsonl")
    if not os.path.exists(catalogue_jsonl):
        raise SystemExit(f"ERROR: {catalogue_jsonl} not found; run 'extract catalogue' first.")

    codebook = _load_codebook(staging_dir)

    tracked = build_snapshot_from_tracked_seed()
    supplied = load_supplied_snapshot(supplied_snapshot) if supplied_snapshot else []
    full_catalogue = tracked + supplied

    requested_norm = {(normalize_e_number(e) or e.strip().upper()): e for e in e_numbers}
    by_norm: dict[str, CatalogueIdentity] = {}
    for c in full_catalogue:
        if c.e_number_normalized in requested_norm and c.e_number_normalized not in by_norm:
            by_norm[c.e_number_normalized] = c

    query_entries: list[CatalogueIdentity] = []
    adhoc_entries: list[str] = []  # requested E-numbers with no tracked/supplied catalogue entry
    for norm, original in requested_norm.items():
        if norm in by_norm:
            query_entries.append(by_norm[norm])
        else:
            adhoc_entries.append(original)
            query_entries.append(
                CatalogueIdentity(
                    id=f"adhoc_query:{norm}",
                    common_name=f"(ad hoc query, not a provisioned NutriGuard ingredient: {original})",
                    e_number_raw=original,
                    e_number_normalized=norm,
                    cas_number_raw=None,
                    cas_number_normalized=None,
                    source="adhoc_query_not_in_tracked_catalogue",
                )
            )

    results = match_catalogue_against_staging(query_entries, catalogue_jsonl)

    profiles_written = []
    for result in results:
        entry = result.catalogue_identity
        safe_id = entry.id.replace(":", "_").replace("/", "_")
        if result.overall_status == "exact_match":
            profile = build_profile(entry, result, dossiers_dir, codebook)
            json_path = os.path.join(output_dir, f"{safe_id}_profile.json")
            md_path = os.path.join(output_dir, f"{safe_id}_profile.md")
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(profile, f, indent=2, sort_keys=False, ensure_ascii=False)
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(
                    "# EN draft (paraphrase, DRAFT, not reviewed)\n\n"
                    + profile["draft_en"]
                    + "\n\n---\n\n# BG draft (paraphrase, DRAFT, not reviewed)\n\n"
                    + profile["draft_bg"]
                    + "\n\n---\n\n"
                    + profile["internal_evidence_en"]
                    + "\n"
                )
            profiles_written.append({"id": entry.id, "json": json_path, "md": md_path, "outcome": profile["outcome"]})

    pilot_producing_code = git_fingerprint()
    staging_producing_code = _staging_producing_code(staging_dir)
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_sha": pilot_producing_code.get("git_sha"),  # kept for backward compatibility with pilot/v1's field name
        "inputs": {
            "dossiers_dir": dossiers_dir,
            "staging_dir": staging_dir,
            "catalogue_jsonl": catalogue_jsonl,
            "catalogue_jsonl_sha256": _sha256_file(catalogue_jsonl),
            "supplied_snapshot": supplied_snapshot,
            "requested_e_numbers": e_numbers,
            "pilot_code_extraction_logic_version": EXTRACTION_LOGIC_VERSION,
            "pilot_producing_code": pilot_producing_code,
            "staging_catalogue_producing_code": staging_producing_code,
            "extraction_schema_version": {
                "max_raw_fields_per_document": MAX_RAW_FIELDS_PER_DOCUMENT,
                "note": (
                    "Identity matching (CAS/EC/E-number) read the identity fields already staged in "
                    "catalogue_jsonl above -- see staging_catalogue_producing_code for exactly what code "
                    "produced that file, which may differ from pilot_producing_code (this run's own code) "
                    "if the staging catalogue predates a later fix. Every exact match's reference-value/"
                    "endpoint evidence below was freshly re-extracted directly from the original archive "
                    "using *this run's* code (pilot_producing_code), not read from the staged snapshot -- "
                    "see docs/OPENFOODTOX_PILOT_REPORT.md for the full account."
                ),
            },
        },
        "requested_e_numbers_not_in_tracked_or_supplied_catalogue": adhoc_entries,
        "coverage_label": "seed/supplied-snapshot coverage only -- NOT production database coverage",
        "match_results": [r.to_dict() for r in results],
        "profiles_written": profiles_written,
    }
    summary_path = os.path.join(output_dir, "pilot_summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, sort_keys=False, ensure_ascii=False)
    summary["summary_path"] = summary_path
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dossiers-dir", required=True)
    parser.add_argument("--staging-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--e-numbers", nargs="+", default=["E250", "E150d", "E330", "E951"])
    parser.add_argument("--supplied-snapshot", default=None)
    args = parser.parse_args(argv)

    summary = run_pilot(
        dossiers_dir=args.dossiers_dir,
        staging_dir=args.staging_dir,
        output_dir=args.output_dir,
        e_numbers=args.e_numbers,
        supplied_snapshot=args.supplied_snapshot,
    )
    print(json.dumps({k: v for k, v in summary.items() if k != "match_results"}, indent=2, default=str))
    for r in summary["match_results"]:
        print(f"{r['catalogue_identity']['e_number_raw']}: {r['overall_status']} -- {r['explanation']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
