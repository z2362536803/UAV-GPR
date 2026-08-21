"""Timezone-aware UTC utilities and monotonic time value objects.

Two separate time domains exist and must never be mixed:

- UTC wall-clock time (``datetime``, always timezone-aware, normalized to UTC);
- monotonic nanoseconds since an unspecified epoch (``MonotonicNs``).

Naive datetimes are rejected throughout.  The ``Clock`` protocol allows
injecting a deterministic clock (see ``ManualClock``) into tests and services.
"""

from __future__ import annotations

import time as _time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, Self, runtime_checkable

from uav_gpr.core.errors import DomainError, ErrorCode

_NAIVE_REASON = "timezone-aware UTC datetime required, got naive datetime"


@dataclass(frozen=True)
class MonotonicNs:
    """Non-negative count of nanoseconds on a monotonic clock."""

    ns: int

    def __post_init__(self) -> None:
        if isinstance(self.ns, bool) or not isinstance(self.ns, int):
            raise TypeError(
                f"monotonic ns must be an int, got {type(self.ns).__name__}"
            )
        if self.ns < 0:
            raise ValueError(f"monotonic ns must be non-negative, got {self.ns}")

    def to_json(self) -> int:
        return self.ns

    @classmethod
    def from_json(cls, value: int) -> Self:
        return cls(value)

    def __add__(self, other: object) -> Self:
        if not isinstance(other, MonotonicNs):
            return NotImplemented
        return type(self)(self.ns + other.ns)

    def __sub__(self, other: object) -> int:
        if not isinstance(other, MonotonicNs):
            return NotImplemented
        return self.ns - other.ns


def utc_now() -> datetime:
    """Current UTC wall-clock time (timezone-aware, normalized to UTC)."""
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    """Return ``value`` normalized to UTC; reject naive or offset-less datetimes."""
    if not isinstance(value, datetime):
        raise TypeError(
            f"datetime required, got {type(value).__name__}"
        )
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainError(ErrorCode.NAIVE_DATETIME, _NAIVE_REASON)
    return value.astimezone(UTC)


def to_utc_iso(value: datetime) -> str:
    """Serialize an aware datetime to a canonical UTC ISO-8601 string (with Z)."""
    utc = ensure_utc(value)
    return utc.isoformat(timespec="microseconds").replace("+00:00", "Z")


def from_utc_iso(value: str) -> datetime:
    """Parse a canonical UTC ISO-8601 string (``Z`` or ``+00:00`` suffix)."""
    if not isinstance(value, str):
        raise TypeError(f"ISO string required, got {type(value).__name__}")
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        raise ValueError(f"invalid UTC ISO-8601 string: {value!r}") from None
    return ensure_utc(parsed)


@runtime_checkable
class Clock(Protocol):
    """Injected time source: UTC wall-clock plus monotonic nanoseconds."""

    def utc_now(self) -> datetime:
        """Current UTC wall-clock time (aware, normalized to UTC)."""
        ...

    def monotonic_ns(self) -> MonotonicNs:
        """Monotonic nanoseconds now (never negative, never mixed with UTC)."""
        ...


class SystemClock:
    """Production clock backed by the operating system clocks."""

    def utc_now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic_ns(self) -> MonotonicNs:
        return MonotonicNs(_time.monotonic_ns())


class ManualClock:
    """Deterministic test clock: UTC and monotonic advance independently."""

    def __init__(self, utc: datetime, monotonic_ns: int = 0) -> None:
        self._utc = ensure_utc(utc)
        self._mono = MonotonicNs(monotonic_ns)

    def utc_now(self) -> datetime:
        return self._utc

    def monotonic_ns(self) -> MonotonicNs:
        return self._mono

    def advance_utc(self, delta: timedelta) -> None:
        """Advance only the UTC domain."""
        self._utc = ensure_utc(self._utc + delta)

    def advance_monotonic(self, ns: int) -> None:
        """Advance only the monotonic domain."""
        self._mono = MonotonicNs(self._mono.ns + ns)
