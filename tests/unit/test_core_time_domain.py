"""Tests for processing provenance and time-domain models (ISSUE-007).

Covers: ProcessingRecord/ProcessingHistory immutability and chaining,
frequency-domain and time-domain data domains, JSON-safe canonical stage
parameters, TimeDomainScan fixed shape/axis rules, kind/history consistency
and the prohibition of uncalibrated depth fields.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import numpy as np
import pytest

from uav_gpr.core import (
    BackgroundReferenceId,
    CalibrationProfileId,
    ChannelSpec,
    DataDomain,
    DeviceId,
    DomainError,
    ErrorCode,
    LogicalPolarization,
    MissionId,
    MonotonicNs,
    ProcessingHistory,
    ProcessingRecord,
    SParameter,
    TimeDomainKind,
    TimeDomainScan,
    TraceMetadata,
    TraceQualityReason,
    TraceQualityStatus,
    TraceUid,
)

CREATED_UTC = datetime(2026, 1, 1, tzinfo=UTC)
CAL_ID = CalibrationProfileId("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
BG_ID = BackgroundReferenceId("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")

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

MISSION = MissionId("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
DEVICE = DeviceId("cccccccc-cccc-4ccc-8ccc-cccccccccccc")

TIME_AXIS = np.linspace(0.0, 5.0e-9, 6)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _record(
    stage: str = "osl_calibration",
    version: str = "1.0",
    parameters: dict[str, object] | None = None,
    input_domain: DataDomain = DataDomain.FREQUENCY_RAW,
    output_domain: DataDomain = DataDomain.FREQUENCY_CALIBRATED,
    *,
    executed_utc: datetime | None = None,
    software_version: str = "0.1.0.dev0",
    calibration_id: CalibrationProfileId | None = None,
    background_id: BackgroundReferenceId | None = None,
) -> ProcessingRecord:
    return ProcessingRecord(
        stage_name=stage,
        stage_version=version,
        parameters=parameters or {"mode": "default"},
        input_domain=input_domain,
        output_domain=output_domain,
        executed_utc=executed_utc or CREATED_UTC,
        software_version=software_version,
        calibration_profile_id=calibration_id,
        background_reference_id=background_id,
    )


def _uid(index: int) -> str:
    return f"bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbb{index:02d}"


def _meta(index: int = 0, *, mission_id: MissionId | None = None) -> TraceMetadata:
    return TraceMetadata(
        mission_id=mission_id if mission_id is not None else MISSION,
        trace_index=index,
        trace_uid=TraceUid(_uid(index)),
        device_id=DEVICE,
        sweep_started_utc=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
        sweep_midpoint_utc=datetime(2026, 1, 1, 12, 0, 0, 250000, tzinfo=UTC),
        sweep_finished_utc=datetime(2026, 1, 1, 12, 0, 0, 500000, tzinfo=UTC),
        sweep_started_monotonic_ns=MonotonicNs(1_000),
        sweep_midpoint_monotonic_ns=MonotonicNs(1_250),
        sweep_finished_monotonic_ns=MonotonicNs(1_500),
        target_interval_s=0.5,
        actual_interval_s=None if index == 0 else 0.5,
        schedule_error_s=None if index == 0 else 0.0,
        connection_generation=2,
        raw_trace_sha256=None,
        gnss_match=None,
        quality_status=TraceQualityStatus.DEGRADED,
        quality_reasons=(TraceQualityReason.GNSS_MISSING,),
    )


def _scan(
    kind: TimeDomainKind = TimeDomainKind.TIME_BASE,
    *,
    n_traces: int = 2,
    history: ProcessingHistory | None = None,
    metadata: tuple[TraceMetadata | None, ...] | None = None,
) -> TimeDomainScan:
    data = np.arange(n_traces * 1 * len(TIME_AXIS), dtype=np.float64).reshape(
        n_traces, 1, len(TIME_AXIS)
    ) + 1j * np.arange(n_traces * 1 * len(TIME_AXIS), dtype=np.float64).reshape(
        n_traces, 1, len(TIME_AXIS)
    )
    return TimeDomainScan(
        channels=[HH_S11],
        time_axis_s=TIME_AXIS,
        data=data,
        kind=kind,
        history=history if history is not None else ProcessingHistory(),
        metadata=metadata if metadata is not None else (),
    )


# ---------------------------------------------------------------------------
# ProcessingRecord: validation, JSON safety, immutability
# ---------------------------------------------------------------------------


def test_record_round_trip_and_canonical_parameters() -> None:
    record = _record(
        calibration_id=CAL_ID, background_id=BG_ID, parameters={"taps": [1, 2], "gain": 0.5}
    )
    assert record.stage_name == "osl_calibration"
    assert record.stage_version == "1.0"
    assert record.software_version == "0.1.0.dev0"
    assert record.input_domain is DataDomain.FREQUENCY_RAW
    assert record.output_domain is DataDomain.FREQUENCY_CALIBRATED
    assert record.calibration_profile_id == CAL_ID
    assert record.background_reference_id == BG_ID
    restored = ProcessingRecord.from_dict(record.to_dict())
    assert restored == record
    assert restored.to_dict() == record.to_dict()
    # Canonical parameters are deterministic JSON.
    assert record.parameters_canonical_json() == (
        '{"gain":0.5,"taps":[1,2]}'
    )
    assert record.parameters_canonical_json() == restored.parameters_canonical_json()


def test_record_rejects_non_json_parameters() -> None:
    for bad in (
        b"bytes",
        {"set": {1, 2}},
        {"nested": object()},
        np.array([1.0, 2.0]),
        {"nan": float("nan")},
        {"inf": float("inf")},
    ):
        with pytest.raises(TypeError):
            _record(parameters={"bad": bad})
    with pytest.raises(TypeError):
        _record(parameters=[1, 2])  # type: ignore[arg-type]


def test_record_parameters_are_deep_copied() -> None:
    source: dict[str, object] = {"bands": [[1.0, 2.0]], "label": "ok"}
    record = _record(parameters=source)
    source["bands"].append([3.0, 4.0])
    source["label"] = "mutated"
    # The record must not see the caller's later mutations.
    assert record.parameters["label"] == "ok"
    assert record.parameters["bands"] == [[1.0, 2.0]]
    # Mutating a returned mapping must not reach the stored parameters;
    # the top level is read-only, nested containers are independent copies.
    returned = record.parameters
    returned["bands"].append([9.0])
    with pytest.raises(TypeError):
        returned["label"] = "tampered"
    assert record.parameters["label"] == "ok"
    assert record.parameters["bands"] == [[1.0, 2.0]]


def test_record_validation() -> None:
    with pytest.raises(DomainError):
        _record(stage="Bad Name")
    with pytest.raises(DomainError):
        _record(stage="1bad")
    with pytest.raises(DomainError):
        _record(stage="")
    with pytest.raises(DomainError):
        _record(version="")
    with pytest.raises(DomainError):
        _record(software_version="")
    with pytest.raises(TypeError):
        _record(input_domain="raw")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        _record(output_domain="time_base")  # type: ignore[arg-type]
    with pytest.raises(DomainError) as excinfo:
        _record(executed_utc=datetime(2026, 1, 1))  # naive
    assert excinfo.value.code is ErrorCode.NAIVE_DATETIME
    with pytest.raises(TypeError):
        _record(calibration_id="dddddddd-dddd-4ddd-8ddd-dddddddddddd")  # type: ignore[arg-type]


def test_record_software_version_and_utc_are_serialized() -> None:
    record = _record(executed_utc=datetime(2026, 2, 2, 3, 4, 5, tzinfo=UTC))
    payload = record.to_dict()
    assert payload["executed_utc"].endswith("Z")
    assert payload["software_version"] == "0.1.0.dev0"


# ---------------------------------------------------------------------------
# ProcessingHistory: chain, append-copy, duplicate stages
# ---------------------------------------------------------------------------


def test_history_append_returns_new_object() -> None:
    first = _record(
        stage="bandpass", input_domain=DataDomain.FREQUENCY_RAW,
        output_domain=DataDomain.FREQUENCY_RAW,
    )
    second = _record(
        stage="ifft", input_domain=DataDomain.FREQUENCY_RAW,
        output_domain=DataDomain.TIME_BASE,
    )
    history = ProcessingHistory([first])
    appended = history.append(second)
    assert appended is not history
    assert len(appended) == 2
    assert appended.records == (first, second)
    assert len(history) == 1
    assert history.records == (first,)
    # The old history is unaffected by the append.
    assert history.append(second) == appended


def test_history_chain_mismatch_is_rejected() -> None:
    calibrated = _record(
        input_domain=DataDomain.FREQUENCY_RAW,
        output_domain=DataDomain.FREQUENCY_CALIBRATED,
    )
    with pytest.raises(DomainError) as excinfo:
        ProcessingHistory([calibrated]).append(
            _record(
                stage="bandpass",
                input_domain=DataDomain.FREQUENCY_BACKGROUND_APPLIED,
                output_domain=DataDomain.FREQUENCY_BACKGROUND_APPLIED,
            )
        )
    assert excinfo.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH
    with pytest.raises(DomainError) as excinfo:
        ProcessingHistory(
            [
                calibrated,
                _record(
                    stage="bad_chain",
                    input_domain=DataDomain.TIME_BASE,
                    output_domain=DataDomain.TIME_BASE,
                ),
            ]
        )
    assert excinfo.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH


def test_history_chain_accepts_identity_domains() -> None:
    history = ProcessingHistory(
        [
            _record(
                stage="bandpass",
                input_domain=DataDomain.FREQUENCY_RAW,
                output_domain=DataDomain.FREQUENCY_RAW,
            ),
            _record(
                stage="ifft",
                input_domain=DataDomain.FREQUENCY_RAW,
                output_domain=DataDomain.TIME_BASE,
            ),
        ]
    )
    assert len(history) == 2


def test_history_rejects_duplicate_stage_application() -> None:
    first = _record(
        stage="bandpass", input_domain=DataDomain.FREQUENCY_RAW,
        output_domain=DataDomain.FREQUENCY_RAW,
    )
    second = _record(
        stage="bandpass", input_domain=DataDomain.FREQUENCY_RAW,
        output_domain=DataDomain.FREQUENCY_RAW, parameters={"cutoff": 1.0e9},
    )
    with pytest.raises(DomainError) as excinfo:
        ProcessingHistory([first]).append(second)
    assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT
    # A new stage version is required for a repeated application.
    bumped = _record(
        stage="bandpass", version="2.0", input_domain=DataDomain.FREQUENCY_RAW,
        output_domain=DataDomain.FREQUENCY_RAW,
    )
    assert len(ProcessingHistory([first]).append(bumped)) == 2


def test_history_serialization_round_trip() -> None:
    empty = ProcessingHistory()
    assert empty.to_dict() == {"records": []}
    assert ProcessingHistory.from_dict(empty.to_dict()) == empty
    history = ProcessingHistory(
        [
            _record(
                stage="osl_calibration",
                input_domain=DataDomain.FREQUENCY_RAW,
                output_domain=DataDomain.FREQUENCY_CALIBRATED,
                calibration_id=CAL_ID,
            ),
            _record(
                stage="bandpass",
                input_domain=DataDomain.FREQUENCY_CALIBRATED,
                output_domain=DataDomain.FREQUENCY_CALIBRATED,
                parameters={"cut_hz": 1.0e9},
            ),
        ]
    )
    restored = ProcessingHistory.from_dict(history.to_dict())
    assert restored == history
    assert [r.stage_name for r in restored] == ["osl_calibration", "bandpass"]
    assert restored.records[0].calibration_profile_id == CAL_ID


# ---------------------------------------------------------------------------
# TimeDomainScan: shape, axis, immutability, metadata
# ---------------------------------------------------------------------------


def test_scan_shape_and_axis() -> None:
    scan = _scan(n_traces=2)
    assert scan.data.shape == (2, 1, len(TIME_AXIS))
    assert scan.data.dtype == np.complex128
    assert scan.time_axis_s.shape == (len(TIME_AXIS),)
    assert scan.time_axis_s.dtype == np.float64
    assert scan.channels == (HH_S11,)
    assert scan.kind is TimeDomainKind.TIME_BASE
    assert len(scan.history) == 0
    assert scan.metadata == ()


def test_scan_rejects_shape_mismatch() -> None:
    with pytest.raises(DomainError) as excinfo:
        TimeDomainScan(
            channels=[HH_S11],
            time_axis_s=TIME_AXIS,
            data=np.zeros((2, 1, 3), dtype=np.complex128),
            kind=TimeDomainKind.TIME_BASE,
        )
    assert excinfo.value.code is ErrorCode.SHAPE_MISMATCH
    with pytest.raises(DomainError) as excinfo:
        TimeDomainScan(
            channels=[HH_S11],
            time_axis_s=TIME_AXIS,
            data=np.zeros((2, 3), dtype=np.complex128),
            kind=TimeDomainKind.TIME_BASE,
        )
    assert excinfo.value.code is ErrorCode.SHAPE_MISMATCH
    with pytest.raises(DomainError) as excinfo:
        TimeDomainScan(
            channels=[HH_S11],
            time_axis_s=TIME_AXIS,
            data=np.zeros((0, 1, 6), dtype=np.complex128),
            kind=TimeDomainKind.TIME_BASE,
        )
    assert excinfo.value.code is ErrorCode.SHAPE_MISMATCH
    with pytest.raises(DomainError):
        TimeDomainScan(channels=[], time_axis_s=TIME_AXIS,
                       data=np.zeros((1, 0, 6)), kind=TimeDomainKind.TIME_BASE)


def test_scan_rejects_bad_time_axis() -> None:
    good = TIME_AXIS
    for bad in (
        np.array([0.0, 1.0, 1.0, 2.0]),
        np.array([0.0, np.nan, 2.0]),
        np.array([0.0, np.inf, 2.0]),
        np.array([], dtype=np.float64),
        np.array([[0.0, 1.0, 2.0]]),
    ):
        with pytest.raises(DomainError):
            TimeDomainScan(
                channels=[HH_S11], time_axis_s=bad,
                data=np.zeros((1, 1, len(good))),
                kind=TimeDomainKind.TIME_BASE,
            )
    # Negative start is permitted: the axis only has to be strictly increasing.
    allowed = TimeDomainScan(
        channels=[HH_S11],
        time_axis_s=np.array([-5.0e-9, -2.0e-9, 1.0e-9]),
        data=np.zeros((1, 1, 3)),
        kind=TimeDomainKind.TIME_BASE,
    )
    assert allowed.time_axis_s[0] == -5.0e-9


def test_scan_arrays_are_owned_and_immutable() -> None:
    source_data = np.zeros((2, 1, len(TIME_AXIS)), dtype=np.complex128)
    source_axis = TIME_AXIS.copy()
    scan = TimeDomainScan(
        channels=[HH_S11], time_axis_s=source_axis, data=source_data,
        kind=TimeDomainKind.TIME_BASE,
    )
    source_data[..., :] = 7 + 7j
    source_axis[0] = 99.0
    assert np.all(scan.data == 0j)
    assert scan.time_axis_s[0] == 0.0
    for array in (scan.data, scan.data[0], scan.time_axis_s):
        with pytest.raises(ValueError):
            array.setflags(write=True)
        with pytest.raises(ValueError):
            array[0] = 0  # type: ignore[index]
    with pytest.raises(ValueError):
        scan.data[0, 0, 0] = 1j


def test_scan_metadata_validation() -> None:
    scan = _scan(n_traces=2, metadata=(_meta(0), _meta(1)))
    assert len(scan.metadata) == 2
    with pytest.raises(DomainError) as excinfo:
        _scan(n_traces=2, metadata=(_meta(0),))
    assert excinfo.value.code is ErrorCode.SHAPE_MISMATCH
    with pytest.raises(DomainError) as excinfo:
        _scan(n_traces=2, metadata=(_meta(0), _meta(0)))
    assert excinfo.value.code is ErrorCode.ID_CONFLICT
    with pytest.raises(DomainError) as excinfo:
        _scan(n_traces=2, metadata=(_meta(1), _meta(0)))
    assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT
    other_mission = MissionId("99999999-9999-4999-8999-999999999999")
    with pytest.raises(DomainError) as excinfo:
        _scan(n_traces=2, metadata=(_meta(0), _meta(0, mission_id=other_mission)))
    assert excinfo.value.code is ErrorCode.ID_CONFLICT


def test_scan_kind_history_consistency() -> None:
    # time_base with empty history is fine.
    assert len(_scan(TimeDomainKind.TIME_BASE).history) == 0
    # time_processed without provenance records is rejected.
    with pytest.raises(DomainError) as excinfo:
        _scan(TimeDomainKind.TIME_PROCESSED)
    assert excinfo.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH
    ifft = _record(
        stage="ifft",
        input_domain=DataDomain.FREQUENCY_RAW,
        output_domain=DataDomain.TIME_BASE,
    )
    assert len(_scan(TimeDomainKind.TIME_BASE, history=ProcessingHistory([ifft])).history) == 1
    with pytest.raises(DomainError) as excinfo:
        _scan(
            TimeDomainKind.TIME_BASE,
            history=ProcessingHistory(
                [
                    ifft,
                    _record(
                        stage="dewow",
                        input_domain=DataDomain.TIME_BASE,
                        output_domain=DataDomain.TIME_PROCESSED,
                    ),
                ]
            ),
        )
    assert excinfo.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH
    processed_history = ProcessingHistory(
        [
            ifft,
            _record(
                stage="dewow",
                input_domain=DataDomain.TIME_BASE,
                output_domain=DataDomain.TIME_PROCESSED,
            ),
        ]
    )
    assert len(_scan(TimeDomainKind.TIME_PROCESSED, history=processed_history).history) == 2
    with pytest.raises(DomainError) as excinfo:
        _scan(
            TimeDomainKind.TIME_PROCESSED,
            history=ProcessingHistory(
                [
                    _record(
                        stage="ifft",
                        input_domain=DataDomain.FREQUENCY_RAW,
                        output_domain=DataDomain.TIME_BASE,
                    )
                ]
            ),
        )
    assert excinfo.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH


def test_scan_with_history_returns_new_object() -> None:
    original = _scan(TimeDomainKind.TIME_BASE)
    history = ProcessingHistory(
        [
            _record(
                stage="ifft",
                input_domain=DataDomain.FREQUENCY_RAW,
                output_domain=DataDomain.TIME_BASE,
            )
        ]
    )
    updated = original.with_history(history)
    assert updated is not original
    assert len(updated.history) == 1
    assert len(original.history) == 0
    with pytest.raises(DomainError):
        original.with_history(
            ProcessingHistory(
                [
                    _record(
                        stage="ifft",
                        input_domain=DataDomain.FREQUENCY_RAW,
                        output_domain=DataDomain.TIME_PROCESSED,
                    )
                ]
            )
        )


def test_no_uncalibrated_depth_fields() -> None:
    # Depth is an uncalibrated concept: it must not appear in these models.
    for cls in (TimeDomainScan, ProcessingRecord, ProcessingHistory):
        names = {field.name for field in dataclasses.fields(cls)}
        assert "depth" not in names
        assert not any("depth" in name for name in names)
        assert not hasattr(cls, "depth")
    assert "depth" not in dataclasses.fields(TimeDomainScan)[0].name
    # The models carry no fake depth-time values either.
    assert TimeDomainScan.__dataclass_fields__.keys() == {
        "channels", "time_axis_s", "data", "kind", "history", "metadata",
    }
