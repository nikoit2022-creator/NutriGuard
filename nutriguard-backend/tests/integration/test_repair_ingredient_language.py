"""
Coverage for `app.seed.repair_ingredient_language`: the dry-run-by-
default maintenance tool that finds and (only with `--apply`) repairs
`Ingredient`/`Product` rows persisted BEFORE the ingredient-language
pipeline (`app.services.ingredient_catalog.materialize_ingredients`)
existed, so they may still hold foreign-language text mislabeled as
canonical English/Bulgarian.

`translate_ingredient_tokens` is monkeypatched at the module level
(`repair_module.translate_ingredient_tokens`) rather than exercising the
real Gemini-backed implementation -- this suite is about the repair
tool's own detection/categorization/apply/idempotency behavior, not
about `app.services.ingredient_translation` itself (which has its own
test coverage). The fake always returns text that genuinely satisfies
`app.services.language_detection.detect_language(...) == "en"`, exactly
like a real `reliable=True` result would (see
`ingredient_translation._translation_is_reliable`), so idempotency
assertions reflect real post-repair behavior, not a test-only shortcut.
"""
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.enums import IngredientSource, IngredientVerificationStatus, RiskLevel
from app.models.ingredient import Ingredient
from app.models.ingredient_alias import IngredientAlias
from app.models.product import Product
from app.repositories import ingredient_alias_repository
from app.seed import repair_ingredient_language as repair_module
from app.services.ingredient_normalization import normalize_ingredient_name
from app.services.ingredient_translation import IngredientTokenTranslation

# A genuine translation candidate: Romanian "sugar and salt", Latin
# script, no English strong/weak-word evidence -> detect_language
# returns "other" (see tests/unit/test_language_detection.py for the
# heuristic's own coverage).
_RO_COMMON_NAME = "Zahăr și sare"
_RO_TRANSLATED = "Sugar and salt"

# Ambiguous-segmentation candidate (EU allergen-emphasis ALL-CAPS
# embedded in an otherwise lowercase clause) -- also "other"-language,
# but must NEVER be auto-translated (task requirement 6).
_AMBIGUOUS_COMMON_NAME = "agenti de crestere: enzime"

# Superficially foreign-looking, but CURATED_SEED -- must never be
# touched (task requirement 7).
_CURATED_COMMON_NAME = "Ulei de masline extra virgin"


def _make_ingredient(
    ingredient_id: str,
    common_name: str,
    *,
    source: IngredientSource,
    verification_status: IngredientVerificationStatus = IngredientVerificationStatus.UNVERIFIED,
) -> Ingredient:
    return Ingredient(
        id=ingredient_id,
        common_name=common_name,
        normalized_name=normalize_ingredient_name(common_name),
        scientific_name="",
        e_number=None,
        category="Ingredient",
        risk_level=RiskLevel.SAFE,
        risk_assessment_available=False,
        verification_status=verification_status,
        source=source,
        confidence=0.2,
    )


async def _fake_translate_ok(tokens: list[str]) -> list[IngredientTokenTranslation]:
    assert tokens == [_RO_COMMON_NAME]
    return [
        IngredientTokenTranslation(
            original_text=tokens[0],
            translated_text=_RO_TRANSLATED,
            detected_language="ro",
            confidence=0.92,
            reliable=True,
        )
    ]


async def _fake_translate_unreliable(tokens: list[str]) -> list[IngredientTokenTranslation]:
    return [
        IngredientTokenTranslation(
            original_text=t, translated_text=None, detected_language="other", confidence=None, reliable=False
        )
        for t in tokens
    ]


def _session_factory(db_engine):
    return async_sessionmaker(bind=db_engine, expire_on_commit=False, class_=AsyncSession)


@pytest.mark.asyncio
async def test_dry_run_reports_but_writes_nothing(db_engine, monkeypatch):
    monkeypatch.setattr(repair_module, "translate_ingredient_tokens", _fake_translate_ok)
    session_factory = _session_factory(db_engine)

    async with session_factory() as db:
        db.add(_make_ingredient("synth_ro_sugar_salt", _RO_COMMON_NAME, source=IngredientSource.OCR_HEURISTIC))
        await db.commit()

    async with session_factory() as db:
        report = await repair_module.run_repair(db, apply=False)

    assert report.mode == "dry_run"
    counts = report.ingredient_counts
    assert counts[repair_module.CATEGORY_TRANSLATED] == 1
    translated_entry = next(e for e in report.ingredient_details if e.ingredient_id == "synth_ro_sugar_salt")
    assert translated_entry.category == repair_module.CATEGORY_TRANSLATED
    assert translated_entry.proposed_translation == _RO_TRANSLATED
    assert translated_entry.applied is False

    # Requirement (d): zero database writes in dry-run mode -- verify
    # via a FRESH query/session, not the one the report was built with.
    async with session_factory() as db:
        row = await db.get(Ingredient, "synth_ro_sugar_salt")
        assert row.common_name == _RO_COMMON_NAME
        assert row.source == IngredientSource.OCR_HEURISTIC
        aliases = (await db.execute(select(IngredientAlias))).scalars().all()
        assert aliases == []


@pytest.mark.asyncio
async def test_apply_repairs_clean_ocr_row_and_preserves_original_text_as_alias(db_engine, monkeypatch):
    monkeypatch.setattr(repair_module, "translate_ingredient_tokens", _fake_translate_ok)
    session_factory = _session_factory(db_engine)

    async with session_factory() as db:
        db.add(_make_ingredient("synth_ro_sugar_salt", _RO_COMMON_NAME, source=IngredientSource.OCR_HEURISTIC))
        await db.commit()

    async with session_factory() as db:
        report = await repair_module.run_repair(db, apply=True)

    assert report.mode == "apply"
    entry = next(e for e in report.ingredient_details if e.ingredient_id == "synth_ro_sugar_salt")
    assert entry.category == repair_module.CATEGORY_TRANSLATED
    assert entry.applied is True

    async with session_factory() as db:
        row = await db.get(Ingredient, "synth_ro_sugar_salt")
        assert row.common_name == _RO_TRANSLATED
        assert row.normalized_name == normalize_ingredient_name(_RO_TRANSLATED)
        assert row.source == IngredientSource.GEMINI

        # Original (pre-repair) text survives as a language-tagged alias.
        original_alias = await ingredient_alias_repository.get_by_normalized(
            db, normalize_ingredient_name(_RO_COMMON_NAME)
        )
        assert original_alias is not None
        assert original_alias.ingredient_id == "synth_ro_sugar_salt"
        assert original_alias.language == "ro"

        # The new English text is also registered, so a future scan of
        # the same (now-English) text converges back onto this row.
        new_alias = await ingredient_alias_repository.get_by_normalized(
            db, normalize_ingredient_name(_RO_TRANSLATED)
        )
        assert new_alias is not None
        assert new_alias.ingredient_id == "synth_ro_sugar_salt"


@pytest.mark.asyncio
async def test_ambiguous_segmentation_is_flagged_unresolved_never_auto_repaired(db_engine, monkeypatch):
    async def _fail_if_called(tokens: list[str]) -> list[IngredientTokenTranslation]:
        raise AssertionError("translate_ingredient_tokens must never be called for an ambiguous token")

    monkeypatch.setattr(repair_module, "translate_ingredient_tokens", _fail_if_called)
    session_factory = _session_factory(db_engine)

    async with session_factory() as db:
        db.add(_make_ingredient("synth_ambiguous", _AMBIGUOUS_COMMON_NAME, source=IngredientSource.GEMINI))
        await db.commit()

    async with session_factory() as db:
        report = await repair_module.run_repair(db, apply=True)

    entry = next(e for e in report.ingredient_details if e.ingredient_id == "synth_ambiguous")
    assert entry.category == repair_module.CATEGORY_FLAGGED_UNRESOLVED_AMBIGUOUS
    assert entry.reason == "COLON_SEPARATED_CLAUSE_MERGE"

    async with session_factory() as db:
        row = await db.get(Ingredient, "synth_ambiguous")
        assert row.common_name == _AMBIGUOUS_COMMON_NAME
        assert row.source == IngredientSource.GEMINI


@pytest.mark.asyncio
async def test_curated_seed_row_is_never_touched_even_if_it_looks_foreign(db_engine, monkeypatch):
    async def _fail_if_called(tokens: list[str]) -> list[IngredientTokenTranslation]:
        raise AssertionError("translate_ingredient_tokens must never be called for a trusted-source row")

    monkeypatch.setattr(repair_module, "translate_ingredient_tokens", _fail_if_called)
    session_factory = _session_factory(db_engine)

    async with session_factory() as db:
        db.add(
            _make_ingredient(
                "curated_olive_oil",
                _CURATED_COMMON_NAME,
                source=IngredientSource.CURATED_SEED,
                verification_status=IngredientVerificationStatus.VERIFIED,
            )
        )
        await db.commit()

    async with session_factory() as db:
        report = await repair_module.run_repair(db, apply=True)

    entry = next(e for e in report.ingredient_details if e.ingredient_id == "curated_olive_oil")
    assert entry.category == repair_module.CATEGORY_SKIPPED_TRUSTED_SOURCE

    async with session_factory() as db:
        row = await db.get(Ingredient, "curated_olive_oil")
        assert row.common_name == _CURATED_COMMON_NAME
        assert row.source == IngredientSource.CURATED_SEED
        aliases = (await db.execute(select(IngredientAlias))).scalars().all()
        assert aliases == []


@pytest.mark.asyncio
async def test_apply_twice_is_idempotent(db_engine, monkeypatch):
    monkeypatch.setattr(repair_module, "translate_ingredient_tokens", _fake_translate_ok)
    session_factory = _session_factory(db_engine)

    async with session_factory() as db:
        db.add(_make_ingredient("synth_ro_sugar_salt", _RO_COMMON_NAME, source=IngredientSource.OCR_HEURISTIC))
        await db.commit()

    async with session_factory() as db:
        first_report = await repair_module.run_repair(db, apply=True)
    assert first_report.ingredient_counts[repair_module.CATEGORY_TRANSLATED] == 1

    async with session_factory() as db:
        second_report = await repair_module.run_repair(db, apply=True)

    # Already repaired -- the row's common_name is now genuinely English,
    # so the second run finds nothing left to translate for it.
    assert second_report.ingredient_counts[repair_module.CATEGORY_TRANSLATED] == 0
    entry = next(e for e in second_report.ingredient_details if e.ingredient_id == "synth_ro_sugar_salt")
    assert entry.category == repair_module.CATEGORY_ALREADY_FINE

    async with session_factory() as db:
        row = await db.get(Ingredient, "synth_ro_sugar_salt")
        assert row.common_name == _RO_TRANSLATED

        # No duplicate alias rows from running twice.
        aliases = (await db.execute(select(IngredientAlias))).scalars().all()
        normalized_texts = sorted(a.alias_normalized for a in aliases)
        assert normalized_texts == sorted(
            {normalize_ingredient_name(_RO_COMMON_NAME), normalize_ingredient_name(_RO_TRANSLATED)}
        )


@pytest.mark.asyncio
async def test_unreliable_translation_is_flagged_not_guessed(db_engine, monkeypatch):
    monkeypatch.setattr(repair_module, "translate_ingredient_tokens", _fake_translate_unreliable)
    session_factory = _session_factory(db_engine)

    async with session_factory() as db:
        db.add(_make_ingredient("synth_ro_sugar_salt", _RO_COMMON_NAME, source=IngredientSource.OCR_HEURISTIC))
        await db.commit()

    async with session_factory() as db:
        report = await repair_module.run_repair(db, apply=True)

    entry = next(e for e in report.ingredient_details if e.ingredient_id == "synth_ro_sugar_salt")
    assert entry.category == repair_module.CATEGORY_FLAGGED_UNRESOLVED_TRANSLATION_FAILED
    assert entry.reason == "TRANSLATION_UNRELIABLE"

    async with session_factory() as db:
        row = await db.get(Ingredient, "synth_ro_sugar_salt")
        assert row.common_name == _RO_COMMON_NAME
        assert row.source == IngredientSource.OCR_HEURISTIC


@pytest.mark.asyncio
async def test_product_raw_ingredient_text_is_reported_but_never_rewritten(db_engine, monkeypatch):
    async def _fail_if_called(tokens: list[str]) -> list[IngredientTokenTranslation]:
        raise AssertionError("no ingredient candidates in this test")

    monkeypatch.setattr(repair_module, "translate_ingredient_tokens", _fail_if_called)
    session_factory = _session_factory(db_engine)

    async with session_factory() as db:
        db.add(
            Product(
                barcode="1234567890123",
                product_name="Test product",
                raw_ingredient_text=_RO_COMMON_NAME,
                health_score=50,
                nova_group=1,
            )
        )
        await db.commit()

    async with session_factory() as db:
        report = await repair_module.run_repair(db, apply=True)

    assert len(report.product_flags) == 1
    flag = report.product_flags[0]
    assert flag.barcode == "1234567890123"
    assert flag.detected_language == "other"

    async with session_factory() as db:
        product = await db.get(Product, "1234567890123")
        assert product.raw_ingredient_text == _RO_COMMON_NAME
