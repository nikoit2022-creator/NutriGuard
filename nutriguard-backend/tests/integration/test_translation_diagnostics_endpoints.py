"""
Full-HTTP coverage of the translation rejection-reason diagnostics
(issue #21, section 4): each failure class reaches the single per-scan
diagnostic line as a bounded counter, attempts / partial batches / final
outcome are reported separately, nothing about a rejection leaks into a
public response, and a failing diagnostic write (or counter builder)
never changes a scan's response.

Gemini is always mocked at `gemini_service.*` -- no network, no live
provider. The diagnostics writer is either captured (like
`test_scan_diagnostics_endpoints.py`) or, where the on-disk line is the
thing under test, the REAL `record_scan_diagnostic` pointed at a temp
path.
"""
import json

import pytest

import app.api.v1.scan as scan_module
import app.services.food_analysis as food_analysis_module
from app.core import scan_diagnostics
from app.integrations.gemini import GeminiUnavailableError, gemini_service
from app.services.translation_rejection import PROVIDER_FAILURE_KEYS, REJECTION_KEYS

_MARKER = "ZQXMARKER7731"

_RAW_TEXT = "Ingredients: Ulei de rapiță, Compus Foarte Necunoscut"
_TOKENS = ["Ulei de rapiță", "Compus Foarte Necunoscut"]


async def _register_device(client, device_id: str) -> dict:
    resp = await client.post("/api/v1/auth/device", json={"deviceId": device_id})
    token = resp.json()["accessToken"]
    return {"Authorization": f"Bearer {token}"}


def _capture(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    def _fake(**fields):
        calls.append(fields)

    monkeypatch.setattr(scan_module, "record_scan_diagnostic", _fake)
    return calls


def _entry(token: str, translated: str, *, confidence: float = 0.9) -> dict:
    return {
        "originalText": token,
        "detectedLanguage": "ro",
        "confidence": confidence,
        "translatedText": translated,
    }


def _install_translator(monkeypatch, respond) -> list[list[str]]:
    """`respond(tokens)` -> the raw string the provider returns (or raises).
    Returns the list of per-call target lists (one item per provider call)."""
    seen: list[list[str]] = []

    async def _fake(tokens: list[str], *, context=None) -> str:
        seen.append(list(tokens))
        return respond(tokens)

    monkeypatch.setattr(gemini_service, "translate_ingredient_list", _fake)
    return seen


async def _ocr(app_client, headers, raw_text: str = _RAW_TEXT, **extra):
    return await app_client.post(
        "/api/v1/scan/ocr-text", json={"rawText": raw_text, **extra}, headers=headers
    )


def _walk(value):
    """Every dict key and every scalar value in a JSON document."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)
    else:
        yield value


# --- Partial batch ------------------------------------------------------------


@pytest.mark.asyncio
async def test_partial_batch_reports_attempts_partials_reasons_and_final_outcome_separately(
    app_client, monkeypatch
):
    calls = _capture(monkeypatch)
    headers = await _register_device(app_client, "trd-partial")
    seen = _install_translator(
        monkeypatch,
        lambda tokens: json.dumps(
            [
                _entry(tokens[0], "Oil Made From Rapeseed"),
                _entry(tokens[1], "Unverified Guess", confidence=0.2),
            ]
        ),
    )

    resp = await _ocr(app_client, headers)
    assert resp.status_code == 200
    assert len(seen) == 1  # ONE provider call for the whole request

    (diag,) = calls
    # (a) attempts
    assert diag["ingredientTranslationAttempted"] is True
    assert diag["ingredientTranslationBatches"] == 1
    # (b) partial outcome, by reason
    assert diag["ingredientTranslationReliableCount"] == 1
    assert diag["ingredientTranslationUnreliableCount"] == 1
    assert diag["ingredientTranslationPartialBatches"] == 1
    assert diag["ingredientTranslationFailedBatches"] == 0
    assert diag["ingredientTranslationReasons"] == {"lowConfidence": 1}
    assert "ingredientTranslationProviderFailures" not in diag or diag["ingredientTranslationProviderFailures"] is None
    # (c) final translation outcome (distinct from the request `outcome`)
    assert diag["translationOutcome"] == "partial"
    assert diag["outcome"] == "success"
    # The final per-entry state and the batch counters agree.
    assert diag["untranslatedIngredientCount"] == diag["ingredientTranslationUnreliableCount"] == 1


@pytest.mark.asyncio
async def test_public_response_never_carries_rejection_reasons_or_categories(app_client, monkeypatch):
    _capture(monkeypatch)
    headers = await _register_device(app_client, "trd-public")
    _install_translator(
        monkeypatch,
        lambda tokens: json.dumps(
            [_entry(tokens[0], "Oil Made From Rapeseed"), _entry(tokens[1], "N/A", confidence=0.9)]
        ),
    )

    resp = await _ocr(app_client, headers)
    assert resp.status_code == 200
    body = resp.json()

    uncertain = [ing for ing in body["ingredients"] if ing["identityUncertain"]]
    assert uncertain, "the rejected entry must still surface as identity-uncertain"
    # `TRANSLATION_UNRELIABLE` stays the ONLY public reason for a rejected translation.
    assert {ing["uncertaintyReason"] for ing in uncertain} == {"TRANSLATION_UNRELIABLE"}

    forbidden_strings = set(REJECTION_KEYS) | set(PROVIDER_FAILURE_KEYS)
    forbidden_keys = {
        "rejectionReason",
        "translationFailureReason",
        "translationOutcome",
        "ingredientTranslationReasons",
        "labelTranslationFailureReason",
        "providerFailureCategory",
    }
    for item in _walk(body):
        assert item not in forbidden_strings, item
        assert item not in forbidden_keys, item


@pytest.mark.asyncio
async def test_public_404_error_body_never_carries_rejection_reasons(app_client, monkeypatch):
    calls = _capture(monkeypatch)
    headers = await _register_device(app_client, "trd-public-404")
    _install_translator(
        monkeypatch,
        lambda tokens: json.dumps([_entry(t, "Unverified Guess", confidence=0.2) for t in tokens]),
    )
    monkeypatch.setattr(food_analysis_module, "_ingredients_group_is_complete", lambda *a, **k: False)

    resp = await _ocr(app_client, headers)
    assert resp.status_code == 404
    forbidden = set(REJECTION_KEYS) | set(PROVIDER_FAILURE_KEYS) | {"ingredientTranslationReasons", "translationOutcome"}
    for item in _walk(resp.json()):
        assert item not in forbidden, item

    (diag,) = calls
    assert diag["outcome"] == "partial"
    assert diag["ingredientTranslationReasons"] == {"lowConfidence": 2}  # preserved across the raise
    assert diag["translationOutcome"] == "failed"


# --- One scenario per failure class, end to end ---------------------------------


def _invalid_entry(tokens):
    return json.dumps([{**_entry(t, "Oil Made From Rapeseed"), "junk": 1} for t in tokens])


_SCENARIOS = {
    "providerUnavailable": lambda tokens: (_ for _ in ()).throw(
        GeminiUnavailableError(f"boom {_MARKER}", category="httpServerError")
    ),
    "malformedResponse-not-json": lambda tokens: "this is not json",
    "malformedResponse-not-a-list": lambda tokens: json.dumps({"not": "a list"}),
    "malformedResponse-invalid-entries": _invalid_entry,
    "noMatchingEntry": lambda tokens: json.dumps([]),
    "lowConfidence": lambda tokens: json.dumps([_entry(t, "Oil Made From Rapeseed", confidence=0.1) for t in tokens]),
    "emptyTranslation": lambda tokens: json.dumps([_entry(t, "N/A") for t in tokens]),
    "languageRejected": lambda tokens: json.dumps([_entry(t, "Lait Entier Complet") for t in tokens]),
    "eNumberMismatch": lambda tokens: json.dumps([_entry(t, "Oil Made From Rapeseed E999") for t in tokens]),
    "numericMismatch": lambda tokens: json.dumps([_entry(t, "Oil Made From Rapeseed 15%") for t in tokens]),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", list(_SCENARIOS))
async def test_every_failure_class_is_counted_under_its_own_reason_only(app_client, monkeypatch, scenario):
    calls = _capture(monkeypatch)
    headers = await _register_device(app_client, f"trd-{scenario}")
    _install_translator(monkeypatch, _SCENARIOS[scenario])

    resp = await _ocr(app_client, headers)
    assert resp.status_code == 200, "a rejected translation never fails the scan"

    (diag,) = calls
    expected_reason = scenario.split("-")[0]
    assert diag["ingredientTranslationReasons"] == {expected_reason: 2}
    assert diag["ingredientTranslationUnreliableCount"] == 2
    assert diag["ingredientTranslationReliableCount"] == 0
    # Whole batch rejected -> a FAILED batch, never a partial one.
    assert diag["ingredientTranslationBatches"] == 1
    assert diag["ingredientTranslationFailedBatches"] == 1
    assert diag["ingredientTranslationPartialBatches"] == 0
    assert diag["translationOutcome"] == "failed"
    if expected_reason == "providerUnavailable":
        assert diag["ingredientTranslationProviderFailures"] == {"httpServerError": 2}
    else:
        assert diag.get("ingredientTranslationProviderFailures") is None
    # No secret/exception message anywhere in what would be written.
    assert _MARKER not in json.dumps(diag, default=str)


@pytest.mark.asyncio
async def test_unconfigured_provider_is_distinguished_from_a_content_rejection(app_client, monkeypatch):
    """No mocking of the provider at all: the suite runs with an empty
    GEMINI_API_KEY, so the REAL `GeminiService` raises its own
    not-configured error -- the category must say so (and only the
    aggregate reason, never a message, is recorded)."""
    calls = _capture(monkeypatch)
    headers = await _register_device(app_client, "trd-not-configured")

    resp = await _ocr(app_client, headers)
    assert resp.status_code == 200
    (diag,) = calls
    assert diag["ingredientTranslationReasons"] == {"providerUnavailable": 2}
    assert diag["ingredientTranslationProviderFailures"] == {"notConfigured": 2}
    assert diag["translationOutcome"] == "failed"


# --- Attempts are not double counted --------------------------------------------


@pytest.mark.asyncio
async def test_rescanning_rejected_text_does_not_report_a_second_attempt(app_client, monkeypatch):
    """A rejected entry is persisted (with an alias) by the first request,
    so a second request for the SAME text makes no provider call: its
    diagnostic must report zero batches / not_attempted, not a repeat of
    the first request's failure."""
    calls = _capture(monkeypatch)
    headers = await _register_device(app_client, "trd-rescan")
    seen = _install_translator(
        monkeypatch,
        lambda tokens: json.dumps([_entry(t, "Unverified Guess", confidence=0.2) for t in tokens]),
    )

    first = await _ocr(app_client, headers)
    assert first.status_code == 200
    second = await _ocr(app_client, headers)
    assert second.status_code == 200

    assert len(seen) == 1, "exactly one provider call across both requests"
    first_diag, second_diag = calls
    assert first_diag["ingredientTranslationBatches"] == 1
    assert first_diag["ingredientTranslationAttempted"] is True
    assert second_diag["ingredientTranslationBatches"] == 0
    assert second_diag["ingredientTranslationAttempted"] is False
    assert second_diag["translationOutcome"] == "not_attempted"
    assert second_diag.get("ingredientTranslationReasons") is None


# --- Whole-label pass ---------------------------------------------------------------

_GERMAN = "Zutaten: Wasser, Zucker, Salz, Natriumbenzoat"
_BARCODE = "7242388496966"


@pytest.mark.asyncio
async def test_strict_whole_label_translation_failure_records_its_reason_and_stays_a_422(
    app_client, monkeypatch
):
    calls = _capture(monkeypatch)
    headers = await _register_device(app_client, "trd-label-strict")

    async def _fake_label(text: str) -> str:
        return json.dumps({"detectedLanguage": "de", "confidence": 0.05, "translatedText": "??"})

    monkeypatch.setattr(gemini_service, "translate_label_text", _fake_label)

    resp = await _ocr(app_client, headers, _GERMAN, barcode=_BARCODE)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "LABEL_TRANSLATION_UNRELIABLE"
    forbidden = set(REJECTION_KEYS) | set(PROVIDER_FAILURE_KEYS)
    for item in _walk(resp.json()):
        assert item not in forbidden, item

    (diag,) = calls
    assert diag["outcome"] == "failed"
    assert diag["errorCode"] == "LABEL_TRANSLATION_UNRELIABLE"
    assert diag["translationAttempted"] is True
    assert diag["translationResult"] == "unreliable_error"
    assert diag["labelTranslationFailureReason"] == "lowConfidence"
    assert diag["translationOutcome"] == "failed"


@pytest.mark.asyncio
async def test_strict_whole_label_provider_failure_records_the_provider_category(app_client, monkeypatch):
    calls = _capture(monkeypatch)
    headers = await _register_device(app_client, "trd-label-provider")

    async def _raise(text: str) -> str:
        raise GeminiUnavailableError(f"boom {_MARKER}", category="httpAuth")

    monkeypatch.setattr(gemini_service, "translate_label_text", _raise)

    resp = await _ocr(app_client, headers, _GERMAN, barcode=_BARCODE)
    assert resp.status_code == 422
    assert _MARKER not in resp.text and "httpAuth" not in resp.text

    (diag,) = calls
    assert diag["labelTranslationFailureReason"] == "providerUnavailable"
    assert diag["labelTranslationProviderFailureCategory"] == "httpAuth"
    assert _MARKER not in json.dumps(diag, default=str)


@pytest.mark.asyncio
async def test_non_strict_whole_label_fallback_records_its_reason_and_stays_a_200(app_client, monkeypatch):
    calls = _capture(monkeypatch)
    headers = await _register_device(app_client, "trd-label-fallback")

    async def _fake_label(text: str) -> str:
        return json.dumps(
            {"detectedLanguage": "de", "confidence": 0.9, "translatedText": "Water, Sugar, Salt (E999)"}
        )

    monkeypatch.setattr(gemini_service, "translate_label_text", _fake_label)

    resp = await _ocr(app_client, headers, _GERMAN)
    assert resp.status_code == 200
    (diag,) = calls
    assert diag["translationResult"] == "unreliable_fallback"
    assert diag["labelTranslationFailureReason"] == "eNumberMismatch"
    assert diag["translationOutcome"] == "failed"


# --- Nothing is persisted / no public schema field --------------------------------------


def test_no_rejection_reason_field_exists_on_any_model_or_public_schema():
    from app.models.ingredient import Ingredient
    from app.models.ingredient_alias import IngredientAlias
    from app.models.product import Product
    from app.schemas.ingredient import IngredientOut
    from app.schemas.scan import FullProductAnalysisOut

    needles = ("reject", "failure", "provider_failure", "translation_reason", "outcome")
    for model in (Ingredient, IngredientAlias, Product):
        for column in model.__table__.columns:
            assert not any(n in column.name for n in needles), (model.__name__, column.name)
    for schema in (IngredientOut, FullProductAnalysisOut):
        for name in schema.model_fields:
            assert not any(n in name for n in needles), (schema.__name__, name)
    assert IngredientOut.model_fields["uncertainty_reason"].annotation == (str | None)


# --- A failing diagnostic write / counter builder never changes a scan ---------------------


async def _partial_scan_responses(app_client, monkeypatch, device: str):
    headers = await _register_device(app_client, device)
    _install_translator(
        monkeypatch,
        lambda tokens: json.dumps(
            [_entry(t, "Oil Made From Rapeseed" if t == _TOKENS[0] else "N/A") for t in tokens]
        ),
    )
    return await _ocr(app_client, headers)


def _assert_healthy_partial_scan(resp) -> None:
    assert resp.status_code == 200
    names = {ing["commonName"] for ing in resp.json()["ingredients"]}
    assert "Oil Made From Rapeseed" in names
    assert any(ing["uncertaintyReason"] == "TRANSLATION_UNRELIABLE" for ing in resp.json()["ingredients"])


def _enable_real_writer(monkeypatch, path) -> None:
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_ENABLED", True)
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_PATH", str(path))
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_MAX_BYTES", 1024 * 1024)
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_BACKUP_COUNT", 1)


@pytest.mark.asyncio
async def test_unwritable_diagnostics_path_does_not_change_the_response(app_client, monkeypatch, tmp_path):
    blocker = tmp_path / "not_a_directory"
    blocker.write_text("x")
    _enable_real_writer(monkeypatch, blocker / "scan.jsonl")  # parent is a FILE -> every write fails

    resp = await _partial_scan_responses(app_client, monkeypatch, "trd-unwritable")
    _assert_healthy_partial_scan(resp)
    assert not (blocker / "scan.jsonl").exists()


@pytest.mark.asyncio
async def test_writer_internals_raising_do_not_change_the_response(app_client, monkeypatch, tmp_path):
    _enable_real_writer(monkeypatch, tmp_path / "scan.jsonl")

    def _boom(*args, **kwargs):
        raise OSError(f"disk on fire {_MARKER}")

    monkeypatch.setattr(scan_diagnostics, "_append_locked", _boom)

    resp = await _partial_scan_responses(app_client, monkeypatch, "trd-writer-raises")
    _assert_healthy_partial_scan(resp)


@pytest.mark.asyncio
async def test_record_function_raising_does_not_change_success_or_error_responses(app_client, monkeypatch):
    def _boom(**fields):
        raise RuntimeError(f"diagnostics exploded {_MARKER}")

    monkeypatch.setattr(scan_module, "record_scan_diagnostic", _boom)

    resp = await _partial_scan_responses(app_client, monkeypatch, "trd-record-raises")
    _assert_healthy_partial_scan(resp)

    # The 404 path (real domain error) must stay a 404, not become a 500.
    monkeypatch.setattr(food_analysis_module, "_ingredients_group_is_complete", lambda *a, **k: False)
    headers = await _register_device(app_client, "trd-record-raises-404")
    resp = await _ocr(app_client, headers, "Ingredients: Ulei de rapiță, Aluat acrisor")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "PRODUCT_NOT_FOUND"


@pytest.mark.asyncio
async def test_counter_builder_raising_does_not_change_success_or_error_responses(app_client, monkeypatch):
    calls = _capture(monkeypatch)

    def _boom(result):
        raise RuntimeError(f"builder exploded {_MARKER}")

    monkeypatch.setattr(scan_module, "_build_translation_fields", _boom)

    resp = await _partial_scan_responses(app_client, monkeypatch, "trd-builder-raises")
    _assert_healthy_partial_scan(resp)
    (diag,) = calls
    # The scan is still diagnosed; only the translation fields are dropped.
    assert diag["outcome"] == "success"
    assert "translationOutcome" not in diag and "ingredientTranslationReasons" not in diag

    monkeypatch.setattr(food_analysis_module, "_ingredients_group_is_complete", lambda *a, **k: False)
    headers = await _register_device(app_client, "trd-builder-raises-404")
    resp = await _ocr(app_client, headers, "Ingredients: Ulei de rapiță, Aluat acrisor")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "PRODUCT_NOT_FOUND"
    assert calls[-1]["outcome"] == "partial"
    assert "translationOutcome" not in calls[-1]


# --- The line actually written to disk: privacy + size -----------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["provider-exception", "marker-in-translation"])
async def test_written_line_never_contains_ingredient_text_or_exception_messages(
    app_client, monkeypatch, tmp_path, failure
):
    path = tmp_path / "scan.jsonl"
    _enable_real_writer(monkeypatch, path)
    headers = await _register_device(app_client, f"trd-privacy-{failure}")

    marker_token = f"Compus {_MARKER} Foarte Necunoscut"
    raw_text = f"Ingredients: Ulei de rapiță, {marker_token}"

    def _respond(tokens):
        if failure == "provider-exception":
            raise GeminiUnavailableError(f"upstream said {_MARKER}", category="transport")
        return json.dumps(
            [
                _entry(tokens[0], f"Oil Made From Rapeseed {_MARKER}"),  # marker echoed in a translation
                _entry(tokens[1], f"Unverified {_MARKER}", confidence=0.2),
            ]
        )

    _install_translator(monkeypatch, _respond)

    resp = await _ocr(app_client, headers, raw_text)
    assert resp.status_code == 200

    raw = path.read_text(encoding="utf-8")
    assert raw.count("\n") == 1, "exactly one line for one scan"
    assert len(raw.encode("utf-8")) < 2048
    assert _MARKER not in raw
    assert "rapiță" not in raw and "Ulei" not in raw and "Necunoscut" not in raw and "Oil Made" not in raw

    payload = json.loads(raw)
    assert payload["operation"] == "scan_ocr_text"
    assert payload["translationOutcome"] in {"failed", "partial"}
    reasons = payload["ingredientTranslationReasons"]
    assert set(reasons) <= set(REJECTION_KEYS)
    assert all(type(v) is int for v in reasons.values())
    # Only counters / closed-vocabulary enums / bounded language codes.
    for key, value in payload.items():
        if key.startswith(("ingredientTranslation", "translation", "labelTranslation")):
            assert isinstance(value, (int, bool, str, dict, list)), key
