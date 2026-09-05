"""Contract tests for the ISSUE-036 processing orchestrator (application layer).

Deterministic pure tests plus scratch-dir storage round trips: no hardware, no
threads, no sleeps.  Randomness is confined to fixed-seed ``default_rng``.

Contract summary (docs/issues/M06_CALIBRATION_PROCESSING.md ISSUE-036,
docs/PROCESSING.md sections 1/2/7, docs/CALIBRATION.md sections 1/5/7,
docs/DATA_FORMAT.md sections 2/3/3.1, t1 baseline report section 3,
docs/plans/2026-09-05-issue-036-orchestration.md D1-D9):

- :func:`run_processing` is the single complete-chain implementation; the chain
  order is optional OSL -> calibrated snapshot -> optional air background ->
  optional bandpass -> IFFT/time_base -> optional Dewow -> optional Flat;
- two strict entries: fresh raw (empty history required) and safe replay reuse
  (strictly identical profile provenance only, never a second calibration);
- processing revision / cancellation: stale display results are dropped while
  raw storage stays byte-identical;
- derived data + history attach to a ground ``.rcscan`` through a controlled
  interface that only writes schema-declared optional groups;
- every combination keeps ``frequency_raw`` bytes unchanged, keeps the domain /
  history order legal, always produces ``time_base`` from IFFT and produces
  ``time_processed`` only when a time-domain stage is enabled.

Frozen reference digests exercised through the reused stages (t1 baseline):
``processing/osl_calibration.py = 30224c9a0091c02b``,
``processing/background_subtraction.py = a96d59f63289a8c8``,
``processing/bandpass.py = f707839674ceb5e1``,
``processing/time_domain.py = b7da55717148645b``,
``processing/dewow.py = 7efaa728ab3f96ad``,
``processing/flat_reflection.py = 01b9d9b7f2c5d321``.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import h5py
import numpy as np
import pytest

from uav_gpr.application.processing_orchestrator import (
    ENTRY_FRESH_RAW,
    ENTRY_SAFE_REPLAY_REUSE,
    AirBackgroundSelection,
    CalibratedSnapshot,
    DerivedAttachmentError,
    DerivedAttachmentWriter,
    DerivedWritePayload,
    ProcessingController,
    ProcessingProfile,
    ProcessingRequest,
    StaleProcessingResult,
    assert_raw_bytes_unchanged,
    attach_derived_result,
    derived_contract_for,
    raw_column_fingerprint,
    run_processing,
)
from uav_gpr.calibration.osl import OslCalibrationSet, build_osl_calibration
from uav_gpr.calibration.reference import AirBackgroundReference, ReferenceDomain
from uav_gpr.core.channels import ChannelSpec
from uav_gpr.core.config import MissionConfig
from uav_gpr.core.enums import (
    AcquisitionMode,
    DataDomain,
    EndpointRole,
    GnssFixQuality,
    GnssMatchMethod,
    GnssNoFixPolicy,
    LogicalPolarization,
    MissionTerminalState,
    SParameter,
    TimeDomainKind,
    TraceQualityStatus,
)
from uav_gpr.core.errors import DomainError, ErrorCode
from uav_gpr.core.frequency import FrequencyScan, FrequencySweep
from uav_gpr.core.gnss import GnssFix, GnssMatch
from uav_gpr.core.identifiers import (
    AirFileId,
    BackgroundReferenceId,
    CalibrationProfileId,
    DeviceId,
    GroundFileId,
    MissionId,
    TraceUid,
)
from uav_gpr.core.metadata import TraceMetadata
from uav_gpr.core.raw_hash import compute_raw_trace_sha256
from uav_gpr.core.time_domain import ProcessingHistory, ProcessingRecord, TimeDomainScan
from uav_gpr.core.timeutil import ManualClock, MonotonicNs
from uav_gpr.processing.background_subtraction import AIR_BACKGROUND_STAGE_NAME
from uav_gpr.processing.bandpass import BANDPASS_STAGE_NAME
from uav_gpr.processing.dewow import DEWOW_STAGE_NAME
from uav_gpr.processing.flat_reflection import FLAT_STAGE_NAME
from uav_gpr.processing.osl_calibration import (
    OSL_CALIBRATION_STAGE_NAME,
    osl_set_digest,
)
from uav_gpr.processing.time_domain import IFFT_STAGE_NAME
from uav_gpr.storage import rcscan_v2 as schema
from uav_gpr.storage.incremental_writer import RcScanIncrementalWriter, TraceAppendRequest
from uav_gpr.storage.rcscan_reader import RcScanReader, RcScanValidator

# ---------------------------------------------------------------------------
# Shared synthetic scenario (dual-channel S11/S22 reflection mission).
# ---------------------------------------------------------------------------

FREQUENCY_HZ = np.linspace(800e6, 2600e6, 33)
_NORM = (FREQUENCY_HZ - FREQUENCY_HZ[0]) / (FREQUENCY_HZ[-1] - FREQUENCY_HZ[0])

CH_S11 = ChannelSpec("hh_s11", LogicalPolarization.HH, SParameter.S11, "S11 port")
CH_S22 = ChannelSpec("vv_s22", LogicalPolarization.VV, SParameter.S22, "S22 port")
CHANNELS = (CH_S11, CH_S22)

PID_S11 = CalibrationProfileId("aaaaaaaa-1111-4111-8111-111111111111")
PID_S22 = CalibrationProfileId("bbbbbbbb-2222-4222-8222-222222222222")
PID_OTHER = CalibrationProfileId("cccccccc-3333-4333-8333-333333333333")
BGID = BackgroundReferenceId("dddddddd-4444-4444-8444-444444444444")

MISSION_ID = MissionId("0f0e8a3b-6f2d-4c1e-9a7b-112233445566")
DEVICE_ID = DeviceId("d1c0ffee-0000-4000-8000-000000000001")
GROUND_FILE_ID = GroundFileId("aaaaaaa2-0000-4000-8000-000000000002")
_AIR_FILE_ID_STR = "aaaaaaa3-0000-4000-8000-000000000003"

CREATED_UTC = datetime(2026, 9, 5, 8, 0, 0, tzinfo=UTC)
STAMP_UTC = datetime(2026, 9, 5, 9, 0, 0, tzinfo=UTC)
WRITER_VERSION = "uav-gpr.issue036.test.1"

BANDPASS_EDGES = (900e6, 1100e6, 2200e6, 2400e6)
TRACE_COUNT = 5
DEWOW_WINDOW_S = 8e-9


def _error_terms(shift: float = 0.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    directivity = (0.025 + 0.008j) * np.exp(1j * (_NORM + shift))
    tracking = (0.91 - 0.04j) * np.exp(-0.08j * (_NORM + shift))
    source_match = 0.07 + 0.015j * (_NORM + shift)
    return directivity, tracking, source_match


def _forward(gamma: complex, terms: tuple[np.ndarray, np.ndarray, np.ndarray]) -> np.ndarray:
    d, t, s = terms
    return d + gamma * t / (1.0 - gamma * s)


def _profile_of(
    channel: ChannelSpec,
    profile_id: CalibrationProfileId,
    *,
    shift: float = 0.0,
) -> object:
    terms = _error_terms(shift)
    return build_osl_calibration(
        channel=channel,
        frequency_hz=FREQUENCY_HZ,
        open_measured=_forward(1.0 + 0.0j, terms),
        short_measured=_forward(-1.0 + 0.0j, terms),
        load_measured=_forward(0.0 + 0.0j, terms),
        profile_id=profile_id,
    )


def _calibration() -> OslCalibrationSet:
    return OslCalibrationSet(
        (
            _profile_of(CH_S11, PID_S11),
            _profile_of(CH_S22, PID_S22, shift=0.25),
        )
    )


def _resolved_calibration() -> OslCalibrationSet:
    """Same profile ids, different content (a re-solve): digests differ."""
    return OslCalibrationSet(
        (
            _profile_of(CH_S11, PID_S11, shift=0.4),
            _profile_of(CH_S22, PID_S22, shift=0.65),
        )
    )


def _swapped_calibration() -> OslCalibrationSet:
    """Channel bindings swapped relative to the recorded provenance."""
    return OslCalibrationSet(
        (
            _profile_of(CH_S22, PID_S22),
            _profile_of(CH_S11, PID_S11, shift=-0.25),
        )
    )


def _raw_scan(rng_seed: int = 21, traces: int = TRACE_COUNT) -> FrequencyScan:
    rng = np.random.default_rng(rng_seed)
    shape = (traces, 2, FREQUENCY_HZ.size)
    data = rng.standard_normal(shape) + 1j * rng.standard_normal(shape)
    return FrequencyScan(channels=CHANNELS, frequencies_hz=FREQUENCY_HZ, data=data)


def _background(
    domain: ReferenceDomain, *, profile_id: CalibrationProfileId | None
) -> AirBackgroundReference:
    rng = np.random.default_rng(31)
    mean = rng.standard_normal((2, FREQUENCY_HZ.size)) + 1j * rng.standard_normal(
        (2, FREQUENCY_HZ.size)
    )
    return AirBackgroundReference(
        channels=CHANNELS,
        frequency_hz=FREQUENCY_HZ.copy(),
        mean_data=np.ascontiguousarray(mean, dtype=np.complex128),
        trace_count=12,
        domain=domain,
        calibration_profile_id=profile_id,
    )


def _selection(
    *,
    osl: bool = False,
    background: ReferenceDomain | None = None,
    bandpass: bool = False,
    dewow: bool = False,
    flat: bool = False,
) -> ProcessingProfile:
    calibration = _calibration() if osl else None
    selection: AirBackgroundSelection | None = None
    if background is not None:
        selection = AirBackgroundSelection(
            reference=_background(
                background,
                profile_id=(
                    PID_S11
                    if background is ReferenceDomain.OSL_CALIBRATED
                    else None
                ),
            ),
            reference_id=BGID,
            current_calibration=calibration,
        )
    return ProcessingProfile(
        calibration=calibration,
        background=selection,
        bandpass_edges_hz=BANDPASS_EDGES if bandpass else None,
        dewow_window_s=DEWOW_WINDOW_S if dewow else None,
        flat_window_traces=3 if flat else None,
    )


def _request(profile: ProcessingProfile, **overrides: object) -> ProcessingRequest:
    kwargs: dict[str, object] = {
        "profile": profile,
        "source": _raw_scan(),
        "history": ProcessingHistory(),
        "executed_utc": STAMP_UTC,
    }
    kwargs.update(overrides)
    return ProcessingRequest(**kwargs)  # type: ignore[arg-type]


COMBINATIONS: list[tuple[str, dict[str, object]]] = [
    ("all_off", {}),
    ("osl_only", {"osl": True}),
    ("background_on_raw", {"background": ReferenceDomain.RAW}),
    ("osl_plus_background", {"osl": True, "background": ReferenceDomain.OSL_CALIBRATED}),
    ("bandpass_only", {"bandpass": True}),
    ("osl_bandpass", {"osl": True, "bandpass": True}),
    (
        "full_chain_no_flat",
        {
            "osl": True,
            "background": ReferenceDomain.OSL_CALIBRATED,
            "bandpass": True,
            "dewow": True,
        },
    ),
    (
        "full_chain",
        {
            "osl": True,
            "background": ReferenceDomain.OSL_CALIBRATED,
            "bandpass": True,
            "dewow": True,
            "flat": True,
        },
    ),
    ("dewow_only", {"dewow": True}),
    ("flat_only", {"flat": True}),
    ("dewow_then_flat", {"dewow": True, "flat": True}),
    (
        "background_bandpass_dewow",
        {"background": ReferenceDomain.RAW, "bandpass": True, "dewow": True},
    ),
    ("osl_flat", {"osl": True, "flat": True}),
    ("osl_background_ifft", {"osl": True, "background": ReferenceDomain.OSL_CALIBRATED}),
]

CANONICAL_ORDER = [
    OSL_CALIBRATION_STAGE_NAME,
    AIR_BACKGROUND_STAGE_NAME,
    BANDPASS_STAGE_NAME,
    IFFT_STAGE_NAME,
    DEWOW_STAGE_NAME,
    FLAT_STAGE_NAME,
]

FLAG_TO_STAGE = {
    "osl": OSL_CALIBRATION_STAGE_NAME,
    "background": AIR_BACKGROUND_STAGE_NAME,
    "bandpass": BANDPASS_STAGE_NAME,
    "dewow": DEWOW_STAGE_NAME,
    "flat": FLAT_STAGE_NAME,
}


def _stage_names(result) -> list[str]:
    return [record.stage_name for record in result.history.records]


# ---------------------------------------------------------------------------
# 1. Chain order and key combinations (acceptance 1 + 5).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "flags"), COMBINATIONS, ids=[combo[0] for combo in COMBINATIONS]
)
def test_chain_order_and_domains(name: str, flags: dict[str, object]) -> None:
    profile = _selection(**flags)  # type: ignore[arg-type]
    result = run_processing(_request(profile))

    expected = [stage for key, on in flags.items() if on for stage in [FLAG_TO_STAGE[key]]]
    actual = _stage_names(result)
    frequency_part = [
        stage
        for stage in expected
        if stage
        in {
            OSL_CALIBRATION_STAGE_NAME,
            AIR_BACKGROUND_STAGE_NAME,
            BANDPASS_STAGE_NAME,
        }
    ]
    time_part = [s for s in expected if s in {DEWOW_STAGE_NAME, FLAT_STAGE_NAME}]
    assert actual == [*frequency_part, IFFT_STAGE_NAME, *time_part], name
    # The sequence is always the canonical chain restricted to enabled stages.
    assert actual == [s for s in CANONICAL_ORDER if s in set([*expected, IFFT_STAGE_NAME])]
    assert len(set(actual)) == len(actual)

    assert result.time_base.kind is TimeDomainKind.TIME_BASE
    has_time_stage = bool({"dewow", "flat"} & set(flags))
    if has_time_stage:
        assert result.time_processed is not None
        assert result.time_processed.kind is TimeDomainKind.TIME_PROCESSED
        assert result.final_domain is DataDomain.TIME_PROCESSED
    else:
        assert result.time_processed is None
        assert result.final_domain is DataDomain.TIME_BASE
    assert result.final_domain is result.history.records[-1].output_domain

    if profile.calibration is not None:
        assert result.calibrated_snapshot is not None
        assert _stage_names_of(result.calibrated_snapshot.history)[0] == (
            OSL_CALIBRATION_STAGE_NAME
        )
        assert result.calibrated_snapshot.calibration_digest == osl_set_digest(
            profile.calibration
        )
        assert result.calibrated_record_count == 1
    else:
        assert result.calibrated_snapshot is None


def _stage_names_of(history: ProcessingHistory) -> list[str]:
    return [record.stage_name for record in history.records]


def test_time_base_is_always_the_ifft_output() -> None:
    result = run_processing(_request(_selection(osl=True, bandpass=True, dewow=True)))
    names = _stage_names(result)
    cut = names.index(IFFT_STAGE_NAME)
    assert result.history.records[cut].output_domain is DataDomain.TIME_BASE
    assert _stage_names_of(result.time_base.history) == names[: cut + 1]
    assert np.array_equal(result.time_axis_s, result.time_base.time_axis_s)


def test_display_crop_never_enters_history() -> None:
    physical = run_processing(_request(_selection()))
    cropped = run_processing(
        _request(_selection(), display_start_s=0.0, display_duration_s=5e-9)
    )
    assert _stage_names(cropped) == _stage_names(physical)
    assert cropped.display_view is not None
    assert cropped.display_view.sample_count < physical.time_base.data.shape[-1]
    assert cropped.time_base.data.shape == physical.time_base.data.shape


def test_sweep_input_supported_and_container_preserved() -> None:
    rng = np.random.default_rng(5)
    sweep = FrequencySweep(
        channels=CHANNELS,
        frequencies_hz=FREQUENCY_HZ,
        data=rng.standard_normal((2, FREQUENCY_HZ.size))
        + 1j * rng.standard_normal((2, FREQUENCY_HZ.size)),
    )
    result = run_processing(
        ProcessingRequest(
            profile=_selection(osl=True),
            source=sweep,
            history=ProcessingHistory(),
            executed_utc=STAMP_UTC,
        )
    )
    snapshot = result.calibrated_snapshot
    assert snapshot is not None
    assert isinstance(snapshot.source, FrequencySweep)
    assert isinstance(result.time_base, TimeDomainScan)


# ---------------------------------------------------------------------------
# 2. Fresh raw entry strictness (acceptance 2).
# ---------------------------------------------------------------------------


def test_fresh_entry_rejects_non_empty_history() -> None:
    warm = run_processing(_request(_selection(osl=True)))
    with pytest.raises(DomainError) as caught:
        run_processing(
            ProcessingRequest(
                profile=_selection(),
                source=_raw_scan(),
                history=warm.history,
                entry=ENTRY_FRESH_RAW,
                executed_utc=STAMP_UTC,
            )
        )
    assert caught.value.code is ErrorCode.INVALID_ARGUMENT
    assert "empty" in str(caught.value.message).lower()


def test_fresh_entry_requires_frequency_container() -> None:
    built = run_processing(_request(_selection()))
    with pytest.raises(DomainError):
        run_processing(
            ProcessingRequest(
                profile=_selection(),
                source=built.time_base,  # type: ignore[arg-type]
                history=ProcessingHistory(),
                entry=ENTRY_FRESH_RAW,
                executed_utc=STAMP_UTC,
            )
        )


def test_orchestrator_does_not_mutate_input_source() -> None:
    scan = _raw_scan()
    before = scan.data.tobytes()
    run_processing(
        _request(
            _selection(osl=True, bandpass=True, dewow=True, flat=True), source=scan
        )
    )
    assert scan.data.tobytes() == before
    assert not scan.data.flags.writeable


# ---------------------------------------------------------------------------
# 3. Safe replay reuse (acceptance 2 + 6).
# ---------------------------------------------------------------------------


def _snapshot_from(**flags: bool) -> tuple[CalibratedSnapshot, ProcessingProfile]:
    profile = _selection(**flags)  # type: ignore[arg-type]
    result = run_processing(_request(profile))
    snapshot = result.calibrated_snapshot
    assert snapshot is not None
    return snapshot, profile


def test_reuse_with_identical_provenance_skips_second_calibration() -> None:
    snapshot, profile = _snapshot_from(osl=True)
    reused = run_processing(
        ProcessingRequest(
            profile=profile,
            source=snapshot.source,
            history=snapshot.history,
            entry=ENTRY_SAFE_REPLAY_REUSE,
            snapshot=snapshot,
            executed_utc=STAMP_UTC + timedelta(seconds=1),
        )
    )
    names = _stage_names(reused)
    assert names.count(OSL_CALIBRATION_STAGE_NAME) == 1
    # Reuse keeps the requested profile's identity (same digest as a fresh run).
    assert reused.profile_digest == profile.profile_digest()
    assert names[0] == OSL_CALIBRATION_STAGE_NAME
    assert reused.reused_calibrated is True
    assert snapshot.source.data.shape == reused.calibrated_snapshot.source.data.shape


def test_reuse_matches_fresh_run_bit_exact() -> None:
    snapshot, profile = _snapshot_from(osl=True)
    reused = run_processing(
        ProcessingRequest(
            profile=profile,
            source=snapshot.source,
            history=snapshot.history,
            entry=ENTRY_SAFE_REPLAY_REUSE,
            snapshot=snapshot,
            executed_utc=STAMP_UTC,
        )
    )
    fresh = run_processing(_request(profile, executed_utc=STAMP_UTC))
    assert np.array_equal(reused.time_base.data, fresh.time_base.data)
    assert [r.to_dict() for r in reused.history.records] == [
        r.to_dict() for r in fresh.history.records
    ]


@pytest.mark.parametrize(
    "kind", ["resolved_content", "different_id", "swapped_binding", "legacy_provenance"]
)
def test_reuse_refuses_wrong_profile(kind: str) -> None:
    snapshot, profile = _snapshot_from(osl=True)
    calibration = profile.calibration
    assert calibration is not None
    if kind == "resolved_content":
        wrong = _resolved_calibration()
    elif kind == "different_id":
        other = _profile_of(CH_S11, PID_OTHER, shift=0.9)
        wrong = OslCalibrationSet((other, calibration.profiles[1]))
    elif kind == "swapped_binding":
        wrong = _swapped_calibration()
    else:
        stripped = ProcessingHistory(
            tuple(
                ProcessingRecord(
                    stage_name=record.stage_name,
                    stage_version=record.stage_version,
                    parameters={
                        key: value
                        for key, value in record.parameters.items()
                        if key not in {"profiles", "set_content_sha256"}
                    },
                    input_domain=record.input_domain,
                    output_domain=record.output_domain,
                    executed_utc=record.executed_utc,
                    software_version=record.software_version,
                    calibration_profile_id=record.calibration_profile_id,
                    background_reference_id=record.background_reference_id,
                )
                for record in snapshot.history.records
            )
        )
        snapshot = CalibratedSnapshot(
            source=snapshot.source,
            history=stripped,
            calibration_digest=snapshot.calibration_digest,
        )
        wrong = calibration
    mismatched = ProcessingProfile(
        calibration=wrong,
        background=profile.background,
        bandpass_edges_hz=profile.bandpass_edges_hz,
        dewow_window_s=profile.dewow_window_s,
        flat_window_traces=profile.flat_window_traces,
    )
    with pytest.raises(DomainError) as caught:
        run_processing(
            ProcessingRequest(
                profile=mismatched,
                source=snapshot.source,
                history=snapshot.history,
                entry=ENTRY_SAFE_REPLAY_REUSE,
                snapshot=snapshot,
                executed_utc=STAMP_UTC,
            )
        )
    assert caught.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH
    assert caught.value.context.get("mismatches")


def test_snapshot_construction_is_fail_closed() -> None:
    """A calibrated snapshot cannot be forged from a time-domain scan."""
    profile = _selection()
    bare = run_processing(_request(profile))
    with pytest.raises(DomainError) as caught:
        CalibratedSnapshot(
            source=bare.time_base,  # type: ignore[arg-type]
            history=bare.history,
            calibration_digest="0" * 64,
        )
    assert caught.value.code is ErrorCode.INVALID_ARGUMENT
    # A frequency container whose history does not end calibrated is refused too.
    raw_only = _raw_scan()
    with pytest.raises(DomainError) as caught2:
        CalibratedSnapshot(
            source=raw_only,
            history=ProcessingHistory(),
            calibration_digest="1" * 64,
        )
    assert caught2.value.code in {ErrorCode.PROCESSING_DOMAIN_MISMATCH, ErrorCode.INVALID_ARGUMENT}


def test_reuse_entry_refuses_missing_or_mismatched_snapshot() -> None:
    profile = _selection(osl=True)
    fresh = run_processing(_request(profile))
    # No snapshot at all.
    with pytest.raises(DomainError) as caught:
        run_processing(
            ProcessingRequest(
                profile=profile,
                source=_raw_scan(),
                history=ProcessingHistory(),
                entry=ENTRY_SAFE_REPLAY_REUSE,
                executed_utc=STAMP_UTC,
            )
        )
    assert caught.value.code is ErrorCode.INVALID_ARGUMENT
    # Snapshot of another mission's data (shape/content mismatch against request).
    snapshot = fresh.calibrated_snapshot
    assert snapshot is not None
    with pytest.raises(DomainError) as caught2:
        run_processing(
            ProcessingRequest(
                profile=profile,
                source=_raw_scan(rng_seed=99, traces=3),
                history=ProcessingHistory(),
                entry=ENTRY_SAFE_REPLAY_REUSE,
                snapshot=snapshot,
                executed_utc=STAMP_UTC,
            )
        )
    assert caught2.value.code in {
        ErrorCode.SHAPE_MISMATCH,
        ErrorCode.CHANNEL_CONTRACT_MISMATCH,
        ErrorCode.PROCESSING_DOMAIN_MISMATCH,
    }


def test_second_background_application_is_refused_by_the_chain() -> None:
    profile = _selection(background=ReferenceDomain.RAW)
    first = run_processing(_request(profile))
    with pytest.raises(DomainError) as caught:
        run_processing(
            ProcessingRequest(
                profile=profile,
                source=first.input_frequency_source,
                history=first.history,
                entry=ENTRY_FRESH_RAW,
                executed_utc=STAMP_UTC,
            )
        )
    assert caught.value.code in {
        ErrorCode.INVALID_ARGUMENT,
        ErrorCode.PROCESSING_DOMAIN_MISMATCH,
    }


def test_double_osl_through_stages_is_refused_even_bumping_version() -> None:
    from uav_gpr.processing.osl_calibration import OslCalibrationStage

    profile = _selection(osl=True)
    first = run_processing(_request(profile))
    stage = OslCalibrationStage(profile.calibration)
    with pytest.raises(DomainError) as caught:
        stage.apply(
            first.input_frequency_source,
            history=first.history,
            executed_utc=STAMP_UTC,
        )
    assert caught.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH


# ---------------------------------------------------------------------------
# 4. Snapshot serialization round trip (acceptance 2 + 7).
# ---------------------------------------------------------------------------


def test_calibrated_snapshot_round_trips_losslessly() -> None:
    snapshot, profile = _snapshot_from(osl=True)
    payload = snapshot.to_dict()
    restored = CalibratedSnapshot.from_dict(payload)
    assert np.array_equal(restored.source.data, snapshot.source.data)
    assert restored.source.data.dtype == snapshot.source.data.dtype
    assert [r.to_dict() for r in restored.history.records] == [
        r.to_dict() for r in snapshot.history.records
    ]
    assert restored.calibration_digest == snapshot.calibration_digest
    assert restored.calibration_digest == osl_set_digest(profile.calibration)
    reused = run_processing(
        ProcessingRequest(
            profile=profile,
            source=restored.source,
            history=restored.history,
            entry=ENTRY_SAFE_REPLAY_REUSE,
            snapshot=restored,
            executed_utc=STAMP_UTC,
        )
    )
    fresh = run_processing(_request(profile, executed_utc=STAMP_UTC))
    assert np.array_equal(reused.time_base.data, fresh.time_base.data)


# ---------------------------------------------------------------------------
# 5. Revision / cancellation (acceptance 3).
# ---------------------------------------------------------------------------


def test_revision_token_advances_and_publishes() -> None:
    controller = ProcessingController(initial_revision=1)
    token = controller.begin(1)
    result = run_processing(_request(_selection(osl=True, bandpass=True)), token=token)
    assert result.revision == 1
    assert controller.publish(result) is result
    assert controller.snapshot().visible_revision == 1


def test_stale_worker_result_is_dropped_before_publication() -> None:
    controller = ProcessingController(initial_revision=1)
    old = controller.begin(1)
    new = controller.begin(2)
    profile = _selection(bandpass=True)
    with pytest.raises(StaleProcessingResult) as caught:
        run_processing(_request(profile), token=old)
    assert caught.value.revision == 1
    assert caught.value.current_revision == 2
    fresh = run_processing(_request(profile), token=new)
    controller.publish(fresh)
    state = controller.snapshot()
    assert state.visible_revision == 2
    assert state.dropped >= 1
    assert not controller.accepts(1)


def test_republish_older_revision_never_overwrites() -> None:
    controller = ProcessingController(initial_revision=1)
    first = run_processing(_request(_selection()), token=controller.begin(1))
    assert controller.publish(first) is first
    second = run_processing(_request(_selection(dewow=True)), token=controller.begin(2))
    assert controller.publish(second) is second
    # A late publication of the older attempt is refused, state unchanged.
    assert controller.publish(first) is second
    assert controller.snapshot().visible_revision == 2


def test_cancelled_revision_raises_and_next_revision_completes() -> None:
    controller = ProcessingController(initial_revision=1)
    token = controller.begin(1)
    controller.cancel(token.revision)
    with pytest.raises(StaleProcessingResult) as caught:
        run_processing(_request(_selection()), token=token)
    assert caught.value.cancelled is True
    nxt = controller.begin(2)
    result = run_processing(_request(_selection(osl=True)), token=nxt)
    assert result.revision == 2
    controller.publish(result)
    assert controller.snapshot().visible_revision == 2


def test_cancellation_leaves_raw_storage_untouched(tmp_path: Path) -> None:
    path = build_ground_rcscan(tmp_path)
    before = raw_column_digest(path)
    controller = ProcessingController(initial_revision=1)
    token = controller.begin(1)
    profile = _selection(osl=True, bandpass=True, dewow=True)
    run_processing(_source_request(profile, path), token=token)
    controller.cancel(token.revision)
    with pytest.raises(StaleProcessingResult):
        run_processing(_source_request(profile, path), token=token)
    assert raw_column_digest(path) == before
    with h5py.File(path, "r") as h5:
        assert "/time_base/data" not in h5


# ---------------------------------------------------------------------------
# 6. Controlled storage attachment (acceptance 4 + 5 + 7).
# ---------------------------------------------------------------------------


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def raw_column_digest(path: Path) -> str:
    """Independent digest of /frequency/raw plus every required row column.

    Deliberately implemented separately from the module under test (no shared
    helper), so a bug in one cannot hide a change in the other.  Variable-length
    string cells are hashed element-wise with an explicit separator.
    """
    digest = hashlib.sha256()
    contracts = schema.dataset_contracts(len(CHANNELS), int(FREQUENCY_HZ.size))
    with h5py.File(path, "r") as h5:
        raw = h5["/frequency/raw"]
        digest.update(b"/frequency/raw" + bytes([0]))
        digest.update(str(raw.shape).encode("ascii"))
        digest.update(np.ascontiguousarray(raw[()], dtype="<c16").tobytes())
        for contract in contracts:
            path_name = contract.path
            if contract.optional or not path_name.startswith(
                ("/trace_metadata/", "/gnss/", "/acquisition/", "/transport/")
            ):
                continue
            dataset = h5.get(path_name)
            if dataset is None:
                continue
            values = dataset[()]
            digest.update(path_name.encode("utf-8"))
            digest.update(str(values.shape).encode("ascii"))
            if values.dtype.kind == "O" or values.dtype.kind in "STU":
                flat = np.atleast_1d(values).ravel()
                for item in flat:
                    payload = item if isinstance(item, bytes) else str(item).encode("utf-8")
                    digest.update(payload)
                    digest.update(b"\x00")
            else:
                digest.update(np.ascontiguousarray(values).tobytes())
    return digest.hexdigest()


def mission_config() -> MissionConfig:
    return MissionConfig(
        frequency_start_hz=float(FREQUENCY_HZ[0]),
        frequency_stop_hz=float(FREQUENCY_HZ[-1]),
        frequency_points=int(FREQUENCY_HZ.size),
        if_bw_hz=1_000.0,
        power_dbm=-3.0,
        channels=CHANNELS,
        acquisition_mode=AcquisitionMode.FIXED_COUNT,
        planned_trace_count=TRACE_COUNT,
        target_interval_s=0.1,
        gnss_max_age_s=2.0,
        gnss_no_fix_policy=GnssNoFixPolicy.RECORD_WITHOUT_POSITION,
        calibration_profile_id=None,
        apply_calibration=False,
        background_reference_id=None,
        apply_background=False,
        created_utc=CREATED_UTC,
        software_version="0.1.0.dev0",
    )


def trace_metadata(index: int, raw: np.ndarray) -> TraceMetadata:
    midpoint = CREATED_UTC + timedelta(seconds=1 + index)
    monotonic = 1_000_000 * index
    match = GnssMatch(
        fix=GnssFix(
            received_utc=midpoint,
            nmea_utc=midpoint,
            received_monotonic_ns=MonotonicNs(monotonic),
            latitude_deg=30.123456,
            longitude_deg=120.654321,
            altitude_msl_m=42.5,
            geoid_separation_m=12.25,
            fix_quality=GnssFixQuality.RTK_FLOAT,
            satellites=14,
            hdop=0.8,
            ground_speed_mps=3.25,
            course_deg=181.5,
            valid=True,
            invalid_reason=None,
        ),
        trace_midpoint_utc=midpoint,
        age_s=0.12,
        method=GnssMatchMethod.NEAREST_MIDPOINT,
        usable_for_map=True,
        reason=None,
    )
    uid = TraceUid(f"eeeeeeee-{index:04d}-4000-8000-00000000000{index}")
    return TraceMetadata(
        mission_id=MISSION_ID,
        trace_index=index,
        trace_uid=uid,
        device_id=DEVICE_ID,
        sweep_started_utc=midpoint - timedelta(microseconds=500),
        sweep_midpoint_utc=midpoint,
        sweep_finished_utc=midpoint + timedelta(microseconds=500),
        sweep_started_monotonic_ns=MonotonicNs(monotonic),
        sweep_midpoint_monotonic_ns=MonotonicNs(monotonic + 500_000),
        sweep_finished_monotonic_ns=MonotonicNs(monotonic + 1_000_000),
        target_interval_s=0.1,
        actual_interval_s=0.1001,
        schedule_error_s=0.0001,
        connection_generation=1,
        raw_trace_sha256=compute_raw_trace_sha256(
            MISSION_ID, index, uid, CHANNELS, FREQUENCY_HZ, raw
        ),
        gnss_match=match,
        quality_status=TraceQualityStatus.NOMINAL,
        quality_reasons=(),
    )


def build_rcscan(
    tmp_path: Path,
    *,
    traces: int = TRACE_COUNT,
    role: EndpointRole = EndpointRole.GROUND,
    finalize: bool = True,
) -> Path:
    """Create a ``.rcscan`` with ``traces`` committed rows (finalize optional)."""
    config = mission_config()
    file_id = (
        GROUND_FILE_ID if role is EndpointRole.GROUND else AirFileId(_AIR_FILE_ID_STR)
    )
    writer = RcScanIncrementalWriter.create(
        tmp_path,
        mission_id=MISSION_ID,
        device_id=DEVICE_ID,
        file_id=file_id,
        role=role,
        config=config,
        channels=CHANNELS,
        frequencies_hz=FREQUENCY_HZ,
        created_utc=CREATED_UTC,
        writer_version=WRITER_VERSION,
        clock=ManualClock(CREATED_UTC, monotonic_ns=0),
    )
    scan = _raw_scan(traces=traces)
    for index in range(traces):
        raw = np.ascontiguousarray(scan.data[index], dtype="<c16")
        writer.append_trace(
            TraceAppendRequest(
                metadata=trace_metadata(index, raw),
                frequency_raw=raw,
                channels=CHANNELS,
                frequencies_hz=FREQUENCY_HZ,
                config_sha256=config.config_sha256,
            )
        )
    if not finalize:
        # Leave the writer open: the partial keeps lifecycle_state="writing".
        return Path(writer.partial_path)
    finalized = writer.close(MissionTerminalState.COMPLETED, ended_utc=STAMP_UTC)
    return Path(finalized.final_path)


def build_ground_rcscan(tmp_path: Path, *, traces: int = TRACE_COUNT) -> Path:
    """Create and finalize a ground ``.rcscan`` with ``traces`` committed rows."""
    return build_rcscan(tmp_path, traces=traces)


def _committed_scan(path: Path) -> FrequencyScan:
    with h5py.File(path, "r") as h5:
        raw = np.asarray(h5["/frequency/raw"][()], dtype="<c16")
    return FrequencyScan(channels=CHANNELS, frequencies_hz=FREQUENCY_HZ, data=raw)


def _source_request(profile: ProcessingProfile, path: Path) -> ProcessingRequest:
    return ProcessingRequest(
        profile=profile,
        source=_committed_scan(path),
        history=ProcessingHistory(),
        executed_utc=STAMP_UTC,
    )


def test_attachment_refuses_a_grid_the_frozen_reader_cannot_validate(
    tmp_path: Path,
) -> None:
    """The 011 reader validates present derived groups against the schema
    default (time_points == frequency_points).  An interpolated IFFT grid is
    wider than that, so a conformant mission must be refused — with the ground
    file left byte-identical and every raw row still readable."""
    path = build_ground_rcscan(tmp_path)
    raw_before = raw_column_digest(path)
    whole_before = file_sha256(path)
    profile = _selection(osl=True, bandpass=True, dewow=True)
    result = run_processing(_source_request(profile, path))
    written = attach_derived_result(path, result)

    assert written.published is False
    assert written.refused_reason == "strict_validation"
    assert written.derived_paths == ()
    assert raw_column_digest(path) == raw_before
    assert file_sha256(path) == whole_before
    with h5py.File(path, "r") as h5:
        assert "/time_base/data" not in h5
        assert "/frequency/calibrated" not in h5
    report = RcScanValidator.validate(path)
    assert report.summary()["missing"] == 0


def test_archivable_grid_guard_is_fail_closed() -> None:
    from uav_gpr.application.processing_orchestrator import (
        archive_to_schema_grid,
        archived_frequency_points,
    )

    assert archived_frequency_points(64) == 64
    with pytest.raises(DomainError) as caught:
        archived_frequency_points(33)
    assert caught.value.code is ErrorCode.SHAPE_MISMATCH
    # A chain on an archivable grid passes straight through (no silent resample).
    axis = np.linspace(800e6, 2600e6, 33)
    scan = FrequencyScan(
        channels=CHANNELS,
        frequencies_hz=axis,
        data=_raw_scan().data,
    )
    result = run_processing(
        ProcessingRequest(
            profile=ProcessingProfile(),
            source=scan,
            history=ProcessingHistory(),
            executed_utc=STAMP_UTC,
        )
    )
    same = archive_to_schema_grid(result.time_base)
    assert same is result.time_base


def test_payload_matches_the_contract_of_its_own_grid(tmp_path: Path) -> None:
    """Whatever lands on disk must match dataset_contracts(time_points=n_actual)."""
    path = build_ground_rcscan(tmp_path)
    result = run_processing(_source_request(_selection(osl=True), path))
    payload = result.derived_payload()
    time_samples = int(result.time_base.data.shape[2])
    # The module's own contract view (single authority) must agree with a
    # directly computed one; neither may be hardcoded.
    contracts = dict(derived_contract_for(len(CHANNELS), int(FREQUENCY_HZ.size), time_samples))
    assert contracts == {
        contract.path: contract
        for contract in schema.dataset_contracts(
            len(CHANNELS), int(FREQUENCY_HZ.size), time_samples
        )
    }
    optional = {path_key for path_key, c in contracts.items() if c.optional}
    assert set(payload.groups) <= optional | {"_frequency_history_json"}
    for path_key in payload.paths():
        if path_key == "_frequency_history_json":
            continue
        values = np.asarray(payload.groups[path_key])
        contract = contracts[path_key]
        assert values.ndim == len(contract.initial_shape)
        if contract.maxshape[0] is not None:
            assert tuple(values.shape) == tuple(contract.initial_shape)
        else:
            assert tuple(values.shape[1:]) == tuple(contract.initial_shape[1:])
            assert values.shape[0] == TRACE_COUNT


def test_writer_refuses_payloads_outside_the_controlled_allow_list(
    tmp_path: Path,
) -> None:
    """The gate must not be bypassable by a caller-built payload."""
    path = build_ground_rcscan(tmp_path)
    whole_before = file_sha256(path)
    raw_before = raw_column_digest(path)
    fingerprint_before = assert_raw_bytes_unchanged(path)
    result = run_processing(_source_request(_selection(bandpass=True), path))
    keep = int(FREQUENCY_HZ.size)
    base = _schema_conformant_groups(result, keep=keep)

    # (a) a required dataset (raw) is never writable through this door.
    tampered = dict(base)
    tampered["/frequency/raw"] = np.zeros((TRACE_COUNT, len(CHANNELS), keep), dtype="<c16")
    with pytest.raises(DerivedAttachmentError):
        DerivedAttachmentWriter(path).write(_payload(tampered, result, time_samples=keep))

    # (b) an invented path outside the schema is refused.
    invented = dict(base)
    invented["/processing/extra"] = np.zeros(1, dtype="<f8")
    with pytest.raises(DerivedAttachmentError):
        DerivedAttachmentWriter(path).write(_payload(invented, result, time_samples=keep))

    # (c) wrong row count is refused before anything is staged.
    short = dict(base)
    short["/time_base/data"] = np.zeros((2, len(CHANNELS), keep), dtype="<c16")
    with pytest.raises(DerivedAttachmentError):
        DerivedAttachmentWriter(path).write(_payload(short, result, time_samples=keep))

    # (d) non-canonical history text is refused.
    broken = dict(base)
    broken["/time_base/history_json"] = np.array(
        ["not-json"], dtype=h5py.string_dtype(encoding="utf-8")
    )
    with pytest.raises(DerivedAttachmentError):
        DerivedAttachmentWriter(path).write(_payload(broken, result, time_samples=keep))

    # Nothing landed and raw is byte-identical (real before/after comparison).
    with h5py.File(path, "r") as h5:
        assert "/time_base/data" not in h5
    assert file_sha256(path) == whole_before
    assert raw_column_digest(path) == raw_before
    assert assert_raw_bytes_unchanged(path, fingerprint_before) == fingerprint_before


def test_saved_loaded_replay_round_trip_is_bit_exact(tmp_path: Path) -> None:
    """Deterministic replay: the same raw + profile reproduces every derived
    array and the whole history byte-for-byte (and refuses a stale grid only
    after checking it against the frozen contract)."""
    path = build_ground_rcscan(tmp_path)
    raw_before = raw_column_digest(path)
    profile = _selection(
        osl=True,
        background=ReferenceDomain.OSL_CALIBRATED,
        bandpass=True,
        dewow=True,
        flat=True,
    )
    first = run_processing(_source_request(profile, path))
    attach_derived_result(path, first)

    replay = run_processing(_source_request(profile, path))
    written = attach_derived_result(path, replay)

    # Determinism of the orchestration itself: identical payloads both times.
    a = first.derived_payload()
    b = replay.derived_payload()
    assert a.paths() == b.paths()
    for key in a.paths():
        left, right = a.groups[key], b.groups[key]
        if isinstance(left, str):
            assert left == right
        elif hasattr(left, "dtype"):
            assert np.array_equal(np.asarray(left), np.asarray(right))
        else:
            assert str(left) == str(right)
    assert first.history_json() == replay.history_json()
    assert first.profile_digest == replay.profile_digest
    assert written.raw_fingerprint == raw_column_fingerprint(path)
    assert raw_column_digest(path) == raw_before

    # On-disk round trip when the grid is schema-conformant: what was written
    # is exactly what a fresh load/replay recomputes.
    if written.published:
        with h5py.File(path, "r") as h5:
            stored_time_base = np.asarray(h5["/time_base/data"][()], dtype="<c16")
            stored_processed = np.asarray(
                h5["/time_processed/data"][()], dtype="<c16"
            )
            raw_cell = h5["/time_base/history_json"][0]
            stored_history = (
                raw_cell.decode("utf-8")
                if isinstance(raw_cell, bytes)
                else str(raw_cell)
            )
            stored_calibrated = np.asarray(
                h5["/frequency/calibrated"][()], dtype="<c16"
            )
        assert np.array_equal(stored_time_base, replay.time_base.data)
        assert stored_history == replay.history_json()
        assert np.array_equal(
            stored_calibrated, replay.calibrated_snapshot.source.data
        )
        assert np.array_equal(stored_processed, replay.time_processed.data)

        entries = schema.loads_utf8_json(stored_history)
        assert isinstance(entries, list)
        snapshot = CalibratedSnapshot(
            source=FrequencyScan(
                channels=CHANNELS,
                frequencies_hz=FREQUENCY_HZ,
                data=stored_calibrated,
            ),
            history=ProcessingHistory(
                tuple(
                    ProcessingRecord.from_dict(entry)
                    for entry in entries[: first.calibrated_record_count]
                )
            ),
            calibration_digest=osl_set_digest(profile.calibration),
        )
        reused = run_processing(
            ProcessingRequest(
                profile=profile,
                source=snapshot.source,
                history=ProcessingHistory(),
                entry=ENTRY_SAFE_REPLAY_REUSE,
                snapshot=snapshot,
                executed_utc=STAMP_UTC,
            )
        )
        assert np.array_equal(reused.time_base.data, first.time_base.data)
        assert reused.profile_digest == first.profile_digest
    else:
        assert written.refused_reason == "strict_validation"


def test_reader_accepts_attached_file(tmp_path: Path) -> None:
    path = build_ground_rcscan(tmp_path)
    result = run_processing(_source_request(_selection(osl=True, bandpass=True), path))
    attach_derived_result(path, result)
    with RcScanReader(path) as reader:
        chunks = list(reader.iter_logical())
        total = sum(len(chunk.records) for chunk in chunks)
        assert total == TRACE_COUNT
        assert all(
            trace.hash_verified for chunk in chunks for trace in chunk.records
        )
        assert reader.mission_id == MISSION_ID


def test_attachment_rejects_row_count_mismatch_without_touching_raw(
    tmp_path: Path,
) -> None:
    path = build_ground_rcscan(tmp_path)
    result = run_processing(
        _request(_selection(dewow=True), source=_raw_scan(traces=2))
    )
    before = raw_column_digest(path)
    with pytest.raises(DerivedAttachmentError):
        attach_derived_result(path, result)
    assert raw_column_digest(path) == before


def test_assert_raw_bytes_unchanged_detects_tampering(tmp_path: Path) -> None:
    path = build_ground_rcscan(tmp_path)
    fingerprint = assert_raw_bytes_unchanged(path)
    assert fingerprint
    with h5py.File(path, "r+") as h5:
        row = np.array(h5["/frequency/raw"][0], dtype="<c16")
        row[0] += 1.0
        h5["/frequency/raw"][0] = row
    with pytest.raises(DerivedAttachmentError):
        assert_raw_bytes_unchanged(path, fingerprint)


def _cell_text(cell: object) -> str:
    return cell.decode("utf-8") if isinstance(cell, bytes) else str(cell)


def _schema_conformant_groups(result, *, keep: int) -> dict[str, object]:
    """The controlled writer's legal payload for one result on a given grid."""
    history = result.history_json()
    groups: dict[str, object] = {
        "/axes/time_base_s": np.asarray(result.time_base.time_axis_s[:keep], dtype="<f8"),
        "/time_base/data": np.asarray(result.time_base.data[..., :keep], dtype="<c16"),
        "/time_base/history_json": np.array(
            [history], dtype=h5py.string_dtype(encoding="utf-8")
        ),
    }
    snapshot = result.calibrated_snapshot
    if snapshot is not None:
        groups["/frequency/calibrated"] = np.asarray(snapshot.source.data, dtype="<c16")
        groups["_frequency_history_json"] = history
    processed = result.time_processed
    if processed is not None:
        groups["/axes/time_processed_s"] = np.asarray(
            processed.time_axis_s[:keep], dtype="<f8"
        )
        groups["/time_processed/data"] = np.asarray(
            processed.data[..., :keep], dtype="<c16"
        )
        groups["/time_processed/history_json"] = np.array(
            [history], dtype=h5py.string_dtype(encoding="utf-8")
        )
    return groups


def _payload(groups: dict[str, object], result, *, time_samples: int) -> DerivedWritePayload:
    return DerivedWritePayload(
        groups=groups,
        history_records=len(result.history.records),
        trace_count=int(result.time_base.data.shape[0]),
        time_samples=time_samples,
        profile_digest=result.profile_digest,
    )


def _schema_conformant_payload(result) -> DerivedWritePayload:
    """Project one result onto the grid the frozen reader validates.

    Keeps the archived values of the first frequency_points samples (the
    interpolated FFT grid is uniform, so this is one sample per native bin — the
    shape ISSUE-008 ties /axes/time_*_s to).  Test-only: the orchestrator itself
    never resamples a stage's output.
    """
    keep = int(FREQUENCY_HZ.size)
    return _payload(_schema_conformant_groups(result, keep=keep), result, time_samples=keep)


def test_attachment_refuses_an_air_file(tmp_path: Path) -> None:
    """F1 (P2): derived data is a ground-end capability only.

    AGENTS.md section 6 / docs/DATA_FORMAT.md section 6 authorize calibration,
    processing and derived data for the GROUND copy; the air end is a lightweight
    executor.  An air-role file must be refused fail-closed by the controlled
    writer -- before any staging copy is made, with the file byte-identical.
    """
    air_dir = tmp_path / "air"
    air_dir.mkdir()
    path = build_rcscan(air_dir, role=EndpointRole.AIR)
    whole_before = file_sha256(path)
    raw_before = raw_column_digest(path)
    fingerprint_before = assert_raw_bytes_unchanged(path)

    result = run_processing(_source_request(_selection(osl=True, bandpass=True), path))
    payload = _schema_conformant_payload(result)
    with pytest.raises(DerivedAttachmentError) as caught:
        DerivedAttachmentWriter(path).write(payload)
    assert caught.value.code is ErrorCode.INVALID_ARGUMENT
    assert "ground" in str(caught.value.message).lower()
    assert caught.value.context.get("file_role") == EndpointRole.AIR.value

    assert file_sha256(path) == whole_before
    assert raw_column_digest(path) == raw_before
    assert assert_raw_bytes_unchanged(path, fingerprint_before) == fingerprint_before
    with h5py.File(path, "r") as h5:
        assert "/time_base/data" not in h5


def test_attachment_refuses_a_writing_partial(tmp_path: Path) -> None:
    """F1 (P2): replacing an in-flight partial would fork the writer's handle.

    ``lifecycle_state=writing`` files are owned by a live incremental writer;
    publishing derived data over them must be refused (not left to whatever the
    OS file lock happens to do), again with the original bytes untouched.
    """
    part_dir = tmp_path / "partial"
    part_dir.mkdir()
    path = build_rcscan(part_dir, finalize=False)
    whole_before = file_sha256(path)
    raw_before = raw_column_digest(path)

    # Same committed rows, computed from the partial itself.
    profile = _selection(bandpass=True)
    result = run_processing(_source_request(profile, path))
    payload = _schema_conformant_payload(result)
    with pytest.raises(DerivedAttachmentError) as caught:
        DerivedAttachmentWriter(path).write(payload)
    assert caught.value.code is ErrorCode.INVALID_ARGUMENT
    assert caught.value.context.get("lifecycle_state") == "writing"

    assert file_sha256(path) == whole_before
    assert raw_column_digest(path) == raw_before
    with h5py.File(path, "r") as h5:
        assert "/time_base/data" not in h5
    # No staging litter left behind by the refusal.
    assert list(part_dir.glob("*.derived.tmp")) == []


def test_recovered_files_are_accepted(tmp_path: Path) -> None:
    """The gate allows the two settled states (finalized / recovered)."""
    good_dir = tmp_path / "ok"
    good_dir.mkdir()
    path = build_rcscan(good_dir)
    with h5py.File(path, "r+") as h5:
        h5.attrs["lifecycle_state"] = "recovered"
    result = run_processing(_source_request(_selection(bandpass=True), path))
    report = DerivedAttachmentWriter(path).write(_schema_conformant_payload(result))
    assert report.published is True


def test_report_serialization_exposes_the_refusal_reason(tmp_path: Path) -> None:
    """F4 (P3): auditors must see refusals through to_dict() too."""
    path = build_ground_rcscan(tmp_path)
    result = run_processing(_source_request(_selection(osl=True), path))
    refused = attach_derived_result(path, result)
    payload = refused.to_dict()
    assert payload["published"] is False
    assert payload["refused_reason"] == "strict_validation"

    landed = DerivedAttachmentWriter(path).write(
        _schema_conformant_payload(result)
    ).to_dict()
    assert landed["published"] is True
    assert landed["refused_reason"] is None
    assert "/time_base/data" in landed["derived_paths"]


def test_published_attachment_round_trips_and_is_replaceable(tmp_path: Path) -> None:
    """Full save -> strict load -> replay -> re-attach cycle on the success path."""
    path = build_ground_rcscan(tmp_path)
    profile = _selection(osl=True, bandpass=True, dewow=True, flat=True)
    result = run_processing(_source_request(profile, path))
    first_payload = _schema_conformant_payload(result)
    raw_before = raw_column_digest(path)
    whole_before = file_sha256(path)

    written = DerivedAttachmentWriter(path).write(first_payload)
    assert written.published is True
    assert "/time_base/data" in written.derived_paths
    assert "/time_processed/data" in written.derived_paths
    assert "/frequency/calibrated" in written.derived_paths
    assert raw_column_digest(path) == raw_before

    with RcScanReader(path) as reader:
        assert reader.committed_record_count == TRACE_COUNT
        chunks = list(reader.iter_logical())
        assert sum(len(c.records) for c in chunks) == TRACE_COUNT
        assert all(t.hash_verified for c in chunks for t in c.records)

    with h5py.File(path, "r") as h5:
        stored_history = _cell_text(h5["/time_base/history_json"][0])
        stored_time_base = np.asarray(h5["/time_base/data"][()], dtype="<c16")
        stored_calibrated = np.asarray(h5["/frequency/calibrated"][()], dtype="<c16")
        stored_processed = np.asarray(h5["/time_processed/data"][()], dtype="<c16")
        assert (
            str(h5["mission"].attrs["derived_profile_digest"]) == result.profile_digest
        )
        parsed = schema.loads_utf8_json(stored_history)
        assert [entry["stage_name"] for entry in parsed] == _stage_names(result)

    replay = run_processing(_source_request(profile, path))
    replay_payload = _schema_conformant_payload(replay)
    assert replay_payload.history_records == first_payload.history_records
    assert np.array_equal(
        np.asarray(replay_payload.groups["/time_base/data"]), stored_time_base
    )
    assert np.array_equal(
        np.asarray(replay_payload.groups["/frequency/calibrated"]), stored_calibrated
    )
    assert np.array_equal(
        np.asarray(replay_payload.groups["/time_processed/data"]), stored_processed
    )
    second = DerivedAttachmentWriter(path).write(replay_payload)
    assert second.published is True
    assert {"/time_base/data", "/time_processed/data"} <= set(second.replaced_existing)
    assert raw_column_digest(path) == raw_before
    assert file_sha256(path) != whole_before


def test_failed_attach_leaves_file_byte_identical(tmp_path: Path) -> None:
    path = build_ground_rcscan(tmp_path)
    result = run_processing(_source_request(_selection(osl=True), path))
    before = raw_column_digest(path)
    whole_before = file_sha256(path)
    # Sabotage the payload so the controlled writer refuses mid-flight.
    object.__setattr__(result, "_derived_override", {"mismatched-key": object()})
    with pytest.raises(DerivedAttachmentError):
        attach_derived_result(path, result)
    assert raw_column_digest(path) == before
    assert file_sha256(path) == whole_before


# ---------------------------------------------------------------------------
# 7. Profile identity and provenance completeness (acceptance 2 helper).
# ---------------------------------------------------------------------------


def test_profile_digest_tracks_content_not_identity() -> None:
    a = _selection(osl=True, bandpass=True, dewow=True)
    b = _selection(osl=True, bandpass=True, dewow=True)
    assert a.profile_digest() == b.profile_digest()
    c = _selection(osl=True, bandpass=True, dewow=True, flat=True)
    assert c.profile_digest() != a.profile_digest()
    resolved = ProcessingProfile(
        calibration=_resolved_calibration(),
        background=None,
        bandpass_edges_hz=BANDPASS_EDGES,
        dewow_window_s=DEWOW_WINDOW_S,
        flat_window_traces=None,
    )
    plain = ProcessingProfile(
        calibration=_calibration(),
        background=None,
        bandpass_edges_hz=BANDPASS_EDGES,
        dewow_window_s=DEWOW_WINDOW_S,
        flat_window_traces=None,
    )
    assert resolved.profile_digest() != plain.profile_digest()


def test_every_stage_record_keeps_full_provenance() -> None:
    result = run_processing(
        _request(
            _selection(
                osl=True,
                background=ReferenceDomain.OSL_CALIBRATED,
                bandpass=True,
                dewow=True,
                flat=True,
            )
        )
    )
    assert len(result.history.records) == 6
    for record in result.history.records:
        payload = record.to_dict()
        assert payload["stage_name"]
        # Time-domain stages legitimately hop time_processed -> time_processed
        # (docs/PROCESSING.md section 2); every frequency stage must change it.
        if payload["stage_name"] not in {DEWOW_STAGE_NAME, FLAT_STAGE_NAME}:
            assert payload["input_domain"] != payload["output_domain"]
        assert isinstance(payload["parameters"], dict) and payload["parameters"]
        assert payload["software_version"]
    rebuilt = ProcessingHistory.from_dict(
        {"records": [r.to_dict() for r in result.history.records]}
    )
    assert [r.to_dict() for r in rebuilt.records] == [
        r.to_dict() for r in result.history.records
    ]


def test_history_first_record_always_consumes_frequency_raw() -> None:
    for _, flags in COMBINATIONS:
        result = run_processing(_request(_selection(**flags)))  # type: ignore[arg-type]
        assert result.history.records[0].input_domain is DataDomain.FREQUENCY_RAW
