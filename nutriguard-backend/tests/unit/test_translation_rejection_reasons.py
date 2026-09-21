"""
Internal rejection REASONS for translations (issue #21, section 4):
every failure class is tested independently, plus check-order
precedence, verdict parity with the pre-existing boolean check, partial
batches, the provider sub-categories behind "provider unavailable", and
the whole-label (`label_language`) reason side channel.

The reasons are diagnostics only -- nothing here changes which
translation is accepted. No DB, no network: every Gemini call is mocked.
"""
import json
import math

import httpx
import pytest

from app.core.exceptions import TranslationUnreliableError
from app.integrations import gemini as gemini_module
from app.integrations.gemini import GeminiService, GeminiUnavailableError, gemini_service
from app.services import ingredient_translation as it
from app.services.ingredient_translation import (
    IngredientTokenTranslation,
    translate_ingredient_tokens,
)
from app.services.label_language import resolve_label_text
from app.services.translation_rejection import (
    PROVIDER_FAILURE_KEYS,
    REJECTION_KEYS,
    ProviderFailureCategory,
    TranslationRejection,
    component_outcome,
    count_pairs,
    merge_count_pairs,
    provider_failure_category,
    sparse_counts,
    zero_filled_counts,
)

# Marker that must never surface in any reason/category value.
_MARKER = "ZQXMARKER7731"

# Captured at import time: `_service_with_transport` monkeypatches
# `httpx.AsyncClient` itself, so a second call in one test must not wrap
# the first call's factory.
_REAL_ASYNC_CLIENT = httpx.AsyncClient

_RO = "Ulei de rapiță"
_RO_TRANSLATED = "Oil Made From Rapeseed"


def _entry(original: str, translated: str, *, confidence: float = 0.9, detected: str = "ro") -> dict:
    return {
        "originalText": original,
        "detectedLanguage": detected,
        "confidence": confidence,
        "translatedText": translated,
    }


def _respond_with(monkeypatch, payload) -> None:
    async def _fake(targets, *, context=None) -> str:
        return payload if isinstance(payload, str) else json.dumps(payload)

    monkeypatch.setattr(gemini_service, "translate_ingredient_list", _fake)


# --- Vocabulary -------------------------------------------------------------


def test_reason_vocabularies_are_closed_camelcase_and_text_free():
    assert len(set(REJECTION_KEYS)) == len(REJECTION_KEYS) == len(TranslationRejection)
    assert len(set(PROVIDER_FAILURE_KEYS)) == len(PROVIDER_FAILURE_KEYS) == len(ProviderFailureCategory)
    for key in (*REJECTION_KEYS, *PROVIDER_FAILURE_KEYS):
        assert key.isidentifier() and key[0].islower() and "_" not in key


def test_zero_filled_and_sparse_counts_drop_unknown_keys():
    observed = {"lowConfidence": 2, _MARKER: 9, "eNumberMismatch": 0}
    filled = zero_filled_counts(REJECTION_KEYS, observed)
    assert tuple(filled) == REJECTION_KEYS
    assert filled["lowConfidence"] == 2 and filled["providerUnavailable"] == 0
    assert _MARKER not in filled
    assert sparse_counts(REJECTION_KEYS, observed) == {"lowConfidence": 2}


def test_count_pairs_and_merge_are_deterministic():
    assert count_pairs({"b": 1, "a": 2, "z": 0}) == (("a", 2), ("b", 1))
    assert merge_count_pairs((("a", 1),), (("a", 2), ("b", 1))) == (("a", 3), ("b", 1))


def test_component_outcome_labels():
    assert component_outcome(3, 0) == "succeeded"
    assert component_outcome(0, 3) == "failed"
    assert component_outcome(2, 1) == "partial"


# --- One test PER failure class (through translate_ingredient_tokens) -------


@pytest.mark.asyncio
async def test_reliable_result_has_no_reason(monkeypatch):
    _respond_with(monkeypatch, [_entry(_RO, _RO_TRANSLATED)])
    (result,) = await translate_ingredient_tokens([_RO])
    assert result.reliable is True
    assert result.rejection_reason is None
    assert result.provider_failure_category is None


@pytest.mark.asyncio
async def test_provider_unavailable_reason_and_category(monkeypatch):
    async def _raise(targets, **kwargs):
        raise GeminiUnavailableError(f"boom {_MARKER}", category="httpRateLimited")

    monkeypatch.setattr(gemini_service, "translate_ingredient_list", _raise)
    results = await translate_ingredient_tokens([_RO, "Zahar"])
    for result in results:
        assert result.reliable is False
        assert result.rejection_reason is TranslationRejection.PROVIDER_UNAVAILABLE
        assert result.provider_failure_category is ProviderFailureCategory.HTTP_RATE_LIMITED
        assert _MARKER not in repr(result.rejection_reason) + repr(result.provider_failure_category)


@pytest.mark.asyncio
async def test_provider_unavailable_without_a_category_is_unknown(monkeypatch):
    async def _raise(targets, **kwargs):
        raise GeminiUnavailableError("plain, legacy-style raise")

    monkeypatch.setattr(gemini_service, "translate_ingredient_list", _raise)
    (result,) = await translate_ingredient_tokens([_RO])
    assert result.rejection_reason is TranslationRejection.PROVIDER_UNAVAILABLE
    assert result.provider_failure_category is ProviderFailureCategory.UNKNOWN


@pytest.mark.asyncio
async def test_non_json_response_is_malformed(monkeypatch):
    _respond_with(monkeypatch, "this is not json")
    results = await translate_ingredient_tokens([_RO, "Zahar"])
    assert {r.rejection_reason for r in results} == {TranslationRejection.MALFORMED_RESPONSE}
    assert all(r.provider_failure_category is None for r in results)


@pytest.mark.asyncio
async def test_none_response_is_malformed(monkeypatch):
    async def _fake(targets, *, context=None):
        return None

    monkeypatch.setattr(gemini_service, "translate_ingredient_list", _fake)
    (result,) = await translate_ingredient_tokens([_RO])
    assert result.rejection_reason is TranslationRejection.MALFORMED_RESPONSE


@pytest.mark.asyncio
async def test_non_list_json_is_malformed(monkeypatch):
    _respond_with(monkeypatch, {"not": "a list"})
    (result,) = await translate_ingredient_tokens([_RO])
    assert result.rejection_reason is TranslationRejection.MALFORMED_RESPONSE


@pytest.mark.asyncio
async def test_missing_entry_is_no_matching_entry(monkeypatch):
    _respond_with(monkeypatch, [_entry("Semințe de mac", "Made With Poppy Seeds")])
    (result,) = await translate_ingredient_tokens([_RO])
    assert result.reliable is False
    assert result.rejection_reason is TranslationRejection.NO_MATCHING_ENTRY


@pytest.mark.asyncio
async def test_empty_list_response_leaves_every_target_without_a_matching_entry(monkeypatch):
    _respond_with(monkeypatch, [])
    results = await translate_ingredient_tokens([_RO, "Zahar"])
    assert {r.rejection_reason for r in results} == {TranslationRejection.NO_MATCHING_ENTRY}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "broken",
    [
        {**_entry(_RO, _RO_TRANSLATED), "unexpectedField": "x"},  # extra key (extra="forbid")
        {"originalText": _RO, "detectedLanguage": "ro", "confidence": 0.9},  # missing field
        _entry(_RO, _RO_TRANSLATED, confidence=1.5),  # out-of-range confidence
        _entry(_RO, ""),  # empty translatedText violates min_length=1
        _entry(_RO, _RO_TRANSLATED, confidence=float("nan")),  # NaN fails the ge/le bound
    ],
)
async def test_schema_invalid_entry_attributable_to_a_target_is_malformed_not_missing(monkeypatch, broken):
    """DECISION: a per-entry schema failure whose `originalText` is still
    readable and matches a target is `MALFORMED_RESPONSE` ("the model
    answered, in a bad shape"), NOT `NO_MATCHING_ENTRY` ("the model never
    answered"). The entry is discarded either way (verdict unchanged)."""
    _respond_with(monkeypatch, [broken, _entry("Semințe de mac", "Made With Poppy Seeds")])
    results = await translate_ingredient_tokens([_RO, "Semințe de mac"])
    assert results[0].reliable is False
    assert results[0].translated_text is None
    assert results[0].rejection_reason is TranslationRejection.MALFORMED_RESPONSE
    # Siblings are unaffected.
    assert results[1].reliable is True
    assert results[1].rejection_reason is None


@pytest.mark.asyncio
async def test_schema_invalid_entry_without_a_readable_original_text_is_no_matching_entry(monkeypatch):
    """Nothing to attribute it to -> the target is simply unmatched."""
    _respond_with(
        monkeypatch,
        [
            {"detectedLanguage": "ro", "confidence": 0.9, "translatedText": "x"},  # no originalText
            "not even an object",
            {"originalText": 123, "detectedLanguage": "ro", "confidence": 0.9, "translatedText": "x"},
        ],
    )
    (result,) = await translate_ingredient_tokens([_RO])
    assert result.rejection_reason is TranslationRejection.NO_MATCHING_ENTRY


@pytest.mark.asyncio
async def test_schema_invalid_entry_for_an_unknown_target_does_not_taint_other_targets(monkeypatch):
    _respond_with(monkeypatch, [{**_entry("Something Else", "x"), "extra": 1}])
    (result,) = await translate_ingredient_tokens([_RO])
    assert result.rejection_reason is TranslationRejection.NO_MATCHING_ENTRY


@pytest.mark.asyncio
async def test_duplicate_targets_consume_malformed_entries_one_for_one(monkeypatch):
    """Two identical targets, one invalid response entry and no valid one:
    the first occurrence takes the malformed attribution, the second has
    nothing left and is a missing entry."""
    _respond_with(monkeypatch, [{**_entry(_RO, _RO_TRANSLATED), "extra": 1}])
    results = await translate_ingredient_tokens([_RO, _RO])
    assert [r.rejection_reason for r in results] == [
        TranslationRejection.MALFORMED_RESPONSE,
        TranslationRejection.NO_MATCHING_ENTRY,
    ]


@pytest.mark.asyncio
async def test_confidence_below_threshold_is_low_confidence(monkeypatch):
    _respond_with(monkeypatch, [_entry(_RO, _RO_TRANSLATED, confidence=0.4)])
    (result,) = await translate_ingredient_tokens([_RO])
    assert result.rejection_reason is TranslationRejection.LOW_CONFIDENCE
    assert result.confidence == 0.4  # unchanged existing behavior: still reported


@pytest.mark.asyncio
async def test_confidence_exactly_at_threshold_is_accepted(monkeypatch):
    _respond_with(monkeypatch, [_entry(_RO, _RO_TRANSLATED, confidence=0.55)])
    (result,) = await translate_ingredient_tokens([_RO])
    assert result.reliable is True and result.rejection_reason is None


def test_non_finite_confidence_is_low_confidence():
    """`float("nan")`/`inf` can never reach this check via the response
    schema (Pydantic's bounds reject them first -> `MALFORMED_RESPONSE`,
    see the NaN case above); `_first_rejection` still guards it directly."""
    for bad in (float("nan"), float("inf"), float("-inf")):
        assert not math.isfinite(bad)
        assert (
            it._first_rejection(_RO, _RO_TRANSLATED, bad, known_normalized_names=frozenset())
            is TranslationRejection.LOW_CONFIDENCE
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("translated", ["N/A", "null", "   "])
async def test_placeholder_or_blank_translation_is_empty_translation(monkeypatch, translated):
    _respond_with(monkeypatch, [_entry(_RO, translated, confidence=0.95)])
    (result,) = await translate_ingredient_tokens([_RO])
    assert result.rejection_reason is TranslationRejection.EMPTY_TRANSLATION


@pytest.mark.asyncio
async def test_translation_not_independently_english_is_language_rejected(monkeypatch):
    _respond_with(monkeypatch, [_entry("Lait entier", "Lait Entier Complet", confidence=0.95, detected="en")])
    (result,) = await translate_ingredient_tokens(["Lait entier"])
    assert result.rejection_reason is TranslationRejection.LANGUAGE_REJECTED
    assert result.detected_language == "en"  # Gemini's own claim is still kept for diagnostics


@pytest.mark.asyncio
async def test_language_rejection_without_catalog_evidence_but_accepted_with_it(monkeypatch):
    _respond_with(monkeypatch, [_entry("MILK", "Milk", confidence=0.97, detected="en")])
    (rejected,) = await translate_ingredient_tokens(["MILK"], known_normalized_names=frozenset())
    assert rejected.rejection_reason is TranslationRejection.LANGUAGE_REJECTED
    (accepted,) = await translate_ingredient_tokens(["MILK"], known_normalized_names=frozenset({"milk"}))
    assert accepted.reliable is True and accepted.rejection_reason is None


@pytest.mark.asyncio
async def test_e_number_mismatch(monkeypatch):
    _respond_with(
        monkeypatch,
        [_entry("Benzoat de sodiu (E211)", "Sodium Benzoate, Made From Salt", confidence=0.9)],
    )
    (result,) = await translate_ingredient_tokens(["Benzoat de sodiu (E211)"])
    assert result.rejection_reason is TranslationRejection.E_NUMBER_MISMATCH


@pytest.mark.asyncio
async def test_numeric_or_unit_mismatch(monkeypatch):
    _respond_with(monkeypatch, [_entry("Zahar 12%", "Sugar Made From 15% Beet", confidence=0.9)])
    (changed_value,) = await translate_ingredient_tokens(["Zahar 12%"])
    assert changed_value.rejection_reason is TranslationRejection.NUMERIC_MISMATCH

    _respond_with(monkeypatch, [_entry("Zahar 12%", "Sugar Made From 12 Beet", confidence=0.9)])
    (dropped_unit,) = await translate_ingredient_tokens(["Zahar 12%"])
    assert dropped_unit.rejection_reason is TranslationRejection.NUMERIC_MISMATCH


# --- Check-order precedence: the FIRST failing check in the existing order --


@pytest.mark.parametrize(
    ("source", "translated", "confidence", "expected"),
    [
        # low confidence + placeholder + language + E + numeric all fail -> confidence first
        ("Zahar 12% E211", "N/A", 0.1, TranslationRejection.LOW_CONFIDENCE),
        # placeholder + language + E + numeric fail -> empty/placeholder first
        ("Zahar 12% E211", "N/A", 0.9, TranslationRejection.EMPTY_TRANSLATION),
        # language + E + numeric fail -> language first
        ("Lait entier 12% E211", "Lait Entier Complet", 0.9, TranslationRejection.LANGUAGE_REJECTED),
        # E + numeric fail (translation is English) -> E-number first
        ("Zahar 12% E211", "Sugar Made From 15% Beet", 0.9, TranslationRejection.E_NUMBER_MISMATCH),
        # only numeric fails
        ("Zahar 12% E211", "Sugar Made From 15% Beet E211", 0.9, TranslationRejection.NUMERIC_MISMATCH),
    ],
)
def test_first_failing_check_in_the_existing_order_wins(source, translated, confidence, expected):
    assert (
        it._first_rejection(source, translated, confidence, known_normalized_names=frozenset()) is expected
    )


@pytest.mark.parametrize(
    ("source", "translated", "confidence", "known"),
    [
        (_RO, _RO_TRANSLATED, 0.9, frozenset()),
        (_RO, _RO_TRANSLATED, 0.54, frozenset()),
        (_RO, _RO_TRANSLATED, 0.55, frozenset()),
        (_RO, "N/A", 0.9, frozenset()),
        (_RO, "  ", 0.9, frozenset()),
        ("MILK", "Milk", 0.9, frozenset()),
        ("MILK", "Milk", 0.9, frozenset({"milk"})),
        ("Benzoat (E211)", "Sodium Benzoate, Made From Salt", 0.9, frozenset()),
        ("Zahar 12%", "Sugar Made From 15% Beet", 0.9, frozenset()),
        ("Zahar 12%", "Sugar Made From 12% Beet", 0.9, frozenset()),
        (_RO, _RO_TRANSLATED, float("nan"), frozenset()),
    ],
)
def test_boolean_wrapper_verdict_matches_the_reason_split(source, translated, confidence, known):
    """The boolean `_translation_is_reliable` and the reason-returning
    `_first_rejection` can never disagree: the reason split only adds
    detail, never changes which translations are accepted."""
    reliable = it._translation_is_reliable(source, translated, confidence, known_normalized_names=known)
    rejection = it._first_rejection(source, translated, confidence, known_normalized_names=known)
    assert reliable is (rejection is None)


def test_existing_positional_construction_still_works_and_defaults_to_no_reason():
    result = IngredientTokenTranslation("a", None, "other", None, False)
    assert result.rejection_reason is None and result.provider_failure_category is None
    assert IngredientTokenTranslation("a", "b", "ro", 0.9, True).rejection_reason is None


# --- Partial batches --------------------------------------------------------


@pytest.mark.asyncio
async def test_mixed_batch_reports_a_distinct_reason_per_entry_and_keeps_order(monkeypatch):
    targets = [
        _RO,  # reliable
        "Semințe de mac",  # low confidence
        "Benzoat de sodiu (E211)",  # E-number mismatch
        "Zahar 12%",  # numeric mismatch
        "Lait entier",  # language rejected
        "Compus Necunoscut",  # missing from the response
        "Făină de grâu",  # schema-invalid entry -> malformed
    ]
    _respond_with(
        monkeypatch,
        [
            _entry(_RO, _RO_TRANSLATED),
            _entry("Semințe de mac", "Made With Poppy Seeds", confidence=0.2),
            _entry("Benzoat de sodiu (E211)", "Sodium Benzoate, Made From Salt"),
            _entry("Zahar 12%", "Sugar Made From 15% Beet"),
            _entry("Lait entier", "Lait Entier Complet", detected="en"),
            {**_entry("Făină de grâu", "Flour Made From Wheat"), "junk": True},
        ],
    )
    results = await translate_ingredient_tokens(targets)

    assert [r.original_text for r in results] == targets
    assert [r.reliable for r in results] == [True, False, False, False, False, False, False]
    assert [r.rejection_reason for r in results] == [
        None,
        TranslationRejection.LOW_CONFIDENCE,
        TranslationRejection.E_NUMBER_MISMATCH,
        TranslationRejection.NUMERIC_MISMATCH,
        TranslationRejection.LANGUAGE_REJECTED,
        TranslationRejection.NO_MATCHING_ENTRY,
        TranslationRejection.MALFORMED_RESPONSE,
    ]
    # Every rejected result carries exactly one reason; provider category
    # is only ever set for providerUnavailable.
    assert all(r.rejection_reason is not None for r in results if not r.reliable)
    assert all(r.provider_failure_category is None for r in results)


# --- Provider sub-categories (GeminiService._call) ---------------------------


def _service_with_transport(monkeypatch, handler_or_exc):
    """A configured `GeminiService` whose HTTP layer is an in-process
    `httpx.MockTransport` -- no network is ever opened."""
    def _handler(request: httpx.Request) -> httpx.Response:
        if isinstance(handler_or_exc, Exception):
            raise handler_or_exc
        return handler_or_exc

    def _factory(**kwargs):
        return _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(_handler), **kwargs)

    monkeypatch.setattr(gemini_module.httpx, "AsyncClient", _factory)
    service = GeminiService()
    service._api_key = "not-a-real-key-for-tests"
    return service


@pytest.mark.asyncio
async def test_not_configured_category():
    service = GeminiService()
    service._api_key = ""
    with pytest.raises(GeminiUnavailableError) as excinfo:
        await service.translate_ingredient_list(["x"])
    assert provider_failure_category(excinfo.value) is ProviderFailureCategory.NOT_CONFIGURED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, ProviderFailureCategory.HTTP_AUTH),
        (403, ProviderFailureCategory.HTTP_AUTH),
        (429, ProviderFailureCategory.HTTP_RATE_LIMITED),
        (400, ProviderFailureCategory.HTTP_CLIENT_ERROR),
        (404, ProviderFailureCategory.HTTP_CLIENT_ERROR),
        (500, ProviderFailureCategory.HTTP_SERVER_ERROR),
        (503, ProviderFailureCategory.HTTP_SERVER_ERROR),
        (302, ProviderFailureCategory.HTTP_OTHER),
    ],
)
async def test_http_status_categories(monkeypatch, status, expected):
    service = _service_with_transport(monkeypatch, httpx.Response(status, text=f"body {_MARKER}"))
    with pytest.raises(GeminiUnavailableError) as excinfo:
        await service.translate_ingredient_list(["x"])
    assert provider_failure_category(excinfo.value) is expected


@pytest.mark.asyncio
async def test_transport_and_timeout_categories(monkeypatch):
    service = _service_with_transport(monkeypatch, httpx.ConnectError(f"refused {_MARKER}"))
    with pytest.raises(GeminiUnavailableError) as excinfo:
        await service.translate_ingredient_list(["x"])
    assert provider_failure_category(excinfo.value) is ProviderFailureCategory.TRANSPORT

    service = _service_with_transport(monkeypatch, httpx.ReadTimeout(f"slow {_MARKER}"))
    with pytest.raises(GeminiUnavailableError) as excinfo:
        await service.translate_ingredient_list(["x"])
    assert provider_failure_category(excinfo.value) is ProviderFailureCategory.TIMEOUT


@pytest.mark.asyncio
async def test_unparsable_provider_response_category(monkeypatch):
    service = _service_with_transport(monkeypatch, httpx.Response(200, json={"candidates": []}))
    with pytest.raises(GeminiUnavailableError) as excinfo:
        await service.translate_ingredient_list(["x"])
    assert provider_failure_category(excinfo.value) is ProviderFailureCategory.UNPARSABLE_RESPONSE


def test_every_category_gemini_can_raise_maps_to_a_known_non_unknown_member():
    raised = {
        getattr(gemini_module, name)
        for name in dir(gemini_module)
        if name.startswith("FAILURE_") and name != "FAILURE_UNKNOWN"
    }
    assert raised == {c.value for c in ProviderFailureCategory if c is not ProviderFailureCategory.UNKNOWN}


def test_provider_category_never_reads_the_exception_message():
    exc = GeminiUnavailableError(f"contains {_MARKER}", category=_MARKER)  # unrecognized label
    assert provider_failure_category(exc) is ProviderFailureCategory.UNKNOWN
    assert provider_failure_category(RuntimeError(_MARKER)) is ProviderFailureCategory.UNKNOWN


# --- Whole-label (label_language) reason side channel -----------------------

_GERMAN = "Zutaten: Wasser, Zucker, Salz"


def _label_response(monkeypatch, payload) -> None:
    async def _fake(text: str) -> str:
        return payload if isinstance(payload, str) else json.dumps(payload)

    monkeypatch.setattr(gemini_service, "translate_label_text", _fake)


def _label_payload(**overrides) -> dict:
    return {
        "detectedLanguage": "de",
        "confidence": 0.9,
        "translatedText": "Water, Sugar, Salt",
        **overrides,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ("not json", TranslationRejection.MALFORMED_RESPONSE),
        ({"detectedLanguage": "de"}, TranslationRejection.MALFORMED_RESPONSE),
        (_label_payload(confidence=0.1), TranslationRejection.LOW_CONFIDENCE),
        (_label_payload(translatedText="N/A"), TranslationRejection.EMPTY_TRANSLATION),
        (_label_payload(translatedText="Zutaten Wasser Zucker Salz"), TranslationRejection.LANGUAGE_REJECTED),
        (_label_payload(translatedText="Water, Sugar, Salt (E211)"), TranslationRejection.E_NUMBER_MISMATCH),
    ],
)
async def test_whole_label_failure_reason_is_carried_internally(monkeypatch, response, expected):
    _label_response(monkeypatch, response)

    with pytest.raises(TranslationUnreliableError) as excinfo:
        await resolve_label_text(_GERMAN)  # strict (default)
    assert (excinfo.value.diagnostic_metadata or {}).get("label_translation_failure_reason") == expected.value

    fallback = await resolve_label_text(_GERMAN, strict=False)
    assert fallback.status == "translation_unreliable_fallback"
    assert fallback.translation_failure_reason == expected.value
    assert fallback.translation_provider_failure_category is None


@pytest.mark.asyncio
async def test_whole_label_numeric_mismatch_reason(monkeypatch):
    source = "Zutaten: Wasser, Zucker 12%, Salz"
    _label_response(monkeypatch, _label_payload(translatedText="Water, Sugar 15%, Salt"))
    with pytest.raises(TranslationUnreliableError) as excinfo:
        await resolve_label_text(source)
    assert (
        excinfo.value.diagnostic_metadata["label_translation_failure_reason"]
        == TranslationRejection.NUMERIC_MISMATCH.value
    )


@pytest.mark.asyncio
async def test_whole_label_provider_unavailable_carries_category(monkeypatch):
    async def _raise(text: str) -> str:
        raise GeminiUnavailableError(f"boom {_MARKER}", category="httpAuth")

    monkeypatch.setattr(gemini_service, "translate_label_text", _raise)
    fallback = await resolve_label_text(_GERMAN, strict=False)
    assert fallback.translation_failure_reason == TranslationRejection.PROVIDER_UNAVAILABLE.value
    assert fallback.translation_provider_failure_category == ProviderFailureCategory.HTTP_AUTH.value

    with pytest.raises(TranslationUnreliableError) as excinfo:
        await resolve_label_text(_GERMAN)
    assert excinfo.value.diagnostic_metadata["label_translation_provider_failure_category"] == "httpAuth"
    # The PUBLIC message/details are unchanged and never carry the reason,
    # the category, or the underlying exception message.
    public = f"{excinfo.value.message} {excinfo.value.details}"
    assert _MARKER not in public and "httpAuth" not in public and "providerUnavailable" not in public


@pytest.mark.asyncio
async def test_successful_whole_label_translation_has_no_failure_reason(monkeypatch):
    _label_response(monkeypatch, _label_payload())
    result = await resolve_label_text(_GERMAN)
    assert result.status == "translated"
    assert result.translation_failure_reason is None
    assert result.translation_provider_failure_category is None
