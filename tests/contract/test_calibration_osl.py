"""Contract tests for the ISSUE-027 one-port OSL calibration model and solver.

Pure deterministic tests: synthetic forward error models only - no hardware,
no threads, no sleeps, no serial.  Randomness is confined to fixed-seed
``default_rng`` noisy-capture scenarios.

Contract summary (docs/issues/M06_CALIBRATION_PROCESSING.md ISSUE-027,
docs/CALIBRATION.md, docs/reports/ISSUE_027_BASELINE_CONFIRMATION.md and
docs/plans/2026-09-02-issue-027-osl-calibration.md D1-D10):

- error model ``m = D + T*gamma/(1 - gamma*S)``; correction
  ``x = m - D; corrected = x/(T + S*x)`` (reference-identical algebra);
- ideal OSL recovers a known DUT; S11/S22 profiles are independent and each
  binds a full ``ChannelSpec`` (S21/S12 channels rejected);
- golden vectors were produced by executing the frozen standalone reference
  (rebar-inspector calibration_reference/osl_calibration.py, SHA-256
  0e278bf0...) on a synthetic scenario and are asserted within tolerance;
- degenerate standards (normalized determinant <= 1e-12) and correction
  singularities (|T + S*x| <= 1e-12*(|T| + |S*x|)) fail closed with
  DomainError(INVALID_ARGUMENT) and a structured context kind;
- axis/channel/profile mismatches fail closed (AXIS_MISMATCH /
  CHANNEL_CONTRACT_MISMATCH / DUPLICATE_CHANNEL / SHAPE_MISMATCH /
  DTYPE_MISMATCH / NON_FINITE_AXIS / NON_INCREASING_AXIS);
- raw inputs are never mutated; all returned arrays are read-only copies
  or views of write-protected bases.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import numpy as np
import pytest

from uav_gpr.calibration.osl import (
    OslCalibrationProfile,
    OslCalibrationQuality,
    OslCalibrationSet,
    OslStandard,
    build_osl_calibration,
)
from uav_gpr.core.channels import ChannelSpec
from uav_gpr.core.enums import LogicalPolarization, SParameter
from uav_gpr.core.errors import DomainError, ErrorCode
from uav_gpr.core.identifiers import CalibrationProfileId

# ---------------------------------------------------------------------------
# Shared synthetic scenario (independent of any real-world data).
# ---------------------------------------------------------------------------

FREQUENCY_HZ = np.linspace(0.5e9, 2.5e9, 41)
_NORM = (FREQUENCY_HZ - FREQUENCY_HZ[0]) / (FREQUENCY_HZ[-1] - FREQUENCY_HZ[0])

CH_S11 = ChannelSpec("ch_s11", LogicalPolarization.HH, SParameter.S11, "S11 antenna")
CH_S22 = ChannelSpec("ch_s22", LogicalPolarization.VV, SParameter.S22, "S22 antenna")
CH_S21 = ChannelSpec("ch_s21", LogicalPolarization.HV, SParameter.S21, "S21 antenna")

PID_S11 = CalibrationProfileId("11111111-1111-4111-8111-111111111111")
PID_S22 = CalibrationProfileId("22222222-2222-4222-8222-222222222222")

_SAMPLED = (0, 10, 20, 30, 40)

# Golden vectors produced on 2026-09-02 by executing the frozen standalone
# reference (D:/博士任务/rebar-inspector/calibration_reference/osl_calibration.py,
# SHA-256 0e278bf009b661ef066b845d7175fe047df538ef80721fa2ff3325f21dd3921d) with
# the synthetic scenario below; see the ISSUE-027 plan document section 8.
_GOLDEN_DIRECTIVITY = {
    0: 0.024999999999999998 + 0.008j,
    10: 0.023020768325963822 + 0.007751299373685159j,
    20: 0.02116459569116638 + 0.007020660495122981j,
    30: 0.01954688991981333 + 0.0058535109509905685j,
    40: 0.018268232121536828 + 0.004322418446945119j,
}
_GOLDEN_TRACKING = {
    0: 0.9100000000000001 - 0.04000000000000001j,
    10: 0.9090180593988525 - 0.05819078695759622j,
    20: 0.9076725236940246 - 0.07635829837627621j,
    30: 0.9059639310818581 - 0.09449526749370274j,
    40: 0.9038929649766168 - 0.11259443976405197j,
}
_GOLDEN_SOURCE_MATCH = {
    0: 0.07000000000000008 - 3.313258592900023e-18j,
    10: 0.07000000000000005 + 0.003750000000000003j,
    20: 0.07000000000000002 + 0.00749999999999999j,
    30: 0.06999999999999998 + 0.011250000000000005j,
    40: 0.07000000000000009 + 0.01500000000000002j,
}
_GOLDEN_CORRECTED = {
    0: 0.5 + 0.0j,
    10: 0.344714555828442 + 0.3447145558284419j,
    20: 3.437669203176522e-17 + 0.47500000000000003j,
    30: -0.32703688629877825 + 0.32703688629877825j,
    40: -0.45000000000000007 + 3.116457652735913e-17j,
}
# Full-vector complex checksums from the reference run.
_GOLDEN_SUMS = {
    "directivity": 0.8745385156466184 + 0.27541789969949515j,
    "reflection_tracking": 37.20415426544017 - 3.129813713734421j,
    "source_match": 2.870000000000002 + 0.3075000000000001j,
    "corrected": 0.43049313217365415 + 12.089557300194613j,
}

_SAMPLED_ATOL = 1e-9
_SUM_ATOL = 1e-6


def _error_terms() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Deterministic smooth synthetic error terms used by every scenario."""
    directivity = 0.025 + 0.008j * np.exp(1j * _NORM)
    tracking = (0.91 - 0.04j) * np.exp(-0.08j * _NORM)
    source_match = 0.07 + 0.015j * _NORM
    return directivity, tracking, source_match


def _forward(
    gamma: np.ndarray | complex,
    directivity: np.ndarray,
    tracking: np.ndarray,
    source_match: np.ndarray,
) -> np.ndarray:
    gamma_array = np.asarray(gamma, dtype=np.complex128)
    return directivity + gamma_array * tracking / (1.0 - gamma_array * source_match)


def _standard_measurements(
    directivity: np.ndarray,
    tracking: np.ndarray,
    source_match: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Ideal-standard Open/Short/Load forward measurements (single capture)."""
    return (
        _forward(1.0 + 0.0j, directivity, tracking, source_match),
        _forward(-1.0 + 0.0j, directivity, tracking, source_match),
        _forward(0.0 + 0.0j, directivity, tracking, source_match),
    )


def _dut_gamma() -> np.ndarray:
    return 0.5 * np.exp(1j * np.pi * _NORM) * (1.0 - 0.1 * _NORM)


def _golden_scenario_profile(
    channel: ChannelSpec = CH_S11,
    profile_id: CalibrationProfileId = PID_S11,
) -> OslCalibrationProfile:
    d, t, s = _error_terms()
    open_m, short_m, load_m = _standard_measurements(d, t, s)
    return build_osl_calibration(
        channel=channel,
        frequency_hz=FREQUENCY_HZ,
        open_measured=open_m,
        short_measured=short_m,
        load_measured=load_m,
        profile_id=profile_id,
    )


def _assert_close(actual: np.ndarray, expected: np.ndarray, atol: float) -> None:
    assert actual.shape == expected.shape
    assert np.allclose(actual, expected, rtol=1e-9, atol=atol)


# ---------------------------------------------------------------------------
# Ideal recovery and reference golden vectors (acceptance 1 + 3).
# ---------------------------------------------------------------------------


def test_ideal_osl_recovers_known_dut() -> None:
    d, t, s = _error_terms()
    profile = _golden_scenario_profile()
    _assert_close(profile.directivity, d, atol=1e-9)
    _assert_close(profile.reflection_tracking, t, atol=1e-9)
    _assert_close(profile.source_match, s, atol=1e-9)
    gamma = _dut_gamma()
    measured = _forward(gamma, d, t, s)
    _assert_close(profile.correct(measured), gamma, atol=1e-9)
    assert profile.n_frequencies == FREQUENCY_HZ.size
    assert profile.s_parameter is SParameter.S11
    assert profile.channel == CH_S11


def test_reference_golden_vectors_within_tolerance() -> None:
    profile = _golden_scenario_profile()
    for index, expected in _GOLDEN_DIRECTIVITY.items():
        assert abs(profile.directivity[index] - expected) <= _SAMPLED_ATOL
    for index, expected in _GOLDEN_TRACKING.items():
        assert abs(profile.reflection_tracking[index] - expected) <= _SAMPLED_ATOL
    for index, expected in _GOLDEN_SOURCE_MATCH.items():
        assert abs(profile.source_match[index] - expected) <= _SAMPLED_ATOL
    d, t, s = _error_terms()
    gamma = _dut_gamma()
    corrected = profile.correct(_forward(gamma, d, t, s))
    for index, expected in _GOLDEN_CORRECTED.items():
        assert abs(corrected[index] - expected) <= _SAMPLED_ATOL
    assert abs(complex(np.sum(profile.directivity)) - _GOLDEN_SUMS["directivity"]) <= _SUM_ATOL
    assert (
        abs(complex(np.sum(profile.reflection_tracking)) - _GOLDEN_SUMS["reflection_tracking"])
        <= _SUM_ATOL
    )
    assert abs(complex(np.sum(profile.source_match)) - _GOLDEN_SUMS["source_match"]) <= _SUM_ATOL
    assert abs(complex(np.sum(corrected)) - _GOLDEN_SUMS["corrected"]) <= _SUM_ATOL


def test_independent_closed_form_cross_check() -> None:
    """Ideal-standards closed form must agree with the transcribed solve."""
    profile = _golden_scenario_profile()
    expected_d = profile.load_measured_mean
    a = profile.open_measured_mean - profile.load_measured_mean
    b_prime = profile.short_measured_mean - profile.load_measured_mean
    expected_s = (a + b_prime) / (a - b_prime)
    expected_t = a * (1.0 - expected_s)
    _assert_close(profile.directivity, expected_d, atol=1e-9)
    _assert_close(profile.source_match, expected_s, atol=1e-9)
    _assert_close(profile.reflection_tracking, expected_t, atol=1e-9)


# ---------------------------------------------------------------------------
# Frequency-dependent actual standards and scalar broadcast.
# ---------------------------------------------------------------------------


def test_frequency_dependent_actual_standards_recover_dut() -> None:
    d, t, s = _error_terms()
    open_actual = 1.0 + 0.001j * _NORM
    short_actual = -1.0 + 0.002j * _NORM
    load_actual = 0.05j * _NORM
    open_m = _forward(open_actual, d, t, s)
    short_m = _forward(short_actual, d, t, s)
    load_m = _forward(load_actual, d, t, s)
    profile = build_osl_calibration(
        channel=CH_S11,
        frequency_hz=FREQUENCY_HZ,
        open_measured=open_m,
        short_measured=short_m,
        load_measured=load_m,
        open_actual=open_actual,
        short_actual=short_actual,
        load_actual=load_actual,
        profile_id=PID_S11,
    )
    _assert_close(profile.open_actual, open_actual, atol=1e-12)
    _assert_close(profile.short_actual, short_actual, atol=1e-12)
    _assert_close(profile.load_actual, load_actual, atol=1e-12)
    gamma = _dut_gamma()
    _assert_close(profile.correct(_forward(gamma, d, t, s)), gamma, atol=1e-9)


def test_actual_length_mismatch_rejected() -> None:
    d, t, s = _error_terms()
    open_m, short_m, load_m = _standard_measurements(d, t, s)
    with pytest.raises(DomainError) as exc:
        build_osl_calibration(
            channel=CH_S11,
            frequency_hz=FREQUENCY_HZ,
            open_measured=open_m,
            short_measured=short_m,
            load_measured=load_m,
            open_actual=np.ones(FREQUENCY_HZ.size - 1, dtype=np.complex128),
        )
    assert exc.value.code is ErrorCode.AXIS_MISMATCH


# ---------------------------------------------------------------------------
# Multi-capture coherent averaging and quality metrics.
# ---------------------------------------------------------------------------


def _noisy_captures(
    base: np.ndarray, n_captures: int, seed: int, noise_amp: float
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    noise = noise_amp * (
        rng.standard_normal((n_captures, base.size))
        + 1j * rng.standard_normal((n_captures, base.size))
    )
    return base[np.newaxis, :] + noise


def test_multi_capture_averaging_and_quality_metrics() -> None:
    d, t, s = _error_terms()
    open_m, short_m, load_m = _standard_measurements(d, t, s)
    profile = build_osl_calibration(
        channel=CH_S11,
        frequency_hz=FREQUENCY_HZ,
        open_measured=_noisy_captures(open_m, 24, 7, 2e-4),
        short_measured=_noisy_captures(short_m, 24, 8, 2e-4),
        load_measured=_noisy_captures(load_m, 24, 9, 2e-4),
        profile_id=PID_S11,
    )
    counts = (
        profile.open_capture_count,
        profile.short_capture_count,
        profile.load_capture_count,
    )
    assert counts == (24, 24, 24)
    gamma = _dut_gamma()
    corrected = profile.correct(_forward(gamma, d, t, s))
    assert np.max(np.abs(corrected - gamma)) < 1e-2
    quality = profile.quality
    assert quality.open_rms_abs_error >= 0.0
    assert quality.open_max_abs_error >= quality.open_rms_abs_error
    assert quality.short_max_abs_error >= quality.short_rms_abs_error
    assert quality.load_max_abs_error >= quality.load_rms_abs_error
    for value in (
        quality.open_rms_abs_error,
        quality.open_max_abs_error,
        quality.short_rms_abs_error,
        quality.short_max_abs_error,
        quality.load_rms_abs_error,
        quality.load_max_abs_error,
    ):
        assert np.isfinite(value)
        assert 1e-6 < value < 1e-2  # noise 2e-4 dominates but stays bounded


def test_quality_metrics_scale_with_noise() -> None:
    d, t, s = _error_terms()
    open_m, short_m, load_m = _standard_measurements(d, t, s)
    quiet = build_osl_calibration(
        channel=CH_S11,
        frequency_hz=FREQUENCY_HZ,
        open_measured=_noisy_captures(open_m, 24, 1, 1e-6),
        short_measured=_noisy_captures(short_m, 24, 2, 1e-6),
        load_measured=_noisy_captures(load_m, 24, 3, 1e-6),
        profile_id=PID_S11,
    )
    loud = build_osl_calibration(
        channel=CH_S11,
        frequency_hz=FREQUENCY_HZ,
        open_measured=_noisy_captures(open_m, 24, 1, 1e-3),
        short_measured=_noisy_captures(short_m, 24, 2, 1e-3),
        load_measured=_noisy_captures(load_m, 24, 3, 1e-3),
        profile_id=PID_S11,
    )
    assert quiet.quality.open_rms_abs_error < loud.quality.open_rms_abs_error
    assert loud.quality.open_max_abs_error < 1e-1


def test_noiseless_single_capture_quality_is_zero() -> None:
    profile = _golden_scenario_profile()
    quality = profile.quality
    for value in (
        quality.open_rms_abs_error,
        quality.open_max_abs_error,
        quality.short_rms_abs_error,
        quality.short_max_abs_error,
        quality.load_rms_abs_error,
        quality.load_max_abs_error,
    ):
        assert value < 1e-9


def test_one_dimensional_measurement_is_single_capture() -> None:
    d, t, s = _error_terms()
    open_m, short_m, load_m = _standard_measurements(d, t, s)
    profile = build_osl_calibration(
        channel=CH_S11,
        frequency_hz=FREQUENCY_HZ,
        open_measured=open_m,
        short_measured=short_m,
        load_measured=load_m,
        profile_id=PID_S11,
    )
    counts = (
        profile.open_capture_count,
        profile.short_capture_count,
        profile.load_capture_count,
    )
    assert counts == (1, 1, 1)
    stacked = np.stack([open_m, open_m], axis=0)
    profile2 = build_osl_calibration(
        channel=CH_S11,
        frequency_hz=FREQUENCY_HZ,
        open_measured=stacked,
        short_measured=np.stack([short_m, short_m], axis=0),
        load_measured=np.stack([load_m, load_m], axis=0),
        profile_id=PID_S11,
    )
    _assert_close(profile2.open_measured_mean, profile.open_measured_mean, atol=1e-15)


# ---------------------------------------------------------------------------
# S11/S22 independence and the ordered multi-channel container.
# ---------------------------------------------------------------------------


def test_s11_s22_profiles_are_independent() -> None:
    d11, t11, s11 = _error_terms()
    # S22 port uses a different error model on purpose.
    d22 = d11 * 0.8 + 0.01j
    t22 = t11 * np.exp(0.03j * _NORM)
    s22 = s11 - 0.02 - 0.01j * _NORM
    p11 = build_osl_calibration(
        channel=CH_S11,
        frequency_hz=FREQUENCY_HZ,
        open_measured=_forward(1.0, d11, t11, s11),
        short_measured=_forward(-1.0, d11, t11, s11),
        load_measured=_forward(0.0, d11, t11, s11),
        profile_id=PID_S11,
    )
    p22 = build_osl_calibration(
        channel=CH_S22,
        frequency_hz=FREQUENCY_HZ,
        open_measured=_forward(1.0, d22, t22, s22),
        short_measured=_forward(-1.0, d22, t22, s22),
        load_measured=_forward(0.0, d22, t22, s22),
        profile_id=PID_S22,
    )
    assert p11.s_parameter is SParameter.S11
    assert p22.s_parameter is SParameter.S22
    assert not np.array_equal(p11.directivity, p22.directivity)
    # Each profile recovers its own port.
    gamma = _dut_gamma()
    _assert_close(p11.correct(_forward(gamma, d11, t11, s11)), gamma, atol=1e-9)
    _assert_close(p22.correct(_forward(gamma, d22, t22, s22)), gamma, atol=1e-9)


def test_calibration_set_ordered_binding_and_apply() -> None:
    d11, t11, s11 = _error_terms()
    d22 = d11 * 0.8 + 0.01j
    t22 = t11 * np.exp(0.03j * _NORM)
    s22 = s11 - 0.02 - 0.01j * _NORM
    p11 = build_osl_calibration(
        channel=CH_S11,
        frequency_hz=FREQUENCY_HZ,
        open_measured=_forward(1.0, d11, t11, s11),
        short_measured=_forward(-1.0, d11, t11, s11),
        load_measured=_forward(0.0, d11, t11, s11),
        profile_id=PID_S11,
    )
    p22 = build_osl_calibration(
        channel=CH_S22,
        frequency_hz=FREQUENCY_HZ,
        open_measured=_forward(1.0, d22, t22, s22),
        short_measured=_forward(-1.0, d22, t22, s22),
        load_measured=_forward(0.0, d22, t22, s22),
        profile_id=PID_S22,
    )
    cal_set = OslCalibrationSet((p11, p22))
    assert cal_set.channels == (CH_S11, CH_S22)
    assert cal_set.profile_for(CH_S11) is p11
    assert cal_set.profile_for(CH_S22) is p22

    gamma = _dut_gamma()
    dut = np.stack(
        [_forward(gamma, d11, t11, s11), _forward(gamma, d22, t22, s22)], axis=0
    )
    corrected = cal_set.apply(dut, (CH_S11, CH_S22))
    assert corrected.shape == (2, FREQUENCY_HZ.size)
    _assert_close(corrected[0], gamma, atol=1e-9)
    _assert_close(corrected[1], gamma, atol=1e-9)
    # Input data stays untouched.
    _assert_close(
        dut,
        np.stack([_forward(gamma, d11, t11, s11), _forward(gamma, d22, t22, s22)], axis=0),
        atol=0.0,
    )


def test_calibration_set_channel_mismatch_fail_closed() -> None:
    p11 = _golden_scenario_profile()
    p22 = build_osl_calibration(
        channel=CH_S22,
        frequency_hz=FREQUENCY_HZ,
        open_measured=np.ones(FREQUENCY_HZ.size, dtype=np.complex128) * (0.3 - 0.1j),
        short_measured=np.ones(FREQUENCY_HZ.size, dtype=np.complex128) * (-0.2 + 0.4j),
        load_measured=np.ones(FREQUENCY_HZ.size, dtype=np.complex128) * 0.05j,
        profile_id=PID_S22,
    )
    cal_set = OslCalibrationSet((p11, p22))
    dut = np.zeros((2, FREQUENCY_HZ.size), dtype=np.complex128)
    # Unknown channel spec.
    other = ChannelSpec("ch_other", LogicalPolarization.HH, SParameter.S11, "other")
    with pytest.raises(DomainError) as exc:
        cal_set.profile_for(other)
    assert exc.value.code is ErrorCode.CHANNEL_CONTRACT_MISMATCH
    # Wrong S parameter for the position.
    with pytest.raises(DomainError) as exc:
        cal_set.apply(dut, (CH_S22, CH_S11))
    assert exc.value.code is ErrorCode.CHANNEL_CONTRACT_MISMATCH
    # Reordered channels are also a contract mismatch.
    with pytest.raises(DomainError) as exc:
        cal_set.apply(dut, (CH_S22, CH_S11))
    assert exc.value.code is ErrorCode.CHANNEL_CONTRACT_MISMATCH
    # Wrong row count and wrong length fail closed too.
    with pytest.raises(DomainError) as exc:
        cal_set.apply(dut[:1], (CH_S11,))
    assert exc.value.code is ErrorCode.SHAPE_MISMATCH
    with pytest.raises(DomainError) as exc:
        cal_set.apply(dut[:, :-1], (CH_S11, CH_S22))
    assert exc.value.code is ErrorCode.AXIS_MISMATCH


def test_calibration_set_duplicate_channel_rejected() -> None:
    p11a = _golden_scenario_profile()
    p11b = build_osl_calibration(
        channel=CH_S11,
        frequency_hz=FREQUENCY_HZ,
        open_measured=np.ones(FREQUENCY_HZ.size, dtype=np.complex128) * 0.4j,
        short_measured=np.ones(FREQUENCY_HZ.size, dtype=np.complex128) * (-0.3 + 0.1j),
        load_measured=np.ones(FREQUENCY_HZ.size, dtype=np.complex128) * 0.02,
        profile_id=PID_S22,
    )
    with pytest.raises(DomainError) as exc:
        OslCalibrationSet((p11a, p11b))
    assert exc.value.code is ErrorCode.DUPLICATE_CHANNEL


def test_calibration_set_axis_mismatch_rejected() -> None:
    other_grid = np.linspace(0.6e9, 2.6e9, 41)
    p22 = build_osl_calibration(
        channel=CH_S22,
        frequency_hz=other_grid,
        open_measured=np.ones(41, dtype=np.complex128) * 0.3j,
        short_measured=np.ones(41, dtype=np.complex128) * (-0.2 + 0.2j),
        load_measured=np.ones(41, dtype=np.complex128) * 0.01j,
        profile_id=PID_S22,
    )
    with pytest.raises(DomainError) as exc:
        OslCalibrationSet((_golden_scenario_profile(), p22))
    assert exc.value.code is ErrorCode.AXIS_MISMATCH


def test_empty_calibration_set_rejected() -> None:
    with pytest.raises(DomainError) as exc:
        OslCalibrationSet(())
    assert exc.value.code is ErrorCode.INVALID_ARGUMENT


def test_transmission_channel_rejected() -> None:
    d, t, s = _error_terms()
    open_m, short_m, load_m = _standard_measurements(d, t, s)
    with pytest.raises(DomainError) as exc:
        build_osl_calibration(
            channel=CH_S21,
            frequency_hz=FREQUENCY_HZ,
            open_measured=open_m,
            short_measured=short_m,
            load_measured=load_m,
        )
    assert exc.value.code is ErrorCode.CHANNEL_CONTRACT_MISMATCH


# ---------------------------------------------------------------------------
# Degenerate and singular scenarios fail closed.
# ---------------------------------------------------------------------------


def test_degenerate_standards_rejected() -> None:
    d, t, s = _error_terms()
    open_m, _short_m, load_m = _standard_measurements(d, t, s)
    with pytest.raises(DomainError) as exc:
        build_osl_calibration(
            channel=CH_S11,
            frequency_hz=FREQUENCY_HZ,
            open_measured=open_m,
            short_measured=open_m.copy(),  # short == open: no unique solution
            load_measured=load_m,
        )
    assert exc.value.code is ErrorCode.INVALID_ARGUMENT
    assert exc.value.context.get("kind") == "degenerate_standards"


def test_single_frequency_degeneracy_rejected() -> None:
    d, t, s = _error_terms()
    open_m, short_m, load_m = _standard_measurements(d, t, s)
    short_bad = short_m.copy()
    short_bad[20] = open_m[20]  # degenerate at exactly one frequency point
    with pytest.raises(DomainError) as exc:
        build_osl_calibration(
            channel=CH_S11,
            frequency_hz=FREQUENCY_HZ,
            open_measured=open_m,
            short_measured=short_bad,
            load_measured=load_m,
        )
    assert exc.value.code is ErrorCode.INVALID_ARGUMENT
    assert exc.value.context.get("kind") == "degenerate_standards"
    assert exc.value.context.get("first_index") == 20


def test_all_zero_measurements_rejected() -> None:
    zeros = np.zeros(FREQUENCY_HZ.size, dtype=np.complex128)
    with pytest.raises(DomainError) as exc:
        build_osl_calibration(
            channel=CH_S11,
            frequency_hz=FREQUENCY_HZ,
            open_measured=zeros,
            short_measured=zeros.copy(),
            load_measured=zeros.copy(),
        )
    assert exc.value.code is ErrorCode.INVALID_ARGUMENT
    assert exc.value.context.get("kind") == "degenerate_standards"


def test_near_degenerate_but_healthy_standards_solve() -> None:
    d, t, s = _error_terms()
    open_m, short_m, load_m = _standard_measurements(d, t, s)
    short_near = short_m + 1e-6 * (open_m - short_m)
    profile = build_osl_calibration(
        channel=CH_S11,
        frequency_hz=FREQUENCY_HZ,
        open_measured=open_m,
        short_measured=short_near,
        load_measured=load_m,
        profile_id=PID_S11,
    )
    assert profile.n_frequencies == FREQUENCY_HZ.size


def test_correction_singular_dut_rejected() -> None:
    profile = _golden_scenario_profile()
    # x = -T/S makes the correction denominator vanish.
    d = profile.directivity
    t = profile.reflection_tracking
    s = profile.source_match
    measured = d - t / s
    with pytest.raises(DomainError) as exc:
        profile.correct(measured)
    assert exc.value.code is ErrorCode.INVALID_ARGUMENT
    assert exc.value.context.get("kind") == "correction_singular"


def test_nonfinite_inputs_rejected() -> None:
    d, t, s = _error_terms()
    open_m, short_m, load_m = _standard_measurements(d, t, s)
    bad_nan = open_m.copy()
    bad_nan[5] = np.nan + 0j
    with pytest.raises(DomainError) as exc:
        build_osl_calibration(
            channel=CH_S11,
            frequency_hz=FREQUENCY_HZ,
            open_measured=bad_nan,
            short_measured=short_m,
            load_measured=load_m,
        )
    assert exc.value.code is ErrorCode.INVALID_ARGUMENT
    bad_inf = short_m.copy()
    bad_inf[5] = np.inf * (1 + 0j)
    with pytest.raises(DomainError) as exc:
        build_osl_calibration(
            channel=CH_S11,
            frequency_hz=FREQUENCY_HZ,
            open_measured=open_m,
            short_measured=bad_inf,
            load_measured=load_m,
        )
    assert exc.value.code is ErrorCode.INVALID_ARGUMENT
    with pytest.raises(DomainError) as exc:
        build_osl_calibration(
            channel=CH_S11,
            frequency_hz=FREQUENCY_HZ,
            open_measured=open_m,
            short_measured=short_m,
            load_measured=load_m,
            open_actual=np.full(FREQUENCY_HZ.size, np.nan + 0j),
        )
    assert exc.value.code is ErrorCode.INVALID_ARGUMENT
    # Non-finite axis.
    bad_axis = FREQUENCY_HZ.copy()
    bad_axis[3] = np.nan
    with pytest.raises(DomainError) as exc:
        build_osl_calibration(
            channel=CH_S11,
            frequency_hz=bad_axis,
            open_measured=open_m,
            short_measured=short_m,
            load_measured=load_m,
        )
    assert exc.value.code is ErrorCode.NON_FINITE_AXIS


def test_axis_validation_fail_closed() -> None:
    d, t, s = _error_terms()
    open_m, short_m, load_m = _standard_measurements(d, t, s)
    decreasing = FREQUENCY_HZ[::-1].copy()
    with pytest.raises(DomainError) as exc:
        build_osl_calibration(
            channel=CH_S11,
            frequency_hz=decreasing,
            open_measured=open_m,
            short_measured=short_m,
            load_measured=load_m,
        )
    assert exc.value.code is ErrorCode.NON_INCREASING_AXIS
    duplicate = FREQUENCY_HZ.copy()
    duplicate[10] = duplicate[9]
    with pytest.raises(DomainError) as exc:
        build_osl_calibration(
            channel=CH_S11,
            frequency_hz=duplicate,
            open_measured=open_m,
            short_measured=short_m,
            load_measured=load_m,
        )
    assert exc.value.code is ErrorCode.NON_INCREASING_AXIS
    # Complex axis dtype.
    with pytest.raises(DomainError) as exc:
        build_osl_calibration(
            channel=CH_S11,
            frequency_hz=FREQUENCY_HZ.astype(np.complex128),
            open_measured=open_m,
            short_measured=short_m,
            load_measured=load_m,
        )
    assert exc.value.code is ErrorCode.DTYPE_MISMATCH


def test_dtype_and_shape_validation_fail_closed() -> None:
    d, t, s = _error_terms()
    open_m, short_m, load_m = _standard_measurements(d, t, s)
    # Float (non-complex) measurements are rejected.
    with pytest.raises(DomainError) as exc:
        build_osl_calibration(
            channel=CH_S11,
            frequency_hz=FREQUENCY_HZ,
            open_measured=np.abs(open_m),
            short_measured=short_m,
            load_measured=load_m,
        )
    assert exc.value.code is ErrorCode.DTYPE_MISMATCH
    # 3-D measurements are rejected.
    with pytest.raises(DomainError) as exc:
        build_osl_calibration(
            channel=CH_S11,
            frequency_hz=FREQUENCY_HZ,
            open_measured=open_m[np.newaxis, np.newaxis, :],
            short_measured=short_m,
            load_measured=load_m,
        )
    assert exc.value.code is ErrorCode.SHAPE_MISMATCH
    # Zero capture rows are rejected.
    with pytest.raises(DomainError) as exc:
        build_osl_calibration(
            channel=CH_S11,
            frequency_hz=FREQUENCY_HZ,
            open_measured=np.empty((0, FREQUENCY_HZ.size), dtype=np.complex128),
            short_measured=short_m,
            load_measured=load_m,
        )
    assert exc.value.code is ErrorCode.INVALID_ARGUMENT
    # Frequency-point mismatch is an axis mismatch.
    with pytest.raises(DomainError) as exc:
        build_osl_calibration(
            channel=CH_S11,
            frequency_hz=FREQUENCY_HZ,
            open_measured=open_m[:-1],
            short_measured=short_m,
            load_measured=load_m,
        )
    assert exc.value.code is ErrorCode.AXIS_MISMATCH


def test_correct_length_mismatch_and_dtype_rejected() -> None:
    profile = _golden_scenario_profile()
    d, t, s = _error_terms()
    measured = _forward(_dut_gamma(), d, t, s)
    with pytest.raises(DomainError) as exc:
        profile.correct(measured[:-1])
    assert exc.value.code is ErrorCode.AXIS_MISMATCH
    with pytest.raises(DomainError) as exc:
        profile.correct(np.abs(measured))
    assert exc.value.code is ErrorCode.DTYPE_MISMATCH
    with pytest.raises(DomainError) as exc:
        profile.correct(np.array(1.0 + 0j))  # scalar (0-d) is not a frequency axis
    assert exc.value.code is ErrorCode.SHAPE_MISMATCH


# ---------------------------------------------------------------------------
# Immutability: raw inputs never change; profile arrays are write-protected.
# ---------------------------------------------------------------------------


def test_build_never_mutates_inputs() -> None:
    d, t, s = _error_terms()
    open_m, short_m, load_m = _standard_measurements(d, t, s)
    axis_copy = FREQUENCY_HZ.copy()
    open_copy = open_m.copy()
    short_copy = short_m.copy()
    load_copy = load_m.copy()
    build_osl_calibration(
        channel=CH_S11,
        frequency_hz=FREQUENCY_HZ,
        open_measured=open_m,
        short_measured=short_m,
        load_measured=load_m,
        profile_id=PID_S11,
    )
    assert np.array_equal(FREQUENCY_HZ, axis_copy)
    assert np.array_equal(open_m, open_copy)
    assert np.array_equal(short_m, short_copy)
    assert np.array_equal(load_m, load_copy)


def test_later_input_mutation_does_not_affect_profile() -> None:
    open_m = np.ones(FREQUENCY_HZ.size, dtype=np.complex128) * (0.4 - 0.2j)
    short_m = np.ones(FREQUENCY_HZ.size, dtype=np.complex128) * (-0.5 + 0.3j)
    load_m = np.ones(FREQUENCY_HZ.size, dtype=np.complex128) * 0.02j
    profile = build_osl_calibration(
        channel=CH_S11,
        frequency_hz=FREQUENCY_HZ,
        open_measured=open_m,
        short_measured=short_m,
        load_measured=load_m,
        profile_id=PID_S11,
    )
    open_m[:] = 99.0 + 0j
    assert np.all(profile.open_measured_mean != 99.0)
    # Mutating a returned view is impossible: the base is write-protected.
    view = profile.directivity
    with pytest.raises(ValueError):
        view.setflags(write=True)  # type: ignore[attr-defined]
    with pytest.raises(FrozenInstanceError):
        profile.frequency_hz = FREQUENCY_HZ  # type: ignore[misc]


def test_correct_and_apply_never_mutate_inputs() -> None:
    profile = _golden_scenario_profile()
    d, t, s = _error_terms()
    measured = _forward(_dut_gamma(), d, t, s)
    before = measured.copy()
    profile.correct(measured)
    assert np.array_equal(measured, before)


def test_profile_equality_and_repr() -> None:
    first = _golden_scenario_profile(profile_id=PID_S11)
    second = _golden_scenario_profile(profile_id=PID_S11)
    assert first == second
    assert "OslCalibrationProfile" in repr(first)
    third = _golden_scenario_profile(
        profile_id=CalibrationProfileId("33333333-3333-4333-8333-333333333333")
    )
    assert first != third


# ---------------------------------------------------------------------------
# Model surface details.
# ---------------------------------------------------------------------------


def test_quality_object_is_frozen_and_typed() -> None:
    quality = _golden_scenario_profile().quality
    assert isinstance(quality, OslCalibrationQuality)
    assert OslStandard.OPEN.value == "open"
    assert OslStandard.SHORT.value == "short"
    assert OslStandard.LOAD.value == "load"
    with pytest.raises(FrozenInstanceError):
        quality.open_rms_abs_error = 0.0  # type: ignore[misc]


def test_build_requires_keyword_channel_and_generates_profile_id() -> None:
    d, t, s = _error_terms()
    open_m, short_m, load_m = _standard_measurements(d, t, s)
    profile = build_osl_calibration(
        channel=CH_S11,
        frequency_hz=FREQUENCY_HZ,
        open_measured=open_m,
        short_measured=short_m,
        load_measured=load_m,
    )
    assert isinstance(profile.profile_id, CalibrationProfileId)
    assert profile.profile_id is not None
