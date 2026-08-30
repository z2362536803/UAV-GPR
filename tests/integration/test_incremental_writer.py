"""ISSUE-010 integration tests: incremental writer, checkpoint, atomic finalize.

Why ``tests/integration`` and not ``tests/contract``:

- ``tests/contract`` currently pins the *frozen physical artifact* (golden
  manifest, dtype/shape/encoding) of ISSUE-008/009.  Those files must stay
  untouched by this Issue.
- These tests drive the writer through crash-like fault points over **real
  HDF5 files on disk**, composing the storage layer with the core codecs
  (``trace_metadata_to_cells``), the ISSUE-009 raw hash and an injectable
  filesystem facade.  ``docs/TESTING.md`` section 1 defines
  ``tests/integration`` exactly as "multi-layer composition and crash /
  reconnect flows", so this is the project-conventional home.

No reader exists yet (that is ISSUE-011).  The "what a reader would see"
verification is therefore done by a **test-only probe helper** in this module
that applies the frozen rule from ``docs/DATA_FORMAT.md`` section 3:

    a physical row is committed iff its position < committed_record_count
    and every required trace-major column has at least that many rows.

"Half trace not visible" is thus judged mechanically: after any fault, the
committed view must still decode through ``trace_metadata_from_cells`` for
every committed row, and the checkpoint must never point past durable data.

Fault injection is deterministic: a phase hook raises at an exact write step
and a replaceable filesystem facade fails the rename.  No ``sleep``, no timing
guessing, no hardware.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
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
from uav_gpr.core.timeutil import ManualClock, MonotonicNs
from uav_gpr.storage import rcscan_v2 as schema
from uav_gpr.storage.incremental_writer import (
    PARTIAL_SUFFIX,
    AppendDecision,
    FileSystemFacade,
    InjectedStorageFault,
    LocalFileSystemFacade,
    PhaseFaultHook,
    RcScanIncrementalWriter,
    StorageFaultHook,
    TraceAppendRequest,
    WritePhase,
    WriterState,
)

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Frozen synthetic test contract
# ---------------------------------------------------------------------------

_FREQUENCY_POINTS = 16
_CHANNEL_COUNT = 2

_MISSION_ID = MissionId("0f0e8a3b-6f2d-4c1e-9a7b-112233445566")
_DEVICE_ID = DeviceId("d1c0ffee-0000-4000-8000-000000000001")
_AIR_FILE_ID = AirFileId("aaaaaaa1-0000-4000-8000-000000000002")
_GROUND_FILE_ID = GroundFileId("aaaaaaa2-0000-4000-8000-000000000002")
_CREATED_UTC = datetime(2026, 8, 28, 9, 0, 0, tzinfo=UTC)
_WRITER_VERSION = "uav-gpr.test.1"


def build_channels() -> tuple[ChannelSpec, ChannelSpec]:
    """Two-channel contract (multi-channel from day one)."""
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


@pytest.fixture()
def fault_hook() -> PhaseFaultHook:
    return PhaseFaultHook()


@pytest.fixture()
def filesystem() -> LocalFileSystemFacade:
    return LocalFileSystemFacade()


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
    with_gnss: bool = True,
) -> TraceMetadata:
    """Acquired (hash-less) metadata for one logical trace index."""
    base = _CREATED_UTC + timedelta(seconds=1 + index)
    started = base + timedelta(milliseconds=0)
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
    with_gnss: bool = True,
    config_sha256: str | None = None,
    metadata: TraceMetadata | None = None,
) -> TraceAppendRequest:
    return TraceAppendRequest(
        metadata=metadata if metadata is not None else make_metadata(index, with_gnss=with_gnss),
        frequency_raw=make_raw(
            index,
            channels=len(channels),
            frequencies=int(frequencies.size),
            salt=salt,
        ),
        channels=channels,
        frequencies_hz=frequencies,
        config_sha256=config_sha256,
    )


def create_writer(
    scratch_dir: Path,
    *,
    role: EndpointRole,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
    clock: ManualClock,
    fault_hook: StorageFaultHook | None = None,
    filesystem: FileSystemFacade | None = None,
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


@pytest.fixture()
def air_writer(
    scratch_dir: Path,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
    clock: ManualClock,
    fault_hook: PhaseFaultHook,
    filesystem: LocalFileSystemFacade,
) -> Iterator[RcScanIncrementalWriter]:
    writer = create_writer(
        scratch_dir,
        role=EndpointRole.AIR,
        channels=channels,
        frequencies=frequencies,
        mission_config=mission_config,
        clock=clock,
        fault_hook=fault_hook,
        filesystem=filesystem,
    )
    yield writer
    if writer.state is WriterState.OPEN:
        writer.abort()


# ---------------------------------------------------------------------------
# Test-only committed-view probe (the reader rule of DATA_FORMAT.md section 3)
# ---------------------------------------------------------------------------


def _cell_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


@dataclass(frozen=True)
class CommittedView:
    """What a checkpoint-respecting reader would see (test-only)."""

    committed_record_count: int
    physical_rows: int
    traces: tuple[TraceMetadata, ...]
    raw: np.ndarray
    hashes: tuple[str, ...]
    trace_indices: tuple[int, ...]

    @property
    def half_written_rows(self) -> int:
        """Physical rows that exist but are not committed (invisible)."""
        return self.physical_rows - self.committed_record_count


def row_cell_paths() -> tuple[str, ...]:
    """Every physical cell path produced by the authoritative row codec."""
    return tuple(sorted(schema.trace_metadata_to_cells(make_metadata(0)).keys()))


def read_committed_view(path: Path) -> CommittedView:
    """Read only committed rows, applying the frozen checkpoint rule.

    A row is visible iff ``position < committed_record_count`` **and** every
    required trace-major column physically holds that row.  Decoding through
    ``trace_metadata_from_cells`` additionally proves the row is complete
    (a half-written row raises instead of silently passing).
    """
    paths = row_cell_paths()
    with h5py.File(path, "r") as h5:
        committed = int(h5["/checkpoints/committed_record_count"][0])
        lengths = {p: int(h5[p].shape[0]) for p in paths if p in h5}
        missing = [p for p, length in lengths.items() if length < committed]
        assert not missing, f"committed rows missing physical cells: {sorted(missing)}"
        traces: list[TraceMetadata] = []
        hashes: list[str] = []
        for position in range(committed):
            cells: dict[str, object] = {}
            for dataset_path in paths:
                if dataset_path in h5:
                    cells[dataset_path] = h5[dataset_path][position]
            traces.append(
                schema.trace_metadata_from_cells(
                    cells, mission_id=_MISSION_ID, device_id=_DEVICE_ID
                )
            )
            hashes.append(_cell_text(cells["/trace_metadata/raw_trace_sha256"]))
        raw = np.asarray(h5["/frequency/raw"][:committed])
        indices = tuple(int(v) for v in h5["/trace_metadata/trace_index"][:committed])
        physical_rows = int(h5["/frequency/raw"].shape[0])
    return CommittedView(
        committed_record_count=committed,
        physical_rows=physical_rows,
        traces=tuple(traces),
        raw=raw,
        hashes=tuple(hashes),
        trace_indices=indices,
    )


def expected_hash(
    index: int,
    *,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    salt: float = 0.0,
) -> str:
    """Independent ISSUE-009 hash recomputation (never from the writer)."""
    return compute_raw_trace_sha256(
        mission_id=_MISSION_ID,
        trace_index=index,
        trace_uid=_trace_uid(index),
        channels=channels,
        frequencies_hz=frequencies,
        data=make_raw(
            index,
            channels=len(channels),
            frequencies=int(frequencies.size),
            salt=salt,
        ),
    )


def test_appending_preserves_the_frozen_issue008_schema(
    air_writer: RcScanIncrementalWriter,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
) -> None:
    """The writer reuses the ISSUE-008 contract instead of inventing its own."""
    for index in range(3):
        air_writer.append_trace(make_request(index, channels=channels, frequencies=frequencies))
    air_writer.abort()

    contracts = schema.dataset_contracts(_CHANNEL_COUNT, _FREQUENCY_POINTS)
    with h5py.File(air_writer.partial_path, "r") as h5:
        for contract in contracts:
            if contract.optional:
                assert contract.path not in h5, f"{contract.path} must stay absent"
                continue
            assert contract.path in h5, f"{contract.path} is missing"
            dataset = h5[contract.path]
            assert dataset.dtype == contract.dtype, contract.path
            assert tuple(dataset.maxshape) == contract.maxshape, contract.path
            assert dataset.compression == contract.compression, contract.path
            if contract.chunks is None:
                assert dataset.chunks is None, contract.path
            else:
                assert tuple(dataset.chunks) == contract.chunks, contract.path
            assert len(dataset.shape) == len(contract.initial_shape), contract.path
            for actual, initial, maximum in zip(
                dataset.shape, contract.initial_shape, contract.maxshape, strict=True
            ):
                if maximum is None:
                    # Trace-major axis: exactly one physical row per commit.
                    assert actual == 3, contract.path
                else:
                    assert actual == initial, contract.path


def test_raw_data_is_stored_unmodified(
    air_writer: RcScanIncrementalWriter,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
) -> None:
    """``frequency_raw`` is immutable: what was submitted is what is stored."""
    submitted = [
        make_raw(index, channels=_CHANNEL_COUNT, frequencies=_FREQUENCY_POINTS)
        for index in range(2)
    ]
    for index, raw in enumerate(submitted):
        air_writer.append_trace(
            TraceAppendRequest(
                metadata=make_metadata(index),
                frequency_raw=raw,
                channels=channels,
                frequencies_hz=frequencies,
            )
        )
    air_writer.abort()

    view = read_committed_view(air_writer.partial_path)
    for index, raw in enumerate(submitted):
        assert np.array_equal(view.raw[index], raw)
    assert view.raw.dtype == np.dtype("<c16")


# ---------------------------------------------------------------------------
# Creation and frozen contract
# ---------------------------------------------------------------------------


def test_create_makes_partial_file_in_writing_state(
    air_writer: RcScanIncrementalWriter,
) -> None:
    assert air_writer.partial_path.name == f"{_AIR_FILE_ID}.partial.rcscan"
    assert air_writer.partial_path.exists()
    assert air_writer.final_path.name == f"{_MISSION_ID}.rcscan"
    assert not air_writer.final_path.exists()
    assert air_writer.state is WriterState.OPEN
    assert air_writer.committed_record_count == 0
    assert air_writer.physical_record_count == 0

    probe = schema.probe_rcscan_v2(air_writer.partial_path)
    assert probe.lifecycle_state == "writing"
    assert probe.file_role is EndpointRole.AIR
    assert probe.channel_ids == ("hh_s11", "vv_s22")


def test_create_refuses_existing_partial(
    scratch_dir: Path,
    air_writer: RcScanIncrementalWriter,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
    clock: ManualClock,
) -> None:
    with pytest.raises(DomainError) as error:
        create_writer(
            scratch_dir,
            role=EndpointRole.AIR,
            channels=channels,
            frequencies=frequencies,
            mission_config=mission_config,
            clock=clock,
        )
    assert error.value.code is ErrorCode.INVALID_ARGUMENT


def test_create_freezes_mission_config_axis_and_channels(
    air_writer: RcScanIncrementalWriter,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
) -> None:
    frozen = air_writer.frozen_contract
    assert frozen.mission_id == _MISSION_ID
    assert frozen.device_id == _DEVICE_ID
    assert frozen.channels == channels
    assert np.array_equal(frozen.frequencies_hz, frequencies)
    assert frozen.config_sha256 == mission_config.config_sha256
    assert air_writer.config_sha256 == mission_config.config_sha256


def test_create_rejects_axis_that_contradicts_the_config(
    scratch_dir: Path,
    channels: tuple[ChannelSpec, ...],
    mission_config: MissionConfig,
    clock: ManualClock,
) -> None:
    wrong_axis = np.linspace(900e6, 2700e6, _FREQUENCY_POINTS)
    with pytest.raises(DomainError) as error:
        RcScanIncrementalWriter.create(
            scratch_dir,
            mission_id=_MISSION_ID,
            device_id=_DEVICE_ID,
            file_id=_AIR_FILE_ID,
            role=EndpointRole.AIR,
            config=mission_config,
            channels=channels,
            frequencies_hz=wrong_axis,
            created_utc=_CREATED_UTC,
            writer_version=_WRITER_VERSION,
            clock=clock,
        )
    assert error.value.code is ErrorCode.AXIS_MISMATCH


def test_create_rejects_channels_that_contradict_the_config(
    scratch_dir: Path,
    frequencies: np.ndarray,
    mission_config: MissionConfig,
) -> None:
    wrong_channels = (
        ChannelSpec(
            channel_id="xx_s21",
            logical_polarization=LogicalPolarization.HV,
            s_parameter=SParameter.S21,
            display_name="Wrong",
        ),
    )
    with pytest.raises(DomainError) as error:
        RcScanIncrementalWriter.create(
            scratch_dir,
            mission_id=_MISSION_ID,
            device_id=_DEVICE_ID,
            file_id=_AIR_FILE_ID,
            role=EndpointRole.AIR,
            config=mission_config,
            channels=wrong_channels,
            frequencies_hz=frequencies,
            created_utc=_CREATED_UTC,
            writer_version=_WRITER_VERSION,
        )
    assert error.value.code is ErrorCode.CHANNEL_CONTRACT_MISMATCH


# ---------------------------------------------------------------------------
# Normal append / commit ordering
# ---------------------------------------------------------------------------


def test_append_commits_one_complete_trace_per_checkpoint(
    air_writer: RcScanIncrementalWriter,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
) -> None:
    for index in range(3):
        result = air_writer.append_trace(
            make_request(index, channels=channels, frequencies=frequencies)
        )
        assert result.decision is AppendDecision.NEW
        assert result.record_position == index
        assert result.committed_record_count == index + 1
        assert result.raw_trace_sha256 == expected_hash(
            index, channels=channels, frequencies=frequencies
        )

    assert air_writer.committed_record_count == 3
    assert air_writer.physical_record_count == 3


def test_committed_view_decodes_every_trace_and_matches_issue009_hash(
    air_writer: RcScanIncrementalWriter,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
) -> None:
    for index in range(3):
        air_writer.append_trace(
            make_request(index, channels=channels, frequencies=frequencies)
        )
    air_writer.abort()

    view = read_committed_view(air_writer.partial_path)
    assert view.committed_record_count == 3
    assert view.half_written_rows == 0
    assert view.trace_indices == (0, 1, 2)
    assert view.hashes == tuple(
        expected_hash(index, channels=channels, frequencies=frequencies)
        for index in range(3)
    )
    for index, trace in enumerate(view.traces):
        assert trace.trace_index == index
        assert trace.trace_uid == _trace_uid(index)
        assert trace.raw_trace_sha256 == view.hashes[index]
        assert trace.gnss_match is not None
        assert trace.gnss_match.usable_for_map
    assert view.raw.shape == (3, _CHANNEL_COUNT, _FREQUENCY_POINTS)


def test_single_trace_commit_is_durable_before_the_checkpoint_advances(
    air_writer: RcScanIncrementalWriter,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    fault_hook: PhaseFaultHook,
) -> None:
    """Checkpoint update is the last step: data is flushed before it moves."""
    fault_hook.arm(WritePhase.AFTER_DATA_FLUSH)
    observed: list[int] = []
    original = fault_hook.on_phase

    def spy(phase: WritePhase) -> None:
        if phase is WritePhase.AFTER_DATA_FLUSH:
            observed.append(int(air_writer.committed_record_count))
        original(phase)

    fault_hook.on_phase = spy  # type: ignore[method-assign]
    with pytest.raises(InjectedStorageFault):
        air_writer.append_trace(make_request(0, channels=channels, frequencies=frequencies))

    assert observed == [0], "checkpoint must still read 0 when data is flushed"
    view = read_committed_view(air_writer.partial_path)
    assert view.committed_record_count == 0
    assert view.physical_rows == 1, "the half row exists physically but stays invisible"


def test_explicit_flush_does_not_move_the_checkpoint(
    air_writer: RcScanIncrementalWriter,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
) -> None:
    air_writer.append_trace(make_request(0, channels=channels, frequencies=frequencies))
    air_writer.flush()
    assert air_writer.committed_record_count == 1


def test_ground_writer_appends_without_transport_group(
    scratch_dir: Path,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
    clock: ManualClock,
) -> None:
    writer = create_writer(
        scratch_dir,
        role=EndpointRole.GROUND,
        channels=channels,
        frequencies=frequencies,
        mission_config=mission_config,
        clock=clock,
    )
    writer.append_trace(make_request(0, channels=channels, frequencies=frequencies))
    writer.abort()

    with h5py.File(writer.partial_path, "r") as h5:
        assert "/transport" not in h5
        assert int(h5["/trace_metadata/trace_index"].shape[0]) == 1
    view = read_committed_view(writer.partial_path)
    assert view.committed_record_count == 1
    assert view.traces[0].trace_index == 0


# ---------------------------------------------------------------------------
# Physical row is not trace_index
# ---------------------------------------------------------------------------


def test_physical_record_order_is_independent_of_trace_index(
    air_writer: RcScanIncrementalWriter,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
) -> None:
    """Out-of-order (retransmit) appends keep physical order = arrival order."""
    logical_order = (0, 5, 3)
    positions: dict[int, int] = {}
    for index in logical_order:
        result = air_writer.append_trace(
            make_request(index, channels=channels, frequencies=frequencies)
        )
        positions[index] = result.record_position

    assert positions == {0: 0, 5: 1, 3: 2}
    assert air_writer.record_position_for(5) == 1
    assert air_writer.trace_index_at_record(1) == 5
    assert air_writer.logical_trace_indices() == (0, 3, 5)
    assert air_writer.committed_record_count == 3

    air_writer.abort()
    view = read_committed_view(air_writer.partial_path)
    assert view.trace_indices == logical_order
    assert [trace.trace_index for trace in view.traces] == list(logical_order)

    with h5py.File(air_writer.partial_path, "r") as h5:
        assert int(h5["/checkpoints/last_trace_index"][0]) == 5


# ---------------------------------------------------------------------------
# Duplicate / conflict
# ---------------------------------------------------------------------------


def test_classify_trace_reports_new_duplicate_and_conflict(
    air_writer: RcScanIncrementalWriter,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
) -> None:
    index = 0
    digest = expected_hash(index, channels=channels, frequencies=frequencies)
    assert air_writer.classify_trace(index, digest) is AppendDecision.NEW
    air_writer.append_trace(make_request(index, channels=channels, frequencies=frequencies))
    assert air_writer.classify_trace(index, digest) is AppendDecision.DUPLICATE
    assert air_writer.classify_trace(index, "f" * 64) is AppendDecision.CONFLICT


def test_duplicate_trace_is_idempotent_and_does_not_advance_checkpoint(
    air_writer: RcScanIncrementalWriter,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
) -> None:
    request = make_request(0, channels=channels, frequencies=frequencies)
    first = air_writer.append_trace(request)
    second = air_writer.append_trace(make_request(0, channels=channels, frequencies=frequencies))

    assert first.decision is AppendDecision.NEW
    assert second.decision is AppendDecision.DUPLICATE
    assert second.record_position == first.record_position
    assert second.committed_record_count == 1
    assert air_writer.physical_record_count == 1
    assert air_writer.logical_trace_indices() == (0,)


def test_conflicting_hash_is_rejected_and_evidence_is_preserved(
    air_writer: RcScanIncrementalWriter,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
) -> None:
    good = air_writer.append_trace(make_request(0, channels=channels, frequencies=frequencies))
    stored_hash = good.raw_trace_sha256

    with pytest.raises(DomainError) as error:
        air_writer.append_trace(
            make_request(0, channels=channels, frequencies=frequencies, salt=1.0)
        )
    assert error.value.code is ErrorCode.ID_CONFLICT
    context = error.value.context
    assert context["stored_hash"] == stored_hash
    assert context["incoming_hash"] == expected_hash(
        0, channels=channels, frequencies=frequencies, salt=1.0
    )
    assert context["trace_index"] == 0

    # Evidence retained, original record untouched, nothing further written.
    conflicts = air_writer.conflicts
    assert len(conflicts) == 1
    assert conflicts[0].trace_index == 0
    assert conflicts[0].stored_hash == stored_hash
    assert conflicts[0].incoming_hash == context["incoming_hash"]
    assert conflicts[0].record_position == 0
    assert air_writer.committed_record_count == 1
    assert air_writer.physical_record_count == 1

    air_writer.abort()
    view = read_committed_view(air_writer.partial_path)
    assert view.hashes == (stored_hash,)


def test_reused_trace_uid_on_another_index_is_rejected(
    air_writer: RcScanIncrementalWriter,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
) -> None:
    air_writer.append_trace(make_request(0, channels=channels, frequencies=frequencies))

    # A different logical index carrying an already committed trace_uid.
    reused_uid = replace(make_metadata(1), trace_uid=_trace_uid(0))
    with pytest.raises(DomainError) as error:
        air_writer.append_trace(
            make_request(
                1,
                channels=channels,
                frequencies=frequencies,
                metadata=reused_uid,
            )
        )
    assert error.value.code is ErrorCode.ID_CONFLICT
    assert error.value.context["conflicting_trace_index"] == 0
    assert error.value.context["incoming_trace_uid"] == _trace_uid(0).to_json()
    assert air_writer.committed_record_count == 1
    assert air_writer.physical_record_count == 1


def test_pre_attached_contradictory_hash_is_a_conflict_with_evidence(
    air_writer: RcScanIncrementalWriter,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
) -> None:
    """A claimed hash contradicting the recomputed digest is a real conflict.

    Retransmission can carry a ``raw_trace_sha256`` that disagrees with the
    digest recomputed from the trace's own raw sweep (AGENTS.md section 4:
    same mission + index, different hash).  It must take the same conflict
    path and leave the same evidence trail - ``writer.conflicts`` plus a
    unified context key set - instead of escaping through the domain model.
    """
    air_writer.append_trace(make_request(0, channels=channels, frequencies=frequencies))

    claimed = "a" * 64
    metadata = replace(make_metadata(1), raw_trace_sha256=claimed)
    with pytest.raises(DomainError) as error:
        air_writer.append_trace(
            make_request(1, channels=channels, frequencies=frequencies, metadata=metadata)
        )
    assert error.value.code is ErrorCode.ID_CONFLICT
    context = error.value.context
    assert context["trace_index"] == 1
    assert context["record_position"] == -1, "nothing was ever committed under this index"
    assert context["stored_hash"] == claimed
    assert context["incoming_hash"] == expected_hash(
        1, channels=channels, frequencies=frequencies
    )
    assert context["stored_trace_uid"] == _trace_uid(1).to_json()
    assert context["incoming_trace_uid"] == _trace_uid(1).to_json()

    conflicts = air_writer.conflicts
    assert len(conflicts) == 1
    assert conflicts[0].trace_index == 1
    assert conflicts[0].record_position == -1
    assert conflicts[0].stored_hash == claimed
    assert conflicts[0].incoming_hash == context["incoming_hash"]
    assert conflicts[0].stored_trace_uid == _trace_uid(1).to_json()
    assert conflicts[0].incoming_trace_uid == _trace_uid(1).to_json()

    # Fail-closed: nothing was written, the stored trace is untouched and
    # the writer stays usable for the same index without the bad claim.
    assert air_writer.committed_record_count == 1
    assert air_writer.physical_record_count == 1
    retry = air_writer.append_trace(
        make_request(1, channels=channels, frequencies=frequencies)
    )
    assert retry.decision is AppendDecision.NEW
    assert len(air_writer.conflicts) == 1


def test_pre_attached_contradictory_hash_on_committed_index_records_stored_evidence(
    air_writer: RcScanIncrementalWriter,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
) -> None:
    """Same index already committed: the evidence points at the stored row."""
    good = air_writer.append_trace(make_request(0, channels=channels, frequencies=frequencies))
    stored_hash = good.raw_trace_sha256

    claimed = "b" * 64
    metadata = replace(make_metadata(0), raw_trace_sha256=claimed)
    with pytest.raises(DomainError) as error:
        air_writer.append_trace(
            make_request(0, channels=channels, frequencies=frequencies, metadata=metadata)
        )
    assert error.value.code is ErrorCode.ID_CONFLICT
    assert error.value.context["record_position"] == 0
    assert error.value.context["stored_hash"] == stored_hash
    assert error.value.context["incoming_hash"] == expected_hash(
        0, channels=channels, frequencies=frequencies
    )

    conflicts = air_writer.conflicts
    assert len(conflicts) == 1
    assert conflicts[0].record_position == 0
    assert conflicts[0].stored_hash == stored_hash
    assert air_writer.committed_record_count == 1
    assert air_writer.physical_record_count == 1

    air_writer.abort()
    view = read_committed_view(air_writer.partial_path)
    assert view.hashes == (stored_hash,)


def test_pre_attached_matching_hash_is_accepted_idempotently(
    air_writer: RcScanIncrementalWriter,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
) -> None:
    """A claimed hash equal to the recomputed digest is not a conflict."""
    digest = expected_hash(0, channels=channels, frequencies=frequencies)
    metadata = replace(make_metadata(0), raw_trace_sha256=digest)
    first = air_writer.append_trace(
        make_request(0, channels=channels, frequencies=frequencies, metadata=metadata)
    )
    assert first.decision is AppendDecision.NEW
    assert first.raw_trace_sha256 == digest
    assert air_writer.conflicts == ()

    # Same index, same digest, hash pre-attached again: idempotent duplicate.
    second = air_writer.append_trace(
        make_request(0, channels=channels, frequencies=frequencies, metadata=metadata)
    )
    assert second.decision is AppendDecision.DUPLICATE
    assert air_writer.conflicts == ()
    assert air_writer.committed_record_count == 1
    assert air_writer.physical_record_count == 1


# ---------------------------------------------------------------------------
# Frozen-contract violations on append
# ---------------------------------------------------------------------------


def test_append_rejects_incompatible_frequency_axis(
    air_writer: RcScanIncrementalWriter,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
) -> None:
    shifted = frequencies + 1.0e6
    with pytest.raises(DomainError) as error:
        air_writer.append_trace(
            make_request(0, channels=channels, frequencies=shifted)
        )
    assert error.value.code is ErrorCode.AXIS_MISMATCH
    assert air_writer.committed_record_count == 0
    assert air_writer.physical_record_count == 0


def test_append_rejects_axis_with_a_different_point_count(
    air_writer: RcScanIncrementalWriter,
    channels: tuple[ChannelSpec, ...],
) -> None:
    other_axis = np.linspace(800e6, 2600e6, _FREQUENCY_POINTS + 1)
    with pytest.raises(DomainError) as error:
        air_writer.append_trace(
            make_request(0, channels=channels, frequencies=other_axis)
        )
    assert error.value.code is ErrorCode.AXIS_MISMATCH


def test_append_rejects_incompatible_channels(
    air_writer: RcScanIncrementalWriter,
    frequencies: np.ndarray,
) -> None:
    swapped = (
        ChannelSpec(
            channel_id="vv_s22",
            logical_polarization=LogicalPolarization.VV,
            s_parameter=SParameter.S22,
            display_name="V vertical port",
            antenna_note="port B",
        ),
        ChannelSpec(
            channel_id="hh_s11",
            logical_polarization=LogicalPolarization.HH,
            s_parameter=SParameter.S11,
            display_name="H height S11",
        ),
    )
    with pytest.raises(DomainError) as error:
        air_writer.append_trace(make_request(0, channels=swapped, frequencies=frequencies))
    assert error.value.code is ErrorCode.CHANNEL_CONTRACT_MISMATCH
    assert air_writer.physical_record_count == 0


def test_append_rejects_stale_config_digest(
    air_writer: RcScanIncrementalWriter,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
) -> None:
    with pytest.raises(DomainError) as error:
        air_writer.append_trace(
            make_request(
                0,
                channels=channels,
                frequencies=frequencies,
                config_sha256="e" * 64,
            )
        )
    assert error.value.code is ErrorCode.CONFIG_DIGEST_MISMATCH


def test_append_rejects_raw_shape_mismatch(
    air_writer: RcScanIncrementalWriter,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
) -> None:
    request = TraceAppendRequest(
        metadata=make_metadata(0),
        frequency_raw=np.zeros((1, int(frequencies.size)), dtype="<c16"),
        channels=channels,
        frequencies_hz=frequencies,
    )
    with pytest.raises(DomainError) as error:
        air_writer.append_trace(request)
    assert error.value.code is ErrorCode.SHAPE_MISMATCH


def test_append_rejects_raw_dtype_mismatch(
    air_writer: RcScanIncrementalWriter,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
) -> None:
    request = TraceAppendRequest(
        metadata=make_metadata(0),
        frequency_raw=np.zeros(
            (len(channels), int(frequencies.size)), dtype="<U4"
        ),
        channels=channels,
        frequencies_hz=frequencies,
    )
    with pytest.raises(DomainError) as error:
        air_writer.append_trace(request)
    assert error.value.code is ErrorCode.DTYPE_MISMATCH


def test_append_rejects_foreign_mission_id(
    air_writer: RcScanIncrementalWriter,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
) -> None:
    raw = make_request(0, channels=channels, frequencies=frequencies)
    foreign = TraceMetadata(
        mission_id=MissionId("11111111-2222-4333-8444-555555555555"),
        trace_index=0,
        trace_uid=_trace_uid(0),
        device_id=_DEVICE_ID,
        sweep_started_utc=raw.metadata.sweep_started_utc,
        sweep_midpoint_utc=raw.metadata.sweep_midpoint_utc,
        sweep_finished_utc=raw.metadata.sweep_finished_utc,
        sweep_started_monotonic_ns=raw.metadata.sweep_started_monotonic_ns,
        sweep_midpoint_monotonic_ns=raw.metadata.sweep_midpoint_monotonic_ns,
        sweep_finished_monotonic_ns=raw.metadata.sweep_finished_monotonic_ns,
        target_interval_s=0.1,
        actual_interval_s=None,
        schedule_error_s=None,
        connection_generation=1,
        raw_trace_sha256=None,
        gnss_match=raw.metadata.gnss_match,
        quality_status=TraceQualityStatus.DEGRADED
        if raw.metadata.gnss_match is None
        else TraceQualityStatus.NOMINAL,
        quality_reasons=(TraceQualityReason.GNSS_MISSING,)
        if raw.metadata.gnss_match is None
        else (),
    )
    request = TraceAppendRequest(
        metadata=foreign,
        frequency_raw=raw.frequency_raw,
        channels=channels,
        frequencies_hz=frequencies,
    )
    with pytest.raises(DomainError) as error:
        air_writer.append_trace(request)
    assert error.value.code is ErrorCode.INVALID_ARGUMENT


# ---------------------------------------------------------------------------
# Fault injection: every write / flush / checkpoint phase
# ---------------------------------------------------------------------------

_PRE_CHECKPOINT_PHASES = [
    WritePhase.BEFORE_RAW_WRITE,
    WritePhase.AFTER_RAW_WRITE,
    WritePhase.AFTER_TRACE_COLUMNS,
    WritePhase.AFTER_DATA_FLUSH,
]


@pytest.mark.parametrize("phase", _PRE_CHECKPOINT_PHASES, ids=lambda p: p.value)
def test_fault_before_checkpoint_never_advances_the_checkpoint(
    air_writer: RcScanIncrementalWriter,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    fault_hook: PhaseFaultHook,
    phase: WritePhase,
) -> None:
    """Reader sees the last complete checkpoint; the half trace stays invisible."""
    for index in range(2):
        air_writer.append_trace(make_request(index, channels=channels, frequencies=frequencies))

    fault_hook.arm(phase)
    with pytest.raises(InjectedStorageFault):
        air_writer.append_trace(make_request(2, channels=channels, frequencies=frequencies))

    assert air_writer.state is WriterState.ABORTED
    assert air_writer.committed_record_count == 2

    view = read_committed_view(air_writer.partial_path)
    assert view.committed_record_count == 2
    assert [trace.trace_index for trace in view.traces] == [0, 1]
    assert view.hashes == tuple(
        expected_hash(index, channels=channels, frequencies=frequencies) for index in range(2)
    )
    # The interrupted trace may occupy a physical row; it is never committed.
    assert view.physical_rows in (2, 3)
    assert view.half_written_rows == view.physical_rows - 2
    with h5py.File(air_writer.partial_path, "r") as h5:
        assert str(h5.attrs["lifecycle_state"]) == "writing"
        assert str(h5["mission"].attrs["completion_kind"]) == ""


def test_fault_after_raw_write_leaves_metadata_columns_short(
    air_writer: RcScanIncrementalWriter,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    fault_hook: PhaseFaultHook,
) -> None:
    """The half-written row is physically present but not committed."""
    air_writer.append_trace(make_request(0, channels=channels, frequencies=frequencies))
    fault_hook.arm(WritePhase.AFTER_RAW_WRITE)
    with pytest.raises(InjectedStorageFault):
        air_writer.append_trace(make_request(1, channels=channels, frequencies=frequencies))

    with h5py.File(air_writer.partial_path, "r") as h5:
        assert h5["/frequency/raw"].shape[0] == 2
        assert h5["/trace_metadata/trace_index"].shape[0] == 1
        assert int(h5["/checkpoints/committed_record_count"][0]) == 1

    view = read_committed_view(air_writer.partial_path)
    assert view.committed_record_count == 1
    assert view.half_written_rows == 1


def test_fault_after_checkpoint_write_keeps_a_complete_committed_trace(
    air_writer: RcScanIncrementalWriter,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    fault_hook: PhaseFaultHook,
) -> None:
    """The checkpoint only advances after the data flush, so it is never ahead."""
    for index in range(2):
        air_writer.append_trace(make_request(index, channels=channels, frequencies=frequencies))

    fault_hook.arm(WritePhase.AFTER_CHECKPOINT_WRITE)
    with pytest.raises(InjectedStorageFault):
        air_writer.append_trace(make_request(2, channels=channels, frequencies=frequencies))

    view = read_committed_view(air_writer.partial_path)
    assert view.committed_record_count == 3
    assert view.half_written_rows == 0
    assert [trace.trace_index for trace in view.traces] == [0, 1, 2]
    assert view.hashes[2] == expected_hash(2, channels=channels, frequencies=frequencies)
    assert view.traces[2].raw_trace_sha256 == view.hashes[2]


# ---------------------------------------------------------------------------
# The two real HDF5 flushes of one commit
# ---------------------------------------------------------------------------
#
# h5py/HDF5 in this environment makes every write visible to other processes
# immediately, so *removing* ``h5.flush()`` cannot be detected by comparing
# file bytes - not even after a hard crash (measured: the on-disk state is
# identical with and without the flushes).  The only place where a flush is
# observable is the HDF5 handle itself, so the writer's ``hdf5_opener`` seam
# injects a wrapper that records every real ``flush()`` and can fail one
# deterministically.  Deleting a flush while keeping its phase announcement
# (which is all the phase-sequence test watches) breaks these tests.


class _FlushSpy:
    """HDF5 handle wrapper: records every real ``flush()``; can fail one."""

    def __init__(self, inner: Any, events: list[str]) -> None:
        self._inner = inner
        self._events = events
        self.calls = 0
        self._fail_next = False

    def fail_next_flush(self) -> None:
        """Make the very next ``flush()`` raise a real ENOSPC OSError."""
        self._fail_next = True

    def flush(self) -> None:
        self.calls += 1
        self._events.append(f"flush#{self.calls}")
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


class _PhaseRecorder:
    """Fault hook that only records the announced phases (never fails)."""

    def __init__(self, events: list[str]) -> None:
        self._events = events

    def on_phase(self, phase: WritePhase) -> None:
        self._events.append(phase.value)


class _ArmFlushFailure:
    """Fault hook that arms the spy's next flush failure at ``phase``.

    Because ``_flush`` announces the phase *after* flushing, arming at
    ``AFTER_TRACE_COLUMNS`` fails flush #1 (the data flush) and arming at
    ``AFTER_CHECKPOINT_WRITE`` fails flush #2 (the commit flush).
    """

    def __init__(self, phase: WritePhase, spy: Callable[[], _FlushSpy]) -> None:
        self._phase = phase
        self._spy = spy
        self._armed = False

    def arm(self) -> None:
        self._armed = True

    def on_phase(self, phase: WritePhase) -> None:
        if self._armed and phase is self._phase:
            self._spy().fail_next_flush()


def _spying_opener(events: list[str], spies: list[_FlushSpy]) -> Callable[[Path], Any]:
    def opener(path: Path) -> Any:
        spy = _FlushSpy(h5py.File(path, "r+"), events)
        spies.append(spy)
        return spy

    return opener


def test_each_commit_performs_two_real_hdf5_flushes_around_the_checkpoint(
    scratch_dir: Path,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
    clock: ManualClock,
) -> None:
    """Every commit really flushes twice: data, then the checkpoint.

    The phase sequence alone cannot prove this (the announcement is a separate
    statement), so the handle sees the calls: exactly two per commit, one on
    each side of the checkpoint write, plus one for the finalize.
    """
    events: list[str] = []
    spies: list[_FlushSpy] = []
    writer = create_writer(
        scratch_dir,
        role=EndpointRole.AIR,
        channels=channels,
        frequencies=frequencies,
        mission_config=mission_config,
        clock=clock,
        fault_hook=_PhaseRecorder(events),
        hdf5_opener=_spying_opener(events, spies),
    )
    assert spies[0].calls == 1, "create() flushes once after stamping started_utc"

    for index in range(2):
        writer.append_trace(make_request(index, channels=channels, frequencies=frequencies))
    writer.close(MissionTerminalState.COMPLETED)

    spy = spies[0]
    assert spy.calls == 6, "1 create flush + 2 per commit + 1 for the finalize"
    assert events == [
        "flush#1",
        WritePhase.BEFORE_RAW_WRITE.value,
        WritePhase.AFTER_RAW_WRITE.value,
        WritePhase.AFTER_TRACE_COLUMNS.value,
        "flush#2",
        WritePhase.AFTER_DATA_FLUSH.value,
        WritePhase.AFTER_CHECKPOINT_WRITE.value,
        "flush#3",
        WritePhase.AFTER_COMMIT_FLUSH.value,
        WritePhase.BEFORE_RAW_WRITE.value,
        WritePhase.AFTER_RAW_WRITE.value,
        WritePhase.AFTER_TRACE_COLUMNS.value,
        "flush#4",
        WritePhase.AFTER_DATA_FLUSH.value,
        WritePhase.AFTER_CHECKPOINT_WRITE.value,
        "flush#5",
        WritePhase.AFTER_COMMIT_FLUSH.value,
        WritePhase.BEFORE_FINALIZE.value,
        WritePhase.AFTER_FINALIZE_MARK.value,
        "flush#6",
        WritePhase.AFTER_FINALIZE_FLUSH.value,
        WritePhase.BEFORE_RENAME.value,
    ]


def test_data_flush_failure_never_advances_the_checkpoint(
    scratch_dir: Path,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
    clock: ManualClock,
) -> None:
    """A real ENOSPC at the data flush: the whole commit is lost, not half."""
    hook = _ArmFlushFailure(WritePhase.AFTER_TRACE_COLUMNS, lambda: spies[0])
    events: list[str] = []
    spies: list[_FlushSpy] = []
    writer = create_writer(
        scratch_dir,
        role=EndpointRole.AIR,
        channels=channels,
        frequencies=frequencies,
        mission_config=mission_config,
        clock=clock,
        fault_hook=hook,
        hdf5_opener=_spying_opener(events, spies),
    )
    writer.append_trace(make_request(0, channels=channels, frequencies=frequencies))
    assert writer.committed_record_count == 1
    hook.arm()  # only the second commit loses a flush

    with pytest.raises(OSError):
        writer.append_trace(make_request(1, channels=channels, frequencies=frequencies))

    assert writer.state is WriterState.ABORTED
    assert writer.committed_record_count == 1
    view = read_committed_view(writer.partial_path)
    assert view.committed_record_count == 1, "the checkpoint never moved"
    assert view.physical_rows == 2, "the interrupted row exists but stays invisible"
    assert view.half_written_rows == 1
    assert view.trace_indices == (0,)


def test_commit_flush_failure_never_exposes_an_incomplete_committed_row(
    scratch_dir: Path,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
    clock: ManualClock,
) -> None:
    """A real ENOSPC at the commit flush: the file stays consistent.

    The checkpoint is only written after the data flush, so whatever a later
    reader finds is a row that was already made durable - never a half trace -
    even though the writer itself refuses to claim this commit.
    """
    hook = _ArmFlushFailure(WritePhase.AFTER_CHECKPOINT_WRITE, lambda: spies[0])
    events: list[str] = []
    spies: list[_FlushSpy] = []
    writer = create_writer(
        scratch_dir,
        role=EndpointRole.AIR,
        channels=channels,
        frequencies=frequencies,
        mission_config=mission_config,
        clock=clock,
        fault_hook=hook,
        hdf5_opener=_spying_opener(events, spies),
    )
    writer.append_trace(make_request(0, channels=channels, frequencies=frequencies))
    hook.arm()  # only the second commit loses a flush

    with pytest.raises(OSError):
        writer.append_trace(make_request(1, channels=channels, frequencies=frequencies))

    assert writer.state is WriterState.ABORTED
    assert writer.committed_record_count == 1, "the writer never claims the failed commit"
    view = read_committed_view(writer.partial_path)
    assert view.committed_record_count == 2, "the file itself stayed readable"
    assert view.half_written_rows == 0
    assert view.trace_indices == (0, 1)
    assert [trace.trace_index for trace in view.traces] == [0, 1]


# ---------------------------------------------------------------------------
# Cross-process invariant check: a child process exits at one write phase
# ---------------------------------------------------------------------------
#
# Crash-model note: the writer flushes and closes the HDF5 handle *before*
# an injected fault propagates out of ``append_trace`` / ``_finalize_file``
# (``_force_close_handle`` runs on the way out), so by the time the child
# below calls ``os._exit`` the file is already flushed and closed.  The
# child model therefore leaves exactly the same on-disk state as the
# in-process fault tests; it does **not** simulate an unflushed power loss
# (this environment cannot produce one: HDF5 writes are visible to other
# processes immediately, and the handle spy in
# ``test_each_commit_performs_two_real_hdf5_flushes_around_the_checkpoint``
# is what pins the flush calls themselves).
#
# What the child model does add is a cross-process invariant check: the
# parent re-opens the abandoned partial from a *different process* and must
# still see only the last complete checkpoint with no half trace.  The run
# is deterministic (the parent waits for the child; no sleeps, no timing).

_CRASH_MARKER = "CRASH-FAULT"


def crash_child_main(phase_value: str, directory: str) -> None:
    """Child entry point: commit two traces, then crash inside the third."""
    channels = build_channels()
    config = build_mission_config(channels)
    axis = np.asarray(config.frequency_axis_hz, dtype="<f8")
    hook = PhaseFaultHook()
    writer = RcScanIncrementalWriter.create(
        Path(directory),
        mission_id=_MISSION_ID,
        device_id=_DEVICE_ID,
        file_id=_AIR_FILE_ID,
        role=EndpointRole.AIR,
        config=config,
        channels=channels,
        frequencies_hz=axis,
        created_utc=_CREATED_UTC,
        writer_version=_WRITER_VERSION,
        clock=ManualClock(_CREATED_UTC, monotonic_ns=0),
        fault_hook=hook,
    )
    for index in range(2):
        writer.append_trace(make_request(index, channels=channels, frequencies=axis))
    # Arm only now: the first two traces must commit cleanly.
    hook.arm(WritePhase(phase_value))
    try:
        writer.append_trace(make_request(2, channels=channels, frequencies=axis))
    except InjectedStorageFault:
        print(f"{_CRASH_MARKER}:{phase_value}", flush=True)
        return
    raise AssertionError(f"the injected fault at {phase_value} never fired")


def run_crash_child(phase: WritePhase, directory: Path) -> Path:
    """Crash a child process at ``phase``; return the abandoned partial file."""
    bootstrap = (
        "import sys; sys.path.insert(0, "
        + repr(str(Path(__file__).resolve().parent))
        + "); import test_incremental_writer as tw; "
        + f"tw.crash_child_main({phase.value!r}, {str(directory)!r}); "
        + "import os; os._exit(0)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", bootstrap],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, (
        f"crash child failed (exit {completed.returncode}): {completed.stderr}"
    )
    assert f"{_CRASH_MARKER}:{phase.value}" in completed.stdout, (
        f"the child never reached phase {phase.value}: {completed.stdout}"
    )
    partial = directory / f"{_AIR_FILE_ID}{PARTIAL_SUFFIX}"
    assert partial.exists()
    return partial


@pytest.mark.parametrize(
    "phase",
    [WritePhase.AFTER_TRACE_COLUMNS, WritePhase.AFTER_DATA_FLUSH],
    ids=lambda p: p.value,
)
def test_power_loss_before_the_checkpoint_keeps_the_previous_checkpoint(
    scratch_dir: Path,
    phase: WritePhase,
) -> None:
    """Crashed mid-commit: only the last complete checkpoint is visible."""
    partial = run_crash_child(phase, scratch_dir)
    view = read_committed_view(partial)
    assert view.committed_record_count == 2, "checkpoint must not have moved"
    assert [trace.trace_index for trace in view.traces] == [0, 1]
    assert view.hashes == tuple(
        expected_hash(
            index,
            channels=build_channels(),
            frequencies=np.asarray(build_mission_config(build_channels()).frequency_axis_hz),
        )
        for index in range(2)
    )
    with h5py.File(partial, "r") as h5:
        assert str(h5.attrs["lifecycle_state"]) == "writing"


def test_power_loss_after_the_data_flush_has_made_the_row_durable(
    scratch_dir: Path,
) -> None:
    """After the data flush the row is on disk while still uncommitted.

    This is the assertion a writer fails if it ever moves
    ``committed_record_count`` before flushing the trace data.
    """
    partial = run_crash_child(WritePhase.AFTER_DATA_FLUSH, scratch_dir)
    view = read_committed_view(partial)
    assert view.committed_record_count == 2
    assert view.physical_rows == 3, "data was flushed, so the row is on disk"
    assert view.half_written_rows == 1, "but it is not committed, so it stays invisible"
    assert np.array_equal(
        np.asarray(_read_raw_row(partial, 2)),
        make_raw(2, channels=_CHANNEL_COUNT, frequencies=_FREQUENCY_POINTS),
    )


def _read_raw_row(path: Path, position: int) -> np.ndarray:
    with h5py.File(path, "r") as h5:
        return np.asarray(h5["/frequency/raw"][position])


@pytest.mark.parametrize(
    "phase",
    [WritePhase.AFTER_CHECKPOINT_WRITE, WritePhase.AFTER_COMMIT_FLUSH],
    ids=lambda p: p.value,
)
def test_power_loss_after_the_checkpoint_never_exposes_a_half_trace(
    scratch_dir: Path,
    phase: WritePhase,
) -> None:
    """A durable checkpoint always points at durable, complete trace data."""
    partial = run_crash_child(phase, scratch_dir)
    view = read_committed_view(partial)
    assert view.committed_record_count in (2, 3)
    assert view.committed_record_count <= view.physical_rows
    for position, trace in enumerate(view.traces):
        assert trace.raw_trace_sha256 is not None
        assert len(trace.raw_trace_sha256) == 64
        assert trace.trace_uid.to_json() == _trace_uid(trace.trace_index).to_json()
        assert view.raw.shape[0] >= position + 1
    if view.committed_record_count == 3:
        assert [trace.trace_index for trace in view.traces] == [0, 1, 2]
        assert view.hashes == tuple(
            expected_hash(
                index,
                channels=build_channels(),
                frequencies=np.asarray(
                    build_mission_config(build_channels()).frequency_axis_hz
                ),
            )
            for index in range(3)
        )


def test_power_loss_after_the_commit_flush_makes_the_checkpoint_durable(
    scratch_dir: Path,
) -> None:
    """The final flush is what publishes the checkpoint to any later reader."""
    partial = run_crash_child(WritePhase.AFTER_COMMIT_FLUSH, scratch_dir)
    view = read_committed_view(partial)
    assert view.committed_record_count == 3
    assert view.half_written_rows == 0
    assert [trace.trace_index for trace in view.traces] == [0, 1, 2]


# ---------------------------------------------------------------------------
# Finalize faults, terminal states and atomic rename
# ---------------------------------------------------------------------------


def _append_three(
    writer: RcScanIncrementalWriter,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
) -> None:
    for index in range(3):
        writer.append_trace(make_request(index, channels=channels, frequencies=frequencies))


def test_fault_before_finalize_leaves_a_writing_partial_and_no_final_file(
    air_writer: RcScanIncrementalWriter,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    fault_hook: PhaseFaultHook,
) -> None:
    _append_three(air_writer, channels, frequencies)
    fault_hook.arm(WritePhase.BEFORE_FINALIZE)
    with pytest.raises(InjectedStorageFault):
        air_writer.close(MissionTerminalState.COMPLETED)

    assert air_writer.partial_path.exists()
    assert not air_writer.final_path.exists()
    with h5py.File(air_writer.partial_path, "r") as h5:
        assert str(h5.attrs["lifecycle_state"]) == "writing"
        assert str(h5["mission"].attrs["completion_kind"]) == ""
    assert read_committed_view(air_writer.partial_path).committed_record_count == 3


def test_fault_after_finalize_mark_is_durable_and_rename_still_possible(
    air_writer: RcScanIncrementalWriter,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    fault_hook: PhaseFaultHook,
) -> None:
    _append_three(air_writer, channels, frequencies)
    fault_hook.arm(WritePhase.AFTER_FINALIZE_MARK)
    with pytest.raises(InjectedStorageFault):
        air_writer.close(MissionTerminalState.COMPLETED)

    assert air_writer.partial_path.exists()
    assert not air_writer.final_path.exists()
    with h5py.File(air_writer.partial_path, "r") as h5:
        assert str(h5.attrs["lifecycle_state"]) == "finalized"
        assert str(h5["mission"].attrs["completion_kind"]) == "completed"
    assert read_committed_view(air_writer.partial_path).committed_record_count == 3

    # The partial is intact and can still be renamed (retry path).
    fault_hook.disarm(WritePhase.AFTER_FINALIZE_MARK)
    result = air_writer.close(MissionTerminalState.COMPLETED)
    assert result.final_path.exists()


def test_fault_before_rename_keeps_the_finalized_partial(
    air_writer: RcScanIncrementalWriter,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    fault_hook: PhaseFaultHook,
) -> None:
    _append_three(air_writer, channels, frequencies)
    fault_hook.arm(WritePhase.BEFORE_RENAME)
    with pytest.raises(InjectedStorageFault):
        air_writer.close(MissionTerminalState.COMPLETED)

    assert air_writer.partial_path.exists()
    assert not air_writer.final_path.exists()
    with h5py.File(air_writer.partial_path, "r") as h5:
        assert str(h5.attrs["lifecycle_state"]) == "finalized"

    fault_hook.disarm(WritePhase.BEFORE_RENAME)
    result = air_writer.close(MissionTerminalState.COMPLETED)
    assert result.final_path.exists()
    assert not air_writer.partial_path.exists()


class _FlakyRenameFacade(LocalFileSystemFacade):
    """Deterministic filesystem failure: the rename fails ``fail_times`` times.

    The facade is the writer's only filesystem surface, so "the operator fixed
    the disk" needs no private poking: the writer simply calls it again.
    """

    def __init__(self, fail_times: int = 1) -> None:
        self.attempts = 0
        self._fail_times = fail_times

    def replace(self, source: Path, target: Path) -> None:
        self.attempts += 1
        if self.attempts <= self._fail_times:
            raise OSError(28, "No space left on device")
        super().replace(source, target)


def test_rename_failure_preserves_partial_and_can_be_retried(
    scratch_dir: Path,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
    clock: ManualClock,
) -> None:
    filesystem = _FlakyRenameFacade(fail_times=1)
    writer = create_writer(
        scratch_dir,
        role=EndpointRole.AIR,
        channels=channels,
        frequencies=frequencies,
        mission_config=mission_config,
        clock=clock,
        filesystem=filesystem,
    )
    _append_three(writer, channels, frequencies)

    with pytest.raises(OSError):
        writer.close(MissionTerminalState.COMPLETED)
    assert filesystem.attempts == 1
    assert writer.state is WriterState.AWAITING_RENAME
    assert writer.partial_path.exists()
    assert not writer.final_path.exists()
    assert read_committed_view(writer.partial_path).committed_record_count == 3

    # The operator fixed the disk: the very same facade now succeeds.
    result = writer.close(MissionTerminalState.COMPLETED)
    assert filesystem.attempts == 2
    assert result.final_path.exists()
    assert not writer.partial_path.exists()


def test_rename_retry_refuses_when_the_target_appeared_in_the_meantime(
    scratch_dir: Path,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
    clock: ManualClock,
) -> None:
    """The rename-time guard: a target that appears while the rename is pending wins.

    This is the only path that reaches the second "target already exists"
    guard: the file is already finalized on disk and only the rename is left,
    so ``_finalize_file`` is never entered again and its guard cannot help.
    """
    filesystem = _FlakyRenameFacade(fail_times=1)
    writer = create_writer(
        scratch_dir,
        role=EndpointRole.AIR,
        channels=channels,
        frequencies=frequencies,
        mission_config=mission_config,
        clock=clock,
        filesystem=filesystem,
    )
    _append_three(writer, channels, frequencies)

    with pytest.raises(OSError):
        writer.close(MissionTerminalState.COMPLETED)
    assert writer.state is WriterState.AWAITING_RENAME

    sentinel = b"final artifact created by someone else while the rename was pending"
    writer.final_path.write_bytes(sentinel)

    with pytest.raises(DomainError) as error:
        writer.close(MissionTerminalState.COMPLETED)
    assert error.value.code is ErrorCode.INVALID_ARGUMENT
    assert error.value.context["path"] == str(writer.final_path)

    assert writer.final_path.read_bytes() == sentinel, "the existing artifact is untouched"
    assert filesystem.attempts == 1, "the rename was never attempted again"
    assert writer.partial_path.exists()
    assert schema.probe_rcscan_v2(writer.partial_path).lifecycle_state == "finalized"
    assert read_committed_view(writer.partial_path).committed_record_count == 3


def test_close_finalizes_and_atomically_renames(
    air_writer: RcScanIncrementalWriter,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    clock: ManualClock,
) -> None:
    _append_three(air_writer, channels, frequencies)
    clock.advance_utc(timedelta(seconds=30))
    ended = clock.utc_now()

    result = air_writer.close(MissionTerminalState.COMPLETED, ended_utc=ended)

    assert result.final_path == air_writer.final_path
    assert result.final_path.name == f"{_MISSION_ID}.rcscan"
    assert result.final_path.exists()
    assert not air_writer.partial_path.exists()
    assert result.committed_record_count == 3
    assert result.completion_kind is MissionTerminalState.COMPLETED
    assert air_writer.state is WriterState.FINALIZED

    probe = schema.probe_rcscan_v2(result.final_path)
    assert probe.lifecycle_state == "finalized"
    with h5py.File(result.final_path, "r") as h5:
        assert str(h5["mission"].attrs["completion_kind"]) == "completed"
        assert str(h5["mission"].attrs["ended_utc"]) == schema.to_utc_iso(ended)
        assert str(h5["mission"].attrs["started_utc"]) != ""
    view = read_committed_view(result.final_path)
    assert view.committed_record_count == 3
    assert view.half_written_rows == 0


@pytest.mark.parametrize(
    "kind",
    [
        MissionTerminalState.COMPLETED,
        MissionTerminalState.USER_STOPPED,
        MissionTerminalState.FAILED,
        MissionTerminalState.CRASH_RECOVERED,
    ],
    ids=lambda k: k.value,
)
def test_every_terminal_state_is_recorded_explicitly(
    scratch_dir: Path,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
    clock: ManualClock,
    kind: MissionTerminalState,
) -> None:
    writer = create_writer(
        scratch_dir,
        role=EndpointRole.AIR,
        channels=channels,
        frequencies=frequencies,
        mission_config=mission_config,
        clock=clock,
    )
    writer.append_trace(make_request(0, channels=channels, frequencies=frequencies))
    result = writer.close(kind)

    assert result.completion_kind is kind
    with h5py.File(result.final_path, "r") as h5:
        assert str(h5["mission"].attrs["completion_kind"]) == kind.value
        assert str(h5.attrs["lifecycle_state"]) == "finalized"


def test_close_is_idempotent(
    air_writer: RcScanIncrementalWriter,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
) -> None:
    _append_three(air_writer, channels, frequencies)
    first = air_writer.close(MissionTerminalState.COMPLETED)
    size_before = first.final_path.stat().st_size

    second = air_writer.close(MissionTerminalState.COMPLETED)
    third = air_writer.close(MissionTerminalState.USER_STOPPED)

    assert second == first
    assert third == first
    assert third.completion_kind is MissionTerminalState.COMPLETED
    assert first.final_path.stat().st_size == size_before
    assert not air_writer.partial_path.exists()


def test_close_refuses_to_overwrite_an_existing_target(
    air_writer: RcScanIncrementalWriter,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
) -> None:
    _append_three(air_writer, channels, frequencies)
    sentinel = b"pre-existing final artifact - must never be overwritten"
    air_writer.final_path.write_bytes(sentinel)

    with pytest.raises(DomainError) as error:
        air_writer.close(MissionTerminalState.COMPLETED)
    assert error.value.code is ErrorCode.INVALID_ARGUMENT

    assert air_writer.final_path.read_bytes() == sentinel
    assert air_writer.partial_path.exists()
    assert read_committed_view(air_writer.partial_path).committed_record_count == 3

    # No half state: nothing was renamed, nothing was truncated.
    with h5py.File(air_writer.partial_path, "r") as h5:
        assert str(h5.attrs["lifecycle_state"]) == "writing"


def test_finalized_file_cannot_be_appended_or_aborted(
    air_writer: RcScanIncrementalWriter,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
) -> None:
    air_writer.append_trace(make_request(0, channels=channels, frequencies=frequencies))
    air_writer.close(MissionTerminalState.COMPLETED)

    with pytest.raises(DomainError) as error:
        air_writer.append_trace(make_request(1, channels=channels, frequencies=frequencies))
    assert error.value.code is ErrorCode.INVALID_ARGUMENT
    with pytest.raises(DomainError):
        air_writer.abort()
    with pytest.raises(DomainError):
        air_writer.flush()


def test_abort_leaves_the_partial_for_recovery_and_blocks_appends(
    air_writer: RcScanIncrementalWriter,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
) -> None:
    air_writer.append_trace(make_request(0, channels=channels, frequencies=frequencies))
    air_writer.abort()

    assert air_writer.state is WriterState.ABORTED
    assert air_writer.partial_path.exists()
    with pytest.raises(DomainError) as error:
        air_writer.append_trace(make_request(1, channels=channels, frequencies=frequencies))
    assert error.value.code is ErrorCode.INVALID_ARGUMENT
    with pytest.raises(DomainError):
        air_writer.close(MissionTerminalState.COMPLETED)

    air_writer.abort()  # idempotent


def test_context_manager_aborts_without_finalizing(
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
        partial = writer.partial_path
        writer.append_trace(make_request(0, channels=channels, frequencies=frequencies))

    assert writer.state is WriterState.ABORTED
    assert partial.exists()
    assert not writer.final_path.exists()


# ---------------------------------------------------------------------------
# Fault bookkeeping
# ---------------------------------------------------------------------------


def test_commit_phase_sequence_is_data_then_flush_then_checkpoint(
    air_writer: RcScanIncrementalWriter,
    channels: tuple[ChannelSpec, ...],
    frequencies: np.ndarray,
    fault_hook: PhaseFaultHook,
) -> None:
    """The frozen commit order (DATA_FORMAT.md section 3) is pinned exactly.

    ``AFTER_DATA_FLUSH`` is announced by the writer's flush step itself, so
    dropping the flush before the checkpoint removes the phase and breaks
    this sequence.
    """
    air_writer.append_trace(make_request(0, channels=channels, frequencies=frequencies))
    assert list(fault_hook.observed) == [
        WritePhase.BEFORE_RAW_WRITE,
        WritePhase.AFTER_RAW_WRITE,
        WritePhase.AFTER_TRACE_COLUMNS,
        WritePhase.AFTER_DATA_FLUSH,
        WritePhase.AFTER_CHECKPOINT_WRITE,
        WritePhase.AFTER_COMMIT_FLUSH,
    ]

    air_writer.close(MissionTerminalState.COMPLETED)
    assert list(fault_hook.observed)[6:] == [
        WritePhase.BEFORE_FINALIZE,
        WritePhase.AFTER_FINALIZE_MARK,
        WritePhase.AFTER_FINALIZE_FLUSH,
        WritePhase.BEFORE_RENAME,
    ]


def test_local_filesystem_facade_rename_is_atomic_replace() -> None:
    """The default facade uses ``os.replace`` (same-volume atomic rename)."""
    source = Path(os.path.join(str(_scratch_root()), "facade-src.bin"))
    target = Path(os.path.join(str(_scratch_root()), "facade-dst.bin"))
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"partial")
    if target.exists():
        target.unlink()

    LocalFileSystemFacade().replace(source, target)

    assert not source.exists()
    assert target.read_bytes() == b"partial"
    assert LocalFileSystemFacade().exists(target)
    target.unlink()


def _scratch_root() -> Path:
    """System temp directory: no probe artifacts are left inside the project."""
    import tempfile

    return Path(tempfile.gettempdir()) / "uav-gpr-issue-010-probe"
