"""Build a minimal, offline NutriGuard ingredient-catalogue snapshot to
match against the OpenFoodTox dataset (see docs/OPENFOODTOX_PILOT_TASK.md).

Two sources are supported:

1. ``build_snapshot_from_tracked_seed()`` -- the catalogue NutriGuard
   actually ships, reconstructed from the tracked seed files
   (``app/seed/ingredients_seed.json`` and
   ``app/seed/e_additives_curated_starter.csv``). This reuses the real
   seed loader's own pure row-transform functions
   (``app.seed.load_seed._row_to_kwargs`` / ``_starter_row_to_kwargs``)
   directly on those files and replicates the loader's "an existing
   JSON-seed row for this E-number wins over the CSV starter row"
   merge rule (see ``app.seed.load_seed._load_e_additive_starter``) --
   but it never opens a database connection, not even an isolated or
   disposable one. Those transform functions are pure (``row dict ->
   kwargs dict``), so no engine or session is ever created -- a
   stronger guarantee than the task's own "isolated test database if
   required" allowance.

2. ``load_supplied_snapshot(path)`` -- a separately supplied,
   authorized ingredient-only JSON export (task: "Support a separately
   supplied authorized ingredient-only JSON snapshot for later use").
   Not used by the real pilot run in this task (no such export exists
   yet; no live export is fetched here) -- implemented and tested so a
   later, explicitly authorized run can supply one without code
   changes.

Every identity this module returns is a minimal, explicitly sourced
set of identifiers (id/common name/E-number/CAS) -- never the full
scientific-content row -- because the pilot's matcher only ever needs
identifiers to compare, and keeping the rest out avoids any temptation
to treat catalogue content as independently verified dataset evidence.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .e_numbers import parse_e_number_candidate

_SEED_DIR = Path(__file__).resolve().parents[2] / "app" / "seed"
_SEED_FILE = _SEED_DIR / "ingredients_seed.json"
_E_ADDITIVE_SEED_FILE = _SEED_DIR / "e_additives_curated_starter.csv"


@dataclass(frozen=True)
class CatalogueIdentity:
    id: str
    common_name: str
    e_number_raw: str | None
    e_number_normalized: str | None
    cas_number_raw: str | None
    cas_number_normalized: str | None
    source: str  # "tracked_seed_json" | "tracked_seed_csv_starter" | "supplied_snapshot"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "common_name": self.common_name,
            "e_number_raw": self.e_number_raw,
            "e_number_normalized": self.e_number_normalized,
            "cas_number_raw": self.cas_number_raw,
            "cas_number_normalized": self.cas_number_normalized,
            "source": self.source,
        }


def normalize_e_number(raw: str | None) -> str | None:
    """Normalize a catalogue-side E-number the same way dossier-side
    synonym-derived candidates are normalized (see ``e_numbers.py``), so
    the two are directly comparable. Returns ``None`` -- not a guess --
    for anything that isn't a single, unambiguous, recognized E-number
    (unrecognized shape, or an explicit range, which per the matching
    rules can never become an exact single-substance link on its own).
    """
    if not raw:
        return None
    candidate = parse_e_number_candidate(raw)
    if candidate is None or not candidate.recognized or candidate.kind != "single":
        return None
    return candidate.normalized


def normalize_cas(raw: str | None) -> str | None:
    """Normalize a CAS Registry Number to its canonical ``NN...-NN-N``
    form (digits and hyphens only, whitespace stripped). Returns
    ``None`` -- not a best-effort guess -- for anything that doesn't
    match that shape at all, so a malformed CAS is quarantined rather
    than silently compared.
    """
    if not raw:
        return None
    stripped = raw.strip()
    import re

    if not re.fullmatch(r"\d{2,7}-\d{2}-\d", stripped):
        return None
    return stripped


def build_snapshot_from_tracked_seed() -> list[CatalogueIdentity]:
    from app.seed.load_seed import _row_to_kwargs, _starter_row_to_kwargs  # local import: see module docstring

    identities: list[CatalogueIdentity] = []
    used_e_numbers: set[str] = set()

    seed_rows = json.loads(_SEED_FILE.read_text(encoding="utf-8"))
    for row in seed_rows:
        kwargs = _row_to_kwargs(row)
        e_number_raw = kwargs.get("e_number")
        if e_number_raw:
            used_e_numbers.add(e_number_raw)
        identities.append(
            CatalogueIdentity(
                id=kwargs["id"],
                common_name=kwargs["common_name"],
                e_number_raw=e_number_raw,
                e_number_normalized=normalize_e_number(e_number_raw),
                cas_number_raw=kwargs.get("cas_number"),
                cas_number_normalized=normalize_cas(kwargs.get("cas_number")),
                source="tracked_seed_json",
            )
        )

    with _E_ADDITIVE_SEED_FILE.open(encoding="utf-8-sig", newline="") as handle:
        csv_rows = list(csv.DictReader(handle))
    for row in csv_rows:
        kwargs = _starter_row_to_kwargs(row)
        e_number_raw = kwargs["e_number"]
        if e_number_raw in used_e_numbers:
            # Mirrors app.seed.load_seed._load_e_additive_starter: an
            # existing, richer JSON-seed row for this E-number is never
            # shadowed by the starter pack.
            continue
        used_e_numbers.add(e_number_raw)
        identities.append(
            CatalogueIdentity(
                id=kwargs["id"],
                common_name=kwargs["common_name"],
                e_number_raw=e_number_raw,
                e_number_normalized=normalize_e_number(e_number_raw),
                cas_number_raw=kwargs.get("cas_number"),
                cas_number_normalized=normalize_cas(kwargs.get("cas_number")),
                source="tracked_seed_csv_starter",
            )
        )

    return identities


_SUPPLIED_FIELD_ALIASES = {
    "id": ("id",),
    "common_name": ("commonName", "common_name", "name"),
    "e_number": ("eNumber", "e_number"),
    "cas_number": ("casNumber", "cas_number"),
}


def load_supplied_snapshot(path: str | Path) -> list[CatalogueIdentity]:
    """Load a separately supplied, authorized ingredient-only JSON
    snapshot (a flat list of objects with at least an id and a name;
    E-number/CAS optional). Not a live export fetch -- reads a local
    file the caller has already obtained and authorized for this use.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("supplied snapshot must be a JSON array of ingredient-only objects")

    identities: list[CatalogueIdentity] = []
    for row in data:
        if not isinstance(row, dict):
            raise ValueError(f"supplied snapshot entry is not an object: {row!r}")

        def _get(field: str) -> str | None:
            for key in _SUPPLIED_FIELD_ALIASES[field]:
                if key in row and row[key]:
                    return str(row[key])
            return None

        row_id = _get("id")
        common_name = _get("common_name")
        if not row_id or not common_name:
            raise ValueError(f"supplied snapshot entry missing id/name: {row!r}")
        e_number_raw = _get("e_number")
        cas_number_raw = _get("cas_number")
        identities.append(
            CatalogueIdentity(
                id=row_id,
                common_name=common_name,
                e_number_raw=e_number_raw,
                e_number_normalized=normalize_e_number(e_number_raw),
                cas_number_raw=cas_number_raw,
                cas_number_normalized=normalize_cas(cas_number_raw),
                source="supplied_snapshot",
            )
        )
    return identities
