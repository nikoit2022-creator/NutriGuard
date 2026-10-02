"""Safe construction of additive EN/BG ingredient display profiles."""

import hashlib
import json
from typing import Any

from app.models.enums import IngredientTranslationStatus


LOCALIZED_FIELDS = (
    "common_name",
    "category",
    "description",
    "purpose_in_food",
    "health_concerns",
    "evidence_level",
    "countries_restricted_or_banned",
    "efsa_status",
    "fda_status",
    "acceptable_daily_intake",
    "side_effects",
    "allergens",
    "effect_conditions",
    "dietary_guidance",
)


def canonical_text_payload(ingredient: Any) -> dict[str, str]:
    return {field: str(getattr(ingredient, field, "") or "") for field in LOCALIZED_FIELDS}


def canonical_text_hash(ingredient: Any) -> str:
    encoded = json.dumps(
        canonical_text_payload(ingredient), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _wire_profile(source: Any) -> dict[str, Any]:
    profile: dict[str, Any] = {
        "commonName": str(getattr(source, "common_name", "") or ""),
        "category": str(getattr(source, "category", "") or ""),
        "description": str(getattr(source, "description", "") or ""),
        "purposeInFood": str(getattr(source, "purpose_in_food", "") or ""),
        "healthConcerns": str(getattr(source, "health_concerns", "") or ""),
        "evidenceLevel": str(getattr(source, "evidence_level", "") or ""),
        "countriesRestrictedOrBanned": str(getattr(source, "countries_restricted_or_banned", "") or ""),
        "efsaStatus": str(getattr(source, "efsa_status", "") or ""),
        "fdaStatus": str(getattr(source, "fda_status", "") or ""),
        "acceptableDailyIntake": str(getattr(source, "acceptable_daily_intake", "") or ""),
        "sideEffects": str(getattr(source, "side_effects", "") or ""),
        "allergens": str(getattr(source, "allergens", "") or ""),
        "effectConditions": str(getattr(source, "effect_conditions", "") or ""),
        "dietaryGuidance": str(getattr(source, "dietary_guidance", "") or ""),
        "riskRationale": str(getattr(source, "evidence_level", "") or ""),
    }
    status = getattr(source, "translation_status", None)
    translation_source = getattr(source, "translation_source", None)
    profile["translationStatus"] = status.value if hasattr(status, "value") else status
    profile["translationSource"] = (
        translation_source.value if hasattr(translation_source, "value") else translation_source
    )
    profile["ownerApprovedWithoutReview"] = bool(getattr(source, "owner_approved_without_review", False))
    return profile


def _is_servable(row: Any, *, expected_hash: str) -> bool:
    """A localization row may be served when EITHER it has gone through
    real translation review (`REVIEWED` + current hash, the original,
    unchanged rule), OR an owner has explicitly approved this specific
    DRAFT content for display without that review (docs/
    OPENFOODTOX_APP_PILOT_INTEGRATION_TASK.md's narrowly scoped
    owner-approved-publication mechanism). The hash check still applies
    in both branches -- an owner's approval covers the content as it
    was when approved, never a later, unreviewed edit to the English
    source. The flag never widens to cover an ordinary, unapproved
    DRAFT row (every existing/other writer leaves it `False`), so this
    is strictly additive, not a loosening of the REVIEWED gate itself."""
    if getattr(row, "source_content_hash", None) != expected_hash:
        return False
    status = getattr(row, "translation_status", None)
    status_value = status.value if hasattr(status, "value") else status
    if status_value == IngredientTranslationStatus.REVIEWED.value:
        return True
    return status_value == IngredientTranslationStatus.DRAFT.value and bool(
        getattr(row, "owner_approved_without_review", False)
    )


def build_localizations(ingredient: Any) -> dict[str, dict[str, Any]]:
    """Return canonical English plus only current, servable Bulgarian.

    Drafts and translations of an older canonical source are omitted,
    causing the Android client to fall back to the unchanged English
    fields instead of showing stale or unreviewed scientific prose --
    unless an owner has explicitly approved that specific DRAFT content
    for display (see `_is_servable`).
    """
    result = {"en": _wire_profile(ingredient)}
    expected_hash = canonical_text_hash(ingredient)
    # SQLAlchemy Ingredient exposes this safe view so response building
    # never lazily queries an async relationship from synchronous code.
    # Plain test/adaptor objects keep using ``localization_rows``.
    rows = getattr(ingredient, "loaded_localization_rows", None)
    if rows is None:
        rows = getattr(ingredient, "localization_rows", ())
    for row in rows or ():
        if getattr(row, "language", None) == "bg" and _is_servable(row, expected_hash=expected_hash):
            result["bg"] = _wire_profile(row)
            break
    return result
