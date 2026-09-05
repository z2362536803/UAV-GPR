"""ISSUE-037: protocol v1 immutable messages, envelope and incremental codec.

Frozen by ``docs/adr/0006-protocol-v1-framing.md``.  This module is pure wire
formatting: it never opens a socket, never touches the outbox and drives no
business state machine (those belong to ISSUE-038..041).

Frame layout (all big-endian network order; fixed 18-byte prefix)::

    [0:4)   magic = b"UAVP"
    [4]     major u8        (current: 1; any other value is rejected)
    [5]     minor u8        (capability-negotiated; see CapabilityPolicy)
    [6:8)   type  u16be     (MessageKind code; unknown codes are rejected)
    [8:10)  flags u16be     (must be zero in v1)
    [10:14) header_length u32be   <= MAX_HEADER_BYTES
    [14:18) payload_length u32be  <= MAX_PAYLOAD_BYTES
    [18:18+H)   header  UTF-8 canonical JSON (sorted keys, compact separators,
                          ASCII-only, non-finite floats rejected, duplicate
                          keys rejected)
    then  payload binary: only trace_record carries bytes -- the canonical
            raw ``channel x frequency`` complex128 little-endian array, whose
            SHA-256 domain is shared with the ISSUE-009 raw hash.

Security properties (TRANSPORT_PROTOCOL section 9): every declared length is
validated *before* its body is buffered, so a corrupt or hostile stream can
never force unbounded allocation.  A structural failure poisons the parser
until :meth:`FrameParser.reset` so misframed bytes are never guessed at.

Canonicalization note (ADR-0006): the trace payload's integrity anchor is
recomputed from the received bytes over a **canonical, config-free** frame
(``identity | channel ids | dtype/shape stamps | raw bytes``) and must equal
the ``raw_trace_sha256`` carried in the metadata.  The full ISSUE-009 hash
over the real frequency axis is additionally verified when the receiver binds
the frozen mission config via :func:`decode_trace_with_config`; without the
config the identity/hash binding is still fail-closed on-wire.

Field validation deliberately delegates to ``uav_gpr.core`` types (canonical
UUIDs, 64-lowercase-hex hashes, UTC ISO strings, fail-closed numerics); this
module defines no parallel domain types.  Pickle and NPZ cannot pass the
magic gate and are structurally excluded; display/time-derived arrays have
no field slot at all (structural prohibition, TRANSPORT_PROTOCOL section 2).

Global state (registered per ISSUE_037 review P3-2): importing this module
builds GOLDEN_FRAMES and seeds the process-global channel-contract registry
with the two golden-fixture ids (hh_s11/vv_s22) via
``_register_golden_contracts()``.  The ids are fixture-only; real missions
register their own contracts through ``register_mission_config`` /
``register_trace_channels``, and conflicting re-registration fails closed, so
the seed can never mask a contract mismatch.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum
from typing import Final, Self, cast

import numpy as np

from uav_gpr.core.channels import ChannelSpec
from uav_gpr.core.config import MissionConfig
from uav_gpr.core.enums import StableStrEnum, TraceQualityReason, TraceQualityStatus
from uav_gpr.core.errors import DomainError, ErrorCode, JsonValue
from uav_gpr.core.identifiers import CommandId, DeviceId, MissionId, TraceUid
from uav_gpr.core.metadata import TraceMetadata
from uav_gpr.core.raw_hash import compute_raw_trace_sha256
from uav_gpr.core.timeutil import MonotonicNs, from_utc_iso, to_utc_iso

# ---------------------------------------------------------------------------
# frozen constants (ADR-0006)
# ---------------------------------------------------------------------------

MAGIC: Final[bytes] = b"UAVP"
PROTOCOL_MAJOR: Final[int] = 1
PROTOCOL_MINOR: Final[int] = 0
HEADER_SPEC_VERSION: Final[int] = 1
MAX_HEADER_BYTES: Final[int] = 1 << 20  # 1 MiB
MAX_PAYLOAD_BYTES: Final[int] = 64 << 20  # 64 MiB
PREFIX_LENGTH: Final[int] = 18
FLAG_RESERVED_MASK: Final[int] = 0xFFFF
TRACE_HASH_DOMAIN_TAG: Final[str] = "issue009-raw-sha256-v1"
INVENTORY_HASH_DOMAIN_TAG: Final[str] = "inventory-xor-v1"

_COMPLEX128_ITEMSIZE: Final[int] = 16
_TRACE_INDEX_MAX: Final[int] = 2**63 - 1  # aligned with ISSUE-008 <i8 column


class MessageKind(IntEnum):
    """Stable wire type codes.  Never renumber or reuse a retired code."""

    HELLO = 0x0001
    STATUS = 0x0002
    COMMAND = 0x0003
    MISSION = 0x0004
    TRACE = 0x0005
    ACK = 0x0006
    INVENTORY = 0x0007
    ERROR = 0x0008

    @property
    def code(self) -> int:
        """Wire type code (stable numeric identity)."""
        return int(self)

    @property
    def wire_name(self) -> str:
        return self.name.lower()

    @classmethod
    def from_wire(cls, value: object) -> MessageKind:
        if isinstance(value, bool) or not isinstance(value, int):
            raise FrameError(
                ErrorCode.INVALID_ARGUMENT,
                "message type code must be an int",
                {"type": repr(value)},
            )
        try:
            return cls(value)
        except ValueError:
            raise FrameError(
                ErrorCode.INVALID_ARGUMENT,
                "unknown protocol message type code",
                {"type": value},
            ) from None


_KIND_BY_NAME: Final[Mapping[str, MessageKind]] = {kind.wire_name: kind for kind in MessageKind}


class AckState(StableStrEnum):
    """Air-side acquisition lifecycle (wire vocabulary for status frames)."""

    IDLE = "idle"
    ARMED = "armed"
    ACQUIRING = "acquiring"
    PAUSED = "paused"
    COMPLETED = "completed"
    USER_STOPPED = "user_stopped"
    FAULT_STOPPED = "fault_stopped"


class AckResult(StableStrEnum):
    """Ground persistence verdict carried by ACK frames (AGENTS.md section 6)."""

    PERSISTED = "persisted"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"
    CONFLICT = "conflict"


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------


class FrameError(Exception):
    """Structural wire error with a stable code and safe display text.

    Messages are ASCII-only (like ``DomainError``); branching happens on
    ``code`` and ``context``, never on prose.
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        context: Mapping[str, JsonValue] | None = None,
    ) -> None:
        if not isinstance(code, ErrorCode):
            raise TypeError("code must be an ErrorCode")
        if not message or not message.isascii():
            raise ValueError("FrameError message must be non-empty ASCII")
        self.code = code
        self.message = message
        self.context: dict[str, JsonValue] = {key: value for key, value in (context or {}).items()}
        super().__init__(message)


def missing_field(field_name: str) -> FrameError:
    return FrameError(
        ErrorCode.INVALID_ARGUMENT,
        "required protocol header field is missing",
        {"field": field_name},
    )


# ---------------------------------------------------------------------------
# canonical header serialization
# ---------------------------------------------------------------------------


def _reject_non_finite(value: object) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "protocol headers must contain only finite floats",
            {"offending_value": repr(value)},
        )
    if isinstance(value, Mapping):
        for item in value.values():
            _reject_non_finite(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_non_finite(item)


class _NotCanonicalText(Exception):
    """Internal signal: the text parses but its raw form is not canonical."""


def _reject_duplicate_keys(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    seen: set[str] = set()
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in seen:
            raise FrameError(
                ErrorCode.INVALID_ARGUMENT,
                "header is not canonical UTF-8 JSON (duplicate key)",
                {"reason": "duplicate-key", "key": key},
            )
        seen.add(key)
        result[key] = value
    return result


_STRICT_DECODER: Final = json.JSONDecoder(
    object_pairs_hook=_reject_duplicate_keys,
    parse_constant=lambda name: (_ for _ in ()).throw(
        FrameError(
            ErrorCode.INVALID_ARGUMENT,
            "non-finite JSON constant forbidden in protocol header",
            {"reason": "constant", "constant": name},
        )
    ),
)


def _reject_constant(name: str) -> JsonValue:
    raise FrameError(
        ErrorCode.INVALID_ARGUMENT,
        "non-finite JSON constant forbidden in protocol header",
        {"reason": "constant", "constant": name},
    )


def canonical_header_bytes(header: Mapping[str, JsonValue]) -> bytes:
    """Serialize a header mapping to the frozen canonical form.

    Sorted keys at every level, compact separators, ASCII-only (ensure_ascii),
    NaN/Infinity rejected.  Mirrors ``MissionConfig.to_canonical_json`` while
    staying in its own hash domain.
    """
    _reject_non_finite(header)
    text = json.dumps(dict(header), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return text.encode("ascii")


def _parse_canonical_header(data: bytes) -> dict[str, JsonValue]:
    """Parse header bytes strictly: valid UTF-8 single JSON object, canonical.

    Rejection is fail-closed on every deviation: bad charset ("charset"),
    unparsable JSON ("json"), duplicate keys ("duplicate-key"), NaN/Infinity
    constants ("constant"), non-object documents ("shape"), and any raw-text
    difference from our canonical re-serialization -- key order, whitespace,
    BOM or alternate escapes all land in the "canonical" bucket.  The check
    compares *text*, never parsed-dict round-trips (which are order-blind).
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise FrameError(
            ErrorCode.INVALID_ARGUMENT,
            "header is not valid UTF-8",
            {"reason": "charset"},
        ) from None
    if len(text) != len(data):
        # BOM/alternate encodings decode to fewer chars than bytes? UTF-8 with
        # a BOM yields an extra leading char; reject anything that survived
        # decode but was not pure-ASCII canonical JSON below anyway.
        pass
    try:
        parsed = _STRICT_DECODER.decode(text)
    except FrameError:
        raise
    except (json.JSONDecodeError, RecursionError, ValueError):
        raise FrameError(
            ErrorCode.INVALID_ARGUMENT,
            "header is not parsable JSON",
            {"reason": "json"},
        ) from None
    if not isinstance(parsed, dict):
        raise FrameError(ErrorCode.INVALID_ARGUMENT, "header JSON must be an object",
            {"reason": "shape"})
    try:
        reserialized = canonical_header_bytes(parsed)
    except DomainError as exc:
        raise FrameError(exc.code, "header contains non-canonical values",
            {"reason": exc.message}) from None
    if reserialized != data:
        raise FrameError(
            ErrorCode.INVALID_ARGUMENT,
            "header is not canonical UTF-8 JSON (sorted keys, compact separators)",
            {"reason": "canonical"},
        )
    return parsed


# ---------------------------------------------------------------------------
# shared field validators (fail-closed)
# ---------------------------------------------------------------------------


def _require_device(value: object, field_name: str = "device_id") -> DeviceId:
    if isinstance(value, DeviceId):
        return value
    if isinstance(value, str):
        try:
            return DeviceId(value)
        except ValueError:
            pass
    raise DomainError(ErrorCode.INVALID_UUID, f"{field_name} must be a canonical UUID",
        {field_name: repr(value)})


def _require_mission(value: object, field_name: str = "mission_id") -> MissionId:
    if isinstance(value, MissionId):
        return value
    if isinstance(value, str):
        try:
            return MissionId(value)
        except ValueError:
            pass
    raise DomainError(ErrorCode.INVALID_UUID, f"{field_name} must be a canonical UUID",
        {field_name: repr(value)})


def _require_uid(value: object, field_name: str = "trace_uid") -> TraceUid:
    if isinstance(value, TraceUid):
        return value
    if isinstance(value, str):
        try:
            return TraceUid(value)
        except ValueError:
            pass
    raise DomainError(ErrorCode.INVALID_UUID, f"{field_name} must be a canonical UUID",
        {field_name: repr(value)})


def _require_command_id(value: object) -> CommandId:
    if isinstance(value, CommandId):
        return value
    if isinstance(value, str):
        try:
            return CommandId(value)
        except ValueError:
            pass
    raise DomainError(
        ErrorCode.INVALID_UUID, "command_id must be a canonical UUID", {"command_id": repr(value)}
    )


def _require_str(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            f"{field_name} must be a non-empty string",
            {field_name: repr(value)},
        )
    return value


def _require_int(value: object, field_name: str, *, minimum: int = 0,
    maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DomainError(ErrorCode.INVALID_ARGUMENT, f"{field_name} must be an int",
            {field_name: repr(value)})
    if value < minimum:
        raise DomainError(
            ErrorCode.OUT_OF_RANGE, f"{field_name} must be >= {minimum}", {field_name: value,
                "min": minimum}
        )
    if maximum is not None and value > maximum:
        raise DomainError(
            ErrorCode.OUT_OF_RANGE,
            f"{field_name} must be <= {maximum}",
            {field_name: value, "max": maximum},
        )
    return value


def _require_bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT, f"{field_name} must be a bool", {field_name: repr(value)}
        )
    return value


def _require_utc(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise DomainError(
            ErrorCode.NAIVE_DATETIME, f"{field_name} must be a datetime", {field_name: repr(value)}
        )
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainError(
            ErrorCode.NAIVE_DATETIME,
            f"{field_name} must be timezone-aware UTC",
            {field_name: repr(value)},
        )
    return value


def _require_hash(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            f"{field_name} field contract is 64 lowercase hex characters",
            {field_name: value if isinstance(value, str) else type(value).__name__},
        )
    return value


def _require_enum(value: object, enum_cls: type[StableStrEnum], field_name: str) -> StableStrEnum:
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str):
        try:
            return enum_cls.from_value(value)
        except (TypeError, ValueError):
            pass
    raise DomainError(
        ErrorCode.INVALID_ARGUMENT,
        f"{field_name} must be a {enum_cls.__name__}",
        {field_name: repr(value)},
    )


def _require_error_code(value: object, field_name: str) -> ErrorCode:
    if isinstance(value, ErrorCode):
        return value
    if isinstance(value, str):
        try:
            return ErrorCode(value)
        except ValueError:
            pass
    raise DomainError(
        ErrorCode.INVALID_ARGUMENT,
        f"{field_name} must be a known machine code",
        {field_name: repr(value)},
    )


def _require_tuple(value: object, field_name: str) -> tuple[JsonValue, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            f"{field_name} must be a sequence",
            {field_name: type(value).__name__},
        )
    return tuple(cast("Sequence[JsonValue]", value))


def _json_safe(value: object, path: str = "$") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT, f"context value at {path} is not finite", {"path": path}
            )
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _json_safe(item, f"{path}[{index}]")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise DomainError(ErrorCode.INVALID_ARGUMENT, f"context key at {path} must be str")
            _json_safe(item, f"{path}.{key}")
        return
    raise DomainError(
        ErrorCode.INVALID_ARGUMENT, f"context value at {path} is not JSON-safe", {"path": path}
    )


def _copy_json(value: JsonValue) -> JsonValue:
    if isinstance(value, list):
        return [_copy_json(item) for item in value]
    if isinstance(value, dict):
        return {key: _copy_json(item) for key, item in value.items()}
    return value


# ---------------------------------------------------------------------------
# canonical per-trace wire hash (shared domain with ISSUE-009 framing style)
# ---------------------------------------------------------------------------


def _u64(value: int) -> bytes:
    return int(value).to_bytes(8, "big")


def _frame_text(value: str) -> bytes:
    data = value.encode("utf-8")
    return _u64(len(data)) + data


# ---------------------------------------------------------------------------
# message models (frozen; header authority lives here)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HelloMessage:
    device_id: DeviceId
    software_version: str
    connection_generation: int
    capabilities: tuple[str, ...]
    session_id: str | None
    mission_id: MissionId | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "device_id", _require_device(self.device_id))
        object.__setattr__(
            self, "software_version", _require_str(self.software_version, "software_version")
        )
        object.__setattr__(
            self,
            "connection_generation",
            _require_int(self.connection_generation, "connection_generation"),
        )
        caps = _require_tuple(self.capabilities, "capabilities")
        object.__setattr__(
            self,
            "capabilities",
            tuple(_require_str(item, "capabilities[]") for item in caps),
        )
        if self.session_id is not None:
            object.__setattr__(self, "session_id", _require_str(self.session_id, "session_id"))
        if self.mission_id is not None:
            object.__setattr__(self, "mission_id", _require_mission(self.mission_id))

    @property
    def protocol_major(self) -> int:
        return PROTOCOL_MAJOR

    @property
    def protocol_minor(self) -> int:
        return PROTOCOL_MINOR

    def to_header(self) -> dict[str, JsonValue]:
        return {
            "device_id": self.device_id.to_json(),
            "software_version": self.software_version,
            "connection_generation": self.connection_generation,
            "capabilities": list(self.capabilities),
            "session_id": self.session_id,
            "mission_id": self.mission_id.to_json() if self.mission_id else None,
        }

    @classmethod
    def from_header(cls, header: Mapping[str, JsonValue]) -> Self:
        mission_raw = header.get("mission_id")
        session_raw = header.get("session_id")
        return cls(
            device_id=_require_device(header["device_id"]),
            software_version=_require_str(header["software_version"], "software_version"),
            connection_generation=_require_int(header["connection_generation"],
                "connection_generation"),
            capabilities=tuple(
                _require_str(item, "capabilities[]")
                for item in _require_tuple(header["capabilities"], "capabilities")
            ),
            session_id=None if session_raw is None else _require_str(session_raw, "session_id"),
            mission_id=None if mission_raw is None else _require_mission(mission_raw),
        )


@dataclass(frozen=True, slots=True)
class StatusMessage:
    device_id: DeviceId
    mission_id: MissionId
    connection_generation: int
    acquisition_state: AckState
    storage_writable: bool
    pending_trace_count: int
    last_error_code: ErrorCode | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "device_id", _require_device(self.device_id))
        object.__setattr__(self, "mission_id", _require_mission(self.mission_id))
        object.__setattr__(
            self,
            "connection_generation",
            _require_int(self.connection_generation, "connection_generation"),
        )
        object.__setattr__(
            self, "acquisition_state", _require_enum(self.acquisition_state, AckState,
                "acquisition_state")
        )
        object.__setattr__(
            self, "storage_writable", _require_bool(self.storage_writable, "storage_writable")
        )
        object.__setattr__(
            self, "pending_trace_count", _require_int(self.pending_trace_count,
                "pending_trace_count")
        )
        if self.last_error_code is not None:
            object.__setattr__(self, "last_error_code", _require_error_code(self.last_error_code,
                "last_error_code"))

    def to_header(self) -> dict[str, JsonValue]:
        return {
            "device_id": self.device_id.to_json(),
            "mission_id": self.mission_id.to_json(),
            "connection_generation": self.connection_generation,
            "acquisition_state": self.acquisition_state.value,
            "storage_writable": self.storage_writable,
            "pending_trace_count": self.pending_trace_count,
            "last_error_code": (
                self.last_error_code.value if self.last_error_code is not None else None
            ),
        }

    @classmethod
    def from_header(cls, header: Mapping[str, JsonValue]) -> Self:
        code_raw = header.get("last_error_code")
        return cls(
            device_id=_require_device(header["device_id"]),
            mission_id=_require_mission(header["mission_id"]),
            connection_generation=_require_int(header["connection_generation"],
                "connection_generation"),
            acquisition_state=AckState.from_value(
                _require_str(header["acquisition_state"], "acquisition_state")
            ),
            storage_writable=_require_bool(header["storage_writable"], "storage_writable"),
            pending_trace_count=_require_int(header["pending_trace_count"], "pending_trace_count"),
            last_error_code=None if code_raw is None else _require_error_code(code_raw,
                "last_error_code"),
        )


@dataclass(frozen=True, slots=True)
class CommandMessage:
    command_id: CommandId
    operation: str
    issued_utc: datetime
    mission_id: MissionId | None
    payload_json: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", _require_command_id(self.command_id))
        object.__setattr__(self, "operation", _require_str(self.operation, "operation"))
        object.__setattr__(self, "issued_utc", _require_utc(self.issued_utc, "issued_utc"))
        if self.mission_id is not None:
            object.__setattr__(self, "mission_id", _require_mission(self.mission_id))
        if self.payload_json is not None:
            if not isinstance(self.payload_json, str):
                raise DomainError(ErrorCode.INVALID_ARGUMENT,
                    "payload_json must be a string or null")
            try:
                json.loads(self.payload_json)
            except (json.JSONDecodeError, RecursionError):
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "payload_json must be valid JSON text",
                    {"payload_json": "unparsable"},
                ) from None

    def to_header(self) -> dict[str, JsonValue]:
        return {
            "command_id": self.command_id.to_json(),
            "operation": self.operation,
            "issued_utc": to_utc_iso(self.issued_utc),
            "mission_id": self.mission_id.to_json() if self.mission_id else None,
            "payload_json": self.payload_json,
        }

    @classmethod
    def from_header(cls, header: Mapping[str, JsonValue]) -> Self:
        mission_raw = header.get("mission_id")
        payload_raw = header.get("payload_json")
        if payload_raw is not None and not isinstance(payload_raw, str):
            raise DomainError(ErrorCode.INVALID_ARGUMENT, "payload_json must be a string or null")
        return cls(
            command_id=_require_command_id(header["command_id"]),
            operation=_require_str(header["operation"], "operation"),
            issued_utc=from_utc_iso(_require_str(header["issued_utc"], "issued_utc")),
            mission_id=None if mission_raw is None else _require_mission(mission_raw),
            payload_json=payload_raw,
        )


@dataclass(frozen=True, slots=True)
class MissionMessage:
    """Frozen task configuration downlink (full config + digest re-check)."""

    config: MissionConfig

    def __post_init__(self) -> None:
        if not isinstance(self.config, MissionConfig):
            raise TypeError("config must be a MissionConfig")

    @property
    def mission_digest(self) -> str:
        return self.config.config_sha256

    def to_header(self) -> dict[str, JsonValue]:
        return {"config": cast("JsonValue", self.config.to_dict())}

    @classmethod
    def from_header(cls, header: Mapping[str, JsonValue]) -> Self:
        config_raw = header.get("config")
        if not isinstance(config_raw, dict):
            raise FrameError(
                ErrorCode.INVALID_ARGUMENT,
                "mission header requires a config object",
                {"field": "config"},
            )
        try:
            config = MissionConfig.from_dict(config_raw)
        except (DomainError, ValueError, TypeError, KeyError) as exc:
            raise FrameError(
                ErrorCode.CONFIG_DIGEST_MISMATCH,
                "mission config failed strict decode or digest re-check",
                {"reason": str(exc)[:200]},
            ) from None
        return cls(config=config)


#: Channel contracts registered by endpoints that *acquire* or hold mission
#: configs, enabling exact ISSUE-009 recomputation at encode/decode time.
#: channel_ids on a trace must resolve through this registry (or through
#: the bound MissionConfig in decode_trace_with_config); unknown ids fail
#: closed with an explicit reconciliation error instead of a fake spec.
_TRACE_CHANNEL_REGISTRY: dict[str, ChannelSpec] = {}


def register_trace_channels(channels: Sequence[ChannelSpec]) -> None:
    """Register channel specs under their canonical ids (idempotent).

    Re-registering an identical spec is a no-op; re-registering a different
    spec under the same id is a contract conflict and fails closed.
    """
    for channel in channels:
        if not isinstance(channel, ChannelSpec):
            raise TypeError("channels must contain ChannelSpec")
        existing = _TRACE_CHANNEL_REGISTRY.get(channel.channel_id)
        if existing is not None and existing != channel:
            raise DomainError(
                ErrorCode.ID_CONFLICT,
                "channel_id already registered with a different contract",
                {"channel_id": channel.channel_id},
            )
        _TRACE_CHANNEL_REGISTRY[channel.channel_id] = channel


def _resolve_channels(channel_ids: Sequence[str]) -> tuple[ChannelSpec, ...]:
    resolved: list[ChannelSpec] = []
    unknown: list[str] = []
    for channel_id in channel_ids:
        spec = _TRACE_CHANNEL_REGISTRY.get(channel_id)
        if spec is None:
            unknown.append(channel_id)
        else:
            resolved.append(spec)
    if unknown:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "cannot recompute ISSUE-009 hash: channel contract not registered"
            " (bind the mission config via decode_trace_with_config)",
            {"unknown_channel_ids": list(unknown)},
        )
    return tuple(resolved)


@dataclass(frozen=True, slots=True)
class TraceMessage:
    """One canonical raw trace: metadata in the header, raw bytes as payload.

    ``channel_ids`` freezes the ordered channel contract beside the config
    digest; ``frequencies_hz`` is the axis that was used when the ISSUE-009
    ``raw_trace_sha256`` was computed (it is NOT re-sent on the wire -- the
    receiver recovers it from the bound mission config or recomputes over the
    wire-domain hash).
    """

    mission_id: MissionId
    trace_uid: TraceUid
    trace_index: int
    device_id: DeviceId
    config_sha256: str
    metadata: TraceMetadata
    frequencies_hz: np.ndarray
    data: np.ndarray
    channel_ids: tuple[str, ...]
    frequency_start_hz: float | None = None
    frequency_stop_hz: float | None = None
    frequency_points: int | None = None

    def __post_init__(self) -> None:
        mission = _require_mission(self.mission_id)
        uid = _require_uid(self.trace_uid)
        device = _require_device(self.device_id)
        digest = _require_hash(self.config_sha256, "config_sha256")
        if not isinstance(self.metadata, TraceMetadata):
            raise TypeError("metadata must be a TraceMetadata")
        if (
            self.metadata.mission_id != mission
            or self.metadata.trace_uid != uid
            or self.metadata.device_id != device
        ):
            raise DomainError(
                ErrorCode.ID_CONFLICT,
                "trace identity fields must agree between message and metadata",
                {
                    "mission_id": mission.to_json(),
                    "trace_uid": uid.to_json(),
                    "device_id": device.to_json(),
                },
            )
        if self.metadata.raw_trace_sha256 is None:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "transmitted traces require the ISSUE-009 raw_trace_sha256",
                {"trace_index": self.metadata.trace_index},
            )
        index = _require_int(self.metadata.trace_index, "trace_index", maximum=_TRACE_INDEX_MAX)
        if self.trace_index != index:
            raise DomainError(
                ErrorCode.ID_CONFLICT,
                "trace_index must equal metadata.trace_index",
                {"trace_index": self.trace_index, "metadata_trace_index": index},
            )
        channel_ids = tuple(_require_str(item,
            "channel_ids[]") for item in _require_tuple(self.channel_ids, "channel_ids"))
        if len(set(channel_ids)) != len(channel_ids) or not channel_ids:
            raise DomainError(
                ErrorCode.DUPLICATE_CHANNEL,
                "channel_ids must be unique and non-empty",
                {"channel_ids": list(channel_ids)},
            )
        freqs = np.asarray(self.frequencies_hz)
        if freqs.dtype.kind not in "fu":
            raise DomainError(ErrorCode.DTYPE_MISMATCH, "frequencies_hz must be numeric",
                {"dtype": str(freqs.dtype)})
        freqs = np.ascontiguousarray(freqs, dtype="<f8")
        if freqs.ndim != 1 or freqs.size < 1:
            raise DomainError(
                ErrorCode.SHAPE_MISMATCH,
                "frequencies_hz must be a non-empty 1-D axis",
                {"ndim": int(freqs.ndim), "size": int(freqs.size)},
            )
        finite_all = bool(np.all(np.isfinite(freqs)))
        strictly_increasing = freqs.size <= 1 or bool(np.all(np.diff(freqs) > 0))
        if not finite_all or not strictly_increasing:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "frequencies_hz must be finite and strictly increasing",
            )
        raw = np.asarray(self.data)
        if raw.dtype.kind not in "iufc":
            raise DomainError(ErrorCode.DTYPE_MISMATCH, "raw data must be numeric",
                {"dtype": str(raw.dtype)})
        probe = np.ascontiguousarray(raw, dtype="<c16")
        expected = (len(channel_ids), int(freqs.size))
        if probe.ndim != 2 or probe.shape != expected:
            raise DomainError(
                ErrorCode.SHAPE_MISMATCH,
                "data shape must be channel x frequency matching the header contract",
                {"expected": list(expected), "got": [int(dim) for dim in probe.shape]},
            )
        owned = probe.tobytes(order="C")
        view = np.frombuffer(owned, dtype="<c16").reshape(expected)
        # The metadata's ISSUE-009 hash must equal the canonical hash over the
        # axis supplied at construction.  Senders and config-bound decoders
        # pass the real frozen axis; a placeholder axis therefore fails closed
        # here (see module docstring for the reconciliation flow).
        issue009 = compute_raw_trace_sha256(mission, index, uid, _resolve_channels(channel_ids),
            freqs, view)
        if issue009 != self.metadata.raw_trace_sha256:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "raw_trace_sha256 does not match the canonical payload (fail closed)",
                {"raw_trace_sha256": self.metadata.raw_trace_sha256, "recomputed": issue009},
            )
        object.__setattr__(self, "mission_id", mission)
        object.__setattr__(self, "trace_uid", uid)
        object.__setattr__(self, "device_id", device)
        object.__setattr__(self, "config_sha256", digest)
        object.__setattr__(self, "channel_ids", channel_ids)
        object.__setattr__(self, "frequencies_hz", np.frombuffer(freqs.tobytes(), dtype="<f8"))
        object.__setattr__(
            self, "data", np.frombuffer(owned, dtype="<c16").reshape(expected[0], expected[1])
        )
        start_raw = (
            float(freqs[0]) if self.frequency_start_hz is None else self.frequency_start_hz
        )
        stop_raw = (
            float(freqs[-1]) if self.frequency_stop_hz is None else self.frequency_stop_hz
        )
        points_raw = (
            self.frequency_points if self.frequency_points is not None else int(freqs.size)
        )
        start_f = _require_float_field(start_raw, "frequency_start_hz")
        stop_f = _require_float_field(stop_raw, "frequency_stop_hz")
        points_i = _require_int(points_raw, "frequency_points", minimum=1)
        if points_i != int(freqs.size):
            raise DomainError(
                ErrorCode.SHAPE_MISMATCH,
                "frequency_points must equal len(frequencies_hz)",
                {"frequency_points": points_i, "axis_size": int(freqs.size)},
            )
        if stop_f <= start_f:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "frequency_stop_hz must exceed frequency_start_hz",
                {"frequency_start_hz": start_f, "frequency_stop_hz": stop_f},
            )
        # The sender's real axis must be exactly the frozen linspace implied by
        # its own stamps -- otherwise the stamps would lie about the config.
        implied = np.ascontiguousarray(np.linspace(start_f, stop_f, points_i), dtype="<f8")
        if implied.tobytes(order="C") != freqs.tobytes(order="C"):
            raise DomainError(
                ErrorCode.CONFIG_DIGEST_MISMATCH,
                "frequency axis does not match its declared uniform stamps",
                {
                    "frequency_start_hz": start_f,
                    "frequency_stop_hz": stop_f,
                    "frequency_points": points_i,
                },
            )
        object.__setattr__(self, "frequency_start_hz", start_f)
        object.__setattr__(self, "frequency_stop_hz", stop_f)
        object.__setattr__(self, "frequency_points", points_i)

    def to_header(self) -> dict[str, JsonValue]:
        return {
            "mission_id": self.mission_id.to_json(),
            "trace_uid": self.trace_uid.to_json(),
            "trace_index": self.trace_index,
            "device_id": self.device_id.to_json(),
            "config_sha256": self.config_sha256,
            "hash_domain": TRACE_HASH_DOMAIN_TAG,
            "raw_trace_sha256": self.metadata.raw_trace_sha256,
            "channel_ids": list(self.channel_ids),
            "frequency_count": int(self.frequencies_hz.size),
            "dtype": "complex128",
            "byte_order": "little",
            "shape": [len(self.channel_ids), int(self.frequencies_hz.size)],
            # Uniform-axis stamps: enough to rebuild the frozen linspace and
            # verify the ISSUE-009 hash without re-sending the whole axis.
            "frequency_start_hz": self.frequency_start_hz,
            "frequency_stop_hz": self.frequency_stop_hz,
            "frequency_points": self.frequency_points,
            "metadata": cast("JsonValue", self.metadata.to_dict()),
        }

    def payload_expected_bytes(self) -> int:
        return int(self.data.size) * _COMPLEX128_ITEMSIZE

    def to_payload(self) -> bytes:
        return self.data.tobytes(order="C")


@dataclass(frozen=True, slots=True)
class AckMessage:
    mission_id: MissionId
    trace_uid: TraceUid
    trace_index: int
    raw_trace_sha256: str
    result: AckResult
    received_utc: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "mission_id", _require_mission(self.mission_id))
        object.__setattr__(self, "trace_uid", _require_uid(self.trace_uid))
        object.__setattr__(
            self, "trace_index", _require_int(self.trace_index, "trace_index",
                maximum=_TRACE_INDEX_MAX)
        )
        object.__setattr__(
            self, "raw_trace_sha256", _require_hash(self.raw_trace_sha256, "raw_trace_sha256")
        )
        object.__setattr__(self, "result", _require_enum(self.result, AckResult, "result"))
        object.__setattr__(self, "received_utc", _require_utc(self.received_utc, "received_utc"))

    def to_header(self) -> dict[str, JsonValue]:
        return {
            "mission_id": self.mission_id.to_json(),
            "trace_uid": self.trace_uid.to_json(),
            "trace_index": self.trace_index,
            "raw_trace_sha256": self.raw_trace_sha256,
            "result": self.result.value,
            "received_utc": to_utc_iso(self.received_utc),
        }

    @classmethod
    def from_header(cls, header: Mapping[str, JsonValue]) -> Self:
        for field_name in (
            "mission_id",
            "trace_uid",
            "trace_index",
            "raw_trace_sha256",
            "result",
            "received_utc",
        ):
            if field_name not in header:
                raise missing_field(field_name)
        return cls(
            mission_id=_require_mission(header["mission_id"]),
            trace_uid=_require_uid(header["trace_uid"]),
            trace_index=_require_int(header["trace_index"], "trace_index",
                maximum=_TRACE_INDEX_MAX),
            raw_trace_sha256=_require_hash(header["raw_trace_sha256"], "raw_trace_sha256"),
            result=AckResult.from_value(_require_str(header["result"], "result")),
            received_utc=from_utc_iso(_require_str(header["received_utc"], "received_utc")),
        )


MissingRange = tuple[int, int]


@dataclass(frozen=True, slots=True)
class InventoryMessage:
    mission_id: MissionId
    device_id: DeviceId
    first_index: int
    last_index: int
    count: int
    xor_of_hashes: str
    missing_ranges: tuple[MissingRange, ...]
    conflicts: tuple[MissingRange, ...]
    complete: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "mission_id", _require_mission(self.mission_id))
        object.__setattr__(self, "device_id", _require_device(self.device_id))
        first = _require_int(self.first_index, "first_index")
        last = _require_int(self.last_index, "last_index")
        count = _require_int(self.count, "count")
        if last < first:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "last_index must be >= first_index",
                {"first_index": first, "last_index": last},
            )
        span = last - first + 1
        if count > span:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "count exceeds the declared inclusive index span",
                {"count": count, "span": span},
            )
        object.__setattr__(self, "missing_ranges", _normalize_ranges(self.missing_ranges,
            "missing_ranges"))
        object.__setattr__(self, "conflicts", _normalize_ranges(self.conflicts, "conflicts"))
        object.__setattr__(self, "xor_of_hashes", _require_hash(self.xor_of_hashes,
            "xor_of_hashes"))
        object.__setattr__(self, "complete", _require_bool(self.complete, "complete"))

    def to_header(self) -> dict[str, JsonValue]:
        return {
            "mission_id": self.mission_id.to_json(),
            "device_id": self.device_id.to_json(),
            "first_index": self.first_index,
            "last_index": self.last_index,
            "count": self.count,
            "hash_domain": INVENTORY_HASH_DOMAIN_TAG,
            "xor_of_hashes": self.xor_of_hashes,
            "missing_ranges": [[low, high] for low, high in self.missing_ranges],
            "conflicts": [[low, high] for low, high in self.conflicts],
            "complete": self.complete,
        }

    @classmethod
    def from_header(cls, header: Mapping[str, JsonValue]) -> Self:
        for field_name in (
            "mission_id",
            "device_id",
            "first_index",
            "last_index",
            "count",
            "xor_of_hashes",
            "missing_ranges",
            "conflicts",
            "complete",
        ):
            if field_name not in header:
                raise missing_field(field_name)
        if header.get("hash_domain") != INVENTORY_HASH_DOMAIN_TAG:
            raise FrameError(
                ErrorCode.INVALID_ARGUMENT,
                "unknown inventory hash domain tag",
                {"hash_domain": repr(header.get("hash_domain"))},
            )
        return cls(
            mission_id=_require_mission(header["mission_id"]),
            device_id=_require_device(header["device_id"]),
            first_index=_require_int(header["first_index"], "first_index"),
            last_index=_require_int(header["last_index"], "last_index"),
            count=_require_int(header["count"], "count"),
            xor_of_hashes=_require_hash(header["xor_of_hashes"], "xor_of_hashes"),
            missing_ranges=_ranges_from_json(header["missing_ranges"]),
            conflicts=_ranges_from_json(header["conflicts"]),
            complete=_require_bool(header["complete"], "complete"),
        )


def _normalize_ranges(value: object, field_name: str) -> tuple[MissingRange, ...]:
    items = _require_tuple(value, field_name)
    result: list[MissingRange] = []
    for item in items:
        pair = _require_tuple(item, f"{field_name}[]")
        if len(pair) != 2:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                f"{field_name} entries must be [low, high] pairs",
                {"entry": repr(item)},
            )
        low = _require_int(pair[0], f"{field_name}.low")
        high = _require_int(pair[1], f"{field_name}.high")
        if high < low:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                f"{field_name} range must satisfy low <= high",
                {"range": [low, high]},
            )
        result.append((low, high))
    return tuple(result)


def _ranges_from_json(value: object) -> tuple[MissingRange, ...]:
    items = _require_tuple(value, "ranges")
    result: list[MissingRange] = []
    for item in items:
        pair = _require_tuple(item, "range entry")
        if len(pair) != 2:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "range entries must be [low, high]",
                {"entry": repr(item)},
            )
        result.append((_require_int(pair[0], "range.low"), _require_int(pair[1], "range.high")))
    return tuple(result)


@dataclass(frozen=True, slots=True)
class ErrorInfo:
    """Structured incompatibility detail carried inside an ErrorMessage."""

    observed_major: int
    supported_major: int

    def to_dict(self) -> dict[str, JsonValue]:
        return {"observed_major": self.observed_major, "supported_major": self.supported_major}


@dataclass(frozen=True, slots=True)
class ErrorMessage:
    code: ErrorCode
    message: str
    context: Mapping[str, JsonValue]
    device_id: DeviceId
    occurred_utc: datetime
    mission_id: MissionId | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _require_error_code(self.code, "code"))
        _require_str(self.message, "message")
        if not self.message.isascii():
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "message must be ASCII-safe display text",
                {"message": "non-ascii"},
            )
        _json_safe(self.context, "$.context")
        copied = {key: _copy_json(value) for key, value in self.context.items()}
        object.__setattr__(self, "context", copied)
        object.__setattr__(self, "device_id", _require_device(self.device_id))
        object.__setattr__(self, "occurred_utc", _require_utc(self.occurred_utc, "occurred_utc"))
        if self.mission_id is not None:
            object.__setattr__(self, "mission_id", _require_mission(self.mission_id))

    def to_header(self) -> dict[str, JsonValue]:
        return {
            "code": self.code.value,
            "message": self.message,
            "context": cast("JsonValue", dict(self.context)),
            "device_id": self.device_id.to_json(),
            "occurred_utc": to_utc_iso(self.occurred_utc),
            "mission_id": self.mission_id.to_json() if self.mission_id else None,
        }

    @classmethod
    def from_header(cls, header: Mapping[str, JsonValue]) -> Self:
        for field_name in ("code", "message", "context", "device_id"):
            if field_name not in header:
                raise missing_field(field_name)
        context_raw = header["context"]
        if not isinstance(context_raw, dict):
            raise FrameError(ErrorCode.INVALID_ARGUMENT, "context must be an object",
                {"field": "context"})
        mission_raw = header.get("mission_id")
        return cls(
            code=_require_error_code(header["code"], "code"),
            message=_require_str(header["message"], "message"),
            context=dict(context_raw),
            device_id=_require_device(header["device_id"]),
            occurred_utc=from_utc_iso(_require_str(header["occurred_utc"], "occurred_utc")),
            mission_id=None if mission_raw is None else _require_mission(mission_raw),
        )


Message = (
    HelloMessage
    | StatusMessage
    | CommandMessage
    | MissionMessage
    | TraceMessage
    | AckMessage
    | InventoryMessage
    | ErrorMessage
)

_MESSAGE_TYPES: Final[Mapping[MessageKind, type]] = {
    MessageKind.HELLO: HelloMessage,
    MessageKind.STATUS: StatusMessage,
    MessageKind.COMMAND: CommandMessage,
    MessageKind.MISSION: MissionMessage,
    MessageKind.TRACE: TraceMessage,
    MessageKind.ACK: AckMessage,
    MessageKind.INVENTORY: InventoryMessage,
    MessageKind.ERROR: ErrorMessage,
}

_HeaderCapable = (
    HelloMessage
    | StatusMessage
    | CommandMessage
    | MissionMessage
    | TraceMessage
    | AckMessage
    | InventoryMessage
    | ErrorMessage
)


def kind_of(message: object) -> MessageKind:
    for kind, cls in _MESSAGE_TYPES.items():
        if isinstance(message, cls):
            return kind
    raise DomainError(
        ErrorCode.INVALID_ARGUMENT,
        "not a protocol v1 message",
        {"type": type(message).__name__},
    )


# ---------------------------------------------------------------------------
# capability policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CapabilityPolicy:
    """How this endpoint negotiates minor versions (ADR-0006).

    Defaults accept any minor of the current major (additive semantics);
    narrow the window to hard-reject unknown minors.
    """

    minor_low: int = 0
    minor_high: int = 255

    def __post_init__(self) -> None:
        object.__setattr__(self, "minor_low", _require_int(self.minor_low, "minor_low",
            maximum=255))
        object.__setattr__(self, "minor_high", _require_int(self.minor_high, "minor_high",
            maximum=255))
        if self.minor_high < self.minor_low:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "minor_high must be >= minor_low",
                {"minor_low": self.minor_low, "minor_high": self.minor_high},
            )

    def accepts_minor(self, minor: int) -> bool:
        return self.minor_low <= minor <= self.minor_high


# ---------------------------------------------------------------------------
# encoding
# ---------------------------------------------------------------------------


def build_frame_bytes(
    header: Mapping[str, JsonValue],
    *,
    kind: MessageKind,
    payload: bytes = b"",
    major: int = PROTOCOL_MAJOR,
    minor: int = PROTOCOL_MINOR,
) -> bytes:
    header_bytes = canonical_header_bytes(header)
    if len(header_bytes) > MAX_HEADER_BYTES:
        raise FrameError(
            ErrorCode.OUT_OF_RANGE,
            "canonical header exceeds MAX_HEADER_BYTES",
            {"header_length": len(header_bytes), "max": MAX_HEADER_BYTES},
        )
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise FrameError(
            ErrorCode.OUT_OF_RANGE,
            "payload exceeds MAX_PAYLOAD_BYTES",
            {"payload_length": len(payload), "max": MAX_PAYLOAD_BYTES},
        )
    return b"".join(
        [
            MAGIC,
            bytes([major, minor]),
            int(kind).to_bytes(2, "big"),
            (0).to_bytes(2, "big"),
            len(header_bytes).to_bytes(4, "big"),
            len(payload).to_bytes(4, "big"),
            header_bytes,
            payload,
        ]
    )


def encode_message(message: _HeaderCapable, *, minor: int = PROTOCOL_MINOR) -> bytes:
    """Encode one immutable message into a protocol v1 frame."""
    kind = kind_of(message)
    header: dict[str, JsonValue] = {
        "spec_version": HEADER_SPEC_VERSION,
        "major": PROTOCOL_MAJOR,
        "minor": minor,
        "type": kind.wire_name,
        "flags": 0,
    }
    for key, value in message.to_header().items():
        if key in header:
            raise FrameError(
                ErrorCode.INVALID_ARGUMENT,
                "message header may not shadow envelope fields",
                {"field": key},
            )
        header[key] = value
    payload = (
        cast("TraceMessage", message).to_payload() if kind is MessageKind.TRACE else b""
    )
    return build_frame_bytes(header, kind=kind, payload=payload, minor=minor)


def encode_frame(message: _HeaderCapable, *, minor: int = PROTOCOL_MINOR) -> bytes:
    """Explicit-minor encoder (capability-negotiation tests / alternate minors)."""
    return encode_message(message, minor=minor)


# ---------------------------------------------------------------------------
# decoding
# ---------------------------------------------------------------------------


def _check_prefix(
    major: int, minor: int, kind_code: int, flags: int, policy: CapabilityPolicy
) -> MessageKind:
    if major != PROTOCOL_MAJOR:
        raise FrameError(
            ErrorCode.UNSUPPORTED_PROTOCOL_VERSION,
            "incompatible protocol major version",
            {"major": major, "supported_major": PROTOCOL_MAJOR},
        )
    if flags != 0:
        raise FrameError(
            ErrorCode.INVALID_ARGUMENT,
            "flags must be zero in protocol v1",
            {"flags": flags},
        )
    kind = MessageKind.from_wire(kind_code)
    if not policy.accepts_minor(minor):
        raise FrameError(
            ErrorCode.UNSUPPORTED_PROTOCOL_VERSION,
            "minor version outside capability policy",
            {"minor": minor, "minor_low": policy.minor_low, "minor_high": policy.minor_high},
        )
    return kind


def _finish_decode(
    major: int,
    minor: int,
    kind: MessageKind,
    flags: int,
    header: Mapping[str, JsonValue],
    payload: bytes,
    policy: CapabilityPolicy,
) -> DecodedFrame:
    for field_name, expected in (
        ("spec_version", HEADER_SPEC_VERSION),
        ("major", major),
        ("minor", minor),
        ("type", kind.wire_name),
        ("flags", 0),
    ):
        actual = header.get(field_name)
        if isinstance(actual, bool) and not isinstance(expected, bool):
            raise FrameError(
                ErrorCode.INVALID_ARGUMENT,
                "header envelope field must not be a bool where an int is expected",
                {
                    "field": field_name,
                    "header_value": repr(actual),
                    "prefix_value": repr(expected),
                },
            )
        if actual != expected:
            raise FrameError(
                ErrorCode.INVALID_ARGUMENT,
                "header envelope field mismatches binary prefix",
                {"field": field_name, "header_value": repr(actual), "prefix_value": repr(expected)},
            )
    if kind is not MessageKind.TRACE and payload:
        raise FrameError(
            ErrorCode.INVALID_ARGUMENT,
            "only trace frames may carry a payload",
            {"type": kind.wire_name, "payload_length": len(payload)},
        )
    message = _decode_message(kind, header, payload)
    envelope = ProtocolEnvelope(
        major=major,
        minor=minor,
        kind=kind,
        flags=flags,
        header=dict(header),
        header_bytes=canonical_header_bytes(header),
        payload=payload,
    )
    return DecodedFrame(envelope=envelope, message=message)


_ENVELOPE_KEYS: Final[frozenset[str]] = frozenset(
    {"spec_version", "major", "minor", "type", "flags"}
)

_EXACT_KEYS_CACHE: dict[MessageKind, frozenset[str]] = {}


def _sample_message_for(kind: MessageKind) -> Message:
    """One deterministic instance per kind (golden trace + minimal others)."""
    mission = MissionId(GOLDEN_MISSION_ID)
    device = DeviceId(GOLDEN_DEVICE_ID)
    uid = TraceUid(GOLDEN_TRACE_UID)
    t0 = golden_created_utc()
    if kind is MessageKind.HELLO:
        return HelloMessage(device, "s", 0, (), None, None)
    if kind is MessageKind.STATUS:
        return StatusMessage(device, mission, 0, AckState.IDLE, False, 0, None)
    if kind is MessageKind.COMMAND:
        return CommandMessage(CommandId.new(), "op", t0, None, None)
    if kind is MessageKind.MISSION:
        return MissionMessage(golden_mission_config())
    if kind is MessageKind.TRACE:
        golden = golden_messages()["trace"]
        assert isinstance(golden, TraceMessage)
        return golden
    if kind is MessageKind.ACK:
        return AckMessage(mission, uid, 0, "0" * 64, AckResult.PERSISTED, t0)
    if kind is MessageKind.INVENTORY:
        return InventoryMessage(mission, device, 0, 0, 0, "0" * 64, (), (), True)
    return ErrorMessage(ErrorCode.INVALID_ARGUMENT, "m", {}, device, t0, None)


def _exact_keys_for(kind: MessageKind) -> frozenset[str]:
    """Frozen key set per kind, derived once from canonical sample instances."""
    cached = _EXACT_KEYS_CACHE.get(kind)
    if cached is not None:
        return cached
    allowed = _ENVELOPE_KEYS | frozenset(_sample_message_for(kind).to_header().keys())
    _EXACT_KEYS_CACHE[kind] = allowed
    return allowed


def _check_exact_keys(kind: MessageKind, header: Mapping[str, JsonValue]) -> None:
    allowed = _exact_keys_for(kind)
    unexpected = sorted(key for key in header if key not in allowed)
    missing = sorted(key for key in allowed if key not in header)
    if unexpected or missing:
        raise FrameError(
            ErrorCode.INVALID_ARGUMENT,
            "header key set does not match the frozen message contract",
            {
                "type": kind.wire_name,
                "unexpected": list(unexpected),
                "missing": list(missing),
            },
        )


def _decode_message(kind: MessageKind, header: Mapping[str, JsonValue], payload: bytes) -> Message:
    _check_exact_keys(kind, header)
    if kind is MessageKind.TRACE:
        return _decode_trace(header, payload)
    cls = _MESSAGE_TYPES[kind]
    factory = cls.from_header  # type: ignore[attr-defined]
    try:
        return cast("Message", factory(header))
    except DomainError as exc:
        raise FrameError(exc.code, exc.message, dict(exc.context)) from None




@dataclass(frozen=True, slots=True)
class _TraceContext:
    mission_id: MissionId
    trace_uid: TraceUid
    trace_index: int
    device_id: DeviceId
    config_sha256: str
    raw_trace_sha256: str
    channel_ids: tuple[str, ...]
    frequency_count: int
    channels_n: int
    metadata: TraceMetadata


def _decode_trace(header: Mapping[str, JsonValue], payload: bytes) -> TraceMessage:
    # P2-1 (ISSUE_037_REVIEW_REPORT section 3): every validation failure on the
    # trace path -- including DomainError raised by the shared _require_* field
    # validators inside _trace_context/_recover_axis -- is converted to
    # FrameError exactly like the non-trace branch, so feed()'s poisoned-on-
    # FrameError contract (ADR-0006) can never be bypassed.
    # One conversion boundary wraps the WHOLE trace validation path below.
    try:
        return _decode_trace_inner(header, payload)
    except DomainError as exc:
        raise FrameError(exc.code, exc.message, dict(exc.context)) from None


def _decode_trace_inner(header: Mapping[str, JsonValue], payload: bytes) -> TraceMessage:
    context = _trace_context(header)
    channels_n = context.channels_n
    frequency_count = context.frequency_count
    expected_bytes = channels_n * frequency_count * _COMPLEX128_ITEMSIZE
    if len(payload) != expected_bytes:
        raise FrameError(
            ErrorCode.SHAPE_MISMATCH,
            "trace payload length does not match the declared shape",
            {"payload_length": len(payload), "expected": expected_bytes},
        )
    array = np.frombuffer(payload, dtype="<c16").reshape(channels_n, frequency_count)
    # Integrity authority is the ISSUE-009 raw_trace_sha256.  Its canonical
    # input includes the frozen frequency axis, which is deliberately NOT
    # re-sent per trace (TRANSPORT_PROTOCOL section 5): the receiver recovers
    # it deterministically from the header's uniform-axis stamps plus the
    # registered channel contract and recomputes the exact hash here.  When
    # the full mission config is bound, decode_trace_with_config additionally
    # verifies digest/channel agreement against the authoritative config.
    try:
        axis = _recover_axis(header, context)
    except DomainError as exc:
        raise FrameError(exc.code, exc.message, dict(exc.context)) from None
    # Axis stamps are self-consistent with the ISSUE-009 hash below; they must
    # also agree with the frozen mission contract whenever this endpoint has
    # it registered (config digests are published via MissionMessage).  The
    # declared uniform-axis parameters are checked against every registered
    # config whose digest matches this frame's config_sha256.
    _check_stamps_against_registered_config(context.config_sha256, axis, context)
    try:
        return TraceMessage(
            mission_id=context.mission_id,
            trace_uid=context.trace_uid,
            trace_index=context.trace_index,
            device_id=context.device_id,
            config_sha256=context.config_sha256,
            metadata=context.metadata,
            frequencies_hz=axis,
            data=array,
            channel_ids=context.channel_ids,
        )
    except DomainError as exc:
        raise FrameError(exc.code, exc.message, dict(exc.context)) from None


def _recover_axis(header: Mapping[str, JsonValue], context: _TraceContext) -> np.ndarray:
    """Rebuild the sender's frozen axis from the header axis stamps."""
    start = header.get("frequency_start_hz")
    stop = header.get("frequency_stop_hz")
    points = header.get("frequency_points")
    if start is None or stop is None or points is None:
        raise FrameError(
            ErrorCode.INVALID_ARGUMENT,
            "trace header must carry the uniform axis stamps"
            " (frequency_start_hz/frequency_stop_hz/frequency_points)",
            {"field": "frequency_axis_stamps"},
        )
    start_f = _require_float_field(start, "frequency_start_hz")
    stop_f = _require_float_field(stop, "frequency_stop_hz")
    points_i = _require_int(points, "frequency_points", minimum=1)
    if points_i != context.frequency_count:
        raise FrameError(
            ErrorCode.SHAPE_MISMATCH,
            "frequency_points disagrees with frequency_count",
            {"frequency_points": points_i, "frequency_count": context.frequency_count},
        )
    if not math.isfinite(start_f) or not math.isfinite(stop_f) or stop_f <= start_f:
        raise FrameError(
            ErrorCode.INVALID_ARGUMENT,
            "axis stamps must be finite with stop > start",
            {"frequency_start_hz": start_f, "frequency_stop_hz": stop_f},
        )
    axis = np.linspace(start_f, stop_f, points_i)
    return np.ascontiguousarray(axis, dtype="<f8")


_MISSION_CONFIG_REGISTRY: dict[str, MissionConfig] = {}


def register_mission_config(config: MissionConfig) -> None:
    """Bind a mission config under its canonical digest (idempotent).

    Endpoints that hold the authoritative config call this so plain
    :class: decoding can cross-check the trace header's uniform
    axis stamps without a second explicit decode pass.  Conflicting digests
    fail closed.
    """
    if not isinstance(config, MissionConfig):
        raise TypeError("config must be a MissionConfig")
    existing = _MISSION_CONFIG_REGISTRY.get(config.config_sha256)
    if existing is not None and existing != config:
        raise DomainError(
            ErrorCode.ID_CONFLICT,
            "config digest already registered with different content",
            {"config_sha256": config.config_sha256},
        )
    _MISSION_CONFIG_REGISTRY[config.config_sha256] = config
    register_trace_channels(config.channels)


def _check_stamps_against_registered_config(
    config_sha256: str, axis: np.ndarray, context: _TraceContext
) -> None:
    config = _MISSION_CONFIG_REGISTRY.get(config_sha256)
    if config is None:
        return  # no bound contract yet: stamps + ISSUE-009 binding still verified
    if (
        float(config.frequency_start_hz) != float(axis[0])
        or float(config.frequency_stop_hz) != float(axis[-1])
        or int(config.frequency_points) != int(axis.size)
    ):
        raise FrameError(
            ErrorCode.CONFIG_DIGEST_MISMATCH,
            "trace axis stamps disagree with the registered mission config",
            {
                "config_sha256": config_sha256,
                "header_axis": [float(axis[0]), float(axis[-1]), int(axis.size)],
                "config_axis": [
                    float(config.frequency_start_hz),
                    float(config.frequency_stop_hz),
                    int(config.frequency_points),
                ],
            },
        )
    channel_ids = context.channel_ids
    config_channel_ids = tuple(channel.channel_id for channel in config.channels)
    if config_channel_ids != channel_ids:
        raise FrameError(
            ErrorCode.CHANNEL_CONTRACT_MISMATCH,
            "trace channel contract disagrees with the registered mission config",
            {"header": list(channel_ids), "config": list(config_channel_ids)},
        )


def _require_float_field(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT, f"{field_name} must be numeric", {field_name: repr(value)}
        )
    result = float(value)
    if not math.isfinite(result):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT, f"{field_name} must be finite", {field_name: result}
        )
    return result


def _trace_context(header: Mapping[str, JsonValue]) -> _TraceContext:
    for field_name in (
        "mission_id",
        "trace_uid",
        "trace_index",
        "device_id",
        "config_sha256",
        "raw_trace_sha256",
        "channel_ids",
        "frequency_count",
        "dtype",
        "byte_order",
        "shape",
        "metadata",
    ):
        if field_name not in header:
            raise missing_field(field_name)
    if header.get("hash_domain") != TRACE_HASH_DOMAIN_TAG:
        raise FrameError(
            ErrorCode.INVALID_ARGUMENT,
            "unknown trace hash domain tag",
            {"hash_domain": repr(header.get("hash_domain"))},
        )
    dtype_raw = _require_str(header["dtype"], "dtype")
    if dtype_raw != "complex128":
        raise FrameError(
            ErrorCode.DTYPE_MISMATCH,
            "v1 trace payload dtype must be complex128",
            {"dtype": dtype_raw},
        )
    byte_order = _require_str(header["byte_order"], "byte_order")
    if byte_order != "little":
        raise FrameError(
            ErrorCode.DTYPE_MISMATCH,
            "v1 trace payload byte order must be little",
            {"byte_order": byte_order},
        )
    shape_raw = header["shape"]
    if not isinstance(shape_raw, list) or len(shape_raw) != 2:
        raise FrameError(ErrorCode.SHAPE_MISMATCH, "shape must be [channel, frequency]",
            {"field": "shape"})
    channels_n = _require_int(shape_raw[0], "shape[0]", minimum=1)
    freqs_n = _require_int(shape_raw[1], "shape[1]", minimum=1)
    channel_ids = tuple(
        _require_str(item, "channel_ids[]") for item in _require_tuple(header["channel_ids"],
            "channel_ids")
    )
    if channels_n != len(channel_ids):
        raise FrameError(
            ErrorCode.SHAPE_MISMATCH,
            "shape channel count must equal len(channel_ids)",
            {"shape": shape_raw, "channel_ids": len(channel_ids)},
        )
    frequency_count = _require_int(header["frequency_count"], "frequency_count", minimum=1)
    if frequency_count != freqs_n:
        raise FrameError(
            ErrorCode.SHAPE_MISMATCH,
            "frequency_count disagrees with shape",
            {"frequency_count": frequency_count, "shape": shape_raw},
        )
    meta_raw = header["metadata"]
    if not isinstance(meta_raw, dict):
        raise FrameError(ErrorCode.INVALID_ARGUMENT, "metadata must be an object",
            {"field": "metadata"})
    try:
        metadata = TraceMetadata.from_dict(dict(meta_raw))
    except (DomainError, ValueError, TypeError, KeyError) as exc:
        raise FrameError(
            ErrorCode.INVALID_ARGUMENT,
            "trace metadata failed strict decode",
            {"reason": str(exc)[:200]},
        ) from None
    return _TraceContext(
        mission_id=_require_mission(header["mission_id"]),
        trace_uid=_require_uid(header["trace_uid"]),
        trace_index=_require_int(header["trace_index"], "trace_index", maximum=_TRACE_INDEX_MAX),
        device_id=_require_device(header["device_id"]),
        config_sha256=_require_hash(header["config_sha256"], "config_sha256"),
        raw_trace_sha256=_require_hash(header["raw_trace_sha256"], "raw_trace_sha256"),
        channel_ids=channel_ids,
        frequency_count=frequency_count,
        channels_n=channels_n,
        metadata=metadata,
    )


def decode_trace_with_config(
    header: Mapping[str, JsonValue], payload: bytes, config: MissionConfig
) -> TraceMessage:
    """Exact decode once the mission config (real axis) is bound.

    Adds the full ISSUE-009 cross-validation: the metadata hash must equal
    ``compute_raw_trace_sha256`` over the config's frozen frequency axis, and
    the header config digest must equal the config's canonical digest.
    """
    context = _trace_context(header)
    if context.config_sha256 != config.config_sha256:
        raise FrameError(
            ErrorCode.CONFIG_DIGEST_MISMATCH,
            "trace config_sha256 does not match the bound mission config",
            {"header_digest": context.config_sha256, "config_digest": config.config_sha256},
        )
    channels_n = context.channels_n
    frequency_count = context.frequency_count
    config_channel_ids = tuple(channel.channel_id for channel in config.channels)
    if len(config_channel_ids) != channels_n or config_channel_ids != context.channel_ids:
        raise FrameError(
            ErrorCode.CHANNEL_CONTRACT_MISMATCH,
            "trace channel contract disagrees with the mission config",
            {"header": list(context.channel_ids), "config": list(config_channel_ids)},
        )
    if config.frequency_points != frequency_count:
        raise FrameError(
            ErrorCode.SHAPE_MISMATCH,
            "trace frequency_count disagrees with the mission config",
            {"frequency_count": frequency_count, "config_points": config.frequency_points},
        )
    expected_bytes = channels_n * frequency_count * _COMPLEX128_ITEMSIZE
    if len(payload) != expected_bytes:
        raise FrameError(
            ErrorCode.SHAPE_MISMATCH,
            "trace payload length does not match the declared shape",
            {"payload_length": len(payload), "expected": expected_bytes},
        )
    array = np.frombuffer(payload, dtype="<c16").reshape(channels_n, frequency_count)
    axis = config.frequency_axis_hz
    metadata = context.metadata
    assert isinstance(metadata, TraceMetadata)
    if metadata.raw_trace_sha256 is not None:
        issue009 = compute_raw_trace_sha256(
            context.mission_id,
            context.trace_index,
            context.trace_uid,
            config.channels,
            axis,
            array,
        )
        if issue009 != metadata.raw_trace_sha256:
            raise FrameError(
                ErrorCode.INVALID_ARGUMENT,
                "ISSUE-009 raw hash mismatch against the bound config axis",
                {
                    "raw_trace_sha256": metadata.raw_trace_sha256,
                    "recomputed": issue009,
                },
            )
    try:
        return TraceMessage(
            mission_id=context.mission_id,
            trace_uid=context.trace_uid,
            trace_index=context.trace_index,
            device_id=context.device_id,
            config_sha256=context.config_sha256,
            metadata=metadata,
            frequencies_hz=axis,
            data=array,
            channel_ids=context.channel_ids,
            frequency_start_hz=float(config.frequency_start_hz),
            frequency_stop_hz=float(config.frequency_stop_hz),
            frequency_points=int(config.frequency_points),
        )
    except DomainError as exc:
        raise FrameError(exc.code, exc.message, dict(exc.context)) from None


# ---------------------------------------------------------------------------
# envelope containers
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProtocolEnvelope:
    major: int
    minor: int
    kind: MessageKind
    flags: int
    header: Mapping[str, JsonValue]
    header_bytes: bytes
    payload: bytes


@dataclass(frozen=True, slots=True)
class DecodedFrame:
    envelope: ProtocolEnvelope
    message: Message


def decode_envelope(frame: bytes) -> ProtocolEnvelope:
    """Decode one complete frame into its validated envelope."""
    return _decode_bytes(frame, CapabilityPolicy()).envelope


def _decode_bytes(frame: bytes, policy: CapabilityPolicy) -> DecodedFrame:
    if len(frame) < PREFIX_LENGTH:
        raise FrameError(
            ErrorCode.INVALID_ARGUMENT,
            "frame shorter than fixed prefix",
            {"length": len(frame), "prefix": PREFIX_LENGTH},
        )
    if frame[:4] != MAGIC:
        raise FrameError(ErrorCode.INVALID_ARGUMENT, "bad magic", {"magic": frame[:4].hex()})
    major, minor = frame[4], frame[5]
    kind_code = int.from_bytes(frame[6:8], "big")
    flags = int.from_bytes(frame[8:10], "big")
    header_len = int.from_bytes(frame[10:14], "big")
    payload_len = int.from_bytes(frame[14:18], "big")
    if header_len > MAX_HEADER_BYTES:
        raise FrameError(
            ErrorCode.OUT_OF_RANGE,
            "declared header_length exceeds the limit",
            {"field": "header_length", "value": header_len, "max": MAX_HEADER_BYTES},
        )
    if payload_len > MAX_PAYLOAD_BYTES:
        raise FrameError(
            ErrorCode.OUT_OF_RANGE,
            "declared payload_length exceeds the limit",
            {"field": "payload_length", "value": payload_len, "max": MAX_PAYLOAD_BYTES},
        )
    total = PREFIX_LENGTH + header_len + payload_len
    if len(frame) < total:
        raise FrameError(
            ErrorCode.INVALID_ARGUMENT,
            "truncated frame body",
            {"need": total, "have": len(frame)},
        )
    if len(frame) > total:
        raise FrameError(
            ErrorCode.INVALID_ARGUMENT,
            "extra bytes after frame end",
            {"frame_end": total, "have": len(frame)},
        )
    kind = _check_prefix(major, minor, kind_code, flags, policy)
    header = _parse_canonical_header(frame[PREFIX_LENGTH : PREFIX_LENGTH + header_len])
    payload = frame[PREFIX_LENGTH + header_len : total]
    return _finish_decode(major, minor, kind, flags, header, payload, policy)


# ---------------------------------------------------------------------------
# incremental parser
# ---------------------------------------------------------------------------


class FrameParser:
    """Incremental TCP-stream framing decoder (no sockets, no threads).

    Feed arbitrary chunks; completed, fully validated frames come out in
    order.  Any structural failure raises ``FrameError`` and poisons the
    parser until :meth:`reset`, because guessing frame boundaries inside a
    corrupt stream is forbidden (ADR-0006).
    """

    __slots__ = ("_buffer", "_poisoned", "_policy")

    def __init__(self, policy: CapabilityPolicy | None = None) -> None:
        self._buffer: bytearray = bytearray()
        self._policy: CapabilityPolicy = policy if policy is not None else CapabilityPolicy()
        self._poisoned: bool = False

    @property
    def poisoned(self) -> bool:
        return self._poisoned

    @property
    def pending_bytes(self) -> int:
        return len(self._buffer)

    def reset(self) -> None:
        self._buffer = bytearray()
        self._poisoned = False

    def feed(self, data: bytes | bytearray | memoryview) -> list[DecodedFrame]:
        if self._poisoned:
            raise FrameError(
                ErrorCode.INVALID_ARGUMENT,
                "parser is poisoned; call reset() before further use",
                {"reason": "poisoned"},
            )
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("feed expects bytes-like input")
        self._buffer.extend(data)
        produced: list[DecodedFrame] = []
        while True:
            available = len(self._buffer)
            if available < PREFIX_LENGTH:
                return produced
            if self._buffer[:4] != MAGIC:
                self._poisoned = True
                raise FrameError(
                    ErrorCode.INVALID_ARGUMENT,
                    "bad magic",
                    {"magic": bytes(self._buffer[:4]).hex()},
                )
            header_len = int.from_bytes(self._buffer[10:14], "big")
            payload_len = int.from_bytes(self._buffer[14:18], "big")
            # validate declared lengths BEFORE buffering their bodies
            if header_len > MAX_HEADER_BYTES:
                self._poisoned = True
                raise FrameError(
                    ErrorCode.OUT_OF_RANGE,
                    "declared header_length exceeds the limit",
                    {"field": "header_length", "value": header_len, "max": MAX_HEADER_BYTES},
                )
            if payload_len > MAX_PAYLOAD_BYTES:
                self._poisoned = True
                raise FrameError(
                    ErrorCode.OUT_OF_RANGE,
                    "declared payload_length exceeds the limit",
                    {"field": "payload_length", "value": payload_len, "max": MAX_PAYLOAD_BYTES},
                )
            total = PREFIX_LENGTH + header_len + payload_len
            if available < total:
                return produced
            frame = bytes(self._buffer[:total])
            try:
                decoded = _decode_bytes(frame, self._policy)
            except FrameError:
                self._poisoned = True
                raise
            del self._buffer[:total]
            produced.append(decoded)


# ---------------------------------------------------------------------------
# golden frames (ADR-0006 sample set)
# ---------------------------------------------------------------------------

GOLDEN_MISSION_ID: Final[str] = "11111111-1111-4111-8111-111111111111"
GOLDEN_DEVICE_ID: Final[str] = "22222222-2222-4222-8222-222222222222"
GOLDEN_TRACE_UID: Final[str] = "33333333-3333-4333-8333-333333333333"
GOLDEN_SESSION_ID: Final[str] = "55555555-5555-4555-8555-555555555555"
GOLDEN_CREATED_ISO: Final[str] = "2026-09-05T01:02:03.456789Z"
GOLDEN_SOFTWARE_VERSION: Final[str] = "0.1.0.dev0"
GOLDEN_NOTE: Final[str] = "issue-037 contract fixture"


@dataclass(frozen=True, slots=True)
class GoldenFrame:
    name: str
    frame_hex: str


def _golden_channels() -> tuple[ChannelSpec, ChannelSpec]:
    from uav_gpr.core.enums import LogicalPolarization, SParameter

    hh = ChannelSpec(
        channel_id="hh_s11",
        logical_polarization=LogicalPolarization.HH,
        s_parameter=SParameter.S11,
        display_name="HH S11",
    )
    vv = ChannelSpec(
        channel_id="vv_s22",
        logical_polarization=LogicalPolarization.VV,
        s_parameter=SParameter.S22,
        display_name="VV S22",
    )
    return hh, vv


def golden_created_utc() -> datetime:
    return from_utc_iso(GOLDEN_CREATED_ISO)


def golden_frequency_axis() -> np.ndarray:
    return np.linspace(1.0e9, 2.0e9, 4)


def golden_raw_data() -> np.ndarray:
    return np.array(
        [[complex(i, -i) for i in range(4)], [complex(-i, i) for i in range(4)]],
        dtype=np.complex128,
    )


def golden_mission_config() -> MissionConfig:
    from uav_gpr.core.enums import AcquisitionMode, GnssNoFixPolicy

    hh, vv = _golden_channels()
    return MissionConfig(
        frequency_start_hz=1.0e9,
        frequency_stop_hz=2.0e9,
        frequency_points=4,
        if_bw_hz=1_000.0,
        power_dbm=-10.0,
        channels=(hh, vv),
        acquisition_mode=AcquisitionMode.FIXED_COUNT,
        planned_trace_count=10,
        target_interval_s=0.25,
        gnss_max_age_s=2.0,
        gnss_no_fix_policy=GnssNoFixPolicy.RECORD_WITHOUT_POSITION,
        calibration_profile_id=None,
        apply_calibration=False,
        background_reference_id=None,
        apply_background=False,
        created_utc=golden_created_utc(),
        software_version=GOLDEN_SOFTWARE_VERSION,
        note=GOLDEN_NOTE,
    )


def golden_messages() -> dict[str, Message]:
    """The exact message objects behind GOLDEN_FRAMES (shared with tests)."""
    mission = MissionId(GOLDEN_MISSION_ID)
    device = DeviceId(GOLDEN_DEVICE_ID)
    uid = TraceUid(GOLDEN_TRACE_UID)
    t0 = golden_created_utc()
    freqs = golden_frequency_axis()
    raw = golden_raw_data()
    hh, vv = _golden_channels()
    # NOTE: golden_messages() is a PURE builder; channel-contract registration
    # (an explicit process-global side effect) lives in
    # _register_golden_contracts(), invoked once for the sample set.  Importing
    # this module therefore still seeds the registry with the two fixture-only
    # channel ids -- see the module docstring's "Global state" section.
    config = golden_mission_config()
    # Golden traces carry the authoritative ISSUE-009 hash in metadata; the
    # decoder recomputes it exactly from the header's uniform-axis stamps.
    digest = compute_raw_trace_sha256(mission, 1, uid, (hh, vv), freqs, raw)
    metadata = TraceMetadata(
        mission_id=mission,
        trace_index=1,
        trace_uid=uid,
        device_id=device,
        sweep_started_utc=t0,
        sweep_midpoint_utc=t0,
        sweep_finished_utc=t0,
        sweep_started_monotonic_ns=MonotonicNs(1_000),
        sweep_midpoint_monotonic_ns=MonotonicNs(1_250),
        sweep_finished_monotonic_ns=MonotonicNs(1_500),
        target_interval_s=0.25,
        actual_interval_s=0.25,
        schedule_error_s=0.0,
        connection_generation=2,
        raw_trace_sha256=digest,
        gnss_match=None,
        quality_status=TraceQualityStatus.DEGRADED,
        quality_reasons=(TraceQualityReason.GNSS_MISSING,),
    )
    hello = HelloMessage(device, GOLDEN_SOFTWARE_VERSION, 2, ("gnss", "osl"), GOLDEN_SESSION_ID,
        None)
    err = ErrorMessage(
        ErrorCode.UNSUPPORTED_PROTOCOL_VERSION,
        "major mismatch",
        {"observed_major": 2},
        device,
        t0,
        None,
    )
    trace = TraceMessage(
        mission_id=mission,
        trace_uid=uid,
        trace_index=1,
        device_id=device,
        config_sha256=config.config_sha256,
        metadata=metadata,
        frequencies_hz=freqs,
        data=raw,
        channel_ids=("hh_s11", "vv_s22"),
    )
    ack = AckMessage(mission, uid, 1, "a" * 64, AckResult.CONFLICT, t0)
    return {"hello": hello, "error": err, "trace": trace, "ack": ack}



def _register_golden_contracts() -> None:
    """Register the golden fixture channel contract (explicit side effect)."""
    register_trace_channels(_golden_channels())


def _build_golden_frames() -> tuple[GoldenFrame, ...]:
    return tuple(
        GoldenFrame(name, encode_message(message).hex())
        for name, message in golden_messages().items()
    )


_register_golden_contracts()
GOLDEN_FRAMES: Final[tuple[GoldenFrame, ...]] = _build_golden_frames()

__all__ = [
    "FLAG_RESERVED_MASK",
    "GOLDEN_CREATED_ISO",
    "GOLDEN_DEVICE_ID",
    "GOLDEN_FRAMES",
    "GOLDEN_MISSION_ID",
    "GOLDEN_NOTE",
    "GOLDEN_SESSION_ID",
    "GOLDEN_SOFTWARE_VERSION",
    "GOLDEN_TRACE_UID",
    "HEADER_SPEC_VERSION",
    "INVENTORY_HASH_DOMAIN_TAG",
    "MAGIC",
    "MAX_HEADER_BYTES",
    "MAX_PAYLOAD_BYTES",
    "PREFIX_LENGTH",
    "PROTOCOL_MAJOR",
    "PROTOCOL_MINOR",
    "TRACE_HASH_DOMAIN_TAG",
    "AckMessage",
    "AckResult",
    "AckState",
    "CapabilityPolicy",
    "CommandMessage",
    "DecodedFrame",
    "ErrorInfo",
    "ErrorMessage",
    "FrameError",
    "FrameParser",
    "HelloMessage",
    "InventoryMessage",
    "Message",
    "MessageKind",
    "MissionMessage",
    "ProtocolEnvelope",
    "StatusMessage",
    "TraceMessage",
    "build_frame_bytes",
    "canonical_header_bytes",
    "decode_envelope",
    "decode_trace_with_config",
    "encode_frame",
    "encode_message",
    "golden_frequency_axis",
    "golden_mission_config",
    "golden_raw_data",
    "kind_of",
    "missing_field",
]
