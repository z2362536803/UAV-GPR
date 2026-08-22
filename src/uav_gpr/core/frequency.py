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
from dataclasses import dataclass, replace
from typing import Self

import numpy as np

from uav_gpr.core.channels import ChannelSpec
from uav_gpr.core.errors import DomainError, ErrorCode, JsonValue
from uav_gpr.core.metadata import TraceMetadata

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


def _trace_identity(metadata: TraceMetadata) -> tuple[str, int, str]:
    """The trace identity of a metadata object (mission, index, uid)."""
    return (
        metadata.mission_id.to_json(),
        metadata.trace_index,
        metadata.trace_uid.to_json(),
    )


# Acquisition facts that must never change during metadata evolution.
_ACQUISITION_FIELDS: tuple[str, ...] = (
    "device_id",
    "sweep_started_utc",
    "sweep_midpoint_utc",
    "sweep_finished_utc",
    "sweep_started_monotonic_ns",
    "sweep_midpoint_monotonic_ns",
    "sweep_finished_monotonic_ns",
    "target_interval_s",
    "actual_interval_s",
    "schedule_error_s",
    "connection_generation",
)


def _validate_metadata_evolution(old: TraceMetadata, new: TraceMetadata) -> None:
    """Shared fail-closed rule for sweep and scan metadata evolution.

    Allowed evolution between two non-equal metadata objects with the same
    trace identity:

    - ``raw_trace_sha256``: ``None -> None`` or ``None -> valid hash`` only
      (a bound hash can never be dropped or replaced by another hash);
    - ``gnss_match``, ``quality_status``, ``quality_reasons`` (via the
      copy-update APIs of ``TraceMetadata``).

    Everything else (identity and the acquisition facts) must be identical.
    """
    if _trace_identity(old) != _trace_identity(new):
        raise DomainError(
            ErrorCode.ID_CONFLICT,
            "metadata is already bound to a different trace identity",
            {
                "bound": list(_trace_identity(old)),
                "incoming": list(_trace_identity(new)),
            },
        )
    changed: list[JsonValue] = [
        field for field in _ACQUISITION_FIELDS
        if getattr(old, field) != getattr(new, field)
    ]
    if changed:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "acquisition facts cannot change during metadata evolution",
            {"changed_fields": changed},
        )
    if old.raw_trace_sha256 is None:
        return  # None -> None or None -> valid hash (constructor validates)
    if new.raw_trace_sha256 is None:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "cannot drop existing raw trace integrity",
            {"stored_hash": old.raw_trace_sha256},
        )
    if new.raw_trace_sha256 != old.raw_trace_sha256:
        raise DomainError(
            ErrorCode.ID_CONFLICT,
            "raw trace hash conflict",
            {
                "stored_hash": old.raw_trace_sha256,
                "incoming_hash": new.raw_trace_sha256,
            },
        )


@dataclass(frozen=True, slots=True)
class FrequencySweep:
    """One complete sweep: ``channel x frequency`` complex data.

    ``metadata`` is the per-trace ``TraceMetadata`` (``None`` while the trace
    is still acquired without metadata).  Attaching is fail-closed: existing
    metadata can never be silently detached (``with_metadata(None)``) or
    replaced by a different trace identity; only the same trace identity may
    evolve (e.g. acquired -> integrity-attached).
    """

    channels: tuple[ChannelSpec, ...]
    frequencies_hz: np.ndarray
    data: np.ndarray
    metadata: TraceMetadata | None = None

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
        if self.metadata is not None and not isinstance(self.metadata, TraceMetadata):
            raise TypeError(
                f"metadata must be a TraceMetadata or None, got {type(self.metadata).__name__}"
            )
        object.__setattr__(self, "channels", channels)
        object.__setattr__(
            self, "frequencies_hz", _immutable_array(frequencies, _FREQUENCY_DTYPE)
        )
        object.__setattr__(self, "data", _immutable_array(data, _DATA_DTYPE))

    def with_metadata(self, metadata: TraceMetadata | None) -> FrequencySweep:
        """Return a new sweep with metadata attached; fail-closed on removal.

        - unattached + ``None``: explicit no-op (returns ``self``);
        - unattached + metadata: first attach (new object);
        - attached + ``None``: rejected (silent deletion is forbidden);
        - attached + equal metadata: idempotent no-op (returns ``self``);
        - attached + same trace identity, allowed evolution: accepted only if
          acquisition facts are unchanged and the raw hash only moves from
          ``None`` to a valid hash (shared :func:`_validate_metadata_evolution`);
        - attached + different trace identity or conflicting hash: conflict.
        """
        if metadata is not None and not isinstance(metadata, TraceMetadata):
            raise TypeError(
                f"metadata must be a TraceMetadata or None, got {type(metadata).__name__}"
            )
        if self.metadata is None:
            if metadata is None:
                return self
            return replace(self, metadata=metadata)
        if metadata is None:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "cannot silently detach existing sweep metadata",
                {"trace_uid": self.metadata.trace_uid.to_json()},
            )
        if self.metadata == metadata:
            return self
        _validate_metadata_evolution(self.metadata, metadata)
        return replace(self, metadata=metadata)


@dataclass(frozen=True, slots=True)
class FrequencyScan:
    """Continuous data: ``trace x channel x frequency``.

    Every trace shares the same channel contract and frequency axis.
    ``metadata`` is a per-trace tuple aligned with the trace axis: a non-empty
    tuple must have exactly one entry per trace (``None`` allowed when a trace
    has no metadata yet); an empty tuple means no metadata is recorded.
    """

    channels: tuple[ChannelSpec, ...]
    frequencies_hz: np.ndarray
    data: np.ndarray
    metadata: tuple[TraceMetadata | None, ...] = ()

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
        metadata = _validate_scan_metadata(self.metadata, int(data.shape[0]))
        object.__setattr__(self, "channels", channels)
        object.__setattr__(
            self, "frequencies_hz", _immutable_array(frequencies, _FREQUENCY_DTYPE)
        )
        object.__setattr__(self, "data", _immutable_array(data, _DATA_DTYPE))
        object.__setattr__(self, "metadata", metadata)

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
        metadata = _compact_scan_metadata(
            tuple(item.metadata for item in sweeps)
        )
        return cls(
            channels=first.channels,
            frequencies_hz=first.frequencies_hz,
            data=stacked,
            metadata=metadata,
        )

    def append(self, sweep: FrequencySweep) -> FrequencyScan:
        """Return a new scan with one extra trace; this object is unchanged."""
        _require_same_contract(
            self.channels,
            self.frequencies_hz,
            sweep.channels,
            sweep.frequencies_hz,
        )
        added = np.concatenate([self.data, sweep.data[np.newaxis, ...]], axis=0)
        current = self.metadata
        if current == ():
            current = (None,) * int(self.data.shape[0])
        metadata = _compact_scan_metadata((*current, sweep.metadata))
        return FrequencyScan(
            channels=self.channels,
            frequencies_hz=self.frequencies_hz,
            data=added,
            metadata=metadata,
        )

    def with_metadata(
        self, metadata: Sequence[TraceMetadata | None]
    ) -> FrequencyScan:
        """Return a new scan with a full per-trace metadata tuple attached.

        Fail-closed: an empty or all-``None`` tuple is rejected while any
        metadata is attached (silent deletion), per-trace detachments and
        different trace identities are rejected; filling a ``None`` slot,
        keeping equal entries, or applying a *valid* evolution to the same
        trace identity (acquisition facts unchanged, raw hash only
        ``None -> valid``) is allowed.  Re-attaching the exact same tuple is
        an idempotent no-op.
        """
        incoming = tuple(metadata)
        n_traces = int(self.data.shape[0])
        for item in incoming:
            if item is not None and not isinstance(item, TraceMetadata):
                raise TypeError(
                    f"scan metadata entries must be TraceMetadata or None, "
                    f"got {type(item).__name__}"
                )
        current = self.metadata
        if not incoming or all(item is None for item in incoming):
            if current == ():
                return self  # explicit no-op: nothing is attached
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "cannot silently clear existing scan metadata",
                {"metadata_count": len(current)},
            )
        if len(incoming) != n_traces:
            raise DomainError(
                ErrorCode.SHAPE_MISMATCH,
                "scan metadata count must match the trace axis",
                {"n_traces": n_traces, "metadata_count": len(incoming)},
            )
        if incoming == current:
            return self  # idempotent no-op
        aligned_current = current if current else (None,) * n_traces
        for old, new in zip(aligned_current, incoming, strict=True):
            if old is None or old == new:
                continue
            if new is None:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "cannot silently detach existing scan metadata",
                    {"trace_uid": old.trace_uid.to_json()},
                )
            _validate_metadata_evolution(old, new)
        return replace(self, metadata=incoming)


def _validate_scan_metadata(
    metadata: Sequence[TraceMetadata | None], n_traces: int
) -> tuple[TraceMetadata | None, ...]:
    result = tuple(metadata)
    if not result:
        return ()
    if len(result) != n_traces:
        raise DomainError(
            ErrorCode.SHAPE_MISMATCH,
            "scan metadata count must match the trace axis",
            {"n_traces": n_traces, "metadata_count": len(result)},
        )
    for item in result:
        if item is not None and not isinstance(item, TraceMetadata):
            raise TypeError(
                f"scan metadata entries must be TraceMetadata or None, "
                f"got {type(item).__name__}"
            )
    seen_uids: set[str] = set()
    seen_identities: set[tuple[str, int]] = set()
    mission_id: str | None = None
    last_index: int | None = None
    for item in result:
        if item is None:
            continue
        uid = item.trace_uid.to_json()
        identity = (item.mission_id.to_json(), item.trace_index)
        if uid in seen_uids:
            raise DomainError(
                ErrorCode.ID_CONFLICT,
                "duplicate trace_uid in scan metadata",
                {"trace_uid": uid},
            )
        seen_uids.add(uid)
        if identity in seen_identities:
            raise DomainError(
                ErrorCode.ID_CONFLICT,
                "duplicate mission_id + trace_index in scan metadata",
                {"mission_id": identity[0], "trace_index": identity[1]},
            )
        seen_identities.add(identity)
        if mission_id is None:
            mission_id = identity[0]
        elif identity[0] != mission_id:
            raise DomainError(
                ErrorCode.ID_CONFLICT,
                "scan metadata mixes traces from different missions",
                {
                    "expected_mission_id": mission_id,
                    "got_mission_id": identity[0],
                },
            )
        if last_index is not None and item.trace_index <= last_index:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "scan metadata trace_index must strictly increase "
                "along the trace axis",
                {"previous_trace_index": last_index, "got": item.trace_index},
            )
        last_index = item.trace_index
    return result


def _compact_scan_metadata(
    metadata: Sequence[TraceMetadata | None],
) -> tuple[TraceMetadata | None, ...]:
    """Return ``()`` when no trace has metadata, else the aligned tuple."""
    result = tuple(metadata)
    if all(item is None for item in result):
        return ()
    return result
