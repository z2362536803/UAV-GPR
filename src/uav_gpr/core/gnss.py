"""Immutable GNSS fix and trace-to-fix match domain contracts.

No NMEA parsing, no serial I/O, no matcher algorithm and no hash computing
happens here.  ``GnssFix`` is one fix produced by a receiver; ``GnssMatch``
is how one trace was matched to a fix (or why it was not).  A missing position
is always represented as ``None`` plus a structured reason — never as
``(0, 0)`` and never by reusing a stale fix as the current position.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from uav_gpr.core.enums import (
    GnssFixQuality,
    GnssMatchMethod,
    GnssStatus,
    GnssUnavailableReason,
)
from uav_gpr.core.errors import DomainError, ErrorCode
from uav_gpr.core.timeutil import (
    MonotonicNs,
    ensure_utc,
    from_utc_iso,
    to_utc_iso,
)

_LAT_MIN, _LAT_MAX = -90.0, 90.0
_LON_MIN, _LON_MAX = -180.0, 180.0
_SATELLITES_MAX = 99
_HDOP_MAX = 99.9
_COURSE_MAX = 360.0
_FIX_QUALITY_FOR_VALID = (GnssFixQuality.GPS_FIX, GnssFixQuality.DGPS,
                          GnssFixQuality.RTK_FIXED, GnssFixQuality.RTK_FLOAT,
                          GnssFixQuality.ESTIMATED, GnssFixQuality.SIMULATED)
_FIX_INVALID_REASONS = (GnssUnavailableReason.NO_FIX, GnssUnavailableReason.INVALID)


def _require_finite_float(value: float | None, field: str) -> None:
    if value is not None and (not isinstance(value, float) or not math.isfinite(value)):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            f"{field} must be a finite float or None",
            {"field": field},
        )


def _require_range(
    value: float | None,
    low: float,
    high: float,
    field: str,
    *,
    high_inclusive: bool = False,
) -> None:
    _require_finite_float(value, field)
    if value is None:
        return
    within = value <= high if high_inclusive else value < high
    if not (low <= value and within):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            f"{field} out of range",
            {"field": field, "min": low, "max": high, "got": value},
        )


@dataclass(frozen=True, slots=True)
class GnssFix:
    """One GNSS fix (immutable).  Latitude/longitude are WGS84 degrees."""

    received_utc: datetime
    nmea_utc: datetime | None
    received_monotonic_ns: MonotonicNs
    latitude_deg: float | None
    longitude_deg: float | None
    altitude_msl_m: float | None
    geoid_separation_m: float | None
    fix_quality: GnssFixQuality
    satellites: int | None
    hdop: float | None
    ground_speed_mps: float | None
    course_deg: float | None
    valid: bool
    invalid_reason: GnssUnavailableReason | None

    def __post_init__(self) -> None:
        received = ensure_utc(self.received_utc)
        nmea = ensure_utc(self.nmea_utc) if self.nmea_utc is not None else None
        object.__setattr__(self, "received_utc", received)
        object.__setattr__(self, "nmea_utc", nmea)
        if not isinstance(self.received_monotonic_ns, MonotonicNs):
            raise TypeError(
                "received_monotonic_ns must be a MonotonicNs, "
                f"got {type(self.received_monotonic_ns).__name__}"
            )
        if not isinstance(self.fix_quality, GnssFixQuality):
            raise TypeError(
                f"fix_quality must be a GnssFixQuality, got {type(self.fix_quality).__name__}"
            )
        lat, lon = self.latitude_deg, self.longitude_deg
        if (lat is None) != (lon is None):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "latitude and longitude must both be set or both be None",
            )
        if lat is not None and lon is not None:
            _require_range(lat, _LAT_MIN, _LAT_MAX, "latitude_deg", high_inclusive=True)
            _require_range(lon, _LON_MIN, _LON_MAX, "longitude_deg", high_inclusive=True)
        _require_finite_float(self.altitude_msl_m, "altitude_msl_m")
        _require_finite_float(self.geoid_separation_m, "geoid_separation_m")
        if self.satellites is not None:
            if isinstance(self.satellites, bool) or not isinstance(self.satellites, int):
                raise TypeError("satellites must be an int or None")
            if not (0 <= self.satellites <= _SATELLITES_MAX):
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "satellites out of range",
                    {"satellites": self.satellites},
                )
        _require_range(self.hdop, 0.0, _HDOP_MAX, "hdop", high_inclusive=True)
        if self.ground_speed_mps is not None and not (self.ground_speed_mps >= 0.0):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "ground_speed_mps must be non-negative",
                {"ground_speed_mps": self.ground_speed_mps},
            )
        _require_range(self.course_deg, 0.0, _COURSE_MAX, "course_deg")
        if self.valid:
            if lat is None or lon is None:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "valid fix requires latitude and longitude",
                )
            if self.fix_quality not in _FIX_QUALITY_FOR_VALID:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "valid fix requires a non-invalid fix quality",
                )
            if self.invalid_reason is not None:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "valid fix must not carry an invalid_reason",
                )
        else:
            if lat is not None or lon is not None:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "invalid fix must not carry coordinates",
                )
            if self.invalid_reason not in _FIX_INVALID_REASONS:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "invalid fix requires a no_fix or invalid reason",
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "received_utc": to_utc_iso(self.received_utc),
            "nmea_utc": to_utc_iso(self.nmea_utc) if self.nmea_utc is not None else None,
            "received_monotonic_ns": self.received_monotonic_ns.ns,
            "latitude_deg": self.latitude_deg,
            "longitude_deg": self.longitude_deg,
            "altitude_msl_m": self.altitude_msl_m,
            "geoid_separation_m": self.geoid_separation_m,
            "fix_quality": self.fix_quality.value,
            "satellites": self.satellites,
            "hdop": self.hdop,
            "ground_speed_mps": self.ground_speed_mps,
            "course_deg": self.course_deg,
            "valid": self.valid,
            "invalid_reason": self.invalid_reason.value if self.invalid_reason else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> GnssFix:
        return cls(
            received_utc=from_utc_iso(_require_str(data["received_utc"], "received_utc")),
            nmea_utc=(
                from_utc_iso(_require_str(data["nmea_utc"], "nmea_utc"))
                if data.get("nmea_utc") is not None
                else None
            ),
            received_monotonic_ns=MonotonicNs(_require_int(data["received_monotonic_ns"])),
            latitude_deg=_optional_float(data.get("latitude_deg")),
            longitude_deg=_optional_float(data.get("longitude_deg")),
            altitude_msl_m=_optional_float(data.get("altitude_msl_m")),
            geoid_separation_m=_optional_float(data.get("geoid_separation_m")),
            fix_quality=GnssFixQuality.from_value(
                _require_str(data["fix_quality"], "fix_quality")
            ),
            satellites=_optional_int(data.get("satellites")),
            hdop=_optional_float(data.get("hdop")),
            ground_speed_mps=_optional_float(data.get("ground_speed_mps")),
            course_deg=_optional_float(data.get("course_deg")),
            valid=_require_bool(data["valid"]),
            invalid_reason=(
                GnssUnavailableReason.from_value(
                    _require_str(data["invalid_reason"], "invalid_reason")
                )
                if data.get("invalid_reason") is not None
                else None
            ),
        )


def _require_str(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def _require_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"expected an int, got {type(value).__name__}")
    return value


def _require_bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"expected a bool, got {type(value).__name__}")
    return value


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, float):
        raise ValueError(f"expected a float or None, got {type(value).__name__}")
    return value


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return _require_int(value)


@dataclass(frozen=True, slots=True)
class GnssMatch:
    """How one trace matched a fix, or why it could not (immutable)."""

    fix: GnssFix | None
    trace_midpoint_utc: datetime
    age_s: float | None
    method: GnssMatchMethod
    usable_for_map: bool
    reason: GnssUnavailableReason | None

    def __post_init__(self) -> None:
        midpoint = ensure_utc(self.trace_midpoint_utc)
        object.__setattr__(self, "trace_midpoint_utc", midpoint)
        if self.fix is not None and not isinstance(self.fix, GnssFix):
            raise TypeError(
                f"fix must be a GnssFix or None, got {type(self.fix).__name__}"
            )
        if not isinstance(self.method, GnssMatchMethod):
            raise TypeError(
                f"method must be a GnssMatchMethod, got {type(self.method).__name__}"
            )
        if self.reason is not None and not isinstance(self.reason, GnssUnavailableReason):
            raise TypeError(
                f"reason must be a GnssUnavailableReason or None, "
                f"got {type(self.reason).__name__}"
            )
        if self.fix is None:
            if self.reason is None:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "a fix-less match requires an unavailable reason",
                )
            if self.reason is GnssUnavailableReason.STALE:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "a fix-less match cannot be stale",
                )
            if self.age_s is not None:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "age_s must be None when there is no fix",
                )
            if self.usable_for_map:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "a fix-less match cannot be usable for the map",
                )
        else:
            if self.age_s is None:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "age_s is required when a fix is present",
                )
            if not (self.age_s >= 0.0):
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "age_s must be non-negative",
                    {"age_s": self.age_s},
                )
            if self.usable_for_map and not self.fix.valid:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "a map-usable match requires a valid fix",
                )
            if self.usable_for_map and self.reason is not None:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "a map-usable match must not carry an unavailable reason",
                )
            if not self.usable_for_map and self.reason is None:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "a non-usable match requires an unavailable reason",
                )
        if self.reason is GnssUnavailableReason.NO_FIX and self.fix is not None:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "no_fix reason requires an absent fix",
            )

    @property
    def status(self) -> GnssStatus:
        """Derived GNSS health status for this match."""
        if self.usable_for_map:
            return GnssStatus.VALID
        if self.reason is GnssUnavailableReason.STALE:
            return GnssStatus.STALE
        if self.reason is GnssUnavailableReason.INVALID:
            return GnssStatus.INVALID
        return GnssStatus.NO_FIX

    def to_dict(self) -> dict[str, object]:
        return {
            "fix": self.fix.to_dict() if self.fix is not None else None,
            "trace_midpoint_utc": to_utc_iso(self.trace_midpoint_utc),
            "age_s": self.age_s,
            "method": self.method.value,
            "usable_for_map": self.usable_for_map,
            "reason": self.reason.value if self.reason is not None else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> GnssMatch:
        fix_data = data.get("fix")
        if fix_data is not None and not isinstance(fix_data, dict):
            raise ValueError("fix must be an object or null")
        return cls(
            fix=GnssFix.from_dict(fix_data) if fix_data is not None else None,
            trace_midpoint_utc=from_utc_iso(
                _require_str(data["trace_midpoint_utc"], "trace_midpoint_utc")
            ),
            age_s=_optional_float(data.get("age_s")),
            method=GnssMatchMethod.from_value(
                _require_str(data["method"], "method")
            ),
            usable_for_map=_require_bool(data["usable_for_map"]),
            reason=(
                GnssUnavailableReason.from_value(
                    _require_str(data["reason"], "reason")
                )
                if data.get("reason") is not None
                else None
            ),
        )
