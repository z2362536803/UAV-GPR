"""ISSUE-008: frozen physical schema and codec for ``.rcscan`` v2.

This module pins the physical HDF5 contract that ``docs/DATA_FORMAT.md``
leaves to the implementation:

- exact root attributes: ``format_name``/``schema_version``/``profile``/
  ``file_id``/``file_role``/``writer_version``/``lifecycle_state``;
- exact group/dataset names, dtypes, initial shapes, maxshapes and
  trace-major chunking/compression defaults;
- fixed encodings: little-endian float64/complex128/int64, variable-length
  UTF-8 for JSON/reason columns, fixed-width ASCII for UUID/hash columns,
  epoch-nanosecond int64 timestamps;
- explicit missing-value semantics: int64 ``INT64_MIN`` sentinel, float NaN
  paired with a boolean presence column, empty string for optional text;
- fail-closed probing: unsupported major version/profile, wrong or missing
  ``format_name``, non-HDF5 payload, missing/forbidden role-specific groups.

Only the schema constants, codec helpers and the one-shot file creator live
here.  The incremental writer, reader, recovery and migration are separate
later Issues (ISSUE-010/011/012/013).
"""

from __future__ import annotations

import enum
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import h5py  # type: ignore[import-untyped]
import numpy as np

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
from uav_gpr.core.errors import DomainError, ErrorCode, JsonValue
from uav_gpr.core.gnss import GnssFix, GnssMatch
from uav_gpr.core.identifiers import (
    AirFileId,
    BackgroundReferenceId,
    CalibrationProfileId,
    DeviceId,
    GroundFileId,
    MissionId,
    TraceUid,
)
from uav_gpr.core.metadata import TraceMetadata
from uav_gpr.core.timeutil import MonotonicNs, ensure_utc, to_utc_iso

# ---------------------------------------------------------------------------
# Frozen public constants
# ---------------------------------------------------------------------------

FORMAT_NAME = "rcscan"
SCHEMA_VERSION_MAJOR = 2
SUPPORTED_SCHEMA_VERSIONS = frozenset({SCHEMA_VERSION_MAJOR})
PROFILE = "uav_gpr"
SUPPORTED_PROFILES = frozenset({PROFILE})
LIFECYCLE_STATES = ("writing", "finalized", "recovered")

# The single reserved missing-value sentinel for columnar int64 storage.
MISSING_INT64 = -(2**63)

# Field-level presence bitmask constants (versioned, append-only).
TIMING_PRESENT_ACTUAL_INTERVAL = 1 << 0
TIMING_PRESENT_SCHEDULE_ERROR = 1 << 1
GNSS_PRESENT_ALTITUDE = 1 << 0
GNSS_PRESENT_GEOID = 1 << 1
GNSS_PRESENT_HDOP = 1 << 2
GNSS_PRESENT_SATELLITES = 1 << 3
GNSS_PRESENT_GROUND_SPEED = 1 << 4
GNSS_PRESENT_COURSE = 1 << 5
GNSS_PRESENT_MATCH_AGE = 1 << 6

# Frozen field-level presence maps (versioned, append-only).
TIMING_PRESENT_FIELDS = ("actual_interval_s", "schedule_error_s")
TIMING_PRESENT_BITS = {
    "actual_interval_s": TIMING_PRESENT_ACTUAL_INTERVAL,
    "schedule_error_s": TIMING_PRESENT_SCHEDULE_ERROR,
}
GNSS_PRESENT_FIELDS = (
    "altitude_msl_m",
    "geoid_separation_m",
    "hdop",
    "satellites",
    "ground_speed_mps",
    "course_deg",
    "match_age_s",
)
GNSS_PRESENT_BITS = {
    "altitude_msl_m": GNSS_PRESENT_ALTITUDE,
    "geoid_separation_m": GNSS_PRESENT_GEOID,
    "hdop": GNSS_PRESENT_HDOP,
    "satellites": GNSS_PRESENT_SATELLITES,
    "ground_speed_mps": GNSS_PRESENT_GROUND_SPEED,
    "course_deg": GNSS_PRESENT_COURSE,
    "match_age_s": GNSS_PRESENT_MATCH_AGE,
}

# Completion kind values accepted on creation.  The middle states are the
# task terminal states from the domain enum plus the explicit recovery state.
_COMPLETION_KINDS = frozenset(
    {"", "recovered", *(state.value for state in MissionTerminalState)}
)

# Version-like token: starts alphanumeric, then alphanumeric/._-.
_VERSION_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# Canonical lowercase SHA-256 digest.
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


class ValueKind(enum.StrEnum):
    """How a dataset string column is physically encoded."""

    VLEN_UTF8 = "vlen_utf8"
    ASCII_FIXED = "ascii_fixed"
    NUMERIC = "numeric"


@dataclass(frozen=True, slots=True)
class DatasetContract:
    """One physical dataset contract: path, shape, maxshape, dtype, kind.

    ``chunks`` is the frozen HDF5 chunk shape (``None`` for fixed-size
    datasets) and ``compression`` is the frozen compression algorithm
    (``None`` means no compression, reserved pending benchmark).
    ``optional`` marks datasets that are declared by the schema but not
    created by the initial skeleton creator.
    """

    path: str
    initial_shape: tuple[int | None, ...]
    maxshape: tuple[int | None, ...]
    dtype: np.dtype[Any]
    kind: ValueKind
    chunks: tuple[int, ...] | None = None
    compression: str | None = None
    optional: bool = False


@dataclass(frozen=True, slots=True)
class RcscanProbe:
    """Read-only probe of an ``.rcscan`` v2 file (schema metadata only)."""

    format_name: str
    schema_version: int
    profile: str
    file_id: str
    file_role: EndpointRole
    writer_version: str
    lifecycle_state: str
    channel_ids: tuple[str, ...]
    optional_axes_present: dict[str, bool]


def _vlen(
    path: str,
    shape: tuple[int | None, ...] = (0,),
    maxshape: tuple[int | None, ...] = (None,),
    *,
    chunks: tuple[int, ...] | None = None,
    compression: str | None = None,
    optional: bool = False,
) -> DatasetContract:
    return DatasetContract(
        path,
        shape,
        maxshape,
        h5py.string_dtype(encoding="utf-8"),
        ValueKind.VLEN_UTF8,
        chunks,
        compression,
        optional,
    )


def _ascii(
    path: str,
    length: int,
    shape: tuple[int | None, ...] = (0,),
    maxshape: tuple[int | None, ...] = (None,),
    *,
    chunks: tuple[int, ...] | None = None,
    compression: str | None = None,
    optional: bool = False,
) -> DatasetContract:
    return DatasetContract(
        path,
        shape,
        maxshape,
        h5py.string_dtype(encoding="ascii", length=length),
        ValueKind.ASCII_FIXED,
        chunks,
        compression,
        optional,
    )


def _num(
    path: str,
    dtype: str,
    shape: tuple[int | None, ...] = (0,),
    maxshape: tuple[int | None, ...] = (None,),
    *,
    chunks: tuple[int, ...] | None = None,
    compression: str | None = None,
    optional: bool = False,
) -> DatasetContract:
    return DatasetContract(
        path,
        shape,
        maxshape,
        np.dtype(dtype),
        ValueKind.NUMERIC,
        chunks,
        compression,
        optional,
    )


def _trace_num(path: str, dtype: str) -> DatasetContract:
    """A trace-major numeric column: extendable, one row per chunk."""
    return _num(path, dtype, (0,), (None,), chunks=(1,))


def _trace_vlen(path: str) -> DatasetContract:
    """A trace-major variable-length UTF-8 column."""
    return _vlen(path, (0,), (None,), chunks=(1,))


def _trace_ascii(path: str, length: int) -> DatasetContract:
    """A trace-major fixed-width ASCII column."""
    return _ascii(path, length, (0,), (None,), chunks=(1,))


def dataset_contracts(
    channel_count: int,
    frequency_points: int,
    time_points: int | None = None,
) -> tuple[DatasetContract, ...]:
    """Return the frozen physical contract of a freshly created v2 file.

    Optional domains (``time_base``, ``time_processed``, calibrated
    frequency and processed axes) are declared here with ``optional=True``
    but are **not** created by the initial skeleton creator; they are added by
    later processing stages.  ``time_points`` parameterizes their derived
    shapes; when omitted it defaults to ``frequency_points`` so the optional
    time-axis length and the time-domain trailing dimension always agree.
    """
    if not isinstance(channel_count, int) or channel_count < 1:
        raise ValueError("channel_count must be a positive integer")
    if not isinstance(frequency_points, int) or frequency_points < 2:
        raise ValueError("frequency_points must be at least 2")
    if time_points is None:
        time_points = frequency_points
    if not isinstance(time_points, int) or time_points < 2:
        raise ValueError("time_points must be at least 2")
    contracts: list[DatasetContract] = [
        _vlen("/mission/config_json", (), ()),
        _vlen("/channels/definitions_json", (1,), (1,)),
        _num(
            "/axes/frequencies_hz",
            "<f8",
            (frequency_points,),
            (frequency_points,),
        ),
        _num(
            "/frequency/raw",
            "<c16",
            (0, channel_count, frequency_points),
            (None, channel_count, frequency_points),
            chunks=(1, channel_count, frequency_points),
        ),
        _trace_num("/trace_metadata/trace_index", "<i8"),
        _trace_ascii("/trace_metadata/trace_uid", 36),
        _trace_num("/trace_metadata/sweep_started_utc_ns", "<i8"),
        _trace_num("/trace_metadata/sweep_midpoint_utc_ns", "<i8"),
        _trace_num("/trace_metadata/sweep_finished_utc_ns", "<i8"),
        _trace_num("/trace_metadata/sweep_started_monotonic_ns", "<i8"),
        _trace_num("/trace_metadata/sweep_midpoint_monotonic_ns", "<i8"),
        _trace_num("/trace_metadata/sweep_finished_monotonic_ns", "<i8"),
        _trace_num("/trace_metadata/target_interval_s", "<f8"),
        _trace_num("/trace_metadata/actual_interval_s", "<f8"),
        _trace_num("/trace_metadata/schedule_error_s", "<f8"),
        _trace_num("/trace_metadata/connection_generation", "<i8"),
        _trace_num("/trace_metadata/timing_present_mask", "<i8"),
        _trace_ascii("/trace_metadata/raw_trace_sha256", 64),
        _trace_vlen("/trace_metadata/quality_status"),
        _trace_vlen("/trace_metadata/quality_reasons"),
        _trace_num("/gnss/valid", "<i8"),
        _trace_vlen("/gnss/invalid_reason"),
        _trace_num("/gnss/received_utc_ns", "<i8"),
        _trace_num("/gnss/nmea_utc_ns", "<i8"),
        _trace_num("/gnss/latitude_deg", "<f8"),
        _trace_num("/gnss/longitude_deg", "<f8"),
        _trace_num("/gnss/altitude_msl_m", "<f8"),
        _trace_num("/gnss/geoid_separation_m", "<f8"),
        _trace_vlen("/gnss/fix_type"),
        _trace_num("/gnss/satellites", "<i8"),
        _trace_num("/gnss/hdop", "<f8"),
        _trace_num("/gnss/ground_speed_mps", "<f8"),
        _trace_num("/gnss/course_deg", "<f8"),
        _trace_num("/gnss/optional_present_mask", "<i8"),
        _trace_num("/gnss/match_age_s", "<f8"),
        _trace_vlen("/gnss/raw_nmea"),
        _trace_num("/gnss/received_monotonic_ns", "<i8"),
        _trace_num("/gnss/match_usable", "<i8"),
        _trace_vlen("/gnss/match_method"),
        _trace_vlen("/gnss/match_reason"),
        _trace_vlen("/acquisition/device_status_json"),
        _trace_vlen("/acquisition/quality_flags"),
        _trace_num("/transport/sent_utc_ns", "<i8"),
        _trace_num("/transport/ack_utc_ns", "<i8"),
        _trace_num("/transport/retry_count", "<i8"),
        _trace_vlen("/transport/receive_status"),
        _num("/checkpoints/committed_record_count", "<i8", (1,), (1,)),
        _num("/checkpoints/last_trace_index", "<i8", (1,), (1,)),
        _vlen("/checkpoints/updated_utc", (1,), (1,)),
        _num(
            "/axes/time_base_s",
            "<f8",
            (time_points,),
            (time_points,),
            optional=True,
        ),
        _num(
            "/axes/time_processed_s",
            "<f8",
            (time_points,),
            (time_points,),
            optional=True,
        ),
        _num(
            "/frequency/calibrated",
            "<c16",
            (0, channel_count, frequency_points),
            (None, channel_count, frequency_points),
            chunks=(1, channel_count, frequency_points),
            optional=True,
        ),
        _num(
            "/time_base/data",
            "<c16",
            (0, channel_count, time_points),
            (None, channel_count, time_points),
            chunks=(1, channel_count, time_points),
            optional=True,
        ),
        _vlen("/time_base/history_json", (1,), (1,), optional=True),
        _num(
            "/time_processed/data",
            "<c16",
            (0, channel_count, time_points),
            (None, channel_count, time_points),
            chunks=(1, channel_count, time_points),
            optional=True,
        ),
        _vlen("/time_processed/history_json", (1,), (1,), optional=True),
    ]
    return tuple(contracts)


# ---------------------------------------------------------------------------
# Canonical UTF-8 JSON codec
# ---------------------------------------------------------------------------


def dumps_utf8_json(value: object) -> str:
    """Deterministic compact JSON (sorted keys, no spaces, no NaN/Inf)."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def _reject_duplicate_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key is not allowed: {key!r}")
        result[key] = value
    return result


def loads_utf8_json(text: str | bytes) -> object:
    """Strict JSON decode: reject NaN/Inf and duplicate object keys."""
    if isinstance(text, bytes):
        text = text.decode("utf-8")
    return json.loads(
        text,
        parse_constant=_reject_json_constant,
        object_pairs_hook=_reject_duplicate_object_pairs,
    )


# ---------------------------------------------------------------------------
# Missing-value codecs
# ---------------------------------------------------------------------------


def encode_optional_int64(payloads: Sequence[int | None]) -> np.ndarray:
    """Encode ``None`` as the int64 sentinel (``MISSING_INT64``).

    Only non-bool ints are accepted; floats and strings are rejected instead
    of being truncated.  The returned array is always little-endian int64.
    """
    result: list[int] = []
    for item in payloads:
        if item is None:
            result.append(MISSING_INT64)
        elif isinstance(item, bool) or not isinstance(item, (int, np.integer)):
            raise TypeError("optional int64 items must be int or None")
        elif not (np.iinfo(np.int64).min <= int(item) <= np.iinfo(np.int64).max):
            raise ValueError("optional int64 value is out of int64 range")
        elif int(item) == MISSING_INT64:
            raise ValueError("optional int64 value collides with the missing sentinel")
        else:
            result.append(int(item))
    return np.array(result, dtype="<i8")


def missing_int64_mask(values: np.ndarray | Sequence[int]) -> np.ndarray:
    """Boolean mask of entries equal to the int64 missing sentinel."""
    result: np.ndarray = np.asarray(values, dtype="<i8") == MISSING_INT64
    return result


def bool_column(values: Sequence[bool]) -> np.ndarray:
    """Encode a boolean presence column as int64 (0 = absent, 1 = present).

    Only real booleans are accepted — ``2``, ``"true"`` or ``1`` are rejected
    instead of being coerced, so the encoder cannot silently accept a
    different writer's representation.
    """
    result: list[int] = []
    for item in values:
        if not isinstance(item, (bool, np.bool_)):
            raise TypeError("bool column items must be bool")
        result.append(1 if bool(item) else 0)
    return np.array(result, dtype="<i8")


def decode_bool_column(values: np.ndarray | Sequence[int]) -> np.ndarray:
    """Decode a strict int64 0/1 boolean column.

    Only exact integer values (not bool/float/str) and only ``0``/``1`` are
    accepted; any type deception or out-of-range value fails closed.
    """
    result: list[bool] = []
    for item in values:
        if isinstance(item, (bool, np.bool_)) or not isinstance(
            item, (int, np.integer)
        ):
            raise TypeError("bool column items must be integers")
        value = int(item)
        if value not in (0, 1):
            raise ValueError("bool column contains a value other than 0 or 1")
        result.append(value == 1)
    return np.array(result, dtype=bool)


# ---------------------------------------------------------------------------
# Exact integer UTC-nanosecond codec (never through float)
# ---------------------------------------------------------------------------

_UTC_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def encode_utc_ns(value: datetime) -> int:
    """Exact UTC wall-clock datetime -> epoch nanoseconds (integer math)."""
    utc = ensure_utc(value)
    delta = utc - _UTC_EPOCH
    ns = (
        delta.days * 86_400_000_000_000
        + delta.seconds * 1_000_000_000
        + delta.microseconds * 1_000
    )
    if ns == MISSING_INT64:
        raise ValueError("UTC nanosecond value collides with the missing sentinel")
    if not (np.iinfo(np.int64).min < ns <= np.iinfo(np.int64).max):
        raise ValueError("UTC nanosecond value is out of int64 range")
    return ns


def decode_utc_ns(ns: object) -> datetime:
    """Exact epoch nanoseconds -> UTC wall-clock datetime (integer math)."""
    if isinstance(ns, bool) or not isinstance(ns, (int, np.integer)):
        raise TypeError("UTC nanosecond value must be an exact integer")
    value = int(ns)
    if value == MISSING_INT64:
        raise ValueError("missing sentinel is not a valid UTC timestamp")
    if value < np.iinfo(np.int64).min or value > np.iinfo(np.int64).max:
        raise ValueError("UTC nanosecond value is out of int64 range")
    seconds, remainder = divmod(value, 1_000_000_000)
    if remainder % 1_000 != 0:
        raise ValueError("UTC nanosecond value must be microsecond-aligned")
    days, sec_of_day = divmod(seconds, 86_400)
    return _UTC_EPOCH + timedelta(
        days=days, seconds=sec_of_day, microseconds=remainder // 1_000
    )


# ---------------------------------------------------------------------------
# Presence-mask codec (versioned bits, unknown bits fail closed)
# ---------------------------------------------------------------------------


def encode_presence_mask(bits: Sequence[str], known: Mapping[str, int]) -> int:
    """Encode named presence bits into an int64 mask using the frozen map."""
    mask = 0
    for bit in bits:
        if bit not in known:
            raise ValueError(f"unknown presence bit: {bit!r}")
        mask |= int(known[bit])
    return mask


def decode_presence_mask(mask: object, known: Mapping[str, int]) -> set[str]:
    """Decode an int64 presence mask, rejecting any unknown bit."""
    if isinstance(mask, bool) or not isinstance(mask, (int, np.integer)):
        raise TypeError("presence mask must be an integer")
    value = int(mask)
    all_bits = 0
    for bit in known.values():
        all_bits |= int(bit)
    if value & ~all_bits:
        raise ValueError("presence mask contains unknown bits")
    return {name for name, bit in known.items() if value & int(bit)}


def presence_mask_from_values(
    values: Mapping[str, object], known: Mapping[str, int]
) -> int:
    """Build a presence mask from the actual field values (None = absent)."""
    mask = 0
    for name, bit in known.items():
        if values.get(name) is not None:
            mask |= int(bit)
    return mask


def validate_presence_mask(
    mask: object, values: Mapping[str, object], known: Mapping[str, int]
) -> None:
    """Fail closed unless mask and payload agree exactly (None vs NaN/value)."""
    value = _cell_int(mask, "presence mask")  # strict int check
    decode_presence_mask(value, known)
    for name, bit in known.items():
        present = bool(value & int(bit))
        field_value = values.get(name)
        if present:
            if field_value is None:
                raise ValueError(f"presence bit set for missing field {name}")
            if isinstance(field_value, float) and np.isnan(field_value):
                raise ValueError(
                    f"presence bit set but payload is NaN for {name}"
                )
        else:
            if field_value is not None:
                raise ValueError(
                    f"presence bit clear but payload is present for {name}"
                )


# ---------------------------------------------------------------------------
# Lossless row codec: single projection from one domain object to all cells
# ---------------------------------------------------------------------------


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _cell_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise TypeError(f"{field} must be an integer")
    return int(value)


def _cell_float(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(
        value, (int, float, np.integer, np.floating)
    ):
        raise TypeError(f"{field} must be a number")
    return float(value)


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    result = _cell_float(value, "optional float")
    if np.isnan(result):
        return None
    return result


def _optional_int64(value: object) -> int | None:
    if value is None:
        return None
    result = _cell_int(value, "optional int64")
    if result == MISSING_INT64:
        return None
    return result


def trace_metadata_to_cells(metadata: TraceMetadata) -> dict[str, object]:
    """Project one ``TraceMetadata`` into every physical schema cell.

    This is the single authoritative row projection: the future writer writes
    exactly these cells, and the reader reconstructs the domain object from
    them.  There is no separate JSON copy, so no redundancy conflicts arise.
    """
    timing_values = {
        "actual_interval_s": metadata.actual_interval_s,
        "schedule_error_s": metadata.schedule_error_s,
    }
    timing_mask = presence_mask_from_values(timing_values, TIMING_PRESENT_BITS)

    cells: dict[str, object] = {
        "/trace_metadata/trace_index": metadata.trace_index,
        "/trace_metadata/trace_uid": str(metadata.trace_uid),
        "/trace_metadata/sweep_started_utc_ns": encode_utc_ns(
            metadata.sweep_started_utc
        ),
        "/trace_metadata/sweep_midpoint_utc_ns": encode_utc_ns(
            metadata.sweep_midpoint_utc
        ),
        "/trace_metadata/sweep_finished_utc_ns": encode_utc_ns(
            metadata.sweep_finished_utc
        ),
        "/trace_metadata/sweep_started_monotonic_ns": (
            metadata.sweep_started_monotonic_ns.ns
        ),
        "/trace_metadata/sweep_midpoint_monotonic_ns": (
            metadata.sweep_midpoint_monotonic_ns.ns
        ),
        "/trace_metadata/sweep_finished_monotonic_ns": (
            metadata.sweep_finished_monotonic_ns.ns
        ),
        "/trace_metadata/target_interval_s": metadata.target_interval_s,
        "/trace_metadata/actual_interval_s": (
            metadata.actual_interval_s
            if metadata.actual_interval_s is not None
            else np.nan
        ),
        "/trace_metadata/schedule_error_s": (
            metadata.schedule_error_s
            if metadata.schedule_error_s is not None
            else np.nan
        ),
        "/trace_metadata/connection_generation": metadata.connection_generation,
        "/trace_metadata/timing_present_mask": timing_mask,
        "/trace_metadata/raw_trace_sha256": (
            metadata.raw_trace_sha256 if metadata.raw_trace_sha256 is not None else ""
        ),
        "/trace_metadata/quality_status": metadata.quality_status.value,
        "/trace_metadata/quality_reasons": dumps_utf8_json(
            [reason.value for reason in metadata.quality_reasons]
        ),
        "/acquisition/device_status_json": "",
        "/acquisition/quality_flags": "[]",
        "/transport/sent_utc_ns": MISSING_INT64,
        "/transport/ack_utc_ns": MISSING_INT64,
        "/transport/retry_count": 0,
        "/transport/receive_status": "",
    }

    match = metadata.gnss_match
    if match is None:
        gnss: dict[str, object] = {
            "/gnss/valid": 0,
            "/gnss/invalid_reason": "",
            "/gnss/received_utc_ns": MISSING_INT64,
            "/gnss/nmea_utc_ns": MISSING_INT64,
            "/gnss/received_monotonic_ns": MISSING_INT64,
            "/gnss/latitude_deg": np.nan,
            "/gnss/longitude_deg": np.nan,
            "/gnss/altitude_msl_m": np.nan,
            "/gnss/geoid_separation_m": np.nan,
            "/gnss/fix_type": "",
            "/gnss/satellites": MISSING_INT64,
            "/gnss/hdop": np.nan,
            "/gnss/ground_speed_mps": np.nan,
            "/gnss/course_deg": np.nan,
            "/gnss/match_age_s": np.nan,
            "/gnss/optional_present_mask": 0,
            "/gnss/match_method": "",
            "/gnss/match_usable": 0,
            "/gnss/match_reason": "",
            "/gnss/raw_nmea": "",
        }
    elif match.fix is None:
        gnss = {
            "/gnss/valid": 0,
            "/gnss/invalid_reason": (
                match.reason.value if match.reason is not None else ""
            ),
            "/gnss/received_utc_ns": MISSING_INT64,
            "/gnss/nmea_utc_ns": MISSING_INT64,
            "/gnss/received_monotonic_ns": MISSING_INT64,
            "/gnss/latitude_deg": np.nan,
            "/gnss/longitude_deg": np.nan,
            "/gnss/altitude_msl_m": np.nan,
            "/gnss/geoid_separation_m": np.nan,
            "/gnss/fix_type": "",
            "/gnss/satellites": MISSING_INT64,
            "/gnss/hdop": np.nan,
            "/gnss/ground_speed_mps": np.nan,
            "/gnss/course_deg": np.nan,
            "/gnss/match_age_s": match.age_s,
            "/gnss/optional_present_mask": 0,
            "/gnss/match_method": (
                match.method.value if match.method is not None else ""
            ),
            "/gnss/match_usable": 1 if match.usable_for_map else 0,
            "/gnss/match_reason": (
                match.reason.value if match.reason is not None else ""
            ),
            "/gnss/raw_nmea": "",
        }
    else:
        fix = match.fix
        gnss_values = {
            "altitude_msl_m": fix.altitude_msl_m,
            "geoid_separation_m": fix.geoid_separation_m,
            "hdop": fix.hdop,
            "satellites": fix.satellites,
            "ground_speed_mps": fix.ground_speed_mps,
            "course_deg": fix.course_deg,
            "match_age_s": match.age_s,
        }
        gnss_mask = presence_mask_from_values(gnss_values, GNSS_PRESENT_BITS)
        gnss = {
            "/gnss/valid": 1 if fix.valid else 0,
            "/gnss/invalid_reason": (
                fix.invalid_reason.value if fix.invalid_reason is not None else ""
            ),
            "/gnss/received_utc_ns": encode_utc_ns(fix.received_utc),
            "/gnss/nmea_utc_ns": (
                encode_utc_ns(fix.nmea_utc) if fix.nmea_utc is not None else MISSING_INT64
            ),
            "/gnss/received_monotonic_ns": fix.received_monotonic_ns.ns,
            "/gnss/latitude_deg": (
                fix.latitude_deg if fix.latitude_deg is not None else np.nan
            ),
            "/gnss/longitude_deg": (
                fix.longitude_deg if fix.longitude_deg is not None else np.nan
            ),
            "/gnss/altitude_msl_m": (
                fix.altitude_msl_m if fix.altitude_msl_m is not None else np.nan
            ),
            "/gnss/geoid_separation_m": (
                fix.geoid_separation_m
                if fix.geoid_separation_m is not None
                else np.nan
            ),
            "/gnss/fix_type": fix.fix_quality.value,
            "/gnss/satellites": (
                fix.satellites if fix.satellites is not None else MISSING_INT64
            ),
            "/gnss/hdop": fix.hdop if fix.hdop is not None else np.nan,
            "/gnss/ground_speed_mps": (
                fix.ground_speed_mps if fix.ground_speed_mps is not None else np.nan
            ),
            "/gnss/course_deg": (
                fix.course_deg if fix.course_deg is not None else np.nan
            ),
            "/gnss/match_age_s": match.age_s,
            "/gnss/optional_present_mask": gnss_mask,
            "/gnss/match_method": (
                match.method.value if match.method is not None else ""
            ),
            "/gnss/match_usable": 1 if match.usable_for_map else 0,
            "/gnss/match_reason": (
                match.reason.value if match.reason is not None else ""
            ),
            "/gnss/raw_nmea": "",
        }
    cells.update(gnss)
    return cells


def trace_metadata_from_cells(
    cells: dict[str, object],
    *,
    mission_id: MissionId,
    device_id: DeviceId,
) -> TraceMetadata:
    """Reconstruct one ``TraceMetadata`` from the full physical cell row.

    Only the stored values are read; no domain object is carried over from
    the encoder side.  The missing sentinel, NaN and empty-string encodings
    are decoded by the strict codecs.
    """
    quality_status = TraceQualityStatus.from_value(
        _text(cells["/trace_metadata/quality_status"])
    )
    reasons_data = loads_utf8_json(_text(cells["/trace_metadata/quality_reasons"]))
    if not isinstance(reasons_data, list):
        raise ValueError("quality_reasons payload must be a JSON array")
    quality_reasons = tuple(
        TraceQualityReason.from_value(str(reason)) for reason in reasons_data
    )

    valid = _cell_int(cells["/gnss/valid"], "valid")
    match_usable = _cell_int(cells["/gnss/match_usable"], "match_usable")
    if valid not in (0, 1):
        raise ValueError("gnss valid column must be 0 or 1")
    if match_usable not in (0, 1):
        raise ValueError("gnss match_usable column must be 0 or 1")
    timing_mask = _cell_int(
        cells["/trace_metadata/timing_present_mask"], "timing_present_mask"
    )
    validate_presence_mask(
        timing_mask,
        {
            "actual_interval_s": _optional_float(
                cells["/trace_metadata/actual_interval_s"]
            ),
            "schedule_error_s": _optional_float(
                cells["/trace_metadata/schedule_error_s"]
            ),
        },
        TIMING_PRESENT_BITS,
    )
    gnss_mask = _cell_int(
        cells["/gnss/optional_present_mask"], "optional_present_mask"
    )
    validate_presence_mask(
        gnss_mask,
        {
            "altitude_msl_m": _optional_float(cells["/gnss/altitude_msl_m"]),
            "geoid_separation_m": _optional_float(cells["/gnss/geoid_separation_m"]),
            "hdop": _optional_float(cells["/gnss/hdop"]),
            "satellites": _optional_int64(cells["/gnss/satellites"]),
            "ground_speed_mps": _optional_float(cells["/gnss/ground_speed_mps"]),
            "course_deg": _optional_float(cells["/gnss/course_deg"]),
            "match_age_s": _optional_float(cells["/gnss/match_age_s"]),
        },
        GNSS_PRESENT_BITS,
    )
    invalid_reason_raw = _text(cells["/gnss/invalid_reason"])
    match_reason_raw = _text(cells["/gnss/match_reason"])
    match_method_raw = _text(cells["/gnss/match_method"])
    fix_type_raw = _text(cells["/gnss/fix_type"])
    match_usable = _cell_int(cells["/gnss/match_usable"], "match_usable")
    midpoint_utc = decode_utc_ns(cells["/trace_metadata/sweep_midpoint_utc_ns"])
    match_age = _optional_float(cells["/gnss/match_age_s"])
    reason = (
        GnssUnavailableReason.from_value(match_reason_raw)
        if match_reason_raw
        else None
    )

    if valid == 0 and match_usable == 0 and not match_reason_raw and not invalid_reason_raw:
        gnss_match: GnssMatch | None = None
    elif not fix_type_raw:
        method = GnssMatchMethod.from_value(match_method_raw)
        gnss_match = GnssMatch(
            fix=None,
            trace_midpoint_utc=midpoint_utc,
            age_s=match_age,
            method=method,
            usable_for_map=match_usable == 1,
            reason=reason,
        )
    else:
        method = GnssMatchMethod.from_value(match_method_raw)
        nmea_ns = _cell_int(cells["/gnss/nmea_utc_ns"], "nmea_utc_ns")
        fix = GnssFix(
            received_utc=decode_utc_ns(cells["/gnss/received_utc_ns"]),
            nmea_utc=decode_utc_ns(nmea_ns) if nmea_ns != MISSING_INT64 else None,
            received_monotonic_ns=MonotonicNs(
                _cell_int(cells["/gnss/received_monotonic_ns"], "received_monotonic_ns")
            ),
            latitude_deg=_optional_float(cells["/gnss/latitude_deg"]),
            longitude_deg=_optional_float(cells["/gnss/longitude_deg"]),
            altitude_msl_m=_optional_float(cells["/gnss/altitude_msl_m"]),
            geoid_separation_m=_optional_float(cells["/gnss/geoid_separation_m"]),
            fix_quality=GnssFixQuality.from_value(fix_type_raw),
            satellites=_optional_int64(cells["/gnss/satellites"]),
            hdop=_optional_float(cells["/gnss/hdop"]),
            ground_speed_mps=_optional_float(cells["/gnss/ground_speed_mps"]),
            course_deg=_optional_float(cells["/gnss/course_deg"]),
            valid=valid == 1,
            invalid_reason=(
                GnssUnavailableReason.from_value(invalid_reason_raw)
                if invalid_reason_raw
                else None
            ),
        )
        gnss_match = GnssMatch(
            fix=fix,
            trace_midpoint_utc=midpoint_utc,
            age_s=_cell_float(cells["/gnss/match_age_s"], "match_age_s"),
            method=method,
            usable_for_map=match_usable == 1,
            reason=reason,
        )

    return TraceMetadata(
        mission_id=mission_id,
        trace_index=_cell_int(cells["/trace_metadata/trace_index"], "trace_index"),
        trace_uid=TraceUid.from_json(_text(cells["/trace_metadata/trace_uid"])),
        device_id=device_id,
        sweep_started_utc=decode_utc_ns(
            cells["/trace_metadata/sweep_started_utc_ns"]
        ),
        sweep_midpoint_utc=midpoint_utc,
        sweep_finished_utc=decode_utc_ns(
            cells["/trace_metadata/sweep_finished_utc_ns"]
        ),
        sweep_started_monotonic_ns=MonotonicNs(
            _cell_int(
                cells["/trace_metadata/sweep_started_monotonic_ns"],
                "sweep_started_monotonic_ns",
            )
        ),
        sweep_midpoint_monotonic_ns=MonotonicNs(
            _cell_int(
                cells["/trace_metadata/sweep_midpoint_monotonic_ns"],
                "sweep_midpoint_monotonic_ns",
            )
        ),
        sweep_finished_monotonic_ns=MonotonicNs(
            _cell_int(
                cells["/trace_metadata/sweep_finished_monotonic_ns"],
                "sweep_finished_monotonic_ns",
            )
        ),
        target_interval_s=_cell_float(
            cells["/trace_metadata/target_interval_s"], "target_interval_s"
        ),
        actual_interval_s=_optional_float(cells["/trace_metadata/actual_interval_s"]),
        schedule_error_s=_optional_float(cells["/trace_metadata/schedule_error_s"]),
        connection_generation=_cell_int(
            cells["/trace_metadata/connection_generation"], "connection_generation"
        ),
        raw_trace_sha256=_text(cells["/trace_metadata/raw_trace_sha256"]) or None,
        gnss_match=gnss_match,
        quality_status=quality_status,
        quality_reasons=quality_reasons,
    )


# ---------------------------------------------------------------------------
# One-shot creator
# ---------------------------------------------------------------------------


def _channel_dict(channel: ChannelSpec) -> dict[str, object]:
    return {
        "channel_id": channel.channel_id,
        "logical_polarization": channel.logical_polarization.value,
        "s_parameter": channel.s_parameter.value,
        "display_name": channel.display_name,
        "antenna_note": channel.antenna_note,
    }


def _require_frequency_axis(frequencies_hz: object) -> np.ndarray:
    axis = np.asarray(frequencies_hz, dtype=np.float64)
    if axis.ndim != 1:
        raise ValueError("frequency axis must be one-dimensional")
    if axis.size < 2:
        raise ValueError("frequency axis requires at least 2 points")
    if not np.all(np.isfinite(axis)):
        raise ValueError("frequency axis must be finite")
    if np.any(np.diff(axis) <= 0):
        raise ValueError("frequency axis must be strictly increasing")
    return axis


def _as_float(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    return float(value)


def _as_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _as_str(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def _as_bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _optional_config_id(value: object, expected: type[Any]) -> Any | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("optional config id must be a string or null")
    return expected.from_json(value)


def _build_mission_config(data: dict[str, object], created_utc: datetime) -> MissionConfig:
    """Rebuild a MissionConfig from its canonical contract dict (fail closed)."""
    raw_channels = data.get("channels")
    if not isinstance(raw_channels, list):
        raise ValueError("config_json channels must be an array")
    channels: list[ChannelSpec] = []
    for item in raw_channels:
        if not isinstance(item, dict):
            raise ValueError("config_json channel must be an object")
        channels.append(
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
                    _as_str(item["antenna_note"], "antenna_note")
                    if item.get("antenna_note") is not None
                    else None
                ),
            )
        )
    display_duration = data.get("display_duration_s")
    return MissionConfig(
        frequency_start_hz=_as_float(data["frequency_start_hz"], "frequency_start_hz"),
        frequency_stop_hz=_as_float(data["frequency_stop_hz"], "frequency_stop_hz"),
        frequency_points=_as_int(data["frequency_points"], "frequency_points"),
        if_bw_hz=_as_float(data["if_bw_hz"], "if_bw_hz"),
        power_dbm=_as_float(data["power_dbm"], "power_dbm"),
        channels=tuple(channels),
        acquisition_mode=AcquisitionMode.from_value(
            _as_str(data["acquisition_mode"], "acquisition_mode")
        ),
        planned_trace_count=(
            _as_int(data["planned_trace_count"], "planned_trace_count")
            if data.get("planned_trace_count") is not None
            else None
        ),
        target_interval_s=_as_float(data["target_interval_s"], "target_interval_s"),
        gnss_max_age_s=_as_float(data["gnss_max_age_s"], "gnss_max_age_s"),
        gnss_no_fix_policy=GnssNoFixPolicy.from_value(
            _as_str(data["gnss_no_fix_policy"], "gnss_no_fix_policy")
        ),
        calibration_profile_id=cast(
            "CalibrationProfileId | None",
            _optional_config_id(
                data.get("calibration_profile_id"), CalibrationProfileId
            ),
        ),
        apply_calibration=_as_bool(data["apply_calibration"], "apply_calibration"),
        background_reference_id=cast(
            "BackgroundReferenceId | None",
            _optional_config_id(
                data.get("background_reference_id"), BackgroundReferenceId
            ),
        ),
        apply_background=_as_bool(data["apply_background"], "apply_background"),
        created_utc=created_utc,
        software_version=_as_str(data["software_version"], "software_version"),
        protocol_version=_as_str(data["protocol_version"], "protocol_version"),
        display_start_s=_as_float(data.get("display_start_s", 0.0), "display_start_s"),
        display_duration_s=(
            _as_float(display_duration, "display_duration_s")
            if display_duration is not None
            else None
        ),
        config_schema_version=_as_str(
            data["config_schema_version"], "config_schema_version"
        ),
    )


def create_rcscan_v2(
    path: str | Path,
    *,
    mission_id: object,
    device_id: object,
    file_id: object,
    created_utc: datetime,
    completed_utc: datetime | None,
    completion_kind: str | None,
    file_role: EndpointRole | str = EndpointRole.AIR,
    channels: Sequence[ChannelSpec],
    frequencies_hz: object,
    config_json: str,
    config_sha256: str,
    writer_version: str,
) -> Path:
    """Create a fresh ``.partial.rcscan`` v2 skeleton (one-shot, no writer).

    All validation happens before any file is touched; the target must not
    already exist.  The created file is in ``writing`` lifecycle state with
    empty mission end fields and zero-length trace-major datasets.
    """
    target = Path(path)
    if target.exists():
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "target already exists",
            {"path": str(target)},
        )
    channel_tuple = tuple(channels)
    if not channel_tuple:
        raise ValueError("channels must contain at least one channel")
    for channel in channel_tuple:
        if not isinstance(channel, ChannelSpec):
            raise TypeError(
                f"channels must contain ChannelSpec, got {type(channel).__name__}"
            )
    role = (
        file_role
        if isinstance(file_role, EndpointRole)
        else EndpointRole.from_value(str(file_role))
    )
    axis = _require_frequency_axis(frequencies_hz)
    for value, name, expected in (
        (mission_id, "mission_id", MissionId),
        (device_id, "device_id", DeviceId),
    ):
        if not isinstance(value, expected):
            raise TypeError(
                f"{name} must be a {expected.__name__}, "
                f"got {type(value).__name__}"
            )
    expected_file_id = AirFileId if role is EndpointRole.AIR else GroundFileId
    if not isinstance(file_id, expected_file_id):
        raise TypeError(
            f"file_id must be a {expected_file_id.__name__} for role {role.value}, "
            f"got {type(file_id).__name__}"
        )
    if not isinstance(config_json, str) or not config_json:
        raise ValueError("config_json must be a non-empty string")
    try:
        parsed_config = loads_utf8_json(config_json)
    except ValueError as error:
        raise ValueError(f"config_json must be valid canonical JSON: {error}") from None
    if not isinstance(parsed_config, dict):
        raise ValueError("config_json must encode a JSON object")
    if not isinstance(config_sha256, str) or _HEX64_RE.fullmatch(config_sha256) is None:
        raise ValueError("config_sha256 must be a 64-character lowercase hex digest")
    created = ensure_utc(created_utc)
    channel_count = len(channel_tuple)
    frequency_points = int(axis.size)
    try:
        reconstructed = _build_mission_config(parsed_config, created)
    except (DomainError, KeyError, TypeError, ValueError) as error:
        raise ValueError(
            f"config_json is not a valid canonical MissionConfig: {error}"
        ) from None
    if reconstructed.to_canonical_json() != config_json:
        raise ValueError("config_json is not canonical MissionConfig JSON")
    if reconstructed.config_sha256 != config_sha256:
        raise ValueError("config_sha256 does not match config_json")
    if reconstructed.channels != channel_tuple:
        raise ValueError("config_json channels do not match creator channels")
    if not np.array_equal(axis, reconstructed.frequency_axis_hz):
        raise ValueError("config_json frequency axis does not match creator axis")
    if not isinstance(writer_version, str) or _VERSION_TOKEN_RE.fullmatch(writer_version) is None:
        raise ValueError(
            "writer_version must be a version-like token "
            "(alphanumeric first, then letters/digits/dot/underscore/hyphen)"
        )
    if completion_kind is None:
        completion_kind = ""
    elif not isinstance(completion_kind, str) or completion_kind not in _COMPLETION_KINDS:
        raise ValueError(f"unknown completion_kind: {completion_kind!r}")
    if completion_kind != "":
        raise ValueError("a writing skeleton requires completion_kind to be empty")
    if completed_utc is not None:
        raise ValueError("completed_utc must be None while the file is writing")
    contracts = dataset_contracts(channel_count, frequency_points)
    special = {
        "/mission/config_json",
        "/channels/definitions_json",
        "/axes/frequencies_hz",
        "/frequency/raw",
        "/checkpoints/committed_record_count",
        "/checkpoints/last_trace_index",
        "/checkpoints/updated_utc",
    }

    try:
        h5 = h5py.File(target, "x")
    except OSError as error:
        if target.exists():
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "target already exists",
                {"path": str(target)},
            ) from error
        raise
    with h5:
        h5.attrs["format_name"] = FORMAT_NAME
        h5.attrs["schema_version"] = int(SCHEMA_VERSION_MAJOR)
        h5.attrs["profile"] = PROFILE
        h5.attrs["file_id"] = str(file_id)
        h5.attrs["file_role"] = role.value
        h5.attrs["writer_version"] = writer_version
        h5.attrs["lifecycle_state"] = "writing"

        mission = h5.create_group("mission")
        mission.attrs["mission_id"] = str(mission_id)
        mission.attrs["device_id"] = str(device_id)
        mission.attrs["created_utc"] = to_utc_iso(created)
        mission.attrs["started_utc"] = ""
        mission.attrs["ended_utc"] = ""
        mission.attrs["completion_kind"] = completion_kind
        mission.attrs["config_sha256"] = config_sha256
        h5.create_dataset(
            "/mission/config_json",
            data=config_json,
            dtype=h5py.string_dtype(encoding="utf-8"),
        )
        h5.create_dataset(
            "/channels/definitions_json",
            data=np.array(
                [dumps_utf8_json([_channel_dict(channel) for channel in channel_tuple])],
                dtype=h5py.string_dtype(encoding="utf-8"),
            ),
        )
        h5.create_dataset(
            "/axes/frequencies_hz",
            data=axis.astype("<f8", copy=False),
            dtype="<f8",
        )
        h5.create_dataset(
            "/frequency/raw",
            shape=(0, channel_count, frequency_points),
            maxshape=(None, channel_count, frequency_points),
            dtype="<c16",
            chunks=(1, channel_count, frequency_points),
        )
        h5.create_dataset(
            "/checkpoints/committed_record_count",
            data=np.array([0], dtype="<i8"),
        )
        h5.create_dataset(
            "/checkpoints/last_trace_index",
            data=np.array([MISSING_INT64], dtype="<i8"),
        )
        h5.create_dataset(
            "/checkpoints/updated_utc",
            data=np.array(
                [to_utc_iso(created)],
                dtype=h5py.string_dtype(encoding="utf-8"),
            ),
        )

        for contract in contracts:
            if contract.path in special:
                continue
            if contract.optional:
                continue
            if contract.path.startswith("/transport") and role is EndpointRole.GROUND:
                continue
            h5.create_dataset(
                contract.path,
                shape=contract.initial_shape,
                maxshape=contract.maxshape,
                dtype=contract.dtype,
                chunks=contract.chunks,
                compression=contract.compression,
            )
    return target


def _validate_dataset_against_contract(
    dataset: h5py.Dataset, contract: DatasetContract
) -> None:
    """Validate a dataset against its frozen contract (runtime-safe check).

    Extendable trace-major axes keep their current length (any non-negative
    value) instead of being compared to the initial shape; fixed axes retain
    their exact lengths.  dtype, maxshape, chunks and compression are exact.
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
    else:
        if dataset.chunks is None or tuple(dataset.chunks) != contract.chunks:
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
        zip(
            dataset.shape,
            contract.initial_shape,
            contract.maxshape,
            strict=True,
        )
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


# ---------------------------------------------------------------------------
# Fail-closed probing
# ---------------------------------------------------------------------------


def probe_rcscan_v2(path: str | Path) -> RcscanProbe:
    """Read and validate the rcscan v2 schema metadata; fail closed.

    The probe never mutates the file.  It validates the frozen identity and
    role-specific group requirements before exposing any metadata.
    """
    target = Path(path)
    try:
        with h5py.File(target, "r") as h5:
            attrs = dict(h5.attrs)
            format_name = attrs.get("format_name")
            if format_name != FORMAT_NAME:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "not an rcscan v2 file",
                    {"field": "format_name"},
                )
            raw_version = attrs.get("schema_version")
            if isinstance(raw_version, bool) or not isinstance(
                raw_version, (int, np.integer)
            ):
                raise DomainError(
                    ErrorCode.UNSUPPORTED_SCHEMA_VERSION,
                    "unsupported rcscan schema version",
                    {
                        "detected_version": (
                            raw_version if raw_version is not None else "missing"
                        ),
                        "known_major": False,
                    },
                )
            schema_version = int(raw_version)
            if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
                raise DomainError(
                    ErrorCode.UNSUPPORTED_SCHEMA_VERSION,
                    "unsupported rcscan schema version",
                    {
                        "detected_version": schema_version,
                        "known_major": schema_version in SUPPORTED_SCHEMA_VERSIONS,
                    },
                )
            profile = attrs.get("profile")
            if profile not in SUPPORTED_PROFILES:
                raise DomainError(
                    ErrorCode.UNSUPPORTED_SCHEMA_VERSION,
                    "unsupported rcscan profile",
                    {"field": "profile"},
                )
            try:
                file_role = EndpointRole.from_value(str(attrs.get("file_role")))
            except ValueError as error:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "rcscan file_role attribute is invalid",
                    {"field": "file_role"},
                ) from error
            lifecycle_state = str(attrs.get("lifecycle_state"))
            if lifecycle_state not in LIFECYCLE_STATES:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "rcscan lifecycle_state attribute is invalid",
                    {"field": "lifecycle_state"},
                )
            if file_role is EndpointRole.AIR and "/transport" not in h5:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "air rcscan file requires the transport group",
                    {"missing": ["/transport"]},
                )
            # Option A (frozen): ground-side /transport is role-specific
            # optional.  Ground files may carry the group or omit it; the
            # per-column semantics are deferred to ISSUE-041/043.
            file_id_raw = attrs.get("file_id")
            if not isinstance(file_id_raw, str):
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "rcscan file_id attribute is invalid",
                    {"field": "file_id"},
                )
            try:
                if file_role is EndpointRole.AIR:
                    AirFileId.from_json(file_id_raw)
                else:
                    GroundFileId.from_json(file_id_raw)
            except ValueError as error:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "rcscan file_id is not a canonical UUID",
                    {"field": "file_id"},
                ) from error
            writer_version_raw = attrs.get("writer_version")
            if (
                not isinstance(writer_version_raw, str)
                or _VERSION_TOKEN_RE.fullmatch(writer_version_raw) is None
            ):
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "rcscan writer_version attribute is invalid",
                    {"field": "writer_version"},
                )
            if lifecycle_state == "writing":
                mission = h5.get("mission")
                if mission is not None:
                    completion_kind = str(mission.attrs.get("completion_kind", ""))
                    if completion_kind != "":
                        raise DomainError(
                            ErrorCode.INVALID_ARGUMENT,
                            "writing rcscan file must not carry completion_kind",
                            {
                                "field": "lifecycle_state",
                                "completion_kind": completion_kind,
                            },
                        )
            definitions = h5["/channels/definitions_json"]
            definition_text = definitions[0]
            definition_text = (
                definition_text.decode("utf-8")
                if isinstance(definition_text, bytes)
                else str(definition_text)
            )
            parsed_definitions = loads_utf8_json(definition_text)
            if not isinstance(parsed_definitions, list):
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "channel definitions_json entry must be an array",
                    {"field": "definitions_json"},
                )
            channel_ids: list[str] = []
            for item in parsed_definitions:
                if not isinstance(item, dict):
                    raise DomainError(
                        ErrorCode.INVALID_ARGUMENT,
                        "channel definitions_json entry must be an object",
                        {"field": "definitions_json"},
                    )
                channel_ids.append(str(item["channel_id"]))
            if "/transport" in h5:
                channel_count = len(channel_ids)
                frequency_points = int(h5["/axes/frequencies_hz"].shape[0])
                expected_transport = [
                    contract
                    for contract in dataset_contracts(channel_count, frequency_points)
                    if contract.path.startswith("/transport")
                ]
                missing_transport = [
                    contract.path
                    for contract in expected_transport
                    if contract.path not in h5
                ]
                if missing_transport:
                    raise DomainError(
                        ErrorCode.INVALID_ARGUMENT,
                        "transport group is incomplete",
                        {"missing": cast(JsonValue, missing_transport)},
                    )
                for contract in expected_transport:
                    _validate_dataset_against_contract(
                        h5[contract.path], contract
                    )
            return RcscanProbe(
                format_name=str(attrs["format_name"]),
                schema_version=schema_version,
                profile=str(attrs["profile"]),
                file_id=str(attrs["file_id"]),
                file_role=file_role,
                writer_version=str(attrs["writer_version"]),
                lifecycle_state=str(attrs["lifecycle_state"]),
                channel_ids=tuple(channel_ids),
                optional_axes_present={
                    "/axes/time_base_s": "/axes/time_base_s" in h5,
                    "/axes/time_processed_s": "/axes/time_processed_s" in h5,
                },
            )
    except OSError as error:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "not an HDF5 rcscan file",
            {"path": str(target)},
        ) from error
