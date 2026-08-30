"""ISSUE-011: read-only RcScanReader / RcScanValidator for ``.rcscan`` v2.

This module implements the read side of the frozen storage contract on top
of the ISSUE-008 physical schema (:mod:`uav_gpr.storage.rcscan_v2`), the
ISSUE-009 canonical raw hash (:mod:`uav_gpr.core.raw_hash`) and the
ISSUE-010 writer semantics (:mod:`uav_gpr.storage.incremental_writer`).

Contract (``docs/DATA_FORMAT.md`` sections 2/3, M02 ISSUE-011):

- **Strict open-time validation, fail closed.**  ``format_name`` /
  ``schema_version`` / ``profile`` / ``file_role`` / ``lifecycle_state``
  come from the ISSUE-008 probe; every present dataset is checked against
  its frozen contract (dtype, maxshape, chunks, compression, fixed-axis
  lengths); required datasets must be present (``/transport`` is optional
  for ground files); the mission config, channel definitions, frequency
  axis and config digest must agree with each other; the checkpoint
  (``committed_record_count`` in ``[0, min column length]``,
  ``last_trace_index`` and ``updated_utc``) must be sane.  Unknown schema
  versions and profiles are rejected by the probe.  Optional processed
  groups (``time_base`` / ``time_processed`` / calibrated) may be absent;
  when present they are validated like any other dataset.
- **Visibility window.**  Only physical rows ``< committed_record_count``
  whose required columns are complete are ever exposed; a half-written tail
  (rows beyond the last checkpoint) is invisible by construction.
- **Dual views.**  :meth:`RcScanReader.iter_physical` yields the committed
  rows in physical commit order; :meth:`RcScanReader.iter_logical` yields
  them ordered by the explicit ``trace_index`` (ties broken by commit
  position) with duplicates collapsed to their first committed copy.
  Conflicts never resolve silently: an index whose committed copies carry
  different raw hashes, or a ``trace_uid`` reused at a different index, is
  excluded from the logical view, reported with retained evidence
  (:class:`ConflictTrace`) and rejected by :meth:`RcScanReader.trace_by_index`
  with ``ErrorCode.ID_CONFLICT`` (fail closed, no arbitrary copy).
- **Missing / duplicates / issues.**  Gaps inside ``[0, max committed
  trace_index]`` are reported as :class:`MissingTrace`; identical copies as
  :class:`DuplicateTrace`; per-row problems (missing stored hash, hash
  mismatch, undecodable cells) as :class:`RowIssue`.
- **Lazy, bounded reads.**  Iteration reads row columns and raw data in
  slices of at most ``chunk_rows`` records; the whole file is never
  materialized.  The classification scan decodes committed rows once, on
  first use, keeping only small per-row metadata in memory.
- **Lifecycle presentation.**  ``lifecycle_state`` is exposed verbatim.
  A finalized/recovered file that is still named ``*.partial.rcscan``
  (the ISSUE-010 ``awaiting_rename`` state after a failed rename) is
  presented with :attr:`RcScanReader.rename_pending` ``True`` and reads as a
  completed task — never as an ordinary unfinished (``writing``) mission.
  Full recovery handling is ISSUE-012, out of scope here.

The reader never mutates the file (strictly ``"r"`` access) and never
repairs, migrates or processes data.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

import h5py  # type: ignore[import-untyped]
import numpy as np

from uav_gpr.core.channels import ChannelSpec
from uav_gpr.core.config import MissionConfig
from uav_gpr.core.enums import EndpointRole, LogicalPolarization, SParameter
from uav_gpr.core.errors import DomainError, ErrorCode, JsonValue
from uav_gpr.core.identifiers import DeviceId, MissionId
from uav_gpr.core.metadata import TraceMetadata
from uav_gpr.core.raw_hash import compute_raw_trace_sha256
from uav_gpr.core.timeutil import from_utc_iso, to_utc_iso
from uav_gpr.storage import rcscan_v2 as schema
from uav_gpr.storage.incremental_writer import FINAL_SUFFIX, PARTIAL_SUFFIX

__all__ = [
    "ConflictTrace",
    "DuplicateTrace",
    "IssueKind",
    "MissingTrace",
    "RcScanReader",
    "RcScanValidator",
    "ReadTrace",
    "RowIssue",
    "TraceChunk",
    "ValidationReport",
]

#: Trace-major physical column groups written per logical commit (mirrors the
#: ISSUE-010 writer's row prefix set; kept local so the reader never depends
#: on writer-internal names).
_ROW_PREFIXES = ("/trace_metadata/", "/gnss/", "/acquisition/", "/transport/")

#: Canonical lowercase SHA-256 digest (same contract as rcscan_v2).
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

#: Records decoded per classification slice (bounded memory).
_CLASSIFY_CHUNK = 64


class IssueKind(StrEnum):
    """Machine-readable kind of one per-row validation issue."""

    MISSING_HASH = "missing_hash"
    HASH_MISMATCH = "hash_mismatch"
    ROW_DECODE_ERROR = "row_decode_error"
    CHECKPOINT_INCONSISTENCY = "checkpoint_inconsistency"
    LIFECYCLE_NAME_MISMATCH = "lifecycle_name_mismatch"


@dataclass(frozen=True, slots=True)
class RowIssue:
    """One per-row data-level issue (reported, never silently dropped)."""

    kind: IssueKind
    record_position: int
    trace_index: int | None
    trace_uid: str | None
    detail: str


@dataclass(frozen=True, slots=True)
class MissingTrace:
    """One logical ``trace_index`` absent from the committed set."""

    trace_index: int


@dataclass(frozen=True, slots=True)
class DuplicateTrace:
    """Identical copies of one logical trace (same index, uid and hash)."""

    trace_index: int
    trace_uid: str
    raw_trace_sha256: str
    record_positions: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ConflictTrace:
    """Ambiguous identity with retained evidence (never silently resolved).

    ``trace_index`` is the first committed index of the group; for a
    ``trace_uid`` reused at different indices it is the first index carrying
    that uid.
    """

    trace_index: int
    trace_uid: str
    record_positions: tuple[int, ...]
    raw_hashes: tuple[str, ...]
    trace_uids: tuple[str, ...]
    detail: str


@dataclass(frozen=True, slots=True)
class ReadTrace:
    """One decoded committed trace as served by a view."""

    record_position: int
    trace_index: int
    trace_uid: str
    metadata: TraceMetadata
    frequency_raw: np.ndarray
    raw_trace_sha256: str
    hash_verified: bool


@dataclass(frozen=True, slots=True)
class TraceChunk:
    """One bounded slice of a view: served records ``[start_position,
    stop_position)`` of that view's ordinal sequence."""

    start_position: int
    stop_position: int
    records: tuple[ReadTrace, ...]


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Structured, serializable outcome of a strict read/validation pass."""

    path: str
    format_name: str
    schema_version: int
    profile: str
    file_role: str
    lifecycle_state: str
    completion_kind: str
    planned_trace_count: int | None
    committed_record_count: int
    physical_record_count: int
    missing: tuple[MissingTrace, ...]
    duplicates: tuple[DuplicateTrace, ...]
    conflicts: tuple[ConflictTrace, ...]
    issues: tuple[RowIssue, ...]

    def summary(self) -> dict[str, int]:
        """Counts of each report category (plain ints)."""
        return {
            "committed_record_count": self.committed_record_count,
            "physical_record_count": self.physical_record_count,
            "missing": len(self.missing),
            "duplicates": len(self.duplicates),
            "conflicts": len(self.conflicts),
            "issues": len(self.issues),
        }

    def to_dict(self) -> dict[str, object]:
        """Plain JSON-safe serialization (stable key order)."""
        return {
            "path": self.path,
            "format_name": self.format_name,
            "schema_version": self.schema_version,
            "profile": self.profile,
            "file_role": self.file_role,
            "lifecycle_state": self.lifecycle_state,
            "completion_kind": self.completion_kind,
            "planned_trace_count": self.planned_trace_count,
            "committed_record_count": self.committed_record_count,
            "physical_record_count": self.physical_record_count,
            "missing": [{"trace_index": entry.trace_index} for entry in self.missing],
            "duplicates": [
                {
                    "trace_index": entry.trace_index,
                    "trace_uid": entry.trace_uid,
                    "raw_trace_sha256": entry.raw_trace_sha256,
                    "record_positions": list(entry.record_positions),
                }
                for entry in self.duplicates
            ],
            "conflicts": [
                {
                    "trace_index": entry.trace_index,
                    "trace_uid": entry.trace_uid,
                    "record_positions": list(entry.record_positions),
                    "raw_hashes": list(entry.raw_hashes),
                    "trace_uids": list(entry.trace_uids),
                    "detail": entry.detail,
                }
                for entry in self.conflicts
            ],
            "issues": [
                {
                    "kind": entry.kind.value,
                    "record_position": entry.record_position,
                    "trace_index": entry.trace_index,
                    "trace_uid": entry.trace_uid,
                    "detail": entry.detail,
                }
                for entry in self.issues
            ],
            "summary": self.summary(),
        }


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _as_str(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def _as_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise ValueError(f"{field} must be an integer")
    return int(value)


def _require_hex64(value: object, field: str) -> str:
    text = _as_str(value, field)
    if _HEX64_RE.fullmatch(text) is None:
        raise ValueError(f"{field} must be a 64-character lowercase hex digest")
    return text


def _parse_channel_specs(parsed: object) -> tuple[ChannelSpec, ...]:
    """Rebuild ChannelSpec from the frozen definitions_json contract."""
    if not isinstance(parsed, list):
        raise ValueError("channel definitions_json entry must be an array")
    specs: list[ChannelSpec] = []
    for item in parsed:
        if not isinstance(item, dict):
            raise ValueError("channel definitions_json entry must be an object")
        antenna_note = item.get("antenna_note")
        specs.append(
            ChannelSpec(
                channel_id=_as_str(item["channel_id"], "channel_id"),
                logical_polarization=LogicalPolarization.from_value(
                    _as_str(item["logical_polarization"], "logical_polarization")
                ),
                s_parameter=SParameter.from_value(
                    _as_str(item["s_parameter"], "s_parameter")
                ),
                display_name=_as_str(item["display_name"], "display_name"),
                antenna_note=(
                    _as_str(antenna_note, "antenna_note")
                    if antenna_note is not None
                    else None
                ),
            )
        )
    return tuple(specs)


@dataclass(slots=True)
class _RowInfo:
    """Small per-row metadata retained from the classification scan."""

    position: int
    metadata: TraceMetadata
    raw_trace_sha256: str
    hash_verified: bool


class RcScanReader:
    """Read-only strict reader over one ``.rcscan`` v2 file (ISSUE-011).

    Opening validates the whole schema-level contract and fails closed on
    any violation; per-row data-level problems never raise at open but are
    reported by :meth:`validation_report` and are excluded from the served
    views according to the module contract.  The instance owns the HDF5
    handle: call :meth:`close` (or use it as a context manager).
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._probe = schema.probe_rcscan_v2(self._path)
        try:
            self._h5 = h5py.File(self._path, "r")
        except OSError as error:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "cannot open rcscan file for reading",
                {"path": str(self._path)},
            ) from error
        self._closed = False
        try:
            self._load_mission()
            self._load_contract()
            self._load_checkpoint()
        except BaseException:
            self._h5.close()
            raise

        # Lazily built classification of the committed rows.
        self._classification_built = False
        self._row_infos: list[_RowInfo] = []
        self._info_by_position: dict[int, _RowInfo] = {}
        self._issues: list[RowIssue] = []
        self._by_index: dict[int, list[int]] = {}
        self._by_uid: dict[str, list[int]] = {}
        self._conflicting_indices: set[int] = set()
        self._conflicting_uids: set[str] = set()
        self._missing: tuple[MissingTrace, ...] = ()
        self._duplicates: list[DuplicateTrace] = []
        self._conflicts: list[ConflictTrace] = []
        self._max_decoded_index = -1

    # -- construction helpers (all fail closed) -----------------------------

    def _load_mission(self) -> None:
        attrs = self._h5["mission"].attrs
        try:
            self._mission_id = MissionId.from_json(
                _as_str(attrs["mission_id"], "mission_id")
            )
            self._device_id = DeviceId.from_json(
                _as_str(attrs["device_id"], "device_id")
            )
            created_raw = _as_str(attrs["created_utc"], "created_utc")
            self._created_utc = from_utc_iso(created_raw)
            started_raw = _as_str(attrs["started_utc"], "started_utc")
            self._started_utc = from_utc_iso(started_raw) if started_raw else None
            ended_raw = _as_str(attrs["ended_utc"], "ended_utc")
            self._ended_utc = from_utc_iso(ended_raw) if ended_raw else None
            self._completion_kind = _as_str(attrs["completion_kind"], "completion_kind")
            self._config_sha256 = _require_hex64(attrs["config_sha256"], "config_sha256")
        except (DomainError, KeyError, TypeError, ValueError) as error:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "rcscan mission attributes are invalid",
                {"field": "mission"},
            ) from error

    def _load_contract(self) -> None:
        # config_json -> MissionConfig (digest verified fail-closed).
        raw_config = self._h5["/mission/config_json"][()]
        config_text = _text(raw_config)
        try:
            parsed_config = schema.loads_utf8_json(config_text)
        except ValueError as error:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "config_json is not valid canonical JSON",
                {"field": "config_json"},
            ) from error
        if not isinstance(parsed_config, dict):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "config_json must encode a JSON object",
                {"field": "config_json"},
            )
        # The canonical contract JSON omits descriptive keys; inject the
        # mission-level values so the public MissionConfig.from_dict (which
        # verifies the digest fail-closed) accepts it.
        config_data = dict(parsed_config)
        config_data["created_utc"] = to_utc_iso(self._created_utc)
        config_data["config_sha256"] = self._config_sha256
        try:
            config = MissionConfig.from_dict(config_data)
        except (DomainError, KeyError, TypeError, ValueError) as error:
            raise DomainError(
                ErrorCode.CONFIG_DIGEST_MISMATCH,
                "config_json is not a valid MissionConfig contract",
                {"field": "config_json"},
            ) from error
        if config.to_canonical_json() != config_text:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "config_json is not canonical MissionConfig JSON",
                {"field": "config_json"},
            )
        self._config = config

        # Channel definitions -> ChannelSpec tuple; must equal the config.
        raw_definitions = self._h5["/channels/definitions_json"][0]
        try:
            parsed_definitions = schema.loads_utf8_json(_text(raw_definitions))
            definitions = _parse_channel_specs(parsed_definitions)
        except (DomainError, KeyError, TypeError, ValueError) as error:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "channel definitions_json is invalid",
                {"field": "definitions_json"},
            ) from error
        if definitions != config.channels:
            raise DomainError(
                ErrorCode.CHANNEL_CONTRACT_MISMATCH,
                "channel definitions do not match the mission config",
                {
                    "definitions": [c.channel_id for c in definitions],
                    "config_channels": [c.channel_id for c in config.channels],
                },
            )
        channel_ids = [c.channel_id for c in definitions]
        if len(set(channel_ids)) != len(channel_ids):
            raise DomainError(
                ErrorCode.DUPLICATE_CHANNEL,
                "channel definitions contain duplicate channel ids",
                {},
            )
        self._channels = definitions

        # Frequency axis: value-level contract + agreement with the config.
        axis = np.asarray(self._h5["/axes/frequencies_hz"][...], dtype="<f8")
        if axis.ndim != 1 or axis.size < 2:
            raise DomainError(
                ErrorCode.AXIS_MISMATCH,
                "frequency axis must be one-dimensional with at least 2 points",
                {"frequency_points": int(axis.size)},
            )
        if not np.all(np.isfinite(axis)):
            raise DomainError(ErrorCode.NON_FINITE_AXIS, "frequency axis must be finite")
        if not np.all(np.diff(axis) > 0):
            raise DomainError(
                ErrorCode.NON_INCREASING_AXIS,
                "frequency axis must be strictly increasing",
            )
        config_axis = np.asarray(config.frequency_axis_hz, dtype="<f8")
        if axis.shape != config_axis.shape or not np.array_equal(axis, config_axis):
            raise DomainError(
                ErrorCode.AXIS_MISMATCH,
                "frequency axis does not match the mission config axis",
                {"axis_points": int(axis.size), "config_points": int(config_axis.size)},
            )
        self._frequencies_hz = axis

        # Every present dataset must match its frozen contract; required
        # datasets must exist (transport optional for ground files).
        contracts = schema.dataset_contracts(len(self._channels), int(axis.size))
        required_row_paths: list[str] = []
        for contract in contracts:
            present = contract.path in self._h5
            if present:
                self._validate_present_dataset(self._h5[contract.path], contract)
            if contract.optional:
                continue
            if contract.path.startswith("/transport") and (
                self._probe.file_role is EndpointRole.GROUND
            ):
                continue
            if not present:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "required dataset is missing from the rcscan file",
                    {"dataset": contract.path},
                )
            if contract.path.startswith(_ROW_PREFIXES):
                required_row_paths.append(contract.path)
        self._row_paths = tuple(required_row_paths)

        # Column-length window: the checkpoint may never exceed the shortest
        # required trace-major column (a shorter column is corruption).
        lengths = [int(self._h5[path].shape[0]) for path in self._row_paths]
        lengths.append(int(self._h5["/frequency/raw"].shape[0]))
        self._physical_record_count = min(lengths)

    @staticmethod
    def _validate_present_dataset(dataset: Any, contract: schema.DatasetContract) -> None:
        """Validate one present dataset against its frozen ISSUE-008 contract.

        Local mirror of ``rcscan_v2._validate_dataset_against_contract``
        (which stays private there; this Issue may not modify that module).
        """
        if tuple(dataset.maxshape) != contract.maxshape:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "dataset maxshape does not match frozen schema",
                {"dataset": contract.path},
            )
        if dataset.dtype != contract.dtype:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "dataset dtype does not match frozen schema",
                {"dataset": contract.path},
            )
        if contract.chunks is None:
            if dataset.chunks is not None:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "fixed dataset must not carry explicit chunks",
                    {"dataset": contract.path},
                )
        elif dataset.chunks is None or tuple(dataset.chunks) != contract.chunks:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "dataset chunks do not match frozen schema",
                {"dataset": contract.path},
            )
        if dataset.compression != contract.compression:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "dataset compression does not match frozen schema",
                {"dataset": contract.path},
            )
        if len(dataset.shape) != len(contract.initial_shape):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "dataset rank does not match frozen schema",
                {"dataset": contract.path},
            )
        for axis_index, (actual, initial, maximum) in enumerate(
            zip(dataset.shape, contract.initial_shape, contract.maxshape, strict=True)
        ):
            if maximum is None:
                if actual < 0:
                    raise DomainError(
                        ErrorCode.INVALID_ARGUMENT,
                        "dataset shape is negative",
                        {"dataset": contract.path, "axis": axis_index},
                    )
                continue
            if actual != initial:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "dataset fixed axis does not match frozen schema",
                    {"dataset": contract.path, "axis": axis_index},
                )

    def _load_checkpoint(self) -> None:
        committed = _as_int(
            self._h5["/checkpoints/committed_record_count"][0], "checkpoint"
        )
        if committed < 0:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "checkpoint committed_record_count is negative",
                {"committed_record_count": committed},
            )
        if committed > self._physical_record_count:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "checkpoint points beyond the durable record columns",
                {
                    "committed_record_count": committed,
                    "physical_record_count": self._physical_record_count,
                },
            )
        self._committed = committed
        last_index = _as_int(
            self._h5["/checkpoints/last_trace_index"][0], "last_trace_index"
        )
        if committed == 0:
            if last_index != schema.MISSING_INT64:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "empty checkpoint must carry the missing last_trace_index",
                    {"last_trace_index": last_index},
                )
        elif last_index < 0:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "committed checkpoint carries an invalid last_trace_index",
                {"last_trace_index": last_index},
            )
        self._last_trace_index = last_index
        raw_updated = self._h5["/checkpoints/updated_utc"][0]
        try:
            self._checkpoint_updated_utc = from_utc_iso(_text(raw_updated))
        except (DomainError, TypeError, ValueError) as error:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "checkpoint updated_utc is not a valid UTC timestamp",
                {"field": "updated_utc"},
            ) from error

        name = self._path.name
        lifecycle = self._probe.lifecycle_state
        self._rename_pending = (
            lifecycle in ("finalized", "recovered") and name.endswith(PARTIAL_SUFFIX)
        )

    # -- introspection ------------------------------------------------------

    @property
    def path(self) -> Path:
        return self._path

    @property
    def probe(self) -> schema.RcscanProbe:
        return self._probe

    @property
    def mission_id(self) -> MissionId:
        return self._mission_id

    @property
    def device_id(self) -> DeviceId:
        return self._device_id

    @property
    def channels(self) -> tuple[ChannelSpec, ...]:
        return self._channels

    @property
    def frequencies_hz(self) -> np.ndarray:
        return self._frequencies_hz

    @property
    def config(self) -> MissionConfig:
        return self._config

    @property
    def committed_record_count(self) -> int:
        """Checkpoint: how many physical rows a reader may see."""
        return self._committed

    @property
    def physical_record_count(self) -> int:
        """Physical rows present in every required column (>= committed)."""
        return self._physical_record_count

    @property
    def lifecycle_state(self) -> str:
        return self._probe.lifecycle_state

    @property
    def completion_kind(self) -> str:
        return self._completion_kind

    @property
    def rename_pending(self) -> bool:
        """Finalized/recovered data still named ``*.partial.rcscan`` (the
        writer's ``awaiting_rename`` presentation)."""
        return self._rename_pending

    @property
    def last_trace_index(self) -> int:
        return self._last_trace_index

    @property
    def checkpoint_updated_utc(self) -> datetime:
        return self._checkpoint_updated_utc

    # -- classification (lazy, once) ----------------------------------------

    def _ensure_classification(self) -> None:
        if self._classification_built:
            return
        for start in range(0, self._committed, _CLASSIFY_CHUNK):
            stop = min(start + _CLASSIFY_CHUNK, self._committed)
            column_slices = {
                path: self._h5[path][start:stop] for path in self._row_paths
            }
            raw_slice = self._h5["/frequency/raw"][start:stop]
            for offset, position in enumerate(range(start, stop)):
                info = self._decode_row(offset, position, column_slices, raw_slice)
                if info is None:
                    continue
                self._row_infos.append(info)
                self._info_by_position[position] = info
                self._by_index.setdefault(info.metadata.trace_index, []).append(position)
                self._by_uid.setdefault(info.metadata.trace_uid.to_json(), []).append(
                    position
                )
                if info.metadata.trace_index > self._max_decoded_index:
                    self._max_decoded_index = info.metadata.trace_index

        decoded_indices: set[int] = set()
        for index, positions in self._by_index.items():
            hashes = tuple(
                self._info_by_position[p].raw_trace_sha256 for p in positions
            )
            uids = tuple(
                self._info_by_position[p].metadata.trace_uid.to_json() for p in positions
            )
            if len(set(hashes)) > 1:
                self._conflicting_indices.add(index)
                self._conflicts.append(
                    ConflictTrace(
                        trace_index=index,
                        trace_uid=uids[0],
                        record_positions=tuple(positions),
                        raw_hashes=hashes,
                        trace_uids=uids,
                        detail=(
                            "committed copies of trace_index carry different "
                            "raw hashes"
                        ),
                    )
                )
            elif len(positions) > 1:
                self._duplicates.append(
                    DuplicateTrace(
                        trace_index=index,
                        trace_uid=uids[0],
                        raw_trace_sha256=hashes[0],
                        record_positions=tuple(positions),
                    )
                )
            decoded_indices.add(index)

        for uid, positions in self._by_uid.items():
            indices = {
                self._info_by_position[p].metadata.trace_index for p in positions
            }
            if len(indices) > 1:
                self._conflicting_uids.add(uid)
                ordered = tuple(positions)
                first_index = self._info_by_position[ordered[0]].metadata.trace_index
                self._conflicts.append(
                    ConflictTrace(
                        trace_index=first_index,
                        trace_uid=uid,
                        record_positions=ordered,
                        raw_hashes=tuple(
                            self._info_by_position[p].raw_trace_sha256
                            for p in ordered
                        ),
                        trace_uids=tuple(
                            self._info_by_position[p].metadata.trace_uid.to_json()
                            for p in ordered
                        ),
                        detail="trace_uid is reused at different trace_index values",
                    )
                )

        # Missing: holes inside [0, max committed decoded index].
        if self._max_decoded_index >= 0:
            self._missing = tuple(
                MissingTrace(trace_index=index)
                for index in range(0, self._max_decoded_index + 1)
                if index not in decoded_indices
            )

        if (
            self._probe.lifecycle_state == "writing"
            and self._path.name.endswith(FINAL_SUFFIX)
            and not self._path.name.endswith(PARTIAL_SUFFIX)
        ):
            self._issues.append(
                RowIssue(
                    kind=IssueKind.LIFECYCLE_NAME_MISMATCH,
                    record_position=-1,
                    trace_index=None,
                    trace_uid=None,
                    detail="writing lifecycle_state in a file named .rcscan",
                )
            )
        if self._committed > 0 and self._last_trace_index < self._max_decoded_index:
            self._issues.append(
                RowIssue(
                    kind=IssueKind.CHECKPOINT_INCONSISTENCY,
                    record_position=-1,
                    trace_index=None,
                    trace_uid=None,
                    detail=(
                        f"checkpoint last_trace_index {self._last_trace_index} is "
                        f"below the max committed trace_index {self._max_decoded_index}"
                    ),
                )
            )
        self._classification_built = True

    def _decode_row(
        self,
        offset: int,
        position: int,
        column_slices: Mapping[str, Any],
        raw_slice: Any,
    ) -> _RowInfo | None:
        """Decode one committed row from bounded slice arrays.

        ``offset`` indexes into the slices, ``position`` is the absolute
        physical row.  Any cell-level failure records a :class:`RowIssue` and
        yields ``None``: a row without valid identity is not a complete
        record and is never served.
        """
        cells = {path: values[offset] for path, values in column_slices.items()}
        try:
            metadata = schema.trace_metadata_from_cells(
                cells,
                mission_id=self._mission_id,
                device_id=self._device_id,
            )
        except (DomainError, KeyError, TypeError, ValueError) as error:
            self._issues.append(
                RowIssue(
                    kind=IssueKind.ROW_DECODE_ERROR,
                    record_position=position,
                    trace_index=None,
                    trace_uid=None,
                    detail=(
                        f"row {position} cannot be decoded: "
                        f"{type(error).__name__}: {error}"
                    ),
                )
            )
            return None
        stored = metadata.raw_trace_sha256
        if stored is None:
            self._issues.append(
                RowIssue(
                    kind=IssueKind.MISSING_HASH,
                    record_position=position,
                    trace_index=metadata.trace_index,
                    trace_uid=metadata.trace_uid.to_json(),
                    detail="row carries no stored raw_trace_sha256",
                )
            )
            return _RowInfo(
                position=position,
                metadata=metadata,
                raw_trace_sha256="",
                hash_verified=False,
            )
        # The framing input must be a plain int: HDF5 <i8 cells come back as
        # np.int64 and the ISSUE-009 validator requires an exact int.
        raw = np.asarray(raw_slice[offset], dtype="<c16")
        recomputed = compute_raw_trace_sha256(
            mission_id=self._mission_id,
            trace_index=int(metadata.trace_index),
            trace_uid=metadata.trace_uid,
            channels=self._channels,
            frequencies_hz=self._frequencies_hz,
            data=raw,
        )
        verified = recomputed == stored
        if not verified:
            self._issues.append(
                RowIssue(
                    kind=IssueKind.HASH_MISMATCH,
                    record_position=position,
                    trace_index=metadata.trace_index,
                    trace_uid=metadata.trace_uid.to_json(),
                    detail=(
                        f"stored hash {stored!r} differs from the recomputed "
                        f"digest {recomputed!r}"
                    ),
                )
            )
        return _RowInfo(
            position=position,
            metadata=metadata,
            raw_trace_sha256=recomputed,
            hash_verified=verified,
        )

    # -- row reading --------------------------------------------------------

    def _read_records(self, positions: Sequence[int]) -> tuple[ReadTrace, ...]:
        """Read and decode committed rows at ``positions`` in bounded slices.

        Consecutive positions are read as one column/raw slice; non-consecutive
        positions are grouped into consecutive runs, so memory stays bounded
        by the longest run (never the whole file).
        """
        if not positions:
            return ()
        records: list[ReadTrace] = []
        run_start = positions[0]
        run_end = positions[0] + 1
        for position in positions[1:]:
            if position == run_end:
                run_end = position + 1
                continue
            records.extend(self._read_run(run_start, run_end))
            run_start = position
            run_end = position + 1
        records.extend(self._read_run(run_start, run_end))
        return tuple(records)

    def _read_run(self, start: int, stop: int) -> list[ReadTrace]:
        raw_slice = self._h5["/frequency/raw"][start:stop]
        records: list[ReadTrace] = []
        for offset, position in enumerate(range(start, stop)):
            info = self._info_by_position.get(position)
            if info is None:
                continue
            records.append(
                ReadTrace(
                    record_position=position,
                    trace_index=info.metadata.trace_index,
                    trace_uid=info.metadata.trace_uid.to_json(),
                    metadata=info.metadata,
                    frequency_raw=np.asarray(raw_slice[offset], dtype="<c16"),
                    raw_trace_sha256=info.raw_trace_sha256,
                    hash_verified=info.hash_verified,
                )
            )
        return records

    # -- views --------------------------------------------------------------

    def iter_physical(self, chunk_rows: int = 64) -> Iterator[TraceChunk]:
        """Iterate the committed, decodable rows in physical commit order."""
        if chunk_rows < 1:
            raise ValueError("chunk_rows must be at least 1")
        self._ensure_classification()
        served = [info.position for info in self._row_infos]
        for start in range(0, len(served), chunk_rows):
            stop = min(start + chunk_rows, len(served))
            records = self._read_records(served[start:stop])
            yield TraceChunk(start, stop, records)

    def iter_logical(self, chunk_rows: int = 64) -> Iterator[TraceChunk]:
        """Iterate the logical view: ordered by explicit ``trace_index``
        (ties by commit position), duplicates collapsed to their first
        committed copy, conflicting identity excluded."""
        if chunk_rows < 1:
            raise ValueError("chunk_rows must be at least 1")
        self._ensure_classification()
        served: list[int] = []
        for index in sorted(self._by_index):
            if index in self._conflicting_indices:
                continue
            first = self._by_index[index][0]
            if (
                self._info_by_position[first].metadata.trace_uid.to_json()
                in self._conflicting_uids
            ):
                continue
            served.append(first)
        for start in range(0, len(served), chunk_rows):
            stop = min(start + chunk_rows, len(served))
            records = self._read_records(served[start:stop])
            yield TraceChunk(start, stop, records)

    def trace_by_index(self, trace_index: int) -> ReadTrace:
        """Return the single logical record for ``trace_index``.

        Raises ``ErrorCode.ID_CONFLICT`` when the index is part of a
        conflicting identity group (never an arbitrary copy) and
        ``ErrorCode.INVALID_ARGUMENT`` when it is not committed.
        """
        if isinstance(trace_index, bool) or not isinstance(trace_index, int):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "trace_index must be an int",
                {"trace_index_type": type(trace_index).__name__},
            )
        self._ensure_classification()
        if trace_index in self._conflicting_indices:
            evidence = next(
                entry for entry in self._conflicts if entry.trace_index == trace_index
            )
            raise DomainError(
                ErrorCode.ID_CONFLICT,
                "trace_index conflicts with another committed record",
                {
                    "trace_index": trace_index,
                    "record_positions": cast(JsonValue, list(evidence.record_positions)),
                    "raw_hashes": cast(JsonValue, list(evidence.raw_hashes)),
                    "trace_uids": cast(JsonValue, list(evidence.trace_uids)),
                },
            )
        positions = self._by_index.get(trace_index)
        if positions is None:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "trace_index is not committed in this file",
                {"trace_index": trace_index},
            )
        first = positions[0]
        uid = self._info_by_position[first].metadata.trace_uid.to_json()
        if uid in self._conflicting_uids:
            raise DomainError(
                ErrorCode.ID_CONFLICT,
                "trace_uid of this record conflicts with another trace_index",
                {"trace_index": trace_index, "trace_uid": uid},
            )
        return self._read_records((first,))[0]

    # -- report -------------------------------------------------------------

    def validation_report(self) -> ValidationReport:
        """Run the full classification and return the structured report."""
        self._ensure_classification()
        return ValidationReport(
            path=str(self._path),
            format_name=self._probe.format_name,
            schema_version=self._probe.schema_version,
            profile=self._probe.profile,
            file_role=self._probe.file_role.value,
            lifecycle_state=self._probe.lifecycle_state,
            completion_kind=self._completion_kind,
            planned_trace_count=self._config.planned_trace_count,
            committed_record_count=self._committed,
            physical_record_count=self._physical_record_count,
            missing=self._missing,
            duplicates=tuple(self._duplicates),
            conflicts=tuple(self._conflicts),
            issues=tuple(self._issues),
        )

    # -- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        """Close the owned HDF5 handle (idempotent)."""
        if self._closed:
            return
        self._closed = True
        self._h5.close()

    def __enter__(self) -> RcScanReader:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class RcScanValidator:
    """Strict validation entry point (ISSUE-011).

    ``validate`` opens the file through :class:`RcScanReader`, runs the full
    classification and returns the report; schema-level violations propagate
    as :class:`DomainError` and the file handle is always closed.
    """

    @staticmethod
    def validate(path: str | Path) -> ValidationReport:
        with RcScanReader(path) as reader:
            return reader.validation_report()
