"""Immutable multi-channel frequency-domain models.

Fixed shapes (AGENTS.md section 3):

- one sweep:      ``channel x frequency``
- continuous:     ``trace x channel x frequency``

Every model owns a private immutable snapshot of its arrays.  The arrays are
backed by ``bytes`` (via ``numpy.frombuffer``), so neither the model's
properties nor any view or slice can ever be made writable again — the
``setflags(write=True)`` attack is rejected by NumPy itself.

Single-channel and dual-channel data use exactly the same classes; there is no
S11-only special model.  Combining or stacking always returns a new object and
never mutates the inputs.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Self

import numpy as np

from uav_gpr.core.channels import ChannelSpec
from uav_gpr.core.errors import DomainError, ErrorCode

_FREQUENCY_DTYPE = np.dtype(np.float64)
_DATA_DTYPE = np.dtype(np.complex128)


def _immutable_array(values: object, dtype: np.dtype) -> np.ndarray:
    """Copy values into an owned snapshot that can never become writable."""
    owned = np.array(values, dtype=dtype, copy=True, order="C")
    payload = owned.tobytes()
    return np.frombuffer(payload, dtype=dtype).reshape(owned.shape)


def _validate_channels(channels: Sequence[ChannelSpec]) -> tuple[ChannelSpec, ...]:
    result = tuple(channels)
    if not result:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT, "at least one channel is required"
        )
    for channel in result:
        if not isinstance(channel, ChannelSpec):
            raise TypeError(
                f"channels must contain ChannelSpec, got {type(channel).__name__}"
            )
    seen: list[str] = []
    for channel in result:
        if channel.channel_id in seen:
            raise DomainError(
                ErrorCode.DUPLICATE_CHANNEL,
                "duplicate channel_id in channel contract",
                {"channel_id": channel.channel_id, "channel_ids": list(seen)},
            )
        seen.append(channel.channel_id)
    return result


def _validate_frequency_axis(values: object) -> np.ndarray:
    raw = np.asarray(values)
    if raw.dtype.kind not in "iuf":
        raise DomainError(
            ErrorCode.DTYPE_MISMATCH,
            "frequency axis must be real-valued numeric",
            {"dtype": str(raw.dtype)},
        )
    if raw.ndim != 1:
        raise DomainError(
            ErrorCode.AXIS_MISMATCH,
            "frequency axis must be one-dimensional",
            {"ndim": raw.ndim},
        )
    if raw.size == 0:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT, "frequency axis must not be empty"
        )
    if not np.all(np.isfinite(raw)):
        raise DomainError(
            ErrorCode.NON_FINITE_AXIS,
            "frequency axis must contain only finite values",
        )
    if raw.size > 1 and not np.all(np.diff(raw) > 0):
        raise DomainError(
            ErrorCode.NON_INCREASING_AXIS,
            "frequency axis must be strictly increasing",
        )
    return np.asarray(raw, dtype=_FREQUENCY_DTYPE)


def _require_complex_numeric(values: object, field: str) -> np.ndarray:
    raw = np.asarray(values)
    if raw.dtype.kind not in "iufc":
        raise DomainError(
            ErrorCode.DTYPE_MISMATCH,
            f"{field} must be numeric (stored as complex128)",
            {"dtype": str(raw.dtype)},
        )
    return np.asarray(raw, dtype=_DATA_DTYPE)


def _channels_equal(left: Sequence[ChannelSpec], right: Sequence[ChannelSpec]) -> bool:
    return tuple(left) == tuple(right)


def _require_same_contract(
    left_channels: Sequence[ChannelSpec],
    left_frequencies: np.ndarray,
    right_channels: Sequence[ChannelSpec],
    right_frequencies: np.ndarray,
) -> None:
    if not _channels_equal(left_channels, right_channels):
        raise DomainError(
            ErrorCode.CHANNEL_CONTRACT_MISMATCH,
            "channel contracts differ between sweeps",
            {
                "left_channel_ids": [c.channel_id for c in left_channels],
                "right_channel_ids": [c.channel_id for c in right_channels],
            },
        )
    if not np.array_equal(left_frequencies, right_frequencies):
        raise DomainError(
            ErrorCode.AXIS_MISMATCH,
            "frequency axes differ between sweeps",
        )


@dataclass(frozen=True, slots=True)
class FrequencySweep:
    """One complete sweep: ``channel x frequency`` complex data."""

    channels: tuple[ChannelSpec, ...]
    frequencies_hz: np.ndarray
    data: np.ndarray

    def __post_init__(self) -> None:
        channels = _validate_channels(self.channels)
        frequencies = _validate_frequency_axis(self.frequencies_hz)
        data = _require_complex_numeric(self.data, "sweep data")
        expected = (len(channels), int(frequencies.size))
        if data.ndim != 2 or data.shape != expected:
            raise DomainError(
                ErrorCode.SHAPE_MISMATCH,
                "sweep data shape must be channel x frequency",
                {"expected": list(expected), "got": list(data.shape)},
            )
        object.__setattr__(self, "channels", channels)
        object.__setattr__(
            self, "frequencies_hz", _immutable_array(frequencies, _FREQUENCY_DTYPE)
        )
        object.__setattr__(self, "data", _immutable_array(data, _DATA_DTYPE))


@dataclass(frozen=True, slots=True)
class FrequencyScan:
    """Continuous data: ``trace x channel x frequency``.

    Every trace shares the same channel contract and frequency axis.
    """

    channels: tuple[ChannelSpec, ...]
    frequencies_hz: np.ndarray
    data: np.ndarray

    def __post_init__(self) -> None:
        channels = _validate_channels(self.channels)
        frequencies = _validate_frequency_axis(self.frequencies_hz)
        data = _require_complex_numeric(self.data, "scan data")
        if data.ndim != 3 or data.shape[0] == 0:
            raise DomainError(
                ErrorCode.SHAPE_MISMATCH,
                "scan data shape must be trace x channel x frequency",
                {"ndim": data.ndim, "got": list(data.shape)},
            )
        expected_trailing = (len(channels), int(frequencies.size))
        if data.shape[1:] != expected_trailing:
            raise DomainError(
                ErrorCode.SHAPE_MISMATCH,
                "scan trailing shape must be channel x frequency",
                {"expected": list(expected_trailing), "got": list(data.shape)},
            )
        object.__setattr__(self, "channels", channels)
        object.__setattr__(
            self, "frequencies_hz", _immutable_array(frequencies, _FREQUENCY_DTYPE)
        )
        object.__setattr__(self, "data", _immutable_array(data, _DATA_DTYPE))

    @classmethod
    def from_sweeps(cls, sweeps: Sequence[FrequencySweep]) -> Self:
        """Stack sweeps into a new scan; inputs are never mutated."""
        if not sweeps:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT, "at least one sweep is required"
            )
        first = sweeps[0]
        if not all(isinstance(item, FrequencySweep) for item in sweeps):
            raise TypeError("from_sweeps requires FrequencySweep items")
        for sweep in sweeps[1:]:
            _require_same_contract(
                first.channels,
                first.frequencies_hz,
                sweep.channels,
                sweep.frequencies_hz,
            )
        stacked = np.stack([item.data for item in sweeps], axis=0)
        return cls(channels=first.channels, frequencies_hz=first.frequencies_hz, data=stacked)

    def append(self, sweep: FrequencySweep) -> FrequencyScan:
        """Return a new scan with one extra trace; this object is unchanged."""
        _require_same_contract(
            self.channels,
            self.frequencies_hz,
            sweep.channels,
            sweep.frequencies_hz,
        )
        added = np.concatenate([self.data, sweep.data[np.newaxis, ...]], axis=0)
        return FrequencyScan(
            channels=self.channels,
            frequencies_hz=self.frequencies_hz,
            data=added,
        )
