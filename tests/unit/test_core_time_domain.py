"""Tests for processing provenance and time-domain models (ISSUE-007).

Covers: ProcessingRecord/ProcessingHistory immutability, explicit legal
data-domain transitions (raw is never an output), reference/domain
compatibility, duplicate stage protection by stable stage name (version
bumps cannot bypass it), JSON-safe canonical stage parameters,
TimeDomainScan fixed shape/axis rules and full non-empty provenance, and
the prohibition of uncalibrated depth fields.
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
OTHER_CAL = CalibrationProfileId("ffffffff-ffff-4fff-8fff-ffffffffffff")
OTHER_BG = BackgroundReferenceId("99999999-9999-4999-8999-999999999999")

RAW = DataDomain.FREQUENCY_RAW
CALIBRATED = DataDomain.FREQUENCY_CALIBRATED
BACKGROUND_APPLIED = DataDomain.FREQUENCY_BACKGROUND_APPLIED
FILTERED = DataDomain.FREQUENCY_FILTERED
TIME_BASE = DataDomain.TIME_BASE
TIME_PROCESSED = DataDomain.TIME_PROCESSED

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
# Helpers: all records/histories built here are legal under the new contract.
# ---------------------------------------------------------------------------


def _record(
    stage: str = "ifft",
    version: str = "1.0",
    parameters: dict[str, object] | None = None,
    input_domain: DataDomain = RAW,
    output_domain: DataDomain = TIME_BASE,
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


def _osl() -> ProcessingRecord:
    return _record(
        stage="osl_calibration",
        input_domain=RAW,
        output_domain=CALIBRATED,
        calibration_id=CAL_ID,
    )


def _background() -> ProcessingRecord:
    return _record(
        stage="air_background",
        input_domain=CALIBRATED,
        output_domain=BACKGROUND_APPLIED,
        background_id=BG_ID,
    )


def _bandpass(
    input_domain: DataDomain = BACKGROUND_APPLIED,
    *,
    version: str = "1.0",
    calibration_id: CalibrationProfileId | None = None,
    background_id: BackgroundReferenceId | None = None,
) -> ProcessingRecord:
    return _record(
        stage="bandpass",
        version=version,
        input_domain=input_domain,
        output_domain=FILTERED,
        parameters={"cut_hz": 1.0e9},
        calibration_id=calibration_id,
        background_id=background_id,
    )


def _ifft(input_domain: DataDomain = FILTERED) -> ProcessingRecord:
    return _record(
        stage="ifft",
        input_domain=input_domain,
        output_domain=TIME_BASE,
        parameters={"zero_pad": 2},
    )


def _dewow() -> ProcessingRecord:
    return _record(
        stage="dewow",
        input_domain=TIME_BASE,
        output_domain=TIME_PROCESSED,
        parameters={"window_s": 1.0e-10},
    )


def _flat_reflection(version: str = "1.0") -> ProcessingRecord:
    return _record(
        stage="flat_reflection",
        version=version,
        input_domain=TIME_PROCESSED,
        output_domain=TIME_PROCESSED,
        parameters={"window_traces": 5},
    )


def _legal_pipeline() -> ProcessingHistory:
    return ProcessingHistory(
        [_osl(), _background(), _bandpass(), _ifft(), _dewow(), _flat_reflection()]
    )


def _default_history(kind: TimeDomainKind) -> ProcessingHistory:
    ifft = _ifft(RAW)
    if kind is TimeDomainKind.TIME_PROCESSED:
        return ProcessingHistory([ifft, _dewow()])
    return ProcessingHistory([ifft])


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
        history=history if history is not None else _default_history(kind),
        metadata=metadata if metadata is not None else (),
    )


# ---------------------------------------------------------------------------
# ProcessingRecord: validation, JSON safety, immutability
# ---------------------------------------------------------------------------


def test_record_round_trip_and_canonical_parameters() -> None:
    record = _record(
        stage="air_background",
        input_domain=CALIBRATED,
        output_domain=BACKGROUND_APPLIED,
        parameters={"taps": [1, 2], "gain": 0.5},
        calibration_id=CAL_ID,
        background_id=BG_ID,
    )
    assert record.stage_name == "air_background"
    assert record.stage_version == "1.0"
    assert record.software_version == "0.1.0.dev0"
    assert record.input_domain is CALIBRATED
    assert record.output_domain is BACKGROUND_APPLIED
    assert record.calibration_profile_id == CAL_ID
    assert record.background_reference_id == BG_ID
    restored = ProcessingRecord.from_dict(record.to_dict())
    assert restored == record
    assert restored.to_dict() == record.to_dict()
    # Canonical parameters are deterministic JSON.
    assert record.parameters_canonical_json() == '{"gain":0.5,"taps":[1,2]}'
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
    record = _record(input_domain=RAW, output_domain=TIME_BASE, parameters=source)
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
    with pytest.raises(ValueError):
        ProcessingRecord.from_dict({"stage_name": "ifft"})


def test_record_software_version_and_utc_are_serialized() -> None:
    record = _record(executed_utc=datetime(2026, 2, 2, 3, 4, 5, tzinfo=UTC))
    payload = record.to_dict()
    assert payload["executed_utc"].endswith("Z")
    assert payload["software_version"] == "0.1.0.dev0"


# ---------------------------------------------------------------------------
# Explicit legal data-domain transitions
# ---------------------------------------------------------------------------


def test_history_rejects_raw_output_transition() -> None:
    # Any stage outputting frequency_raw is illegal, including identity hops.
    with pytest.raises(DomainError) as excinfo:
        _record(
            stage="bandpass",
            input_domain=RAW,
            output_domain=RAW,
            parameters={"irrelevant": True},
        )
    assert excinfo.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH


def test_history_rejects_time_to_frequency_transition() -> None:
    with pytest.raises(DomainError) as excinfo:
        _record(
            stage="retime",
            input_domain=TIME_BASE,
            output_domain=FILTERED,
        )
    assert excinfo.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH
    with pytest.raises(DomainError) as excinfo:
        _record(
            stage="back_to_freq",
            input_domain=TIME_PROCESSED,
            output_domain=RAW,
        )
    assert excinfo.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH


def test_history_rejects_time_processed_to_time_base() -> None:
    with pytest.raises(DomainError) as excinfo:
        _record(
            stage="unprocess",
            input_domain=TIME_PROCESSED,
            output_domain=TIME_BASE,
        )
    assert excinfo.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH


def test_history_rejects_skipping_time_base() -> None:
    with pytest.raises(DomainError) as excinfo:
        _record(
            stage="fast_ifft",
            input_domain=RAW,
            output_domain=TIME_PROCESSED,
        )
    assert excinfo.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH


def test_history_rejects_first_input_time_domain() -> None:
    # A history must currently start from frequency_raw; a stand-alone
    # time-domain record is legal but cannot be the first history entry.
    dewow = _dewow()
    with pytest.raises(DomainError) as excinfo:
        ProcessingHistory([dewow])
    assert excinfo.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH


def test_history_legal_full_pipeline() -> None:
    history = _legal_pipeline()
    assert [record.stage_name for record in history] == [
        "osl_calibration",
        "air_background",
        "bandpass",
        "ifft",
        "dewow",
        "flat_reflection",
    ]
    assert [record.output_domain for record in history] == [
        CALIBRATED,
        BACKGROUND_APPLIED,
        FILTERED,
        TIME_BASE,
        TIME_PROCESSED,
        TIME_PROCESSED,
    ]
    # Derived-domain positive path: raw/calibrated/background/filtered -> time.
    direct = ProcessingHistory([_ifft(RAW)])
    assert len(direct) == 1


def test_history_append_returns_new_object() -> None:
    first = _bandpass(RAW)
    second = _ifft(FILTERED)
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
    calibrated = _osl()
    # bandpass raw -> filtered is a legal single transition, but its input
    # does not chain from the previous record's calibrated output.
    with pytest.raises(DomainError) as excinfo:
        ProcessingHistory([calibrated]).append(_bandpass(RAW))
    assert excinfo.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH
    with pytest.raises(DomainError) as excinfo:
        ProcessingHistory([_ifft(RAW), _bandpass(RAW)])
    assert excinfo.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH


# ---------------------------------------------------------------------------
# Reference / domain compatibility
# ---------------------------------------------------------------------------


def test_reference_required_for_calibrated_output() -> None:
    with pytest.raises(DomainError) as excinfo:
        _record(
            stage="osl_calibration",
            input_domain=RAW,
            output_domain=CALIBRATED,
            calibration_id=None,
        )
    assert excinfo.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH


def test_reference_required_for_background_output() -> None:
    with pytest.raises(DomainError) as excinfo:
        _record(
            stage="air_background",
            input_domain=CALIBRATED,
            output_domain=BACKGROUND_APPLIED,
            background_id=None,
        )
    assert excinfo.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH


def test_time_stage_rejects_frequency_references() -> None:
    with pytest.raises(DomainError):
        _record(
            stage="dewow",
            input_domain=TIME_BASE,
            output_domain=TIME_PROCESSED,
            calibration_id=CAL_ID,
        )
    with pytest.raises(DomainError):
        _record(
            stage="flat_reflection",
            input_domain=TIME_PROCESSED,
            output_domain=TIME_PROCESSED,
            background_id=BG_ID,
        )


def test_reference_inheritance_on_calibrated_input_is_allowed() -> None:
    # A later frequency stage may explicitly reference the calibration that
    # produced its input (documented, serializable inheritance rule).
    record = _bandpass(CALIBRATED, calibration_id=CAL_ID)
    assert record.calibration_profile_id == CAL_ID
    history = ProcessingHistory([_osl(), record])
    assert len(history) == 2


def test_reference_mismatch_is_rejected() -> None:
    # Calibration reference on a stage whose domains are neither calibrated.
    with pytest.raises(DomainError):
        _bandpass(RAW, calibration_id=CAL_ID)
    # Background reference on a stage whose domains are not background-applied.
    with pytest.raises(DomainError):
        _record(
            stage="bandpass",
            input_domain=RAW,
            output_domain=FILTERED,
            background_id=BG_ID,
        )


# ---------------------------------------------------------------------------
# Provenance continuity: an explicit reference may not change mid-history
# ---------------------------------------------------------------------------


def test_history_rejects_calibration_reference_change() -> None:
    # raw -> calibrated(CAL_A) -> filtered(CAL_B)
    with pytest.raises(DomainError) as excinfo:
        ProcessingHistory([_osl(), _bandpass(CALIBRATED, calibration_id=OTHER_CAL)])
    assert excinfo.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH
    context = excinfo.value.context
    assert context["stage"] == "bandpass"
    assert context["previous_calibration_profile_id"] == CAL_ID.to_json()
    assert context["incoming_calibration_profile_id"] == OTHER_CAL.to_json()


def test_history_rejects_background_reference_change() -> None:
    # raw -> background_applied(BG_A) -> filtered(BG_B)
    producer = _record(
        stage="air_background",
        input_domain=RAW,
        output_domain=BACKGROUND_APPLIED,
        background_id=BG_ID,
    )
    with pytest.raises(DomainError) as excinfo:
        ProcessingHistory(
            [producer, _bandpass(BACKGROUND_APPLIED, background_id=OTHER_BG)]
        )
    assert excinfo.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH
    context = excinfo.value.context
    assert context["stage"] == "bandpass"
    assert context["previous_background_reference_id"] == BG_ID.to_json()
    assert context["incoming_background_reference_id"] == OTHER_BG.to_json()


def test_history_rejects_calibration_change_on_background_stage() -> None:
    # raw -> calibrated(CAL_A) -> background_applied(..., calibration_profile_id=CAL_B)
    background_stage = _record(
        stage="air_background",
        input_domain=CALIBRATED,
        output_domain=BACKGROUND_APPLIED,
        background_id=BG_ID,
        calibration_id=OTHER_CAL,
    )
    with pytest.raises(DomainError) as excinfo:
        ProcessingHistory([_osl(), background_stage])
    assert excinfo.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH
    assert excinfo.value.context["stage"] == "air_background"


def test_history_rejects_derived_frequency_start() -> None:
    # A history may start from frequency_raw only; derived frequency snapshots
    # cannot claim to be complete histories even when carrying references.
    derived_starts = [
        _record(
            stage="cal_reprocess",
            input_domain=CALIBRATED,
            output_domain=FILTERED,
            calibration_id=CAL_ID,
        ),
        _record(
            stage="bg_reprocess",
            input_domain=BACKGROUND_APPLIED,
            output_domain=FILTERED,
            background_id=BG_ID,
        ),
        _ifft(FILTERED),
    ]
    for record in derived_starts:
        with pytest.raises(DomainError) as excinfo:
            ProcessingHistory([record])
        assert excinfo.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH
        assert excinfo.value.context["first_input_domain"] in {
            CALIBRATED.value,
            BACKGROUND_APPLIED.value,
            FILTERED.value,
        }


def test_history_from_dict_rejects_illegal_payloads() -> None:
    # The same validation path applies to deserialized payloads.
    payload = {
        "records": [
            _osl().to_dict(),
            _bandpass(CALIBRATED, calibration_id=OTHER_CAL).to_dict(),
        ],
    }
    with pytest.raises(DomainError) as excinfo:
        ProcessingHistory.from_dict(payload)
    assert excinfo.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH
    derived = {"records": [_ifft(FILTERED).to_dict()]}
    with pytest.raises(DomainError) as excinfo:
        ProcessingHistory.from_dict(derived)
    assert excinfo.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH


def test_history_omitted_repeated_reference_is_legal() -> None:
    # Omitting a repeated reference is not a reference change: the producer's
    # ID stays recorded in the history.
    history = ProcessingHistory([_osl(), _bandpass(CALIBRATED)])
    assert history.records[0].calibration_profile_id == CAL_ID
    assert history.records[1].calibration_profile_id is None
    # Explicit inheritance with the identical ID remains legal.
    inherited = ProcessingHistory([_osl(), _bandpass(CALIBRATED, calibration_id=CAL_ID)])
    assert inherited.records[1].calibration_profile_id == CAL_ID


# ---------------------------------------------------------------------------
# ProcessingHistory: duplicates by stable stage name (version cannot bypass)
# ---------------------------------------------------------------------------


def test_history_rejects_duplicate_stage_even_with_new_version() -> None:
    base = _legal_pipeline()  # ends with flat_reflection (time_processed)
    bumped = _flat_reflection(version="2.0")
    # Appending the same stable stage name is rejected even with a new version
    # (flat_reflection chains legally, so the duplicate rule fires here).
    with pytest.raises(DomainError) as excinfo:
        base.append(bumped)
    assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT
    assert "stage" in excinfo.value.message
    # Re-processing opens a new history/revision: a fresh history may apply
    # the same stage name once under its new version.
    alternative = _record(
        stage="osl_calibration",
        version="2.0",
        input_domain=RAW,
        output_domain=CALIBRATED,
        calibration_id=CAL_ID,
    )
    fresh = ProcessingHistory([alternative, _ifft(CALIBRATED)])
    assert [record.stage_version for record in fresh] == ["2.0", "1.0"]


def test_history_serialization_round_trip() -> None:
    empty = ProcessingHistory()
    assert empty.to_dict() == {"records": []}
    assert ProcessingHistory.from_dict(empty.to_dict()) == empty
    history = _legal_pipeline()
    restored = ProcessingHistory.from_dict(history.to_dict())
    assert restored == history
    assert [record.stage_name for record in restored] == [
        "osl_calibration",
        "air_background",
        "bandpass",
        "ifft",
        "dewow",
        "flat_reflection",
    ]
    assert restored.records[0].calibration_profile_id == CAL_ID
    assert restored.records[1].background_reference_id == BG_ID


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
    assert len(scan.history) == 1
    assert scan.history.records[0].output_domain is TIME_BASE
    assert scan.metadata == ()


def test_scan_rejects_shape_mismatch() -> None:
    with pytest.raises(DomainError) as excinfo:
        TimeDomainScan(
            channels=[HH_S11],
            time_axis_s=TIME_AXIS,
            data=np.zeros((2, 1, 3), dtype=np.complex128),
            kind=TimeDomainKind.TIME_BASE,
            history=_default_history(TimeDomainKind.TIME_BASE),
        )
    assert excinfo.value.code is ErrorCode.SHAPE_MISMATCH
    with pytest.raises(DomainError) as excinfo:
        TimeDomainScan(
            channels=[HH_S11],
            time_axis_s=TIME_AXIS,
            data=np.zeros((2, 3), dtype=np.complex128),
            kind=TimeDomainKind.TIME_BASE,
            history=_default_history(TimeDomainKind.TIME_BASE),
        )
    assert excinfo.value.code is ErrorCode.SHAPE_MISMATCH
    with pytest.raises(DomainError) as excinfo:
        TimeDomainScan(
            channels=[HH_S11],
            time_axis_s=TIME_AXIS,
            data=np.zeros((0, 1, 6), dtype=np.complex128),
            kind=TimeDomainKind.TIME_BASE,
            history=_default_history(TimeDomainKind.TIME_BASE),
        )
    assert excinfo.value.code is ErrorCode.SHAPE_MISMATCH
    with pytest.raises(DomainError):
        TimeDomainScan(
            channels=[],
            time_axis_s=TIME_AXIS,
            data=np.zeros((1, 0, 6)),
            kind=TimeDomainKind.TIME_BASE,
            history=_default_history(TimeDomainKind.TIME_BASE),
        )


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
                channels=[HH_S11],
                time_axis_s=bad,
                data=np.zeros((1, 1, len(good))),
                kind=TimeDomainKind.TIME_BASE,
                history=_default_history(TimeDomainKind.TIME_BASE),
            )
    # Negative start is permitted: the axis only has to be strictly increasing.
    allowed = TimeDomainScan(
        channels=[HH_S11],
        time_axis_s=np.array([-5.0e-9, -2.0e-9, 1.0e-9]),
        data=np.zeros((1, 1, 3)),
        kind=TimeDomainKind.TIME_BASE,
        history=_default_history(TimeDomainKind.TIME_BASE),
    )
    assert allowed.time_axis_s[0] == -5.0e-9


def test_scan_arrays_are_owned_and_immutable() -> None:
    source_data = np.zeros((2, 1, len(TIME_AXIS)), dtype=np.complex128)
    source_axis = TIME_AXIS.copy()
    scan = TimeDomainScan(
        channels=[HH_S11],
        time_axis_s=source_axis,
        data=source_data,
        kind=TimeDomainKind.TIME_BASE,
        history=_default_history(TimeDomainKind.TIME_BASE),
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


def test_scan_requires_non_empty_history_for_both_kinds() -> None:
    with pytest.raises(DomainError) as excinfo:
        _scan(TimeDomainKind.TIME_BASE, history=ProcessingHistory())
    assert excinfo.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH
    with pytest.raises(DomainError) as excinfo:
        _scan(TimeDomainKind.TIME_PROCESSED, history=ProcessingHistory())
    assert excinfo.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH


def test_scan_kind_history_consistency() -> None:
    ifft = _ifft(RAW)
    assert len(_scan(TimeDomainKind.TIME_BASE, history=ProcessingHistory([ifft])).history) == 1
    with pytest.raises(DomainError) as excinfo:
        _scan(
            TimeDomainKind.TIME_BASE,
            history=ProcessingHistory([ifft, _dewow()]),
        )
    assert excinfo.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH
    processed_history = ProcessingHistory([ifft, _dewow()])
    assert len(_scan(TimeDomainKind.TIME_PROCESSED, history=processed_history).history) == 2
    with pytest.raises(DomainError) as excinfo:
        _scan(
            TimeDomainKind.TIME_PROCESSED,
            history=ProcessingHistory([ifft]),
        )
    assert excinfo.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH


def test_scan_with_history_returns_new_object() -> None:
    original = _scan(TimeDomainKind.TIME_BASE)
    history = ProcessingHistory([_osl(), _ifft(CALIBRATED)])
    updated = original.with_history(history)
    assert updated is not original
    assert len(updated.history) == 2
    assert updated.history.records[1].input_domain is CALIBRATED
    assert len(original.history) == 1
    # Re-attaching the equal history is an idempotent no-op.
    assert original.with_history(original.history) is original
    with pytest.raises(DomainError):
        original.with_history(ProcessingHistory([_ifft(RAW), _dewow()]))


def test_no_uncalibrated_depth_fields() -> None:
    # Depth is an uncalibrated concept: it must not appear in these models.
    for cls in (TimeDomainScan, ProcessingRecord, ProcessingHistory):
        names = {field.name for field in dataclasses.fields(cls)}
        assert "depth" not in names
        assert not any("depth" in name for name in names)
        assert not hasattr(cls, "depth")
    # The models carry no fake depth-time values either.
    assert TimeDomainScan.__dataclass_fields__.keys() == {
        "channels", "time_axis_s", "data", "kind", "history", "metadata",
    }
