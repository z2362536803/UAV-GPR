"""Tests for UTC and monotonic time primitives (ISSUE-003)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from uav_gpr.core import (
    Clock,
    DomainError,
    ErrorCode,
    ManualClock,
    MonotonicNs,
    SystemClock,
    ensure_utc,
    from_utc_iso,
    to_utc_iso,
    utc_now,
)


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(DomainError) as excinfo:
        ensure_utc(datetime(2026, 1, 1, 12, 0, 0))
    assert excinfo.value.code is ErrorCode.NAIVE_DATETIME
    with pytest.raises(DomainError):
        ManualClock(datetime(2026, 1, 1, 12, 0, 0))


def test_aware_datetime_is_normalized_to_utc() -> None:
    chinese = timezone(timedelta(hours=8))
    aware = datetime(2026, 1, 1, 12, 0, 0, tzinfo=chinese)
    normalized = ensure_utc(aware)
    assert normalized.tzinfo is UTC
    assert normalized.hour == 4
    assert normalized == datetime(2026, 1, 1, 4, 0, 0, tzinfo=UTC)


def test_to_and_from_utc_iso_round_trip() -> None:
    instant = datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=UTC)
    encoded = to_utc_iso(instant)
    assert encoded == "2026-01-02T03:04:05.123456Z"
    assert from_utc_iso(encoded) == instant
    # Non-UTC offsets are normalized on encode and round-trip exactly.
    offset = datetime(2026, 1, 2, 11, 4, 5, 123456, tzinfo=timezone(timedelta(hours=8)))
    assert from_utc_iso(to_utc_iso(offset)) == instant


def test_from_utc_iso_rejects_naive_and_garbage() -> None:
    with pytest.raises(DomainError) as excinfo:
        from_utc_iso("2026-01-02T03:04:05")
    assert excinfo.value.code is ErrorCode.NAIVE_DATETIME
    with pytest.raises(ValueError, match="invalid UTC ISO-8601"):
        from_utc_iso("not-a-date")
    with pytest.raises(TypeError):
        from_utc_iso(123)  # type: ignore[arg-type]


def test_monotonic_ns_value_object() -> None:
    value = MonotonicNs(5)
    assert value.ns == 5
    assert value.to_json() == 5
    assert MonotonicNs.from_json(5) == value
    assert MonotonicNs(5) == MonotonicNs(5)
    assert MonotonicNs(2) + MonotonicNs(3) == MonotonicNs(5)
    assert MonotonicNs(5) - MonotonicNs(2) == 3
    with pytest.raises(ValueError, match="non-negative"):
        MonotonicNs(-1)
    with pytest.raises(TypeError):
        MonotonicNs(True)  # type: ignore[arg-type]


def test_monotonic_ns_does_not_mix_with_other_types() -> None:
    with pytest.raises(TypeError):
        _ = MonotonicNs(5) + 1  # type: ignore[operator]
    with pytest.raises(TypeError):
        _ = MonotonicNs(5) - datetime(2026, 1, 1, tzinfo=UTC)  # type: ignore[operator]
    with pytest.raises(TypeError):
        ensure_utc(MonotonicNs(5))  # type: ignore[arg-type]


def test_manual_clock_domains_advance_independently() -> None:
    clock = ManualClock(datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC), monotonic_ns=100)
    assert clock.utc_now() == datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    assert clock.monotonic_ns() == MonotonicNs(100)
    clock.advance_utc(timedelta(seconds=2))
    assert clock.utc_now().second == 2
    assert clock.monotonic_ns() == MonotonicNs(100)  # untouched
    clock.advance_monotonic(50)
    assert clock.monotonic_ns() == MonotonicNs(150)
    assert clock.utc_now().second == 2  # untouched


def test_system_clock_produces_aware_utc_and_monotonic() -> None:
    clock = SystemClock()
    first = clock.utc_now()
    assert first.tzinfo is not None
    mono_first = clock.monotonic_ns()
    assert mono_first.ns >= 0
    mono_second = clock.monotonic_ns()
    assert mono_second.ns >= mono_first.ns


def test_clock_protocol_conformance() -> None:
    manual = ManualClock(datetime(2026, 1, 1, tzinfo=UTC))
    system = SystemClock()
    assert isinstance(manual, Clock)
    assert isinstance(system, Clock)
    assert isinstance(utc_now(), datetime)
