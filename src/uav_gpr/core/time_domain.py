"""Immutable processing provenance and time-domain models.

``ProcessingRecord`` captures one processing application: a stable stage name
and version, fully JSON-safe canonical parameters, the input/output
``DataDomain``, the executing software version, execution UTC and optional
calibration/background reference IDs (docs/DATA_MODEL.md section 8,
docs/PROCESSING.md).

``ProcessingHistory`` is an immutable, ordered list of records.  Appending
returns a new object and validates the domain chain (each record's
``input_domain`` must equal the previous record's ``output_domain``) and the
uniqueness of a ``(stage_name, stage_version)`` application; re-applying a
stage requires a new stage version.

``TimeDomainScan`` is the immutable time-domain container with the fixed
shape ``trace x channel x time``, a strictly increasing one-dimensional time
axis in seconds and a ``kind`` of ``time_base`` or ``time_processed``
(docs/PROCESSING.md section 4).  Provenance is fail-closed:

- ``time_base`` may have an empty history (raw IFFT output with no recorded
  stage) or a history ending in ``time_base``;
- ``time_processed`` requires a non-empty history ending in ``time_processed``.

Arrays are bytes-backed and can never be made writable again; caller arrays
and returned views cannot mutate the model.  No uncalibrated depth field or
depth-time value exists anywhere in these models: use ``kind`` and the time
axis, never a depth axis.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from itertools import pairwise
from types import MappingProxyType
from typing import Self, cast

import numpy as np

from uav_gpr.core.channels import ChannelSpec
from uav_gpr.core.enums import DataDomain, TimeDomainKind
from uav_gpr.core.errors import (
    DomainError,
    ErrorCode,
    JsonValue,
    _deep_copy_json,
    _require_json_safe,
)
from uav_gpr.core.frequency import (
    _compact_scan_metadata,
    _immutable_array,
    _require_complex_numeric,
    _validate_channels,
    _validate_scan_metadata,
)
from uav_gpr.core.identifiers import BackgroundReferenceId, CalibrationProfileId
from uav_gpr.core.metadata import TraceMetadata
from uav_gpr.core.timeutil import ensure_utc, from_utc_iso, to_utc_iso

# Stable snake_case stage name: lowercase letter first, then letters/digits/_.
_STAGE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
# Version-like token: starts alphanumeric, then alphanumeric/._-.
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

_TIME_DTYPE = np.dtype(np.float64)
_DATA_DTYPE = np.dtype(np.complex128)


def _require_stage_name(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(
            f"stage_name must be a str, got {type(value).__name__}"
        )
    if _STAGE_NAME_RE.fullmatch(value) is None:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "stage_name must be stable snake_case "
            "(lowercase letter first, then letters/digits/underscore)",
            {"stage_name": value},
        )
    return value


def _require_token(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(
            f"{field} must be a str, got {type(value).__name__}"
        )
    if _TOKEN_RE.fullmatch(value) is None:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            f"{field} must be a version-like token "
            "(alphanumeric first, then letters/digits/dot/underscore/hyphen)",
            {field: value},
        )
    return value


def _validate_time_axis(values: object) -> np.ndarray:
    """One-dimensional strictly increasing finite time axis in seconds."""
    raw = np.asarray(values)
    if raw.dtype.kind not in "iuf":
        raise DomainError(
            ErrorCode.DTYPE_MISMATCH,
            "time axis must be real-valued numeric",
            {"dtype": str(raw.dtype)},
        )
    if raw.ndim != 1:
        raise DomainError(
            ErrorCode.AXIS_MISMATCH,
            "time axis must be one-dimensional",
            {"ndim": raw.ndim},
        )
    if raw.size == 0:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT, "time axis must not be empty"
        )
    if not np.all(np.isfinite(raw)):
        raise DomainError(
            ErrorCode.NON_FINITE_AXIS,
            "time axis must contain only finite values",
        )
    if raw.size > 1 and not np.all(np.diff(raw) > 0):
        raise DomainError(
            ErrorCode.NON_INCREASING_AXIS,
            "time axis must be strictly increasing",
        )
    return np.asarray(raw, dtype=_TIME_DTYPE)


@dataclass(frozen=True, slots=True, init=False)
class ProcessingRecord:
    """One processing application with full provenance (see module docstring).

    ``parameters`` is stored as an independent, read-only, JSON-safe copy; the
    property returns a fresh deep copy on every access, so neither the caller's
    input nor a returned value can mutate the record.
    """

    _stage_name: str
    _stage_version: str
    _parameters: Mapping[str, JsonValue]
    _input_domain: DataDomain
    _output_domain: DataDomain
    _executed_utc: datetime
    _software_version: str
    _calibration_profile_id: CalibrationProfileId | None
    _background_reference_id: BackgroundReferenceId | None

    def __init__(
        self,
        *,
        stage_name: str,
        stage_version: str,
        parameters: Mapping[str, JsonValue],
        input_domain: DataDomain,
        output_domain: DataDomain,
        executed_utc: datetime,
        software_version: str,
        calibration_profile_id: CalibrationProfileId | None = None,
        background_reference_id: BackgroundReferenceId | None = None,
    ) -> None:
        name = _require_stage_name(stage_name)
        version = _require_token(stage_version, "stage_version")
        sw_version = _require_token(software_version, "software_version")
        if not isinstance(parameters, Mapping):
            raise TypeError(
                "parameters must be a mapping, "
                f"got {type(parameters).__name__}"
            )
        parameters_dict = dict(parameters)
        _require_json_safe(parameters_dict, "$parameters")
        if not isinstance(input_domain, DataDomain):
            raise TypeError(
                f"input_domain must be a DataDomain, "
                f"got {type(input_domain).__name__}"
            )
        if not isinstance(output_domain, DataDomain):
            raise TypeError(
                f"output_domain must be a DataDomain, "
                f"got {type(output_domain).__name__}"
            )
        utc = ensure_utc(executed_utc)
        if calibration_profile_id is not None and not isinstance(
            calibration_profile_id, CalibrationProfileId
        ):
            raise TypeError(
                "calibration_profile_id must be a CalibrationProfileId or None"
            )
        if background_reference_id is not None and not isinstance(
            background_reference_id, BackgroundReferenceId
        ):
            raise TypeError(
                "background_reference_id must be a BackgroundReferenceId or None"
            )
        object.__setattr__(self, "_stage_name", name)
        object.__setattr__(self, "_stage_version", version)
        object.__setattr__(
            self,
            "_parameters",
            MappingProxyType(
                cast(
                    dict[str, JsonValue],
                    _deep_copy_json(parameters_dict),
                )
            ),
        )
        object.__setattr__(self, "_input_domain", input_domain)
        object.__setattr__(self, "_output_domain", output_domain)
        object.__setattr__(self, "_executed_utc", utc)
        object.__setattr__(self, "_software_version", sw_version)
        object.__setattr__(self, "_calibration_profile_id", calibration_profile_id)
        object.__setattr__(self, "_background_reference_id", background_reference_id)

    @property
    def stage_name(self) -> str:
        return self._stage_name

    @property
    def stage_version(self) -> str:
        return self._stage_version

    @property
    def parameters(self) -> Mapping[str, JsonValue]:
        """Independent, read-only snapshot of the JSON-safe parameters."""
        return MappingProxyType(
            cast(dict[str, JsonValue], _deep_copy_json(dict(self._parameters)))
        )

    @property
    def input_domain(self) -> DataDomain:
        return self._input_domain

    @property
    def output_domain(self) -> DataDomain:
        return self._output_domain

    @property
    def executed_utc(self) -> datetime:
        return self._executed_utc

    @property
    def software_version(self) -> str:
        return self._software_version

    @property
    def calibration_profile_id(self) -> CalibrationProfileId | None:
        return self._calibration_profile_id

    @property
    def background_reference_id(self) -> BackgroundReferenceId | None:
        return self._background_reference_id

    def parameters_canonical_json(self) -> str:
        """Deterministic canonical JSON of the stage parameters."""
        return json.dumps(
            dict(self._parameters),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )

    def to_dict(self) -> dict[str, JsonValue]:
        """JSON-safe serialization (all scalar values, no arrays)."""
        return {
            "stage_name": self._stage_name,
            "stage_version": self._stage_version,
            "parameters": _deep_copy_json(dict(self._parameters)),
            "input_domain": self._input_domain.value,
            "output_domain": self._output_domain.value,
            "executed_utc": to_utc_iso(self._executed_utc),
            "software_version": self._software_version,
            "calibration_profile_id": (
                self._calibration_profile_id.to_json()
                if self._calibration_profile_id is not None
                else None
            ),
            "background_reference_id": (
                self._background_reference_id.to_json()
                if self._background_reference_id is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        parameters_raw = data.get("parameters")
        if not isinstance(parameters_raw, dict):
            raise ValueError("parameters must be an object")
        cal_raw = data.get("calibration_profile_id")
        bg_raw = data.get("background_reference_id")
        if cal_raw is not None and not isinstance(cal_raw, str):
            raise ValueError("calibration_profile_id must be a string or null")
        if bg_raw is not None and not isinstance(bg_raw, str):
            raise ValueError("background_reference_id must be a string or null")
        return cls(
            stage_name=_require_json_str(data.get("stage_name"), "stage_name"),
            stage_version=_require_json_str(
                data.get("stage_version"), "stage_version"
            ),
            parameters=cast(
                Mapping[str, JsonValue],
                dict(cast(dict[str, object], parameters_raw)),
            ),
            input_domain=DataDomain.from_value(
                _require_json_str(data.get("input_domain"), "input_domain")
            ),
            output_domain=DataDomain.from_value(
                _require_json_str(data.get("output_domain"), "output_domain")
            ),
            executed_utc=from_utc_iso(
                _require_json_str(data.get("executed_utc"), "executed_utc")
            ),
            software_version=_require_json_str(
                data.get("software_version"), "software_version"
            ),
            calibration_profile_id=(
                CalibrationProfileId.from_json(cal_raw)
                if cal_raw is not None
                else None
            ),
            background_reference_id=(
                BackgroundReferenceId.from_json(bg_raw)
                if bg_raw is not None
                else None
            ),
        )


def _require_json_str(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


@dataclass(frozen=True, slots=True, init=False)
class ProcessingHistory:
    """Immutable ordered processing history (see module docstring).

    Every append returns a new object; this instance is never modified.
    """

    _records: tuple[ProcessingRecord, ...]

    def __init__(self, records: Sequence[ProcessingRecord] = ()) -> None:
        result = tuple(records)
        for record in result:
            if not isinstance(record, ProcessingRecord):
                raise TypeError(
                    "history entries must be ProcessingRecord, "
                    f"got {type(record).__name__}"
                )
        for left, right in pairwise(result):
            if left.output_domain != right.input_domain:
                raise DomainError(
                    ErrorCode.PROCESSING_DOMAIN_MISMATCH,
                    "processing history domain chain is broken",
                    {
                        "previous_stage": left.stage_name,
                        "previous_output_domain": left.output_domain.value,
                        "incoming_stage": right.stage_name,
                        "incoming_input_domain": right.input_domain.value,
                    },
                )
        seen: set[tuple[str, str]] = set()
        for record in result:
            key = (record.stage_name, record.stage_version)
            if key in seen:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "a stage application may appear only once per history; "
                    "re-application requires a new stage version",
                    {
                        "stage_name": record.stage_name,
                        "stage_version": record.stage_version,
                    },
                )
            seen.add(key)
        object.__setattr__(self, "_records", result)

    @property
    def records(self) -> tuple[ProcessingRecord, ...]:
        """The ordered records (the tuple itself is immutable)."""
        return self._records

    def append(self, record: ProcessingRecord) -> ProcessingHistory:
        """Return a new history with ``record`` appended (self unchanged)."""
        if not isinstance(record, ProcessingRecord):
            raise TypeError(
                f"record must be a ProcessingRecord, got {type(record).__name__}"
            )
        return ProcessingHistory((*self._records, record))

    def __len__(self) -> int:
        return len(self._records)

    def __iter__(self) -> Iterator[ProcessingRecord]:
        return iter(self._records)

    def __getitem__(self, index: int) -> ProcessingRecord:
        return self._records[index]

    def to_dict(self) -> dict[str, JsonValue]:
        return {"records": [record.to_dict() for record in self._records]}

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        raw_records = data.get("records")
        if not isinstance(raw_records, list):
            raise ValueError("records must be a list")
        return cls(tuple(ProcessingRecord.from_dict(item) for item in raw_records))


def _validate_history_kind(
    history: ProcessingHistory, kind: TimeDomainKind
) -> None:
    """Fail-closed provenance rule linking the scan kind to its history."""
    expected_domain = DataDomain(kind.value)
    if not history.records:
        if kind is TimeDomainKind.TIME_PROCESSED:
            raise DomainError(
                ErrorCode.PROCESSING_DOMAIN_MISMATCH,
                "time_processed data requires a non-empty processing history",
                {"kind": kind.value},
            )
        return
    last = history.records[-1]
    if last.output_domain != expected_domain:
        raise DomainError(
            ErrorCode.PROCESSING_DOMAIN_MISMATCH,
            "time-domain scan history must end in the domain matching its kind",
            {
                "kind": kind.value,
                "expected_domain": expected_domain.value,
                "history_last_output_domain": last.output_domain.value,
            },
        )


@dataclass(frozen=True, slots=True)
class TimeDomainScan:
    """Immutable continuous time-domain data: ``trace x channel x time``.

    ``time_axis_s`` is a strictly increasing, finite one-dimensional axis in
    seconds (negative start values are allowed: the axis must only be ordered).
    ``kind`` distinguishes ``time_base`` from ``time_processed``; a
    ``time_processed`` scan requires a processing history ending in
    ``time_processed`` (see :func:`_validate_history_kind`).  ``metadata``
    follows the same per-trace rules as ``FrequencyScan``.
    """

    channels: tuple[ChannelSpec, ...]
    time_axis_s: np.ndarray
    data: np.ndarray
    kind: TimeDomainKind
    history: ProcessingHistory = field(default_factory=ProcessingHistory)
    metadata: tuple[TraceMetadata | None, ...] = ()

    def __post_init__(self) -> None:
        channels = _validate_channels(self.channels)
        time_axis = _validate_time_axis(self.time_axis_s)
        data = _require_complex_numeric(self.data, "time-domain data")
        if data.ndim != 3 or data.shape[0] == 0:
            raise DomainError(
                ErrorCode.SHAPE_MISMATCH,
                "time-domain data shape must be trace x channel x time",
                {"ndim": data.ndim, "got": list(data.shape)},
            )
        expected_trailing = (len(channels), int(time_axis.size))
        if data.shape[1:] != expected_trailing:
            raise DomainError(
                ErrorCode.SHAPE_MISMATCH,
                "time-domain trailing shape must be channel x time",
                {"expected": list(expected_trailing), "got": list(data.shape)},
            )
        if not isinstance(self.kind, TimeDomainKind):
            raise TypeError(
                f"kind must be a TimeDomainKind, got {type(self.kind).__name__}"
            )
        if not isinstance(self.history, ProcessingHistory):
            raise TypeError(
                f"history must be a ProcessingHistory, "
                f"got {type(self.history).__name__}"
            )
        _validate_history_kind(self.history, self.kind)
        metadata = _validate_scan_metadata(self.metadata, int(data.shape[0]))
        object.__setattr__(self, "channels", channels)
        object.__setattr__(self, "time_axis_s", _immutable_array(time_axis, _TIME_DTYPE))
        object.__setattr__(self, "data", _immutable_array(data, _DATA_DTYPE))
        object.__setattr__(self, "metadata", _compact_scan_metadata(metadata))

    def with_history(self, history: ProcessingHistory) -> TimeDomainScan:
        """Return a new scan with ``history`` attached (self unchanged).

        ``history`` is validated against ``kind`` fail-closed
        (:func:`_validate_history_kind`); attaching an equal history is an
        idempotent no-op returning ``self``.
        """
        if not isinstance(history, ProcessingHistory):
            raise TypeError(
                f"history must be a ProcessingHistory, "
                f"got {type(history).__name__}"
            )
        if history == self.history:
            return self
        return replace(self, history=history)
