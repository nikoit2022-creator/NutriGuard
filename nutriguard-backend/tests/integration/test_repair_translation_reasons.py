"""
`app.seed.repair_ingredient_language` -- privacy-safe, additive
translation-failure REASON aggregates in the dry-run report (issue #21,
section 4).

Unlike `test_repair_ingredient_language.py` (which monkeypatches
`translate_ingredient_tokens`), these tests drive the REAL
`ingredient_translation.translate_ingredient_tokens` and only mock the
Gemini call underneath (`gemini_service.translate_ingredient_list`), so
the reason attached to every flagged entry is the one the real
validation actually produced. No network, no `--apply`.
"""
import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.integrations.gemini import GeminiUnavailableError, gemini_service
from app.models.enums import IngredientSource, IngredientVerificationStatus, RiskLevel
from app.models.ingredient import Ingredient
from app.seed import repair_ingredient_language as repair_module
from app.services.ingredient_normalization import normalize_ingredient_name
from app.services.ingredient_translation import IngredientTokenTranslation
from app.services.translation_rejection import (
    PROVIDER_FAILURE_KEYS,
    REJECTION_KEYS,
    ProviderFailureCategory,
    TranslationRejection,
)

_MARKER = "ZQXMARKER7731"

# Foreign-looking OCR rows (detect_language == "other", unambiguous
# segmentation): each becomes a translation candidate.
_RELIABLE = "Ulei de rapiță"
_LOW_CONF = "Semințe de mac"
_E_NUM = "Benzoat de sodiu (E211)"
_NUMERIC = "Zahăr 12%"
_LANG = "Lait entier"
_MISSING = "Compus Necunoscut"
_MALFORMED = "Făină de grâu"

_ALL_CANDIDATES = [_RELIABLE, _LOW_CONF, _E_NUM, _NUMERIC, _LANG, _MISSING, _MALFORMED]


def _entry(original: str, translated: str, *, confidence: float = 0.9, detected: str = "ro") -> dict:
    return {
        "originalText": original,
        "detectedLanguage": detected,
        "confidence": confidence,
        "translatedText": translated,
    }


def _make_ingredient(ingredient_id: str, common_name: str) -> Ingredient:
    return Ingredient(
        id=ingredient_id,
        common_name=common_name,
        normalized_name=normalize_ingredient_name(common_name),
        scientific_name="",
        e_number=None,
        category="Ingredient",
        risk_level=RiskLevel.SAFE,
        risk_assessment_available=False,
        verification_status=IngredientVerificationStatus.UNVERIFIED,
        source=IngredientSource.OCR_HEURISTIC,
        confidence=0.2,
    )


async def _seed(db_engine, names: list[str]) -> None:
    session_factory = async_sessionmaker(bind=db_engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as db:
        for index, name in enumerate(names):
            db.add(_make_ingredient(f"synth_repair_reason_{index}", name))
        await db.commit()


async def _dry_run(db_engine):
    session_factory = async_sessionmaker(bind=db_engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as db:
        return await repair_module.run_repair(db, apply=False)


def _mock_gemini(monkeypatch, *, response=None, raises: Exception | None = None):
    async def _fake(targets, *, context=None) -> str:
        if raises is not None:
            raise raises
        return response if isinstance(response, str) else json.dumps(response)

    monkeypatch.setattr(gemini_service, "translate_ingredient_list", _fake)


def _flagged_by_name(report) -> dict[str, repair_module.IngredientRepairEntry]:
    return {
        e.common_name: e
        for e in report.ingredient_details
        if e.category == repair_module.CATEGORY_FLAGGED_UNRESOLVED_TRANSLATION_FAILED
    }


@pytest.mark.asyncio
async def test_mixed_batch_report_counts_each_reason_and_is_self_consistent(db_engine, monkeypatch):
    await _seed(db_engine, _ALL_CANDIDATES)
    _mock_gemini(
        monkeypatch,
        response=[
            _entry(_RELIABLE, "Oil Made From Rapeseed"),
            _entry(_LOW_CONF, "Made With Poppy Seeds", confidence=0.2),
            _entry(_E_NUM, "Sodium Benzoate, Made From Salt"),
            _entry(_NUMERIC, "Sugar Made From 15% Beet"),
            _entry(_LANG, "Lait Entier Complet", detected="en"),
            {**_entry(_MALFORMED, "Flour Made From Wheat"), "junk": True},
            # _MISSING: deliberately absent from the response
        ],
    )

    report = await _dry_run(db_engine)
    data = report.to_dict()

    # Every candidate really went through the translation branch.
    assert data["ingredientCounts"]["translated"] == 1
    assert data["ingredientCounts"]["flaggedUnresolvedTranslationFailed"] == 6

    reasons = data["translationFailureReasons"]
    assert reasons == {
        "providerUnavailable": 0,
        "malformedResponse": 1,
        "noMatchingEntry": 1,
        "lowConfidence": 1,
        "emptyTranslation": 0,
        "languageRejected": 1,
        "eNumberMismatch": 1,
        "numericMismatch": 1,
        "unspecified": 0,
    }
    # Aggregate is consistent with the existing flagged count.
    assert sum(reasons.values()) == data["ingredientCounts"]["flaggedUnresolvedTranslationFailed"]

    flagged = _flagged_by_name(report)
    assert flagged[_LOW_CONF].translation_failure_reason == TranslationRejection.LOW_CONFIDENCE.value
    assert flagged[_E_NUM].translation_failure_reason == TranslationRejection.E_NUMBER_MISMATCH.value
    assert flagged[_NUMERIC].translation_failure_reason == TranslationRejection.NUMERIC_MISMATCH.value
    assert flagged[_LANG].translation_failure_reason == TranslationRejection.LANGUAGE_REJECTED.value
    assert flagged[_MISSING].translation_failure_reason == TranslationRejection.NO_MATCHING_ENTRY.value
    assert flagged[_MALFORMED].translation_failure_reason == TranslationRejection.MALFORMED_RESPONSE.value
    # Backward compatible: the per-entry `reason` string is unchanged.
    assert {e.reason for e in flagged.values()} == {"TRANSLATION_UNRELIABLE"}

    # Attempt / partial / final semantics for the one batched call.
    assert data["translationAttempt"] == {
        "batches": 1,
        "targets": 7,
        "reliable": 1,
        "rejected": 6,
        "outcome": "partial",
    }
    # No provider failure happened -> every provider category is zero.
    assert set(data["translationProviderFailureCategories"]) == set(PROVIDER_FAILURE_KEYS)
    assert sum(data["translationProviderFailureCategories"].values()) == 0


@pytest.mark.asyncio
async def test_provider_unavailable_run_is_a_failed_batch_with_a_category(db_engine, monkeypatch):
    await _seed(db_engine, [_RELIABLE, _LOW_CONF, _E_NUM])
    _mock_gemini(monkeypatch, raises=GeminiUnavailableError(f"boom {_MARKER}", category="notConfigured"))

    report = await _dry_run(db_engine)
    data = report.to_dict()

    assert data["ingredientCounts"]["flaggedUnresolvedTranslationFailed"] == 3
    assert data["translationFailureReasons"]["providerUnavailable"] == 3
    assert sum(data["translationFailureReasons"].values()) == 3
    assert data["translationProviderFailureCategories"]["notConfigured"] == 3
    assert sum(data["translationProviderFailureCategories"].values()) == 3
    assert data["translationAttempt"] == {
        "batches": 1,
        "targets": 3,
        "reliable": 0,
        "rejected": 3,
        "outcome": "failed",
    }
    # Every entry: providerUnavailable + the sub-category, no text.
    for entry in data["ingredientDetails"]:
        assert entry["translationFailureReason"] == "providerUnavailable"
        assert entry["translationProviderFailureCategory"] == "notConfigured"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected_reason"),
    [
        ("this is not json", "malformedResponse"),
        ({"not": "a list"}, "malformedResponse"),
        ([], "noMatchingEntry"),
    ],
)
async def test_whole_batch_malformed_or_empty_responses_are_counted_separately_from_provider_failure(
    db_engine, monkeypatch, response, expected_reason
):
    await _seed(db_engine, [_RELIABLE, _LOW_CONF])
    _mock_gemini(monkeypatch, response=response)

    data = (await _dry_run(db_engine)).to_dict()
    assert data["translationFailureReasons"][expected_reason] == 2
    assert data["translationFailureReasons"]["providerUnavailable"] == 0
    assert sum(data["translationFailureReasons"].values()) == 2
    assert data["translationAttempt"]["outcome"] == "failed"


@pytest.mark.asyncio
async def test_all_reliable_run_has_zero_reasons_and_succeeded_outcome(db_engine, monkeypatch):
    await _seed(db_engine, [_RELIABLE])
    _mock_gemini(monkeypatch, response=[_entry(_RELIABLE, "Oil Made From Rapeseed")])

    data = (await _dry_run(db_engine)).to_dict()
    assert data["translationFailureReasons"] == {key: 0 for key in REJECTION_KEYS}
    assert data["translationAttempt"] == {
        "batches": 1,
        "targets": 1,
        "reliable": 1,
        "rejected": 0,
        "outcome": "succeeded",
    }


@pytest.mark.asyncio
async def test_no_candidates_means_no_translation_attempt(db_engine, monkeypatch):
    async def _must_not_be_called(targets, *, context=None):
        raise AssertionError("no candidates -> no provider call")

    monkeypatch.setattr(gemini_service, "translate_ingredient_list", _must_not_be_called)
    await _seed(db_engine, ["Sugar and salt"])  # already English

    data = (await _dry_run(db_engine)).to_dict()
    assert data["translationFailureReasons"] == {key: 0 for key in REJECTION_KEYS}
    assert data["translationAttempt"] == {
        "batches": 0,
        "targets": 0,
        "reliable": 0,
        "rejected": 0,
        "outcome": "not_attempted",
    }


@pytest.mark.asyncio
async def test_reason_objects_contain_no_ingredient_text_and_no_marker(db_engine, monkeypatch):
    marker_name = f"Compus {_MARKER} Necunoscut"
    await _seed(db_engine, [marker_name])
    _mock_gemini(monkeypatch, raises=GeminiUnavailableError(f"boom {_MARKER}", category="httpAuth"))

    data = (await _dry_run(db_engine)).to_dict()
    # The pre-existing `ingredientDetails[*].commonName` legitimately
    # carries the row's own name; the NEW aggregate objects must not.
    for key in ("translationFailureReasons", "translationProviderFailureCategories", "translationAttempt"):
        serialized = json.dumps(data[key])
        assert _MARKER not in serialized
        assert marker_name not in serialized
    assert set(data["translationFailureReasons"]) == set(REJECTION_KEYS)
    assert set(data["translationProviderFailureCategories"]) == set(PROVIDER_FAILURE_KEYS)
    entry = data["ingredientDetails"][0]
    assert entry["translationFailureReason"] == "providerUnavailable"
    assert entry["translationProviderFailureCategory"] == "httpAuth"
    # The exception message never reaches the report anywhere.
    assert f"boom {_MARKER}" not in json.dumps(data)


@pytest.mark.asyncio
async def test_existing_report_shape_is_unchanged_additive_only(db_engine, monkeypatch):
    await _seed(db_engine, [_RELIABLE])
    _mock_gemini(monkeypatch, response=[_entry(_RELIABLE, "Oil Made From Rapeseed")])

    data = (await _dry_run(db_engine)).to_dict()
    # Every pre-existing top-level key is still present...
    for key in ("mode", "generatedAt", "ingredientCounts", "ingredientDetails", "productFlaggedCount", "productFlags"):
        assert key in data
    assert set(data["ingredientCounts"]) == {
        "alreadyFine",
        "translated",
        "flaggedUnresolvedAmbiguous",
        "flaggedUnresolvedTranslationFailed",
        "skippedTrustedSource",
    }
    # ...and the only new ones are the three additive aggregates.
    assert set(data) - {
        "mode",
        "generatedAt",
        "ingredientCounts",
        "ingredientDetails",
        "productFlaggedCount",
        "productFlags",
    } == {"translationFailureReasons", "translationProviderFailureCategories", "translationAttempt"}
    # Every pre-existing per-entry key is still present, with two additive ones.
    entry = data["ingredientDetails"][0]
    assert {
        "ingredientId",
        "commonName",
        "normalizedName",
        "source",
        "detectedLanguage",
        "category",
        "reason",
        "proposedTranslation",
        "applied",
    } <= set(entry)
    assert set(entry) - {
        "ingredientId",
        "commonName",
        "normalizedName",
        "source",
        "detectedLanguage",
        "category",
        "reason",
        "proposedTranslation",
        "applied",
    } == {"translationFailureReason", "translationProviderFailureCategory"}
    # A TRANSLATED entry carries no failure reason.
    assert entry["translationFailureReason"] is None
    assert entry["translationProviderFailureCategory"] is None
    json.dumps(data)  # still JSON-serializable


@pytest.mark.asyncio
async def test_legacy_translator_without_reasons_is_bucketed_as_unspecified(db_engine, monkeypatch):
    """A translator that returns `reliable=False` without any reason (an
    injected/legacy one, like the fakes in `test_repair_ingredient_language.py`)
    must still keep `sum(reasons) == flagged count`."""

    async def _legacy(tokens: list[str]) -> list[IngredientTokenTranslation]:
        return [
            IngredientTokenTranslation(
                original_text=t, translated_text=None, detected_language="other", confidence=None, reliable=False
            )
            for t in tokens
        ]

    monkeypatch.setattr(repair_module, "translate_ingredient_tokens", _legacy)
    await _seed(db_engine, [_RELIABLE, _LOW_CONF])

    data = (await _dry_run(db_engine)).to_dict()
    assert data["translationFailureReasons"]["unspecified"] == 2
    assert sum(data["translationFailureReasons"].values()) == 2
    assert sum(data["translationProviderFailureCategories"].values()) == 0


@pytest.mark.asyncio
async def test_provider_reason_without_category_from_a_custom_translator_keeps_sums_consistent(
    db_engine, monkeypatch
):
    async def _custom(tokens: list[str]) -> list[IngredientTokenTranslation]:
        return [
            IngredientTokenTranslation(
                original_text=t,
                translated_text=None,
                detected_language="other",
                confidence=None,
                reliable=False,
                rejection_reason=TranslationRejection.PROVIDER_UNAVAILABLE,
            )
            for t in tokens
        ]

    monkeypatch.setattr(repair_module, "translate_ingredient_tokens", _custom)
    await _seed(db_engine, [_RELIABLE])

    data = (await _dry_run(db_engine)).to_dict()
    assert data["translationFailureReasons"]["providerUnavailable"] == 1
    assert data["translationProviderFailureCategories"][ProviderFailureCategory.UNKNOWN.value] == 1
