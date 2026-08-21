"""Tests for stable UUID-backed identifiers (ISSUE-003)."""

from __future__ import annotations

import re
import uuid

import pytest

from uav_gpr.core import (
    AirFileId,
    BackgroundReferenceId,
    CalibrationProfileId,
    CommandId,
    DeviceId,
    GroundFileId,
    MissionId,
    TraceUid,
)

ID_TYPES = [
    MissionId,
    TraceUid,
    DeviceId,
    AirFileId,
    GroundFileId,
    CommandId,
    CalibrationProfileId,
    BackgroundReferenceId,
]

CANONICAL = "01234567-89ab-cdef-0123-456789abcdef"
CANONICAL_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


@pytest.mark.parametrize("identifier_type", ID_TYPES)
def test_new_generates_canonical_unique(identifier_type: type) -> None:
    first = identifier_type.new()
    second = identifier_type.new()
    assert CANONICAL_RE.fullmatch(str(first)) is not None
    assert first.to_json() == str(first)
    assert isinstance(first.as_uuid(), uuid.UUID)
    assert first != second


@pytest.mark.parametrize("identifier_type", ID_TYPES)
def test_parse_round_trip(identifier_type: type) -> None:
    value = identifier_type(CANONICAL)
    assert str(value) == CANONICAL
    assert value.to_json() == CANONICAL
    assert identifier_type.from_json(value.to_json()) == value
    assert identifier_type(uuid.UUID(CANONICAL)) == value
    assert value.as_uuid() == uuid.UUID(CANONICAL)


@pytest.mark.parametrize(
    "bad",
    [
        "0123456789abcdef0123456789abcdef01234567",  # 32 hex chars
        "01234567-89AB-CDEF-0123-456789ABCDEF",  # uppercase
        "{01234567-89ab-cdef-0123-456789abcdef}",  # braces
        "urn:uuid:01234567-89ab-cdef-0123-456789abcdef",  # urn prefix
        "01234567-89ab-cdef-0123-456789abcd",  # too short
        "01234567-89ab-cdef-0123-456789abcdef0",  # too long
        "01234567-89ab-cdef-0123-456789abcdeg",  # non-hex
        " 01234567-89ab-cdef-0123-456789abcdef",  # whitespace
        "",
    ],
)
def test_rejects_non_canonical_strings(bad: str) -> None:
    with pytest.raises(ValueError, match="non-canonical UUID"):
        MissionId(bad)


def test_rejects_non_string_input() -> None:
    with pytest.raises(TypeError):
        MissionId(12345)  # type: ignore[arg-type]


def test_different_id_types_are_not_interchangeable() -> None:
    mission = MissionId(CANONICAL)
    trace = TraceUid(CANONICAL)
    assert mission != trace
    assert trace != mission
    assert mission != CANONICAL
    assert not (mission == CANONICAL)
    assert mission == MissionId(CANONICAL)
    assert hash(MissionId(CANONICAL)) == hash(mission)


def test_identifiers_are_immutable() -> None:
    mission = MissionId(CANONICAL)
    with pytest.raises(AttributeError):
        mission._uuid = uuid.uuid4()  # type: ignore[misc]
