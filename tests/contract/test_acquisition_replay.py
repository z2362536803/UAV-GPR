"""ISSUE-018 contract tests: FileReplayBackend (.rcscan file replay backend).

Pins the replay contract on top of the ISSUE-011 strict ``RcScanReader``
(v2 air/ground), the ISSUE-013 ``RcScanV1Reader`` (v1 adapter) and the
ISSUE-015 ``AcquisitionBackend`` lifecycle:

- v2 air/ground and v1 adapter replay output matches the readers'
  values/axis/channels/metadata exactly (trace identity, UTC, GNSS and
  missing fields preserved verbatim; no current time, no 0/0 coordinates);
- out-of-order physical records are replayed in logical ``trace_index``
  order; duplicates collapse to the first committed copy; no-GNSS traces
  keep their missing state; the file's ``calibrated`` group is never
  applied (raw is served);
- pacing modes: per-trace (no waits), original-time ratio (file gaps),
  explicit acceleration; paced waits are cancellable (``cancel``/``close``)
  and honour ``timeout_s`` — tests use events/joins and lower-bound timing
  only, never fixed sleeps;
- corrupt / no-raw / unsupported files are rejected explicitly
  (fail-closed); configure enforces the file mission config (v2 digest,
  v1 channels/axis);
- pause/resume/stop/emergency-stop/close cooperate with the ISSUE-017
  ``AcquisitionController`` (safe boundary, drain, interrupt, no leaked
  worker).

Fixtures are synthetic files written through the frozen ISSUE-008 schema
creator and the authoritative row codec (same contract as the ISSUE-010
writer and the ISSUE-011 test bulk builder).  Everything is synthetic; no
``sleep``, no hardware, no reference projects.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import h5py
import numpy as np
import pytest

from uav_gpr.acquisition.backend import (
    AcquisitionBackend,
    BackendCancelledError,
    BackendClosedError,
    BackendConfigRejectedError,
    BackendState,
    BackendTimeoutError,
    Capabilities,
)
from uav_gpr.acquisition.controller import (
    AcquisitionController,
    BackpressurePolicy,
    ControllerState,
    StopReason,
)
from uav_gpr.acquisition.replay import (
    FileReplayBackend,
    ReplayConfig,
    ReplayCorruptFileError,
    ReplayEndedError,
    ReplayMode,
    ReplayNoRawError,
    ReplayUnsupportedFileError,
)
from uav_gpr.acquisition.scheduler import EventWaiter
from uav_gpr.core.channels import ChannelSpec
from uav_gpr.core.config import MissionConfig
from uav_gpr.core.enums import (
    AcquisitionMode,
    EndpointRole,
    GnssFixQuality,
    GnssMatchMethod,
    GnssNoFixPolicy,
    LogicalPolarization,
    SParameter,
    TraceQualityReason,
    TraceQualityStatus,
)
from uav_gpr.core.frequency import FrequencySweep
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
from uav_gpr.storage.rcscan_reader import RcScanReader, ReadTrace

pytestmark = pytest.mark.contract

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


def build_mission_config(
    channels: tuple[ChannelSpec, ...], *, target_interval_s: float = 0.1
) -> MissionConfig:
    return MissionConfig(
        frequency_start_hz=800e6,
        frequency_stop_hz=2600e6,
        frequency_points=_FREQUENCY_POINTS,
        if_bw_hz=1_000.0,
        power_dbm=-3.0,
        channels=channels,
        acquisition_mode=AcquisitionMode.FIXED_COUNT,
        planned_trace_count=8,
        target_interval_s=target_interval_s,
        gnss_max_age_s=2.0,
        gnss_no_fix_policy=GnssNoFixPolicy.RECORD_WITHOUT_POSITION,
        calibration_profile_id=None,
        apply_calibration=False,
        background_reference_id=None,
        apply_background=False,
        created_utc=_CREATED_UTC,
        software_version="0.1.0.dev0",
    )


_SWEEP_CHANNELS: tuple[ChannelSpec, ...] = build_channels()
_SWEEP_FREQUENCIES: np.ndarray = np.asarray(
    build_mission_config(_SWEEP_CHANNELS).frequency_axis_hz, dtype="<f8"
)


def _uid_str(index: int) -> str:
    return str(uuid.UUID(int=index + 1))


def make_raw(
    index: int, *, channels: int, frequencies: int, salt: float = 0.0
) -> np.ndarray:
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


@dataclass(frozen=True)
class ReplayRow:
    """One logical row for the v2 fixture builder (deterministic synthetic)."""

    trace_index: int
    uid: str
    salt: float = 0.0
    with_gnss: bool = True
    started_mono_ns: int = 0
    started_utc: datetime | None = None


def row_metadata(
    row: ReplayRow, config: MissionConfig, channels: tuple[ChannelSpec, ...]
) -> TraceMetadata:
    """Acquired (hash-less) metadata for one row (ISSUE-011 make_metadata
    pattern with caller-controlled monotonic start for pacing tests)."""
    started = (
        row.started_utc
        if row.started_utc is not None
        else _CREATED_UTC + timedelta(seconds=1 + row.trace_index)
    )
    midpoint = started + timedelta(milliseconds=10)
    finished = started + timedelta(milliseconds=20)
    mono = row.started_mono_ns
    match = (
        make_gnss_match(midpoint, mono + 5_000_000) if row.with_gnss else None
    )
    if match is None:
        status = TraceQualityStatus.DEGRADED
        reasons: tuple[TraceQualityReason, ...] = (TraceQualityReason.GNSS_MISSING,)
    else:
        status = TraceQualityStatus.NOMINAL
        reasons = ()
    actual: float | None = None if row.trace_index == 0 else config.target_interval_s
    schedule: float | None = None if row.trace_index == 0 else 0.0
    return TraceMetadata(
        mission_id=_MISSION_ID,
        trace_index=row.trace_index,
        trace_uid=TraceUid(row.uid),
        device_id=_DEVICE_ID,
        sweep_started_utc=started,
        sweep_midpoint_utc=midpoint,
        sweep_finished_utc=finished,
        sweep_started_monotonic_ns=MonotonicNs(mono),
        sweep_midpoint_monotonic_ns=MonotonicNs(mono + 10_000_000),
        sweep_finished_monotonic_ns=MonotonicNs(mono + 20_000_000),
        target_interval_s=config.target_interval_s,
        actual_interval_s=actual,
        schedule_error_s=schedule,
        connection_generation=1,
        raw_trace_sha256=None,
        gnss_match=match,
        quality_status=status,
        quality_reasons=reasons,
    )


def build_v2_file(
    scratch_dir: Path,
    rows: Sequence[ReplayRow],
    *,
    role: EndpointRole = EndpointRole.AIR,
    target_interval_s: float = 0.1,
    with_calibrated: bool = False,
    lifecycle: str = "finalized",
    completion_kind: str = "completed",
    commit_count: int | None = None,
) -> tuple[Path, MissionConfig]:
    """Build a v2 file fast by writing whole columns at once (test-only).

    Uses the same frozen schema creator and the same authoritative row codec
    (``trace_metadata_to_cells``) as the ISSUE-010 writer and the ISSUE-011
    bulk builder, so the produced bytes are contract-identical.
    """
    channels = build_channels()
    config = build_mission_config(channels, target_interval_s=target_interval_s)
    axis = np.asarray(config.frequency_axis_hz, dtype="<f8")
    file_id = _AIR_FILE_ID if role is EndpointRole.AIR else _GROUND_FILE_ID
    partial = scratch_dir / f"{file_id}{'.partial.rcscan'}"
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
            metadata = row_metadata(row, config, channels)
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
            if path not in column_values:
                continue  # empty fixture: skeleton columns stay zero-length
            dataset = h5[path]
            values = column_values[path]
            array = np.array(values, dtype=dataset.dtype)
            dataset.resize((count,))
            dataset[:] = array
        raw_dataset = h5["/frequency/raw"]
        raw_dataset.resize((count, len(channels), int(axis.size)))
        raw_dataset[:] = np.stack(raw_rows) if raw_rows else np.empty(
            (0, len(channels), int(axis.size)), dtype="<c16"
        )

        if with_calibrated and count > 0:
            cal_contract = next(
                c for c in contracts if c.path == "/frequency/calibrated"
            )
            calibrated = h5.create_dataset(
                "/frequency/calibrated",
                shape=(count, len(channels), int(axis.size)),
                maxshape=cal_contract.maxshape,
                dtype=cal_contract.dtype,
                chunks=cal_contract.chunks,
                compression=cal_contract.compression,
            )
            # Distinct from raw: proves replay never applies the group.
            calibrated[:] = np.stack(raw_rows) + 1000.0

        committed = count if commit_count is None else commit_count
        h5["/checkpoints/committed_record_count"][0] = np.int64(committed)
        last_index = (
            max((row.trace_index for row in rows), default=schema.MISSING_INT64)
            if commit_count is None
            else (
                max((row.trace_index for row in rows), default=schema.MISSING_INT64)
                if committed > 0
                else schema.MISSING_INT64
            )
        )
        h5["/checkpoints/last_trace_index"][0] = np.int64(last_index)
        h5["/checkpoints/updated_utc"][0] = to_utc_iso(
            _CREATED_UTC + timedelta(seconds=count)
        )
        mission = h5["mission"]
        mission.attrs["started_utc"] = to_utc_iso(_CREATED_UTC)
        if lifecycle == "writing":
            mission.attrs["ended_utc"] = ""
            mission.attrs["completion_kind"] = ""
            h5.attrs["lifecycle_state"] = "writing"
        else:
            mission.attrs["ended_utc"] = to_utc_iso(
                _CREATED_UTC + timedelta(seconds=count)
            )
            mission.attrs["completion_kind"] = completion_kind
            h5.attrs["lifecycle_state"] = lifecycle
    if lifecycle in ("finalized", "recovered"):
        final = scratch_dir / f"{_MISSION_ID}.rcscan"
        os.replace(partial, final)
        return final, config
    return partial, config


def build_v1_file(
    scratch_dir: Path,
    n_traces: int,
    *,
    timestamps_utc: Sequence[datetime] | None = None,
) -> Path:
    """Build one minimal synthetic v1 ``.rcscan`` file (test-only).

    Layout mirrors the frozen v1 reader contract (attrs, ``/channels`` JSON,
    ``/axes``, ``/frequency``, optional ``/trace_metadata``); no mission,
    GNSS, position or per-trace UID concepts exist in v1.
    """
    frequencies = np.asarray(_SWEEP_FREQUENCIES, dtype="<f8")
    target = scratch_dir / "v1.rcscan"
    channels_text = json.dumps(
        [
            {"logical": "HH", "s_parameter": "S11"},
            {"logical": "VV", "s_parameter": "S22"},
        ],
        ensure_ascii=False,
        allow_nan=False,
    )
    raw = np.stack(
        [
            make_raw(index, channels=2, frequencies=_FREQUENCY_POINTS)
            for index in range(n_traces)
        ]
    ).astype("<c16", copy=False)
    with h5py.File(target, "x") as h5:
        h5.attrs["format_name"] = "rcscan"
        h5.attrs["schema_version"] = 1
        h5.attrs["created_utc"] = to_utc_iso(_CREATED_UTC)
        h5.attrs["generator"] = "replay-test"
        h5.attrs["trigger"] = "time"
        h5.attrs["position_source"] = "none"
        h5.create_dataset(
            "channels", data=channels_text, dtype=h5py.string_dtype("utf-8")
        )
        axes = h5.create_group("axes")
        axes.create_dataset("frequencies_hz", data=frequencies)
        freq_group = h5.create_group("frequency")
        freq_group.create_dataset("raw", data=raw)
        freq_group.create_dataset(
            "history_json", data="[]", dtype=h5py.string_dtype("utf-8")
        )
        if timestamps_utc is not None:
            meta_group = h5.create_group("trace_metadata")
            meta_group.create_dataset(
                "timestamps_utc",
                data=np.asarray(
                    [to_utc_iso(t) for t in timestamps_utc], dtype=object
                ),
                dtype=h5py.string_dtype("utf-8"),
            )
            meta_group.create_dataset(
                "extras_json",
                data=np.asarray(["{}"] * n_traces, dtype=object),
                dtype=h5py.string_dtype("utf-8"),
            )
    return target


def v1_config(axis: np.ndarray, channels: tuple[ChannelSpec, ...]) -> MissionConfig:
    return MissionConfig.from_frequency_axis(
        frequency_axis_hz=axis,
        if_bw_hz=1_000.0,
        power_dbm=-3.0,
        channels=channels,
        acquisition_mode=AcquisitionMode.CONTINUOUS,
        planned_trace_count=None,
        target_interval_s=0.001,
        gnss_max_age_s=2.0,
        gnss_no_fix_policy=GnssNoFixPolicy.RECORD_WITHOUT_POSITION,
        created_utc=_CREATED_UTC,
        software_version="0.1.0.dev0",
    )


def open_configure(backend: AcquisitionBackend, config: MissionConfig) -> Capabilities:
    capabilities = backend.open()
    backend.configure(config)
    return capabilities


def collect_sweeps(backend: AcquisitionBackend, count: int) -> list[FrequencySweep]:
    return [backend.acquire() for _ in range(count)]


def reader_logical(path: Path) -> list[ReadTrace]:
    with RcScanReader(path) as reader:
        return [record for chunk in reader.iter_logical() for record in chunk.records]


def _assert_sweep_matches_record(
    sweep: FrequencySweep,
    record: ReadTrace,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
) -> None:
    assert tuple(sweep.channels) == channels
    assert np.array_equal(sweep.frequencies_hz, frequencies)
    assert np.array_equal(sweep.data, record.frequency_raw)
    assert sweep.metadata == record.metadata
    # Verbatim preservation of identity/UTC/GNSS/quality/hash (explicit, so
    # the contract survives future metadata evolution).
    assert sweep.metadata is not None
    assert sweep.metadata.mission_id == record.metadata.mission_id
    assert sweep.metadata.trace_index == record.metadata.trace_index
    assert sweep.metadata.trace_uid == record.metadata.trace_uid
    assert sweep.metadata.device_id == record.metadata.device_id
    assert sweep.metadata.sweep_started_utc == record.metadata.sweep_started_utc
    assert sweep.metadata.sweep_midpoint_utc == record.metadata.sweep_midpoint_utc
    assert sweep.metadata.sweep_finished_utc == record.metadata.sweep_finished_utc
    assert (
        sweep.metadata.sweep_started_monotonic_ns
        == record.metadata.sweep_started_monotonic_ns
    )
    assert (
        sweep.metadata.sweep_midpoint_monotonic_ns
        == record.metadata.sweep_midpoint_monotonic_ns
    )
    assert (
        sweep.metadata.sweep_finished_monotonic_ns
        == record.metadata.sweep_finished_monotonic_ns
    )
    assert sweep.metadata.target_interval_s == record.metadata.target_interval_s
    assert sweep.metadata.actual_interval_s == record.metadata.actual_interval_s
    assert sweep.metadata.schedule_error_s == record.metadata.schedule_error_s
    assert (
        sweep.metadata.connection_generation
        == record.metadata.connection_generation
    )
    assert sweep.metadata.raw_trace_sha256 == record.metadata.raw_trace_sha256
    assert sweep.metadata.gnss_match == record.metadata.gnss_match
    assert sweep.metadata.quality_status == record.metadata.quality_status
    assert sweep.metadata.quality_reasons == record.metadata.quality_reasons


# ---------------------------------------------------------------------------
# v2 air/ground numeric round-trip and verbatim metadata
# ---------------------------------------------------------------------------


def test_v2_air_replay_matches_reader_logical(scratch_dir: Path) -> None:
    rows = [ReplayRow(i, _uid_str(i)) for i in range(5)]
    path, config = build_v2_file(scratch_dir, rows, role=EndpointRole.AIR)
    records = reader_logical(path)
    assert [r.trace_index for r in records] == [0, 1, 2, 3, 4]

    backend = FileReplayBackend(path)
    capabilities = open_configure(backend, config)
    assert capabilities.gnss is True
    assert capabilities.fault_injection is False
    assert capabilities.device_id == _DEVICE_ID
    assert capabilities.channels == build_channels()
    assert backend.trace_count == 5
    assert backend.source_format == "rcscan_v2"

    sweeps = collect_sweeps(backend, 5)
    for sweep, record in zip(sweeps, records, strict=True):
        _assert_sweep_matches_record(
            sweep, record, build_channels(), _SWEEP_FREQUENCIES
        )
    assert [s.metadata.trace_index for s in sweeps] == [0, 1, 2, 3, 4]
    backend.close()


def test_v2_ground_replay_matches_reader_logical(scratch_dir: Path) -> None:
    rows = [ReplayRow(i, _uid_str(i), with_gnss=False) for i in range(3)]
    path, config = build_v2_file(scratch_dir, rows, role=EndpointRole.GROUND)
    records = reader_logical(path)

    backend = FileReplayBackend(path)
    open_configure(backend, config)
    assert backend.source_format == "rcscan_v2"
    sweeps = collect_sweeps(backend, 3)
    for sweep, record in zip(sweeps, records, strict=True):
        _assert_sweep_matches_record(
            sweep, record, build_channels(), _SWEEP_FREQUENCIES
        )
    # Ground file without GNSS: capability reports no GNSS source.
    assert backend.capabilities.gnss is False
    backend.close()


def test_out_of_order_physical_rows_replay_in_logical_order(
    scratch_dir: Path,
) -> None:
    rows = [
        ReplayRow(2, _uid_str(2)),
        ReplayRow(0, _uid_str(0)),
        ReplayRow(1, _uid_str(1)),
        ReplayRow(3, _uid_str(3)),
    ]
    path, config = build_v2_file(scratch_dir, rows)
    records = reader_logical(path)
    assert [r.trace_index for r in records] == [0, 1, 2, 3]

    backend = FileReplayBackend(path)
    open_configure(backend, config)
    sweeps = collect_sweeps(backend, 4)
    assert [s.metadata.trace_index for s in sweeps] == [0, 1, 2, 3]
    for sweep, record in zip(sweeps, records, strict=True):
        _assert_sweep_matches_record(
            sweep, record, build_channels(), _SWEEP_FREQUENCIES
        )
    backend.close()


def test_no_gnss_rows_keep_missing(scratch_dir: Path) -> None:
    rows = [ReplayRow(0, _uid_str(0), with_gnss=True),
            ReplayRow(1, _uid_str(1), with_gnss=False)]
    path, config = build_v2_file(scratch_dir, rows)
    backend = FileReplayBackend(path)
    open_configure(backend, config)
    sweeps = collect_sweeps(backend, 2)
    assert sweeps[0].metadata.gnss_match is not None
    assert sweeps[1].metadata.gnss_match is None
    assert sweeps[1].metadata.quality_status is TraceQualityStatus.DEGRADED
    assert sweeps[1].metadata.quality_reasons == (TraceQualityReason.GNSS_MISSING,)
    assert sweeps[1].metadata.raw_trace_sha256 is not None  # integrity kept
    backend.close()


def test_calibrated_group_ignored_raw_served(scratch_dir: Path) -> None:
    rows = [ReplayRow(i, _uid_str(i)) for i in range(2)]
    path, config = build_v2_file(scratch_dir, rows, with_calibrated=True)
    backend = FileReplayBackend(path)
    open_configure(backend, config)
    sweeps = collect_sweeps(backend, 2)
    raw = [r.frequency_raw for r in reader_logical(path)]
    for sweep, expected in zip(sweeps, raw, strict=True):
        assert np.array_equal(sweep.data, expected)
        assert not np.array_equal(sweep.data, expected + 1000.0)
    backend.close()


def test_duplicate_rows_collapse_to_first_committed_copy(scratch_dir: Path) -> None:
    rows = [ReplayRow(0, _uid_str(0), salt=0.0),
            ReplayRow(0, _uid_str(0), salt=0.0)]
    path, config = build_v2_file(scratch_dir, rows)
    records = reader_logical(path)
    assert len(records) == 1  # duplicate collapsed to the first committed copy

    backend = FileReplayBackend(path)
    open_configure(backend, config)
    assert backend.trace_count == 1
    sweep = backend.acquire()
    _assert_sweep_matches_record(sweep, records[0], build_channels(), _SWEEP_FREQUENCIES)
    backend.close()


def test_missing_indices_are_served_as_present(scratch_dir: Path) -> None:
    rows = [ReplayRow(0, _uid_str(0)), ReplayRow(2, _uid_str(2))]
    path, config = build_v2_file(scratch_dir, rows)
    backend = FileReplayBackend(path)
    open_configure(backend, config)
    assert backend.trace_count == 2
    sweeps = collect_sweeps(backend, 2)
    assert [s.metadata.trace_index for s in sweeps] == [0, 2]
    backend.close()


# ---------------------------------------------------------------------------
# End of replay and lifecycle
# ---------------------------------------------------------------------------


def test_trace_count_matches_logical_view(scratch_dir: Path) -> None:
    rows = [ReplayRow(i, _uid_str(i)) for i in range(7)]
    path, config = build_v2_file(scratch_dir, rows)
    backend = FileReplayBackend(path)
    open_configure(backend, config)
    assert backend.trace_count == 7
    collect_sweeps(backend, 7)
    backend.close()


def test_acquire_past_end_raises_replay_ended(scratch_dir: Path) -> None:
    rows = [ReplayRow(0, _uid_str(0))]
    path, config = build_v2_file(scratch_dir, rows)
    backend = FileReplayBackend(path)
    open_configure(backend, config)
    sweep = backend.acquire()
    assert sweep.metadata.trace_index == 0
    with pytest.raises(ReplayEndedError) as exc:
        backend.acquire()
    assert exc.value.reason == "replay_ended"
    with pytest.raises(ReplayEndedError):
        backend.acquire()  # deterministic, repeatable
    assert backend.state is BackendState.CONFIGURED
    backend.close()


def test_lifecycle_and_idempotent_close_reopen(scratch_dir: Path) -> None:
    rows = [ReplayRow(0, _uid_str(0))]
    path, config = build_v2_file(scratch_dir, rows)
    backend = FileReplayBackend(path)
    backend.close()  # close on CLOSED is an idempotent no-op
    capabilities = open_configure(backend, config)
    assert capabilities.device_id == _DEVICE_ID
    sweep = backend.acquire()
    assert sweep.metadata.trace_index == 0
    backend.close()
    backend.close()  # idempotent
    assert backend.state is BackendState.CLOSED


# ---------------------------------------------------------------------------
# configure contract (v2 digest, v1 channels/axis, applied/diff)
# ---------------------------------------------------------------------------


def test_configure_rejects_config_digest_mismatch(scratch_dir: Path) -> None:
    rows = [ReplayRow(0, _uid_str(0))]
    path, config = build_v2_file(scratch_dir, rows)
    other = build_mission_config(build_channels(), target_interval_s=0.2)
    assert other.config_sha256 != config.config_sha256
    backend = FileReplayBackend(path)
    backend.open()
    with pytest.raises(BackendConfigRejectedError) as exc:
        backend.configure(other)
    assert exc.value.reason == "config_rejected"
    backend.close()


def test_configure_accepts_digest_equal_descriptive_variants(
    scratch_dir: Path,
) -> None:
    rows = [ReplayRow(0, _uid_str(0))]
    path, config = build_v2_file(scratch_dir, rows)
    from dataclasses import replace

    variant = replace(
        build_mission_config(build_channels()),
        created_utc=datetime(2020, 1, 1, tzinfo=UTC),
    )
    # descriptive fields (created_utc/note) are outside the config digest
    assert variant.config_sha256 == config.config_sha256
    backend = FileReplayBackend(path)
    backend.open()
    applied = backend.configure(variant)
    assert applied.config == config  # file config is authoritative
    assert applied.diff.changed_fields == ()
    backend.close()


def test_v1_configure_rejects_channel_mismatch(scratch_dir: Path) -> None:
    path = build_v1_file(scratch_dir, 2, timestamps_utc=None)
    backend = FileReplayBackend(path)
    backend.open()
    single = build_channels()[:1]
    config = v1_config(_SWEEP_FREQUENCIES, single)
    with pytest.raises(BackendConfigRejectedError):
        backend.configure(config)
    backend.close()


def test_v1_configure_rejects_axis_mismatch(scratch_dir: Path) -> None:
    """P3-02: the v1 configure contract must reject a replay config whose
    frequency axis differs from the file, both by point count and by
    start/stop (structured BackendConfigRejectedError)."""
    path = build_v1_file(scratch_dir, 2, timestamps_utc=None)
    backend = FileReplayBackend(path)
    backend.open()
    channels = build_channels()
    fewer_points = np.asarray(_SWEEP_FREQUENCIES[:-1], dtype="<f8")
    with pytest.raises(BackendConfigRejectedError):
        backend.configure(v1_config(fewer_points, channels))
    shifted = np.asarray(_SWEEP_FREQUENCIES, dtype="<f8") + 100.0
    with pytest.raises(BackendConfigRejectedError):
        backend.configure(v1_config(shifted, channels))
    backend.close()


def test_v1_paced_mode_without_timestamps_rejected(scratch_dir: Path) -> None:
    path = build_v1_file(scratch_dir, 2, timestamps_utc=None)
    backend = FileReplayBackend(path, replay=ReplayConfig(mode=ReplayMode.ORIGINAL_TIME))
    backend.open()
    with pytest.raises(BackendConfigRejectedError):
        backend.configure(v1_config(_SWEEP_FREQUENCIES, build_channels()))
    backend.close()


# ---------------------------------------------------------------------------
# v1 adapter replay
# ---------------------------------------------------------------------------


def test_v1_replay_preserves_rows_metadata_none(scratch_dir: Path) -> None:
    timestamps = [
        _CREATED_UTC + timedelta(seconds=1 + i) for i in range(3)
    ]
    path = build_v1_file(scratch_dir, 3, timestamps_utc=timestamps)
    config = v1_config(_SWEEP_FREQUENCIES, build_channels())
    backend = FileReplayBackend(path)
    capabilities = open_configure(backend, config)
    assert backend.source_format == "rcscan_v1"
    assert backend.trace_count == 3
    assert capabilities.gnss is False
    assert capabilities.fault_injection is False

    sweeps = collect_sweeps(backend, 3)
    # Compare against the v1 adapter's own raw rows (independent decode).
    from uav_gpr.storage.rcscan_v1 import RcScanV1Reader

    with RcScanV1Reader(path) as reader:
        for index, sweep in enumerate(sweeps):
            assert sweep.metadata is None  # missing stays missing (no v1 mission)
            assert np.array_equal(sweep.data, reader.raw_row(index))
            # channel identity preserved; descriptive fields follow the
            # v1 adapter mapping (display_name/antenna_note)
            assert tuple(c.channel_id for c in sweep.channels) == tuple(
                c.channel_id for c in build_channels()
            )
            assert np.array_equal(sweep.frequencies_hz, _SWEEP_FREQUENCIES)
    backend.close()


def test_v1_without_timestamps_per_trace_mode_ok(scratch_dir: Path) -> None:
    path = build_v1_file(scratch_dir, 2, timestamps_utc=None)
    backend = FileReplayBackend(path)  # default PER_TRACE
    open_configure(backend, v1_config(_SWEEP_FREQUENCIES, build_channels()))
    sweeps = collect_sweeps(backend, 2)
    assert all(sweep.metadata is None for sweep in sweeps)
    backend.close()


def test_v1_original_time_paces_by_timestamps(scratch_dir: Path) -> None:
    timestamps = [
        _CREATED_UTC,
        _CREATED_UTC + timedelta(milliseconds=50),
        _CREATED_UTC + timedelta(milliseconds=150),
    ]
    path = build_v1_file(scratch_dir, 3, timestamps_utc=timestamps)
    backend = FileReplayBackend(path, replay=ReplayConfig(mode=ReplayMode.ORIGINAL_TIME))
    open_configure(backend, v1_config(_SWEEP_FREQUENCIES, build_channels()))
    started = time.monotonic()
    first = backend.acquire()
    assert first.metadata is None
    second_started = time.monotonic()
    backend.acquire()
    third_started = time.monotonic()
    backend.acquire()
    finished = time.monotonic()
    assert second_started - started < 0.02  # first trace: no wait
    assert third_started - second_started >= 0.04  # gap 0.05s (lower bound only)
    assert finished - third_started >= 0.08  # gap 0.10s (lower bound only)
    backend.close()


# ---------------------------------------------------------------------------
# Pacing modes (v2) and cancellable waits
# ---------------------------------------------------------------------------


def _paced_rows() -> list[ReplayRow]:
    return [
        ReplayRow(0, _uid_str(0), started_mono_ns=0),
        ReplayRow(1, _uid_str(1), started_mono_ns=50_000_000),
        ReplayRow(2, _uid_str(2), started_mono_ns=150_000_000),
    ]


def test_per_trace_mode_never_waits(scratch_dir: Path) -> None:
    rows = [
        ReplayRow(0, _uid_str(0), started_mono_ns=0),
        ReplayRow(1, _uid_str(1), started_mono_ns=30_000_000_000),
        ReplayRow(2, _uid_str(2), started_mono_ns=60_000_000_000),
    ]
    path, config = build_v2_file(scratch_dir, rows)
    backend = FileReplayBackend(path)  # default PER_TRACE
    open_configure(backend, config)
    started = time.monotonic()
    collect_sweeps(backend, 3)
    elapsed = time.monotonic() - started
    assert elapsed < 5.0  # 30s gaps would make a pacing bug take >= 60s
    backend.close()


def test_original_time_paces_by_file_gaps(scratch_dir: Path) -> None:
    path, config = build_v2_file(scratch_dir, _paced_rows())
    backend = FileReplayBackend(path, replay=ReplayConfig(mode=ReplayMode.ORIGINAL_TIME))
    open_configure(backend, config)
    started = time.monotonic()
    backend.acquire()  # first trace: no wait
    second_started = time.monotonic()
    backend.acquire()  # waits gap 0.05s
    third_started = time.monotonic()
    backend.acquire()  # waits gap 0.10s
    finished = time.monotonic()
    assert second_started - started < 0.02  # first trace never waits
    assert third_started - second_started >= 0.04
    assert finished - third_started >= 0.08
    backend.close()


def test_accelerated_mode_scales_gaps(scratch_dir: Path) -> None:
    path, config = build_v2_file(scratch_dir, _paced_rows())
    backend = FileReplayBackend(
        path, replay=ReplayConfig(mode=ReplayMode.ACCELERATED, acceleration=4.0)
    )
    open_configure(backend, config)
    started = time.monotonic()
    backend.acquire()
    second_started = time.monotonic()
    backend.acquire()  # waits 4 x 0.05 = 0.20s
    third_started = time.monotonic()
    backend.acquire()  # waits 4 x 0.10 = 0.40s
    finished = time.monotonic()
    assert second_started - started < 0.02
    assert third_started - second_started >= 0.16
    assert finished - third_started >= 0.32
    backend.close()


def test_replay_config_validation() -> None:
    with pytest.raises(ValueError):
        ReplayConfig(mode=ReplayMode.ACCELERATED, acceleration=1.0)
    with pytest.raises(ValueError):
        ReplayConfig(mode=ReplayMode.ORIGINAL_TIME, acceleration=2.0)
    with pytest.raises(ValueError):
        ReplayConfig(mode=ReplayMode.ACCELERATED, acceleration=float("nan"))
    with pytest.raises(TypeError):
        ReplayConfig(mode="per_trace")  # type: ignore[arg-type]


def test_paced_wait_honors_timeout_s(scratch_dir: Path) -> None:
    rows = [
        ReplayRow(0, _uid_str(0), started_mono_ns=0),
        ReplayRow(1, _uid_str(1), started_mono_ns=300_000_000),
    ]
    path, config = build_v2_file(scratch_dir, rows)
    backend = FileReplayBackend(path, replay=ReplayConfig(mode=ReplayMode.ORIGINAL_TIME))
    open_configure(backend, config)
    backend.acquire()
    started = time.monotonic()
    with pytest.raises(BackendTimeoutError) as exc:
        backend.acquire(timeout_s=0.1)  # gap is 0.3s: caller timeout wins
    assert time.monotonic() - started < 0.3
    assert exc.value.reason == "device_timeout"
    backend.close()


def _run_acquire_in_thread(
    backend: AcquisitionBackend,
) -> tuple[threading.Thread, list[object]]:
    results: list[object] = []

    def worker() -> None:
        try:
            results.append(backend.acquire())
        except BaseException as exc:
            results.append(exc)

    thread = threading.Thread(target=worker, name="replay-test-worker")
    thread.start()
    return thread, results


def test_cancel_interrupts_paced_wait(scratch_dir: Path) -> None:
    rows = [
        ReplayRow(0, _uid_str(0), started_mono_ns=0),
        ReplayRow(1, _uid_str(1), started_mono_ns=5_000_000_000),
    ]
    path, config = build_v2_file(scratch_dir, rows)
    backend = FileReplayBackend(path, replay=ReplayConfig(mode=ReplayMode.ORIGINAL_TIME))
    open_configure(backend, config)
    assert backend.acquire().metadata.trace_index == 0
    thread, results = _run_acquire_in_thread(backend)
    assert backend.acquire_started.wait(2.0)
    backend.cancel()
    thread.join(2.0)
    assert not thread.is_alive()
    assert len(results) == 1
    assert isinstance(results[0], BackendCancelledError)
    assert backend.state is BackendState.CONFIGURED
    assert not backend.acquiring
    backend.close()


def test_close_interrupts_paced_wait(scratch_dir: Path) -> None:
    rows = [
        ReplayRow(0, _uid_str(0), started_mono_ns=0),
        ReplayRow(1, _uid_str(1), started_mono_ns=5_000_000_000),
    ]
    path, config = build_v2_file(scratch_dir, rows)
    backend = FileReplayBackend(path, replay=ReplayConfig(mode=ReplayMode.ORIGINAL_TIME))
    open_configure(backend, config)
    backend.acquire()
    thread, results = _run_acquire_in_thread(backend)
    assert backend.acquire_started.wait(2.0)
    backend.close()
    thread.join(2.0)
    assert not thread.is_alive()
    assert len(results) == 1
    assert isinstance(results[0], BackendClosedError)
    assert backend.state is BackendState.CLOSED


def test_cancel_without_pending_acquire_is_noop(scratch_dir: Path) -> None:
    path, config = build_v2_file(scratch_dir, [ReplayRow(0, _uid_str(0))])
    backend = FileReplayBackend(path)
    open_configure(backend, config)
    backend.cancel()
    backend.cancel()  # idempotent
    sweep = backend.acquire()  # cancel must not poison later acquires
    assert sweep.metadata.trace_index == 0
    backend.close()


# ---------------------------------------------------------------------------
# Corrupt / no-raw / unsupported files: explicit rejection (fail-closed)
# ---------------------------------------------------------------------------


def _corrupt_raw_cell(path: Path, position: int) -> None:
    with h5py.File(path, "r+") as h5:
        raw = h5["/frequency/raw"][position]
        h5["/frequency/raw"][position] = raw + 1.0j


def test_hash_mismatch_rejected(scratch_dir: Path) -> None:
    path, _ = build_v2_file(scratch_dir, [ReplayRow(0, _uid_str(0))])
    _corrupt_raw_cell(path, 0)
    backend = FileReplayBackend(path)
    with pytest.raises(ReplayCorruptFileError) as exc:
        backend.open()
    assert exc.value.reason == "corrupt_file"
    backend.close()


def test_missing_stored_hash_rejected(scratch_dir: Path) -> None:
    path, _ = build_v2_file(scratch_dir, [ReplayRow(0, _uid_str(0))])
    with h5py.File(path, "r+") as h5:
        h5["/trace_metadata/raw_trace_sha256"][0] = ""
    backend = FileReplayBackend(path)
    with pytest.raises(ReplayCorruptFileError) as exc:
        backend.open()
    assert exc.value.reason == "corrupt_file"
    backend.close()


def test_conflicting_identity_rejected(scratch_dir: Path) -> None:
    rows = [ReplayRow(0, _uid_str(0), salt=0.0),
            ReplayRow(0, _uid_str(0), salt=1.0)]
    path, _ = build_v2_file(scratch_dir, rows)
    backend = FileReplayBackend(path)
    with pytest.raises(ReplayCorruptFileError) as exc:
        backend.open()
    assert exc.value.reason == "corrupt_file"
    backend.close()


def test_no_committed_raw_rejected(scratch_dir: Path) -> None:
    path, _ = build_v2_file(
        scratch_dir, [], lifecycle="writing", completion_kind=""
    )
    backend = FileReplayBackend(path)
    with pytest.raises(ReplayNoRawError) as exc:
        backend.open()
    assert exc.value.reason == "no_raw"
    backend.close()


def test_non_hdf5_rejected(scratch_dir: Path) -> None:
    path = scratch_dir / "not_hdf5.rcscan"
    path.write_text("not an hdf5 file", encoding="utf-8")
    backend = FileReplayBackend(path)
    with pytest.raises(ReplayUnsupportedFileError) as exc:
        backend.open()
    assert exc.value.reason == "unsupported_file"
    backend.close()


def test_failed_open_rolls_back_to_closed(scratch_dir: Path) -> None:
    """P3-01: a failed open must not leave the backend in OPEN state
    without a reader — the state rolls back to CLOSED (idempotent close)."""
    path = scratch_dir / "bad.rcscan"
    path.write_text("not an hdf5 file", encoding="utf-8")
    backend = FileReplayBackend(path)
    with pytest.raises(ReplayUnsupportedFileError):
        backend.open()
    assert backend.state is BackendState.CLOSED
    # the backend is fully usable again: a valid file can be opened
    good = build_v2_file(scratch_dir, [ReplayRow(0, _uid_str(0))])[0]
    good_backend = FileReplayBackend(good)
    good_backend.open()
    assert good_backend.state is BackendState.OPEN
    good_backend.close()


def test_unknown_schema_version_rejected(scratch_dir: Path) -> None:
    path, _ = build_v2_file(scratch_dir, [ReplayRow(0, _uid_str(0))])
    with h5py.File(path, "r+") as h5:
        h5.attrs["schema_version"] = 3
    backend = FileReplayBackend(path)
    with pytest.raises(ReplayUnsupportedFileError):
        backend.open()
    backend.close()


def test_legacy_rcscan_v1_detected_after_v2_probe(scratch_dir: Path) -> None:
    path = build_v1_file(scratch_dir, 1, timestamps_utc=None)
    backend = FileReplayBackend(path)
    capabilities = backend.open()
    assert capabilities.gnss is False
    assert backend.source_format == "rcscan_v1"
    assert backend.trace_count == 1
    backend.close()


def test_v1_capabilities_device_id_deterministic(scratch_dir: Path) -> None:
    path = build_v1_file(scratch_dir, 1, timestamps_utc=None)
    first = FileReplayBackend(path)
    first_caps = first.open()
    first.close()
    second = FileReplayBackend(path)
    second_caps = second.open()
    second.close()
    assert first_caps.device_id == second_caps.device_id  # deterministic
    assert isinstance(first_caps.device_id, DeviceId)


# ---------------------------------------------------------------------------
# AcquisitionController cooperation (pause/resume/stop/emergency/close)
# ---------------------------------------------------------------------------


def _paced_controller_file(scratch_dir: Path) -> tuple[Path, MissionConfig]:
    rows = [
        ReplayRow(0, _uid_str(0), started_mono_ns=0),
        ReplayRow(1, _uid_str(1), started_mono_ns=200_000_000),
        ReplayRow(2, _uid_str(2), started_mono_ns=400_000_000),
        ReplayRow(3, _uid_str(3), started_mono_ns=600_000_000),
    ]
    return build_v2_file(scratch_dir, rows, target_interval_s=0.001)


def _make_controller(backend: AcquisitionBackend) -> AcquisitionController:
    return AcquisitionController(
        backend,
        capacity=16,
        backpressure=BackpressurePolicy.BLOCK,
        clock=ManualClock(_CREATED_UTC, monotonic_ns=0),
        waiter=EventWaiter(),
    )


def test_controller_pause_resume_replay(scratch_dir: Path) -> None:
    path, config = _paced_controller_file(scratch_dir)
    backend = FileReplayBackend(path, replay=ReplayConfig(mode=ReplayMode.ORIGINAL_TIME))
    controller = _make_controller(backend)
    controller.configure(config)
    controller.start()

    first = controller.sweeps.get(timeout_s=2.0)
    assert first is not None and first.metadata.trace_index == 0
    # worker is now inside acquire(1)'s 0.2s paced wait (safe boundary)
    assert backend.acquire_started.wait(2.0)
    controller.pause()
    in_flight = controller.sweeps.get(timeout_s=1.0)  # in-flight completes
    assert in_flight is not None and in_flight.metadata.trace_index == 1
    assert controller.state is ControllerState.PAUSED
    assert controller.sweeps.get(timeout_s=0.4) is None  # nothing new while paused

    controller.resume()
    next_sweep = controller.sweeps.get(timeout_s=2.0)
    assert next_sweep is not None and next_sweep.metadata.trace_index == 2
    assert backend.acquire_started.wait(2.0)  # worker in acquire(3) paced wait
    controller.stop()
    assert controller.wait_finished(2.0)
    assert controller.state is ControllerState.STOPPED
    assert controller.stop_reason is StopReason.USER_STOP
    drained = controller.sweeps.get(timeout_s=1.0)  # in-flight drained
    assert drained is not None and drained.metadata.trace_index == 3
    assert controller.sweeps.get(timeout_s=0.4) is None
    controller.close()
    assert controller.state is ControllerState.CLOSED


def test_controller_stop_drains_replay(scratch_dir: Path) -> None:
    path, config = _paced_controller_file(scratch_dir)
    backend = FileReplayBackend(path, replay=ReplayConfig(mode=ReplayMode.ORIGINAL_TIME))
    controller = _make_controller(backend)
    controller.configure(config)
    controller.start()

    first = controller.sweeps.get(timeout_s=2.0)
    assert first is not None and first.metadata.trace_index == 0
    assert backend.acquire_started.wait(2.0)  # in acquire(1) paced wait
    controller.stop()
    assert controller.wait_finished(2.0)
    assert controller.state is ControllerState.STOPPED
    assert controller.stop_reason is StopReason.USER_STOP
    drained = controller.sweeps.get(timeout_s=1.0)
    assert drained is not None and drained.metadata.trace_index == 1
    assert controller.sweeps.get(timeout_s=0.4) is None
    controller.close()


def test_controller_emergency_stop_interrupts_paced_wait(scratch_dir: Path) -> None:
    rows = [
        ReplayRow(0, _uid_str(0), started_mono_ns=0),
        ReplayRow(1, _uid_str(1), started_mono_ns=5_000_000_000),
    ]
    path, config = build_v2_file(scratch_dir, rows, target_interval_s=0.001)
    backend = FileReplayBackend(path, replay=ReplayConfig(mode=ReplayMode.ORIGINAL_TIME))
    controller = _make_controller(backend)
    controller.configure(config)
    controller.start()

    first = controller.sweeps.get(timeout_s=2.0)
    assert first is not None and first.metadata.trace_index == 0
    assert backend.acquire_started.wait(2.0)  # in acquire(1) 5s paced wait
    controller.emergency_stop()
    assert controller.wait_finished(2.0)
    assert controller.state is ControllerState.STOPPED
    assert controller.stop_reason is StopReason.EMERGENCY
    # the interrupted in-flight sweep is never published (fail-closed)
    assert controller.sweeps.published == 1
    assert controller.sweeps.get(timeout_s=0.4) is None
    controller.close()


def test_controller_close_no_leaked_worker(scratch_dir: Path) -> None:
    path, config = _paced_controller_file(scratch_dir)
    backend = FileReplayBackend(path, replay=ReplayConfig(mode=ReplayMode.ORIGINAL_TIME))
    controller = _make_controller(backend)
    controller.configure(config)
    controller.start()
    first = controller.sweeps.get(timeout_s=2.0)
    assert first is not None
    assert backend.acquire_started.wait(2.0)  # worker blocked in paced wait
    controller.close()
    assert controller.state is ControllerState.CLOSED
    assert controller.join(timeout_s=1.0)
    controller.close()  # idempotent
    assert controller.state is ControllerState.CLOSED
