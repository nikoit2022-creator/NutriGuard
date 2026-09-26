"""
Issue #23 stage 2 -- the ingredient candidate queue, exercised through the
real service functions and, for the scan-facing behavior, the real HTTP
endpoints. Synthetic fixtures only; every Gemini call is mocked or off.
"""
import io
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.integrations.gemini import GeminiUnavailableError, gemini_service
from app.models.enums import IngredientSource, IngredientVerificationStatus, RiskLevel
from app.models.ingredient import Ingredient
from app.models.ingredient_alias import IngredientAlias
from app.models.ingredient_candidate import IngredientCandidate
from app.models.product import Product
from app.repositories import ingredient_candidate_repository as repo
from app.services import ingredient_candidates, ingredient_catalog
from app.services.ocr_normalizer import create_synthetic_ingredient

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _english_tokens(request, monkeypatch):
    """The stopword-based detector calls a lone word like "Sugar" foreign, and
    without a translation service that marks the identity uncertain. Pin
    "en" so the cases below start from a clean token; the one test about
    uncertain identities opts out."""
    if "identity_uncertain" not in request.node.name:
        monkeypatch.setattr(ingredient_catalog, "detect_language", lambda text: "en")


async def _candidates(db) -> dict[str, IngredientCandidate]:
    db.expire_all()
    rows = (await db.execute(select(IngredientCandidate))).scalars().all()
    return {row.normalized_key: row for row in rows}


async def _count(db, model) -> int:
    return (await db.execute(select(func.count()).select_from(model))).scalar_one()


def _syn(name: str, e_number: str | None = None):
    synthetic = create_synthetic_ingredient(name)
    return replace(synthetic, e_number=e_number) if e_number else synthetic


def _curated(**overrides) -> Ingredient:
    values = dict(
        id="e330_citric_acid", common_name="Citric Acid", normalized_name="citric acid", e_number="E330",
        scientific_name="", category="Acidity Regulator", description="A weak organic acid.",
        purpose_in_food="Acidity regulator.", health_concerns="", evidence_level="", countries_restricted_or_banned="",
        efsa_status="", fda_status="", acceptable_daily_intake="", side_effects="", allergens="None", references="",
        risk_level=RiskLevel.SAFE, risk_assessment_available=True,
        verification_status=IngredientVerificationStatus.VERIFIED, source=IngredientSource.CURATED_SEED,
        confidence=1.0,
    )
    values.update(overrides)
    return Ingredient(**values)


# --- 1. Observation: first/last seen, counts, exact dedup ---------------------------


@pytest.mark.asyncio
async def test_a_new_unknown_token_is_recorded_once_and_linked_to_its_identity(db_session):
    (resolved,), _, _ = await ingredient_catalog.materialize_ingredients(db_session, [_syn("Xylofrobinate")])
    resolved_id = resolved.id
    rows = await _candidates(db_session)

    assert set(rows) == {"xylofrobinate"}
    row = rows["xylofrobinate"]
    assert (row.status, row.flags, row.encounter_count) == ("PENDING", "", 1)
    assert row.ingredient_id == resolved_id  # points at the identity, holds no evidence of its own
    assert row.first_seen_at == row.last_seen_at
    assert row.display_name == "Xylofrobinate" and row.e_number is None


@pytest.mark.asyncio
async def test_repeated_requests_count_and_move_last_seen_but_not_first_seen(db_session):
    obs = [ingredient_candidates.Observation(name="Xylofrobinate", e_number=None)]
    for day in range(3):
        ingredient_candidates.reset_request_scope(db_session)  # a new scan request
        await ingredient_candidates.record_observations(db_session, obs, now=NOW + timedelta(days=day))
    row = (await _candidates(db_session))["xylofrobinate"]

    assert row.encounter_count == 3
    assert row.first_seen_at.replace(tzinfo=timezone.utc) == NOW
    assert row.last_seen_at.replace(tzinfo=timezone.utc) == NOW + timedelta(days=2)


@pytest.mark.asyncio
async def test_a_token_is_counted_once_per_request_even_when_materialized_twice(db_session):
    """`materialize_ingredients` runs twice per scan (pre- and post-rebuild)."""
    await ingredient_catalog.materialize_ingredients(db_session, [_syn("Xylofrobinate")])
    await ingredient_catalog.materialize_ingredients(db_session, [_syn("Xylofrobinate")])
    assert (await _candidates(db_session))["xylofrobinate"].encounter_count == 1

    ingredient_candidates.reset_request_scope(db_session)  # next request
    await ingredient_catalog.materialize_ingredients(db_session, [_syn("Xylofrobinate")])
    assert (await _candidates(db_session))["xylofrobinate"].encounter_count == 2


@pytest.mark.asyncio
async def test_spelling_of_the_same_exact_token_deduplicates(db_session):
    await ingredient_catalog.materialize_ingredients(
        db_session, [_syn("Xylofrobinate"), _syn("xylofrobinate"), _syn("XYLOFROBINATE.")]
    )
    rows = await _candidates(db_session)
    assert list(rows) == ["xylofrobinate"] and rows["xylofrobinate"].encounter_count == 1


@pytest.mark.asyncio
async def test_similar_or_translated_tokens_are_never_merged(db_session):
    """Exact text only: near-spellings and a translation are separate
    observations, and the queue never links them."""
    await ingredient_catalog.materialize_ingredients(
        db_session,
        [_syn("Xylofrobinate"), _syn("Xylofrobinates"), _syn("Xylofrobinat"), _syn("Ксилофробинат")],
    )
    rows = await _candidates(db_session)
    assert len(rows) == 4
    assert len({row.ingredient_id for row in rows.values()}) == 4  # four identities, none merged


@pytest.mark.asyncio
async def test_repeated_attempts_never_promote_trust(db_session):
    await ingredient_catalog.materialize_ingredients(db_session, [_syn("Xylofrobinate")])
    ingredient = (await db_session.execute(select(Ingredient).where(Ingredient.common_name == "Xylofrobinate"))).scalar_one()
    before = {c.name: getattr(ingredient, c.name) for c in Ingredient.__table__.columns}

    for _ in range(25):
        ingredient_candidates.reset_request_scope(db_session)
        await ingredient_catalog.materialize_ingredients(db_session, [_syn("Xylofrobinate")])

    assert (await _candidates(db_session))["xylofrobinate"].encounter_count == 26
    db_session.expire_all()
    ingredient = (await db_session.execute(select(Ingredient).where(Ingredient.common_name == "Xylofrobinate"))).scalar_one()
    assert {c.name: getattr(ingredient, c.name) for c in Ingredient.__table__.columns} == before
    assert ingredient.verification_status == IngredientVerificationStatus.UNVERIFIED


@pytest.mark.asyncio
async def test_a_token_resolving_to_a_curated_identity_is_not_a_candidate(db_session):
    db_session.add(_curated())
    await db_session.flush()
    resolved, *_ = (await ingredient_catalog.materialize_ingredients(db_session, [_syn("Citric Acid (E330)")]))[0]
    assert resolved.id == "e330_citric_acid"
    assert await _count(db_session, IngredientCandidate) == 0


# --- 2. OCR junk: never an identity, only counted -----------------------------------


@pytest.mark.asyncio
async def test_junk_is_not_materialized_or_returned_but_is_counted(db_session):
    tokens = [
        _syn("Sugar"),
        _syn("1234"),
        _syn("Ingredients could not be extracted from the image"),
        _syn("AI response was unavailable or invalid"),
    ]
    materialized, _, _ = await ingredient_catalog.materialize_ingredients(db_session, tokens)

    assert [i.common_name for i in materialized] == ["Sugar"]  # junk is not returned as an ingredient
    names = set((await db_session.execute(select(Ingredient.common_name))).scalars().all())
    assert names == {"Sugar"}  # and never became a catalog identity
    assert await _count(db_session, IngredientAlias) == 1

    rows = await _candidates(db_session)
    assert rows["1234"].status == "JUNK" and rows["1234"].flags == "NO_LETTERS"
    assert rows["1234"].ingredient_id is None
    placeholder = rows["ingredients could not be extracted from the image"]
    assert placeholder.status == "JUNK" and placeholder.flags == "PLACEHOLDER_TEXT"
    assert rows["sugar"].status == "PENDING"


# --- 3. Anomalies are flagged; identity resolution is unchanged ---------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "token, flag",
    [
        ("May contain peanuts", "ALLERGEN_STATEMENT"),
        ("Съставки: вода", "HEADER_ARTIFACT"),
        ("Colorant: e150d", "CLASS_PREFIXED"),
        ("Colour", "GENERIC_FUNCTION_TERM"),
        ("Raising agents (ammonium bicarbonate", "UNBALANCED_PARENTHESIS"),
    ],
)
async def test_anomalous_tokens_are_flagged_and_still_resolve_as_before(db_session, token, flag):
    materialized, _, _ = await ingredient_catalog.materialize_ingredients(db_session, [_syn(token)])
    assert len(materialized) == 1  # unchanged: still returned as an ingredient
    resolved_id = materialized[0].id
    row = next(iter((await _candidates(db_session)).values()))
    assert row.status == "FLAGGED" and flag in row.flags.split(",")
    assert row.ingredient_id == resolved_id


@pytest.mark.asyncio
async def test_the_e_number_in_a_flagged_token_is_kept_on_the_observation(db_session):
    await ingredient_catalog.materialize_ingredients(db_session, [_syn("Colorant: e150d")])
    row = next(iter((await _candidates(db_session)).values()))
    assert row.e_number == "E150D"


@pytest.mark.asyncio
async def test_conflicting_identifiers_are_flagged_and_never_resolved_by_the_queue(db_session):
    first = (await ingredient_catalog.materialize_ingredients(db_session, [_syn("Blorp", "E301")]))[0][0]
    ingredient_candidates.reset_request_scope(db_session)
    second = (await ingredient_catalog.materialize_ingredients(db_session, [_syn("Blorp", "E300")]))[0][0]

    assert second.id == first.id  # the name alias resolved it, exactly as before
    assert second.e_number == "E301"  # the row's identifier was not overwritten
    row = (await _candidates(db_session))["blorp"]
    assert "CONFLICTING_IDENTIFIER" in row.flags.split(",") and row.status == "FLAGGED"
    assert row.encounter_count == 2


@pytest.mark.asyncio
async def test_a_generic_token_resolved_to_a_specific_identity_is_flagged(db_session):
    specific = (await ingredient_catalog.materialize_ingredients(db_session, [_syn("Soy foobar", "E999")]))[0][0]
    ingredient_candidates.reset_request_scope(db_session)
    resolved = (await ingredient_catalog.materialize_ingredients(db_session, [_syn("Foobar", "E999")]))[0][0]

    assert resolved.id == specific.id  # the official identifier decided, as before
    row = (await _candidates(db_session))["foobar"]
    assert "GENERIC_VS_SPECIFIC" in row.flags.split(",")


@pytest.mark.asyncio
async def test_flags_only_accumulate(db_session):
    obs = ingredient_candidates.Observation
    await ingredient_candidates.record_observations(db_session, [obs(name="Colour", e_number=None)], now=NOW)
    ingredient_candidates.reset_request_scope(db_session)
    await ingredient_candidates.record_observations(db_session, [obs(name="Colour", e_number="E150")], now=NOW)
    row = (await _candidates(db_session))["colour"]
    assert row.flags == "GENERIC_FUNCTION_TERM" and row.e_number == "E150"  # identifier filled, never overwritten
    ingredient_candidates.reset_request_scope(db_session)
    await ingredient_candidates.record_observations(db_session, [obs(name="Colour", e_number="E999")], now=NOW)
    assert (await _candidates(db_session))["colour"].e_number == "E150"


@pytest.mark.asyncio
async def test_identity_uncertain_rows_are_flagged_in_the_queue(db_session):
    """No translation service here, so the catalog marks the lone foreign-looking
    word `identity_uncertain`; the queue carries that as a flag and never
    treats the name as verified."""
    materialized, _, _ = await ingredient_catalog.materialize_ingredients(db_session, [_syn("Xylofrobinate")])
    assert materialized[0].identity_uncertain is True
    row = (await _candidates(db_session))["xylofrobinate"]
    assert row.status == "FLAGGED" and row.flags == "IDENTITY_UNCERTAIN"


# --- 4. Bounds ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_long_names_are_bounded_flagged_and_distinct(db_session):
    a, b = "z" * 200 + "a", "z" * 200 + "b"
    await ingredient_candidates.record_observations(
        db_session,
        [ingredient_candidates.Observation(name=a, e_number=None), ingredient_candidates.Observation(name=b, e_number=None)],
        now=NOW,
    )
    rows = list((await _candidates(db_session)).values())
    assert len(rows) == 2
    for row in rows:
        assert len(row.display_name) <= 128 and len(row.normalized_key) <= 128
        assert "TOO_LONG" in row.flags.split(",")


@pytest.mark.asyncio
async def test_the_row_cap_refuses_new_candidates_but_keeps_counting_and_never_evicts(db_session, monkeypatch):
    monkeypatch.setattr(settings, "INGREDIENT_CANDIDATE_MAX_ROWS", 2)
    obs = ingredient_candidates.Observation
    first = await ingredient_candidates.record_observations(
        db_session, [obs(name="Alpha", e_number=None), obs(name="Beta", e_number=None), obs(name="Gamma", e_number=None)], now=NOW
    )
    assert (first.inserted, first.dropped) == (2, 1)
    assert set(await _candidates(db_session)) == {"alpha", "beta"}

    ingredient_candidates.reset_request_scope(db_session)
    second = await ingredient_candidates.record_observations(
        db_session, [obs(name="Alpha", e_number=None), obs(name="Gamma", e_number=None)], now=NOW
    )
    assert (second.updated, second.dropped) == (1, 1)
    rows = await _candidates(db_session)
    assert set(rows) == {"alpha", "beta"} and rows["alpha"].encounter_count == 2


@pytest.mark.asyncio
async def test_a_queue_failure_never_fails_the_scan_or_the_catalog_write(db_session, monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("queue down")

    monkeypatch.setattr(repo, "observe", boom)
    materialized, _, _ = await ingredient_catalog.materialize_ingredients(db_session, [_syn("Xylofrobinate")])

    assert [i.common_name for i in materialized] == ["Xylofrobinate"]
    assert await _count(db_session, Ingredient) == 1  # the catalog row survived
    assert await _count(db_session, IngredientCandidate) == 0
    # ...and a later request can still count it (the failed attempt was not marked seen).
    monkeypatch.undo()
    await ingredient_catalog.materialize_ingredients(db_session, [_syn("Xylofrobinate")])
    assert (await _candidates(db_session))["xylofrobinate"].encounter_count == 1


# --- 5. Concurrency shape on SQLite: separate sessions converge on one row -----------


@pytest.mark.asyncio
async def test_separate_sessions_converge_on_one_row(db_engine):
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False, class_=AsyncSession)
    for _ in range(4):
        async with factory() as session:
            await ingredient_candidates.record_observations(
                session, [ingredient_candidates.Observation(name="Xylofrobinate", e_number=None)], now=NOW
            )
            await session.commit()
    async with factory() as session:
        rows = (await session.execute(select(IngredientCandidate))).scalars().all()
    assert [(r.normalized_key, r.encounter_count) for r in rows] == [("xylofrobinate", 4)]


# --- 6. Manual maintenance: dry-run by default, queue-only ---------------------------


async def _seed_queue(db_session):
    """Four rows: stale+rare, stale+common, fresh+rare, stale+rare but FLAGGED."""
    obs = ingredient_candidates.Observation
    old = NOW - timedelta(days=400)
    for name, when, times in [("Stale rare", old, 1), ("Stale but common", old, 9), ("Fresh rare", NOW, 1), ("Colour", old, 1)]:
        for _ in range(times):
            ingredient_candidates.reset_request_scope(db_session)
            await ingredient_candidates.record_observations(db_session, [obs(name=name, e_number=None)], now=when)
    await db_session.flush()


@pytest.mark.asyncio
async def test_prune_is_a_dry_run_by_default(db_session):
    await _seed_queue(db_session)
    plan = await ingredient_candidates.prune(db_session, now=NOW)

    assert plan.dry_run is True and plan.removed == 0
    assert [(status, flags, count) for _, status, flags, count in plan.candidates] == [("PENDING", "", 1)]  # "stale rare" only
    assert set(await _candidates(db_session)) == {"stale rare", "stale but common", "fresh rare", "colour"}


@pytest.mark.asyncio
async def test_prune_apply_removes_only_stale_rare_unflagged_queue_rows(db_session):
    await _seed_queue(db_session)
    assert (await _candidates(db_session))["colour"].status == "FLAGGED"
    await ingredient_catalog.materialize_ingredients(db_session, [_syn("Widget gum")])
    ingredients_before = await _count(db_session, Ingredient)
    aliases_before = await _count(db_session, IngredientAlias)

    plan = await ingredient_candidates.prune(db_session, dry_run=False, now=NOW)
    remaining = set(await _candidates(db_session))

    assert plan.removed == 1
    assert "stale rare" not in remaining  # stale + rare + unflagged
    assert {"stale but common", "fresh rare", "colour"} <= remaining  # common, fresh, FLAGGED: all kept
    assert await _count(db_session, Ingredient) == ingredients_before  # identities untouched
    assert await _count(db_session, IngredientAlias) == aliases_before


@pytest.mark.asyncio
async def test_backfill_seeds_uncurated_identities_idempotently_without_touching_them(db_session):
    db_session.add(_curated())
    await db_session.flush()
    # Two OCR-derived identities, one referenced by two products.
    await ingredient_catalog.materialize_ingredients(db_session, [_syn("Xylofrobinate"), _syn("Quuxine")])
    ingredient_candidates.reset_request_scope(db_session)
    await db_session.execute(IngredientCandidate.__table__.delete())  # as if scanned before the queue existed
    ids = {i.common_name: i.id for i in (await db_session.execute(select(Ingredient))).scalars().all()}
    for barcode in ("4006381333931", "4014400900057"):
        db_session.add(Product(
            barcode=barcode, product_name="P", brand="B", category="C", raw_ingredient_text="",
            ingredient_ids=ids["Xylofrobinate"], health_score=0, nova_group=1, sugar_grams=0.0, sodium_mg=0.0,
            saturated_fat_grams=0.0, timestamp=1, source="label_scan", is_verified=False,
            has_verified_nutrition=False, has_verified_ingredients=False,
        ))
    await db_session.flush()
    snapshot = {c.name: [getattr(i, c.name) for i in (await db_session.execute(select(Ingredient).order_by(Ingredient.id))).scalars()]
                for c in Ingredient.__table__.columns}

    dry = await ingredient_candidates.backfill_from_catalog(db_session)
    assert (dry.dry_run, dry.would_create, dry.created) == (True, 2, 0)
    assert await _count(db_session, IngredientCandidate) == 0

    applied = await ingredient_candidates.backfill_from_catalog(db_session, dry_run=False)
    assert (applied.created, applied.already_present) == (2, 0)
    rows = await _candidates(db_session)
    assert rows["xylofrobinate"].encounter_count == 2  # measured: two products reference it
    assert rows["quuxine"].encounter_count == 1  # never referenced: floor of 1, not invented history
    assert "e330_citric_acid" not in {r.ingredient_id for r in rows.values()}  # curated identities are not candidates

    again = await ingredient_candidates.backfill_from_catalog(db_session, dry_run=False)
    assert (again.created, again.already_present) == (0, 2)  # idempotent
    db_session.expire_all()
    assert snapshot == {c.name: [getattr(i, c.name) for i in (await db_session.execute(select(Ingredient).order_by(Ingredient.id))).scalars()]
                        for c in Ingredient.__table__.columns}


@pytest.mark.asyncio
async def test_deleting_an_identity_keeps_the_observation(db_session):
    materialized, _, _ = await ingredient_catalog.materialize_ingredients(db_session, [_syn("Xylofrobinate")])
    identity = materialized[0]
    await db_session.execute(IngredientAlias.__table__.delete())
    await db_session.delete(identity)
    await db_session.flush()
    # SQLite enforces FKs only when PRAGMA foreign_keys is on; the PostgreSQL
    # migration test proves ON DELETE SET NULL. Here: the row is still there.
    assert "xylofrobinate" in await _candidates(db_session)


# --- 7. Scan endpoints (real HTTP JSON) -----------------------------------------------


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color=(10, 20, 30)).save(buf, format="JPEG")
    return buf.getvalue()


async def _headers(client, device_id: str) -> dict:
    resp = await client.post("/api/v1/auth/device", json={"deviceId": device_id})
    return {"Authorization": f"Bearer {resp.json()['accessToken']}"}


@pytest.mark.asyncio
async def test_ocr_text_drops_junk_tokens_and_counts_the_unknown_ones(app_client, db_session):
    resp = await app_client.post(
        "/api/v1/scan/ocr-text", headers=await _headers(app_client, "cq-ocr"),
        json={"rawText": "Sugar, 1234, Salt, Xylofrobinate"},
    )
    assert resp.status_code == 200, resp.text
    assert [i["commonName"] for i in resp.json()["ingredients"]] == ["Sugar", "Salt", "Xylofrobinate"]

    rows = await _candidates(db_session)
    assert rows["1234"].status == "JUNK"
    assert {k for k, r in rows.items() if r.status == "PENDING"} == {"sugar", "salt", "xylofrobinate"}
    assert all(r.encounter_count == 1 for r in rows.values())  # once per request, not per pass

    await app_client.post(
        "/api/v1/scan/ocr-text", headers=await _headers(app_client, "cq-ocr-2"), json={"rawText": "Sugar, Salt"}
    )
    rows = await _candidates(db_session)
    assert (rows["sugar"].encounter_count, rows["salt"].encounter_count, rows["xylofrobinate"].encounter_count) == (2, 2, 1)


@pytest.mark.asyncio
async def test_a_failed_label_scan_no_longer_puts_error_text_into_the_catalog(app_client, monkeypatch, db_session):
    """Reproduced at baseline 32bd7ef: the provider-down fallback tokenized
    its own placeholder sentence into two `OCR_HEURISTIC` catalog rows."""

    async def down(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        raise GeminiUnavailableError("down")

    monkeypatch.setattr(gemini_service, "analyze_image", down)
    resp = await app_client.post(
        "/api/v1/scan/label-image", headers=await _headers(app_client, "cq-down"),
        files={"image": ("label.jpg", _jpeg(), "image/jpeg")},
    )
    assert resp.status_code == 404
    assert await _count(db_session, Ingredient) == 0
    assert await _count(db_session, IngredientAlias) == 0
    product = (await db_session.execute(select(Product))).scalar_one()
    assert product.raw_ingredient_text == "" and product.ingredient_ids == ""
    assert "could not be extracted" not in json.dumps(resp.json())


@pytest.mark.asyncio
async def test_an_untrusted_attempt_keeps_an_existing_unverified_products_ingredients(app_client, monkeypatch, db_session):
    barcode = "4260107010029"
    db_session.add(Product(
        barcode=barcode, product_name="Discovered Product", brand="Unknown Brand", category="",
        raw_ingredient_text="Water, Sugar", ingredient_ids="ing_water,ing_sugar", health_score=0, nova_group=1,
        sugar_grams=0.0, sodium_mg=0.0, saturated_fat_grams=0.0, timestamp=1, source="open_food_facts",
        is_verified=False, has_verified_nutrition=False, has_verified_ingredients=False,
    ))
    await db_session.commit()

    async def down(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        raise GeminiUnavailableError("down")

    monkeypatch.setattr(gemini_service, "analyze_image", down)
    resp = await app_client.post(
        "/api/v1/scan/label-image", headers=await _headers(app_client, "cq-keep"),
        files={"image": ("label.jpg", _jpeg(), "image/jpeg")}, data={"barcode": barcode},
    )
    assert resp.status_code == 404

    db_session.expire_all()
    product = (await db_session.execute(select(Product).where(Product.barcode == barcode))).scalar_one()
    assert product.raw_ingredient_text == "Water, Sugar"  # not replaced by an empty/placeholder result
    assert product.ingredient_ids == "ing_water,ing_sugar"  # references survive
    assert product.has_verified_ingredients is False


@pytest.mark.asyncio
async def test_the_wire_contract_is_unchanged(app_client):
    """No new field: the queue is internal."""
    from app.main import app

    schema = json.dumps(app.openapi())
    for internal in ("ingredient_candidate", "encounterCount", "normalizedKey", "CandidateFlag"):
        assert internal not in schema


async def _scan_ocr(client, device_id: str, text: str) -> list[dict]:
    resp = await client.post(
        "/api/v1/scan/ocr-text", headers=await _headers(client, device_id), json={"rawText": text}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["ingredients"]


@pytest.mark.asyncio
async def test_a_later_scan_is_never_merged_into_similar_uncurated_rows(app_client, db_session):
    """Reproduced before stage 2: after "Carbonated Water, Sugar-free
    sweetener, Salted butter", a scan of "Water, Sugar, Salt, Butter"
    returned those same three rows (fragment matching against uncurated rows)."""
    first = await _scan_ocr(app_client, "cq-sim-1", "Carbonated Water, Sugar-free sweetener, Salted butter")
    second = await _scan_ocr(app_client, "cq-sim-2", "Water, Sugar, Salt, Butter")

    assert [i["commonName"] for i in second] == ["Water", "Sugar", "Salt", "Butter"]
    assert {i["id"] for i in first}.isdisjoint({i["id"] for i in second})  # four new identities, none merged


@pytest.mark.asyncio
async def test_an_exact_repeat_reuses_the_identity_and_counts_an_encounter(app_client, db_session):
    first = await _scan_ocr(app_client, "cq-rep-1", "Palm oil, Sugar")
    second = await _scan_ocr(app_client, "cq-rep-2", "palm OIL")
    assert second[0]["id"] == first[0]["id"]  # exact normalized name: same identity
    rows = await _candidates(db_session)
    assert (rows["palm oil"].encounter_count, rows["sugar"].encounter_count) == (2, 1)


@pytest.mark.asyncio
async def test_a_generic_token_never_resolves_to_a_specific_curated_identity(app_client, db_session):
    db_session.add(_curated(id="e322_soy_lecithin", common_name="Soy Lecithin", normalized_name="soy lecithin", e_number="E322"))
    await db_session.commit()

    generic = await _scan_ocr(app_client, "cq-gen-1", "Lecithin")
    assert generic[0]["id"].startswith("synth_")  # its own unverified identity, not the soy row
    identified = await _scan_ocr(app_client, "cq-gen-2", "Lecithin (E322)")
    assert identified[0]["id"] == "e322_soy_lecithin"  # only the official identifier links them

