"""
Scan-attempt correlation headers (issue #30, see
docs/SCAN_ATTEMPT_DIAGNOSTICS.md for the full contract).

`X-Scan-Attempt-Id` identifies one user-initiated scan attempt end to
end; `X-Scan-Request-Sequence` counts automatic transport retries of
that same attempt. Both are diagnostics-only -- nothing here may ever
fail, delay, or alter a scan request; a missing or malformed header is
always handled by substituting a safe default, never by rejecting the
request.

This numeric ID is NOT an authentication mechanism and is NOT a
guaranteed-globally-unique key (see the contract doc). Generation here
only avoids immediately reusing one of the last
`SCAN_ATTEMPT_ID_RECENT_CACHE_SIZE` ids this process itself generated
-- a best-effort, process-local reduction of collision risk, not an
absolute uniqueness guarantee across processes, workers, or time.
"""
from __future__ import annotations

import re
import secrets
import threading
from collections import OrderedDict
from dataclasses import dataclass

from app.core.config import settings

SCAN_ATTEMPT_ID_HEADER = "X-Scan-Attempt-Id"
SCAN_REQUEST_SEQUENCE_HEADER = "X-Scan-Request-Sequence"

_ATTEMPT_ID_PATTERN = re.compile(r"^[0-9]{16}$")
# A positive integer, deliberately bounded in digit count so an
# absurdly long header value is rejected by regexp rather than parsed
# into an arbitrarily large `int`.
_REQUEST_SEQUENCE_PATTERN = re.compile(r"^[1-9][0-9]{0,8}$")

_lock = threading.Lock()
# Ordered as a bounded FIFO/LRU of already-generated ids: membership is
# checked before returning a freshly generated id, then the id is
# recorded and the oldest entry is evicted once the cache is full.
_recent_generated_ids: "OrderedDict[str, None]" = OrderedDict()

_MAX_GENERATION_ATTEMPTS = 5


def _random_16_digit_id() -> str:
    # `secrets.randbelow` is cryptographically random (contract:
    # "cryptographically random"); `:016d` preserves leading zeros as
    # part of the string, never drops them the way an `int` would.
    return f"{secrets.randbelow(10**16):016d}"


def generate_scan_attempt_id() -> str:
    """A fresh, server-generated 16-digit decimal-string scan-attempt id.

    Used when a client omits the header (legacy client) or supplies one
    that fails validation. Checked against this process's own recently
    generated ids first (bounded, best-effort); the contract explicitly
    does not claim absolute uniqueness -- see the module docstring.
    """
    with _lock:
        for _ in range(_MAX_GENERATION_ATTEMPTS):
            candidate = _random_16_digit_id()
            if candidate not in _recent_generated_ids:
                break
        else:
            candidate = _random_16_digit_id()
        _recent_generated_ids[candidate] = None
        _recent_generated_ids.move_to_end(candidate)
        while len(_recent_generated_ids) > settings.SCAN_ATTEMPT_ID_RECENT_CACHE_SIZE:
            _recent_generated_ids.popitem(last=False)
        return candidate


def is_valid_scan_attempt_id(value: str) -> bool:
    return bool(_ATTEMPT_ID_PATTERN.match(value))


def is_valid_request_sequence(value: str) -> bool:
    return bool(_REQUEST_SEQUENCE_PATTERN.match(value))


@dataclass(frozen=True)
class ScanAttemptContext:
    attempt_id: str
    request_sequence: int
    client_supplied_id: bool
    id_header_was_invalid: bool
    sequence_header_was_invalid: bool


def resolve_scan_attempt_context(
    raw_attempt_id: str | None, raw_request_sequence: str | None
) -> ScanAttemptContext:
    """Reads the two request headers and returns a safe, validated
    context. Never raises -- malformed/oversized values are rejected
    silently (contract: "never log raw input") and replaced with a safe
    default, since these headers must never be able to break a scan.
    """
    id_header_was_invalid = False
    if raw_attempt_id is not None and is_valid_scan_attempt_id(raw_attempt_id):
        attempt_id = raw_attempt_id
        client_supplied_id = True
    else:
        id_header_was_invalid = raw_attempt_id is not None
        attempt_id = generate_scan_attempt_id()
        client_supplied_id = False

    sequence_header_was_invalid = False
    if raw_request_sequence is not None and is_valid_request_sequence(raw_request_sequence):
        request_sequence = int(raw_request_sequence)
    else:
        sequence_header_was_invalid = raw_request_sequence is not None
        request_sequence = 1

    return ScanAttemptContext(
        attempt_id=attempt_id,
        request_sequence=request_sequence,
        client_supplied_id=client_supplied_id,
        id_header_was_invalid=id_header_was_invalid,
        sequence_header_was_invalid=sequence_header_was_invalid,
    )
