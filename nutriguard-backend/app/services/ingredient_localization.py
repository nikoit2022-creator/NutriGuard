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
        "riskRationale": str(getattr(source, "evidence_level", "") or ""),
    }
    status = getattr(source, "translation_status", None)
    translation_source = getattr(source, "translation_source", None)
    profile["translationStatus"] = status.value if hasattr(status, "value") else status
    profile["translationSource"] = (
        translation_source.value if hasattr(translation_source, "value") else translation_source
    )
    return profile


def build_localizations(ingredient: Any) -> dict[str, dict[str, Any]]:
    """Return canonical English plus only current, reviewed Bulgarian.

    Drafts and translations of an older canonical source are omitted,
    causing the Android client to fall back to the unchanged English
    fields instead of showing stale or unreviewed scientific prose.
    """
    result = {"en": _wire_profile(ingredient)}
    expected_hash = canonical_text_hash(ingredient)
    for row in getattr(ingredient, "localization_rows", ()) or ():
        status = getattr(row, "translation_status", None)
        status_value = status.value if hasattr(status, "value") else status
        if (
            getattr(row, "language", None) == "bg"
            and status_value == IngredientTranslationStatus.REVIEWED.value
            and getattr(row, "source_content_hash", None) == expected_hash
        ):
            result["bg"] = _wire_profile(row)
            break
    return result
