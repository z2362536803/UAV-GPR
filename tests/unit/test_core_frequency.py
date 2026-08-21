"""Tests for immutable channel and frequency-domain models (ISSUE-004)."""

from __future__ import annotations

import numpy as np
import pytest

from uav_gpr.core import (
    ChannelSpec,
    DomainError,
    ErrorCode,
    FrequencyScan,
    FrequencySweep,
    LogicalPolarization,
    SParameter,
)

HZ = np.array([1.0e9, 1.1e9, 1.2e9], dtype=np.float64)

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
