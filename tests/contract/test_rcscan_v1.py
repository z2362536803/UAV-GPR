"""ISSUE-013 contract tests: read-only ``.rcscan`` v1 adapter + explicit v1→v2 migration.

This suite pins the v1 read contract and the explicit v1→v2 migration on top
of the ISSUE-008/011 v2 reader, the ISSUE-009 canonical raw hash and the
anonymous golden fixtures declared in ``rcscan_v1_golden.json``:

- read side: ``RcScanV1Reader`` opens a rebar-inspector ``.rcscan`` v1 file
  strictly (mirroring the frozen v1 schema extracted from
  ``src/rebar_inspector/storage/rcscan.py`` @ manifest sha256
  ``290c5dad…``), maps raw/calibrated/time/channels/axes/history into the
  UAV-GPR domain models and keeps missing mission/GNSS/UTC as ``None`` —
  never fabricating times or coordinates; ``inspect_v1`` produces a
  field-level report that never raises for content issues;
- migration side: ``migrate_v1_to_v2`` writes a new v2 file with a new
  mission/file id, deterministic uuid5-derived trace uids, migration
  provenance attributes (source file sha256, tool version, source format)
  and per-row data written through the authoritative ISSUE-008 codec; the
  output must pass the strict ISSUE-011 ``RcScanReader`` with numeric /
  axis / channel / history round-trip equality, source v1 bytes unchanged
  and repeated-migration determinism.

Everything is synthetic (fixed seeds); no sleep, no hardware, no reference
project import.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
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
    GnssNoFixPolicy,
    LogicalPolarization,
    SParameter,
    TraceQualityReason,
    TraceQualityStatus,
)
from uav_gpr.core.errors import DomainError, ErrorCode
from uav_gpr.core.identifiers import (
    AirFileId,
    DeviceId,
    GroundFileId,
    MissionId,
    TraceUid,
)
from uav_gpr.core.raw_hash import compute_raw_trace_sha256
from uav_gpr.core.time_domain import ProcessingHistory
from uav_gpr.core.timeutil import ManualClock, from_utc_iso
from uav_gpr.storage import rcscan_v2 as schema
from uav_gpr.storage.rcscan_reader import RcScanReader
from uav_gpr.storage.rcscan_v1 import (
    V1_MIGRATION_NAMESPACE,
    V1_MIGRATION_TOOL_VERSION,
    RcScanV1Reader,
    V1InspectionReport,
    inspect_v1,
    migrate_v1_to_v2,
)

_GOLDEN_PATH = Path(__file__).with_name("rcscan_v1_golden.json")

#: v1 schema root attribute names (frozen in rcscan.py).
_V1_FORMAT_NAME = "rcscan"


# ---------------------------------------------------------------------------
# Golden manifest
# ---------------------------------------------------------------------------


def _load_golden() -> dict[str, object]:
    with _GOLDEN_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def golden() -> dict[str, object]:
    return _load_golden()


def _variant(golden: Mapping[str, object], name: str) -> dict[str, object]:
    for item in golden["variants"]:
        assert isinstance(item, dict)
        if item["name"] == name:
            return item
    raise AssertionError(f"golden variant not found: {name}")


# ---------------------------------------------------------------------------
# Fixture builder (mirrors save_rcscan() layout of the frozen v1 schema)
# ---------------------------------------------------------------------------

_CHANNEL_JSON = '{{"logical": "{logical}", "s_parameter": "{s_parameter}"}}'


def _history_json(entries: list[dict[str, object]]) -> str:
    return json.dumps(entries, ensure_ascii=False, allow_nan=False)


def _variant_data(spec: Mapping[str, object]) -> dict[str, object]:
    """Deterministic synthetic arrays for one golden variant (shared RNG).

    The generation order is frozen: raw, calibrated, time_base, time_processed.
    Any change here changes the golden digests — do not reorder.
    """
    gen = spec["generation"]
    seed = int(gen["seed"])
    n_traces = int(gen["n_traces"])
    channels = list(gen["channels"])
    n_channels = len(channels)
    freq = gen["frequencies_hz"]
    n_freq = int(freq["count"])
    rng = np.random.RandomState(seed)
    raw = (
        rng.standard_normal((n_traces, n_channels, n_freq))
        + 1j * rng.standard_normal((n_traces, n_channels, n_freq))
    ).astype("<c16")
    calibrated = None
    if gen.get("calibrated"):
        calibrated = (raw * (0.9 + 0.1j)).astype("<c16")
    time_base: np.ndarray | None = None
    time_processed: np.ndarray | None = None
    time_base_axis: np.ndarray | None = None
    time_processed_axis: np.ndarray | None = None
    if "time_base_s" in gen:
        tb = gen["time_base_s"]
        n_tb = int(tb["count"])
        time_base_axis = np.linspace(float(tb["start"]), float(tb["stop"]), n_tb)
        time_base = (
            rng.standard_normal((n_traces, n_channels, n_tb))
            + 1j * rng.standard_normal((n_traces, n_channels, n_tb))
        ).astype("<c16")
    if "time_processed_s" in gen:
        tp = gen["time_processed_s"]
        n_tp = int(tp["count"])
        time_processed_axis = np.linspace(float(tp["start"]), float(tp["stop"]), n_tp)
        time_processed = (
            rng.standard_normal((n_traces, n_channels, n_tp))
            + 1j * rng.standard_normal((n_traces, n_channels, n_tp))
        ).astype("<c16")
    created = datetime.fromisoformat(str(gen["created_utc"]))
    interval = float(gen["trace_interval_s"])
    timestamps = [created + i * timedelta(seconds=interval) for i in range(n_traces)]
    position = np.linspace(0.0, float(gen["position_stop_m"]), n_traces)
    extras = [{"note": f"trace {i}"} for i in range(n_traces)]
    return {
        "channels": channels,
        "frequencies_hz": np.linspace(
            float(freq["start"]), float(freq["stop"]), n_freq
        ),
        "raw": raw,
        "calibrated": calibrated,
        "time_base": time_base,
        "time_processed": time_processed,
        "time_base_axis": time_base_axis,
        "time_processed_axis": time_processed_axis,
        "created_utc": created,
        "timestamps": timestamps,
        "position": position,
        "extras": extras,
    }


def _write_str_dataset(group: h5py.Group, name: str, text: str) -> None:
    group.create_dataset(name, data=text, dtype=h5py.string_dtype("utf-8"))


def _write_str_array(group: h5py.Group, name: str, texts: list[str]) -> None:
    group.create_dataset(
        name,
        data=np.asarray(texts, dtype=object),
        dtype=h5py.string_dtype("utf-8"),
    )


def build_v1_fixture(
    path: str | Path,
    spec: Mapping[str, object],
    *,
    overrides: Mapping[str, object] | None = None,
) -> Path:
    """Build one synthetic v1 ``.rcscan`` file (golden or corrupt variant).

    Layout mirrors ``save_rcscan`` in ``src/rebar_inspector/storage/rcscan.py``
    (frozen sha256 ``290c5dad…``): root attrs, ``/channels`` JSON, ``/axes``,
    ``/frequency``, optional ``/position_m``, ``/trace_metadata``,
    ``/time_base``, ``/time_processed``.  ``overrides`` applies post-hoc
    mutations for negative tests (see the test methods for keys).
    """
    target = Path(path)
    data = _variant_data(spec)
    gen = spec["generation"]
    channels_text = json.dumps(
        [
            {"logical": c["logical"], "s_parameter": c["s_parameter"]}
            for c in data["channels"]
        ],
        ensure_ascii=False,
        allow_nan=False,
    )
    freq_history_text = _history_json(list(spec["frequency_history"]))
    tb_history_text = _history_json(list(spec["time_base_history"]))
    tp_history_text = _history_json(list(spec["time_processed_history"]))

    with h5py.File(target, "x") as h5:
        h5.attrs["format_name"] = _V1_FORMAT_NAME
        h5.attrs["schema_version"] = 1
        h5.attrs["created_utc"] = str(gen["created_utc"])
        h5.attrs["generator"] = str(gen["generator"])
        h5.attrs["trigger"] = str(gen["trigger"])
        h5.attrs["position_source"] = str(gen["position_source"])

        _write_str_dataset(h5, "channels", channels_text)

        axes = h5.create_group("axes")
        axes.create_dataset("frequencies_hz", data=data["frequencies_hz"])
        if data["time_base_axis"] is not None:
            axes.create_dataset("time_base_s", data=data["time_base_axis"])
        if data["time_processed_axis"] is not None:
            axes.create_dataset("time_processed_s", data=data["time_processed_axis"])

        freq_group = h5.create_group("frequency")
        freq_group.create_dataset("raw", data=data["raw"])
        if data["calibrated"] is not None:
            freq_group.create_dataset("calibrated", data=data["calibrated"])
        _write_str_dataset(freq_group, "history_json", freq_history_text)

        if gen.get("with_position", False):
            h5.create_dataset("position_m", data=data["position"])

        if gen.get("with_trace_metadata", False):
            meta_group = h5.create_group("trace_metadata")
            _write_str_array(
                meta_group,
                "timestamps_utc",
                [t.isoformat() for t in data["timestamps"]],
            )
            _write_str_array(
                meta_group,
                "extras_json",
                [
                    json.dumps(e, ensure_ascii=False, allow_nan=False)
                    for e in data["extras"]
                ],
            )

        if data["time_base"] is not None:
            base_group = h5.create_group("time_base")
            base_group.create_dataset("data", data=data["time_base"])
            _write_str_dataset(base_group, "history_json", tb_history_text)

        if data["time_processed"] is not None:
            proc_group = h5.create_group("time_processed")
            proc_group.create_dataset("data", data=data["time_processed"])
            _write_str_dataset(proc_group, "history_json", tp_history_text)

    if overrides:
        _apply_overrides(target, overrides, data)
    return target


def _apply_overrides(
    path: Path, overrides: Mapping[str, object], data: Mapping[str, object]
) -> None:
    """Post-hoc mutations used by the negative tests (all fail-closed cases)."""
    with h5py.File(path, "r+") as h5:
        if "schema_version" in overrides:
            h5.attrs["schema_version"] = overrides["schema_version"]
        if "position_source" in overrides:
            h5.attrs["position_source"] = overrides["position_source"]
        if "created_utc" in overrides:
            h5.attrs["created_utc"] = overrides["created_utc"]
        if "drop" in overrides:
            for node in overrides["drop"]:
                assert isinstance(node, str)
                del h5[node]
        if "channels_text" in overrides:
            h5["channels"][()] = overrides["channels_text"]
        if "channels_as_group" in overrides:
            del h5["channels"]
            group = h5.create_group("channels")
            group.create_dataset("payload", data="x", dtype=h5py.string_dtype("utf-8"))
        if "history_text" in overrides:
            h5["/frequency/history_json"][()] = overrides["history_text"]
        if "raw_array" in overrides:
            del h5["/frequency/raw"]
            h5.create_dataset(
                "/frequency/raw", data=np.asarray(overrides["raw_array"])
            )
        if "calibrated_array" in overrides:
            del h5["/frequency/calibrated"]
            h5.create_dataset(
                "/frequency/calibrated", data=np.asarray(overrides["calibrated_array"])
            )
        if "timestamps_array" in overrides:
            del h5["/trace_metadata/timestamps_utc"]
            _write_str_array(
                h5["trace_metadata"],
                "timestamps_utc",
                list(overrides["timestamps_array"]),
            )
        if "position_array" in overrides:
            del h5["position_m"]
            h5.create_dataset("position_m", data=np.asarray(overrides["position_array"]))
        if "frequencies_array" in overrides:
            del h5["/axes/frequencies_hz"]
            h5.create_dataset(
                "/axes/frequencies_hz", data=np.asarray(overrides["frequencies_array"])
            )
        if "time_base_as_group_missing_axis" in overrides:
            # keep /time_base but drop /axes/time_base_s (axis missing).
            del h5["/axes/time_base_s"]
        if "drop_time_base_group" in overrides:
            del h5["/time_base"]
            if "/axes/time_base_s" in h5:
                del h5["/axes/time_base_s"]


# ---------------------------------------------------------------------------
# Shared expectations
# ---------------------------------------------------------------------------


def _mapped_channels(channels: list[dict[str, str]]) -> tuple[ChannelSpec, ...]:
    return tuple(
        ChannelSpec(
            channel_id=f"{c['logical'].lower()}_{c['s_parameter'].lower()}",
            logical_polarization=LogicalPolarization.from_value(c["logical"].lower()),
            s_parameter=SParameter.from_value(c["s_parameter"].lower()),
            display_name=f"{c['logical']} {c['s_parameter']}",
            antenna_note=None,
        )
        for c in channels
    )


def _expect_digests(
    channels: list[dict[str, str]],
    frequencies_hz: np.ndarray,
    raw: np.ndarray,
    ids: Mapping[str, object],
) -> list[str]:
    """Recompute the golden per-trace ISSUE-009 digests from the manifest IDs."""
    mission_id = MissionId(str(ids["mission_id"]))
    channel_specs = _mapped_channels(channels)
    axis = np.asarray(frequencies_hz, dtype="<f8")
    raw = np.asarray(raw, dtype="<c16")
    return [
        compute_raw_trace_sha256(
            mission_id=mission_id,
            trace_index=i,
            trace_uid=TraceUid(str(ids["trace_uids"][i])),
            channels=channel_specs,
            frequencies_hz=axis,
            data=raw[i],
        )
        for i in range(int(raw.shape[0]))
    ]


# ---------------------------------------------------------------------------
# Read side
# ---------------------------------------------------------------------------


class TestV1Reader:
    def test_golden_digests_match_framing(
        self, golden: dict[str, object], tmp_path: Path
    ) -> None:
        """The manifest's expected digests are reproducible from its own
        generation parameters (pins fixture-data determinism + framing)."""
        for name in ("full", "minimal"):
            spec = _variant(golden, name)
            data = _variant_data(spec)
            expected = spec["expected"]
            digests = _expect_digests(
                list(data["channels"]),
                np.asarray(data["frequencies_hz"]),
                np.asarray(data["raw"]),
                expected["digest_ids"],
            )
            assert digests == expected["raw_trace_sha256"]

    def test_golden_full_roundtrip(self, golden: dict[str, object], tmp_path: Path) -> None:
        spec = _variant(golden, "full")
        path = build_v1_fixture(tmp_path / "full.rcscan", spec)
        expected = spec["expected"]
        data = _variant_data(spec)
        with RcScanV1Reader(path) as reader:
            scanned = reader.data
            assert [c.channel_id for c in scanned.channels] == expected["channel_ids"]
            assert [c.display_name for c in scanned.channels] == expected["display_names"]
            assert [c.logical_polarization.value for c in scanned.channels] == expected[
                "logical_polarizations"
            ]
            assert [c.s_parameter.value for c in scanned.channels] == expected[
                "s_parameters"
            ]
            assert np.array_equal(scanned.frequencies_hz, data["frequencies_hz"])
            assert np.array_equal(scanned.frequency.data, data["raw"])
            assert scanned.frequency.metadata == ()
            assert scanned.frequency_calibrated is not None
            assert np.array_equal(scanned.frequency_calibrated, data["calibrated"])
            assert scanned.time_base is not None
            assert scanned.time_base.kind.value == "time_base"
            assert np.array_equal(scanned.time_base.time_axis_s, data["time_base_axis"])
            assert np.array_equal(scanned.time_base.data, data["time_base"])
            assert scanned.time_processed is not None
            assert scanned.time_processed.kind.value == "time_processed"
            assert np.array_equal(
                scanned.time_processed.time_axis_s, data["time_processed_axis"]
            )
            assert np.array_equal(scanned.time_processed.data, data["time_processed"])
            assert scanned.trigger == "time"
            assert scanned.position_source == "time_estimated"
            assert scanned.position_m is not None
            assert np.array_equal(scanned.position_m, data["position"])
            assert scanned.created_utc == data["created_utc"]
            assert scanned.generator == spec["generation"]["generator"]
            # trace timestamps / extras
            assert scanned.trace_timestamps_utc is not None
            assert scanned.trace_timestamps_utc == tuple(data["timestamps"])
            assert scanned.trace_extras is not None
            assert scanned.trace_extras == tuple(data["extras"])
            # history mapping (stage/params/timestamp preserved)
            assert [h.stage for h in scanned.frequency_history] == [
                h["stage"] for h in spec["frequency_history"]
            ]
            assert [h.params for h in scanned.frequency_history] == [
                h["params"] for h in spec["frequency_history"]
            ]
            assert [h.timestamp for h in scanned.frequency_history] == [
                datetime.fromisoformat(str(h["timestamp"]))
                for h in spec["frequency_history"]
            ]
            # synthesized import provenance for time-domain scans
            tb_history = scanned.time_base.history.records
            assert len(tb_history) == 1
            assert tb_history[0].stage_name == "v1_import_time_base"
            assert tb_history[0].output_domain.value == "time_base"
            assert tb_history[0].executed_utc == data["created_utc"]
            assert (
                tb_history[0].parameters["v1_history_json"]
                == _history_json(list(spec["time_base_history"]))
            )
            tp_history = scanned.time_processed.history.records
            assert [r.stage_name for r in tp_history] == [
                "v1_import_time_base",
                "v1_import_time_processed",
            ]
            assert tp_history[-1].output_domain.value == "time_processed"
            assert reader.source_sha256 == _sha256(path)

    def test_golden_minimal(self, golden: dict[str, object], tmp_path: Path) -> None:
        spec = _variant(golden, "minimal")
        path = build_v1_fixture(tmp_path / "minimal.rcscan", spec)
        data = _variant_data(spec)
        with RcScanV1Reader(path) as reader:
            scanned = reader.data
            assert [c.channel_id for c in scanned.channels] == ["hh_s11"]
            assert np.array_equal(scanned.frequency.data, data["raw"])
            assert scanned.frequency_calibrated is None
            assert scanned.time_base is None
            assert scanned.time_processed is None
            assert scanned.trace_timestamps_utc is None
            assert scanned.trace_extras is None
            assert scanned.position_m is None
            assert scanned.position_source == "none"
            assert scanned.frequency_history == ()

    def test_golden_calibrated_only(self, golden: dict[str, object], tmp_path: Path) -> None:
        spec = _variant(golden, "calibrated_only")
        path = build_v1_fixture(tmp_path / "cal.rcscan", spec)
        with RcScanV1Reader(path) as reader:
            scanned = reader.data
            assert scanned.frequency_calibrated is not None
            assert scanned.time_base is None
            assert scanned.time_processed is None
            assert scanned.trace_timestamps_utc is not None

    def test_golden_time_only(self, golden: dict[str, object], tmp_path: Path) -> None:
        spec = _variant(golden, "time_only")
        path = build_v1_fixture(tmp_path / "time.rcscan", spec)
        with RcScanV1Reader(path) as reader:
            scanned = reader.data
            assert scanned.frequency_calibrated is None
            assert scanned.time_base is not None
            assert scanned.time_processed is not None

    def test_unsupported_schema_version_fails_closed(
        self, golden: dict[str, object], tmp_path: Path
    ) -> None:
        spec = _variant(golden, "minimal")
        for bad in (2, 3, True, 2.5, "1"):
            path = build_v1_fixture(
                tmp_path / f"v{bad!r}.rcscan".replace("'", "").replace(" ", "_"),
                spec,
                overrides={"schema_version": bad},
            )
            with pytest.raises(DomainError) as excinfo:
                RcScanV1Reader(path)
            if bad in (2, 3):
                assert excinfo.value.code == ErrorCode.UNSUPPORTED_SCHEMA_VERSION
            else:
                assert excinfo.value.code == ErrorCode.INVALID_ARGUMENT

    def test_v2_file_rejected(self, tmp_path: Path) -> None:
        target = tmp_path / "v2.rcscan"
        config = MissionConfig.from_frequency_axis(
            frequency_axis_hz=np.linspace(5e8, 2.5e9, 5),
            if_bw_hz=1e3,
            power_dbm=0.0,
            channels=(ChannelSpec("hh_s11", LogicalPolarization.HH, SParameter.S11, "HH S11"),),
            acquisition_mode=AcquisitionMode.CONTINUOUS,
            planned_trace_count=None,
            target_interval_s=0.1,
            gnss_max_age_s=5.0,
            gnss_no_fix_policy=GnssNoFixPolicy.RECORD_WITHOUT_POSITION,
            created_utc=datetime(2026, 8, 1, tzinfo=UTC),
            software_version="issue013.test",
        )
        schema.create_rcscan_v2(
            target,
            mission_id=MissionId("11111111-1111-4111-8111-111111111111"),
            device_id=DeviceId("22222222-2222-4222-8222-222222222222"),
            file_id=GroundFileId("33333333-3333-4333-8333-333333333333"),
            created_utc=datetime(2026, 8, 1, tzinfo=UTC),
            completed_utc=None,
            completion_kind=None,
            file_role=EndpointRole.GROUND,
            channels=config.channels,
            frequencies_hz=config.frequency_axis_hz,
            config_json=config.to_canonical_json(),
            config_sha256=config.config_sha256,
            writer_version="issue013.test",
        )
        with pytest.raises(DomainError) as excinfo:
            RcScanV1Reader(target)
        assert excinfo.value.code == ErrorCode.UNSUPPORTED_SCHEMA_VERSION

    def test_missing_required_node_fails_closed(
        self, golden: dict[str, object], tmp_path: Path
    ) -> None:
        spec = _variant(golden, "minimal")
        for node in (
            "channels",
            "/axes/frequencies_hz",
            "/frequency/raw",
            "/frequency/history_json",
        ):
            path = build_v1_fixture(
                tmp_path / f"drop_{node.strip('/').replace('/', '_')}.rcscan",
                spec,
                overrides={"drop": [node]},
            )
            with pytest.raises(DomainError) as excinfo:
                RcScanV1Reader(path)
            assert excinfo.value.code == ErrorCode.INVALID_ARGUMENT
            assert node in str(excinfo.value.context)

    def test_node_type_mismatch_fails_closed(
        self, golden: dict[str, object], tmp_path: Path
    ) -> None:
        spec = _variant(golden, "minimal")
        path = build_v1_fixture(
            tmp_path / "channels_as_group.rcscan",
            spec,
            overrides={"channels_as_group": True},
        )
        with pytest.raises(DomainError) as excinfo:
            RcScanV1Reader(path)
        assert excinfo.value.code == ErrorCode.INVALID_ARGUMENT

    def test_bad_json_fails_closed(self, golden: dict[str, object], tmp_path: Path) -> None:
        spec = _variant(golden, "minimal")
        path = build_v1_fixture(
            tmp_path / "bad_channels.rcscan",
            spec,
            overrides={"channels_text": '{"logical": "HH", "s_parameter": "S11"}'},
        )
        with pytest.raises(DomainError) as excinfo:
            RcScanV1Reader(path)
        assert excinfo.value.code == ErrorCode.INVALID_ARGUMENT
        path = build_v1_fixture(
            tmp_path / "nan_channels.rcscan",
            spec,
            overrides={"channels_text": '[{"logical": "HH", "s_parameter": NaN}]'},
        )
        with pytest.raises(DomainError) as excinfo:
            RcScanV1Reader(path)
        assert excinfo.value.code == ErrorCode.INVALID_ARGUMENT
        path = build_v1_fixture(
            tmp_path / "nan_history.rcscan",
            spec,
            overrides={
                "history_text": '[{"stage": "ifft", "params": NaN, '
                '"timestamp": "2026-08-01T00:00:00+00:00"}]'
            },
        )
        with pytest.raises(DomainError) as excinfo:
            RcScanV1Reader(path)
        assert excinfo.value.code == ErrorCode.INVALID_ARGUMENT

    def test_unknown_enum_fails_closed(self, golden: dict[str, object], tmp_path: Path) -> None:
        spec = _variant(golden, "minimal")
        path = build_v1_fixture(
            tmp_path / "bad_enum.rcscan",
            spec,
            overrides={"channels_text": '[{"logical": "XX", "s_parameter": "S11"}]'},
        )
        with pytest.raises(DomainError) as excinfo:
            RcScanV1Reader(path)
        assert excinfo.value.code == ErrorCode.INVALID_ARGUMENT

    def test_time_processed_without_time_base_fails_closed(
        self, golden: dict[str, object], tmp_path: Path
    ) -> None:
        spec = _variant(golden, "time_only")
        path = build_v1_fixture(
            tmp_path / "tp_no_tb.rcscan",
            spec,
            overrides={"drop_time_base_group": True},
        )
        with pytest.raises(DomainError) as excinfo:
            RcScanV1Reader(path)
        assert excinfo.value.code == ErrorCode.INVALID_ARGUMENT

    def test_time_base_axis_missing_fails_closed(
        self, golden: dict[str, object], tmp_path: Path
    ) -> None:
        spec = _variant(golden, "time_only")
        path = build_v1_fixture(
            tmp_path / "tb_no_axis.rcscan",
            spec,
            overrides={"time_base_as_group_missing_axis": True},
        )
        with pytest.raises(DomainError) as excinfo:
            RcScanV1Reader(path)
        assert excinfo.value.code == ErrorCode.INVALID_ARGUMENT

    def test_shape_mismatch_fails_closed(self, golden: dict[str, object], tmp_path: Path) -> None:
        spec = _variant(golden, "minimal")
        data = _variant_data(spec)
        raw = np.asarray(data["raw"])
        wrong_channels = np.concatenate([raw, raw], axis=1)
        path = build_v1_fixture(
            tmp_path / "raw_wrong_channels.rcscan",
            spec,
            overrides={"raw_array": wrong_channels},
        )
        with pytest.raises(DomainError) as excinfo:
            RcScanV1Reader(path)
        assert excinfo.value.code in (ErrorCode.SHAPE_MISMATCH, ErrorCode.INVALID_ARGUMENT)
        wrong_freq = raw[:, :, :-1]
        path = build_v1_fixture(
            tmp_path / "raw_wrong_freq.rcscan",
            spec,
            overrides={"raw_array": wrong_freq},
        )
        with pytest.raises(DomainError):
            RcScanV1Reader(path)

    def test_calibrated_shape_mismatch_fails_closed(
        self, golden: dict[str, object], tmp_path: Path
    ) -> None:
        spec = _variant(golden, "calibrated_only")
        data = _variant_data(spec)
        cal = np.asarray(data["calibrated"])[:, :, :-1]
        path = build_v1_fixture(
            tmp_path / "cal_wrong_shape.rcscan",
            spec,
            overrides={"calibrated_array": cal},
        )
        with pytest.raises(DomainError):
            RcScanV1Reader(path)

    def test_timestamps_count_mismatch_fails_closed(
        self, golden: dict[str, object], tmp_path: Path
    ) -> None:
        spec = _variant(golden, "calibrated_only")
        data = _variant_data(spec)
        ts = [t.isoformat() for t in data["timestamps"]][:-1]
        path = build_v1_fixture(
            tmp_path / "ts_short.rcscan",
            spec,
            overrides={"timestamps_array": ts},
        )
        with pytest.raises(DomainError) as excinfo:
            RcScanV1Reader(path)
        assert excinfo.value.code == ErrorCode.INVALID_ARGUMENT

    def test_position_rules_fail_closed(self, golden: dict[str, object], tmp_path: Path) -> None:
        spec = _variant(golden, "full")
        # position present but source none -> inconsistent (mirror v1 rule)
        path = build_v1_fixture(
            tmp_path / "pos_none.rcscan",
            spec,
            overrides={"position_source": "none"},
        )
        with pytest.raises(DomainError):
            RcScanV1Reader(path)
        # position absent but source not none -> inconsistent
        spec_min = _variant(golden, "minimal")
        path = build_v1_fixture(
            tmp_path / "no_pos_src.rcscan",
            spec_min,
            overrides={"position_source": "time_estimated"},
        )
        with pytest.raises(DomainError):
            RcScanV1Reader(path)
        # position length mismatch
        data = _variant_data(spec)
        path = build_v1_fixture(
            tmp_path / "pos_short.rcscan",
            spec,
            overrides={"position_array": np.asarray(data["position"])[:-1]},
        )
        with pytest.raises(DomainError):
            RcScanV1Reader(path)

    def test_duplicate_channel_id_fails_closed(
        self, golden: dict[str, object], tmp_path: Path
    ) -> None:
        spec = _variant(golden, "full")  # two-channel raw
        path = build_v1_fixture(
            tmp_path / "dup_ch.rcscan",
            spec,
            overrides={
                "channels_text": json.dumps(
                    [
                        {"logical": "HH", "s_parameter": "S11"},
                        {"logical": "HH", "s_parameter": "S11"},
                    ],
                    ensure_ascii=False,
                    allow_nan=False,
                )
            },
        )
        with pytest.raises(DomainError) as excinfo:
            RcScanV1Reader(path)
        assert excinfo.value.code == ErrorCode.DUPLICATE_CHANNEL

    def test_read_only_source_bytes_unchanged(
        self, golden: dict[str, object], tmp_path: Path
    ) -> None:
        spec = _variant(golden, "full")
        path = build_v1_fixture(tmp_path / "ro.rcscan", spec)
        before = _sha256(path)
        with RcScanV1Reader(path) as reader:
            assert reader.data.frequency.data.shape[0] == 4
        after = _sha256(path)
        assert before == after

    def test_non_uniform_axis_reads_ok(self, golden: dict[str, object], tmp_path: Path) -> None:
        # v1 allows any strictly increasing axis; the migration layer blocks
        # non-uniform axes, but the reader must still open the file.
        spec = _variant(golden, "minimal")
        path = build_v1_fixture(
            tmp_path / "nonuniform.rcscan",
            spec,
            overrides={
                "frequencies_array": np.asarray([5e8, 6e8, 8e8, 9e8, 2.5e9], dtype="<f8")
            },
        )
        with RcScanV1Reader(path) as reader:
            assert reader.data.frequencies_hz.size == 5


class TestV1Inspection:
    def test_inspect_full_ok(self, golden: dict[str, object], tmp_path: Path) -> None:
        spec = _variant(golden, "full")
        path = build_v1_fixture(tmp_path / "full.rcscan", spec)
        report = inspect_v1(path)
        assert isinstance(report, V1InspectionReport)
        assert report.schema_version == 1
        assert report.source_sha256 == _sha256(path)
        summary = report.summary()
        assert summary["error"] == 0
        assert summary["unsupported"] == 0
        assert summary["missing"] == 0
        assert summary["ok"] > 0

    def test_inspect_unknown_version_reports_not_raises(
        self, golden: dict[str, object], tmp_path: Path
    ) -> None:
        spec = _variant(golden, "minimal")
        path = build_v1_fixture(
            tmp_path / "v2.rcscan",
            spec,
            overrides={"schema_version": 2},
        )
        report = inspect_v1(path)
        assert report.schema_version == 2
        assert report.summary()["unsupported"] >= 1
        assert report.to_dict()["schema_version_status"] == "unsupported"

    def test_inspect_missing_field_reports_missing(
        self, golden: dict[str, object], tmp_path: Path
    ) -> None:
        spec = _variant(golden, "minimal")
        path = build_v1_fixture(
            tmp_path / "drop_raw.rcscan",
            spec,
            overrides={"drop": ["/frequency/raw"]},
        )
        report = inspect_v1(path)
        assert report.summary()["missing"] >= 1
        statuses = {f.path: f.status for f in report.fields}
        assert statuses["/frequency/raw"] == "missing"

    def test_inspect_non_hdf5_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "not_h5.rcscan"
        path.write_bytes(b"this is not an HDF5 file at all")
        with pytest.raises(DomainError):
            inspect_v1(path)


class TestV1Migration:
    def _migrate(
        self,
        source: Path,
        target_dir: Path,
        *,
        clock: ManualClock | None = None,
        **kwargs: object,
    ) -> object:
        return migrate_v1_to_v2(
            source,
            target_dir,
            if_bw_hz=1e3,
            power_dbm=0.0,
            target_interval_s=0.1,
            gnss_max_age_s=5.0,
            software_version="issue013.test",
            clock=clock,
            **kwargs,
        )

    def test_migration_roundtrip_full(
        self, golden: dict[str, object], tmp_path: Path
    ) -> None:
        spec = _variant(golden, "full")
        clock = ManualClock(datetime(2026, 8, 2, 12, 0, 0, tzinfo=UTC), 1_000_000)
        source = build_v1_fixture(tmp_path / "full.rcscan", spec)
        data = _variant_data(spec)
        result = self._migrate(source, tmp_path / "out", clock=clock)
        target = Path(result.target_path)
        assert target.name.endswith(".rcscan")
        with RcScanReader(target) as reader:
            assert reader.committed_record_count == int(spec["generation"]["n_traces"])
            assert reader.lifecycle_state == "finalized"
            assert reader.completion_kind == "completed"
            assert [c.channel_id for c in reader.channels] == spec["expected"][
                "channel_ids"
            ]
            assert np.array_equal(reader.frequencies_hz, data["frequencies_hz"])
            assert reader.mission_id == result.mission_id
            assert reader.device_id == result.device_id
            records = [t for chunk in reader.iter_logical() for t in chunk.records]
            assert len(records) == 4
            for i, trace in enumerate(records):
                assert trace.trace_index == i
                assert trace.hash_verified is True
                assert np.array_equal(
                    trace.frequency_raw, np.asarray(data["raw"])[i]
                )
                assert trace.metadata.gnss_match is None
                assert trace.metadata.quality_status is TraceQualityStatus.DEGRADED
                assert TraceQualityReason.GNSS_MISSING in trace.metadata.quality_reasons
                assert trace.metadata.sweep_started_utc == data["timestamps"][i]
                assert trace.metadata.sweep_midpoint_utc == data["timestamps"][i]
                assert trace.metadata.sweep_finished_utc == data["timestamps"][i]
                derived_ns = int(
                    (data["timestamps"][i] - data["created_utc"]).total_seconds() * 1e9
                )
                assert trace.metadata.sweep_started_monotonic_ns.ns == derived_ns
                assert trace.metadata.connection_generation == 0
                # ISSUE-009 digest is deterministic and stored
                expected_hash = compute_raw_trace_sha256(
                    mission_id=result.mission_id,
                    trace_index=i,
                    trace_uid=trace.metadata.trace_uid,
                    channels=reader.channels,
                    frequencies_hz=reader.frequencies_hz,
                    data=trace.frequency_raw,
                )
                assert trace.raw_trace_sha256 == expected_hash
        # optional groups round-trip numerically
        with h5py.File(target, "r") as h5:
            assert np.array_equal(h5["/frequency/raw"][...], data["raw"])
            assert np.array_equal(h5["/frequency/calibrated"][...], data["calibrated"])
            assert np.array_equal(h5["/time_base/data"][...], data["time_base"])
            assert np.array_equal(
                h5["/time_processed/data"][...], data["time_processed"]
            )
            assert np.array_equal(h5["/axes/time_base_s"][...], data["time_base_axis"])
            # history content: canonical domain history JSON, v1 verbatim embedded
            tb_history = ProcessingHistory.from_dict(
                json.loads(str(h5["/time_base/history_json"].asstr()[0]))
            )
            assert tb_history.records[0].stage_name == "v1_import_time_base"
            assert (
                tb_history.records[0].parameters["v1_history_json"]
                == _history_json(list(spec["time_base_history"]))
            )
            tp_history = ProcessingHistory.from_dict(
                json.loads(str(h5["/time_processed/history_json"].asstr()[0]))
            )
            assert tp_history.records[-1].stage_name == "v1_import_time_processed"
            # migration provenance attrs
            mission = h5["mission"].attrs
            assert mission["migration_source_sha256"] == _sha256(source)
            assert mission["migration_tool_version"] == V1_MIGRATION_TOOL_VERSION
            assert mission["migration_source_format"] == "rcscan_v1"
            assert from_utc_iso(str(mission["migration_v1_created_utc"])) == (
                data["created_utc"]
            )
            assert from_utc_iso(str(mission["started_utc"])) == data["timestamps"][0]
            assert from_utc_iso(str(mission["ended_utc"])) == data["timestamps"][-1]
            assert from_utc_iso(str(mission["created_utc"])) == data["created_utc"]
            # v1 frequency history preserved verbatim as an extra attr
            assert (
                mission["migration_v1_frequency_history"]
                == _history_json(list(spec["frequency_history"]))
            )

    def test_migration_deterministic_and_repeat(
        self, golden: dict[str, object], tmp_path: Path
    ) -> None:
        spec = _variant(golden, "full")
        clock = ManualClock(datetime(2026, 8, 2, 12, 0, 0, tzinfo=UTC), 1_000_000)
        source = build_v1_fixture(tmp_path / "src.rcscan", spec)
        out1 = tmp_path / "out1"
        out2 = tmp_path / "out2"
        r1 = self._migrate(source, out1, clock=clock)
        r2 = self._migrate(source, out2, clock=clock)
        assert r1.mission_id == r2.mission_id
        assert r1.file_id == r2.file_id
        assert r1.device_id == r2.device_id
        assert r1.trace_uids == r2.trace_uids
        assert _sha256(Path(r1.target_path)) == _sha256(Path(r2.target_path))
        # deterministic mission/file ids derive from the source sha256
        source_sha = _sha256(source)
        expected_mission = MissionId(
            str(uuid.uuid5(V1_MIGRATION_NAMESPACE, f"mission:{source_sha}"))
        )
        expected_file = GroundFileId(
            str(uuid.uuid5(V1_MIGRATION_NAMESPACE, f"file:{source_sha}"))
        )
        assert r1.mission_id == expected_mission
        assert r1.file_id == expected_file
        # second migration into the same target directory is refused
        with pytest.raises(DomainError) as excinfo:
            self._migrate(source, out1, clock=clock)
        assert "exists" in str(excinfo.value)
        assert _sha256(Path(r1.target_path)) == _sha256(Path(out1 / f"{r1.file_id}.rcscan"))

    def test_migration_source_bytes_unchanged(
        self, golden: dict[str, object], tmp_path: Path
    ) -> None:
        spec = _variant(golden, "full")
        source = build_v1_fixture(tmp_path / "src.rcscan", spec)
        before = _sha256(source)
        self._migrate(source, tmp_path / "out")
        assert _sha256(source) == before

    def test_migration_no_timestamps_blocked(
        self, golden: dict[str, object], tmp_path: Path
    ) -> None:
        spec = _variant(golden, "minimal")
        source = build_v1_fixture(tmp_path / "min.rcscan", spec)
        out = tmp_path / "out"
        with pytest.raises(DomainError) as excinfo:
            self._migrate(source, out)
        assert excinfo.value.code == ErrorCode.INVALID_ARGUMENT
        assert list(out.glob("*.rcscan")) == []
        assert list(out.glob("*.partial.rcscan")) == []

    def test_migration_non_uniform_axis_blocked(
        self, golden: dict[str, object], tmp_path: Path
    ) -> None:
        spec = _variant(golden, "calibrated_only")
        source = build_v1_fixture(
            tmp_path / "nonuniform.rcscan",
            spec,
            overrides={
                "frequencies_array": np.asarray([5e8, 6e8, 8e8, 9e8, 2.5e9], dtype="<f8")
            },
        )
        out = tmp_path / "out"
        with pytest.raises(DomainError) as excinfo:
            self._migrate(source, out)
        assert excinfo.value.code == ErrorCode.NON_UNIFORM_AXIS
        assert list(out.glob("*.rcscan")) == []

    def test_migration_time_len_mismatch_blocked(
        self, golden: dict[str, object], tmp_path: Path
    ) -> None:
        # v2 reader contract parameterizes time axes by the frequency point
        # count; a v1 time axis of different length cannot be represented.
        spec = _variant(golden, "time_len_mismatch")
        source = build_v1_fixture(tmp_path / "mismatch.rcscan", spec)
        out = tmp_path / "out"
        with RcScanV1Reader(source) as reader:
            assert reader.data.time_base is not None
        with pytest.raises(DomainError) as excinfo:
            self._migrate(source, out)
        assert excinfo.value.code == ErrorCode.INVALID_ARGUMENT
        assert "time" in str(excinfo.value).lower() or "axis" in str(excinfo.value).lower()
        assert list(out.glob("*.rcscan")) == []

    def test_migration_target_exists_refused(
        self, golden: dict[str, object], tmp_path: Path
    ) -> None:
        spec = _variant(golden, "calibrated_only")
        source = build_v1_fixture(tmp_path / "src.rcscan", spec)
        out = tmp_path / "out"
        r1 = self._migrate(source, out)
        target = Path(r1.target_path)
        assert _sha256(target) == _sha256(target)
        with pytest.raises(DomainError):
            self._migrate(source, out)

    def test_migration_fault_cleanup_and_retry(
        self, golden: dict[str, object], tmp_path: Path
    ) -> None:
        spec = _variant(golden, "calibrated_only")
        source = build_v1_fixture(tmp_path / "src.rcscan", spec)
        out = tmp_path / "out"

        def failing_hook(phase: str) -> None:
            if phase == "checkpoint":
                raise RuntimeError("injected failure")

        with pytest.raises(DomainError):
            migrate_v1_to_v2(
                source,
                out,
                if_bw_hz=1e3,
                power_dbm=0.0,
                target_interval_s=0.1,
                gnss_max_age_s=5.0,
                software_version="issue013.test",
                fault_hook=failing_hook,
            )
        assert list(out.glob("*.rcscan")) == []
        # retry after cleanup succeeds
        result = self._migrate(source, out)
        with RcScanReader(Path(result.target_path)) as reader:
            assert reader.committed_record_count == 4

    def test_migration_time_only_variant(
        self, golden: dict[str, object], tmp_path: Path
    ) -> None:
        """Time groups without calibrated migrate per presence (matrix B10)."""
        spec = _variant(golden, "time_only")
        source = build_v1_fixture(tmp_path / "src.rcscan", spec)
        data = _variant_data(spec)
        result = self._migrate(source, tmp_path / "out")
        with h5py.File(Path(result.target_path), "r") as h5:
            assert "/frequency/calibrated" not in h5
            assert np.array_equal(h5["/time_base/data"][...], data["time_base"])
            assert np.array_equal(
                h5["/time_processed/data"][...], data["time_processed"]
            )
        with RcScanReader(Path(result.target_path)) as reader:
            assert reader.committed_record_count == 4

    def test_migration_large_chunked(
        self, golden: dict[str, object], tmp_path: Path
    ) -> None:
        spec = _variant(golden, "full")
        gen = dict(spec["generation"])
        gen["n_traces"] = 2000
        gen["channels"] = [{"logical": "HH", "s_parameter": "S11"}]
        gen["frequencies_hz"] = {"start": 5e8, "stop": 2.5e9, "count": 32}
        gen.pop("calibrated", None)
        gen.pop("time_base_s", None)
        gen.pop("time_processed_s", None)
        gen["with_trace_metadata"] = True
        gen["with_position"] = False
        gen["position_source"] = "none"
        gen["position_stop_m"] = 0.0
        big_spec = dict(spec)
        big_spec["generation"] = gen
        big_spec["expected"] = {
            "channel_ids": ["hh_s11"],
            "display_names": ["HH S11"],
            "logical_polarizations": ["hh"],
            "s_parameters": ["s11"],
        }
        source = build_v1_fixture(tmp_path / "big.rcscan", big_spec)
        out = tmp_path / "out"
        result = self._migrate(source, out)
        with RcScanReader(Path(result.target_path)) as reader:
            assert reader.committed_record_count == 2000
            physical = sum(
                len(chunk.records)
                for chunk in reader.iter_physical(chunk_rows=64)
            )
            assert physical == 2000
            logical = sum(len(chunk.records) for chunk in reader.iter_logical(chunk_rows=64))
            assert logical == 2000

    def test_migration_explicit_ids_and_air_role(
        self, golden: dict[str, object], tmp_path: Path
    ) -> None:
        spec = _variant(golden, "calibrated_only")
        source = build_v1_fixture(tmp_path / "src.rcscan", spec)
        out = tmp_path / "out"
        mission = MissionId("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
        file_id = AirFileId("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
        device = DeviceId("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
        result = migrate_v1_to_v2(
            source,
            out,
            if_bw_hz=1e3,
            power_dbm=0.0,
            target_interval_s=0.1,
            gnss_max_age_s=5.0,
            software_version="issue013.test",
            mission_id=mission,
            file_id=file_id,
            device_id=device,
            role=EndpointRole.AIR,
        )
        assert result.mission_id == mission
        assert result.file_id == file_id
        assert result.device_id == device
        with RcScanReader(Path(result.target_path)) as reader:
            assert reader.mission_id == mission
            assert reader.device_id == device
            assert reader.committed_record_count == 4

    def test_migration_ground_file_has_no_transport(
        self, golden: dict[str, object], tmp_path: Path
    ) -> None:
        spec = _variant(golden, "calibrated_only")
        source = build_v1_fixture(tmp_path / "src.rcscan", spec)
        result = self._migrate(source, tmp_path / "out")
        with h5py.File(Path(result.target_path), "r") as h5:
            assert "/transport" not in h5
        with RcScanReader(Path(result.target_path)) as reader:
            assert reader.committed_record_count == 4


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()
