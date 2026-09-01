"""
Lightweight, deterministic, offline language detection for label OCR
text, scoped to exactly the two languages this app's API contract
treats as primary display languages (English, Bulgarian) plus a
generic "other" bucket for everything else.

Deliberately NOT a general-purpose language identifier and NOT merely a
script classifier: `barcode_text_safety.is_safe_script` already answers
the Latin-vs-Cyrillic-vs-other SCRIPT question, and script alone is not
a language -- German/French/Albanian are Latin script; Russian/
Ukrainian/Serbian are Cyrillic script; neither is English/Bulgarian.
This module adds one further, cheap-but-real signal on top of script:
function-word ("stopword") vocabulary overlap for the Latin case, and
alphabet-exclusion plus Bulgarian-specific vocabulary for the Cyrillic
case, so "some Latin text" is never conflated with "English text", and
"some Cyrillic text" is never conflated with "Bulgarian text".

No network call, no third-party model, no added dependency -- see
README "Language policy" for why (task requirement: no new paid
translation provider/API key, and every test in this area must run
with zero live network calls).

Not a general natural-language identifier: this heuristic is tuned to
fail toward "other" rather than a wrong "en"/"bg" -- a false "other"
(real English/Bulgarian text this heuristic doesn't recognize) only
costs one extra translation call; a false "en"/"bg" would silently
treat unrelated foreign text as verified English/Bulgarian content,
which is the failure mode the "positive evidence required" rule below
exists to prevent.
"""
import re

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

_CYRILLIC_START, _CYRILLIC_END = 0x0400, 0x04FF
_LATIN_EXTENDED_END = 0x024F

# Letters that exist in other Cyrillic-alphabet languages' standard
# orthography but NOT in the modern Bulgarian alphabet -- which has no
# ы, э, ё, і, ї, є, ґ, ў, none of the Serbian-specific letters, and (per
# the 1945 Bulgarian spelling reform) no soft sign ь either, unlike
# Russian/Ukrainian/Belarusian, which use it constantly. Their presence
# is direct evidence the text is Russian, Ukrainian, Belarusian or
# Serbian -- not Bulgarian -- independent of vocabulary.
_NON_BULGARIAN_CYRILLIC_LETTERS = set("ыэёьіїєґўјљњћђџ")

# Common Bulgarian function words and food-label vocabulary. Not
# exhaustive -- a real label only needs one or two hits to be
# confidently Bulgarian, since these words are extremely frequent in
# any Bulgarian sentence (unlike content words, which vary label to
# label).
_BULGARIAN_STOPWORDS = {
    "и", "на", "за", "със", "с", "от", "без", "или", "как", "не", "да", "може",
    "съдържа", "съдържат", "съставки", "продукт", "тегло", "нето", "грам", "грама",
    "захар", "сол", "вода", "мляко", "пшеница", "брашно", "яйце", "яйца",
    "масло", "мазнини", "въглехидрати", "белтъци", "енергийна",
    "стойност", "срок", "годност", "партида", "опаковано", "алергени",
}

# Common English function words and food-label vocabulary. Same
# rationale as above.
_ENGLISH_STOPWORDS = {
    "the", "and", "with", "from", "for", "contains", "ingredients",
    "may", "of", "in", "or", "not", "made", "product", "water",
    "sugar", "salt", "milk", "wheat", "flour", "egg", "eggs", "oil", "free",
    "net", "weight", "energy", "fat", "protein", "carbohydrate",
    "carbohydrates", "per", "serving", "allergen", "allergens", "best",
    "before", "use", "store", "keep",
}

_MIN_STOPWORD_HITS = 1


def _script_counts(text: str) -> tuple[int, int, int]:
    """(cyrillic_chars, latin_chars, other_chars) -- whitespace, digits
    and punctuation are not counted toward any bucket."""
    cyrillic = latin = other = 0
    for ch in text:
        if ch.isspace() or ch.isdigit() or not ch.isalpha():
            continue
        code = ord(ch)
        if _CYRILLIC_START <= code <= _CYRILLIC_END:
            cyrillic += 1
        elif code <= _LATIN_EXTENDED_END:
            latin += 1
        else:
            other += 1
    return cyrillic, latin, other


def detect_language(text: str | None) -> str:
    """
    Returns "en", "bg", "other", or "unknown" (no usable alphabetic
    content at all -- empty, or only digits/punctuation/E-numbers/%
    figures).
    """
    if not text or not text.strip():
        return "unknown"

    # Single letters (an "E" from an E-number like "E202", a lone unit
    # abbreviation) carry no language signal at all -- excluding them
    # keeps a purely numeric/E-number/percentage string "unknown"
    # (nothing to translate or prefer) rather than a false "other".
    words = [w.lower() for w in _WORD_RE.findall(text) if len(w) >= 2]
    if not words:
        return "unknown"

    cyrillic, latin, other = _script_counts(text)
    if cyrillic == 0 and latin == 0 and other == 0:
        return "unknown"

    if cyrillic >= latin and cyrillic >= other:
        lowered = text.lower()
        if any(ch in _NON_BULGARIAN_CYRILLIC_LETTERS for ch in lowered):
            return "other"
        hits = sum(1 for w in words if w in _BULGARIAN_STOPWORDS)
        return "bg" if hits >= _MIN_STOPWORD_HITS else "other"

    if latin >= cyrillic and latin >= other:
        hits = sum(1 for w in words if w in _ENGLISH_STOPWORDS)
        return "en" if hits >= _MIN_STOPWORD_HITS else "other"

    return "other"
