"""Standalone stdlib tests: can run without backend dependencies."""
import importlib.util
import io
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('quality_report', ROOT / 'scripts/audit/catalog_quality_report.py')
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


class AuditTests(unittest.TestCase):
    def snapshot(self):
        return {'schemaVersion': 1, 'generatedAt': '2026-09-26T12:00:00Z',
                'metrics': dict.fromkeys(audit.METRICS, 0)}

    def read(self, data):
        return audit.read_snapshot(io.BytesIO(json.dumps(data).encode()))

    def test_valid(self):
        self.assertEqual(self.read(self.snapshot()), self.snapshot())

    def test_extra_sensitive_fields_rejected(self):
        data = self.snapshot()
        data['rawText'] = 'must not be printed'
        with self.assertRaises(ValueError):
            self.read(data)

    def test_invalid_metrics(self):
        for bad in (-1, True, '0', None, 1.5, 10**16):
            data = self.snapshot()
            data['metrics']['ingredients'] = bad
            with self.assertRaises(ValueError):
                self.read(data)

    def test_oversized(self):
        with self.assertRaises(ValueError):
            audit.read_snapshot(io.BytesIO(b' ' * (audit.MAX_BYTES + 1)))

    def test_version_and_missing_metric(self):
        data = self.snapshot()
        data['schemaVersion'] = 2
        with self.assertRaises(ValueError):
            self.read(data)
        data = self.snapshot()
        del data['metrics']['ingredients']
        with self.assertRaises(ValueError):
            self.read(data)

    def test_delta(self):
        previous = self.snapshot()
        current = self.snapshot()
        current['generatedAt'] = '2026-09-27T12:00:00Z'
        current['metrics']['ingredients'] = 3
        self.assertEqual(audit.build_report(current, previous)['delta']['ingredients'], 3)

    def test_non_older_snapshot_rejected(self):
        with self.assertRaises(ValueError):
            audit.build_report(self.snapshot(), self.snapshot())

    def test_sql_guardrails(self):
        sql = (ROOT / 'scripts/audit/catalog_quality.sql').read_text()
        self.assertIn('REPEATABLE READ READ ONLY', sql)
        self.assertIn('statement_timeout', sql)
        self.assertTrue(sql.strip().endswith('ROLLBACK;'))
        self.assertNotIn('raw_ingredient_text', sql)


if __name__ == '__main__':
    unittest.main()
