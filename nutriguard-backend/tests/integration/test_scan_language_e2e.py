"""
Full HTTP end-to-end coverage (via `POST /api/v1/scan/ocr-text`) for the
reported ingredient-language bug and its fix: a mixed-language OCR
block containing an English trigger word ("Ingredients:") glued to
Romanian ingredient names used to have the WHOLE block classified "en"
at the label-text-blob level (`app.services.label_language.
resolve_label_text`, unchanged by this fix -- see
`app.services.language_detection.detect_language`'s "one STRONG word is
enough" rule) and therefore skip translation entirely, silently letting
foreign-language ingredient names reach the API mislabeled as English.
The fix is at the PER-INGREDIENT-TOKEN level
(`app.services.ingredient_catalog._resolve_ingredient_languages`), which
runs independently of the blob-level classification.

Every Gemini call is mocked (`gemini_service.translate_ingredient_list`
via `analyze_text`'s own fallback path -- `GEMINI_API_KEY=""` in
`tests/conftest.py` already forces the deterministic local fallback for
whole-text analysis; only the per-token translation call needs
stubbing). No test in this file makes a live network call.
"""
import json

import pytest

from app.integrations.gemini import gemini_service


async def _register_device(client, device_id: str) -> dict:
    resp = await client.post("/api/v1/auth/device", json={"deviceId": device_id})
    token = resp.json()["accessToken"]
    return {"Authorization": f"Bearer {token}"}


# Faithful, independently-verifiable (2+ recognized English words, so
# `detect_language` confidently re-classifies them as "en" -- see
# `tests/unit/test_ingredient_translation.py` for why a bare one-word
# translation would NOT reliably pass this same check) translations for
# the Romanian tokens used throughout this file.
_TRANSLATIONS = {
    "Ulei de rapiță": "Oil Made From Rapeseed",
    "Semințe de mac": "Made With Poppy Seeds",
    "Aluat acrisor": "Sourdough Made With Water And Flour",
}


def _counting_fake_translate(call_count: dict):
    async def _fake(tokens: list[str], *, context=None) -> str:
        call_count["n"] = call_count.get("n", 0) + 1
        payload = []
        for t in tokens:
            translated = _TRANSLATIONS.get(t)
            assert translated is not None, f"unexpected token sent for translation: {t!r}"
            payload.append(
                {"originalText": t, "detectedLanguage": "ro", "confidence": 0.9, "translatedText": translated}
            )
        return json.dumps(payload)

    return _fake


@pytest.mark.asyncio
async def test_mixed_english_trigger_word_and_romanian_ingredients_still_translates_the_romanian_part(
    app_client, monkeypatch
):
    """The literal reported bug: an English "Ingredients:" trigger word
    glued to Romanian ingredient names in the same OCR block. Proves the
    Romanian ingredient ends up translated to English in the final
    `IngredientOut`, not left in Romanian."""
    headers = await _register_device(app_client, "mixed-lang-device")
    call_count: dict = {}
    monkeypatch.setattr(gemini_service, "translate_ingredient_list", _counting_fake_translate(call_count))

    raw_text = "Ingredients: Ulei de rapiță, Semințe de mac, Aluat acrisor"
    resp = await app_client.post("/api/v1/scan/ocr-text", json={"rawText": raw_text}, headers=headers)

    assert resp.status_code == 200
    body = resp.json()
    ingredient_names = {ing["commonName"] for ing in body["ingredients"]}

    # The Romanian originals must NOT survive verbatim into the response...
    assert "Ulei de rapiță" not in ingredient_names
    assert "Semințe de mac" not in ingredient_names
    # ...they were translated to their verified English equivalents.
    assert "Oil Made From Rapeseed" in ingredient_names
    assert "Made With Poppy Seeds" in ingredient_names

    translated_ingredient = next(
        ing for ing in body["ingredients"] if ing["commonName"] == "Oil Made From Rapeseed"
    )
    assert translated_ingredient["identityUncertain"] is False
    assert translated_ingredient["riskAssessmentAvailable"] is False
    assert translated_ingredient["verificationStatus"] == "UNVERIFIED"

    assert call_count.get("n", 0) >= 1


@pytest.mark.asyncio
async def test_repeated_scan_of_the_same_mixed_text_does_not_translate_again(app_client, monkeypatch):
    """Two full, independent `/scan/ocr-text` requests carrying the
    exact same raw OCR text -- the SECOND one must resolve entirely via
    the alias the first one registered; zero additional Gemini calls."""
    headers = await _register_device(app_client, "repeat-scan-device")
    call_count: dict = {}
    monkeypatch.setattr(gemini_service, "translate_ingredient_list", _counting_fake_translate(call_count))

    raw_text = "Ingredients: Ulei de rapiță, Aluat acrisor"

    first = await app_client.post("/api/v1/scan/ocr-text", json={"rawText": raw_text}, headers=headers)
    assert first.status_code == 200
    calls_after_first = call_count.get("n", 0)
    assert calls_after_first >= 1

    second = await app_client.post("/api/v1/scan/ocr-text", json={"rawText": raw_text}, headers=headers)
    assert second.status_code == 200

    assert call_count.get("n", 0) == calls_after_first, (
        "a second scan of identical original-language text must not call "
        "translate_ingredient_list again -- it must resolve via the alias "
        "registered by the first scan"
    )

    first_names = {ing["commonName"] for ing in first.json()["ingredients"]}
    second_names = {ing["commonName"] for ing in second.json()["ingredients"]}
    assert "Oil Made From Rapeseed" in first_names
    assert "Oil Made From Rapeseed" in second_names


@pytest.mark.asyncio
async def test_genuinely_ambiguous_duplicate_word_resolves_identity_uncertain_end_to_end(
    app_client, monkeypatch
):
    """Code-review fix (issue 3): of the task's original 4 examples,
    only "LAPTE proteină din LAPTE" ("MILK protein from MILK") is still
    genuinely ambiguous (it repeats the same significant word twice --
    `DUPLICATE_TOKEN_FRAGMENT`, real structural evidence of two merged
    clauses). Driven through the full HTTP scan endpoint: it must
    resolve to `identityUncertain: true` and never be silently
    "translated" into a plausible-sounding but fabricated identity."""

    async def must_not_be_called(tokens, *, context=None):
        raise AssertionError(f"translate_ingredient_list must not be called for {tokens!r}")

    monkeypatch.setattr(gemini_service, "translate_ingredient_list", must_not_be_called)
    headers = await _register_device(app_client, "ambiguous-device")

    raw_text = "LAPTE proteină din LAPTE"
    resp = await app_client.post("/api/v1/scan/ocr-text", json={"rawText": raw_text}, headers=headers)

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["ingredients"]) >= 1
    for ing in body["ingredients"]:
        assert ing["identityUncertain"] is True, ing
        assert ing["uncertaintyReason"] == "DUPLICATE_TOKEN_FRAGMENT"


@pytest.mark.asyncio
async def test_previously_over_flagged_examples_translate_end_to_end_instead_of_blocking(
    app_client, monkeypatch
):
    """Code-review fix (issue 3): "SECARA agenți de creștere", "Produs
    din GRAU", and "ZARA pudră" are ordinary allergen-emphasis compound
    names, not evidence of concatenation -- driven through the full HTTP
    endpoint, they must reach translation (never pre-emptively blocked)
    and resolve to a real, non-uncertain identity when translation
    succeeds. This is a deliberate behavior CHANGE from the old,
    over-broad capitalization heuristic."""

    async def fake_translate(tokens, *, context=None):
        translations = {
            "SECARA agenți de creștere": "Rye Made With Raising Agents",
            "Produs din GRAU": "Product Made From Wheat",
            "ZARA pudră": "Zara Made With Powder",
        }
        payload = [
            {
                "originalText": t,
                "detectedLanguage": "ro",
                "confidence": 0.9,
                "translatedText": translations[t],
            }
            for t in tokens
        ]
        return json.dumps(payload)

    monkeypatch.setattr(gemini_service, "translate_ingredient_list", fake_translate)
    headers = await _register_device(app_client, "previously-flagged-device")

    raw_text = "SECARA agenți de creștere, Produs din GRAU, ZARA pudră"
    resp = await app_client.post("/api/v1/scan/ocr-text", json={"rawText": raw_text}, headers=headers)

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["ingredients"]) >= 3
    for ing in body["ingredients"]:
        assert ing["identityUncertain"] is False, ing
        assert ing["uncertaintyReason"] is None


# --- No fabricated "exceeds ADI" claim anywhere on the wire -----------------


def test_no_schema_field_anywhere_claims_a_product_exceeds_an_ingredient_adi():
    """Negative/absence test (task requirement): grep the actual schema
    source for any field name resembling an "exceeds ADI"/limit
    comparison. This must find nothing at all -- no such field exists,
    or should ever be added without a product-specific quantity to
    compare against (which this backend never has -- OCR/label text
    gives no reliable per-serving quantity)."""
    import pathlib

    schema_dir = pathlib.Path(__file__).resolve().parents[2] / "app" / "schemas"
    suspicious_terms = ("exceed", "overlimit", "over_limit", "adiexceeded", "limitexceeded")
    hits = []
    for path in schema_dir.glob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        for term in suspicious_terms:
            if term in text:
                hits.append((path.name, term))
    assert hits == [], f"found a suspicious 'exceeds limit' field/term in schemas: {hits}"


@pytest.mark.asyncio
async def test_adi_fields_describe_only_the_ingredient_never_a_product_comparison(app_client):
    """`adiMinMgPerKgBwPerDay`/`adiMaxMgPerKgBwPerDay`/`adiPopulationScope`
    on a real response describe the INGREDIENT's own regulatory limit
    only -- never anything product-quantity-specific. Confirms (for a
    real, ordinary scan response) that no ingredient payload carries any
    key suggesting a product-vs-limit comparison was made."""
    headers = await _register_device(app_client, "adi-scope-device")
    resp = await app_client.post(
        "/api/v1/scan/ocr-text",
        json={"rawText": "Water, Sugar, Sodium Benzoate (E211)"},
        headers=headers,
    )
    assert resp.status_code == 200
    for ing in resp.json()["ingredients"]:
        keys_lower = {k.lower() for k in ing.keys()}
        assert not any("exceed" in k for k in keys_lower)
        assert not any("overlimit" in k for k in keys_lower)
