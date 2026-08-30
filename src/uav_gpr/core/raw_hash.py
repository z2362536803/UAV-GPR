"""ISSUE-009: canonical per-trace raw trace SHA-256 (versioned framing).

This module freezes the unambiguous, air/ground-consistent byte framing of
``raw_trace_sha256`` defined by ``docs/DATA_FORMAT.md`` section 5:

.. code-block:: text

    raw_trace_sha256 = sha256(
        b"UAVGPR-RAW-SHA256"            # magic
        + uint64be(RAW_HASH_VERSION)    # hash version (1)
        + uint64be(len(mission_id)) + mission_id_utf8
        + uint64be(len(trace_uid))  + trace_uid_utf8
        + uint64be(trace_index)
        + uint64be(channel_count)
        + for each channel in order:
            uint64be(len(channel_id)) + channel_id_utf8
        + uint64be(frequency_count)
        + float64le(frequency axis)     # contiguous bytes
        + complex128le(raw, C order)    # contiguous bytes
    )

Design rules (AGENTS.md section 3/4, docs/DATA_FORMAT.md section 5):

- **Unambiguous**: every variable-length text field is length-prefixed
  (``uint64be``), so naive concatenation can never be re-split differently.
- **Versioned**: ``RAW_HASH_VERSION`` is the first framing field, enabling
  future evolution without ambiguity.
- **Little-endian numerics**: the frequency axis and raw complex array are
  encoded as little-endian float64/complex128 contiguous bytes, matching the
  ISSUE-008 ``.rcscan`` v2 ``<f8``/``<c16`` column layout and the
  ``/trace_metadata/raw_trace_sha256`` 64-byte ASCII column.
- **Explicit channel order**: channel IDs are hashed in the exact
  ``channels`` sequence order; never inferred from dict/UI order.
- **GNSS excluded**: GNSS fields intentionally never enter the raw hash, so
  position-field corrections do not change the radar raw identity.
- **Inputs never mutated**: this module only reads the domain arrays
  (``astype(..., copy=False)`` is a no-copy view when the dtype matches).
- **Fail closed**: non-canonical IDs, non-numeric dtypes, wrong shapes,
  non-finite/non-increasing frequency axes and duplicate channel IDs are
  rejected with structured ``DomainError`` codes.  The frequency axis is
  validated on its canonical little-endian float64 values (the exact bytes
  hashed), so unsigned-diff underflow, signed overflow and post-conversion
  collapse cannot bypass the strictly-increasing contract.  ``RawHashSpec``
  JSON parsing enforces exact key sets and v1-only spec/hash versions.

The output is a 64-lowercase-hex SHA-256 digest string that satisfies the
``TraceMetadata.raw_trace_sha256`` field contract and fits the ISSUE-008
fixed-width ASCII column.  ``RawHashSpec`` is the self-contained, JSON
round-trippable carrier for one hash computation.
"""

from __future__ import annotations

import hashlib
import re
import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Self

import numpy as np

from uav_gpr.core.channels import ChannelSpec
from uav_gpr.core.errors import DomainError, ErrorCode, JsonValue
from uav_gpr.core.identifiers import MissionId, TraceUid

# ---------------------------------------------------------------------------
# Frozen framing constants (docs/DATA_FORMAT.md section 5)
# ---------------------------------------------------------------------------

#: Magic marker at the start of every framed raw-trace payload.
RAW_HASH_MAGIC = "UAVGPR-RAW-SHA256"
#: Framing version carried as the first ``uint64be`` field.
RAW_HASH_VERSION = 1
#: RawHashSpec JSON schema version (``to_dict``/``from_dict`` contract).
#: ``from_dict`` is v1-only: unknown spec/hash versions are rejected
#: fail-closed instead of being silently re-interpreted.
RAW_HASH_SPEC_VERSION = 1

#: The canonical raw-trace hash field contract: 64 lowercase hex chars
#: (matches ``TraceMetadata.raw_trace_sha256`` and the ISSUE-008 column).
_RAW_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def _u64(value: int) -> bytes:
    """One unsigned 64-bit big-endian frame integer."""
    return struct.pack(">Q", value)


def _validate_channels(channels: Sequence[ChannelSpec]) -> tuple[ChannelSpec, ...]:
    result = tuple(channels)
    if not result:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT, "at least one channel is required"
        )
    for channel in result:
        if not isinstance(channel, ChannelSpec):
            # Structured DomainError (not a bare TypeError): callers must be
            # able to branch on ErrorCode for every validation path.
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "channels must contain ChannelSpec",
                {"channel_type": type(channel).__name__},
            )
    seen: list[str] = []
    for channel in result:
        if channel.channel_id in seen:
            raise DomainError(
                ErrorCode.DUPLICATE_CHANNEL,
                "duplicate channel_id in channel contract",
                {"channel_id": channel.channel_id, "channel_ids": list(seen)},
            )
        seen.append(channel.channel_id)
    return result


def _immutable_array(values: object, dtype: np.dtype) -> np.ndarray:
    """Copy values into an owned snapshot that can never become writable.

    The result is backed by a private ``bytes`` object (via
    ``numpy.frombuffer``), so it is writeable=False and ``setflags(write=True)``
    is rejected by NumPy itself; the caller's source array is fully isolated.
    """
    owned = np.array(values, dtype=dtype, copy=True, order="C")
    payload = owned.tobytes()
    return np.frombuffer(payload, dtype=dtype).reshape(owned.shape)


def _validate_frequency_axis(values: object) -> np.ndarray:
    raw = np.asarray(values)
    if raw.dtype.kind not in "iuf":
        raise DomainError(
            ErrorCode.DTYPE_MISMATCH,
            "frequency axis must be real-valued numeric",
            {"dtype": str(raw.dtype)},
        )
    if raw.ndim != 1:
        raise DomainError(
            ErrorCode.AXIS_MISMATCH,
            "frequency axis must be one-dimensional",
            {"ndim": raw.ndim},
        )
    if raw.size == 0:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT, "frequency axis must not be empty"
        )
    # Canonical little-endian float64 values FIRST: finiteness and strict
    # increase are verified on the exact values that get hashed, so unsigned
    # diff underflow, signed extreme overflow and post-conversion collapse
    # cannot bypass the fail-closed axis contract (P1-01).
    canonical = np.ascontiguousarray(raw, dtype="<f8")
    if not np.all(np.isfinite(canonical)):
        raise DomainError(
            ErrorCode.NON_FINITE_AXIS,
            "frequency axis must contain only finite values",
        )
    if canonical.size > 1 and not np.all(np.diff(canonical) > 0):
        raise DomainError(
            ErrorCode.NON_INCREASING_AXIS,
            "frequency axis must be strictly increasing",
        )
    return canonical


def _validate_data(
    values: object,
    channels: Sequence[ChannelSpec],
    frequencies: np.ndarray,
) -> np.ndarray:
    raw = np.asarray(values)
    if raw.dtype.kind not in "iufc":
        raise DomainError(
            ErrorCode.DTYPE_MISMATCH,
            "raw data must be numeric (stored as complex128)",
            {"dtype": str(raw.dtype)},
        )
    if raw.ndim != 2:
        raise DomainError(
            ErrorCode.SHAPE_MISMATCH,
            "raw data must be channel x frequency",
            {"ndim": raw.ndim, "got": list(raw.shape)},
        )
    expected = (len(channels), int(frequencies.size))
    if raw.shape != expected:
        raise DomainError(
            ErrorCode.SHAPE_MISMATCH,
            "raw data shape must match the channel and frequency contract",
            {"expected": list(expected), "got": list(raw.shape)},
        )
    return np.asarray(raw, dtype="<c16")


#: Upper bound of ``trace_index`` (inclusive): aligned with the ISSUE-008
#: ``.rcscan`` v2 ``/trace_metadata/trace_index`` ``<i8`` (signed 64-bit)
#: storage column, so every accepted index is storable air/ground-wide.
#: The framing itself still encodes the index as ``uint64be``.
_TRACE_INDEX_MAX = 2**63 - 1


def _require_trace_index(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "trace_index must be an int",
            {"trace_index_type": type(value).__name__},
        )
    if value < 0:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "trace_index must be non-negative",
            {"trace_index": value},
        )
    if value > _TRACE_INDEX_MAX:
        raise DomainError(
            ErrorCode.OUT_OF_RANGE,
            "trace_index must be at most 2**63 - 1 "
            "(ISSUE-008 <i8 storage alignment)",
            {"trace_index": value, "max": _TRACE_INDEX_MAX},
        )
    return value


def _require_mission_id(value: MissionId | str) -> MissionId:
    if isinstance(value, MissionId):
        return value
    if not isinstance(value, str):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "mission_id must be a MissionId or canonical UUID string",
            {"mission_id_type": type(value).__name__},
        )
    try:
        return MissionId(value)
    except ValueError:
        raise DomainError(
            ErrorCode.INVALID_UUID,
            "non-canonical mission_id UUID string",
            {"mission_id": value},
        ) from None


def _require_trace_uid(value: TraceUid | str) -> TraceUid:
    if isinstance(value, TraceUid):
        return value
    if not isinstance(value, str):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "trace_uid must be a TraceUid or canonical UUID string",
            {"trace_uid_type": type(value).__name__},
        )
    try:
        return TraceUid(value)
    except ValueError:
        raise DomainError(
            ErrorCode.INVALID_UUID,
            "non-canonical trace_uid UUID string",
            {"trace_uid": value},
        ) from None


def _frame_text(value: str) -> bytes:
    payload = value.encode("utf-8")
    return _u64(len(payload)) + payload


def _frame_f64(axis: np.ndarray) -> bytes:
    # No copy when already little-endian float64; the input stays untouched.
    return np.ascontiguousarray(axis, dtype="<f8").tobytes()


def _frame_c128(data: np.ndarray) -> bytes:
    # No copy when already little-endian complex128; C order is forced for
    # the logical (channel, frequency) layout regardless of input strides.
    return np.ascontiguousarray(data, dtype="<c16").tobytes()


def compute_raw_trace_sha256(
    mission_id: MissionId | str,
    trace_index: int,
    trace_uid: TraceUid | str,
    channels: Sequence[ChannelSpec],
    frequencies_hz: object,
    data: object,
) -> str:
    """Return the canonical 64-lowercase-hex raw-trace SHA-256.

    The framing is fixed by ``docs/DATA_FORMAT.md`` section 5 and the golden
    vectors in ``tests/contract/raw_trace_hash_golden.json``.  Inputs are
    validated fail-closed and never mutated.
    """
    mission = _require_mission_id(mission_id)
    uid = _require_trace_uid(trace_uid)
    index = _require_trace_index(trace_index)
    channel_tuple = _validate_channels(channels)
    freqs = _validate_frequency_axis(frequencies_hz)
    raw = _validate_data(data, channel_tuple, freqs)

    parts: list[bytes] = [
        RAW_HASH_MAGIC.encode("ascii"),
        _u64(RAW_HASH_VERSION),
        _frame_text(mission.to_json()),
        _frame_text(uid.to_json()),
        _u64(index),
        _u64(len(channel_tuple)),
    ]
    parts.extend(_frame_text(channel.channel_id) for channel in channel_tuple)
    parts.extend(
        [
            _u64(int(freqs.size)),
            _frame_f64(freqs),
            _frame_c128(raw),
        ]
    )
    return hashlib.sha256(b"".join(parts)).hexdigest()


def validate_raw_hash(value: object) -> str:
    """Validate a canonical raw-trace hash (64 lowercase hex chars).

    Returns the validated string, or raises ``DomainError``
    (``ErrorCode.INVALID_ARGUMENT``) fail-closed.
    """
    if not isinstance(value, str) or _RAW_HASH_RE.fullmatch(value) is None:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "raw_trace_sha256 field contract is 64 lowercase hex characters",
            {"raw_trace_sha256": value if isinstance(value, str) else type(value).__name__},
        )
    return value


# ---------------------------------------------------------------------------
# RawHashSpec: self-contained, JSON round-trippable hash input carrier
# ---------------------------------------------------------------------------

#: Exact top-level key set of the ``RawHashSpec`` JSON payload.
_SPEC_JSON_KEYS = frozenset(
    {
        "spec_version",
        "hash_version",
        "mission_id",
        "trace_index",
        "trace_uid",
        "channels",
        "frequencies_hz",
        "data",
        "data_shape",
    }
)
#: Exact key set of every channel sub-object in the ``RawHashSpec`` JSON.
_SPEC_CHANNEL_KEYS = frozenset(
    {
        "channel_id",
        "logical_polarization",
        "s_parameter",
        "display_name",
        "antenna_note",
    }
)


@dataclass(frozen=True, slots=True)
class RawHashSpec:
    """The complete input set of one raw-trace hash computation.

    ``frequencies_hz`` and ``data`` are owned, bytes-backed read-only
    snapshots: they never alias caller arrays, stay ``writeable=False`` and
    cannot be re-enabled via ``setflags(write=True)``, so ``compute()`` and
    ``__hash__`` are stable for the object's lifetime.  The spec serializes
    to JSON (complex array split into real/imag parts) carrying explicit
    ``spec_version``/``hash_version`` (v1-only parse, unknown versions
    rejected), and restores losslessly, so hash inputs travel with the trace.
    """

    mission_id: MissionId
    trace_index: int
    trace_uid: TraceUid
    channels: tuple[ChannelSpec, ...]
    frequencies_hz: np.ndarray
    data: np.ndarray

    def __post_init__(self) -> None:
        mission = _require_mission_id(self.mission_id)
        uid = _require_trace_uid(self.trace_uid)
        index = _require_trace_index(self.trace_index)
        channels = _validate_channels(self.channels)
        freqs = _validate_frequency_axis(self.frequencies_hz)
        data = _validate_data(self.data, channels, freqs)
        object.__setattr__(self, "mission_id", mission)
        object.__setattr__(self, "trace_index", index)
        object.__setattr__(self, "trace_uid", uid)
        object.__setattr__(self, "channels", channels)
        object.__setattr__(self, "frequencies_hz", _immutable_array(freqs, np.dtype("<f8")))
        object.__setattr__(self, "data", _immutable_array(data, np.dtype("<c16")))

    def compute(self) -> str:
        """Compute the canonical hash for this spec (inputs unchanged)."""
        return compute_raw_trace_sha256(
            mission_id=self.mission_id,
            trace_index=self.trace_index,
            trace_uid=self.trace_uid,
            channels=self.channels,
            frequencies_hz=self.frequencies_hz,
            data=self.data,
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RawHashSpec):
            return NotImplemented
        return (
            self.mission_id == other.mission_id
            and self.trace_index == other.trace_index
            and self.trace_uid == other.trace_uid
            and self.channels == other.channels
            and np.array_equal(self.frequencies_hz, other.frequencies_hz)
            and np.array_equal(self.data, other.data)
        )

    def __hash__(self) -> int:
        return hash(
            (
                self.mission_id,
                self.trace_index,
                self.trace_uid,
                self.channels,
                self.frequencies_hz.tobytes(),
                self.data.tobytes(),
            )
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "spec_version": RAW_HASH_SPEC_VERSION,
            "hash_version": RAW_HASH_VERSION,
            "mission_id": self.mission_id.to_json(),
            "trace_index": self.trace_index,
            "trace_uid": self.trace_uid.to_json(),
            "channels": [
                {
                    "channel_id": channel.channel_id,
                    "logical_polarization": channel.logical_polarization.value,
                    "s_parameter": channel.s_parameter.value,
                    "display_name": channel.display_name,
                    "antenna_note": channel.antenna_note,
                }
                for channel in self.channels
            ],
            "frequencies_hz": [float(value) for value in self.frequencies_hz],
            "data": [
                [float(value.real), float(value.imag)]
                for value in self.data.reshape(-1)
            ],
            "data_shape": list(self.data.shape),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        from uav_gpr.core.channels import ChannelSpec as _ChannelSpec
        from uav_gpr.core.enums import LogicalPolarization, SParameter

        if not isinstance(data, Mapping):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "RawHashSpec JSON payload must be an object",
                {"payload_type": type(data).__name__},
            )
        unknown = set(data) - _SPEC_JSON_KEYS
        if unknown:
            unknown_fields: list[JsonValue] = [field for field in sorted(unknown)]
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "unknown RawHashSpec JSON fields",
                {"unknown_fields": unknown_fields},
            )
        missing = _SPEC_JSON_KEYS - set(data)
        if missing:
            missing_fields: list[JsonValue] = [field for field in sorted(missing)]
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "missing RawHashSpec JSON fields",
                {"missing_fields": missing_fields},
            )
        spec_version = data["spec_version"]
        hash_version = data["hash_version"]
        if isinstance(spec_version, bool) or not isinstance(spec_version, int):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "spec_version must be an int",
                {"spec_version_type": type(spec_version).__name__},
            )
        if isinstance(hash_version, bool) or not isinstance(hash_version, int):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "hash_version must be an int",
                {"hash_version_type": type(hash_version).__name__},
            )
        if spec_version != RAW_HASH_SPEC_VERSION:
            raise DomainError(
                ErrorCode.UNSUPPORTED_SCHEMA_VERSION,
                "RawHashSpec JSON schema version 1 only",
                {"spec_version": spec_version},
            )
        if hash_version != RAW_HASH_VERSION:
            raise DomainError(
                ErrorCode.UNSUPPORTED_SCHEMA_VERSION,
                "raw hash framing version 1 only",
                {"hash_version": hash_version},
            )

        mission_raw = data["mission_id"]
        uid_raw = data["trace_uid"]
        index_raw = data["trace_index"]
        channels_raw = data["channels"]
        freqs_raw = data["frequencies_hz"]
        data_raw = data["data"]
        shape_raw = data["data_shape"]
        if not isinstance(mission_raw, str) or not isinstance(uid_raw, str):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "mission_id and trace_uid must be strings",
            )
        if not isinstance(channels_raw, list) or not channels_raw:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "channels must be a non-empty list",
            )
        if not isinstance(freqs_raw, list) or not freqs_raw:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "frequencies_hz must be a non-empty list",
            )
        if not isinstance(data_raw, list) or not isinstance(shape_raw, list):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "data must be a list and data_shape must be a list",
            )
        if len(shape_raw) != 2:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "data_shape must be [channel, frequency]",
                {"data_shape": shape_raw},
            )

        def _require_dim(value: object, name: str) -> int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    f"{name} must be a non-bool int",
                    {f"{name}_type": type(value).__name__},
                )
            if value < 1:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    f"{name} must be positive",
                    {name: value},
                )
            return value

        channels_dim = _require_dim(shape_raw[0], "data_shape[0]")
        freq_dim = _require_dim(shape_raw[1], "data_shape[1]")
        if len(channels_raw) != channels_dim:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "channels length must match data_shape[0]",
                {"channels_len": len(channels_raw), "channels_dim": channels_dim},
            )
        if len(freqs_raw) != freq_dim:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "frequencies_hz length must match data_shape[1]",
                {"freqs_len": len(freqs_raw), "freq_dim": freq_dim},
            )
        if len(data_raw) != channels_dim * freq_dim:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "data length must match the data_shape product",
                {"data_len": len(data_raw), "expected": channels_dim * freq_dim},
            )

        channels: list[ChannelSpec] = []
        for item in channels_raw:
            if not isinstance(item, dict):
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "channel entries must be JSON objects",
                    {"channel_type": type(item).__name__},
                )
            unknown = set(item) - _SPEC_CHANNEL_KEYS
            if unknown:
                unknown_channel_fields: list[JsonValue] = [
                    field for field in sorted(unknown)
                ]
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "unknown channel JSON fields",
                    {"unknown_fields": unknown_channel_fields},
                )
            missing = _SPEC_CHANNEL_KEYS - set(item)
            if missing:
                missing_channel_fields: list[JsonValue] = [
                    field for field in sorted(missing)
                ]
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "missing channel JSON fields",
                    {"missing_fields": missing_channel_fields},
                )
            channel_id_raw = item["channel_id"]
            pol_raw = item["logical_polarization"]
            spar_raw = item["s_parameter"]
            name_raw = item["display_name"]
            note_raw = item["antenna_note"]
            if not all(
                isinstance(value, str)
                for value in (channel_id_raw, pol_raw, spar_raw, name_raw)
            ):
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "channel_id/logical_polarization/s_parameter/display_name "
                    "must be strings",
                )
            if note_raw is not None and not isinstance(note_raw, str):
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "antenna_note must be a string or null",
                    {"antenna_note_type": type(note_raw).__name__},
                )
            try:
                channels.append(
                    _ChannelSpec(
                        channel_id=channel_id_raw,
                        logical_polarization=LogicalPolarization.from_value(pol_raw),
                        s_parameter=SParameter.from_value(spar_raw),
                        display_name=name_raw,
                        antenna_note=note_raw,
                    )
                )
            except ValueError:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "invalid channel contract in RawHashSpec JSON",
                ) from None

        axis_values: list[float] = []
        for entry in freqs_raw:
            if isinstance(entry, bool) or not isinstance(entry, (int, float)):
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "frequencies_hz entries must be real numbers",
                    {"entry_type": type(entry).__name__},
                )
            axis_values.append(float(entry))
        pairs: list[complex] = []
        for pair in data_raw:
            if not isinstance(pair, list) or len(pair) != 2:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "data entries must be [real, imag] pairs",
                )
            real_raw, imag_raw = pair
            if (
                isinstance(real_raw, bool)
                or not isinstance(real_raw, (int, float))
                or isinstance(imag_raw, bool)
                or not isinstance(imag_raw, (int, float))
            ):
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "data entries must be numeric [real, imag] pairs",
                )
            pairs.append(complex(real_raw, imag_raw))
        flat = np.asarray(pairs, dtype="<c16").reshape(channels_dim, freq_dim)
        if isinstance(index_raw, bool) or not isinstance(index_raw, int):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "trace_index must be an int",
                {"trace_index_type": type(index_raw).__name__},
            )
        return cls(
            mission_id=_require_mission_id(mission_raw),
            trace_index=index_raw,
            trace_uid=_require_trace_uid(uid_raw),
            channels=tuple(channels),
            frequencies_hz=np.asarray(axis_values, dtype="<f8"),
            data=flat,
        )
