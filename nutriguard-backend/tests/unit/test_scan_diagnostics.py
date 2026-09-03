import json

from app.core import scan_diagnostics


def test_scan_diagnostic_is_bounded_and_contains_no_payload(tmp_path, monkeypatch):
    path = tmp_path / "scan.jsonl"
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_ENABLED", True)
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_PATH", str(path))
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_MAX_BYTES", 1024)
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_BACKUP_COUNT", 1)
    scan_diagnostics._handler = None

    scan_diagnostics.record_scan_diagnostic(
        requestId="request-1",
        barcode="3800123456789",
        outcome="partial",
        errorCode="PRODUCT_NOT_FOUND",
        recognizedIngredientCount=4,
    )
    scan_diagnostics._handler.flush()

    payload = json.loads(path.read_text(encoding="utf-8").strip())
    assert payload["barcode"] == "3800123456789"
    assert payload["outcome"] == "partial"
    assert payload["recognizedIngredientCount"] == 4
    assert "image" not in payload
    assert "rawText" not in payload

    scan_diagnostics._handler.close()
    scan_diagnostics._handler = None
