"""ISSUE-012 integration tests: read-only partial inspection and non-destructive recovery.

This suite pins the ISSUE-012 recovery API on top of the ISSUE-010 writer
(fault-injected crash partials), the ISSUE-011 strict reader (validation and
recovered-file verification) and the ISSUE-008 frozen schema:

- ``inspect_partial``: deterministic, serializable read-only report of a
  crashed ``*.partial.rcscan`` (schema/checkpoint/column lengths/tail state/
  hash and ID classification/source SHA-256);
- ``plan_recovery``: default dry-run — never writes, decides target path and
  new file id, blocks non-writing/non-partial sources and target collisions;
- ``execute_recovery``: explicit, confirmed execution that copies the last
  complete committed rows **as raw physical cells** into a new recovered
  ``.rcscan`` (new file id, ``completion_kind=recovered``, source SHA-256
  provenance), verifies the result with the strict reader, and cleans up on
  any mid-recovery failure so no pseudo-finalized file survives.

Everything is synthetic; no ``sleep``, no hardware, no reference projects.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import h5py
import numpy as np
import pytest

from uav_gpr.core.channels import ChannelSpec
from uav_gpr.core.config import MissionConfig
from uav_gpr.core.enums import (
    AcquisitionMode,
    EndpointRole,
    GnssFixQuality,
    GnssMatchMethod,
    GnssNoFixPolicy,
    LogicalPolarization,
    MissionTerminalState,
    SParameter,
    TraceQualityReason,
    TraceQualityStatus,
)
from uav_gpr.core.errors import DomainError, ErrorCode
from uav_gpr.core.gnss import GnssFix, GnssMatch
from uav_gpr.core.identifiers import (
    AirFileId,
    DeviceId,
    GroundFileId,
    MissionId,
    TraceUid,
)
from uav_gpr.core.metadata import TraceMetadata
from uav_gpr.core.raw_hash import compute_raw_trace_sha256
from uav_gpr.core.timeutil import ManualClock, MonotonicNs, to_utc_iso
from uav_gpr.storage import rcscan_v2 as schema
from uav_gpr.storage.incremental_writer import (
    PARTIAL_SUFFIX,
    InjectedStorageFault,
    LocalFileSystemFacade,
    PhaseFaultHook,
    RcScanIncrementalWriter,
    TraceAppendRequest,
    WritePhase,
)
from uav_gpr.storage.partial_recovery import (
    RECOVERY_COMPONENT_VERSION,
    InjectedRecoveryFault,
    RecoveryFaultHook,
    RecoveryPhase,
    RecoveryResult,
    execute_recovery,
    inspect_partial,
    plan_recovery,
)
from uav_gpr.storage.rcscan_reader import (
    MissingTrace,
    RcScanReader,
    RcScanValidator,
    ReadTrace,
)

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Frozen synthetic test contract (mirrors the ISSUE-010/011 test constants)
# ---------------------------------------------------------------------------

_FREQUENCY_POINTS = 16
_MISSION_ID = MissionId("0f0e8a3b-6f2d-4c1e-9a7b-112233445566")
_DEVICE_ID = DeviceId("d1c0ffee-0000-4000-8000-000000000001")
_AIR_FILE_ID = AirFileId("aaaaaaa1-0000-4000-8000-000000000002")
_GROUND_FILE_ID = GroundFileId("aaaaaaa2-0000-4000-8000-000000000002")
_CREATED_UTC = datetime(2026, 8, 28, 9, 0, 0, tzinfo=UTC)
_WRITER_VERSION = "uav-gpr.test.1"


def build_channels() -> tuple[ChannelSpec, ChannelSpec]:
    return (
        ChannelSpec(
            channel_id="hh_s11",
            logical_polarization=LogicalPolarization.HH,
            s_parameter=SParameter.S11,
            display_name="H height S11",
        ),
        ChannelSpec(
            channel_id="vv_s22",
            logical_polarization=LogicalPolarization.VV,
            s_parameter=SParameter.S22,
            display_name="V vertical port",
            antenna_note="port B",
        ),
    )


def build_mission_config(channels: tuple[ChannelSpec, ...]) -> MissionConfig:
    return MissionConfig(
        frequency_start_hz=800e6,
        frequency_stop_hz=2600e6,
        frequency_points=_FREQUENCY_POINTS,
        if_bw_hz=1_000.0,
        power_dbm=-3.0,
        channels=channels,
        acquisition_mode=AcquisitionMode.FIXED_COUNT,
        planned_trace_count=8,
        target_interval_s=0.1,
        gnss_max_age_s=2.0,
        gnss_no_fix_policy=GnssNoFixPolicy.RECORD_WITHOUT_POSITION,
        calibration_profile_id=None,
        apply_calibration=False,
        background_reference_id=None,
        apply_background=False,
        created_utc=_CREATED_UTC,
        software_version="0.1.0.dev0",
    )


@pytest.fixture()
def channels() -> tuple[ChannelSpec, ChannelSpec]:
    return build_channels()


@pytest.fixture()
def mission_config(channels: tuple[ChannelSpec, ...]) -> MissionConfig:
    return build_mission_config(channels)


@pytest.fixture()
def frequencies(mission_config: MissionConfig) -> np.ndarray:
    return np.asarray(mission_config.frequency_axis_hz, dtype="<f8")


@pytest.fixture()
def clock() -> ManualClock:
    return ManualClock(_CREATED_UTC + timedelta(hours=2), monotonic_ns=0)


def _trace_uid(index: int) -> TraceUid:
    """Deterministic canonical UUID per logical trace index."""
    return TraceUid(uuid.UUID(int=index + 1))


def make_raw(index: int, *, channels: int, frequencies: int, salt: float = 0.0) -> np.ndarray:
    """Deterministic complex raw sweep ``channel x frequency``."""
    rows = np.arange(channels, dtype=np.float64)[:, None]
    cols = np.arange(frequencies, dtype=np.float64)[None, :]
    real = np.cos((rows * 0.5) + (cols * 0.25) + index + salt)
    imag = np.sin((rows * 0.25) + (cols * 0.5) + index + salt)
    return np.ascontiguousarray(real + 1j * imag, dtype="<c16")


def make_gnss_match(midpoint: datetime, monotonic_ns: int) -> GnssMatch:
    fix = GnssFix(
        received_utc=midpoint,
        nmea_utc=midpoint,
        received_monotonic_ns=MonotonicNs(monotonic_ns),
        latitude_deg=30.123456,
        longitude_deg=120.654321,
        altitude_msl_m=42.5,
        geoid_separation_m=12.25,
        fix_quality=GnssFixQuality.RTK_FLOAT,
        satellites=14,
        hdop=0.8,
        ground_speed_mps=3.25,
        course_deg=181.5,
        valid=True,
        invalid_reason=None,
    )
    return GnssMatch(
        fix=fix,
        trace_midpoint_utc=midpoint,
        age_s=0.12,
        method=GnssMatchMethod.NEAREST_MIDPOINT,
        usable_for_map=True,
        reason=None,
    )


def make_metadata(index: int, *, with_gnss: bool = True) -> TraceMetadata:
    """Acquired (hash-less) metadata for one logical trace index."""
    base = _CREATED_UTC + timedelta(seconds=1 + index)
    started = base
    midpoint = base + timedelta(milliseconds=50)
    finished = base + timedelta(milliseconds=100)
    monotonic = 1_000_000_000 * (index + 1)
    actual = None if index == 0 else 0.1
    schedule = None if index == 0 else 0.001
    match = (
        make_gnss_match(midpoint, monotonic + 50_000_000) if with_gnss else None
    )
    if match is None:
        status = TraceQualityStatus.DEGRADED
        reasons: tuple[TraceQualityReason, ...] = (TraceQualityReason.GNSS_MISSING,)
    else:
        status = TraceQualityStatus.NOMINAL
        reasons = ()
    return TraceMetadata(
        mission_id=_MISSION_ID,
        trace_index=index,
        trace_uid=_trace_uid(index),
        device_id=_DEVICE_ID,
        sweep_started_utc=started,
        sweep_midpoint_utc=midpoint,
        sweep_finished_utc=finished,
        sweep_started_monotonic_ns=MonotonicNs(monotonic),
        sweep_midpoint_monotonic_ns=MonotonicNs(monotonic + 50_000_000),
        sweep_finished_monotonic_ns=MonotonicNs(monotonic + 100_000_000),
        target_interval_s=0.1,
        actual_interval_s=actual,
        schedule_error_s=schedule,
        connection_generation=1,
        raw_trace_sha256=None,
        gnss_match=match,
        quality_status=status,
        quality_reasons=reasons,
    )


def make_request(
    index: int,
    *,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    salt: float = 0.0,
) -> TraceAppendRequest:
    return TraceAppendRequest(
        metadata=make_metadata(index),
        frequency_raw=make_raw(
            index, channels=len(channels), frequencies=int(frequencies.size), salt=salt
        ),
        channels=channels,
        frequencies_hz=frequencies,
    )


def create_writer(
    scratch_dir: Path,
    *,
    role: EndpointRole,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
    clock: ManualClock,
    fault_hook: Any = None,
    filesystem: Any = None,
    hdf5_opener: Callable[[Path], Any] | None = None,
) -> RcScanIncrementalWriter:
    return RcScanIncrementalWriter.create(
        scratch_dir,
        mission_id=_MISSION_ID,
        device_id=_DEVICE_ID,
        file_id=_AIR_FILE_ID if role is EndpointRole.AIR else _GROUND_FILE_ID,
        role=role,
        config=mission_config,
        channels=channels,
        frequencies_hz=frequencies,
        created_utc=_CREATED_UTC,
        writer_version=_WRITER_VERSION,
        clock=clock,
        fault_hook=fault_hook,
        filesystem=filesystem,
        hdf5_opener=hdf5_opener,
    )


def partial_path(scratch_dir: Path, role: EndpointRole = EndpointRole.AIR) -> Path:
    file_id = _AIR_FILE_ID if role is EndpointRole.AIR else _GROUND_FILE_ID
    return scratch_dir / f"{file_id}{PARTIAL_SUFFIX}"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


#: Fault phases that fire inside ``close()`` (finalize/rename) instead of
#: ``append_trace``; the faulting index is never written.
_CLOSE_FAULT_PHASES = frozenset(
    {
        WritePhase.BEFORE_FINALIZE,
        WritePhase.AFTER_FINALIZE_MARK,
        WritePhase.AFTER_FINALIZE_FLUSH,
        WritePhase.BEFORE_RENAME,
    }
)


def write_crashed_partial(
    scratch_dir: Path,
    *,
    committed_indices: Sequence[int],
    fault_phase: WritePhase,
    fault_index: int,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
    clock: ManualClock,
) -> Path:
    """Write ``committed_indices`` then crash the writer at ``fault_phase``
    while appending ``fault_index``; return the aborted partial path."""
    scratch_dir.mkdir(parents=True, exist_ok=True)
    hook = PhaseFaultHook()
    writer = create_writer(
        scratch_dir,
        role=EndpointRole.AIR,
        channels=channels,
        frequencies=frequencies,
        mission_config=mission_config,
        clock=clock,
        fault_hook=hook,
    )
    try:
        for index in committed_indices:
            writer.append_trace(
                make_request(index, channels=channels, frequencies=frequencies)
            )
        hook.arm(fault_phase)
        if fault_phase in _CLOSE_FAULT_PHASES:
            with pytest.raises(InjectedStorageFault):
                writer.close(MissionTerminalState.COMPLETED)
        else:
            with pytest.raises(InjectedStorageFault):
                writer.append_trace(
                    make_request(fault_index, channels=channels, frequencies=frequencies)
                )
    finally:
        writer.abort()
    return partial_path(scratch_dir)


class _FlushSpy:
    """HDF5 handle wrapper: records real ``flush()`` calls; can fail one with
    a real ``OSError(ENOSPC)`` (mirrors the ISSUE-010 test seam)."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls = 0
        self._fail_next = False

    def fail_next_flush(self) -> None:
        """Make the very next ``flush()`` raise a real ENOSPC OSError."""
        self._fail_next = True

    def flush(self) -> None:
        self.calls += 1
        if self._fail_next:
            self._fail_next = False
            raise OSError(28, "No space left on device")
        self._inner.flush()

    def __getitem__(self, key: str) -> Any:
        return self._inner[key]

    def __contains__(self, key: object) -> bool:
        return key in self._inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def close(self) -> None:
        self._inner.close()


class _EnospcHook:
    """Fault hook that fails the next real flush at ``phase`` (ENOSPC)."""

    def __init__(self, phase: WritePhase, spies: list[_FlushSpy]) -> None:
        self._phase = phase
        self._spies = spies
        self._armed = False

    def arm(self) -> None:
        self._armed = True

    def on_phase(self, phase: WritePhase) -> None:
        if self._armed and phase is self._phase:
            self._spies[0].fail_next_flush()


def _spying_opener(spies: list[_FlushSpy]) -> Callable[[Path], Any]:
    def opener(path: Path) -> Any:
        spy = _FlushSpy(h5py.File(path, "r+"))
        spies.append(spy)
        return spy

    return opener


class _FlakyRenameFacade(LocalFileSystemFacade):
    """Deterministic rename failure: ``replace`` raises ENOSPC ``fail_times``
    times (the ISSUE-010 "lying/failing facade" fixture)."""

    def __init__(self, fail_times: int = 1) -> None:
        self.attempts = 0
        self._fail_times = fail_times

    def replace(self, source: Path, target: Path) -> None:
        self.attempts += 1
        if self.attempts <= self._fail_times:
            raise OSError(28, "No space left on device")
        super().replace(source, target)


@dataclass(frozen=True)
class BulkRow:
    """One logical row for the fast bulk builder (deterministic synthetic)."""

    trace_index: int
    salt: float = 0.0
    with_gnss: bool = True
    attach_hash: bool = True


def bulk_write_partial(
    scratch_dir: Path,
    rows: Sequence[BulkRow],
    *,
    role: EndpointRole = EndpointRole.AIR,
    checkpoint_override: int | None = None,
) -> Path:
    """Build a ``writing`` ``*.partial.rcscan`` fast (whole-column writes).

    Uses the same frozen schema creator and the same authoritative row codec
    (``trace_metadata_to_cells``) as the ISSUE-010 writer, so the produced
    bytes are contract-identical; the difference is only write throughput.
    """
    channels = build_channels()
    config = build_mission_config(channels)
    axis = np.asarray(config.frequency_axis_hz, dtype="<f8")
    file_id = _AIR_FILE_ID if role is EndpointRole.AIR else _GROUND_FILE_ID
    partial = scratch_dir / f"{file_id}{PARTIAL_SUFFIX}"
    partial.parent.mkdir(parents=True, exist_ok=True)
    schema.create_rcscan_v2(
        partial,
        mission_id=_MISSION_ID,
        device_id=_DEVICE_ID,
        file_id=file_id,
        created_utc=_CREATED_UTC,
        completed_utc=None,
        completion_kind=None,
        file_role=role,
        channels=channels,
        frequencies_hz=axis,
        config_json=config.to_canonical_json(),
        config_sha256=config.config_sha256,
        writer_version=_WRITER_VERSION,
    )
    with h5py.File(partial, "r+") as h5:
        column_values: dict[str, list[object]] = {}
        raw_rows: list[np.ndarray] = []
        for row in rows:
            metadata = make_metadata(row.trace_index, with_gnss=row.with_gnss)
            raw = make_raw(
                row.trace_index,
                channels=len(channels),
                frequencies=int(axis.size),
                salt=row.salt,
            )
            cells = schema.trace_metadata_to_cells(metadata)
            if row.attach_hash:
                digest = compute_raw_trace_sha256(
                    mission_id=_MISSION_ID,
                    trace_index=row.trace_index,
                    trace_uid=metadata.trace_uid,
                    channels=channels,
                    frequencies_hz=axis,
                    data=raw,
                )
                cells = schema.trace_metadata_to_cells(metadata.with_integrity(digest))
            else:
                cells["/trace_metadata/raw_trace_sha256"] = ""
            for path, value in cells.items():
                column_values.setdefault(path, []).append(value)
            raw_rows.append(raw)
        count = len(rows)
        contracts = schema.dataset_contracts(len(channels), int(axis.size))
        row_paths = [
            contract.path
            for contract in contracts
            if not contract.optional
            and contract.path.startswith(
                ("/trace_metadata/", "/gnss/", "/acquisition/", "/transport/")
            )
            and (not contract.path.startswith("/transport") or role is EndpointRole.AIR)
        ]
        for path in row_paths:
            dataset = h5[path]
            values = column_values[path]
            array = np.array(values, dtype=dataset.dtype)
            dataset.resize((count,))
            dataset[:] = array
        raw_dataset = h5["/frequency/raw"]
        raw_dataset.resize((count, len(channels), int(axis.size)))
        raw_dataset[:] = np.stack(raw_rows)
        committed = count if checkpoint_override is None else checkpoint_override
        h5["/checkpoints/committed_record_count"][0] = np.int64(committed)
        last_index = (
            max((row.trace_index for row in rows), default=schema.MISSING_INT64)
            if checkpoint_override is None
            else schema.MISSING_INT64
        )
        h5["/checkpoints/last_trace_index"][0] = np.int64(last_index)
        h5["/checkpoints/updated_utc"][0] = to_utc_iso(
            _CREATED_UTC + timedelta(seconds=count)
        )
        h5["mission"].attrs["started_utc"] = to_utc_iso(_CREATED_UTC)
    return partial


def corrupt_attr(path: Path, attr: str, value: object) -> None:
    with h5py.File(path, "r+") as h5:
        h5.attrs[attr] = value


def add_optional_time_group(path: Path, *, channels: int, frequency_points: int) -> None:
    """Add contract-valid optional processed datasets to a partial (test-only)."""
    with h5py.File(path, "r+") as h5:
        h5.create_dataset(
            "/axes/time_base_s", data=np.zeros(frequency_points, dtype="<f8")
        )
        h5.create_dataset(
            "/time_base/data",
            shape=(0, channels, frequency_points),
            maxshape=(None, channels, frequency_points),
            dtype="<c16",
            chunks=(1, channels, frequency_points),
        )
        h5.create_dataset(
            "/time_base/history_json",
            data=np.array(["[]"], dtype=h5py.string_dtype(encoding="utf-8")),
        )


def collect_all(reader: RcScanReader, *, logical: bool) -> tuple[ReadTrace, ...]:
    chunks = reader.iter_logical() if logical else reader.iter_physical()
    return tuple(record for chunk in chunks for record in chunk.records)


def trace_key(record: ReadTrace) -> tuple[int, int, str, str, bool]:
    return (
        record.record_position,
        record.trace_index,
        record.trace_uid,
        record.raw_trace_sha256,
        record.hash_verified,
    )


# ---------------------------------------------------------------------------
# Inspect: deterministic, read-only, tail-aware reports
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fault_phase,expected_committed,expected_tail,expected_lifecycle",
    [
        # 10-phase crash matrix of the ISSUE-010 writer.  Semantics are pinned
        # by the ISSUE-010/011 suites in this environment: checkpoint writes
        # are visible to readers immediately, so AFTER_CHECKPOINT_WRITE and
        # AFTER_COMMIT_FLUSH leave the faulting row committed; the finalize
        # phases leave a *finalized* partial (awaiting_rename classification).
        (WritePhase.BEFORE_RAW_WRITE, 2, 0, "writing"),
        (WritePhase.AFTER_RAW_WRITE, 2, 1, "writing"),
        (WritePhase.AFTER_TRACE_COLUMNS, 2, 1, "writing"),
        (WritePhase.AFTER_DATA_FLUSH, 2, 1, "writing"),
        (WritePhase.AFTER_CHECKPOINT_WRITE, 3, 0, "writing"),
        (WritePhase.AFTER_COMMIT_FLUSH, 3, 0, "writing"),
        (WritePhase.BEFORE_FINALIZE, 2, 0, "writing"),
        (WritePhase.AFTER_FINALIZE_MARK, 2, 0, "finalized"),
        (WritePhase.AFTER_FINALIZE_FLUSH, 2, 0, "finalized"),
        (WritePhase.BEFORE_RENAME, 2, 0, "finalized"),
    ],
)
def test_inspect_report_is_deterministic_across_writer_faults(
    scratch_dir: Path,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
    clock: ManualClock,
    fault_phase: WritePhase,
    expected_committed: int,
    expected_tail: int,
    expected_lifecycle: str,
) -> None:
    """Any crashed partial yields a stable, correct read-only inspect report."""
    partial = write_crashed_partial(
        scratch_dir,
        committed_indices=(0, 1),
        fault_phase=fault_phase,
        fault_index=2,
        channels=channels,
        frequencies=frequencies,
        mission_config=mission_config,
        clock=clock,
    )
    before = file_sha256(partial)

    first = inspect_partial(partial)
    second = inspect_partial(partial)

    assert first.to_dict() == second.to_dict()  # deterministic
    assert first.source_sha256 == before == second.source_sha256
    assert first.lifecycle_state == expected_lifecycle
    assert first.file_id == str(_AIR_FILE_ID)
    assert first.mission_id == _MISSION_ID.to_json()
    assert first.committed_record_count == expected_committed
    assert first.tail_rows == expected_tail
    assert first.physical_record_count == min(first.column_lengths.values())
    assert first.tail_rows == max(first.column_lengths.values()) - expected_committed
    assert first.last_trace_index == (expected_committed - 1)
    assert first.column_lengths["/frequency/raw"] == (
        expected_committed + expected_tail
    )
    assert first.optional_groups_present == ()
    assert first.validation.committed_record_count == expected_committed
    assert first.validation.issues == ()
    assert file_sha256(partial) == before  # inspection never mutates the source
    # JSON-safe serialization round trip
    payload = json.dumps(first.to_dict(), sort_keys=True)
    assert json.loads(payload)["committed_record_count"] == expected_committed


def test_inspect_on_empty_partial_reports_zero_committed(
    scratch_dir: Path,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
    clock: ManualClock,
) -> None:
    writer = create_writer(
        scratch_dir,
        role=EndpointRole.AIR,
        channels=channels,
        frequencies=frequencies,
        mission_config=mission_config,
        clock=clock,
    )
    writer.abort()
    partial = partial_path(scratch_dir)

    report = inspect_partial(partial)
    assert report.committed_record_count == 0
    assert report.tail_rows == 0
    assert report.physical_record_count == 0
    assert report.last_trace_index == schema.MISSING_INT64
    assert report.validation.missing == ()


def test_inspect_is_stable_for_real_enospc_flush_fixtures(
    scratch_dir: Path,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
    clock: ManualClock,
) -> None:
    """Real ``OSError(ENOSPC)`` at a real HDF5 flush leaves a partial whose
    inspect report is stable and correct (ISSUE-010 pinned semantics)."""
    # ENOSPC at the data flush: the whole commit is lost, the tail stays.
    (scratch_dir / "enospc_data").mkdir()
    spies: list[_FlushSpy] = []
    hook = _EnospcHook(WritePhase.AFTER_TRACE_COLUMNS, spies)
    writer = create_writer(
        scratch_dir / "enospc_data",
        role=EndpointRole.AIR,
        channels=channels,
        frequencies=frequencies,
        mission_config=mission_config,
        clock=clock,
        fault_hook=hook,
        hdf5_opener=_spying_opener(spies),
    )
    writer.append_trace(make_request(0, channels=channels, frequencies=frequencies))
    hook.arm()
    with pytest.raises(OSError) as caught:
        writer.append_trace(make_request(1, channels=channels, frequencies=frequencies))
    assert caught.value.errno == 28
    writer.abort()
    partial = scratch_dir / "enospc_data" / f"{_AIR_FILE_ID}{PARTIAL_SUFFIX}"
    report = inspect_partial(partial)
    assert report.committed_record_count == 1
    assert report.tail_rows == 1
    assert report.column_lengths["/frequency/raw"] == 2
    assert report.to_dict() == inspect_partial(partial).to_dict()

    # ENOSPC at the commit flush: the row is durable and committed in the file.
    (scratch_dir / "enospc_commit").mkdir()
    spies2: list[_FlushSpy] = []
    hook2 = _EnospcHook(WritePhase.AFTER_CHECKPOINT_WRITE, spies2)
    writer2 = create_writer(
        scratch_dir / "enospc_commit",
        role=EndpointRole.AIR,
        channels=channels,
        frequencies=frequencies,
        mission_config=mission_config,
        clock=clock,
        fault_hook=hook2,
        hdf5_opener=_spying_opener(spies2),
    )
    writer2.append_trace(make_request(0, channels=channels, frequencies=frequencies))
    hook2.arm()
    with pytest.raises(OSError):
        writer2.append_trace(make_request(1, channels=channels, frequencies=frequencies))
    writer2.abort()
    partial2 = scratch_dir / "enospc_commit" / f"{_AIR_FILE_ID}{PARTIAL_SUFFIX}"
    report2 = inspect_partial(partial2)
    assert report2.committed_record_count == 2
    assert report2.tail_rows == 0
    assert report2.validation.issues == ()
    assert report2.to_dict() == inspect_partial(partial2).to_dict()


def test_awaiting_rename_partial_is_classified_as_completed_not_unfinished(
    scratch_dir: Path,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
    clock: ManualClock,
) -> None:
    """A finalized partial whose rename failed (lying facade) is classified
    with the ISSUE-011 ``rename_pending`` semantics: a completed task, never
    an ordinary unfinished one — and therefore not recoverable."""
    filesystem = _FlakyRenameFacade(fail_times=1)
    (scratch_dir / "rename").mkdir()
    writer = create_writer(
        scratch_dir / "rename",
        role=EndpointRole.AIR,
        channels=channels,
        frequencies=frequencies,
        mission_config=mission_config,
        clock=clock,
        filesystem=filesystem,
    )
    for index in range(3):
        writer.append_trace(make_request(index, channels=channels, frequencies=frequencies))
    with pytest.raises(OSError) as caught:
        writer.close(MissionTerminalState.COMPLETED)
    assert caught.value.errno == 28
    assert filesystem.attempts == 1

    partial = scratch_dir / "rename" / f"{_AIR_FILE_ID}{PARTIAL_SUFFIX}"
    assert partial.exists()
    assert not (scratch_dir / "rename" / f"{_MISSION_ID}.rcscan").exists()
    source_sha = file_sha256(partial)

    with RcScanReader(partial) as reader:
        assert reader.rename_pending is True
        assert reader.lifecycle_state == "finalized"
        assert reader.completion_kind == "completed"
        assert reader.committed_record_count == 3

    report = inspect_partial(partial)
    assert report.lifecycle_state == "finalized"
    assert report.completion_kind == "completed"
    assert report.committed_record_count == 3
    assert report.tail_rows == 0

    plan = plan_recovery(partial, clock=clock)
    assert plan.recoverable is False
    assert any("lifecycle_state" in reason for reason in plan.blocked_reasons)
    with pytest.raises(DomainError):
        execute_recovery(plan)
    assert file_sha256(partial) == source_sha  # never touched by plan/execute


def test_inspect_fails_closed_on_unknown_version_and_bad_checkpoint(
    scratch_dir: Path,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
    clock: ManualClock,
) -> None:
    partial = write_crashed_partial(
        scratch_dir,
        committed_indices=(0, 1),
        fault_phase=WritePhase.AFTER_TRACE_COLUMNS,
        fault_index=2,
        channels=channels,
        frequencies=frequencies,
        mission_config=mission_config,
        clock=clock,
    )
    corrupt_attr(partial, "schema_version", 3)
    with pytest.raises(DomainError) as caught:
        inspect_partial(partial)
    assert caught.value.code is ErrorCode.UNSUPPORTED_SCHEMA_VERSION

    bad = bulk_write_partial(scratch_dir / "bad", [BulkRow(0), BulkRow(1)], checkpoint_override=5)
    with pytest.raises(DomainError):
        inspect_partial(bad)

    garbage = scratch_dir / "not-a-partial.rcscan"
    garbage.write_bytes(b"this is not an HDF5 file")
    with pytest.raises(DomainError):
        inspect_partial(garbage)


# ---------------------------------------------------------------------------
# Plan: default dry-run, recoverability gating, target decision
# ---------------------------------------------------------------------------


def test_plan_is_dry_run_and_never_writes(
    scratch_dir: Path,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
    clock: ManualClock,
) -> None:
    partial = write_crashed_partial(
        scratch_dir,
        committed_indices=(0, 1),
        fault_phase=WritePhase.AFTER_TRACE_COLUMNS,
        fault_index=2,
        channels=channels,
        frequencies=frequencies,
        mission_config=mission_config,
        clock=clock,
    )
    before = file_sha256(partial)
    before_entries = sorted(entry.name for entry in scratch_dir.iterdir())
    new_file_id = AirFileId("bbbbbbb1-0000-4000-8000-0000000000aa")

    plan = plan_recovery(partial, new_file_id=new_file_id, clock=clock)
    again = plan_recovery(partial, new_file_id=new_file_id, clock=clock)

    assert plan.to_dict() == again.to_dict()  # deterministic
    assert plan.recoverable is True
    assert plan.blocked_reasons == ()
    assert plan.new_file_id == str(new_file_id)
    assert plan.target_path == str(scratch_dir / f"{new_file_id}.rcscan")
    assert plan.source_sha256 == before
    assert plan.committed_record_count == 2
    assert plan.tail_rows == 1
    assert plan.tool_version == RECOVERY_COMPONENT_VERSION
    # dry-run: no file appeared, source untouched
    assert sorted(entry.name for entry in scratch_dir.iterdir()) == before_entries
    assert file_sha256(partial) == before
    # serializable
    json.dumps(plan.to_dict(), sort_keys=True)


def test_plan_blocks_non_writing_and_non_partial_sources_without_writing(
    scratch_dir: Path,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
    clock: ManualClock,
) -> None:
    # finalized .rcscan (completed mission, no recovery needed)
    (scratch_dir / "final").mkdir()
    with create_writer(
        scratch_dir / "final",
        role=EndpointRole.AIR,
        channels=channels,
        frequencies=frequencies,
        mission_config=mission_config,
        clock=clock,
    ) as writer:
        writer.append_trace(make_request(0, channels=channels, frequencies=frequencies))
        writer.close(MissionTerminalState.COMPLETED)
    final = scratch_dir / "final" / f"{_MISSION_ID}.rcscan"
    plan = plan_recovery(final, clock=clock)
    assert plan.recoverable is False
    assert any("lifecycle_state" in reason for reason in plan.blocked_reasons)
    with pytest.raises(DomainError):
        execute_recovery(plan)
    assert final.exists()

    # finalized content still named *.partial.rcscan (rename pending)
    rename_pending = scratch_dir / "pending" / f"{_AIR_FILE_ID}{PARTIAL_SUFFIX}"
    rename_pending.parent.mkdir()
    bulk_write_partial(rename_pending.parent, [BulkRow(0)])
    with h5py.File(rename_pending, "r+") as h5:
        h5["mission"].attrs["ended_utc"] = to_utc_iso(_CREATED_UTC)
        h5["mission"].attrs["completion_kind"] = "completed"
        h5.attrs["lifecycle_state"] = "finalized"
    plan = plan_recovery(rename_pending, clock=clock)
    assert plan.recoverable is False
    assert any("lifecycle_state" in reason for reason in plan.blocked_reasons)

    # a *writing* file that is not partial-named
    misnamed = scratch_dir / "misnamed" / f"{_MISSION_ID}.rcscan"
    misnamed.parent.mkdir()
    bulk_write_partial(misnamed.parent, [BulkRow(0)])
    os.replace(misnamed.parent / f"{_AIR_FILE_ID}{PARTIAL_SUFFIX}", misnamed)
    plan = plan_recovery(misnamed, clock=clock)
    assert plan.recoverable is False
    assert any("partial" in reason for reason in plan.blocked_reasons)
    assert list(misnamed.parent.iterdir()) == [misnamed]  # nothing extra created


def test_plan_blocks_when_target_already_exists(
    scratch_dir: Path,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
    clock: ManualClock,
) -> None:
    partial = write_crashed_partial(
        scratch_dir,
        committed_indices=(0, 1),
        fault_phase=WritePhase.AFTER_TRACE_COLUMNS,
        fault_index=2,
        channels=channels,
        frequencies=frequencies,
        mission_config=mission_config,
        clock=clock,
    )
    new_file_id = AirFileId("bbbbbbb1-0000-4000-8000-0000000000aa")
    existing = scratch_dir / f"{new_file_id}.rcscan"
    existing.write_bytes(b"occupied")
    plan = plan_recovery(partial, new_file_id=new_file_id, clock=clock)
    assert plan.recoverable is False
    assert any("target" in reason for reason in plan.blocked_reasons)


def test_plan_rejects_wrong_role_file_id(
    scratch_dir: Path,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
    clock: ManualClock,
) -> None:
    partial = write_crashed_partial(
        scratch_dir,
        committed_indices=(0,),
        fault_phase=WritePhase.AFTER_TRACE_COLUMNS,
        fault_index=1,
        channels=channels,
        frequencies=frequencies,
        mission_config=mission_config,
        clock=clock,
    )
    with pytest.raises(DomainError):
        plan_recovery(
            partial,
            new_file_id=GroundFileId("bbbbbbb2-0000-4000-8000-0000000000aa"),
            clock=clock,
        )


def test_plan_honours_explicit_target_dir_and_optional_groups_block(
    scratch_dir: Path,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
    clock: ManualClock,
) -> None:
    partial = write_crashed_partial(
        scratch_dir,
        committed_indices=(0,),
        fault_phase=WritePhase.AFTER_TRACE_COLUMNS,
        fault_index=1,
        channels=channels,
        frequencies=frequencies,
        mission_config=mission_config,
        clock=clock,
    )
    out_dir = scratch_dir / "out"
    out_dir.mkdir()
    new_file_id = AirFileId("bbbbbbb1-0000-4000-8000-0000000000aa")
    plan = plan_recovery(partial, new_file_id=new_file_id, target_dir=out_dir, clock=clock)
    assert plan.target_path == str(out_dir / f"{new_file_id}.rcscan")
    assert plan.recoverable is True

    # optional processed groups present -> fail-closed block (never dropped)
    optional_partial = scratch_dir / "optional" / f"{_AIR_FILE_ID}{PARTIAL_SUFFIX}"
    optional_partial.parent.mkdir()
    bulk_write_partial(optional_partial.parent, [BulkRow(0)])
    add_optional_time_group(optional_partial, channels=2, frequency_points=_FREQUENCY_POINTS)
    report = inspect_partial(optional_partial)
    assert "/axes/time_base_s" in report.optional_groups_present
    plan = plan_recovery(optional_partial, clock=clock)
    assert plan.recoverable is False
    assert any("optional" in reason for reason in plan.blocked_reasons)
    with pytest.raises(DomainError):
        execute_recovery(plan)
    assert optional_partial.exists()


# ---------------------------------------------------------------------------
# Execute: round trip, tail handling, collisions, source protection
# ---------------------------------------------------------------------------


def test_recovery_roundtrip_matches_source_committed_rows(
    scratch_dir: Path,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
    clock: ManualClock,
) -> None:
    partial = write_crashed_partial(
        scratch_dir,
        committed_indices=(5, 1, 3, 0, 4, 2),
        fault_phase=WritePhase.AFTER_TRACE_COLUMNS,
        fault_index=6,
        channels=channels,
        frequencies=frequencies,
        mission_config=mission_config,
        clock=clock,
    )
    source_sha = file_sha256(partial)
    new_file_id = AirFileId("bbbbbbb1-0000-4000-8000-0000000000aa")
    plan = plan_recovery(partial, new_file_id=new_file_id, clock=clock)

    result = execute_recovery(plan, clock=clock)

    assert isinstance(result, RecoveryResult)
    assert result.target_path == plan.target_path
    assert result.new_file_id == plan.new_file_id
    assert result.source_sha256 == source_sha
    assert result.copied_record_count == 6
    assert result.recovered_utc == clock.utc_now()
    assert result.tool_version == RECOVERY_COMPONENT_VERSION
    json.dumps(result.to_dict(), sort_keys=True)

    target = Path(result.target_path)
    assert target.exists()
    assert source_sha == file_sha256(partial)  # source bytes never change

    with RcScanReader(target) as reader:
        assert reader.lifecycle_state == "recovered"
        assert reader.completion_kind == "recovered"
        assert reader.rename_pending is False
        assert reader.probe.file_id == str(new_file_id)
        assert reader.committed_record_count == 6
        assert reader.physical_record_count == 6
        assert reader.mission_id == _MISSION_ID
        assert reader.channels == channels
        assert np.array_equal(reader.frequencies_hz, frequencies)
        # physical view = source committed rows, in the same commit order
        physical = collect_all(reader, logical=False)
        assert [record.trace_index for record in physical] == [5, 1, 3, 0, 4, 2]
        assert [record.record_position for record in physical] == [0, 1, 2, 3, 4, 5]
        # logical view sorted by explicit trace_index
        logical = collect_all(reader, logical=True)
        assert [record.trace_index for record in logical] == [0, 1, 2, 3, 4, 5]
        report = reader.validation_report()
        assert report.missing == ()
        assert report.duplicates == ()
        assert report.conflicts == ()
        assert report.issues == ()

    with RcScanReader(partial) as source:
        source_records = collect_all(source, logical=False)
    assert tuple(trace_key(record) for record in source_records) == tuple(
        trace_key(record) for record in physical
    )
    for record in physical:
        assert record.hash_verified is True
        assert record.metadata.gnss_match is not None
        expected_raw = make_raw(
            record.trace_index, channels=len(channels), frequencies=int(frequencies.size)
        )
        assert np.array_equal(record.frequency_raw, expected_raw)

    # provenance recorded on the recovered file
    with h5py.File(target, "r") as h5:
        mission = h5["mission"].attrs
        assert mission["recovery_source_sha256"] == source_sha
        assert mission["recovery_source_file_id"] == str(_AIR_FILE_ID)
        assert mission["recovery_tool_version"] == RECOVERY_COMPONENT_VERSION
        assert mission["completion_kind"] == "recovered"
        assert h5.attrs["lifecycle_state"] == "recovered"

    # post-validation via the strict validator passes on the final path
    post = RcScanValidator.validate(target)
    assert post.committed_record_count == 6
    assert post.lifecycle_state == "recovered"


def test_half_written_tail_is_not_copied(
    scratch_dir: Path,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
    clock: ManualClock,
) -> None:
    partial = write_crashed_partial(
        scratch_dir,
        committed_indices=(0, 1),
        fault_phase=WritePhase.AFTER_RAW_WRITE,
        fault_index=2,
        channels=channels,
        frequencies=frequencies,
        mission_config=mission_config,
        clock=clock,
    )
    plan = plan_recovery(partial, clock=clock)
    assert plan.committed_record_count == 2
    assert plan.tail_rows == 1

    result = execute_recovery(plan, clock=clock)
    target = Path(result.target_path)
    with h5py.File(target, "r") as h5:
        assert h5["/frequency/raw"].shape[0] == 2
        for path in (
            "/trace_metadata/trace_index",
            "/trace_metadata/trace_uid",
            "/gnss/valid",
        ):
            assert h5[path].shape[0] == 2
    with RcScanReader(target) as reader:
        records = collect_all(reader, logical=True)
        assert [record.trace_index for record in records] == [0, 1]


def test_execute_fails_closed_on_target_collision_and_source_change(
    scratch_dir: Path,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
    clock: ManualClock,
) -> None:
    partial = write_crashed_partial(
        scratch_dir,
        committed_indices=(0, 1),
        fault_phase=WritePhase.AFTER_TRACE_COLUMNS,
        fault_index=2,
        channels=channels,
        frequencies=frequencies,
        mission_config=mission_config,
        clock=clock,
    )
    new_file_id = AirFileId("bbbbbbb1-0000-4000-8000-0000000000aa")
    plan = plan_recovery(partial, new_file_id=new_file_id, clock=clock)
    target = Path(plan.target_path)
    target.write_bytes(b"occupied")
    target_sha = file_sha256(target)
    source_sha = file_sha256(partial)
    with pytest.raises(DomainError) as caught:
        execute_recovery(plan, clock=clock)
    assert caught.value.code is ErrorCode.INVALID_ARGUMENT
    assert target_sha == file_sha256(target)  # existing target untouched
    assert source_sha == file_sha256(partial)
    assert not (scratch_dir / f"{new_file_id}{PARTIAL_SUFFIX}").exists()

    # source changed after planning -> refuse (never recover a different file)
    partial2 = write_crashed_partial(
        scratch_dir / "changed",        committed_indices=(0, 1),
        fault_phase=WritePhase.AFTER_TRACE_COLUMNS,
        fault_index=2,
        channels=channels,
        frequencies=frequencies,
        mission_config=mission_config,
        clock=clock,
    )
    plan2 = plan_recovery(
        partial2,
        new_file_id=AirFileId("ccccccc1-0000-4000-8000-0000000000aa"),
        clock=clock,
    )
    with h5py.File(partial2, "r+") as h5:
        h5["/trace_metadata/trace_index"][0] = np.int64(99)
    with pytest.raises(DomainError) as caught:
        execute_recovery(plan2, clock=clock)
    assert caught.value.code is ErrorCode.INVALID_ARGUMENT
    assert not Path(plan2.target_path).exists()


# ---------------------------------------------------------------------------
# Failure handling: cleanup on mid-recovery faults, never a pseudo-finalized file
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fault_phase",
    [
        RecoveryPhase.BEFORE_TARGET_CREATE,
        RecoveryPhase.AFTER_TARGET_CREATE,
        RecoveryPhase.AFTER_ROW_COPY,
        RecoveryPhase.AFTER_CHECKPOINT_WRITE,
        RecoveryPhase.BEFORE_FINAL_MARK,
        RecoveryPhase.AFTER_FINAL_MARK,
        RecoveryPhase.BEFORE_RENAME,
    ],
)
def test_mid_recovery_failure_cleans_target_and_allows_retry(
    scratch_dir: Path,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
    clock: ManualClock,
    fault_phase: RecoveryPhase,
) -> None:
    partial = write_crashed_partial(
        scratch_dir,
        committed_indices=(0, 1, 2),
        fault_phase=WritePhase.AFTER_TRACE_COLUMNS,
        fault_index=3,
        channels=channels,
        frequencies=frequencies,
        mission_config=mission_config,
        clock=clock,
    )
    new_file_id = AirFileId("bbbbbbb1-0000-4000-8000-0000000000aa")
    plan = plan_recovery(partial, new_file_id=new_file_id, clock=clock)
    source_sha = file_sha256(partial)
    hook = RecoveryFaultHook()
    hook.arm(fault_phase)

    with pytest.raises(InjectedRecoveryFault):
        execute_recovery(plan, clock=clock, fault_hook=hook)

    # no final target and no leftover temp: nothing pseudo-finalized survives
    assert not Path(plan.target_path).exists()
    assert not (scratch_dir / f"{new_file_id}{PARTIAL_SUFFIX}").exists()
    assert file_sha256(partial) == source_sha

    # a clean retry succeeds after the failed attempt was cleaned up
    result = execute_recovery(plan, clock=clock)
    assert Path(result.target_path).exists()
    with RcScanReader(result.target_path) as reader:
        assert reader.lifecycle_state == "recovered"
        assert reader.committed_record_count == 3


class FailingRemoveFileSystem:
    """Recovery filesystem seam whose ``remove`` always fails (test-only)."""

    def exists(self, path: Path) -> bool:
        return path.exists()

    def remove(self, path: Path) -> None:
        raise OSError("injected remove failure")

    def replace(self, source: Path, target: Path) -> None:
        os.replace(source, target)


def test_cleanup_remove_failure_leaves_only_partial_named_remnant(
    scratch_dir: Path,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
    clock: ManualClock,
) -> None:
    """If even cleanup fails, the remnant is partial-named (never a final
    ``.rcscan``) and is reported explicitly — fail closed, not silent."""
    partial = write_crashed_partial(
        scratch_dir,
        committed_indices=(0, 1),
        fault_phase=WritePhase.AFTER_TRACE_COLUMNS,
        fault_index=2,
        channels=channels,
        frequencies=frequencies,
        mission_config=mission_config,
        clock=clock,
    )
    source_sha = file_sha256(partial)
    new_file_id = AirFileId("bbbbbbb1-0000-4000-8000-0000000000aa")
    plan = plan_recovery(partial, new_file_id=new_file_id, clock=clock)
    hook = RecoveryFaultHook()
    hook.arm(RecoveryPhase.AFTER_ROW_COPY)

    with pytest.raises(DomainError) as caught:
        execute_recovery(
            plan,
            clock=clock,
            fault_hook=hook,
            filesystem=FailingRemoveFileSystem(),
        )
    assert "leftover" in caught.value.message
    remnant = scratch_dir / f"{new_file_id}{PARTIAL_SUFFIX}"
    assert remnant.exists()
    assert remnant.name.endswith(PARTIAL_SUFFIX)  # never a final-looking name
    with h5py.File(remnant, "r") as h5:
        assert h5.attrs["lifecycle_state"] == "writing"  # never pseudo-finalized
    assert not Path(plan.target_path).exists()
    assert file_sha256(partial) == source_sha


def test_cleanup_failure_after_final_mark_still_leaves_partial_named_remnant(
    scratch_dir: Path,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
    clock: ManualClock,
) -> None:
    partial = write_crashed_partial(
        scratch_dir,
        committed_indices=(0,),
        fault_phase=WritePhase.AFTER_TRACE_COLUMNS,
        fault_index=1,
        channels=channels,
        frequencies=frequencies,
        mission_config=mission_config,
        clock=clock,
    )
    new_file_id = AirFileId("bbbbbbb1-0000-4000-8000-0000000000aa")
    plan = plan_recovery(partial, new_file_id=new_file_id, clock=clock)
    hook = RecoveryFaultHook()
    hook.arm(RecoveryPhase.AFTER_FINAL_MARK)

    with pytest.raises(DomainError):
        execute_recovery(
            plan,
            clock=clock,
            fault_hook=hook,
            filesystem=FailingRemoveFileSystem(),
        )
    remnant = scratch_dir / f"{new_file_id}{PARTIAL_SUFFIX}"
    assert remnant.exists()
    assert remnant.name.endswith(PARTIAL_SUFFIX)
    assert not Path(plan.target_path).exists()


# ---------------------------------------------------------------------------
# Evidence preservation: duplicates, conflicts, missing hash, out-of-order rows
# ---------------------------------------------------------------------------


def test_duplicates_conflicts_and_missing_hash_are_preserved_verbatim(
    scratch_dir: Path,
) -> None:
    """Recovery copies committed rows as raw cells: ISSUE-011's classification
    of the recovered file is identical to the source's classification."""
    partial = bulk_write_partial(
        scratch_dir,
        [
            BulkRow(0, salt=0.0),
            BulkRow(1, salt=1.0),
            BulkRow(1, salt=1.0),  # duplicate: same index/uid/hash
            BulkRow(2, salt=2.0),
            BulkRow(2, salt=3.0),  # conflict: same index, different hash
            BulkRow(4, salt=4.0, attach_hash=False),  # missing stored hash
        ],
    )
    with RcScanReader(partial) as source:
        source_report = source.validation_report()
        assert len(source_report.duplicates) == 1
        assert len(source_report.conflicts) == 1
        assert source_report.missing == (MissingTrace(trace_index=3),)
        assert len(source_report.issues) == 1
        assert source_report.issues[0].kind.value == "missing_hash"

    report = inspect_partial(partial)
    assert len(report.validation.duplicates) == 1
    assert len(report.validation.conflicts) == 1
    assert [entry.trace_index for entry in report.validation.missing] == [3]
    assert len(report.validation.issues) == 1

    plan = plan_recovery(partial, clock=ManualClock(_CREATED_UTC, 0))
    assert plan.recoverable is True
    assert plan.warnings, "data-level issues must surface as warnings"
    assert "conflicts" in plan.warnings[0]

    result = execute_recovery(plan, clock=ManualClock(_CREATED_UTC, 0))
    target = Path(result.target_path)
    with RcScanReader(target) as reader:
        assert reader.committed_record_count == 6
        recovered_report = reader.validation_report()
        assert len(recovered_report.duplicates) == 1
        assert len(recovered_report.conflicts) == 1
        assert [entry.trace_index for entry in recovered_report.missing] == [3]
        assert len(recovered_report.issues) == 1
        # logical view excludes the conflicting identity, serves the rest
        logical = collect_all(reader, logical=True)
        assert [record.trace_index for record in logical] == [0, 1, 4]
        physical = collect_all(reader, logical=False)
        assert len(physical) == 6
        assert [record.trace_index for record in physical] == [0, 1, 1, 2, 2, 4]
    # byte-identical classification: source and target reports agree
    assert recovered_report.to_dict()["summary"] == source_report.to_dict()["summary"]


def test_out_of_order_physical_rows_survive_recovery_in_commit_order(
    scratch_dir: Path,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
    clock: ManualClock,
) -> None:
    partial = write_crashed_partial(
        scratch_dir,
        committed_indices=(9, 2, 7, 0),
        fault_phase=WritePhase.AFTER_TRACE_COLUMNS,
        fault_index=5,
        channels=channels,
        frequencies=frequencies,
        mission_config=mission_config,
        clock=clock,
    )
    plan = plan_recovery(partial, clock=clock)
    result = execute_recovery(plan, clock=clock)
    with RcScanReader(result.target_path) as reader:
        physical = collect_all(reader, logical=False)
        assert [record.trace_index for record in physical] == [9, 2, 7, 0]
        logical = collect_all(reader, logical=True)
        assert [record.trace_index for record in logical] == [0, 2, 7, 9]


def test_ground_partial_without_transport_recovers(
    scratch_dir: Path,
) -> None:
    partial = bulk_write_partial(
        scratch_dir,
        [BulkRow(0), BulkRow(1, with_gnss=False)],
        role=EndpointRole.GROUND,
    )
    with RcScanReader(partial) as reader:
        assert reader.probe.file_role is EndpointRole.GROUND
        assert reader.committed_record_count == 2
    plan = plan_recovery(partial, clock=ManualClock(_CREATED_UTC, 0))
    assert plan.recoverable is True
    result = execute_recovery(plan, clock=ManualClock(_CREATED_UTC, 0))
    with RcScanReader(result.target_path) as reader:
        assert reader.probe.file_role is EndpointRole.GROUND
        assert reader.committed_record_count == 2
        records = collect_all(reader, logical=True)
        assert [record.trace_index for record in records] == [0, 1]
        assert records[1].metadata.gnss_match is None  # no GNSS fabricated
    with h5py.File(result.target_path, "r") as h5:
        assert "/transport" not in h5


def test_empty_partial_recovers_to_empty_recovered_file(
    scratch_dir: Path,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
    clock: ManualClock,
) -> None:
    writer = create_writer(
        scratch_dir,
        role=EndpointRole.AIR,
        channels=channels,
        frequencies=frequencies,
        mission_config=mission_config,
        clock=clock,
    )
    writer.abort()
    partial = partial_path(scratch_dir)
    plan = plan_recovery(partial, clock=clock)
    assert plan.recoverable is True
    result = execute_recovery(plan, clock=clock)
    assert result.copied_record_count == 0
    with RcScanReader(result.target_path) as reader:
        assert reader.committed_record_count == 0
        assert reader.lifecycle_state == "recovered"
        assert reader.last_trace_index == schema.MISSING_INT64
        assert collect_all(reader, logical=True) == ()


def test_large_file_recovery_is_correct_and_bounded(
    scratch_dir: Path,
) -> None:
    rows = [BulkRow(index, salt=float(index) * 0.01) for index in range(2000)]
    partial = bulk_write_partial(scratch_dir, rows)
    plan = plan_recovery(partial, clock=ManualClock(_CREATED_UTC, 0))
    assert plan.committed_record_count == 2000
    result = execute_recovery(plan, clock=ManualClock(_CREATED_UTC, 0))
    with RcScanReader(result.target_path) as reader:
        assert reader.committed_record_count == 2000
        chunk_sizes: list[int] = []
        total = 0
        for chunk in reader.iter_physical(chunk_rows=64):
            chunk_sizes.append(len(chunk.records))
            total += len(chunk.records)
        assert total == 2000
        assert all(size <= 64 for size in chunk_sizes)
        assert len(chunk_sizes) == 32
        logical = collect_all(reader, logical=True)
        assert len(logical) == 2000
        assert logical[0].trace_index == 0
        assert logical[-1].trace_index == 1999
        report = reader.validation_report()
        assert report.missing == ()
        assert report.issues == ()


def test_recovered_target_naming_and_explicit_file_id(
    scratch_dir: Path,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
    clock: ManualClock,
) -> None:
    partial = write_crashed_partial(
        scratch_dir,
        committed_indices=(0,),
        fault_phase=WritePhase.AFTER_TRACE_COLUMNS,
        fault_index=1,
        channels=channels,
        frequencies=frequencies,
        mission_config=mission_config,
        clock=clock,
    )
    new_file_id = AirFileId("ddddddd1-0000-4000-8000-0000000000dd")
    plan = plan_recovery(partial, new_file_id=new_file_id, clock=clock)
    result = execute_recovery(plan, clock=clock)
    assert Path(result.target_path).name == f"{new_file_id}.rcscan"
    assert Path(result.target_path).parent == scratch_dir
    # default generation: plan without explicit id picks a fresh role-typed id
    auto = plan_recovery(partial, clock=clock)
    assert auto.recoverable is True
    assert auto.new_file_id != str(new_file_id)
    assert AirFileId.from_json(auto.new_file_id) is not None
