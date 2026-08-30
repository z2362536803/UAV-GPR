"""ISSUE-010: single-owner incremental ``.partial.rcscan`` writer.

This module implements the reliable local-commit half of store-then-forward
(AGENTS.md section 6, ``docs/ARCHITECTURE.md`` section 4.2, ADR-0004) on top
of the frozen physical schema of :mod:`uav_gpr.storage.rcscan_v2` (ISSUE-008)
and the canonical raw hash of :mod:`uav_gpr.core.raw_hash` (ISSUE-009).

Lifecycle (``docs/DATA_FORMAT.md`` section 4)::

    create <file_id>.partial.rcscan        lifecycle_state = writing
      -> append/flush/checkpoint per committed trace
      -> set mission end + completion_kind
      -> lifecycle_state = finalized
      -> close HDF5
      -> atomic rename to <mission_id>.rcscan

Reliability contract (docs/DATA_FORMAT.md section 3)
----------------------------------------------------

HDF5 has no cross-dataset transaction, so one logical trace commit is an
explicitly ordered sequence::

    raw -> trace metadata + GNSS + hash columns -> flush()
      -> committed_record_count (+ last_trace_index, updated_utc) -> flush()

The checkpoint is therefore the *last* thing that changes: it can only ever
point at data that was already flushed to the file.  Any failure before the
checkpoint write leaves a physical row that no checkpoint-respecting reader
may see (a "half trace" is invisible by construction).  Every failure closes
the HDF5 handle and leaves the ``.partial.rcscan`` on disk for a later
non-destructive recovery (ISSUE-012 is deliberately not implemented here).

Physical rows are the commit order and are **not** ``trace_index``: the
ground side may append retransmitted traces out of order.  The logical
identity is always the explicit ``trace_index``/``trace_uid`` pair.

Frozen contract
---------------

``mission_id``, ``MissionConfig`` (digest), the frequency axis and the channel
tuple are frozen at creation.  Every append re-states them and an
incompatible axis / channel / config is rejected fail-closed instead of being
silently accepted.

Duplicate vs conflict (AGENTS.md section 4)
-------------------------------------------

* same ``trace_index`` + same raw hash -> :attr:`AppendDecision.DUPLICATE`,
  an idempotent no-op (safe retransmission): nothing is written and the
  checkpoint does not move;
* same ``trace_index`` + different hash -> :attr:`AppendDecision.CONFLICT`,
  rejected with ``ErrorCode.ID_CONFLICT``; the committed record is preserved
  and an immutable :class:`TraceConflict` evidence entry is retained;
* a submitted trace that already carries a ``raw_trace_sha256`` contradicting
  the digest recomputed from its own raw data -> the same conflict path
  (claimed identity and raw data disagree; fail-closed with evidence).

Fault injection
---------------

:class:`WritePhase` names every observable step of a commit and a finalize.
A :class:`StorageFaultHook` (see :class:`PhaseFaultHook`) is called at each
phase, and the filesystem is reached through a replaceable
:class:`FileSystemFacade`.  Both seams are part of the production API so the
failure matrix is deterministic (no sleeps, no timing guessing).

Out of scope: readers, recovery tools, network ACK, outbox, UI, v1 migration
and processing.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

import h5py  # type: ignore[import-untyped]
import numpy as np

from uav_gpr.core.channels import ChannelSpec
from uav_gpr.core.config import MissionConfig
from uav_gpr.core.enums import EndpointRole, MissionTerminalState
from uav_gpr.core.errors import DomainError, ErrorCode, JsonValue
from uav_gpr.core.identifiers import AirFileId, DeviceId, GroundFileId, MissionId
from uav_gpr.core.metadata import TraceMetadata
from uav_gpr.core.raw_hash import compute_raw_trace_sha256
from uav_gpr.core.timeutil import Clock, SystemClock, ensure_utc, to_utc_iso
from uav_gpr.storage import rcscan_v2 as schema

__all__ = [
    "AppendDecision",
    "AppendResult",
    "FileSystemFacade",
    "FinalizeResult",
    "FrozenWriterContract",
    "InjectedStorageFault",
    "LocalFileSystemFacade",
    "PhaseFaultHook",
    "RcScanIncrementalWriter",
    "StorageFaultHook",
    "TraceAppendRequest",
    "TraceConflict",
    "WritePhase",
    "WriterState",
]

PARTIAL_SUFFIX = ".partial.rcscan"
FINAL_SUFFIX = ".rcscan"

#: Writer component version recorded in ``writer_version`` consumers' logs and
#: in the returned results (the HDF5 ``writer_version`` attribute carries the
#: caller-supplied token, which is frozen by ISSUE-008).
WRITER_COMPONENT_VERSION = "issue010.1"

#: Trace-major physical column groups written per logical commit.
_ROW_PREFIXES = ("/trace_metadata/", "/gnss/", "/acquisition/", "/transport/")


class WriterState(StrEnum):
    """Lifecycle of one writer instance (single owner, single file)."""

    OPEN = "open"
    #: HDF5 closed and finalized on disk, atomic rename still pending/failed.
    AWAITING_RENAME = "awaiting_rename"
    #: HDF5 closed, file renamed to the final ``.rcscan``.
    FINALIZED = "finalized"
    #: HDF5 closed without finalize: the partial is left for recovery.
    ABORTED = "aborted"


class WritePhase(StrEnum):
    """Every observable step of a trace commit and a finalize (fault seams)."""

    BEFORE_RAW_WRITE = "before_raw_write"
    AFTER_RAW_WRITE = "after_raw_write"
    AFTER_TRACE_COLUMNS = "after_trace_columns"
    AFTER_DATA_FLUSH = "after_data_flush"
    AFTER_CHECKPOINT_WRITE = "after_checkpoint_write"
    AFTER_COMMIT_FLUSH = "after_commit_flush"
    BEFORE_FINALIZE = "before_finalize"
    AFTER_FINALIZE_MARK = "after_finalize_mark"
    AFTER_FINALIZE_FLUSH = "after_finalize_flush"
    BEFORE_RENAME = "before_rename"


class AppendDecision(StrEnum):
    """Outcome classification of one append attempt."""

    NEW = "new"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"


class InjectedStorageFault(RuntimeError):
    """Deterministic fault raised by :class:`PhaseFaultHook`."""


class StorageFaultHook(Protocol):
    """Called by the writer at every :class:`WritePhase` seam."""

    def on_phase(self, phase: WritePhase) -> None:
        """Observe (or fail at) one writer phase."""
        ...


class PhaseFaultHook:
    """Mutable fault-injection hook: arms named phases to raise on demand.

    ``observed`` records every phase in arrival order so a test can prove the
    commit sequence itself, not only its outcome.  Arming is independent of
    the writer lifecycle, so a fault can be targeted at the Nth append.
    """

    def __init__(
        self,
        *phases: WritePhase,
        exception: type[BaseException] = InjectedStorageFault,
    ) -> None:
        self._armed: set[WritePhase] = set(phases)
        self._exception = exception
        self._observed: list[WritePhase] = []

    def arm(self, *phases: WritePhase) -> None:
        self._armed.update(phases)

    def disarm(self, *phases: WritePhase) -> None:
        self._armed.difference_update(phases)

    @property
    def observed(self) -> tuple[WritePhase, ...]:
        return tuple(self._observed)

    @property
    def armed(self) -> frozenset[WritePhase]:
        return frozenset(self._armed)

    def on_phase(self, phase: WritePhase) -> None:
        self._observed.append(phase)
        if phase in self._armed:
            raise self._exception(f"injected storage fault at phase {phase.value}")


class FileSystemFacade(Protocol):
    """The only filesystem surface the writer uses (injectable for tests)."""

    def exists(self, path: Path) -> bool:
        """Whether ``path`` exists (target-collision guard before rename)."""
        ...

    def replace(self, source: Path, target: Path) -> None:
        """Atomically move ``source`` to ``target``.

        ``os.replace`` overwrites an existing target unconditionally, so the
        writer guards every call with :meth:`exists`; a facade may implement
        the move itself as fail-closed instead.
        """
        ...


class LocalFileSystemFacade:
    """Production facade: ``Path.exists`` and ``os.replace`` (atomic, same volume)."""

    def exists(self, path: Path) -> bool:
        return path.exists()

    def replace(self, source: Path, target: Path) -> None:
        os.replace(source, target)


@dataclass(frozen=True)
class FrozenWriterContract:
    """Everything frozen when the partial file was created."""

    mission_id: MissionId
    device_id: DeviceId
    role: EndpointRole
    file_id: AirFileId | GroundFileId
    channels: tuple[ChannelSpec, ...]
    frequencies_hz: np.ndarray
    config_sha256: str
    writer_version: str
    created_utc: datetime


@dataclass(frozen=True)
class TraceAppendRequest:
    """One immutable logical trace submission to the writer.

    ``channels``, ``frequencies_hz`` and (optionally) ``config_sha256`` are
    re-stated by the caller so an incompatible sweep is rejected at the
    storage boundary instead of being silently written.
    """

    metadata: TraceMetadata
    frequency_raw: np.ndarray
    channels: tuple[ChannelSpec, ...]
    frequencies_hz: np.ndarray
    config_sha256: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.metadata, TraceMetadata):
            raise TypeError(
                f"metadata must be a TraceMetadata, got {type(self.metadata).__name__}"
            )
        if not isinstance(self.frequency_raw, np.ndarray):
            raise TypeError(
                "frequency_raw must be a numpy ndarray, "
                f"got {type(self.frequency_raw).__name__}"
            )
        if not isinstance(self.channels, tuple) or not all(
            isinstance(channel, ChannelSpec) for channel in self.channels
        ):
            raise TypeError("channels must be a tuple of ChannelSpec")
        if not isinstance(self.frequencies_hz, np.ndarray):
            raise TypeError(
                "frequencies_hz must be a numpy ndarray, "
                f"got {type(self.frequencies_hz).__name__}"
            )
        if self.config_sha256 is not None and not isinstance(self.config_sha256, str):
            raise TypeError("config_sha256 must be a string or None")


@dataclass(frozen=True)
class AppendResult:
    """Outcome of one accepted (or duplicate) append."""

    decision: AppendDecision
    trace_index: int
    trace_uid: str
    record_position: int
    committed_record_count: int
    raw_trace_sha256: str


@dataclass(frozen=True)
class TraceConflict:
    """Immutable evidence of one rejected conflicting trace."""

    trace_index: int
    record_position: int
    stored_hash: str
    incoming_hash: str
    stored_trace_uid: str
    incoming_trace_uid: str
    detected_utc: datetime


@dataclass(frozen=True)
class FinalizeResult:
    """Outcome of a successful :meth:`RcScanIncrementalWriter.close`."""

    partial_path: Path
    final_path: Path
    completion_kind: MissionTerminalState
    committed_record_count: int
    ended_utc: datetime
    writer_component_version: str = WRITER_COMPONENT_VERSION


def _require_axis(values: object, field: str) -> np.ndarray:
    axis = np.asarray(values, dtype=np.float64)
    if axis.ndim != 1:
        raise DomainError(
            ErrorCode.AXIS_MISMATCH,
            f"{field} must be one-dimensional",
            {"ndim": int(axis.ndim)},
        )
    if axis.size < 2:
        raise DomainError(
            ErrorCode.AXIS_MISMATCH,
            f"{field} requires at least 2 points",
            {"frequency_points": int(axis.size)},
        )
    if not np.all(np.isfinite(axis)):
        raise DomainError(ErrorCode.NON_FINITE_AXIS, f"{field} must be finite")
    if not np.all(np.diff(axis) > 0):
        raise DomainError(
            ErrorCode.NON_INCREASING_AXIS, f"{field} must be strictly increasing"
        )
    return axis


def _require_channels(channels: Sequence[ChannelSpec]) -> tuple[ChannelSpec, ...]:
    result = tuple(channels)
    if not result:
        raise DomainError(
            ErrorCode.CHANNEL_CONTRACT_MISMATCH, "at least one channel is required"
        )
    for channel in result:
        if not isinstance(channel, ChannelSpec):
            raise DomainError(
                ErrorCode.CHANNEL_CONTRACT_MISMATCH,
                "channels must contain ChannelSpec",
                {"channel_type": type(channel).__name__},
            )
    return result


class RcScanIncrementalWriter:
    """Single-owner incremental ``.partial.rcscan`` writer (ISSUE-010).

    Use :meth:`create` to build one; the HDF5 handle is owned exclusively by
    the instance.  ``append_trace`` commits one logical trace;
    :meth:`close` finalizes and atomically renames to ``<mission_id>.rcscan``;
    :meth:`abort` closes without finalizing and leaves the partial intact for
    recovery.  Instances are not thread-safe: other threads must submit
    immutable requests through a bounded queue (AGENTS.md section 7).
    """

    def __init__(
        self,
        *,
        handle: Any,
        partial_path: Path,
        final_path: Path,
        contract: FrozenWriterContract,
        row_paths: tuple[str, ...],
        channel_count: int,
        frequency_points: int,
        clock: Clock,
        fault_hook: StorageFaultHook,
        filesystem: FileSystemFacade,
    ) -> None:
        self._h5: Any = handle
        self._partial_path = partial_path
        self._final_path = final_path
        self._contract = contract
        self._row_paths = row_paths
        self._channel_count = channel_count
        self._frequency_points = frequency_points
        self._clock = clock
        self._fault_hook = fault_hook
        self._filesystem = filesystem
        self._state = WriterState.OPEN
        self._physical_rows = 0
        self._committed = 0
        self._last_trace_index: int | None = None
        self._by_trace_index: dict[int, tuple[int, str, str]] = {}
        self._by_trace_uid: dict[str, int] = {}
        #: position -> trace_index, so the reverse lookup is O(1) per row.
        self._by_position: dict[int, int] = {}
        self._conflicts: list[TraceConflict] = []
        self._finalize_result: FinalizeResult | None = None
        self._finalize_marks_written = False
        self._ended_utc: datetime | None = None

    # -- construction ------------------------------------------------------

    @classmethod
    def create(
        cls,
        directory: str | Path,
        *,
        mission_id: MissionId,
        device_id: DeviceId,
        file_id: AirFileId | GroundFileId,
        role: EndpointRole,
        config: MissionConfig,
        channels: Sequence[ChannelSpec],
        frequencies_hz: object,
        created_utc: datetime,
        writer_version: str,
        started_utc: datetime | None = None,
        clock: Clock | None = None,
        fault_hook: StorageFaultHook | None = None,
        filesystem: FileSystemFacade | None = None,
        hdf5_opener: Callable[[Path], Any] | None = None,
    ) -> RcScanIncrementalWriter:
        """Create ``<file_id>.partial.rcscan`` in ``writing`` state and own it.

        Everything is validated before the file is touched; the mission
        config, the frequency axis and the channel tuple must agree with each
        other, which is what "frozen contract" means for later appends.

        ``hdf5_opener`` replaces the default ``h5py.File(path, "r+")``.  It is
        a production seam like ``fault_hook`` and ``filesystem``: HDF5 in this
        environment makes writes visible to other processes immediately, so
        only the handle itself can prove that a commit really calls
        ``flush()``.  The default keeps the previous behaviour unchanged.

        Note: a pre-existing final ``<mission_id>.rcscan`` is *not* rejected
        here; it is detected fail-closed at finalize time (see :meth:`close`).
        """
        if not isinstance(mission_id, MissionId):
            raise TypeError(
                f"mission_id must be a MissionId, got {type(mission_id).__name__}"
            )
        if not isinstance(device_id, DeviceId):
            raise TypeError(
                f"device_id must be a DeviceId, got {type(device_id).__name__}"
            )
        if not isinstance(role, EndpointRole):
            raise TypeError(
                f"role must be an EndpointRole, got {type(role).__name__}"
            )
        if not isinstance(config, MissionConfig):
            raise TypeError(
                f"config must be a MissionConfig, got {type(config).__name__}"
            )
        expected_file_id = AirFileId if role is EndpointRole.AIR else GroundFileId
        if not isinstance(file_id, expected_file_id):
            raise TypeError(
                f"file_id must be a {expected_file_id.__name__} for role "
                f"{role.value}, got {type(file_id).__name__}"
            )
        channel_tuple = _require_channels(channels)
        axis = _require_axis(frequencies_hz, "frequencies_hz")
        if channel_tuple != config.channels:
            raise DomainError(
                ErrorCode.CHANNEL_CONTRACT_MISMATCH,
                "writer channels do not match the mission config",
                {
                    "writer_channels": [c.channel_id for c in channel_tuple],
                    "config_channels": [c.channel_id for c in config.channels],
                },
            )
        config_axis = np.asarray(config.frequency_axis_hz, dtype=np.float64)
        if axis.shape != config_axis.shape or not np.array_equal(axis, config_axis):
            raise DomainError(
                ErrorCode.AXIS_MISMATCH,
                "frequency axis does not match the mission config axis",
                {
                    "axis_points": int(axis.size),
                    "config_points": int(config_axis.size),
                },
            )
        created = ensure_utc(created_utc)
        started = ensure_utc(started_utc) if started_utc is not None else created

        directory_path = Path(directory)
        partial_path = directory_path / f"{file_id}{PARTIAL_SUFFIX}"
        final_path = directory_path / f"{mission_id}{FINAL_SUFFIX}"
        schema.create_rcscan_v2(
            partial_path,
            mission_id=mission_id,
            device_id=device_id,
            file_id=file_id,
            created_utc=created,
            completed_utc=None,
            completion_kind=None,
            file_role=role,
            channels=channel_tuple,
            frequencies_hz=axis,
            config_json=config.to_canonical_json(),
            config_sha256=config.config_sha256,
            writer_version=writer_version,
        )
        open_handle: Callable[[Path], Any] = (
            hdf5_opener if hdf5_opener is not None else lambda path: h5py.File(path, "r+")
        )
        handle = open_handle(partial_path)
        try:
            handle["mission"].attrs["started_utc"] = to_utc_iso(started)
            handle.flush()
        except BaseException:
            handle.close()
            raise

        contracts = schema.dataset_contracts(len(channel_tuple), int(axis.size))
        row_paths = tuple(
            contract.path
            for contract in contracts
            if not contract.optional
            and contract.path.startswith(_ROW_PREFIXES)
            and contract.path in handle
        )
        return cls(
            handle=handle,
            partial_path=partial_path,
            final_path=final_path,
            contract=FrozenWriterContract(
                mission_id=mission_id,
                device_id=device_id,
                role=role,
                file_id=file_id,
                channels=channel_tuple,
                frequencies_hz=axis,
                config_sha256=config.config_sha256,
                writer_version=writer_version,
                created_utc=created,
            ),
            row_paths=row_paths,
            channel_count=len(channel_tuple),
            frequency_points=int(axis.size),
            clock=clock if clock is not None else SystemClock(),
            fault_hook=fault_hook if fault_hook is not None else PhaseFaultHook(),
            filesystem=filesystem if filesystem is not None else LocalFileSystemFacade(),
        )

    # -- introspection -----------------------------------------------------

    @property
    def state(self) -> WriterState:
        return self._state

    @property
    def partial_path(self) -> Path:
        return self._partial_path

    @property
    def final_path(self) -> Path:
        return self._final_path

    @property
    def frozen_contract(self) -> FrozenWriterContract:
        return self._contract

    @property
    def config_sha256(self) -> str:
        return self._contract.config_sha256

    @property
    def channel_count(self) -> int:
        return self._channel_count

    @property
    def frequency_points(self) -> int:
        return self._frequency_points

    @property
    def committed_record_count(self) -> int:
        """Checkpoint: how many physical rows a reader may see."""
        return self._committed

    @property
    def physical_record_count(self) -> int:
        """Physical rows allocated so far (may exceed the checkpoint)."""
        return self._physical_rows

    @property
    def conflicts(self) -> tuple[TraceConflict, ...]:
        """Immutable evidence of every rejected conflicting trace."""
        return tuple(self._conflicts)

    @property
    def is_open(self) -> bool:
        return self._state is WriterState.OPEN

    def logical_trace_indices(self) -> tuple[int, ...]:
        """Committed logical trace indices in logical (sorted) order."""
        return tuple(sorted(self._by_trace_index))

    def record_position_for(self, trace_index: int) -> int:
        """Physical row of a committed ``trace_index`` (never the same thing)."""
        entry = self._by_trace_index.get(int(trace_index))
        if entry is None:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "trace_index is not committed in this file",
                {"trace_index": int(trace_index)},
            )
        return entry[0]

    def trace_index_at_record(self, record_position: int) -> int:
        """Logical ``trace_index`` stored at a physical row (fail-closed)."""
        trace_index = self._index_of_position(int(record_position))
        if trace_index >= 0:
            return trace_index
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "record_position is not committed in this file",
            {"record_position": int(record_position)},
        )

    def classify_trace(self, trace_index: int, raw_trace_sha256: str) -> AppendDecision:
        """Pre-append classification: new, duplicate (same hash) or conflict."""
        if not isinstance(trace_index, int) or isinstance(trace_index, bool):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "trace_index must be an int",
                {"trace_index_type": type(trace_index).__name__},
            )
        if not isinstance(raw_trace_sha256, str):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "raw_trace_sha256 must be a string",
                {"raw_trace_sha256_type": type(raw_trace_sha256).__name__},
            )
        entry = self._by_trace_index.get(trace_index)
        if entry is None:
            return AppendDecision.NEW
        return AppendDecision.DUPLICATE if entry[1] == raw_trace_sha256 else AppendDecision.CONFLICT

    # -- writing -----------------------------------------------------------

    def flush(self) -> None:
        """Flush the HDF5 handle; never moves the checkpoint."""
        self._require_open("flush")
        self._h5.flush()

    def append_trace(self, request: TraceAppendRequest) -> AppendResult:
        """Commit one logical trace (raw + metadata + GNSS + hash + checkpoint).

        The commit order is the reliability contract: all trace data is
        written and flushed *before* ``committed_record_count`` moves, and the
        checkpoint is flushed again immediately afterwards.  A rejected
        request (incompatible axis/channel/config, conflict, bad shape) leaves
        the writer usable and the file untouched; an I/O fault closes the
        handle and aborts, leaving the partial for recovery.
        """
        if not isinstance(request, TraceAppendRequest):
            raise TypeError(
                f"request must be a TraceAppendRequest, got {type(request).__name__}"
            )
        self._require_open("append a trace")
        try:
            return self._append_trace(request)
        except DomainError:
            raise
        except BaseException:
            self._force_close_handle(WriterState.ABORTED)
            raise

    def _append_trace(self, request: TraceAppendRequest) -> AppendResult:
        metadata = request.metadata
        if metadata.mission_id != self._contract.mission_id:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "trace mission_id does not match the frozen mission",
                {
                    "trace_mission_id": metadata.mission_id.to_json(),
                    "frozen_mission_id": self._contract.mission_id.to_json(),
                },
            )
        trace_index = metadata.trace_index
        self._require_frozen_contract(request)

        raw = self._require_raw(request.frequency_raw)
        digest = compute_raw_trace_sha256(
            mission_id=self._contract.mission_id,
            trace_index=trace_index,
            trace_uid=metadata.trace_uid,
            channels=self._contract.channels,
            frequencies_hz=self._contract.frequencies_hz,
            data=raw,
        )
        uid_text = metadata.trace_uid.to_json()
        # A pre-attached hash that contradicts the digest recomputed from the
        # trace's own raw data is a conflict of the same kind (claimed identity
        # vs. raw data), so it must leave the same evidence trail instead of
        # escaping through the domain model alone (AGENTS.md section 4).
        preset_hash = metadata.raw_trace_sha256
        if preset_hash is not None and preset_hash != digest:
            stored = self._by_trace_index.get(trace_index)
            self._record_conflict(
                trace_index=trace_index,
                # ``-1``: nothing was ever committed under this index here.
                stored=stored if stored is not None else (-1, preset_hash, uid_text),
                incoming_hash=digest,
                incoming_uid=uid_text,
            )
        # Attaching a different hash to an already-attached trace is a
        # conflict (ErrorCode.ID_CONFLICT) raised by the domain model itself.
        metadata = metadata.with_integrity(digest)

        existing = self._by_trace_index.get(trace_index)
        if existing is not None:
            if existing[1] == digest:
                return AppendResult(
                    decision=AppendDecision.DUPLICATE,
                    trace_index=trace_index,
                    trace_uid=uid_text,
                    record_position=existing[0],
                    committed_record_count=self._committed,
                    raw_trace_sha256=digest,
                )
            self._record_conflict(
                trace_index=trace_index,
                stored=existing,
                incoming_hash=digest,
                incoming_uid=uid_text,
            )
        other_index = self._by_trace_uid.get(uid_text)
        if other_index is not None and other_index != trace_index:
            self._record_conflict(
                trace_index=trace_index,
                stored=self._by_trace_index[other_index],
                incoming_hash=digest,
                incoming_uid=uid_text,
                conflicting_uid=True,
            )

        position = self._physical_rows
        self._fault_hook.on_phase(WritePhase.BEFORE_RAW_WRITE)

        raw_dataset = self._h5["/frequency/raw"]
        raw_dataset.resize((position + 1, self._channel_count, self._frequency_points))
        raw_dataset[position] = raw
        self._physical_rows = position + 1
        self._fault_hook.on_phase(WritePhase.AFTER_RAW_WRITE)

        cells = schema.trace_metadata_to_cells(metadata)
        self._write_row(position, cells)
        self._fault_hook.on_phase(WritePhase.AFTER_TRACE_COLUMNS)

        # Flush #1: the whole trace row is durable before it may be committed.
        self._flush(WritePhase.AFTER_DATA_FLUSH)

        # The checkpoint moves only now: everything above is already durable.
        self._h5["/checkpoints/committed_record_count"][0] = np.int64(position + 1)
        last_index = trace_index if self._last_trace_index is None else max(
            self._last_trace_index, trace_index
        )
        self._h5["/checkpoints/last_trace_index"][0] = np.int64(last_index)
        self._h5["/checkpoints/updated_utc"][0] = to_utc_iso(self._clock.utc_now())
        self._fault_hook.on_phase(WritePhase.AFTER_CHECKPOINT_WRITE)

        # Flush #2: publish the checkpoint itself.
        self._flush(WritePhase.AFTER_COMMIT_FLUSH)

        self._committed = position + 1
        self._last_trace_index = last_index
        self._by_trace_index[trace_index] = (position, digest, uid_text)
        self._by_trace_uid[uid_text] = trace_index
        self._by_position[position] = trace_index
        return AppendResult(
            decision=AppendDecision.NEW,
            trace_index=trace_index,
            trace_uid=uid_text,
            record_position=position,
            committed_record_count=self._committed,
            raw_trace_sha256=digest,
        )

    def _flush(self, phase: WritePhase) -> None:
        """Flush the HDF5 handle and announce ``phase`` as one atomic step.

        Coupling the flush and its announcement is deliberate: the commit
        sequence can only be reordered by deleting a phase, which the
        phase-sequence tests detect.
        """
        self._h5.flush()
        self._fault_hook.on_phase(phase)

    def _require_frozen_contract(self, request: TraceAppendRequest) -> None:
        axis = np.asarray(request.frequencies_hz, dtype=np.float64)
        frozen_axis = self._contract.frequencies_hz
        if axis.shape != frozen_axis.shape or not np.array_equal(axis, frozen_axis):
            raise DomainError(
                ErrorCode.AXIS_MISMATCH,
                "sweep frequency axis does not match the frozen mission axis",
                {
                    "sweep_points": int(axis.size) if axis.ndim == 1 else -1,
                    "frozen_points": int(frozen_axis.size),
                },
            )
        if request.channels != self._contract.channels:
            raise DomainError(
                ErrorCode.CHANNEL_CONTRACT_MISMATCH,
                "sweep channels do not match the frozen channel contract",
                {
                    "sweep_channels": [c.channel_id for c in request.channels],
                    "frozen_channels": [c.channel_id for c in self._contract.channels],
                },
            )
        if (
            request.config_sha256 is not None
            and request.config_sha256 != self._contract.config_sha256
        ):
            raise DomainError(
                ErrorCode.CONFIG_DIGEST_MISMATCH,
                "sweep config digest does not match the frozen mission config",
                {
                    "sweep_config_sha256": request.config_sha256,
                    "frozen_config_sha256": self._contract.config_sha256,
                },
            )

    def _require_raw(self, values: np.ndarray) -> np.ndarray:
        raw = np.asarray(values)
        if raw.ndim != 2:
            raise DomainError(
                ErrorCode.SHAPE_MISMATCH,
                "raw sweep data must be channel x frequency",
                {"ndim": int(raw.ndim), "got": [int(v) for v in raw.shape]},
            )
        expected = (self._channel_count, self._frequency_points)
        if raw.shape != expected:
            raise DomainError(
                ErrorCode.SHAPE_MISMATCH,
                "raw sweep shape must match the frozen channel and frequency contract",
                {"expected": list(expected), "got": [int(v) for v in raw.shape]},
            )
        if raw.dtype.kind not in "iufc":
            raise DomainError(
                ErrorCode.DTYPE_MISMATCH,
                "raw sweep data must be numeric (stored as complex128)",
                {"dtype": str(raw.dtype)},
            )
        return np.ascontiguousarray(raw, dtype="<c16")

    def _write_row(self, position: int, cells: dict[str, object]) -> None:
        missing = [path for path in self._row_paths if path not in cells]
        if missing:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "row projection is missing required physical columns",
                {"missing_columns": [str(path) for path in sorted(missing)]},
            )
        for path in self._row_paths:
            dataset = self._h5[path]
            dataset.resize((position + 1,))
            dataset[position] = cells[path]

    def _index_of_position(self, record_position: int) -> int:
        return self._by_position.get(int(record_position), -1)

    def _record_conflict(
        self,
        *,
        trace_index: int,
        stored: tuple[int, str, str],
        incoming_hash: str,
        incoming_uid: str,
        conflicting_uid: bool = False,
    ) -> None:
        evidence = TraceConflict(
            trace_index=trace_index,
            record_position=stored[0],
            stored_hash=stored[1],
            incoming_hash=incoming_hash,
            stored_trace_uid=stored[2],
            incoming_trace_uid=incoming_uid,
            detected_utc=self._clock.utc_now(),
        )
        self._conflicts.append(evidence)
        context: dict[str, JsonValue] = {
            "trace_index": trace_index,
            "record_position": stored[0],
            "stored_hash": stored[1],
            "incoming_hash": incoming_hash,
            "stored_trace_uid": stored[2],
            "incoming_trace_uid": incoming_uid,
            "duplicate_trace_uid": conflicting_uid,
            "conflicting_trace_index": self._index_of_position(stored[0]),
        }
        raise DomainError(
            ErrorCode.ID_CONFLICT,
            "trace index conflicts with an already committed trace",
            context,
        )

    # -- terminal states ---------------------------------------------------

    def close(
        self,
        completion_kind: MissionTerminalState,
        *,
        ended_utc: datetime | None = None,
    ) -> FinalizeResult:
        """Finalize, close the HDF5 handle and atomically rename (idempotent).

        ``completion_kind`` distinguishes completed / user_stopped / failed /
        crash_recovered (AGENTS.md section 4: end states are never collapsed).
        An existing target file is never overwritten.  If the rename fails the
        writer stays in ``awaiting_rename`` so the finalize can be retried
        once the operator fixed the underlying problem.
        """
        if not isinstance(completion_kind, MissionTerminalState):
            raise TypeError(
                "completion_kind must be a MissionTerminalState, "
                f"got {type(completion_kind).__name__}"
            )
        if self._state is WriterState.FINALIZED:
            assert self._finalize_result is not None
            return self._finalize_result
        if self._state is WriterState.ABORTED:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "an aborted writer cannot be finalized",
                {"path": str(self._partial_path)},
            )

        if self._state is WriterState.OPEN:
            self._finalize_file(completion_kind, ended_utc=ended_utc)

        # Re-checked after the handle is closed: the rename must never
        # overwrite an existing final artifact.  This is a best-effort
        # check-then-use guard, not a TOCTOU-proof one: it is sound because
        # the writer is the single owner of this file, while ``os.replace``
        # on Windows overwrites unconditionally (see the facade docstring).
        if self._filesystem.exists(self._final_path):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "finalize target already exists",
                {"path": str(self._final_path)},
            )
        self._fault_hook.on_phase(WritePhase.BEFORE_RENAME)
        self._filesystem.replace(self._partial_path, self._final_path)

        self._state = WriterState.FINALIZED
        committed = self._committed
        ended = self._ended_utc or self._clock.utc_now()
        self._finalize_result = FinalizeResult(
            partial_path=self._partial_path,
            final_path=self._final_path,
            completion_kind=completion_kind,
            committed_record_count=committed,
            ended_utc=ended,
        )
        return self._finalize_result

    def _finalize_file(
        self,
        completion_kind: MissionTerminalState,
        *,
        ended_utc: datetime | None,
    ) -> None:
        if self._filesystem.exists(self._final_path):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "finalize target already exists",
                {"path": str(self._final_path)},
            )
        ended = ensure_utc(ended_utc) if ended_utc is not None else self._clock.utc_now()
        self._ended_utc = ended
        try:
            self._fault_hook.on_phase(WritePhase.BEFORE_FINALIZE)
            self._h5["mission"].attrs["ended_utc"] = to_utc_iso(ended)
            self._h5["mission"].attrs["completion_kind"] = completion_kind.value
            self._h5.attrs["lifecycle_state"] = "finalized"
            self._finalize_marks_written = True
            self._fault_hook.on_phase(WritePhase.AFTER_FINALIZE_MARK)
            self._flush(WritePhase.AFTER_FINALIZE_FLUSH)
        except BaseException:
            self._force_close_handle(
                WriterState.AWAITING_RENAME
                if self._finalize_marks_written
                else WriterState.ABORTED
            )
            raise
        self._h5.close()
        self._state = WriterState.AWAITING_RENAME

    def abort(self) -> None:
        """Close without finalizing; the partial stays for a later recovery.

        Idempotent while aborted or rename-pending; refused once finalized
        (a finalized artifact can never go back to writing).
        """
        if self._state is WriterState.FINALIZED:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "a finalized writer cannot be aborted",
                {"path": str(self._final_path)},
            )
        if self._state is not WriterState.OPEN:
            return
        self._force_close_handle(WriterState.ABORTED)

    def _require_open(self, action: str) -> None:
        if self._state is not WriterState.OPEN:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                f"cannot {action}: the writer is {self._state.value}",
                {"state": self._state.value, "path": str(self._partial_path)},
            )

    def _force_close_handle(self, state: WriterState) -> None:
        """Close the HDF5 handle after a fault (the file itself is untouched)."""
        self._state = state
        try:
            self._h5.flush()
        except Exception:  # a second fault must never mask the first
            pass
        try:
            self._h5.close()
        except Exception:  # ditto
            pass

    # -- context manager ---------------------------------------------------

    def __enter__(self) -> RcScanIncrementalWriter:
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Never finalize implicitly: an open writer is aborted, not renamed."""
        if self._state is WriterState.OPEN:
            self.abort()
