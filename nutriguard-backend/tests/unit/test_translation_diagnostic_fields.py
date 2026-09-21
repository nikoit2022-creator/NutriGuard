"""
Scan-diagnostics translation fields (issue #21, section 4): the
attempt / partial / final-outcome semantics of
`app.api.v1.scan._translation_fields`, the request-scoped
`IngredientTranslationSummary` it consumes, and that the resulting
single JSON line stays small, closed-vocabulary, privacy-safe and
inside the existing 1 MiB-per-file + 1 backup rotation policy.

Pure unit tests: no DB, no network. The rotation, cross-process `flock`
and fail-open behavior of `app.core.scan_diagnostics` itself are
UNCHANGED by this work and covered by `test_scan_diagnostics.py`; the
tests here only prove the new fields fit inside that same budget.
"""
import json

import pytest

import app.api.v1.scan as scan_module
from app.core import scan_diagnostics
from app.services.ingredient_catalog import IngredientTranslationSummary
from app.services.translation_rejection import (
    PROVIDER_FAILURE_KEYS,
    REJECTION_KEYS,
    count_pairs,
)

_MARKER = "ZQXMARKER7731"


def _summary(
    *, reliable=0, unreliable=0, reasons=None, providers=None, batches=None, partial=None, failed=None
) -> IngredientTranslationSummary:
    attempted = reliable + unreliable
    return IngredientTranslationSummary(
        attempted=attempted,
        reliable=reliable,
        unreliable=unreliable,
        detected_languages=("ro",) if attempted else (),
        batches=(1 if attempted else 0) if batches is None else batches,
        partial_batches=(1 if reliable and unreliable else 0) if partial is None else partial,
        failed_batches=(1 if unreliable and not reliable else 0) if failed is None else failed,
        rejection_counts=count_pairs(reasons or {}),
        provider_failure_counts=count_pairs(providers or {}),
    )


def _fields(**result) -> dict:
    return scan_module._translation_fields(result)


# --- Final outcome vs attempts vs partials ------------------------------------


@pytest.mark.parametrize(
    ("status", "summary", "expected_outcome"),
    [
        ("ok", _summary(), "not_attempted"),
        ("ok", None, "not_attempted"),  # language policy ran, nothing to translate
        (None, None, None),  # no language pipeline at all -> genuinely not applicable
        ("translated", None, "succeeded"),
        ("translation_unreliable_fallback", None, "failed"),
        ("ok", _summary(reliable=3), "succeeded"),
        ("ok", _summary(unreliable=3, reasons={"lowConfidence": 3}), "failed"),
        ("ok", _summary(reliable=2, unreliable=1, reasons={"lowConfidence": 1}), "partial"),
        # whole-label translation worked, but some per-ingredient tokens were rejected
        ("translated", _summary(reliable=1, unreliable=1, reasons={"lowConfidence": 1}), "partial"),
        # whole-label translation worked and every token verified
        ("translated", _summary(reliable=2), "succeeded"),
        # whole-label fell back and the per-ingredient pass also failed entirely
        ("translation_unreliable_fallback", _summary(unreliable=1, reasons={"noMatchingEntry": 1}), "failed"),
        # whole-label fell back, per-ingredient pass succeeded -> mixed
        ("translation_unreliable_fallback", _summary(reliable=1), "partial"),
    ],
)
def test_final_translation_outcome_table(status, summary, expected_outcome):
    fields = _fields(label_language_status=status, ingredient_translation_summary=summary)
    assert fields.get("translationOutcome") == expected_outcome


def test_partial_batch_fields_keep_attempts_partials_and_final_distinct():
    fields = _fields(
        label_language_status="ok",
        ingredient_translation_summary=_summary(
            reliable=2,
            unreliable=3,
            reasons={"lowConfidence": 1, "eNumberMismatch": 2},
        ),
    )
    # (a) attempts
    assert fields["translationAttempted"] is True
    assert fields["ingredientTranslationAttempted"] is True
    assert fields["ingredientTranslationBatches"] == 1
    # (b) partial outcome: one batch that was neither fully good nor fully bad
    assert fields["ingredientTranslationPartialBatches"] == 1
    assert fields["ingredientTranslationFailedBatches"] == 0
    assert fields["ingredientTranslationReliableCount"] == 2
    assert fields["ingredientTranslationUnreliableCount"] == 3
    # per-reason counters are over the REJECTED entries only, not double counted
    assert fields["ingredientTranslationReasons"] == {"eNumberMismatch": 2, "lowConfidence": 1}
    assert sum(fields["ingredientTranslationReasons"].values()) == fields["ingredientTranslationUnreliableCount"]
    # (c) final outcome
    assert fields["translationOutcome"] == "partial"
    # the whole-label pass did not run a translation
    assert fields["translationResult"] == "not_needed"
    assert "labelTranslationFailureReason" not in {k for k, v in fields.items() if v is not None}


def test_failed_batch_is_not_reported_as_partial():
    fields = _fields(
        label_language_status="ok",
        ingredient_translation_summary=_summary(
            unreliable=4, reasons={"providerUnavailable": 4}, providers={"notConfigured": 4}
        ),
    )
    assert fields["ingredientTranslationFailedBatches"] == 1
    assert fields["ingredientTranslationPartialBatches"] == 0
    assert fields["ingredientTranslationProviderFailures"] == {"notConfigured": 4}
    assert fields["translationOutcome"] == "failed"


def test_reason_counters_are_sparse_and_omitted_when_nothing_was_rejected():
    fields = _fields(label_language_status="ok", ingredient_translation_summary=_summary(reliable=2))
    assert fields["ingredientTranslationReasons"] is None  # dropped by record_scan_diagnostic
    assert fields["ingredientTranslationProviderFailures"] is None
    assert fields["ingredientTranslationBatches"] == 1


def test_merged_passes_add_up_without_double_counting_and_keep_reason_sum_consistent():
    first = _summary(reliable=1, unreliable=2, reasons={"lowConfidence": 1, "noMatchingEntry": 1})
    second = _summary(unreliable=1, reasons={"lowConfidence": 1})
    merged = first.merged_with(second)
    assert merged.attempted == 4 and merged.reliable == 1 and merged.unreliable == 3
    assert merged.batches == 2
    assert merged.partial_batches == 1 and merged.failed_batches == 1
    assert dict(merged.rejection_counts) == {"lowConfidence": 2, "noMatchingEntry": 1}
    assert sum(dict(merged.rejection_counts).values()) == merged.unreliable
    # an empty pass (nothing left to translate) contributes nothing
    assert first.merged_with(IngredientTranslationSummary()) == first


def test_summary_keeps_backward_compatible_positional_construction():
    legacy = IngredientTranslationSummary(2, 1, 1, ("ro",))
    assert (legacy.attempted, legacy.reliable, legacy.unreliable, legacy.detected_languages) == (2, 1, 1, ("ro",))
    assert legacy.batches == 0 and legacy.rejection_counts == () and legacy.provider_failure_counts == ()
    fields = _fields(label_language_status="ok", ingredient_translation_summary=legacy)
    assert fields["ingredientTranslationBatches"] == 0
    assert fields["ingredientTranslationReasons"] is None


# --- Whole-label pass ---------------------------------------------------------


def test_strict_whole_label_failure_metadata_becomes_an_attempted_failed_translation():
    """`TranslationUnreliableError.diagnostic_metadata` (no `LabelTextResult`
    exists on this path) still yields honest attempt/result/outcome fields."""
    fields = _fields(
        label_translation_failure_reason="providerUnavailable",
        label_translation_provider_failure_category="httpAuth",
    )
    assert fields["translationAttempted"] is True
    assert fields["translationResult"] == "unreliable_error"
    assert fields["translationReason"] == "translation_unreliable_error"
    assert fields["labelTranslationFailureReason"] == "providerUnavailable"
    assert fields["labelTranslationProviderFailureCategory"] == "httpAuth"
    assert fields["translationOutcome"] == "failed"


def test_non_strict_whole_label_fallback_carries_its_reason():
    fields = _fields(
        label_language_status="translation_unreliable_fallback",
        label_translation_failure_reason="lowConfidence",
        ingredient_translation_summary=_summary(),
    )
    assert fields["translationResult"] == "unreliable_fallback"
    assert fields["labelTranslationFailureReason"] == "lowConfidence"
    assert fields["translationOutcome"] == "failed"


def test_metadata_without_any_translation_information_yields_no_fields():
    assert all(value is None for value in _fields().values())
    assert all(value is None for value in scan_module._translation_fields({}).values())


# --- Closed vocabulary / hostile input ----------------------------------------


def test_unknown_reason_and_category_strings_are_never_passed_through():
    fields = _fields(
        label_translation_failure_reason=f"raw text {_MARKER}",
        label_translation_provider_failure_category=_MARKER,
        label_language_status="translation_unreliable_fallback",
        ingredient_translation_summary=_summary(
            unreliable=2,
            reasons={f"evil {_MARKER}": 1, "lowConfidence": 1},
            providers={_MARKER: 1, "timeout": 1},
        ),
    )
    serialized = json.dumps(fields)
    assert _MARKER not in serialized
    assert fields["labelTranslationFailureReason"] is None
    assert fields["ingredientTranslationReasons"] == {"lowConfidence": 1}
    assert fields["ingredientTranslationProviderFailures"] == {"timeout": 1}


def test_builder_failure_is_swallowed_and_yields_no_fields(monkeypatch):
    class _Exploding:
        @property
        def attempted(self):
            raise RuntimeError(f"secret detail {_MARKER}")

    assert scan_module._translation_fields({"ingredient_translation_summary": _Exploding()}) == {}
    assert scan_module._translation_fields({"ingredient_translation_summary": "not a summary"}) == {}

    def _boom(result):
        raise RuntimeError(_MARKER)

    monkeypatch.setattr(scan_module, "_build_translation_fields", _boom)
    assert scan_module._translation_fields({"label_language_status": "ok"}) == {}


# --- The line itself: small, JSON, closed vocabulary, inside the budget ---------


def _worst_case_fields() -> dict:
    summary = _summary(
        reliable=40,
        unreliable=400,
        reasons={key: 400 // len(REJECTION_KEYS) + 1 for key in REJECTION_KEYS},
        providers={key: 39 for key in PROVIDER_FAILURE_KEYS},
    )
    return scan_module._translation_fields(
        {
            "label_language_status": "translation_unreliable_fallback",
            "label_translation_failure_reason": "providerUnavailable",
            "label_translation_provider_failure_category": "httpServerError",
            "label_detected_language": "unknown",
            "ingredient_translation_summary": summary,
        }
    )


def _configure(monkeypatch, path, *, max_bytes, backup_count=1) -> None:
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_ENABLED", True)
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_PATH", str(path))
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_MAX_BYTES", max_bytes)
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_BACKUP_COUNT", backup_count)


def test_worst_case_translation_fields_keep_the_line_small_and_typed(tmp_path, monkeypatch):
    path = tmp_path / "scan.jsonl"
    _configure(monkeypatch, path, max_bytes=1024 * 1024)

    scan_diagnostics.record_scan_diagnostic(
        requestId="req-1",
        operation="scan_ocr_text",
        stage="response_built",
        barcode="3800123456789",
        dataSource="label_scan",
        outcome="success",
        recognizedIngredientCount=400,
        unresolvedIngredientCount=400,
        untranslatedIngredientCount=400,
        durationMs=1234.56,
        **_worst_case_fields(),
    )

    raw = path.read_text(encoding="utf-8")
    assert raw.endswith("\n") and raw.count("\n") == 1
    assert len(raw.encode("utf-8")) < 2048, "worst-case line grew past the small-line budget"

    payload = json.loads(raw)
    reasons = payload["ingredientTranslationReasons"]
    assert set(reasons) <= set(REJECTION_KEYS)
    assert set(payload["ingredientTranslationProviderFailures"]) <= set(PROVIDER_FAILURE_KEYS)
    assert all(type(v) is int for v in reasons.values())
    assert payload["translationOutcome"] in {"not_attempted", "succeeded", "partial", "failed"}
    assert payload["labelTranslationFailureReason"] == "providerUnavailable"


def test_translation_laden_lines_respect_the_existing_rotation_budget(tmp_path, monkeypatch):
    """Same technique/limits as `test_scan_diagnostics.py`: the new fields
    add bytes per line but never change the 1-file + exactly-one-backup
    bound (`SCAN_DIAGNOSTICS_MAX_BYTES` is unchanged in meaning)."""
    path = tmp_path / "scan.jsonl"
    max_bytes = 8192
    _configure(monkeypatch, path, max_bytes=max_bytes, backup_count=1)
    fields = _worst_case_fields()

    for i in range(200):
        scan_diagnostics.record_scan_diagnostic(
            requestId=f"req-{i}", operation="scan_ocr_text", outcome="success", **fields
        )

    backup = path.with_name(path.name + ".1")
    assert backup.exists()
    assert not path.with_name(path.name + ".2").exists()
    line_length = len(path.read_text(encoding="utf-8").splitlines()[0].encode("utf-8")) + 1
    assert path.stat().st_size <= max_bytes + line_length
    assert backup.stat().st_size <= max_bytes + line_length
    for candidate in (path, backup):
        for raw_line in candidate.read_text(encoding="utf-8").splitlines():
            json.loads(raw_line)  # never a truncated/merged line


def test_default_storage_budget_is_still_one_mib_and_one_backup():
    """This work must not have widened the diagnostics storage budget."""
    from app.core.config import Settings

    assert Settings.model_fields["SCAN_DIAGNOSTICS_MAX_BYTES"].default == 1024 * 1024
    assert Settings.model_fields["SCAN_DIAGNOSTICS_BACKUP_COUNT"].default == 1
