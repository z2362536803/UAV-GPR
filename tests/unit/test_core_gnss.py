"""Tests for immutable GNSS fix and match models (ISSUE-005)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from uav_gpr.core import (
    DomainError,
    ErrorCode,
    GnssFix,
    GnssFixQuality,
    GnssMatch,
    GnssMatchMethod,
    GnssStatus,
    GnssUnavailableReason,
    MonotonicNs,
)

MIDPOINT = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _valid_fix(
    lat: float = 30.5,
    lon: float = 120.1,
    *,
    quality: GnssFixQuality = GnssFixQuality.RTK_FIXED,
) -> GnssFix:
    return GnssFix(
        received_utc=datetime(2026, 1, 1, 11, 59, 30, tzinfo=UTC),
        nmea_utc=datetime(2026, 1, 1, 11, 59, 29, 500000, tzinfo=UTC),
        received_monotonic_ns=MonotonicNs(1_000),
        latitude_deg=lat,
        longitude_deg=lon,
        altitude_msl_m=12.5,
        geoid_separation_m=-8.3,
        fix_quality=quality,
        satellites=14,
        hdop=0.9,
        ground_speed_mps=3.4,
        course_deg=123.0,
        valid=True,
        invalid_reason=None,
    )


def _no_fix(reason: GnssUnavailableReason = GnssUnavailableReason.NO_FIX) -> GnssFix:
    return GnssFix(
        received_utc=datetime(2026, 1, 1, 11, 59, 30, tzinfo=UTC),
        nmea_utc=None,
        received_monotonic_ns=MonotonicNs(2_000),
        latitude_deg=None,
        longitude_deg=None,
        altitude_msl_m=None,
        geoid_separation_m=None,
        fix_quality=GnssFixQuality.INVALID,
        satellites=0,
        hdop=0.0,
        ground_speed_mps=None,
        course_deg=None,
        valid=False,
        invalid_reason=reason,
    )


def test_valid_fix_json_round_trip() -> None:
    fix = _valid_fix()
    assert fix.valid is True
    assert fix.latitude_deg == 30.5
    assert fix.longitude_deg == 120.1
    assert fix.altitude_msl_m == 12.5
    assert fix.geoid_separation_m == -8.3
    restored = GnssFix.from_dict(fix.to_dict())
    assert restored == fix


def test_southern_and_western_hemisphere() -> None:
    fix = _valid_fix(lat=-33.9, lon=-70.6)
    assert fix.latitude_deg == -33.9
    assert fix.longitude_deg == -70.6
    assert fix.valid


def test_no_fix_has_no_coordinates_and_explicit_reason() -> None:
    fix = _no_fix()
    assert fix.valid is False
    assert fix.latitude_deg is None
    assert fix.longitude_deg is None
    assert fix.invalid_reason is GnssUnavailableReason.NO_FIX
    assert fix.fix_quality is GnssFixQuality.INVALID
    assert GnssFix.from_dict(fix.to_dict()) == fix


def test_invalid_fix_reason() -> None:
    fix = _no_fix(GnssUnavailableReason.INVALID)
    assert fix.invalid_reason is GnssUnavailableReason.INVALID


def _fix_with(**overrides: object) -> GnssFix:
    params: dict[str, object] = {
        "received_utc": datetime(2026, 1, 1, 11, 59, 30, tzinfo=UTC),
        "nmea_utc": None,
        "received_monotonic_ns": MonotonicNs(1_000),
        "latitude_deg": 30.5,
        "longitude_deg": 120.1,
        "altitude_msl_m": 12.5,
        "geoid_separation_m": -8.3,
        "fix_quality": GnssFixQuality.RTK_FIXED,
        "satellites": 14,
        "hdop": 0.9,
        "ground_speed_mps": 3.4,
        "course_deg": 123.0,
        "valid": True,
        "invalid_reason": None,
    }
    params.update(overrides)
    return GnssFix(**params)


def test_range_validation() -> None:
    cases = [
        {"latitude_deg": 91.0},
        {"latitude_deg": -91.0},
        {"longitude_deg": 181.0},
        {"longitude_deg": -181.0},
        {"hdop": 100.0},
        {"hdop": -0.1},
        {"course_deg": 360.0},
        {"course_deg": -0.1},
        {"ground_speed_mps": -1.0},
        {"ground_speed_mps": float("nan")},
        {"ground_speed_mps": float("inf")},
        {"ground_speed_mps": float("-inf")},
        {"altitude_msl_m": float("nan")},
        {"altitude_msl_m": float("inf")},
    ]
    for override in cases:
        with pytest.raises(DomainError) as excinfo:
            _fix_with(**override)
        assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT


def test_float_fields_reject_wrong_types() -> None:
    for override in (
        {"ground_speed_mps": "fast"},  # type: ignore[arg-type]
        {"hdop": "many"},  # type: ignore[arg-type]
        {"altitude_msl_m": 12},  # int is not a float field value
    ):
        with pytest.raises(DomainError):
            _fix_with(**override)


def test_valid_flag_rejects_non_bool() -> None:
    with pytest.raises(TypeError, match="valid must be a bool"):
        _fix_with(valid=1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="valid must be a bool"):
        _fix_with(valid="yes")  # type: ignore[arg-type]


def test_satellite_count_range() -> None:
    for bad in (-1, 100):
        with pytest.raises(DomainError) as excinfo:
            _fix_with(satellites=bad)
        assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT
    # Boundary values are accepted.
    assert _fix_with(satellites=0).satellites == 0
    assert _fix_with(satellites=99).satellites == 99


def test_coordinate_pairing_and_validity_rules() -> None:
    with pytest.raises(DomainError):
        GnssFix(
            received_utc=datetime(2026, 1, 1, tzinfo=UTC),
            nmea_utc=None,
            received_monotonic_ns=MonotonicNs(1),
            latitude_deg=1.0,
            longitude_deg=None,
            altitude_msl_m=None,
            geoid_separation_m=None,
            fix_quality=GnssFixQuality.GPS_FIX,
            satellites=5,
            hdop=1.0,
            ground_speed_mps=None,
            course_deg=None,
            valid=True,
            invalid_reason=None,
        )
    with pytest.raises(DomainError):
        GnssFix(
            received_utc=datetime(2026, 1, 1, tzinfo=UTC),
            nmea_utc=None,
            received_monotonic_ns=MonotonicNs(1),
            latitude_deg=1.0,
            longitude_deg=1.0,
            altitude_msl_m=None,
            geoid_separation_m=None,
            fix_quality=GnssFixQuality.INVALID,
            satellites=0,
            hdop=0.0,
            ground_speed_mps=None,
            course_deg=None,
            valid=True,
            invalid_reason=None,
        )


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(DomainError) as excinfo:
        _fix_with(received_utc=datetime(2026, 1, 1, 11, 59, 30))
    assert excinfo.value.code is ErrorCode.NAIVE_DATETIME


def test_msl_semantics_are_not_agl() -> None:
    fix = _valid_fix()
    # MSL altitude is a distinct field; there is no AGL field at all.
    assert fix.altitude_msl_m == 12.5
    assert not hasattr(fix, "altitude_agl_m")
    assert not hasattr(fix, "agl_m")
    assert not any("agl" in field for field in fix.__dataclass_fields__)


def test_usable_match_status_is_valid() -> None:
    fix = _valid_fix()
    match = GnssMatch(
        fix=fix,
        trace_midpoint_utc=MIDPOINT,
        age_s=0.25,
        method=GnssMatchMethod.NEAREST_MIDPOINT,
        usable_for_map=True,
        reason=None,
    )
    assert match.status is GnssStatus.VALID
    assert match.age_s == 0.25
    assert GnssMatch.from_dict(match.to_dict()) == match


def test_stale_match_keeps_fix_but_is_not_usable() -> None:
    fix = _valid_fix()
    match = GnssMatch(
        fix=fix,
        trace_midpoint_utc=MIDPOINT,
        age_s=7.0,
        method=GnssMatchMethod.NEAREST_MIDPOINT,
        usable_for_map=False,
        reason=GnssUnavailableReason.STALE,
    )
    assert match.status is GnssStatus.STALE
    assert match.fix is fix
    assert match.usable_for_map is False


def test_no_fix_match() -> None:
    match = GnssMatch(
        fix=None,
        trace_midpoint_utc=MIDPOINT,
        age_s=None,
        method=GnssMatchMethod.NEAREST_MIDPOINT,
        usable_for_map=False,
        reason=GnssUnavailableReason.NO_FIX,
    )
    assert match.status is GnssStatus.NO_FIX
    assert match.fix is None
    assert match.age_s is None


def test_invalid_match_status() -> None:
    match = GnssMatch(
        fix=None,
        trace_midpoint_utc=MIDPOINT,
        age_s=None,
        method=GnssMatchMethod.NEAREST_MIDPOINT,
        usable_for_map=False,
        reason=GnssUnavailableReason.INVALID,
    )
    assert match.status is GnssStatus.INVALID


def test_match_consistency_rules() -> None:
    fix = _valid_fix()
    with pytest.raises(DomainError):  # fix-less match without reason
        GnssMatch(
            fix=None,
            trace_midpoint_utc=MIDPOINT,
            age_s=None,
            method=GnssMatchMethod.NEAREST_MIDPOINT,
            usable_for_map=False,
            reason=None,
        )
    with pytest.raises(DomainError):  # usable match with reason
        GnssMatch(
            fix=fix,
            trace_midpoint_utc=MIDPOINT,
            age_s=0.1,
            method=GnssMatchMethod.NEAREST_MIDPOINT,
            usable_for_map=True,
            reason=GnssUnavailableReason.STALE,
        )
    with pytest.raises(DomainError):  # unusable match without reason
        GnssMatch(
            fix=fix,
            trace_midpoint_utc=MIDPOINT,
            age_s=0.1,
            method=GnssMatchMethod.NEAREST_MIDPOINT,
            usable_for_map=False,
            reason=None,
        )
    with pytest.raises(DomainError):  # no_fix reason with fix present
        GnssMatch(
            fix=fix,
            trace_midpoint_utc=MIDPOINT,
            age_s=0.1,
            method=GnssMatchMethod.NEAREST_MIDPOINT,
            usable_for_map=False,
            reason=GnssUnavailableReason.NO_FIX,
        )
    with pytest.raises(DomainError):  # fix-less stale is contradictory
        GnssMatch(
            fix=None,
            trace_midpoint_utc=MIDPOINT,
            age_s=None,
            method=GnssMatchMethod.NEAREST_MIDPOINT,
            usable_for_map=False,
            reason=GnssUnavailableReason.STALE,
        )


def test_match_age_s_rejects_non_finite_and_wrong_types() -> None:
    fix = _valid_fix()
    for bad_age in (float("nan"), float("inf"), float("-inf"), "old"):
        with pytest.raises(DomainError):
            GnssMatch(
                fix=fix,
                trace_midpoint_utc=MIDPOINT,
                age_s=bad_age,  # type: ignore[arg-type]
                method=GnssMatchMethod.NEAREST_MIDPOINT,
                usable_for_map=True,
                reason=None,
            )


def test_usable_for_map_rejects_non_bool() -> None:
    fix = _valid_fix()
    with pytest.raises(TypeError, match="usable_for_map must be a bool"):
        GnssMatch(
            fix=fix,
            trace_midpoint_utc=MIDPOINT,
            age_s=0.1,
            method=GnssMatchMethod.NEAREST_MIDPOINT,
            usable_for_map=1,  # type: ignore[arg-type]
            reason=None,
        )
