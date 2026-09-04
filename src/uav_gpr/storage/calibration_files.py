"""Versioned ``.rcal`` / ``.rcbg`` reference files (ISSUE-029).

This module is the file-level contract for calibration and air-background
reference artifacts: a discriminated JSON envelope (``format_name`` +
integer ``schema_version``), lossless complex encoding with explicit
dtype/shape, a self-verifying content digest, strict writer/reader pairs,
and field-level compatibility verdicts.  It persists models built by
ISSUE-027 (:mod:`uav_gpr.calibration.osl`) and ISSUE-028
(:mod:`uav_gpr.calibration.reference`) as read-only consumers; it never
applies OSL or background correction to mission data, owns no UI, and has
no "enable" side effect — reading or selecting a file only proves that the
file is parseable and digest-intact (docs/CALIBRATION.md §6/§8: loading,
enabling, and physical capture are distinct actions).

Envelope layout (schema_version = 1)::

    {
      "format_name": "uav_gpr_rcal" | "uav_gpr_rcbg",
      "schema_version": 1,
      "payload": { ... type-specific document ... },
      "content_sha256": "<64 lowercase hex of canonical payload JSON>"
    }

Rules:

* ``content_sha256`` covers SHA-256 of the canonical JSON (sorted keys,
  compact separators, UTF-8) of ``payload`` alone; writers compute it,
  readers recompute and compare before any interpretation, so a single
  tampered numeric byte fails closed with ``INVALID_ARGUMENT``.
* Unknown ``format_name`` / ``schema_version`` (including float/string/bool
  look-alikes such as ``1.0`` / ``"1"`` / ``true``) fail closed with
  ``UNSUPPORTED_SCHEMA_VERSION``; there is no silent migration.
* Complex arrays are stored as ``{"dtype", "shape", "re", "im"}`` through
  IEEE-754 doubles (bit-exact round-trip); real axes use the same object
  without ``im``.  NaN/Infinity constants are rejected on both write
  (``allow_nan=False``) and parse (``parse_constant``) paths.
* Non-finite values, shape violations, duplicate channels, mixed axes, and
  domain/profile-id binding errors are rejected by the typed payload
  constructors *before* any file is created.
* Writers refuse to overwrite existing files (``WriteConflictError``,
  original preserved), write to a unique temporary sibling, fsync, then
  atomically ``os.replace``; failures clean up partial output.
* Compatibility checks return a frozen ``CompatibilityResult`` listing every
  field-level check: hard mismatches (channel order/S-parameter identity,
  exact frequency axis, domain, calibrated-domain profile binding, device
  port/channel contract) yield ``incompatible``; soft differences (device
  id, software version, age, environment note) yield warnings only.

Storage layer: depends on core + calibration models only (AGENTS.md §9);
imports no Qt, hardware, or network module; raises core ``DomainError``
with existing ``ErrorCode`` values only.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np

from uav_gpr.calibration.osl import OslCalibrationProfile, OslCalibrationQuality
from uav_gpr.calibration.reference import AirBackgroundReference, ReferenceDomain
from uav_gpr.core.channels import ChannelSpec
from uav_gpr.core.enums import LogicalPolarization, SParameter
from uav_gpr.core.errors import DomainError, ErrorCode, JsonValue
from uav_gpr.core.identifiers import CalibrationProfileId, DeviceId
from uav_gpr.core.timeutil import from_utc_iso, to_utc_iso

__all__ = [
    "BACKGROUND_EXTENSION",
    "BACKGROUND_FORMAT_NAME",
    "CALIBRATION_EXTENSION",
    "CALIBRATION_FORMAT_NAME",
    "SCHEMA_VERSION",
    "AirBackgroundFilePayload",
    "CompatibilityCheck",
    "CompatibilityContext",
    "CompatibilityField",
    "CompatibilityResult",
    "CompatibilitySeverity",
    "CompatibilityVerdict",
    "OslCalibrationFilePayload",
    "StoredOslProfile",
    "WriteConflictError",
    "check_air_background_compatibility",
    "check_osl_compatibility",
    "read_air_background_file",
    "read_osl_calibration_file",
    "write_air_background_file",
    "write_osl_calibration_file",
]

#: The only schema version this reader/writer pair understands.
SCHEMA_VERSION = 1

CALIBRATION_FORMAT_NAME = "uav_gpr_rcal"
BACKGROUND_FORMAT_NAME = "uav_gpr_rcbg"
CALIBRATION_EXTENSION = ".rcal"
BACKGROUND_EXTENSION = ".rcbg"

_FREQUENCY_UNIT = "Hz"


class WriteConflictError(OSError):
    """A target file already exists; nothing was written or overwritten."""


# ---------------------------------------------------------------------------
# strict JSON helpers
# ---------------------------------------------------------------------------


def _reject_constant(name: str) -> None:
    raise ValueError(f"JSON constant {name!r} is not allowed")


def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _digest_of(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _number_escape_field(value: object, path: str = "$") -> str | None:
    """Locate the first non-finite float that leaked in as a JSON literal.

    ``1e999``/``-1e999`` are valid JSON number tokens: Python's parser turns
    them into inf/-inf without ever invoking ``parse_constant``, so they can
    only be caught by walking the decoded document.  Returns the dotted
    field path of the offender (``None`` when the document is clean).
    """
    if isinstance(value, float):
        return None if math.isfinite(value) else path
    if isinstance(value, dict):
        for key, item in value.items():
            found = _number_escape_field(item, f"{path}.{key}")
            if found is not None:
                return found
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found = _number_escape_field(item, f"{path}[{index}]")
            if found is not None:
                return found
    return None


def _invalid(field_path: str, reason: str, **context: JsonValue) -> DomainError:
    return DomainError(
        ErrorCode.INVALID_ARGUMENT,
        f"{field_path}: {reason}",
        {"field": field_path, **context},
    )


def _require_dict(value: object, field_path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _invalid(field_path, f"must be an object, got {type(value).__name__}")
    return value


def _require_str(value: object, field_path: str) -> str:
    if not isinstance(value, str) or not value:
        raise _invalid(field_path, "must be a non-empty string")
    return value


def _require_int(value: object, field_path: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _invalid(
            field_path, f"must be an integer >= {minimum}, got {type(value).__name__}"
        )
    if value < minimum:
        raise _invalid(field_path, f"must be >= {minimum}, got {value}")
    return value


def _finite_float(value: object, field_path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _invalid(field_path, "must be a JSON number")
    out = float(value)
    if not math.isfinite(out):
        raise _invalid(field_path, "must be finite")
    return out


def _number_list(value: object, field_path: str, *, min_length: int = 1) -> list[float]:
    if not isinstance(value, list):
        raise _invalid(field_path, "must be a JSON array")
    if len(value) < min_length:
        raise _invalid(field_path, f"must contain at least {min_length} entries")
    return [_finite_float(item, f"{field_path}[{index}]") for index, item in enumerate(value)]


def _encode_array(values: np.ndarray, dtype_tag: str) -> dict[str, JsonValue]:
    arr = np.asarray(values)
    expected = np.complex128 if dtype_tag == "complex128" else np.float64
    if arr.dtype != expected:
        try:
            arr = np.asarray(arr, dtype=expected)
        except (TypeError, ValueError) as exc:
            raise _invalid("array", f"cannot encode as {dtype_tag}: {exc}") from exc
    if arr.ndim == 0 or arr.size == 0:
        raise _invalid("array", "must have at least one element")
    flat = arr.reshape(-1)
    if not np.all(np.isfinite(flat)):
        bad = int(np.argmax(~np.isfinite(flat)))
        raise _invalid("array", f"contains a non-finite value at flat index {bad}")
    payload: dict[str, JsonValue] = {
        "dtype": dtype_tag,
        "shape": [int(n) for n in arr.shape],
        "re": [float(v) for v in flat.real],
    }
    if dtype_tag == "complex128":
        payload["im"] = [float(v) for v in flat.imag]
    return payload


def _decode_array(node: object, field_path: str, dtype_tag: str) -> np.ndarray:
    obj = _require_dict(node, field_path)
    if obj.get("dtype") != dtype_tag:
        raise _invalid(field_path, f"dtype must be {dtype_tag!r}",
                       found=obj.get("dtype"))
    shape_raw = obj.get("shape")
    if not isinstance(shape_raw, list) or not shape_raw:
        raise _invalid(field_path, "shape must be a non-empty array")
    shape = tuple(
        _require_int(item, f"{field_path}.shape[{i}]", minimum=1)
        for i, item in enumerate(shape_raw)
    )
    re = _number_list(obj.get("re"), f"{field_path}.re")
    size = int(np.prod(shape))
    if len(re) != size:
        raise _invalid(field_path, f"re length {len(re)} does not match shape size {size}")
    if dtype_tag == "float64":
        if "im" in obj:
            raise _invalid(field_path, "float64 arrays must not carry an im component")
        return np.asarray(re, dtype=np.float64).reshape(shape)
    im = _number_list(obj.get("im"), f"{field_path}.im")
    if len(im) != len(re):
        raise _invalid(
            field_path, f"imag length {len(im)} does not match real length {len(re)}"
        )
    return (np.asarray(re, dtype=np.float64) + 1j * np.asarray(im, dtype=np.float64)).reshape(
        shape
    )


def _readonly_view(arr: np.ndarray) -> np.ndarray:
    base = np.array(arr, copy=True)
    base.setflags(write=False)
    return base.view()


def _channel_to_dict(channel: ChannelSpec) -> dict[str, JsonValue]:
    return {
        "channel_id": channel.channel_id,
        "logical_polarization": channel.logical_polarization.value,
        "s_parameter": channel.s_parameter.value,
        "display_name": channel.display_name,
        "antenna_note": channel.antenna_note,
    }


def _channel_from_dict(node: object, field_path: str) -> ChannelSpec:
    obj = _require_dict(node, field_path)
    antenna_note = obj.get("antenna_note")
    if antenna_note is not None and not isinstance(antenna_note, str):
        raise _invalid(f"{field_path}.antenna_note", "must be a string or null")
    try:
        polarization = LogicalPolarization.from_value(
            _require_str(obj.get("logical_polarization"), f"{field_path}.logical_polarization")
        )
        s_parameter = SParameter.from_value(
            _require_str(obj.get("s_parameter"), f"{field_path}.s_parameter")
        )
    except ValueError as exc:
        raise _invalid(field_path, str(exc)) from exc
    try:
        return ChannelSpec(
            channel_id=_require_str(obj.get("channel_id"), f"{field_path}.channel_id"),
            logical_polarization=polarization,
            s_parameter=s_parameter,
            display_name=_require_str(obj.get("display_name"), f"{field_path}.display_name"),
            antenna_note=antenna_note,
        )
    except (ValueError, TypeError) as exc:
        raise _invalid(field_path, str(exc)) from exc


def _config_sha(value: object, field_path: str) -> str | None:
    if value is None:
        return None
    text = _require_str(value, field_path)
    if len(text) != 64 or any(c not in "0123456789abcdef" for c in text):
        raise _invalid(field_path, "must be a 64-character lowercase hex digest")
    return text


# ---------------------------------------------------------------------------
# StoredOslProfile: persisted mirror of an OslCalibrationProfile
# ---------------------------------------------------------------------------


class StoredOslProfile:
    """Immutable, self-contained mirror of one solved OSL profile.

    Field validation mirrors :mod:`uav_gpr.calibration.osl`: every array is
    finite, 1-D, and shares one frequency-axis length; capture counts are
    positive.  Instances never alias caller arrays (defensive copies,
    write-protected bases exposed as read-only views).
    """

    _profile_id: CalibrationProfileId
    _channel: ChannelSpec
    _frequency_hz: np.ndarray
    _open_measured_mean: np.ndarray
    _short_measured_mean: np.ndarray
    _load_measured_mean: np.ndarray
    _open_actual: np.ndarray
    _short_actual: np.ndarray
    _load_actual: np.ndarray
    _directivity: np.ndarray
    _reflection_tracking: np.ndarray
    _source_match: np.ndarray
    _open_capture_count: int
    _short_capture_count: int
    _load_capture_count: int
    _quality: OslCalibrationQuality

    __slots__ = (
        "_channel",
        "_directivity",
        "_frequency_hz",
        "_load_actual",
        "_load_capture_count",
        "_load_measured_mean",
        "_open_actual",
        "_open_capture_count",
        "_open_measured_mean",
        "_profile_id",
        "_quality",
        "_reflection_tracking",
        "_short_actual",
        "_short_capture_count",
        "_short_measured_mean",
        "_source_match",
    )

    def __init__(
        self,
        *,
        profile_id: CalibrationProfileId,
        channel: ChannelSpec,
        frequency_hz: object,
        open_measured_mean: object,
        short_measured_mean: object,
        load_measured_mean: object,
        open_actual: object,
        short_actual: object,
        load_actual: object,
        directivity: object,
        reflection_tracking: object,
        source_match: object,
        open_capture_count: int,
        short_capture_count: int,
        load_capture_count: int,
        quality: OslCalibrationQuality,
    ) -> None:
        if not isinstance(profile_id, CalibrationProfileId):
            raise _invalid("profile_id", "must be a CalibrationProfileId")
        if not isinstance(channel, ChannelSpec):
            raise _invalid("channel", "must be a ChannelSpec")
        if channel.s_parameter not in (SParameter.S11, SParameter.S22):
            raise _invalid(
                "channel", "OSL profiles bind to reflection channels only",
                s_parameter=channel.s_parameter.value,
            )
        if not isinstance(quality, OslCalibrationQuality):
            raise _invalid("quality", "must be an OslCalibrationQuality")
        axis = np.asarray(frequency_hz)
        if axis.ndim != 1 or axis.size == 0:
            raise _invalid("frequency_hz", "must be a non-empty 1-D axis")
        count = int(axis.size)
        arrays: dict[str, np.ndarray] = {}
        counts: dict[str, int] = {}
        for name, raw in (
            ("frequency_hz", axis),
            ("open_measured_mean", open_measured_mean),
            ("short_measured_mean", short_measured_mean),
            ("load_measured_mean", load_measured_mean),
            ("open_actual", open_actual),
            ("short_actual", short_actual),
            ("load_actual", load_actual),
            ("directivity", directivity),
            ("reflection_tracking", reflection_tracking),
            ("source_match", source_match),
        ):
            arr = np.asarray(raw)
            if arr.ndim != 1 or arr.size != count:
                raise _invalid(
                    name, f"must be a 1-D array of length {count}",
                    got=None if arr.ndim != 1 else int(arr.size),
                )
            if not np.all(np.isfinite(arr)):
                raise _invalid(name, "contains a non-finite value")
            arrays[name] = arr
        for name, value in (
            ("open_capture_count", open_capture_count),
            ("short_capture_count", short_capture_count),
            ("load_capture_count", load_capture_count),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise _invalid(name, "must be a positive integer")
            counts[name] = value
        for name in arrays:
            object.__setattr__(self, f"_{name}", _readonly_base(arrays[name]))
        for name in counts:
            object.__setattr__(self, f"_{name}", counts[name])
        object.__setattr__(self, "_profile_id", profile_id)
        object.__setattr__(self, "_channel", channel)
        object.__setattr__(self, "_quality", quality)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"StoredOslProfile is immutable: cannot set {name!r}")

    def __eq__(self, other: object) -> bool:
        if type(self) is not type(other):
            return NotImplemented
        assert isinstance(other, StoredOslProfile)
        return self.to_payload() == other.to_payload()

    def __repr__(self) -> str:
        return (
            "StoredOslProfile("
            f"profile_id={self._profile_id!s}, "
            f"channel={self._channel.channel_id!r})"
        )

    @classmethod
    def from_profile(cls, profile: OslCalibrationProfile) -> StoredOslProfile:
        """Snapshot a live I027 profile into its persisted representation."""
        if not isinstance(profile, OslCalibrationProfile):
            raise _invalid("profile", "must be an OslCalibrationProfile")
        return cls(
            profile_id=profile.profile_id,
            channel=profile.channel,
            frequency_hz=profile.frequency_hz,
            open_measured_mean=profile.open_measured_mean,
            short_measured_mean=profile.short_measured_mean,
            load_measured_mean=profile.load_measured_mean,
            open_actual=profile.open_actual,
            short_actual=profile.short_actual,
            load_actual=profile.load_actual,
            directivity=profile.directivity,
            reflection_tracking=profile.reflection_tracking,
            source_match=profile.source_match,
            open_capture_count=profile.open_capture_count,
            short_capture_count=profile.short_capture_count,
            load_capture_count=profile.load_capture_count,
            quality=profile.quality,
        )

    @property
    def profile_id(self) -> CalibrationProfileId:
        return self._profile_id

    @property
    def channel(self) -> ChannelSpec:
        return self._channel

    @property
    def frequency_hz(self) -> np.ndarray:
        return self._frequency_hz.view()

    @property
    def open_measured_mean(self) -> np.ndarray:
        return self._open_measured_mean.view()

    @property
    def short_measured_mean(self) -> np.ndarray:
        return self._short_measured_mean.view()

    @property
    def load_measured_mean(self) -> np.ndarray:
        return self._load_measured_mean.view()

    @property
    def open_actual(self) -> np.ndarray:
        return self._open_actual.view()

    @property
    def short_actual(self) -> np.ndarray:
        return self._short_actual.view()

    @property
    def load_actual(self) -> np.ndarray:
        return self._load_actual.view()

    @property
    def directivity(self) -> np.ndarray:
        return self._directivity.view()

    @property
    def reflection_tracking(self) -> np.ndarray:
        return self._reflection_tracking.view()

    @property
    def source_match(self) -> np.ndarray:
        return self._source_match.view()

    @property
    def open_capture_count(self) -> int:
        return self._open_capture_count

    @property
    def short_capture_count(self) -> int:
        return self._short_capture_count

    @property
    def load_capture_count(self) -> int:
        return self._load_capture_count

    @property
    def quality(self) -> OslCalibrationQuality:
        return self._quality

    def to_payload(self) -> dict[str, JsonValue]:
        return {
            "profile_id": str(self._profile_id),
            "channel": _channel_to_dict(self._channel),
            "s_parameter": self._channel.s_parameter.value,
            "frequency_hz": _encode_array(self._frequency_hz, "float64"),
            "standards": {
                standard: {
                    "measured_mean": _encode_array(
                        getattr(self, f"{standard}_measured_mean"), "complex128"
                    ),
                    "actual": _encode_array(getattr(self, f"{standard}_actual"), "complex128"),
                    "capture_count": getattr(self, f"{standard}_capture_count"),
                }
                for standard in ("open", "short", "load")
            },
            "error_terms": {
                "directivity": _encode_array(self._directivity, "complex128"),
                "reflection_tracking": _encode_array(self._reflection_tracking, "complex128"),
                "source_match": _encode_array(self._source_match, "complex128"),
            },
            "quality": {
                "open_rms_abs_error": float(self._quality.open_rms_abs_error),
                "open_max_abs_error": float(self._quality.open_max_abs_error),
                "short_rms_abs_error": float(self._quality.short_rms_abs_error),
                "short_max_abs_error": float(self._quality.short_max_abs_error),
                "load_rms_abs_error": float(self._quality.load_rms_abs_error),
                "load_max_abs_error": float(self._quality.load_max_abs_error),
                "worst_max_abs_error": float(self._quality.worst_max_abs_error),
                "solve_degenerate": False,
            },
        }

    @classmethod
    def from_payload(cls, node: object, field_path: str) -> StoredOslProfile:
        obj = _require_dict(node, field_path)
        try:
            profile_id = CalibrationProfileId.from_json(
                _require_str(obj.get("profile_id"), f"{field_path}.profile_id")
            )
        except ValueError as exc:
            raise _invalid(f"{field_path}.profile_id", str(exc)) from exc
        channel = _channel_from_dict(obj.get("channel"), f"{field_path}.channel")
        s_param_raw = _require_str(obj.get("s_parameter"), f"{field_path}.s_parameter")
        if s_param_raw != channel.s_parameter.value:
            raise _invalid(
                f"{field_path}.s_parameter",
                "must equal the bound channel's S parameter",
                found=s_param_raw,
                expected=channel.s_parameter.value,
            )
        standards = _require_dict(obj.get("standards"), f"{field_path}.standards")
        error_terms = _require_dict(obj.get("error_terms"), f"{field_path}.error_terms")
        quality_node = _require_dict(obj.get("quality"), f"{field_path}.quality")
        frequency = _decode_array(obj.get("frequency_hz"), f"{field_path}.frequency_hz", "float64")
        if frequency.ndim != 1:
            raise _invalid(f"{field_path}.frequency_hz", "must be a 1-D axis")
        std_arrays: dict[str, tuple[np.ndarray, np.ndarray, int]] = {}
        for standard in ("open", "short", "load"):
            entry = _require_dict(
                standards.get(standard), f"{field_path}.standards.{standard}"
            )
            std_arrays[standard] = (
                _decode_array(
                    entry.get("measured_mean"),
                    f"{field_path}.standards.{standard}.measured_mean",
                    "complex128",
                ),
                _decode_array(
                    entry.get("actual"),
                    f"{field_path}.standards.{standard}.actual",
                    "complex128",
                ),
                _require_int(
                    entry.get("capture_count"),
                    f"{field_path}.standards.{standard}.capture_count",
                    minimum=1,
                ),
            )
        quality = OslCalibrationQuality(
            open_rms_abs_error=_finite_float(
                quality_node.get("open_rms_abs_error"),
                f"{field_path}.quality.open_rms_abs_error",
            ),
            open_max_abs_error=_finite_float(
                quality_node.get("open_max_abs_error"),
                f"{field_path}.quality.open_max_abs_error",
            ),
            short_rms_abs_error=_finite_float(
                quality_node.get("short_rms_abs_error"),
                f"{field_path}.quality.short_rms_abs_error",
            ),
            short_max_abs_error=_finite_float(
                quality_node.get("short_max_abs_error"),
                f"{field_path}.quality.short_max_abs_error",
            ),
            load_rms_abs_error=_finite_float(
                quality_node.get("load_rms_abs_error"),
                f"{field_path}.quality.load_rms_abs_error",
            ),
            load_max_abs_error=_finite_float(
                quality_node.get("load_max_abs_error"),
                f"{field_path}.quality.load_max_abs_error",
            ),
        )
        return cls(
            profile_id=profile_id,
            channel=channel,
            frequency_hz=frequency,
            open_measured_mean=std_arrays["open"][0],
            short_measured_mean=std_arrays["short"][0],
            load_measured_mean=std_arrays["load"][0],
            open_actual=std_arrays["open"][1],
            short_actual=std_arrays["short"][1],
            load_actual=std_arrays["load"][1],
            directivity=_decode_array(
                error_terms.get("directivity"),
                f"{field_path}.error_terms.directivity",
                "complex128",
            ),
            reflection_tracking=_decode_array(
                error_terms.get("reflection_tracking"),
                f"{field_path}.error_terms.reflection_tracking",
                "complex128",
            ),
            source_match=_decode_array(
                error_terms.get("source_match"),
                f"{field_path}.error_terms.source_match",
                "complex128",
            ),
            open_capture_count=std_arrays["open"][2],
            short_capture_count=std_arrays["short"][2],
            load_capture_count=std_arrays["load"][2],
            quality=quality,
        )


def _readonly_base(arr: np.ndarray) -> np.ndarray:
    base = np.array(arr, copy=True)
    base.setflags(write=False)
    return base


# ---------------------------------------------------------------------------
# file payloads
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OslCalibrationFilePayload:
    """One ordered ``.rcal`` document (profiles follow the channel contract)."""

    profiles: tuple[StoredOslProfile, ...]
    created_utc: datetime
    software_version: str
    device_id: DeviceId | None = None
    config_sha256: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.profiles, tuple) or not self.profiles:
            raise _invalid("profiles", "must be a non-empty tuple")
        for index, profile in enumerate(self.profiles):
            if not isinstance(profile, StoredOslProfile):
                raise _invalid(f"profiles[{index}]", "must be a StoredOslProfile")
        seen: set[str] = set()
        first_axis = self.profiles[0].frequency_hz
        for index, profile in enumerate(self.profiles):
            if profile.channel.channel_id in seen:
                raise _invalid(
                    f"profiles[{index}].channel", "duplicate channel in one .rcal",
                    channel_id=profile.channel.channel_id,
                )
            seen.add(profile.channel.channel_id)
            if not np.array_equal(profile.frequency_hz, first_axis):
                raise _invalid(
                    f"profiles[{index}].frequency_hz",
                    "all profiles must share one frequency axis",
                )
        object.__setattr__(self, "created_utc", _aware(self.created_utc))
        if not isinstance(self.software_version, str) or not self.software_version:
            raise _invalid("software_version", "must be a non-empty string")
        if self.device_id is not None and not isinstance(self.device_id, DeviceId):
            raise _invalid("device_id", "must be a DeviceId or None")
        if self.config_sha256 is not None:
            _config_sha(self.config_sha256, "config_sha256")

    @property
    def frequency_hz(self) -> np.ndarray:
        return self.profiles[0].frequency_hz.view()

    @property
    def channels(self) -> tuple[ChannelSpec, ...]:
        return tuple(profile.channel for profile in self.profiles)

    def to_document(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "profile_kind": "osl_set",
            "axis_unit": _FREQUENCY_UNIT,
            "channels": [_channel_to_dict(c) for c in self.channels],
            "frequency_hz": _encode_array(self.frequency_hz, "float64"),
            "profiles": [profile.to_payload() for profile in self.profiles],
            "provenance": {
                "created_utc": to_utc_iso(self.created_utc),
                "software_version": self.software_version,
                "device_id": None if self.device_id is None else str(self.device_id),
                "config_sha256": self.config_sha256,
                "algorithm": "osl_one_port_v1",
            },
        }
        return payload

    @property
    def digest(self) -> str:
        return _digest_of(self.to_document())

    @classmethod
    def from_document(cls, node: object) -> OslCalibrationFilePayload:
        doc = _require_dict(node, "payload")
        if doc.get("profile_kind") != "osl_set":
            raise _invalid("payload.profile_kind", "unknown .rcal kind",
                           found=doc.get("profile_kind"))
        if doc.get("axis_unit") != _FREQUENCY_UNIT:
            raise _invalid("payload.axis_unit", "axis unit must be 'Hz'",
                           found=doc.get("axis_unit"))
        channels_node = doc.get("channels")
        if not isinstance(channels_node, list):
            raise _invalid("payload.channels", "must be a JSON array")
        channels = tuple(
            _channel_from_dict(item, f"payload.channels[{i}]")
            for i, item in enumerate(channels_node)
        )
        axis = _decode_array(doc.get("frequency_hz"), "payload.frequency_hz", "float64")
        if axis.ndim != 1:
            raise _invalid("payload.frequency_hz", "must be a 1-D axis")
        profiles_node = doc.get("profiles")
        if not isinstance(profiles_node, list) or not profiles_node:
            raise _invalid("payload.profiles", "must be a non-empty array")
        profiles = tuple(
            StoredOslProfile.from_payload(item, f"payload.profiles[{i}]")
            for i, item in enumerate(profiles_node)
        )
        if tuple(p.channel for p in profiles) != channels:
            raise _invalid(
                "payload.channels",
                "the channel table must equal the ordered profile channels",
            )
        prov = _require_dict(doc.get("provenance"), "payload.provenance")
        try:
            created = from_utc_iso(
                _require_str(prov.get("created_utc"), "payload.provenance.created_utc")
            )
        except (ValueError, TypeError) as exc:
            raise _invalid("payload.provenance.created_utc", str(exc)) from exc
        device_raw = prov.get("device_id")
        device_id: DeviceId | None = None
        if device_raw is not None:
            try:
                device_id = DeviceId.from_json(
                    _require_str(device_raw, "payload.provenance.device_id")
                )
            except ValueError as exc:
                raise _invalid("payload.provenance.device_id", str(exc)) from exc
        software = _require_str(
            prov.get("software_version"), "payload.provenance.software_version"
        )
        _require_str(prov.get("algorithm"), "payload.provenance.algorithm")
        config_sha = _config_sha(
            prov.get("config_sha256"), "payload.provenance.config_sha256"
        )
        return cls(
            profiles=profiles,
            created_utc=created,
            software_version=software,
            device_id=device_id,
            config_sha256=config_sha,
        )


@dataclass(frozen=True, slots=True)
class AirBackgroundFilePayload:
    """One ``.rcbg`` document: reference + provenance + quality report."""

    reference: AirBackgroundReference
    created_utc: datetime
    software_version: str
    device_id: DeviceId | None = None
    config_sha256: str | None = None
    stability_mad_hz: object = None
    outlier_max_deviation: object = None
    non_finite_rejected_traces: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.reference, AirBackgroundReference):
            raise _invalid("reference", "must be an AirBackgroundReference")
        reference = self.reference
        if (
            isinstance(reference.trace_count, bool)
            or not isinstance(reference.trace_count, int)
            or reference.trace_count < 1
        ):
            raise _invalid(
                "reference.trace_count", "must be a positive integer"
            )
        mean = np.asarray(reference.mean_data)
        if mean.dtype != np.complex128 or not np.all(np.isfinite(mean)):
            raise _invalid(
                "reference.mean_data", "must be a finite complex128 array"
            )
        if not np.all(np.isfinite(reference.frequency_hz)):
            raise _invalid("reference.frequency_hz", "must be finite")
        if mean.shape != (
            len(reference.channels),
            reference.frequency_hz.size,
        ):
            raise _invalid(
                "reference.mean_data",
                "mean data shape must be channel x frequency",
            )
        if reference.domain is ReferenceDomain.OSL_CALIBRATED and (
            reference.calibration_profile_id is None
        ):
            raise _invalid(
                "reference.calibration_profile_id",
                "osl_calibrated domain requires a calibration_profile_id",
            )
        if reference.domain is ReferenceDomain.RAW and (
            reference.calibration_profile_id is not None
        ):
            raise _invalid(
                "reference.calibration_profile_id",
                "raw domain must not declare a calibration_profile_id",
            )
        object.__setattr__(self, "created_utc", _aware(self.created_utc))
        if not isinstance(self.software_version, str) or not self.software_version:
            raise _invalid("software_version", "must be a non-empty string")
        if self.device_id is not None and not isinstance(self.device_id, DeviceId):
            raise _invalid("device_id", "must be a DeviceId or None")
        if self.config_sha256 is not None:
            _config_sha(self.config_sha256, "config_sha256")
        expected = (len(self.reference.channels), self.reference.frequency_hz.size)
        for name in ("stability_mad_hz", "outlier_max_deviation"):
            value = getattr(self, name)
            if value is None:
                continue
            arr = np.asarray(value, dtype=np.float64)
            if arr.shape != expected or not np.all(np.isfinite(arr)):
                raise _invalid(
                    name, f"must be a finite channel x frequency array {expected}"
                )
            object.__setattr__(self, name, _readonly_base(arr))
        count = self.non_finite_rejected_traces
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise _invalid("non_finite_rejected_traces", "must be a non-negative integer")

    @property
    def digest(self) -> str:
        return _digest_of(self.to_document())

    def to_document(self) -> dict[str, JsonValue]:
        reference = self.reference
        payload: dict[str, JsonValue] = {
            "domain": reference.domain.value,
            "calibration_profile_id": (
                None
                if reference.calibration_profile_id is None
                else str(reference.calibration_profile_id)
            ),
            "axis_unit": _FREQUENCY_UNIT,
            "channels": [_channel_to_dict(c) for c in reference.channels],
            "frequency_hz": _encode_array(reference.frequency_hz, "float64"),
            "mean_data": _encode_array(reference.mean_data, "complex128"),
            "provenance": {
                "created_utc": to_utc_iso(self.created_utc),
                "software_version": self.software_version,
                "device_id": None if self.device_id is None else str(self.device_id),
                "config_sha256": self.config_sha256,
                "algorithm": "air_background_mean_v1",
            },
            "quality": {
                "trace_count": reference.trace_count,
                "non_finite_rejected_traces": self.non_finite_rejected_traces,
                "stability_mad": (
                    None
                    if self.stability_mad_hz is None
                    else _encode_array(np.asarray(self.stability_mad_hz), "float64")
                ),
                "outlier_max_deviation": (
                    None
                    if self.outlier_max_deviation is None
                    else _encode_array(
                        np.asarray(self.outlier_max_deviation), "float64"
                    )
                ),
            },
        }
        return payload

    @classmethod
    def from_document(cls, node: object) -> AirBackgroundFilePayload:
        doc = _require_dict(node, "payload")
        domain_raw = _require_str(doc.get("domain"), "payload.domain")
        try:
            domain = ReferenceDomain.from_value(domain_raw)
        except ValueError as exc:
            raise _invalid("payload.domain", str(exc)) from exc
        profile_raw = doc.get("calibration_profile_id")
        profile_id: CalibrationProfileId | None = None
        if profile_raw is not None:
            try:
                profile_id = CalibrationProfileId.from_json(
                    _require_str(profile_raw, "payload.calibration_profile_id")
                )
            except ValueError as exc:
                raise _invalid("payload.calibration_profile_id", str(exc)) from exc
        if doc.get("axis_unit") != _FREQUENCY_UNIT:
            raise _invalid("payload.axis_unit", "axis unit must be 'Hz'",
                           found=doc.get("axis_unit"))
        channels_node = doc.get("channels")
        if not isinstance(channels_node, list) or not channels_node:
            raise _invalid("payload.channels", "must be a non-empty array")
        channels = tuple(
            _channel_from_dict(item, f"payload.channels[{i}]")
            for i, item in enumerate(channels_node)
        )
        frequency = _decode_array(doc.get("frequency_hz"), "payload.frequency_hz", "float64")
        if frequency.ndim != 1:
            raise _invalid("payload.frequency_hz", "must be a 1-D axis")
        mean = _decode_array(doc.get("mean_data"), "payload.mean_data", "complex128")
        if mean.ndim != 2 or mean.shape != (len(channels), frequency.size):
            raise _invalid(
                "payload.mean_data",
                f"must be a {len(channels)}x{frequency.size} matrix",
                got=[int(x) for x in mean.shape],
            )
        quality = _require_dict(doc.get("quality"), "payload.quality")
        trace_count = _require_int(
            quality.get("trace_count"), "payload.quality.trace_count", minimum=1
        )
        rejected = _require_int(
            quality.get("non_finite_rejected_traces"),
            "payload.quality.non_finite_rejected_traces",
            minimum=0,
        )
        stability_node = quality.get("stability_mad")
        outlier_node = quality.get("outlier_max_deviation")
        stability = (
            None
            if stability_node is None
            else _decode_array(stability_node, "payload.quality.stability_mad", "float64")
        )
        outliers = (
            None
            if outlier_node is None
            else _decode_array(
                outlier_node, "payload.quality.outlier_max_deviation", "float64"
            )
        )
        prov = _require_dict(doc.get("provenance"), "payload.provenance")
        try:
            created = from_utc_iso(
                _require_str(prov.get("created_utc"), "payload.provenance.created_utc")
            )
        except (ValueError, TypeError) as exc:
            raise _invalid("payload.provenance.created_utc", str(exc)) from exc
        device_raw = prov.get("device_id")
        device_id: DeviceId | None = None
        if device_raw is not None:
            try:
                device_id = DeviceId.from_json(
                    _require_str(device_raw, "payload.provenance.device_id")
                )
            except ValueError as exc:
                raise _invalid("payload.provenance.device_id", str(exc)) from exc
        software = _require_str(
            prov.get("software_version"), "payload.provenance.software_version"
        )
        _require_str(prov.get("algorithm"), "payload.provenance.algorithm")
        config_sha = _config_sha(prov.get("config_sha256"), "payload.provenance.config_sha256")
        # Rebuild with write-protected bases: property accessors hand out
        # views that can never be re-enabled for writing, so a loaded
        # reference is as immutable as one solved in memory.
        reference = AirBackgroundReference(
            channels=channels,
            frequency_hz=_readonly_base(np.asarray(frequency, dtype=np.float64)),
            mean_data=_readonly_base(np.asarray(mean, dtype=np.complex128)),
            trace_count=trace_count,
            domain=domain,
            calibration_profile_id=profile_id,
        )
        object.__setattr__(reference, "frequency_hz", _readonly_view(reference.frequency_hz))
        object.__setattr__(reference, "mean_data", _readonly_view(reference.mean_data))
        return cls(
            reference=reference,
            created_utc=created,
            software_version=software,
            device_id=device_id,
            config_sha256=config_sha,
            stability_mad_hz=stability,
            outlier_max_deviation=outliers,
            non_finite_rejected_traces=rejected,
        )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise _invalid("created_utc", "must be a timezone-aware UTC datetime")
    return value.astimezone(UTC)


# ---------------------------------------------------------------------------
# envelope writer / reader
# ---------------------------------------------------------------------------


def _write_envelope(
    payload_doc: Mapping[str, Any], target: Path, extension: str, format_name: str
) -> Path:
    if target.suffix != extension:
        raise _invalid("path", f"target suffix must be {extension!r}", found=target.suffix)
    if target.exists():
        raise WriteConflictError(f"refusing to overwrite {target}")
    document: dict[str, JsonValue] = {
        "format_name": format_name,
        "schema_version": SCHEMA_VERSION,
        "payload": dict(payload_doc),
        "content_sha256": _digest_of(payload_doc),
    }
    tmp = target.with_name(f".{target.name}.tmp-{uuid.uuid4().hex}")
    try:
        text = json.dumps(document, ensure_ascii=False, allow_nan=False, indent=2)
    except (TypeError, ValueError) as exc:
        raise _invalid("payload", f"serialization failed: {exc}") from exc
    try:
        with tmp.open("x", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        if isinstance(exc, FileExistsError):
            raise WriteConflictError(f"refusing to overwrite {target}") from exc
        raise _invalid("path", f"write failed: {exc}") from exc
    return target


def _read_envelope(source: Path, expected_format: str) -> dict[str, Any]:
    if source.suffix != (
        CALIBRATION_EXTENSION
        if expected_format == CALIBRATION_FORMAT_NAME
        else BACKGROUND_EXTENSION
    ):
        raise _invalid("path", f"source suffix must match {expected_format}")
    if not source.is_file():
        raise FileNotFoundError(f"reference file not found: {source}")
    try:
        text = source.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise _invalid("file", f"not valid UTF-8: {exc}") from exc
    except OSError as exc:
        raise _invalid("file", f"unreadable: {exc}") from exc
    try:
        document = json.loads(text, parse_constant=_reject_constant)
    except ValueError as exc:
        raise _invalid("file", f"corrupted JSON: {exc}") from exc
    escape_field = _number_escape_field(document)
    if escape_field is not None:
        # 1e999-style literals parsed into inf/-inf: fail closed with the
        # structured contract instead of leaking a bare ValueError from the
        # later allow_nan=False canonical re-encoding.
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "corrupted JSON: out-of-range numeric literal parsed to a "
            "non-finite value",
            {"field": escape_field, "kind": "unparseable_numeric"},
        )
    doc = _require_dict(document, "$")
    format_name = doc.get("format_name")
    version = doc.get("schema_version")
    supported = (
        format_name == expected_format
        and isinstance(version, int)
        and not isinstance(version, bool)
        and version == SCHEMA_VERSION
    )
    if not supported:
        raise DomainError(
            ErrorCode.UNSUPPORTED_SCHEMA_VERSION,
            "unsupported reference file format/schema",
            {
                "found_format": format_name if isinstance(format_name, str) else None,
                "found": version
                if isinstance(version, (int, float, str)) or isinstance(version, bool)
                else None,
                "expected_format": expected_format,
                "expected_schema_version": SCHEMA_VERSION,
            },
        )
    payload = _require_dict(doc.get("payload"), "payload")
    stored_digest = _require_str(doc.get("content_sha256"), "content_sha256")
    if len(stored_digest) != 64 or any(c not in "0123456789abcdef" for c in stored_digest):
        raise _invalid("content_sha256", "must be a 64-character lowercase hex digest")
    computed = _digest_of(payload)
    if computed != stored_digest:
        raise _invalid(
            "content_sha256",
            "content digest mismatch (tampered or corrupted payload)",
            stored_digest=stored_digest,
            computed_digest=computed,
        )
    return payload


def write_osl_calibration_file(payload: OslCalibrationFilePayload, path: str | Path) -> Path:
    """Atomically write an ``.rcal`` file (never overwrites; fail-closed)."""
    if not isinstance(payload, OslCalibrationFilePayload):
        raise _invalid("payload", "must be an OslCalibrationFilePayload")
    return _write_envelope(
        payload.to_document(), Path(path), CALIBRATION_EXTENSION, CALIBRATION_FORMAT_NAME
    )


def read_osl_calibration_file(path: str | Path) -> OslCalibrationFilePayload:
    """Read and strictly validate an ``.rcal`` file (digest checked first)."""
    return OslCalibrationFilePayload.from_document(
        _read_envelope(Path(path), CALIBRATION_FORMAT_NAME)
    )


def write_air_background_file(payload: AirBackgroundFilePayload, path: str | Path) -> Path:
    """Atomically write an ``.rcbg`` file (never overwrites; fail-closed)."""
    if not isinstance(payload, AirBackgroundFilePayload):
        raise _invalid("payload", "must be an AirBackgroundFilePayload")
    return _write_envelope(
        payload.to_document(), Path(path), BACKGROUND_EXTENSION, BACKGROUND_FORMAT_NAME
    )


def read_air_background_file(path: str | Path) -> AirBackgroundFilePayload:
    """Read and strictly validate an ``.rcbg`` file (digest checked first)."""
    return AirBackgroundFilePayload.from_document(
        _read_envelope(Path(path), BACKGROUND_FORMAT_NAME)
    )


# ---------------------------------------------------------------------------
# field-level compatibility results
# ---------------------------------------------------------------------------


class CompatibilityVerdict(StrEnum):
    COMPATIBLE = "compatible"
    COMPATIBLE_WITH_WARNINGS = "compatible_with_warnings"
    INCOMPATIBLE = "incompatible"


class CompatibilitySeverity(StrEnum):
    HARD = "hard"
    SOFT = "soft"


class CompatibilityField(StrEnum):
    FORMAT = "format"
    CHANNELS = "channels"
    FREQUENCY_HZ = "frequency_hz"
    CONFIG_SHA256 = "config_sha256"
    DOMAIN = "domain"
    CALIBRATION_PROFILE_ID = "calibration_profile_id"
    DEVICE_ID = "device_id"
    SOFTWARE_VERSION = "software_version"
    AGE_DAYS = "age_days"
    ENVIRONMENT_NOTE = "environment_note"


@dataclass(frozen=True, slots=True)
class CompatibilityCheck:
    field: CompatibilityField
    severity: CompatibilitySeverity
    matched: bool
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class CompatibilityContext:
    """The current task context a reference file would be enabled against.

    Building a context is a pure description step: it never enables or
    applies anything (docs/CALIBRATION.md §8).
    """

    channels: tuple[ChannelSpec, ...]
    frequency_hz: np.ndarray
    config_sha256: str | None = None
    device_id: DeviceId | None = None
    software_version: str | None = None
    created_utc: datetime | None = None
    now: datetime | None = None
    max_age_days: float | None = None
    environment_note: str | None = None
    domain: ReferenceDomain | None = None
    calibration_profile_id: CalibrationProfileId | None = None

    def __post_init__(self) -> None:
        channels = tuple(self.channels)
        if not channels:
            raise _invalid("channels", "must be a non-empty tuple")
        object.__setattr__(self, "channels", channels)
        axis = np.asarray(self.frequency_hz, dtype=np.float64)
        if axis.ndim != 1 or axis.size == 0:
            raise _invalid("frequency_hz", "must be a non-empty 1-D axis")
        object.__setattr__(self, "frequency_hz", _readonly_base(axis))

    @classmethod
    def from_payload(
        cls,
        payload: OslCalibrationFilePayload | AirBackgroundFilePayload,
        *,
        now: datetime | None = None,
        environment_note: str | None = None,
        max_age_days: float | None = None,
    ) -> CompatibilityContext:
        """Mirror a loaded file into a matching context (identity defaults)."""
        if isinstance(payload, OslCalibrationFilePayload):
            return cls(
                channels=payload.channels,
                frequency_hz=payload.frequency_hz,
                config_sha256=payload.config_sha256,
                device_id=payload.device_id,
                software_version=payload.software_version,
                created_utc=payload.created_utc,
                now=now,
                environment_note=environment_note,
                max_age_days=max_age_days,
            )
        if isinstance(payload, AirBackgroundFilePayload):
            return cls(
                channels=payload.reference.channels,
                frequency_hz=payload.reference.frequency_hz,
                config_sha256=payload.config_sha256,
                device_id=payload.device_id,
                software_version=payload.software_version,
                created_utc=payload.created_utc,
                now=now,
                environment_note=environment_note,
                max_age_days=max_age_days,
                domain=payload.reference.domain,
                calibration_profile_id=payload.reference.calibration_profile_id,
            )
        raise _invalid("payload", "must be a file payload")


@dataclass(frozen=True, slots=True)
class CompatibilityResult:
    verdict: CompatibilityVerdict
    checks: tuple[CompatibilityCheck, ...] = field(default_factory=tuple)

    @property
    def hard_mismatches(self) -> tuple[CompatibilityCheck, ...]:
        return tuple(
            check
            for check in self.checks
            if check.severity is CompatibilitySeverity.HARD and not check.matched
        )

    @property
    def warnings(self) -> tuple[CompatibilityCheck, ...]:
        return tuple(
            check
            for check in self.checks
            if check.severity is CompatibilitySeverity.SOFT and not check.matched
        )


def _compare_common(
    checks: list[CompatibilityCheck],
    *,
    want_channels: tuple[ChannelSpec, ...],
    have_channels: tuple[ChannelSpec, ...],
    want_axis: np.ndarray,
    have_axis: np.ndarray,
    want_config: str | None,
    have_config: str | None,
    context: CompatibilityContext,
    payload_created: datetime,
    payload_device: DeviceId | None,
    payload_software: str,
) -> None:
    checks.append(
        CompatibilityCheck(
            field=CompatibilityField.CHANNELS,
            severity=CompatibilitySeverity.HARD,
            matched=have_channels == want_channels,
            detail=(
                None
                if have_channels == want_channels
                else "channel/S-parameter order differs: "
                f"file={[c.channel_id for c in have_channels]}, "
                f"task={[c.channel_id for c in want_channels]}"
            ),
        )
    )
    axis_ok = have_axis.shape == want_axis.shape and bool(
        np.array_equal(have_axis, want_axis)
    )
    checks.append(
        CompatibilityCheck(
            field=CompatibilityField.FREQUENCY_HZ,
            severity=CompatibilitySeverity.HARD,
            matched=axis_ok,
            detail=(
                None
                if axis_ok
                else f"frequency axis differs (points {have_axis.size} vs "
                f"{want_axis.size}; exact pointwise equality required)"
            ),
        )
    )
    if context.config_sha256 is not None and have_config is not None:
        checks.append(
            CompatibilityCheck(
                field=CompatibilityField.CONFIG_SHA256,
                severity=CompatibilitySeverity.HARD,
                matched=have_config == want_config,
                detail=(
                    None
                    if have_config == want_config
                    else "source configuration digest differs"
                ),
            )
        )
    # soft fields -----------------------------------------------------------
    checks.append(
        CompatibilityCheck(
            field=CompatibilityField.DEVICE_ID,
            severity=CompatibilitySeverity.SOFT,
            matched=context.device_id is None or payload_device == context.device_id,
            detail=(
                None
                if context.device_id is None or payload_device == context.device_id
                else f"captured on device {payload_device}, task uses {context.device_id}"
            ),
        )
    )
    checks.append(
        CompatibilityCheck(
            field=CompatibilityField.SOFTWARE_VERSION,
            severity=CompatibilitySeverity.SOFT,
            matched=context.software_version is None
            or payload_software == context.software_version,
            detail=(
                None
                if context.software_version is None
                or payload_software == context.software_version
                else f"created by software {payload_software}, task runs {context.software_version}"
            ),
        )
    )
    if context.now is not None and context.max_age_days is not None:
        age_days = (context.now - payload_created).total_seconds() / 86_400.0
        stale = age_days > context.max_age_days
        checks.append(
            CompatibilityCheck(
                field=CompatibilityField.AGE_DAYS,
                severity=CompatibilitySeverity.SOFT,
                matched=not stale,
                detail=(
                    None
                    if not stale
                    else f"reference is {age_days:.1f} days old (budget {context.max_age_days})"
                ),
            )
        )
    if context.environment_note is not None:
        checks.append(
            CompatibilityCheck(
                field=CompatibilityField.ENVIRONMENT_NOTE,
                severity=CompatibilitySeverity.SOFT,
                matched=False,
                detail=(
                    "current environment recorded explicitly for audit: "
                    f"{context.environment_note!r} (capture-time environment is not "
                    "stored in this file and must be compared manually)"
                ),
            )
        )


def _verdict(checks: tuple[CompatibilityCheck, ...]) -> CompatibilityVerdict:
    result = CompatibilityResult(verdict=CompatibilityVerdict.COMPATIBLE, checks=checks)
    if result.hard_mismatches:
        return CompatibilityVerdict.INCOMPATIBLE
    if result.warnings:
        return CompatibilityVerdict.COMPATIBLE_WITH_WARNINGS
    return CompatibilityVerdict.COMPATIBLE


def check_osl_compatibility(
    payload: OslCalibrationFilePayload, context: CompatibilityContext
) -> CompatibilityResult:
    """Field-level verdict for enabling a loaded ``.rcal`` against a task."""
    if not isinstance(payload, OslCalibrationFilePayload):
        raise _invalid("payload", "must be an OslCalibrationFilePayload")
    checks: list[CompatibilityCheck] = []
    _compare_common(
        checks,
        want_channels=context.channels,
        have_channels=payload.channels,
        want_axis=np.asarray(context.frequency_hz),
        have_axis=payload.frequency_hz,
        want_config=context.config_sha256,
        have_config=payload.config_sha256,
        context=context,
        payload_created=payload.created_utc,
        payload_device=payload.device_id,
        payload_software=payload.software_version,
    )
    return CompatibilityResult(verdict=_verdict(tuple(checks)), checks=tuple(checks))


def check_air_background_compatibility(
    payload: AirBackgroundFilePayload, context: CompatibilityContext
) -> CompatibilityResult:
    """Field-level verdict for enabling a loaded ``.rcbg`` against a task."""
    if not isinstance(payload, AirBackgroundFilePayload):
        raise _invalid("payload", "must be an AirBackgroundFilePayload")
    checks: list[CompatibilityCheck] = []
    reference = payload.reference
    checks.append(
        CompatibilityCheck(
            field=CompatibilityField.DOMAIN,
            severity=CompatibilitySeverity.HARD,
            matched=context.domain is None or reference.domain is context.domain,
            detail=(
                None
                if context.domain is None or reference.domain is context.domain
                else f"reference domain is {reference.domain.value}, "
                f"task expects {context.domain.value}"
            ),
        )
    )
    if reference.domain is ReferenceDomain.OSL_CALIBRATED:
        wanted = context.calibration_profile_id
        checks.append(
            CompatibilityCheck(
                field=CompatibilityField.CALIBRATION_PROFILE_ID,
                severity=CompatibilitySeverity.HARD,
                matched=wanted is not None
                and reference.calibration_profile_id == wanted,
                detail=(
                    None
                    if wanted is not None and reference.calibration_profile_id == wanted
                    else "calibrated-domain background must bind the exact active "
                    "calibration profile id"
                ),
            )
        )
    _compare_common(
        checks,
        want_channels=context.channels,
        have_channels=reference.channels,
        want_axis=np.asarray(context.frequency_hz),
        have_axis=reference.frequency_hz,
        want_config=context.config_sha256,
        have_config=payload.config_sha256,
        context=context,
        payload_created=payload.created_utc,
        payload_device=payload.device_id,
        payload_software=payload.software_version,
    )
    return CompatibilityResult(verdict=_verdict(tuple(checks)), checks=tuple(checks))
