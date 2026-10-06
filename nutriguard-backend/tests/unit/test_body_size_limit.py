"""
Direct ASGI-level tests for `app.core.body_size_limit.BodySizeLimitMiddleware`
(issue #30, Codex review round 3).

These bypass FastAPI/Starlette entirely and drive the middleware with
a hand-rolled `scope`/`receive`/`send`, so they can assert the one
thing that matters and that an HTTP-level test cannot observe
directly: the wrapped application is never invoked at all for an
oversized body, and the middleware never accumulates more than the
configured limit (plus the one chunk that pushed it over) before
rejecting -- i.e. the bound holds even if a malicious client's
`receive()` stream never ends.
"""
import pytest

from app.core.body_size_limit import BodySizeLimitMiddleware

_PATH = "/api/v1/scan-diagnostics/client-events"


def _scope(*, path: str = _PATH, method: str = "POST") -> dict:
    return {"type": "http", "method": method, "path": path}


def _chunks(chunks: list[bytes]):
    """A `receive` that yields one `http.request` message per chunk,
    `more_body=True` on all but the last, then raises if called again
    (so a bug that keeps pulling after the body ended shows up loudly
    instead of hanging)."""
    remaining = list(chunks)

    async def receive() -> dict:
        if not remaining:
            raise AssertionError("receive() called after the body was already fully delivered")
        chunk = remaining.pop(0)
        return {"type": "http.request", "body": chunk, "more_body": bool(remaining)}

    return receive


def _unlimited_chunks(chunk: bytes):
    """A `receive` that could in principle be called forever (an
    adversarial client that never stops sending) -- used to prove
    rejection happens without draining the whole thing."""
    calls = {"count": 0}

    async def receive() -> dict:
        calls["count"] += 1
        return {"type": "http.request", "body": chunk, "more_body": True}

    return receive, calls


class _RecordingApp:
    def __init__(self) -> None:
        self.called = False
        self.received_messages: list[dict] = []

    async def __call__(self, scope, receive, send) -> None:
        self.called = True
        while True:
            message = await receive()
            self.received_messages.append(message)
            if message["type"] != "http.request" or not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})


def _sender():
    sent: list[dict] = []

    async def send(message: dict) -> None:
        sent.append(message)

    return send, sent


@pytest.mark.asyncio
async def test_passes_through_a_body_within_the_limit_unchanged(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "SCAN_DIAGNOSTICS_CLIENT_EVENTS_MAX_BODY_BYTES", 64)

    inner = _RecordingApp()
    middleware = BodySizeLimitMiddleware(inner)
    receive = _chunks([b"hello ", b"world"])
    send, sent = _sender()

    await middleware(_scope(), receive, send)

    assert inner.called is True
    assert b"".join(m["body"] for m in inner.received_messages) == b"hello world"
    assert sent[0]["status"] == 200


@pytest.mark.asyncio
async def test_rejects_before_fully_buffering_a_body_that_never_stops_sending(monkeypatch):
    """The core guarantee: an unbounded/adversarial body -- one whose
    `receive()` would in principle never stop yielding more chunks --
    must be rejected after only a bounded number of `receive()` calls,
    and the wrapped application must never be invoked. (The middleware
    also must not keep draining the rest of such a stream after
    rejecting, or it would simply trade this memory bound for an
    unbounded wait -- this test's fake `receive` would hang the test
    itself if it did.)"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "SCAN_DIAGNOSTICS_CLIENT_EVENTS_MAX_BODY_BYTES", 100)

    inner = _RecordingApp()
    middleware = BodySizeLimitMiddleware(inner)
    receive, calls = _unlimited_chunks(b"x" * 10)
    send, sent = _sender()

    await middleware(_scope(), receive, send)

    assert inner.called is False
    assert calls["count"] <= 12  # bounded: ~limit/chunk_size, not unbounded
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 413
    import json

    body = json.loads(sent[1]["body"])
    assert body["error"]["code"] == "PAYLOAD_TOO_LARGE"


@pytest.mark.asyncio
async def test_rejects_a_body_split_across_many_small_chunks_with_no_declared_length(monkeypatch):
    """Simulates exactly the attack the fix addresses: no Content-Length
    at all (this scope carries none), delivered as many small chunks
    that individually look harmless but sum past the limit."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "SCAN_DIAGNOSTICS_CLIENT_EVENTS_MAX_BODY_BYTES", 50)

    inner = _RecordingApp()
    middleware = BodySizeLimitMiddleware(inner)
    receive = _chunks([b"a" * 10 for _ in range(10)])  # 100 bytes total, limit is 50
    send, sent = _sender()

    await middleware(_scope(), receive, send)

    assert inner.called is False
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_ignores_requests_to_other_paths_or_methods(monkeypatch):
    """Scoped to exactly one route -- everything else is untouched."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "SCAN_DIAGNOSTICS_CLIENT_EVENTS_MAX_BODY_BYTES", 1)

    for scope in (_scope(path="/api/v1/scan/barcode"), _scope(method="GET")):
        inner = _RecordingApp()
        middleware = BodySizeLimitMiddleware(inner)
        receive = _chunks([b"this is way more than one byte"])
        send, sent = _sender()

        await middleware(scope, receive, send)

        assert inner.called is True
        assert sent[0]["status"] == 200


@pytest.mark.asyncio
async def test_rejects_immediately_when_the_first_chunk_alone_exceeds_the_limit(monkeypatch):
    """The first chunk alone can already exceed the limit (`more_body`
    still `True`) -- must reject on that chunk, without waiting for or
    reading any of the chunks still to come."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "SCAN_DIAGNOSTICS_CLIENT_EVENTS_MAX_BODY_BYTES", 10)

    inner = _RecordingApp()
    middleware = BodySizeLimitMiddleware(inner)
    receive = _chunks([b"x" * 20, b"y" * 20, b"z" * 5])
    send, sent = _sender()

    await middleware(_scope(), receive, send)

    assert inner.called is False
    assert sent[0]["status"] == 413
