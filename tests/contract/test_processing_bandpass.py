"""Contract tests for the ISSUE-030 ProcessingStage framework and sin^2 frequency bandpass.

Pure deterministic tests: synthetic fixed-formula inputs only - no hardware,
no threads, no sleeps, no scipy at runtime.

Contract summary (docs/issues/M06_CALIBRATION_PROCESSING.md ISSUE-030,
docs/PROCESSING.md sections 1-3, docs/DATA_MODEL.md section 8,
docs/reports/ISSUE_030_BASELINE_CONFIRMATION.md section 3 and
docs/plans/2026-09-05-issue-030-bandpass.md D1-D9):

- ``ProcessingStage`` is a runtime-checkable protocol (stable stage_name /
  stage_version / input_domain / output_domain / apply); ``BandpassStage``
  implements it over the frozen ISSUE-007 ``ProcessingRecord`` /
  ``ProcessingHistory`` (no parallel history type, core/** untouched);
- migrated window math is byte-for-byte the rebar-inspector contract
  (processing/bandpass.py SHA-256
  3ee559e33e95c71702b04fe19eb9a24d2f676206d0b5471ec1e5038e17c38d51, verified
  against docs/reference-baselines/manifest.md in t1): four Hz edges with
  0 <= f1 < f2 <= f3 < f4, sin^2 raised/falling skirts, unit passband;
- golden literals below were produced on 2026-09-05 by re-evaluating that
  reference formula verbatim in this venv (plan section 7 M1/M6); an
  independent 4th-order Butterworth response was pinned alongside to prove
  the bandpass has no implicit coupling to other filters or IFFT;
- domain checks fail closed: illegal input domains (frequency_filtered /
  time_base / time_processed) raise PROCESSING_DOMAIN_MISMATCH, non-disjoint
  edge bands raise OUT_OF_RANGE, malformed edges raise INVALID_ARGUMENT,
  duplicate stage application is rejected by the history chain (a new
  stage_version does not bypass it);
- raw inputs are never mutated: returned arrays are fresh core-owned
  write-protected snapshots; sweeps stay sweeps, scans stay scans.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from uav_gpr.core import (
    BackgroundReferenceId,
    CalibrationProfileId,
    ChannelSpec,
    DataDomain,
    DeviceId,
    DomainError,
    ErrorCode,
    FrequencyScan,
    FrequencySweep,
    LogicalPolarization,
    ManualClock,
    MissionId,
    MonotonicNs,
    ProcessingHistory,
    ProcessingRecord,
    SParameter,
    TraceQualityReason,
    TraceQualityStatus,
    TraceUid,
)
from uav_gpr.processing.bandpass import (
    BANDPASS_STAGE_NAME,
    DEFAULT_BANDPASS_EDGES_HZ,
    BandpassStage,
    ProcessingStage,
    StageResult,
    build_bandpass_window,
)

# ---------------------------------------------------------------- fixtures

CREATED_UTC = datetime(2026, 1, 1, tzinfo=UTC)
MISSION_ID = MissionId(uuid.UUID(int=1))
DEVICE_ID = DeviceId(uuid.UUID(int=2))

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

# Six-point axis spanning stop/rising/passband/falling edges of the default
# (0.5, 1.0, 1.5, 2.5) GHz window.
GOLDEN_AXIS_HZ = np.array([0.4e9, 0.6e9, 0.75e9, 1.0e9, 1.8e9, 2.5e9])

# Golden window values: re-evaluated from the frozen reference formula
# (rebar-inspector processing/bandpass.py, SHA-256 3ee559e3...c38d51) on
# GOLDEN_AXIS_HZ on 2026-09-05.
GOLDEN_WINDOW = [
    0.0,
    0.09549150281252627,
    0.5000000000000001,
    1.0,
    0.7938926261462365,
    0.0,
]

# Independent third-party check (NOT the contract): a 4th-order Butterworth
# bandpass normalized onto the same axis differs from the sin^2 window by
# ~1.0 max.  Pinned here to prove the migrated math is the sin^2 window and
# nothing equivalent to another filter family (acceptance: no implicit
# coupling).  Produced once with scipy.signal in the project venv.
THIRD_PARTY_BUTTERWORTH = [
    0.000302088295005909,
    0.0015783389434194958,
    0.0039799426743229145,
    0.013505676488080764,
    0.20634264445961567,
    0.8304580847056163,
]


def _golden_input(channel_count: int) -> np.ndarray:
    """Deterministic complex input built without any RNG."""
    index = np.arange(GOLDEN_AXIS_HZ.size)
    row0 = (index + 1).astype(float) + 1j * (np.cos(index * 0.7) * 2.0)
    if channel_count == 1:
        return np.stack([row0])
    row1 = (-0.5 * index).astype(float) + 1j * (1.0 + 0.25 * index * index)
    return np.stack([row0, row1])


# Golden outputs (_golden_input(n) * GOLDEN_WINDOW), same provenance.
GOLDEN_OUT_CH0 = [
    complex(0.0, 0.0),
    complex(0.19098300562505255, 0.14607185975643097),
    complex(1.5000000000000004, 0.16996714290024106),
    complex(4.0, -1.0096922091997143),
    complex(3.9694631307311825, -1.4960467368941897),
    complex(0.0, 0.0),
]
GOLDEN_OUT_CH1 = [
    complex(0.0, 0.0),
    complex(-0.04774575140626314, 0.11936437851565784),
    complex(-0.5000000000000001, 1.0000000000000002),
    complex(-1.5, 3.25),
    complex(-1.587785252292473, 3.9694631307311825),
    complex(-0.0, 0.0),
]


def _sweep(channels: tuple[ChannelSpec, ...], data: np.ndarray) -> FrequencySweep:
    return FrequencySweep(
        channels=channels, frequencies_hz=GOLDEN_AXIS_HZ, data=data
    )


def _scan(channels: tuple[ChannelSpec, ...], data: np.ndarray) -> FrequencyScan:
    return FrequencyScan(
        channels=channels, frequencies_hz=GOLDEN_AXIS_HZ, data=data
    )


def _record(
    *,
    stage: str = "osl_calibration",
    version: str = "1.0",
    input_domain: DataDomain,
    output_domain: DataDomain,
    parameters: dict[str, object] | None = None,
    calibration_id: CalibrationProfileId | None = None,
    background_id: BackgroundReferenceId | None = None,
) -> ProcessingRecord:
    return ProcessingRecord(
        stage_name=stage,
        stage_version=version,
        parameters=parameters or {"mode": "default"},
        input_domain=input_domain,
        output_domain=output_domain,
        executed_utc=CREATED_UTC,
        software_version="0.1.0.dev0",
        calibration_profile_id=calibration_id,
        background_reference_id=background_id,
    )


RAW = DataDomain.FREQUENCY_RAW
CALIBRATED = DataDomain.FREQUENCY_CALIBRATED
BACKGROUND_APPLIED = DataDomain.FREQUENCY_BACKGROUND_APPLIED
FILTERED = DataDomain.FREQUENCY_FILTERED
TIME_BASE = DataDomain.TIME_BASE

PID = CalibrationProfileId(uuid.UUID(int=7))
BGID = BackgroundReferenceId(uuid.UUID(int=8))


def _history(*records: ProcessingRecord) -> ProcessingHistory:
    return ProcessingHistory(records)


def _raw_history() -> ProcessingHistory:
    return _history()


def _calibrated_history() -> ProcessingHistory:
    return _history(
        _record(
            input_domain=RAW,
            output_domain=CALIBRATED,
            calibration_id=PID,
        )
    )


def _background_history() -> ProcessingHistory:
    return _history(
        _record(input_domain=RAW, output_domain=CALIBRATED, calibration_id=PID),
        _record(
            input_domain=CALIBRATED,
            output_domain=BACKGROUND_APPLIED,
            stage="air_background_subtraction",
            background_id=BGID,
        ),
    )


# ---------------------------------------------------------------- protocol


def test_bandpass_stage_satisfies_processing_stage_protocol() -> None:
    stage = BandpassStage()
    assert isinstance(stage, ProcessingStage)
    assert stage.stage_name == BANDPASS_STAGE_NAME == "frequency_bandpass"
    assert stage.stage_version == "1.0"
    assert stage.output_domain is FILTERED
    assert RAW in stage.input_domain
    assert CALIBRATED in stage.input_domain
    assert BACKGROUND_APPLIED in stage.input_domain
    assert FILTERED not in stage.input_domain
    assert TIME_BASE not in stage.input_domain


def test_default_edges_are_the_rebar_four_frequencies_in_hz() -> None:
    assert DEFAULT_BANDPASS_EDGES_HZ == (0.5e9, 1.0e9, 1.5e9, 2.5e9)
    assert BandpassStage().edges_hz == DEFAULT_BANDPASS_EDGES_HZ


# ---------------------------------------------------------------- window math


def test_build_window_matches_golden_reference_values() -> None:
    window = build_bandpass_window(GOLDEN_AXIS_HZ)
    assert window.dtype == np.float64
    assert window.shape == GOLDEN_AXIS_HZ.shape
    assert window.tolist() == GOLDEN_WINDOW


def test_build_window_boundaries_and_shapes() -> None:
    f1, f2, f3, f4 = DEFAULT_BANDPASS_EDGES_HZ
    axis = np.array([f1, 0.75e9, f2, 1.2e9, f3, 2.0e9, f4])
    window = build_bandpass_window(axis)
    # exactly zero at and below f1 / above f4, unit across the passband
    assert window[0] == 0.0
    assert window[-1] == 0.0
    assert window[2] == 1.0
    assert window[3] == 1.0
    assert window[4] == 1.0
    assert window[1] == pytest.approx(np.sin(0.5 * np.pi * 0.5) ** 2)
    # falling skirt at 2.0 GHz sits halfway between f3 and f4: sin^2(pi/4) = 0.5
    assert window[5] == pytest.approx(np.sin(0.5 * np.pi * (f4 - 2.0e9) / (f4 - f3)) ** 2)
    # custom but legal edges (f2 == f3 collapses the passband, still legal)
    collapsed = build_bandpass_window(axis, (0.5e9, 1.0e9, 1.0e9, 2.5e9))
    assert collapsed.shape == axis.shape
    assert collapsed[2] == 1.0  # f == f2 == f3 hits both skirts' endpoints


def test_window_differs_from_third_party_filter_family() -> None:
    # Acceptance "no implicit coupling": the migrated response is the sin^2
    # window, demonstrably not some Butterworth-style response and not any
    # time-domain operation (this module imports no FFT symbols at all).
    window = build_bandpass_window(GOLDEN_AXIS_HZ)
    assert np.max(np.abs(window - np.array(THIRD_PARTY_BUTTERWORTH))) > 0.5


@pytest.mark.parametrize(
    "bad_edges",
    [
        (0.5e9, 1.0e9, 1.5e9),                    # too few
        (0.5e9, 1.0e9, 1.5e9, 2.5e9, 3.0e9),      # too many
        (1.0e9, 0.5e9, 1.5e9, 2.5e9),             # unordered
        (0.5e9, 1.0e9, 1.0e9, 0.5e9),             # inverted tail
        (-0.5e9, 1.0e9, 1.5e9, 2.5e9),            # negative
        (0.5e9, np.nan, 1.5e9, 2.5e9),            # NaN
        (0.5e9, 1.0e9, 1.5e9, np.inf),            # inf
        (True, 1.0e9, 1.5e9, 2.5e9),              # bool is not a frequency
        ("0.5e9", 1.0e9, 1.5e9, 2.5e9),           # string
        (None, 1.0e9, 1.5e9, 2.5e9),              # None
        (0.5e9, 1.0e9, 1.5e9, 1.0e9),             # f4 <= f3
    ],
)
def test_malformed_edges_fail_closed(bad_edges: object) -> None:
    with pytest.raises(DomainError) as excinfo:
        BandpassStage(bad_edges)  # type: ignore[arg-type]
    assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT
    assert excinfo.value.context["edges_hz"] is not None
    with pytest.raises((DomainError, TypeError, ValueError)):
        build_bandpass_window(GOLDEN_AXIS_HZ, bad_edges)  # type: ignore[arg-type]


def test_disjoint_band_is_rejected() -> None:
    sweep = _sweep((HH_S11,), _golden_input(1))
    # entirely above the acquisition band
    high = BandpassStage((5.0e9, 6.0e9, 7.0e9, 8.0e9))
    with pytest.raises(DomainError) as excinfo:
        high.apply(sweep, history=_raw_history())
    assert excinfo.value.code is ErrorCode.OUT_OF_RANGE
    # entirely below the acquisition band must be refused too
    low = BandpassStage((0.05e9, 0.1e9, 0.15e9, 0.2e9))
    with pytest.raises(DomainError) as excinfo:
        low.apply(sweep, history=_raw_history())
    assert excinfo.value.code is ErrorCode.OUT_OF_RANGE
    # partial overlap stays legal (reference semantics preserved)
    partial = BandpassStage((0.9e9, 1.0e9, 1.1e9, 5.0e9))
    result = partial.apply(sweep, history=_raw_history())
    assert result.source.data.shape == sweep.data.shape


# ---------------------------------------------------------------- happy paths


def test_apply_single_channel_sweep_produces_new_object_and_record() -> None:
    data = _golden_input(1)
    sweep = _sweep((HH_S11,), data)
    clock = ManualClock(CREATED_UTC)
    stage = BandpassStage()
    result = stage.apply(sweep, history=_raw_history(), clock=clock)

    assert isinstance(result, StageResult)
    assert isinstance(result.source, FrequencySweep)
    assert result.source is not sweep
    assert result.domain is FILTERED
    assert result.history is not None and len(result.history) == 1
    record = result.history.records[0]
    assert record.stage_name == "frequency_bandpass"
    assert record.stage_version == "1.0"
    assert record.input_domain is RAW
    assert record.output_domain is FILTERED
    assert record.software_version == "0.1.0.dev0"
    assert record.executed_utc == CREATED_UTC
    assert record.parameters == {
        "edges_hz": [0.5e9, 1.0e9, 1.5e9, 2.5e9],
        "window": "sin_squared",
    }
    assert all(isinstance(v, float) for v in record.parameters["edges_hz"])  # type: ignore[index]
    assert record.calibration_profile_id is None
    assert record.background_reference_id is None

    out = result.source.data
    assert out.dtype == np.complex128
    assert not out.flags.writeable
    expected = np.array(GOLDEN_OUT_CH0)
    np.testing.assert_array_equal(out[0], expected)


def test_apply_two_channel_scan_keeps_shape_and_metadata() -> None:
    channels = (HH_S11, VV_S22)
    data = _golden_input(2)
    scan = _scan(channels, data[np.newaxis, :, :])
    stage = BandpassStage()
    result = stage.apply(scan, history=_calibrated_history(), clock=ManualClock(CREATED_UTC))

    assert isinstance(result.source, FrequencyScan)
    assert result.source.channels == channels
    assert result.source.data.shape == (1, 2, GOLDEN_AXIS_HZ.size)
    expected = np.stack(
        [
            np.array(GOLDEN_OUT_CH0),
            np.array(GOLDEN_OUT_CH1),
        ]
    )
    np.testing.assert_array_equal(result.source.data[0], expected)
    record = result.history.records[-1]  # type: ignore[union-attr]
    assert record.input_domain is CALIBRATED
    assert record.output_domain is FILTERED


def test_per_channel_consistency_between_sweep_and_scan() -> None:
    stage = BandpassStage((0.4e9, 0.8e9, 1.4e9, 2.4e9))
    data = _golden_input(2)
    per_sweep = [
        stage.apply(
            _sweep((channel,), data[c : c + 1]),
            history=_raw_history(),
            clock=ManualClock(CREATED_UTC),
        ).source.data
        for c, channel in enumerate((HH_S11, VV_S22))
    ]
    scan_result = stage.apply(
        _scan((HH_S11, VV_S22), data[np.newaxis, :, :]),
        history=_raw_history(),
        clock=ManualClock(CREATED_UTC),
    )
    combined = np.concatenate(per_sweep, axis=0)
    np.testing.assert_array_equal(scan_result.source.data[0], combined)


def test_background_applied_history_chains_legally() -> None:
    stage = BandpassStage()
    result = stage.apply(
        _sweep((HH_S11,), _golden_input(1)),
        history=_background_history(),
        clock=ManualClock(CREATED_UTC),
    )
    assert result.history is not None
    assert [r.stage_name for r in result.history.records] == [
        "osl_calibration",
        "air_background_subtraction",
        "frequency_bandpass",
    ]
    assert result.history.records[-1].input_domain is BACKGROUND_APPLIED


def test_metadata_passthrough_on_scan() -> None:
    base = CREATED_UTC + timedelta(seconds=1)
    metadata = _metadata(0, base)
    scan = FrequencyScan(
        channels=(HH_S11,),
        frequencies_hz=GOLDEN_AXIS_HZ,
        data=_golden_input(1)[np.newaxis, :, :],
        metadata=(metadata,),
    )
    result = BandpassStage().apply(
        scan, history=_raw_history(), clock=ManualClock(CREATED_UTC)
    )
    assert result.source.metadata == (metadata,)


def _metadata(index: int, started: datetime):  # type: ignore[no-untyped-def]
    from uav_gpr.core import TraceMetadata

    monotonic = 1_000_000_000 * (index + 1)
    return TraceMetadata(
        mission_id=MISSION_ID,
        trace_index=index,
        trace_uid=TraceUid(uuid.UUID(int=100 + index)),
        device_id=DEVICE_ID,
        sweep_started_utc=started,
        sweep_midpoint_utc=started + timedelta(milliseconds=50),
        sweep_finished_utc=started + timedelta(milliseconds=100),
        sweep_started_monotonic_ns=MonotonicNs(monotonic),
        sweep_midpoint_monotonic_ns=MonotonicNs(monotonic + 50_000_000),
        sweep_finished_monotonic_ns=MonotonicNs(monotonic + 100_000_000),
        target_interval_s=0.1,
        actual_interval_s=None if index == 0 else 0.1,
        schedule_error_s=None if index == 0 else 0.001,
        connection_generation=1,
        raw_trace_sha256=None,
        gnss_match=None,
        quality_status=TraceQualityStatus.DEGRADED,
        quality_reasons=(TraceQualityReason.GNSS_MISSING,),
    )


# ---------------------------------------------------------------- domain gates


def test_illegal_input_domains_rejected() -> None:
    sweep = _sweep((HH_S11,), _golden_input(1))
    # history ending in frequency_filtered: re-filtering is forbidden
    filtered_history = _history(
        _record(input_domain=RAW, output_domain=FILTERED, stage="frequency_bandpass")
    )
    with pytest.raises(DomainError) as excinfo:
        BandpassStage().apply(sweep, history=filtered_history)
    assert excinfo.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH
    assert excinfo.value.context["input_domain"] == "frequency_filtered"

    # history ending in a time domain: cannot feed a frequency stage
    time_history = _history(
        _record(input_domain=RAW, output_domain=TIME_BASE, stage="ifft")
    )
    with pytest.raises(DomainError) as excinfo:
        BandpassStage().apply(sweep, history=time_history)
    assert excinfo.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH


def test_duplicate_stage_rejected_even_with_new_version() -> None:
    sweep = _sweep((HH_S11,), _golden_input(1))
    first = BandpassStage().apply(sweep, history=_raw_history())
    assert first.history is not None
    # second application on the resulting history fails closed at the stage's
    # own domain gate (a filtered input is not a legal predecessor) and would
    # additionally hit history uniqueness if that gate were bypassed
    with pytest.raises(DomainError) as excinfo:
        BandpassStage().apply(sweep, history=first.history)
    assert excinfo.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH
    # repeating the stage name inside one history is refused even with a
    # bumped version token.  No chain-valid pair exists today that lets two
    # bandpass records reach uniqueness (every second filtered hop violates
    # legality first), so pin the ordering contract: duplicate names on a
    # raw-starting pair fail closed, and any bypass of the domain gate still
    # hits INVALID_ARGUMENT uniqueness inside ProcessingHistory construction.
    with pytest.raises(DomainError):
        ProcessingHistory(
            (
                _record(input_domain=RAW, output_domain=CALIBRATED, calibration_id=PID),
                _record(
                    input_domain=CALIBRATED,
                    output_domain=BACKGROUND_APPLIED,
                    stage="air_background_subtraction",
                    background_id=BGID,
                ),
                _record(
                    input_domain=BACKGROUND_APPLIED,
                    output_domain=FILTERED,
                    stage="frequency_bandpass",
                ),
                _record(
                    input_domain=BACKGROUND_APPLIED,
                    output_domain=FILTERED,
                    stage="frequency_bandpass",
                    version="9.9",
                ),
            )
        )
    import uav_gpr.core.time_domain as td

    original_transition = td._validate_transition
    original_pairwise = td.pairwise
    try:
        # simulate a buggy relaxation of the transition gate plus a broken
        # chain (raw -> raw second hop); the remaining per-history stage
        # uniqueness rule must still refuse the repeated name — a version
        # bump is no bypass
        td._validate_transition = lambda record: None
        td.pairwise = lambda records: ()
        with pytest.raises(DomainError) as excinfo:
            ProcessingHistory(
                (
                    _record(
                        input_domain=RAW,
                        output_domain=FILTERED,
                        stage="frequency_bandpass",
                    ),
                    _record(
                        input_domain=RAW,
                        output_domain=FILTERED,
                        stage="frequency_bandpass",
                        version="9.9",
                    ),
                )
            )
        assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT
        assert excinfo.value.context["stage_name"] == "frequency_bandpass"
        assert excinfo.value.context["stage_version"] == "9.9"
    finally:
        td._validate_transition = original_transition
        td.pairwise = original_pairwise


def test_unsupported_source_type_rejected() -> None:
    with pytest.raises(TypeError):
        BandpassStage().apply(object(), history=_raw_history())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        BandpassStage().apply(np.zeros(4, dtype=np.complex128), history=_raw_history())


# ---------------------------------------------------------------- raw immutability


def test_raw_input_never_mutated_and_output_is_fresh() -> None:
    data = _golden_input(1)
    sweep = _sweep((HH_S11,), data)
    before = sweep.data.copy(order="C")
    result = BandpassStage().apply(sweep, history=_raw_history())
    # identity of the caller's model object unchanged, contents bit-identical
    np.testing.assert_array_equal(sweep.data, before)
    assert sweep.data.flags.writeable is False
    result_data = result.source.data
    assert result_data.flags.writeable is False
    # output buffer shares no memory with the input
    assert not np.may_share_memory(result_data, sweep.data)
    # attempts to mutate through any view fail
    with pytest.raises(ValueError):
        result_data.setflags(write=True)


def test_history_argument_never_mutated() -> None:
    history = _calibrated_history()
    original_records = history.records
    BandpassStage().apply(
        _sweep((HH_S11,), _golden_input(1)), history=history
    )
    assert history.records == original_records
    assert len(history) == 1


# ---------------------------------------------------------------- serialization


def test_record_round_trips_through_dict() -> None:
    result = BandpassStage((0.5e9, 1.0e9, 1.5e9, 2.5e9)).apply(
        _sweep((HH_S11,), _golden_input(1)),
        history=_raw_history(),
        clock=ManualClock(CREATED_UTC),
    )
    record = result.history.records[0]  # type: ignore[union-attr]
    payload = record.to_dict()
    assert payload["stage_name"] == "frequency_bandpass"
    assert payload["input_domain"] == "frequency_raw"
    assert payload["output_domain"] == "frequency_filtered"
    assert payload["executed_utc"] == "2026-01-01T00:00:00.000000Z"
    restored = ProcessingRecord.from_dict(payload)
    assert restored == record
    canonical = record.parameters_canonical_json()
    expected_canonical = (
        '{"edges_hz":[500000000.0,1000000000.0,1500000000.0,2500000000.0],'
        '"window":"sin_squared"}'
    )
    assert canonical == expected_canonical


def test_parameters_survive_caller_mutation() -> None:
    edges = [0.5e9, 1.0e9, 1.5e9, 2.5e9]
    stage = BandpassStage(edges)
    edges[0] = 9.9e9  # mutating the caller list must not leak into the stage
    assert stage.edges_hz == (0.5e9, 1.0e9, 1.5e9, 2.5e9)
    result = stage.apply(
        _sweep((HH_S11,), _golden_input(1)), history=_raw_history()
    )
    assert result.history.records[0].parameters["edges_hz"] == [  # type: ignore[union-attr,index]
        0.5e9,
        1.0e9,
        1.5e9,
        2.5e9,
    ]


# ---------------------------------------------------------------- clock policy


def test_executed_utc_defaults_to_injected_clock() -> None:
    stamp = datetime(2026, 5, 4, 3, 2, 1, tzinfo=UTC)
    result = BandpassStage().apply(
        _sweep((HH_S11,), _golden_input(1)),
        history=_raw_history(),
        clock=ManualClock(stamp),
    )
    assert result.history.records[0].executed_utc == stamp  # type: ignore[union-attr]
    # default SystemClock path yields an aware UTC datetime, no sleeping needed
    live = BandpassStage().apply(_sweep((HH_S11,), _golden_input(1)), history=_raw_history())
    executed = live.history.records[0].executed_utc  # type: ignore[union-attr]
    assert executed.tzinfo is not None and executed.utcoffset() == timedelta(0)
    assert executed >= CREATED_UTC


def test_naive_executed_utc_rejected() -> None:
    naive = datetime(2026, 1, 1)
    with pytest.raises(DomainError) as excinfo:
        BandpassStage().apply(
            _sweep((HH_S11,), _golden_input(1)),
            history=_raw_history(),
            executed_utc=naive,
        )
    assert excinfo.value.code is ErrorCode.NAIVE_DATETIME


def test_explicit_executed_utc_wins_over_clock() -> None:
    stamp = datetime(2026, 7, 7, tzinfo=UTC)
    result = BandpassStage().apply(
        _sweep((HH_S11,), _golden_input(1)),
        history=_raw_history(),
        executed_utc=stamp,
        clock=ManualClock(CREATED_UTC),
    )
    assert result.history.records[0].executed_utc == stamp  # type: ignore[union-attr]
