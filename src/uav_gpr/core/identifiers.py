"""Stable UUID-backed identifiers for UAV-GPR domain objects.

Every identifier is a canonical, lowercase, hyphenated UUID string.  Parsing
is strict: non-canonical strings (uppercase, missing hyphens, braces, bad hex)
are rejected.  JSON round-tripping is lossless through ``to_json()`` /
``from_json()``.
"""

from __future__ import annotations

import re
import uuid
from typing import Self

_CANONICAL_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def _require_canonical_uuid(value: str | uuid.UUID) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    if not isinstance(value, str):
        raise TypeError(
            f"identifier must be a str or uuid.UUID, got {type(value).__name__}"
        )
    if _CANONICAL_UUID_RE.fullmatch(value) is None:
        raise ValueError(f"non-canonical UUID string: {value!r}")
    return uuid.UUID(value)


class _UuidId:
    """Base for UUID-backed identifier value objects (immutable, canonical)."""

    __slots__ = ("_uuid",)

    _label: str = "id"
    _uuid: uuid.UUID

    def __init__(self, value: str | uuid.UUID) -> None:
        object.__setattr__(self, "_uuid", _require_canonical_uuid(value))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"identifier is immutable: cannot set {name!r}")

    @classmethod
    def new(cls) -> Self:
        """Generate a fresh random (version 4) UUID identifier."""
        return cls(uuid.uuid4())

    @classmethod
    def from_json(cls, value: str) -> Self:
        return cls(value)

    def to_json(self) -> str:
        return str(self._uuid)

    def as_uuid(self) -> uuid.UUID:
        return self._uuid

    def __str__(self) -> str:
        return str(self._uuid)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({str(self)!r})"

    def __eq__(self, other: object) -> bool:
        if type(self) is not type(other):
            return NotImplemented
        assert isinstance(other, _UuidId)
        return self._uuid == other._uuid

    def __hash__(self) -> int:
        return hash((type(self), self._uuid))


class MissionId(_UuidId):
    """A frozen acquisition mission (one task, one mission_id)."""

    _label = "mission_id"

    def __init__(self, value: str | uuid.UUID) -> None:
        super().__init__(value)


class TraceUid(_UuidId):
    """A globally unique trace UID (air/ground transport and UI key)."""

    _label = "trace_uid"

    def __init__(self, value: str | uuid.UUID) -> None:
        super().__init__(value)


class DeviceId(_UuidId):
    """Air-end device identity assigned by deployment configuration."""

    _label = "device_id"

    def __init__(self, value: str | uuid.UUID) -> None:
        super().__init__(value)


class AirFileId(_UuidId):
    """Air-end .rcscan file instance ID."""

    _label = "air_file_id"

    def __init__(self, value: str | uuid.UUID) -> None:
        super().__init__(value)


class GroundFileId(_UuidId):
    """Ground-end .rcscan file instance ID."""

    _label = "ground_file_id"

    def __init__(self, value: str | uuid.UUID) -> None:
        super().__init__(value)


class CommandId(_UuidId):
    """Remote command idempotency and tracing ID."""

    _label = "command_id"

    def __init__(self, value: str | uuid.UUID) -> None:
        super().__init__(value)


class CalibrationProfileId(_UuidId):
    """A .rcal calibration profile unique ID."""

    _label = "calibration_profile_id"

    def __init__(self, value: str | uuid.UUID) -> None:
        super().__init__(value)


class BackgroundReferenceId(_UuidId):
    """A .rcbg empty-scan background reference unique ID."""

    _label = "background_reference_id"

    def __init__(self, value: str | uuid.UUID) -> None:
        super().__init__(value)
