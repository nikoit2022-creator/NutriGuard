"""Unit tests for app.core.scan_attempt (issue #30 header contract)."""
import re

from app.core import scan_attempt


def test_generated_id_is_16_ascii_digits():
    generated = scan_attempt.generate_scan_attempt_id()
    assert isinstance(generated, str)
    assert re.fullmatch(r"[0-9]{16}", generated)


def test_generated_ids_avoid_recent_collisions(monkeypatch):
    # Force every random draw to the same value; the recent-id cache
    # must still notice the collision and try again rather than
    # returning the same id twice in a row.
    monkeypatch.setattr(scan_attempt, "_recent_generated_ids", scan_attempt._recent_generated_ids.__class__())
    calls = iter(["1111111111111111", "1111111111111111", "2222222222222222"])
    monkeypatch.setattr(scan_attempt, "_random_16_digit_id", lambda: next(calls))

    first = scan_attempt.generate_scan_attempt_id()
    second = scan_attempt.generate_scan_attempt_id()
    assert first == "1111111111111111"
    assert second == "2222222222222222"


def test_recent_id_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(scan_attempt.settings, "SCAN_ATTEMPT_ID_RECENT_CACHE_SIZE", 3)
    monkeypatch.setattr(scan_attempt, "_recent_generated_ids", scan_attempt._recent_generated_ids.__class__())
    for _ in range(10):
        scan_attempt.generate_scan_attempt_id()
    assert len(scan_attempt._recent_generated_ids) <= 3


def test_valid_attempt_id_round_trips_leading_zeros():
    ctx = scan_attempt.resolve_scan_attempt_context("0000000000000042", None)
    assert ctx.attempt_id == "0000000000000042"
    assert ctx.client_supplied_id is True
    assert ctx.id_header_was_invalid is False


def test_missing_attempt_id_header_generates_one():
    ctx = scan_attempt.resolve_scan_attempt_context(None, None)
    assert re.fullmatch(r"[0-9]{16}", ctx.attempt_id)
    assert ctx.client_supplied_id is False
    assert ctx.id_header_was_invalid is False


def test_malformed_attempt_id_header_is_replaced_not_rejected():
    for bad in ["too-short", "12345678901234567", "abcd1234abcd1234", "", " " * 16]:
        ctx = scan_attempt.resolve_scan_attempt_context(bad, None)
        assert re.fullmatch(r"[0-9]{16}", ctx.attempt_id)
        assert ctx.client_supplied_id is False
        assert ctx.id_header_was_invalid is True
        # The raw invalid value must never be echoed back or otherwise
        # surface as the accepted id.
        assert ctx.attempt_id != bad


def test_missing_request_sequence_defaults_to_one():
    ctx = scan_attempt.resolve_scan_attempt_context("1" * 16, None)
    assert ctx.request_sequence == 1
    assert ctx.sequence_header_was_invalid is False


def test_valid_request_sequence_is_parsed():
    ctx = scan_attempt.resolve_scan_attempt_context("1" * 16, "7")
    assert ctx.request_sequence == 7


def test_malformed_request_sequence_defaults_to_one_without_raising():
    for bad in ["0", "-1", "not-a-number", "", "01"]:
        ctx = scan_attempt.resolve_scan_attempt_context("1" * 16, bad)
        assert ctx.request_sequence == 1
        assert ctx.sequence_header_was_invalid is True


def test_oversized_request_sequence_defaults_to_one():
    ctx = scan_attempt.resolve_scan_attempt_context("1" * 16, "9" * 20)
    assert ctx.request_sequence == 1
    assert ctx.sequence_header_was_invalid is True
