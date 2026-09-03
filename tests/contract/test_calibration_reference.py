"""Contract tests for the UI-free reference capture service (ISSUE-028).

Covers: explicit OSL six-step and air-background session state machines that
forbid skipping and mixed configurations; step-frozen sweep configuration
(axis/channel checked strictly in ``accept_sweep``); delegation to the
ISSUE-027 OSL solver; in-flight sweeps rejected after the accept gate
closes; per-step retry that preserves prior steps; device errors exhausting
the retry budget fail closed; cancellation with no thread leaks; and the
gate-closed-before-controller-stop ordering.  Everything is driven by the
``SimulatedBackend`` + ``AcquisitionController`` with virtual time
(``ManualClock`` + event-based ``ManualWaiter``); there are no fixed sleeps
— all waits are events, joins, or bounded condition waits.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

import numpy as np
import pytest

from uav_gpr.acquisition.backend import SimulatedBackend, SimulationFaults
from uav_gpr.acquisition.controller import AcquisitionController
from uav_gpr.calibration.osl import OslCalibrationSet, OslStandard
from uav_gpr.calibration.reference import (
    AirBackgroundReference,
    AirBackgroundSession,
    ControllerReferenceAdapter,
    OslReferenceSession,
    ReferenceDomain,
    ReferenceSessionState,
)
from uav_gpr.core import (
    AcquisitionMode,
    CalibrationProfileId,
    ChannelSpec,
    DeviceId,
    DomainError,
    ErrorCode,
    FrequencySweep,
    GnssNoFixPolicy,
    LogicalPolarization,
    ManualClock,
    MissionConfig,
    MissionId,
    SParameter,
)

CREATED_UTC = datetime(2026, 1, 1, tzinfo=UTC)
MISSION = MissionId("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
DEVICE = DeviceId("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
INTERVAL_S = 1.0
INTERVAL_NS = 1_000_000_000

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
HH_S21 = ChannelSpec(
    channel_id="hh_s21",
    logical_polarization=LogicalPolarization.HH,
    s_parameter=SParameter.S21,
    display_name="HH S21",
)


def make_config(
    channels: tuple[ChannelSpec, ...] = (HH_S11, VV_S22),
) -> MissionConfig:
    return MissionConfig(
        frequency_start_hz=1.0e9,
        frequency_stop_hz=2.0e9,
        frequency_points=11,
        if_bw_hz=1_000.0,
        power_dbm=-10.0,
        channels=list(channels),
        acquisition_mode=AcquisitionMode.FIXED_COUNT,
        planned_trace_count=100,
        target_interval_s=INTERVAL_S,
        gnss_max_age_s=2.0,
        gnss_no_fix_policy=GnssNoFixPolicy.RECORD_WITHOUT_POSITION,
        calibration_profile_id=None,
        apply_calibration=False,
        background_reference_id=None,
        apply_background=False,
        created_utc=CREATED_UTC,
        software_version="0.1.0.dev0",
    )


def make_sweep(config: MissionConfig, seed: int = 0) -> FrequencySweep:
    rng = np.random.default_rng(seed)
    axis = config.frequency_axis_hz
    shape = (len(config.channels), axis.size)
    data = rng.standard_normal(shape) + 1j * rng.standard_normal(shape)
    return FrequencySweep(channels=config.channels, frequencies_hz=axis, data=data)


class ManualWaiter:
    """Event-based interruptible waiter (ISSUE-016/017 virtual-time pattern)."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self.waiting_event = threading.Event()

    def wait(self, timeout_ns: int) -> bool:
        self.waiting_event.set()
        woke = self._event.wait(timeout_ns / 1_000_000_000.0)
        self._event.clear()
        return woke

    def wake(self) -> None:
        self._event.set()


def advance_and_wake(clock: ManualClock, waiter: ManualWaiter) -> None:
    assert waiter.waiting_event.wait(10.0)
    clock.advance_monotonic(INTERVAL_NS)
    waiter.waiting_event.clear()
    waiter.wake()


class _Track:
    def __init__(self) -> None:
        self.controllers: list[AcquisitionController] = []

    def __call__(self, controller: AcquisitionController) -> AcquisitionController:
        self.controllers.append(controller)
        return controller


def make_backend(
    channels: tuple[ChannelSpec, ...], seed: int, faults: SimulationFaults | None = None
) -> SimulatedBackend:
    return SimulatedBackend(
        mission_id=MISSION,
        device_id=DEVICE,
        channels=channels,
        seed=seed,
        faults=faults,
        gnss_enabled=False,
    )


# ---------------------------------------------------------------------------
# construction validation
# ---------------------------------------------------------------------------


def test_osl_session_rejects_non_reflection_channel() -> None:
    with pytest.raises(DomainError) as info:
        OslReferenceSession(make_config(channels=(HH_S21,)), captures_per_step=2)
    assert info.value.code is ErrorCode.CHANNEL_CONTRACT_MISMATCH


def test_osl_session_rejects_invalid_counts() -> None:
    with pytest.raises(DomainError):
        OslReferenceSession(make_config(), captures_per_step=0)
    with pytest.raises(DomainError):
        OslReferenceSession(make_config(), captures_per_step=2, max_step_retries=-1)


def test_air_session_requires_profile_for_calibrated_domain() -> None:
    with pytest.raises(DomainError):
        AirBackgroundSession(
            make_config(), target_traces=3, domain=ReferenceDomain.OSL_CALIBRATED
        )
    session = AirBackgroundSession(
        make_config(),
        target_traces=3,
        domain=ReferenceDomain.OSL_CALIBRATED,
        calibration_profile_id=CalibrationProfileId.new(),
    )
    assert session.domain is ReferenceDomain.OSL_CALIBRATED
    with pytest.raises(DomainError):
        AirBackgroundSession(
            make_config(), target_traces=0, domain=ReferenceDomain.RAW
        )


def test_osl_session_steps_are_physical_six_steps() -> None:
    session = OslReferenceSession(make_config(), captures_per_step=1)
    steps = session.steps
    assert len(steps) == 6
    assert [(s.channel, s.standard) for s in steps] == [
        (HH_S11, OslStandard.OPEN),
        (HH_S11, OslStandard.SHORT),
        (HH_S11, OslStandard.LOAD),
        (VV_S22, OslStandard.OPEN),
        (VV_S22, OslStandard.SHORT),
        (VV_S22, OslStandard.LOAD),
    ]


# ---------------------------------------------------------------------------
# gate / state machine basics
# ---------------------------------------------------------------------------


def test_accept_before_start_is_rejected_and_start_twice_fails() -> None:
    config = make_config()
    session = OslReferenceSession(config, captures_per_step=1)
    result = session.accept_sweep(make_sweep(config, seed=1))
    assert result.accepted is False
    session.start()
    with pytest.raises(DomainError):
        session.start()
    session.cancel()
    assert session.state is ReferenceSessionState.CANCELLED


def test_skip_is_forbidden_build_requires_all_steps_completed() -> None:
    config = make_config(channels=(HH_S11,))
    session = OslReferenceSession(config, captures_per_step=1)
    session.start()
    session.accept_sweep(make_sweep(config, seed=2))
    with pytest.raises(DomainError):
        session.build()
    session.cancel()


# ---------------------------------------------------------------------------
# controller-driven happy path
# ---------------------------------------------------------------------------


def test_osl_six_step_happy_path_with_gate_before_stop() -> None:
    config = make_config()
    clock = ManualClock(CREATED_UTC, 0)
    waiter = ManualWaiter()
    track = _Track()
    controllers: list[AcquisitionController] = []

    def factory() -> AcquisitionController:
        backend = make_backend(config.channels, seed=7)
        controller = AcquisitionController(backend, clock=clock, waiter=waiter)
        track(controller)
        controller.configure(config)
        controller.start()
        controllers.append(controller)
        return controller

    session = OslReferenceSession(config, captures_per_step=1)
    adapter = ControllerReferenceAdapter(session, factory)
    thread = threading.Thread(target=adapter.run, name="reference-adapter")
    thread.start()
    for _ in range(6):
        advance_and_wake(clock, waiter)
    thread.join(timeout=10.0)
    assert not thread.is_alive()

    assert session.state is ReferenceSessionState.COMPLETED
    assert session.accepting_gate is False
    calibration = session.build()
    assert isinstance(calibration, OslCalibrationSet)
    assert len(calibration.profiles) == 2
    for profile in calibration.profiles:
        assert profile.open_capture_count == 1
        assert profile.short_capture_count == 1
        assert profile.load_capture_count == 1
        assert np.isfinite(profile.quality.worst_max_abs_error)
    # the accept gate closed strictly before the controller was stopped
    events = adapter.events
    assert events.index("gate_closed") < events.index("controller_stopped")
    assert events[-1] == "controller_closed"
    for controller in controllers:
        assert controller.join(timeout_s=5.0)
    assert controllers[-1].state.value == "closed"


# ---------------------------------------------------------------------------
# strict accept_sweep checks
# ---------------------------------------------------------------------------


def test_accept_rejects_mismatched_axis_and_nonfinite() -> None:
    config = make_config(channels=(HH_S11,))
    session = OslReferenceSession(config, captures_per_step=2)
    session.start()

    axis = config.frequency_axis_hz
    # wrong axis values (shifted): valid sweep, mismatched frozen axis
    wrong_axis_sweep = FrequencySweep(
        channels=config.channels,
        frequencies_hz=axis * 1.1,
        data=np.ones((1, axis.size), dtype=np.complex128),
    )
    with pytest.raises(DomainError):
        session.accept_sweep(wrong_axis_sweep)
    # non-finite data must be rejected before aggregation
    nonfinite = FrequencySweep(
        channels=config.channels,
        frequencies_hz=axis,
        data=np.full((1, axis.size), np.nan, dtype=np.complex128),
    )
    with pytest.raises(DomainError):
        session.accept_sweep(nonfinite)
    assert session.accepted_total == 0


# ---------------------------------------------------------------------------
# in-flight semantics
# ---------------------------------------------------------------------------


def test_inflight_sweeps_rejected_after_gate_closes() -> None:
    config = make_config(channels=(HH_S11,))
    session = OslReferenceSession(config, captures_per_step=1)
    session.start()
    # each accepted sweep fills exactly one physical step in order
    # (open, short, load) — no skipping or mixing is possible
    for seed in (4, 5):
        result = session.accept_sweep(make_sweep(config, seed=seed))
        assert result.accepted is True
        assert session.state is ReferenceSessionState.RUNNING
    final = session.accept_sweep(make_sweep(config, seed=6))
    assert final.accepted is True
    assert session.accepted_total == 3
    # target met: the session completed and the accept gate is closed, so
    # in-flight sweeps are rejected (gate closes before any controller stop)
    assert session.state is ReferenceSessionState.COMPLETED
    assert session.accepting_gate is False
    late = session.accept_sweep(make_sweep(config, seed=7))
    assert late.accepted is False


# ---------------------------------------------------------------------------
# retry / preserve prior steps
# ---------------------------------------------------------------------------


def test_retry_after_device_error_preserves_prior_steps() -> None:
    """P3-3 fix: retry must preserve prior *accepted* steps bit-exact.

    The first controller succeeds on sweep 0 (open step completed and
    accepted), then times out on sweep 1; the adapter records the step
    failure, rebuilds a healthy controller, and the session completes with
    the open-step row captured before the failure preserved bit-exact.
    """
    config = make_config(channels=(HH_S11,))
    clock = ManualClock(CREATED_UTC, 0)
    waiter = ManualWaiter()
    track = _Track()
    calls = {"n": 0}
    accepted_rows: list[np.ndarray] = []
    original_accept = OslReferenceSession.accept_sweep

    def _capture_accept(self: OslReferenceSession, sweep: object) -> object:
        result = original_accept(self, sweep)
        if getattr(result, "accepted", False):
            accepted_rows.append(np.array(np.asarray(sweep.data)[0], copy=True))
        return result

    OslReferenceSession.accept_sweep = _capture_accept  # type: ignore[method-assign]
    try:
        # fault at index 1: open (index 0) is captured before the failure.
        # The retry backend uses a different seed (0) so the combined
        # open/short/load measurements stay non-degenerate for the solver.
        def factory() -> AcquisitionController:
            if calls["n"] == 0:
                faults: SimulationFaults | None = SimulationFaults(timeout_at=(1,))
                seed = 11
            else:
                faults = None
                seed = 0
            calls["n"] += 1
            backend = make_backend(config.channels, seed=seed, faults=faults)
            controller = AcquisitionController(
                backend, clock=clock, waiter=waiter
            )
            track(controller)
            controller.configure(config)
            controller.start()
            return controller

        session = OslReferenceSession(
            config, captures_per_step=1, max_step_retries=3
        )
        adapter = ControllerReferenceAdapter(session, factory)
        thread = threading.Thread(target=adapter.run, name="reference-adapter-retry")
        thread.start()
        # bounded, event-driven wait for the terminal state (no fixed sleep)
        deadline = time.monotonic() + 20.0
        while (
            session.state is not ReferenceSessionState.COMPLETED
            and time.monotonic() < deadline
        ):
            thread.join(0.05)
        assert session.state is ReferenceSessionState.COMPLETED
        thread.join(timeout=10.0)
        assert not thread.is_alive()

        calibration = session.build()
        assert isinstance(calibration, OslCalibrationSet)
        # exactly two controllers: the first failed (mid-capture), the retry
        # completed the session — the retry itself proves a device failure
        # was recorded (step_failure_count is per-step and resets on step
        # advance, so the terminal count is not asserted here).
        assert calls["n"] == 2
        # the open-step row accepted BEFORE the device failure is preserved
        # bit-exact in the final calibration input (prior steps kept).
        assert len(accepted_rows) >= 1
        profile = calibration.profiles[0]
        assert np.array_equal(profile.open_measured_mean, accepted_rows[0])
        for controller in track.controllers:
            assert controller.join(timeout_s=5.0)
    finally:
        OslReferenceSession.accept_sweep = original_accept  # type: ignore[method-assign]


def test_device_error_exhausting_retries_fails_closed() -> None:
    config = make_config(channels=(HH_S11,))
    clock = ManualClock(CREATED_UTC, 0)
    waiter = ManualWaiter()
    track = _Track()

    def factory() -> AcquisitionController:
        backend = make_backend(
            config.channels, seed=13, faults=SimulationFaults(timeout_at=(0,))
        )
        controller = AcquisitionController(backend, clock=clock, waiter=waiter)
        track(controller)
        controller.configure(config)
        controller.start()
        return controller

    session = OslReferenceSession(config, captures_per_step=1, max_step_retries=0)
    adapter = ControllerReferenceAdapter(session, factory)
    thread = threading.Thread(target=adapter.run, name="reference-adapter-fail")
    thread.start()
    # let the failing controller reach FAILED deterministically
    track.controllers[0].wait_finished(10.0)
    thread.join(timeout=10.0)
    assert not thread.is_alive()

    assert session.state is ReferenceSessionState.FAILED
    assert session.accepting_gate is False
    for controller in track.controllers:
        assert controller.join(timeout_s=5.0)


# ---------------------------------------------------------------------------
# cancellation / resource closure
# ---------------------------------------------------------------------------


def test_cancel_closes_gate_and_stops_without_thread_leak() -> None:
    config = make_config(channels=(HH_S11,))
    clock = ManualClock(CREATED_UTC, 0)
    waiter = ManualWaiter()
    track = _Track()

    def factory() -> AcquisitionController:
        backend = make_backend(config.channels, seed=17)
        controller = AcquisitionController(backend, clock=clock, waiter=waiter)
        track(controller)
        controller.configure(config)
        controller.start()
        return controller

    session = OslReferenceSession(config, captures_per_step=1)
    adapter = ControllerReferenceAdapter(session, factory)
    thread = threading.Thread(target=adapter.run, name="reference-adapter-cancel")
    thread.start()
    # accept at least one sweep, then cancel from another thread
    assert track.controllers[0].sweeps.get(10.0) is not None
    session.cancel()
    thread.join(timeout=10.0)
    assert not thread.is_alive()

    assert session.state is ReferenceSessionState.CANCELLED
    assert session.accepting_gate is False
    assert session.accept_sweep(make_sweep(config, seed=18)).accepted is False
    for controller in track.controllers:
        assert controller.join(timeout_s=5.0)


# ---------------------------------------------------------------------------
# air background session
# ---------------------------------------------------------------------------


def test_air_background_reference_mean_and_readonly() -> None:
    config = make_config(channels=(HH_S11,))
    session = AirBackgroundSession(
        config, target_traces=3, domain=ReferenceDomain.RAW
    )
    session.start()
    sweeps = [make_sweep(config, seed=20 + i) for i in range(3)]
    for sweep in sweeps:
        result = session.accept_sweep(sweep)
        assert result.accepted is True
    reference = session.build()
    assert isinstance(reference, AirBackgroundReference)
    assert reference.domain is ReferenceDomain.RAW
    assert reference.trace_count == 3
    assert reference.channels == config.channels
    assert np.array_equal(reference.frequency_hz, config.frequency_axis_hz)
    expected = np.mean(
        np.stack([np.asarray(s.data, dtype=np.complex128) for s in sweeps]), axis=0
    )
    np.testing.assert_allclose(reference.mean_data, expected)
    with pytest.raises(ValueError):
        reference.mean_data.setflags(write=True)


def test_air_background_calibrated_domain_carries_profile_id() -> None:
    config = make_config(channels=(HH_S11,))
    profile_id = CalibrationProfileId.new()
    session = AirBackgroundSession(
        config,
        target_traces=1,
        domain=ReferenceDomain.OSL_CALIBRATED,
        calibration_profile_id=profile_id,
    )
    session.start()
    session.accept_sweep(make_sweep(config, seed=30))
    reference = session.build()
    assert reference.calibration_profile_id == profile_id
    assert reference.domain is ReferenceDomain.OSL_CALIBRATED


def test_air_background_failure_budget_fails_closed() -> None:
    """P2-1 regression: persistent device errors must exhaust the bounded
    budget and fail the air session closed — no unbounded retry storm.

    The first controller times out on every acquire; each recorded failure
    counts against ``max_retries`` and the fourth exceeds the budget, so
    the adapter stops cleanly after a bounded number of factory calls.
    """
    config = make_config(channels=(HH_S11,))
    clock = ManualClock(CREATED_UTC, 0)
    waiter = ManualWaiter()
    track = _Track()
    calls = {"n": 0}

    def factory() -> AcquisitionController:
        calls["n"] += 1
        backend = make_backend(
            config.channels, seed=21, faults=SimulationFaults(timeout_at=(0,))
        )
        controller = AcquisitionController(backend, clock=clock, waiter=waiter)
        track(controller)
        controller.configure(config)
        controller.start()
        return controller

    session = AirBackgroundSession(
        config, target_traces=3, domain=ReferenceDomain.RAW, max_retries=3
    )
    adapter = ControllerReferenceAdapter(session, factory)
    thread = threading.Thread(target=adapter.run, name="reference-adapter-air-fail")
    thread.start()
    deadline = time.monotonic() + 15.0
    while (
        session.state is not ReferenceSessionState.FAILED
        and thread.is_alive()
        and time.monotonic() < deadline
    ):
        thread.join(0.05)
    thread.join(timeout=10.0)
    assert not thread.is_alive()

    assert session.state is ReferenceSessionState.FAILED
    assert session.accepting_gate is False
    # bounded: budget + 1 controllers, never a retry storm
    assert calls["n"] == 4
    for controller in track.controllers:
        assert controller.join(timeout_s=5.0)
