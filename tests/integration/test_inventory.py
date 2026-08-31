"""ISSUE-014 integration tests: air-ground inventory and per-trace consistency.

This suite pins the ISSUE-014 ``MissionInventory`` service contract on top of
ISSUE-011 reader files (built with the frozen ISSUE-008/009/010 codecs):

- cross-side classification: missing / extra / consistent / conflict
  (trace_uid or raw hash mismatch) / gnss_diff (raw identity intact);
- physical row order never affects the result; duplicate-same is distinct
  from different-hash conflict; ground-only processed groups never cause raw
  conflicts;
- mission/config/axis/channel contract checks; stable deterministic report
  serialization; paginated streaming over 100_000 traces.

Everything is synthetic; no sleep, no hardware, no network, no reference
projects.  Fixture files are built with the authoritative row codec
(``trace_metadata_to_cells``) and the ISSUE-009 canonical raw hash, mirroring
the ISSUE-011 reader test builder but parameterized (role / mission / channels
/ axis / processed groups) so air and ground sides can diverge on purpose.
"""

from __future__ import annotations

import json
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
from uav_gpr.core.timeutil import MonotonicNs, to_utc_iso
from uav_gpr.storage import rcscan_v2 as schema
from uav_gpr.storage.incremental_writer import PARTIAL_SUFFIX
from uav_gpr.storage.inventory import InventoryItemKind, MissionInventory
from uav_gpr.storage.rcscan_reader import RcScanReader

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Frozen synthetic constants (mirrors the ISSUE-010/011 test constants)
# ---------------------------------------------------------------------------

_FREQUENCY_POINTS = 16
_MISSION_ID = MissionId("0f0e8a3b-6f2d-4c1e-9a7b-112233445566")
_MISSION_ID_OTHER = MissionId("0f0e8a3b-6f2d-4c1e-9a7b-998877665544")
_DEVICE_ID = DeviceId("d1c0ffee-0000-4000-8000-000000000001")
_AIR_FILE_ID = AirFileId("aaaaaaa1-0000-4000-8000-000000000002")
_GROUND_FILE_ID = GroundFileId("aaaaaaa2-0000-4000-8000-000000000002")
_CREATED_UTC = datetime(2026, 8, 30, 9, 0, 0, tzinfo=UTC)
_WRITER_VERSION = "uav-gpr.test.1"
_CHUNK = 4096


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


def build_single_channel() -> tuple[ChannelSpec, ...]:
    return (
        ChannelSpec(
            channel_id="hh_s11",
            logical_polarization=LogicalPolarization.HH,
            s_parameter=SParameter.S11,
            display_name="H height S11",
        ),
    )


def build_mission_config(
    channels: tuple[ChannelSpec, ...],
    *,
    frequency_points: int = _FREQUENCY_POINTS,
    frequency_stop_hz: float = 2600e6,
) -> MissionConfig:
    return MissionConfig(
        frequency_start_hz=800e6,
        frequency_stop_hz=frequency_stop_hz,
        frequency_points=frequency_points,
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


def make_gnss_match(
    midpoint: datetime,
    monotonic_ns: int,
    *,
    valid: bool = True,
    latitude_deg: float = 30.123456,
) -> GnssMatch:
    if valid:
        fix = GnssFix(
            received_utc=midpoint,
            nmea_utc=midpoint,
            received_monotonic_ns=MonotonicNs(monotonic_ns),
            latitude_deg=latitude_deg,
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
    mission_id: MissionId,
    trace_uid: TraceUid,
    with_gnss: bool = True,
    gnss_latitude_deg: float | None = None,
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
        make_gnss_match(
            midpoint,
            monotonic + 50_000_000,
            valid=True,
            latitude_deg=gnss_latitude_deg if gnss_latitude_deg is not None else 30.123456,
        )
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
        mission_id=mission_id,
        trace_index=index,
        trace_uid=trace_uid,
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


@dataclass(frozen=True)
class Row:
    """One logical row for the fast parameterized builder."""

    trace_index: int
    trace_uid: str
    salt: float = 0.0
    with_gnss: bool = True
    gnss_latitude_deg: float | None = None


def build_file(
    scratch_dir: Path,
    rows: Sequence[Row],
    *,
    role: EndpointRole = EndpointRole.AIR,
    mission_id: MissionId = _MISSION_ID,
    channels: tuple[ChannelSpec, ...] | None = None,
    config: MissionConfig | None = None,
    with_processed: bool = False,
    lifecycle: str = "finalized",
) -> Path:
    """Build a v2 file fast by writing whole column chunks (test-only).

    Uses the same frozen schema creator and the same authoritative row codec
    as the ISSUE-010 writer; the difference is only write throughput.
    ``with_processed`` additionally writes valid optional processed groups
    (calibrated frequency, time_base, time_processed) as a ground side may
    legitimately carry them.
    """
    channels_tuple = build_channels() if channels is None else tuple(channels)
    config = build_mission_config(channels_tuple) if config is None else config
    axis = np.asarray(config.frequency_axis_hz, dtype="<f8")
    file_id = _AIR_FILE_ID if role is EndpointRole.AIR else _GROUND_FILE_ID
    partial = scratch_dir / f"{file_id}-{mission_id}{PARTIAL_SUFFIX}"
    schema.create_rcscan_v2(
        partial,
        mission_id=mission_id,
        device_id=_DEVICE_ID,
        file_id=file_id,
        created_utc=_CREATED_UTC,
        completed_utc=None,
        completion_kind=None,
        file_role=role,
        channels=channels_tuple,
        frequencies_hz=axis,
        config_json=config.to_canonical_json(),
        config_sha256=config.config_sha256,
        writer_version=_WRITER_VERSION,
    )
    contracts = schema.dataset_contracts(len(channels_tuple), int(axis.size))
    row_paths = [
        contract.path
        for contract in contracts
        if not contract.optional
        and contract.path.startswith(
            ("/trace_metadata/", "/gnss/", "/acquisition/", "/transport/")
        )
        and (not contract.path.startswith("/transport") or role is EndpointRole.AIR)
    ]
    channel_count = len(channels_tuple)
    frequency_count = int(axis.size)
    total = len(rows)
    with h5py.File(partial, "r+") as h5:
        for start in range(0, total, _CHUNK):
            stop = min(start + _CHUNK, total)
            column_values: dict[str, list[object]] = {}
            raw_rows: list[np.ndarray] = []
            for row in rows[start:stop]:
                metadata = make_metadata(
                    row.trace_index,
                    mission_id=mission_id,
                    trace_uid=TraceUid(uuid.UUID(row.trace_uid)),
                    with_gnss=row.with_gnss,
                    gnss_latitude_deg=row.gnss_latitude_deg,
                )
                raw = make_raw(
                    row.trace_index,
                    channels=channel_count,
                    frequencies=frequency_count,
                    salt=row.salt,
                )
                digest = compute_raw_trace_sha256(
                    mission_id=mission_id,
                    trace_index=row.trace_index,
                    trace_uid=metadata.trace_uid,
                    channels=channels_tuple,
                    frequencies_hz=axis,
                    data=raw,
                )
                cells = schema.trace_metadata_to_cells(metadata.with_integrity(digest))
                for path, value in cells.items():
                    column_values.setdefault(path, []).append(value)
                raw_rows.append(raw)
            for path in row_paths:
                dataset = h5[path]
                new_count = dataset.shape[0] + (stop - start)
                dataset.resize((new_count,))
                values = np.array(column_values[path], dtype=dataset.dtype)
                dataset[start:stop] = values
            raw_dataset = h5["/frequency/raw"]
            raw_dataset.resize(
                (
                    raw_dataset.shape[0] + (stop - start),
                    channel_count,
                    frequency_count,
                )
            )
            raw_dataset[start:stop] = np.stack(raw_rows)
        committed = total
        h5["/checkpoints/committed_record_count"][0] = np.int64(committed)
        last_index = max((row.trace_index for row in rows), default=schema.MISSING_INT64)
        h5["/checkpoints/last_trace_index"][0] = np.int64(last_index)
        h5["/checkpoints/updated_utc"][0] = to_utc_iso(_CREATED_UTC + timedelta(seconds=total))
        mission = h5["mission"]
        mission.attrs["started_utc"] = to_utc_iso(_CREATED_UTC)
        if lifecycle == "writing":
            mission.attrs["ended_utc"] = ""
            mission.attrs["completion_kind"] = ""
            h5.attrs["lifecycle_state"] = "writing"
        else:
            mission.attrs["ended_utc"] = to_utc_iso(_CREATED_UTC + timedelta(seconds=total))
            mission.attrs["completion_kind"] = "completed"
            h5.attrs["lifecycle_state"] = lifecycle
        if with_processed:
            _add_processed_groups(h5, total, channel_count, frequency_count)
    final = scratch_dir / f"{role.value}-{mission_id}.rcscan"
    os.replace(partial, final)
    return final


def _add_processed_groups(
    h5: h5py.File,
    count: int,
    channel_count: int,
    frequency_count: int,
) -> None:
    """Add valid optional processed groups (ground-side extras)."""
    h5.create_dataset(
        "/frequency/calibrated",
        shape=(count, channel_count, frequency_count),
        maxshape=(None, channel_count, frequency_count),
        dtype="<c16",
        chunks=(1, channel_count, frequency_count),
    )
    h5.create_dataset(
        "/axes/time_base_s",
        data=np.linspace(0.0, 1e-9, frequency_count).astype("<f8"),
        dtype="<f8",
    )
    h5.create_dataset(
        "/time_base/data",
        shape=(count, channel_count, frequency_count),
        maxshape=(None, channel_count, frequency_count),
        dtype="<c16",
        chunks=(1, channel_count, frequency_count),
    )
    h5.create_dataset(
        "/time_base/history_json",
        data=np.array(["[]"], dtype=h5py.string_dtype(encoding="utf-8")),
    )
    h5.create_dataset(
        "/axes/time_processed_s",
        data=np.linspace(0.0, 1e-9, frequency_count).astype("<f8"),
        dtype="<f8",
    )
    h5.create_dataset(
        "/time_processed/data",
        shape=(count, channel_count, frequency_count),
        maxshape=(None, channel_count, frequency_count),
        dtype="<c16",
        chunks=(1, channel_count, frequency_count),
    )
    h5.create_dataset(
        "/time_processed/history_json",
        data=np.array(["[]"], dtype=h5py.string_dtype(encoding="utf-8")),
    )


def rows_for(
    count: int,
    *,
    drop: set[int] | None = None,
    salt: float = 0.0,
    with_gnss: bool = True,
) -> list[Row]:
    """Deterministic row list for indices ``0..count-1`` minus ``drop``."""
    drop = drop or set()
    return [
        Row(index, _uid_str(index), salt=salt, with_gnss=with_gnss)
        for index in range(count)
        if index not in drop
    ]


def open_pair(
    air_path: Path,
    ground_path: Path,
    *,
    page_size: int = 1000,
) -> tuple[MissionInventory, RcScanReader, RcScanReader]:
    air = RcScanReader(air_path)
    ground = RcScanReader(ground_path)
    return MissionInventory(air, ground, page_size=page_size), air, ground


# ---------------------------------------------------------------------------
# Baseline: fully matching air and ground files
# ---------------------------------------------------------------------------


def test_matching_files_are_fully_consistent(scratch_dir: Path) -> None:
    count = 50
    rows = rows_for(count)
    air_path = build_file(scratch_dir, rows, role=EndpointRole.AIR)
    ground_path = build_file(scratch_dir, rows, role=EndpointRole.GROUND)
    inventory, air, ground = open_pair(air_path, ground_path)

    contract = inventory.contract()
    assert contract.mission_id_match is True
    assert contract.channels_match is True
    assert contract.frequencies_match is True
    assert contract.config_match is True
    assert contract.issues == ()

    summary = inventory.summary()
    assert summary.matched == count
    assert summary.missing == 0
    assert summary.extra == 0
    assert summary.conflicts == 0
    assert summary.gnss_diffs == 0
    assert summary.air_traces == count
    assert summary.ground_traces == count

    items = list(inventory.iter_items())
    assert len(items) == count
    assert all(item.kind is InventoryItemKind.CONSISTENT for item in items)
    assert [item.trace_index for item in items] == list(range(count))
    for item in items:
        assert item.air_trace_uid == item.ground_trace_uid
        assert item.air_raw_sha256 == item.ground_raw_sha256
        assert item.detail is None
    air.close()
    ground.close()


# ---------------------------------------------------------------------------
# Physical row order independence
# ---------------------------------------------------------------------------


def test_out_of_order_physical_records_do_not_affect_result(scratch_dir: Path) -> None:
    count = 60
    ordered = rows_for(count)
    # Air written in scrambled physical order; ground in ascending order.
    scrambled = [ordered[2 * i] for i in range(count // 2)] + [
        ordered[2 * i + 1] for i in range(count // 2)
    ]
    air_path = build_file(scratch_dir, scrambled, role=EndpointRole.AIR)
    ground_path = build_file(scratch_dir, ordered, role=EndpointRole.GROUND)
    inventory, air, ground = open_pair(air_path, ground_path)

    summary = inventory.summary()
    assert summary.matched == count
    assert summary.missing == 0
    assert summary.extra == 0
    assert summary.conflicts == 0
    assert [item.trace_index for item in inventory.iter_items()] == list(range(count))
    air.close()
    ground.close()


# ---------------------------------------------------------------------------
# Missing / extra classification
# ---------------------------------------------------------------------------


def test_missing_and_extra_traces_are_classified(scratch_dir: Path) -> None:
    count = 40
    air_rows = rows_for(count)
    # Ground lacks indices {3, 17, 39} but carries one extra index (100).
    ground_rows = rows_for(count, drop={3, 17, 39})
    ground_rows.append(Row(100, _uid_str(100)))
    air_path = build_file(scratch_dir, air_rows, role=EndpointRole.AIR)
    ground_path = build_file(scratch_dir, ground_rows, role=EndpointRole.GROUND)
    inventory, air, ground = open_pair(air_path, ground_path)

    summary = inventory.summary()
    assert summary.matched == count - 3
    assert summary.missing == 3
    assert summary.extra == 1
    assert summary.conflicts == 0

    missing = [item for item in inventory.iter_items(kind=InventoryItemKind.MISSING)]
    assert [item.trace_index for item in missing] == [3, 17, 39]
    for item in missing:
        assert item.air_trace_uid is not None
        assert item.ground_trace_uid is None
        assert item.air_raw_sha256 is not None
        assert item.ground_raw_sha256 is None

    extra = [item for item in inventory.iter_items(kind=InventoryItemKind.EXTRA)]
    assert [item.trace_index for item in extra] == [100]
    assert extra[0].ground_trace_uid is not None
    assert extra[0].air_trace_uid is None
    air.close()
    ground.close()


# ---------------------------------------------------------------------------
# Duplicate-same vs different-hash conflict
# ---------------------------------------------------------------------------


def test_duplicate_same_hash_is_not_a_conflict(scratch_dir: Path) -> None:
    """Same index+uid+hash written twice in one file: duplicate (collapsed by
    the reader), never a cross-side conflict."""
    air_rows = rows_for(10)
    ground_rows = rows_for(10)
    # Duplicate the ground row 5 (identical identity and raw bytes).
    ground_rows.insert(6, Row(5, _uid_str(5)))
    air_path = build_file(scratch_dir, air_rows, role=EndpointRole.AIR)
    ground_path = build_file(scratch_dir, ground_rows, role=EndpointRole.GROUND)
    inventory, air, ground = open_pair(air_path, ground_path)

    summary = inventory.summary()
    assert summary.matched == 10
    assert summary.conflicts == 0
    assert summary.ground_duplicates == 1
    assert summary.air_duplicates == 0
    assert summary.ground_conflicts == 0
    air.close()
    ground.close()


def test_conflicting_raw_hash_is_fail_closed(scratch_dir: Path) -> None:
    """Same index + uid but different raw bytes on the ground side: conflict
    with both hashes retained as evidence; no winner is chosen."""
    count = 12
    air_rows = rows_for(count)
    ground_rows = [
        Row(index, _uid_str(index), salt=(0.5 if index == 7 else 0.0))
        for index in range(count)
    ]
    air_path = build_file(scratch_dir, air_rows, role=EndpointRole.AIR)
    ground_path = build_file(scratch_dir, ground_rows, role=EndpointRole.GROUND)
    inventory, air, ground = open_pair(air_path, ground_path)

    summary = inventory.summary()
    assert summary.matched == count - 1
    assert summary.conflicts == 1
    assert summary.missing == 0
    assert summary.extra == 0

    conflicts = [item for item in inventory.iter_items(kind=InventoryItemKind.CONFLICT)]
    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict.trace_index == 7
    assert conflict.detail == "raw_trace_sha256 mismatch"
    assert conflict.air_raw_sha256 != conflict.ground_raw_sha256
    assert conflict.air_trace_uid == conflict.ground_trace_uid
    air.close()
    ground.close()


def test_trace_uid_mismatch_is_a_conflict(scratch_dir: Path) -> None:
    """Same index but different trace_uid: identity conflict, fail-closed."""
    count = 10
    air_rows = rows_for(count)
    ground_rows = [
        Row(index, _uid_str(index + 1000) if index == 4 else _uid_str(index))
        for index in range(count)
    ]
    air_path = build_file(scratch_dir, air_rows, role=EndpointRole.AIR)
    ground_path = build_file(scratch_dir, ground_rows, role=EndpointRole.GROUND)
    inventory, air, ground = open_pair(air_path, ground_path)

    summary = inventory.summary()
    assert summary.conflicts == 1
    conflicts = [item for item in inventory.iter_items(kind=InventoryItemKind.CONFLICT)]
    assert conflicts[0].trace_index == 4
    assert conflicts[0].detail == "trace_uid mismatch"
    assert conflicts[0].air_trace_uid != conflicts[0].ground_trace_uid
    air.close()
    ground.close()


def test_intra_file_conflict_index_is_surfaced_not_misclassified(scratch_dir: Path) -> None:
    """Air side has two different hashes at index 5 (intra-file conflict): the
    reader excludes it from the logical view, and the inventory must report a
    conflict instead of silently classifying ground's copy as extra."""
    air_rows = rows_for(10)
    air_rows.insert(6, Row(5, _uid_str(5), salt=0.75))  # conflicting copy at index 5
    ground_rows = rows_for(10)
    air_path = build_file(scratch_dir, air_rows, role=EndpointRole.AIR)
    ground_path = build_file(scratch_dir, ground_rows, role=EndpointRole.GROUND)
    inventory, air, ground = open_pair(air_path, ground_path)

    summary = inventory.summary()
    assert summary.air_conflicts == 1
    assert summary.conflicts == 1
    assert summary.extra == 0
    conflicts = [item for item in inventory.iter_items(kind=InventoryItemKind.CONFLICT)]
    assert conflicts[0].trace_index == 5
    assert "air" in conflicts[0].detail
    air.close()
    ground.close()


# ---------------------------------------------------------------------------
# GNSS differences are reported separately, never as raw conflicts
# ---------------------------------------------------------------------------


def test_gnss_differences_are_reported_separately(scratch_dir: Path) -> None:
    count = 10
    air_rows = rows_for(count)
    # Ground side: index 2 lost its GNSS fix entirely; index 5 has a different
    # latitude (raw bytes unchanged).
    ground_rows = [
        Row(
            index,
            _uid_str(index),
            with_gnss=(index != 2),
            gnss_latitude_deg=(31.0 if index == 5 else None),
        )
        for index in range(count)
    ]
    air_path = build_file(scratch_dir, air_rows, role=EndpointRole.AIR)
    ground_path = build_file(scratch_dir, ground_rows, role=EndpointRole.GROUND)
    inventory, air, ground = open_pair(air_path, ground_path)

    summary = inventory.summary()
    assert summary.matched == count
    assert summary.conflicts == 0
    assert summary.gnss_diffs == 2

    diffs = [item for item in inventory.iter_items(kind=InventoryItemKind.GNSS_DIFF)]
    assert [item.trace_index for item in diffs] == [2, 5]
    assert "fix" in diffs[1].detail
    air.close()
    ground.close()


# ---------------------------------------------------------------------------
# Ground-only processed/transport fields never cause raw conflicts
# ---------------------------------------------------------------------------


def test_ground_processed_groups_do_not_cause_raw_conflicts(scratch_dir: Path) -> None:
    count = 20
    rows = rows_for(count)
    air_path = build_file(scratch_dir, rows, role=EndpointRole.AIR)
    ground_path = build_file(
        scratch_dir, rows, role=EndpointRole.GROUND, with_processed=True
    )
    inventory, air, ground = open_pair(air_path, ground_path)

    summary = inventory.summary()
    assert summary.matched == count
    assert summary.conflicts == 0
    assert summary.gnss_diffs == 0
    contract = inventory.contract()
    assert contract.mission_id_match and contract.channels_match
    assert contract.frequencies_match and contract.config_match
    # The processed groups must be readable on the ground side.
    assert ground.probe.optional_axes_present["/axes/time_base_s"] is True
    air.close()
    ground.close()


# ---------------------------------------------------------------------------
# Contract checks
# ---------------------------------------------------------------------------


def test_contract_mismatches_are_reported(scratch_dir: Path) -> None:
    rows = rows_for(8)
    air_path = build_file(scratch_dir, rows, role=EndpointRole.AIR)
    ground_rows = rows_for(8)
    ground_path = build_file(
        scratch_dir,
        ground_rows,
        role=EndpointRole.GROUND,
        mission_id=_MISSION_ID_OTHER,
    )
    inventory, air, ground = open_pair(air_path, ground_path)

    contract = inventory.contract()
    assert contract.mission_id_match is False
    assert contract.channels_match is True
    assert contract.frequencies_match is True
    assert contract.config_match is True
    fields = {issue.field for issue in contract.issues}
    assert fields == {"mission_id"}
    # Comparison still ran and produced raw-hash conflicts (fail-closed:
    # hashes are framed under different mission ids).
    assert inventory.summary().conflicts == 8
    air.close()
    ground.close()


def test_channel_and_axis_mismatches_are_reported(scratch_dir: Path) -> None:
    rows = rows_for(8)
    air_path = build_file(scratch_dir, rows, role=EndpointRole.AIR)
    single = build_single_channel()
    single_config = build_mission_config(single, frequency_points=32)
    ground_path = build_file(
        scratch_dir,
        rows,
        role=EndpointRole.GROUND,
        channels=single,
        config=single_config,
    )
    inventory, air, ground = open_pair(air_path, ground_path)

    contract = inventory.contract()
    assert contract.mission_id_match is True
    assert contract.channels_match is False
    assert contract.frequencies_match is False
    assert contract.config_match is False
    fields = {issue.field for issue in contract.issues}
    assert fields == {"channels", "frequencies", "config"}
    air.close()
    ground.close()


# ---------------------------------------------------------------------------
# Pagination and streaming over a large mission
# ---------------------------------------------------------------------------


def test_large_mission_pagination_is_bounded_and_deterministic(scratch_dir: Path) -> None:
    """100_000 air traces against 99_950 ground traces (50 missing): summary
    counts are exact, pages are bounded, replay is deterministic."""
    count = 100_000
    dropped = {index for index in range(0, count, 2_000)}  # 50 indices
    air_rows = rows_for(count)
    ground_rows = rows_for(count, drop=dropped)
    air_path = build_file(scratch_dir, air_rows, role=EndpointRole.AIR)
    ground_path = build_file(scratch_dir, ground_rows, role=EndpointRole.GROUND)
    inventory, air, ground = open_pair(air_path, ground_path, page_size=10_000)

    summary = inventory.summary()
    assert summary.air_traces == count
    assert summary.ground_traces == count - 50
    assert summary.matched == count - 50
    assert summary.missing == 50
    assert summary.extra == 0
    assert summary.conflicts == 0
    assert summary.gnss_diffs == 0

    # Full streaming pass totals.
    total = sum(1 for _ in inventory.iter_items())
    assert total == count

    # Bounded pages with correct has_more flags.
    page = inventory.page(0)
    assert len(page.items) == page.page_size == 10_000
    assert page.has_more is True
    page9 = inventory.page(9)
    assert len(page9.items) == 10_000
    assert page9.has_more is False  # items 90_000..99_999 are the last 10_000
    page10 = inventory.page(10)
    assert len(page10.items) == 0
    assert page10.has_more is False

    # Replay determinism: identical page content on a second pass.
    again = inventory.page(0)
    assert [item.to_dict() for item in page.items] == [item.to_dict() for item in again.items]

    # Kind-filtered paging over the anomaly list (missing_request semantics).
    missing_page = inventory.page(0, kind=InventoryItemKind.MISSING)
    assert len(missing_page.items) == 50
    assert missing_page.has_more is False
    assert [item.trace_index for item in missing_page.items] == sorted(dropped)
    air.close()
    ground.close()


# ---------------------------------------------------------------------------
# Report serialization
# ---------------------------------------------------------------------------


def test_report_serialization_is_deterministic(scratch_dir: Path) -> None:
    air_rows = rows_for(12)
    ground_rows = [
        Row(index, _uid_str(index), with_gnss=(index != 3)) for index in range(12)
    ]
    air_path = build_file(scratch_dir, air_rows, role=EndpointRole.AIR)
    ground_path = build_file(scratch_dir, ground_rows, role=EndpointRole.GROUND)
    inventory, air, ground = open_pair(air_path, ground_path, page_size=5)

    first = inventory.to_dict()
    second = inventory.to_dict()
    assert first == second
    assert first["report_format"] == "uav_gpr_air_ground_inventory"
    assert first["report_version"] == 1
    assert list(first.keys()) == list(second.keys())
    text = json.dumps(first, sort_keys=False)
    assert json.loads(text) == first
    assert json.dumps(first) == json.dumps(second)
    kinds = {item["kind"] for item in first["items"]}
    assert kinds == {"gnss_diff"}
    assert first["summary"]["gnss_diffs"] == 1
    air.close()
    ground.close()


# ---------------------------------------------------------------------------
# Fail-closed argument validation and empty-side edges
# ---------------------------------------------------------------------------


def test_invalid_page_arguments_fail_closed(scratch_dir: Path) -> None:
    air_path = build_file(scratch_dir, rows_for(4), role=EndpointRole.AIR)
    ground_path = build_file(scratch_dir, rows_for(4), role=EndpointRole.GROUND)
    with pytest.raises(DomainError) as page_size_zero:
        MissionInventory(RcScanReader(air_path), RcScanReader(ground_path), page_size=0)
    assert page_size_zero.value.code is ErrorCode.INVALID_ARGUMENT
    with pytest.raises(DomainError) as page_size_bool:
        MissionInventory(RcScanReader(air_path), RcScanReader(ground_path), page_size=True)
    assert page_size_bool.value.code is ErrorCode.INVALID_ARGUMENT

    inventory = MissionInventory(RcScanReader(air_path), RcScanReader(ground_path))
    with pytest.raises(DomainError) as negative_page:
        inventory.page(-1)
    assert negative_page.value.code is ErrorCode.INVALID_ARGUMENT
    with pytest.raises(DomainError) as bool_page:
        inventory.page(True)
    assert bool_page.value.code is ErrorCode.INVALID_ARGUMENT
    with pytest.raises(DomainError) as bad_kind:
        inventory.page(0, kind="not-a-kind")
    assert bad_kind.value.code is ErrorCode.INVALID_ARGUMENT


def test_empty_side_edges(scratch_dir: Path) -> None:
    air_path = build_file(scratch_dir, [], role=EndpointRole.AIR)
    ground_path = build_file(scratch_dir, rows_for(5), role=EndpointRole.GROUND)
    inventory, air, ground = open_pair(air_path, ground_path)
    summary = inventory.summary()
    assert summary.air_traces == 0
    assert summary.ground_traces == 5
    assert summary.matched == 0
    assert summary.missing == 0
    assert summary.extra == 5
    extra_items = list(inventory.iter_items(kind=InventoryItemKind.EXTRA))
    assert [item.trace_index for item in extra_items] == list(range(5))
    air.close()
    ground.close()

    # Both sides empty: zero counts, no items.  (Readers above are closed so
    # the same file names can be rebuilt in the same scratch dir.)
    empty_air_path = build_file(scratch_dir, [], role=EndpointRole.AIR)
    empty_ground_path = build_file(scratch_dir, [], role=EndpointRole.GROUND)
    empty = MissionInventory(
        RcScanReader(empty_air_path), RcScanReader(empty_ground_path)
    )
    empty_summary = empty.summary()
    assert empty_summary.matched == 0
    assert empty_summary.missing == 0
    assert empty_summary.extra == 0
    assert empty_summary.conflicts == 0
    assert list(empty.iter_items()) == []
