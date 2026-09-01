"""
Lightweight, deterministic, offline language detection for label OCR
text, scoped to exactly the two languages this app's API contract
treats as primary display languages (English, Bulgarian) plus a
generic "other" bucket for everything else.

Deliberately NOT a general-purpose language identifier and NOT merely a
script classifier: `barcode_text_safety.is_safe_script` already answers
the Latin-vs-Cyrillic-vs-other SCRIPT question, and script alone is not
a language -- German/French/Italian/Spanish/Romanian are Latin script;
Russian/Ukrainian/Serbian are Cyrillic script; neither is English/
Bulgarian. This module adds two further signals on top of script:

  1. Alphabet exclusion (Cyrillic case only): a letter that exists in
     another Cyrillic-alphabet language's standard orthography but not
     in modern Bulgarian's is direct, independent-of-vocabulary evidence
     the text isn't Bulgarian.
  2. Lexical evidence, tiered by how ambiguous a word is across related
     languages: a small set of STRONG words (distinctive food-label
     vocabulary very unlikely to coincide with a neighboring language)
     is enough on its own; anything else is a WEAK word (a short
     function word like "in"/"or"/"per"/"и"/"на", which frequently
     coincides across unrelated languages purely by chance) and
     requires at least two DISTINCT weak hits together before they're
     trusted -- a single ambiguous token must never tip the
     classification by itself.

No network call, no third-party model, no added dependency -- see
README "Language policy" for why (task requirement: no new paid
translation provider/API key, and every test in this area must run
with zero live network calls).

Not a general natural-language identifier: this heuristic is tuned to
fail toward "other" rather than a wrong "en"/"bg" -- a false "other"
(real English/Bulgarian text this heuristic doesn't recognize) only
costs one extra translation call; a false "en"/"bg" would silently
treat unrelated foreign text as verified English/Bulgarian content,
which is the failure mode the "positive evidence required" rule above
exists to prevent.
"""
import re

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

_CYRILLIC_START, _CYRILLIC_END = 0x0400, 0x04FF
_LATIN_EXTENDED_END = 0x024F

# Letters that exist in other Cyrillic-alphabet languages' standard
# orthography but NOT in the modern Bulgarian alphabet: Bulgarian has
# no ы, э, ё, і, ї, є, ґ, ў, and none of the Serbian-specific letters
# (ј, љ, њ, ћ, ђ, џ). Their presence is direct evidence the text is
# Russian, Ukrainian, Belarusian or Serbian -- not Bulgarian --
# independent of vocabulary.
#
# NOTE: ь (soft sign) is deliberately NOT in this set. It IS a valid
# modern Bulgarian letter (e.g. "шофьор" [chauffeur], "каньон"
# [canyon], "монтьор" [mechanic]) -- far less frequent than in Russian,
# but its mere presence must never by itself disqualify Bulgarian.
_NON_BULGARIAN_CYRILLIC_LETTERS = set("ыэёіїєґўјљњћђџ")

# STRONG words: distinctive enough (exact spelling, food-label-specific)
# that a single hit is trusted on its own -- picked to be very unlikely
# to coincide with German/French/Italian/Spanish/Romanian/Russian/
# Ukrainian text by chance.
_ENGLISH_STRONG_WORDS = {"ingredients", "contains", "allergen", "allergens", "manufactured"}
_BULGARIAN_STRONG_WORDS = {
    "захар", "съставки", "съдържа", "съдържат", "пшеница",
    "въглехидрати", "белтъци", "годност", "алергени",
}

# WEAK words: common function words / generic label vocabulary that
# frequently coincides with a neighboring language purely by chance
# (Bulgarian "и"/"на"/"за" are shared Slavic function words). A single
# weak hit proves nothing; at least two DISTINCT weak hits are required
# before they're trusted as evidence (see `_has_sufficient_evidence`).
#
# Deliberately EXCLUDES short, cross-language-collision-prone
# prepositions like "in" (also German/Dutch), "or" (also French "gold"),
# "per" (also Italian "for"), and "of" -- even several such tokens
# together must never be read as English evidence; see the module
# docstring and the "single ambiguous token" review finding.
_ENGLISH_WEAK_WORDS = {
    "the", "and", "with", "from", "for", "may", "not",
    "made", "product", "water", "sugar", "salt", "milk", "wheat", "flour",
    "egg", "eggs", "oil", "free", "net", "weight", "energy", "fat",
    "protein", "carbohydrate", "carbohydrates", "serving", "best",
    "before", "use", "store", "keep",
}
_BULGARIAN_WEAK_WORDS = {
    "и", "на", "за", "със", "с", "от", "без", "или", "как", "не", "да", "може",
    "продукт", "тегло", "нето", "грам", "грама", "сол", "вода", "мляко",
    "брашно", "яйце", "яйца", "масло", "мазнини", "енергийна",
    "стойност", "срок", "партида", "опаковано",
}

_MIN_WEAK_HITS = 2


def _has_sufficient_evidence(words: list[str], strong: set[str], weak: set[str]) -> bool:
    distinct = set(words)
    if distinct & strong:
        return True
    return len(distinct & weak) >= _MIN_WEAK_HITS


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
        if _has_sufficient_evidence(words, _BULGARIAN_STRONG_WORDS, _BULGARIAN_WEAK_WORDS):
            return "bg"
        return "other"

    if latin >= cyrillic and latin >= other:
        if _has_sufficient_evidence(words, _ENGLISH_STRONG_WORDS, _ENGLISH_WEAK_WORDS):
            return "en"
        return "other"

    return "other"
