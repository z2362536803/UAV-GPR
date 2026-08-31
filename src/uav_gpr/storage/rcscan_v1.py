"""ISSUE-013: read-only rebar-inspector ``.rcscan`` v1 adapter + explicit v1→v2 migration.

This module implements the read side of the frozen v1 format contract of the
rebar-inspector project (``src/rebar_inspector/storage/rcscan.py`` @ manifest
sha256 ``290c5dad…``, extracted read-only from the local reference copy) and
the explicit, deterministic v1→v2 migration:

- :class:`RcScanV1Reader` opens a v1 file strictly (``format_name="rcscan"``
  and an exact integer ``schema_version == 1``), mirrors the v1 validation
  semantics (strict JSON, required nodes, node types, shape agreement,
  ``time_processed`` requires ``time_base``, position-source consistency) and
  maps raw/calibrated/time/channels/axes/history onto the UAV-GPR domain
  models (:class:`~uav_gpr.core.frequency.FrequencyScan`,
  :class:`~uav_gpr.core.time_domain.TimeDomainScan`).  v1 has no mission,
  GNSS or (optionally) per-trace UTC: those stay ``None``/empty — no current
  time, no 0/0 coordinates are ever fabricated.  Time-domain scans receive a
  synthesized import provenance record (``v1_import_time_base`` /
  ``v1_import_time_processed``) whose parameters embed the verbatim v1
  history JSON and whose ``executed_utc`` is the v1 file's ``created_utc``
  (never ``now()``).
- :func:`inspect_v1` returns a field-level :class:`V1InspectionReport` and
  never raises for content issues (only unreadable/non-HDF5 files raise).
- :func:`migrate_v1_to_v2` writes a new v2 file: a new ``mission_id`` /
  ``file_id`` / ``device_id`` and per-trace ``trace_uid`` are derived
  deterministically with uuid5 (``V1_MIGRATION_NAMESPACE`` + the source file
  sha256), every row is projected through the authoritative ISSUE-008 codec
  (:func:`~uav_gpr.storage.rcscan_v2.trace_metadata_to_cells`), optional
  groups (calibrated / time_base / time_processed) are written directly, and
  the output is staged as ``*.partial.rcscan``, validated with the strict
  ISSUE-011 :class:`~uav_gpr.storage.rcscan_reader.RcScanReader` and only
  then atomically renamed.  Migration provenance attributes follow the
  ISSUE-012 4.1 pattern (``migration_source_sha256``,
  ``migration_tool_version``, ``migration_v1_created_utc``,
  ``migration_source_format``, ``migration_v1_frequency_history``).

Honesty rules enforced here (AGENTS.md sections 3/5):

- the source v1 file is only ever opened read-only and its bytes never
  change (tests pin the sha256 before/after);
- no UTC/GNSS/position fabrication: missing per-trace timestamps block the
  migration (the frozen v2 row contract cannot express "no acquisition
  time"); GNSS rows are all invalid with ``gnss_missing`` quality;
- monotonic nanoseconds in migrated rows are *derived* deterministically
  from the v1 UTC timestamps relative to the v1 ``created_utc`` (v1 has no
  monotonic clock record and the frozen v2 row contract does not allow a
  missing value) — documented import artifacts, never presented as hardware
  monotonic readings;
- v1 frequency history has no v2 dataset: it is preserved verbatim in the
  ``migration_v1_frequency_history`` mission attribute;
- v2 ``history_json`` datasets carry the canonical serialization of the
  mapped domain :class:`~uav_gpr.core.time_domain.ProcessingHistory` (the
  verbatim v1 history is embedded in the import record parameters).
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import h5py  # type: ignore[import-untyped]
import numpy as np

from uav_gpr.core.channels import ChannelSpec
from uav_gpr.core.config import MissionConfig
from uav_gpr.core.enums import (
    AcquisitionMode,
    DataDomain,
    EndpointRole,
    GnssNoFixPolicy,
    LogicalPolarization,
    SParameter,
    TimeDomainKind,
    TraceQualityReason,
    TraceQualityStatus,
)
from uav_gpr.core.errors import DomainError, ErrorCode, JsonValue
from uav_gpr.core.frequency import FrequencyScan
from uav_gpr.core.identifiers import AirFileId, DeviceId, GroundFileId, MissionId, TraceUid
from uav_gpr.core.metadata import TraceMetadata
from uav_gpr.core.raw_hash import compute_raw_trace_sha256
from uav_gpr.core.time_domain import ProcessingHistory, ProcessingRecord, TimeDomainScan
from uav_gpr.core.timeutil import (
    Clock,
    MonotonicNs,
    SystemClock,
    ensure_utc,
    from_utc_iso,
    to_utc_iso,
)
from uav_gpr.storage import rcscan_v2 as schema
from uav_gpr.storage.rcscan_reader import RcScanReader

__all__ = [
    "V1_MIGRATION_NAMESPACE",
    "V1_MIGRATION_TOOL_VERSION",
    "MigrationResult",
    "RcScanV1Reader",
    "V1FieldStatus",
    "V1HistoryEntry",
    "V1InspectionReport",
    "V1RcScanData",
    "inspect_v1",
    "migrate_v1_to_v2",
]

#: Frozen v1 format identity (rcscan.py).
_FORMAT_NAME = "rcscan"
_SCHEMA_VERSION = 1

#: v1 enum value sets (core/enums.py).
_V1_LOGICAL_CHANNELS = frozenset({"HH", "VV"})
_V1_S_PARAMETERS = frozenset({"S11", "S21", "S12", "S22"})
_V1_TRIGGER_MODES = frozenset({"time", "encoder_wheel", "optical_encoder"})
_V1_POSITION_SOURCES = frozenset({"none", "time_estimated", "encoder"})

#: Migration identity namespace (frozen; golden manifest pins the value).
V1_MIGRATION_NAMESPACE = uuid.UUID("9c5c4f3e-2a1b-4c6d-8e7f-0a1b2c3d4e5f")
#: Migration tool component version (also used as writer_version / import
#: provenance software version).
V1_MIGRATION_TOOL_VERSION = "issue013.1"

#: Row-prefix datasets written per migrated trace (mirrors the ISSUE-010
#: writer row set; transport is skipped when absent for ground files).
_ROW_PREFIXES = ("/trace_metadata/", "/gnss/", "/acquisition/", "/transport/")


def _loads_json_strict(text: str, what: str) -> Any:
    """Strict JSON parse mirroring the v1 reader (rejects NaN/Infinity)."""

    def _reject_constant(name: str) -> Any:
        raise ValueError(f"JSON constant {name!r} is not allowed")

    try:
        return json.loads(text, parse_constant=_reject_constant)
    except ValueError as exc:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "invalid JSON payload",
            {"field": what, "detail": str(exc)},
        ) from exc


def _require_dataset(container: h5py.Group | h5py.File, path: str) -> h5py.Dataset:
    if path not in container:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "required v1 node is missing",
            {"field": path},
        )
    node = container[path]
    if not isinstance(node, h5py.Dataset):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "v1 node must be a dataset",
            {"field": path, "kind": type(node).__name__},
        )
    return node


def _require_group(container: h5py.Group | h5py.File, path: str) -> h5py.Group:
    if path not in container:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "required v1 node is missing",
            {"field": path},
        )
    node = container[path]
    if not isinstance(node, h5py.Group):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "v1 node must be a group",
            {"field": path, "kind": type(node).__name__},
        )
    return node


def _attr_text(container: h5py.Group | h5py.File, name: str) -> str:
    if name not in container.attrs:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "required v1 attribute is missing",
            {"field": name},
        )
    value = container.attrs[name]
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _coerce_schema_version(raw: object) -> int:
    """v1 requires a true integer (bool/float/str rejected)."""
    if isinstance(raw, bool):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "schema_version must be a true integer",
            {"schema_version": repr(raw)},
        )
    if isinstance(raw, int):
        return raw
    if isinstance(raw, np.integer):
        return int(raw)
    raise DomainError(
        ErrorCode.INVALID_ARGUMENT,
        "schema_version must be a true integer",
        {"schema_version_type": type(raw).__name__},
    )


def _require_finite_axis(values: object, field: str) -> np.ndarray:
    axis = np.asarray(values, dtype="<f8")
    if axis.ndim != 1 or axis.size == 0:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "v1 axis must be one-dimensional and non-empty",
            {"field": field, "ndim": int(axis.ndim), "size": int(axis.size)},
        )
    if not np.all(np.isfinite(axis)):
        raise DomainError(ErrorCode.NON_FINITE_AXIS, "v1 axis must be finite")
    if axis.size > 1 and not np.all(np.diff(axis) > 0):
        raise DomainError(
            ErrorCode.NON_INCREASING_AXIS, "v1 axis must be strictly increasing"
        )
    return axis


def _history_entries_from_json(text: str, what: str) -> tuple[V1HistoryEntry, ...]:
    parsed = _loads_json_strict(text, what)
    if not isinstance(parsed, list):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "v1 history payload must be a JSON array",
            {"field": what},
        )
    entries: list[V1HistoryEntry] = []
    for item in parsed:
        if not isinstance(item, dict):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "v1 history entry must be an object",
                {"field": what},
            )
        stage = item.get("stage")
        if not isinstance(stage, str) or not stage.strip():
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "v1 history entry requires a non-empty stage",
                {"field": what},
            )
        params = item.get("params", {})
        if not isinstance(params, dict):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "v1 history params must be an object",
                {"field": what},
            )
        timestamp_raw = item.get("timestamp")
        if not isinstance(timestamp_raw, str):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "v1 history entry requires an ISO timestamp",
                {"field": what},
            )
        try:
            timestamp = from_utc_iso(timestamp_raw)
        except (DomainError, TypeError, ValueError) as exc:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "v1 history timestamp is not a valid UTC time",
                {"field": what, "detail": str(exc)},
            ) from exc
        entries.append(V1HistoryEntry(stage=stage, params=params, timestamp=timestamp))
    return tuple(entries)


def _map_channel(item: Mapping[str, object]) -> ChannelSpec:
    logical = item.get("logical")
    s_parameter = item.get("s_parameter")
    if not isinstance(logical, str) or not isinstance(s_parameter, str):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "v1 channel entry requires logical and s_parameter strings",
            {},
        )
    if logical not in _V1_LOGICAL_CHANNELS or s_parameter not in _V1_S_PARAMETERS:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "unknown v1 channel enum value",
            {"logical": logical, "s_parameter": s_parameter},
        )
    return ChannelSpec(
        channel_id=f"{logical.lower()}_{s_parameter.lower()}",
        logical_polarization=LogicalPolarization.from_value(logical.lower()),
        s_parameter=SParameter.from_value(s_parameter.lower()),
        display_name=f"{logical} {s_parameter}",
        antenna_note=None,
    )


def _channels_from_json(text: str) -> tuple[ChannelSpec, ...]:
    parsed = _loads_json_strict(text, "channels")
    if not isinstance(parsed, list):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "v1 channels payload must be a JSON array",
            {"field": "channels"},
        )
    try:
        return tuple(_map_channel(item) for item in parsed)
    except (KeyError, TypeError, ValueError) as exc:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "invalid v1 channel entry",
            {"field": "channels", "detail": str(exc)},
        ) from exc


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _read_text_dataset(dataset: h5py.Dataset) -> str:
    return _text(dataset.asstr()[()])


def _read_text_array(dataset: h5py.Dataset) -> list[str]:
    return [str(value) for value in dataset.asstr()[()]]


@dataclass(frozen=True, slots=True)
class V1HistoryEntry:
    """One v1 processing-history entry (stage/params/timestamp)."""

    stage: str
    params: Mapping[str, JsonValue]
    timestamp: datetime

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "stage": self.stage,
            "params": dict(self.params),
            "timestamp": to_utc_iso(self.timestamp),
        }


@dataclass(frozen=True, slots=True)
class V1FieldStatus:
    """One field of the v1 structure inspection."""

    path: str
    kind: str
    present: bool
    status: str
    detail: str = ""
    dtype: str | None = None
    shape: tuple[int, ...] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "kind": self.kind,
            "present": self.present,
            "status": self.status,
            "detail": self.detail,
            "dtype": self.dtype,
            "shape": list(self.shape) if self.shape is not None else None,
        }


@dataclass(frozen=True, slots=True)
class V1InspectionReport:
    """Field-level report of one v1 file (never raises for content issues)."""

    path: str
    format_name: str | None
    schema_version: object
    source_sha256: str
    fields: tuple[V1FieldStatus, ...]

    @property
    def schema_version_status(self) -> str:
        for entry in self.fields:
            if entry.path == "schema_version":
                return entry.status
        return "missing"

    def summary(self) -> dict[str, int]:
        counts = {"ok": 0, "missing": 0, "unsupported": 0, "error": 0}
        for entry in self.fields:
            counts[entry.status] += 1
        return counts

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "format_name": self.format_name,
            "schema_version": self.schema_version,
            "schema_version_status": self.schema_version_status,
            "source_sha256": self.source_sha256,
            "fields": [entry.to_dict() for entry in self.fields],
            "summary": self.summary(),
        }


@dataclass(frozen=True, slots=True)
class V1RcScanData:
    """Mapped v1 content (domain models + preserved optional fields).

    Missing v1 concepts (mission, GNSS, per-trace UTC when absent) are
    ``None``/empty — never fabricated.
    """

    channels: tuple[ChannelSpec, ...]
    frequencies_hz: np.ndarray
    frequency: FrequencyScan
    frequency_calibrated: np.ndarray | None
    frequency_history: tuple[V1HistoryEntry, ...]
    time_base: TimeDomainScan | None
    time_processed: TimeDomainScan | None
    trace_timestamps_utc: tuple[datetime, ...] | None
    trace_extras: tuple[Mapping[str, JsonValue], ...] | None
    position_m: np.ndarray | None
    position_source: str | None
    trigger: str | None
    created_utc: datetime | None
    generator: str | None
    source_sha256: str


@dataclass(frozen=True, slots=True)
class MigrationResult:
    """Outcome of one explicit v1→v2 migration."""

    source_path: str
    target_path: str
    source_sha256: str
    mission_id: MissionId
    file_id: AirFileId | GroundFileId
    device_id: DeviceId
    trace_uids: tuple[str, ...]
    committed_record_count: int
    config_sha256: str
    tool_version: str


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _synthesize_time_history(
    *,
    kind: TimeDomainKind,
    created_utc: datetime,
    v1_history_json: str,
) -> ProcessingHistory:
    """Build the import provenance for one v1 time-domain scan.

    The verbatim v1 history JSON is embedded in the record parameters (lossless
    preservation); ``executed_utc`` is the v1 file's ``created_utc`` — never
    the current time.
    """
    base = ProcessingRecord(
        stage_name="v1_import_time_base",
        stage_version=V1_MIGRATION_TOOL_VERSION,
        parameters={"v1_history_json": v1_history_json},
        input_domain=DataDomain.FREQUENCY_RAW,
        output_domain=DataDomain.TIME_BASE,
        executed_utc=created_utc,
        software_version=V1_MIGRATION_TOOL_VERSION,
    )
    if kind is TimeDomainKind.TIME_BASE:
        return ProcessingHistory((base,))
    processed = ProcessingRecord(
        stage_name="v1_import_time_processed",
        stage_version=V1_MIGRATION_TOOL_VERSION,
        parameters={"v1_history_json": v1_history_json},
        input_domain=DataDomain.TIME_BASE,
        output_domain=DataDomain.TIME_PROCESSED,
        executed_utc=created_utc,
        software_version=V1_MIGRATION_TOOL_VERSION,
    )
    return ProcessingHistory((base, processed))


class RcScanV1Reader:
    """Read-only strict reader over one rebar-inspector ``.rcscan`` v1 file.

    Opening validates the whole v1 structure and fails closed on any
    violation (unknown version, missing/typed-wrong required nodes, strict
    JSON failures, shape disagreement, ``time_processed`` without
    ``time_base``, position-source inconsistency).  The instance owns the
    HDF5 handle: call :meth:`close` (or use it as a context manager).  The
    source file is opened ``"r"`` and never modified.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._source_sha256 = _sha256_file(self._path)
        try:
            self._h5 = h5py.File(self._path, "r")
        except OSError as error:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "cannot open file as HDF5",
                {"path": str(self._path)},
            ) from error
        self._closed = False
        try:
            self._data = self._read_document()
        except BaseException:
            self._h5.close()
            raise

    # -- validation ---------------------------------------------------------

    def _require_version(self) -> None:
        format_name = _attr_text(self._h5, "format_name")
        if format_name != _FORMAT_NAME:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "not a rcscan v1 file",
                {"format_name": format_name},
            )
        if "schema_version" not in self._h5.attrs:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "schema_version attribute is missing",
                {"field": "schema_version"},
            )
        version = _coerce_schema_version(self._h5.attrs["schema_version"])
        if version != _SCHEMA_VERSION:
            raise DomainError(
                ErrorCode.UNSUPPORTED_SCHEMA_VERSION,
                "unsupported rcscan schema_version",
                {"schema_version": version, "supported": _SCHEMA_VERSION},
            )

    def _read_document(self) -> V1RcScanData:
        self._require_version()
        trigger = _attr_text(self._h5, "trigger")
        position_source = _attr_text(self._h5, "position_source")
        if trigger not in _V1_TRIGGER_MODES:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "unknown v1 trigger value",
                {"trigger": trigger},
            )
        if position_source not in _V1_POSITION_SOURCES:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "unknown v1 position_source value",
                {"position_source": position_source},
            )

        created_raw = self._h5.attrs.get("created_utc")
        created_utc: datetime | None = None
        if created_raw is not None:
            try:
                created_utc = from_utc_iso(_text(created_raw))
            except (DomainError, TypeError, ValueError) as exc:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "v1 created_utc attribute is not a valid UTC time",
                    {"field": "created_utc", "detail": str(exc)},
                ) from exc
        generator = self._h5.attrs.get("generator")
        generator_text = _text(generator) if generator is not None else None

        channels = _channels_from_json(_read_text_dataset(_require_dataset(self._h5, "channels")))
        frequencies = _require_finite_axis(
            _require_dataset(self._h5, "/axes/frequencies_hz")[()],
            "/axes/frequencies_hz",
        )
        freq_group = _require_group(self._h5, "frequency")
        raw = np.asarray(_require_dataset(freq_group, "/frequency/raw")[()], dtype="<c16")
        if raw.ndim != 3 or raw.shape[0] == 0:
            raise DomainError(
                ErrorCode.SHAPE_MISMATCH,
                "v1 raw must be a non-empty trace x channel x frequency cube",
                {"shape": list(raw.shape)},
            )
        if raw.shape[1] != len(channels):
            raise DomainError(
                ErrorCode.SHAPE_MISMATCH,
                "v1 raw channel axis does not match channels",
                {
                    "raw_channels": int(raw.shape[1]),
                    "channel_count": len(channels),
                },
            )
        if raw.shape[2] != int(frequencies.size):
            raise DomainError(
                ErrorCode.SHAPE_MISMATCH,
                "v1 raw frequency axis does not match the frequency axis",
                {
                    "raw_frequencies": int(raw.shape[2]),
                    "frequency_points": int(frequencies.size),
                },
            )
        n_traces = int(raw.shape[0])

        calibrated = None
        if "calibrated" in freq_group:
            calibrated = np.asarray(
                _require_dataset(freq_group, "/frequency/calibrated")[()], dtype="<c16"
            )
            if calibrated.shape != raw.shape:
                raise DomainError(
                    ErrorCode.SHAPE_MISMATCH,
                    "v1 calibrated shape must equal raw shape",
                    {"calibrated_shape": list(calibrated.shape), "raw_shape": list(raw.shape)},
                )
            calibrated.setflags(write=False)

        frequency_history = _history_entries_from_json(
            _read_text_dataset(_require_dataset(freq_group, "/frequency/history_json")),
            "frequency history_json",
        )

        position_m: np.ndarray | None = None
        if "position_m" in self._h5:
            position_m = np.asarray(
                _require_dataset(self._h5, "position_m")[()], dtype="<f8"
            )
            if position_m.ndim != 1 or position_m.shape[0] != n_traces:
                raise DomainError(
                    ErrorCode.SHAPE_MISMATCH,
                    "v1 position_m must have one value per trace",
                    {"position_len": int(position_m.shape[0]), "n_traces": n_traces},
                )
            if not np.all(np.isfinite(position_m)):
                raise DomainError(ErrorCode.NON_FINITE_AXIS, "v1 position_m must be finite")
            position_m.setflags(write=False)
        if (position_m is None) != (position_source == "none"):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "v1 position_m and position_source are inconsistent",
                {
                    "has_position": position_m is not None,
                    "position_source": position_source,
                },
            )

        trace_timestamps: tuple[datetime, ...] | None = None
        trace_extras: tuple[Mapping[str, JsonValue], ...] | None = None
        if "trace_metadata" in self._h5:
            meta_group = _require_group(self._h5, "trace_metadata")
            timestamps_raw = _read_text_array(
                _require_dataset(meta_group, "timestamps_utc")
            )
            extras_raw = _read_text_array(_require_dataset(meta_group, "extras_json"))
            if len(timestamps_raw) != n_traces or len(extras_raw) != n_traces:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "v1 trace_metadata counts must equal the trace count",
                    {
                        "timestamps": len(timestamps_raw),
                        "extras": len(extras_raw),
                        "n_traces": n_traces,
                    },
                )
            parsed_timestamps: list[datetime] = []
            for index, text in enumerate(timestamps_raw):
                try:
                    parsed_timestamps.append(from_utc_iso(text))
                except (DomainError, TypeError, ValueError) as exc:
                    raise DomainError(
                        ErrorCode.INVALID_ARGUMENT,
                        "v1 trace timestamp is not a valid UTC time",
                        {"trace_index": index, "detail": str(exc)},
                    ) from exc
            parsed_extras: list[Mapping[str, JsonValue]] = []
            for index, text in enumerate(extras_raw):
                extra = _loads_json_strict(text, "trace_metadata extras")
                if not isinstance(extra, dict):
                    raise DomainError(
                        ErrorCode.INVALID_ARGUMENT,
                        "v1 trace extras must be a JSON object",
                        {"trace_index": index},
                    )
                parsed_extras.append(cast(Mapping[str, JsonValue], extra))
            trace_timestamps = tuple(parsed_timestamps)
            trace_extras = tuple(parsed_extras)

        time_base = self._read_time_scan(
            kind=TimeDomainKind.TIME_BASE,
            created_utc=created_utc,
            n_traces=n_traces,
            channels=channels,
        )
        time_processed = None
        if "time_processed" in self._h5:
            if time_base is None:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "v1 file contains time_processed but no time_base",
                    {"field": "time_processed"},
                )
            time_processed = self._read_time_scan(
                kind=TimeDomainKind.TIME_PROCESSED,
                created_utc=created_utc,
                n_traces=n_traces,
                channels=channels,
            )

        frequency = FrequencyScan(
            channels=channels,
            frequencies_hz=frequencies,
            data=raw,
            metadata=(),
        )
        frequencies.setflags(write=False)
        return V1RcScanData(
            channels=channels,
            frequencies_hz=frequencies,
            frequency=frequency,
            frequency_calibrated=calibrated,
            frequency_history=frequency_history,
            time_base=time_base,
            time_processed=time_processed,
            trace_timestamps_utc=trace_timestamps,
            trace_extras=trace_extras,
            position_m=position_m,
            position_source=position_source,
            trigger=trigger,
            created_utc=created_utc,
            generator=generator_text,
            source_sha256=self._source_sha256,
        )

    def _read_time_scan(
        self,
        *,
        kind: TimeDomainKind,
        created_utc: datetime | None,
        n_traces: int,
        channels: tuple[ChannelSpec, ...],
    ) -> TimeDomainScan | None:
        node = kind.value  # "time_base" | "time_processed"
        if node not in self._h5:
            return None
        group = _require_group(self._h5, node)
        axis_name = f"/axes/{node}_s"
        time_axis = _require_finite_axis(_require_dataset(self._h5, axis_name)[()], axis_name)
        data = np.asarray(_require_dataset(group, "data")[()], dtype="<c16")
        if data.ndim != 3 or data.shape[0] != n_traces:
            raise DomainError(
                ErrorCode.SHAPE_MISMATCH,
                "v1 time-domain data must match the trace count",
                {"field": node, "shape": list(data.shape), "n_traces": n_traces},
            )
        if data.shape[1] != len(channels):
            raise DomainError(
                ErrorCode.SHAPE_MISMATCH,
                "v1 time-domain channel axis does not match channels",
                {"field": node, "channels": int(data.shape[1]), "expected": len(channels)},
            )
        if data.shape[2] != int(time_axis.size):
            raise DomainError(
                ErrorCode.SHAPE_MISMATCH,
                "v1 time-domain data does not match its time axis",
                {"field": node},
            )
        history_json = _read_text_dataset(_require_dataset(group, "history_json"))
        _history_entries_from_json(history_json, f"{node} history_json")
        if created_utc is None:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "v1 time-domain group present but created_utc is missing",
                {"field": node},
            )
        history = _synthesize_time_history(
            kind=kind,
            created_utc=created_utc,
            v1_history_json=history_json,
        )
        return TimeDomainScan(
            channels=channels,
            time_axis_s=time_axis,
            data=data,
            kind=kind,
            history=history,
            metadata=(),
        )

    # -- introspection ------------------------------------------------------

    @property
    def path(self) -> Path:
        return self._path

    @property
    def source_sha256(self) -> str:
        return self._source_sha256

    @property
    def data(self) -> V1RcScanData:
        return self._data

    # -- bounded row access (used by the migration writer) ------------------

    def raw_row(self, index: int) -> np.ndarray:
        return np.asarray(self._h5["/frequency/raw"][index], dtype="<c16")

    def calibrated_row(self, index: int) -> np.ndarray | None:
        if self._data.frequency_calibrated is None:
            return None
        return np.asarray(self._h5["/frequency/calibrated"][index], dtype="<c16")

    def time_base_row(self, index: int) -> np.ndarray | None:
        if self._data.time_base is None:
            return None
        return np.asarray(self._h5["/time_base/data"][index], dtype="<c16")

    def time_processed_row(self, index: int) -> np.ndarray | None:
        if self._data.time_processed is None:
            return None
        return np.asarray(self._h5["/time_processed/data"][index], dtype="<c16")

    def time_base_axis(self) -> np.ndarray | None:
        if self._data.time_base is None:
            return None
        return np.asarray(self._h5["/axes/time_base_s"][...], dtype="<f8")

    def time_processed_axis(self) -> np.ndarray | None:
        if self._data.time_processed is None:
            return None
        return np.asarray(self._h5["/axes/time_processed_s"][...], dtype="<f8")

    def frequency_history_json(self) -> str:
        return _read_text_dataset(self._h5["/frequency/history_json"])

    def time_base_history_json(self) -> str | None:
        if self._data.time_base is None:
            return None
        return _read_text_dataset(self._h5["/time_base/history_json"])

    def time_processed_history_json(self) -> str | None:
        if self._data.time_processed is None:
            return None
        return _read_text_dataset(self._h5["/time_processed/history_json"])

    # -- report -------------------------------------------------------------

    def inspection_report(self) -> V1InspectionReport:
        return _build_report(self._h5, self._path, self._source_sha256)

    # -- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._h5.close()

    def __enter__(self) -> RcScanV1Reader:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Field-level inspection
# ---------------------------------------------------------------------------

_ATTR_FIELDS = (
    "format_name",
    "schema_version",
    "created_utc",
    "generator",
    "trigger",
    "position_source",
)
_NODE_FIELDS = (
    "channels",
    "/axes/frequencies_hz",
    "/axes/time_base_s",
    "/axes/time_processed_s",
    "/frequency",
    "/frequency/raw",
    "/frequency/calibrated",
    "/frequency/history_json",
    "/position_m",
    "/trace_metadata",
    "/trace_metadata/timestamps_utc",
    "/trace_metadata/extras_json",
    "/time_base",
    "/time_base/data",
    "/time_base/history_json",
    "/time_processed",
    "/time_processed/data",
    "/time_processed/history_json",
)


def _build_report(
    h5: h5py.File, path: Path, source_sha256: str
) -> V1InspectionReport:
    fields: list[V1FieldStatus] = []
    for name in _ATTR_FIELDS:
        if name not in h5.attrs:
            fields.append(V1FieldStatus(name, "attr", False, "missing"))
            continue
        value = h5.attrs[name]
        status = "ok"
        detail = ""
        if name == "schema_version":
            try:
                version = _coerce_schema_version(value)
            except DomainError as exc:
                status = "error"
                detail = str(exc)
            else:
                if version != _SCHEMA_VERSION:
                    status = "unsupported"
                    detail = f"supported: {_SCHEMA_VERSION}"
        elif name == "format_name" and _text(value) != _FORMAT_NAME:
            status = "error"
            detail = f"format_name={_text(value)}"
        fields.append(
            V1FieldStatus(name, "attr", True, status, detail, dtype=type(value).__name__)
        )
    for node in _NODE_FIELDS:
        if node not in h5:
            node_kind = "group" if node.count("/") == 0 else "dataset"
            fields.append(V1FieldStatus(node, node_kind, False, "missing"))
            continue
        item = h5[node]
        kind = "group" if isinstance(item, h5py.Group) else "dataset"
        group_nodes = frozenset(
            {"/frequency", "/trace_metadata", "/time_base", "/time_processed"}
        )
        expected = "group" if node in group_nodes else "dataset"
        status = "ok"
        detail = ""
        if kind != expected:
            status = "error"
            detail = f"expected {expected}, found {kind}"
        dtype = str(item.dtype) if kind == "dataset" else None
        shape = tuple(int(axis) for axis in item.shape) if kind == "dataset" else None
        fields.append(
            V1FieldStatus(node, kind, True, status, detail, dtype=dtype, shape=shape)
        )
    format_name = (
        _text(h5.attrs["format_name"]) if "format_name" in h5.attrs else None
    )
    schema_version = h5.attrs.get("schema_version")
    return V1InspectionReport(
        path=str(path),
        format_name=format_name,
        schema_version=schema_version,
        source_sha256=source_sha256,
        fields=tuple(fields),
    )


def inspect_v1(path: str | Path) -> V1InspectionReport:
    """Field-level inspection of one v1 file.

    Never raises for content issues (unknown version, missing fields,
    corrupt nodes are reported per field); only unreadable / non-HDF5 files
    raise a structured :class:`DomainError`.
    """
    source = Path(path)
    try:
        h5 = h5py.File(source, "r")
    except OSError as error:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "cannot open file as HDF5",
            {"path": str(source)},
        ) from error
    try:
        return _build_report(h5, source, _sha256_file(source))
    finally:
        h5.close()


# ---------------------------------------------------------------------------
# Explicit v1 -> v2 migration
# ---------------------------------------------------------------------------


def _derive_id(namespace: uuid.UUID, name: str, cls: type[Any]) -> Any:
    return cls(str(uuid.uuid5(namespace, name)))


def _require_non_decreasing(timestamps: Sequence[datetime], field: str) -> None:
    for left, right in itertools.pairwise(timestamps):
        if right < left:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "v1 trace timestamps must be non-decreasing",
                {"field": field, "left": to_utc_iso(left), "right": to_utc_iso(right)},
            )

def migrate_v1_to_v2(
    source: str | Path,
    target_dir: str | Path,
    *,
    if_bw_hz: float,
    power_dbm: float,
    target_interval_s: float,
    gnss_max_age_s: float,
    software_version: str,
    mission_id: MissionId | None = None,
    file_id: AirFileId | GroundFileId | None = None,
    device_id: DeviceId | None = None,
    role: EndpointRole = EndpointRole.GROUND,
    created_utc: datetime | None = None,
    note: str | None = None,
    clock: Clock | None = None,
    fault_hook: Any = None,
) -> MigrationResult:
    """Explicitly migrate one v1 file into a new v2 file.

    Determinism contract: with identical inputs and an identical injected
    clock, repeated migrations produce byte-identical outputs; identifiers
    derive from the source file sha256 via uuid5 (``V1_MIGRATION_NAMESPACE``)
    unless explicitly overridden.  The source file is never modified; the
    target is never overwritten (an existing target or stale staging partial
    refuses the migration).  ``fault_hook(phase)`` receives
    ``"rows"``/``"optional"``/``"checkpoint"``/``"finalize"`` and may raise
    to exercise the fail-closed cleanup path.
    """
    source_path = Path(source)
    target_directory = Path(target_dir)
    if not isinstance(role, EndpointRole):
        raise TypeError(f"role must be an EndpointRole, got {type(role).__name__}")
    target_directory.mkdir(parents=True, exist_ok=True)
    clock_impl = clock if clock is not None else SystemClock()
    hook = fault_hook if fault_hook is not None else (lambda phase: None)

    with RcScanV1Reader(source_path) as reader:
        data = reader.data
        timestamps = data.trace_timestamps_utc
        if timestamps is None:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "v1 file has no per-trace timestamps; migration cannot build "
                "v2 trace rows without acquisition time",
                {"path": str(source_path)},
            )
        if not timestamps:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "v1 file has no per-trace timestamps",
                {"path": str(source_path)},
            )
        created = ensure_utc(created_utc) if created_utc is not None else data.created_utc
        if created is None:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "v1 created_utc attribute is missing and no explicit value was given",
                {"path": str(source_path)},
            )
        _require_non_decreasing(timestamps, "trace timestamps")
        for index, timestamp in enumerate(timestamps):
            if timestamp < created:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "v1 trace timestamp precedes created_utc; monotonic values "
                    "cannot be derived",
                    {"trace_index": index},
                )

        frequency_points = int(data.frequencies_hz.size)
        for axis_name, axis in (
            ("time_base", reader.time_base_axis()),
            ("time_processed", reader.time_processed_axis()),
        ):
            if axis is not None and int(axis.size) != frequency_points:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "v1 time-domain axis length differs from the frequency "
                    "axis length; the frozen v2 reader contract ties time axes "
                    "to the frequency point count",
                    {
                        "field": axis_name,
                        "time_points": int(axis.size),
                        "frequency_points": frequency_points,
                    },
                )

        config = MissionConfig.from_frequency_axis(
            frequency_axis_hz=data.frequencies_hz,
            if_bw_hz=if_bw_hz,
            power_dbm=power_dbm,
            channels=data.channels,
            acquisition_mode=AcquisitionMode.CONTINUOUS,
            planned_trace_count=None,
            target_interval_s=target_interval_s,
            gnss_max_age_s=gnss_max_age_s,
            gnss_no_fix_policy=GnssNoFixPolicy.RECORD_WITHOUT_POSITION,
            created_utc=created,
            software_version=software_version,
            note=note,
        )

        source_sha256 = reader.source_sha256
        mission = mission_id if mission_id is not None else _derive_id(
            V1_MIGRATION_NAMESPACE, f"mission:{source_sha256}", MissionId
        )
        if file_id is not None:
            expected_type = AirFileId if role is EndpointRole.AIR else GroundFileId
            if not isinstance(file_id, expected_type):
                raise TypeError(
                    f"file_id must be a {expected_type.__name__} for role "
                    f"{role.value}, got {type(file_id).__name__}"
                )
            chosen_file_id = file_id
        elif role is EndpointRole.AIR:
            chosen_file_id = _derive_id(
                V1_MIGRATION_NAMESPACE, f"file:{source_sha256}", AirFileId
            )
        else:
            chosen_file_id = _derive_id(
                V1_MIGRATION_NAMESPACE, f"file:{source_sha256}", GroundFileId
            )
        device = device_id if device_id is not None else _derive_id(
            V1_MIGRATION_NAMESPACE, f"device:{source_sha256}", DeviceId
        )

        n_traces = int(data.frequency.data.shape[0])
        trace_uids = tuple(
            str(
                uuid.uuid5(
                    V1_MIGRATION_NAMESPACE, f"trace:{source_sha256}:{index}"
                )
            )
            for index in range(n_traces)
        )

        metadata_rows: list[TraceMetadata] = []
        for index in range(n_traces):
            timestamp = timestamps[index]
            derived_ns = int((timestamp - created).total_seconds() * 1_000_000_000)
            monotonic = MonotonicNs(derived_ns)
            actual: float | None = None
            schedule: float | None = None
            if index > 0:
                actual = (timestamp - timestamps[index - 1]).total_seconds()
                schedule = actual - target_interval_s
            metadata_rows.append(
                TraceMetadata(
                    mission_id=mission,
                    trace_index=index,
                    trace_uid=TraceUid(trace_uids[index]),
                    device_id=device,
                    sweep_started_utc=timestamp,
                    sweep_midpoint_utc=timestamp,
                    sweep_finished_utc=timestamp,
                    sweep_started_monotonic_ns=monotonic,
                    sweep_midpoint_monotonic_ns=monotonic,
                    sweep_finished_monotonic_ns=monotonic,
                    target_interval_s=target_interval_s,
                    actual_interval_s=actual,
                    schedule_error_s=schedule,
                    connection_generation=0,
                    raw_trace_sha256=compute_raw_trace_sha256(
                        mission_id=mission,
                        trace_index=index,
                        trace_uid=TraceUid(trace_uids[index]),
                        channels=data.channels,
                        frequencies_hz=data.frequencies_hz,
                        data=reader.raw_row(index),
                    ),
                    gnss_match=None,
                    quality_status=TraceQualityStatus.DEGRADED,
                    quality_reasons=(TraceQualityReason.GNSS_MISSING,),
                )
            )

        partial_path = target_directory / f"{chosen_file_id}{'.partial.rcscan'}"
        final_path = target_directory / f"{chosen_file_id}.rcscan"
        if final_path.exists():
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "target already exists; refusing to overwrite",
                {"path": str(final_path)},
            )
        if partial_path.exists():
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "stale staging file already exists; refusing to overwrite",
                {"path": str(partial_path)},
            )

        schema.create_rcscan_v2(
            partial_path,
            mission_id=mission,
            device_id=device,
            file_id=chosen_file_id,
            created_utc=created,
            completed_utc=None,
            completion_kind=None,
            file_role=role,
            channels=data.channels,
            frequencies_hz=data.frequencies_hz,
            config_json=config.to_canonical_json(),
            config_sha256=config.config_sha256,
            writer_version=V1_MIGRATION_TOOL_VERSION,
        )
        v1_created_utc = data.created_utc
        frequency_history_json = reader.frequency_history_json()
        time_base_history_json = (
            schema.dumps_utf8_json(data.time_base.history.to_dict())
            if data.time_base is not None
            else None
        )
        time_processed_history_json = (
            schema.dumps_utf8_json(data.time_processed.history.to_dict())
            if data.time_processed is not None
            else None
        )

    try:
        _write_migrated_file(
            partial_path=partial_path,
            final_path=final_path,
            source_path=source_path,
            source_sha256=source_sha256,
            device=device,
            config=config,
            metadata_rows=metadata_rows,
            v1_created_utc=v1_created_utc,
            timestamps=timestamps,
            frequency_history_json=frequency_history_json,
            time_base_history_json=time_base_history_json,
            time_processed_history_json=time_processed_history_json,
            clock=clock_impl,
            fault_hook=hook,
        )
    except BaseException as error:
        _cleanup_staging(partial_path)
        if isinstance(error, DomainError):
            raise
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "v1 to v2 migration failed",
            {"path": str(partial_path), "detail": str(error)},
        ) from error

    return MigrationResult(
        source_path=str(source_path),
        target_path=str(final_path),
        source_sha256=source_sha256,
        mission_id=mission,
        file_id=chosen_file_id,
        device_id=device,
        trace_uids=trace_uids,
        committed_record_count=len(metadata_rows),
        config_sha256=config.config_sha256,
        tool_version=V1_MIGRATION_TOOL_VERSION,
    )


def _cleanup_staging(partial_path: Path) -> None:
    try:
        partial_path.unlink(missing_ok=True)
    except OSError:
        pass


def _write_migrated_file(
    *,
    partial_path: Path,
    final_path: Path,
    source_path: Path,
    source_sha256: str,
    device: DeviceId,
    config: MissionConfig,
    metadata_rows: Sequence[TraceMetadata],
    v1_created_utc: datetime | None,
    timestamps: Sequence[datetime],
    frequency_history_json: str,
    time_base_history_json: str | None,
    time_processed_history_json: str | None,
    clock: Clock,
    fault_hook: Any,
) -> None:
    h5 = h5py.File(partial_path, "r+")
    try:
        contracts = schema.dataset_contracts(
            len(config.channels), int(config.frequency_axis_hz.size)
        )
        row_paths = tuple(
            contract.path
            for contract in contracts
            if not contract.optional
            and contract.path.startswith(_ROW_PREFIXES)
            and contract.path in h5
        )
        n_traces = len(metadata_rows)
        for path in row_paths:
            h5[path].resize((n_traces,))
        h5["/frequency/raw"].resize((n_traces, *h5["/frequency/raw"].shape[1:]))

        source = h5py.File(source_path, "r")
        try:
            for index, metadata in enumerate(metadata_rows):
                cells = schema.trace_metadata_to_cells(metadata)
                for path in row_paths:
                    h5[path][index] = cells[path]
                h5["/frequency/raw"][index] = source["/frequency/raw"][index]
        finally:
            source.close()
        fault_hook("rows")

        _write_optional_groups(
            h5,
            source_path,
            n_traces,
            len(config.channels),
            config,
            time_base_history_json,
            time_processed_history_json,
        )
        fault_hook("optional")

        mission_attrs = h5["mission"].attrs
        mission_attrs["started_utc"] = to_utc_iso(timestamps[0])
        mission_attrs["ended_utc"] = to_utc_iso(timestamps[-1])
        mission_attrs["completion_kind"] = "completed"
        mission_attrs["migration_source_sha256"] = source_sha256
        mission_attrs["migration_tool_version"] = V1_MIGRATION_TOOL_VERSION
        mission_attrs["migration_source_format"] = "rcscan_v1"
        mission_attrs["migration_v1_created_utc"] = (
            to_utc_iso(v1_created_utc) if v1_created_utc is not None else ""
        )
        mission_attrs["migration_v1_frequency_history"] = frequency_history_json
        h5.attrs["lifecycle_state"] = "finalized"

        fault_hook("checkpoint")
        h5["/checkpoints/committed_record_count"][0] = n_traces
        h5["/checkpoints/last_trace_index"][0] = n_traces - 1
        h5["/checkpoints/updated_utc"][0] = to_utc_iso(clock.utc_now())
        h5.flush()
        h5.close()

        fault_hook("finalize")
        with RcScanReader(partial_path) as reader:
            if reader.committed_record_count != n_traces:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "migrated file failed strict reader validation",
                    {
                        "committed": reader.committed_record_count,
                        "expected": n_traces,
                    },
                )
        if final_path.exists():
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "target already exists; refusing to overwrite",
                {"path": str(final_path)},
            )
        os.replace(partial_path, final_path)
    except BaseException:
        try:
            h5.close()
        except Exception:
            pass
        raise


def _write_optional_groups(
    h5: h5py.File,
    source_path: Path,
    n_traces: int,
    channel_count: int,
    config: MissionConfig,
    time_base_history_json: str | None,
    time_processed_history_json: str | None,
) -> None:
    source = h5py.File(source_path, "r")
    try:
        frequency_points = int(config.frequency_axis_hz.size)
        if "/frequency/calibrated" in source["frequency"]:
            dataset = h5.create_dataset(
                "/frequency/calibrated",
                shape=(n_traces, channel_count, frequency_points),
                maxshape=(None, channel_count, frequency_points),
                dtype="<c16",
                chunks=(1, channel_count, frequency_points),
            )
            for index in range(n_traces):
                dataset[index] = source["/frequency/calibrated"][index]
        for node, history_json in (
            ("time_base", time_base_history_json),
            ("time_processed", time_processed_history_json),
        ):
            if node not in source:
                continue
            assert history_json is not None
            time_points = int(source[f"/axes/{node}_s"].shape[0])
            h5.create_dataset(
                f"/axes/{node}_s",
                data=source[f"/axes/{node}_s"][...],
                dtype="<f8",
            )
            dataset = h5.create_dataset(
                f"/{node}/data",
                shape=(n_traces, channel_count, time_points),
                maxshape=(None, channel_count, time_points),
                dtype="<c16",
                chunks=(1, channel_count, time_points),
            )
            for index in range(n_traces):
                dataset[index] = source[f"/{node}/data"][index]
            h5.create_dataset(
                f"/{node}/history_json",
                data=np.asarray([history_json], dtype=h5py.string_dtype(encoding="utf-8")),
                dtype=h5py.string_dtype(encoding="utf-8"),
            )
    finally:
        source.close()
