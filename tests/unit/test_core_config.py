"""Tests for immutable mission configuration contracts (ISSUE-006).

Covers: canonical JSON + SHA256 digest determinism, uniform frequency step
and physical time-window derivation, display crop bounds, requested/applied
field-level diff, unit/range validation and deep immutability.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime

import numpy as np
import pytest

from uav_gpr.core import (
    AcquisitionMode,
    BackgroundReferenceId,
    CalibrationProfileId,
    ChannelSpec,
    ConfigDiff,
    ConfigFieldDiff,
    DomainError,
    ErrorCode,
    GnssNoFixPolicy,
    LogicalPolarization,
    MissionConfig,
    SParameter,
)

CREATED_UTC = datetime(2026, 1, 1, tzinfo=UTC)

HH_S11 = ChannelSpec(
    channel_id="hh_s11",
    logical_polarization=LogicalPolarization.HH,
    s_parameter=SParameter.S11,
    display_name="HH S11",
)
VV_S22 = ChannelSpec(
    channel_id="vv_s22",
    logical_polarization=LogicalPolarization.VV,
    s_parameter=SParameter.S22,
    display_name="VV S22",
)
CAL_ID = CalibrationProfileId("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
BG_ID = BackgroundReferenceId("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")


def make_config(**overrides: object) -> MissionConfig:
    base: dict[str, object] = dict(
        frequency_start_hz=1.0e9,
        frequency_stop_hz=2.0e9,
        frequency_points=11,
        if_bw_hz=1_000.0,
        power_dbm=-10.0,
        channels=[HH_S11, VV_S22],
        acquisition_mode=AcquisitionMode.FIXED_COUNT,
        planned_trace_count=100,
        target_interval_s=0.25,
        gnss_max_age_s=2.0,
        gnss_no_fix_policy=GnssNoFixPolicy.RECORD_WITHOUT_POSITION,
        calibration_profile_id=None,
        apply_calibration=False,
        background_reference_id=None,
        apply_background=False,
        display_start_s=0.0,
        display_duration_s=None,
        created_utc=CREATED_UTC,
        note="field test",
        software_version="0.1.0.dev0",
    )
    base.update(overrides)
    return MissionConfig(**base)


# ---------------------------------------------------------------------------
# Normal paths: derivation, digest, round-trip
# ---------------------------------------------------------------------------


def test_full_config_derives_step_physical_window_and_bandwidth() -> None:
    config = make_config()
    assert config.frequency_step_hz == pytest.approx(1.0e8)
    assert config.physical_time_window_s == pytest.approx(1.0e-8)
    assert config.bandwidth_hz == pytest.approx(1.0e9)
    assert config.frequency_points == 11
    assert config.target_interval_s == 0.25
    assert config.power_dbm == -10.0
    assert config.if_bw_hz == 1_000.0


def test_equivalent_configs_produce_same_digest() -> None:
    a = make_config(created_utc=CREATED_UTC, note="first")
    b = make_config(created_utc=datetime(2027, 6, 1, tzinfo=UTC), note="second")
    assert a.config_sha256 == b.config_sha256
    assert a.to_canonical_json() == b.to_canonical_json()


def test_digest_changes_with_channel_order() -> None:
    a = make_config(channels=[HH_S11, VV_S22])
    b = make_config(channels=[VV_S22, HH_S11])
    assert a.config_sha256 != b.config_sha256


def test_digest_changes_with_field_value() -> None:
    a = make_config(power_dbm=-10.0)
    b = make_config(power_dbm=-15.0)
    assert a.config_sha256 != b.config_sha256


def test_canonical_json_is_deterministic_and_sorted() -> None:
    a = make_config()
    b = make_config()
    assert a.to_canonical_json() == b.to_canonical_json()
    assert a.to_canonical_json() == a.to_canonical_json()
    decoded = json.loads(a.to_canonical_json())
    assert decoded == json.loads(a.to_canonical_json())
    assert list(decoded.keys()) == sorted(decoded.keys())


def test_to_dict_from_dict_round_trip() -> None:
    config = make_config(
        calibration_profile_id=CAL_ID,
        apply_calibration=True,
        background_reference_id=BG_ID,
        apply_background=True,
        display_start_s=1.0e-9,
        display_duration_s=2.0e-9,
    )
    restored = MissionConfig.from_dict(config.to_dict())
    assert restored == config
    assert restored.config_sha256 == config.config_sha256
    assert restored.to_dict() == config.to_dict()


def test_from_dict_rejects_tampered_digest() -> None:
    payload = make_config(power_dbm=-10.0).to_dict()
    payload["power_dbm"] = -20.0
    with pytest.raises(DomainError) as excinfo:
        MissionConfig.from_dict(payload)
    assert excinfo.value.code is ErrorCode.CONFIG_DIGEST_MISMATCH


def test_from_frequency_axis_matches_explicit_construction() -> None:
    axis = np.linspace(1.0e9, 2.0e9, 11)
    explicit = make_config()
    via_axis = MissionConfig.from_frequency_axis(
        frequency_axis_hz=axis,
        if_bw_hz=1_000.0,
        power_dbm=-10.0,
        channels=[HH_S11, VV_S22],
        acquisition_mode=AcquisitionMode.FIXED_COUNT,
        planned_trace_count=100,
        target_interval_s=0.25,
        gnss_max_age_s=2.0,
        gnss_no_fix_policy=GnssNoFixPolicy.RECORD_WITHOUT_POSITION,
        display_start_s=0.0,
        display_duration_s=None,
        created_utc=CREATED_UTC,
        note="field test",
        software_version="0.1.0.dev0",
    )
    assert via_axis == explicit
    assert via_axis.config_sha256 == explicit.config_sha256
    assert via_axis.frequency_axis_hz.tolist() == explicit.frequency_axis_hz.tolist()


def test_frequency_axis_property_is_immutable() -> None:
    config = make_config()
    axis = config.frequency_axis_hz
    assert axis.dtype == np.float64
    with pytest.raises(ValueError):
        axis.setflags(write=True)
    with pytest.raises(ValueError):
        axis[0] = 0.0


def test_unit_suffixes_are_explicit() -> None:
    keys = set(make_config().to_dict().keys())
    assert {"frequency_start_hz", "frequency_stop_hz", "if_bw_hz", "power_dbm",
            "target_interval_s", "gnss_max_age_s", "display_start_s",
            "display_duration_s"} <= keys


# ---------------------------------------------------------------------------
# Error paths: axes, ranges, display window
# ---------------------------------------------------------------------------


def make_from_axis(axis: object) -> MissionConfig:
    return MissionConfig.from_frequency_axis(
        frequency_axis_hz=axis,
        if_bw_hz=1_000.0,
        power_dbm=-10.0,
        channels=[HH_S11],
        acquisition_mode=AcquisitionMode.CONTINUOUS,
        planned_trace_count=None,
        target_interval_s=0.25,
        gnss_max_age_s=2.0,
        gnss_no_fix_policy=GnssNoFixPolicy.RECORD_WITHOUT_POSITION,
        created_utc=CREATED_UTC,
        software_version="0.1.0.dev0",
    )


def test_non_uniform_axis_is_rejected() -> None:
    axis = np.array([1.0e9, 1.1e9, 1.2001e9, 1.3e9])
    with pytest.raises(DomainError) as excinfo:
        make_from_axis(axis)
    assert excinfo.value.code is ErrorCode.NON_UNIFORM_AXIS


def test_non_increasing_and_nonfinite_axis_are_rejected() -> None:
    with pytest.raises(DomainError) as excinfo:
        make_from_axis(np.array([1.0e9, 2.0e9, 2.0e9]))
    assert excinfo.value.code is ErrorCode.NON_INCREASING_AXIS
    with pytest.raises(DomainError) as excinfo:
        make_from_axis(np.array([1.0e9, np.nan, 2.0e9]))
    assert excinfo.value.code is ErrorCode.NON_FINITE_AXIS
    with pytest.raises(DomainError) as excinfo:
        make_from_axis(np.array([-1.0e9, -0.5e9]))
    assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT


def test_illegal_ifbw_is_rejected() -> None:
    for bad in (0.0, -100.0, float("nan"), float("inf")):
        with pytest.raises(DomainError):
            make_config(if_bw_hz=bad)


def test_illegal_target_interval_is_rejected() -> None:
    for bad in (0.0, -1.0, float("nan"), float("inf")):
        with pytest.raises(DomainError):
            make_config(target_interval_s=bad)


def test_illegal_frequency_range_is_rejected() -> None:
    with pytest.raises(DomainError):
        make_config(frequency_start_hz=2.0e9, frequency_stop_hz=1.0e9)
    with pytest.raises(DomainError):
        make_config(frequency_start_hz=2.0e9, frequency_stop_hz=2.0e9)
    with pytest.raises(DomainError):
        make_config(frequency_points=1)
    with pytest.raises(DomainError):
        make_config(frequency_start_hz=float("nan"))


def test_illegal_display_window_is_rejected() -> None:
    physical = make_config().physical_time_window_s  # 1e-8
    with pytest.raises(DomainError) as excinfo:
        make_config(display_duration_s=physical * 1.0001)
    assert excinfo.value.code is ErrorCode.OUT_OF_RANGE
    with pytest.raises(DomainError) as excinfo:
        make_config(display_start_s=physical * 0.9, display_duration_s=physical * 0.5)
    assert excinfo.value.code is ErrorCode.OUT_OF_RANGE
    with pytest.raises(DomainError):
        make_config(display_duration_s=0.0)
    with pytest.raises(DomainError):
        make_config(display_duration_s=-1.0)
    with pytest.raises(DomainError):
        make_config(display_start_s=-1.0)
    with pytest.raises(DomainError):
        make_config(display_duration_s=float("nan"))


def test_display_window_at_physical_boundary_is_allowed() -> None:
    physical = make_config().physical_time_window_s
    full = make_config(display_start_s=0.0, display_duration_s=physical)
    assert full.display_duration_s == pytest.approx(physical)
    assert full.display_start_s == 0.0
    # None is normalized to the full physical window.
    default = make_config(display_start_s=0.0, display_duration_s=None)
    assert default.display_duration_s == pytest.approx(physical)


# ---------------------------------------------------------------------------
# Mode, GNSS policy, references
# ---------------------------------------------------------------------------


def test_mode_count_constraints() -> None:
    with pytest.raises(DomainError):
        make_config(
            acquisition_mode=AcquisitionMode.FIXED_COUNT, planned_trace_count=None
        )
    with pytest.raises(DomainError):
        make_config(
            acquisition_mode=AcquisitionMode.CONTINUOUS, planned_trace_count=10
        )
    with pytest.raises(DomainError):
        make_config(
            acquisition_mode=AcquisitionMode.FIXED_COUNT, planned_trace_count=0
        )
    with pytest.raises(TypeError):
        make_config(
            acquisition_mode=AcquisitionMode.FIXED_COUNT, planned_trace_count=True
        )
    continuous = make_config(
        acquisition_mode=AcquisitionMode.CONTINUOUS, planned_trace_count=None
    )
    assert continuous.planned_trace_count is None


def test_gnss_policy_validation() -> None:
    for bad in (0.0, -1.0, float("nan"), float("inf")):
        with pytest.raises(DomainError):
            make_config(gnss_max_age_s=bad)
    with pytest.raises(TypeError):
        make_config(gnss_no_fix_policy="continue")  # type: ignore[arg-type]


def test_reference_consistency() -> None:
    with pytest.raises(DomainError):
        make_config(calibration_profile_id=None, apply_calibration=True)
    with pytest.raises(DomainError):
        make_config(background_reference_id=None, apply_background=True)
    # A reference may be present but not applied (named but disabled);
    # applying always requires the reference ID.
    disabled = make_config(calibration_profile_id=CAL_ID, apply_calibration=False)
    assert disabled.calibration_profile_id == CAL_ID
    assert not disabled.apply_calibration
    config = make_config(
        calibration_profile_id=CAL_ID,
        apply_calibration=True,
        background_reference_id=BG_ID,
        apply_background=True,
    )
    assert config.calibration_profile_id == CAL_ID
    assert config.background_reference_id == BG_ID


def test_channel_validation() -> None:
    with pytest.raises(DomainError):
        make_config(channels=[])
    with pytest.raises(DomainError):
        make_config(channels=[HH_S11, HH_S11])
    with pytest.raises(TypeError):
        make_config(channels=[HH_S11, "vv"])  # type: ignore[list-item]


def test_frozen_and_with_display_window() -> None:
    config = make_config(display_start_s=0.0, display_duration_s=2.0e-9)
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.power_dbm = -20.0  # type: ignore[misc]
    updated = config.with_display_window(start_s=0.0, duration_s=4.0e-9)
    assert updated is not config
    assert updated.display_duration_s == pytest.approx(4.0e-9)
    assert updated != config
    assert config.display_duration_s == pytest.approx(2.0e-9)
    with pytest.raises(DomainError):
        config.with_display_window(start_s=0.0, duration_s=2.0e-8)  # beyond physical
    with pytest.raises(DomainError):
        config.with_display_window(start_s=-0.1, duration_s=1.0e-9)


# ---------------------------------------------------------------------------
# Requested/applied field-level diff
# ---------------------------------------------------------------------------


def test_config_diff_identical() -> None:
    a = make_config()
    b = make_config()
    diff = ConfigDiff.compute(a, b)
    assert diff.is_identical
    assert diff.changed_fields == ()


def test_config_diff_field_change() -> None:
    requested = make_config(power_dbm=-10.0)
    applied = make_config(power_dbm=-15.0)
    diff = ConfigDiff.compute(requested, applied)
    assert not diff.is_identical
    assert diff.changed_fields == ("power_dbm",)
    entry = diff.field("power_dbm")
    assert entry is not None
    assert entry.changed
    assert entry.requested_value == -10.0
    assert entry.applied_value == -15.0


def test_config_diff_channel_order_change() -> None:
    requested = make_config(channels=[HH_S11, VV_S22])
    applied = make_config(channels=[VV_S22, HH_S11])
    diff = ConfigDiff.compute(requested, applied)
    assert diff.changed_fields == ("channels",)
    assert diff.field("channels") is not None


def test_config_diff_serialization() -> None:
    diff = ConfigDiff.compute(make_config(power_dbm=-10.0), make_config(power_dbm=-15.0))
    restored = ConfigDiff.from_dict(diff.to_dict())
    assert restored == diff
    assert restored.changed_fields == ("power_dbm",)


def test_config_diff_is_immutable_copy() -> None:
    requested = make_config(channels=[HH_S11, VV_S22])
    applied = make_config(channels=[VV_S22, HH_S11])
    entry = ConfigDiff.compute(requested, applied).field("channels")
    assert entry is not None
    values = entry.requested_value
    assert isinstance(values, list)
    values.append("tampered")
    # The value returned again by the same diff must not contain the mutation.
    again = ConfigDiff.compute(requested, applied).field("channels")
    assert again is not None
    assert again.requested_value == entry.to_dict()["requested_value"]
    assert "tampered" not in again.requested_value  # type: ignore[operator]
    assert again.changed


def test_config_field_diff_accessor_and_changed_property() -> None:
    requested = make_config()
    applied = make_config(target_interval_s=0.5)
    diff = ConfigDiff.compute(requested, applied)
    assert diff.changed_fields == ("target_interval_s",)
    entry: ConfigFieldDiff | None = diff.field("target_interval_s")
    assert entry is not None
    assert entry.changed
    assert diff.field("power_dbm") is None
    assert entry.to_dict() == {
        "field": "target_interval_s",
        "requested_value": 0.25,
        "applied_value": 0.5,
        "changed": True,
    }


# ---------------------------------------------------------------------------
# Version contract: fail-closed on unknown schema/protocol
# ---------------------------------------------------------------------------


def test_version_fields_are_persisted_and_round_trip() -> None:
    config = make_config(software_version="0.2.0")
    payload = config.to_dict()
    assert payload["software_version"] == "0.2.0"
    assert payload["protocol_version"] == "1"
    assert payload["config_schema_version"] == "1"
    restored = MissionConfig.from_dict(payload)
    assert restored == config
    assert restored.software_version == "0.2.0"
    assert restored.protocol_version == "1"
    assert restored.config_schema_version == "1"


def test_versions_enter_digest_and_diff() -> None:
    a = make_config(software_version="0.1.0.dev0")
    b = make_config(software_version="0.2.0")
    assert a.config_sha256 != b.config_sha256
    diff = ConfigDiff.compute(a, b)
    assert diff.changed_fields == ("software_version",)
    entry = diff.field("software_version")
    assert entry is not None
    assert entry.requested_value == "0.1.0.dev0"
    assert entry.applied_value == "0.2.0"


def test_unknown_schema_version_is_rejected() -> None:
    with pytest.raises(DomainError) as excinfo:
        make_config(config_schema_version="2")
    assert excinfo.value.code is ErrorCode.UNSUPPORTED_SCHEMA_VERSION
    payload = make_config().to_dict()
    payload["config_schema_version"] = "99"
    with pytest.raises(DomainError) as excinfo:
        MissionConfig.from_dict(payload)
    assert excinfo.value.code is ErrorCode.UNSUPPORTED_SCHEMA_VERSION


def test_unknown_protocol_version_is_rejected() -> None:
    with pytest.raises(DomainError) as excinfo:
        make_config(protocol_version="2")
    assert excinfo.value.code is ErrorCode.UNSUPPORTED_PROTOCOL_VERSION


def test_version_fields_missing_or_wrong_type_are_rejected() -> None:
    payload = make_config().to_dict()
    del payload["software_version"]
    with pytest.raises(ValueError):
        MissionConfig.from_dict(payload)
    payload = make_config().to_dict()
    del payload["protocol_version"]
    with pytest.raises(ValueError):
        MissionConfig.from_dict(payload)
    bad = make_config().to_dict()
    bad["software_version"] = 5
    with pytest.raises(ValueError):
        MissionConfig.from_dict(bad)
    with pytest.raises(TypeError):
        make_config(software_version=5)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        make_config(protocol_version=None)  # type: ignore[arg-type]
    with pytest.raises(DomainError):
        make_config(software_version="bad version")


# ---------------------------------------------------------------------------
# Canonical numeric normalization: signed zero
# ---------------------------------------------------------------------------


def test_signed_zero_is_canonically_equivalent() -> None:
    positive = make_config(frequency_start_hz=0.0, power_dbm=0.0, display_start_s=0.0)
    negative = make_config(
        frequency_start_hz=-0.0, power_dbm=-0.0, display_start_s=-0.0
    )
    assert positive.to_canonical_json() == negative.to_canonical_json()
    assert positive.config_sha256 == negative.config_sha256
    assert positive.frequency_start_hz == 0.0
    assert positive.power_dbm == 0.0
    assert positive.display_start_s == 0.0
    # Other nonzero fields keep exact sign-normalized values.
    config = make_config(power_dbm=-10.0)
    assert config.to_dict()["power_dbm"] == -10.0


def test_nan_and_inf_are_still_rejected_after_normalization() -> None:
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(DomainError):
            make_config(power_dbm=bad)
        with pytest.raises(DomainError):
            make_config(display_start_s=bad)


# ---------------------------------------------------------------------------
# ConfigDiff strict value-object rules
# ---------------------------------------------------------------------------


def _diff_entry(
    field: str, requested: object, applied: object, changed: bool
) -> dict[str, object]:
    return {
        "field": field,
        "requested_value": requested,
        "applied_value": applied,
        "changed": changed,
    }


def test_config_diff_from_dict_source_list_is_isolated() -> None:
    source = [_diff_entry("power_dbm", -10.0, -15.0, True)]
    diff = ConfigDiff.from_dict({"fields": source})
    source[0]["requested_value"] = 999.0
    source[0]["applied_value"] = -999.0
    assert isinstance(diff.fields, tuple)
    assert diff.fields[0].requested_value == -10.0
    assert diff.fields[0].applied_value == -15.0
    assert diff.changed_fields == ("power_dbm",)


def test_config_diff_normalizes_fields_to_tuple() -> None:
    entries = [ConfigFieldDiff("power_dbm", -10.0, -15.0)]
    diff = ConfigDiff(fields=entries)
    assert isinstance(diff.fields, tuple)
    entries.append(ConfigFieldDiff("target_interval_s", 0.25, 0.5))
    assert diff.changed_fields == ("power_dbm",)


def test_config_diff_rejects_unknown_field() -> None:
    with pytest.raises(ValueError):
        ConfigFieldDiff("bogus_field", 1.0, 2.0)
    with pytest.raises(ValueError):
        ConfigDiff.from_dict({"fields": [_diff_entry("bogus", 1, 2, True)]})


def test_config_diff_rejects_duplicate_fields() -> None:
    with pytest.raises(ValueError):
        ConfigDiff.from_dict(
            {
                "fields": [
                    _diff_entry("power_dbm", -10.0, -15.0, True),
                    _diff_entry("power_dbm", -10.0, -20.0, True),
                ]
            }
        )


def test_config_diff_rejects_non_canonical_order() -> None:
    with pytest.raises(ValueError):
        ConfigDiff.from_dict(
            {
                "fields": [
                    _diff_entry("target_interval_s", 0.25, 0.5, True),
                    _diff_entry("power_dbm", -10.0, -15.0, True),
                ]
            }
        )


def test_config_diff_rejects_unchanged_entry() -> None:
    with pytest.raises(ValueError):
        ConfigDiff.from_dict(
            {"fields": [_diff_entry("power_dbm", -10.0, -10.0, False)]}
        )
    with pytest.raises(ValueError):
        ConfigFieldDiff("power_dbm", -10.0, -10.0)


def test_config_diff_rejects_contradictory_changed_flag() -> None:
    with pytest.raises(ValueError):
        ConfigDiff.from_dict(
            {"fields": [_diff_entry("power_dbm", -10.0, -15.0, False)]}
        )
    with pytest.raises(ValueError):
        ConfigDiff.from_dict(
            {"fields": [_diff_entry("power_dbm", -10.0, -10.0, True)]}
        )


def test_config_diff_rejects_missing_fields_and_malformed_payload() -> None:
    with pytest.raises(ValueError):
        ConfigDiff.from_dict(
            {
                "fields": [
                    {
                        "field": "power_dbm",
                        "applied_value": -15.0,
                        "changed": True,
                    }
                ]
            }
        )
    with pytest.raises(ValueError):
        ConfigDiff.from_dict(
            {
                "fields": [
                    {
                        "field": "power_dbm",
                        "requested_value": -10.0,
                        "changed": True,
                    }
                ]
            }
        )
    with pytest.raises(ValueError):
        ConfigDiff.from_dict(
            {
                "fields": [
                    {
                        "field": "power_dbm",
                        "requested_value": -10.0,
                        "applied_value": -15.0,
                    }
                ]
            }
        )
    with pytest.raises(ValueError):
        ConfigDiff.from_dict(
            {
                "fields": [
                    {
                        "field": "power_dbm",
                        "requested_value": -10.0,
                        "applied_value": -15.0,
                        "changed": "yes",
                    }
                ]
            }
        )
    with pytest.raises(ValueError):
        ConfigDiff.from_dict({"fields": ["not-an-entry"]})
    with pytest.raises(ValueError):
        ConfigDiff.from_dict({})
    with pytest.raises(ValueError):
        ConfigDiff.from_dict({"fields": "nope"})


def test_config_diff_rejects_non_json_diff_values() -> None:
    with pytest.raises(TypeError):
        ConfigFieldDiff("power_dbm", b"bytes", -15.0)  # type: ignore[arg-type]


def test_config_diff_multiple_changes_are_canonically_ordered() -> None:
    a = make_config()
    b = make_config(target_interval_s=0.5, power_dbm=-15.0)
    diff = ConfigDiff.compute(a, b)
    assert diff.changed_fields == ("power_dbm", "target_interval_s")
    assert [entry.field for entry in diff.fields] == sorted(diff.changed_fields)
