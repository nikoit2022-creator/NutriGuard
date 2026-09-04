"""
Deterministic port of `com.example.service.ocr.OcrNormalizer`.

Tokenization rules, matching heuristics, and synthetic-ingredient risk
heuristics are copied verbatim from the Kotlin source (API Contract
section 7.3).
"""
import hashlib
import re
from dataclasses import dataclass, replace
from typing import Any

from app.models.enums import RiskLevel

_BRACKET_OR_PERCENT = re.compile(r"\[.*?\]|\(.*?%\)")
_NON_WORD_EDGES = re.compile(r"^\W+|\W+$")
_E_NUMBER = re.compile(r"e[- ]?(\d{3,4}[a-z]?)", re.IGNORECASE)


@dataclass(frozen=True)
class NormalizedIngredientResult:
    matched_ingredients: list[Any]
    unknown_ingredients: list[str]
    raw_tokens: list[str]


def normalize_and_extract_tokens(raw_text: str) -> list[str]:
    cleaned = _BRACKET_OR_PERCENT.sub("", raw_text)
    cleaned = cleaned.replace("\n", " ")
    cleaned = re.sub(re.escape("Ingredients:"), "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(re.escape("CONTAINS:"), "", cleaned, flags=re.IGNORECASE)

    tokens = re.split(r"[,;.]", cleaned)
    result = []
    for token in tokens:
        t = token.strip()
        t = _NON_WORD_EDGES.sub("", t)
        if len(t) > 1:
            result.append(t)
    return result


def match_against_database(tokens: list[str], db_ingredients: list[Any]) -> NormalizedIngredientResult:
    matched: list[Any] = []
    unknown: list[str] = []

    for token in tokens:
        lower_token = token.lower()
        found = None

        e_match = _E_NUMBER.search(lower_token)
        if e_match:
            formatted_e = "E" + e_match.group(1).upper()
            found = next(
                (ing for ing in db_ingredients if (ing.e_number or "").upper() == formatted_e.upper()),
                None,
            )

        if found is None:
            for ing in db_ingredients:
                common = ing.common_name.lower()
                sci = (ing.scientific_name or "").lower()
                if (
                    (common and (lower_token in common or common in lower_token))
                    or (sci and lower_token in sci)
                ):
                    found = ing
                    break

        if found is not None:
            if found not in matched:
                matched.append(found)
        else:
            if token and token not in unknown:
                unknown.append(token)

    return NormalizedIngredientResult(matched_ingredients=matched, unknown_ingredients=unknown, raw_tokens=tokens)


@dataclass
class SyntheticIngredient:
    """Plain in-memory stand-in for an IngredientEntity that has no
    scientific-database match. It is built ONLY from what the OCR text
    itself genuinely provides (the raw name, and an E-number when the
    text literally contains one) -- OCR is provenance, not scientific
    evidence, so every field that would require verified scientific/
    regulatory data (description, purpose, health concerns, evidence
    level, EFSA/FDA status, ADI, countries, references, risk level) is
    left honestly empty/neutral rather than filled with a fabricated
    generic placeholder. See `create_synthetic_ingredient`."""

    id: str
    common_name: str
    scientific_name: str
    e_number: str | None
    category: str
    description: str
    purpose_in_food: str
    health_concerns: str
    evidence_level: str
    countries_restricted_or_banned: str
    efsa_status: str
    fda_status: str
    who_iarc_classification: str | None
    acceptable_daily_intake: str
    side_effects: str
    allergens: str
    references: str
    risk_level: RiskLevel
    # Always False here: no scientific-database match means no real
    # assessment was ever made. `risk_level` above is a safe, neutral
    # placeholder (SAFE) -- never a keyword guess -- and callers must
    # gate on this flag rather than trusting it directly (see
    # `IngredientOut.risk_assessment_available` and
    # `food_analysis._score_and_warnings`, which excludes any
    # ingredient with this flag False from the Health Score).
    risk_assessment_available: bool = False
    is_gluten: bool = False
    is_lactose: bool = False
    is_vegan: bool = True
    is_vegetarian: bool = True
    is_halal: bool = True
    is_kosher: bool = True
    bad_for_diabetes: bool = False
    bad_for_hypertension: bool = False
    bad_for_kidney_disease: bool = False
    bad_for_gout: bool = False
    bad_for_pregnancy: bool = False
    bad_for_children: bool = False
    bad_for_high_cholesterol: bool = False


def create_synthetic_ingredient(name: str) -> SyntheticIngredient:
    """Build a placeholder ingredient for an OCR token that has no
    scientific-database match.

    Deliberately honest, not "helpful": OCR only ever tells us the raw
    label text (and, when literally present, an E-number) -- it is
    provenance, not scientific evidence. Every field that would require
    real curated/verified data (description, purpose, health concerns,
    evidence level, EFSA/FDA status, ADI, countries, references, and
    the risk assessment itself) is left honestly empty/neutral rather
    than filled with a fabricated generic string. `risk_level` is
    always the safe, neutral default (never inferred from a keyword in
    the OCR name) and `risk_assessment_available=False` tells callers
    (Health Score, API clients) that it is not a real assessment.
    """
    lower = name.lower()
    e_match = _E_NUMBER.search(lower)
    formatted_e = ("E" + e_match.group(1).upper()) if e_match else None

    slug = re.sub(r"[^a-z0-9_]", "", name.lower().replace(" ", "_"))
    if not slug:
        # A name with no ASCII-alphanumeric characters at all (e.g. a
        # Cyrillic-only Bulgarian ingredient word that didn't match the
        # scientific database -- see `app.services.label_language`)
        # would otherwise collapse every such token to the same empty
        # slug ("synth_"), silently colliding distinct ingredients into
        # one id. Fall back to a short, stable content hash instead, so
        # each distinct non-Latin name still gets its own id.
        slug = hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]

    return SyntheticIngredient(
        id=f"synth_{slug}",
        common_name=name[:1].upper() + name[1:] if name else name,
        scientific_name=formatted_e or "",
        e_number=formatted_e,
        category=f"Food Additive ({formatted_e})" if formatted_e else "Ingredient",
        description="",
        purpose_in_food="",
        health_concerns="",
        evidence_level="",
        countries_restricted_or_banned="",
        efsa_status="",
        fda_status="",
        who_iarc_classification=None,
        acceptable_daily_intake="",
        side_effects="",
        allergens=(
            "Potential Allergen"
            if any(kw in lower for kw in ("milk", "whey", "soy", "wheat", "peanut"))
            else "None"
        ),
        references="",
        risk_level=RiskLevel.SAFE,
        risk_assessment_available=False,
        bad_for_diabetes=any(kw in lower for kw in ("sugar", "syrup", "dextrose")),
        bad_for_hypertension=any(kw in lower for kw in ("sodium", "salt", "msg")),
        bad_for_high_cholesterol=any(kw in lower for kw in ("palm", "fat", "hydrogenated")),
    )


def reconstruct_synthetic_ingredient(ingredient_id: str, raw_text: str) -> SyntheticIngredient:
    """Recover a synthetic ingredient's human label from persisted OCR text.

    Products historically stored only synthetic IDs. Rebuilding directly
    from that ID exposed implementation strings such as ``Synth_5ecbec8146``
    to users and permanently lost Cyrillic names. Recreate each raw token and
    match its deterministic ID first; use a readable legacy fallback only
    when the original token is genuinely unavailable.
    """
    for token in normalize_and_extract_tokens(raw_text or ""):
        candidate = create_synthetic_ingredient(token)
        if candidate.id == ingredient_id:
            return candidate

    slug = ingredient_id.removeprefix("synth_")
    if slug and not re.fullmatch(r"[0-9a-f]{10}", slug):
        readable = re.sub(r"_+", " ", slug).strip()
        if readable:
            return replace(create_synthetic_ingredient(readable), id=ingredient_id)

    return replace(create_synthetic_ingredient("Ingredient detected on label"), id=ingredient_id)
