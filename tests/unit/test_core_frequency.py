"""Tests for immutable channel and frequency-domain models (ISSUE-004)."""

from __future__ import annotations

from dataclasses import replace as dc_replace
from datetime import UTC, datetime

import numpy as np
import pytest

from uav_gpr.core import (
    ChannelSpec,
    DeviceId,
    DomainError,
    ErrorCode,
    FrequencyScan,
    FrequencySweep,
    GnssFix,
    GnssFixQuality,
    GnssMatch,
    GnssMatchMethod,
    LogicalPolarization,
    MissionId,
    MonotonicNs,
    SParameter,
    TraceMetadata,
    TraceQualityReason,
    TraceQualityStatus,
    TraceUid,
)

HZ = np.array([1.0e9, 1.1e9, 1.2e9], dtype=np.float64)

MISSION = MissionId("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
DEVICE = DeviceId("cccccccc-cccc-4ccc-8ccc-cccccccccccc")

HH_S11 = ChannelSpec(
    channel_id="hh_s11",
    logical_polarization=LogicalPolarization.HH,
    s_parameter=SParameter.S11,
    display_name="HH S11",
)
VV_S22 = ChannelSpec(
    channel_id="vv_s22",
    logical_polarization=LogicalPolarization.VV,
    s_parameter=SParameter.S22,
    display_name="VV S22",
)


def _sweep(channels: list[ChannelSpec], n_traces: int = 0) -> FrequencySweep:
    data = np.arange(len(channels) * len(HZ), dtype=np.float64).reshape(
        len(channels), len(HZ)
    ) + 1j * np.arange(len(channels) * len(HZ), dtype=np.float64).reshape(
        len(channels), len(HZ)
    )
    return FrequencySweep(channels=channels, frequencies_hz=HZ, data=data)


def test_single_channel_uses_channel_x_frequency() -> None:
    sweep = _sweep([HH_S11])
    assert sweep.data.shape == (1, len(HZ))
    assert sweep.data.dtype == np.complex128
    assert sweep.frequencies_hz.shape == (len(HZ),)
    assert sweep.frequencies_hz.dtype == np.float64
    assert sweep.channels == (HH_S11,)


def test_dual_channel_uses_same_structure() -> None:
    sweep = _sweep([HH_S11, VV_S22])
    assert sweep.data.shape == (2, len(HZ))
    assert sweep.channels == (HH_S11, VV_S22)
    # Same class as single channel; no S11-only special model.
    assert type(sweep) is FrequencySweep


def test_channel_order_is_explicit_and_preserved() -> None:
    sweep = _sweep([VV_S22, HH_S11])
    assert sweep.channels == (VV_S22, HH_S11)
    assert sweep.data[0, 0] == 0j  # first row belongs to vv_s22's first bin
    assert sweep.data[1, 0] == 3 + 3j  # second row belongs to hh_s11


def test_wrong_shape_is_rejected() -> None:
    data = np.zeros((1, 2), dtype=np.complex128)
    with pytest.raises(DomainError) as excinfo:
        FrequencySweep(channels=[HH_S11], frequencies_hz=HZ, data=data)
    assert excinfo.value.code is ErrorCode.SHAPE_MISMATCH
    with pytest.raises(DomainError) as excinfo:
        FrequencySweep(channels=[HH_S11, VV_S22], frequencies_hz=HZ, data=data)
    assert excinfo.value.code is ErrorCode.SHAPE_MISMATCH


def test_duplicate_channel_ids_are_rejected() -> None:
    with pytest.raises(DomainError) as excinfo:
        _sweep([HH_S11, HH_S11])
    assert excinfo.value.code is ErrorCode.DUPLICATE_CHANNEL
    assert excinfo.value.context["channel_id"] == "hh_s11"


def test_non_increasing_frequency_axis_is_rejected() -> None:
    bad = np.array([1.0e9, 1.2e9, 1.2e9])
    with pytest.raises(DomainError) as excinfo:
        FrequencySweep(channels=[HH_S11], frequencies_hz=bad, data=np.zeros((1, 3)))
    assert excinfo.value.code is ErrorCode.NON_INCREASING_AXIS


def test_nan_and_inf_frequency_are_rejected() -> None:
    for bad in (np.array([1.0, np.nan, 2.0]), np.array([1.0, np.inf, 2.0])):
        with pytest.raises(DomainError) as excinfo:
            FrequencySweep(channels=[HH_S11], frequencies_hz=bad, data=np.zeros((1, 3)))
        assert excinfo.value.code is ErrorCode.NON_FINITE_AXIS


def test_wrong_dtype_is_rejected() -> None:
    with pytest.raises(DomainError) as excinfo:
        FrequencySweep(
            channels=[HH_S11],
            frequencies_hz=HZ,
            data=np.array([["a", "b", "c"]], dtype=object),
        )
    assert excinfo.value.code is ErrorCode.DTYPE_MISMATCH
    with pytest.raises(DomainError) as excinfo:
        FrequencySweep(
            channels=[HH_S11],
            frequencies_hz=np.array([1.0 + 0j, 2.0 + 0j, 3.0 + 0j]),
            data=np.zeros((1, 3), dtype=np.complex128),
        )
    assert excinfo.value.code is ErrorCode.DTYPE_MISMATCH


def test_caller_mutation_after_construction_does_not_affect_model() -> None:
    original = np.zeros((1, len(HZ)), dtype=np.complex128)
    sweep = FrequencySweep(channels=[HH_S11], frequencies_hz=HZ, data=original)
    original[...] = 42 + 7j
    assert np.all(sweep.data == 0j)
    freq_source = HZ.copy()
    sweep2 = FrequencySweep(channels=[HH_S11], frequencies_hz=freq_source, data=original)
    freq_source[0] = 99.0
    assert sweep2.frequencies_hz[0] == 1.0e9


def test_property_views_cannot_be_made_writable() -> None:
    sweep = _sweep([HH_S11, VV_S22])
    for array in (sweep.data, sweep.data[0], sweep.frequencies_hz):
        with pytest.raises(ValueError):
            array.setflags(write=True)
        with pytest.raises(ValueError):
            array[0] = 0  # type: ignore[index]
    scan = FrequencyScan.from_sweeps([sweep])
    with pytest.raises(ValueError):
        scan.data.setflags(write=True)
    with pytest.raises(ValueError):
        scan.data[0, 0, 0] = 1j


def test_scan_stacks_traces_and_old_objects_stay_unchanged() -> None:
    first = _sweep([HH_S11, VV_S22])
    second = _sweep([HH_S11, VV_S22])
    third = _sweep([HH_S11, VV_S22])
    scan = FrequencyScan.from_sweeps([first, second])
    assert scan.data.shape == (2, 2, len(HZ))
    appended = scan.append(third)
    assert appended.data.shape == (3, 2, len(HZ))
    # Old objects remain unchanged.
    assert scan.data.shape == (2, 2, len(HZ))
    assert first.data.shape == (2, len(HZ))
    assert np.all(first.data == second.data)


def test_scan_rejects_inconsistent_contracts() -> None:
    first = _sweep([HH_S11, VV_S22])
    other_channels = _sweep([VV_S22, HH_S11])
    with pytest.raises(DomainError) as excinfo:
        FrequencyScan.from_sweeps([first, other_channels])
    assert excinfo.value.code is ErrorCode.CHANNEL_CONTRACT_MISMATCH
    scan = FrequencyScan.from_sweeps([first])
    other_freq = FrequencySweep(
        channels=[HH_S11, VV_S22],
        frequencies_hz=np.array([2.0e9, 2.1e9, 2.2e9]),
        data=np.zeros((2, 3), dtype=np.complex128),
    )
    with pytest.raises(DomainError) as excinfo:
        scan.append(other_freq)
    assert excinfo.value.code is ErrorCode.AXIS_MISMATCH


def test_scan_rejects_wrong_shapes_and_requires_traces() -> None:
    with pytest.raises(DomainError) as excinfo:
        FrequencyScan.from_sweeps([])
    assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT
    scan_data = np.zeros((2, 2, 3), dtype=np.complex128)
    good = FrequencyScan(channels=[HH_S11, VV_S22], frequencies_hz=HZ, data=scan_data)
    assert good.data.shape == (2, 2, 3)
    bad_2d = np.zeros((2, 3), dtype=np.complex128)
    with pytest.raises(DomainError) as excinfo:
        FrequencyScan(channels=[HH_S11, VV_S22], frequencies_hz=HZ, data=bad_2d)
    assert excinfo.value.code is ErrorCode.SHAPE_MISMATCH
    empty = np.zeros((0, 2, 3), dtype=np.complex128)
    with pytest.raises(DomainError) as excinfo:
        FrequencyScan(channels=[HH_S11, VV_S22], frequencies_hz=HZ, data=empty)
    assert excinfo.value.code is ErrorCode.SHAPE_MISMATCH


def test_empty_channels_are_rejected() -> None:
    with pytest.raises(DomainError) as excinfo:
        FrequencySweep(
            channels=[],
            frequencies_hz=HZ,
            data=np.zeros((0, len(HZ)), dtype=np.complex128),
        )
    assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT


def test_single_channel_scan_uses_same_structure() -> None:
    sweep = _sweep([HH_S11])
    scan = FrequencyScan.from_sweeps([sweep])
    assert scan.data.shape == (1, 1, len(HZ))
    assert type(scan) is FrequencyScan


def _uid(index: int) -> str:
    # Distinct canonical UUID per trace index (last group is 12 hex chars).
    return f"bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbb{index:02d}"


def _meta(index: int = 0, *, mission_id: MissionId | None = None) -> TraceMetadata:
    return TraceMetadata(
        mission_id=mission_id if mission_id is not None else MISSION,
        trace_index=index,
        trace_uid=TraceUid(_uid(index)),
        device_id=DEVICE,
        sweep_started_utc=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
        sweep_midpoint_utc=datetime(2026, 1, 1, 12, 0, 0, 250000, tzinfo=UTC),
        sweep_finished_utc=datetime(2026, 1, 1, 12, 0, 0, 500000, tzinfo=UTC),
        sweep_started_monotonic_ns=MonotonicNs(1_000),
        sweep_midpoint_monotonic_ns=MonotonicNs(1_250),
        sweep_finished_monotonic_ns=MonotonicNs(1_500),
        target_interval_s=0.5,
        actual_interval_s=None if index == 0 else 0.5,
        schedule_error_s=None if index == 0 else 0.0,
        connection_generation=2,
        raw_trace_sha256=None,
        gnss_match=None,
        quality_status=TraceQualityStatus.DEGRADED,
        quality_reasons=(TraceQualityReason.GNSS_MISSING,),
    )


def test_sweep_with_metadata_returns_new_object() -> None:
    sweep = _sweep([HH_S11])
    assert sweep.metadata is None
    metadata = _meta()
    sweep2 = sweep.with_metadata(metadata)
    assert sweep2.metadata is metadata
    assert sweep2 is not sweep
    assert sweep.metadata is None  # original unchanged


def test_sweep_with_metadata_none_on_unattached_is_noop() -> None:
    sweep = _sweep([HH_S11])
    assert sweep.with_metadata(None) is sweep


def test_sweep_with_metadata_cannot_silently_detach() -> None:
    attached = _sweep([HH_S11]).with_metadata(_meta(index=0))
    with pytest.raises(DomainError) as excinfo:
        attached.with_metadata(None)
    assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT
    assert attached.metadata is not None  # unchanged


def test_sweep_with_metadata_rejects_different_trace_identity() -> None:
    attached = _sweep([HH_S11]).with_metadata(_meta(index=0))
    with pytest.raises(DomainError) as excinfo:
        attached.with_metadata(_meta(index=1))
    assert excinfo.value.code is ErrorCode.ID_CONFLICT
    with pytest.raises(DomainError):
        attached.with_metadata(
            _meta(index=0, mission_id=MissionId("dddddddd-dddd-4ddd-8ddd-dddddddddddd"))
        )
    assert attached.metadata == _meta(index=0)  # unchanged


def test_sweep_with_metadata_allows_same_identity_evolution() -> None:
    acquired = _meta(index=0)
    attached = _sweep([HH_S11]).with_metadata(acquired)
    evolved = acquired.with_integrity("a" * 64)
    evolved_sweep = attached.with_metadata(evolved)
    assert evolved_sweep.metadata == evolved
    assert evolved_sweep is not attached
    assert attached.metadata == acquired  # original unchanged


def test_sweep_metadata_rejects_wrong_type() -> None:
    sweep = _sweep([HH_S11])
    with pytest.raises(TypeError):
        sweep.with_metadata("not a metadata")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        FrequencySweep(
            channels=[HH_S11],
            frequencies_hz=HZ,
            data=np.zeros((1, len(HZ)), dtype=np.complex128),
            metadata="not a metadata",  # type: ignore[arg-type]
        )


def test_scan_from_sweeps_preserves_per_trace_metadata() -> None:
    first = _sweep([HH_S11])
    second = _sweep([HH_S11])
    second_with_meta = second.with_metadata(_meta(index=1))
    scan = FrequencyScan.from_sweeps([first, second_with_meta])
    assert len(scan.metadata) == 2
    assert scan.metadata[0] is None
    assert scan.metadata[1] is second_with_meta.metadata
    assert scan.data.shape == (2, 1, len(HZ))
    # All-None metadata compacts to the empty tuple.
    empty_scan = FrequencyScan.from_sweeps([first, second])
    assert empty_scan.metadata == ()


def test_scan_append_keeps_metadata_association() -> None:
    first = _sweep([HH_S11])
    second = _sweep([HH_S11])
    third = _sweep([HH_S11])
    scan = FrequencyScan.from_sweeps([first, second])
    assert scan.metadata == ()
    meta2 = _meta(index=2)
    appended = scan.append(third.with_metadata(meta2))
    assert appended.data.shape == (3, 1, len(HZ))
    assert appended.metadata == (None, None, meta2)
    # Old object unchanged: still no metadata and only two traces.
    assert scan.metadata == ()
    assert scan.data.shape == (2, 1, len(HZ))


def test_scan_with_metadata_requires_full_trace_alignment() -> None:
    sweep = _sweep([HH_S11, VV_S22])
    scan = FrequencyScan.from_sweeps([sweep, sweep])
    assert scan.metadata == ()
    meta_a = _meta(index=0)
    meta_b = _meta(index=1)
    aligned = scan.with_metadata([meta_a, meta_b])
    assert aligned.metadata == (meta_a, meta_b)
    assert aligned is not scan
    with pytest.raises(DomainError) as excinfo:
        scan.with_metadata([meta_a])
    assert excinfo.value.code is ErrorCode.SHAPE_MISMATCH
    # Direct construction with a misaligned tuple is rejected too.
    with pytest.raises(DomainError) as excinfo:
        FrequencyScan(
            channels=[HH_S11, VV_S22],
            frequencies_hz=HZ,
            data=np.zeros((2, 2, len(HZ)), dtype=np.complex128),
            metadata=(meta_a,),
        )
    assert excinfo.value.code is ErrorCode.SHAPE_MISMATCH


def test_scan_with_metadata_empty_replacement_fails_closed() -> None:
    scan = FrequencyScan.from_sweeps([_sweep([HH_S11]), _sweep([HH_S11])]).with_metadata(
        [_meta(index=0), _meta(index=1)]
    )
    with pytest.raises(DomainError) as excinfo:
        scan.with_metadata([])
    assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT
    with pytest.raises(DomainError):
        scan.with_metadata([None, None])
    # Original stays attached.
    assert scan.metadata is not None and len(scan.metadata) == 2
    # Empty replacement on a scan without metadata is an explicit no-op.
    bare = FrequencyScan.from_sweeps([_sweep([HH_S11]), _sweep([HH_S11])])
    assert bare.with_metadata([]) is bare


def test_scan_with_metadata_cannot_silently_detach_one_trace() -> None:
    attached = FrequencyScan.from_sweeps(
        [_sweep([HH_S11]), _sweep([HH_S11])]
    ).with_metadata([_meta(index=0), _meta(index=1)])
    with pytest.raises(DomainError) as excinfo:
        attached.with_metadata([attached.metadata[0], None])
    assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT
    assert attached.metadata[1] is not None


def test_scan_with_metadata_rejects_different_trace_identity() -> None:
    attached = FrequencyScan.from_sweeps(
        [_sweep([HH_S11]), _sweep([HH_S11])]
    ).with_metadata([_meta(index=0), _meta(index=1)])
    evolved = attached.metadata[0].with_integrity("a" * 64)  # type: ignore[union-attr]
    evolved_scan = attached.with_metadata([evolved, attached.metadata[1]])
    assert evolved_scan.metadata[0] == evolved
    with pytest.raises(DomainError) as excinfo:
        attached.with_metadata([_meta(index=5), _meta(index=1)])
    assert excinfo.value.code is ErrorCode.ID_CONFLICT


def test_scan_rejects_duplicate_trace_uid() -> None:
    duplicate = TraceMetadata(
        mission_id=MISSION,
        trace_index=1,
        trace_uid=_meta(index=0).trace_uid,  # same uid as trace 0
        device_id=DEVICE,
        sweep_started_utc=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
        sweep_midpoint_utc=datetime(2026, 1, 1, 12, 0, 0, 250000, tzinfo=UTC),
        sweep_finished_utc=datetime(2026, 1, 1, 12, 0, 0, 500000, tzinfo=UTC),
        sweep_started_monotonic_ns=MonotonicNs(1_000),
        sweep_midpoint_monotonic_ns=MonotonicNs(1_250),
        sweep_finished_monotonic_ns=MonotonicNs(1_500),
        target_interval_s=0.5,
        actual_interval_s=0.5,
        schedule_error_s=0.0,
        connection_generation=2,
        raw_trace_sha256=None,
        gnss_match=None,
        quality_status=TraceQualityStatus.DEGRADED,
        quality_reasons=(TraceQualityReason.GNSS_MISSING,),
    )
    metas = [_meta(index=0), duplicate]
    with pytest.raises(DomainError) as excinfo:
        FrequencyScan.from_sweeps([_sweep([HH_S11]), _sweep([HH_S11])]).with_metadata(metas)
    assert excinfo.value.code is ErrorCode.ID_CONFLICT


def test_scan_rejects_mixed_missions_and_out_of_order_indices() -> None:
    other_mission = MissionId("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
    mixed = [_meta(index=0), _meta(index=1, mission_id=other_mission)]
    with pytest.raises(DomainError) as excinfo:
        FrequencyScan.from_sweeps([_sweep([HH_S11]), _sweep([HH_S11])]).with_metadata(mixed)
    assert excinfo.value.code is ErrorCode.ID_CONFLICT
    out_of_order = [_meta(index=1), _meta(index=0)]
    with pytest.raises(DomainError) as excinfo:
        FrequencyScan.from_sweeps([_sweep([HH_S11]), _sweep([HH_S11])]).with_metadata(
            out_of_order
        )
    assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT


HASH_A = "a" * 64
HASH_B = "b" * 64


def _usable_match() -> GnssMatch:
    return GnssMatch(
        fix=GnssFix(
            received_utc=datetime(2026, 1, 1, 11, 59, 30, tzinfo=UTC),
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
        trace_midpoint_utc=datetime(2026, 1, 1, 12, 0, 0, 250000, tzinfo=UTC),
        age_s=0.2,
        method=GnssMatchMethod.NEAREST_MIDPOINT,
        usable_for_map=True,
        reason=None,
    )


def test_sweep_hash_evolution_matrix() -> None:
    base = _meta(index=0)
    attached = _sweep([HH_S11]).with_metadata(base)
    # acquired None -> valid hash is allowed
    with_hash = attached.with_metadata(base.with_integrity(HASH_A))
    assert with_hash.metadata.raw_trace_sha256 == HASH_A
    # bound a... -> a... is an idempotent no-op (equal metadata -> self)
    assert with_hash.with_metadata(with_hash.metadata) is with_hash
    # bound a... -> None is rejected
    downgraded = dc_replace(base.with_integrity(HASH_A), raw_trace_sha256=None)
    with pytest.raises(DomainError) as excinfo:
        with_hash.with_metadata(downgraded)
    assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT
    # bound a... -> b... is a structured conflict
    other_hash = dc_replace(base.with_integrity(HASH_A), raw_trace_sha256=HASH_B)
    with pytest.raises(DomainError) as excinfo:
        with_hash.with_metadata(other_hash)
    assert excinfo.value.code is ErrorCode.ID_CONFLICT
    # original stays untouched
    assert with_hash.metadata == base.with_integrity(HASH_A)


def test_sweep_rejects_acquisition_fact_changes() -> None:
    base = _meta(index=0)
    attached = _sweep([HH_S11]).with_metadata(base)
    variants = [
        dc_replace(base, device_id=DeviceId("dddddddd-dddd-4ddd-8ddd-dddddddddddd")),
        dc_replace(base, sweep_started_utc=datetime(2026, 1, 1, 11, 59, 59, tzinfo=UTC)),
        dc_replace(base, sweep_finished_monotonic_ns=MonotonicNs(1_501)),
        dc_replace(base, target_interval_s=0.6),
        dc_replace(base, actual_interval_s=0.7),
        dc_replace(base, schedule_error_s=0.1),
        dc_replace(base, connection_generation=3),
    ]
    for variant in variants:
        with pytest.raises(DomainError) as excinfo:
            attached.with_metadata(variant)
        assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT
        assert "changed_fields" in excinfo.value.context
    assert attached.metadata == base


def test_sweep_allows_gnss_and_quality_evolution() -> None:
    base = _meta(index=0)
    attached = _sweep([HH_S11]).with_metadata(base)
    evolved = base.with_gnss_match(_usable_match())
    evolved = evolved.with_data_quality(
        TraceQualityStatus.DEGRADED, (TraceQualityReason.DEVICE_STATUS,)
    )
    updated = attached.with_metadata(evolved)
    assert updated.metadata is evolved
    assert attached.metadata == base


def test_scan_hash_and_fact_evolution_matrix() -> None:
    m0 = _meta(index=0)
    m1 = _meta(index=1)
    scan = FrequencyScan.from_sweeps([_sweep([HH_S11]), _sweep([HH_S11])]).with_metadata(
        [m0, m1]
    )
    # None -> hash allowed
    scan_a = scan.with_metadata([m0.with_integrity(HASH_A), m1])
    assert scan_a.metadata[0].raw_trace_sha256 == HASH_A
    # hash -> None rejected on the same entry
    with pytest.raises(DomainError) as excinfo:
        scan_a.with_metadata([dc_replace(m0.with_integrity(HASH_A), raw_trace_sha256=None), m1])
    assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT
    # hash -> different hash rejected
    with pytest.raises(DomainError) as excinfo:
        scan_a.with_metadata([dc_replace(m0.with_integrity(HASH_A), raw_trace_sha256=HASH_B), m1])
    assert excinfo.value.code is ErrorCode.ID_CONFLICT
    # acquisition fact change rejected
    with pytest.raises(DomainError) as excinfo:
        scan_a.with_metadata([m0.with_integrity(HASH_A), dc_replace(m1, connection_generation=9)])
    assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT
    # gnss/quality evolution allowed
    evolved1 = m1.with_gnss_match(_usable_match())
    updated = scan_a.with_metadata([m0.with_integrity(HASH_A), evolved1])
    assert updated.metadata[1] is evolved1


def test_scan_identical_hash_replacement_is_idempotent() -> None:
    m0 = _meta(index=0).with_integrity(HASH_A)
    m1 = _meta(index=1).with_integrity(HASH_A)
    scan = FrequencyScan.from_sweeps([_sweep([HH_S11]), _sweep([HH_S11])]).with_metadata(
        [m0, m1]
    )
    assert scan.with_metadata([m0, m1]) is scan
