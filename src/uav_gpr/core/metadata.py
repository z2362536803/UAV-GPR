"""Immutable trace metadata with copy-on-update integrity attachment.

``TraceMetadata`` is the per-trace contract: identity, sweep time windows
(UTC and monotonic stored separately), scheduling fields, connection
generation, the raw trace SHA-256 **field contract** (the hash itself is
computed by storage, never here), the GNSS match and a data quality summary.

The first trace may have ``actual_interval_s`` / ``schedule_error_s`` set to
``None`` (there is no previous trace); every later trace requires them.

"Acquired" metadata gets integrity information attached by returning a new
object through :meth:`with_gnss_match` / :meth:`with_data_quality`; frozen
instances are never modified.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Self

from uav_gpr.core.enums import (
    TraceQualityReason,
    TraceQualityStatus,
)
from uav_gpr.core.errors import DomainError, ErrorCode
from uav_gpr.core.gnss import GnssMatch, _optional_float, _require_int, _require_str
from uav_gpr.core.identifiers import DeviceId, MissionId, TraceUid
from uav_gpr.core.timeutil import MonotonicNs, ensure_utc, from_utc_iso, to_utc_iso

_RAW_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def _as_monotonic(value: object, field: str) -> MonotonicNs:
    if not isinstance(value, MonotonicNs):
        raise TypeError(
            f"{field} must be a MonotonicNs, got {type(value).__name__}"
        )
    return value


@dataclass(frozen=True, slots=True)
class TraceMetadata:
    """Per-trace immutable metadata (see module docstring)."""

    mission_id: MissionId
    trace_index: int
    trace_uid: TraceUid
    device_id: DeviceId
    sweep_started_utc: datetime
    sweep_midpoint_utc: datetime
    sweep_finished_utc: datetime
    sweep_started_monotonic_ns: MonotonicNs
    sweep_midpoint_monotonic_ns: MonotonicNs
    sweep_finished_monotonic_ns: MonotonicNs
    target_interval_s: float
    actual_interval_s: float | None
    schedule_error_s: float | None
    connection_generation: int
    raw_trace_sha256: str
    gnss_match: GnssMatch | None
    quality_status: TraceQualityStatus
    quality_reasons: tuple[TraceQualityReason, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.mission_id, MissionId):
            raise TypeError("mission_id must be a MissionId")
        if isinstance(self.trace_index, bool) or not isinstance(self.trace_index, int):
            raise TypeError("trace_index must be an int")
        if self.trace_index < 0:
            raise DomainError(ErrorCode.INVALID_ARGUMENT, "trace_index must be non-negative")
        if not isinstance(self.trace_uid, TraceUid):
            raise TypeError("trace_uid must be a TraceUid")
        if not isinstance(self.device_id, DeviceId):
            raise TypeError("device_id must be a DeviceId")

        started = ensure_utc(self.sweep_started_utc)
        midpoint = ensure_utc(self.sweep_midpoint_utc)
        finished = ensure_utc(self.sweep_finished_utc)
        object.__setattr__(self, "sweep_started_utc", started)
        object.__setattr__(self, "sweep_midpoint_utc", midpoint)
        object.__setattr__(self, "sweep_finished_utc", finished)
        mono_start = _as_monotonic(self.sweep_started_monotonic_ns, "sweep_started_monotonic_ns")
        mono_mid = _as_monotonic(self.sweep_midpoint_monotonic_ns, "sweep_midpoint_monotonic_ns")
        mono_finish = _as_monotonic(self.sweep_finished_monotonic_ns, "sweep_finished_monotonic_ns")
        if not (started <= midpoint <= finished):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "sweep UTC times must be ordered start <= midpoint <= finish",
            )
        if not (mono_start.ns <= mono_mid.ns <= mono_finish.ns):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "sweep monotonic times must be ordered start <= midpoint <= finish",
            )

        if isinstance(self.target_interval_s, bool) or not isinstance(
            self.target_interval_s, float
        ):
            raise TypeError("target_interval_s must be a float")
        if not math.isfinite(self.target_interval_s) or not self.target_interval_s > 0.0:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "target_interval_s must be positive and finite",
                {"target_interval_s": self.target_interval_s},
            )
        actual = _optional_float(self.actual_interval_s)
        schedule = _optional_float(self.schedule_error_s)
        if actual is not None and (not math.isfinite(actual) or actual < 0.0):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "actual_interval_s must be finite and non-negative",
                {"actual_interval_s": actual},
            )
        if schedule is not None and not math.isfinite(schedule):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "schedule_error_s must be finite",
                {"schedule_error_s": schedule},
            )
        if self.trace_index == 0:
            # First trace: actual interval and schedule error may be None.
            pass
        elif actual is None or schedule is None:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "traces after the first require actual_interval_s and schedule_error_s",
                {"trace_index": self.trace_index},
            )

        if isinstance(self.connection_generation, bool) or not isinstance(
            self.connection_generation, int
        ):
            raise TypeError("connection_generation must be an int")
        if self.connection_generation < 0:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "connection_generation must be non-negative",
                {"connection_generation": self.connection_generation},
            )
        if not isinstance(self.raw_trace_sha256, str) or _RAW_HASH_RE.fullmatch(
            self.raw_trace_sha256
        ) is None:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "raw_trace_sha256 field contract is 64 lowercase hex characters",
                {"raw_trace_sha256": self.raw_trace_sha256},
            )
        if self.gnss_match is not None and not isinstance(self.gnss_match, GnssMatch):
            raise TypeError("gnss_match must be a GnssMatch or None")
        if not isinstance(self.quality_status, TraceQualityStatus):
            raise TypeError("quality_status must be a TraceQualityStatus")
        reasons = tuple(self.quality_reasons)
        if not all(isinstance(item, TraceQualityReason) for item in reasons):
            raise TypeError("quality_reasons must contain TraceQualityReason values")
        reasons = tuple(dict.fromkeys(reasons))
        object.__setattr__(self, "quality_reasons", reasons)
        if self.gnss_match is None and TraceQualityReason.GNSS_MISSING not in reasons:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "missing GNSS requires the explicit gnss_missing quality reason",
            )
        if self.gnss_match is not None and TraceQualityReason.GNSS_MISSING in reasons:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "gnss_missing reason contradicts an attached gnss_match",
            )
        if (not reasons) != (self.quality_status is TraceQualityStatus.NOMINAL):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "quality reasons must be empty iff status is nominal",
            )

    def with_gnss_match(self, match: GnssMatch | None) -> TraceMetadata:
        """Return a copy with the GNSS match attached (or detached).

        Attaching removes ``gnss_missing``; detaching adds it and degrades a
        nominal status to ``degraded``.  The caller may still need
        :meth:`with_data_quality` for other quality reasons.
        """
        reasons = list(self.quality_reasons)
        status = self.quality_status
        if match is None:
            if TraceQualityReason.GNSS_MISSING not in reasons:
                reasons.append(TraceQualityReason.GNSS_MISSING)
            if status is TraceQualityStatus.NOMINAL:
                status = TraceQualityStatus.DEGRADED
        else:
            reasons = [r for r in reasons if r is not TraceQualityReason.GNSS_MISSING]
            if not reasons:
                status = TraceQualityStatus.NOMINAL
        return replace(
            self,
            gnss_match=match,
            quality_status=status,
            quality_reasons=tuple(reasons),
        )

    def with_data_quality(
        self,
        status: TraceQualityStatus,
        reasons: Sequence[TraceQualityReason],
    ) -> TraceMetadata:
        """Return a copy with an updated data quality summary."""
        return replace(self, quality_status=status, quality_reasons=tuple(reasons))

    def to_dict(self) -> dict[str, object]:
        return {
            "mission_id": self.mission_id.to_json(),
            "trace_index": self.trace_index,
            "trace_uid": self.trace_uid.to_json(),
            "device_id": self.device_id.to_json(),
            "sweep_started_utc": to_utc_iso(self.sweep_started_utc),
            "sweep_midpoint_utc": to_utc_iso(self.sweep_midpoint_utc),
            "sweep_finished_utc": to_utc_iso(self.sweep_finished_utc),
            "sweep_started_monotonic_ns": self.sweep_started_monotonic_ns.ns,
            "sweep_midpoint_monotonic_ns": self.sweep_midpoint_monotonic_ns.ns,
            "sweep_finished_monotonic_ns": self.sweep_finished_monotonic_ns.ns,
            "target_interval_s": self.target_interval_s,
            "actual_interval_s": self.actual_interval_s,
            "schedule_error_s": self.schedule_error_s,
            "connection_generation": self.connection_generation,
            "raw_trace_sha256": self.raw_trace_sha256,
            "gnss_match": self.gnss_match.to_dict() if self.gnss_match else None,
            "quality_status": self.quality_status.value,
            "quality_reasons": [reason.value for reason in self.quality_reasons],
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Self:
        match_data = data.get("gnss_match")
        if match_data is not None and not isinstance(match_data, dict):
            raise ValueError("gnss_match must be an object or null")
        return cls(
            mission_id=MissionId.from_json(_require_str(data["mission_id"], "mission_id")),
            trace_index=_require_int(data["trace_index"]),
            trace_uid=TraceUid.from_json(_require_str(data["trace_uid"], "trace_uid")),
            device_id=DeviceId.from_json(_require_str(data["device_id"], "device_id")),
            sweep_started_utc=from_utc_iso(
                _require_str(data["sweep_started_utc"], "sweep_started_utc")
            ),
            sweep_midpoint_utc=from_utc_iso(
                _require_str(data["sweep_midpoint_utc"], "sweep_midpoint_utc")
            ),
            sweep_finished_utc=from_utc_iso(
                _require_str(data["sweep_finished_utc"], "sweep_finished_utc")
            ),
            sweep_started_monotonic_ns=MonotonicNs(
                _require_int(data["sweep_started_monotonic_ns"])
            ),
            sweep_midpoint_monotonic_ns=MonotonicNs(
                _require_int(data["sweep_midpoint_monotonic_ns"])
            ),
            sweep_finished_monotonic_ns=MonotonicNs(
                _require_int(data["sweep_finished_monotonic_ns"])
            ),
            target_interval_s=_required_float(data["target_interval_s"], "target_interval_s"),
            actual_interval_s=_optional_float(data.get("actual_interval_s")),
            schedule_error_s=_optional_float(data.get("schedule_error_s")),
            connection_generation=_require_int(data["connection_generation"]),
            raw_trace_sha256=_require_str(data["raw_trace_sha256"], "raw_trace_sha256"),
            gnss_match=GnssMatch.from_dict(match_data) if match_data is not None else None,
            quality_status=TraceQualityStatus.from_value(
                _require_str(data["quality_status"], "quality_status")
            ),
            quality_reasons=tuple(
                TraceQualityReason.from_value(item)
                for item in _require_str_list(data["quality_reasons"], "quality_reasons")
            ),
        )


def _required_float(value: object, field: str) -> float:
    result = _optional_float(value)
    if result is None:
        raise ValueError(f"{field} must be a float")
    return result


def _require_str_list(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field} must be a list of strings")
    return value
