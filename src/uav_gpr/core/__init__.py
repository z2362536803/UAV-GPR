"""UAV-GPR shared core: identifiers, stable enums, structured errors, time."""

from uav_gpr.core.channels import ChannelSpec
from uav_gpr.core.config import ConfigDiff, ConfigFieldDiff, MissionConfig
from uav_gpr.core.enums import (
    AcquisitionMode,
    EndpointRole,
    GnssFixQuality,
    GnssMatchMethod,
    GnssNoFixPolicy,
    GnssStatus,
    GnssUnavailableReason,
    LogicalPolarization,
    MissionTerminalState,
    SParameter,
    StableStrEnum,
    TraceQualityReason,
    TraceQualityStatus,
)
from uav_gpr.core.errors import DomainError, ErrorCode
from uav_gpr.core.frequency import FrequencyScan, FrequencySweep
from uav_gpr.core.gnss import GnssFix, GnssMatch
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
from uav_gpr.core.metadata import TraceMetadata
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
    "AcquisitionMode",
    "AirFileId",
    "BackgroundReferenceId",
    "CalibrationProfileId",
    "ChannelSpec",
    "Clock",
    "CommandId",
    "ConfigDiff",
    "ConfigFieldDiff",
    "DeviceId",
    "DomainError",
    "EndpointRole",
    "ErrorCode",
    "FrequencyScan",
    "FrequencySweep",
    "GnssFix",
    "GnssFixQuality",
    "GnssMatch",
    "GnssMatchMethod",
    "GnssNoFixPolicy",
    "GnssStatus",
    "GnssUnavailableReason",
    "GroundFileId",
    "LogicalPolarization",
    "ManualClock",
    "MissionConfig",
    "MissionId",
    "MissionTerminalState",
    "MonotonicNs",
    "SParameter",
    "StableStrEnum",
    "SystemClock",
    "TraceMetadata",
    "TraceQualityReason",
    "TraceQualityStatus",
    "TraceUid",
    "ensure_utc",
    "from_utc_iso",
    "to_utc_iso",
    "utc_now",
]
