"""
Internal, closed-vocabulary reasons for why a translation was NOT
accepted (see `app.services.ingredient_translation` and
`app.services.label_language`), plus the coarse provider-failure
sub-categories behind "provider unavailable".

INTERNAL-ONLY, diagnostics-only: these values feed the repair tool's
dry-run report and the scan-diagnostics journal as small counters. They
are never persisted on a model, never part of a public response schema
or error message, and never change WHICH translation is accepted -- the
public "translation could not be verified" signal is still exactly
`identity_uncertain` / `uncertainty_reason="TRANSLATION_UNRELIABLE"`.
Both enums are closed sets of camelCase strings (the wire style already
used by every diagnostics JSON key), so a value can be emitted verbatim
without ever carrying ingredient/OCR text, a model response, an
exception message or a secret.
"""
from collections.abc import Iterable, Mapping
from enum import Enum


class TranslationRejection(str, Enum):
    """Why one translation was rejected. Each member maps to exactly one
    check/failure point; see `ingredient_translation._first_rejection`
    (per-entry checks, in their fixed evaluation order) and
    `ingredient_translation.translate_ingredient_tokens` (whole-call and
    matching failures)."""

    # `GeminiUnavailableError` -- provider not configured / transport /
    # HTTP failure / provider response not parsable at the HTTP layer.
    # The finer cause is `ProviderFailureCategory`.
    PROVIDER_UNAVAILABLE = "providerUnavailable"
    # The model's reply was not JSON, not a JSON list, or THIS target's
    # entry was present but failed the per-entry schema (missing/extra
    # field, out-of-range or non-finite confidence, empty text, ...).
    MALFORMED_RESPONSE = "malformedResponse"
    # A well-formed reply that simply contains no entry for this target.
    NO_MATCHING_ENTRY = "noMatchingEntry"
    # Model-reported confidence below the minimum (or not finite).
    LOW_CONFIDENCE = "lowConfidence"
    # Translated text is empty or a placeholder ("N/A", "null", ...).
    EMPTY_TRANSLATION = "emptyTranslation"
    # Translated text is neither detected as English nor a known,
    # English-tagged catalog name.
    LANGUAGE_REJECTED = "languageRejected"
    # The multiset of E-numbers differs between source and translation.
    E_NUMBER_MISMATCH = "eNumberMismatch"
    # The multiset of numbers/units/percentages differs.
    NUMERIC_MISMATCH = "numericMismatch"
    # An unreliable result whose producer reported no reason (only an
    # injected/legacy translator can do this; never produced by
    # `translate_ingredient_tokens`).
    UNSPECIFIED = "unspecified"


class ProviderFailureCategory(str, Enum):
    """Coarse, non-sensitive sub-category of
    `TranslationRejection.PROVIDER_UNAVAILABLE` (values mirror the
    `FAILURE_*` constants in `app.integrations.gemini`). A category label
    only -- never a message, status text, URL or key."""

    NOT_CONFIGURED = "notConfigured"
    TIMEOUT = "timeout"
    TRANSPORT = "transport"
    HTTP_AUTH = "httpAuth"
    HTTP_RATE_LIMITED = "httpRateLimited"
    HTTP_CLIENT_ERROR = "httpClientError"
    HTTP_SERVER_ERROR = "httpServerError"
    HTTP_OTHER = "httpOther"
    UNPARSABLE_RESPONSE = "unparsableResponse"
    UNKNOWN = "unknown"


REJECTION_KEYS: tuple[str, ...] = tuple(member.value for member in TranslationRejection)
PROVIDER_FAILURE_KEYS: tuple[str, ...] = tuple(member.value for member in ProviderFailureCategory)


def provider_failure_category(exc: BaseException) -> ProviderFailureCategory:
    """Maps a `GeminiUnavailableError`'s own `category` label into the
    closed enum. Reads ONLY that label -- never `str(exc)`/its args --
    and any missing/unrecognized value degrades to `UNKNOWN`."""
    raw = getattr(exc, "category", None)
    try:
        return ProviderFailureCategory(raw)
    except ValueError:
        return ProviderFailureCategory.UNKNOWN


def zero_filled_counts(keys: Iterable[str], observed: Mapping[str, int]) -> dict[str, int]:
    """Every key in `keys`, in order, `0` when not observed. Observed keys
    outside `keys` are dropped (closed vocabulary, never pass-through)."""
    return {key: int(observed.get(key, 0)) for key in keys}


def sparse_counts(keys: Iterable[str], observed: Mapping[str, int]) -> dict[str, int]:
    """Only the non-zero entries of `zero_filled_counts` -- keeps the
    single per-scan diagnostics line small."""
    return {key: count for key, count in zero_filled_counts(keys, observed).items() if count > 0}


def count_pairs(counts: Mapping[str, int]) -> tuple[tuple[str, int], ...]:
    """Hashable, deterministic (sorted) form of a small counter -- what a
    frozen request-scoped summary dataclass stores. Zero entries dropped."""
    return tuple(sorted((key, int(count)) for key, count in counts.items() if count > 0))


def merge_count_pairs(
    left: Iterable[tuple[str, int]], right: Iterable[tuple[str, int]]
) -> tuple[tuple[str, int], ...]:
    merged: dict[str, int] = {}
    for key, count in (*left, *right):
        merged[key] = merged.get(key, 0) + count
    return count_pairs(merged)


def component_outcome(succeeded: int, failed: int) -> str:
    """Final outcome label for one translation pass given how many of its
    entries were verified (`succeeded`) vs rejected (`failed`):
    `"succeeded"` (none rejected), `"failed"` (none verified) or
    `"partial"` (a mix). A pass with nothing attempted is the caller's
    `"not_attempted"` -- this function is only for a pass that ran."""
    if failed == 0 and succeeded > 0:
        return "succeeded"
    if succeeded == 0:
        return "failed"
    return "partial"
