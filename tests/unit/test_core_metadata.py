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
    GnssUnavailableReason,
    MissionId,
    MonotonicNs,
    TraceMetadata,
    TraceQualityReason,
    TraceQualityStatus,
    TraceUid,
    to_utc_iso,
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
    hash_value: str | None = HASH64,
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
        raw_trace_sha256=hash_value,
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
    with pytest.raises(TypeError):
        _fix_time_domain(raw_trace_sha256=12345)  # type: ignore[arg-type]


def test_acquired_state_allows_no_hash_and_round_trips() -> None:
    acquired = _metadata(hash_value=None)
    assert acquired.raw_trace_sha256 is None
    assert TraceMetadata.from_dict(acquired.to_dict()) == acquired
    payload = acquired.to_dict()
    assert payload["raw_trace_sha256"] is None


def test_with_integrity_first_attach_returns_new_object() -> None:
    acquired = _metadata(hash_value=None)
    attached = acquired.with_integrity(HASH64)
    assert attached.raw_trace_sha256 == HASH64
    assert attached is not acquired
    assert acquired.raw_trace_sha256 is None  # original stays acquired


def test_with_integrity_identical_hash_is_explicit_noop() -> None:
    attached = _metadata(hash_value=HASH64)
    again = attached.with_integrity(HASH64)
    assert again is attached


def test_with_integrity_conflicting_hash_fails_closed() -> None:
    attached = _metadata(hash_value=HASH64)
    with pytest.raises(DomainError) as excinfo:
        attached.with_integrity("b" * 64)
    assert excinfo.value.code is ErrorCode.ID_CONFLICT
    assert excinfo.value.context["stored_hash"] == HASH64
    assert excinfo.value.context["incoming_hash"] == "b" * 64
    assert attached.raw_trace_sha256 == HASH64  # unchanged


def test_with_integrity_rejects_non_canonical_hash() -> None:
    acquired = _metadata(hash_value=None)
    with pytest.raises(DomainError) as excinfo:
        acquired.with_integrity("A" * 64)
    assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT
    assert acquired.raw_trace_sha256 is None


def test_gnss_match_midpoint_must_equal_sweep_midpoint() -> None:
    wrong_match = GnssMatch(
        fix=_good_match().fix,
        trace_midpoint_utc=FINISH_UTC,  # differs from the sweep midpoint
        age_s=0.3,
        method=GnssMatchMethod.NEAREST_MIDPOINT,
        usable_for_map=True,
        reason=None,
    )
    with pytest.raises(DomainError) as excinfo:
        _metadata(match=wrong_match)
    assert excinfo.value.code is ErrorCode.GNSS_MIDPOINT_MISMATCH
    assert excinfo.value.context["sweep_midpoint_utc"] == to_utc_iso(MID_UTC)
    assert excinfo.value.context["match_midpoint_utc"] == to_utc_iso(FINISH_UTC)


def test_unusable_gnss_match_cannot_be_summarized_as_nominal() -> None:
    stale_match = GnssMatch(
        fix=_good_match().fix,
        trace_midpoint_utc=MID_UTC,
        age_s=7.0,
        method=GnssMatchMethod.NEAREST_MIDPOINT,
        usable_for_map=False,
        reason=GnssUnavailableReason.STALE,
    )
    # Missing the required trace reason -> rejected.
    with pytest.raises(DomainError) as excinfo:
        _metadata(match=stale_match)
    assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT
    # With the matching reason, the status can never be nominal.
    degraded = _metadata(
        match=stale_match,
        status=TraceQualityStatus.DEGRADED,
        reasons=(TraceQualityReason.GNSS_STALE,),
    )
    assert degraded.quality_status is TraceQualityStatus.DEGRADED
    with pytest.raises(DomainError):
        _metadata(
            match=stale_match,
            status=TraceQualityStatus.NOMINAL,
            reasons=(TraceQualityReason.GNSS_STALE,),
        )


def test_with_gnss_match_manages_unusable_reason_automatically() -> None:
    no_match = _metadata(match=None)
    stale_match = GnssMatch(
        fix=_good_match().fix,
        trace_midpoint_utc=MID_UTC,
        age_s=7.0,
        method=GnssMatchMethod.NEAREST_MIDPOINT,
        usable_for_map=False,
        reason=GnssUnavailableReason.STALE,
    )
    attached = no_match.with_gnss_match(stale_match)
    assert attached.gnss_match is stale_match
    assert TraceQualityReason.GNSS_STALE in attached.quality_reasons
    assert TraceQualityReason.GNSS_MISSING not in attached.quality_reasons
    assert attached.quality_status is not TraceQualityStatus.NOMINAL
    # Detach restores gnss_missing.
    detached = attached.with_gnss_match(None)
    assert TraceQualityReason.GNSS_MISSING in detached.quality_reasons
    assert TraceQualityReason.GNSS_STALE not in detached.quality_reasons


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


def test_with_integrity_rejects_none_and_non_string() -> None:
    acquired = _metadata(hash_value=None)
    with pytest.raises(TypeError, match="must be a str"):
        acquired.with_integrity(None)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="must be a str"):
        acquired.with_integrity(12345)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="must be a str"):
        attached = _metadata(hash_value=HASH64)
        attached.with_integrity(b"a" * 64)  # type: ignore[arg-type]
    # Originals are unchanged by the rejected calls.
    assert acquired.raw_trace_sha256 is None


def _stale_match() -> GnssMatch:
    return GnssMatch(
        fix=_good_match().fix,
        trace_midpoint_utc=MID_UTC,
        age_s=7.0,
        method=GnssMatchMethod.NEAREST_MIDPOINT,
        usable_for_map=False,
        reason=GnssUnavailableReason.STALE,
    )


def test_usable_match_forbids_all_gnss_quality_reasons() -> None:
    for reason in (
        TraceQualityReason.GNSS_NO_FIX,
        TraceQualityReason.GNSS_STALE,
        TraceQualityReason.GNSS_INVALID,
    ):
        with pytest.raises(DomainError):
            _metadata(
                match=_good_match(),
                status=TraceQualityStatus.DEGRADED,
                reasons=(reason,),
            )
    with pytest.raises(DomainError):
        _metadata(
            match=_good_match(),
            status=TraceQualityStatus.DEGRADED,
            reasons=(TraceQualityReason.GNSS_MISSING,),
        )


def test_unusable_match_forbids_mismatched_gnss_reasons() -> None:
    stale = _stale_match()
    with pytest.raises(DomainError):
        _metadata(
            match=stale,
            status=TraceQualityStatus.DEGRADED,
            reasons=(TraceQualityReason.GNSS_INVALID,),
        )
    with pytest.raises(DomainError):
        _metadata(
            match=stale,
            status=TraceQualityStatus.DEGRADED,
            reasons=(TraceQualityReason.GNSS_STALE, TraceQualityReason.GNSS_NO_FIX),
        )
    ok = _metadata(
        match=stale,
        status=TraceQualityStatus.DEGRADED,
        reasons=(TraceQualityReason.GNSS_STALE, TraceQualityReason.DEVICE_STATUS),
    )
    assert TraceQualityReason.GNSS_STALE in ok.quality_reasons


def test_from_dict_rejects_contradicted_gnss_quality_reasons() -> None:
    good = _metadata(match=_good_match())
    payload = good.to_dict()
    payload["quality_reasons"] = [TraceQualityReason.GNSS_STALE.value]
    with pytest.raises(DomainError):
        TraceMetadata.from_dict(payload)
    stale = _metadata(
        match=_stale_match(),
        status=TraceQualityStatus.DEGRADED,
        reasons=(TraceQualityReason.GNSS_STALE,),
    )
    stale_payload = stale.to_dict()
    stale_payload["quality_reasons"] = []
    with pytest.raises(DomainError):
        TraceMetadata.from_dict(stale_payload)


def test_with_data_quality_enforces_bidirectional_consistency() -> None:
    usable_meta = _metadata(match=_good_match())
    with pytest.raises(DomainError):
        usable_meta.with_data_quality(
            TraceQualityStatus.DEGRADED, (TraceQualityReason.GNSS_STALE,)
        )
    stale_meta = _metadata(
        match=_stale_match(),
        status=TraceQualityStatus.DEGRADED,
        reasons=(TraceQualityReason.GNSS_STALE,),
    )
    with pytest.raises(DomainError):
        stale_meta.with_data_quality(
            TraceQualityStatus.DEGRADED, (TraceQualityReason.GNSS_INVALID,)
        )
    updated = stale_meta.with_data_quality(
        TraceQualityStatus.DEGRADED,
        (TraceQualityReason.GNSS_STALE, TraceQualityReason.DEVICE_STATUS),
    )
    assert updated.quality_reasons == (
        TraceQualityReason.GNSS_STALE,
        TraceQualityReason.DEVICE_STATUS,
    )
    assert stale_meta.quality_reasons == (TraceQualityReason.GNSS_STALE,)


def test_no_gnss_match_forbids_other_gnss_reasons() -> None:
    for bad in (
        TraceQualityReason.GNSS_NO_FIX,
        TraceQualityReason.GNSS_STALE,
        TraceQualityReason.GNSS_INVALID,
    ):
        with pytest.raises(DomainError) as excinfo:
            _metadata(
                match=None,
                status=TraceQualityStatus.DEGRADED,
                reasons=(TraceQualityReason.GNSS_MISSING, bad),
            )
        assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT
        assert "forbidden_reasons" in excinfo.value.context


def test_no_gnss_match_allows_non_gnss_reasons() -> None:
    metadata = _metadata(
        match=None,
        status=TraceQualityStatus.DEGRADED,
        reasons=(TraceQualityReason.GNSS_MISSING, TraceQualityReason.DEVICE_STATUS),
    )
    assert set(metadata.quality_reasons) == {
        TraceQualityReason.GNSS_MISSING,
        TraceQualityReason.DEVICE_STATUS,
    }


def test_no_gnss_match_rule_cannot_be_bypassed_by_from_dict() -> None:
    metadata = _metadata(match=None)
    payload = metadata.to_dict()
    payload["quality_reasons"] = [
        TraceQualityReason.GNSS_MISSING.value,
        TraceQualityReason.GNSS_STALE.value,
    ]
    with pytest.raises(DomainError) as excinfo:
        TraceMetadata.from_dict(payload)
    assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT


def test_no_gnss_match_rule_cannot_be_bypassed_by_with_data_quality() -> None:
    metadata = _metadata(match=None)
    with pytest.raises(DomainError) as excinfo:
        metadata.with_data_quality(
            TraceQualityStatus.DEGRADED,
            (TraceQualityReason.GNSS_MISSING, TraceQualityReason.GNSS_STALE),
        )
    assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT
