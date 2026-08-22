"""Immutable mission configuration: canonical digest and window derivation.

``MissionConfig`` is the frozen value object created at mission start
(docs/DATA_MODEL.md section 4, docs/ACQUISITION.md sections 4 and 6).  It is
fully validated at construction and can never be mutated afterwards; any
change produces a new object via :meth:`~MissionConfig.with_display_window` or
a new mission.

Persisted units are fixed and explicit in the field names:

- frequencies and IFBW: hertz (``*_hz``);
- power: dBm (``power_dbm``);
- target interval, GNSS max age, display crop: seconds (``*_s``);
- the derived physical window: seconds (``physical_time_window_s``).

The frequency axis is uniform by construction: ``frequency_start_hz``,
``frequency_stop_hz`` and ``frequency_points`` define the linear grid and

``frequency_step_hz = (stop - start) / (points - 1)``
``physical_time_window_s = 1 / frequency_step_hz``

(docs/ACQUISITION.md section 6).  ``from_frequency_axis`` accepts an explicit
axis and rejects non-uniform, non-increasing, non-finite or negative-start
axes with structured errors.

The display crop is a separate value that can only shrink inside the physical
window: ``display_start_s + display_duration_s <= physical_time_window_s``.
``display_duration_s=None`` normalizes to the full physical window.  Display
crop changes return a new ``MissionConfig`` and never modify any raw data.

Canonical JSON and SHA-256 digest cover the mission contract fields only:
``created_utc`` and ``note`` are descriptive metadata (see docs/DATA_MODEL.md
section 4 and docs/ACQUISITION.md section 4) and are excluded, so equivalent
configurations produce the same digest.  ``to_dict()`` / ``from_dict()``
serialize the whole object and verify the digest fail-closed
(``ErrorCode.CONFIG_DIGEST_MISMATCH``).  Numeric normalization is uniform:
every float field maps signed zero to ``0.0`` (NaN/Inf stay rejected), so
``0.0`` and ``-0.0`` are canonically equivalent.

Version contract: ``software_version``, ``protocol_version`` and
``config_schema_version`` are persisted, stable version strings that enter
the canonical JSON, the digest, ``to_dict``/``from_dict`` and ``ConfigDiff``.
Support is gated by :data:`SUPPORTED_CONFIG_SCHEMA_VERSIONS` and
:data:`SUPPORTED_PROTOCOL_VERSIONS`; unknown values are rejected with
``unsupported_schema_version`` / ``unsupported_protocol_version``.  The
transport itself is not implemented yet; ``protocol_version`` only fixes the
compatibility contract carried by a mission config.

``ConfigDiff`` is the field-level difference between a requested and an
applied configuration (docs/ACQUISITION.md section 4): one entry per changed
contract field, deep-copied and JSON-serializable.  Entries are restricted to
contract fields, unique, canonically ordered and must describe a real change;
``from_dict`` validates the complete payload (including the consistency of the
``changed`` flag) and never silently ignores contradictions.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Self, TypeVar, cast

import numpy as np

from uav_gpr.core.channels import ChannelSpec
from uav_gpr.core.enums import (
    AcquisitionMode,
    GnssNoFixPolicy,
    LogicalPolarization,
    SParameter,
)
from uav_gpr.core.errors import (
    DomainError,
    ErrorCode,
    JsonValue,
    _deep_copy_json,
    _require_json_safe,
)
from uav_gpr.core.frequency import _immutable_array, _validate_channels, _validate_frequency_axis
from uav_gpr.core.identifiers import BackgroundReferenceId, CalibrationProfileId
from uav_gpr.core.timeutil import ensure_utc, from_utc_iso, to_utc_iso

_FREQ_DTYPE = np.dtype(np.float64)

# Version contract (fail-closed): the config schema and the air/ground
# protocol versions supported by this codebase.  The protocol transport is
# not implemented yet; these constants only define what a mission config may
# carry and which values are rejected.
SUPPORTED_CONFIG_SCHEMA_VERSION = "1"
SUPPORTED_PROTOCOL_VERSION = "1"
SUPPORTED_CONFIG_SCHEMA_VERSIONS = frozenset({SUPPORTED_CONFIG_SCHEMA_VERSION})
SUPPORTED_PROTOCOL_VERSIONS = frozenset({SUPPORTED_PROTOCOL_VERSION})

# Version-like token: starts alphanumeric, then alphanumeric/._-.
_VERSION_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# Uniform-grid tolerance for explicit axes: relative 1e-9 plus 1 microhertz.
_UNIFORM_RTOL = 1e-9
_UNIFORM_ATOL = 1e-6
# Display crop bound tolerance: relative 1e-9 plus 1 femtosecond (only absorbs
# one-ulp arithmetic noise; meaningful overshoot is always rejected).
_WINDOW_RTOL = 1e-9
_WINDOW_ATOL = 1e-15

# Contract field names (flat, stable, used by the canonical JSON and the diff;
# they must be sorted lexicographically because the canonical order is the
# contract order).
_CONTRACT_FIELDS: tuple[str, ...] = (
    "acquisition_mode",
    "apply_background",
    "apply_calibration",
    "background_reference_id",
    "calibration_profile_id",
    "channels",
    "config_schema_version",
    "display_duration_s",
    "display_start_s",
    "frequency_points",
    "frequency_start_hz",
    "frequency_stop_hz",
    "gnss_max_age_s",
    "gnss_no_fix_policy",
    "if_bw_hz",
    "planned_trace_count",
    "power_dbm",
    "protocol_version",
    "software_version",
    "target_interval_s",
)


def _require_float(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(
            f"{field} must be a real number, got {type(value).__name__}"
        )
    result = float(value)
    if not math.isfinite(result):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            f"{field} must be finite",
            {"field": field},
        )
    # Canonical numeric normalization: signed zero is one value (0.0); NaN/Inf
    # are rejected above and never reach the canonical JSON or the digest.
    if result == 0.0:
        return 0.0
    return result


def _require_positive_float(value: object, field: str) -> float:
    result = _require_float(value, field)
    if result <= 0.0:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            f"{field} must be positive",
            {"field": field, "value": result},
        )
    return result


def _require_non_negative_float(value: object, field: str) -> float:
    result = _require_float(value, field)
    if result < 0.0:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            f"{field} must be non-negative",
            {"field": field, "value": result},
        )
    return result


def _require_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(
            f"{field} must be an int, got {type(value).__name__}"
        )
    return value


def _require_str(value: object, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise TypeError(
            f"{field} must be a str, got {type(value).__name__}"
        )
    if not allow_empty and not value:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT, f"{field} must not be empty"
        )
    return value


def _require_bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(
            f"{field} must be a bool, got {type(value).__name__}"
        )
    return value


def _require_version_token(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(
            f"{field} must be a str, got {type(value).__name__}"
        )
    if _VERSION_TOKEN_RE.fullmatch(value) is None:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            f"{field} must be a version-like token "
            "(alphanumeric first, then letters/digits/dot/underscore/hyphen)",
            {field: value},
        )
    return value


def _require_json_str(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def _channel_to_dict(channel: ChannelSpec) -> dict[str, JsonValue]:
    return {
        "channel_id": channel.channel_id,
        "logical_polarization": channel.logical_polarization.value,
        "s_parameter": channel.s_parameter.value,
        "display_name": channel.display_name,
        "antenna_note": channel.antenna_note,
    }


def _channel_from_dict(data: object) -> ChannelSpec:
    if not isinstance(data, dict):
        raise ValueError("each channels entry must be an object")
    return ChannelSpec(
        channel_id=_require_str(data.get("channel_id"), "channel_id"),
        logical_polarization=LogicalPolarization.from_value(
            _require_str(data.get("logical_polarization"), "logical_polarization")
        ),
        s_parameter=SParameter.from_value(
            _require_str(data.get("s_parameter"), "s_parameter")
        ),
        display_name=_require_str(data.get("display_name"), "display_name"),
        antenna_note=data.get("antenna_note"),
    )


@dataclass(frozen=True, slots=True)
class MissionConfig:
    """Frozen mission configuration value object (see module docstring).

    Required fields come first; ``display_start_s`` and ``display_duration_s``
    are the independent display crop inside the physical window
    (``display_duration_s=None`` means "full physical window").
    """

    frequency_start_hz: float
    frequency_stop_hz: float
    frequency_points: int
    if_bw_hz: float
    power_dbm: float
    channels: tuple[ChannelSpec, ...]
    acquisition_mode: AcquisitionMode
    planned_trace_count: int | None
    target_interval_s: float
    gnss_max_age_s: float
    gnss_no_fix_policy: GnssNoFixPolicy
    calibration_profile_id: CalibrationProfileId | None
    apply_calibration: bool
    background_reference_id: BackgroundReferenceId | None
    apply_background: bool
    created_utc: datetime
    software_version: str
    protocol_version: str = SUPPORTED_PROTOCOL_VERSION
    display_start_s: float = 0.0
    display_duration_s: float | None = None
    note: str | None = None
    config_schema_version: str = SUPPORTED_CONFIG_SCHEMA_VERSION

    def __post_init__(self) -> None:
        start = _require_non_negative_float(
            self.frequency_start_hz, "frequency_start_hz"
        )
        stop = _require_non_negative_float(
            self.frequency_stop_hz, "frequency_stop_hz"
        )
        if stop <= start:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "frequency_stop_hz must be greater than frequency_start_hz",
                {
                    "frequency_start_hz": start,
                    "frequency_stop_hz": stop,
                },
            )
        points = _require_int(self.frequency_points, "frequency_points")
        if points < 2:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "frequency_points must be at least 2 (a uniform step and its "
                "physical window require two or more points)",
                {"frequency_points": points},
            )
        if_bw = _require_positive_float(self.if_bw_hz, "if_bw_hz")
        power = _require_float(self.power_dbm, "power_dbm")
        channels = _validate_channels(self.channels)
        if not isinstance(self.acquisition_mode, AcquisitionMode):
            raise TypeError(
                "acquisition_mode must be an AcquisitionMode, "
                f"got {type(self.acquisition_mode).__name__}"
            )
        if self.acquisition_mode is AcquisitionMode.FIXED_COUNT:
            if self.planned_trace_count is None:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "fixed_count mode requires planned_trace_count",
                )
            planned = _require_int(self.planned_trace_count, "planned_trace_count")
            if planned < 1:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "planned_trace_count must be at least 1",
                    {"planned_trace_count": planned},
                )
        else:
            if self.planned_trace_count is not None:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "continuous mode forbids planned_trace_count",
                    {"planned_trace_count": self.planned_trace_count},
                )
            planned = None
        interval = _require_positive_float(self.target_interval_s, "target_interval_s")
        gnss_age = _require_positive_float(self.gnss_max_age_s, "gnss_max_age_s")
        if not isinstance(self.gnss_no_fix_policy, GnssNoFixPolicy):
            raise TypeError(
                "gnss_no_fix_policy must be a GnssNoFixPolicy, "
                f"got {type(self.gnss_no_fix_policy).__name__}"
            )
        cal_id = self._require_optional_id(
            self.calibration_profile_id, "calibration_profile_id", CalibrationProfileId
        )
        bg_id = self._require_optional_id(
            self.background_reference_id, "background_reference_id", BackgroundReferenceId
        )
        apply_cal = _require_bool(self.apply_calibration, "apply_calibration")
        apply_bg = _require_bool(self.apply_background, "apply_background")
        if apply_cal and cal_id is None:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "apply_calibration requires calibration_profile_id",
            )
        if apply_bg and bg_id is None:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "apply_background requires background_reference_id",
            )
        display_start = _require_non_negative_float(
            self.display_start_s, "display_start_s"
        )
        display_duration: float | None
        if self.display_duration_s is None:
            display_duration = None
        else:
            display_duration = _require_positive_float(
                self.display_duration_s, "display_duration_s"
            )
        if self.note is not None and not isinstance(self.note, str):
            raise TypeError(
                f"note must be a str or None, got {type(self.note).__name__}"
            )
        software = _require_version_token(
            self.software_version, "software_version"
        )
        protocol = _require_version_token(
            self.protocol_version, "protocol_version"
        )
        if protocol not in SUPPORTED_PROTOCOL_VERSIONS:
            raise DomainError(
                ErrorCode.UNSUPPORTED_PROTOCOL_VERSION,
                "unsupported air/ground protocol version",
                {"protocol_version": protocol},
            )
        schema_version = _require_version_token(
            self.config_schema_version, "config_schema_version"
        )
        if schema_version not in SUPPORTED_CONFIG_SCHEMA_VERSIONS:
            raise DomainError(
                ErrorCode.UNSUPPORTED_SCHEMA_VERSION,
                "unsupported config schema version",
                {"config_schema_version": schema_version},
            )
        created = ensure_utc(self.created_utc)

        step = (stop - start) / (points - 1)
        physical = 1.0 / step
        tolerance = physical * _WINDOW_RTOL + _WINDOW_ATOL
        if display_duration is None:
            display_duration = physical
        if display_start > physical + tolerance:
            raise DomainError(
                ErrorCode.OUT_OF_RANGE,
                "display_start_s exceeds the physical time window",
                {
                    "display_start_s": display_start,
                    "physical_time_window_s": physical,
                },
            )
        if display_start + display_duration > physical + tolerance:
            raise DomainError(
                ErrorCode.OUT_OF_RANGE,
                "display crop must lie inside the physical time window",
                {
                    "display_start_s": display_start,
                    "display_duration_s": display_duration,
                    "display_end_s": display_start + display_duration,
                    "physical_time_window_s": physical,
                },
            )

        object.__setattr__(self, "frequency_start_hz", start)
        object.__setattr__(self, "frequency_stop_hz", stop)
        object.__setattr__(self, "frequency_points", points)
        object.__setattr__(self, "if_bw_hz", if_bw)
        object.__setattr__(self, "power_dbm", power)
        object.__setattr__(self, "channels", channels)
        object.__setattr__(self, "planned_trace_count", planned)
        object.__setattr__(self, "target_interval_s", interval)
        object.__setattr__(self, "gnss_max_age_s", gnss_age)
        object.__setattr__(self, "calibration_profile_id", cal_id)
        object.__setattr__(self, "apply_calibration", apply_cal)
        object.__setattr__(self, "background_reference_id", bg_id)
        object.__setattr__(self, "apply_background", apply_bg)
        object.__setattr__(self, "display_start_s", display_start)
        object.__setattr__(self, "display_duration_s", display_duration)
        object.__setattr__(self, "created_utc", created)
        object.__setattr__(self, "software_version", software)
        object.__setattr__(self, "protocol_version", protocol)
        object.__setattr__(self, "config_schema_version", schema_version)

    @staticmethod
    def _require_optional_id(
        value: object,
        field: str,
        expected: type[CalibrationProfileId] | type[BackgroundReferenceId],
    ) -> CalibrationProfileId | BackgroundReferenceId | None:
        if value is None:
            return None
        if not isinstance(value, expected):
            raise TypeError(
                f"{field} must be {expected.__name__} or None, "
                f"got {type(value).__name__}"
            )
        return value

    # -- derived values ----------------------------------------------------

    @property
    def frequency_step_hz(self) -> float:
        """Uniform grid step ``(stop - start) / (points - 1)`` in Hz."""
        return (self.frequency_stop_hz - self.frequency_start_hz) / (
            self.frequency_points - 1
        )

    @property
    def physical_time_window_s(self) -> float:
        """``1 / frequency_step_hz``: the unambiguous physical time window."""
        return 1.0 / self.frequency_step_hz

    @property
    def bandwidth_hz(self) -> float:
        """``frequency_stop_hz - frequency_start_hz`` in Hz."""
        return self.frequency_stop_hz - self.frequency_start_hz

    @property
    def frequency_axis_hz(self) -> np.ndarray:
        """The explicit uniform frequency axis (owned, never writable)."""
        axis = np.linspace(
            self.frequency_start_hz, self.frequency_stop_hz, self.frequency_points
        )
        return _immutable_array(axis, _FREQ_DTYPE)

    # -- canonical JSON and digest ------------------------------------------

    def _contract_dict(self) -> dict[str, JsonValue]:
        """JSON-safe contract fields (the digest and diff input).

        ``created_utc`` and ``note`` are descriptive metadata: they are part of
        :meth:`to_dict` but never of the contract, so equivalent configurations
        share one digest.
        """
        return {
            "acquisition_mode": self.acquisition_mode.value,
            "apply_background": self.apply_background,
            "apply_calibration": self.apply_calibration,
            "background_reference_id": (
                self.background_reference_id.to_json()
                if self.background_reference_id is not None
                else None
            ),
            "calibration_profile_id": (
                self.calibration_profile_id.to_json()
                if self.calibration_profile_id is not None
                else None
            ),
            "channels": [_channel_to_dict(channel) for channel in self.channels],
            "config_schema_version": self.config_schema_version,
            "display_duration_s": self.display_duration_s,
            "display_start_s": self.display_start_s,
            "frequency_points": self.frequency_points,
            "frequency_start_hz": self.frequency_start_hz,
            "frequency_stop_hz": self.frequency_stop_hz,
            "gnss_max_age_s": self.gnss_max_age_s,
            "gnss_no_fix_policy": self.gnss_no_fix_policy.value,
            "if_bw_hz": self.if_bw_hz,
            "planned_trace_count": self.planned_trace_count,
            "power_dbm": self.power_dbm,
            "protocol_version": self.protocol_version,
            "software_version": self.software_version,
            "target_interval_s": self.target_interval_s,
        }

    def to_canonical_json(self) -> str:
        """Deterministic canonical JSON of the mission contract.

        Keys are sorted at every level, compact separators, no whitespace;
        lists keep their order (channel order is part of the contract).
        """
        return json.dumps(
            self._contract_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )

    @property
    def config_sha256(self) -> str:
        """SHA-256 of the canonical JSON (64 lowercase hex characters)."""
        payload = self.to_canonical_json().encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    # -- construction and copy ----------------------------------------------

    @classmethod
    def from_frequency_axis(
        cls,
        *,
        frequency_axis_hz: object,
        if_bw_hz: float,
        power_dbm: float,
        channels: Sequence[ChannelSpec],
        acquisition_mode: AcquisitionMode,
        planned_trace_count: int | None,
        target_interval_s: float,
        gnss_max_age_s: float,
        gnss_no_fix_policy: GnssNoFixPolicy,
        created_utc: datetime,
        software_version: str,
        calibration_profile_id: CalibrationProfileId | None = None,
        apply_calibration: bool = False,
        background_reference_id: BackgroundReferenceId | None = None,
        apply_background: bool = False,
        display_start_s: float = 0.0,
        display_duration_s: float | None = None,
        note: str | None = None,
        protocol_version: str = SUPPORTED_PROTOCOL_VERSION,
        config_schema_version: str = SUPPORTED_CONFIG_SCHEMA_VERSION,
    ) -> MissionConfig:
        """Build a config from an explicit, uniformly spaced frequency axis.

        The axis must be one-dimensional, finite, strictly increasing with at
        least two points, start at or above 0 Hz and be uniform within
        ``rtol=1e-9, atol=1e-6 Hz``.  The uniform grid is normalized to
        ``start/stop/points`` so the canonical representation (and digest) is
        identical to an equivalent explicit construction.
        """
        axis = _validate_frequency_axis(frequency_axis_hz)
        if float(axis[0]) < 0.0:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "frequency axis must start at or above 0 Hz",
                {"frequency_start_hz": float(axis[0])},
            )
        if axis.size < 2:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "frequency axis requires at least 2 points",
                {"frequency_points": int(axis.size)},
            )
        diffs = np.diff(axis)
        if not np.allclose(diffs, diffs[0], rtol=_UNIFORM_RTOL, atol=_UNIFORM_ATOL):
            deviations = np.abs(diffs - diffs[0]) / diffs[0]
            raise DomainError(
                ErrorCode.NON_UNIFORM_AXIS,
                "frequency axis must be uniformly spaced",
                {"max_relative_deviation": float(np.max(deviations))},
            )
        return cls(
            frequency_start_hz=float(axis[0]),
            frequency_stop_hz=float(axis[-1]),
            frequency_points=int(axis.size),
            if_bw_hz=if_bw_hz,
            power_dbm=power_dbm,
            channels=tuple(channels),
            acquisition_mode=acquisition_mode,
            planned_trace_count=planned_trace_count,
            target_interval_s=target_interval_s,
            gnss_max_age_s=gnss_max_age_s,
            gnss_no_fix_policy=gnss_no_fix_policy,
            calibration_profile_id=calibration_profile_id,
            apply_calibration=apply_calibration,
            background_reference_id=background_reference_id,
            apply_background=apply_background,
            display_start_s=display_start_s,
            display_duration_s=display_duration_s,
            created_utc=created_utc,
            note=note,
            software_version=software_version,
            protocol_version=protocol_version,
            config_schema_version=config_schema_version,
        )

    def with_display_window(
        self, *, start_s: float, duration_s: float | None
    ) -> MissionConfig:
        """Return a new config with a different display crop (self unchanged).

        The crop is still validated against the physical window; a
        ``duration_s`` of ``None`` means the full physical window.
        """
        return replace(self, display_start_s=start_s, display_duration_s=duration_s)

    # -- serialization ------------------------------------------------------

    def to_dict(self) -> dict[str, JsonValue]:
        """Full JSON-safe serialization (contract fields + descriptive data)."""
        data = self._contract_dict()
        data["created_utc"] = to_utc_iso(self.created_utc)
        data["note"] = self.note
        data["config_sha256"] = self.config_sha256
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        """Strict deserialization; verifies ``config_sha256`` fail-closed."""
        channels_raw = data.get("channels")
        if not isinstance(channels_raw, list):
            raise ValueError("channels must be a list")
        config = cls(
            frequency_start_hz=_require_float(
                data.get("frequency_start_hz"), "frequency_start_hz"
            ),
            frequency_stop_hz=_require_float(
                data.get("frequency_stop_hz"), "frequency_stop_hz"
            ),
            frequency_points=_require_int(
                data.get("frequency_points"), "frequency_points"
            ),
            if_bw_hz=_require_float(data.get("if_bw_hz"), "if_bw_hz"),
            power_dbm=_require_float(data.get("power_dbm"), "power_dbm"),
            channels=tuple(_channel_from_dict(item) for item in channels_raw),
            acquisition_mode=AcquisitionMode.from_value(
                _require_str(data.get("acquisition_mode"), "acquisition_mode")
            ),
            planned_trace_count=_optional_int(
                data.get("planned_trace_count"), "planned_trace_count"
            ),
            target_interval_s=_require_float(
                data.get("target_interval_s"), "target_interval_s"
            ),
            gnss_max_age_s=_require_float(
                data.get("gnss_max_age_s"), "gnss_max_age_s"
            ),
            gnss_no_fix_policy=GnssNoFixPolicy.from_value(
                _require_str(data.get("gnss_no_fix_policy"), "gnss_no_fix_policy")
            ),
            calibration_profile_id=_optional_id(
                data.get("calibration_profile_id"),
                "calibration_profile_id",
                CalibrationProfileId,
            ),
            apply_calibration=_require_bool(
                data.get("apply_calibration"), "apply_calibration"
            ),
            background_reference_id=_optional_id(
                data.get("background_reference_id"),
                "background_reference_id",
                BackgroundReferenceId,
            ),
            apply_background=_require_bool(
                data.get("apply_background"), "apply_background"
            ),
            display_start_s=_require_float(
                data.get("display_start_s"), "display_start_s"
            ),
            display_duration_s=_optional_positive_float(
                data.get("display_duration_s"), "display_duration_s"
            ),
            created_utc=from_utc_iso(
                _require_str(data.get("created_utc"), "created_utc")
            ),
            note=_optional_str(data.get("note"), "note"),
            software_version=_require_json_str(
                data.get("software_version"), "software_version"
            ),
            protocol_version=_require_json_str(
                data.get("protocol_version"), "protocol_version"
            ),
            config_schema_version=_require_json_str(
                data.get("config_schema_version"), "config_schema_version"
            ),
        )
        digest = data.get("config_sha256")
        if not isinstance(digest, str):
            raise ValueError("config_sha256 must be a string")
        if digest != config.config_sha256:
            raise DomainError(
                ErrorCode.CONFIG_DIGEST_MISMATCH,
                "config digest mismatch",
                {"stored_digest": digest, "computed_digest": config.config_sha256},
            )
        return config


def _optional_str(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string or null")
    return value


def _optional_int(value: object, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer or null")
    return value


def _optional_positive_float(value: object, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number or null")
    result = float(value)
    if not math.isfinite(result) or result <= 0.0:
        raise ValueError(f"{field} must be a positive finite number or null")
    return result


_IdT = TypeVar("_IdT", CalibrationProfileId, BackgroundReferenceId)


def _optional_id(
    value: object,
    field: str,
    expected: type[_IdT],
) -> _IdT | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string or null")
    return expected.from_json(value)


@dataclass(frozen=True, slots=True, init=False)
class ConfigFieldDiff:
    """One config contract field: requested vs applied values.

    Strict value-object rules: the field must be a contract field and the
    entry must describe an actual change (requested != applied).  Stored
    values are deep-copied at construction and every accessor returns a fresh
    deep copy, so neither the caller's input nor a returned value can ever
    mutate the diff.
    """

    field: str
    _requested_value: JsonValue
    _applied_value: JsonValue

    def __init__(
        self, field: str, requested_value: JsonValue, applied_value: JsonValue
    ) -> None:
        if not isinstance(field, str) or not field:
            raise ValueError("diff field must be a non-empty string")
        if field not in _CONTRACT_FIELDS:
            raise ValueError(f"unknown diff field: {field!r}")
        _require_json_safe(requested_value, f"$requested_value[{field}]")
        _require_json_safe(applied_value, f"$applied_value[{field}]")
        requested = cast(JsonValue, _deep_copy_json(requested_value))
        applied = cast(JsonValue, _deep_copy_json(applied_value))
        if requested == applied:
            raise ValueError("a diff entry must describe an actual change")
        object.__setattr__(self, "field", field)
        object.__setattr__(self, "_requested_value", requested)
        object.__setattr__(self, "_applied_value", applied)

    @property
    def requested_value(self) -> JsonValue:
        """Independent deep copy of the requested value."""
        return cast(JsonValue, _deep_copy_json(self._requested_value))

    @property
    def applied_value(self) -> JsonValue:
        """Independent deep copy of the applied value."""
        return cast(JsonValue, _deep_copy_json(self._applied_value))

    @property
    def changed(self) -> bool:
        return self._requested_value != self._applied_value

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "field": self.field,
            "requested_value": self.requested_value,
            "applied_value": self.applied_value,
            "changed": self.changed,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        if not isinstance(data, Mapping):
            raise ValueError("each diff entry must be an object")
        field = data.get("field")
        if not isinstance(field, str) or not field:
            raise ValueError("diff field must be a non-empty string")
        if "requested_value" not in data or "applied_value" not in data:
            raise ValueError(
                "diff entry requires requested_value and applied_value"
            )
        requested = data["requested_value"]
        applied = data["applied_value"]
        try:
            _require_json_safe(requested, f"$requested_value[{field}]")
            _require_json_safe(applied, f"$applied_value[{field}]")
        except TypeError as error:
            raise ValueError(f"diff entry values must be JSON safe: {error}") from None
        changed = data.get("changed")
        if not isinstance(changed, bool):
            raise ValueError("diff entry 'changed' must be a boolean")
        values_equal = requested == applied
        if changed == values_equal:
            raise ValueError(
                "diff entry 'changed' flag contradicts requested/applied values"
            )
        return cls(field, cast(JsonValue, requested), cast(JsonValue, applied))


@dataclass(frozen=True, slots=True)
class ConfigDiff:
    """Field-level difference between requested and applied configurations.

    Strict value-object rules: only contract fields, at most one entry per
    field, canonically ordered and each entry describing a real change
    (docs/ACQUISITION.md section 4: device quantization or rejection reasons
    surface here).  Unchanged fields never appear; ``compute()`` builds the
    diff in canonical contract order.
    """

    fields: tuple[ConfigFieldDiff, ...]

    def __post_init__(self) -> None:
        fields = tuple(self.fields)
        for entry in fields:
            if not isinstance(entry, ConfigFieldDiff):
                raise TypeError(
                    "diff entries must be ConfigFieldDiff, "
                    f"got {type(entry).__name__}"
                )
            if entry.field not in _CONTRACT_FIELDS:
                raise ValueError(f"unknown diff field: {entry.field!r}")
        names = [entry.field for entry in fields]
        if len(set(names)) != len(names):
            raise ValueError("diff fields must be unique")
        if names != sorted(names):
            raise ValueError("diff fields must appear in canonical contract order")
        object.__setattr__(self, "fields", fields)

    @classmethod
    def compute(cls, requested: MissionConfig, applied: MissionConfig) -> ConfigDiff:
        if not isinstance(requested, MissionConfig) or not isinstance(
            applied, MissionConfig
        ):
            raise TypeError("ConfigDiff.compute requires two MissionConfig objects")
        requested_contract = requested._contract_dict()
        applied_contract = applied._contract_dict()
        entries: list[ConfigFieldDiff] = []
        for key in sorted(requested_contract):
            if requested_contract[key] != applied_contract[key]:
                entries.append(
                    ConfigFieldDiff(key, requested_contract[key], applied_contract[key])
                )
        return cls(tuple(entries))

    @property
    def changed_fields(self) -> tuple[str, ...]:
        """Changed field names in canonical (sorted) order."""
        return tuple(entry.field for entry in self.fields)

    @property
    def is_identical(self) -> bool:
        return not self.fields

    def field(self, name: str) -> ConfigFieldDiff | None:
        """The diff entry for ``name``, or ``None`` if the field is unchanged."""
        for entry in self.fields:
            if entry.field == name:
                return entry
        return None

    def to_dict(self) -> dict[str, JsonValue]:
        return {"fields": [entry.to_dict() for entry in self.fields]}

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        raw_fields = data.get("fields")
        if not isinstance(raw_fields, list):
            raise ValueError("fields must be a list")
        return cls(tuple(ConfigFieldDiff.from_dict(item) for item in raw_fields))
