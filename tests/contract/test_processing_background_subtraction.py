"""Contract tests for the ISSUE-033 air-background subtraction processing stage.

Pure deterministic tests: synthetic complex spectra and frozen ISSUE-029
``AirBackgroundReference`` objects only - no hardware, no threads, no sleeps,
no file IO.  Randomness is confined to fixed-seed ``default_rng`` scenarios.

Contract summary (docs/issues/M06_CALIBRATION_PROCESSING.md ISSUE-033,
docs/CALIBRATION.md sections 4-5, docs/PROCESSING.md sections 1-2, t1 baseline
report section 3, docs/plans/2026-09-05-issue-033-bg-subtraction.md D1-D9):

- ``AirBackgroundSubtractionStage`` subtracts the reference ``mean_data``
  row-by-row in the complex frequency domain, turning ``frequency_raw`` or
  ``frequency_calibrated`` into a brand-new ``frequency_background_applied``
  model; scans broadcast the SAME per-channel vector along the trace axis
  (never any trace-axis statistics - that is Flat Reflection, ISSUE-035);
- data-domain protection: a raw reference can never hit calibrated data and
  vice versa; a calibrated input additionally requires the reference's
  ``calibration_profile_id`` to equal the profile id recorded by the history
  AND that record's stored ``content_sha256`` to equal the digest recomputed
  from the live calibration set (same ID, different content is rejected);
- exact ordered channel binding, full frequency-axis equality, shape/dtype/
  finiteness checks on the reference - all fail closed with structured
  DomainError contexts;
- history/provenance: one appended record carrying ``background_reference_id``
  plus an ordered ``{channel_id, s_parameter}`` reference description with
  content digests; calibrated inputs explicitly inherit the matching
  ``calibration_profile_id``; duplicate application is refused twice over
  (the stage's predecessor gate and the core per-history stage-name
  uniqueness - a bumped ``stage_version`` does not bypass it);
- inputs are never mutated; outputs are write-protected fresh copies.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime

import numpy as np
import pytest

from uav_gpr.calibration.osl import OslCalibrationSet, build_osl_calibration
from uav_gpr.calibration.reference import AirBackgroundReference, ReferenceDomain
from uav_gpr.core import time_domain as core_time_domain
from uav_gpr.core.channels import ChannelSpec
from uav_gpr.core.enums import DataDomain, LogicalPolarization, SParameter
from uav_gpr.core.errors import DomainError, ErrorCode, JsonValue
from uav_gpr.core.frequency import FrequencyScan, FrequencySweep
from uav_gpr.core.identifiers import BackgroundReferenceId, CalibrationProfileId
from uav_gpr.core.time_domain import ProcessingHistory, ProcessingRecord
from uav_gpr.processing.background_subtraction import (
    AIR_BACKGROUND_STAGE_NAME,
    AIR_BACKGROUND_STAGE_VERSION,
    AirBackgroundSubtractionStage,
    background_reference_digest,
    check_safe_reuse,
    require_matching_calibration_provenance,
)
from uav_gpr.processing.bandpass import BandpassStage, ProcessingStage
from uav_gpr.processing.osl_calibration import (
    OslCalibrationStage,
    osl_profile_digest,
)

# ---------------------------------------------------------------------------
# Shared synthetic scenario (mirrors the frozen ISSUE-027/032 test vectors).
# ---------------------------------------------------------------------------

FREQUENCY_HZ = np.linspace(0.5e9, 2.5e9, 41)
_NORM = (FREQUENCY_HZ - FREQUENCY_HZ[0]) / (FREQUENCY_HZ[-1] - FREQUENCY_HZ[0])

CH_S11 = ChannelSpec("ch_s11", LogicalPolarization.HH, SParameter.S11, "S11 antenna")
CH_S22 = ChannelSpec("ch_s22", LogicalPolarization.VV, SParameter.S22, "S22 antenna")

PID_S11 = CalibrationProfileId("11111111-1111-4111-8111-111111111111")
PID_S22 = CalibrationProfileId("22222222-2222-4222-8222-222222222222")
PID_OTHER = CalibrationProfileId("33333333-3333-4333-8333-333333333333")

BGID_RAW = BackgroundReferenceId(uuid.UUID(int=101))
BGID_CAL = BackgroundReferenceId(uuid.UUID(int=202))
BGID_OTHER = BackgroundReferenceId(uuid.UUID(int=303))

STAMP_UTC = datetime(2026, 9, 5, 12, 0, 0, tzinfo=UTC)


def _error_terms(seed_shift: float = 0.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    directivity = (0.025 + 0.008j) * np.exp(1j * (_NORM + seed_shift))
    tracking = (0.91 - 0.04j) * np.exp(-0.08j * (_NORM + seed_shift))
    source_match = 0.07 + 0.015j * (_NORM + seed_shift)
    return directivity, tracking, source_match


def _forward(
    gamma: np.ndarray | complex,
    directivity: np.ndarray,
    tracking: np.ndarray,
    source_match: np.ndarray,
) -> np.ndarray:
    g = np.asarray(gamma, dtype=np.complex128)
    return directivity + g * tracking / (1.0 - g * source_match)


def _profile(
    channel: ChannelSpec,
    profile_id: CalibrationProfileId,
    *,
    seed_shift: float = 0.0,
    frequency_hz: np.ndarray | None = None,
) -> object:
    axis = FREQUENCY_HZ if frequency_hz is None else frequency_hz
    d, t, s = _error_terms(seed_shift)
    return build_osl_calibration(
        channel=channel,
        frequency_hz=axis,
        open_measured=_forward(1.0 + 0.0j, d, t, s),
        short_measured=_forward(-1.0 + 0.0j, d, t, s),
        load_measured=_forward(0.0 + 0.0j, d, t, s),
        profile_id=profile_id,
    )


def _calibration() -> OslCalibrationSet:
    return OslCalibrationSet(
        (_profile(CH_S11, PID_S11), _profile(CH_S22, PID_S22, seed_shift=0.25))
    )


def _mean_matrix(traces_note: int = 0) -> np.ndarray:
    """Deterministic channel x frequency background mean (fixed seed)."""
    rng = np.random.default_rng(31)
    return rng.standard_normal((2, FREQUENCY_HZ.size)) + 1j * rng.standard_normal(
        (2, FREQUENCY_HZ.size)
    )


def _reference(
    *,
    domain: ReferenceDomain = ReferenceDomain.RAW,
    channels: tuple[ChannelSpec, ...] = (CH_S11, CH_S22),
    frequency_hz: np.ndarray | None = None,
    mean_data: np.ndarray | None = None,
    calibration_profile_id: CalibrationProfileId | None = None,
    trace_count: int = 12,
) -> AirBackgroundReference:
    return AirBackgroundReference(
        channels=channels,
        frequency_hz=(
            FREQUENCY_HZ.copy() if frequency_hz is None else frequency_hz
        ),
        mean_data=_mean_matrix() if mean_data is None else mean_data,
        trace_count=trace_count,
        domain=domain,
        calibration_profile_id=calibration_profile_id,
    )


def _raw_sweep(rng: np.random.Generator) -> FrequencySweep:
    data = rng.standard_normal((2, FREQUENCY_HZ.size)) + 1j * rng.standard_normal(
        (2, FREQUENCY_HZ.size)
    )
    return FrequencySweep(
        channels=(CH_S11, CH_S22), frequencies_hz=FREQUENCY_HZ, data=data
    )


def _raw_scan(rng: np.random.Generator, traces: int = 3) -> FrequencyScan:
    data = rng.standard_normal(
        (traces, 2, FREQUENCY_HZ.size)
    ) + 1j * rng.standard_normal((traces, 2, FREQUENCY_HZ.size))
    return FrequencyScan(
        channels=(CH_S11, CH_S22), frequencies_hz=FREQUENCY_HZ, data=data
    )


def _calibrated_pair() -> tuple[FrequencySweep, ProcessingHistory]:
    """A real raw sweep pushed through the ISSUE-032 OSL stage."""
    sweep = _raw_sweep(np.random.default_rng(41))
    result = OslCalibrationStage(_calibration()).apply(
        sweep, history=ProcessingHistory(), executed_utc=STAMP_UTC
    )
    assert isinstance(result.source, FrequencySweep)
    return result.source, result.history


def _raw_stage() -> AirBackgroundSubtractionStage:
    return AirBackgroundSubtractionStage(
        _reference(domain=ReferenceDomain.RAW), BGID_RAW
    )


def _calibrated_stage(
    *,
    profile_id: CalibrationProfileId | None = PID_S11,
    calibration: OslCalibrationSet | None = None,
    reference_channels: tuple[ChannelSpec, ...] | None = None,
) -> AirBackgroundSubtractionStage:
    reference = _reference(
        domain=ReferenceDomain.OSL_CALIBRATED,
        channels=reference_channels or (CH_S11, CH_S22),
        calibration_profile_id=profile_id,
    )
    return AirBackgroundSubtractionStage(
        reference, BGID_CAL, current_calibration=calibration or _calibration()
    )


# ---------------------------------------------------------------------------
# 1. Protocol conformance and constructor discipline.
# ---------------------------------------------------------------------------


def test_stage_satisfies_frozen_protocol() -> None:
    stage = _raw_stage()
    assert isinstance(stage, ProcessingStage)
    assert stage.stage_name == AIR_BACKGROUND_STAGE_NAME == "air_background_subtraction"
    assert stage.stage_version == AIR_BACKGROUND_STAGE_VERSION
    assert stage.input_domain == frozenset(
        {DataDomain.FREQUENCY_RAW, DataDomain.FREQUENCY_CALIBRATED}
    )
    assert stage.output_domain is DataDomain.FREQUENCY_BACKGROUND_APPLIED


@pytest.mark.parametrize(
    ("reference", "reference_id"),
    [
        (None, BGID_RAW),
        ("not-a-reference", BGID_RAW),
        (_reference(), None),
        (_reference(), "not-an-id"),
        (_reference(), PID_S11),  # a CalibrationProfileId must never pass
    ],
)
def test_constructor_rejects_bad_arguments(reference, reference_id) -> None:
    with pytest.raises(TypeError):
        AirBackgroundSubtractionStage(reference, reference_id)


def test_apply_returns_background_applied_stage_result() -> None:
    stage = _raw_stage()
    sweep = _raw_sweep(np.random.default_rng(7))
    result = stage.apply(sweep, history=ProcessingHistory())
    assert result.domain is DataDomain.FREQUENCY_BACKGROUND_APPLIED
    assert isinstance(result.source, FrequencySweep)
    assert len(result.history) == 1
    assert result.history.records[0].output_domain is (
        DataDomain.FREQUENCY_BACKGROUND_APPLIED
    )


# ---------------------------------------------------------------------------
# 2. Data-domain protection: raw/calibrated mismatch rejected both ways.
# ---------------------------------------------------------------------------


def test_raw_reference_on_calibrated_data_rejected() -> None:
    _, history = _calibrated_pair()
    stage = _raw_stage()  # reference domain = RAW
    sweep = _raw_sweep(np.random.default_rng(13))
    with pytest.raises(DomainError) as excinfo:
        stage.apply(sweep, history=history)
    assert excinfo.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH
    context = excinfo.value.context
    assert context.get("input_domain") == DataDomain.FREQUENCY_CALIBRATED.value
    assert context.get("reference_domain") == ReferenceDomain.RAW.value


def test_calibrated_reference_on_raw_data_rejected() -> None:
    stage = _calibrated_stage()  # reference domain = OSL_CALIBRATED
    sweep = _raw_sweep(np.random.default_rng(17))
    with pytest.raises(DomainError) as excinfo:
        stage.apply(sweep, history=ProcessingHistory())
    assert excinfo.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH
    assert excinfo.value.context.get("input_domain") == DataDomain.FREQUENCY_RAW.value


def test_osl_reference_without_live_calibration_construction_rejected() -> None:
    """Fail-closed construction: ID-only matching can never be the fallback."""
    reference = _reference(
        domain=ReferenceDomain.OSL_CALIBRATED, calibration_profile_id=PID_S11
    )
    with pytest.raises(DomainError) as excinfo:
        AirBackgroundSubtractionStage(reference, BGID_CAL)  # no current_calibration
    assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT
    assert excinfo.value.context.get("kind") == "missing_current_calibration"


def test_background_applied_predecessor_rejected() -> None:
    stage = _raw_stage()
    sweep = _raw_sweep(np.random.default_rng(19))
    first = stage.apply(sweep, history=ProcessingHistory())
    assert isinstance(first.source, FrequencySweep)
    with pytest.raises(DomainError) as excinfo:
        stage.apply(first.source, history=first.history)
    assert excinfo.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH
    assert excinfo.value.context.get("input_domain") == (
        DataDomain.FREQUENCY_BACKGROUND_APPLIED.value
    )


def test_illegal_reference_domain_value_rejected() -> None:
    broken = replace(_reference(), domain="raw")  # bypasses 029 typed session
    with pytest.raises(DomainError) as excinfo:
        AirBackgroundSubtractionStage(broken, BGID_RAW)
    assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT


# ---------------------------------------------------------------------------
# 3. Multi-channel order and binding.
# ---------------------------------------------------------------------------


def test_swapped_reference_channel_order_rejected() -> None:
    reference = _reference(channels=(CH_S22, CH_S11))
    stage = AirBackgroundSubtractionStage(reference, BGID_OTHER)
    sweep = _raw_sweep(np.random.default_rng(23))
    with pytest.raises(DomainError) as excinfo:
        stage.apply(sweep, history=ProcessingHistory())
    assert excinfo.value.code is ErrorCode.CHANNEL_CONTRACT_MISMATCH
    assert excinfo.value.context.get("left_channel_ids") == ["ch_s11", "ch_s22"]
    assert excinfo.value.context.get("right_channel_ids") == ["ch_s22", "ch_s11"]


def test_missing_and_extra_channel_rejected() -> None:
    sweep = _raw_sweep(np.random.default_rng(29))
    for channels in ((CH_S11,), (CH_S11, CH_S22, CH_S11)):
        reference = _reference(channels=channels)
        stage = AirBackgroundSubtractionStage(reference, BGID_OTHER)
        with pytest.raises(DomainError) as excinfo:
            stage.apply(sweep, history=ProcessingHistory())
        assert excinfo.value.code is ErrorCode.CHANNEL_CONTRACT_MISMATCH


def test_dual_channel_rows_subtract_independently() -> None:
    """Row i of mean_data must bind to channel i (cross-binding would change bits)."""
    mean = _mean_matrix()
    reference = _reference(mean_data=mean)
    stage = AirBackgroundSubtractionStage(reference, BGID_RAW)
    sweep = _raw_sweep(np.random.default_rng(31))
    result = stage.apply(sweep, history=ProcessingHistory())
    expected = sweep.data - mean
    crossed = sweep.data - mean[::-1]
    assert np.array_equal(result.source.data, expected)
    assert not np.array_equal(result.source.data, crossed)


# ---------------------------------------------------------------------------
# 4. Calibrated-domain profile provenance: ID + digest strict match.
# ---------------------------------------------------------------------------


def test_calibrated_matching_profile_passes() -> None:
    sweep, history = _calibrated_pair()
    stage = _calibrated_stage()  # ref profile id = PID_S11 = set first profile
    result = stage.apply(sweep, history=history)
    assert result.domain is DataDomain.FREQUENCY_BACKGROUND_APPLIED
    record = result.history.records[-1]
    assert record.input_domain is DataDomain.FREQUENCY_CALIBRATED
    assert record.background_reference_id == BGID_CAL
    assert record.calibration_profile_id == PID_S11


def test_calibrated_different_profile_id_rejected() -> None:
    sweep, history = _calibrated_pair()
    stage = _calibrated_stage(profile_id=PID_OTHER)  # live set binds PID_S11/S22
    with pytest.raises(DomainError) as excinfo:
        stage.apply(sweep, history=history)
    assert excinfo.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH
    assert excinfo.value.context.get("kind") == "calibration_provenance_mismatch"
    diffs = excinfo.value.context.get("mismatches")
    assert isinstance(diffs, list) and any("profile_id" in str(item) for item in diffs)


def test_calibrated_reference_without_profile_id_rejected() -> None:
    sweep, history = _calibrated_pair()
    reference = _reference(
        domain=ReferenceDomain.OSL_CALIBRATED, calibration_profile_id=None
    )
    stage = AirBackgroundSubtractionStage(
        reference, BGID_CAL, current_calibration=_calibration()
    )
    with pytest.raises(DomainError) as excinfo:
        stage.apply(sweep, history=history)
    assert excinfo.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH
    assert excinfo.value.context.get("kind") == "calibration_provenance_mismatch"


def test_calibrated_legacy_record_without_digest_rejected() -> None:
    """Strictness: a calibrated record lacking content digests cannot pass."""
    sweep, _ = _calibrated_pair()
    legacy_record = ProcessingRecord(
        stage_name="legacy_osl",
        stage_version="0.9",
        parameters={},  # no profiles digest entries at all
        input_domain=DataDomain.FREQUENCY_RAW,
        output_domain=DataDomain.FREQUENCY_CALIBRATED,
        executed_utc=STAMP_UTC,
        software_version="0.0.0",
        calibration_profile_id=PID_S11,
    )
    reference = _reference(
        domain=ReferenceDomain.OSL_CALIBRATED, calibration_profile_id=PID_S11
    )
    verdict = check_safe_reuse(ProcessingHistory((legacy_record,)), reference)
    assert verdict.compatible is False
    assert any("digest" in item or "profiles" in item for item in verdict.mismatches)
    stage = _calibrated_stage()  # live set supplied; ID matches the legacy record
    with pytest.raises(DomainError) as excinfo:
        stage.apply(sweep, history=ProcessingHistory((legacy_record,)))
    assert excinfo.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH
    diffs = excinfo.value.context.get("mismatches")
    assert isinstance(diffs, list) and any("digest" in str(item) for item in diffs)


def test_same_id_different_profile_content_rejected() -> None:
    """Tampering guard: re-solved profile under the same UUID flips the digest."""
    original = _calibration()
    tampered_profile = _profile(CH_S11, PID_S11, seed_shift=0.4)
    tampered = OslCalibrationSet((tampered_profile, original.profiles[1]))
    # The live-set digest differs although every profile_id stayed identical.
    assert osl_profile_digest(original.profiles[0]) != osl_profile_digest(
        tampered_profile
    )
    reference = _reference(
        domain=ReferenceDomain.OSL_CALIBRATED, calibration_profile_id=PID_S11
    )
    sweep = _raw_sweep(np.random.default_rng(37))
    # History produced by the TAMPERED solve; judge it against the ORIGINAL
    # live set: IDs match position-by-position but content digests differ.
    history_tampered = OslCalibrationStage(tampered).apply(
        sweep, history=ProcessingHistory(), executed_utc=STAMP_UTC
    ).history
    verdict_mix = check_safe_reuse(
        history_tampered, reference, current_calibration=original
    )
    assert verdict_mix.compatible is False
    assert any("content_sha256" in item for item in verdict_mix.mismatches)
    # And the stage itself refuses to run on that data with that authority.
    stage = AirBackgroundSubtractionStage(
        reference, BGID_CAL, current_calibration=original
    )
    tampered_calibrated = OslCalibrationStage(tampered).apply(
        sweep, history=ProcessingHistory(), executed_utc=STAMP_UTC
    ).source
    assert isinstance(tampered_calibrated, FrequencySweep)
    with pytest.raises(DomainError) as excinfo:
        stage.apply(tampered_calibrated, history=history_tampered)
    assert excinfo.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH
    assert excinfo.value.context.get("kind") == "calibration_provenance_mismatch"
    # Control: the SAME data judged against its true producing set passes.
    verdict_ok = check_safe_reuse(
        history_tampered, reference, current_calibration=tampered
    )
    assert verdict_ok.compatible is True


def test_require_matching_calibration_provenance_pure_raw_passes() -> None:
    _, history = _calibrated_pair()
    reference = _reference(
        domain=ReferenceDomain.OSL_CALIBRATED, calibration_profile_id=PID_S11
    )
    require_matching_calibration_provenance(
        history, reference, current_calibration=_calibration()
    )  # must not raise
    with pytest.raises(DomainError):
        require_matching_calibration_provenance(
            ProcessingHistory(),  # empty history ends in raw
            reference,
            current_calibration=_calibration(),
        )


# ---------------------------------------------------------------------------
# 5. Axis / shape / dtype / finiteness guards.
# ---------------------------------------------------------------------------


def test_axis_mismatch_rejected() -> None:
    shifted = FREQUENCY_HZ + 1.0
    reference = _reference(frequency_hz=shifted)
    stage = AirBackgroundSubtractionStage(reference, BGID_OTHER)
    sweep = _raw_sweep(np.random.default_rng(43))
    with pytest.raises(DomainError) as excinfo:
        stage.apply(sweep, history=ProcessingHistory())
    assert excinfo.value.code is ErrorCode.AXIS_MISMATCH


def test_mean_data_shape_rejected() -> None:
    """Channel-count-dependent shape errors surface at apply time."""
    mean = _mean_matrix()
    bad_shapes = [mean.T, mean[:, :-1], mean[[0]]]
    sweep = _raw_sweep(np.random.default_rng(47))
    for bad in bad_shapes:
        reference = _reference(mean_data=bad)
        stage = AirBackgroundSubtractionStage(reference, BGID_OTHER)
        with pytest.raises(DomainError) as excinfo:
            stage.apply(sweep, history=ProcessingHistory())
        assert excinfo.value.code is ErrorCode.SHAPE_MISMATCH


def test_mean_data_float_dtype_rejected_at_construction() -> None:
    reference = _reference(mean_data=_mean_matrix().real.astype(np.float64))
    with pytest.raises(DomainError) as excinfo:
        AirBackgroundSubtractionStage(reference, BGID_OTHER)
    assert excinfo.value.code is ErrorCode.DTYPE_MISMATCH


def test_non_finite_mean_data_rejected_at_construction() -> None:
    mean = _mean_matrix().copy()
    mean[1, 5] = np.nan + 0j
    reference = _reference(mean_data=mean)
    with pytest.raises(DomainError) as excinfo:
        AirBackgroundSubtractionStage(reference, BGID_OTHER)
    assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT
    assert excinfo.value.context.get("flat_index") == int(
        np.argmax(~np.isfinite(mean.reshape(-1)))
    )


def test_non_writeable_input_arrays_accepted_unchanged() -> None:
    stage = _raw_stage()
    sweep = _raw_sweep(np.random.default_rng(61))
    before = sweep.data.tobytes()
    assert not sweep.data.flags.writeable
    stage.apply(sweep, history=ProcessingHistory())
    assert sweep.data.tobytes() == before


# ---------------------------------------------------------------------------
# 6. Duplicate application refused twice over; core backstop.
# ---------------------------------------------------------------------------


def test_duplicate_stage_name_rejected_on_real_chain() -> None:
    stage = _raw_stage()
    sweep = _raw_sweep(np.random.default_rng(67))
    first = stage.apply(sweep, history=ProcessingHistory())
    assert isinstance(first.source, FrequencySweep)
    second_stage = AirBackgroundSubtractionStage(_reference(), BGID_OTHER)
    with pytest.raises(DomainError) as excinfo:
        second_stage.apply(first.source, history=first.history)
    # Gate 1 fires first (predecessor domain), never reaching core uniqueness.
    assert excinfo.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH


def test_core_uniqueness_probe_bumped_version_still_rejected() -> None:
    """Isolated probe: with legality forced, ONLY the uniqueness rule can refuse."""
    stage = _raw_stage()
    sweep = _raw_sweep(np.random.default_rng(71))
    record = stage.apply(sweep, history=ProcessingHistory()).history.records[0]
    # Records are validated at construction, so the transition table must be
    # patched BEFORE building the duplicate hop (restored in finally).
    original_allowed = core_time_domain._ALLOWED_TRANSITIONS
    patched = dict(original_allowed)
    patched[DataDomain.FREQUENCY_BACKGROUND_APPLIED] = patched[
        DataDomain.FREQUENCY_BACKGROUND_APPLIED
    ] | {DataDomain.FREQUENCY_BACKGROUND_APPLIED}
    core_time_domain._ALLOWED_TRANSITIONS = patched
    try:
        chained = ProcessingRecord(
            stage_name=record.stage_name,
            stage_version="9.9",  # bumped version must NOT bypass the guard
            parameters=dict(record.parameters),
            input_domain=DataDomain.FREQUENCY_BACKGROUND_APPLIED,
            output_domain=DataDomain.FREQUENCY_BACKGROUND_APPLIED,
            executed_utc=record.executed_utc,
            software_version=record.software_version,
            background_reference_id=record.background_reference_id,  # same producer id
        )
        with pytest.raises(DomainError) as excinfo:
            ProcessingHistory((record, chained))
    finally:
        core_time_domain._ALLOWED_TRANSITIONS = original_allowed
    assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT
    assert excinfo.value.context["stage_name"] == AIR_BACKGROUND_STAGE_NAME


def test_record_always_carries_background_reference_id() -> None:
    stage = _raw_stage()
    sweep = _raw_sweep(np.random.default_rng(73))
    history = stage.apply(sweep, history=ProcessingHistory()).history
    assert history.records[0].background_reference_id == BGID_RAW


# ---------------------------------------------------------------------------
# 7. Numeric / history parity, provenance structure, digests, timestamps.
# ---------------------------------------------------------------------------


def test_golden_small_vector_subtraction() -> None:
    axis = np.array([1.0e8, 2.0e8, 3.0e8], dtype=np.float64)
    channel = ChannelSpec("ch_a", LogicalPolarization.HH, SParameter.S11, "A")
    mean = np.array([[1.0 - 2.0j, 0.5 + 0.25j, -3.0 + 1.0j]], dtype=np.complex128)
    reference = AirBackgroundReference(
        channels=(channel,),
        frequency_hz=axis,
        mean_data=mean,
        trace_count=3,
        domain=ReferenceDomain.RAW,
        calibration_profile_id=None,
    )
    data = np.array([[4.0 + 5.0j, -1.0 + 0.0j, 2.5 - 6.5j]], dtype=np.complex128)
    sweep = FrequencySweep(channels=(channel,), frequencies_hz=axis, data=data)
    stage = AirBackgroundSubtractionStage(reference, BGID_RAW)
    result = stage.apply(sweep, history=ProcessingHistory(), executed_utc=STAMP_UTC)
    expected = np.array(
        [[3.0 + 7.0j, -1.5 - 0.25j, 5.5 - 7.5j]], dtype=np.complex128
    )
    assert np.array_equal(result.source.data, expected)


def test_scan_broadcast_matches_per_trace_sweeps() -> None:
    stage = _raw_stage()
    scan = _raw_scan(np.random.default_rng(79), traces=4)
    result = stage.apply(scan, history=ProcessingHistory())
    assert isinstance(result.source, FrequencyScan)
    per_trace = []
    for index in range(scan.data.shape[0]):
        sweep = FrequencySweep(
            channels=scan.channels,
            frequencies_hz=scan.frequencies_hz,
            data=scan.data[index],
        )
        single = stage.apply(sweep, history=ProcessingHistory())
        assert isinstance(single.source, FrequencySweep)
        per_trace.append(single.source.data)
    assert np.array_equal(result.source.data, np.stack(per_trace, axis=0))
    # No trace-axis statistics: every trace used the SAME reference rows.
    expected = scan.data - _mean_matrix()
    assert np.array_equal(result.source.data, expected)


def test_container_type_channels_metadata_preserved() -> None:
    stage = _raw_stage()
    scan = _raw_scan(np.random.default_rng(83), traces=2)
    result = stage.apply(scan, history=ProcessingHistory())
    assert isinstance(result.source, FrequencyScan)
    assert result.source.channels == scan.channels
    assert np.array_equal(result.source.frequencies_hz, FREQUENCY_HZ)
    assert result.source.metadata == scan.metadata
    assert result.source is not scan


def test_history_record_fields_and_round_trip() -> None:
    stage = _raw_stage()
    sweep = _raw_sweep(np.random.default_rng(89))
    history = stage.apply(sweep, history=ProcessingHistory()).history
    record = history.records[0]
    assert record.stage_name == AIR_BACKGROUND_STAGE_NAME
    assert record.stage_version == AIR_BACKGROUND_STAGE_VERSION
    assert record.input_domain is DataDomain.FREQUENCY_RAW
    assert record.output_domain is DataDomain.FREQUENCY_BACKGROUND_APPLIED
    assert record.calibration_profile_id is None
    assert record.software_version == "0.1.0.dev0"
    parameters = record.parameters
    assert parameters["algorithm"] == "air_background_complex_subtract_v1"
    reference_node = parameters["reference"]
    assert isinstance(reference_node, dict)
    assert reference_node["reference_id"] == BGID_RAW.to_json()
    assert reference_node["domain"] == ReferenceDomain.RAW.value
    assert reference_node["trace_count"] == 12
    channels = reference_node["channels"]
    assert isinstance(channels, list)
    assert [entry["channel_id"] for entry in channels if isinstance(entry, dict)] == [
        "ch_s11",
        "ch_s22",
    ]
    restored = ProcessingRecord.from_dict(record.to_dict())
    assert restored.to_dict() == record.to_dict()
    assert restored.background_reference_id == BGID_RAW


def test_calibrated_record_inherits_calibration_profile_id() -> None:
    sweep, history = _calibrated_pair()
    stage = _calibrated_stage()
    result = stage.apply(sweep, history=history)
    record = result.history.records[-1]
    assert record.calibration_profile_id == PID_S11
    parameters = record.parameters
    reference_node = parameters["reference"]
    assert isinstance(reference_node, dict)
    assert reference_node["calibration_profile_id"] == PID_S11.to_json()
    content = reference_node.get("calibration_profile_content_sha256")
    assert isinstance(content, str) and len(content) == 64
    assert content == osl_profile_digest(_calibration().profiles[0])


def test_digest_is_canonical_and_content_sensitive() -> None:
    reference = _reference()
    digest = background_reference_digest(reference)
    assert len(digest) == 64
    assert digest == background_reference_digest(reference)  # deterministic
    changed_mean = _mean_matrix().copy()
    changed_mean[0, 0] += 1e-12
    flipped = background_reference_digest(_reference(mean_data=changed_mean))
    assert flipped != digest
    swapped = background_reference_digest(
        _reference(channels=(CH_S22, CH_S11))
    )
    assert swapped != digest
    # Independent canonical recomputation pins the payload contract.
    payload: JsonValue = {
        "format": "uav_gpr_rcbg_payload_v1",
        "domain": "raw",
        "calibration_profile_id": None,
        "axis_unit": "Hz",
        "channels": [
            {
                "channel_id": "ch_s11",
                "logical_polarization": "hh",
                "s_parameter": "s11",
                "display_name": "S11 antenna",
                "antenna_note": None,
            },
            {
                "channel_id": "ch_s22",
                "logical_polarization": "vv",
                "s_parameter": "s22",
                "display_name": "S22 antenna",
                "antenna_note": None,
            },
        ],
        "frequency_hz": {
            "dtype": "float64",
            "shape": [41],
            "re": [float(v) for v in FREQUENCY_HZ],
        },
        "mean_data": {
            "dtype": "complex128",
            "shape": [2, 41],
            "re": [float(v) for v in _mean_matrix().reshape(-1).real],
            "im": [float(v) for v in _mean_matrix().reshape(-1).imag],
        },
        "trace_count": 12,
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    assert digest == hashlib.sha256(canonical).hexdigest()


def test_executed_utc_wins_over_clock() -> None:
    stage = _raw_stage()
    sweep = _raw_sweep(np.random.default_rng(91))
    result = stage.apply(sweep, history=ProcessingHistory(), executed_utc=STAMP_UTC)
    assert result.history.records[0].executed_utc == STAMP_UTC


def test_naive_executed_utc_rejected_before_any_work() -> None:
    stage = _raw_stage()
    sweep = _raw_sweep(np.random.default_rng(93))
    with pytest.raises(DomainError) as excinfo:
        stage.apply(
            sweep,
            history=ProcessingHistory(),
            executed_utc=datetime(2026, 9, 5, 12, 0, 0),
        )
    assert excinfo.value.code is ErrorCode.NAIVE_DATETIME


def test_type_errors_for_bad_inputs() -> None:
    stage = _raw_stage()
    with pytest.raises(TypeError):
        stage.apply("not-a-model", history=ProcessingHistory())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        stage.apply(_raw_sweep(np.random.default_rng(95)), history=[])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 8. Input immutability, output protection, Flat distinction, exclusions.
# ---------------------------------------------------------------------------


def test_inputs_never_modified_outputs_write_protected() -> None:
    stage = _raw_stage()
    reference = stage.reference
    sweep_bytes = _raw_sweep(np.random.default_rng(97)).data.tobytes()
    sweep = _raw_sweep(np.random.default_rng(97))
    sweep_bytes = sweep.data.tobytes()
    mean_bytes = reference.mean_data.tobytes()
    axis_bytes = reference.frequency_hz.tobytes()
    result = stage.apply(sweep, history=ProcessingHistory())
    assert sweep.data.tobytes() == sweep_bytes
    assert reference.mean_data.tobytes() == mean_bytes
    assert reference.frequency_hz.tobytes() == axis_bytes
    assert not result.source.data.flags.writeable
    with pytest.raises(ValueError):
        result.source.data[0, 0] = 0.0
    assert bool(result.history.records[0].parameters)  # provenance is complete


def test_single_trace_and_multi_trace_share_one_reference_semantics() -> None:
    """Flat distinction: every trace of a scan uses the SAME reference rows."""
    stage = _raw_stage()
    rng = np.random.default_rng(99)
    data = rng.standard_normal((5, 2, FREQUENCY_HZ.size)) + 1j * rng.standard_normal(
        (5, 2, FREQUENCY_HZ.size)
    )
    multi = FrequencyScan(
        channels=(CH_S11, CH_S22), frequencies_hz=FREQUENCY_HZ, data=data
    )
    single = FrequencySweep(
        channels=(CH_S11, CH_S22), frequencies_hz=FREQUENCY_HZ, data=data[0]
    )
    single_result = stage.apply(single, history=ProcessingHistory())
    multi_result = stage.apply(multi, history=ProcessingHistory())
    assert isinstance(single_result.source, FrequencySweep)
    assert isinstance(multi_result.source, FrequencyScan)
    # The trace-0 sweep result is bit-identical to the scan's trace-0 row:
    # broadcasting never consults other traces (no trace-axis statistic).
    assert np.array_equal(multi_result.source.data[0], single_result.source.data)
    # A constant signal keeps its DC level minus the reference row (a
    # trace-axis moving average would instead collapse toward zero).
    flat = np.full((4, 2, FREQUENCY_HZ.size), 3.0 + 4.0j, dtype=np.complex128)
    scan_flat = FrequencyScan(
        channels=(CH_S11, CH_S22), frequencies_hz=FREQUENCY_HZ, data=flat
    )
    reduced = stage.apply(scan_flat, history=ProcessingHistory())
    assert isinstance(reduced.source, FrequencyScan)
    assert np.allclose(reduced.source.data[0], reduced.source.data[3])
    residual_dc = reduced.source.data.mean(axis=0)
    assert np.max(np.abs(residual_dc)) > 1e-6  # NOT averaged away along traces


def test_downstream_bandpass_chain_accepts_background_output() -> None:
    stage = _raw_stage()
    sweep = _raw_sweep(np.random.default_rng(101))
    bg = stage.apply(sweep, history=ProcessingHistory())
    assert isinstance(bg.source, FrequencySweep)
    filtered = BandpassStage().apply(bg.source, history=bg.history)
    assert filtered.domain is DataDomain.FREQUENCY_FILTERED
    assert len(filtered.history) == 2


def test_module_source_contains_no_excluded_symbols() -> None:
    import ast
    import inspect

    import uav_gpr.processing.background_subtraction as module

    source = inspect.getsource(module)
    tree = ast.parse(source)
    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.append(node.module)
        elif isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
    # Hard exclusion 1: no storage (no .rcbg read/write) - AGENTS.md §9.
    assert not [m for m in imported_modules if m.startswith("uav_gpr.storage")]
    # Hard exclusion 2: no acquisition (no reference capture here).
    assert not [m for m in imported_modules if m.startswith("uav_gpr.acquisition")]
    # Hard exclusion 3: no UI / hardware / plotting.
    assert not [
        m
        for m in imported_modules
        if m.split(".")[0]
        in ("PySide6", "PySide2", "matplotlib", "serial", "usb")
    ]
    # Hard exclusion 4: no FFT (IFFT belongs to ISSUE-031).
    assert "np.fft" not in source and "numpy.fft" not in source
    # Semantic vocabulary guard over executable names only: no trace-axis
    # statistics machinery (that is Flat Reflection, ISSUE-035) and no
    # session/orchestration symbols from ISSUE-028/029.
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id.lower())
        elif isinstance(node, ast.Attribute):
            names.add(node.attr.lower())
    for banned in (
        "moving_average",
        "sliding",
        "airbackgroundsession",
        "controllerreferenceadapter",
        "accept_sweep",
        "depth",
        "ui",
    ):
        assert banned not in names
