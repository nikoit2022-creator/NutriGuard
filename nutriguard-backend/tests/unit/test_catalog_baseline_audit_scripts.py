"""Issue #23 stage 1: the baseline audit scripts stay read-only and
deterministic. No DB, no network."""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SQL = ROOT / "scripts" / "audit" / "catalog_baseline_readonly.sql"
INVENTORY = ROOT / "scripts" / "audit" / "seed_sources_inventory.py"

_WRITE_KEYWORDS = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|vacuum|copy|call|do)\b", re.IGNORECASE
)


def _statements_without_comments_and_strings() -> str:
    text = "\n".join(line for line in SQL.read_text().splitlines() if not line.lstrip().startswith("--"))
    return re.sub(r"'[^']*'", "''", text)


def test_baseline_sql_is_one_read_only_transaction():
    text = _statements_without_comments_and_strings()
    assert text.strip().upper().startswith("BEGIN READ ONLY;")
    assert text.strip().upper().endswith("ROLLBACK;")
    assert not _WRITE_KEYWORDS.search(text)


def test_baseline_sql_reads_only_catalog_tables_and_product_id_lists():
    text = _statements_without_comments_and_strings().lower()
    tables = set(re.findall(r"\b(?:from|join)\s+([a-z_]+)", text))
    assert tables <= {
        "ingredients", "ingredient_aliases", "ingredient_localizations", "products", "product_sources",
        "g", "refs", "d", "q", "unnest",
    }
    # Never user, device, token or scan tables.
    for forbidden in ("users", "devices", "refresh_tokens", "scan_history", "user_health_profiles"):
        assert forbidden not in tables


def test_seed_inventory_reports_the_pinned_seed_shape():
    out = subprocess.run(
        [sys.executable, str(INVENTORY)], capture_output=True, text=True, check=True
    ).stdout
    rows = {tuple(p.strip() for p in line.split(" | ")[:2]): line.split(" | ")[2] for line in out.splitlines()}
    assert rows[("registry", "supplied 800-row registry present in repository")] == "no"
    assert rows[("json", "rows")] == "12"
    assert rows[("csv", "rows")] == "43"
    assert rows[("union", "distinct E-codes (json + csv)")] == "45"
    # Deterministic: a second run is byte-identical.
    again = subprocess.run([sys.executable, str(INVENTORY)], capture_output=True, text=True, check=True).stdout
    assert again == out
