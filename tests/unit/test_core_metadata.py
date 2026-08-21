"""Tests for immutable trace metadata (ISSUE-005)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from uav_gpr.core import (
    DeviceId,
    DomainError,
    ErrorCode,
    GnssFix,
    GnssFixQuality,
    GnssMatch,
    GnssMatchMethod,
    MissionId,
    MonotonicNs,
    TraceMetadata,
    TraceQualityReason,
    TraceQualityStatus,
    TraceUid,
)

MISSION = MissionId("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
TRACE_UID = TraceUid("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
DEVICE = DeviceId("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
HASH64 = "a" * 64

START_UTC = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
MID_UTC = datetime(2026, 1, 1, 12, 0, 0, 250000, tzinfo=UTC)
FINISH_UTC = datetime(2026, 1, 1, 12, 0, 0, 500000, tzinfo=UTC)


def _good_match() -> GnssMatch:
    return GnssMatch(
        fix=GnssFix(
            received_utc=START_UTC,
            nmea_utc=None,
            received_monotonic_ns=MonotonicNs(1_000_000),
            latitude_deg=30.5,
            longitude_deg=120.1,
            altitude_msl_m=12.0,
            geoid_separation_m=-8.0,
            fix_quality=GnssFixQuality.RTK_FIXED,
            satellites=14,
            hdop=0.8,
            ground_speed_mps=2.5,
            course_deg=90.0,
            valid=True,
            invalid_reason=None,
        ),
        trace_midpoint_utc=MID_UTC,
        age_s=0.2,
        method=GnssMatchMethod.NEAREST_MIDPOINT,
        usable_for_map=True,
        reason=None,
    )


def _metadata(
    *,
    index: int = 0,
    actual: float | None = None,
    schedule: float | None = None,
    match: GnssMatch | None = None,
    status: TraceQualityStatus = TraceQualityStatus.NOMINAL,
    reasons: tuple[TraceQualityReason, ...] = (),
) -> TraceMetadata:
    if match is None and TraceQualityReason.GNSS_MISSING not in reasons:
        status = TraceQualityStatus.DEGRADED
        reasons = (TraceQualityReason.GNSS_MISSING,)
    return TraceMetadata(
        mission_id=MISSION,
        trace_index=index,
        trace_uid=TRACE_UID,
        device_id=DEVICE,
        sweep_started_utc=START_UTC,
        sweep_midpoint_utc=MID_UTC,
        sweep_finished_utc=FINISH_UTC,
        sweep_started_monotonic_ns=MonotonicNs(1_000),
        sweep_midpoint_monotonic_ns=MonotonicNs(1_250),
        sweep_finished_monotonic_ns=MonotonicNs(1_500),
        target_interval_s=0.5,
        actual_interval_s=actual,
        schedule_error_s=schedule,
        connection_generation=2,
        raw_trace_sha256=HASH64,
        gnss_match=match,
        quality_status=status,
        quality_reasons=reasons,
    )


def test_full_metadata_json_round_trip() -> None:
    metadata = _metadata(index=1, actual=0.5, schedule=0.0, match=_good_match())
    restored = TraceMetadata.from_dict(metadata.to_dict())
    assert restored == metadata
    assert restored.gnss_match == _good_match()
    assert restored.mission_id == MISSION
    assert restored.trace_index == 1


def test_no_gnss_requires_explicit_reason() -> None:
    metadata = _metadata(match=None)
    assert metadata.gnss_match is None
    assert TraceQualityReason.GNSS_MISSING in metadata.quality_reasons
    assert metadata.quality_status is TraceQualityStatus.DEGRADED


def test_no_gnss_without_reason_is_rejected() -> None:
    with pytest.raises(DomainError) as excinfo:
        TraceMetadata(
            mission_id=MISSION,
            trace_index=0,
            trace_uid=TRACE_UID,
            device_id=DEVICE,
            sweep_started_utc=START_UTC,
            sweep_midpoint_utc=MID_UTC,
            sweep_finished_utc=FINISH_UTC,
            sweep_started_monotonic_ns=MonotonicNs(1_000),
            sweep_midpoint_monotonic_ns=MonotonicNs(1_250),
            sweep_finished_monotonic_ns=MonotonicNs(1_500),
            target_interval_s=0.5,
            actual_interval_s=None,
            schedule_error_s=None,
            connection_generation=2,
            raw_trace_sha256=HASH64,
            gnss_match=None,
            quality_status=TraceQualityStatus.NOMINAL,
            quality_reasons=(),
        )
    assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT


def test_first_trace_may_miss_intervals() -> None:
    first = _metadata(index=0, actual=None, schedule=None)
    assert first.actual_interval_s is None
    assert first.schedule_error_s is None


def test_later_traces_require_intervals() -> None:
    with pytest.raises(DomainError) as excinfo:
        _metadata(index=1, actual=None, schedule=None)
    assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT
    with pytest.raises(DomainError):
        _metadata(index=1, actual=0.5, schedule=None)


def test_sweep_time_order_is_enforced() -> None:
    with pytest.raises(DomainError):
        _fix_time_domain(
            sweep_started_utc=FINISH_UTC,
            sweep_midpoint_utc=MID_UTC,
            sweep_finished_utc=START_UTC,
        )
    with pytest.raises(DomainError):
        # UTC order OK but monotonic order broken: domains are independent.
        _fix_time_domain(
            sweep_started_monotonic_ns=MonotonicNs(1_500),
            sweep_midpoint_monotonic_ns=MonotonicNs(1_250),
            sweep_finished_monotonic_ns=MonotonicNs(1_000),
        )


def test_utc_and_monotonic_domains_are_separated() -> None:
    with pytest.raises(TypeError):
        _fix_time_domain(sweep_started_monotonic_ns=START_UTC)
    with pytest.raises(TypeError):
        _fix_time_domain(sweep_started_utc=MonotonicNs(1))


def _fix_time_domain(**overrides: object) -> TraceMetadata:
    params: dict[str, object] = {
        "mission_id": MISSION,
        "trace_index": 0,
        "trace_uid": TRACE_UID,
        "device_id": DEVICE,
        "sweep_started_utc": START_UTC,
        "sweep_midpoint_utc": MID_UTC,
        "sweep_finished_utc": FINISH_UTC,
        "sweep_started_monotonic_ns": MonotonicNs(1_000),
        "sweep_midpoint_monotonic_ns": MonotonicNs(1_250),
        "sweep_finished_monotonic_ns": MonotonicNs(1_500),
        "target_interval_s": 0.5,
        "actual_interval_s": None,
        "schedule_error_s": None,
        "connection_generation": 2,
        "raw_trace_sha256": HASH64,
        "gnss_match": None,
        "quality_status": TraceQualityStatus.DEGRADED,
        "quality_reasons": (TraceQualityReason.GNSS_MISSING,),
    }
    params.update(overrides)
    return TraceMetadata(**params)  # type: ignore[arg-type]


def test_raw_hash_field_contract() -> None:
    _metadata(match=_good_match())
    with pytest.raises(DomainError) as excinfo:
        _fix_time_domain(raw_trace_sha256="A" * 64)  # uppercase is not canonical
    assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT
    with pytest.raises(DomainError):
        _fix_time_domain(raw_trace_sha256="a" * 63)
    with pytest.raises(DomainError):
        _fix_time_domain(raw_trace_sha256="a" * 65)


def test_metadata_is_immutable() -> None:
    metadata = _metadata(match=_good_match())
    with pytest.raises(FrozenInstanceError):
        metadata.trace_index = 5  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        metadata.raw_trace_sha256 = "b" * 64  # type: ignore[misc]


def test_with_gnss_match_returns_new_object() -> None:
    no_match = _metadata(match=None)
    match = _good_match()
    attached = no_match.with_gnss_match(match)
    assert attached.gnss_match is match
    assert attached.quality_status is TraceQualityStatus.NOMINAL
    assert TraceQualityReason.GNSS_MISSING not in attached.quality_reasons
    # Original stays frozen with no GNSS.
    assert no_match.gnss_match is None
    assert TraceQualityReason.GNSS_MISSING in no_match.quality_reasons
    assert no_match.quality_status is TraceQualityStatus.DEGRADED

    detached = attached.with_gnss_match(None)
    assert detached.gnss_match is None
    assert detached.quality_status is TraceQualityStatus.DEGRADED
    assert TraceQualityReason.GNSS_MISSING in detached.quality_reasons
    # Attachment object is unchanged.
    assert attached.gnss_match is match


def test_with_data_quality_returns_new_object() -> None:
    metadata = _metadata(match=_good_match())
    updated = metadata.with_data_quality(
        TraceQualityStatus.DEGRADED,
        (TraceQualityReason.DEVICE_STATUS,),
    )
    assert updated.quality_status is TraceQualityStatus.DEGRADED
    assert updated.quality_reasons == (TraceQualityReason.DEVICE_STATUS,)
    assert metadata.quality_status is TraceQualityStatus.NOMINAL
    assert metadata.quality_reasons == ()


def test_quality_status_reason_consistency() -> None:
    with pytest.raises(DomainError):
        _metadata(match=_good_match(), status=TraceQualityStatus.DEGRADED, reasons=())
    with pytest.raises(DomainError):
        _metadata(
            match=_good_match(),
            status=TraceQualityStatus.NOMINAL,
            reasons=(TraceQualityReason.DEVICE_STATUS,),
        )
