"""ISSUE-011 contract tests: read-only RcScanReader/RcScanValidator.

This suite pins the ISSUE-011 reader contract on top of the frozen
ISSUE-008 physical schema (``rcscan_v2.py``), the ISSUE-009 canonical raw
hash and ISSUE-010 writer-produced files:

- strict open-time validation: schema/profile/role/lifecycle/dtype/length/
  checkpoint; unknown versions fail closed; optional processed groups may be
  absent;
- visibility window: only rows below ``committed_record_count`` with complete
  required columns are exposed (half-written tails are invisible);
- dual views: physical commit order and a logical view sorted by explicit
  ``trace_index``/``trace_uid``; missing/duplicate/conflict classification
  with retained evidence;
- lazy chunked iteration over large synthetic files (bounded memory).

Fixtures use both the real ISSUE-010 writer (fault injection for half-written
tails, rename failure for ``awaiting_rename`` presentation) and a fast
test-only bulk builder for large/corrupt files.  Everything is synthetic;
no ``sleep``, no hardware, no reference projects.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
    GnssUnavailableReason,
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
    FileSystemFacade,
    InjectedStorageFault,
    PhaseFaultHook,
    RcScanIncrementalWriter,
    TraceAppendRequest,
    WritePhase,
)
from uav_gpr.storage.rcscan_reader import (
    ConflictTrace,
    DuplicateTrace,
    IssueKind,
    MissingTrace,
    RcScanReader,
    RcScanValidator,
    ReadTrace,
    TraceChunk,
    ValidationReport,
)

pytestmark = pytest.mark.contract

# ---------------------------------------------------------------------------
# Frozen synthetic test contract (mirrors the ISSUE-010 test constants)
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
    return ManualClock(_CREATED_UTC, monotonic_ns=0)


def _uid_str(index: int) -> str:
    """Deterministic canonical lowercase UUID string per trace index."""
    return str(uuid.UUID(int=index + 1))


def make_raw(index: int, *, channels: int, frequencies: int, salt: float = 0.0) -> np.ndarray:
    """Deterministic complex raw sweep ``channel x frequency``."""
    rows = np.arange(channels, dtype=np.float64)[:, None]
    cols = np.arange(frequencies, dtype=np.float64)[None, :]
    real = np.cos((rows * 0.5) + (cols * 0.25) + index + salt)
    imag = np.sin((rows * 0.25) + (cols * 0.5) + index + salt)
    return np.ascontiguousarray(real + 1j * imag, dtype="<c16")


def make_gnss_match(midpoint: datetime, monotonic_ns: int, *, valid: bool = True) -> GnssMatch:
    if valid:
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
    return GnssMatch(
        fix=None,
        trace_midpoint_utc=midpoint,
        age_s=None,
        method=GnssMatchMethod.NEAREST_MIDPOINT,
        usable_for_map=False,
        reason=GnssUnavailableReason.NO_FIX,
    )


def make_metadata(
    index: int,
    *,
    trace_uid: TraceUid | None = None,
    with_gnss: bool = True,
) -> TraceMetadata:
    """Acquired (hash-less) metadata for one logical trace index."""
    base = _CREATED_UTC + timedelta(seconds=1 + index)
    started = base
    midpoint = base + timedelta(milliseconds=50)
    finished = base + timedelta(milliseconds=100)
    monotonic = 1_000_000_000 * (index + 1)
    actual = None if index == 0 else 0.1
    schedule = None if index == 0 else 0.001
    match = (
        make_gnss_match(midpoint, monotonic + 50_000_000, valid=True)
        if with_gnss
        else None
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
        trace_uid=trace_uid if trace_uid is not None else TraceUid(uuid.UUID(int=index + 1)),
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
    with_gnss: bool = True,
) -> TraceAppendRequest:
    return TraceAppendRequest(
        metadata=make_metadata(index, with_gnss=with_gnss),
        frequency_raw=make_raw(
            index,
            channels=len(channels),
            frequencies=int(frequencies.size),
            salt=salt,
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
    fault_hook: PhaseFaultHook | None = None,
    filesystem: FileSystemFacade | None = None,
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
    )


def final_path(scratch_dir: Path) -> Path:
    return scratch_dir / f"{_MISSION_ID}.rcscan"


def partial_path(scratch_dir: Path, role: EndpointRole = EndpointRole.AIR) -> Path:
    file_id = _AIR_FILE_ID if role is EndpointRole.AIR else _GROUND_FILE_ID
    return scratch_dir / f"{file_id}{PARTIAL_SUFFIX}"


# ---------------------------------------------------------------------------
# Fast bulk builder for large / duplicate / conflict / corrupt fixtures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BulkRow:
    """One logical row for the fast bulk builder (deterministic synthetic)."""

    trace_index: int
    trace_uid: str
    salt: float = 0.0
    with_gnss: bool = True


def bulk_build_rcscan(
    scratch_dir: Path,
    rows: Sequence[BulkRow],
    *,
    role: EndpointRole = EndpointRole.AIR,
    completion_kind: str = "completed",
    lifecycle: str = "finalized",
    checkpoint_override: int | None = None,
    last_index_override: int | None = None,
    updated_utc_override: str | None = None,
) -> Path:
    """Build a v2 file fast by writing whole columns at once (test-only).

    Uses the same frozen schema creator and the same authoritative row codec
    (``trace_metadata_to_cells``) as the ISSUE-010 writer, so the produced
    bytes are contract-identical; the difference is only write throughput.
    """
    channels = build_channels()
    config = build_mission_config(channels)
    axis = np.asarray(config.frequency_axis_hz, dtype="<f8")
    file_id = _AIR_FILE_ID if role is EndpointRole.AIR else _GROUND_FILE_ID
    partial = scratch_dir / f"{file_id}{PARTIAL_SUFFIX}"
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
            metadata = make_metadata(
                row.trace_index,
                trace_uid=TraceUid(uuid.UUID(row.trace_uid)),
                with_gnss=row.with_gnss,
            )
            raw = make_raw(
                row.trace_index,
                channels=len(channels),
                frequencies=int(axis.size),
                salt=row.salt,
            )
            digest = compute_raw_trace_sha256(
                mission_id=_MISSION_ID,
                trace_index=row.trace_index,
                trace_uid=metadata.trace_uid,
                channels=channels,
                frequencies_hz=axis,
                data=raw,
            )
            cells = schema.trace_metadata_to_cells(metadata.with_integrity(digest))
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
            if last_index_override is None
            else last_index_override
        )
        h5["/checkpoints/last_trace_index"][0] = np.int64(last_index)
        updated = updated_utc_override if updated_utc_override is not None else to_utc_iso(
            _CREATED_UTC + timedelta(seconds=count)
        )
        h5["/checkpoints/updated_utc"][0] = updated
        mission = h5["mission"]
        mission.attrs["started_utc"] = to_utc_iso(_CREATED_UTC)
        if lifecycle == "writing":
            mission.attrs["ended_utc"] = ""
            mission.attrs["completion_kind"] = ""
            h5.attrs["lifecycle_state"] = "writing"
        else:
            mission.attrs["ended_utc"] = to_utc_iso(_CREATED_UTC + timedelta(seconds=count))
            mission.attrs["completion_kind"] = completion_kind
            h5.attrs["lifecycle_state"] = lifecycle
    if lifecycle in ("finalized", "recovered"):
        final = scratch_dir / f"{_MISSION_ID}.rcscan"
        os.replace(partial, final)
        return final
    return partial


def corrupt_cell(path: Path, dataset_path: str, position: int, value: object) -> None:
    """Overwrite one stored cell (test-only corruption)."""
    with h5py.File(path, "r+") as h5:
        h5[dataset_path][position] = value


def corrupt_attr(path: Path, attr: str, value: object) -> None:
    with h5py.File(path, "r+") as h5:
        h5.attrs[attr] = value


def delete_dataset(path: Path, dataset_path: str) -> None:
    with h5py.File(path, "r+") as h5:
        del h5[dataset_path]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def collect_all(reader: RcScanReader, *, logical: bool) -> tuple[ReadTrace, ...]:
    chunks = reader.iter_logical() if logical else reader.iter_physical()
    return tuple(record for chunk in chunks for record in chunk.records)


def as_chunk_sizes(reader: RcScanReader, *, logical: bool, chunk_rows: int) -> tuple[int, ...]:
    iterator = (
        reader.iter_logical(chunk_rows=chunk_rows)
        if logical
        else reader.iter_physical(chunk_rows=chunk_rows)
    )
    return tuple(len(chunk.records) for chunk in iterator)


# ---------------------------------------------------------------------------
# Happy paths: writer-produced files, dual views, round trip
# ---------------------------------------------------------------------------


def test_writer_file_roundtrip_physical_and_logical_views(
    scratch_dir: Path,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
    clock: ManualClock,
) -> None:
    """A real writer-produced finalized file decodes completely in both views."""
    with create_writer(
        scratch_dir,
        role=EndpointRole.AIR,
        channels=channels,
        frequencies=frequencies,
        mission_config=mission_config,
        clock=clock,
    ) as writer:
        for index in range(4):
            writer.append_trace(make_request(index, channels=channels, frequencies=frequencies))
        writer.close(MissionTerminalState.COMPLETED)

    reader = RcScanReader(final_path(scratch_dir))
    assert reader.committed_record_count == 4
    assert reader.physical_record_count == 4
    assert reader.lifecycle_state == "finalized"
    assert reader.completion_kind == "completed"
    assert reader.rename_pending is False
    assert reader.channels == channels
    assert np.array_equal(reader.frequencies_hz, frequencies)

    physical = collect_all(reader, logical=False)
    logical = collect_all(reader, logical=True)
    assert [record.trace_index for record in physical] == [0, 1, 2, 3]
    assert [record.trace_index for record in logical] == [0, 1, 2, 3]
    for record in physical:
        assert record.hash_verified is True
        assert record.raw_trace_sha256 == record.metadata.raw_trace_sha256
        assert record.trace_uid == _uid_str(record.trace_index)
        expected_raw = make_raw(
            record.trace_index, channels=len(channels), frequencies=int(frequencies.size)
        )
        assert np.array_equal(record.frequency_raw, expected_raw)
        assert record.metadata.mission_id == _MISSION_ID
        assert record.metadata.device_id == _DEVICE_ID
        assert record.metadata.gnss_match is not None
        assert record.metadata.gnss_match.fix is not None
        assert record.metadata.gnss_match.fix.valid is True
    def key(record: ReadTrace) -> tuple[int, int, str, str, bool]:
        return (
            record.record_position,
            record.trace_index,
            record.trace_uid,
            record.raw_trace_sha256,
            record.hash_verified,
        )

    assert tuple(key(record) for record in collect_all(reader, logical=False)) == tuple(
        key(record) for record in physical
    )  # stable, repeatable

    report = reader.validation_report()
    assert report.committed_record_count == 4
    assert report.missing == ()
    assert report.duplicates == ()
    assert report.conflicts == ()
    assert report.issues == ()
    reader.close()


def test_out_of_order_physical_rows_are_sorted_in_logical_view(
    scratch_dir: Path,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
    clock: ManualClock,
) -> None:
    """Retransmitted traces (out-of-order physical rows) sort by trace_index."""
    with create_writer(
        scratch_dir,
        role=EndpointRole.AIR,
        channels=channels,
        frequencies=frequencies,
        mission_config=mission_config,
        clock=clock,
    ) as writer:
        for index in (5, 1, 3, 0, 4, 2):
            writer.append_trace(make_request(index, channels=channels, frequencies=frequencies))
        writer.close(MissionTerminalState.COMPLETED)

    reader = RcScanReader(final_path(scratch_dir))
    physical = collect_all(reader, logical=False)
    logical = collect_all(reader, logical=True)
    assert [record.trace_index for record in physical] == [5, 1, 3, 0, 4, 2]
    assert [record.trace_index for record in logical] == [0, 1, 2, 3, 4, 5]
    assert [record.record_position for record in logical] == [3, 1, 5, 2, 4, 0]
    report = reader.validation_report()
    assert report.missing == ()
    reader.close()


def test_missing_indices_are_reported(
    scratch_dir: Path,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
    clock: ManualClock,
) -> None:
    with create_writer(
        scratch_dir,
        role=EndpointRole.AIR,
        channels=channels,
        frequencies=frequencies,
        mission_config=mission_config,
        clock=clock,
    ) as writer:
        for index in (0, 1, 3, 5):
            writer.append_trace(make_request(index, channels=channels, frequencies=frequencies))
        writer.close(MissionTerminalState.COMPLETED)

    reader = RcScanReader(final_path(scratch_dir))
    report = reader.validation_report()
    assert report.missing == (MissingTrace(trace_index=2), MissingTrace(trace_index=4))
    assert [record.trace_index for record in collect_all(reader, logical=True)] == [0, 1, 3, 5]
    reader.close()


def test_missing_gnss_rows_decode_without_fabrication(
    scratch_dir: Path,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
    clock: ManualClock,
) -> None:
    """Rows without GNSS keep the schema sentinel semantics; no fake position."""
    with create_writer(
        scratch_dir,
        role=EndpointRole.AIR,
        channels=channels,
        frequencies=frequencies,
        mission_config=mission_config,
        clock=clock,
    ) as writer:
        writer.append_trace(
            make_request(0, channels=channels, frequencies=frequencies, with_gnss=False)
        )
        writer.append_trace(
            make_request(1, channels=channels, frequencies=frequencies, with_gnss=True)
        )
        writer.close(MissionTerminalState.COMPLETED)

    reader = RcScanReader(final_path(scratch_dir))
    records = collect_all(reader, logical=True)
    assert records[0].metadata.gnss_match is None
    assert records[0].metadata.quality_status is TraceQualityStatus.DEGRADED
    assert records[1].metadata.gnss_match is not None
    assert records[1].metadata.gnss_match.fix is not None
    reader.close()


def test_ground_role_without_transport_group_reads(
    scratch_dir: Path,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
    clock: ManualClock,
) -> None:
    with create_writer(
        scratch_dir,
        role=EndpointRole.GROUND,
        channels=channels,
        frequencies=frequencies,
        mission_config=mission_config,
        clock=clock,
    ) as writer:
        writer.append_trace(make_request(0, channels=channels, frequencies=frequencies))
        writer.append_trace(make_request(1, channels=channels, frequencies=frequencies))
        writer.close(MissionTerminalState.COMPLETED)

    reader = RcScanReader(final_path(scratch_dir))
    assert reader.probe.file_role is EndpointRole.GROUND
    assert [record.trace_index for record in collect_all(reader, logical=True)] == [0, 1]
    report = reader.validation_report()
    assert report.issues == ()
    reader.close()


def test_empty_writing_skeleton_is_readable(
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

    reader = RcScanReader(partial_path(scratch_dir))
    assert reader.committed_record_count == 0
    assert reader.lifecycle_state == "writing"
    assert reader.rename_pending is False
    assert collect_all(reader, logical=False) == ()
    assert collect_all(reader, logical=True) == ()
    report = reader.validation_report()
    assert report.committed_record_count == 0
    assert report.missing == ()
    reader.close()


def test_aborted_writing_partial_committed_rows_still_readable(
    scratch_dir: Path,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
    clock: ManualClock,
) -> None:
    """A ``writing`` partial (crash before finalize) reads its committed rows."""
    writer = create_writer(
        scratch_dir,
        role=EndpointRole.AIR,
        channels=channels,
        frequencies=frequencies,
        mission_config=mission_config,
        clock=clock,
    )
    for index in range(3):
        writer.append_trace(make_request(index, channels=channels, frequencies=frequencies))
    writer.abort()

    reader = RcScanReader(partial_path(scratch_dir))
    assert reader.committed_record_count == 3
    assert reader.lifecycle_state == "writing"
    assert [record.trace_index for record in collect_all(reader, logical=True)] == [0, 1, 2]
    reader.close()


# ---------------------------------------------------------------------------
# Half-written tails (ISSUE-010 fault injection) are invisible
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fault_phase,expected_writer_physical",
    [
        (WritePhase.BEFORE_RAW_WRITE, 3),
        (WritePhase.AFTER_RAW_WRITE, 4),
        (WritePhase.AFTER_TRACE_COLUMNS, 4),
    ],
)
def test_half_written_tail_is_invisible_after_writer_fault(
    scratch_dir: Path,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
    clock: ManualClock,
    fault_phase: WritePhase,
    expected_writer_physical: int,
) -> None:
    """Reader sees only the last full checkpoint, never the half row."""
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
    for index in range(3):
        writer.append_trace(make_request(index, channels=channels, frequencies=frequencies))
    hook.arm(fault_phase)
    with pytest.raises(InjectedStorageFault):
        writer.append_trace(make_request(3, channels=channels, frequencies=frequencies))
    assert writer.committed_record_count == 3
    assert writer.physical_record_count == expected_writer_physical

    reader = RcScanReader(partial_path(scratch_dir))
    assert reader.committed_record_count == 3
    records = collect_all(reader, logical=True)
    assert [record.trace_index for record in records] == [0, 1, 2]
    report = reader.validation_report()
    assert report.issues == ()
    reader.close()


def test_physical_rows_beyond_checkpoint_are_exposed_as_count_only(
    scratch_dir: Path,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
    clock: ManualClock,
) -> None:
    """The raw dataset may physically hold a tail row; the reader counts but
    never exposes it as a record."""
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
    for index in range(2):
        writer.append_trace(make_request(index, channels=channels, frequencies=frequencies))
    hook.arm(WritePhase.AFTER_RAW_WRITE)
    with pytest.raises(InjectedStorageFault):
        writer.append_trace(make_request(2, channels=channels, frequencies=frequencies))

    with h5py.File(partial_path(scratch_dir), "r") as h5:
        assert h5["/frequency/raw"].shape[0] == 3
    reader = RcScanReader(partial_path(scratch_dir))
    assert reader.committed_record_count == 2
    assert reader.physical_record_count == 2  # min column length (columns stop at 2)
    assert [record.trace_index for record in collect_all(reader, logical=True)] == [0, 1]
    reader.close()


# ---------------------------------------------------------------------------
# Strict open-time validation: versions, checkpoint, corrupt lengths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_version", [3, 1, 2.5])
def test_unknown_schema_version_fail_closed(
    scratch_dir: Path,
    bad_version: object,
) -> None:
    path = bulk_build_rcscan(scratch_dir, [BulkRow(0, _uid_str(0))])
    corrupt_attr(path, "schema_version", bad_version)
    with pytest.raises(DomainError) as caught:
        RcScanReader(path)
    assert caught.value.code is ErrorCode.UNSUPPORTED_SCHEMA_VERSION


def test_unknown_profile_fail_closed(scratch_dir: Path) -> None:
    path = bulk_build_rcscan(scratch_dir, [BulkRow(0, _uid_str(0))])
    corrupt_attr(path, "profile", "other_profile")
    with pytest.raises(DomainError) as caught:
        RcScanReader(path)
    assert caught.value.code is ErrorCode.UNSUPPORTED_SCHEMA_VERSION


@pytest.mark.parametrize(
    "checkpoint_value",
    [99, -1],
)
def test_corrupted_checkpoint_value_fail_closed(
    scratch_dir: Path,
    checkpoint_value: int,
) -> None:
    path = bulk_build_rcscan(
        scratch_dir,
        [BulkRow(0, _uid_str(0)), BulkRow(1, _uid_str(1))],
        checkpoint_override=checkpoint_value,
    )
    with pytest.raises(DomainError) as caught:
        RcScanReader(path)
    assert caught.value.code is ErrorCode.INVALID_ARGUMENT


def test_missing_checkpoint_dataset_fail_closed(scratch_dir: Path) -> None:
    path = bulk_build_rcscan(scratch_dir, [BulkRow(0, _uid_str(0))])
    delete_dataset(path, "/checkpoints/committed_record_count")
    with pytest.raises(DomainError) as caught:
        RcScanReader(path)
    assert caught.value.code is ErrorCode.INVALID_ARGUMENT


def test_corrupted_checkpoint_timestamp_fail_closed(scratch_dir: Path) -> None:
    path = bulk_build_rcscan(scratch_dir, [BulkRow(0, _uid_str(0))])
    corrupt_cell(path, "/checkpoints/updated_utc", 0, "not-a-timestamp")
    with pytest.raises(DomainError) as caught:
        RcScanReader(path)
    assert caught.value.code is ErrorCode.INVALID_ARGUMENT


def test_shortened_required_column_fail_closed(scratch_dir: Path) -> None:
    """A required column physically shorter than the checkpoint is corruption."""
    path = bulk_build_rcscan(
        scratch_dir,
        [BulkRow(0, _uid_str(0)), BulkRow(1, _uid_str(1)), BulkRow(2, _uid_str(2))],
    )
    with h5py.File(path, "r+") as h5:
        h5["/trace_metadata/trace_uid"].resize((2,))
    with pytest.raises(DomainError) as caught:
        RcScanReader(path)
    assert caught.value.code is ErrorCode.INVALID_ARGUMENT


def test_wrong_dtype_fail_closed(scratch_dir: Path) -> None:
    path = bulk_build_rcscan(scratch_dir, [BulkRow(0, _uid_str(0))])
    with h5py.File(path, "r+") as h5:
        del h5["/trace_metadata/connection_generation"]
        h5.create_dataset(
            "/trace_metadata/connection_generation",
            shape=(1,),
            maxshape=(None,),
            dtype="<f8",
            chunks=(1,),
        )
        h5["/trace_metadata/connection_generation"][0] = 1.0
    with pytest.raises(DomainError) as caught:
        RcScanReader(path)
    assert caught.value.code is ErrorCode.INVALID_ARGUMENT


def test_optional_processed_groups_may_be_absent(scratch_dir: Path) -> None:
    """time_base / time_processed / calibrated are optional by contract."""
    path = bulk_build_rcscan(scratch_dir, [BulkRow(0, _uid_str(0)), BulkRow(1, _uid_str(1))])
    with h5py.File(path, "r") as h5:
        assert "/axes/time_base_s" not in h5
        assert "/time_base/data" not in h5
        assert "/time_processed/data" not in h5
        assert "/frequency/calibrated" not in h5
    reader = RcScanReader(path)
    assert reader.probe.optional_axes_present["/axes/time_base_s"] is False
    assert [record.trace_index for record in collect_all(reader, logical=True)] == [0, 1]
    reader.close()


def test_optional_processed_groups_present_are_validated(
    scratch_dir: Path,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
) -> None:
    path = bulk_build_rcscan(scratch_dir, [BulkRow(0, _uid_str(0))])
    with h5py.File(path, "r+") as h5:
        # The ISSUE-008 creator writes fixed axes via ``data=`` (no explicit
        # maxshape), which is the only chunk-less form for a fixed axis.
        h5.create_dataset(
            "/axes/time_base_s",
            data=np.linspace(0.0, 1e-9, _FREQUENCY_POINTS).astype("<f8"),
            dtype="<f8",
        )
        h5.create_dataset(
            "/time_base/data",
            shape=(1, len(channels), _FREQUENCY_POINTS),
            maxshape=(None, len(channels), _FREQUENCY_POINTS),
            dtype="<c16",
            chunks=(1, len(channels), _FREQUENCY_POINTS),
        )
        h5["/time_base/data"][0] = make_raw(
            0, channels=len(channels), frequencies=_FREQUENCY_POINTS
        )
        h5.create_dataset(
            "/time_base/history_json",
            data=np.array(["[]"], dtype=h5py.string_dtype(encoding="utf-8")),
        )
    reader = RcScanReader(path)
    assert reader.probe.optional_axes_present["/axes/time_base_s"] is True
    assert len(collect_all(reader, logical=True)) == 1
    reader.close()


def test_bad_optional_processed_group_dtype_fail_closed(
    scratch_dir: Path,
    channels: tuple[ChannelSpec, ...],
) -> None:
    path = bulk_build_rcscan(scratch_dir, [BulkRow(0, _uid_str(0))])
    with h5py.File(path, "r+") as h5:
        h5.create_dataset(
            "/frequency/calibrated",
            shape=(1, len(channels), _FREQUENCY_POINTS),
            maxshape=(None, len(channels), _FREQUENCY_POINTS),
            dtype="<f8",  # wrong: must be <c16
            chunks=(1, len(channels), _FREQUENCY_POINTS),
        )
    with pytest.raises(DomainError) as caught:
        RcScanReader(path)
    assert caught.value.code is ErrorCode.INVALID_ARGUMENT


# ---------------------------------------------------------------------------
# Duplicate / conflict / hash / row classification
# ---------------------------------------------------------------------------


def test_duplicate_same_hash_is_reported_and_served_once(
    scratch_dir: Path,
) -> None:
    """Same trace_index + uid + raw twice: duplicate, not an error; logical
    view serves exactly one copy (the first committed position)."""
    path = bulk_build_rcscan(
        scratch_dir,
        [
            BulkRow(0, _uid_str(0)),
            BulkRow(1, _uid_str(1)),
            BulkRow(1, _uid_str(1)),  # identical copy
        ],
    )
    reader = RcScanReader(path)
    assert reader.committed_record_count == 3
    physical = collect_all(reader, logical=False)
    assert [record.trace_index for record in physical] == [0, 1, 1]
    logical = collect_all(reader, logical=True)
    assert [record.trace_index for record in logical] == [0, 1]
    assert [record.record_position for record in logical] == [0, 1]
    report = reader.validation_report()
    assert report.duplicates == (
        DuplicateTrace(
            trace_index=1,
            trace_uid=_uid_str(1),
            raw_trace_sha256=physical[1].raw_trace_sha256,
            record_positions=(1, 2),
        ),
    )
    assert report.conflicts == ()
    assert report.issues == ()
    reader.close()


def test_conflicting_hash_is_classified_and_fails_closed_in_logical_view(
    scratch_dir: Path,
) -> None:
    """Same trace_index, different raw data: conflict with retained evidence;
    the logical view never serves an arbitrary copy and lookup fails closed."""
    path = bulk_build_rcscan(
        scratch_dir,
        [
            BulkRow(0, _uid_str(0)),
            BulkRow(1, _uid_str(1), salt=0.0),
            BulkRow(1, _uid_str(1), salt=7.0),  # different raw -> different hash
        ],
    )
    reader = RcScanReader(path)
    report = reader.validation_report()
    assert len(report.conflicts) == 1
    conflict = report.conflicts[0]
    assert isinstance(conflict, ConflictTrace)
    assert conflict.trace_index == 1
    assert conflict.record_positions == (1, 2)
    assert len(conflict.raw_hashes) == 2
    assert conflict.raw_hashes[0] != conflict.raw_hashes[1]
    assert conflict.trace_uids == (_uid_str(1), _uid_str(1))
    assert report.duplicates == ()

    logical = collect_all(reader, logical=True)
    assert [record.trace_index for record in logical] == [0]
    physical = collect_all(reader, logical=False)
    assert [record.trace_index for record in physical] == [0, 1, 1]  # evidence visible

    with pytest.raises(DomainError) as caught:
        reader.trace_by_index(1)
    assert caught.value.code is ErrorCode.ID_CONFLICT
    assert reader.trace_by_index(0).trace_index == 0
    reader.close()


def test_trace_uid_reuse_across_indices_is_conflict_and_excluded(
    scratch_dir: Path,
) -> None:
    """The same trace_uid at two different trace_index values is ambiguous
    identity: both indices are excluded from the logical view and the
    conflict carries both positions."""
    shared = "00000000-0000-4000-8000-0000000000aa"
    path = bulk_build_rcscan(
        scratch_dir,
        [
            BulkRow(0, _uid_str(0)),
            BulkRow(2, shared),
            BulkRow(5, shared),
        ],
    )
    reader = RcScanReader(path)
    report = reader.validation_report()
    assert len(report.conflicts) == 1
    conflict = report.conflicts[0]
    assert conflict.trace_uid == shared
    assert conflict.record_positions == (1, 2)
    assert conflict.trace_uids == (shared, shared)
    assert report.missing == (
        MissingTrace(trace_index=1),
        MissingTrace(trace_index=3),
        MissingTrace(trace_index=4),
    )
    assert [record.trace_index for record in collect_all(reader, logical=True)] == [0]
    with pytest.raises(DomainError) as caught:
        reader.trace_by_index(2)
    assert caught.value.code is ErrorCode.ID_CONFLICT
    reader.close()


def test_hash_mismatch_is_reported_and_row_flagged(
    scratch_dir: Path,
) -> None:
    """A stored hash contradicting the recomputed digest is reported; the row
    is still served with ``hash_verified=False`` (evidence, not silence)."""
    path = bulk_build_rcscan(scratch_dir, [BulkRow(0, _uid_str(0)), BulkRow(1, _uid_str(1))])
    corrupt_cell(path, "/trace_metadata/raw_trace_sha256", 1, "a" * 64)
    reader = RcScanReader(path)
    records = collect_all(reader, logical=True)
    assert records[0].hash_verified is True
    assert records[1].hash_verified is False
    report = reader.validation_report()
    assert len(report.issues) == 1
    issue = report.issues[0]
    assert issue.kind is IssueKind.HASH_MISMATCH
    assert issue.record_position == 1
    assert issue.trace_index == 1
    assert issue.trace_uid == _uid_str(1)
    reader.close()


def test_missing_stored_hash_is_reported(scratch_dir: Path) -> None:
    path = bulk_build_rcscan(scratch_dir, [BulkRow(0, _uid_str(0)), BulkRow(1, _uid_str(1))])
    corrupt_cell(path, "/trace_metadata/raw_trace_sha256", 1, "")
    reader = RcScanReader(path)
    report = reader.validation_report()
    assert any(issue.kind is IssueKind.MISSING_HASH for issue in report.issues)
    reader.close()


def test_undecodable_row_is_reported_and_skipped_in_both_views(
    scratch_dir: Path,
) -> None:
    """A row whose cells cannot rebuild a domain object is reported and never
    served (a row without valid identity is not a complete record)."""
    path = bulk_build_rcscan(
        scratch_dir,
        [BulkRow(0, _uid_str(0)), BulkRow(1, _uid_str(1)), BulkRow(2, _uid_str(2))],
    )
    corrupt_cell(path, "/trace_metadata/trace_uid", 1, "not-a-uuid")
    reader = RcScanReader(path)
    physical = collect_all(reader, logical=False)
    assert [record.trace_index for record in physical] == [0, 2]
    logical = collect_all(reader, logical=True)
    assert [record.trace_index for record in logical] == [0, 2]
    report = reader.validation_report()
    assert any(
        issue.kind is IssueKind.ROW_DECODE_ERROR and issue.record_position == 1
        for issue in report.issues
    )
    reader.close()


def test_invalid_trace_index_cell_is_reported(scratch_dir: Path) -> None:
    path = bulk_build_rcscan(scratch_dir, [BulkRow(0, _uid_str(0))])
    corrupt_cell(path, "/trace_metadata/trace_index", 0, -5)
    reader = RcScanReader(path)
    report = reader.validation_report()
    assert any(issue.kind is IssueKind.ROW_DECODE_ERROR for issue in report.issues)
    assert collect_all(reader, logical=True) == ()
    reader.close()


def test_last_trace_index_inconsistency_is_reported(scratch_dir: Path) -> None:
    """last_trace_index below the actual max committed index is a checkpoint
    anomaly: opening succeeds, the report flags it."""
    path = bulk_build_rcscan(
        scratch_dir,
        [BulkRow(0, _uid_str(0)), BulkRow(1, _uid_str(1)), BulkRow(3, _uid_str(3))],
        last_index_override=1,
    )
    reader = RcScanReader(path)
    report = reader.validation_report()
    assert any(issue.kind is IssueKind.CHECKPOINT_INCONSISTENCY for issue in report.issues)
    assert [record.trace_index for record in collect_all(reader, logical=True)] == [0, 1, 3]
    reader.close()


def test_trace_by_index_raises_for_missing_index(
    scratch_dir: Path,
) -> None:
    path = bulk_build_rcscan(scratch_dir, [BulkRow(0, _uid_str(0)), BulkRow(2, _uid_str(2))])
    reader = RcScanReader(path)
    assert reader.trace_by_index(2).trace_index == 2
    with pytest.raises(DomainError) as caught:
        reader.trace_by_index(1)
    assert caught.value.code is ErrorCode.INVALID_ARGUMENT
    reader.close()


# ---------------------------------------------------------------------------
# awaiting_rename presentation and read-only guarantee
# ---------------------------------------------------------------------------


def test_awaiting_rename_partial_is_presented_as_finalized(
    scratch_dir: Path,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
    clock: ManualClock,
) -> None:
    """A finalized partial whose rename failed must read as a completed task
    (lifecycle=finalized, completion_kind set, rename_pending=True), never as
    an ordinary unfinished (writing) mission."""

    class _FlakyRename(FileSystemFacade):
        def exists(self, path: Path) -> bool:
            return path.exists()

        def replace(self, source: Path, target: Path) -> None:
            raise OSError("injected rename failure")

    writer = create_writer(
        scratch_dir,
        role=EndpointRole.AIR,
        channels=channels,
        frequencies=frequencies,
        mission_config=mission_config,
        clock=clock,
        filesystem=_FlakyRename(),
    )
    for index in range(3):
        writer.append_trace(make_request(index, channels=channels, frequencies=frequencies))
    with pytest.raises(OSError):
        writer.close(MissionTerminalState.COMPLETED)

    partial = partial_path(scratch_dir)
    assert partial.exists()
    assert not final_path(scratch_dir).exists()

    reader = RcScanReader(partial)
    assert reader.lifecycle_state == "finalized"
    assert reader.completion_kind == "completed"
    assert reader.rename_pending is True
    assert reader.committed_record_count == 3
    assert [record.trace_index for record in collect_all(reader, logical=True)] == [0, 1, 2]
    reader.close()


def test_reader_is_strictly_read_only(scratch_dir: Path) -> None:
    """Reading (both views + validation report) never mutates the file."""
    path = bulk_build_rcscan(
        scratch_dir,
        [BulkRow(0, _uid_str(0)), BulkRow(1, _uid_str(1)), BulkRow(2, _uid_str(2))],
    )
    before = file_sha256(path)
    reader = RcScanReader(path)
    collect_all(reader, logical=False)
    collect_all(reader, logical=True)
    reader.validation_report()
    reader.trace_by_index(1)
    reader.close()
    after = file_sha256(path)
    assert before == after


# ---------------------------------------------------------------------------
# Lazy / chunked iteration over a large synthetic file
# ---------------------------------------------------------------------------


def test_large_file_chunked_iteration_is_correct_and_bounded(scratch_dir: Path) -> None:
    """10_000 traces iterate through both views in bounded chunks; every
    chunk is at most ``chunk_rows`` records and the totals agree."""
    count = 10_000
    rows = [BulkRow(index, _uid_str(index), with_gnss=index % 2 == 0) for index in range(count)]
    path = bulk_build_rcscan(scratch_dir, rows)
    chunk_rows = 64

    reader = RcScanReader(path)
    assert reader.committed_record_count == count
    assert reader.physical_record_count == count

    physical_sizes = as_chunk_sizes(reader, logical=False, chunk_rows=chunk_rows)
    logical_sizes = as_chunk_sizes(reader, logical=True, chunk_rows=chunk_rows)
    assert all(0 < size <= chunk_rows for size in physical_sizes)
    assert all(0 < size <= chunk_rows for size in logical_sizes)
    assert sum(physical_sizes) == count
    assert sum(logical_sizes) == count
    assert len(physical_sizes) == (count + chunk_rows - 1) // chunk_rows
    assert len(logical_sizes) == (count + chunk_rows - 1) // chunk_rows

    seen = 0
    for chunk in reader.iter_logical(chunk_rows=chunk_rows):
        assert isinstance(chunk, TraceChunk)
        assert chunk.start_position <= chunk.stop_position
        assert len(chunk.records) == chunk.stop_position - chunk.start_position
        assert len(chunk.records) <= chunk_rows
        previous = None
        for record in chunk.records:
            assert record.trace_index == seen
            assert record.hash_verified is True
            if previous is not None:
                assert record.record_position > previous
            previous = record.record_position
            seen += 1
    assert seen == count

    report = reader.validation_report()
    assert report.committed_record_count == count
    assert report.missing == ()
    assert report.duplicates == ()
    assert report.conflicts == ()
    assert report.issues == ()
    reader.close()


def test_chunk_boundaries_are_continuous(scratch_dir: Path) -> None:
    count = 100
    rows = [BulkRow(index, _uid_str(index)) for index in range(count)]
    path = bulk_build_rcscan(scratch_dir, rows)
    reader = RcScanReader(path)
    chunk_rows = 37
    chunks = list(reader.iter_physical(chunk_rows=chunk_rows))
    assert [len(chunk.records) for chunk in chunks] == [37, 37, 26]
    positions = [record.record_position for chunk in chunks for record in chunk.records]
    assert positions == list(range(count))
    reader.close()


# ---------------------------------------------------------------------------
# Validator surface
# ---------------------------------------------------------------------------


def test_validate_rcscan_v2_returns_report_without_keeping_reader(
    scratch_dir: Path,
) -> None:
    path = bulk_build_rcscan(
        scratch_dir,
        [BulkRow(0, _uid_str(0)), BulkRow(1, _uid_str(1)), BulkRow(1, _uid_str(1))],
    )
    report = RcScanValidator.validate(path)
    assert isinstance(report, ValidationReport)
    assert report.committed_record_count == 3
    assert len(report.duplicates) == 1
    assert report.to_dict()["committed_record_count"] == 3
    summary = report.summary()
    assert summary["duplicates"] == 1
    assert summary["conflicts"] == 0


def test_validate_rcscan_v2_propagates_schema_failure(scratch_dir: Path) -> None:
    path = bulk_build_rcscan(scratch_dir, [BulkRow(0, _uid_str(0))])
    corrupt_attr(path, "schema_version", 9)
    with pytest.raises(DomainError) as caught:
        RcScanValidator.validate(path)
    assert caught.value.code is ErrorCode.UNSUPPORTED_SCHEMA_VERSION
