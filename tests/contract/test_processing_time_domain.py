"""Contract tests for ISSUE-031: zero-padded IFFT, physical time axis, display crop.

Pure deterministic tests: synthetic fixed-formula inputs only - no hardware,
no threads, no sleeps, no RNG at runtime.

Contract summary (docs/issues/M06_CALIBRATION_PROCESSING.md ISSUE-031,
docs/PROCESSING.md section 4, docs/ACQUISITION.md section 6,
docs/DATA_MODEL.md section 8, docs/reports/ISSUE_031_BASELINE_CONFIRMATION.md
section 3 and docs/plans/2026-09-05-issue-031-ifft.md D1-D9):

- ``FrequencyToTimeStage`` structurally satisfies the frozen ISSUE-030
  ``ProcessingStage`` protocol over the ISSUE-007 ``ProcessingRecord`` /
  ``ProcessingHistory`` (no parallel history type, core/** untouched); its
  output is a fresh immutable ``TimeDomainScan(kind=time_base)`` whose full
  axis spans the physical unambiguous period ``T = 1/df``;
- migrated grid math is the rebar-inspector contract verbatim
  (processing/ifft.py SHA-256
  9496288e9e918f788b88f41945ea5e43889cfb3c298cccf7543a33b5a41d297a, verified
  against docs/reference-baselines/manifest.md in t1): uniform-step tolerance
  ``max(1 Hz, df*1e-6)``, DC->start nearest-bin zero padding, sub-bin start
  offset restored by ``exp(2j*pi*offset*t)``, FFT length
  ``next_pow2(bins+n)*oversampling``, seconds axis ``arange(N)/(N*df)``;
  the reference's ``max_time_s`` truncation is deliberately NOT migrated
  (display cropping replaces it, plan M2);
- golden literals below were produced on 2026-09-05 by re-evaluating that
  reference algorithm verbatim in this project venv (plan section 7 M1);
- fail-closed matrix: non-uniform / duplicate-decreasing / negative /
  non-finite / undersized axes are rejected, illegal input domains raise
  PROCESSING_DOMAIN_MISMATCH, repeated stage application inside one history
  is refused by the history chain (a bumped stage_version does not bypass),
  malformed oversampling/fft_size/crop bounds raise INVALID_ARGUMENT or
  OUT_OF_RANGE;
- raw inputs are never mutated and the display crop view is read-only over
  the ARCHIVED buffers: cropping never copies, truncates or rewrites the
  stored ``time_base``.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime

import numpy as np
import pytest

from uav_gpr.core import (
    BackgroundReferenceId,
    CalibrationProfileId,
    ChannelSpec,
    DataDomain,
    DomainError,
    ErrorCode,
    FrequencyScan,
    FrequencySweep,
    LogicalPolarization,
    ManualClock,
    ProcessingHistory,
    ProcessingRecord,
    SParameter,
    TimeDomainKind,
    TimeDomainScan,
)
from uav_gpr.core.errors import JsonValue
from uav_gpr.processing.bandpass import BandpassStage, ProcessingStage
from uav_gpr.processing.time_domain import (
    DEFAULT_IFFT_OVERSAMPLING,
    IFFT_GRID_TOLERANCE_REL,
    IFFT_STAGE_NAME,
    DisplayCropConfig,
    DisplayTimeWindowView,
    FrequencyToTimeStage,
    TimeDomainStageResult,
    compute_ifft_grid,
)

# ---------------------------------------------------------------- fixtures

CREATED_UTC = datetime(2026, 1, 1, tzinfo=UTC)

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

RAW = DataDomain.FREQUENCY_RAW
CALIBRATED = DataDomain.FREQUENCY_CALIBRATED
BACKGROUND_APPLIED = DataDomain.FREQUENCY_BACKGROUND_APPLIED
FILTERED = DataDomain.FREQUENCY_FILTERED
TIME_BASE = DataDomain.TIME_BASE
TIME_PROCESSED = DataDomain.TIME_PROCESSED

PID = CalibrationProfileId(uuid.UUID(int=7))
BGID = BackgroundReferenceId(uuid.UUID(int=8))

# Ten-point uniform 100 MHz axis starting at 50 MHz (first measured point
# lands exactly on bin 0 of the df grid; the residual 50 MHz sub-bin offset
# exercises the phase-ramp correction).
GOLDEN_AXIS_HZ = np.arange(10) * 1e8 + 5e7
GOLDEN_DF_HZ = 1e8
GOLDEN_OVERSAMPLING = 4  # next_pow2(0+10)=16 -> fft_size 64
GOLDEN_DT_S = 1.0 / (64 * GOLDEN_DF_HZ)  # 1.5625e-10 s

# Deterministic complex spectrum built without any RNG: fixed formula
# cos(2*pi*0.35*i/df) + 0.5j*sin(2*pi*0.125*i/df) over the bin index i,
# identical to the one used when the golden literals below were produced.
_GOLDEN_I = np.arange(10, dtype=np.float64)
GOLDEN_SIGNAL = (
    np.cos(2 * np.pi * 0.35 * _GOLDEN_I / GOLDEN_DF_HZ)
    + 0.5j * np.sin(2 * np.pi * 0.125 * _GOLDEN_I / GOLDEN_DF_HZ)
).astype(np.complex128)


def _golden_channel(factor: float, skew: complex) -> np.ndarray:
    return GOLDEN_SIGNAL * factor + skew


# Golden time_axis_s (first 8 samples of the 64-point full axis), produced
# 2026-09-05 by the frozen reference grid formula in this venv.
GOLDEN_TIME_AXIS_HEAD = [
    0.0,
    1.5625e-10,
    3.125e-10,
    4.6875e-10,
    6.25e-10,
    7.8125e-10,
    9.375e-10,
    1.09375e-09,
]

# Golden ifft output (channel hh_s11, first 8 samples after the phase-ramp
# correction), same provenance as above.
GOLDEN_TIME_SAMPLES_HEAD = [
    complex(0.15624999999999892, 2.761165418194153e-09),
    complex(0.132385656194074, 0.07076161048371808),
    complex(0.0736382288178206, 0.1102074018172022),
    complex(0.010387364013626764, 0.10546469581463856),
    complex(-0.02831648339676757, 0.06836203483828984),
    complex(-0.03153502015653014, 0.025880121278220855),
    complex(-0.010299248446962882, 0.002048647701613368),
    complex(0.012883714471123648, 0.003908232305984138),
]


def _sweep(channels: tuple[ChannelSpec, ...]) -> FrequencySweep:
    rows = [_golden_channel(1.0, 0.0j)]
    if len(channels) == 2:
        rows.append(_golden_channel(1.0, complex(0.25, -0.5)))
    return FrequencySweep(
        channels=channels,
        frequencies_hz=GOLDEN_AXIS_HZ,
        data=np.stack(rows),
    )


def _scan(channels: tuple[ChannelSpec, ...], n_traces: int) -> FrequencyScan:
    sweep = _sweep(channels)
    traces = []
    for k in range(n_traces):
        traces.append(sweep.data * (1.0 + 0.125 * k))
    return FrequencyScan(
        channels=channels,
        frequencies_hz=GOLDEN_AXIS_HZ,
        data=np.stack(traces),
    )


def _record(
    *,
    stage: str = "osl_calibration",
    version: str = "1.0",
    input_domain: DataDomain,
    output_domain: DataDomain,
    parameters: Mapping[str, JsonValue] | None = None,
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


def _filtered_history() -> ProcessingHistory:
    """Legal chain raw -> calibrated -> background -> frequency_filtered."""
    return _history(
        _record(
            input_domain=RAW,
            output_domain=CALIBRATED,
            calibration_id=PID,
        ),
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
    )


def _time_base_history() -> ProcessingHistory:
    return _history(
        _record(
            input_domain=RAW,
            output_domain=TIME_BASE,
            stage="legacy_ifft",
        )
    )


# ---------------------------------------------------------------- protocol


def test_stage_satisfies_frozen_processing_stage_protocol() -> None:
    stage = FrequencyToTimeStage()
    assert isinstance(stage, ProcessingStage)
    assert stage.stage_name == IFFT_STAGE_NAME == "frequency_to_time_ifft"
    assert stage.stage_version == "1.0"
    assert stage.output_domain is TIME_BASE
    assert stage.input_domain == frozenset(
        {RAW, CALIBRATED, BACKGROUND_APPLIED, FILTERED}
    )
    assert TIME_BASE not in stage.input_domain
    assert TIME_PROCESSED not in stage.input_domain


def test_default_oversampling_is_explicit_and_recorded() -> None:
    stage = FrequencyToTimeStage()
    assert stage.oversampling == DEFAULT_IFFT_OVERSAMPLING == 16
    assert stage.fft_size is None
    params = dict(stage.parameters)
    assert params["oversampling_factor"] == 16
    assert params["fft_size_mode"] == "power_of_two_times_oversampling"
    assert params["zero_padding_policy"] == "dc_to_start_low_frequency_zeros"
    assert params["interpolation_only_no_physical_resolution_gain"] is True
    assert params["time_axis_unit"] == "s"
    assert params["depth_calculation"] is False


# ---------------------------------------------------------------- grid math


def test_compute_grid_matches_reference_parameters() -> None:
    axis, df, fft_size, first_bin, offset = compute_ifft_grid(
        GOLDEN_AXIS_HZ, oversampling=GOLDEN_OVERSAMPLING
    )
    assert df == GOLDEN_DF_HZ
    assert first_bin == 0
    assert offset == 5e7  # 50 MHz start sits half a bin above DC grid point
    assert fft_size == 64  # next_pow2(10) * 4
    assert axis.size == 64
    assert axis[0] == 0.0
    # The full axis covers one unambiguous period T = 1/df sampled at N points:
    # the LAST sample sits at T - dt (period is size * step, not last value).
    assert float(axis[-1]) == pytest.approx(1.0 / df - GOLDEN_DT_S, abs=1e-20)
    assert axis.size * float(axis[1] - axis[0]) == pytest.approx(1.0 / df, rel=1e-12)


def test_compute_grid_bin_aligned_start_has_zero_offset() -> None:
    # Start at 200 MHz with df 100 MHz: exact bin 2 -> zero offset and two
    # low-frequency zero bins recorded by the policy.
    axis = np.arange(10) * 1e8 + 2e8
    _, df, fft_size, first_bin, offset = compute_ifft_grid(
        axis, oversampling=GOLDEN_OVERSAMPLING
    )
    assert df == 1e8
    assert first_bin == 2
    assert offset == 0.0
    assert fft_size == 64  # next_pow2(2+10)=16, *4


def test_compute_grid_half_bin_start_rounds_down() -> None:
    # 150 MHz against a 100 MHz grid rounds to bin 2 (round-half-even) and
    # leaves the documented residual -50 MHz restored by the phase ramp.
    axis = np.arange(10) * 1e8 + 15e7
    _, _, _, first_bin, offset = compute_ifft_grid(
        axis, oversampling=GOLDEN_OVERSAMPLING
    )
    assert first_bin == 2
    assert offset == -5e7


def test_explicit_fft_size_rules() -> None:
    _, _, fft_size, _, _ = compute_ifft_grid(
        GOLDEN_AXIS_HZ, oversampling=16, fft_size=32
    )
    assert fft_size == 32
    with pytest.raises(DomainError) as info:
        compute_ifft_grid(GOLDEN_AXIS_HZ, fft_size=24)
    assert info.value.code is ErrorCode.INVALID_ARGUMENT
    # 200 points x 1 MHz need 256 padded bins minimum; 128 cannot hold them.
    with pytest.raises(DomainError) as info:
        compute_ifft_grid(np.arange(200) * 1e6 + 1e6, fft_size=128)
    assert info.value.code is ErrorCode.OUT_OF_RANGE


@pytest.mark.parametrize("bad", [0, -4, True, False, 4.0, "4", None])
def test_oversampling_must_be_positive_int(bad: object) -> None:
    with pytest.raises(DomainError) as info:
        compute_ifft_grid(GOLDEN_AXIS_HZ, oversampling=bad)  # type: ignore[arg-type]
    assert info.value.code is ErrorCode.INVALID_ARGUMENT


# --------------------------------------------------- fail-closed axis rules


def test_non_uniform_axis_rejected_with_tolerance_context() -> None:
    axis = np.array([1e8, 2e8, 3e8, 4.5e8, 5.5e8])  # step drift 50 MHz
    with pytest.raises(DomainError) as info:
        compute_ifft_grid(axis)
    assert info.value.code is ErrorCode.NON_UNIFORM_AXIS
    ctx = info.value.context
    assert ctx["allowed_error_hz"] == pytest.approx(
        max(1.0, 1e8 * IFFT_GRID_TOLERANCE_REL)
    )
    max_error = ctx["max_step_error_hz"]
    allowed_error = ctx["allowed_error_hz"]
    assert isinstance(max_error, float) and isinstance(allowed_error, float)
    assert max_error > allowed_error


def test_within_tolerance_axis_accepted_at_edge() -> None:
    # A single step displaced by exactly half the allowed error stays legal.
    df = 1e8
    allowed = max(1.0, df * IFFT_GRID_TOLERANCE_REL)
    axis = np.concatenate(
        [np.arange(9) * df, np.array([8 * df + df + allowed * 0.5])]
    )
    _, computed_df, fft_size, _, _ = compute_ifft_grid(axis, oversampling=2)
    assert computed_df == df
    assert fft_size >= 2 * axis.size


def test_duplicate_frequency_rejected_before_models() -> None:
    axis = np.array([1e8, 2e8, 2e8, 3e8])
    with pytest.raises(DomainError) as info:
        compute_ifft_grid(axis)
    assert info.value.code is ErrorCode.NON_INCREASING_AXIS


def test_decreasing_axis_rejected() -> None:
    with pytest.raises(DomainError) as info:
        compute_ifft_grid(np.array([3e8, 2e8, 1e8]))
    assert info.value.code is ErrorCode.NON_INCREASING_AXIS


def test_negative_frequency_rejected() -> None:
    with pytest.raises(DomainError) as info:
        compute_ifft_grid(np.array([-1e8, 1e8, 2e8]))
    assert info.value.code is ErrorCode.OUT_OF_RANGE


def test_non_finite_axis_rejected() -> None:
    with pytest.raises(DomainError) as info:
        compute_ifft_grid(np.array([1e8, np.nan, 2e8]))
    assert info.value.code is ErrorCode.NON_FINITE_AXIS


def test_single_point_axis_rejected() -> None:
    with pytest.raises(DomainError) as info:
        compute_ifft_grid(np.array([1e8]))
    assert info.value.code is ErrorCode.INVALID_ARGUMENT


# ---------------------------------------------------------------- golden


def _golden_series_full() -> np.ndarray:
    """Independent transcription of the frozen reference padded-IFFT pipeline."""
    df = GOLDEN_DF_HZ
    n = GOLDEN_AXIS_HZ.size
    first_bin = round(float(GOLDEN_AXIS_HZ[0]) / df)
    offset = float(GOLDEN_AXIS_HZ[0]) - first_bin * df
    power_of_two = 1 << (first_bin + n - 1).bit_length()
    fft_size = power_of_two * GOLDEN_OVERSAMPLING
    padded = np.zeros(fft_size, dtype=np.complex128)
    padded[first_bin : first_bin + n] = GOLDEN_SIGNAL
    response = np.fft.ifft(padded)
    t = np.arange(fft_size, dtype=np.float64) / (fft_size * df)
    return response * np.exp(2j * np.pi * offset * t)


def test_golden_full_output_matches_reference_algorithm() -> None:
    result = FrequencyToTimeStage(oversampling=GOLDEN_OVERSAMPLING).apply(
        _sweep((HH_S11,)), history=_raw_history(), executed_utc=CREATED_UTC
    )
    scan = result.source
    assert isinstance(scan, TimeDomainScan)
    assert scan.kind is TimeDomainKind.TIME_BASE
    assert scan.data.shape == (1, 1, 64)
    assert scan.data.dtype == np.complex128
    # Axis head literals pin the seconds scale (T = 1/df = 10 ns over 64 pts).
    assert scan.time_axis_s[:8].tolist() == GOLDEN_TIME_AXIS_HEAD
    got = scan.data[0, 0]
    # Head samples match the pinned venv literals from the same day.
    assert [complex(x) for x in got[:8]] == GOLDEN_TIME_SAMPLES_HEAD
    # Whole-buffer bit-level equality against an independent transcription of
    # the frozen reference algorithm (same IEEE ops, same order).
    assert np.array_equal(got, _golden_series_full())


def test_direct_numpy_ifft_equivalence_zero_offset_case() -> None:
    # Exact-bin start (offset == 0): the stage must equal a plain
    # np.fft.ifft of the zero-padded vector, sample for sample, exactly.
    axis = np.arange(8) * 1e8 + 2e8  # 200 MHz .. 900 MHz, bin-aligned
    data = (
        np.exp(1j * np.arange(8) * 0.7)[np.newaxis, :]
        + np.arange(8)[np.newaxis, :] * 0.01
    )
    sweep = FrequencySweep(channels=(HH_S11,), frequencies_hz=axis, data=data)
    result = FrequencyToTimeStage(oversampling=4).apply(
        sweep, history=_raw_history(), executed_utc=CREATED_UTC
    )
    padded = np.zeros(64, dtype=np.complex128)
    padded[2 : 2 + 8] = data[0]
    expected = np.fft.ifft(padded)
    assert np.array_equal(result.source.data[0, 0], expected)


# ---------------------------------------------------------------- domain


def test_legal_predecessor_domains_all_apply() -> None:
    stage = FrequencyToTimeStage(oversampling=2)
    histories = {
        RAW: _raw_history(),
        CALIBRATED: _calibrated_history(),
        FILTERED: _filtered_history(),
    }
    for expected_input, history in histories.items():
        result = stage.apply(
            _sweep((HH_S11,)), history=history, executed_utc=CREATED_UTC
        )
        record = result.history.records[-1]
        assert record.input_domain is expected_input
        assert record.output_domain is TIME_BASE
        assert result.domain is TIME_BASE


def test_illegal_input_domains_fail_closed() -> None:
    stage = FrequencyToTimeStage(oversampling=2)
    bad_histories = {
        "time_base predecessor": _time_base_history(),
    }
    for label, history in bad_histories.items():
        with pytest.raises(DomainError) as info:
            stage.apply(_sweep((HH_S11,)), history=history)
        assert info.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH, label
        assert info.value.context["input_domain"] == "time_base"
    # time_processed cannot be reached without a valid time_base hop first.
    processed = _history(
        _record(input_domain=RAW, output_domain=TIME_BASE, stage="legacy_ifft"),
        _record(
            input_domain=TIME_BASE,
            output_domain=TIME_PROCESSED,
            stage="dewow",
        ),
    )
    with pytest.raises(DomainError) as info:
        stage.apply(_sweep((HH_S11,)), history=processed)
    assert info.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH


def test_duplicate_stage_rejected_by_history_chain() -> None:
    stage = FrequencyToTimeStage(oversampling=2)
    first = stage.apply(
        _sweep((HH_S11,)), history=_filtered_history(), executed_utc=CREATED_UTC
    )
    assert first.history.records[-1].stage_name == IFFT_STAGE_NAME
    # Real path: applying the ifft again on its own output fails closed at
    # the stage's domain gate (time_base is no legal frequency predecessor).
    with pytest.raises(DomainError) as info:
        stage.apply(_sweep((HH_S11,)), history=first.history)
    assert info.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH
    # Isolated probe of the per-history uniqueness rule (same stage_name,
    # bumped stage_version must not bypass): the core chain rules make a
    # second raw->time_base hop unconstructable in normal flow, so this
    # temporarily swaps only the ``pairwise`` iteration to yield nothing
    # (chain check skipped) — exactly the 030 precedent (plan log item 2) —
    # and restores it in ``finally``.
    import uav_gpr.core.time_domain as core_td

    original_pairwise = core_td.pairwise  # type: ignore[attr-defined]

    def _no_pairs(_records: object) -> list[tuple[()]]:
        return []

    duplicate_a = ProcessingRecord(
        stage_name=IFFT_STAGE_NAME,
        stage_version="1.0",
        parameters={"probe": 1},
        input_domain=RAW,
        output_domain=TIME_BASE,
        executed_utc=CREATED_UTC,
        software_version="0.1.0.dev0",
    )
    duplicate_b = ProcessingRecord(
        stage_name=IFFT_STAGE_NAME,
        stage_version="9.9",
        parameters={"probe": 2},
        input_domain=RAW,
        output_domain=TIME_BASE,
        executed_utc=CREATED_UTC,
        software_version="0.1.0.dev0",
    )
    try:
        core_td.pairwise = _no_pairs  # type: ignore[attr-defined,assignment]
        chain = ProcessingHistory([duplicate_a])
        with pytest.raises(DomainError) as info:
            chain.append(duplicate_b)
        assert info.value.code is ErrorCode.INVALID_ARGUMENT
        assert info.value.context.get("stage_name") == IFFT_STAGE_NAME
    finally:
        core_td.pairwise = original_pairwise  # type: ignore[attr-defined]


def test_bandpass_then_ifft_stays_two_independent_stages() -> None:
    bandpass = BandpassStage(edges_hz=(0.5e8, 1.0e8, 1.5e8, 2.5e8))
    filtered = bandpass.apply(
        _sweep((HH_S11,)), history=_raw_history(), executed_utc=CREATED_UTC
    )
    assert filtered.domain is FILTERED
    result = FrequencyToTimeStage(oversampling=2).apply(
        filtered.source, history=filtered.history, executed_utc=CREATED_UTC
    )
    names = [r.stage_name for r in result.history.records]
    assert names == ["frequency_bandpass", IFFT_STAGE_NAME]
    assert result.domain is TIME_BASE
    # The ifft consumed exactly the bandpass output spectrum (padded):
    n = GOLDEN_AXIS_HZ.size
    padded = np.zeros(32, dtype=np.complex128)
    padded[:n] = filtered.source.data[0]
    t = np.arange(32, dtype=np.float64) / (32 * GOLDEN_DF_HZ)
    expected = np.fft.ifft(padded) * np.exp(2j * np.pi * 5e7 * t)
    assert np.array_equal(result.source.data[0, 0], expected)


# ---------------------------------------------------------------- history


def test_record_parameters_capture_reproducible_grid() -> None:
    result = FrequencyToTimeStage(oversampling=GOLDEN_OVERSAMPLING).apply(
        _sweep((HH_S11,)), history=_raw_history(), executed_utc=CREATED_UTC
    )
    record = result.history.records[-1]
    params = dict(record.parameters)
    assert params["frequency_delta_hz"] == GOLDEN_DF_HZ
    assert params["frequency_point_count"] == 10
    assert params["start_frequency_hz"] == 5e7
    assert params["stop_frequency_hz"] == 95e7
    assert params["low_frequency_zero_bins"] == 0
    assert params["start_frequency_offset_hz"] == 5e7
    assert params["fft_size"] == 64
    assert params["time_sample_interval_s"] == pytest.approx(GOLDEN_DT_S)
    assert params["physical_unambiguous_period_s"] == pytest.approx(1e-8)
    assert params["explicit_fft_size"] is None
    assert params["grid"] == "uniform_dft"
    # Serializes through the frozen record round-trip unchanged.
    restored = ProcessingRecord.from_dict(record.to_dict())
    assert restored == record
    json_text = record.parameters_canonical_json()
    assert '"fft_size":64' in json_text
    assert '"physical_unambiguous_period_s":1e-08' in json_text


def test_history_appends_exactly_one_record_per_application() -> None:
    stage = FrequencyToTimeStage(oversampling=2)
    base = _calibrated_history()
    result = stage.apply(
        _sweep((HH_S11,)), history=base, executed_utc=CREATED_UTC
    )
    assert len(result.history) == len(base) + 1
    assert result.history.records[:-1] == base.records
    assert base.records == _calibrated_history().records  # old untouched


def test_clock_injection_and_naive_datetime() -> None:
    clock = ManualClock(CREATED_UTC)
    stage = FrequencyToTimeStage(oversampling=2)
    result = stage.apply(_sweep((HH_S11,)), history=_raw_history(), clock=clock)
    assert result.history.records[-1].executed_utc == CREATED_UTC
    with pytest.raises(DomainError) as info:
        stage.apply(
            _sweep((HH_S11,)),
            history=_raw_history(),
            executed_utc=datetime(2026, 1, 1),  # naive
        )
    assert info.value.code is ErrorCode.NAIVE_DATETIME


# ------------------------------------------------------------ vectorization


def test_single_and_dual_channel_match_per_channel_application() -> None:
    stage = FrequencyToTimeStage(oversampling=2)

    def sweep_for(channel: ChannelSpec, scale: float) -> FrequencySweep:
        return FrequencySweep(
            channels=(channel,),
            frequencies_hz=GOLDEN_AXIS_HZ,
            data=_golden_channel(scale, 0.0j)[np.newaxis, :],
        )

    dual_sweep = FrequencySweep(
        channels=(HH_S11, VV_S22),
        frequencies_hz=GOLDEN_AXIS_HZ,
        data=np.stack(
            [
                _golden_channel(1.0, 0.0j),
                _golden_channel(1.0, complex(0.25, -0.5)),
            ]
        ),
    )
    dual = stage.apply(
        dual_sweep, history=_raw_history(), executed_utc=CREATED_UTC
    )
    single_hh = stage.apply(
        sweep_for(HH_S11, 1.0), history=_raw_history(), executed_utc=CREATED_UTC
    )
    assert np.array_equal(dual.source.data[0, 0], single_hh.source.data[0, 0])
    # Linearity probe on the second channel (constant skew maps to a DC-only
    # impulse after the IFFT): compare against its own pure-channel result.
    single_vv = stage.apply(
        FrequencySweep(
            channels=(VV_S22,),
            frequencies_hz=GOLDEN_AXIS_HZ,
            data=_golden_channel(1.0, complex(0.25, -0.5))[np.newaxis, :],
        ),
        history=_raw_history(),
        executed_utc=CREATED_UTC,
    )
    assert np.array_equal(dual.source.data[0, 1], single_vv.source.data[0, 0])


def test_scan_vectorization_equals_per_trace_sweep_processing() -> None:
    stage = FrequencyToTimeStage(oversampling=2)
    scan_in = _scan((HH_S11, VV_S22), n_traces=3)
    batched = stage.apply(
        scan_in, history=_raw_history(), executed_utc=CREATED_UTC
    )
    assert batched.source.data.shape == (3, 2, 32)
    for trace in range(3):
        sweep = FrequencySweep(
            channels=scan_in.channels,
            frequencies_hz=scan_in.frequencies_hz,
            data=scan_in.data[trace],
        )
        per_trace = stage.apply(
            sweep, history=_raw_history(), executed_utc=CREATED_UTC
        )
        assert np.array_equal(
            batched.source.data[trace], per_trace.source.data[0]
        )


def test_physical_period_is_inverse_of_step() -> None:
    result = FrequencyToTimeStage(oversampling=GOLDEN_OVERSAMPLING).apply(
        _sweep((HH_S11,)), history=_raw_history(), executed_utc=CREATED_UTC
    )
    axis = result.source.time_axis_s
    total = axis.size * (axis[1] - axis[0])
    assert total == pytest.approx(1.0 / GOLDEN_DF_HZ, rel=1e-12)


# ---------------------------------------------------------------- raw immutability


def test_inputs_never_mutated_and_outputs_are_fresh_snapshots() -> None:
    stage = FrequencyToTimeStage(oversampling=2)
    sweep = _sweep((HH_S11, VV_S22))
    before = sweep.data.copy()
    result = stage.apply(sweep, history=_raw_history(), executed_utc=CREATED_UTC)
    assert not isinstance(result.source, FrequencySweep | FrequencyScan)
    assert result.source.data is not sweep.data
    assert np.array_equal(sweep.data, before)
    assert not sweep.data.flags.writeable
    assert not result.source.data.flags.writeable
    assert not result.source.time_axis_s.flags.writeable
    with pytest.raises(ValueError):
        result.source.data[0, 0, 0] = 1.0 + 1.0j


def test_scan_metadata_passes_through_unchanged() -> None:
    stage = FrequencyToTimeStage(oversampling=2)
    scan_in = _scan((HH_S11,), n_traces=2)
    result = stage.apply(
        scan_in, history=_raw_history(), executed_utc=CREATED_UTC
    )
    assert result.source.metadata == scan_in.metadata


# ---------------------------------------------------------------- display crop


def _archived_result() -> TimeDomainStageResult:
    return FrequencyToTimeStage(oversampling=GOLDEN_OVERSAMPLING).apply(
        _scan((HH_S11, VV_S22), n_traces=2),
        history=_raw_history(),
        executed_utc=CREATED_UTC,
    )


def test_crop_view_is_readonly_slice_of_archived_buffers() -> None:
    archived = _archived_result()
    scan = archived.source
    axis_snapshot = scan.time_axis_s.copy()
    data_snapshot = scan.data.copy()
    config = DisplayCropConfig(start_s=2e-9, end_s=5e-9)
    view = DisplayTimeWindowView.for_scan(scan, config)
    assert view.sample_count == view.stop_index - view.start_index
    assert view.time_axis_s.size == view.sample_count
    assert view.data.shape == (2, 2, view.sample_count)
    # Same underlying buffers: the view aliases the archive, never copies.
    assert view.time_axis_s.__array_interface__["data"][0] == (
        scan.time_axis_s.__array_interface__["data"][0]
        + view.start_index * scan.time_axis_s.itemsize
    )
    assert not view.data.flags.writeable
    assert not view.time_axis_s.flags.writeable
    # All archived samples inside the requested window, none outside.
    assert float(view.time_axis_s[0]) >= config.start_s
    assert float(view.time_axis_s[-1]) <= config.end_s
    assert np.array_equal(
        scan.time_axis_s[view.start_index : view.stop_index],
        view.time_axis_s,
    )
    # The archive itself is byte-for-byte untouched by creating the view.
    assert np.array_equal(scan.time_axis_s, axis_snapshot)
    assert np.array_equal(scan.data, data_snapshot)
    assert scan.time_axis_s.size == 64  # full T = 10 ns still archived


def test_crop_boundaries_and_empty_window_rules() -> None:
    archived = _archived_result()
    scan = archived.source
    # Full-window config keeps every sample.
    full = DisplayTimeWindowView.for_scan(
        scan, DisplayCropConfig(0.0, float(scan.time_axis_s[-1]))
    )
    assert full.sample_count == scan.time_axis_s.size
    # Zero-width config at a sample instant yields exactly one sample.
    point = DisplayTimeWindowView.for_scan(
        scan, DisplayCropConfig(GOLDEN_DT_S * 4, GOLDEN_DT_S * 4)
    )
    assert point.sample_count == 1
    # Bounds between samples may legitimately resolve to an empty window:
    mid = GOLDEN_DT_S * 4 + GOLDEN_DT_S * 0.5
    with pytest.raises(DomainError) as info:
        DisplayTimeWindowView.for_scan(
            scan, DisplayCropConfig(mid, mid + GOLDEN_DT_S * 0.2)
        )
    assert info.value.code is ErrorCode.INVALID_ARGUMENT
    # Out-of-range beyond the archived axis fails closed (no silent clamp).
    with pytest.raises(DomainError) as info:
        DisplayTimeWindowView.for_scan(
            scan, DisplayCropConfig(0.0, float(scan.time_axis_s[-1]) + GOLDEN_DT_S)
        )
    assert info.value.code is ErrorCode.OUT_OF_RANGE


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (-1e-9, 5e-9),          # negative start
        (5e-9, 4e-9),           # end before start
        (float("nan"), 5e-9),   # non-finite
        (0.0, float("inf")),    # non-finite
        (True, 5e-9),           # bool
        (0.0, "5"),             # non-scalar number
    ],
)
def test_invalid_crop_configs_rejected(start: object, end: object) -> None:
    with pytest.raises(DomainError) as info:
        DisplayCropConfig(start, end)  # type: ignore[arg-type]
    assert info.value.code in (ErrorCode.INVALID_ARGUMENT, ErrorCode.OUT_OF_RANGE)


def test_crop_config_is_serializable_pure_data() -> None:
    config = DisplayCropConfig(2e-9, 5e-9)
    assert config.to_json() == {"start_s": 2e-9, "end_s": 5e-9}
    assert dict(config.parameters) == {"start_s": 2e-9, "end_s": 5e-9}
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.start_s = 3e-9  # type: ignore[misc]


def test_display_crop_never_touches_processing_history() -> None:
    archived = _archived_result()
    scan = archived.source
    before = scan.history
    _ = DisplayTimeWindowView.for_scan(
        scan, DisplayCropConfig(1e-9, 8e-9)
    )
    assert scan.history is before
    assert len(before) == 1  # exactly the one ifft record
    assert before.records[0].output_domain is TIME_BASE


def test_view_refuses_non_time_base_scans() -> None:
    archived = _archived_result()
    scan = archived.source
    processed_history = scan.history.append(
        _record(
            stage="dewow",
            input_domain=TIME_BASE,
            output_domain=TIME_PROCESSED,
        )
    )
    processed_scan = TimeDomainScan(
        channels=scan.channels,
        time_axis_s=scan.time_axis_s,
        data=scan.data,
        kind=TimeDomainKind.TIME_PROCESSED,
        history=processed_history,
    )
    with pytest.raises(DomainError) as info:
        DisplayTimeWindowView.for_scan(
            processed_scan, DisplayCropConfig(0.0, 5e-9)
        )
    assert info.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH


# ------------------------------------------------------- exclusion guarantees


def test_module_exposes_no_depth_or_resolution_claims() -> None:
    import inspect

    import uav_gpr.processing.time_domain as module

    source = inspect.getsource(module)
    lowered = source.lower()
    for forbidden in ("velocity_m", "depth_m", "distance_m", "speed_of_light"):
        assert forbidden not in lowered
    # No bandpass computation embedded in this module.
    assert "sin(" not in lowered.replace("np.sinh", "")
    assert "bandpass" in lowered  # documented references only
    assert callable(inspect.getattr_static(module, "compute_ifft_grid"))
    # Public surface contains no bandpass/window builder symbol.
    public = set(module.__all__)
    assert not any("bandpass" in name.lower() for name in public)
    assert "build_bandpass_window" not in dir(FrequencyToTimeStage)
