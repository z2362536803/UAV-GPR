"""Contract tests for ISSUE-034: Dewow time-domain stage.

Pure deterministic tests: synthetic fixed-formula inputs only - no hardware,
no threads, no runtime RNG.

Contract summary (docs/issues/M06_CALIBRATION_PROCESSING.md ISSUE-034,
docs/PROCESSING.md sections 2/5, docs/DATA_MODEL.md L152,
docs/reports/ISSUE_034_BASELINE_CONFIRMATION.md section 3 and
docs/plans/2026-09-05-issue-034-dewow.md D1-D9):

- ``DewowStage`` structurally satisfies the frozen ISSUE-030
  ``ProcessingStage`` protocol over the ISSUE-007 history: stable
  ``stage_name="dewow"``, accepted input domains {time_base, time_processed},
  output domain ``time_processed``; it subtracts a centered moving average
  along the LAST (time) axis of an immutable ``TimeDomainScan`` and returns a
  fresh ``TimeDomainScan(kind=time_processed)`` plus ``TimeDomainStageResult``
  (the ISSUE-031 sibling result type);
- migrated math is the rebar-inspector contract verbatim
  (processing/dewow.py SHA-256
  eb6690e7fabf0bc80e051831ab6264e6e6d112b6568fb6dc30556a3a7f030e2c and
  processing/_time_stage_common.py SHA-256
  e0c201b55acbaece0edb1546bbb8a00492874bb79fb9caf789d5ba416d333c81, verified
  against docs/reference-baselines/manifest.json in t1): seconds->samples
  rounding chain round -> max(1) -> oddify -> reject ==1 -> reject > n_time,
  fixed "reflect" boundary padding, O(N) cumulative sums in complex128
  (equivalent to processing real and imaginary parts independently);
- golden literals below were produced on 2026-09-05 by re-evaluating that
  reference algorithm verbatim in this project venv (plan D9 scene A/B/C;
  canonical golden JSON SHA-256
  e5e2486180697b7338c2e96137714aa18a76d1ad6ea03b34c50c79b1852231b8);
- fail-closed matrix: empty/frequency/out-of-order histories raise
  PROCESSING_DOMAIN_MISMATCH, duplicate dewow is refused by the stage gate
  and independently by core history uniqueness (a bumped stage_version does
  not bypass), flat-before-dewow is refused, non-finite data/axes, short
  axes, undersized and oversized windows raise structured DomainErrors;
- inputs are never mutated: the source scan buffers stay byte-identical and
  write-protected; channels / time axis / per-trace metadata are preserved.
"""

from __future__ import annotations

import dataclasses
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
from uav_gpr.processing.dewow import (
    DEFAULT_DEWOW_WINDOW_S,
    DEWOW_AXIS_TOLERANCE_REL,
    DEWOW_PADDING,
    DEWOW_STAGE_NAME,
    DEWOW_STAGE_VERSION,
    DewowStage,
    centered_moving_mean,
    derive_sample_interval_s,
    window_samples_for,
)
from uav_gpr.processing.time_domain import TimeDomainStageResult

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
        stage="frequency_to_time_ifft",
        input_domain=RAW,
        output_domain=TIME_BASE,
    )


def _dewow_record(
    input_domain: DataDomain = TIME_BASE,
    *,
    version: str = "1.0",
) -> ProcessingRecord:
    return _record(
        stage="dewow",
        version=version,
        input_domain=input_domain,
        output_domain=TIME_PROCESSED,
    )


def _flat_record() -> ProcessingRecord:
    return _record(
        stage="flat_reflection_filter",
        input_domain=TIME_BASE,
        output_domain=TIME_PROCESSED,
    )


def _history(*records: ProcessingRecord) -> ProcessingHistory:
    return ProcessingHistory(records)


#: Golden dt of every default synthetic scan: a 1 ns sampling grid.
DT_S = 1e-9
N_TIME = 16
AXIS_S = np.arange(N_TIME, dtype=np.float64) * DT_S


def _base_history() -> ProcessingHistory:
    return _history(_ifft_record())


def _axis(n: int, dt: float = DT_S) -> np.ndarray:
    return np.arange(n, dtype=np.float64) * dt


def _base_history_for(n: int) -> ProcessingHistory:
    return _history(_ifft_record()) if n >= 2 else _history(
        _record(stage="legacy_ifft", input_domain=RAW, output_domain=TIME_BASE)
    )


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
            history = _base_history_for(n_time)
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


def _legacy_dcgate_record() -> ProcessingRecord:
    """A foreign time_processed producer (not dewow, not flat)."""
    return _record(
        stage="legacy_dcgate",
        input_domain=TIME_BASE,
        output_domain=TIME_PROCESSED,
    )


def _ramp_scan(traces: int = 2, channels: int = 2) -> TimeDomainScan:
    i, j, k = np.meshgrid(
        np.arange(traces), np.arange(channels), np.arange(N_TIME), indexing="ij"
    )
    data = (i + 0.5 * j + 0.25 * k + 1j * (0.1 * k - j)).astype(np.complex128)
    return _scan(data, channels=(HH_S11, VV_S22)[:channels])


STAGE = DewowStage()

# ------------------------------------------------------- reference digests

DEWOW_REFERENCE_SHA256 = (
    "eb6690e7fabf0bc80e051831ab6264e6e6d112b6568fb6dc30556a3a7f030e2c"
)
COMMON_REFERENCE_SHA256 = (
    "e0c201b55acbaece0edb1546bbb8a00492874bb79fb9caf789d5ba416d333c81"
)

# ------------------------------------------------------------------ golden
# Scene A: single trace x one channel x 8 samples, dt=1 s, window_s=3 ->
# W=3.  Values
# reproduced 2026-09-05 from a verbatim port of the frozen reference
# _centered_moving_mean in this venv (plan D9 leg 2).
GOLDEN_AXIS_A = np.arange(8, dtype=np.float64)
GOLDEN_DATA_A_RE = [
    1.0,
    0.5403023058681398,
    -0.4161468365471424,
    -0.9899924966004454,
    -0.6536436208636119,
    0.28366218546322625,
    0.960170286650366,
    0.7539022543433046,
]
GOLDEN_DATA_A_IM = [
    0.0,
    0.22732435670642043,
    -0.18920062382698205,
    -0.06985387454973147,
    0.24733956165584545,
    -0.13600527772234244,
    -0.13414322950010873,
    0.2476518389237176,
]
GOLDEN_MEAN_A_RE = [
    0.6935348705787598,
    0.3747184897736658,
    -0.28861234242648265,
    -0.6865943180037332,
    -0.453324644000277,
    0.19672961708332673,
    0.6659115754856322,
    0.8914142758813455,
]
GOLDEN_MEAN_A_IM = [
    0.1515495711376136,
    0.012707910959812791,
    -0.010576713890097695,
    -0.003904978906956032,
    0.013826803127923837,
    -0.007602981855535253,
    -0.007498889432911196,
    -0.006878206692166636,
]
GOLDEN_OUT_A_RE = [
    0.30646512942124016,
    0.16558381609447398,
    -0.12753449412065976,
    -0.30339817859671225,
    -0.20031897686333494,
    0.08693256837989952,
    0.2942587111647338,
    -0.13751202153804087,
]
GOLDEN_OUT_A_IM = [
    -0.1515495711376136,
    0.21461644574660763,
    -0.17862390993688435,
    -0.06594889564277544,
    0.2335127585279216,
    -0.12840229586680718,
    -0.12664434006719755,
    0.2545300456158842,
]

# Scene B: full 2x2x6 buffer, dt=2 s, window_s=10 -> exactly 5 samples.
# The kernel accumulates via pairwise-safe cumulative sums; transcriptions
# with a different summation ORDER can differ by <= ~2 ulp per sample, so
# independent re-transcriptions compare with exact equality only on the
# dyadic quarter-grid probe below and tolerance elsewhere (plan D9).
GOLDEN_DT_B = 2.0
GOLDEN_AXIS_B = np.arange(6, dtype=np.float64) * GOLDEN_DT_B
GOLDEN_OUT_B_RE = [
    -0.4, -0.4000000000000001, -0.40000000000000013, -0.40000000000000036,
    0.3999999999999999, 2.0,
    -0.40000000000000013, -0.40000000000000013, -0.40000000000000013,
    -0.40000000000000036, 0.3999999999999999, 2.0,
    -0.40000000000000013, -0.40000000000000013, -0.40000000000000013,
    -0.40000000000000036, 0.3999999999999999, 2.0,
    -0.40000000000000036, -0.3999999999999999, -0.40000000000000036,
    -0.40000000000000036, 0.39999999999999947, 2.0,
]
GOLDEN_OUT_B_IM = [
    -1.2000000000000002, -0.40000000000000013, 0.0, 0.0, 0.3999999999999999,
    1.1999999999999997,
    -1.2, -0.3999999999999999, 0.0, 0.0, 0.3999999999999999, 1.2,
    -1.2000000000000002, -0.40000000000000013, 0.0, 0.0, 0.39999999999999947,
    1.2000000000000002,
    -1.2, -0.4, 0.0, 0.0, 0.3999999999999999, 1.1999999999999997,
]
GOLDEN_MEAN_B_RE = [
    0.4, 0.6000000000000001, 1.2000000000000002, 2.2, 2.8000000000000003, 3.0,
    1.4000000000000001, 1.6, 2.2, 3.2, 3.8000000000000003, 4.0,
    1.4000000000000001, 1.6, 2.2, 3.2, 3.8000000000000003, 4.0,
    2.4000000000000004, 2.6, 3.2, 4.2, 4.800000000000001, 5.0,
]
GOLDEN_MEAN_B_IM = [
    1.2000000000000002, 1.4000000000000001, 2.0, 3.0, 3.6, 3.8000000000000003,
    -0.8, -0.6000000000000001, 0.0, 1.0, 1.6, 1.8,
    1.7000000000000002, 1.9000000000000001, 2.5, 3.5, 4.1000000000000005, 4.3,
    -0.30000000000000004, -0.1, 0.5, 1.5, 2.1, 2.3000000000000003,
]

# Golden scene B input buffer as EXACT float64 literals (same provenance as
# the mean/out literals above: frozen verbatim-kernel run of 2026-09-05).
_G_B_RE = np.array(
    [
        0.0, 0.2, 0.8, 1.7999999999999998, 3.2, 5.0,
        1.0, 1.2, 1.8, 2.8, 4.2, 6.0,
        1.0, 1.2, 1.8, 2.8, 4.2, 6.0,
        2.0, 2.2, 2.8, 3.8, 5.2, 7.0,
    ],
    dtype=np.float64,
).reshape(2, 2, 6)
_G_B_IM = np.array(
    [
        0.0, 1.0, 2.0, 3.0, 4.0, 5.0,
        -2.0, -1.0, 0.0, 1.0, 2.0, 3.0,
        0.5, 1.5, 2.5, 3.5, 4.5, 5.5,
        -1.5, -0.5, 0.5, 1.5, 2.5, 3.5,
    ],
    dtype=np.float64,
).reshape(2, 2, 6)
GOLDEN_DATA_B = (_G_B_RE + 1j * _G_B_IM).astype(np.complex128)

# Scene C: hand-checked reflect boundary on a 4-sample row, W=3.
GOLDEN_DATA_C = [1 + 1j, 2 - 0.5j, -3 + 2j, 0.25 - 4j]
GOLDEN_MEAN_C = [
    1.6666666666666665 + 0.0j,
    0.0 + 0.8333333333333333j,
    -0.25 - 0.8333333333333333j,
    -1.9166666666666665 + 0.0j,
]
GOLDEN_OUT_C = [
    -0.6666666666666665 + 1.0j,
    2.0 - 1.3333333333333333j,
    -2.75 + 2.833333333333333j,
    2.1666666666666665 - 4.0j,
]


def _naive_centered_mean(x: np.ndarray, window: int) -> np.ndarray:
    """Independent O(N*W) transcription with explicit reflection indexing."""
    x = np.asarray(x, dtype=np.complex128)
    n = x.shape[-1]
    half = window // 2
    out = np.empty_like(x)
    for t in range(n):
        acc = np.zeros(x.shape[:-1], dtype=np.complex128)
        for offset in range(-half, half + 1):
            idx = t + offset
            if idx < 0:
                idx = -idx  # reflect without edge duplication
            elif idx >= n:
                idx = 2 * (n - 1) - idx
            acc += x[..., idx]
        out[..., t] = acc / window
    return out


# ------------------------------------------------- protocol & construction


def test_stage_structurally_satisfies_frozen_protocol() -> None:
    assert isinstance(STAGE, ProcessingStage)
    assert STAGE.stage_name == DEWOW_STAGE_NAME == "dewow"
    assert STAGE.stage_version == DEWOW_STAGE_VERSION == "1.0"
    assert STAGE.input_domain == frozenset({TIME_BASE, TIME_PROCESSED})
    assert STAGE.output_domain is TIME_PROCESSED


def test_default_window_matches_reference_four_ns_in_seconds() -> None:
    # Reference default was 4.0 ns; the migrated contract expresses seconds.
    assert DEFAULT_DEWOW_WINDOW_S == pytest.approx(4e-9)
    assert DewowStage().window_s == DEFAULT_DEWOW_WINDOW_S


@pytest.mark.parametrize(
    "bad",
    [True, False, "4e-9", None, [], float("nan"), float("inf"), -float("inf"), 0.0, -1e-9],
)
def test_invalid_window_s_rejected(bad: object) -> None:
    with pytest.raises(DomainError) as info:
        DewowStage(window_s=bad)  # type: ignore[arg-type]
    assert info.value.code is ErrorCode.INVALID_ARGUMENT


def test_apply_rejects_non_scan_sources() -> None:
    for junk in (None, 42, "dewow", np.zeros((2, 2)), object()):
        with pytest.raises(TypeError):
            STAGE.apply(junk, history=_base_history())  # type: ignore[arg-type]


# ------------------------------------------------------------ numeric core


def test_constant_field_maps_to_zero() -> None:
    data = np.full((3, 2, N_TIME), 4.0 - 2.5j, dtype=np.complex128)
    result = STAGE.apply(_scan(data, channels=(HH_S11, VV_S22)))
    assert float(np.max(np.abs(result.source.data))) < 1e-12


def test_pulse_response_pins_reflect_boundary() -> None:
    # delta at sample 1, W=3: mean has support only around the pulse; the
    # leading boundary rows keep the hand-derived 1/3 tails.
    data = np.zeros((1, 1, 8), dtype=np.complex128)
    data[0, 0, 1] = 1.0 + 1.0j
    stage = DewowStage(window_s=3.0 * DT_S)
    out = stage.apply(_scan(data)).source.data
    third = (1 + 1j) / 3
    expected_mean = np.zeros(8, dtype=np.complex128)
    expected_mean[0] = third + third  # window [x1, x0, x1]: pulse counted twice
    expected_mean[1] = third
    expected_mean[2] = third
    expected = data[0, 0] - expected_mean
    assert np.array_equal(out[0, 0], expected)
    assert np.all(out[0, 0, 3:] == 0.0)


def test_complex_linearity_real_imag_independent() -> None:
    scan = _ramp_scan()
    combined = STAGE.apply(scan).source.data
    real_part = np.ascontiguousarray(scan.data.real, dtype=np.float64)
    imag_part = np.ascontiguousarray(scan.data.imag, dtype=np.float64)
    # Default window: round(4 ns / 1 ns) = 4 -> oddified to 5 samples.
    mean_r = centered_moving_mean(real_part.astype(np.complex128), window=5)
    mean_i = centered_moving_mean(imag_part.astype(np.complex128), window=5)
    assert np.array_equal(combined.real, real_part - mean_r.real)
    assert np.array_equal(combined.imag, imag_part - mean_i.real)


def _quarter_grid_buffer() -> np.ndarray:
    # Values are multiples of 2^-k small enough that every +/- c shift,
    # every window partial sum and every /5 division stay exact in float64,
    # making the mathematical translation-invariance of moving averages a
    # BIT-level identity on this buffer.
    i, j, k = np.meshgrid(
        np.arange(2), np.arange(2), np.arange(6), indexing="ij"
    )
    return (
        (i + j) * 0.3125
        + k * 0.15625
        + 1j * (k * 0.3125 - j * 0.15625)
    ).astype(np.complex128)


def test_constant_skew_is_bit_exact_on_dyadic_probe() -> None:
    q = _quarter_grid_buffer()
    axis = np.arange(6, dtype=np.float64) * GOLDEN_DT_B
    plain = q - centered_moving_mean(q, window=5)
    skew = q + np.array([3.0 + 4.0j, -1.0 + 0.5j])[None, :, None]
    skewed = skew - centered_moving_mean(skew, window=5)
    assert np.array_equal(skewed, plain)
    # and through the full stage entry on the same buffer
    scan = _scan(q, channels=(HH_S11, VV_S22), time_axis_s=axis)
    result = DewowStage(window_s=10.0).apply(scan)
    assert np.array_equal(result.source.data, plain)


def test_general_skew_matches_within_float_noise() -> None:
    # On arbitrary buffers the same invariance holds up to summation-order
    # rounding (<= ~4 ulp): asserting tolerance here keeps the mathematical
    # claim honest without pinning IEEE addition order.
    base = _ramp_scan(traces=2, channels=2)
    plain = STAGE.apply(base).source.data
    skew = base.data + np.array([3.0 + 4.0j, -1.0 + 0.5j])[None, :, None]
    skewed = STAGE.apply(_scan(skew, channels=(HH_S11, VV_S22))).source.data
    assert np.max(np.abs(skewed - plain)) < 1e-12


def test_golden_scene_a_exact_literals() -> None:
    data = (
        np.array(GOLDEN_DATA_A_RE, dtype=np.float64)
        + 1j * np.array(GOLDEN_DATA_A_IM, dtype=np.float64)
    ).astype(np.complex128)
    mean = centered_moving_mean(data[np.newaxis, np.newaxis, :], window=3)
    assert mean[0, 0].real.tolist() == GOLDEN_MEAN_A_RE
    assert mean[0, 0].imag.tolist() == GOLDEN_MEAN_A_IM
    out = data - mean[0, 0]
    assert out.real.tolist() == GOLDEN_OUT_A_RE
    assert out.imag.tolist() == GOLDEN_OUT_A_IM


def test_golden_scene_b_full_buffer_via_stage_apply() -> None:
    # window_s=10 over dt=2 -> exactly 5 samples (odd, no adjust).
    scan = _scan(
        GOLDEN_DATA_B,
        channels=(HH_S11, VV_S22),
        time_axis_s=GOLDEN_AXIS_B,
    )
    result = DewowStage(window_s=10.0).apply(scan)
    assert result.domain is TIME_PROCESSED
    assert result.source.data.real.ravel().tolist() == GOLDEN_OUT_B_RE
    assert result.source.data.imag.ravel().tolist() == GOLDEN_OUT_B_IM


def test_golden_scene_c_hand_checked_boundary_row() -> None:
    data = np.array(GOLDEN_DATA_C, dtype=np.complex128)[np.newaxis, np.newaxis, :]
    mean = centered_moving_mean(data, window=3)
    assert np.array_equal(mean[0, 0], np.array(GOLDEN_MEAN_C, dtype=np.complex128))
    assert np.array_equal(
        data[0, 0] - mean[0, 0], np.array(GOLDEN_OUT_C, dtype=np.complex128)
    )


def test_independent_transcriptions_match_kernel() -> None:
    # Cross-check legs against the pinned vectorized kernel on scene B:
    # a naive reflect-index loop reproduces it BIT-EXACTLY on this buffer,
    # and an np.convolve transcription agrees within last-bit summation noise
    # (transcription legs prove the formula, not the accumulation order).
    window = 5
    vec = centered_moving_mean(GOLDEN_DATA_B, window=window)
    naive = _naive_centered_mean(GOLDEN_DATA_B, window)
    assert np.array_equal(naive.real, vec.real)
    assert np.array_equal(naive.imag, vec.imag)
    n = GOLDEN_DATA_B.shape[-1]
    half = window // 2
    pad = [(0, 0)] * GOLDEN_DATA_B.ndim
    pad[-1] = (half, half)
    padded = np.pad(GOLDEN_DATA_B, pad, mode="reflect")
    ones = np.ones(window, dtype=np.float64)
    conv_re = np.apply_along_axis(
        lambda col: np.convolve(col, ones, mode="valid"), -1, padded.real
    )
    conv_im = np.apply_along_axis(
        lambda col: np.convolve(col, ones, mode="valid"), -1, padded.imag
    )
    assert conv_re.shape == (2, 2, n)
    assert np.allclose(conv_re / window, vec.real, rtol=1e-12, atol=0.0)
    assert np.allclose(conv_im / window, vec.imag, rtol=1e-12, atol=0.0)
    # the dyadic probe kernel-path == naive path == literal path all exact:
    q = _quarter_grid_buffer()
    qv = centered_moving_mean(q, window=5)
    assert np.array_equal(qv, _naive_centered_mean(q, 5))


def test_golden_canonical_digest_stability() -> None:
    payload = {
        "sources": {
            "dewow.py": DEWOW_REFERENCE_SHA256,
            "_time_stage_common.py": COMMON_REFERENCE_SHA256,
        },
        "scenes": {
            "A_mean_re": GOLDEN_MEAN_A_RE,
            "B_out_im": GOLDEN_OUT_B_IM,
            "C_out": [[v.real, v.imag] for v in np.array(GOLDEN_OUT_C)],
        },
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    digest = hashlib.sha256(blob.encode()).hexdigest()
    # Pinned canonical golden digest (plan D9 leg 2): any drift in a golden
    # literal or the reference-source hashes flips this and fails here.
    assert digest == "e5e2486180697b7338c2e96137714aa18a76d1ad6ea03b34c50c79b1852231b8"
    assert blob.startswith('{"scenes"')


# ----------------------------------------------------------- pure helpers


def test_derive_sample_interval_rules() -> None:
    assert derive_sample_interval_s(np.arange(5, dtype=np.float64) * 2.0) == 2.0
    with pytest.raises(DomainError) as info:
        derive_sample_interval_s(np.array([0.0]))
    assert info.value.code is ErrorCode.INVALID_ARGUMENT
    with pytest.raises(DomainError) as info:
        derive_sample_interval_s(np.array([0.0, 1.0, 1.0, 2.0]))
    assert info.value.code is ErrorCode.NON_INCREASING_AXIS
    with pytest.raises(DomainError) as info:
        derive_sample_interval_s(np.array([0.0, 1.0, 2.5, 3.5]))
    assert info.value.code is ErrorCode.NON_UNIFORM_AXIS
    with pytest.raises(DomainError) as info:
        derive_sample_interval_s(np.array([0.0, float("nan")]))
    assert info.value.code is ErrorCode.NON_FINITE_AXIS
    # inside the documented relative tolerance the jittered grid passes and
    # the derived interval is the median step itself
    step = 1e-3
    jittered = np.array([0.0, step, step * 2 + step * DEWOW_AXIS_TOLERANCE_REL / 10])
    assert derive_sample_interval_s(jittered) == float(np.median(np.diff(jittered)))


def test_window_samples_rounding_chain() -> None:
    # exact odd already
    assert window_samples_for(3.0, 1.0, 8) == 3
    # even -> oddified upward
    assert window_samples_for(4.0, 1.0, 8) == 5
    # rounds to zero -> clamps to 1 -> rejected (too small)
    with pytest.raises(DomainError) as info:
        window_samples_for(0.4, 1.0, 8)
    assert info.value.code is ErrorCode.INVALID_ARGUMENT
    assert "window_s" in str(info.value.message)
    # round-half-to-even semantics pinned: 2.5 -> round -> 2 -> oddify 3
    assert window_samples_for(2.5, 1.0, 8) == 3
    # 3.5 -> round(3.5)=4 (half-even) -> oddify 5
    assert window_samples_for(3.5, 1.0, 8) == 5
    # oversized window rejected with actionable message
    with pytest.raises(DomainError) as info:
        window_samples_for(9.0, 1.0, 8)
    assert info.value.code is ErrorCode.INVALID_ARGUMENT
    assert "window_s" in str(info.value.message)
    # exactly n_time (odd) accepted
    assert window_samples_for(8.0, 1.0, 9) == 9


def test_centered_moving_mean_guard_matrix() -> None:
    data = np.ones((1, 1, 6), dtype=np.complex128)
    with pytest.raises(DomainError):
        centered_moving_mean(data, window=2)  # even
    with pytest.raises(DomainError):
        centered_moving_mean(data, window=1)  # too small
    with pytest.raises(DomainError):
        centered_moving_mean(data, window=7)  # longer than axis
    with pytest.raises(DomainError):
        centered_moving_mean(data.real, window=3)  # not complex
    out = centered_moving_mean(data, window=3)
    assert out.shape == data.shape
    assert out.dtype == np.complex128


# ------------------------------------------------------ domain & ordering


def test_empty_history_rejected_as_illegal_predecessor() -> None:
    scan = _scan(
        np.zeros((1, 1, 4), dtype=np.complex128),
        history=_base_history_for(4),
    )
    with pytest.raises(DomainError) as info:
        STAGE.apply(scan, history=_history())
    assert info.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH
    assert info.value.context["input_domain"] == RAW.value


def _freq_hist(end_domain: DataDomain) -> ProcessingHistory:
    """Legal chain ending exactly at a frequency end_domain."""
    rec_kwargs: dict[str, object] = {}
    if end_domain is CALIBRATED:
        rec_kwargs["calibration_id"] = CalibrationProfileId(uuid.UUID(int=7))
    elif end_domain is BACKGROUND_APPLIED:
        rec_kwargs["background_id"] = BackgroundReferenceId(uuid.UUID(int=8))
    return _history(
        _record(
            stage="any_freq_stage",
            input_domain=RAW,
            output_domain=end_domain,
            **rec_kwargs,  # type: ignore[arg-type]
        ),
    )


@pytest.mark.parametrize(
    "end_domain", [CALIBRATED, BACKGROUND_APPLIED, FILTERED]
)
def test_frequency_history_rejected(end_domain: DataDomain) -> None:
    # A history whose last hop ends in a frequency domain is an illegal dewow
    # predecessor.  The snapshot itself carries a valid time_base history
    # (core refuses kind/history mismatches at construction), so apply's own
    # explicit-history predecessor gate is what must refuse the call.
    freq_hist = _freq_hist(end_domain)
    scan = _scan(
        np.zeros((1, 1, 4), dtype=np.complex128),
        history=_base_history_for(4),
    )
    with pytest.raises(DomainError) as info:
        STAGE.apply(scan, history=freq_hist)
    assert info.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH
    assert info.value.context["input_domain"] == end_domain.value


def test_kind_history_mismatch_fails_closed_at_core_entry() -> None:
    # A time_processed scan whose history ends in time_base cannot even be
    # constructed: core _validate_history_kind refuses the snapshot itself.
    with pytest.raises(DomainError):
        _scan(
            np.zeros((1, 1, 4), dtype=np.complex128),
            kind=TimeDomainKind.TIME_PROCESSED,
            history=_history(_ifft_record()),
        )


def test_duplicate_dewow_rejected_by_stage_gate() -> None:
    scan = _ramp_scan()
    first = STAGE.apply(scan)
    with pytest.raises(DomainError) as info:
        STAGE.apply(first.source, history=first.history)
    assert info.value.code is ErrorCode.INVALID_ARGUMENT
    assert info.value.context["stage_name"] == "dewow"


def test_core_history_uniqueness_is_an_independent_second_gate() -> None:
    # Isolated probe: appending a second dewow record straight onto a history
    # that already has one fails inside ProcessingHistory even with a bumped
    # stage_version (bypassing the stage gate entirely).
    base = _history(_ifft_record(), _dewow_record())
    # the appended twin must be chain-legal (input time_processed like any
    # post-dewow hop would be), so only the uniqueness rule can reject it.
    with pytest.raises(DomainError) as info:
        base.append(_dewow_record(input_domain=TIME_PROCESSED, version="99.0"))
    assert info.value.code is ErrorCode.INVALID_ARGUMENT
    assert info.value.context["stage_name"] == "dewow"


def test_flat_before_dewow_rejected() -> None:
    hist = _history(_ifft_record(), _flat_record())
    scan = _scan(
        np.zeros((1, 1, 4), dtype=np.complex128),
        kind=TimeDomainKind.TIME_PROCESSED,
        history=hist,
    )
    # history ends in time_processed (a legal domain predecessor), yet the
    # ORDER guard must still fire before any numeric work.
    with pytest.raises(DomainError) as info:
        STAGE.apply(scan, history=hist)
    assert info.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH
    assert "flat_reflection_filter" in str(info.value.message)


def test_foreign_time_processed_predecessor_accepted() -> None:
    # The transition table allows time_processed -> time_processed; with a
    # non-dewow/non-flat producer there is no ordering conflict, so the stage
    # runs and records the true predecessor domain.
    hist = _history(_ifft_record(), _legacy_dcgate_record())
    scan = _scan(
        np.zeros((1, 1, 8), dtype=np.complex128),
        kind=TimeDomainKind.TIME_PROCESSED,
        history=hist,
    )
    result = STAGE.apply(scan, history=hist)
    assert result.domain is TIME_PROCESSED
    assert result.history.records[-1].input_domain is TIME_PROCESSED


# ------------------------------------------------------------- validation


def test_non_finite_data_rejected() -> None:
    data = np.zeros((1, 1, 8), dtype=np.complex128)
    data[0, 0, 3] = complex(float("nan"), 0.0)
    with pytest.raises(DomainError) as info:
        STAGE.apply(_scan(data))
    assert info.value.code is ErrorCode.NON_FINITE_AXIS
    data2 = np.zeros((1, 1, 8), dtype=np.complex128)
    data2[0, 0, 7] = complex(0.0, float("inf"))
    with pytest.raises(DomainError):
        STAGE.apply(_scan(data2))


def test_single_sample_axis_rejected_by_stage() -> None:
    axis = np.array([5e-9])
    data = np.zeros((1, 1, 1), dtype=np.complex128)
    scan = _scan(data, time_axis_s=axis)
    with pytest.raises(DomainError) as info:
        STAGE.apply(scan)
    assert info.value.code is ErrorCode.INVALID_ARGUMENT


def test_non_uniform_axis_rejected_by_stage() -> None:
    axis = np.array([0.0, 1e-9, 3e-9, 4e-9, 5e-9, 6e-9, 7e-9, 8e-9])
    data = np.zeros((1, 1, 8), dtype=np.complex128)
    scan = _scan(data, time_axis_s=axis)
    with pytest.raises(DomainError) as info:
        STAGE.apply(scan)
    assert info.value.code is ErrorCode.NON_UNIFORM_AXIS


def test_undersized_window_rejected_by_stage() -> None:
    # dt=1 ns, window 0.4 ns -> rounds to 1 sample -> rejected pre-flight.
    scan = _ramp_scan()
    with pytest.raises(DomainError) as info:
        DewowStage(window_s=0.4e-9).apply(scan)
    assert info.value.code is ErrorCode.INVALID_ARGUMENT


def test_oversized_window_rejected_by_stage() -> None:
    scan = _ramp_scan()
    with pytest.raises(DomainError) as info:
        DewowStage(window_s=N_TIME * DT_S + 1e-9).apply(scan)
    assert info.value.code is ErrorCode.INVALID_ARGUMENT


# --------------------------------------------------------- provenance


def test_record_fields_and_parameters() -> None:
    scan = _ramp_scan()
    result = DewowStage(window_s=3e-9).apply(scan, executed_utc=CREATED_UTC)
    record = result.history.records[-1]
    assert record.stage_name == "dewow"
    assert record.stage_version == "1.0"
    assert record.input_domain is TIME_BASE
    assert record.output_domain is TIME_PROCESSED
    assert record.executed_utc == CREATED_UTC
    params = record.parameters
    assert params["operation"] == "subtract_centered_moving_average"
    assert params["axis"] == "time_last"
    assert params["padding"] == DEWOW_PADDING == "reflect"
    assert params["window_s"] == 3e-9
    assert params["dt_s"] == DT_S
    assert params["window_samples"] == 3
    assert params["time_sample_count"] == N_TIME
    refs = params["reference_source_sha256"]
    assert refs["rebar_processing_dewow_py"] == DEWOW_REFERENCE_SHA256
    assert refs["rebar_time_stage_common_py"] == COMMON_REFERENCE_SHA256


def test_history_roundtrip_json_safe() -> None:
    result = STAGE.apply(_ramp_scan(), executed_utc=CREATED_UTC)
    dumped = result.history.to_dict()
    restored = ProcessingHistory.from_dict(dumped)
    assert restored == result.history
    blob = json.dumps(dumped)  # fully serializable, no lossy structures
    assert "dewow" in blob


def test_clock_injection_and_naive_rejection() -> None:
    clock = ManualClock(CREATED_UTC)
    result = STAGE.apply(_ramp_scan(), clock=clock)
    assert result.history.records[-1].executed_utc == CREATED_UTC
    naive = datetime(2026, 1, 1)
    with pytest.raises(DomainError) as info:
        STAGE.apply(_ramp_scan(), executed_utc=naive)
    assert info.value.code is ErrorCode.NAIVE_DATETIME


def test_result_type_shape() -> None:
    result = STAGE.apply(_ramp_scan())
    assert isinstance(result, TimeDomainStageResult)
    assert isinstance(result.source, TimeDomainScan)
    assert result.source.kind is TimeDomainKind.TIME_PROCESSED


# ---------------------------------------------------------- immutability


def test_inputs_never_mutated_outputs_protected() -> None:
    scan = _ramp_scan()
    data_bytes = scan.data.tobytes()
    axis_bytes = scan.time_axis_s.tobytes()
    history_before = scan.history
    result = STAGE.apply(scan)
    assert scan.data.tobytes() == data_bytes
    assert scan.time_axis_s.tobytes() == axis_bytes
    assert scan.history is history_before
    assert result.source is not scan
    assert not result.source.data.flags.writeable
    with pytest.raises(ValueError):
        result.source.data[0, 0, 0] = 1.0 + 0j
    with pytest.raises(ValueError):
        scan.data[0, 0, 0] = 1.0 + 0j


def test_channels_axis_metadata_preserved() -> None:
    from uav_gpr.core.enums import TraceQualityReason

    monotonic = 1_000_000_000
    md = TraceMetadata(
        mission_id=MissionId(uuid.UUID(int=1)),
        trace_index=0,
        trace_uid=TraceUid(uuid.UUID(int=100)),
        device_id=DeviceId(uuid.UUID(int=2)),
        sweep_started_utc=CREATED_UTC,
        sweep_midpoint_utc=CREATED_UTC,
        sweep_finished_utc=CREATED_UTC,
        sweep_started_monotonic_ns=MonotonicNs(monotonic),
        sweep_midpoint_monotonic_ns=MonotonicNs(monotonic + 50_000_000),
        sweep_finished_monotonic_ns=MonotonicNs(monotonic + 100_000_000),
        target_interval_s=1e-3,
        actual_interval_s=None,
        schedule_error_s=None,
        connection_generation=1,
        raw_trace_sha256=None,
        gnss_match=None,
        quality_status=TraceQualityStatus.DEGRADED,
        quality_reasons=(TraceQualityReason.GNSS_MISSING,),
    )
    scan = _ramp_scan(traces=2, channels=2)
    second = dataclasses.replace(
        md,
        trace_index=1,
        trace_uid=TraceUid(uuid.UUID(int=101)),
        actual_interval_s=1e-3,
        schedule_error_s=0.0,
    )
    scan_with_md = TimeDomainScan(
        channels=scan.channels,
        time_axis_s=scan.time_axis_s,
        data=scan.data,
        kind=scan.kind,
        history=scan.history,
        metadata=(md, second),
    )
    result = STAGE.apply(scan_with_md)
    assert result.source.channels == scan_with_md.channels
    assert np.array_equal(result.source.time_axis_s, scan_with_md.time_axis_s)
    assert result.source.kind is TimeDomainKind.TIME_PROCESSED
    assert result.source.metadata == scan_with_md.metadata
    assert result.source.data.shape == scan_with_md.data.shape
    assert result.source.data.dtype == np.complex128


def test_multichannel_rows_are_independent() -> None:
    scan = _ramp_scan(traces=1, channels=2)
    out = STAGE.apply(scan).source.data
    # default window resolves to 5 samples (round(4 ns / 1 ns)=4 -> oddify 5)
    for ch in range(2):
        row_in = scan.data[0, ch]
        manual = row_in - centered_moving_mean(
            row_in[np.newaxis, :], window=5
        )[0]
        assert np.array_equal(out[0, ch], manual)
    # and the two channels never mix: processing each channel alone through
    # the kernel equals the batched rows
    stacked = np.stack(
        [
            centered_moving_mean(scan.data[0, ch : ch + 1], window=5)[0]
            for ch in range(2)
        ],
        axis=0,
    )[np.newaxis, ...]
    assert np.array_equal(
        scan.data - stacked, out
    )


def test_trace_broadcast_equals_batch_of_scans() -> None:
    multi = _ramp_scan(traces=3, channels=2)
    whole = STAGE.apply(multi).source.data
    for t in range(3):
        single = _scan(multi.data[t : t + 1], channels=(HH_S11, VV_S22))
        part = STAGE.apply(single).source.data
        assert np.array_equal(whole[t : t + 1], part)


# -------------------------------------------------------- performance smoke


def test_performance_smoke_linear_time() -> None:
    traces, channels, n = 512, 2, 1024
    axis = np.arange(n, dtype=np.float64) * DT_S
    i, j, k = np.meshgrid(
        np.arange(traces), np.arange(channels), np.arange(n), indexing="ij"
    )
    data = (0.01 * i + j + np.cos(k * 1e-3) + 1j * np.sin(k * 7e-4)).astype(
        np.complex128
    )
    scan = _scan(data, channels=(HH_S11, VV_S22), time_axis_s=axis)
    start = time.perf_counter()
    result = DewowStage(window_s=8e-9).apply(scan)
    elapsed = time.perf_counter() - start
    assert result.source.data.shape == data.shape
    assert elapsed < 10.0, f"dewow apply took {elapsed:.2f}s (O(N) expected)"


# --------------------------------------------------------- exclusion guards


def test_module_imports_stay_within_allowed_layers() -> None:
    import ast

    import uav_gpr.processing.dewow as module

    tree = ast.parse(inspect.getsource(module))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    allowed_roots = {
        "__future__",
        "collections",
        "datetime",
        "math",
        "types",
        "typing",
        "numpy",
        "uav_gpr",
    }
    for name in imported:
        assert name.split(".", 1)[0] in allowed_roots, f"unexpected root: {name}"
        assert "rebar" not in name
        assert ".storage" not in name and ".acquisition" not in name
    # No Qt / plotting libraries anywhere near a processing stage.
    whole = inspect.getsource(module)
    for forbidden in ("PySide6", "pyqtgraph", "matplotlib"):
        assert forbidden not in whole


def test_no_flat_or_display_symbols_exposed() -> None:
    import uav_gpr.processing.dewow as module

    public = set(module.__all__)
    assert not any(
        token in name.lower()
        for name in public
        for token in ("flat", "display", "crop", "depth", "velocity")
    )
    # the only boundary mode this module ever requests is reflect
    assert 'padding="edge"' not in inspect.getsource(module)
    assert "mode='edge'" not in inspect.getsource(module)
