"""Contract tests for the monotonic acquisition scheduler (ISSUE-016).

Covers: injectable monotonic Clock/Waiter; absolute-deadline scheduling with
zero cumulative drift over tens of thousands of virtual-time cycles; single
sweep serialization; target/actual interval, schedule error and overrun
observations that feed TraceMetadata construction (no fake wall clock); first
sweep null semantics; pause/resume re-anchoring without burst (no debt
catching up); immediate cancel of an in-flight wait; and UTC wall-clock jumps
never affecting scheduling.

All timing is virtual: ``AdvancingWaiter`` advances a ``ManualClock`` by exact
integer nanoseconds (no fixed sleeps); interruption tests use events and
thread joins only.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest

from uav_gpr.acquisition.scheduler import (
    MonotonicAcquisitionScheduler,
    ScheduleObservation,
    SchedulerState,
    SchedulerStateError,
)
from uav_gpr.core import (
    DeviceId,
    ManualClock,
    MissionId,
    MonotonicNs,
    TraceQualityReason,
    TraceQualityStatus,
    TraceUid,
)
from uav_gpr.core.metadata import TraceMetadata

CREATED_UTC = datetime(2026, 1, 1, tzinfo=UTC)
MISSION = MissionId("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
DEVICE = DeviceId("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
TRACE_UID = TraceUid("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


def make_clock(monotonic_ns: int = 0) -> ManualClock:
    return ManualClock(CREATED_UTC, monotonic_ns)


class AdvancingWaiter:
    """Virtual waiter: advances the shared ManualClock by exactly the
    requested nanoseconds and is never interrupted (pure virtual time).

    Records ``total_advanced_ns`` so tests can assert the exact amount of
    virtual time consumed by scheduling waits.
    """

    def __init__(self, clock: ManualClock) -> None:
        self._clock = clock
        self.total_advanced_ns = 0

    def wait(self, timeout_ns: int) -> bool:
        self._clock.advance_monotonic(timeout_ns)
        self.total_advanced_ns += timeout_ns
        return False

    def wake(self) -> None:
        pass


class BlockingWaiter:
    """Event-based waiter for interruption tests.

    ``wait()`` blocks on an internal event until :meth:`wake` (the caller
    runs the scheduler in a worker thread); ``waiting_event`` signals that a
    wait is in flight so the test never guesses timing with fixed sleeps.
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


def make_scheduler(
    *,
    target_interval_s: float = 1.0,
    clock: ManualClock | None = None,
    waiter: object | None = None,
) -> MonotonicAcquisitionScheduler:
    clock = clock if clock is not None else make_clock()
    return MonotonicAcquisitionScheduler(
        target_interval_s=target_interval_s,
        clock=clock,
        waiter=waiter if waiter is not None else AdvancingWaiter(clock),  # type: ignore[arg-type]
    )


class ScriptedClock(ManualClock):
    """ManualClock whose monotonic reads follow a scripted sequence (the
    last value repeats).  Used to inject clock anomalies deterministically
    (e.g. a stale first read, or a backwards jump between sweeps).
    """

    def __init__(self, values: list[int]) -> None:
        super().__init__(CREATED_UTC, values[0])
        self._values = values
        self._reads = 0

    def monotonic_ns(self) -> MonotonicNs:
        value = self._values[min(self._reads, len(self._values) - 1)]
        self._reads += 1
        return MonotonicNs(value)


class RecordingWaiter(AdvancingWaiter):
    """AdvancingWaiter that records every requested wait duration so tests
    can assert exactly how long the scheduler waited (and that it did not
    skip the wait entirely)."""

    def __init__(self, clock: ManualClock) -> None:
        super().__init__(clock)
        self.waited_ns: list[int] = []

    def wait(self, timeout_ns: int) -> bool:
        self.waited_ns.append(timeout_ns)
        return super().wait(timeout_ns)


def build_metadata(
    clock: ManualClock, obs: ScheduleObservation, *, trace_index: int
) -> TraceMetadata:
    """Prove the observation values are directly usable for metadata building."""
    mid_ns = (
        obs.sweep_started_monotonic_ns.ns + obs.sweep_finished_monotonic_ns.ns
    ) // 2
    return TraceMetadata(
        mission_id=MISSION,
        trace_index=trace_index,
        trace_uid=TRACE_UID,
        device_id=DEVICE,
        sweep_started_utc=clock.utc_now(),
        sweep_midpoint_utc=clock.utc_now(),
        sweep_finished_utc=clock.utc_now(),
        sweep_started_monotonic_ns=obs.sweep_started_monotonic_ns,
        sweep_midpoint_monotonic_ns=MonotonicNs(mid_ns),
        sweep_finished_monotonic_ns=obs.sweep_finished_monotonic_ns,
        target_interval_s=obs.target_interval_s,
        actual_interval_s=obs.actual_interval_s,
        schedule_error_s=obs.schedule_error_s,
        connection_generation=1,
        raw_trace_sha256=None,
        gnss_match=None,
        quality_status=TraceQualityStatus.DEGRADED,
        quality_reasons=(TraceQualityReason.GNSS_MISSING,),
    )


# ---------------------------------------------------------------------------
# 1. construction validation
# ---------------------------------------------------------------------------


def test_target_interval_must_be_a_positive_finite_float() -> None:
    for bad in (True, 1, 0.0, -0.5, float("nan"), float("inf")):
        with pytest.raises((TypeError, ValueError)):
            MonotonicAcquisitionScheduler(target_interval_s=bad)  # type: ignore[arg-type]


def test_target_below_scheduling_quantum_rejected() -> None:
    with pytest.raises(ValueError, match="quantum"):
        MonotonicAcquisitionScheduler(target_interval_s=1e-10)


def test_clock_and_waiter_must_implement_protocols() -> None:
    with pytest.raises(TypeError):
        MonotonicAcquisitionScheduler(
            target_interval_s=1.0, clock=object()  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError):
        MonotonicAcquisitionScheduler(
            target_interval_s=1.0, waiter=object()  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# 2. lifecycle
# ---------------------------------------------------------------------------


def test_start_twice_rejected() -> None:
    sched = make_scheduler()
    sched.start()
    with pytest.raises(SchedulerStateError):
        sched.start()


def test_operations_before_start_rejected() -> None:
    sched = make_scheduler()
    with pytest.raises(SchedulerStateError):
        sched.wait_for_next()
    with pytest.raises(SchedulerStateError):
        sched.sweep_started()
    with pytest.raises(SchedulerStateError):
        sched.sweep_finished()


def test_sweep_finished_without_started_sweep_rejected() -> None:
    sched = make_scheduler()
    sched.start()
    with pytest.raises(SchedulerStateError):
        sched.sweep_finished()


def test_single_sweep_serial_enforced() -> None:
    clock = make_clock()
    sched = make_scheduler(clock=clock)
    sched.start()
    assert sched.wait_for_next() is True
    sched.sweep_started()
    with pytest.raises(SchedulerStateError) as exc:
        sched.sweep_started()
    assert exc.value.context["busy"] is True
    with pytest.raises(SchedulerStateError) as exc:
        sched.wait_for_next()
    assert exc.value.context["busy"] is True


def test_pause_resume_cancel_lifecycle() -> None:
    clock = make_clock()
    sched = make_scheduler(clock=clock)
    with pytest.raises(SchedulerStateError):
        sched.pause()  # idle
    with pytest.raises(SchedulerStateError):
        sched.resume()  # idle
    sched.start()
    sched.pause()
    sched.pause()  # idempotent
    assert sched.state is SchedulerState.PAUSED
    sched.resume()
    sched.resume()  # no-op in running (never re-anchors a running schedule)
    assert sched.state is SchedulerState.RUNNING
    sched.cancel()
    assert sched.state is SchedulerState.CANCELLED
    sched.cancel()  # idempotent
    sched.pause()  # no-op after cancel
    with pytest.raises(SchedulerStateError):
        sched.resume()  # cancelled is terminal


def test_sweep_started_rejected_while_paused_or_cancelled() -> None:
    clock = make_clock()
    sched = make_scheduler(clock=clock)
    sched.start()
    sched.pause()
    with pytest.raises(SchedulerStateError):
        sched.sweep_started()
    sched.resume()
    sched.cancel()
    with pytest.raises(SchedulerStateError):
        sched.sweep_started()


# ---------------------------------------------------------------------------
# 3. first sweep
# ---------------------------------------------------------------------------


def test_first_sweep_due_immediately_with_null_interval_fields() -> None:
    clock = make_clock()
    waiter = AdvancingWaiter(clock)
    sched = MonotonicAcquisitionScheduler(
        target_interval_s=0.25, clock=clock, waiter=waiter
    )
    sched.start()
    assert sched.wait_for_next() is True
    assert waiter.total_advanced_ns == 0  # first deadline == anchor
    sched.sweep_started()
    start = clock.monotonic_ns()
    clock.advance_monotonic(50_000_000)  # 0.05 s sweep
    obs = sched.sweep_finished()
    assert obs.target_interval_s == 0.25
    assert obs.actual_interval_s is None
    assert obs.schedule_error_s is None
    assert obs.overrun_s == 0.0
    assert obs.sweep_started_monotonic_ns == start
    assert obs.deadline_monotonic_ns == start  # deadline == anchor
    assert obs.sweep_duration_s == 0.05


# ---------------------------------------------------------------------------
# 4. long-run zero drift (virtual time)
# ---------------------------------------------------------------------------


def test_no_drift_over_fifty_thousand_cycles() -> None:
    clock = make_clock()
    waiter = AdvancingWaiter(clock)
    target = 1.0
    duration_ns = 100_000_000  # 0.1 s sweep
    interval_ns = 1_000_000_000
    count = 50_000
    sched = MonotonicAcquisitionScheduler(
        target_interval_s=target, clock=clock, waiter=waiter
    )
    sched.start()
    assert sched.wait_for_next() is True
    anchor = clock.monotonic_ns()
    sched.sweep_started()
    clock.advance_monotonic(duration_ns)
    first = sched.sweep_finished()
    assert first.actual_interval_s is None
    assert first.deadline_monotonic_ns == anchor
    for k in range(1, count):
        assert sched.wait_for_next() is True
        sched.sweep_started()
        clock.advance_monotonic(duration_ns)
        obs = sched.sweep_finished()
        assert obs.actual_interval_s == target
        assert obs.schedule_error_s == 0.0
        assert obs.overrun_s == 0.0
        # absolute deadline chain: exactly anchor + k * interval, integer-exact
        assert obs.deadline_monotonic_ns.ns == anchor.ns + k * interval_ns
        assert obs.sweep_started_monotonic_ns.ns == anchor.ns + k * interval_ns
    # exact virtual time consumed by waits, integer-exact
    assert waiter.total_advanced_ns == (count - 1) * (interval_ns - duration_ns)
    assert clock.monotonic_ns().ns == (
        anchor.ns + (count - 1) * interval_ns + duration_ns
    )


# ---------------------------------------------------------------------------
# 5. overrun
# ---------------------------------------------------------------------------


def test_overrun_flagged_when_duration_exceeds_interval() -> None:
    clock = make_clock()
    waiter = AdvancingWaiter(clock)
    sched = MonotonicAcquisitionScheduler(
        target_interval_s=1.0, clock=clock, waiter=waiter
    )
    sched.start()
    assert sched.wait_for_next() is True
    interval_ns = 1_000_000_000
    sched.sweep_started()
    clock.advance_monotonic(3_000_000_000)  # 3 s > 1 s
    obs1 = sched.sweep_finished()
    assert obs1.overrun_s == 2.0
    # next deadline already passed -> due immediately, waiter untouched
    assert sched.wait_for_next() is True
    assert waiter.total_advanced_ns == 0
    sched.sweep_started()
    clock.advance_monotonic(2_000_000_000)
    obs2 = sched.sweep_finished()
    assert obs2.overrun_s == 1.0
    assert obs2.actual_interval_s == 3.0  # starts at 0 and 3 s
    assert obs2.schedule_error_s == 2.0
    # deadline chain still absolute: sweep 2 was due at anchor + 1 * interval
    assert obs2.deadline_monotonic_ns.ns == (
        obs1.sweep_started_monotonic_ns.ns + interval_ns
    )


def test_overrun_zero_when_duration_at_or_below_interval() -> None:
    clock = make_clock()
    sched = make_scheduler(clock=clock)
    sched.start()
    assert sched.wait_for_next() is True
    sched.sweep_started()
    clock.advance_monotonic(1_000_000_000)  # exactly the target
    assert sched.sweep_finished().overrun_s == 0.0
    assert sched.wait_for_next() is True
    sched.sweep_started()
    clock.advance_monotonic(500_000_000)  # faster than the target
    assert sched.sweep_finished().overrun_s == 0.0


def test_deadline_chain_anchored_under_repeated_overrun() -> None:
    clock = make_clock()
    waiter = AdvancingWaiter(clock)
    sched = MonotonicAcquisitionScheduler(
        target_interval_s=1.0, clock=clock, waiter=waiter
    )
    sched.start()
    anchor = clock.monotonic_ns()
    for k in range(100):
        assert sched.wait_for_next() is True
        sched.sweep_started()
        clock.advance_monotonic(2_500_000_000)  # 2.5 s per sweep
        obs = sched.sweep_finished()
        assert obs.overrun_s == 1.5
        assert obs.deadline_monotonic_ns.ns == anchor.ns + k * 1_000_000_000
        if k > 0:
            assert obs.actual_interval_s == 2.5
            assert obs.schedule_error_s == 1.5
    assert waiter.total_advanced_ns == 0  # always overdue: never waited


# ---------------------------------------------------------------------------
# 6. pause/resume re-anchoring without burst
# ---------------------------------------------------------------------------


def test_pause_resume_reanchors_without_burst() -> None:
    clock = make_clock()
    waiter = AdvancingWaiter(clock)
    sched = MonotonicAcquisitionScheduler(
        target_interval_s=1.0, clock=clock, waiter=waiter
    )
    sched.start()
    assert sched.wait_for_next() is True
    sched.sweep_started()
    clock.advance_monotonic(50_000_000)
    obs1 = sched.sweep_finished()
    first_start_ns = obs1.sweep_started_monotonic_ns.ns  # 0

    sched.pause()
    before = waiter.total_advanced_ns
    assert sched.wait_for_next() is False  # paused: no scheduling at all
    assert waiter.total_advanced_ns == before  # nothing advanced
    clock.advance_monotonic(10_000_000_000)  # 10 s virtual pause gap
    resume_ns = clock.monotonic_ns().ns
    sched.resume()  # new anchor == resume instant

    before = waiter.total_advanced_ns
    assert sched.wait_for_next() is True
    # exactly one full interval of waiting after resume -> no burst, no debt
    assert waiter.total_advanced_ns - before == 1_000_000_000
    sched.sweep_started()
    assert clock.monotonic_ns().ns == resume_ns + 1_000_000_000
    clock.advance_monotonic(50_000_000)
    obs2 = sched.sweep_finished()
    # honest interval: includes the pause gap (10 s) plus the 1 s cadence
    assert obs2.actual_interval_s == 11.05
    assert obs2.schedule_error_s == 10.05
    assert obs2.sweep_started_monotonic_ns.ns == first_start_ns + 11_050_000_000
    # cadence fully restored afterwards
    assert sched.wait_for_next() is True
    sched.sweep_started()
    assert clock.monotonic_ns().ns == resume_ns + 2_000_000_000
    clock.advance_monotonic(50_000_000)
    obs3 = sched.sweep_finished()
    assert obs3.actual_interval_s == 1.0
    assert obs3.schedule_error_s == 0.0


def test_wait_advances_exactly_to_deadline() -> None:
    clock = make_clock()
    waiter = AdvancingWaiter(clock)
    sched = MonotonicAcquisitionScheduler(
        target_interval_s=0.5, clock=clock, waiter=waiter
    )
    sched.start()
    assert sched.wait_for_next() is True
    sched.sweep_started()
    clock.advance_monotonic(100_000_000)
    sched.sweep_finished()
    assert waiter.total_advanced_ns == 0
    assert sched.wait_for_next() is True
    assert clock.monotonic_ns().ns == 500_000_000
    assert waiter.total_advanced_ns == 400_000_000


# ---------------------------------------------------------------------------
# 7. interruption of an in-flight wait (events + joins, no fixed sleeps)
# ---------------------------------------------------------------------------


def _start_blocked_wait(
    sched: MonotonicAcquisitionScheduler, waiter: BlockingWaiter
) -> tuple[threading.Thread, list[bool | None]]:
    result: list[bool | None] = [None]

    def worker() -> None:
        result[0] = sched.wait_for_next()

    thread = threading.Thread(target=worker)
    thread.start()
    assert waiter.waiting_event.wait(10.0)  # event-based sync; safety bound only
    return thread, result


def test_pause_interrupts_in_flight_wait() -> None:
    clock = make_clock()
    waiter = BlockingWaiter()
    sched = MonotonicAcquisitionScheduler(
        target_interval_s=1.0, clock=clock, waiter=waiter
    )
    sched.start()
    assert sched.wait_for_next() is True
    sched.sweep_started()
    clock.advance_monotonic(100_000_000)
    sched.sweep_finished()  # next deadline is 0.9 s of virtual time away
    thread, result = _start_blocked_wait(sched, waiter)
    try:
        sched.pause()
        thread.join(10.0)
        assert not thread.is_alive()
        assert result[0] is False
    finally:
        thread.join(10.0)


def test_cancel_interrupts_in_flight_wait_immediately() -> None:
    clock = make_clock()
    waiter = BlockingWaiter()
    sched = MonotonicAcquisitionScheduler(
        target_interval_s=1.0, clock=clock, waiter=waiter
    )
    sched.start()
    assert sched.wait_for_next() is True
    sched.sweep_started()
    clock.advance_monotonic(100_000_000)
    sched.sweep_finished()
    thread, result = _start_blocked_wait(sched, waiter)
    try:
        sched.cancel()
        thread.join(10.0)
        assert not thread.is_alive()
        assert result[0] is False
    finally:
        thread.join(10.0)


def test_resume_after_pause_does_not_burst_a_blocked_wait() -> None:
    clock = make_clock()
    waiter = BlockingWaiter()
    sched = MonotonicAcquisitionScheduler(
        target_interval_s=1.0, clock=clock, waiter=waiter
    )
    sched.start()
    assert sched.wait_for_next() is True
    sched.sweep_started()
    clock.advance_monotonic(100_000_000)
    sched.sweep_finished()
    thread, result = _start_blocked_wait(sched, waiter)
    try:
        sched.pause()
        thread.join(10.0)
        assert result[0] is False
        sched.resume()
        # after resume the next sweep is a full interval away: the same
        # frozen-clock worker must NOT see it as immediately due.
        assert waiter.waiting_event.is_set()
        before = waiter.wait_calls
        waiter.waiting_event.clear()
        thread2, result2 = _start_blocked_wait(sched, waiter)
        try:
            sched.pause()  # wake the second blocked wait
            thread2.join(10.0)
            assert result2[0] is False
            assert waiter.wait_calls == before + 1  # it entered wait, not due
        finally:
            thread2.join(10.0)
    finally:
        thread.join(10.0)


# ---------------------------------------------------------------------------
# 8. cancel semantics
# ---------------------------------------------------------------------------


def test_cancel_before_wait_returns_false_without_waiting() -> None:
    clock = make_clock()
    waiter = AdvancingWaiter(clock)
    sched = MonotonicAcquisitionScheduler(
        target_interval_s=1.0, clock=clock, waiter=waiter
    )
    sched.start()
    sched.cancel()
    assert sched.wait_for_next() is False
    assert waiter.total_advanced_ns == 0
    assert sched.state is SchedulerState.CANCELLED


def test_cancel_with_in_flight_sweep_still_records_observation() -> None:
    clock = make_clock()
    sched = make_scheduler(clock=clock)
    sched.start()
    assert sched.wait_for_next() is True
    sched.sweep_started()
    clock.advance_monotonic(200_000_000)
    sched.cancel()
    obs = sched.sweep_finished()  # honest completion of the in-flight sweep
    assert obs.overrun_s == 0.0
    assert obs.actual_interval_s is None
    assert sched.wait_for_next() is False
    with pytest.raises(SchedulerStateError):
        sched.sweep_started()


# ---------------------------------------------------------------------------
# 9. UTC wall-clock jumps never affect scheduling
# ---------------------------------------------------------------------------


def test_utc_jumps_do_not_affect_scheduling() -> None:
    def run_with_jumps(jumps: bool) -> list[tuple[float | None, float | None, float]]:
        clock = make_clock()
        waiter = AdvancingWaiter(clock)
        sched = MonotonicAcquisitionScheduler(
            target_interval_s=1.0, clock=clock, waiter=waiter
        )
        sched.start()
        observations: list[tuple[float | None, float | None, float]] = []
        for k in range(100):
            assert sched.wait_for_next() is True
            if jumps and k == 40:
                clock.advance_utc(timedelta(hours=5))
            if jumps and k == 70:
                clock.advance_utc(timedelta(days=-1))
            sched.sweep_started()
            clock.advance_monotonic(100_000_000)
            obs = sched.sweep_finished()
            observations.append(
                (obs.actual_interval_s, obs.schedule_error_s, obs.overrun_s)
            )
        return observations

    assert run_with_jumps(jumps=True) == run_with_jumps(jumps=False)


# ---------------------------------------------------------------------------
# 10. metadata compatibility (observations feed TraceMetadata directly)
# ---------------------------------------------------------------------------


def test_observation_feeds_trace_metadata() -> None:
    clock = make_clock()
    sched = make_scheduler(clock=clock, target_interval_s=0.25)
    sched.start()
    assert sched.wait_for_next() is True
    sched.sweep_started()
    clock.advance_monotonic(50_000_000)
    first = sched.sweep_finished()
    meta0 = build_metadata(clock, first, trace_index=0)
    assert meta0.target_interval_s == 0.25
    assert meta0.actual_interval_s is None  # first trace: nullable by contract
    assert meta0.schedule_error_s is None

    assert sched.wait_for_next() is True
    sched.sweep_started()
    clock.advance_monotonic(50_000_000)
    second = sched.sweep_finished()
    meta1 = build_metadata(clock, second, trace_index=1)
    assert meta1.actual_interval_s == second.actual_interval_s
    assert meta1.schedule_error_s == second.schedule_error_s
    assert meta1.target_interval_s == second.target_interval_s
    assert meta1.sweep_started_monotonic_ns == second.sweep_started_monotonic_ns
    assert meta1.sweep_finished_monotonic_ns == second.sweep_finished_monotonic_ns
    assert second.actual_interval_s == 0.25
    assert second.schedule_error_s == 0.0


# ---------------------------------------------------------------------------
# 11. post-review hardening (ISSUE-016 review report §10: P3-1 / P3-2)
# ---------------------------------------------------------------------------


def test_late_wait_for_next_after_reanchor_waits_for_new_deadline() -> None:
    """P3-1: a late ``wait_for_next`` must re-check the *current* deadline
    and clock before declaring a sweep due.

    Models the pause+resume re-anchor race deterministically: the deadline
    has already been re-anchored into the future and the first clock read of
    ``wait_for_next`` is stale (overdue).  The scheduler must not return
    ``True`` early — it must recompute against the new anchor and wait for
    the remaining time.
    """
    clock = ScriptedClock(
        [
            0,  # start() anchor
            0,  # sweep_started()
            2_000_000_000,  # sweep_finished(): duration 2s, deadline -> 1s
            3_000_000_000,  # resume(): re-anchor -> deadline 4s
            5_000_000_000,  # wait_for_next() first read: stale (overdue)
            3_000_000_000,  # wait_for_next() re-check read: current time
            3_000_000_000,  # loop read after the re-check
        ]
    )
    waiter = RecordingWaiter(clock)
    sched = make_scheduler(clock=clock, waiter=waiter)
    sched.start()
    sched.sweep_started()
    sched.sweep_finished()
    sched.pause()
    sched.resume()  # new anchor: deadline = 3s + 1s = 4s

    assert sched.wait_for_next() is True
    # The re-anchored deadline is 4s and current time is 3s: the scheduler
    # must wait exactly the remaining 1s instead of returning immediately.
    assert waiter.waited_ns == [1_000_000_000]
    assert waiter.total_advanced_ns == 1_000_000_000


def test_cross_trace_clock_rollback_rejected() -> None:
    """P3-2: a monotonic rollback *between* sweeps is rejected structurally
    (symmetric with the in-sweep rollback check), never producing a negative
    ``actual_interval_s`` observation."""
    clock = ScriptedClock(
        [
            0,  # start() anchor
            1_000,  # sweep_started() trace 0
            2_000,  # sweep_finished() trace 0
            500,  # sweep_started() trace 1: start < previous start (rollback)
            2_500,  # sweep_finished() trace 1
        ]
    )
    sched = make_scheduler(clock=clock)
    sched.start()
    sched.sweep_started()
    sched.sweep_finished()
    sched.sweep_started()
    with pytest.raises(SchedulerStateError) as exc:
        sched.sweep_finished()
    assert exc.value.context["operation"] == "sweep_finished"
    assert "backwards between sweeps" in str(exc.value)
    # fail-closed: nothing was advanced, the in-flight sweep is still recorded
    # as unfinished and the caller can retry after fixing the clock source.
    assert sched.state is SchedulerState.RUNNING
