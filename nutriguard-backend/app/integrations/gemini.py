"""
GeminiService: the ONLY place in the backend that talks to Google's
Gemini API. The API key never leaves the server and is never returned
to the client (API Contract section 3.4).

This is deliberately a thin, swappable abstraction so a different AI
provider could replace Gemini later without touching callers.
"""
import base64

import httpx
import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)


class GeminiUnavailableError(Exception):
    """Raised for any Gemini failure: network error, timeout, non-2xx,
    or an unparsable/empty response. Callers MUST catch this and fall
    back to the deterministic local analysis (API Contract 7.4) rather
    than propagating it to the client."""


_LANGUAGE_RULES = """
Language rules (strict -- the output is shown directly to end users, do not deviate):
- Every text value you return (productName, brand names ARE the one exception -- see below,
  ingredient commonName, scientificName, category, description, and every other string field)
  must be written in English or Bulgarian ONLY. No other language may appear anywhere in the output.
- If the source label is in English: return the English text as-is.
- If the source label is in Bulgarian: return the correct Bulgarian text as-is; you do not need
  to translate it to English.
- If the source label is in ANY OTHER language (German, French, Albanian, or anything else):
  translate every product/ingredient name and description to English before returning it.
  Never return the original non-English/non-Bulgarian text for these fields.
- Brand names are the one exception: NEVER translate a brand name. Return it exactly as printed
  on the label, in its original language/spelling.
- For every ingredient, ALWAYS also provide its canonical English common name (the standard
  English scientific/food name used in ingredient databases, e.g. "Aspartame", "Sodium Benzoate")
  in `commonName`, even if the label itself is in Bulgarian or another language -- this is required
  for matching against an English-language scientific database, regardless of what the source
  label's original text said.
- ALWAYS preserve the E-number (e.g. "E951") in `eNumber` whenever the label shows or implies one,
  even if you also translated the ingredient's name.
- Never invent a value. If a field is genuinely unknown, use JSON null (for optional fields) --
  never the literal text "null", "None", "N/A", or any other placeholder string.
"""

_TEXT_PROMPT_TEMPLATE = """
You are a scientific food database parser. Analyze the following food ingredient text and return a JSON object ONLY with no markdown formatting.
Text: "{raw_text}"
""" + _LANGUAGE_RULES + """
Required format:
{{
  "productName": "Estimated Product Name",
  "brand": "Brand or Generic",
  "sugarGrams": 0.0,
  "sodiumMg": 100.0,
  "saturatedFatGrams": 0.0,
  "hasArtificialSweeteners": false,
  "hasPreservatives": false,
  "isGlutenFree": true,
  "isLactoseFree": true,
  "isVegan": true,
  "isVegetarian": true,
  "isHalal": true,
  "isKosher": true,
  "novaGroup": 3,
  "ingredients": [
    {{
      "commonName": "Aspartame",
      "scientificName": "L-alpha-aspartyl-L-phenylalanine methyl ester",
      "eNumber": "E951",
      "category": "Artificial Sweetener",
      "description": "High-intensity artificial sweetener",
      "purposeInFood": "Sweetener",
      "healthConcerns": "IARC 2B possibly carcinogenic",
      "evidenceLevel": "Moderate Evidence",
      "countriesRestrictedOrBanned": "PKU warnings required",
      "efsaStatus": "Authorized",
      "fdaStatus": "Approved",
      "whoIarcClassification": "Group 2B",
      "acceptableDailyIntake": "40 mg/kg",
      "sideEffects": "Headaches in sensitive individuals",
      "allergens": "Phenylalanine",
      "riskLevel": "HIGH_CONCERN",
      "badForDiabetes": true,
      "badForHypertension": false,
      "badForPregnancy": true,
      "badForChildren": true
    }}
  ]
}}
""".strip()

_IMAGE_PROMPT = (
    "Extract all ingredients from this food label image and analyze them scientifically. "
    "Return JSON with keys: productName, brand, sugarGrams, sodiumMg, saturatedFatGrams, "
    "hasArtificialSweeteners, hasPreservatives, isGlutenFree, isLactoseFree, isVegan, "
    "isVegetarian, isHalal, isKosher, novaGroup, rawIngredientText, ingredients array "
    "(each with commonName, scientificName, eNumber, category, description, etc.).\n"
    + _LANGUAGE_RULES
)


class GeminiService:
    def __init__(self) -> None:
        self._api_key = settings.GEMINI_API_KEY
        self._base_url = settings.GEMINI_BASE_URL
        self._model = settings.GEMINI_MODEL
        self._timeout = settings.GEMINI_TIMEOUT_SECONDS

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key) and self._api_key != "MY_GEMINI_API_KEY"

    def _endpoint(self) -> str:
        return f"{self._base_url}/models/{self._model}:generateContent?key={self._api_key}"

    async def _call(self, parts: list[dict]) -> str:
        if not self.is_configured:
            raise GeminiUnavailableError("Gemini API key is not configured on the server.")

        payload = {"contents": [{"parts": parts}]}

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(self._endpoint(), json=payload)
        except httpx.HTTPError as exc:
            logger.warning("gemini_call_failed", reason=str(exc))
            raise GeminiUnavailableError("Network error calling Gemini API.") from exc

        if response.status_code != 200:
            logger.warning("gemini_non_200", status_code=response.status_code)
            raise GeminiUnavailableError(f"Gemini API returned status {response.status_code}.")

        try:
            body = response.json()
            candidates = body.get("candidates") or []
            if not candidates:
                raise ValueError("no candidates")
            parts_out = candidates[0].get("content", {}).get("parts") or []
            if not parts_out:
                raise ValueError("no parts")
            text = parts_out[0].get("text", "")
            if not text.strip():
                raise ValueError("empty text")
        except (ValueError, KeyError, IndexError, AttributeError) as exc:
            logger.warning("gemini_unparsable_response")
            raise GeminiUnavailableError("Gemini API response could not be parsed.") from exc

        return text.replace("```json", "").replace("```", "").strip()

    async def analyze_text(self, raw_ingredient_text: str) -> str:
        prompt = _TEXT_PROMPT_TEMPLATE.format(raw_text=raw_ingredient_text)
        return await self._call([{"text": prompt}])

    async def analyze_image(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        b64 = base64.b64encode(image_bytes).decode("ascii")
        parts = [
            {"text": _IMAGE_PROMPT},
            {"inlineData": {"mimeType": mime_type, "data": b64}},
        ]
        return await self._call(parts)


gemini_service = GeminiService()
