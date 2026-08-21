"""Stable lowercase string enums.

Enum members must never be persisted by ordinal or by the Python enum member
name: only ``.value`` (the stable lowercase string) may cross process,
protocol or file boundaries.  ``StableStrEnum.from_value`` is strict: unknown
or wrongly-cased values are rejected instead of silently coerced.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self


class StableStrEnum(StrEnum):
    """Base class: persisted value is the stable lowercase string."""

    @classmethod
    def from_value(cls, value: str) -> Self:
        if not isinstance(value, str):
            raise TypeError(
                f"{cls.__name__} value must be a str, got {type(value).__name__}"
            )
        try:
            return cls(value)
        except ValueError:
            raise ValueError(f"unknown {cls.__name__} value: {value!r}") from None

    def to_json(self) -> str:
        return self.value

    @classmethod
    def from_json(cls, value: str) -> Self:
        return cls.from_value(value)


class EndpointRole(StableStrEnum):
    """Endpoint role in the air/ground system."""

    AIR = "air"
    GROUND = "ground"


class SParameter(StableStrEnum):
    """Scattering parameter bound to a channel."""

    S11 = "s11"
    S21 = "s21"
    S12 = "s12"
    S22 = "s22"


class LogicalPolarization(StableStrEnum):
    """Logical polarization of a channel."""

    HH = "hh"
    HV = "hv"
    VH = "vh"
    VV = "vv"


class MissionTerminalState(StableStrEnum):
    """Task end states (must be distinguishable, never collapsed)."""

    COMPLETED = "completed"
    USER_STOPPED = "user_stopped"
    FAILED = "failed"
    CRASH_RECOVERED = "crash_recovered"


class GnssStatus(StableStrEnum):
    """Base GNSS health states (docs/GNSS.md section 4)."""

    DISCONNECTED = "disconnected"
    NO_SENTENCE = "no_sentence"
    NO_FIX = "no_fix"
    VALID = "valid"
    STALE = "stale"
    INVALID = "invalid"
