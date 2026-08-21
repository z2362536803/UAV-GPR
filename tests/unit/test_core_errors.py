"""Tests for structured domain errors (ISSUE-003)."""

from __future__ import annotations

import pytest

from uav_gpr.core import DomainError, ErrorCode


def test_domain_error_exposes_code_message_and_context() -> None:
    error = DomainError(ErrorCode.SHAPE_MISMATCH, "invalid array shape", {"got": [1, 2]})
    assert error.code is ErrorCode.SHAPE_MISMATCH
    assert error.message == "invalid array shape"
    assert error.context["got"] == [1, 2]
    assert error.context == {"got": [1, 2]}
    assert ErrorCode.SHAPE_MISMATCH.value == "shape_mismatch"
    assert "shape_mismatch" in str(error)


def test_json_round_trip_preserves_code_message_context() -> None:
    original = DomainError(
        ErrorCode.GNSS_UNAVAILABLE,
        "no valid position for this trace",
        {"trace_index": 7, "status": "stale"},
    )
    payload = original.to_dict()
    restored = DomainError.from_dict(payload)
    assert restored == original
    assert restored.code is ErrorCode.GNSS_UNAVAILABLE
    assert restored.to_dict() == payload


def test_safe_display_message_must_be_ascii() -> None:
    # Business flow must never branch on localized exception text.
    with pytest.raises(ValueError, match="ASCII"):
        DomainError(ErrorCode.SHAPE_MISMATCH, "无效的数组形状")
    with pytest.raises(ValueError, match="non-empty"):
        DomainError(ErrorCode.SHAPE_MISMATCH, "")


def test_context_must_be_json_safe() -> None:
    DomainError(ErrorCode.INVALID_ARGUMENT, "nested context", {"a": [1, None, True]})
    with pytest.raises(TypeError, match="not JSON safe"):
        DomainError(ErrorCode.INVALID_ARGUMENT, "set is not safe", {"a": {1, 2}})  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="not finite"):
        DomainError(ErrorCode.INVALID_ARGUMENT, "nan is not safe", {"a": float("nan")})
    with pytest.raises(TypeError, match="not finite"):
        DomainError(ErrorCode.INVALID_ARGUMENT, "inf is not safe", {"a": float("inf")})
    with pytest.raises(TypeError, match="must be a str"):
        DomainError(ErrorCode.INVALID_ARGUMENT, "key type", {1: "x"})  # type: ignore[arg-type]


def test_from_dict_rejects_unknown_code() -> None:
    with pytest.raises(ValueError, match="unknown ErrorCode"):
        DomainError.from_dict(
            {"code": "no_such_code", "message": "boom", "context": {}}
        )


def test_business_logic_branches_on_code_not_message() -> None:
    try:
        raise DomainError(ErrorCode.ID_CONFLICT, "trace hash mismatch", {"hash": "x"})
    except DomainError as error:
        # Identical branching for any display language is guaranteed by code.
        assert error.code is ErrorCode.ID_CONFLICT
        assert error.code.value == "id_conflict"
    else:
        raise AssertionError("DomainError was not raised")


def test_equality_with_other_types_is_false() -> None:
    error = DomainError(ErrorCode.NAIVE_DATETIME, "naive datetime")
    assert error != "naive datetime"
    assert error != DomainError(ErrorCode.INVALID_ARGUMENT, "naive datetime")
