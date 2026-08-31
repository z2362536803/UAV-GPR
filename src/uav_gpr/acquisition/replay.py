"""ISSUE-018: FileReplayBackend — ``.rcscan`` file replay backend.

Replays raw data through the same :class:`~uav_gpr.acquisition.backend.
AcquisitionBackend` interface as real and simulated backends
(docs/ACQUISITION.md sections 1/2):

- ``FileReplayBackend`` opens a strict ISSUE-011 ``RcScanReader`` over an
  ``.rcscan`` v2 file (air or ground) or, when the v2 probe reports an
  unsupported schema version, an ISSUE-013 ``RcScanV1Reader`` over a v1
  file (the only legal detection signal).  ``open()`` fails closed on
  schema violations, on conflicting trace identity, on raw rows whose
  stored hash does not verify and on files with no committed raw traces.
- ``configure()`` treats the file's own mission config as the authoritative
  applied config: the requested digest must equal the file's
  ``config_sha256`` (v2) or the requested channels/frequency axis must
  match the file (v1, which has no mission config); the requested/applied
  ``ConfigDiff`` is recorded honestly.  Paced replay of a v1 file without
  per-trace timestamps is rejected (no time source to pace from).
- ``acquire()`` serves the logical view of the file (explicit
  ``trace_index`` order, duplicates collapsed to their first committed
  copy, conflicting identities excluded by the reader) as immutable
  ``FrequencySweep`` objects.  Pacing follows ``ReplayConfig``: per-trace
  (no waits), original-time ratio (inter-trace gaps from the file's
  monotonic starts — v2 — or UTC timestamps — v1) or an explicit
  acceleration factor.  Paced waits reuse the ISSUE-015 cancellable wait
  primitive, so ``cancel()``/``close()`` interrupt them and ``timeout_s``
  caps them.  Past the last trace ``acquire`` raises ``ReplayEndedError``;
  consumers stop via the ISSUE-017 controller after ``trace_count`` sweeps
  (mission-level auto-stop belongs to ISSUE-043/048).
- Every trace identity/UTC/GNSS/quality field is preserved verbatim from
  the file; missing fields stay missing — no current time, no 0/0
  coordinates are ever fabricated (AGENTS.md sections 3/5).  The file's
  optional processed groups (``frequency/calibrated``, ``time_base``,
  ``time_processed``) are never read or applied: only ``frequency_raw`` is
  served (docs/PROCESSING.md safe-replay rules).

The reader handle is owned by the backend; ``close()`` releases it
idempotently.  No threads are created and no file is ever modified.
"""

from __future__ import annotations

import itertools
import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from uav_gpr.acquisition.backend import (
    AcquisitionBackend,
    AppliedConfig,
    BackendClosedError,
    BackendConfigRejectedError,
    BackendError,
    BackendState,
    Capabilities,
)
from uav_gpr.core import ConfigDiff, DeviceId, FrequencySweep, MissionConfig
from uav_gpr.core.channels import ChannelSpec
from uav_gpr.core.enums import StableStrEnum
from uav_gpr.core.errors import DomainError, ErrorCode
from uav_gpr.storage.rcscan_reader import IssueKind, RcScanReader
from uav_gpr.storage.rcscan_v1 import V1_MIGRATION_NAMESPACE, RcScanV1Reader

__all__ = [
    "FileReplayBackend",
    "ReplayConfig",
    "ReplayCorruptFileError",
    "ReplayEndedError",
    "ReplayError",
    "ReplayMode",
    "ReplayNoRawError",
    "ReplayUnsupportedFileError",
]

_NANOSECONDS_PER_SECOND = 1_000_000_000


class ReplayMode(StableStrEnum):
    """Pacing contract of one replay session."""

    PER_TRACE = "per_trace"
    """Serve every trace as fast as possible (no inter-trace waits)."""

    ORIGINAL_TIME = "original_time"
    """Pace at the file's recorded inter-trace gaps (ratio 1.0)."""

    ACCELERATED = "accelerated"
    """Pace at the file's gaps divided by an explicit acceleration (> 1.0)."""


@dataclass(frozen=True, slots=True)
class ReplayConfig:
    """Pacing configuration of a :class:`FileReplayBackend` session.

    ``acceleration`` is only meaningful for :attr:`ReplayMode.ACCELERATED`
    (must be a finite float > 1.0); the other modes require the default
    1.0 so a silent "no-op acceleration" can never be configured.
    """

    mode: ReplayMode = ReplayMode.PER_TRACE
    acceleration: float = 1.0

    def __post_init__(self) -> None:
        if not isinstance(self.mode, ReplayMode):
            raise TypeError(
                f"mode must be a ReplayMode, got {type(self.mode).__name__}"
            )
        if (
            isinstance(self.acceleration, bool)
            or not isinstance(self.acceleration, float)
            or not math.isfinite(self.acceleration)
        ):
            raise ValueError("acceleration must be a finite float")
        if self.mode is ReplayMode.ACCELERATED and self.acceleration <= 1.0:
            raise ValueError("ACCELERATED replay requires acceleration > 1.0")
        if self.mode is not ReplayMode.ACCELERATED and self.acceleration != 1.0:
            raise ValueError("acceleration applies only to ACCELERATED replay")

    @property
    def ratio(self) -> float:
        """Pacing multiplier over the file's inter-trace gaps (0 = none)."""
        if self.mode is ReplayMode.PER_TRACE:
            return 0.0
        if self.mode is ReplayMode.ORIGINAL_TIME:
            return 1.0
        return self.acceleration


class ReplayError(BackendError):
    """File replay backend failure: ``DomainError`` with a stable reason."""

    _reason = "replay_error"


class ReplayUnsupportedFileError(ReplayError):
    """The path is not a readable ``.rcscan`` v2/v1 file."""

    _reason = "unsupported_file"


class ReplayCorruptFileError(ReplayError):
    """The file is data-corrupt: conflicting identity or unverified raw."""

    _reason = "corrupt_file"


class ReplayNoRawError(ReplayError):
    """The file has no committed raw traces to replay."""

    _reason = "no_raw"


class ReplayEndedError(ReplayError):
    """Every logical trace has been replayed (acquire past the end)."""

    _reason = "replay_ended"


def _gaps_from_starts(starts: Sequence[int]) -> list[float]:
    """Inter-trace gaps in seconds from monotonic start nanoseconds.

    The first gap is always 0.0 (no wait before the first trace); later
    gaps are clamped to ``>= 0`` so a backward file clock can never produce
    a negative (instant-catch-up) wait.
    """
    gaps: list[float] = [0.0]
    for previous, current in itertools.pairwise(starts):
        gaps.append(max(0.0, (current - previous) / _NANOSECONDS_PER_SECOND))
    return gaps


class FileReplayBackend(AcquisitionBackend):
    """Replay one ``.rcscan`` file (v2 or v1) as raw sweeps.

    Lifecycle follows the ISSUE-015 contract: ``open()`` validates and
    indexes the file, ``configure()`` pins the mission contract and pacing
    feasibility, ``acquire()`` serves the logical view in order with
    optional pacing, ``cancel()``/``close()`` interrupt any paced wait.
    The file is opened read-only and never modified.
    """

    def __init__(
        self, path: str | Path, replay: ReplayConfig | None = None
    ) -> None:
        super().__init__()
        self._path = Path(path)
        if replay is not None and not isinstance(replay, ReplayConfig):
            raise TypeError(
                f"replay must be a ReplayConfig or None, got {type(replay).__name__}"
            )
        self._replay = replay if replay is not None else ReplayConfig()
        self._reader: RcScanReader | None = None
        self._v1_reader: RcScanV1Reader | None = None
        self._format: str | None = None
        self._order: list[int] = []
        self._gaps: list[float] = []
        self._channels: tuple[ChannelSpec, ...] = ()
        self._frequencies_hz: np.ndarray = np.empty(0, dtype="<f8")
        self._device_id: DeviceId | None = None
        self._has_gnss = False
        self._has_pacing_source = False
        self._position = 0
        self._attempt = 0

    # -- introspection ------------------------------------------------------

    @property
    def path(self) -> Path:
        return self._path

    @property
    def source_format(self) -> str:
        """``"rcscan_v2"`` or ``"rcscan_v1"`` once opened (``""`` before)."""
        return self._format or ""

    @property
    def trace_count(self) -> int:
        """Number of logical traces this file will replay (0 before open)."""
        return len(self._order)

    @property
    def capabilities(self) -> Capabilities:
        """The opened file's capabilities (device/channels/gnss/faults)."""
        if self._device_id is None:
            raise ReplayError(
                "capabilities require an opened replay backend",
                operation="capabilities",
                state=self.state.value,
            )
        return Capabilities(
            device_id=self._device_id,
            channels=self._channels,
            fault_injection=False,
            gnss=self._has_gnss,
        )

    # -- hooks --------------------------------------------------------------

    def _do_open(self) -> Capabilities:
        try:
            return self._do_open_impl()
        except BaseException:
            # P3-01 hardening: a failed open must not leave the backend in
            # OPEN state without a reader; roll back to CLOSED (idempotent).
            self.close()
            raise

    def _do_open_impl(self) -> Capabilities:
        try:
            reader = RcScanReader(self._path)
        except DomainError as exc:
            if exc.code is ErrorCode.UNSUPPORTED_SCHEMA_VERSION:
                return self._open_v1()
            raise ReplayUnsupportedFileError(
                "not a replayable rcscan file",
                cause_type=type(exc).__name__,
                cause_message=str(exc) or type(exc).__name__,
            ) from exc
        except OSError as exc:
            raise ReplayUnsupportedFileError(
                "cannot open file for replay",
                cause_type="OSError",
                cause_message=str(exc) or "OSError",
            ) from exc
        try:
            report = reader.validation_report()
            if report.conflicts:
                raise ReplayCorruptFileError(
                    "replay file has conflicting trace identity "
                    "(never silently resolved)",
                    conflict_count=len(report.conflicts),
                )
            hash_issues = [
                issue
                for issue in report.issues
                if issue.kind in (IssueKind.HASH_MISMATCH, IssueKind.MISSING_HASH)
            ]
            if hash_issues:
                raise ReplayCorruptFileError(
                    "replay file has raw traces whose stored hash does not verify",
                    issue_count=len(hash_issues),
                )
            if report.committed_record_count == 0:
                raise ReplayNoRawError(
                    "replay file has no committed raw traces",
                    committed_record_count=0,
                )
            order: list[int] = []
            starts: list[int] = []
            has_gnss = False
            for chunk in reader.iter_logical(chunk_rows=64):
                for record in chunk.records:
                    order.append(record.trace_index)
                    starts.append(record.metadata.sweep_started_monotonic_ns.ns)
                    has_gnss = has_gnss or record.metadata.gnss_match is not None
        except BaseException:
            reader.close()
            raise
        self._reader = reader
        self._format = "rcscan_v2"
        self._order = order
        self._gaps = _gaps_from_starts(starts)
        self._channels = reader.channels
        self._frequencies_hz = np.asarray(reader.frequencies_hz, dtype="<f8")
        self._device_id = reader.device_id
        self._has_gnss = has_gnss
        self._has_pacing_source = True
        return Capabilities(
            device_id=reader.device_id,
            channels=reader.channels,
            fault_injection=False,
            gnss=has_gnss,
        )

    def _open_v1(self) -> Capabilities:
        try:
            reader = RcScanV1Reader(self._path)
        except DomainError as exc:
            raise ReplayUnsupportedFileError(
                "not a replayable rcscan file (v1 adapter failed)",
                cause_type=type(exc).__name__,
                cause_message=str(exc) or type(exc).__name__,
            ) from exc
        data = reader.data
        timestamps = data.trace_timestamps_utc
        n_traces = int(data.frequency.data.shape[0])
        if timestamps is None:
            gaps = [0.0] * n_traces
            has_pacing_source = False
        else:
            gaps = [0.0]
            for left, right in itertools.pairwise(timestamps):
                gaps.append(max(0.0, (right - left).total_seconds()))
            has_pacing_source = True
        device_id = DeviceId(
            str(uuid.uuid5(V1_MIGRATION_NAMESPACE, f"device:{data.source_sha256}"))
        )
        self._v1_reader = reader
        self._format = "rcscan_v1"
        self._order = list(range(n_traces))
        self._gaps = gaps
        self._channels = data.channels
        self._frequencies_hz = np.asarray(data.frequencies_hz, dtype="<f8")
        self._device_id = device_id
        self._has_gnss = False
        self._has_pacing_source = has_pacing_source
        return Capabilities(
            device_id=device_id,
            channels=data.channels,
            fault_injection=False,
            gnss=False,
        )

    def _do_configure(self, config: MissionConfig) -> AppliedConfig:
        if self._format == "rcscan_v2":
            reader = self._reader
            assert reader is not None
            file_config = reader.config
            if config.config_sha256 != file_config.config_sha256:
                raise BackendConfigRejectedError(
                    "replay config digest does not match the file mission config",
                    requested_sha256=config.config_sha256,
                    file_sha256=file_config.config_sha256,
                )
            applied = AppliedConfig(
                config=file_config,
                diff=ConfigDiff.compute(config, file_config),
            )
        elif self._format == "rcscan_v1":
            # The v1 adapter maps channels with its own descriptive fields
            # (display_name/antenna_note); the data contract is the ordered
            # channel identity, so compare channel ids.
            if tuple(c.channel_id for c in config.channels) != tuple(
                c.channel_id for c in self._channels
            ):
                raise BackendConfigRejectedError(
                    "replay config channels do not match the v1 file",
                    requested=[c.channel_id for c in config.channels],
                    file=[c.channel_id for c in self._channels],
                )
            if not np.array_equal(config.frequency_axis_hz, self._frequencies_hz):
                raise BackendConfigRejectedError(
                    "replay config frequency axis does not match the v1 file",
                )
            if (
                not self._has_pacing_source
                and self._replay.mode is not ReplayMode.PER_TRACE
            ):
                raise BackendConfigRejectedError(
                    "paced replay requires per-trace time records; "
                    "this v1 file has none",
                    mode=self._replay.mode.value,
                )
            applied = AppliedConfig(
                config=config,
                diff=ConfigDiff.compute(config, config),
            )
        else:
            raise ReplayError(
                "replay backend is not open",
                operation="configure",
                format=self._format or "none",
            )
        self._position = 0
        self._attempt = 0
        return applied

    def _do_acquire(self, timeout_s: float | None) -> FrequencySweep:
        position = self._position
        if position >= len(self._order):
            raise ReplayEndedError(
                "replay exhausted: no more traces in the file",
                replayed=position,
                trace_count=len(self._order),
            )
        wait_seconds = self._gaps[position] * self._replay.ratio
        if wait_seconds > 0.0:
            self._wait_cancellable(
                seconds=wait_seconds,
                attempt=self._attempt,
                timeout_s=timeout_s,
            )
        # P3-03 hardening: after the cancellable wait returns (normally or
        # woken), a close in the intervening window must surface as a
        # structured BackendClosedError instead of a raw h5py error from
        # the closed reader.
        with self._lock:
            if self._state is BackendState.CLOSED:
                raise BackendClosedError(
                    "backend closed during replay wait",
                    attempt=self._attempt,
                )
        self._attempt += 1
        self._position = position + 1
        if self._format == "rcscan_v2":
            reader = self._reader
            assert reader is not None
            record = reader.trace_by_index(self._order[position])
            return FrequencySweep(
                channels=reader.channels,
                frequencies_hz=reader.frequencies_hz,
                data=record.frequency_raw,
                metadata=record.metadata,
            )
        v1_reader = self._v1_reader
        assert v1_reader is not None
        return FrequencySweep(
            channels=self._channels,
            frequencies_hz=self._frequencies_hz,
            data=v1_reader.raw_row(position),
            metadata=None,
        )

    def _do_close(self) -> None:
        if self._reader is not None:
            self._reader.close()
            self._reader = None
        if self._v1_reader is not None:
            self._v1_reader.close()
            self._v1_reader = None
