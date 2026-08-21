"""UAV-GPR shared core: identifiers, stable enums, structured errors, time."""

from uav_gpr.core.enums import (
    EndpointRole,
    GnssStatus,
    LogicalPolarization,
    MissionTerminalState,
    SParameter,
    StableStrEnum,
)
from uav_gpr.core.errors import DomainError, ErrorCode
from uav_gpr.core.identifiers import (
    AirFileId,
    BackgroundReferenceId,
    CalibrationProfileId,
    CommandId,
    DeviceId,
    GroundFileId,
    MissionId,
    TraceUid,
)
from uav_gpr.core.timeutil import (
    Clock,
    ManualClock,
    MonotonicNs,
    SystemClock,
    ensure_utc,
    from_utc_iso,
    to_utc_iso,
    utc_now,
)

__all__ = [
    "AirFileId",
    "BackgroundReferenceId",
    "CalibrationProfileId",
    "Clock",
    "CommandId",
    "DeviceId",
    "DomainError",
    "EndpointRole",
    "ErrorCode",
    "GnssStatus",
    "GroundFileId",
    "LogicalPolarization",
    "ManualClock",
    "MissionId",
    "MissionTerminalState",
    "MonotonicNs",
    "SParameter",
    "StableStrEnum",
    "SystemClock",
    "TraceUid",
    "ensure_utc",
    "from_utc_iso",
    "to_utc_iso",
    "utc_now",
]
