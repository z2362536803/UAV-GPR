"""ISSUE-008 contract tests: rcscan v2 physical HDF5 schema and codec.

The tests pin down the physical contract that ``docs/DATA_FORMAT.md``
leaves to the implementation:

- golden file structure: exact root attributes, groups, dataset names,
  dtypes, initial shapes, maxshapes and initial scalar checkpoints;
- fixed encoding decisions: little-endian float64/complex128/int64,
  variable-length UTF-8 JSON, fixed-width ASCII UUID/hash storage,
  timestamp columns in epoch nanoseconds;
- missing-value sentinels: int64 ``INT64_MIN``, float NaN paired with an
  explicit boolean presence column, variable-length columns defaulting
  to the empty string;
- fail-closed detection: unknown schema major/minor versions, unknown
  profiles, wrong or missing ``format_name``, non-HDF5 payloads and
  air/ground role-group violations.

No business writer lives here: the tested surface is the schema constants,
the codec helpers and the one-shot file creator.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
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
from uav_gpr.core.timeutil import MonotonicNs, to_utc_iso
from uav_gpr.storage import rcscan_v2 as schema

pytestmark = pytest.mark.contract


_FREQUENCY_POINTS = 16

_MISSION_ID = MissionId("0f0e8a3b-6f2d-4c1e-9a7b-112233445566")
_DEVICE_ID = DeviceId("d1c0ffee-0000-4000-8000-000000000001")
_FILE_ID = GroundFileId("aaaaaaa2-0000-4000-8000-000000000002")
_AIR_FILE_ID = AirFileId("aaaaaaa1-0000-4000-8000-000000000002")


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def channels() -> tuple[ChannelSpec, ChannelSpec]:
    """Two-channel contract (multi-channel by construction)."""
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


@pytest.fixture()
def frequencies() -> np.ndarray:
    return np.linspace(800e6, 2600e6, _FREQUENCY_POINTS)


@pytest.fixture()
def mission_config(channels: tuple[ChannelSpec, ...]) -> MissionConfig:
    return MissionConfig(
        frequency_start_hz=800e6,
        frequency_stop_hz=2600e6,
        frequency_points=_FREQUENCY_POINTS,
        if_bw_hz=1_000.0,
        power_dbm=-3.0,
        channels=channels,
        acquisition_mode=AcquisitionMode.FIXED_COUNT,
        planned_trace_count=240,
        target_interval_s=0.1,
        gnss_max_age_s=2.0,
        gnss_no_fix_policy=GnssNoFixPolicy.RECORD_WITHOUT_POSITION,
        calibration_profile_id=None,
        apply_calibration=False,
        background_reference_id=None,
        apply_background=False,
        created_utc=datetime(2026, 8, 27, 12, 0, 0, tzinfo=UTC),
        software_version="0.1.0.dev0",
    )


@pytest.fixture()
def layout_kwargs(
    channels: tuple[ChannelSpec, ChannelSpec],
    frequencies: np.ndarray,
    mission_config: MissionConfig,
) -> dict[str, object]:
    return {
        "mission_id": _MISSION_ID,
        "device_id": _DEVICE_ID,
        "file_id": _AIR_FILE_ID,
        "created_utc": datetime(2026, 8, 27, 12, 0, 0, tzinfo=UTC),
        "completed_utc": None,
        "completion_kind": None,
        "channels": channels,
        "frequencies_hz": frequencies,
        "config_json": mission_config.to_canonical_json(),
        "config_sha256": mission_config.config_sha256,
        "writer_version": "uav-gpr.test.1",
    }


def load_golden_manifest() -> dict[str, object]:
    """Load the independent golden manifest (never derived from production)."""
    path = Path(__file__).with_name("rcscan_v2_golden.json")
    result = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(result, dict), "golden manifest must be an object"
    return result


def _as_text(value: object) -> str:
    """Normalize an HDF5 string cell (bytes or str) to text."""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _from_epoch_ns(ns: int) -> datetime:
    """UTC datetime from the frozen epoch-nanosecond column format."""
    return datetime.fromtimestamp(ns / 1_000_000_000, tz=UTC)


def _write_row_to_h5(h5: h5py.File, cells: dict[str, object]) -> None:
    for path, value in cells.items():
        if path not in h5:
            continue
        dataset = h5[path]
        dataset.resize((1,))
        dataset[0] = value  # type: ignore[index]


def _read_row_from_h5(h5: h5py.File, cells: dict[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for path in cells:
        dataset = h5[path]
        value = dataset[0]
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        result[path] = value
    return result


def _dataset_paths(h5: h5py.File) -> set[str]:
    paths: set[str] = set()

    def visit(name: str, obj: object) -> None:
        if isinstance(obj, h5py.Dataset):
            paths.add("/" + name)

    h5.visititems(visit)
    return paths


def _assert_manifest_matches_h5(
    manifest: dict[str, object], file_path: Path, role: str
) -> None:
    """Assert a given manifest and a real HDF5 file agree line by line."""
    datasets = manifest["datasets"]
    assert isinstance(datasets, list)
    with h5py.File(file_path, "r") as h5:
        expected: set[str] = set()
        for entry in datasets:
            assert isinstance(entry, dict), f"bad manifest entry {entry!r}"
            path = str(entry["path"])
            required_for = entry["required_for"]
            optional_for = entry.get("optional_for", [])
            assert isinstance(required_for, list)
            assert isinstance(optional_for, list)
            role_required = role in required_for
            role_optional = role in optional_for
            if role_required:
                expected.add(path)
                assert path in h5, f"missing dataset {path}"
            elif role_optional:
                if path not in h5:
                    continue
                expected.add(path)
            else:
                assert path not in h5, f"unexpected dataset {path}"
                continue
            dataset = h5[path]
            assert tuple(dataset.shape) == tuple(entry["initial_shape"]), path
            assert tuple(dataset.maxshape) == tuple(entry["maxshape"]), path
            kind = str(entry["kind"])
            if kind == "vlen_utf8":
                info = h5py.check_string_dtype(dataset.dtype)
                assert info is not None and info.encoding == "utf-8", path
                assert info.length is None, path
            elif kind == "ascii_fixed":
                info = h5py.check_string_dtype(dataset.dtype)
                assert info is not None and info.encoding == "ascii", path
                expected_length = int(str(entry["dtype"]).split(":")[1])
                assert info.length == expected_length, path
            else:
                assert dataset.dtype.byteorder in ("<", "="), path
                assert dataset.dtype == np.dtype(str(entry["dtype"])), path
            chunks = entry["chunks"]
            if chunks is None:
                assert dataset.chunks is None, path
            else:
                assert dataset.chunks is not None, path
                assert tuple(dataset.chunks) == tuple(chunks), path
            assert dataset.compression == entry["compression"], path
        actual = _dataset_paths(h5)
        assert actual == expected, (
            f"dataset key set mismatch: extra={sorted(actual - expected)}, "
            f"missing={sorted(expected - actual)}"
        )


@pytest.fixture()
def air_path(tmp_path: Path, layout_kwargs: dict[str, object]) -> Path:
    """A freshly created two-channel air-end golden skeleton."""
    kwargs = dict(layout_kwargs)
    kwargs["file_role"] = EndpointRole.AIR
    kwargs["file_id"] = _AIR_FILE_ID
    path = tmp_path / "air.partial.rcscan"
    schema.create_rcscan_v2(path, **kwargs)  # type: ignore[arg-type]
    return path


@pytest.fixture()
def ground_path(tmp_path: Path, layout_kwargs: dict[str, object]) -> Path:
    """A freshly created two-channel ground-end golden skeleton."""
    kwargs = dict(layout_kwargs)
    kwargs["file_role"] = EndpointRole.GROUND
    kwargs["file_id"] = _FILE_ID
    path = tmp_path / "ground.partial.rcscan"
    schema.create_rcscan_v2(path, **kwargs)  # type: ignore[arg-type]
    return path


def _mutate_root_attr(path: Path, name: str, value: object) -> None:
    """Flip a root attribute in place (probe-failure fixtures)."""
    with h5py.File(path, "r+") as h5:
        h5.attrs[name] = value


# ---------------------------------------------------------------------------
# Golden structure: root and mission attributes
# ---------------------------------------------------------------------------


class TestRootAttributes:
    def test_create_then_probe_reports_frozen_identity(self, air_path: Path) -> None:
        probe = schema.probe_rcscan_v2(air_path)
        assert probe.format_name == "rcscan"
        assert probe.schema_version == 2
        assert probe.profile == "uav_gpr"
        assert probe.file_id == str(_AIR_FILE_ID)
        assert probe.file_role is EndpointRole.AIR
        assert probe.writer_version == "uav-gpr.test.1"
        assert probe.lifecycle_state == "writing"

    def test_root_attrs_are_exactly_the_documented_set(self, air_path: Path) -> None:
        with h5py.File(air_path, "r") as h5:
            attrs = {name: h5.attrs[name] for name in h5.attrs}
        assert set(attrs) == {
            "format_name",
            "schema_version",
            "profile",
            "file_id",
            "file_role",
            "writer_version",
            "lifecycle_state",
        }
        assert attrs["format_name"] == "rcscan"
        assert attrs["profile"] == "uav_gpr"
        # schema_version is stored as a scalar integer attribute.
        assert isinstance(attrs["schema_version"], (int, np.integer))
        assert int(attrs["schema_version"]) == 2

    def test_mission_attributes_and_config_payload_pinned(
        self, air_path: Path, layout_kwargs: dict[str, object]
    ) -> None:
        with h5py.File(air_path, "r") as h5:
            mission = h5["/mission"]
            assert sorted(mission.attrs) == [
                "completion_kind",
                "config_sha256",
                "created_utc",
                "device_id",
                "ended_utc",
                "mission_id",
                "started_utc",
            ]
            assert mission.attrs["mission_id"] == str(_MISSION_ID)
            assert mission.attrs["device_id"] == str(_DEVICE_ID)
            assert mission.attrs["config_sha256"] == layout_kwargs["config_sha256"]
            assert mission.attrs["started_utc"] == ""
            assert mission.attrs["ended_utc"] == ""
            assert mission.attrs["completion_kind"] == ""
            blob = h5["/mission/config_json"][()]
        stored_text = blob.decode("utf-8") if isinstance(blob, bytes) else str(blob)
        assert json.loads(stored_text) == json.loads(
            str(layout_kwargs["config_json"])
        )


# ---------------------------------------------------------------------------
# Golden structure: groups / datasets / dtypes / shapes
# ---------------------------------------------------------------------------


class TestDatasetContracts:
    def test_every_declared_contract_exists_with_exact_shape_and_dtype(
        self, air_path: Path
    ) -> None:
        manifest = load_golden_manifest()
        _assert_manifest_matches_h5(manifest, air_path, "air")
        # The manifest itself must really pin the layout (sanity guard).
        assert len(manifest["datasets"]) > 40

    def test_production_dataset_contracts_match_independent_manifest(
        self,
    ) -> None:
        manifest = load_golden_manifest()
        manifest_paths = {str(entry["path"]) for entry in manifest["datasets"]}
        contracts = {
            contract.path: contract
            for contract in schema.dataset_contracts(
                channel_count=2, frequency_points=_FREQUENCY_POINTS
            )
        }
        assert set(contracts) == manifest_paths, (
            f"production paths differ: extra={sorted(set(contracts) - manifest_paths)}, "
            f"missing={sorted(manifest_paths - set(contracts))}"
        )
        for entry in manifest["datasets"]:
            path = str(entry["path"])
            contract = contracts[path]
            assert contract.initial_shape == tuple(entry["initial_shape"]), path
            assert contract.maxshape == tuple(entry["maxshape"]), path
            assert contract.kind is schema.ValueKind(str(entry["kind"])), path
            if entry["kind"] != "vlen_utf8" and entry["kind"] != "ascii_fixed":
                assert contract.dtype == np.dtype(str(entry["dtype"])), path
            chunks = entry["chunks"]
            assert contract.chunks == (
                tuple(chunks) if chunks is not None else None
            ), path
            assert contract.compression == entry["compression"], path
            assert contract.optional is (not bool(entry["required_for"])), path

    def test_golden_manifest_deletes_or_renames_are_detected(
        self, air_path: Path
    ) -> None:
        manifest = load_golden_manifest()
        datasets = manifest["datasets"]
        assert isinstance(datasets, list)
        datasets[0] = dict(
            datasets[0], path="/mission/config_json_renamed"  # type: ignore[index]
        )
        with pytest.raises(AssertionError):
            _assert_manifest_matches_h5(manifest, air_path, "air")

    def test_air_required_groups_present(self, air_path: Path) -> None:
        with h5py.File(air_path, "r") as h5:
            for group in (
                "/mission",
                "/channels",
                "/axes",
                "/frequency",
                "/trace_metadata",
                "/gnss",
                "/acquisition",
                "/transport",  # air-end mandatory block
                "/checkpoints",
            ):
                assert group in h5

    def test_ground_file_has_no_transport_group(self, ground_path: Path) -> None:
        probe = schema.probe_rcscan_v2(ground_path)
        assert probe.file_role is EndpointRole.GROUND
        with h5py.File(ground_path, "r") as h5:
            assert "/transport" not in h5
            for group in ("/mission", "/channels", "/axes", "/frequency"):
                assert group in h5

    def test_arrays_start_empty_and_extend_on_trace_axis(self, air_path: Path) -> None:
        with h5py.File(air_path, "r") as h5:
            raw = h5["/frequency/raw"]
            assert raw.shape == (0, 2, _FREQUENCY_POINTS)
            assert raw.maxshape == (None, 2, _FREQUENCY_POINTS)
            assert raw.chunks is not None
            assert raw.chunks[0] == 1  # trace-major chunking: one trace per chunk
            assert raw.compression is None  # reserved pending CPU/disk benchmark
            axis = h5["/axes/frequencies_hz"][...]
        assert axis.dtype == np.dtype("<f8")
        np.testing.assert_array_equal(axis, np.linspace(800e6, 2600e6, 16))

    def test_optional_axes_absent_until_created(self, air_path: Path) -> None:
        with h5py.File(air_path, "r") as h5:
            assert "/axes/time_base_s" not in h5
            assert "/axes/time_processed_s" not in h5
            assert "/frequency/calibrated" not in h5
            assert "/time_base" not in h5
            assert "/time_processed" not in h5
        probe = schema.probe_rcscan_v2(air_path)
        assert probe.optional_axes_present == {
            "/axes/time_base_s": False,
            "/axes/time_processed_s": False,
        }

    def test_single_channel_creator_chunk_shape(self, tmp_path: Path) -> None:
        single = (
            ChannelSpec(
                channel_id="hh_s11",
                logical_polarization=LogicalPolarization.HH,
                s_parameter=SParameter.S11,
                display_name="single",
            ),
        )
        config = MissionConfig(
            frequency_start_hz=800e6,
            frequency_stop_hz=2600e6,
            frequency_points=_FREQUENCY_POINTS,
            if_bw_hz=1_000.0,
            power_dbm=-3.0,
            channels=single,
            acquisition_mode=AcquisitionMode.CONTINUOUS,
            planned_trace_count=None,
            target_interval_s=0.1,
            gnss_max_age_s=2.0,
            gnss_no_fix_policy=GnssNoFixPolicy.ABORT_TASK,
            calibration_profile_id=None,
            apply_calibration=False,
            background_reference_id=None,
            apply_background=False,
            created_utc=datetime(2026, 8, 27, 13, 0, 0, tzinfo=UTC),
            software_version="0.1.0.dev0",
        )
        path = tmp_path / "one.partial.rcscan"
        schema.create_rcscan_v2(
            path,
            mission_id=MissionId("0f0e8a3b-6f2d-4c1e-9a7b-112233445567"),
            device_id=DeviceId("d1c0ffee-0000-4000-8000-000000000003"),
            file_id=AirFileId("aaaaaaa1-0000-4000-8000-000000000004"),
            created_utc=datetime(2026, 8, 27, 13, 0, 0, tzinfo=UTC),
            completed_utc=None,
            completion_kind=None,
            file_role=EndpointRole.AIR,
            channels=single,
            frequencies_hz=np.linspace(800e6, 2600e6, _FREQUENCY_POINTS),
            config_json=config.to_canonical_json(),
            config_sha256=config.config_sha256,
            writer_version="uav-gpr.test.1",
        )
        with h5py.File(path, "r") as h5:
            assert h5["/frequency/raw"].chunks == (1, 1, _FREQUENCY_POINTS)

    def test_checkpoint_scalars_initialized(self, air_path: Path) -> None:
        with h5py.File(air_path, "r") as h5:
            committed = h5["/checkpoints/committed_record_count"]
            last_index = h5["/checkpoints/last_trace_index"]
            assert int(committed[0]) == 0
            assert int(last_index[0]) == schema.MISSING_INT64
            updated = h5["/checkpoints/updated_utc"][0]
        decoded = updated.decode("utf-8") if isinstance(updated, bytes) else updated
        assert decoded == to_utc_iso(datetime(2026, 8, 27, 12, 0, 0, tzinfo=UTC))
        assert decoded.endswith("Z")


# ---------------------------------------------------------------------------
# Codec: identity + JSON + hash columns
# ---------------------------------------------------------------------------


class TestCodecEncoding:
    def test_channel_definitions_round_trip(
        self, air_path: Path, channels: tuple[ChannelSpec, ...]
    ) -> None:
        probe = schema.probe_rcscan_v2(air_path)
        assert probe.channel_ids == tuple(c.channel_id for c in channels)
        with h5py.File(air_path, "r") as h5:
            blob = h5["/channels/definitions_json"][0]
        text = blob.decode("utf-8") if isinstance(blob, bytes) else str(blob)
        restored = json.loads(text)
        assert restored == [
            {
                "channel_id": "hh_s11",
                "logical_polarization": "hh",
                "s_parameter": "s11",
                "display_name": "H height S11",
                "antenna_note": None,
            },
            {
                "channel_id": "vv_s22",
                "logical_polarization": "vv",
                "s_parameter": "s22",
                "display_name": "V vertical port",
                "antenna_note": "port B",
            },
        ]

    def test_uid_and_hash_columns_have_fixed_ascii_width(self, air_path: Path) -> None:
        with h5py.File(air_path, "r") as h5:
            uid_info = h5py.check_string_dtype(
                h5["/trace_metadata/trace_uid"].dtype
            )
            sha_info = h5py.check_string_dtype(
                h5["/trace_metadata/raw_trace_sha256"].dtype
            )
        assert uid_info is not None and uid_info.length == 36
        assert sha_info is not None and sha_info.length == 64

    def test_canonical_json_codec_round_trip_and_determinism(self) -> None:
        first = schema.dumps_utf8_json({"b": 1, "a": [1, 2]})
        second = schema.dumps_utf8_json({"a": [1, 2], "b": 1})
        assert first == '{"a":[1,2],"b":1}'
        assert first == second
        assert schema.loads_utf8_json(first) == {"a": [1, 2], "b": 1}

    def test_canonical_json_codec_rejects_non_finite(self) -> None:
        with pytest.raises(ValueError):
            schema.dumps_utf8_json({"bad": float("nan")})

    def test_int64_timestamp_sentinel_round_trip(self) -> None:
        encoded = schema.encode_optional_int64(
            payloads=[1_268_247_504_123_456_789, None, 42]
        )
        assert encoded.dtype == np.dtype("int64")
        assert encoded.tolist() == [
            1_268_247_504_123_456_789,
            schema.MISSING_INT64,
            42,
        ]
        assert schema.missing_int64_mask(encoded).tolist() == [False, True, False]

    def test_float_missing_column_semantics(self) -> None:
        payload = np.array([np.nan, 2.5], dtype="<f8")
        assert bool(np.isnan(payload[0]))  # NaN marks the missing value...
        present = schema.bool_column([False, True])
        assert present.dtype == np.int64
        assert present.tolist() == [0, 1]  # ...and the int64 0/1 column says why
        decoded = schema.decode_bool_column([0, 1])
        assert decoded.tolist() == [False, True]
        with pytest.raises(ValueError):
            schema.decode_bool_column([0, 2])


# ---------------------------------------------------------------------------
# Fail-closed probing
# ---------------------------------------------------------------------------


class TestFailClosedDetection:
    def test_unknown_newer_major_version_rejected(self, air_path: Path) -> None:
        mutated = air_path.with_suffix(".tmp.h5")
        air_path.replace(mutated)
        try:
            _mutate_root_attr(mutated, "schema_version", 3)
            with pytest.raises(DomainError) as excinfo:
                schema.probe_rcscan_v2(mutated)
        finally:
            mutated.replace(air_path)
        assert excinfo.value.code is ErrorCode.UNSUPPORTED_SCHEMA_VERSION
        assert excinfo.value.context["detected_version"] == 3

    def test_unknown_older_major_version_rejected(self, air_path: Path) -> None:
        mutated = air_path.with_suffix(".tmp.h5")
        air_path.replace(mutated)
        try:
            _mutate_root_attr(mutated, "schema_version", 1)
            with pytest.raises(DomainError) as excinfo:
                schema.probe_rcscan_v2(mutated)
        finally:
            mutated.replace(air_path)
        assert excinfo.value.code is ErrorCode.UNSUPPORTED_SCHEMA_VERSION
        assert excinfo.value.context.get("known_major") is False

    def test_unknown_profile_rejected(self, air_path: Path) -> None:
        mutated = air_path.with_suffix(".tmp.h5")
        air_path.replace(mutated)
        try:
            _mutate_root_attr(mutated, "profile", "reinforcement_gauge_v1")
            with pytest.raises(DomainError) as excinfo:
                schema.probe_rcscan_v2(mutated)
        finally:
            mutated.replace(air_path)
        assert excinfo.value.code is ErrorCode.UNSUPPORTED_SCHEMA_VERSION
        assert excinfo.value.context["field"] == "profile"

    def test_wrong_format_name_rejected(self, air_path: Path) -> None:
        mutated = air_path.with_suffix(".tmp.h5")
        air_path.replace(mutated)
        try:
            _mutate_root_attr(mutated, "format_name", "something_else")
            with pytest.raises(DomainError) as excinfo:
                schema.probe_rcscan_v2(mutated)
        finally:
            mutated.replace(air_path)
        assert excinfo.value.context["field"] == "format_name"

    def test_non_hdf5_payload_rejected(self, tmp_path: Path) -> None:
        fake = tmp_path / "fake.rcscan"
        fake.write_bytes(b"definitely not hdf5, long enough to fail cleanly")
        with pytest.raises(DomainError) as excinfo:
            schema.probe_rcscan_v2(fake)
        assert excinfo.value.context["path"].endswith("fake.rcscan")

    def test_plain_hdf5_without_marker_rejected(self, tmp_path: Path) -> None:
        other = tmp_path / "other.h5"
        with h5py.File(other, "w"):
            pass
        with pytest.raises(DomainError) as excinfo:
            schema.probe_rcscan_v2(other)
        assert excinfo.value.context["field"] == "format_name"

    def test_air_file_requires_transport_group(
        self, tmp_path: Path, layout_kwargs: dict[str, object]
    ) -> None:
        kwargs = dict(layout_kwargs)
        kwargs["file_role"] = EndpointRole.AIR
        path = tmp_path / "broken.partial.rcscan"
        schema.create_rcscan_v2(path, **kwargs)  # type: ignore[arg-type]
        with h5py.File(path, "r+") as h5:
            del h5["/transport"]
        with pytest.raises(DomainError) as excinfo:
            schema.probe_rcscan_v2(path)
        assert excinfo.value.context["missing"] == ["/transport"]

    def test_ground_file_optional_transport_group(self, ground_path: Path) -> None:
        # Absent ground /transport is valid (Option A).
        probe = schema.probe_rcscan_v2(ground_path)
        assert probe.file_role is EndpointRole.GROUND
        with h5py.File(ground_path, "r") as h5:
            assert "/transport" not in h5
        # Present ground /transport with the frozen structure is also valid.
        with h5py.File(ground_path, "r+") as h5:
            h5.create_group("/transport")
            for contract in schema.dataset_contracts(
                channel_count=2, frequency_points=_FREQUENCY_POINTS
            ):
                if contract.path.startswith("/transport"):
                    h5.create_dataset(
                        contract.path,
                        shape=contract.initial_shape,
                        maxshape=contract.maxshape,
                        dtype=contract.dtype,
                        chunks=contract.chunks,
                        compression=contract.compression,
                    )
        probe = schema.probe_rcscan_v2(ground_path)
        assert probe.file_role is EndpointRole.GROUND


# ---------------------------------------------------------------------------
# Creator input validation (fail-closed before any file is touched)
# ---------------------------------------------------------------------------


class TestCreatorValidation:
    def test_existing_target_is_refused(
        self, air_path: Path, layout_kwargs: dict[str, object]
    ) -> None:
        with pytest.raises(DomainError) as excinfo:
            schema.create_rcscan_v2(air_path, **layout_kwargs)  # type: ignore[arg-type]
        assert "already exists" in excinfo.value.message
        assert excinfo.value.context["path"].endswith("air.partial.rcscan")

    def test_unknown_completion_kind_is_refused(
        self, tmp_path: Path, layout_kwargs: dict[str, object]
    ) -> None:
        kwargs = dict(layout_kwargs)
        kwargs["completion_kind"] = "kind_of_made_up"
        path = tmp_path / "x.partial.rcscan"
        with pytest.raises(ValueError, match="completion_kind"):
            schema.create_rcscan_v2(path, **kwargs)  # type: ignore[arg-type]
        assert not path.exists()

    def test_writer_version_token_enforced(
        self, tmp_path: Path, layout_kwargs: dict[str, object]
    ) -> None:
        kwargs = dict(layout_kwargs)
        kwargs["writer_version"] = "not a version token"
        path = tmp_path / "y.partial.rcscan"
        with pytest.raises(ValueError, match="writer_version"):
            schema.create_rcscan_v2(path, **kwargs)  # type: ignore[arg-type]
        assert not path.exists()

    def test_empty_channel_contract_refused(
        self,
        tmp_path: Path,
        frequencies: np.ndarray,
        mission_config: MissionConfig,
    ) -> None:
        path = tmp_path / "z.partial.rcscan"
        with pytest.raises(Exception, match=r"[Cc]hannel"):
            schema.create_rcscan_v2(
                path,
                mission_id=MissionId("0f0e8a3b-6f2d-4c1e-9a7b-112233445568"),
                device_id=DeviceId("d1c0ffee-0000-4000-8000-000000000009"),
                file_id=GroundFileId("aaaaaaa2-0000-4000-8000-00000000000a"),
                created_utc=datetime(2026, 8, 27, tzinfo=UTC),
                completed_utc=None,
                completion_kind=None,
                file_role=EndpointRole.GROUND,
                channels=(),
                frequencies_hz=frequencies,
                config_json=mission_config.to_canonical_json(),
                config_sha256=mission_config.config_sha256,
                writer_version="uav-gpr.test.1",
            )
        assert not path.exists()

    def test_descending_frequency_axis_refused(
        self,
        tmp_path: Path,
        channels: tuple[ChannelSpec, ChannelSpec],
        mission_config: MissionConfig,
    ) -> None:
        descending = np.linspace(2600e6, 800e6, _FREQUENCY_POINTS)
        path = tmp_path / "down.partial.rcscan"
        with pytest.raises(Exception, match=r"[aA]xis"):
            schema.create_rcscan_v2(
                path,
                mission_id=MissionId("0f0e8a3b-6f2d-4c1e-9a7b-112233445569"),
                device_id=DeviceId("d1c0ffee-0000-4000-8000-00000000000b"),
                file_id=GroundFileId("aaaaaaa2-0000-4000-8000-00000000000c"),
                created_utc=datetime(2026, 8, 27, tzinfo=UTC),
                completed_utc=None,
                completion_kind=None,
                file_role=EndpointRole.GROUND,
                channels=channels,
                frequencies_hz=descending,
                config_json=mission_config.to_canonical_json(),
                config_sha256=mission_config.config_sha256,
                writer_version="uav-gpr.test.1",
            )
        assert not path.exists()


# ---------------------------------------------------------------------------
# Repair round: strict fail-closed boundaries + codec closure
# ---------------------------------------------------------------------------


class TestStrictFailClosedRepair:
    def test_schema_version_2_5_is_rejected_not_truncated(
        self, air_path: Path
    ) -> None:
        mutated = air_path.with_suffix(".tmp.h5")
        air_path.replace(mutated)
        try:
            _mutate_root_attr(mutated, "schema_version", 2.5)
            with pytest.raises(DomainError) as excinfo:
                schema.probe_rcscan_v2(mutated)
        finally:
            mutated.replace(air_path)
        assert excinfo.value.code is ErrorCode.UNSUPPORTED_SCHEMA_VERSION
        assert excinfo.value.context.get("detected_version") == 2.5
        assert excinfo.value.context.get("known_major") is False

    def test_json_nan_and_duplicate_keys_rejected(self) -> None:
        with pytest.raises(ValueError):
            schema.loads_utf8_json('{"value": NaN}')
        with pytest.raises(ValueError):
            schema.loads_utf8_json('{"a": 1, "a": 2}')

    def test_creator_rejects_non_uuid_identifier(
        self, tmp_path: Path, layout_kwargs: dict[str, object]
    ) -> None:
        kwargs = dict(layout_kwargs)
        kwargs["mission_id"] = "not-a-uuid"
        path = tmp_path / "bad_id.partial.rcscan"
        with pytest.raises(TypeError, match="mission_id"):
            schema.create_rcscan_v2(path, **kwargs)  # type: ignore[arg-type]
        assert not path.exists()

    def test_creator_rejects_non_hex_digest(
        self, tmp_path: Path, layout_kwargs: dict[str, object]
    ) -> None:
        kwargs = dict(layout_kwargs)
        kwargs["config_sha256"] = "z" * 64
        path = tmp_path / "bad_digest.partial.rcscan"
        with pytest.raises(ValueError, match="config_sha256"):
            schema.create_rcscan_v2(path, **kwargs)  # type: ignore[arg-type]
        assert not path.exists()

    def test_creator_rejects_writing_with_completion_kind(
        self, tmp_path: Path, layout_kwargs: dict[str, object]
    ) -> None:
        kwargs = dict(layout_kwargs)
        kwargs["completion_kind"] = "completed"
        path = tmp_path / "bad_state.partial.rcscan"
        with pytest.raises(ValueError, match="completion_kind"):
            schema.create_rcscan_v2(path, **kwargs)  # type: ignore[arg-type]
        assert not path.exists()

    def test_creator_rejects_completed_utc_on_writing_skeleton(
        self, tmp_path: Path, layout_kwargs: dict[str, object]
    ) -> None:
        kwargs = dict(layout_kwargs)
        kwargs["completed_utc"] = datetime(2026, 8, 27, 13, 0, 0, tzinfo=UTC)
        path = tmp_path / "bad_completed.partial.rcscan"
        with pytest.raises(ValueError, match="completed_utc"):
            schema.create_rcscan_v2(path, **kwargs)  # type: ignore[arg-type]
        assert not path.exists()

    def test_creator_rejects_non_finite_json_config(
        self, tmp_path: Path, layout_kwargs: dict[str, object]
    ) -> None:
        kwargs = dict(layout_kwargs)
        kwargs["config_json"] = '{"n": NaN}'
        path = tmp_path / "bad_config.partial.rcscan"
        with pytest.raises(ValueError, match="config_json"):
            schema.create_rcscan_v2(path, **kwargs)  # type: ignore[arg-type]
        assert not path.exists()

    def test_creator_rejects_non_object_json_config(
        self, tmp_path: Path, layout_kwargs: dict[str, object]
    ) -> None:
        kwargs = dict(layout_kwargs)
        kwargs["config_json"] = "[1, 2, 3]"
        path = tmp_path / "bad_config2.partial.rcscan"
        with pytest.raises(ValueError, match="JSON object"):
            schema.create_rcscan_v2(path, **kwargs)  # type: ignore[arg-type]
        assert not path.exists()

    def test_creator_rejects_wrong_file_id_for_role(
        self, tmp_path: Path, layout_kwargs: dict[str, object]
    ) -> None:
        path = tmp_path / "wrong_role.partial.rcscan"
        kwargs = dict(layout_kwargs)
        kwargs["file_id"] = _FILE_ID  # GroundFileId on an AIR skeleton
        with pytest.raises(TypeError, match="file_id"):
            schema.create_rcscan_v2(path, **kwargs)  # type: ignore[arg-type]
        assert not path.exists()
        kwargs = dict(layout_kwargs)
        kwargs["file_role"] = EndpointRole.GROUND
        kwargs["file_id"] = _AIR_FILE_ID
        with pytest.raises(TypeError, match="file_id"):
            schema.create_rcscan_v2(path, **kwargs)  # type: ignore[arg-type]
        assert not path.exists()

    def test_creator_rejects_config_layout_mismatch(
        self,
        tmp_path: Path,
        layout_kwargs: dict[str, object],
        channels: tuple[ChannelSpec, ChannelSpec],
    ) -> None:
        single = MissionConfig(
            frequency_start_hz=800e6,
            frequency_stop_hz=2600e6,
            frequency_points=_FREQUENCY_POINTS,
            if_bw_hz=1_000.0,
            power_dbm=-3.0,
            channels=channels[:1],
            acquisition_mode=AcquisitionMode.FIXED_COUNT,
            planned_trace_count=240,
            target_interval_s=0.1,
            gnss_max_age_s=2.0,
            gnss_no_fix_policy=GnssNoFixPolicy.RECORD_WITHOUT_POSITION,
            calibration_profile_id=None,
            apply_calibration=False,
            background_reference_id=None,
            apply_background=False,
            created_utc=datetime(2026, 8, 27, 12, 0, 0, tzinfo=UTC),
            software_version="0.1.0.dev0",
        )
        kwargs = dict(layout_kwargs)
        kwargs["config_json"] = single.to_canonical_json()
        kwargs["config_sha256"] = single.config_sha256
        path = tmp_path / "layout_mismatch.partial.rcscan"
        with pytest.raises(ValueError, match="channels"):
            schema.create_rcscan_v2(path, **kwargs)  # type: ignore[arg-type]
        assert not path.exists()

    def test_creator_rejects_config_digest_mismatch(
        self, tmp_path: Path, layout_kwargs: dict[str, object]
    ) -> None:
        kwargs = dict(layout_kwargs)
        kwargs["config_sha256"] = "b" * 64
        path = tmp_path / "digest_mismatch.partial.rcscan"
        with pytest.raises(ValueError, match="config_sha256"):
            schema.create_rcscan_v2(path, **kwargs)  # type: ignore[arg-type]
        assert not path.exists()

    def test_probe_accepts_nonempty_transport(self, air_path: Path) -> None:
        with h5py.File(air_path, "r+") as h5:
            for contract in schema.dataset_contracts(2, _FREQUENCY_POINTS):
                if contract.path.startswith("/transport"):
                    h5[contract.path].resize((1,))
        probe = schema.probe_rcscan_v2(air_path)
        assert probe.file_role is EndpointRole.AIR

    def test_strict_primitive_codecs_reject_type_fraud(self) -> None:
        with pytest.raises(TypeError):
            schema.bool_column([2])
        with pytest.raises(TypeError):
            schema.bool_column(["false"])
        with pytest.raises(TypeError):
            schema.decode_bool_column([0.5])
        with pytest.raises(TypeError):
            schema.decode_bool_column([1.5])
        with pytest.raises(TypeError):
            schema.decode_bool_column(["1"])
        with pytest.raises(TypeError):
            schema.encode_optional_int64([True])
        with pytest.raises(TypeError):
            schema.encode_optional_int64([2.9])
        encoded = schema.encode_optional_int64([None, 42])
        assert encoded.dtype == np.dtype("<i8")
        assert encoded.tolist() == [schema.MISSING_INT64, 42]

    def test_exact_utc_ns_codec_and_golden_vector(self) -> None:
        dt = datetime(2026, 8, 27, 12, 0, 0, 500000, tzinfo=UTC)
        ns = schema.encode_utc_ns(dt)
        epoch = datetime(1970, 1, 1, tzinfo=UTC)
        delta = dt - epoch
        expected = (
            delta.days * 86_400_000_000_000
            + delta.seconds * 1_000_000_000
            + delta.microseconds * 1_000
        )
        assert ns == expected
        assert schema.decode_utc_ns(ns) == dt
        for microseconds in (1, 654321, 999999):
            sample = datetime(
                2026, 8, 27, 12, 0, 0, microseconds, tzinfo=UTC
            )
            encoded = schema.encode_utc_ns(sample)
            assert schema.decode_utc_ns(encoded) == sample
        with pytest.raises(ValueError):
            schema.decode_utc_ns(schema.MISSING_INT64)

    def test_utc_ns_range_and_sentinel_collision(self) -> None:
        with pytest.raises(ValueError):
            schema.encode_optional_int64([schema.MISSING_INT64])
        with pytest.raises(ValueError):
            schema.decode_utc_ns(np.iinfo(np.int64).min)
        with pytest.raises(ValueError):
            schema.encode_utc_ns(datetime.min.replace(tzinfo=UTC))
        with pytest.raises(ValueError):
            schema.encode_utc_ns(datetime.max.replace(tzinfo=UTC))
        with pytest.raises(ValueError):
            schema.decode_utc_ns(np.iinfo(np.int64).max + 1)

    def test_presence_mask_payload_validation(self) -> None:
        known = {"hdop": 1, "speed": 2}
        schema.validate_presence_mask(1, {"hdop": 0.0, "speed": None}, known)
        with pytest.raises(ValueError):
            schema.validate_presence_mask(1, {"hdop": None, "speed": None}, known)
        with pytest.raises(ValueError):
            schema.validate_presence_mask(0, {"hdop": 0.0, "speed": None}, known)

    def test_presence_mask_codec(self) -> None:
        known = {"altitude": 1, "geoid": 2, "speed": 4}
        assert schema.encode_presence_mask(["altitude", "speed"], known) == 5
        assert schema.decode_presence_mask(5, known) == {"altitude", "speed"}
        with pytest.raises(ValueError):
            schema.encode_presence_mask(["unknown"], known)
        with pytest.raises(ValueError):
            schema.decode_presence_mask(8, known)

    def test_creator_rejects_interior_frequency_perturbation(
        self,
        tmp_path: Path,
        layout_kwargs: dict[str, object],
        frequencies: np.ndarray,
    ) -> None:
        perturbed = np.array(frequencies, copy=True)
        perturbed[8] += 12_345.0
        kwargs = dict(layout_kwargs)
        kwargs["frequencies_hz"] = perturbed
        path = tmp_path / "perturbed.partial.rcscan"
        with pytest.raises(ValueError, match="axis"):
            schema.create_rcscan_v2(path, **kwargs)  # type: ignore[arg-type]
        assert not path.exists()

    def test_probe_rejects_malformed_file_id_and_writer_version(
        self, air_path: Path
    ) -> None:
        _mutate_root_attr(air_path, "file_id", "not-a-uuid")
        with pytest.raises(DomainError) as excinfo:
            schema.probe_rcscan_v2(air_path)
        assert excinfo.value.context["field"] == "file_id"
        _mutate_root_attr(air_path, "file_id", str(_AIR_FILE_ID))
        _mutate_root_attr(air_path, "writer_version", "bad token")
        with pytest.raises(DomainError) as excinfo:
            schema.probe_rcscan_v2(air_path)
        assert excinfo.value.context["field"] == "writer_version"

    def test_gnss_fix_type_is_stable_string_column(self, air_path: Path) -> None:
        contract = next(
            c for c in schema.dataset_contracts(2, _FREQUENCY_POINTS)
            if c.path == "/gnss/fix_type"
        )
        assert contract.kind is schema.ValueKind.VLEN_UTF8
        with h5py.File(air_path, "r") as h5:
            info = h5py.check_string_dtype(h5["/gnss/fix_type"].dtype)
            assert info is not None and info.encoding == "utf-8"
            assert info.length is None

    def test_bool_column_codec_is_int64_zero_one(self) -> None:
        encoded = schema.bool_column([False, True, True])
        assert encoded.dtype == np.int64
        assert encoded.tolist() == [0, 1, 1]
        assert schema.decode_bool_column(encoded).tolist() == [False, True, True]
        with pytest.raises(ValueError):
            schema.decode_bool_column([2])

    def test_real_gnss_fix_and_trace_metadata_codec_round_trip(
        self, air_path: Path
    ) -> None:
        fix = GnssFix(
            received_utc=datetime(2026, 8, 27, 11, 59, 30, tzinfo=UTC),
            nmea_utc=datetime(2026, 8, 27, 11, 59, 29, 500000, tzinfo=UTC),
            received_monotonic_ns=MonotonicNs(1_000),
            latitude_deg=30.5,
            longitude_deg=120.1,
            altitude_msl_m=12.5,
            geoid_separation_m=-8.3,
            fix_quality=GnssFixQuality.RTK_FIXED,
            satellites=14,
            hdop=0.9,
            ground_speed_mps=3.4,
            course_deg=None,
            valid=True,
            invalid_reason=None,
        )
        match = GnssMatch(
            fix=fix,
            trace_midpoint_utc=datetime(2026, 8, 27, 12, 0, 0, 250000, tzinfo=UTC),
            age_s=0.2,
            method=GnssMatchMethod.NEAREST_MIDPOINT,
            usable_for_map=True,
            reason=None,
        )
        metadata = TraceMetadata(
            mission_id=MissionId("0f0e8a3b-6f2d-4c1e-9a7b-112233445566"),
            trace_index=0,
            trace_uid=TraceUid("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
            device_id=DeviceId("d1c0ffee-0000-4000-8000-000000000001"),
            sweep_started_utc=datetime(2026, 8, 27, 12, 0, 0, tzinfo=UTC),
            sweep_midpoint_utc=datetime(2026, 8, 27, 12, 0, 0, 250000, tzinfo=UTC),
            sweep_finished_utc=datetime(2026, 8, 27, 12, 0, 0, 500000, tzinfo=UTC),
            sweep_started_monotonic_ns=MonotonicNs(1_000_000),
            sweep_midpoint_monotonic_ns=MonotonicNs(1_000_250),
            sweep_finished_monotonic_ns=MonotonicNs(1_000_500),
            target_interval_s=0.5,
            actual_interval_s=None,
            schedule_error_s=None,
            connection_generation=2,
            raw_trace_sha256="a" * 64,
            gnss_match=match,
            quality_status=TraceQualityStatus.NOMINAL,
            quality_reasons=(),
        )
        cells = schema.trace_metadata_to_cells(metadata)
        with h5py.File(air_path, "r+") as h5:
            _write_row_to_h5(h5, cells)
        with h5py.File(air_path, "r") as h5:
            stored = _read_row_from_h5(h5, cells)
            assert "/trace_metadata/row_json" not in h5
            assert "/gnss/row_json" not in h5
        restored = schema.trace_metadata_from_cells(
            stored,
            mission_id=metadata.mission_id,
            device_id=metadata.device_id,
        )
        assert restored == metadata

    def test_invalid_fix_with_missing_fields_round_trip(
        self, air_path: Path
    ) -> None:
        no_fix_match = GnssMatch(
            fix=None,
            trace_midpoint_utc=datetime(2026, 8, 27, 12, 0, 0, 250000, tzinfo=UTC),
            age_s=None,
            method=GnssMatchMethod.NEAREST_MIDPOINT,
            usable_for_map=False,
            reason=GnssUnavailableReason.NO_FIX,
        )
        metadata = TraceMetadata(
            mission_id=MissionId("0f0e8a3b-6f2d-4c1e-9a7b-112233445566"),
            trace_index=0,
            trace_uid=TraceUid("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbc"),
            device_id=DeviceId("d1c0ffee-0000-4000-8000-000000000001"),
            sweep_started_utc=datetime(2026, 8, 27, 12, 0, 0, tzinfo=UTC),
            sweep_midpoint_utc=datetime(2026, 8, 27, 12, 0, 0, 250000, tzinfo=UTC),
            sweep_finished_utc=datetime(2026, 8, 27, 12, 0, 0, 500000, tzinfo=UTC),
            sweep_started_monotonic_ns=MonotonicNs(1_000_000),
            sweep_midpoint_monotonic_ns=MonotonicNs(1_000_250),
            sweep_finished_monotonic_ns=MonotonicNs(1_000_500),
            target_interval_s=0.5,
            actual_interval_s=None,
            schedule_error_s=None,
            connection_generation=2,
            raw_trace_sha256="b" * 64,
            gnss_match=no_fix_match,
            quality_status=TraceQualityStatus.DEGRADED,
            quality_reasons=(TraceQualityReason.GNSS_NO_FIX,),
        )
        cells = schema.trace_metadata_to_cells(metadata)
        assert int(cells["/gnss/optional_present_mask"]) == 0
        with h5py.File(air_path, "r+") as h5:
            _write_row_to_h5(h5, cells)
        with h5py.File(air_path, "r") as h5:
            stored = _read_row_from_h5(h5, cells)
        restored = schema.trace_metadata_from_cells(
            stored,
            mission_id=metadata.mission_id,
            device_id=metadata.device_id,
        )
        assert restored == metadata

    def test_gnss_match_none_round_trip(self, air_path: Path) -> None:
        metadata = TraceMetadata(
            mission_id=MissionId("0f0e8a3b-6f2d-4c1e-9a7b-112233445566"),
            trace_index=0,
            trace_uid=TraceUid("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbd"),
            device_id=DeviceId("d1c0ffee-0000-4000-8000-000000000001"),
            sweep_started_utc=datetime(2026, 8, 27, 12, 0, 0, tzinfo=UTC),
            sweep_midpoint_utc=datetime(2026, 8, 27, 12, 0, 0, 250000, tzinfo=UTC),
            sweep_finished_utc=datetime(2026, 8, 27, 12, 0, 0, 500000, tzinfo=UTC),
            sweep_started_monotonic_ns=MonotonicNs(1_000_000),
            sweep_midpoint_monotonic_ns=MonotonicNs(1_000_250),
            sweep_finished_monotonic_ns=MonotonicNs(1_000_500),
            target_interval_s=0.5,
            actual_interval_s=None,
            schedule_error_s=None,
            connection_generation=2,
            raw_trace_sha256="c" * 64,
            gnss_match=None,
            quality_status=TraceQualityStatus.DEGRADED,
            quality_reasons=(TraceQualityReason.GNSS_MISSING,),
        )
        cells = schema.trace_metadata_to_cells(metadata)
        assert int(cells["/gnss/valid"]) == 0
        assert int(cells["/gnss/match_usable"]) == 0
        with h5py.File(air_path, "r+") as h5:
            _write_row_to_h5(h5, cells)
        with h5py.File(air_path, "r") as h5:
            stored = _read_row_from_h5(h5, cells)
        restored = schema.trace_metadata_from_cells(
            stored,
            mission_id=metadata.mission_id,
            device_id=metadata.device_id,
        )
        assert restored == metadata

    def test_from_cells_rejects_presence_mask_violations(
        self, air_path: Path
    ) -> None:
        metadata = TraceMetadata(
            mission_id=MissionId("0f0e8a3b-6f2d-4c1e-9a7b-112233445566"),
            trace_index=0,
            trace_uid=TraceUid("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbd"),
            device_id=DeviceId("d1c0ffee-0000-4000-8000-000000000001"),
            sweep_started_utc=datetime(2026, 8, 27, 12, 0, 0, tzinfo=UTC),
            sweep_midpoint_utc=datetime(2026, 8, 27, 12, 0, 0, 250000, tzinfo=UTC),
            sweep_finished_utc=datetime(2026, 8, 27, 12, 0, 0, 500000, tzinfo=UTC),
            sweep_started_monotonic_ns=MonotonicNs(1_000_000),
            sweep_midpoint_monotonic_ns=MonotonicNs(1_000_250),
            sweep_finished_monotonic_ns=MonotonicNs(1_000_500),
            target_interval_s=0.5,
            actual_interval_s=None,
            schedule_error_s=None,
            connection_generation=2,
            raw_trace_sha256="c" * 64,
            gnss_match=None,
            quality_status=TraceQualityStatus.DEGRADED,
            quality_reasons=(TraceQualityReason.GNSS_MISSING,),
        )
        cells = schema.trace_metadata_to_cells(metadata)
        bad_timing = dict(cells)
        bad_timing["/trace_metadata/timing_present_mask"] = (
            schema.TIMING_PRESENT_ACTUAL_INTERVAL
        )
        with pytest.raises(ValueError):
            schema.trace_metadata_from_cells(
                bad_timing,
                mission_id=metadata.mission_id,
                device_id=metadata.device_id,
            )
        bad_timing_unknown = dict(cells)
        bad_timing_unknown["/trace_metadata/timing_present_mask"] = 4
        with pytest.raises(ValueError):
            schema.trace_metadata_from_cells(
                bad_timing_unknown,
                mission_id=metadata.mission_id,
                device_id=metadata.device_id,
            )
        bad_gnss_unknown = dict(cells)
        bad_gnss_unknown["/gnss/optional_present_mask"] = 128
        with pytest.raises(ValueError):
            schema.trace_metadata_from_cells(
                bad_gnss_unknown,
                mission_id=metadata.mission_id,
                device_id=metadata.device_id,
            )
        bad_valid = dict(cells)
        bad_valid["/gnss/valid"] = 2
        with pytest.raises(ValueError):
            schema.trace_metadata_from_cells(
                bad_valid,
                mission_id=metadata.mission_id,
                device_id=metadata.device_id,
            )

    def test_invalid_fix_zero_fields_round_trip(self, air_path: Path) -> None:
        invalid_fix = GnssFix(
            received_utc=datetime(2026, 8, 27, 10, 0, 0, tzinfo=UTC),
            nmea_utc=None,
            received_monotonic_ns=MonotonicNs(2_000),
            latitude_deg=None,
            longitude_deg=None,
            altitude_msl_m=None,
            geoid_separation_m=None,
            fix_quality=GnssFixQuality.INVALID,
            satellites=0,
            hdop=0.0,
            ground_speed_mps=None,
            course_deg=None,
            valid=False,
            invalid_reason=GnssUnavailableReason.NO_FIX,
        )
        match = GnssMatch(
            fix=invalid_fix,
            trace_midpoint_utc=datetime(2026, 8, 27, 12, 0, 0, 250000, tzinfo=UTC),
            age_s=0.0,
            method=GnssMatchMethod.NEAREST_MIDPOINT,
            usable_for_map=False,
            reason=GnssUnavailableReason.INVALID,
        )
        metadata = TraceMetadata(
            mission_id=MissionId("0f0e8a3b-6f2d-4c1e-9a7b-112233445566"),
            trace_index=0,
            trace_uid=TraceUid("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbe"),
            device_id=DeviceId("d1c0ffee-0000-4000-8000-000000000001"),
            sweep_started_utc=datetime(2026, 8, 27, 12, 0, 0, tzinfo=UTC),
            sweep_midpoint_utc=datetime(2026, 8, 27, 12, 0, 0, 250000, tzinfo=UTC),
            sweep_finished_utc=datetime(2026, 8, 27, 12, 0, 0, 500000, tzinfo=UTC),
            sweep_started_monotonic_ns=MonotonicNs(1_000_000),
            sweep_midpoint_monotonic_ns=MonotonicNs(1_000_250),
            sweep_finished_monotonic_ns=MonotonicNs(1_000_500),
            target_interval_s=0.5,
            actual_interval_s=None,
            schedule_error_s=None,
            connection_generation=2,
            raw_trace_sha256="d" * 64,
            gnss_match=match,
            quality_status=TraceQualityStatus.DEGRADED,
            quality_reasons=(TraceQualityReason.GNSS_INVALID,),
        )
        cells = schema.trace_metadata_to_cells(metadata)
        assert int(cells["/gnss/optional_present_mask"]) == int(
            schema.GNSS_PRESENT_SATELLITES
            | schema.GNSS_PRESENT_HDOP
            | schema.GNSS_PRESENT_MATCH_AGE
        )
        with h5py.File(air_path, "r+") as h5:
            _write_row_to_h5(h5, cells)
        with h5py.File(air_path, "r") as h5:
            stored = _read_row_from_h5(h5, cells)
        restored = schema.trace_metadata_from_cells(
            stored,
            mission_id=metadata.mission_id,
            device_id=metadata.device_id,
        )
        assert restored == metadata

    def test_ground_present_transport_must_match_frozen_schema(
        self, ground_path: Path
    ) -> None:
        with h5py.File(ground_path, "r+") as h5:
            h5.create_group("/transport")
            for contract in schema.dataset_contracts(2, _FREQUENCY_POINTS):
                if contract.path.startswith("/transport"):
                    h5.create_dataset(
                        contract.path,
                        shape=contract.initial_shape,
                        maxshape=contract.maxshape,
                        dtype=contract.dtype,
                        chunks=contract.chunks,
                        compression=contract.compression,
                    )
        with h5py.File(ground_path, "r+") as h5:
            del h5["/transport/ack_utc_ns"]
        with pytest.raises(DomainError) as excinfo:
            schema.probe_rcscan_v2(ground_path)
        assert excinfo.value.context["missing"] == ["/transport/ack_utc_ns"]


# ---------------------------------------------------------------------------
# Constants sanity
# ---------------------------------------------------------------------------


class TestFrozenConstants:
    def test_public_constant_surface(self) -> None:
        assert schema.FORMAT_NAME == "rcscan"
        assert schema.SCHEMA_VERSION_MAJOR == 2
        assert schema.SUPPORTED_SCHEMA_VERSIONS == frozenset({2})
        assert schema.PROFILE == "uav_gpr"
        assert schema.SUPPORTED_PROFILES == frozenset({"uav_gpr"})
        assert schema.LIFECYCLE_STATES == ("writing", "finalized", "recovered")
        assert {role.value for role in EndpointRole} == {"air", "ground"}
        assert schema.MISSING_INT64 == -(2**63)
