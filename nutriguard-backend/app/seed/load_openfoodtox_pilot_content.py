"""Explicit, transactional, idempotent dry-run/apply import of the four
owner-approved OpenFoodTox pilot identities' display content (E250,
E150d, E330, E951) -- see docs/OPENFOODTOX_APP_PILOT_INTEGRATION_TASK.md.

Deliberately NOT `app/seed/load_seed.py`'s broad merge: that loader's
`session.merge(Ingredient(**kwargs))` path overwrites a row's ENTIRE set
of scientific/regulatory/dietary/scoring fields unconditionally, which
would be exactly the "indiscriminate seed merge" the task explicitly
forbids here. This module touches ONLY the fields listed in
`_UPDATABLE_FIELDS` below, for ONLY the four identities in `_ALLOWLIST`,
and never writes anything in dry-run mode.

What this import does and does not touch
-----------------------------------------
For an EXISTING ingredient (resolved by an EXACT, non-fuzzy `e_number`
match -- see `app.repositories.ingredient_repository.get_by_official_identifier`,
so "E150" can never match "E150d" or vice versa):
  * Updates: `description`, `purpose_in_food`, `health_concerns`,
    `effect_conditions`, `dietary_guidance` (EN on `Ingredient`, EN+BG on
    `IngredientLocalization`), and `references` (EN only -- citations are
    not per-language).
  * Never touches: `risk_level`, `risk_assessment_available`,
    `verification_status`, `source`, `confidence`, `efsa_status`,
    `fda_status`, `acceptable_daily_intake`, `is_gluten`/`is_vegan`/
    `is_vegetarian`/`is_halal`/`is_kosher`, every `bad_for_*` flag,
    `category`, `common_name`, `scientific_name`, `cas_number`,
    `ins_number`, `id`. This is what makes "prove the pilot leaves
    existing product Health Scores unchanged" true by construction for
    E250/E330/E951: nothing this import writes ever reaches
    `app.services.health_score.calculate` or `app.services.warning_engine`.
  * Never promotes `verification_status` -- a row that was `LIMITED_DATA`
    (E330, today) stays `LIMITED_DATA`.

For the one identity missing entirely today (E150d): provisions a new,
distinct `Ingredient` row with `verification_status=LIMITED_DATA`,
`risk_assessment_available=False` (so it can never move any product's
Health Score either, in either direction -- see
`app.services.food_analysis._score_and_warnings`), `risk_level=SAFE`
(the existing neutral placeholder convention for an unassessed row, same
as `app.services.ocr_normalizer.SyntheticIngredient`), every dietary flag
left `None`/`False` (genuinely unknown/unasserted, never guessed), and
`acceptable_daily_intake`/`efsa_status`/`fda_status` left `""` --
the pilot's own numeric-eligibility check withheld E150d's group-ADI
figure from the consumer preview (unresolved chemical basis), and "owner
publication permission alone does not resolve a chemical-basis
ambiguity" (task's own words), so no number or approval-sounding text is
asserted here either.

BG localization honesty
------------------------
Every BG row this import writes is `translation_status=DRAFT`,
`translation_source=MACHINE_TRANSLATED`, `owner_approved_without_review=True`
-- never `REVIEWED`/`HUMAN_CURATED`, because nobody but the owner's
publication-permission decision has signed off on this specific content
(see the task's own framing: "NOT a claim that an independent scientist
or human translator reviewed it"). An existing BG row is replaced UNLESS
its `translation_source` is `HUMAN_CURATED` (a genuinely human-authored
translation, as opposed to this codebase's existing
REVIEWED+MACHINE_TRANSLATED convention for seed-loader translations) --
that one case is always left untouched, regardless of status.

Usage::

    python -m app.seed.load_openfoodtox_pilot_content            # dry run, prints the plan
    python -m app.seed.load_openfoodtox_pilot_content --apply     # writes + commits, one transaction
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import structlog

from app.database.session import AsyncSessionLocal
from app.models.enums import (
    IngredientSource,
    IngredientTranslationSource,
    IngredientTranslationStatus,
    IngredientVerificationStatus,
    RiskLevel,
)
from app.models.ingredient import Ingredient
from app.models.ingredient_localization import IngredientLocalization
from app.repositories import ingredient_repository
from app.services.ingredient_catalog import (
    _dump_field_provenance,
    _load_field_provenance,
    register_curated_alias,
)
from app.services.ingredient_localization import canonical_text_hash

logger = structlog.get_logger(__name__)

_ARTIFACT_PATH = Path(__file__).parent / "openfoodtox_pilot_profiles.json"

# Exact four-identity allowlist -- this import must never touch any
# ingredient outside this set, and an artifact profile for anything else
# is a bug, not silently ignored.
_ALLOWLIST: tuple[str, ...] = ("E250", "E150d", "E330", "E951")

# The only fields this import is ever allowed to write on an EXISTING
# `Ingredient` row -- see module docstring. `references` is EN-only
# (handled separately below, not part of localization).
_UPDATABLE_EN_FIELDS: tuple[str, ...] = (
    "description", "purpose_in_food", "health_concerns", "effect_conditions", "dietary_guidance",
)

# Confidence recorded against each touched field's per-field provenance
# entry -- deliberately below 1.0 (the main curated seed's own value) to
# honestly reflect "source-backed, owner-approved for display, not
# independently regulatory re-verified this round" (see
# `app.services.ingredient_catalog.resolve_field_source`, the only
# intended reader of this value).
_FIELD_CONFIDENCE = 0.9


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class FieldChange:
    field: str
    old: str
    new: str


@dataclass
class IdentityPlan:
    e_number: str
    action: str  # "create" | "update" | "no_op"
    ingredient_id: str
    en_changes: list[FieldChange] = field(default_factory=list)
    bg_action: str = "none"  # "create" | "replace" | "skip_human_curated" | "no_op"
    bg_changes: list[FieldChange] = field(default_factory=list)
    bg_common_name: str = ""
    notes: list[str] = field(default_factory=list)


def _load_artifact() -> dict:
    data = json.loads(_ARTIFACT_PATH.read_text(encoding="utf-8"))
    missing = set(_ALLOWLIST) - set(data.get("profiles", {}))
    if missing:
        raise RuntimeError(f"openfoodtox_pilot_profiles.json is missing required identities: {sorted(missing)}")
    extra = set(data.get("profiles", {})) - set(_ALLOWLIST)
    if extra:
        raise RuntimeError(f"openfoodtox_pilot_profiles.json has non-allowlisted identities: {sorted(extra)}")
    return data


def _plan_update_existing(existing: Ingredient, profile: dict) -> IdentityPlan:
    plan = IdentityPlan(e_number=profile["e_number"], action="update", ingredient_id=existing.id)
    new_en = {
        "description": profile["description"]["en"],
        "purpose_in_food": profile["purpose_in_food"]["en"],
        "health_concerns": profile["health_concerns"]["en"],
        "effect_conditions": profile["effect_conditions"]["en"],
        "dietary_guidance": profile["dietary_guidance"]["en"],
    }
    for field_name, new_value in new_en.items():
        old_value = getattr(existing, field_name) or ""
        if new_value and new_value != old_value:
            plan.en_changes.append(FieldChange(field_name, old_value, new_value))
    new_references = profile["references"]
    if new_references and new_references != (existing.references or ""):
        plan.en_changes.append(FieldChange("references", existing.references or "", new_references))

    bg_row = next(
        (r for r in (existing.localization_rows or []) if r.language == "bg"), None
    )
    _plan_bg(plan, bg_row, profile)
    if not plan.en_changes and plan.bg_action in ("no_op", "skip_human_curated"):
        plan.action = "no_op"
    return plan


def _plan_create(profile: dict) -> IdentityPlan:
    display = profile["new_ingredient_display"]
    plan = IdentityPlan(e_number=profile["e_number"], action="create", ingredient_id=display["id"])
    plan.en_changes = [
        FieldChange("common_name", "", display["common_name"]["en"]),
        FieldChange("category", "", display["category"]["en"]),
        FieldChange("description", "", profile["description"]["en"]),
        FieldChange("purpose_in_food", "", profile["purpose_in_food"]["en"]),
        FieldChange("health_concerns", "", profile["health_concerns"]["en"]),
        FieldChange("effect_conditions", "", profile["effect_conditions"]["en"]),
        FieldChange("dietary_guidance", "", profile["dietary_guidance"]["en"]),
        FieldChange("references", "", profile["references"]),
    ]
    _plan_bg(plan, None, profile)
    plan.notes.append(
        "New ingredient: verification_status=LIMITED_DATA, risk_assessment_available=False, "
        "risk_level=SAFE placeholder -- never contributes to any product's Health Score."
    )
    return plan


def _plan_bg(plan: IdentityPlan, bg_row: IngredientLocalization | None, profile: dict) -> None:
    # Task defect 3 ("BG names are English"): the artifact carries an
    # explicit, owner-approved Bulgarian common name for all four
    # allowlisted identities -- recorded on the plan unconditionally so
    # `_apply_bg` can always assert it, rather than ever falling back to
    # guessing a translation from the canonical English `common_name`.
    plan.bg_common_name = (profile.get("common_name") or {}).get("bg", "")
    if bg_row is not None and bg_row.translation_source == IngredientTranslationSource.HUMAN_CURATED:
        plan.bg_action = "skip_human_curated"
        plan.notes.append(
            f"Existing BG localization for {plan.ingredient_id} is HUMAN_CURATED -- left untouched."
        )
        return
    new_bg = {
        "description": profile["description"]["bg"],
        "purpose_in_food": profile["purpose_in_food"]["bg"],
        "health_concerns": profile["health_concerns"]["bg"],
        "effect_conditions": profile["effect_conditions"]["bg"],
        "dietary_guidance": profile["dietary_guidance"]["bg"],
    }
    if bg_row is None:
        plan.bg_action = "create"
        plan.bg_changes = [FieldChange(k, "", v) for k, v in new_bg.items() if v]
        if plan.bg_common_name:
            plan.bg_changes.append(FieldChange("common_name", "", plan.bg_common_name))
        return
    changes = []
    if plan.bg_common_name and plan.bg_common_name != (bg_row.common_name or ""):
        changes.append(FieldChange("common_name", bg_row.common_name or "", plan.bg_common_name))
    for field_name, new_value in new_bg.items():
        old_value = getattr(bg_row, field_name) or ""
        if new_value and new_value != old_value:
            changes.append(FieldChange(field_name, old_value, new_value))
    plan.bg_changes = changes
    plan.bg_action = "replace" if changes else "no_op"


async def build_plan(session) -> list[IdentityPlan]:
    artifact = _load_artifact()
    plans = []
    for e_number in _ALLOWLIST:
        profile = artifact["profiles"][e_number]
        existing = await ingredient_repository.get_by_official_identifier(session, e_number=e_number)
        if existing is not None:
            plans.append(_plan_update_existing(existing, profile))
        elif "new_ingredient_display" in profile:
            plans.append(_plan_create(profile))
        else:
            raise RuntimeError(
                f"{e_number} does not exist and the artifact has no new_ingredient_display for it -- "
                "refusing to guess; add one explicitly if this identity is genuinely new."
            )
    return plans


def _record_field_provenance(ingredient: Ingredient, touched_fields: list[str], *, now: datetime) -> None:
    if not touched_fields:
        return
    provenance = _load_field_provenance(ingredient)
    entry = {"source": IngredientSource.CURATED_SEED.value, "confidence": _FIELD_CONFIDENCE, "retrievedAt": now.isoformat()}
    for field_name in touched_fields:
        provenance[field_name] = entry
    ingredient.field_provenance_json = _dump_field_provenance(provenance)


async def _apply_update(session, plan: IdentityPlan, now: datetime) -> None:
    existing = await ingredient_repository.get_by_id(session, plan.ingredient_id)
    if existing is None:
        raise RuntimeError(f"{plan.ingredient_id} disappeared between planning and apply")
    touched = []
    for change in plan.en_changes:
        setattr(existing, change.field, change.new)
        touched.append(change.field)
    _record_field_provenance(existing, touched, now=now)
    await _apply_bg(session, existing, plan, now=now)


async def _apply_create(session, plan: IdentityPlan, profile: dict, now: datetime) -> None:
    display = profile["new_ingredient_display"]
    ingredient = Ingredient(
        id=display["id"],
        common_name=display["common_name"]["en"],
        normalized_name="",
        scientific_name="",
        e_number=profile["e_number"],
        ins_number=profile["e_number"][1:].lower() or None,
        cas_number=None,
        category=display["category"]["en"],
        description=profile["description"]["en"],
        purpose_in_food=profile["purpose_in_food"]["en"],
        health_concerns=profile["health_concerns"]["en"],
        evidence_level="",
        countries_restricted_or_banned="",
        efsa_status="",
        fda_status="",
        who_iarc_classification=None,
        acceptable_daily_intake="",
        side_effects="",
        allergens="",
        references=profile["references"],
        effect_conditions=profile["effect_conditions"]["en"],
        dietary_guidance=profile["dietary_guidance"]["en"],
        risk_level=RiskLevel.SAFE,
        risk_assessment_available=False,
        verification_status=IngredientVerificationStatus.LIMITED_DATA,
        source=IngredientSource.CURATED_SEED,
        source_record_id=f"openfoodtox-app-pilot:{profile['e_number']}",
        source_url=None,
        retrieved_at=now,
        last_verified_at=None,
        confidence=_FIELD_CONFIDENCE,
        schema_version=1,
        is_gluten=None,
        is_lactose=None,
        is_vegan=None,
        is_vegetarian=None,
        is_halal=None,
        is_kosher=None,
        bad_for_diabetes=False,
        bad_for_hypertension=False,
        bad_for_kidney_disease=False,
        bad_for_gout=False,
        bad_for_pregnancy=False,
        bad_for_children=False,
        bad_for_high_cholesterol=False,
    )
    from app.services.ingredient_normalization import normalize_ingredient_name

    ingredient.normalized_name = normalize_ingredient_name(ingredient.common_name)
    inserted = await ingredient_repository.insert_new(session, ingredient)
    if inserted is None:
        raise RuntimeError(f"Could not insert new ingredient {display['id']!r} -- id already exists")
    await register_curated_alias(session, canonical=inserted, alias_text=inserted.common_name, language="en")
    await register_curated_alias(session, canonical=inserted, alias_text=profile["e_number"], language=None)
    await _apply_bg(session, inserted, plan, now=now)


async def _apply_bg(session, ingredient: Ingredient, plan: IdentityPlan, *, now: datetime) -> None:
    # Task defect 2 ("EN-only content update makes approved BG
    # disappear"): a `no_op` plan (no BG *text* changed) must still fall
    # through to refresh `source_content_hash`/metadata below -- the only
    # thing that may ever skip this entirely is a HUMAN_CURATED row this
    # import must never touch. Without this, an approved EN-only update
    # (this function IS reached for that case -- see `_apply_update`,
    # which always calls it once `en_changes` is non-empty) would leave
    # the BG row's hash pointing at the ingredient's PREVIOUS canonical
    # English content, and the next read would then (correctly, but
    # unintentionally) treat the still-approved, still-current BG prose
    # as stale and stop serving it.
    if plan.bg_action == "skip_human_curated":
        return
    bg_values = {
        "common_name": plan.bg_common_name or None,
        "description": next((c.new for c in plan.bg_changes if c.field == "description"), None),
        "purpose_in_food": next((c.new for c in plan.bg_changes if c.field == "purpose_in_food"), None),
        "health_concerns": next((c.new for c in plan.bg_changes if c.field == "health_concerns"), None),
        "effect_conditions": next((c.new for c in plan.bg_changes if c.field == "effect_conditions"), None),
        "dietary_guidance": next((c.new for c in plan.bg_changes if c.field == "dietary_guidance"), None),
    }
    existing_bg = await session.get(IngredientLocalization, (ingredient.id, "bg"))
    expected_hash = canonical_text_hash(ingredient)
    if existing_bg is None:
        existing_bg = IngredientLocalization(ingredient_id=ingredient.id, language="bg")
        session.add(existing_bg)
    for field_name, value in bg_values.items():
        if value is not None:
            setattr(existing_bg, field_name, value)
    existing_bg.translation_status = IngredientTranslationStatus.DRAFT
    existing_bg.translation_source = IngredientTranslationSource.MACHINE_TRANSLATED
    existing_bg.owner_approved_without_review = True
    existing_bg.source_content_hash = expected_hash
    existing_bg.reviewed_at = None
    existing_bg.schema_version = 1


async def apply_plan(session, plans: list[IdentityPlan]) -> None:
    artifact = _load_artifact()
    now = _utcnow()
    for plan in plans:
        if plan.action == "no_op":
            continue
        if plan.action == "update":
            await _apply_update(session, plan, now)
        elif plan.action == "create":
            await _apply_create(session, plan, artifact["profiles"][plan.e_number], now)
    await session.flush()


def format_plan(plans: list[IdentityPlan]) -> str:
    lines = ["OpenFoodTox app pilot import -- proposed changes", "=" * 50]
    for plan in plans:
        lines.append(f"\n{plan.e_number} ({plan.ingredient_id}): {plan.action}")
        for change in plan.en_changes:
            old_preview = (change.old[:60] + "...") if len(change.old) > 60 else change.old
            new_preview = (change.new[:60] + "...") if len(change.new) > 60 else change.new
            lines.append(f"  EN {change.field}: {old_preview!r} -> {new_preview!r}")
        lines.append(f"  BG: {plan.bg_action} ({len(plan.bg_changes)} field(s))")
        for note in plan.notes:
            lines.append(f"  NOTE: {note}")
    return "\n".join(lines)


async def run(*, apply: bool) -> int:
    async with AsyncSessionLocal() as session:
        plans = await build_plan(session)
        print(format_plan(plans))
        if not apply:
            print("\nDry run -- no changes written. Re-run with --apply to write and commit.")
            return 0
        try:
            await apply_plan(session, plans)
        except Exception:
            await session.rollback()
            logger.exception("openfoodtox_pilot_import_failed_rolled_back")
            raise
        await session.commit()
        print("\nApplied and committed.")
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write and commit (default: dry run only).")
    args = parser.parse_args(argv)
    return asyncio.run(run(apply=args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
