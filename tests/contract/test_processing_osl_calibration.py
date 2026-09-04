"""Contract tests for the ISSUE-032 OSL calibration processing stage.

Pure deterministic tests: synthetic forward error models only (same algebra
as the frozen ISSUE-027 contract tests) - no hardware, no threads, no sleeps,
no file IO.  Randomness is confined to fixed-seed ``default_rng`` scenarios.

Contract summary (docs/issues/M06_CALIBRATION_PROCESSING.md ISSUE-032,
docs/CALIBRATION.md section 5, docs/PROCESSING.md sections 1-2, t1 baseline
report section 3, docs/plans/2026-09-05-issue-032-osl-stage.md D1-D9):

- ``OslCalibrationStage`` turns ``frequency_raw`` into a brand-new
  ``frequency_calibrated`` model (OSL-after / background-before semantics);
- per-channel validation: exact ordered channel binding, shared frequency
  axis equality, S-parameter reflection check - all fail closed with
  structured DomainError contexts;
- history/provenance: one appended record carrying ordered per-channel
  ``{channel_id, s_parameter, profile_id, content_sha256}`` digests plus a
  set-level ``set_content_sha256`` over the ordered binding;
- double calibration is rejected twice over: the stage's raw-only input gate
  and the core per-history stage-name uniqueness (a bumped version does not
  bypass it);
- safe reuse accepts only strictly identical provenance (ID + digest);
- raw arrays are never mutated; outputs are write-protected copies.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC

import numpy as np
import pytest

from uav_gpr.calibration.osl import OslCalibrationSet, build_osl_calibration
from uav_gpr.core.channels import ChannelSpec
from uav_gpr.core.enums import DataDomain, LogicalPolarization, SParameter
from uav_gpr.core.errors import DomainError, ErrorCode
from uav_gpr.core.frequency import FrequencyScan, FrequencySweep
from uav_gpr.core.identifiers import CalibrationProfileId
from uav_gpr.core.time_domain import ProcessingHistory, ProcessingRecord
from uav_gpr.processing.bandpass import BandpassStage, ProcessingStage
from uav_gpr.processing.osl_calibration import (
    OSL_CALIBRATION_STAGE_NAME,
    OSL_CALIBRATION_STAGE_VERSION,
    OslCalibrationStage,
    check_safe_reuse,
    osl_profile_digest,
    osl_provenance_of,
    osl_set_digest,
    require_safe_reuse,
)

# ---------------------------------------------------------------------------
# Shared synthetic scenario (mirrors the frozen ISSUE-027 test vectors).
# ---------------------------------------------------------------------------

FREQUENCY_HZ = np.linspace(0.5e9, 2.5e9, 41)
_NORM = (FREQUENCY_HZ - FREQUENCY_HZ[0]) / (FREQUENCY_HZ[-1] - FREQUENCY_HZ[0])

CH_S11 = ChannelSpec("ch_s11", LogicalPolarization.HH, SParameter.S11, "S11 antenna")
CH_S22 = ChannelSpec("ch_s22", LogicalPolarization.VV, SParameter.S22, "S22 antenna")
CH_S11_VV = ChannelSpec("ch_s11", LogicalPolarization.VV, SParameter.S11, "S11 vv")

PID_S11 = CalibrationProfileId("11111111-1111-4111-8111-111111111111")
PID_S22 = CalibrationProfileId("22222222-2222-4222-8222-222222222222")
PID_OTHER = CalibrationProfileId("33333333-3333-4333-8333-333333333333")


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


@pytest.fixture()
def stage() -> OslCalibrationStage:
    return OslCalibrationStage(_calibration())


# ---------------------------------------------------------------------------
# 1. Protocol conformance (acceptance 1).
# ---------------------------------------------------------------------------


def test_stage_satisfies_frozen_protocol(stage: OslCalibrationStage) -> None:
    assert isinstance(stage, ProcessingStage)
    assert stage.stage_name == OSL_CALIBRATION_STAGE_NAME == "osl_calibration"
    assert stage.stage_version == OSL_CALIBRATION_STAGE_VERSION
    assert stage.input_domain == frozenset({DataDomain.FREQUENCY_RAW})
    assert stage.output_domain is DataDomain.FREQUENCY_CALIBRATED


def test_apply_returns_calibrated_stage_result(stage: OslCalibrationStage) -> None:
    sweep = _raw_sweep(np.random.default_rng(7))
    result = stage.apply(sweep, history=ProcessingHistory())
    assert result.domain is DataDomain.FREQUENCY_CALIBRATED
    assert isinstance(result.source, FrequencySweep)
    assert len(result.history) == 1


# ---------------------------------------------------------------------------
# 2. Multi-channel application correctness (acceptance 2, numeric parity).
# ---------------------------------------------------------------------------


def test_dual_channel_matches_set_apply_sweep(
    stage: OslCalibrationStage,
) -> None:
    calibration = _calibration()
    sweep = _raw_sweep(np.random.default_rng(11))
    expected = calibration.apply(sweep.data, sweep.channels)
    result = stage.apply(sweep, history=ProcessingHistory())
    assert np.array_equal(result.source.data, expected)
    assert result.source.channels == sweep.channels
    assert np.array_equal(result.source.frequencies_hz, FREQUENCY_HZ)


def test_scan_matches_per_trace_apply_vectorized(
    stage: OslCalibrationStage,
) -> None:
    calibration = _calibration()
    scan = _raw_scan(np.random.default_rng(13))
    rows = [
        calibration.apply(scan.data[index], scan.channels)
        for index in range(scan.data.shape[0])
    ]
    expected = np.stack(rows, axis=0)
    result = stage.apply(scan, history=ProcessingHistory())
    assert isinstance(result.source, FrequencyScan)
    assert result.source.data.shape == scan.data.shape
    assert np.array_equal(result.source.data, expected)


def test_channels_and_metadata_preserved(stage: OslCalibrationStage) -> None:
    scan = _raw_scan(np.random.default_rng(17))
    result = stage.apply(scan, history=ProcessingHistory())
    assert result.source.channels == scan.channels
    assert result.source.metadata == scan.metadata


def test_ideal_osl_recovers_known_gamma() -> None:
    """Ideal solve then stage application recovers the true Gamma vector."""
    d, t, s = _error_terms()
    gamma = 0.5 * np.exp(1j * np.pi * _NORM) * (1.0 - 0.1 * _NORM)
    profile = _profile(CH_S11, PID_S11)
    calibration = OslCalibrationSet((profile,))
    stage = OslCalibrationStage(calibration)
    measured = _forward(gamma, d, t, s)
    sweep = FrequencySweep(
        channels=(CH_S11,), frequencies_hz=FREQUENCY_HZ, data=measured[np.newaxis, :]
    )
    result = stage.apply(sweep, history=ProcessingHistory())
    assert np.allclose(result.source.data[0], gamma, rtol=1e-9, atol=1e-9)


# ---------------------------------------------------------------------------
# 3. Rejection matrix (acceptance 2: wrong profile/axis/channel fail closed).
# ---------------------------------------------------------------------------


def test_wrong_channel_order_rejected() -> None:
    swapped = OslCalibrationSet(
        (
            _profile(CH_S22, PID_S22, seed_shift=0.25),
            _profile(CH_S11, PID_S11),
        )
    )
    stage = OslCalibrationStage(swapped)
    sweep = _raw_sweep(np.random.default_rng(19))
    with pytest.raises(DomainError) as excinfo:
        stage.apply(sweep, history=ProcessingHistory())
    assert excinfo.value.code is ErrorCode.CHANNEL_CONTRACT_MISMATCH
    assert "index" in excinfo.value.context


def test_missing_channel_rejected() -> None:
    single = OslCalibrationSet((_profile(CH_S11, PID_S11),))
    stage = OslCalibrationStage(single)
    sweep = _raw_sweep(np.random.default_rng(23))
    with pytest.raises(DomainError) as excinfo:
        stage.apply(sweep, history=ProcessingHistory())
    assert excinfo.value.code is ErrorCode.CHANNEL_CONTRACT_MISMATCH


def test_different_binding_channel_spec_rejected() -> None:
    """Same channel_id but different polarization = different full spec."""
    bound = OslCalibrationSet(
        (
            _profile(CH_S11_VV, PID_S11),
            _profile(CH_S22, PID_S22, seed_shift=0.25),
        )
    )
    stage = OslCalibrationStage(bound)
    sweep = _raw_sweep(np.random.default_rng(29))
    with pytest.raises(DomainError) as excinfo:
        stage.apply(sweep, history=ProcessingHistory())
    assert excinfo.value.code is ErrorCode.CHANNEL_CONTRACT_MISMATCH


def test_wrong_frequency_axis_rejected() -> None:
    shifted = FREQUENCY_HZ + 1.0  # same length, different values
    calibration = OslCalibrationSet(
        (
            _profile(CH_S11, PID_S11, frequency_hz=shifted),
            _profile(CH_S22, PID_S22, seed_shift=0.25, frequency_hz=shifted),
        )
    )
    stage = OslCalibrationStage(calibration)
    sweep = _raw_sweep(np.random.default_rng(31))
    with pytest.raises(DomainError) as excinfo:
        stage.apply(sweep, history=ProcessingHistory())
    assert excinfo.value.code is ErrorCode.AXIS_MISMATCH


def test_non_reflection_channel_never_constructs(stage: OslCalibrationStage) -> None:
    ch_s21 = ChannelSpec("ch_s21", LogicalPolarization.HV, SParameter.S21, "S21")
    with pytest.raises(DomainError) as excinfo:
        _profile(ch_s21, PID_OTHER)
    assert excinfo.value.code is ErrorCode.CHANNEL_CONTRACT_MISMATCH


def test_typed_rejects_foreign_inputs(stage: OslCalibrationStage) -> None:
    with pytest.raises(TypeError):
        stage.apply(object(), history=ProcessingHistory())
    with pytest.raises(TypeError):
        stage.apply(_raw_sweep(np.random.default_rng(37)), history=None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 4. Double calibration rejection (acceptance 3).
# ---------------------------------------------------------------------------


def test_reapply_on_own_output_rejected(stage: OslCalibrationStage) -> None:
    sweep = _raw_sweep(np.random.default_rng(41))
    first = stage.apply(sweep, history=ProcessingHistory())
    with pytest.raises(DomainError) as excinfo:
        stage.apply(first.source, history=first.history)
    assert excinfo.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH
    assert excinfo.value.context["input_domain"] == DataDomain.FREQUENCY_CALIBRATED.value


def test_calibrated_history_then_other_predecessor_rejected() -> None:
    """Bandpass-then-OSL would put OSL after filtering: illegal, rejected."""
    calibration = _calibration()
    sweep = _raw_sweep(np.random.default_rng(43))
    filtered = BandpassStage().apply(sweep, history=ProcessingHistory())
    stage = OslCalibrationStage(calibration)
    with pytest.raises(DomainError) as excinfo:
        stage.apply(filtered.source, history=filtered.history)
    assert excinfo.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH


def test_duplicate_stage_name_guard_is_core_enforced(
    stage: OslCalibrationStage,
) -> None:
    """Core history rejects a repeated stable stage name even at version bump.

    Isolated probe (ISSUE-030/031 precedent): raw->calibrated twice is an
    illegal hop in its own right, so the probe isolates the uniqueness rule
    by temporarily widening only the pairwise transition table (restored in
    finally); with that check off, the two otherwise-valid records must still
    be refused for sharing one stable ``stage_name``.  A bumped
    ``stage_version`` must not bypass that guard.
    """
    base = stage.apply(
        _raw_sweep(np.random.default_rng(47)), history=ProcessingHistory()
    ).history
    record = base.records[0]
    duplicate = ProcessingRecord(
        stage_name=record.stage_name,
        stage_version="9.9",  # bumped version must NOT bypass the guard
        parameters=dict(record.parameters),
        input_domain=DataDomain.FREQUENCY_RAW,
        output_domain=DataDomain.FREQUENCY_CALIBRATED,
        executed_utc=record.executed_utc,
        software_version=record.software_version,
        calibration_profile_id=record.calibration_profile_id,
    )
    from uav_gpr.core import time_domain as core_time_domain

    # Isolated probe: a repeated raw->calibrated hop trips the pairwise chain
    # test first, so temporarily make the two records chain legally
    # (calibrated -> calibrated) while keeping both stage names identical;
    # only the uniqueness rule can then refuse the pair (restored in finally).
    original_allowed = core_time_domain._ALLOWED_TRANSITIONS
    patched = dict(original_allowed)
    patched[DataDomain.FREQUENCY_CALIBRATED] = patched[
        DataDomain.FREQUENCY_CALIBRATED
    ] | {DataDomain.FREQUENCY_CALIBRATED}
    core_time_domain._ALLOWED_TRANSITIONS = patched
    chained = ProcessingRecord(
        stage_name=duplicate.stage_name,
        stage_version=duplicate.stage_version,
        parameters=dict(duplicate.parameters),
        input_domain=DataDomain.FREQUENCY_CALIBRATED,
        output_domain=DataDomain.FREQUENCY_CALIBRATED,
        executed_utc=duplicate.executed_utc,
        software_version=duplicate.software_version,
        calibration_profile_id=duplicate.calibration_profile_id,
    )
    try:
        with pytest.raises(DomainError) as excinfo:
            ProcessingHistory((record, chained))
    finally:
        core_time_domain._ALLOWED_TRANSITIONS = original_allowed
    assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT
    assert excinfo.value.context["stage_name"] == OSL_CALIBRATION_STAGE_NAME
    assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT
    assert excinfo.value.context["stage_name"] == OSL_CALIBRATION_STAGE_NAME


# ---------------------------------------------------------------------------
# 5. History and provenance completeness (acceptance 3).
# ---------------------------------------------------------------------------


def test_record_fields_and_ordered_profile_provenance(
    stage: OslCalibrationStage,
) -> None:
    from datetime import UTC, datetime

    calibration = _calibration()
    sweep = _raw_sweep(np.random.default_rng(53))
    when = datetime(2026, 9, 5, tzinfo=UTC)
    result = stage.apply(sweep, history=ProcessingHistory(), executed_utc=when)
    assert len(result.history) == 1
    record = result.history.records[0]
    assert record.input_domain is DataDomain.FREQUENCY_RAW
    assert record.output_domain is DataDomain.FREQUENCY_CALIBRATED
    assert record.stage_name == OSL_CALIBRATION_STAGE_NAME
    assert record.stage_version == OSL_CALIBRATION_STAGE_VERSION
    assert record.executed_utc == when
    assert record.calibration_profile_id == PID_S11  # first of ordered set
    profiles = record.parameters["profiles"]
    assert isinstance(profiles, list) and len(profiles) == 2
    first, second = profiles
    assert first["channel_id"] == "ch_s11"
    assert first["s_parameter"] == "s11"
    assert first["profile_id"] == str(PID_S11)
    assert second["channel_id"] == "ch_s22"
    assert second["profile_id"] == str(PID_S22)
    assert first["content_sha256"] == osl_profile_digest(calibration.profiles[0])
    assert second["content_sha256"] == osl_profile_digest(calibration.profiles[1])
    assert record.parameters["set_content_sha256"] == osl_set_digest(calibration)
    assert (
        record.parameters["profile_id_field_semantics"]
        == "first_profile_of_ordered_set"
    )


def test_history_round_trip_json_safe(stage: OslCalibrationStage) -> None:
    sweep = _raw_sweep(np.random.default_rng(59))
    history = stage.apply(sweep, history=ProcessingHistory()).history
    payload = history.to_dict()
    text = json.dumps(payload, sort_keys=True, allow_nan=False)
    restored = ProcessingHistory.from_dict(json.loads(text))
    assert restored.to_dict() == payload
    assert restored.records[0].parameters == history.records[0].parameters


def test_previous_history_untouched(stage: OslCalibrationStage) -> None:
    history = ProcessingHistory()
    sweep = _raw_sweep(np.random.default_rng(61))
    result = stage.apply(sweep, history=history)
    assert len(history) == 0
    assert len(result.history) == 1
    assert result.history is not history


def test_golden_digest_literals() -> None:
    """Canonical digest format is pinned by golden hex literals (D3/D9).

    Recomputed independently on 2026-09-05 through the storage-mirrored
    payload via StoredOslProfile.to_payload + canonical JSON SHA-256; any
    drift in field names, ordering or encoding turns this red.
    """
    from uav_gpr.storage.calibration_files import StoredOslProfile

    profile = _profile(CH_S11, PID_S11)
    reference = hashlib.sha256(
        json.dumps(
            StoredOslProfile.from_profile(profile).to_payload(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    computed = osl_profile_digest(profile)
    assert computed == reference
    # Golden hex literals recorded 2026-09-05 (independent recomputation via
    # the storage codec payload + canonical JSON SHA-256): format drift is
    # caught even if both sides above were changed in lockstep.
    assert computed == (
        "652c6dcdce9fe6442c09089ea1dcc9af"
        "1bcc8407829292e50aefcef00164a73c"
    )
    calibration = OslCalibrationSet((profile,))
    set_digest = osl_set_digest(calibration)
    assert _hex64(set_digest)
    assert set_digest == (
        "7700a379beba3e4a0ae177a9ec87e948"
        "079fa2cce806e51578b6b73ce7b3544d"
    )


def _hex64(value: str) -> bool:
    import re

    return re.fullmatch(r"[0-9a-f]{64}", value) is not None


# ---------------------------------------------------------------------------
# 6. Raw immutability (acceptance 5).
# ---------------------------------------------------------------------------


def test_raw_input_never_modified(stage: OslCalibrationStage) -> None:
    scan = _raw_scan(np.random.default_rng(67))
    before_bytes = scan.data.tobytes()
    assert not scan.data.flags.writeable
    result = stage.apply(scan, history=ProcessingHistory())
    assert scan.data.tobytes() == before_bytes
    assert result.source is not scan
    assert not result.source.data.flags.writeable
    with pytest.raises(ValueError):
        result.source.data[0, 0, 0] = 0.0  # type: ignore[index]


def test_non_finite_row_rejected_by_solver_guards(
    stage: OslCalibrationStage,
) -> None:
    """Non-finite raw data fails closed inside the ISSUE-027 apply path."""
    sweep = _raw_sweep(np.random.default_rng(68))
    poisoned = sweep.data.copy()
    poisoned[0, 5] = np.inf + 0j
    broken = FrequencySweep(
        channels=sweep.channels, frequencies_hz=sweep.frequencies_hz, data=poisoned
    )
    with pytest.raises(DomainError) as excinfo:
        stage.apply(broken, history=ProcessingHistory())
    assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT


# ---------------------------------------------------------------------------
# 6b. Clock injection and stamp discipline (plan D8).
# ---------------------------------------------------------------------------


class _FixedClock:
    """Deterministic injected clock (no sleeping, no polling)."""

    def __init__(self, when: object) -> None:
        self._when = when

    def utc_now(self) -> object:
        return self._when


def test_executed_utc_wins_over_clock(stage: OslCalibrationStage) -> None:
    from datetime import UTC, datetime

    clock_stamp = datetime(2020, 1, 1, tzinfo=UTC)
    explicit = datetime(2026, 9, 5, 12, tzinfo=UTC)
    result = stage.apply(
        _raw_sweep(np.random.default_rng(69)),
        history=ProcessingHistory(),
        executed_utc=explicit,
        clock=_FixedClock(clock_stamp),  # type: ignore[arg-type]
    )
    assert result.history.records[0].executed_utc == explicit


def test_injected_clock_stamps_record(stage: OslCalibrationStage) -> None:
    from datetime import UTC, datetime

    when = datetime(2021, 5, 4, tzinfo=UTC)
    result = stage.apply(
        _raw_sweep(np.random.default_rng(70)),
        history=ProcessingHistory(),
        clock=_FixedClock(when),  # type: ignore[arg-type]
    )
    assert result.history.records[0].executed_utc == when


def test_naive_executed_utc_rejected(stage: OslCalibrationStage) -> None:
    from datetime import datetime

    with pytest.raises(DomainError) as excinfo:
        stage.apply(
            _raw_sweep(np.random.default_rng(71)),
            history=ProcessingHistory(),
            executed_utc=datetime(2026, 9, 5),  # naive: no tzinfo
        )
    assert excinfo.value.code is ErrorCode.NAIVE_DATETIME


def test_offset_aware_executed_utc_normalized(stage: OslCalibrationStage) -> None:
    from datetime import datetime, timedelta, timezone

    offset = timezone(timedelta(hours=8))
    stamped = stage.apply(
        _raw_sweep(np.random.default_rng(72)),
        history=ProcessingHistory(),
        executed_utc=datetime(2026, 9, 5, 8, tzinfo=offset),
    )
    record_utc = stamped.history.records[0].executed_utc
    assert record_utc.utcoffset() == timedelta(0)
    assert record_utc == datetime(2026, 9, 5, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# 6c. Stage independence in the full chain (osl -> bandpass -> ifft).
# ---------------------------------------------------------------------------


def test_osl_then_bandpass_then_ifft_chain_is_three_records(
    stage: OslCalibrationStage,
) -> None:
    """OSL stays an independent hop: downstream stages consume its output."""
    from uav_gpr.processing.time_domain import FrequencyToTimeStage

    sweep = _raw_sweep(np.random.default_rng(73))
    calibrated = stage.apply(sweep, history=ProcessingHistory())
    filtered = BandpassStage().apply(calibrated.source, history=calibrated.history)
    timed = FrequencyToTimeStage().apply(filtered.source, history=filtered.history)
    names = [record.stage_name for record in timed.history.records]
    assert names == [
        OSL_CALIBRATION_STAGE_NAME,
        "frequency_bandpass",
        "frequency_to_time_ifft",
    ]
    assert timed.history.records[0].output_domain is DataDomain.FREQUENCY_CALIBRATED
    assert timed.domain is DataDomain.TIME_BASE


# ---------------------------------------------------------------------------
# 7. Safe reuse judgement (acceptance 4: strict ID + digest provenance).
# ---------------------------------------------------------------------------


def test_safe_reuse_identical_provenance_passes(stage: OslCalibrationStage) -> None:
    sweep = _raw_sweep(np.random.default_rng(71))
    history = stage.apply(sweep, history=ProcessingHistory()).history
    verdict = check_safe_reuse(history, _calibration())
    assert verdict.compatible is True
    assert verdict.mismatches == ()
    require_safe_reuse(history, _calibration())  # must not raise


def test_safe_reuse_different_profile_id_rejected(
    stage: OslCalibrationStage,
) -> None:
    sweep = _raw_sweep(np.random.default_rng(73))
    history = stage.apply(sweep, history=ProcessingHistory()).history
    other = OslCalibrationSet(
        (
            _profile(CH_S11, PID_OTHER),
            _profile(CH_S22, PID_S22, seed_shift=0.25),
        )
    )
    verdict = check_safe_reuse(history, other)
    assert verdict.compatible is False
    assert any("ch_s11" in item for item in verdict.mismatches)
    with pytest.raises(DomainError) as excinfo:
        require_safe_reuse(history, other)
    assert excinfo.value.code is ErrorCode.PROCESSING_DOMAIN_MISMATCH


def test_safe_reuse_same_id_different_content_rejected(
    stage: OslCalibrationStage,
) -> None:
    """A re-solved profile that keeps the old UUID is still NOT the same."""
    sweep = _raw_sweep(np.random.default_rng(79))
    history = stage.apply(sweep, history=ProcessingHistory()).history
    tampered = OslCalibrationSet(
        (
            _profile(CH_S11, PID_S11, seed_shift=0.4),  # same id, new content
            _profile(CH_S22, PID_S22, seed_shift=0.25),
        )
    )
    verdict = check_safe_reuse(history, tampered)
    assert verdict.compatible is False
    assert any("content_sha256" in item for item in verdict.mismatches)


def test_safe_reuse_swapped_binding_order_rejected(
    stage: OslCalibrationStage,
) -> None:
    sweep = _raw_sweep(np.random.default_rng(83))
    history = stage.apply(sweep, history=ProcessingHistory()).history
    swapped = OslCalibrationSet(
        (
            _profile(CH_S22, PID_S22, seed_shift=0.25),
            _profile(CH_S11, PID_S11),
        )
    )
    verdict = check_safe_reuse(history, swapped)
    assert verdict.compatible is False
    assert len(verdict.mismatches) >= 1


def test_safe_reuse_requires_calibrated_last_record(
    stage: OslCalibrationStage,
) -> None:
    sweep = _raw_sweep(np.random.default_rng(89))
    calibrated = stage.apply(sweep, history=ProcessingHistory())
    filtered = BandpassStage().apply(calibrated.source, history=calibrated.history)
    verdict = check_safe_reuse(filtered.history, _calibration())
    assert verdict.compatible is False
    assert any("last" in item.lower() or "domain" in item.lower()
               for item in verdict.mismatches)
    empty = check_safe_reuse(ProcessingHistory(), _calibration())
    assert empty.compatible is False


def test_safe_reuse_legacy_record_without_digest_rejected(
    stage: OslCalibrationStage,
) -> None:
    """Strictness: a calibrated record lacking digests cannot be reused."""
    legacy = ProcessingRecord(
        stage_name="legacy_osl",
        stage_version="0.9",
        parameters={},  # no profiles/set digest at all
        input_domain=DataDomain.FREQUENCY_RAW,
        output_domain=DataDomain.FREQUENCY_CALIBRATED,
        executed_utc=stage.apply(
            _raw_sweep(np.random.default_rng(97)), history=ProcessingHistory()
        ).history.records[0].executed_utc,
        software_version="0.0.0",
        calibration_profile_id=PID_S11,
    )
    verdict = check_safe_reuse(ProcessingHistory((legacy,)), _calibration())
    assert verdict.compatible is False
    assert any("digest" in item or "profiles" in item for item in verdict.mismatches)


def test_osl_provenance_of_extracts_records(stage: OslCalibrationStage) -> None:
    sweep = _raw_sweep(np.random.default_rng(101))
    history = stage.apply(sweep, history=ProcessingHistory()).history
    provenance = osl_provenance_of(history)
    assert provenance is not None
    assert [item.channel_id for item in provenance] == ["ch_s11", "ch_s22"]
    assert osl_provenance_of(ProcessingHistory()) is None


# ---------------------------------------------------------------------------
# 8. Exclusion guards (out-of-scope discipline).
# ---------------------------------------------------------------------------


def test_module_source_contains_no_excluded_symbols() -> None:
    import ast
    import inspect

    import uav_gpr.processing.osl_calibration as module

    source = inspect.getsource(module)
    tree = ast.parse(source)
    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.append(node.module)
        elif isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
    # Hard exclusion 1 (ISSUE-032 "no saving"): processing never imports the
    # storage layer at all (AGENTS.md section 9 direction discipline).
    assert not [m for m in imported_modules if m.startswith("uav_gpr.storage")]
    # Hard exclusion 2/3 (no UI, no hardware): no Qt/matplotlib/serial.
    assert not [
        m
        for m in imported_modules
        if m.split(".")[0]
        in ("PySide6", "PySide2", "matplotlib", "serial", "usb")
    ]
    # Hard exclusion 4 (no re-solve): the ISSUE-027 solver symbol is never
    # imported or referenced anywhere in code or strings.
    assert "build_osl_calibration" not in source
    # Hard exclusion 5 (no IFFT here): the numpy FFT namespace is untouched.
    assert "np.fft" not in source and "numpy.fft" not in source
    # Semantic vocabulary guard over executable names only (identifiers and
    # attribute accesses — prose legitimately explains the domain contract).
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id.lower())
        elif isinstance(node, ast.Attribute):
            names.add(node.attr.lower())
    for banned in ("ifft", "backgroundsubtract", "airreference", "depth", "ui"):
        assert banned not in names
