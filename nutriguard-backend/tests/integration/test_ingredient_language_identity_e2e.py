"""
DB-backed (real `ingredients`/`ingredient_aliases` tables, via the
`db_session` fixture) end-to-end coverage of the ingredient language/
identity fix, exercising `app.services.ingredient_catalog.
materialize_ingredients` -- the layer where `ingredient_segmentation`
and `ingredient_translation` are actually wired together against the
persistent catalog. Full HTTP-level coverage (through
`POST /scan/ocr-text`, proving the reported mixed-language bug is
fixed) lives in `tests/integration/test_scan_language_e2e.py`.

Every Gemini call is mocked (`gemini_service.translate_ingredient_list`)
-- no test in this file makes a live network call.
"""
import json

import pytest

from app.integrations.gemini import gemini_service
from app.models.enums import (
    ApprovalStatus,
    IngredientSource,
    IngredientVerificationStatus,
    RiskLevel,
)
from app.models.ingredient import Ingredient
from app.repositories import ingredient_alias_repository, ingredient_repository
from app.schemas.ingredient import IngredientOut
from app.services import ingredient_catalog
from app.services.ingredient_normalization import normalize_ingredient_name
from app.services.ocr_normalizer import create_synthetic_ingredient


def _fake_translate(mapping: dict[str, tuple[str, float]]):
    """`mapping`: original text -> (translatedText, confidence). Answers
    in the SAME order the batched call asked for, mirroring the real
    full-list-context call."""

    async def _fake(tokens: list[str], *, context=None) -> str:
        payload = [
            {
                "originalText": t,
                "detectedLanguage": "ro",
                "confidence": mapping[t][1],
                "translatedText": mapping[t][0],
            }
            for t in tokens
        ]
        return json.dumps(payload)

    return _fake


# --- Ambiguous segmentation: never translated, always identity_uncertain ---


@pytest.mark.asyncio
async def test_ambiguous_segmentation_token_is_identity_uncertain_and_never_translated(
    db_session, monkeypatch
):
    """Code-review fix (issue 3): capitalization alone is no longer
    evidence of a broken/concatenated token (see
    `app.services.ingredient_segmentation`'s module docstring) -- of the
    4 originally-reported examples, only "LAPTE proteină din LAPTE"
    ("MILK protein from MILK") is STILL genuinely ambiguous: it repeats
    the same significant word ("lapte"/"LAPTE") twice, a real structural
    signal (`DUPLICATE_TOKEN_FRAGMENT`) that two clauses sharing an
    allergen word were merged. The other 3 are ordinary compound
    ingredient names with allergen emphasis -- see
    `test_previously_over_flagged_examples_are_now_sent_for_translation`
    below for their (changed, correct) new behavior."""

    def _must_not_be_called(tokens, *, context=None):
        raise AssertionError(f"translate_ingredient_list must not be called for {tokens!r}")

    monkeypatch.setattr(gemini_service, "translate_ingredient_list", _must_not_be_called)

    token = "LAPTE proteină din LAPTE"
    synthetic = create_synthetic_ingredient(token)
    materialized, translation_occurred = await ingredient_catalog.materialize_ingredients(
        db_session, [synthetic]
    )

    assert translation_occurred is False
    row = materialized[0]
    assert row.identity_uncertain is True
    assert row.uncertainty_reason == "DUPLICATE_TOKEN_FRAGMENT"
    # Never a fabricated/guessed identity -- the observed OCR text is
    # preserved exactly.
    assert row.common_name == synthetic.common_name
    assert row.verification_status == IngredientVerificationStatus.UNVERIFIED


@pytest.mark.asyncio
async def test_previously_over_flagged_examples_are_now_sent_for_translation(db_session, monkeypatch):
    """Code-review fix (issue 3): "SECARA agenți de creștere" (RYE
    raising agents), "Produs din GRAU" (product from WHEAT), and "ZARA
    pudră" all contain ordinary allergen-emphasis capitalization with no
    other structural evidence of concatenation (no colon, no duplicated
    word, under the 7-word length threshold) -- they must be treated as
    plausible, translatable single ingredient names, NOT pre-emptively
    flagged `identity_uncertain` before translation is ever attempted.
    This is a deliberate behavior CHANGE from the old, over-broad
    capitalization heuristic -- these were false positives, not
    genuinely malformed tokens."""
    tokens = ["SECARA agenți de creștere", "Produs din GRAU", "ZARA pudră"]
    calls: list[list[str]] = []

    fake = _fake_translate(
        {
            "SECARA agenți de creștere": ("Rye Made With Raising Agents", 0.9),
            "Produs din GRAU": ("Product Made From Wheat", 0.9),
            "ZARA pudră": ("Zara Made With Powder", 0.9),
        }
    )

    async def _tracking_fake(targets, *, context=None):
        calls.append(list(targets))
        return await fake(targets, context=context)

    monkeypatch.setattr(gemini_service, "translate_ingredient_list", _tracking_fake)

    synthetics = [create_synthetic_ingredient(t) for t in tokens]
    materialized, translation_occurred = await ingredient_catalog.materialize_ingredients(
        db_session, synthetics
    )

    assert calls, "translate_ingredient_list must have been called -- these tokens are not ambiguous"
    assert translation_occurred is True
    for row in materialized:
        assert row.identity_uncertain is False
        assert row.uncertainty_reason is None
        # A verified-reliable translation is real content from a real
        # (Gemini) source -- never promoted past what GEMINI-sourced
        # rows are allowed to reach (see `ingredient_catalog.merge_verified_fields`).
        assert row.source == IngredientSource.GEMINI
        assert row.verification_status != IngredientVerificationStatus.VERIFIED


# --- Reliable translation: identity resolved, verification never promoted --


@pytest.mark.asyncio
async def test_reliable_translation_resolves_identity_and_never_promotes_verification(
    db_session, monkeypatch
):
    monkeypatch.setattr(
        gemini_service,
        "translate_ingredient_list",
        _fake_translate({"Ulei de rapiță": ("Oil Made From Rapeseed", 0.91)}),
    )

    synthetic = create_synthetic_ingredient("Ulei de rapiță")
    materialized, translation_occurred = await ingredient_catalog.materialize_ingredients(
        db_session, [synthetic]
    )
    await db_session.flush()

    assert translation_occurred is True
    row = materialized[0]
    assert row.identity_uncertain is False
    assert row.uncertainty_reason is None
    assert row.common_name == "Oil Made From Rapeseed"

    # Task requirement: "translation must never promote scientific
    # verification" -- a translated synthetic ingredient can NEVER reach
    # VERIFIED, no matter how confident the translation was.
    assert row.verification_status == IngredientVerificationStatus.UNVERIFIED
    assert row.source == IngredientSource.GEMINI
    assert row.risk_assessment_available is False
    assert row.risk_level == RiskLevel.SAFE

    out = IngredientOut.model_validate(row)
    assert out.risk_assessment_available is False
    assert out.efsa_approval_status == ApprovalStatus.NO_INFORMATION
    assert out.fda_approval_status == ApprovalStatus.NO_INFORMATION
    assert out.adi_min_mg_per_kg_bw_per_day is None
    assert out.adi_max_mg_per_kg_bw_per_day is None
    assert out.adi_population_scope is None
    assert out.identity_uncertain is False


@pytest.mark.asyncio
async def test_unreliable_translation_flags_identity_uncertain_and_keeps_original_text(
    db_session, monkeypatch
):
    """Low-confidence Gemini response -> the whole-entry invariant check
    fails -> the ORIGINAL text is preserved (never a guessed
    translation) and the ingredient is flagged for a rescan."""
    monkeypatch.setattr(
        gemini_service,
        "translate_ingredient_list",
        _fake_translate({"Ulei de rapiță": ("Oil Made From Rapeseed", 0.2)}),
    )

    synthetic = create_synthetic_ingredient("Ulei de rapiță")
    materialized, translation_occurred = await ingredient_catalog.materialize_ingredients(
        db_session, [synthetic]
    )

    assert translation_occurred is False
    row = materialized[0]
    assert row.identity_uncertain is True
    assert row.uncertainty_reason == "TRANSLATION_UNRELIABLE"
    assert row.common_name == "Ulei de rapiță"  # untouched, never a guessed translation


# --- Repeated scans reuse the catalog translation ---------------------------


@pytest.mark.asyncio
async def test_repeated_scan_of_the_same_text_reuses_the_catalog_translation(db_session, monkeypatch):
    """First scan spends exactly one Gemini call and persists a
    language-aware alias for the original text; a SECOND, independent
    scan of the EXACT same original-language text must resolve purely
    via that alias -- zero further Gemini calls."""
    call_count = {"n": 0}

    async def counting_fake(tokens: list[str], *, context=None) -> str:
        call_count["n"] += 1
        return json.dumps(
            [{"originalText": t, "detectedLanguage": "ro", "confidence": 0.9, "translatedText": "Oil Made From Rapeseed"} for t in tokens]
        )

    monkeypatch.setattr(gemini_service, "translate_ingredient_list", counting_fake)

    first_synthetic = create_synthetic_ingredient("Ulei de rapiță")
    first_materialized, first_occurred = await ingredient_catalog.materialize_ingredients(
        db_session, [first_synthetic]
    )
    await db_session.flush()
    assert first_occurred is True
    assert call_count["n"] == 1
    first_id = first_materialized[0].id

    # Second, independent "scan" -- a FRESH SyntheticIngredient built
    # from the same raw OCR text, exactly as a new request would.
    second_synthetic = create_synthetic_ingredient("Ulei de rapiță")
    second_materialized, second_occurred = await ingredient_catalog.materialize_ingredients(
        db_session, [second_synthetic]
    )

    assert call_count["n"] == 1, "a second scan of the same original text must not call Gemini again"
    assert second_occurred is False  # nothing NEW was translated this time -- resolved via alias
    assert second_materialized[0].id == first_id


@pytest.mark.asyncio
async def test_pre_existing_alias_for_the_exact_text_skips_translation_entirely(db_session, monkeypatch):
    """Step (b) of `_resolve_ingredient_languages`: if an alias already
    exists for this exact normalized original text (e.g. registered by
    an earlier, differently-shaped request), translation is skipped
    from the very start -- not merely deduped after the fact."""

    async def must_not_be_called(tokens, *, context=None):
        raise AssertionError("translate_ingredient_list must not be called when an alias already exists")

    canonical = Ingredient(
        id="synth_preexisting",
        common_name="Rapeseed Oil",
        normalized_name=normalize_ingredient_name("Rapeseed Oil"),
        scientific_name="",
        risk_level=RiskLevel.SAFE,
        risk_assessment_available=False,
        verification_status=IngredientVerificationStatus.UNVERIFIED,
        source=IngredientSource.GEMINI,
        confidence=0.5,
    )
    db_session.add(canonical)
    await db_session.flush()
    await ingredient_alias_repository.get_or_create(
        db_session,
        ingredient_id=canonical.id,
        alias_text="Ulei de rapiță",
        alias_normalized=normalize_ingredient_name("Ulei de rapiță"),
        language="ro",
        source=IngredientSource.GEMINI,
    )

    monkeypatch.setattr(gemini_service, "translate_ingredient_list", must_not_be_called)

    synthetic = create_synthetic_ingredient("Ulei de rapiță")
    materialized, translation_occurred = await ingredient_catalog.materialize_ingredients(
        db_session, [synthetic]
    )
    assert translation_occurred is False
    assert materialized[0].id == canonical.id


# --- Canonical identity + allergen preservation across languages -----------


@pytest.mark.asyncio
async def test_two_different_language_spellings_of_milk_converge_on_one_canonical_ingredient(
    db_session, monkeypatch
):
    """The task's own example: a Romanian OCR token ("Lapte") and a
    literal English-cased token ("MILK") both converge on the SAME
    canonical `Ingredient` row.

    `detect_language` treats neither bare "Lapte" nor bare "MILK" as
    confidently "en"/"bg" on its own (a single common word never
    reaches the 2-distinct-weak-word threshold -- see
    `tests/unit/test_ingredient_translation.py::
    test_single_word_correct_translation_is_marked_unreliable` for the
    documented gap this causes elsewhere) -- so BOTH tokens are here
    independently routed through translation and Gemini is stubbed to
    answer both with the exact same correct, verifiable phrase. What
    this test actually proves is the real target mechanism: two
    DIFFERENT original observations that resolve to the SAME verified
    translated text converge on exactly one canonical `Ingredient` id
    via alias resolution, never two separate rows.
    """
    translated = "Whole Milk And Water"

    async def fake_translate(tokens: list[str], *, context=None) -> str:
        return json.dumps(
            [
                {"originalText": t, "detectedLanguage": "ro", "confidence": 0.9, "translatedText": translated}
                for t in tokens
            ]
        )

    monkeypatch.setattr(gemini_service, "translate_ingredient_list", fake_translate)

    lapte = create_synthetic_ingredient("Lapte")
    materialized_lapte, _ = await ingredient_catalog.materialize_ingredients(db_session, [lapte])
    await db_session.flush()

    milk = create_synthetic_ingredient("MILK")
    materialized_milk, _ = await ingredient_catalog.materialize_ingredients(db_session, [milk])

    assert materialized_lapte[0].id == materialized_milk[0].id
    assert materialized_lapte[0].common_name == translated
    assert await ingredient_repository.count(db_session) == 1  # exactly one canonical row


@pytest.mark.asyncio
async def test_allergen_keyword_fires_on_translated_text_even_though_original_did_not_contain_it(
    db_session, monkeypatch
):
    """Real, incidental improvement from translating BEFORE allergen-
    keyword detection (`ocr_normalizer.create_synthetic_ingredient`):
    the Romanian original "Lapte" contains none of the hardcoded
    allergen keywords ("milk", "whey", "soy", "wheat", "peanut"), but
    the verified English translation does -- and the FINAL persisted
    row is rebuilt (`create_synthetic_ingredient(translated_text)`) on
    the ENGLISH text, so the keyword match now correctly fires."""
    assert not any(kw in "lapte" for kw in ("milk", "whey", "soy", "wheat", "peanut"))

    monkeypatch.setattr(
        gemini_service,
        "translate_ingredient_list",
        _fake_translate({"Lapte": ("Whole Milk And Water", 0.9)}),
    )

    synthetic = create_synthetic_ingredient("Lapte")
    materialized, _ = await ingredient_catalog.materialize_ingredients(db_session, [synthetic])

    assert materialized[0].allergens == "Potential Allergen"


# --- Old, pre-fix cached rows: safe to read, never a false claim -----------


@pytest.mark.asyncio
async def test_reading_a_pre_fix_foreign_language_cached_row_does_not_crash_or_overclaim(db_session):
    """Simulates a bad row a PRE-FIX version of this code could have
    persisted: foreign-language `common_name`, never flagged, never
    translated. A future repair pass (out of scope here) will need to
    find and fix these -- this test only proves that READING one today
    is safe: it must not crash, and it must not silently present a
    stronger claim (VERIFIED / risk-assessed / identity-confirmed) than
    the row honestly has."""
    bad_row = Ingredient(
        id="synth_legacy_lapte",
        common_name="Lapte",  # untranslated Romanian, exactly as pre-fix code left it
        normalized_name=normalize_ingredient_name("Lapte"),
        scientific_name="",
        risk_level=RiskLevel.SAFE,
        risk_assessment_available=False,
        verification_status=IngredientVerificationStatus.UNVERIFIED,
        source=IngredientSource.OCR_HEURISTIC,
        confidence=0.2,
        # `identity_uncertain`/`uncertainty_reason` default False/None --
        # this row predates the language-identity fix and was never
        # evaluated by it at all.
    )
    db_session.add(bad_row)
    await db_session.flush()

    fetched = await ingredient_repository.get_by_id_or_e_number(db_session, "synth_legacy_lapte")
    assert fetched is not None

    out = IngredientOut.model_validate(fetched)  # must not raise

    assert out.common_name == "Lapte"  # never silently mutated/guessed at read time
    assert out.verification_status == IngredientVerificationStatus.UNVERIFIED
    assert out.risk_assessment_available is False
    assert out.efsa_approval_status == ApprovalStatus.NO_INFORMATION
    assert out.fda_approval_status == ApprovalStatus.NO_INFORMATION
    assert out.adi_min_mg_per_kg_bw_per_day is None
    assert out.adi_max_mg_per_kg_bw_per_day is None
