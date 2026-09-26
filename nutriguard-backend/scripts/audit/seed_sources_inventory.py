"""Issue #23, stage 1: inventory of the Git seed sources (no DB, no network).

Reads `app/seed/ingredients_seed.json`, `e_additives_curated_starter.csv`
and `ingredients_seed_bg.json` and prints deterministic aggregate counts:
E-code overlap, field population, source specificity and BG coverage.

    python scripts/audit/seed_sources_inventory.py

The supplied 800-row E-additive registry named in `load_seed.py` is not in
the repository; this script says so instead of assuming it.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

SEED_DIR = Path(__file__).resolve().parents[2] / "app" / "seed"
JSON_SEED = SEED_DIR / "ingredients_seed.json"
CSV_SEED = SEED_DIR / "e_additives_curated_starter.csv"
BG_SEED = SEED_DIR / "ingredients_seed_bg.json"

# Per-field content columns of the starter CSV (everything except identity/meta).
CSV_CONTENT_COLUMNS = [
    "functional_class", "typical_role_or_foods", "digestion_absorption", "metabolism",
    "potential_effects", "human_evidence", "animal_evidence", "adi_tdi",
    "efsa_assessment", "jecfa_assessment", "eu_regulatory_note",
]
JSON_CONTENT_FIELDS = [
    "scientificName", "category", "description", "purposeInFood", "healthConcerns", "sideEffects",
    "efsaStatus", "fdaStatus", "acceptableDailyIntake", "references", "allergens",
]
# Landing pages, not documents: a citation to one of these supports no specific claim.
GENERIC_AUTHORITY_HOSTS = {
    "www.efsa.europa.eu", "apps.who.int", "codex.fao.org", "food.ec.europa.eu",
}
_E = re.compile(r"^E\d{3,4}[A-Z]?$")


def _norm_e(code: str) -> str:
    return (code or "").strip().upper().replace(" ", "")


def _filled(value) -> bool:
    return bool(str(value or "").strip())


def _generic_only(sources: str) -> bool:
    urls = [u.strip() for u in (sources or "").split("|") if u.strip()]
    if not urls:
        return False
    for url in urls:
        parsed = urlparse(url)
        if parsed.netloc not in GENERIC_AUTHORITY_HOSTS:
            return False
        # A landing/topic/database home, not a specific document.
        if not re.search(r"(/topic/|/Home/?$|/codex-online-databases/|/additives_en$)", parsed.path):
            return False
    return True


def main() -> int:
    json_rows = json.loads(JSON_SEED.read_text(encoding="utf-8"))
    csv_rows = list(csv.DictReader(CSV_SEED.open(encoding="utf-8")))
    bg_rows = json.loads(BG_SEED.read_text(encoding="utf-8"))

    json_codes = {_norm_e(r.get("eNumber", "")) for r in json_rows if _filled(r.get("eNumber"))}
    csv_codes = [_norm_e(r["e_number"]) for r in csv_rows]
    overlap = json_codes & set(csv_codes)
    union = json_codes | set(csv_codes)
    bg_ids = {r["ingredientId"] for r in bg_rows}
    json_ids = {r["id"] for r in json_rows}

    def out(*parts):
        print(" | ".join(str(p) for p in parts))

    out("registry", "supplied 800-row registry present in repository", "no")
    out("json", "rows", len(json_rows))
    out("json", "rows with E-number", len(json_codes))
    out("json", "rows without E-number", [r["id"] for r in json_rows if not _filled(r.get("eNumber"))])
    out("json", "ids", sorted(json_ids))
    out("csv", "rows", len(csv_rows))
    out("csv", "distinct codes", len(set(csv_codes)))
    out("csv", "duplicate codes", sorted(c for c, n in Counter(csv_codes).items() if n > 1))
    out("csv", "invalid code format", sorted(c for c in csv_codes if not _E.match(c)))
    out("union", "distinct E-codes (json + csv)", len(union))
    out("union", "overlap json/csv (loader skips the csv row)", sorted(overlap))
    out("union", "csv-only codes", len(set(csv_codes) - json_codes))
    out("union", "json-only codes", sorted(json_codes - set(csv_codes)))

    for col in CSV_CONTENT_COLUMNS:
        out("csv.fill", col, sum(_filled(r[col]) for r in csv_rows))
    out("csv.fill", "rows with no content column at all", sum(
        not any(_filled(r[c]) for c in CSV_CONTENT_COLUMNS) for r in csv_rows))
    out("csv.fill", "rows with only functional_class/typical_role/adi",
        sum(all(not _filled(r[c]) for c in CSV_CONTENT_COLUMNS
                if c not in ("functional_class", "typical_role_or_foods", "adi_tdi")) for r in csv_rows))
    out("csv.meta", "confidence", dict(Counter(r["confidence"] for r in csv_rows)))
    out("csv.meta", "curation_status", dict(Counter(r["curation_status"] for r in csv_rows)))
    out("csv.meta", "last_reviewed", dict(Counter(r["last_reviewed"] for r in csv_rows)))
    out("csv.source", "rows citing only generic authority landing pages",
        sum(_generic_only(r["primary_sources"]) for r in csv_rows))
    out("csv.source", "rows with no primary_sources", sum(not _filled(r["primary_sources"]) for r in csv_rows))
    out("csv.source", "distinct cited URLs",
        len({u.strip() for r in csv_rows for u in r["primary_sources"].split("|") if u.strip()}))
    out("csv.adi", "rows with a JECFA/EFSA ADI text but no cited document",
        sum(_filled(r["adi_tdi"]) and _generic_only(r["primary_sources"]) for r in csv_rows))

    for field in JSON_CONTENT_FIELDS:
        out("json.fill", field, sum(_filled(r.get(field)) for r in json_rows))
    out("json.meta", "riskLevel", dict(Counter(r.get("riskLevel") for r in json_rows)))
    out("json.source", "rows with a references string", sum(_filled(r.get("references")) for r in json_rows))
    out("json.source", "rows whose references contain a URL", sum("http" in (r.get("references") or "") for r in json_rows))

    out("bg", "rows", len(bg_rows))
    out("bg", "ids not in json seed", sorted(bg_ids - json_ids))
    out("bg", "json rows without a bg profile", sorted(json_ids - bg_ids))
    out("bg", "coverage of the E-code union", f"{len(bg_ids & json_ids)}/{len(union)} rows (bg exists only for json-seed rows)")

    # Identity specificity: catalog ids naming a specific source under a generic code.
    out("identity", "json ids whose slug names a specific source",
        sorted(i for i in json_ids if re.search(r"(soy|palm|corn|milk|egg|wheat)", i)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
