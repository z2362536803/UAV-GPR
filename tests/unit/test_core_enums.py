"""Tests for stable lowercase string enums (ISSUE-003)."""

from __future__ import annotations

import pytest

from uav_gpr.core import (
    EndpointRole,
    GnssStatus,
    LogicalPolarization,
    MissionTerminalState,
    SParameter,
    StableStrEnum,
)


def test_enum_values_are_stable_lowercase_strings() -> None:
    expected = {
        EndpointRole: {"air", "ground"},
        SParameter: {"s11", "s21", "s12", "s22"},
        LogicalPolarization: {"hh", "hv", "vh", "vv"},
        MissionTerminalState: {"completed", "user_stopped", "failed", "crash_recovered"},
        GnssStatus: {
            "disconnected",
            "no_sentence",
            "no_fix",
            "valid",
            "stale",
            "invalid",
        },
    }
    for enum_type, values in expected.items():
        assert isinstance(enum_type, type) and issubclass(enum_type, StableStrEnum)
        assert {member.value for member in enum_type} == values
        for member in enum_type:
            assert isinstance(member.value, str)
            assert member.to_json() == member.value


def test_from_value_round_trip_for_every_member() -> None:
    for enum_type in (
        EndpointRole,
        SParameter,
        LogicalPolarization,
        MissionTerminalState,
        GnssStatus,
    ):
        for member in enum_type:
            assert enum_type.from_value(member.value) is member
            assert enum_type.from_json(member.value) is member


def test_from_value_rejects_unknown_values_and_wrong_case() -> None:
    with pytest.raises(ValueError, match="unknown SParameter value"):
        SParameter.from_value("S11")
    with pytest.raises(ValueError, match="unknown SParameter value"):
        SParameter.from_value("s111")
    with pytest.raises(ValueError, match="unknown SParameter value"):
        SParameter.from_value("")
    with pytest.raises(TypeError):
        SParameter.from_value(11)  # type: ignore[arg-type]


def test_persistence_does_not_depend_on_ordinal_or_member_name() -> None:
    # The persisted value is the declared string, not the Python member name.
    assert SParameter.S11.value == "s11"
    assert LogicalPolarization.HH.value == "hh"
    assert MissionTerminalState.COMPLETED.value == "completed"
    assert GnssStatus.STALE.value == "stale"
    # Values are strings, never the enum ordinal.
    assert not isinstance(SParameter.S11.value, int)
    assert json_payload(SParameter.S11) == "s11"


def json_payload(member: StableStrEnum) -> str:
    """Simulate the persistence boundary: only to_json() crosses it."""
    return member.to_json()
