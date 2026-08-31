"""Contract tests for the acquisition controller and pause/stop state
machine (ISSUE-017).

Covers: the full controller state/command table (deterministic illegal and
repeated commands); synchronous configure (PREPARING observable, READY
exposed); worker ownership with interruptible blocking points (no fixed
sleeps — events/joins/virtual clock only); pause stopping new sweeps at the
safe boundary while the in-flight sweep still finishes and is published;
stop draining completed sweeps; emergency stop interrupting hardware I/O
fail-closed (an interrupted sweep is never published); bounded publish with
BLOCK backpressure (worker throttles and drains on stop) and DROP_NEWEST
(measured drops, queue never grows); structured FAILED with ordered resource
release; the connection_generation reconnect hook; close from every state
with no leaked worker; and idempotent repeated commands.

Timing is virtual: a ``ManualClock`` plus an event-based ``ManualWaiter``
(the ISSUE-016 ``BlockingWaiter`` pattern) drive the scheduler; the test
advances the clock and wakes the waiter only after the worker signals a wait
is in flight (``waiting_event``).  All real-time bounds are safety upper
bounds on event waits and joins, never timing guesses.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import pytest

from uav_gpr.acquisition.backend import (
    AcquisitionBackend,
    AppliedConfig,
    BackendState,
    Capabilities,
    SimulatedBackend,
    SimulationFaults,
)
from uav_gpr.acquisition.controller import (
    AcquisitionController,
    BackpressurePolicy,
    ControllerFailure,
    ControllerState,
    ControllerStateError,
    StopReason,
)
from uav_gpr.core import (
    AcquisitionMode,
    ChannelSpec,
    DeviceId,
    GnssNoFixPolicy,
    LogicalPolarization,
    ManualClock,
    MissionConfig,
    MissionId,
    SParameter,
)
from uav_gpr.core.timeutil import Clock

CREATED_UTC = datetime(2026, 1, 1, tzinfo=UTC)
MISSION = MissionId("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
DEVICE = DeviceId("cccccccc-cccc-4ccc-8ccc-cccccccccccc")

HH_S11 = ChannelSpec(
    channel_id="hh_s11",
    logical_polarization=LogicalPolarization.HH,
    s_parameter=SParameter.S11,
    display_name="HH S11",
)

INTERVAL_S = 1.0
INTERVAL_NS = 1_000_000_000


def make_config(**overrides: object) -> MissionConfig:
    base: dict[str, object] = dict(
        frequency_start_hz=1.0e9,
        frequency_stop_hz=2.0e9,
        frequency_points=11,
        if_bw_hz=1_000.0,
        power_dbm=-10.0,
        channels=[HH_S11],
        acquisition_mode=AcquisitionMode.FIXED_COUNT,
        planned_trace_count=100,
        target_interval_s=INTERVAL_S,
        gnss_max_age_s=2.0,
        gnss_no_fix_policy=GnssNoFixPolicy.RECORD_WITHOUT_POSITION,
        calibration_profile_id=None,
        apply_calibration=False,
        background_reference_id=None,
        apply_background=False,
        display_start_s=0.0,
        display_duration_s=None,
        created_utc=CREATED_UTC,
        note="simulated mission",
        software_version="0.1.0.dev0",
    )
    base.update(overrides)
    return MissionConfig(**base)


def make_backend(
    *,
    seed: int = 0,
    clock: ManualClock | None = None,
    faults: SimulationFaults | None = None,
) -> SimulatedBackend:
    return SimulatedBackend(
        mission_id=MISSION,
        device_id=DEVICE,
        channels=(HH_S11,),
        seed=seed,
        clock=clock,
        faults=faults,
        gnss_enabled=False,
    )


def make_clock() -> ManualClock:
    return ManualClock(CREATED_UTC, 0)


class ManualWaiter:
    """Event-based interruptible waiter; the test wakes it explicitly.

    ``waiting_event`` is set right before the worker blocks, so tests sync on
    it (bounded real wait) and never guess timing; ``wait`` never sleeps on
    its own beyond the caller-supplied timeout bound.
    """

    def __init__(self) -> None:
        self._event = threading.Event()
        self.waiting_event = threading.Event()
        self.wait_calls = 0

    def wait(self, timeout_ns: int) -> bool:
        self.wait_calls += 1
        self.waiting_event.set()
        woke = self._event.wait(timeout_ns / 1_000_000_000.0)
        self._event.clear()
        return woke

    def wake(self) -> None:
        self._event.set()


class BlockingOpenBackend(SimulatedBackend):
    """SimulatedBackend whose ``_do_open`` blocks until released (safety
    bound only), so tests can observe the controller's PREPARING state."""

    def __init__(
        self,
        *,
        open_entered: threading.Event,
        release_open: threading.Event,
        clock: ManualClock | None = None,
    ) -> None:
        super().__init__(
            mission_id=MISSION,
            device_id=DEVICE,
            channels=(HH_S11,),
            seed=0,
            clock=clock,
            faults=None,
            gnss_enabled=False,
        )
        self._open_entered = open_entered
        self._release_open = release_open

    def _do_open(self) -> Capabilities:
        self._open_entered.set()
        if not self._release_open.wait(10.0):
            raise AssertionError("release_open not set within safety bound")
        return self.capabilities


@pytest.fixture()
def controller_cleanup() -> Iterator[Callable[[AcquisitionController], AcquisitionController]]:
    """Track every controller and force-close it at test end so a failing
    test can never leak a non-daemon worker thread."""
    controllers: list[AcquisitionController] = []

    def _track(controller: AcquisitionController) -> AcquisitionController:
        controllers.append(controller)
        return controller

    yield _track
    for controller in controllers:
        if controller.state is not ControllerState.CLOSED:
            controller.close()


def make_controller(
    backend: AcquisitionBackend,
    *,
    track: Callable[[AcquisitionController], AcquisitionController],
    clock: Clock | None = None,
    waiter: ManualWaiter | None = None,
    capacity: int = 16,
    backpressure: BackpressurePolicy = BackpressurePolicy.BLOCK,
    reconnect_hook: Callable[[], None] | None = None,
) -> AcquisitionController:
    return track(
        AcquisitionController(
            backend,
            capacity=capacity,
            backpressure=backpressure,
            clock=clock,
            waiter=waiter,
            reconnect_hook=reconnect_hook,
        )
    )


def advance_and_wake(clock: ManualClock, waiter: ManualWaiter) -> None:
    """Sync on the in-flight scheduler wait, advance one virtual interval,
    then wake the worker (the ISSUE-016 virtual-time pattern)."""
    assert waiter.waiting_event.wait(10.0)
    clock.advance_monotonic(INTERVAL_NS)
    waiter.waiting_event.clear()
    waiter.wake()


# ---------------------------------------------------------------------------
# 1. construction and buffer validation
# ---------------------------------------------------------------------------


def test_controller_constructor_validation(
    controller_cleanup: Callable[[AcquisitionController], AcquisitionController],
) -> None:
    clock = make_clock()
    waiter = ManualWaiter()
    with pytest.raises(TypeError):
        AcquisitionController(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        AcquisitionController(make_backend(), clock=object())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        AcquisitionController(make_backend(), waiter=object())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        AcquisitionController(make_backend(), capacity=1.5)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        AcquisitionController(make_backend(), capacity=True)  # type: ignore[arg-type]
    for bad in (0, -1):
        with pytest.raises(ValueError):
            AcquisitionController(make_backend(), capacity=bad)
    controller = make_controller(
        make_backend(), track=controller_cleanup, clock=clock, waiter=waiter
    )
    assert controller.state is ControllerState.IDLE
    assert controller.capabilities is None
    assert controller.applied_config is None
    assert controller.error is None
    assert controller.connection_generation == 0
    assert controller.metrics().capacity == 16


def test_buffer_capacity_bound_and_get_timeout() -> None:
    clock = make_clock()
    waiter = ManualWaiter()
    backend = make_backend(clock=clock)
    controller = AcquisitionController(
        backend, capacity=1, clock=clock, waiter=waiter
    )
    try:
        assert controller.sweeps.capacity == 1
        assert controller.sweeps.size == 0
        assert controller.sweeps.get(0.05) is None  # bounded absence probe
    finally:
        controller.close()


# ---------------------------------------------------------------------------
# 2. configure: lifecycle, rejections, failures, PREPARING observability
# ---------------------------------------------------------------------------


def test_configure_prepares_and_exposes_contract(
    controller_cleanup: Callable[[AcquisitionController], AcquisitionController],
) -> None:
    clock = make_clock()
    waiter = ManualWaiter()
    backend = make_backend(clock=clock)
    controller = make_controller(
        backend, track=controller_cleanup, clock=clock, waiter=waiter
    )
    config = make_config()
    applied = controller.configure(config)
    assert controller.state is ControllerState.READY
    assert controller.applied_config is applied
    assert controller.applied_config is not None
    assert controller.applied_config.config is config
    assert controller.capabilities is not None
    assert controller.capabilities.device_id == DEVICE
    assert backend.state is BackendState.CONFIGURED


def test_configure_rejections(
    controller_cleanup: Callable[[AcquisitionController], AcquisitionController],
) -> None:
    clock = make_clock()
    waiter = ManualWaiter()
    config = make_config()
    controller = make_controller(
        make_backend(clock=clock), track=controller_cleanup, clock=clock, waiter=waiter
    )
    controller.configure(config)
    # repeat configure from READY is rejected deterministically
    with pytest.raises(ControllerStateError) as info:
        controller.configure(config)
    assert info.value.context["allowed_states"] == ["idle"]
    with pytest.raises(TypeError):
        controller.configure(object())  # type: ignore[arg-type]

    # a backend that is not closed is rejected
    open_backend = make_backend(clock=make_clock())
    open_backend.open()
    other = make_controller(
        open_backend, track=controller_cleanup, clock=clock, waiter=waiter
    )
    with pytest.raises(ControllerStateError):
        other.configure(config)
    assert other.state is ControllerState.IDLE


def test_configure_fault_fails_controller(
    controller_cleanup: Callable[[AcquisitionController], AcquisitionController],
) -> None:
    clock = make_clock()
    waiter = ManualWaiter()
    backend = make_backend(
        clock=clock, faults=SimulationFaults(reject_config=True)
    )
    controller = make_controller(
        backend, track=controller_cleanup, clock=clock, waiter=waiter
    )
    with pytest.raises(ControllerFailure) as info:
        controller.configure(make_config())
    assert controller.state is ControllerState.FAILED
    assert controller.error is not None
    assert controller.error.reason == "controller_failure"
    assert controller.error.context["cause_type"] == "BackendConfigRejectedError"
    assert backend.state is BackendState.CLOSED  # resources released in order
    assert info.value.reason == "controller_failure"


def test_configure_below_scheduler_quantum_fails_structurally(
    controller_cleanup: Callable[[AcquisitionController], AcquisitionController],
) -> None:
    """P3-01: a sub-nanosecond target interval is rejected by the scheduler
    quantum rule; configure must surface it as a structured ControllerFailure
    and transition to FAILED (never a bare ValueError stuck in PREPARING)."""
    clock = make_clock()
    waiter = ManualWaiter()
    backend = make_backend(clock=clock)
    controller = make_controller(
        backend, track=controller_cleanup, clock=clock, waiter=waiter
    )
    with pytest.raises(ControllerFailure) as info:
        controller.configure(make_config(target_interval_s=1e-12))
    assert controller.state is ControllerState.FAILED
    assert controller.error is not None
    assert controller.error.reason == "controller_failure"
    assert controller.error.context["cause_type"] == "ValueError"
    assert backend.state is BackendState.CLOSED  # released in order
    assert info.value.reason == "controller_failure"


def test_preparing_observable_and_close_during_configure(
    controller_cleanup: Callable[[AcquisitionController], AcquisitionController],
) -> None:
    clock = make_clock()
    waiter = ManualWaiter()
    open_entered = threading.Event()
    release_open = threading.Event()
    backend = BlockingOpenBackend(
        open_entered=open_entered, release_open=release_open, clock=clock
    )
    controller = make_controller(
        backend, track=controller_cleanup, clock=clock, waiter=waiter
    )
    outcome: list[AppliedConfig | Exception] = []

    def do_configure() -> None:
        try:
            outcome.append(controller.configure(make_config()))
        except Exception as exc:
            outcome.append(exc)

    thread = threading.Thread(target=do_configure)
    thread.start()
    assert open_entered.wait(10.0)  # backend open is in flight
    assert controller.state is ControllerState.PREPARING  # observable
    release_open.set()
    thread.join(10.0)
    assert not thread.is_alive()
    assert controller.state is ControllerState.READY
    assert isinstance(outcome[0], AppliedConfig)

    # close while configure is in flight: configure aborts with a structured
    # failure and the controller ends CLOSED.
    open_entered2 = threading.Event()
    release_open2 = threading.Event()
    backend2 = BlockingOpenBackend(
        open_entered=open_entered2, release_open=release_open2, clock=make_clock()
    )
    controller2 = make_controller(
        backend2, track=controller_cleanup, clock=clock, waiter=waiter
    )
    outcome2: list[Exception] = []

    def do_configure2() -> None:
        try:
            controller2.configure(make_config())
        except Exception as exc:
            outcome2.append(exc)

    thread2 = threading.Thread(target=do_configure2)
    thread2.start()
    assert open_entered2.wait(10.0)
    assert controller2.state is ControllerState.PREPARING
    controller2.close()
    assert controller2.state is ControllerState.CLOSED
    release_open2.set()
    thread2.join(10.0)
    assert not thread2.is_alive()
    # P1-01 guard: the terminal state stays CLOSED deterministically — the
    # concurrent configure failure must never overwrite CLOSED with FAILED
    # nor record an error (plan section 5.2 command table).
    assert controller2.state is ControllerState.CLOSED
    assert controller2.error is None
    assert len(outcome2) == 1
    assert isinstance(outcome2[0], ControllerFailure)


# ---------------------------------------------------------------------------
# 3. start: lifecycle, idempotent repeat, illegal states
# ---------------------------------------------------------------------------


def test_start_lifecycle_and_idempotent_repeat(
    controller_cleanup: Callable[[AcquisitionController], AcquisitionController],
) -> None:
    clock = make_clock()
    waiter = ManualWaiter()
    backend = make_backend(clock=clock)
    controller = make_controller(
        backend, track=controller_cleanup, clock=clock, waiter=waiter
    )
    controller.configure(make_config())
    controller.start()
    assert controller.state is ControllerState.RUNNING
    controller.start()  # idempotent no-op
    assert controller.state is ControllerState.RUNNING
    sweep = controller.sweeps.get(2.0)  # first sweep due immediately
    assert sweep is not None
    assert sweep.metadata.trace_index == 0
    assert controller.join(2.0) is False  # worker still running
    controller.close()


def test_start_rejected_from_illegal_states(
    controller_cleanup: Callable[[AcquisitionController], AcquisitionController],
) -> None:
    clock = make_clock()
    waiter = ManualWaiter()
    config = make_config()

    idle = make_controller(
        make_backend(clock=clock), track=controller_cleanup, clock=clock, waiter=waiter
    )
    with pytest.raises(ControllerStateError) as info:
        idle.start()
    assert info.value.context["allowed_states"] == ["ready", "running"]

    paused = make_controller(
        make_backend(clock=clock), track=controller_cleanup, clock=clock, waiter=waiter
    )
    paused.configure(config)
    paused.start()
    paused.pause()
    with pytest.raises(ControllerStateError):
        paused.start()

    stopped = make_controller(
        make_backend(clock=clock), track=controller_cleanup, clock=clock, waiter=waiter
    )
    stopped.configure(config)
    stopped.stop()  # from READY: no worker
    with pytest.raises(ControllerStateError):
        stopped.start()

    failed = make_controller(
        make_backend(clock=clock, faults=SimulationFaults(timeout_at=(0,))),
        track=controller_cleanup,
        clock=clock,
        waiter=waiter,
    )
    failed.configure(config)
    failed.start()
    assert failed.wait_finished(2.0)
    with pytest.raises(ControllerStateError):
        failed.start()

    closed = make_controller(
        make_backend(clock=clock), track=controller_cleanup, clock=clock, waiter=waiter
    )
    closed.close()
    with pytest.raises(ControllerStateError):
        closed.start()
    with pytest.raises(ControllerStateError):
        closed.configure(config)
    with pytest.raises(ControllerStateError):
        closed.pause()


# ---------------------------------------------------------------------------
# 4. pause/resume: no new sweeps at the safe boundary, no burst on resume
# ---------------------------------------------------------------------------


def test_pause_stops_new_sweeps_and_resume_has_no_burst(
    controller_cleanup: Callable[[AcquisitionController], AcquisitionController],
) -> None:
    clock = make_clock()
    waiter = ManualWaiter()
    controller = make_controller(
        make_backend(clock=clock), track=controller_cleanup, clock=clock, waiter=waiter
    )
    controller.configure(make_config())
    controller.start()
    first = controller.sweeps.get(2.0)
    assert first is not None
    assert first.metadata.trace_index == 0

    controller.pause()
    assert controller.state is ControllerState.PAUSED
    controller.pause()  # idempotent
    assert controller.state is ControllerState.PAUSED

    # paused: repeated clock advances + wakes produce nothing
    for _ in range(3):
        clock.advance_monotonic(INTERVAL_NS)
        waiter.wake()
    assert controller.sweeps.get(0.1) is None

    controller.resume()
    assert controller.state is ControllerState.RUNNING
    controller.resume()  # idempotent while running
    assert controller.state is ControllerState.RUNNING
    # no burst: the next sweep waits a full interval after resume
    assert controller.sweeps.get(0.1) is None
    advance_and_wake(clock, waiter)
    second = controller.sweeps.get(2.0)
    assert second is not None
    assert second.metadata.trace_index == 1
    controller.close()


def test_pause_allows_in_flight_sweep_to_finish(
    controller_cleanup: Callable[[AcquisitionController], AcquisitionController],
) -> None:
    clock = make_clock()
    waiter = ManualWaiter()
    backend = make_backend(
        clock=clock, faults=SimulationFaults(delay_s={0: 0.05})
    )
    controller = make_controller(
        backend, track=controller_cleanup, clock=clock, waiter=waiter
    )
    controller.configure(make_config())
    controller.start()
    assert backend.acquire_started.wait(10.0)  # sweep 0 in flight (50 ms)
    controller.pause()
    assert controller.state is ControllerState.PAUSED
    # the in-flight sweep finishes at the safe boundary and is published
    sweep = controller.sweeps.get(2.0)
    assert sweep is not None
    assert sweep.metadata.trace_index == 0
    # no new sweep is started after the boundary
    clock.advance_monotonic(INTERVAL_NS)
    waiter.wake()
    assert controller.sweeps.get(0.1) is None
    controller.close()


# ---------------------------------------------------------------------------
# 5. stop: drain completed sweeps, STOPPING observable, idempotent
# ---------------------------------------------------------------------------


def test_stop_drains_in_flight_sweep(
    controller_cleanup: Callable[[AcquisitionController], AcquisitionController],
) -> None:
    clock = make_clock()
    waiter = ManualWaiter()
    backend = make_backend(
        clock=clock, faults=SimulationFaults(delay_s={0: 0.05})
    )
    controller = make_controller(
        backend, track=controller_cleanup, clock=clock, waiter=waiter
    )
    controller.configure(make_config())
    controller.start()
    assert backend.acquire_started.wait(10.0)
    controller.stop()
    assert controller.state is ControllerState.STOPPING  # observable mid-drain
    sweep = controller.sweeps.get(2.0)  # drained completed sweep
    assert sweep is not None
    assert sweep.metadata.trace_index == 0
    assert controller.wait_finished(2.0)
    assert controller.state is ControllerState.STOPPED
    assert controller.stop_reason is StopReason.USER_STOP
    assert controller.join(2.0)
    assert controller.sweeps.get(0.1) is None
    controller.close()


def test_stop_from_paused_and_ready(
    controller_cleanup: Callable[[AcquisitionController], AcquisitionController],
) -> None:
    clock = make_clock()
    waiter = ManualWaiter()
    config = make_config()

    # from PAUSED: no in-flight sweep, stop completes immediately
    paused = make_controller(
        make_backend(clock=clock), track=controller_cleanup, clock=clock, waiter=waiter
    )
    paused.configure(config)
    paused.start()
    paused.pause()
    paused.stop()
    assert paused.wait_finished(2.0)
    assert paused.state is ControllerState.STOPPED
    assert paused.stop_reason is StopReason.USER_STOP

    # from READY: no worker exists, stop completes immediately
    ready = make_controller(
        make_backend(clock=clock), track=controller_cleanup, clock=clock, waiter=waiter
    )
    ready.configure(config)
    ready.stop()
    assert ready.state is ControllerState.STOPPED
    assert ready.join(2.0)

    # repeated stop on terminal states is an idempotent no-op
    ready.stop()
    paused.stop()
    assert ready.state is ControllerState.STOPPED


# ---------------------------------------------------------------------------
# 6. emergency stop: interrupt hardware I/O, fail-closed, reason upgrade
# ---------------------------------------------------------------------------


def test_emergency_stop_interrupts_acquire_fail_closed(
    controller_cleanup: Callable[[AcquisitionController], AcquisitionController],
) -> None:
    clock = make_clock()
    waiter = ManualWaiter()
    backend = make_backend(
        clock=clock, faults=SimulationFaults(block_until_cancelled=True)
    )
    controller = make_controller(
        backend, track=controller_cleanup, clock=clock, waiter=waiter
    )
    controller.configure(make_config())
    controller.start()
    assert backend.acquire_started.wait(10.0)
    controller.emergency_stop()
    assert controller.wait_finished(2.0)
    assert controller.state is ControllerState.STOPPED
    assert controller.stop_reason is StopReason.EMERGENCY
    # the interrupted sweep was never completed: fail-closed, nothing published
    assert controller.sweeps.get(0.1) is None
    assert controller.sweeps.published == 0
    controller.close()


def test_emergency_stop_upgrades_stopping_reason(
    controller_cleanup: Callable[[AcquisitionController], AcquisitionController],
) -> None:
    clock = make_clock()
    waiter = ManualWaiter()
    backend = make_backend(
        clock=clock, faults=SimulationFaults(block_until_cancelled=True)
    )
    controller = make_controller(
        backend, track=controller_cleanup, clock=clock, waiter=waiter
    )
    controller.configure(make_config())
    controller.start()
    assert backend.acquire_started.wait(10.0)
    controller.stop()
    assert controller.state is ControllerState.STOPPING
    controller.emergency_stop()  # upgrade: interrupt I/O, keep reason EMERGENCY
    assert controller.wait_finished(2.0)
    assert controller.state is ControllerState.STOPPED
    assert controller.stop_reason is StopReason.EMERGENCY
    assert controller.sweeps.get(0.1) is None
    controller.close()


# ---------------------------------------------------------------------------
# 7. bounded publish: BLOCK backpressure and DROP_NEWEST policy
# ---------------------------------------------------------------------------


def test_backpressure_block_throttles_and_drains_on_stop(
    controller_cleanup: Callable[[AcquisitionController], AcquisitionController],
) -> None:
    clock = make_clock()
    waiter = ManualWaiter()
    backend = make_backend(
        clock=clock, faults=SimulationFaults(delay_s={1: 0.05})
    )
    controller = make_controller(
        backend,
        track=controller_cleanup,
        clock=clock,
        waiter=waiter,
        capacity=1,
        backpressure=BackpressurePolicy.BLOCK,
    )
    controller.configure(make_config())
    controller.start()
    # sweep 0 published; the worker is now blocked in the scheduler wait
    assert waiter.waiting_event.wait(10.0)
    assert controller.sweeps.size == 1  # capacity bound holds (not consumed)
    assert controller.sweeps.published == 1

    advance_and_wake(clock, waiter)
    assert backend.acquire_started.wait(10.0)  # sweep 1 in flight (50 ms)
    controller.stop()  # STOPPING; the worker must still drain sweep 1
    # the worker is throttled by the full buffer: it cannot finish until the
    # consumer frees a slot (BLOCK = drain guarantee)
    assert controller.wait_finished(0.2) is False
    first_out = controller.sweeps.get(2.0)  # consumer frees a slot
    assert first_out is not None
    assert first_out.metadata.trace_index == 0
    assert controller.wait_finished(2.0)
    assert controller.state is ControllerState.STOPPED
    assert controller.sweeps.published == 2
    assert controller.sweeps.dropped == 0
    drained = controller.sweeps.get(0.1)  # the drained in-flight sweep
    assert drained is not None
    assert drained.metadata.trace_index == 1
    controller.close()


def test_backpressure_drop_newest_measures_drops_and_never_grows(
    controller_cleanup: Callable[[AcquisitionController], AcquisitionController],
) -> None:
    clock = make_clock()
    waiter = ManualWaiter()
    backend = make_backend(
        clock=clock, faults=SimulationFaults(delay_s={1: 0.05})
    )
    controller = make_controller(
        backend,
        track=controller_cleanup,
        clock=clock,
        waiter=waiter,
        capacity=1,
        backpressure=BackpressurePolicy.DROP_NEWEST,
    )
    controller.configure(make_config())
    controller.start()
    # sweep 0 published; the worker is now blocked in the scheduler wait
    assert waiter.waiting_event.wait(10.0)
    assert controller.sweeps.size == 1  # retained, not consumed
    assert controller.sweeps.published == 1

    advance_and_wake(clock, waiter)
    assert backend.acquire_started.wait(10.0)  # sweep 1 in flight
    controller.stop()
    assert controller.wait_finished(2.0)
    assert controller.state is ControllerState.STOPPED
    assert controller.sweeps.published == 1
    assert controller.sweeps.dropped == 1  # newest dropped, measured
    assert controller.sweeps.size == 1  # bounded: never exceeds capacity
    assert controller.sweeps.get(0.1) is not None  # first sweep retained
    controller.close()


# ---------------------------------------------------------------------------
# 8. errors: structured FAILED, ordered release, fail-closed publish
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("faults", "cause_type"),
    [
        (SimulationFaults(timeout_at=(0,)), "BackendTimeoutError"),
        (SimulationFaults(half_sweep_at=(0,)), "BackendHalfSweepError"),
        (SimulationFaults(disconnect_at=(0,)), "BackendDisconnectedError"),
    ],
)
def test_acquire_faults_transition_to_failed(
    faults: SimulationFaults,
    cause_type: str,
    controller_cleanup: Callable[[AcquisitionController], AcquisitionController],
) -> None:
    clock = make_clock()
    waiter = ManualWaiter()
    backend = make_backend(clock=clock, faults=faults)
    controller = make_controller(
        backend, track=controller_cleanup, clock=clock, waiter=waiter
    )
    controller.configure(make_config())
    controller.start()
    assert controller.wait_finished(2.0)
    assert controller.state is ControllerState.FAILED
    assert controller.error is not None
    assert controller.error.reason == "controller_failure"
    assert controller.error.context["cause_type"] == cause_type
    # ordered release: scheduler cancelled, backend closed, worker exited
    assert backend.state is BackendState.CLOSED
    assert controller.join(2.0)
    # fail-closed: the failed sweep is never published
    assert controller.sweeps.get(0.1) is None
    controller.close()


# ---------------------------------------------------------------------------
# 9. reconnect hook and connection generation
# ---------------------------------------------------------------------------


def test_disconnect_reconnect_hook_continues_acquisition(
    controller_cleanup: Callable[[AcquisitionController], AcquisitionController],
) -> None:
    clock = make_clock()
    waiter = ManualWaiter()
    backend = make_backend(
        clock=clock, faults=SimulationFaults(disconnect_at=(1,))
    )
    config = make_config()
    hook_calls: list[int] = []

    def reconnect() -> None:
        hook_calls.append(backend.connection_generation)
        backend.close()
        backend.open()
        backend.configure(config)

    controller = make_controller(
        backend,
        track=controller_cleanup,
        clock=clock,
        waiter=waiter,
        reconnect_hook=reconnect,
    )
    controller.configure(config)
    controller.start()
    first = controller.sweeps.get(2.0)
    assert first is not None

    advance_and_wake(clock, waiter)  # triggers the disconnect at attempt 1
    # reconnect re-anchors the schedule: the next sweep is due immediately
    second = controller.sweeps.get(2.0)
    assert second is not None
    assert len(hook_calls) == 1
    assert hook_calls == [2]  # generation observed at disconnect time
    assert controller.connection_generation == 1  # new connection epoch
    assert controller.state is ControllerState.RUNNING

    advance_and_wake(clock, waiter)
    third = controller.sweeps.get(2.0)
    assert third is not None
    controller.stop()
    assert controller.wait_finished(2.0)
    assert controller.state is ControllerState.STOPPED
    controller.close()


def test_reconnect_hook_failure_and_missing_reestablishment(
    controller_cleanup: Callable[[AcquisitionController], AcquisitionController],
) -> None:
    clock = make_clock()
    waiter = ManualWaiter()
    config = make_config()

    def run(hook: Callable[[], None]) -> None:
        backend = make_backend(
            clock=clock, faults=SimulationFaults(disconnect_at=(1,))
        )
        controller = make_controller(
            backend,
            track=controller_cleanup,
            clock=clock,
            waiter=waiter,
            reconnect_hook=hook,
        )
        controller.configure(config)
        controller.start()
        assert controller.sweeps.get(2.0) is not None
        advance_and_wake(clock, waiter)
        assert controller.wait_finished(2.0)
        assert controller.state is ControllerState.FAILED
        assert controller.error is not None
        assert controller.error.context["cause_type"] in (
            "RuntimeError",
            "ReconnectContract",
        )
        controller.close()

    def raising_hook() -> None:
        raise RuntimeError("hook boom")

    def noop_hook() -> None:
        return None

    run(raising_hook)
    run(noop_hook)
    # a hook that re-opens but does not re-confirm the config is rejected too
    backend_holder: list[SimulatedBackend] = []

    def half_hook() -> None:
        assert backend_holder
        backend_holder[0].close()
        backend_holder[0].open()

    backend = make_backend(clock=clock, faults=SimulationFaults(disconnect_at=(1,)))
    backend_holder.append(backend)
    controller = make_controller(
        backend,
        track=controller_cleanup,
        clock=clock,
        waiter=waiter,
        reconnect_hook=half_hook,
    )
    controller.configure(config)
    controller.start()
    assert controller.sweeps.get(2.0) is not None
    advance_and_wake(clock, waiter)
    assert controller.wait_finished(2.0)
    assert controller.state is ControllerState.FAILED
    assert controller.error is not None
    assert controller.error.context["cause_type"] == "ReconnectContract"
    controller.close()


# ---------------------------------------------------------------------------
# 10. close: every state, idempotent, no leaked worker
# ---------------------------------------------------------------------------


def test_close_from_every_state_and_idempotent(
    controller_cleanup: Callable[[AcquisitionController], AcquisitionController],
) -> None:
    clock = make_clock()
    waiter = ManualWaiter()
    config = make_config()

    builders: list[Callable[[], AcquisitionController]] = []

    def idle() -> AcquisitionController:
        return make_controller(
            make_backend(clock=clock),
            track=controller_cleanup,
            clock=clock,
            waiter=waiter,
        )

    def ready() -> AcquisitionController:
        controller = idle()
        controller.configure(config)
        return controller

    def running() -> AcquisitionController:
        controller = ready()
        controller.start()
        assert controller.sweeps.get(2.0) is not None
        return controller

    def paused() -> AcquisitionController:
        controller = running()
        controller.pause()
        return controller

    def stopping() -> AcquisitionController:
        backend = make_backend(
            clock=clock, faults=SimulationFaults(block_until_cancelled=True)
        )
        controller = make_controller(
            backend, track=controller_cleanup, clock=clock, waiter=waiter
        )
        controller.configure(config)
        controller.start()
        assert backend.acquire_started.wait(10.0)
        controller.stop()
        assert controller.state is ControllerState.STOPPING
        return controller

    def stopped() -> AcquisitionController:
        controller = ready()
        controller.stop()
        return controller

    def failed() -> AcquisitionController:
        backend = make_backend(
            clock=clock, faults=SimulationFaults(timeout_at=(0,))
        )
        controller = make_controller(
            backend, track=controller_cleanup, clock=clock, waiter=waiter
        )
        controller.configure(config)
        controller.start()
        assert controller.wait_finished(2.0)
        return controller

    builders.extend([idle, ready, running, paused, stopping, stopped, failed])
    for builder in builders:
        controller = builder()
        controller.close()
        assert controller.state is ControllerState.CLOSED
        assert controller.wait_finished(2.0)
        assert controller.join(2.0)
        controller.close()  # idempotent
        assert controller.state is ControllerState.CLOSED


def test_close_releases_worker_blocked_in_acquire(
    controller_cleanup: Callable[[AcquisitionController], AcquisitionController],
) -> None:
    clock = make_clock()
    waiter = ManualWaiter()
    backend = make_backend(
        clock=clock, faults=SimulationFaults(block_until_cancelled=True)
    )
    controller = make_controller(
        backend, track=controller_cleanup, clock=clock, waiter=waiter
    )
    controller.configure(make_config())
    controller.start()
    assert backend.acquire_started.wait(10.0)
    controller.close()  # must interrupt the acquire and join the worker
    assert controller.state is ControllerState.CLOSED
    assert controller.join(2.0)
    assert backend.state is BackendState.CLOSED
    # commands after close are rejected deterministically
    with pytest.raises(ControllerStateError):
        controller.pause()
    with pytest.raises(ControllerStateError):
        controller.resume()
    with pytest.raises(ControllerStateError):
        controller.stop()
    with pytest.raises(ControllerStateError):
        controller.emergency_stop()
    with pytest.raises(ControllerStateError):
        controller.start()
    with pytest.raises(ControllerStateError):
        controller.configure(make_config())


def test_resume_rechecks_device_fail_closed(
    controller_cleanup: Callable[[AcquisitionController], AcquisitionController],
) -> None:
    clock = make_clock()
    waiter = ManualWaiter()
    backend = make_backend(clock=clock)
    controller = make_controller(
        backend, track=controller_cleanup, clock=clock, waiter=waiter
    )
    controller.configure(make_config())
    controller.start()
    assert controller.sweeps.get(2.0) is not None
    controller.pause()
    backend.close()  # device lost while paused (fail-closed on resume)
    controller.resume()
    assert controller.wait_finished(2.0)
    assert controller.state is ControllerState.FAILED
    assert controller.error is not None
    assert controller.error.reason == "controller_failure"
    controller.close()


# ---------------------------------------------------------------------------
# 11. P3-02: parametrized full state x command table (plan section 5.2)
# ---------------------------------------------------------------------------

# Outcome per (state, command): "err" = ControllerStateError; "noop" =
# idempotent no-op; ("ok", target_state, stop_reason | None) = transition.
# STOPPED/FAILED/CLOSED are terminal, so "ok" cells assert the terminal
# state and (for STOPPED) the deterministic stop_reason.
_COMMAND_ORDER = (
    "configure",
    "start",
    "pause",
    "resume",
    "stop",
    "emergency_stop",
    "close",
)

_Outcome = str | tuple[str, ControllerState, StopReason | None]

_TABLE: dict[ControllerState, dict[str, _Outcome]] = {
    ControllerState.IDLE: {
        "configure": ("ok", ControllerState.READY, None),
        "start": "err",
        "pause": "err",
        "resume": "err",
        "stop": "err",
        "emergency_stop": "err",
        "close": ("ok", ControllerState.CLOSED, None),
    },
    ControllerState.PREPARING: {
        "configure": "err",
        "start": "err",
        "pause": "err",
        "resume": "err",
        "stop": "err",
        "emergency_stop": "err",
        "close": ("ok", ControllerState.CLOSED, None),
    },
    ControllerState.READY: {
        "configure": "err",
        "start": ("ok", ControllerState.RUNNING, None),
        "pause": "err",
        "resume": "err",
        "stop": ("ok", ControllerState.STOPPED, StopReason.USER_STOP),
        "emergency_stop": ("ok", ControllerState.STOPPED, StopReason.EMERGENCY),
        "close": ("ok", ControllerState.CLOSED, None),
    },
    ControllerState.RUNNING: {
        "configure": "err",
        "start": "noop",
        "pause": ("ok", ControllerState.PAUSED, None),
        "resume": "noop",
        "stop": ("ok", ControllerState.STOPPED, StopReason.USER_STOP),
        "emergency_stop": ("ok", ControllerState.STOPPED, StopReason.EMERGENCY),
        "close": ("ok", ControllerState.CLOSED, None),
    },
    ControllerState.PAUSED: {
        "configure": "err",
        "start": "err",
        "pause": "noop",
        "resume": ("ok", ControllerState.RUNNING, None),
        "stop": ("ok", ControllerState.STOPPED, StopReason.USER_STOP),
        "emergency_stop": ("ok", ControllerState.STOPPED, StopReason.EMERGENCY),
        "close": ("ok", ControllerState.CLOSED, None),
    },
    ControllerState.STOPPING: {
        "configure": "err",
        "start": "err",
        "pause": "noop",
        "resume": "err",
        "stop": "noop",
        "emergency_stop": ("ok", ControllerState.STOPPED, StopReason.EMERGENCY),
        "close": ("ok", ControllerState.CLOSED, None),
    },
    ControllerState.STOPPED: {
        "configure": "err",
        "start": "err",
        "pause": "noop",
        "resume": "err",
        "stop": "noop",
        "emergency_stop": "noop",
        "close": ("ok", ControllerState.CLOSED, None),
    },
    ControllerState.FAILED: {
        "configure": "err",
        "start": "err",
        "pause": "noop",
        "resume": "err",
        "stop": "noop",
        "emergency_stop": "noop",
        "close": ("ok", ControllerState.CLOSED, None),
    },
    ControllerState.CLOSED: {
        "configure": "err",
        "start": "err",
        "pause": "err",
        "resume": "err",
        "stop": "err",
        "emergency_stop": "err",
        "close": "noop",
    },
}


def _build_state_controller(
    state: ControllerState,
    *,
    clock: ManualClock,
    waiter: ManualWaiter,
    track: Callable[[AcquisitionController], AcquisitionController],
    config: MissionConfig,
) -> tuple[AcquisitionController, Callable[[], None]]:
    """Build a fresh controller in ``state`` plus its teardown callable.

    PREPARING uses the blocking-open backend with a configure thread (the
    teardown releases and joins it); STOPPING uses a worker blocked in an
    interruptible acquire (the teardown closes the controller); FAILED uses
    the timeout fault; STOPPED is built from READY (no worker).
    """
    if state is ControllerState.PREPARING:
        open_entered = threading.Event()
        release_open = threading.Event()
        backend = BlockingOpenBackend(
            open_entered=open_entered, release_open=release_open, clock=clock
        )
        controller = make_controller(
            backend, track=track, clock=clock, waiter=waiter
        )
        outcome: list[Exception] = []

        def do_configure() -> None:
            try:
                controller.configure(config)
            except Exception as exc:
                outcome.append(exc)

        thread = threading.Thread(target=do_configure)
        thread.start()
        assert open_entered.wait(10.0)
        assert controller.state is ControllerState.PREPARING

        def teardown() -> None:
            release_open.set()
            thread.join(10.0)
            assert not thread.is_alive()

        return controller, teardown
    if state is ControllerState.STOPPING:
        backend = make_backend(
            clock=clock, faults=SimulationFaults(block_until_cancelled=True)
        )
        controller = make_controller(
            backend, track=track, clock=clock, waiter=waiter
        )
        controller.configure(config)
        controller.start()
        assert backend.acquire_started.wait(10.0)
        controller.stop()
        assert controller.state is ControllerState.STOPPING
        return controller, lambda: controller.close()
    if state is ControllerState.FAILED:
        backend = make_backend(clock=clock, faults=SimulationFaults(timeout_at=(0,)))
        controller = make_controller(
            backend, track=track, clock=clock, waiter=waiter
        )
        controller.configure(config)
        controller.start()
        assert controller.wait_finished(2.0)
        assert controller.state is ControllerState.FAILED
        return controller, lambda: controller.close()
    controller = make_controller(
        make_backend(clock=clock), track=track, clock=clock, waiter=waiter
    )
    if state is ControllerState.IDLE:
        return controller, lambda: controller.close()
    controller.configure(config)
    if state is ControllerState.READY:
        return controller, lambda: controller.close()
    if state is ControllerState.STOPPED:
        controller.stop()  # from READY: no worker, backend released
        assert controller.state is ControllerState.STOPPED
        return controller, lambda: controller.close()
    if state is ControllerState.CLOSED:
        controller.close()  # from READY: no worker
        return controller, lambda: None
    controller.start()
    if state is ControllerState.RUNNING:
        return controller, lambda: controller.close()
    if state is ControllerState.PAUSED:
        controller.pause()
        return controller, lambda: controller.close()
    raise AssertionError(f"unhandled state {state}")


def _run_command(controller: AcquisitionController, command: str) -> None:
    if command == "configure":
        controller.configure(make_config())
    elif command == "start":
        controller.start()
    elif command == "pause":
        controller.pause()
    elif command == "resume":
        controller.resume()
    elif command == "stop":
        controller.stop()
    elif command == "emergency_stop":
        controller.emergency_stop()
    elif command == "close":
        controller.close()
    else:  # pragma: no cover - table is closed
        raise AssertionError(f"unknown command {command}")


@pytest.mark.parametrize(
    "state",
    [item for item in ControllerState],
    ids=[item.value for item in ControllerState],
)
@pytest.mark.parametrize("command", list(_COMMAND_ORDER))
def test_command_table_all_cells(
    command: str,
    state: ControllerState,
    controller_cleanup: Callable[[AcquisitionController], AcquisitionController],
) -> None:
    """Every (state, command) cell of the plan section 5.2 table behaves
    deterministically: structured rejection, idempotent no-op, or the exact
    transition with terminal state and stop_reason."""
    clock = make_clock()
    waiter = ManualWaiter()
    config = make_config()
    controller, teardown = _build_state_controller(
        state, clock=clock, waiter=waiter, track=controller_cleanup, config=config
    )
    try:
        expected = _TABLE[state][command]
        before_reason = controller.stop_reason
        if expected == "err":
            with pytest.raises(ControllerStateError):
                _run_command(controller, command)
            assert controller.state is state
            if state is ControllerState.STOPPED:
                assert controller.stop_reason is before_reason
            return
        _run_command(controller, command)
        if expected == "noop":
            assert controller.state is state
            if state is ControllerState.STOPPED:
                assert controller.stop_reason is StopReason.USER_STOP
            return
        target, reason = expected[1], expected[2]
        if target is ControllerState.STOPPED:
            assert controller.wait_finished(2.0)
        assert controller.state is target
        if reason is not None:
            assert controller.stop_reason is reason
        if target is ControllerState.CLOSED:
            assert controller.wait_finished(2.0)
            assert controller.join(2.0)
        if target is ControllerState.READY:
            assert controller.applied_config is not None
    finally:
        teardown()

