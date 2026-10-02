"""Tests for scripts/openfoodtox/catalogue_snapshot.py.

Builds the snapshot from the real tracked seed files (small, git-tracked,
offline) -- never opens a database connection (not even a disposable
one: see the module docstring) and never touches the real multi-GB
OpenFoodTox dataset.
"""

from __future__ import annotations

import json

from scripts.openfoodtox.catalogue_snapshot import (
    build_snapshot_from_tracked_seed,
    load_supplied_snapshot,
    normalize_cas,
    normalize_e_number,
)


class TestNormalizeHelpers:
    def test_normalize_e_number_plain(self):
        assert normalize_e_number("E951") == "E951"
        assert normalize_e_number("e 951") == "E951"

    def test_normalize_e_number_rejects_range_and_malformed(self):
        assert normalize_e_number("E251-252") is None
        assert normalize_e_number("not-an-e-number") is None
        assert normalize_e_number(None) is None

    def test_normalize_cas(self):
        assert normalize_cas(" 7632-00-0 ") == "7632-00-0"
        assert normalize_cas("not a cas number") is None
        assert normalize_cas(None) is None


class TestBuildSnapshotFromTrackedSeed:
    def test_includes_expected_pilot_identities(self):
        identities = build_snapshot_from_tracked_seed()
        by_e_number = {i.e_number_normalized: i for i in identities if i.e_number_normalized}
        assert "E951" in by_e_number
        assert by_e_number["E951"].source == "tracked_seed_json"
        assert "E250" in by_e_number
        assert by_e_number["E250"].source == "tracked_seed_json"
        # E330 (citric acid) is not in the richer JSON seed -- it should
        # come from the CSV starter pack instead.
        assert "E330" in by_e_number
        assert by_e_number["E330"].source == "tracked_seed_csv_starter"
        # E150d (caramel colour IV) is not provisioned anywhere in the
        # tracked seed data at all -- absence must be honest, not guessed.
        assert "E150d" not in by_e_number

    def test_json_seed_row_shadows_csv_starter_row_for_same_e_number(self):
        """Mirrors app.seed.load_seed._load_e_additive_starter's own
        "existing richer row wins" rule -- a CSV starter row must never
        duplicate/shadow an E-number already provisioned by the JSON seed."""
        identities = build_snapshot_from_tracked_seed()
        e951_rows = [i for i in identities if i.e_number_normalized == "E951"]
        assert len(e951_rows) == 1
        assert e951_rows[0].source == "tracked_seed_json"

    def test_no_identity_has_a_cas_number_in_current_tracked_seed_data(self):
        """Documents a real, reportable catalogue limitation: the tracked
        seed data carries no CAS numbers at all today, so pilot matching
        against it is necessarily E-number-only."""
        identities = build_snapshot_from_tracked_seed()
        assert all(i.cas_number_normalized is None for i in identities)

    def test_repeatable(self):
        a = [i.to_dict() for i in build_snapshot_from_tracked_seed()]
        b = [i.to_dict() for i in build_snapshot_from_tracked_seed()]
        assert a == b


class TestLoadSuppliedSnapshot:
    def test_loads_minimal_ingredient_only_entries(self, tmp_path):
        path = tmp_path / "supplied.json"
        path.write_text(
            json.dumps(
                [
                    {"id": "e100_curcumin", "commonName": "Curcumin", "eNumber": "E100"},
                    {"id": "e200_sorbic_acid", "commonName": "Sorbic acid", "eNumber": "E200", "casNumber": "110-44-1"},
                ]
            ),
            encoding="utf-8",
        )
        identities = load_supplied_snapshot(path)
        assert len(identities) == 2
        assert identities[0].source == "supplied_snapshot"
        assert identities[1].cas_number_normalized == "110-44-1"

    def test_rejects_entry_missing_id_or_name(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps([{"eNumber": "E100"}]), encoding="utf-8")
        try:
            load_supplied_snapshot(path)
            assert False, "expected ValueError"
        except ValueError:
            pass
