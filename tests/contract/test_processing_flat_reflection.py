"""Contract tests for ISSUE-035: Flat Reflection time-domain stage.

Pure deterministic tests: synthetic fixed-formula inputs only - no hardware,
no threads, no runtime RNG.

Contract summary (docs/issues/M06_CALIBRATION_PROCESSING.md ISSUE-035,
docs/PROCESSING.md sections 2/6, docs/CALIBRATION.md L9-10 concept boundary,
docs/reports/ISSUE_035_BASELINE_CONFIRMATION.md section 3 and
docs/plans/2026-09-05-issue-035-flat-reflection.md D1-D9):

- ``FlatReflectionFilterStage`` structurally satisfies the frozen ISSUE-030
  ``ProcessingStage`` protocol over the ISSUE-007 history: stable
  ``stage_name="flat_reflection_filter"`` (the exact token the ISSUE-034 dewow
  ordering guard refuses), accepted input domains {time_base, time_processed},
  output domain ``time_processed``; it subtracts a centered moving average
  along axis 0 (the TRACE / survey-line axis, never frequency or time) of an
  immutable ``TimeDomainScan`` and returns a fresh
  ``TimeDomainScan(kind=time_processed)`` plus ``TimeDomainStageResult`` (the
  ISSUE-031 sibling result type);
- migrated math is the rebar-inspector contract verbatim
  (processing/flat_reflection.py SHA-256
  89e3c01b3ce4135fd96495b27a67ff69760224bdc80c9144fd9aeeaf4ca87df0 and
  processing/_time_stage_common.py SHA-256
  e0c201b55acbaece0edb1546bbb8a00492874bb79fb9caf789d5ba416d333c81, verified
  against docs/reference-baselines/manifest.json in t1): odd window >= 3 in
  TRACE COUNTS validated at construction, fixed "edge" boundary padding,
  O(N) cumulative sums in complex128 (equivalent to processing real and
  imaginary parts independently), short-line refusal (window > n_traces);
- golden literals below were produced on 2026-09-05 by re-evaluating that
  reference kernel verbatim in this project venv (plan D9 scene A/B/C;
  canonical golden JSON SHA-256
  060f8342ce756b4e548ef00ff3c884f86b561efe5dba0881caa99cdd78985c86);
- fail-closed matrix: empty/frequency/out-of-order histories raise
  PROCESSING_DOMAIN_MISMATCH, duplicate flat is refused by the stage gate and
  independently by core history uniqueness (a bumped stage_version does not
  bypass), non-finite data and undersized/oversized/even/bool/non-int windows
  raise structured DomainErrors; the recommended dewow -> flat order chains
  through the REAL DewowStage end to end;
- semantic separation from air background subtraction (CALIBRATION.md L9-10):
  different name, history token, domain chain and mathematical object are
  asserted side by side, and both coexist legally in one long history;
- inputs are never mutated: the source scan buffers stay byte-identical and
  write-protected; channels / time axis / per-trace metadata are preserved.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import time
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
    LogicalPolarization,
    ManualClock,
    ProcessingHistory,
    ProcessingRecord,
    SParameter,
    TimeDomainKind,
    TimeDomainScan,
)
from uav_gpr.core.enums import TraceQualityStatus
from uav_gpr.core.errors import JsonValue
from uav_gpr.core.identifiers import DeviceId, MissionId, TraceUid
from uav_gpr.core.metadata import TraceMetadata
from uav_gpr.core.timeutil import MonotonicNs
from uav_gpr.processing.bandpass import ProcessingStage
from uav_gpr.processing.dewow import DEWOW_STAGE_NAME, DewowStage
from uav_gpr.processing.flat_reflection import (
    DEFAULT_FLAT_REFLECTION_WINDOW_TRACES,
    FLAT_AXIS,
    FLAT_PADDING,
    FLAT_STAGE_NAME,
    FLAT_STAGE_VERSION,
    FlatReflectionFilterStage,
    centered_moving_mean_along_axis,
    validate_window_traces,
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


def _record(
    *,
    stage: str,
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


def _ifft_record() -> ProcessingRecord:
    # The core transition table opens FREQUENCY_RAW -> TIME_BASE directly
    # (031 precedent), and every history must start from frequency_raw.
    return _record(
        stage="frequency_to_time_ifft", input_domain=RAW, output_domain=TIME_BASE
    )


def _air_bg_record() -> ProcessingRecord:
    return _record(
        stage="air_background_subtraction",
        input_domain=CALIBRATED,
        output_domain=BACKGROUND_APPLIED,
        background_id=BackgroundReferenceId(uuid.UUID(int=8)),
    )


def _osl_record() -> ProcessingRecord:
    # Core provenance rule: a frequency_calibrated producer must carry the
    # calibration profile id.
    rec = ProcessingRecord(
        stage_name="osl_calibration",
        stage_version="1.0",
        parameters={"mode": "default"},
        input_domain=RAW,
        output_domain=CALIBRATED,
        executed_utc=CREATED_UTC,
        software_version="0.1.0.dev0",
        calibration_profile_id=CalibrationProfileId(uuid.UUID(int=7)),
    )
    return rec


def _bandpass_record() -> ProcessingRecord:
    return _record(
        stage="frequency_bandpass",
        input_domain=BACKGROUND_APPLIED,
        output_domain=FILTERED,
    )


def _dewow_record(input_domain: DataDomain = TIME_BASE) -> ProcessingRecord:
    return _record(
        stage=DEWOW_STAGE_NAME,
        input_domain=input_domain,
        output_domain=TIME_PROCESSED,
    )


def _legacy_dcgate_record() -> ProcessingRecord:
    """A foreign time_processed producer (not dewow, not flat)."""
    return _record(
        stage="legacy_dcgate", input_domain=TIME_BASE, output_domain=TIME_PROCESSED
    )


def _flat_self_record(
    input_domain: DataDomain = TIME_BASE, *, version: str = "1.0"
) -> ProcessingRecord:
    return _record(
        stage=FLAT_STAGE_NAME,
        version=version,
        input_domain=input_domain,
        output_domain=TIME_PROCESSED,
    )


def _history(*records: ProcessingRecord) -> ProcessingHistory:
    return ProcessingHistory(records)


#: Golden dt of every default synthetic scan: a 1 ns sampling grid.
DT_S = 1e-9
N_TIME = 4
AXIS_S = np.arange(N_TIME, dtype=np.float64) * DT_S


def _axis(n_time: int) -> np.ndarray:
    return np.arange(n_time, dtype=np.float64) * DT_S


def _base_history() -> ProcessingHistory:
    return _history(_ifft_record())


def _scan(
    data: np.ndarray,
    *,
    channels: tuple[ChannelSpec, ...] = (HH_S11,),
    kind: TimeDomainKind = TimeDomainKind.TIME_BASE,
    history: ProcessingHistory | None = None,
    metadata: tuple[TraceMetadata | None, ...] = (),
    time_axis_s: np.ndarray | None = None,
) -> TimeDomainScan:
    n_time = int(data.shape[-1])
    if time_axis_s is None:
        time_axis_s = _axis(n_time)
    if history is None:
        if kind is TimeDomainKind.TIME_BASE:
            history = _base_history()
        else:
            history = _history(_ifft_record(), _legacy_dcgate_record())
    return TimeDomainScan(
        channels=channels,
        time_axis_s=time_axis_s,
        data=data,
        kind=kind,
        history=history,
        metadata=metadata,
    )


def _trace_ramp_scan(traces: int = 5, channels: int = 2, n_time: int = N_TIME) -> TimeDomainScan:
    i, j, k = np.meshgrid(
        np.arange(traces), np.arange(channels), np.arange(n_time), indexing="ij"
    )
    data = (i + 0.25 * j + 0.0625 * k) + 1j * (0.5 * i - 0.125 * k)
    return _scan(data.astype(np.complex128), channels=(HH_S11, VV_S22)[:channels])


def _stamp() -> ManualClock:
    return ManualClock(CREATED_UTC, 1_000)


# ------------------------------------------------------- golden literals
# Produced 2026-09-05 by re-evaluating the frozen reference kernel
# (_time_stage_common._centered_moving_mean, SHA-256 e0c201b5...) VERBATIM in
# this venv (axis moved to 0, pad half/half, complex128 cumsum prefixed by a
# zero slice, cum[W:] - cum[:-W], / W).  Canonical golden JSON (all six
# literals below, sorted keys, compact separators, ensure_ascii=False) hashes
# to sha256 hexdigest
# 060f8342ce756b4e548ef00ff3c884f86b561efe5dba0881caa99cdd78985c86.

GOLDEN_CANONICAL_SHA256 = (
    "060f8342ce756b4e548ef00ff3c884f86b561efe5dba0881caa99cdd78985c86"
)

# Scene A: 5x1x4 complex buffer, W=3, edge padding, axis 0.
A_IN_RE = [
    [[0.0], [1.0], [2.0], [3.0]],
    [[4.0], [5.0], [6.0], [7.0]],
    [[8.0], [9.0], [10.0], [11.0]],
    [[12.0], [13.0], [14.0], [15.0]],
    [[16.0], [17.0], [18.0], [19.0]],
]
A_IN_IM = [
    [[-0.5], [-1.5], [-2.5], [-3.5]],
    [[0.5], [1.5], [2.5], [3.5]],
    [[-1.0], [-2.0], [-3.0], [-4.0]],
    [[1.0], [2.0], [3.0], [4.0]],
    [[-0.25], [-0.75], [-1.25], [-1.75]],
]
A_MEAN_RE = [
    [[1.3333333333333333], [2.333333333333333], [3.333333333333333], [4.333333333333333]],
    [[4.0], [5.0], [6.0], [7.0]],
    [[8.0], [9.0], [10.0], [11.0]],
    [[12.0], [13.0], [14.0], [15.0]],
    [[14.666666666666666], [15.666666666666666], [16.666666666666664], [17.666666666666664]],
]
A_MEAN_IM = [
    [
        [-0.16666666666666666],
        [-0.5],
        [-0.8333333333333333],
        [-1.1666666666666665],
    ],
    [[-0.3333333333333333], [-0.6666666666666666], [-1.0], [-1.3333333333333333]],
    [[0.16666666666666666], [0.5], [0.8333333333333333], [1.1666666666666665]],
    [[-0.08333333333333333], [-0.25], [-0.41666666666666663], [-0.5833333333333333]],
    [[0.16666666666666666], [0.16666666666666666], [0.16666666666666666], [0.16666666666666666]],
]

# Scene B: 3x2x4 dyadic buffer, W=3 — full mean AND filtered output pinned.
B_IN_RE = [
    [[-3.0, -2.5, -2.0, -1.5], [-1.0, -0.5, 0.0, 0.5]],
    [[1.0, 1.5, 2.0, 2.5], [3.0, 3.5, 4.0, 4.5]],
    [[5.0, 5.5, 6.0, 6.5], [7.0, 7.5, 8.0, 8.5]],
]
B_IN_IM = [
    [[0.125, -0.03125, 0.0, -0.0625], [0.0625, -0.0625, 0.0625, 0.0]],
    [[0.03125, -0.125, -0.0625, -0.03125], [0.0, 0.03125, 0.0625, 0.125]],
    [[-0.03125, 0.0, -0.0625, 0.0625], [-0.0625, 0.0625, 0.0, 0.03125]],
]
B_MEAN_RE = [
    [
        [-1.6666666666666665, -1.1666666666666665, -0.6666666666666666, -0.16666666666666666],
        [0.3333333333333333, 0.8333333333333333, 1.3333333333333333, 1.8333333333333333],
    ],
    [[1.0, 1.5, 2.0, 2.5], [3.0, 3.5, 4.0, 4.5]],
    [
        [3.6666666666666665, 4.166666666666666, 4.666666666666666, 5.166666666666666],
        [5.666666666666666, 6.166666666666666, 6.666666666666666, 7.166666666666666],
    ],
]
B_MEAN_IM = [
    [
        [0.09375, -0.0625, -0.020833333333333332, -0.05208333333333333],
        [0.041666666666666664, -0.03125, 0.0625, 0.041666666666666664],
    ],
    [
        [0.041666666666666664, -0.05208333333333333, -0.041666666666666664, -0.010416666666666666],
        [0.0, 0.010416666666666666, 0.041666666666666664, 0.05208333333333333],
    ],
    [
        [-0.010416666666666666, -0.041666666666666664, -0.0625, 0.03125],
        [-0.041666666666666664, 0.05208333333333333, 0.020833333333333332, 0.0625],
    ],
]
B_OUT_RE = [
    [
        [-1.3333333333333335, -1.3333333333333335, -1.3333333333333335, -1.3333333333333333],
        [-1.3333333333333333, -1.3333333333333333, -1.3333333333333333, -1.3333333333333333],
    ],
    [[0.0, 0.0, 0.0, 0.0], [0.0, 0.0, 0.0, 0.0]],
    [
        [1.3333333333333335, 1.333333333333334, 1.333333333333334, 1.333333333333334],
        [1.333333333333334, 1.333333333333334, 1.333333333333334, 1.333333333333334],
    ],
]
B_OUT_IM = [
    [
        [0.03125, 0.03125, 0.020833333333333332, -0.010416666666666671],
        [0.020833333333333336, -0.03125, 0.0, -0.041666666666666664],
    ],
    [
        [-0.010416666666666664, -0.07291666666666667, -0.020833333333333336, -0.020833333333333336],
        [0.0, 0.020833333333333336, 0.020833333333333336, 0.07291666666666667],
    ],
    [
        [-0.020833333333333336, 0.041666666666666664, 0.0, 0.03125],
        [-0.020833333333333336, 0.010416666666666671, -0.020833333333333332, -0.03125],
    ],
]

# Scene C: 4x1x2 buffer, W=3, pure boundary tiling check.
C_IN_RE = [[[1.0], [3.0]], [[5.0], [7.0]], [[9.0], [11.0]], [[13.0], [15.0]]]
C_IN_IM = [[[0.5], [-0.5]], [[1.5], [-1.5]], [[2.5], [-2.5]], [[3.5], [-3.5]]]
C_MEAN_RE = [
    [[2.333333333333333], [4.333333333333333]],
    [[5.0], [7.0]],
    [[9.0], [11.0]],
    [[11.666666666666666], [13.666666666666666]],
]
C_MEAN_IM = [
    [[0.8333333333333333], [-0.8333333333333333]],
    [[1.5], [-1.5]],
    [[2.5], [-2.5]],
    [[3.1666666666666665], [-3.1666666666666665]],
]


def _scene_a() -> np.ndarray:
    return np.array(A_IN_RE, dtype=np.float64) + 1j * np.array(A_IN_IM, dtype=np.float64)


def _scene_b() -> np.ndarray:
    return np.array(B_IN_RE, dtype=np.float64) + 1j * np.array(B_IN_IM, dtype=np.float64)


def _scene_c() -> np.ndarray:
    return np.array(C_IN_RE, dtype=np.float64) + 1j * np.array(C_IN_IM, dtype=np.float64)


# ------------------------------------------------- 1. protocol compliance


def test_protocol_conformance_and_domains() -> None:
    stage = FlatReflectionFilterStage(window_traces=5)
    assert isinstance(stage, ProcessingStage)
    assert stage.stage_name == "flat_reflection_filter"
    assert stage.stage_name == FLAT_STAGE_NAME
    assert stage.stage_version == FLAT_STAGE_VERSION == "1.0"
    assert stage.input_domain == frozenset({TIME_BASE, TIME_PROCESSED})
    assert stage.output_domain == TIME_PROCESSED
    assert stage.window_traces == 5
    params = stage.parameters
    assert params["operation"] == "subtract_local_trace_mean"
    assert params["axis"] == "trace_first"
    assert params["padding"] == "edge"
    assert params["window_traces"] == 5
    assert (
        params["reference_source_sha256"]["rebar_processing_flat_reflection_py"]
        == "89e3c01b3ce4135fd96495b27a67ff69760224bdc80c9144fd9aeeaf4ca87df0"
    )
    json.dumps(dict(params))  # JSON-safe


def test_default_window_matches_reference_contract() -> None:
    assert DEFAULT_FLAT_REFLECTION_WINDOW_TRACES == 101
    assert FlatReflectionFilterStage().window_traces == 101
    assert FLAT_AXIS == 0
    assert FLAT_PADDING == "edge"


def test_apply_signature_widened_like_dewow() -> None:
    sig = inspect.signature(FlatReflectionFilterStage.apply)
    assert list(sig.parameters) == ["self", "source", "history", "executed_utc", "clock"]


@pytest.mark.parametrize("bad", [True, False, "5", 5.0, float("nan"), None, -3, 0, 1, 2, 4, 100])
def test_window_traces_rejection_matrix(bad: object) -> None:
    with pytest.raises(DomainError) as exc:
        FlatReflectionFilterStage(window_traces=bad)  # type: ignore[arg-type]
    assert exc.value.code is ErrorCode.INVALID_ARGUMENT
    with pytest.raises(DomainError):
        validate_window_traces(bad)


def test_window_one_zero_output_reason_is_pinned() -> None:
    # Safety hardening over the upstream prototype: window=1 zeroes output.
    with pytest.raises(DomainError) as exc:
        validate_window_traces(1)
    assert "identically zero" in exc.value.message
    assert exc.value.context["window_traces"] == 1


# --------------------------------------------- 2. horizontal background


def test_horizontal_background_is_removed() -> None:
    # A field constant along the trace axis (perfectly horizontal reflector)
    # is exactly the local mean everywhere -> output ~ 0.
    traces, chans, n_t = 9, 2, 6
    value = 4.0 - 2.5j
    data = np.full((traces, chans, n_t), value, dtype=np.complex128)
    scan = _scan(data, channels=(HH_S11, VV_S22))
    result = FlatReflectionFilterStage(window_traces=5).apply(scan, clock=_stamp())
    out = result.source.data
    assert out.dtype == np.complex128
    assert out.shape == (traces, chans, n_t)
    assert float(np.max(np.abs(out))) == 0.0  # exact: x - mean(x) cancels
    assert result.domain == TIME_PROCESSED
    assert result.source.kind is TimeDomainKind.TIME_PROCESSED


def test_local_target_survives_while_background_suppressed() -> None:
    # Horizontal layered background (identical on every trace) plus a
    # localized target on one trace: flat filtering suppresses the layer
    # and keeps the local anomaly prominent; only the 1/W dilution survives
    # inside the window around the target (docs/PROCESSING.md section 6).
    traces, n_t = 15, 8
    value = 2.0 - 1.5j
    data = np.full((traces, 1, n_t), value, dtype=np.complex128)
    data[7, 0, :] += 5.0  # localized target on trace 7
    scan = _scan(data, channels=(HH_S11,))
    out = FlatReflectionFilterStage(window_traces=9).apply(scan, clock=_stamp()).source.data
    assert abs(out[7, 0, 0]) > 4.0  # target retained (>80% of raw 5.0)
    far_rows = np.concatenate([out[:3].ravel(), out[12:].ravel()])
    assert float(np.max(np.abs(far_rows))) == 0.0  # background fully removed
    near_rows = np.concatenate([out[4:6].ravel(), out[8:11].ravel()])
    assert float(np.max(np.abs(near_rows))) <= 5.0 / 9 + 1e-12  # 1/W dilution


def test_continuous_layer_attenuation_risk_documented_by_math() -> None:
    # A ramp along trace (lateral continuity aligned with the line) is
    # partially removed by the local mean: its residual is strictly smaller
    # than the raw amplitude - the documented weakening behaviour.
    traces = 11
    ramp = np.arange(traces, dtype=np.float64)
    data = np.zeros((traces, 1, 4), dtype=np.complex128)
    data[:, :, :] = ramp[:, None, None]
    scan = _scan(data)
    out = FlatReflectionFilterStage(window_traces=5).apply(scan, clock=_stamp()).source.data
    assert float(np.max(np.abs(out.real))) < float(np.max(np.abs(ramp))) * 0.9


# ---------------------------------------------------- 3. complex handling


def test_complex_equivalence_real_imag_independent() -> None:
    a = _scene_a().real.copy()
    b = _scene_a().imag.copy()
    z = a + 1j * b
    ma = centered_moving_mean_along_axis(a.astype(np.complex128), axis=0, window=3, padding="edge")
    mb = centered_moving_mean_along_axis(b.astype(np.complex128), axis=0, window=3, padding="edge")
    mz = centered_moving_mean_along_axis(z, axis=0, window=3, padding="edge")
    assert np.array_equal(mz.real, ma.real)
    assert np.array_equal(mz.imag, mb.real)


def test_skew_probe_kernel_linearity() -> None:
    # The moving mean of a LINEAR ramp is the value at the window centre, so
    # f(z) = z - mean(z) annihilates linear drift on interior rows (edge rows
    # keep the tiling bias).  Real and imaginary parts are different ramps,
    # proving the two halves are processed independently.
    traces = 7
    idx = np.arange(traces, dtype=np.float64)
    data = np.zeros((traces, 1, 3), dtype=np.complex128)
    data[:, :, :] = ((3.0 * idx + 1.0) + 1j * (-1.5 * idx + 0.5))[:, None, None]
    scan = _scan(data)
    out = FlatReflectionFilterStage(window_traces=3).apply(scan, clock=_stamp()).source.data
    assert np.allclose(out[1:-1, 0, 0], 0.0 + 0.0j, rtol=0, atol=1e-12)
    # Edge rows retain exactly the boundary-bias signature (not zero).
    x = data[:, 0, 0]
    assert out[0, 0, 0] == x[0] - (2 * x[0] + x[1]) / 3
    assert out[-1, 0, 0] == x[-1] - (x[-2] + 2 * x[-1]) / 3


# ----------------------------------------------- 4. multi-channel / axes


def test_channels_and_time_axes_do_not_crosstalk() -> None:
    scan = _trace_ramp_scan(traces=7, channels=2, n_time=4)
    stage = FlatReflectionFilterStage(window_traces=5)
    whole = stage.apply(scan, clock=_stamp()).source.data
    # Per-channel single-channel scans processed separately match bitwise.
    for c in range(2):
        sub_data = scan.data[:, c : c + 1, :]
        sub = _scan(
            sub_data.copy(),
            channels=(scan.channels[c],),
            history=scan.history,
            time_axis_s=scan.time_axis_s,
        )
        got = stage.apply(sub, clock=_stamp()).source.data
        assert np.array_equal(got[:, 0, :], whole[:, c, :])


def test_axis_direction_is_trace_not_time() -> None:
    # Counter-example pinning "along dimension 0, not the last axis": each
    # row varies only along trace (constant over time), so a trace-axis
    # filter annihilates interior rows while a time-axis filter would be a
    # no-op (each time row is constant).
    data = np.zeros((5, 1, 5), dtype=np.complex128)
    data[:, 0, :] = np.arange(5, dtype=np.float64)[:, None]
    scan = _scan(data, time_axis_s=_axis(5))
    out = FlatReflectionFilterStage(window_traces=3).apply(scan, clock=_stamp()).source.data
    assert np.allclose(out[2, 0, :].real, 0.0, atol=1e-12)  # interior fully removed
    assert float(np.max(np.abs(out))) < 1.0  # only edge tiling bias survives


def test_metadata_passthrough() -> None:
    from uav_gpr.core.enums import TraceQualityReason

    def md(i: int) -> TraceMetadata:
        return TraceMetadata(
            mission_id=MissionId(uuid.UUID(int=1)),
            trace_index=i,
            trace_uid=TraceUid(uuid.UUID(int=100 + i)),
            device_id=DeviceId(uuid.UUID(int=2)),
            sweep_started_utc=CREATED_UTC,
            sweep_midpoint_utc=CREATED_UTC,
            sweep_finished_utc=CREATED_UTC,
            sweep_started_monotonic_ns=MonotonicNs(1_000_000_000),
            sweep_midpoint_monotonic_ns=MonotonicNs(1_050_000_000),
            sweep_finished_monotonic_ns=MonotonicNs(1_100_000_000),
            target_interval_s=1e-3,
            actual_interval_s=None if i == 0 else 1e-3,
            schedule_error_s=None if i == 0 else 0.0,
            connection_generation=1,
            raw_trace_sha256=None,
            gnss_match=None,
            quality_status=TraceQualityStatus.DEGRADED,
            quality_reasons=(TraceQualityReason.GNSS_MISSING,),
        )

    traces = 5
    scan = TimeDomainScan(
        channels=(HH_S11,),
        time_axis_s=_axis(N_TIME),
        data=np.zeros((traces, 1, N_TIME), dtype=np.complex128),
        kind=TimeDomainKind.TIME_BASE,
        history=_base_history(),
        metadata=tuple(md(i) for i in range(traces)),
    )
    result = FlatReflectionFilterStage(window_traces=3).apply(scan, clock=_stamp())
    assert result.source.metadata == scan.metadata
    assert result.source.channels == scan.channels
    assert np.array_equal(result.source.time_axis_s, scan.time_axis_s)


# ----------------------------------------------------- 5. short lines etc


def test_short_line_rejected_with_guidance() -> None:
    scan = _scan(np.zeros((3, 1, 4), dtype=np.complex128))
    with pytest.raises(DomainError) as exc:
        FlatReflectionFilterStage(window_traces=5).apply(scan, clock=_stamp())
    assert exc.value.code is ErrorCode.INVALID_ARGUMENT
    assert "exceeds the total trace count 3" in exc.value.message
    assert exc.value.context["n_traces"] == 3


def test_window_equal_to_trace_count_is_legal() -> None:
    scan = _trace_ramp_scan(traces=5, channels=1)
    result = FlatReflectionFilterStage(window_traces=5).apply(scan, clock=_stamp())
    out = result.source.data
    # W == n with edge tiling: row i's window is the clipped multiset
    # {x[i-2], x[i-1], x[i], x[i+1], x[i+2]} (indices clamped to [0, 4]), so
    # every row differs; pin each mean against an explicit clipped-sum model.
    data = scan.data
    n = data.shape[0]
    for i in range(n):
        window = [data[min(max(i + s, 0), n - 1), 0, :] for s in (-2, -1, 0, 1, 2)]
        expected = sum(window) / 5.0
        assert np.allclose(out[i, 0, :], data[i, 0, :] - expected, rtol=0, atol=1e-12)


def test_non_scan_input_type_error() -> None:
    with pytest.raises(TypeError, match="TimeDomainScan"):
        FlatReflectionFilterStage(window_traces=3).apply(object())
    with pytest.raises(TypeError):
        FlatReflectionFilterStage(window_traces=3).apply(
            _trace_ramp_scan(), history=["not-a-history"]  # type: ignore[arg-type]
        )


def test_naive_executed_utc_rejected() -> None:
    from datetime import datetime as dt

    with pytest.raises(DomainError):
        FlatReflectionFilterStage(window_traces=3).apply(
            _trace_ramp_scan(traces=5), executed_utc=dt(2026, 1, 1)
        )


# --------------------------------------------------- 6. window edge cases


def test_minimal_window_three_hand_computed() -> None:
    # W=3 edge: mean[i] = (data[min(max(i-1,0),N-1)] + data[i]
    #              + data[min(max(i+1,0),N-1)]) / 3 — hand-pinned tiny case.
    data = np.zeros((3, 1, 1), dtype=np.complex128)
    data[0, 0, 0] = 1.0 + 1.0j
    data[1, 0, 0] = 2.0 + 0.0j
    data[2, 0, 0] = 0.0 - 3.0j
    scan = _scan(data, time_axis_s=_axis(1))
    out = FlatReflectionFilterStage(window_traces=3).apply(scan, clock=_stamp()).source.data
    m0 = ((1 + 1j) * 2 + (2 + 0j)) / 3
    m1 = ((1 + 1j) + (2 + 0j) + (0 - 3j)) / 3
    m2 = ((2 + 0j) + (0 - 3j) * 2) / 3
    assert out[0, 0, 0] == (1 + 1j) - m0
    assert out[1, 0, 0] == (2 + 0j) - m1
    assert out[2, 0, 0] == (0 - 3j) - m2


def test_edge_tiling_semantics_vs_reflect_counterexample() -> None:
    # On an asymmetric first row, edge vs reflect means differ: pin EDGE.
    data = _scene_a()
    edge = centered_moving_mean_along_axis(data, axis=0, window=3, padding="edge")
    reflect = centered_moving_mean_along_axis(data, axis=0, window=3, padding="reflect")
    assert not np.array_equal(edge, reflect)
    # Edge first-row mean uses x0 twice (tiling), reflect uses x1 (mirror).
    x0 = data[0]
    x1 = data[1]
    assert np.array_equal(edge[0], (2 * x0 + x1) / 3)
    assert np.array_equal(reflect[0], (x1 + x0 + x1) / 3)


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"axis": 0, "window": 2, "padding": "edge"}, ErrorCode.INVALID_ARGUMENT),
        ({"axis": 0, "window": 1, "padding": "edge"}, ErrorCode.INVALID_ARGUMENT),
        ({"axis": 0, "window": True, "padding": "edge"}, ErrorCode.INVALID_ARGUMENT),
        ({"axis": 0, "window": 3, "padding": "wrap"}, ErrorCode.INVALID_ARGUMENT),
        ({"axis": 9, "window": 3, "padding": "edge"}, ErrorCode.INVALID_ARGUMENT),
        ({"axis": 0, "window": 7, "padding": "edge"}, ErrorCode.INVALID_ARGUMENT),  # > 5 rows
    ],
)
def test_kernel_guard_matrix(kwargs: dict, code: ErrorCode) -> None:
    with pytest.raises(DomainError) as exc:
        centered_moving_mean_along_axis(_scene_a(), **kwargs)
    assert exc.value.code is code


def test_kernel_dtype_guard() -> None:
    with pytest.raises(DomainError) as exc:
        centered_moving_mean_along_axis(
            np.ones((5, 1, 4), dtype=np.float64), axis=0, window=3, padding="edge"
        )
    assert exc.value.code is ErrorCode.DTYPE_MISMATCH


# ------------------------------------------------ 7. order & history law


def test_empty_history_refused() -> None:
    scan = _trace_ramp_scan(traces=5)
    with pytest.raises(DomainError) as exc:
        FlatReflectionFilterStage(window_traces=3).apply(
            scan, history=_history(), clock=_stamp()
        )
    assert exc.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH


def test_frequency_predecessor_refused() -> None:
    scan = _trace_ramp_scan(traces=5)
    bad = _history(
        _osl_record(),
    )
    with pytest.raises(DomainError) as exc:
        FlatReflectionFilterStage(window_traces=3).apply(
            scan, history=bad, clock=_stamp()
        )
    assert exc.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH


def test_duplicate_flat_refused_twice() -> None:
    scan = _trace_ramp_scan(traces=5)
    stage = FlatReflectionFilterStage(window_traces=3)
    first = stage.apply(scan, clock=_stamp())
    # Gate 1: stage-level explicit refusal on re-entry (034 pattern: feed the
    # real output back with its own appended history).
    with pytest.raises(DomainError) as exc:
        stage.apply(first.source, history=first.history, clock=_stamp())
    assert exc.value.code is ErrorCode.INVALID_ARGUMENT
    assert "only once per history" in exc.value.message
    assert exc.value.context["stage_name"] == FLAT_STAGE_NAME
    # Gate 2: core history uniqueness as an isolated second probe - even a
    # bumped stage_version cannot bypass it.
    base = _history(_ifft_record(), _flat_self_record(TIME_BASE))
    with pytest.raises(DomainError) as exc:
        base.append(_flat_self_record(TIME_PROCESSED, version="99.0"))
    assert exc.value.code is ErrorCode.INVALID_ARGUMENT
    assert exc.value.context["stage_name"] == FLAT_STAGE_NAME


def test_recommended_order_dewow_then_flat_end_to_end() -> None:
    # Chain the REAL stages: ifft-shaped history -> DewowStage -> flat.
    scan = _trace_ramp_scan(traces=9, channels=1)
    dewowed = DewowStage(window_s=3 * DT_S).apply(scan, clock=_stamp())
    assert dewowed.source.kind is TimeDomainKind.TIME_PROCESSED
    flat = FlatReflectionFilterStage(window_traces=5).apply(dewowed.source, clock=_stamp())
    names = [r.stage_name for r in flat.history.records]
    assert names == ["frequency_to_time_ifft", "dewow", "flat_reflection_filter"]
    domains = [(r.input_domain, r.output_domain) for r in flat.history.records[1:]]
    assert domains == [(TIME_BASE, TIME_PROCESSED), (TIME_PROCESSED, TIME_PROCESSED)]
    assert flat.domain == TIME_PROCESSED


def test_time_base_direct_flat_is_legal() -> None:
    # TIME_BASE -> TIME_PROCESSED hop without dewow is allowed (flat optional
    # after IFFT; orchestration decides).
    scan = _trace_ramp_scan(traces=7)
    result = FlatReflectionFilterStage(window_traces=5).apply(scan, clock=_stamp())
    rec = result.history.records[-1]
    assert rec.input_domain is TIME_BASE
    assert rec.output_domain is TIME_PROCESSED


def test_record_fields_and_round_trip() -> None:
    scan = _trace_ramp_scan(traces=5)
    result = FlatReflectionFilterStage(window_traces=3).apply(
        scan, executed_utc=datetime(2026, 8, 1, 12, tzinfo=UTC)
    )
    rec = result.history.records[-1]
    assert rec.stage_name == FLAT_STAGE_NAME
    assert rec.executed_utc == datetime(2026, 8, 1, 12, tzinfo=UTC)
    assert set(rec.parameters) == {
        "operation",
        "axis",
        "padding",
        "window_traces",
        "reference_source_sha256",
        "trace_sample_count",
    }
    assert rec.parameters["trace_sample_count"] == 5
    dumped = result.history.to_dict()
    restored = ProcessingHistory.from_dict(json.loads(json.dumps(dumped)))
    assert restored.to_dict() == dumped


def test_not_air_background_distinct_everywhere() -> None:
    # Name/token/domain/object separation (CALIBRATION.md L9-10) + legal
    # coexistence in one long history chain.  The scan entering flat carries
    # the whole archived chain as its own history (kind time_processed), so
    # apply() derives the predecessor from source.history itself.
    from uav_gpr.processing.background_subtraction import AIR_BACKGROUND_STAGE_NAME

    assert AIR_BACKGROUND_STAGE_NAME == "air_background_subtraction"
    assert AIR_BACKGROUND_STAGE_NAME != FLAT_STAGE_NAME
    long_chain = _history(
        _osl_record(),
        _air_bg_record(),
        _bandpass_record(),
        _record(stage="frequency_to_time_ifft", input_domain=FILTERED, output_domain=TIME_BASE),
        _dewow_record(TIME_BASE),
    )
    scan = _trace_ramp_scan(traces=5, channels=1)
    proc_scan = TimeDomainScan(
        channels=scan.channels,
        time_axis_s=scan.time_axis_s,
        data=scan.data,
        kind=TimeDomainKind.TIME_PROCESSED,
        history=long_chain,
        metadata=scan.metadata,
    )
    result = FlatReflectionFilterStage(window_traces=3).apply(
        proc_scan, clock=_stamp()
    )
    names = [r.stage_name for r in result.history.records]
    assert names.count("air_background_subtraction") == 1
    assert names.count(FLAT_STAGE_NAME) == 1
    assert names.index("air_background_subtraction") < names.index(FLAT_STAGE_NAME)


def test_kind_mismatch_defense_in_depth() -> None:
    # A TIME_PROCESSED-kind scan carrying a time_base-ending history is
    # refused by guard 3 before any math runs.
    scan_proc = _trace_ramp_scan(traces=5, channels=1)
    stage = FlatReflectionFilterStage(window_traces=3)
    good = stage.apply(scan_proc, clock=_stamp()).source  # TIME_PROCESSED kind
    with pytest.raises(DomainError) as exc:
        stage.apply(good, history=_history(_ifft_record()), clock=_stamp())
    assert exc.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH


# ------------------------------------------------------ 8. immutability


def test_input_never_mutated_output_is_new_protected() -> None:
    scan = _trace_ramp_scan(traces=7, channels=2)
    before = scan.data.tobytes()
    assert scan.data.flags.writeable is False
    result = FlatReflectionFilterStage(window_traces=5).apply(scan, clock=_stamp())
    assert scan.data.tobytes() == before
    out = result.source.data
    assert out is not scan.data
    assert out.flags.writeable is False
    with pytest.raises(ValueError):
        out[0, 0, 0] = 1.0 + 0.0j
    # old history object untouched; append returned a new instance
    assert len(scan.history) == 1
    assert len(result.history) == 2
    assert result.history is not scan.history


# --------------------------------------------------------- 9. goldens


def test_golden_scene_a_mean_exact() -> None:
    mean = centered_moving_mean_along_axis(_scene_a(), axis=0, window=3, padding="edge")
    assert mean.real.tolist() == A_MEAN_RE
    assert mean.imag.tolist() == A_MEAN_IM


def test_golden_scene_b_full_buffer_bitexact() -> None:
    b = _scene_b()
    mean = centered_moving_mean_along_axis(b, axis=0, window=3, padding="edge")
    assert mean.real.tolist() == B_MEAN_RE
    assert mean.imag.tolist() == B_MEAN_IM
    assert (b - mean).real.tolist() == B_OUT_RE
    assert (b - mean).imag.tolist() == B_OUT_IM


def test_golden_scene_c_boundary_exact() -> None:
    mean = centered_moving_mean_along_axis(_scene_c(), axis=0, window=3, padding="edge")
    assert mean.real.tolist() == C_MEAN_RE
    assert mean.imag.tolist() == C_MEAN_IM


def test_golden_canonical_digest_pinned() -> None:
    doc = {
        "A_in_re": A_IN_RE,
        "A_in_im": A_IN_IM,
        "A_mean_re": A_MEAN_RE,
        "A_mean_im": A_MEAN_IM,
        "B_in_re": B_IN_RE,
        "B_in_im": B_IN_IM,
        "B_mean_re": B_MEAN_RE,
        "B_mean_im": B_MEAN_IM,
        "B_out_re": B_OUT_RE,
        "B_out_im": B_OUT_IM,
        "C_in_re": C_IN_RE,
        "C_in_im": C_IN_IM,
        "C_mean_re": C_MEAN_RE,
        "C_mean_im": C_MEAN_IM,
    }
    canon = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert hashlib.sha256(canon.encode("utf-8")).hexdigest() == GOLDEN_CANONICAL_SHA256


def test_naive_transcription_bitexact_on_dyadic_buffer() -> None:
    # Independent O(N*W) clip-index loop (leg 3 of the golden cross-check):
    # on the dyadic scene-B buffer summation order differences vanish, so
    # bit-exact equality is the honest expectation.
    def naive(data: np.ndarray, window: int) -> np.ndarray:
        half = window // 2
        n = data.shape[0]
        res = np.empty_like(data)
        for i in range(n):
            acc = np.zeros(data.shape[1:], dtype=np.complex128)
            for j in range(-half, half + 1):
                acc += data[min(max(i + j, 0), n - 1)]
            res[i] = acc / window
        return res

    b = _scene_b()
    vec = centered_moving_mean_along_axis(b, axis=0, window=3, padding="edge")
    assert naive(b, 3).tobytes() == vec.tobytes()


# ----------------------------------------------------- 10. perf smoke


def test_performance_smoke_o_n() -> None:
    big = np.zeros((512, 2, 1024), dtype=np.complex128)
    idx = np.arange(512, dtype=np.float64)
    big[:, :, :] = idx[:, None, None] + 1j * 0.5 * idx[:, None, None]
    scan = _scan(big, channels=(HH_S11, VV_S22), time_axis_s=_axis(1024))
    started = time.monotonic()
    result = FlatReflectionFilterStage(window_traces=101).apply(scan, clock=_stamp())
    elapsed = time.monotonic() - started
    assert result.source.data.shape == big.shape
    assert elapsed < 10.0  # generous wall-clock bound, proves vectorized O(N)


# -------------------------------------------------- 11. exclusion guards


def test_no_reference_or_forbidden_imports() -> None:
    import ast
    from pathlib import Path

    src = Path("src/uav_gpr/processing/flat_reflection.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    forbidden = {"rebar_inspector", "storage", "acquisition", "PySide6", "pyqtgraph", "matplotlib"}
    assert not roots & forbidden
    assert "ui" not in roots
    # No realtime incremental state caching: apply is a pure function of
    # (source, history) - every ``self.x = ...`` assignment lives inside
    # __init__ only.
    init_nodes = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "__init__"
    ]
    assignments = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and any(
            isinstance(t, ast.Attribute)
            and isinstance(t.value, ast.Name)
            and t.value.id == "self"
            for t in n.targets
        )
    ]
    inside_init = {id(x) for fn in init_nodes for x in ast.walk(fn)}
    assert all(id(n) in inside_init for n in assignments)
    assert assignments  # __init__ does configure itself; guard is live


def test_no_ui_autoenable_symbols() -> None:
    import inspect as ins

    mod = ins.getmodule(FlatReflectionFilterStage)
    public = {n for n in dir(mod) if not n.startswith("_")}
    banned = {"run_pipeline", "process_all", "orchestrate", "register_default", "DEFAULT_ENABLED"}
    assert not public & banned
