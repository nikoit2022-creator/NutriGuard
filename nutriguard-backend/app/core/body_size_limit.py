"""
Pure ASGI middleware enforcing a hard byte cap on one route's request
body (issue #30, Codex review round 3).

`app.api.v1.scan_diagnostics` used to enforce
`settings.SCAN_DIAGNOSTICS_CLIENT_EVENTS_MAX_BODY_BYTES` from a FastAPI
dependency (`Depends(...)`) on that route. That is too late: FastAPI's
own request handler (`fastapi.routing.get_request_handler`) calls
`await request.body()` to parse the route's declared
`batch: ClientEventBatchRequest` parameter BEFORE it resolves ANY of
that route's dependencies -- and Starlette's `Request.body()` has no
size bound of its own; it buffers chunks off the raw ASGI `receive`
channel until the client signals the body is complete, regardless of
`Content-Length` (if any) or this limit. By the time a dependency on
the same route finally runs, the whole oversized body -- whether it
had a missing/lying `Content-Length`, or no declared length at all
(chunked transfer) -- is already sitting fully buffered in memory;
re-reading it via `request.stream()` at that point just replays the
already-buffered bytes in one shot (Starlette caches `self._body` on
the first `.body()` call and `.stream()` yields that cache directly),
so the previous dependency's own "bounded streaming read" never
actually bounded anything.

This middleware instead wraps the raw ASGI `receive` channel for
exactly this one path, BEFORE Starlette's routing (and therefore
FastAPI's own body buffering) ever runs. It counts bytes as they
arrive off the wire and, the instant the running total exceeds the
configured limit, sends the 413 response itself and returns WITHOUT
ever invoking the wrapped application -- the request never reaches
FastAPI/Starlette's routing, body parsing, or this route's own
dependencies at all. A request within the limit is replayed to the
wrapped application byte-for-byte, so normal parsing is unaffected,
and the schema FastAPI publishes in `openapi.json` is untouched (this
middleware is invisible to route/dependency introspection).

Must be registered as the OUTERMOST middleware in
`app.main.create_app` (added last, so it wraps every other middleware)
so it intercepts before CORS, request-context logging, or scan-attempt
header handling ever see the request -- see that function.
"""
from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, MutableMapping

from app.core.config import settings
from app.core.exceptions import PayloadTooLargeError, error_envelope

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class BodySizeLimitMiddleware:
    """Scoped to one exact `(method, path)` -- never touches any other
    route's body, including the application's much larger image-upload
    endpoint (`POST /api/v1/scan/label-image`), which has its own,
    separate size handling."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        method: str = "POST",
        path: str = f"{settings.API_V1_PREFIX}/scan-diagnostics/client-events",
    ) -> None:
        self.app = app
        self.method = method
        self.path = path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != self.method or scope["path"] != self.path:
            await self.app(scope, receive, send)
            return

        # Read fresh on every call, never cached at construction time:
        # `app.main.create_app()` builds this middleware once at import
        # time, long before any individual request (or test, which
        # monkeypatches the shared `settings` singleton per-test) can
        # adjust this value.
        limit = settings.SCAN_DIAGNOSTICS_CLIENT_EVENTS_MAX_BODY_BYTES

        buffered: list[Message] = []
        total = 0
        over_limit = False

        while True:
            message = await receive()
            buffered.append(message)
            if message["type"] != "http.request":
                # e.g. `http.disconnect` before the body finished --
                # nothing left to read; let the replay below hand this
                # straight to the wrapped app.
                break
            total += len(message.get("body", b""))
            if total > limit:
                over_limit = True
                break
            if not message.get("more_body", False):
                break

        if over_limit:
            # Deliberately does NOT keep draining whatever the client
            # still has left to send: for a body that is oversized
            # because it never stops (the scenario this middleware
            # exists for), that would just trade a memory-unbounded
            # read for a time-unbounded one. Sending the response now
            # and returning leaves the body only partially read, which
            # the ASGI server (uvicorn) correctly treats as a
            # non-reusable connection and closes outright -- safe, and
            # the standard way this class of middleware handles
            # rejection; see tests/unit/test_body_size_limit.py for the
            # "never stops sending" case this specifically guards.
            await self._reject(send, limit)
            return

        await self.app(scope, self._replay(buffered, receive), send)

    @staticmethod
    def _replay(buffered: list[Message], receive: Receive) -> Receive:
        iterator = iter(buffered)

        async def replay_receive() -> Message:
            try:
                return next(iterator)
            except StopIteration:
                return await receive()

        return replay_receive

    @staticmethod
    async def _reject(send: Send, limit: int) -> None:
        body = json.dumps(
            error_envelope(
                PayloadTooLargeError.code,
                f"Request body exceeds the {limit}-byte limit.",
            )
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": PayloadTooLargeError.status_code,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})
