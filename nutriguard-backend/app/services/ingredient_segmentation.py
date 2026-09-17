"""
Suspected-OCR-concatenation / ambiguous-segmentation detection for one
already-tokenized ingredient-list entry (see
`app.services.ocr_normalizer.normalize_and_extract_tokens`).

Real EU-style multi-language labels visually emphasize allergens, often
in ALL CAPS, inline with an otherwise normal-case clause (Regulation
(EU) 1169/2011) -- e.g. "agenti de crestere: enzime (SECARA)". Because
tokenization only splits on `,`/`;`/`.` (never `:`), a clause like that
can survive as ONE token, and because `label_language`'s Gemini
translation prompt is explicitly told not to translate proper nouns, an
ALL-CAPS allergen word can survive translation untranslated too. Either
way, a token shaped like this is NOT a reliable single ingredient name.

Task requirement: "Treat suspected OCR concatenation or ambiguous
segmentation as unresolved; translation alone must not certify
identity." This module never tries to re-segment or repair such a
token -- it only decides whether one should be trusted at all. Callers
(`app.services.ingredient_catalog.materialize_ingredients`) must skip
translation entirely for a flagged token and mark the resulting
ingredient `identity_uncertain=True` instead, so the client can offer
another label photo (see `app.schemas.ingredient.IngredientOut.identity_uncertain`).

Deliberately conservative -- biased toward NOT flagging, matching this
codebase's existing "fail toward less certain, not toward a wrong
positive" style (see `app.services.language_detection`'s own module
docstring). A false negative here (a genuinely ambiguous token that
slips through) just gets translated as usual, no worse than before this
module existed; a false positive would block a perfectly good
ingredient's identity resolution for no reason.
"""
import re

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

# A "word" of 3+ letters, entirely uppercase, that still has at least
# one lowercase letter's counterpart elsewhere in the SAME token --
# i.e. the token is not simply "the whole thing is shouting" (a label
# whose entire ingredient list is capitalized is not ambiguous; only a
# capitalization SWITCH within one token is the EU-allergen-emphasis
# signal this module looks for).
_ALL_CAPS_WORD_RE = re.compile(r"\b[^\Wa-z\d_]{3,}\b", re.UNICODE)

# A token this long, with no sentence punctuation left to have split it
# (tokenization already ran), is more likely a mis-split multi-clause
# fragment than one real ingredient name.
_LONG_TOKEN_WORD_COUNT = 7


def _words(token: str) -> list[str]:
    return _WORD_RE.findall(token)


def _has_embedded_allergen_emphasis(token: str, words: list[str]) -> bool:
    if len(words) < 2:
        return False
    if not any(w.islower() for w in words):
        # The whole token is uppercase -- a capitalization convention,
        # not a switch within the token. Nothing to flag here.
        return False
    return bool(_ALL_CAPS_WORD_RE.search(token))


def _has_duplicate_word(words: list[str]) -> bool:
    """A significant word (4+ letters, to skip short connector words
    like "de"/"din"/"cu"/"and"/"or") repeated within the same token --
    the "LAPTE proteina din LAPTE" ("MILK protein from MILK") shape,
    where two adjacent list clauses were merged around a shared
    allergen word."""
    seen: set[str] = set()
    for w in words:
        lowered = w.lower()
        if len(lowered) < 4:
            continue
        if lowered in seen:
            return True
        seen.add(lowered)
    return False


def detect_ambiguous_segmentation(token: str) -> str | None:
    """Returns a short, stable reason code if `token` looks like a
    merged/mis-segmented fragment rather than one reliable ingredient
    name, else `None`. Pure and deterministic -- never touches the
    network, never consults a language model.
    """
    if not token or not token.strip():
        return None
    words = _words(token)
    if not words:
        return None

    if _has_embedded_allergen_emphasis(token, words):
        return "EMBEDDED_ALLERGEN_EMPHASIS_MERGE"

    if _has_duplicate_word(words):
        return "DUPLICATE_TOKEN_FRAGMENT"

    if len(words) >= _LONG_TOKEN_WORD_COUNT:
        return "LONG_UNSEGMENTED_CLAUSE"

    return None
