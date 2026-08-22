"""Structured domain errors.

Business logic must branch on ``DomainError.code`` (a stable machine code),
never on the human-readable ``message`` text.  ``message`` is a safe display
string: ASCII-only, no secrets, no raw context.  Structured context is a
JSON-safe mapping, and the whole error round-trips through ``to_dict()`` /
``from_dict()`` for protocols, logs and UI mapping.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Self

JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


class ErrorCode(StrEnum):
    """Stable machine-readable error codes (never localized)."""

    INVALID_ARGUMENT = "invalid_argument"
    INVALID_UUID = "invalid_uuid"
    SHAPE_MISMATCH = "shape_mismatch"
    AXIS_MISMATCH = "axis_mismatch"
    DUPLICATE_CHANNEL = "duplicate_channel"
    CHANNEL_CONTRACT_MISMATCH = "channel_contract_mismatch"
    NON_INCREASING_AXIS = "non_increasing_axis"
    NON_FINITE_AXIS = "non_finite_axis"
    DTYPE_MISMATCH = "dtype_mismatch"
    NAIVE_DATETIME = "naive_datetime"
    TIME_DOMAIN_MIX = "time_domain_mix"
    ID_CONFLICT = "id_conflict"
    CONFIG_DIGEST_MISMATCH = "config_digest_mismatch"
    CALIBRATION_DOMAIN_MISMATCH = "calibration_domain_mismatch"
    GNSS_UNAVAILABLE = "gnss_unavailable"
    UNSUPPORTED_SCHEMA_VERSION = "unsupported_schema_version"
    UNSUPPORTED_PROTOCOL_VERSION = "unsupported_protocol_version"
    GNSS_MIDPOINT_MISMATCH = "gnss_midpoint_mismatch"


def _require_json_safe(value: Any, path: str = "$") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError(f"context value at {path} is not finite: {value!r}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _require_json_safe(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(
                    f"context key at {path} must be a str, got {type(key).__name__}"
                )
            _require_json_safe(item, f"{path}.{key}")
        return
    raise TypeError(
        f"context value at {path} is not JSON safe: {type(value).__name__}"
    )


def _deep_copy_json(value: Any) -> Any:
    """Recursively copy a JSON-safe value so no caller reference is shared."""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, list):
        return [_deep_copy_json(item) for item in value]
    if isinstance(value, dict):
        return {key: _deep_copy_json(item) for key, item in value.items()}
    raise TypeError(
        f"context value is not JSON safe: {type(value).__name__}"
    )


class DomainError(Exception):
    """A structured domain error with a stable code, safe message and context."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        context: Mapping[str, JsonValue] | None = None,
    ) -> None:
        if not isinstance(code, ErrorCode):
            raise TypeError(f"code must be an ErrorCode, got {type(code).__name__}")
        if not message or not isinstance(message, str):
            raise ValueError("DomainError message must be a non-empty string")
        if not message.isascii():
            raise ValueError("DomainError message must be ASCII (safe display text)")
        _require_json_safe(context or {})
        super().__init__(message)
        self.code = code
        self.message = message
        # Deep copy on construction: later mutation of the caller's input
        # (including nested lists/dicts) can never change this error.
        self._context: Mapping[str, JsonValue] = MappingProxyType(
            _deep_copy_json(dict(context or {}))
        )

    @property
    def context(self) -> Mapping[str, JsonValue]:
        """Independent, read-only snapshot of the structured context.

        Each access deep-copies the stored context, so mutating a returned
        nested list/dict cannot reach the error's internal state.
        """
        return MappingProxyType(_deep_copy_json(dict(self._context)))

    def to_dict(self) -> dict[str, Any]:
        """Independent, plain JSON-safe data (nested values are copied)."""
        return {
            "code": self.code.value,
            "message": self.message,
            "context": _deep_copy_json(dict(self._context)),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        code_raw = data.get("code")
        message = data.get("message")
        context = data.get("context", {})
        if not isinstance(code_raw, str) or not isinstance(message, str):
            raise ValueError("DomainError payload requires 'code' and 'message' strings")
        if not isinstance(context, dict):
            raise ValueError("DomainError payload 'context' must be an object")
        try:
            code = ErrorCode(code_raw)
        except ValueError:
            raise ValueError(f"unknown ErrorCode in payload: {code_raw!r}") from None
        return cls(code, message, context)

    def __str__(self) -> str:
        return f"[{self.code.value}] {self.message}"

    def __repr__(self) -> str:
        return (
            f"DomainError(code={self.code.value!r}, message={self.message!r}, "
            f"context={dict(self._context)!r})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DomainError):
            return NotImplemented
        return (
            self.code == other.code
            and self.message == other.message
            and dict(self._context) == dict(other._context)
        )
