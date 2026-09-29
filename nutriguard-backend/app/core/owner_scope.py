"""
Pseudonymous owner scoping for the scan-diagnostics journal (issue #30,
Codex review round 2, finding 4).

The journal deliberately never stores a raw authenticated user id (see
`app.core.scan_diagnostics`'s module docstring) -- but with NO owner
signal at all, the operator CLI cannot tell two different owners'
attempts apart when their `scanAttemptId`s happen to collide (a real,
documented residual risk -- see docs/SCAN_ATTEMPT_DIAGNOSTICS.md §2.3).
`pseudonymous_owner_scope` gives a STABLE, per-user, but non-reversible
(without the server's own secret) identifier: an HMAC-SHA256 of the raw
user id, keyed by `settings.JWT_SECRET` (already a guarded, never-logged
secret -- see CLAUDE.md §9). Two events from the SAME user always carry
the SAME scope; two different users (overwhelmingly likely) carry
different scopes; nobody holding only the journal file can recover the
original user id from it.
"""
from __future__ import annotations

import hashlib
import hmac

from app.core.config import settings

# Explicit sentinel for "no authenticated owner" (issue #30, Codex
# review round 2, finding 4: "explicitly distinguish unauthenticated/
# unknown scope") -- every current write site is authenticated, so this
# is never emitted by this codebase today, but a malformed/older
# retained journal line may lack `ownerScope` entirely (see
# `app.seed.scan_attempt_trace`), and that absence must be labelled
# explicitly rather than silently treated as a valid scope.
UNKNOWN_OWNER_SCOPE = "unknown"


def pseudonymous_owner_scope(user_id: object) -> str:
    """`user_id` is anything `str()`-convertible to a stable identifier
    (a `uuid.UUID`, or already a string) -- never logged or persisted
    itself, only this one-way derived value is."""
    digest = hmac.new(
        settings.JWT_SECRET.encode("utf-8"),
        str(user_id).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest[:32]
