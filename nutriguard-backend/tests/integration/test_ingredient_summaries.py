"""
Issue #23 stage 3: source-backed ingredient summaries, end to end.

Real HTTP JSON (httpx against the ASGI app, in-memory SQLite) for the read
paths, plus the idempotent loader and the explicit review action. Every
scan is deterministic: OCR text, or a mocked Gemini image response. No
network, no model call, no per-scan content generation.
"""
import copy
import io
import json

import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.integrations.gemini import gemini_service
from app.models.ingredient import Ingredient
from app.models.ingredient_summary import IngredientSummary, IngredientSummaryLocalization
from app.seed import load_seed as load_seed_module
from app.seed import load_summaries as loader
from app.seed import summary_review

E150D_KINDS = ["ORIGIN", "FUNCTION", "EFFECTS", "JURISDICTION", "JURISDICTION", "JURISDICTION"]


@pytest_asyncio.fixture
async def seeded(db_engine, monkeypatch):
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(load_seed_module, "AsyncSessionLocal", factory)
    monkeypatch.setattr(loader, "AsyncSessionLocal", factory)
    monkeypatch.setattr(summary_review, "AsyncSessionLocal", factory)
    await load_seed_module.load_seed()
    return factory


async def _known(factory, *names):
    """Names the catalog has already resolved (a prior, confirmed observation),
    as on a live catalog: their rows are identity-certain and their alias lets a
    bare word such as "Sugar" resolve without a fresh translation."""
    from app.services import ingredient_catalog
    from app.services.ocr_normalizer import create_synthetic_ingredient

    async with factory() as db:
        for name in names:
            await ingredient_catalog.get_or_create_catalog_ingredient(db, create_synthetic_ingredient(name))
        await db.commit()


async def _headers(client, device="summaries-device") -> dict:
    resp = await client.post("/api/v1/auth/device", json={"deviceId": device})
    return {"Authorization": f"Bearer {resp.json()['accessToken']}"}


async def _scan_text(client, headers, text, **extra) -> dict:
    resp = await client.post("/api/v1/scan/ocr-text", json={"rawText": text, **extra}, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _by_name(body: dict) -> dict:
    return {ing["commonName"].lower(): ing for ing in body["ingredients"]}


def _e_number(body: dict, e_number: str) -> dict:
    return next(ing for ing in body["ingredients"] if ing["eNumber"] == e_number)


def _without_summary(ingredient: dict) -> dict:
    return {k: v for k, v in ingredient.items() if k != "summary"}


# ----------------------------------------------------------------- loader


@pytest.mark.asyncio
async def test_the_loader_creates_the_pilot_and_every_translation_starts_as_a_draft(seeded):
    async with seeded() as db:
        rows = (await db.execute(select(IngredientSummary).order_by(IngredientSummary.id))).scalars().all()
        assert {r.subject_key for r in rows} == {"E150D", "E322", "name:sugar", "name:salt", "name:palm oil"}
        assert all(r.evidence_state == "SOURCE_VERIFIED" and r.human_reviewed is False for r in rows)
        assert all(r.source_verified_at is not None for r in rows)
        locs = (await db.execute(select(IngredientSummaryLocalization))).scalars().all()
        assert len(locs) == 5
        assert {(loc.translation_status, loc.translation_source) for loc in locs} == {("DRAFT", "MACHINE_TRANSLATED")}
        assert all(loc.reviewed_at is None and loc.reviewed_by is None for loc in locs)


@pytest.mark.asyncio
async def test_repeated_loads_change_nothing_and_never_duplicate(seeded):
    async with seeded() as db:
        before = {
            r.subject_key: (r.id, r.content_hash, r.updated_at)
            for r in (await db.execute(select(IngredientSummary))).scalars().all()
        }
    for _ in range(3):
        async with seeded() as db:
            counters = await loader.load_summaries(db)
            await db.commit()
        assert counters["created"] == 0 and counters["updated"] == 0 and counters["unchanged"] == 5
        assert counters["translations_created"] == 0 and counters["translations_updated"] == 0
    async with seeded() as db:
        assert (await db.execute(select(func.count()).select_from(IngredientSummary))).scalar_one() == 5
        assert (await db.execute(select(func.count()).select_from(IngredientSummaryLocalization))).scalar_one() == 5
        after = {
            r.subject_key: (r.id, r.content_hash, r.updated_at)
            for r in (await db.execute(select(IngredientSummary))).scalars().all()
        }
    assert after == before


@pytest.mark.asyncio
async def test_a_malformed_content_file_is_refused_before_anything_is_written(seeded, tmp_path):
    good = json.loads(loader.SUMMARIES_SEED_FILE.read_text(encoding="utf-8"))
    bad = copy.deepcopy(good)
    bad["subjects"] = [copy.deepcopy(bad["subjects"][0]), copy.deepcopy(bad["subjects"][2])]
    bad["subjects"][0]["subjectKey"] = "E777"
    bad["subjects"][1]["sections"][0]["citationIds"] = ["not-a-listed-citation"]
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    async with seeded() as db:
        with pytest.raises(ValueError, match="unknown id"):
            await loader.load_summaries(db, path)
        await db.rollback()
        assert (
            await db.execute(select(IngredientSummary).where(IngredientSummary.subject_key == "E777"))
        ).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_changed_english_content_resets_review_and_makes_translations_stale(seeded, tmp_path):
    async with seeded() as db:
        row = (await db.execute(select(IngredientSummary).where(IngredientSummary.subject_key == "E322"))).scalar_one()
        row.human_reviewed = True
        loc = await db.get(IngredientSummaryLocalization, (row.id, "bg"))
        loc.translation_status = "REVIEWED"
        loc.reviewed_by = "reviewer-a"
        old_hash = row.content_hash
        await db.commit()

    data = json.loads(loader.SUMMARIES_SEED_FILE.read_text(encoding="utf-8"))
    e322 = next(s for s in data["subjects"] if s["subjectKey"] == "E322")
    e322["sections"][1]["text"] = "They are used as emulsifiers."
    e322["localizations"]["bg"]["sections"][1]["text"] = "Използват се като емулгатори."
    path = tmp_path / "changed.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    async with seeded() as db:
        counters = await loader.load_summaries(db, path)
        await db.commit()
    assert counters["updated"] == 1 and counters["translations_updated"] == 1

    async with seeded() as db:
        row = (await db.execute(select(IngredientSummary).where(IngredientSummary.subject_key == "E322"))).scalar_one()
        assert row.content_hash != old_hash
        assert row.human_reviewed is False  # a person reviewed different text
        loc = await db.get(IngredientSummaryLocalization, (row.id, "bg"))
        assert loc.translation_status == "DRAFT" and loc.reviewed_by is None
        assert loc.source_content_hash == row.content_hash


@pytest.mark.asyncio
async def test_a_reviewed_current_translation_is_never_overwritten_or_downgraded_by_a_reload(seeded, tmp_path):
    async with seeded() as db:
        row = (await db.execute(select(IngredientSummary).where(IngredientSummary.subject_key == "name:salt"))).scalar_one()
        loc = await db.get(IngredientSummaryLocalization, (row.id, "bg"))
        loc.translation_status = "REVIEWED"
        loc.reviewed_by = "reviewer-b"
        loc.sections_json = json.dumps([{"kind": "EFFECTS", "text": "Коригиран от рецензента."}], ensure_ascii=False)
        await db.commit()
    async with seeded() as db:
        counters = await loader.load_summaries(db)
        await db.commit()
    assert counters["translations_kept"] >= 1 and counters["translations_updated"] == 0
    async with seeded() as db:
        row = (await db.execute(select(IngredientSummary).where(IngredientSummary.subject_key == "name:salt"))).scalar_one()
        loc = await db.get(IngredientSummaryLocalization, (row.id, "bg"))
        assert loc.translation_status == "REVIEWED" and loc.reviewed_by == "reviewer-b"
        assert "Коригиран" in loc.sections_json


# ----------------------------------------------------------------- review


@pytest.mark.asyncio
async def test_the_review_pack_shows_english_beside_the_draft_and_the_hash_to_quote(seeded):
    pack = summary_review.render_review_pack()
    assert "## E150D (E_NUMBER_GENERIC)" in pack and "English content hash: `" in pack
    assert "EN:" in pack and "BG (draft):" in pack and "--reviewer" in pack
    assert "Approve with:" in pack


@pytest.mark.asyncio
async def test_approval_needs_the_exact_hash_a_reviewer_label_and_never_changes_provenance(seeded):
    async with seeded() as db:
        row = (await db.execute(select(IngredientSummary).where(IngredientSummary.subject_key == "E322"))).scalar_one()
        digest = row.content_hash
    assert (await summary_review.approve("E322", "bg", "0" * 64, "reviewer")).startswith("refused")
    assert (await summary_review.approve("E322", "bg", digest, "  ")).startswith("refused")
    assert (await summary_review.approve("E000", "bg", digest, "reviewer")).startswith("refused")
    assert (await summary_review.approve("E322", "bg", digest, "reviewer")).startswith("approved")
    assert (await summary_review.approve("E322", "bg", digest, "reviewer")).startswith("unchanged")
    async with seeded() as db:
        row = (await db.execute(select(IngredientSummary).where(IngredientSummary.subject_key == "E322"))).scalar_one()
        loc = await db.get(IngredientSummaryLocalization, (row.id, "bg"))
        assert loc.translation_status == "REVIEWED" and loc.reviewed_by == "reviewer"
        assert loc.translation_source == "MACHINE_TRANSLATED"  # review is not a change of origin
        assert row.human_reviewed is False  # scientific sign-off is a different, separate decision


# ------------------------------------------------------------- read paths


@pytest.mark.asyncio
async def test_a_generic_e_number_summary_is_served_beside_the_specific_row_and_never_merged_into_it(
    app_client, seeded, monkeypatch
):
    headers = await _headers(app_client)
    with_summary = (await app_client.get("/api/v1/ingredients/E322", headers=headers)).json()
    monkeypatch.setattr(settings, "INGREDIENT_SUMMARIES_SERVE", False)
    without = (await app_client.get("/api/v1/ingredients/E322", headers=headers)).json()

    assert with_summary["id"] == "e322_soy_lecithin" and "oy" in with_summary["commonName"]
    assert with_summary["summary"]["scope"] == "E_NUMBER_GENERIC"
    assert without["summary"] is None
    # Every ingredient-specific field is byte-for-byte what it was without the summary.
    assert _without_summary(with_summary) == _without_summary(without)
    origin = next(s for s in with_summary["summary"]["sections"] if s["kind"] == "ORIGIN")["text"]
    assert "not identified by the E-number alone" in origin
    assert "soy" not in with_summary["summary"]["sections"][0]["text"].lower().replace("soybeans", "")


@pytest.mark.asyncio
async def test_a_source_verified_english_summary_has_sections_citations_and_no_ledger_or_filler(app_client, seeded):
    headers = await _headers(app_client)
    body = (await app_client.get("/api/v1/ingredients/E322", headers=headers)).json()
    summary = body["summary"]
    assert set(summary) == {
        "scope", "evidenceState", "humanReviewed", "verifiedAt", "sections", "citations", "localizations",
    }
    assert summary["evidenceState"] == "SOURCE_VERIFIED" and summary["humanReviewed"] is False
    assert [s["kind"] for s in summary["sections"]] == ["ORIGIN", "FUNCTION", "EFFECTS", "JURISDICTION"]
    assert summary["sections"][3]["jurisdiction"] == "EU" and summary["sections"][0]["jurisdiction"] is None
    cited = {cid for s in summary["sections"] for cid in s["citationIds"]}
    assert cited == {c["id"] for c in summary["citations"]}
    for citation in summary["citations"]:
        assert set(citation) == {"id", "label", "url", "documentDate", "accessType", "supports"}
        assert citation["url"].startswith("https://") and citation["supports"]
    assert all(s["text"].strip() for s in summary["sections"])
    assert "claims" not in json.dumps(summary) and "locator" not in json.dumps(summary)


@pytest.mark.asyncio
async def test_bulgarian_is_absent_until_reviewed_then_served_and_withdrawn_when_english_changes(
    app_client, seeded, tmp_path
):
    headers = await _headers(app_client)
    body = (await app_client.get("/api/v1/ingredients/E322", headers=headers)).json()
    assert body["summary"]["localizations"] == {}  # machine draft: English fallback only

    async with seeded() as db:
        digest = (
            await db.execute(select(IngredientSummary.content_hash).where(IngredientSummary.subject_key == "E322"))
        ).scalar_one()
    assert (await summary_review.approve("E322", "bg", digest, "reviewer")).startswith("approved")
    body = (await app_client.get("/api/v1/ingredients/E322", headers=headers)).json()
    bg = body["summary"]["localizations"]["bg"]
    assert bg["translationStatus"] == "REVIEWED" and bg["translationSource"] == "MACHINE_TRANSLATED"
    assert [s["kind"] for s in bg["sections"]] == [s["kind"] for s in body["summary"]["sections"]]
    assert all(set(s) == {"kind", "text", "jurisdiction"} for s in bg["sections"])  # no citations per language

    # The English source changes: the old review no longer applies, so it is not served.
    data = json.loads(loader.SUMMARIES_SEED_FILE.read_text(encoding="utf-8"))
    e322 = next(s for s in data["subjects"] if s["subjectKey"] == "E322")
    e322["sections"][0]["text"] += " Reworded."
    e322["localizations"]["bg"]["sections"][0]["text"] += " Преформулирано."
    path = tmp_path / "reworded.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    async with seeded() as db:
        await loader.load_summaries(db, path)
        await db.commit()
    body = (await app_client.get("/api/v1/ingredients/E322", headers=headers)).json()
    assert body["summary"]["localizations"] == {}
    assert body["summary"]["sections"][0]["text"].endswith("Reworded.")


@pytest.mark.asyncio
async def test_an_unverified_summary_row_is_not_served(app_client, seeded):
    async with seeded() as db:
        row = (await db.execute(select(IngredientSummary).where(IngredientSummary.subject_key == "E322"))).scalar_one()
        row.evidence_state = "DRAFT"
        await db.commit()
    headers = await _headers(app_client)
    assert (await app_client.get("/api/v1/ingredients/E322", headers=headers)).json()["summary"] is None


@pytest.mark.asyncio
async def test_the_list_endpoint_carries_summaries_too(app_client, seeded):
    headers = await _headers(app_client)
    page = (await app_client.get("/api/v1/ingredients?search=lecithin", headers=headers)).json()
    item = next(i for i in page["items"] if i["eNumber"] == "E322")
    assert item["summary"]["scope"] == "E_NUMBER_GENERIC"
    other = (await app_client.get("/api/v1/ingredients?search=aspartame", headers=headers)).json()["items"][0]
    assert other["summary"] is None  # no summary for E951 in the pilot: null, never filler


@pytest.mark.asyncio
async def test_the_kill_switch_removes_every_summary_without_touching_stored_rows(
    app_client, seeded, monkeypatch
):
    await _known(seeded, "Sugar", "Salt")
    monkeypatch.setattr(settings, "INGREDIENT_SUMMARIES_SERVE", False)
    headers = await _headers(app_client)
    body = await _scan_text(app_client, headers, "Sugar, Salt, Colour (E150d)")
    assert body["ingredients"] and all(ing["summary"] is None for ing in body["ingredients"])
    async with seeded() as db:
        assert (await db.execute(select(func.count()).select_from(IngredientSummary))).scalar_one() == 5


@pytest.mark.asyncio
async def test_a_possibly_merged_token_gets_no_summary_even_with_a_matching_e_number(app_client, seeded):
    async with seeded() as db:
        row = await db.get(Ingredient, "e322_soy_lecithin")
        row.identity_uncertain = True
        row.uncertainty_reason = "COLON_SEPARATED_CLAUSE_MERGE"
        await db.commit()
    headers = await _headers(app_client)
    assert (await app_client.get("/api/v1/ingredients/E322", headers=headers)).json()["summary"] is None


@pytest.mark.asyncio
async def test_an_unverified_name_keeps_the_official_code_summary_but_gets_no_name_summary(app_client, seeded):
    headers = await _headers(app_client)
    # Fresh catalog, no AI: nothing confirms the language of either name.
    body = await _scan_text(app_client, headers, "Sugar, Colour (E150d)")
    by_name = _by_name(body)
    assert by_name["sugar"]["identityUncertain"] is True and by_name["sugar"]["summary"] is None
    coloured = _e_number(body, "E150D")
    assert coloured["identityUncertain"] is True and coloured["uncertaintyReason"] == "TRANSLATION_UNRELIABLE"
    assert coloured["summary"]["scope"] == "E_NUMBER_GENERIC"  # anchored on the official code alone
    assert coloured["verificationStatus"] != "VERIFIED"  # and the row's own trust state is untouched


# -------------------------------------------------------------- scan paths


@pytest.mark.asyncio
async def test_ocr_scan_attaches_summaries_by_exact_identity_only_and_null_elsewhere(
    app_client, seeded
):
    await _known(seeded, "Sugar", "Salt", "Palm oil", "Sunflower oil")
    headers = await _headers(app_client)
    body = await _scan_text(app_client, headers, "Sugar, Salt, Palm oil, Sunflower oil, Colour (E150d)")
    assert all("summary" in ing for ing in body["ingredients"])
    by_name = _by_name(body)

    assert by_name["sugar"]["summary"]["scope"] == "INGREDIENT_NAME"
    assert [s["kind"] for s in by_name["sugar"]["summary"]["sections"]] == ["EFFECTS"]  # absent sections are absent
    assert by_name["salt"]["summary"]["citations"][0]["url"].startswith("https://www.who.int/")
    assert [s["kind"] for s in by_name["palm oil"]["summary"]["sections"]] == ["ORIGIN"]
    assert by_name["sunflower oil"]["summary"] is None  # a different name is a different subject

    e150d = _e_number(body, "E150D")
    assert e150d["summary"]["scope"] == "E_NUMBER_GENERIC"
    assert [s["kind"] for s in e150d["summary"]["sections"]] == E150D_KINDS
    # The summary explains the code but does not fill or promote the ingredient's own fields.
    assert e150d["description"] == ""
    assert e150d["verificationStatus"] != "VERIFIED" and e150d["riskLevel"] not in ("HIGH", "CRITICAL")


@pytest.mark.asyncio
async def test_e150d_spellings_that_normalize_to_the_exact_code_get_the_summary_and_guesses_do_not(
    app_client, seeded
):
    headers = await _headers(app_client)
    for text in ("Colour E150d, Water", "E-150d, Water", "Caramel colour E 150d, Water"):
        body = await _scan_text(app_client, headers, text)
        assert _e_number(body, "E150D")["summary"] is not None, text
    # A spaced suffix reads as the broader E150 and a Cyrillic prefix as no code at all.
    # Neither is silently upgraded to E150d (documented identity-matching gaps).
    spaced = await _scan_text(app_client, headers, "Colour E150 d, Water")
    assert not any(ing["eNumber"] == "E150D" for ing in spaced["ingredients"])
    assert not any(ing["summary"] for ing in spaced["ingredients"] if ing["eNumber"] in (None, "E150"))
    cyrillic = await _scan_text(app_client, headers, "Оцветител Е150d, Вода")
    assert not any(ing["summary"] and ing["summary"]["scope"] == "E_NUMBER_GENERIC" for ing in cyrillic["ingredients"])


@pytest.mark.asyncio
async def test_a_summary_never_changes_the_scan_response_apart_from_its_own_key(
    app_client, seeded, monkeypatch
):
    await _known(seeded, "Water", "Sugar", "Salt")
    headers = await _headers(app_client, "summary-invariance-device")
    text = "Water, Sugar, Salt, Colour (E150d), Soy Lecithin (E322)"
    with_summary = await _scan_text(app_client, headers, text)
    monkeypatch.setattr(settings, "INGREDIENT_SUMMARIES_SERVE", False)
    without = await _scan_text(app_client, headers, text)
    assert [_without_summary(i) for i in with_summary["ingredients"]] == [_without_summary(i) for i in without["ingredients"]]
    assert with_summary["healthScore"] == without["healthScore"] and with_summary["warnings"] == without["warnings"]
    assert with_summary["product"]["hasVerifiedIngredients"] == without["product"]["hasVerifiedIngredients"]
    assert any(i["summary"] for i in with_summary["ingredients"])


@pytest.mark.asyncio
async def test_a_barcode_linked_cached_product_returns_the_same_summaries(app_client, seeded):
    await _known(seeded, "Sugar")
    headers = await _headers(app_client, "summary-barcode-device")
    barcode = "4006381333931"
    first = await _scan_text(app_client, headers, "Contains: Sugar, Colour (E150d)", barcode=barcode)
    assert any(i["summary"] for i in first["ingredients"])
    # An OCR-only product has no verified nutrition, so the barcode scan answers with the
    # existing partial 404 envelope; its preserved ingredients carry the same summaries.
    resp = await app_client.post("/api/v1/scan/barcode", json={"barcode": barcode}, headers=headers)
    assert resp.status_code == 404, resp.text
    partial = resp.json()["error"]["details"]
    assert {i["commonName"]: bool(i["summary"]) for i in partial["ingredients"]} == {
        i["commonName"]: bool(i["summary"]) for i in first["ingredients"]
    }
    assert _e_number(partial, "E150D")["summary"]["scope"] == "E_NUMBER_GENERIC"


@pytest.mark.asyncio
async def test_label_image_scan_attaches_summaries_too(app_client, seeded, monkeypatch):
    payload = {
        "productName": "Test Cola",
        "brand": "Test Co",
        "rawIngredientText": "Contains: Sugar, E150d",
        "ingredients": [{"commonName": "Sugar"}, {"commonName": "E150d", "eNumber": "E150d"}],
    }

    async def fake_analyze_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        return json.dumps(payload)

    monkeypatch.setattr(gemini_service, "analyze_image", fake_analyze_image)
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color=(50, 60, 70)).save(buf, format="JPEG")
    await _known(seeded, "Sugar")
    headers = await _headers(app_client, "summary-image-device")
    resp = await app_client.post(
        "/api/v1/scan/label-image", headers=headers, files={"image": ("label.jpg", buf.getvalue(), "image/jpeg")}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert _e_number(body, "E150D")["summary"]["scope"] == "E_NUMBER_GENERIC"
    assert _by_name(body)["sugar"]["summary"]["scope"] == "INGREDIENT_NAME"
