"""
Deterministic port of `GeminiAnalysisEngine.parseGeminiJsonResult`.

PRESERVED QUIRK (documented, not "fixed" — see API Contract "Critical
Rule" and README "Deviations"): the original Kotlin implementation
extracts `productName`, `brand`, `sugarGrams`, `sodiumMg`,
`saturatedFatGrams` and `novaGroup` from the Gemini JSON via regex, but
then calls `fallbackLocalAnalysis(pName, originalRawText, dbIngredients)`
— i.e. it only ever uses the extracted `productName`. The AI-provided
`brand`, nutrition numbers, NOVA group and the full `ingredients` array
are parsed and then discarded; the actual nutrition figures and matched
ingredients still come from the deterministic keyword-heuristic fallback
run against the ORIGINAL raw text, not the AI's structured output.

This backend reproduces that exact behavior for parity with the
existing app. If the product team wants Gemini's structured output to
actually be used, that is a product decision that should update the
API Contract explicitly (see the "Deviations" section of the README) —
this backend does not silently change it.
"""
import re
from typing import Any

from app.services.fallback_analysis import AnalyzedProductData, fallback_local_analysis

_PRODUCT_NAME_RE = re.compile(r'"productName":\s*"(.*?)"')


def parse_gemini_json_result(
    json_string: str, original_raw_text: str, db_ingredients: list[Any]
) -> tuple[AnalyzedProductData, list[Any]]:
    try:
        match = _PRODUCT_NAME_RE.search(json_string)
        p_name = match.group(1) if match else "Scanned Product"
        return fallback_local_analysis(p_name, original_raw_text, db_ingredients)
    except Exception:  # noqa: BLE001 - mirrors the Kotlin catch-all
        return fallback_local_analysis("Analyzed Product", original_raw_text, db_ingredients)
