"""
`app.services.ingredient_translation.translate_ingredient_tokens` --
batched, per-entry-validated ingredient-token translation. No DB, no
network: every Gemini call is mocked (`gemini_service.
translate_ingredient_list`, monkeypatched exactly like
`tests/unit/test_label_language.py` mocks `translate_label_text`).

Most translated text used in these fakes is phrased with 2+ recognized
English "weak" words (see `app.services.language_detection`'s own
WEAK_WORDS lists) so it passes `detect_language` outright. See the
"Known-catalog-alias fallback" section below for the narrow,
evidence-based fallback used for a SHORT result `detect_language` alone
can't confirm (e.g. "MILK" -> "Milk") -- deliberately NOT a blanket
script/length rule (a previous plain-ASCII version of this fallback
wrongly accepted untranslated short foreign text with no diacritics,
e.g. French "lait entier" -> itself unchanged; see
`test_short_untranslated_foreign_text_without_a_catalog_match_stays_unreliable`).

See "Response-to-target correspondence" for the identifier-based
matching that replaces plain positional zip -- a reordered, duplicated,
mismatched, missing, or extra response entry must never be silently
mismatched to the wrong original ingredient.
"""
import json

import pytest

from app.integrations.gemini import GeminiUnavailableError, gemini_service
from app.services.ingredient_translation import IngredientTokenTranslation, translate_ingredient_tokens


def _fake_translate(entries: list[dict]):
    """`entries`: list of raw dicts (already in the exact
    Gemini-response shape) returned verbatim, in order -- lets tests
    build deliberately malformed/partial/reordered/duplicated payloads
    directly."""

    async def _fake(targets: list[str], *, context: list[str] | None = None) -> str:
        return json.dumps(entries)

    return _fake


def _entry(original: str, translated: str, *, confidence: float = 0.9, detected: str = "ro") -> dict:
    return {
        "originalText": original,
        "detectedLanguage": detected,
        "confidence": confidence,
        "translatedText": translated,
    }


# --- Happy path --------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_token_list_returns_empty_list_without_calling_gemini(monkeypatch):
    async def must_not_be_called(targets, **kwargs):
        raise AssertionError("translate_ingredient_list must not be called for an empty token list")

    monkeypatch.setattr(gemini_service, "translate_ingredient_list", must_not_be_called)
    assert await translate_ingredient_tokens([]) == []


@pytest.mark.asyncio
async def test_successful_batch_translation_all_entries_reliable(monkeypatch):
    monkeypatch.setattr(
        gemini_service,
        "translate_ingredient_list",
        _fake_translate(
            [
                _entry("Ulei de rapiță", "Oil Made From Rapeseed", confidence=0.91),
                _entry("Semințe de mac", "Made With Poppy Seeds", confidence=0.88),
            ]
        ),
    )

    results = await translate_ingredient_tokens(["Ulei de rapiță", "Semințe de mac"])

    assert len(results) == 2
    assert results[0] == IngredientTokenTranslation(
        original_text="Ulei de rapiță",
        translated_text="Oil Made From Rapeseed",
        detected_language="ro",
        confidence=0.91,
        reliable=True,
    )
    assert results[1].reliable is True
    assert results[1].translated_text == "Made With Poppy Seeds"


@pytest.mark.asyncio
async def test_e_numbers_and_numeric_tokens_preserved_through_translation(monkeypatch):
    monkeypatch.setattr(
        gemini_service,
        "translate_ingredient_list",
        _fake_translate([_entry("Zahar 12%", "Sugar Made From 12% Beet", confidence=0.8)]),
    )
    results = await translate_ingredient_tokens(["Zahar 12%"])
    assert results[0].reliable is True
    assert "12%" in results[0].translated_text


@pytest.mark.asyncio
async def test_context_defaults_to_targets_and_is_passed_through(monkeypatch):
    seen = {}

    async def _fake(targets, *, context=None):
        seen["targets"] = targets
        seen["context"] = context
        return json.dumps([_entry("Ulei de rapiță", "Oil Made From Rapeseed")])

    monkeypatch.setattr(gemini_service, "translate_ingredient_list", _fake)
    await translate_ingredient_tokens(["Ulei de rapiță"])
    assert seen["targets"] == ["Ulei de rapiță"]
    assert seen["context"] is None  # caller passed nothing -> service layer defaults it


@pytest.mark.asyncio
async def test_explicit_full_list_context_is_forwarded_separately_from_targets(monkeypatch):
    """Task requirement: "supply the full ingredient-list context
    separately while clearly identifying the target entries" -- the
    caller (`ingredient_catalog`) passes the WHOLE scan's ingredient
    list as `context`, distinct from the (usually smaller) `targets`
    subset that actually needs a translation returned."""
    seen = {}

    async def _fake(targets, *, context=None):
        seen["targets"] = targets
        seen["context"] = context
        return json.dumps([_entry("SECARA", "Rye")])

    monkeypatch.setattr(gemini_service, "translate_ingredient_list", _fake)
    await translate_ingredient_tokens(
        ["SECARA"], context=["Water", "Sugar", "SECARA", "Salt"]
    )
    assert seen["targets"] == ["SECARA"]
    assert seen["context"] == ["Water", "Sugar", "SECARA", "Salt"]


# --- Whole-batch failure: every entry degrades, never raises ---------------


@pytest.mark.asyncio
async def test_gemini_unavailable_degrades_every_entry_to_unreliable(monkeypatch):
    async def raise_unavailable(targets, **kwargs):
        raise GeminiUnavailableError("not configured")

    monkeypatch.setattr(gemini_service, "translate_ingredient_list", raise_unavailable)

    results = await translate_ingredient_tokens(["Ulei de rapiță", "Zahar"])
    assert len(results) == 2
    for r in results:
        assert r.reliable is False
        assert r.translated_text is None
        assert r.confidence is None


@pytest.mark.asyncio
async def test_unparsable_json_degrades_every_entry_to_unreliable(monkeypatch):
    async def bad_json(targets, **kwargs):
        return "this is not json"

    monkeypatch.setattr(gemini_service, "translate_ingredient_list", bad_json)

    results = await translate_ingredient_tokens(["Ulei de rapiță"])
    assert len(results) == 1
    assert results[0].reliable is False


@pytest.mark.asyncio
async def test_non_list_json_payload_degrades_every_entry_to_unreliable(monkeypatch):
    async def object_not_list(targets, **kwargs):
        return json.dumps({"not": "a list"})

    monkeypatch.setattr(gemini_service, "translate_ingredient_list", object_not_list)

    results = await translate_ingredient_tokens(["Ulei de rapiță"])
    assert results[0].reliable is False


# --- Per-entry validation: one bad entry never invalidates the rest --------


@pytest.mark.asyncio
async def test_one_malformed_entry_does_not_invalidate_the_rest_of_the_batch(monkeypatch):
    """`_TranslationEntry` is `extra="forbid"` -- an entry carrying an
    unexpected extra key is rejected, but siblings in the SAME batch
    response must still be processed independently."""
    monkeypatch.setattr(
        gemini_service,
        "translate_ingredient_list",
        _fake_translate(
            [
                {**_entry("Ulei de rapiță", "Oil Made From Rapeseed"), "unexpectedField": "x"},
                _entry("Semințe de mac", "Made With Poppy Seeds", confidence=0.85),
            ]
        ),
    )
    results = await translate_ingredient_tokens(["Ulei de rapiță", "Semințe de mac"])
    assert results[0].reliable is False
    assert results[1].reliable is True
    assert results[1].translated_text == "Made With Poppy Seeds"


@pytest.mark.asyncio
async def test_missing_required_field_marks_only_that_entry_unreliable(monkeypatch):
    broken_entry = {"originalText": "Ulei de rapiță", "detectedLanguage": "ro", "confidence": 0.9}
    monkeypatch.setattr(
        gemini_service,
        "translate_ingredient_list",
        _fake_translate([broken_entry, _entry("Semințe de mac", "Made With Poppy Seeds")]),
    )
    results = await translate_ingredient_tokens(["Ulei de rapiță", "Semințe de mac"])
    assert results[0].reliable is False
    assert results[1].reliable is True


# --- Per-entry invariant checks ---------------------------------------------


@pytest.mark.asyncio
async def test_low_confidence_entry_is_marked_unreliable_but_confidence_is_still_reported(monkeypatch):
    monkeypatch.setattr(
        gemini_service,
        "translate_ingredient_list",
        _fake_translate([_entry("Ulei de rapiță", "Oil Made From Rapeseed", confidence=0.4)]),
    )
    results = await translate_ingredient_tokens(["Ulei de rapiță"])
    assert results[0].reliable is False
    assert results[0].translated_text is None
    assert results[0].confidence == 0.4  # reported for diagnostics even though unreliable


@pytest.mark.asyncio
async def test_placeholder_translated_text_is_marked_unreliable(monkeypatch):
    monkeypatch.setattr(
        gemini_service,
        "translate_ingredient_list",
        _fake_translate([_entry("Ulei de rapiță", "N/A", confidence=0.95)]),
    )
    results = await translate_ingredient_tokens(["Ulei de rapiță"])
    assert results[0].reliable is False


@pytest.mark.asyncio
async def test_translated_text_not_independently_english_is_marked_unreliable(monkeypatch):
    """Gemini claims a confident, "en"-labeled translation, but the
    translated text itself is still French -- the independent
    `detect_language` re-check (never merely trusting Gemini's own
    self-reported `detectedLanguage`) must catch this."""
    monkeypatch.setattr(
        gemini_service,
        "translate_ingredient_list",
        _fake_translate([_entry("Lait entier", "Lait Entier Complet", confidence=0.95, detected="en")]),
    )
    results = await translate_ingredient_tokens(["Lait entier"])
    assert results[0].reliable is False


@pytest.mark.asyncio
async def test_e_number_mismatch_is_marked_unreliable(monkeypatch):
    monkeypatch.setattr(
        gemini_service,
        "translate_ingredient_list",
        _fake_translate(
            [_entry("Benzoat de sodiu (E211)", "Sodium Benzoate, Made From Salt", confidence=0.9)]
        ),
    )
    results = await translate_ingredient_tokens(["Benzoat de sodiu (E211)"])
    assert results[0].reliable is False  # E211 silently dropped by the "translation"


@pytest.mark.asyncio
async def test_numeric_token_mismatch_is_marked_unreliable(monkeypatch):
    monkeypatch.setattr(
        gemini_service,
        "translate_ingredient_list",
        _fake_translate([_entry("Zahar 12%", "Sugar Made From 15% Beet", confidence=0.9)]),
    )
    results = await translate_ingredient_tokens(["Zahar 12%"])
    assert results[0].reliable is False  # 12% silently changed to 15%


# --- Known-catalog-alias fallback (task requirement 1) ----------------------
#
# Issue 1 fix: a previous "any short ASCII text" fallback accepted
# untranslated foreign text like French "lait entier" outright -- a real
# false-accept. It has been replaced with a narrow, evidence-based
# fallback: a short translated result that `detect_language` alone can't
# confirm is only accepted when it matches a name ALREADY established in
# the ingredient catalog (`known_normalized_names`, supplied by the
# caller from `ingredient_alias_repository.get_all_normalized`). No
# blanket script/length rule replaces the old one.


@pytest.mark.asyncio
async def test_short_translation_matching_a_known_catalog_alias_is_reliable(monkeypatch):
    """"MILK" -> "Milk" can never reach `detect_language`'s 2-distinct-
    weak-word bar on its own, but when "milk" is already a known,
    established catalog alias (real independent evidence, not a guess),
    the translation is accepted."""
    monkeypatch.setattr(
        gemini_service,
        "translate_ingredient_list",
        _fake_translate([_entry("MILK", "Milk", confidence=0.97, detected="en")]),
    )
    results = await translate_ingredient_tokens(
        ["MILK"], known_normalized_names=frozenset({"milk"})
    )
    assert results[0].reliable is True
    assert results[0].translated_text == "Milk"


@pytest.mark.asyncio
async def test_short_untranslated_foreign_text_without_a_catalog_match_stays_unreliable(monkeypatch):
    """The exact false-accept the old ASCII fallback allowed: Gemini
    returns "lait entier" completely unchanged (still French, 2 words,
    plain ASCII, no diacritics) and claims high confidence/"en" -- with
    no known catalog alias for it, this must be rejected, not guessed.
    Preserves an honest UNRESOLVED result (task requirement) rather than
    a wrong accept."""
    monkeypatch.setattr(
        gemini_service,
        "translate_ingredient_list",
        _fake_translate([_entry("lait entier", "lait entier", confidence=0.9, detected="en")]),
    )
    results = await translate_ingredient_tokens(["lait entier"], known_normalized_names=frozenset())
    assert results[0].reliable is False
    assert results[0].translated_text is None


@pytest.mark.asyncio
async def test_short_correct_translation_with_no_catalog_match_is_honestly_unresolved(monkeypatch):
    """A brand-new, never-before-seen, genuinely correct short
    translation ("Rapeseed Oil" for a 2-word target) that matches
    neither `detect_language`'s vocabulary nor any known catalog alias
    is honestly reported UNRESOLVED, not silently accepted or rejected
    as definitely wrong -- the caller turns this into
    `identity_uncertain=True`, never a guessed identity."""
    monkeypatch.setattr(
        gemini_service,
        "translate_ingredient_list",
        _fake_translate([_entry("Ulei bio", "Bio Oil", confidence=0.9, detected="ro")]),
    )
    results = await translate_ingredient_tokens(["Ulei bio"], known_normalized_names=frozenset())
    assert results[0].reliable is False
    assert results[0].translated_text is None
    assert results[0].confidence == 0.9  # still reported, even though unresolved


@pytest.mark.asyncio
async def test_known_alias_fallback_still_enforces_confidence_and_invariants(monkeypatch):
    """The catalog-alias fallback only widens the LANGUAGE check -- it
    never bypasses confidence or E-number/numeric-token preservation."""
    monkeypatch.setattr(
        gemini_service,
        "translate_ingredient_list",
        _fake_translate([_entry("SARE", "Salt", confidence=0.3, detected="en")]),
    )
    results = await translate_ingredient_tokens(["SARE"], known_normalized_names=frozenset({"salt"}))
    assert results[0].reliable is False  # confidence 0.3 < 0.55, alias match doesn't override it


# --- Response-to-target correspondence (task requirement 2) -----------------
#
# Issue 2 fix: entries are matched back to their target by `originalText`
# identity, never by response position/order alone.


@pytest.mark.asyncio
async def test_reordered_response_is_matched_by_identifier_not_position(monkeypatch):
    monkeypatch.setattr(
        gemini_service,
        "translate_ingredient_list",
        _fake_translate(
            [
                _entry("Semințe de mac", "Made With Poppy Seeds", confidence=0.9),
                _entry("Ulei de rapiță", "Oil Made From Rapeseed", confidence=0.9),
            ]
        ),
    )
    results = await translate_ingredient_tokens(["Ulei de rapiță", "Semințe de mac"])
    assert results[0].original_text == "Ulei de rapiță"
    assert results[0].translated_text == "Oil Made From Rapeseed"
    assert results[1].original_text == "Semințe de mac"
    assert results[1].translated_text == "Made With Poppy Seeds"


@pytest.mark.asyncio
async def test_response_entry_for_text_not_in_targets_is_discarded_not_misattached(monkeypatch):
    """A response entry whose `originalText` doesn't match any target at
    all (a mismatched/hallucinated entry, or one only relevant to
    `context`) must never be force-attached to a different target's
    position."""
    monkeypatch.setattr(
        gemini_service,
        "translate_ingredient_list",
        _fake_translate(
            [
                _entry("Salt", "Salt", confidence=0.9, detected="en"),  # not a target at all
                _entry("Ulei de rapiță", "Oil Made From Rapeseed", confidence=0.9),
            ]
        ),
    )
    results = await translate_ingredient_tokens(["Ulei de rapiță"])
    assert len(results) == 1
    assert results[0].original_text == "Ulei de rapiță"
    assert results[0].translated_text == "Oil Made From Rapeseed"


@pytest.mark.asyncio
async def test_duplicate_target_text_each_occurrence_gets_its_own_response_entry(monkeypatch):
    """The same original name appearing twice in `targets` gets two
    independent response entries, consumed in response order -- never
    the same response entry reused for both positions."""
    monkeypatch.setattr(
        gemini_service,
        "translate_ingredient_list",
        _fake_translate(
            [
                _entry("SARE", "Salt", confidence=0.9, detected="en"),
                _entry("SARE", "Salt", confidence=0.85, detected="en"),
            ]
        ),
    )
    results = await translate_ingredient_tokens(
        ["SARE", "SARE"], known_normalized_names=frozenset({"salt"})
    )
    assert len(results) == 2
    assert [r.confidence for r in results] == [0.9, 0.85]
    assert all(r.reliable for r in results)


@pytest.mark.asyncio
async def test_duplicate_target_with_only_one_response_entry_leaves_the_other_unresolved(monkeypatch):
    """Two occurrences of the same target text, but the response only
    supplies ONE matching entry -- the first occurrence claims it, the
    second is honestly unresolved (missing), never a duplicated guess."""
    monkeypatch.setattr(
        gemini_service,
        "translate_ingredient_list",
        _fake_translate([_entry("SARE", "Salt", confidence=0.9, detected="en")]),
    )
    results = await translate_ingredient_tokens(
        ["SARE", "SARE"], known_normalized_names=frozenset({"salt"})
    )
    assert len(results) == 2
    assert results[0].reliable is True
    assert results[0].translated_text == "Salt"
    assert results[1].reliable is False
    assert results[1].translated_text is None


@pytest.mark.asyncio
async def test_missing_response_entry_for_a_target_is_unreliable_not_guessed(monkeypatch):
    monkeypatch.setattr(
        gemini_service,
        "translate_ingredient_list",
        _fake_translate([_entry("Ulei de rapiță", "Oil Made From Rapeseed", confidence=0.9)]),
    )
    results = await translate_ingredient_tokens(["Ulei de rapiță", "Semințe de mac"])
    assert results[0].reliable is True
    assert results[1].original_text == "Semințe de mac"
    assert results[1].reliable is False
    assert results[1].translated_text is None


@pytest.mark.asyncio
async def test_extra_response_entries_beyond_targets_are_ignored_safely(monkeypatch):
    monkeypatch.setattr(
        gemini_service,
        "translate_ingredient_list",
        _fake_translate(
            [
                _entry("Ulei de rapiță", "Oil Made From Rapeseed", confidence=0.9),
                _entry("Ulei de rapiță", "Something Made With Water Entirely", confidence=0.9),
                _entry("Aluat acrisor", "Sourdough", confidence=0.9),
            ]
        ),
    )
    results = await translate_ingredient_tokens(["Ulei de rapiță"])
    assert len(results) == 1
    assert results[0].translated_text == "Oil Made From Rapeseed"  # first match claims it; extras discarded


@pytest.mark.asyncio
async def test_response_originaltext_that_only_approximately_matches_a_target_is_not_attached(monkeypatch):
    """`originalText` must match a target EXACTLY (character-for-
    character) -- a near-miss (trailing whitespace/case/punctuation
    difference) is treated as a non-match, never fuzzy-attached to the
    wrong entry."""
    monkeypatch.setattr(
        gemini_service,
        "translate_ingredient_list",
        _fake_translate([_entry("Ulei de Rapita", "Rapeseed Oil", confidence=0.9)]),  # missing diacritics
    )
    results = await translate_ingredient_tokens(["Ulei de rapiță"])
    assert len(results) == 1
    assert results[0].reliable is False
    assert results[0].translated_text is None
