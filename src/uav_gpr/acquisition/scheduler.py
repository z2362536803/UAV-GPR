"""Monotonic clock acquisition scheduler (ISSUE-016).

Pure-logic scheduler that paces sweeps against an absolute monotonic
deadline chain (docs/ACQUISITION.md section 7):

- ``MonotonicAcquisitionScheduler`` anchors the schedule at ``start()`` and
  advances ``deadline += interval_ns`` once per completed sweep, so the
  k-th sweep is always due at ``anchor + (k-1)*interval`` — structurally
  free of cumulative drift, independent of sweep durations, caller delays,
  overruns and pauses.
- An injectable ``Waiter`` provides interruptible waiting (the production
  default ``EventWaiter`` uses a ``threading.Event`` signal; this module
  never creates a thread).  ``pause()``/``cancel()`` wake an in-flight
  wait; ``resume()`` re-anchors so paused time is never compensated
  (no burst, no accumulated debt).
- Each completed sweep yields a ``ScheduleObservation`` (target/actual
  interval, schedule error, overrun, monotonic times) whose numeric
  contract matches ``TraceMetadata``'s scheduling fields, so observations
  feed metadata construction without ever reading the wall clock: UTC
  jumps cannot affect the schedule.

The scheduler never touches a backend, storage, network or GNSS, and never
decides a minimum hardware interval (that budget belongs to ISSUE-017 /
performance baselines, ACQUISITION.md section 7).
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from uav_gpr.core.enums import StableStrEnum
from uav_gpr.core.errors import DomainError, ErrorCode, JsonValue
from uav_gpr.core.timeutil import Clock, MonotonicNs, SystemClock

_NANOSECONDS_PER_SECOND = 1_000_000_000


class SchedulerState(StableStrEnum):
    """Lifecycle states of a :class:`MonotonicAcquisitionScheduler`."""

    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    CANCELLED = "cancelled"


@runtime_checkable
class Waiter(Protocol):
    """Injectable interruptible wait primitive (integer nanoseconds).

    ``wait(timeout_ns)`` blocks up to ``timeout_ns`` nanoseconds and returns
    ``True`` when :meth:`wake` interrupted it early, ``False`` when the full
    timeout elapsed.  The integer-nanosecond contract keeps virtual-time
    tests exact (no float rounding drift over tens of thousands of cycles).
    """

    def wait(self, timeout_ns: int) -> bool:
        """Block up to ``timeout_ns`` ns; ``True`` if woken early."""
        ...

    def wake(self) -> None:
        """Interrupt any in-flight :meth:`wait`; idempotent no-op otherwise."""
        ...


class EventWaiter:
    """Production :class:`Waiter` backed by a ``threading.Event``.

    Creates no thread: the caller's worker thread blocks in ``wait`` while
    any thread (e.g. the controller's pause/cancel path) may call ``wake``.
    A wake that races with a wait timeout is harmless — the scheduler loop
    re-checks its authoritative state after every wake/timeout.
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def wait(self, timeout_ns: int) -> bool:
        woke = self._event.wait(timeout_ns / _NANOSECONDS_PER_SECOND)
        self._event.clear()
        return woke

    def wake(self) -> None:
        self._event.set()


class SchedulerError(DomainError):
    """Scheduler failure: ``DomainError`` with a stable reason.

    Mirrors the ISSUE-015 ``BackendError`` pattern: business logic branches
    on ``code`` plus ``context["reason"]`` (the core ``ErrorCode`` enum is
    read-only, so scheduler faults reuse ``INVALID_ARGUMENT`` with a stable
    reason discriminator).
    """

    _reason: str = "scheduler_error"

    def __init__(self, message: str, **context: JsonValue) -> None:
        super().__init__(
            ErrorCode.INVALID_ARGUMENT,
            message,
            {"reason": self._reason, **context},
        )

    @property
    def reason(self) -> str:
        return self._reason


class SchedulerStateError(SchedulerError):
    """Illegal lifecycle transition or a busy (serialized) call."""

    _reason = "illegal_state"


@dataclass(frozen=True, slots=True)
class ScheduleObservation:
    """Per-sweep scheduling observation; feeds ``TraceMetadata`` fields.

    ``actual_interval_s`` / ``schedule_error_s`` are ``None`` for the first
    sweep (no previous sweep), matching the ``TraceMetadata`` first-trace
    contract; every later sweep carries both.  ``overrun_s`` is the amount
    by which the sweep duration exceeded the target interval (0.0 when it
    did not).  All times are monotonic nanoseconds; UTC is never read.
    """

    target_interval_s: float
    actual_interval_s: float | None
    schedule_error_s: float | None
    overrun_s: float
    sweep_started_monotonic_ns: MonotonicNs
    sweep_finished_monotonic_ns: MonotonicNs
    deadline_monotonic_ns: MonotonicNs

    @property
    def sweep_duration_s(self) -> float:
        """Duration of the sweep in seconds (monotonic, non-negative)."""
        return (
            self.sweep_finished_monotonic_ns.ns - self.sweep_started_monotonic_ns.ns
        ) / _NANOSECONDS_PER_SECOND


class MonotonicAcquisitionScheduler:
    """Pure-logic absolute-deadline scheduler (no threads, no I/O).

    Lifecycle::

        IDLE --start()--> RUNNING --pause()--> PAUSED --resume()--> RUNNING
        RUNNING/PAUSED --cancel()--> CANCELLED (terminal, idempotent)

    Scheduling loop, driven by the caller on its own thread (ISSUE-017)::

        scheduler.start()
        while scheduler.wait_for_next():        # True when a sweep is due now
            scheduler.sweep_started()           # records start + deadline
            sweep = backend.acquire()           # outside this module
            obs = scheduler.sweep_finished()    # observation for metadata

    Deadline chain: ``start()`` anchors at ``now`` (first sweep due
    immediately); every ``sweep_finished()`` advances the deadline by
    exactly one interval, so the k-th sweep is due at
    ``anchor + (k-1)*interval_ns`` regardless of durations, overruns,
    caller delays or pauses — structurally no cumulative drift.  An
    overrun leaves the next wait immediately due (never compensated);
    ``resume()`` re-anchors so paused time is never made up.

    State/deadline mutations are serialized by a lock; the lock is never
    held while the waiter blocks, so ``pause()``/``cancel()`` from another
    thread always interrupt an in-flight wait.
    """

    def __init__(
        self,
        *,
        target_interval_s: float,
        clock: Clock | None = None,
        waiter: Waiter | None = None,
    ) -> None:
        if isinstance(target_interval_s, bool) or not isinstance(
            target_interval_s, float
        ):
            raise TypeError(
                f"target_interval_s must be a float, got "
                f"{type(target_interval_s).__name__}"
            )
        if not math.isfinite(target_interval_s) or target_interval_s <= 0.0:
            raise ValueError("target_interval_s must be positive and finite")
        interval_ns = round(target_interval_s * _NANOSECONDS_PER_SECOND)
        if interval_ns < 1:
            raise ValueError(
                "target_interval_s is below the scheduling quantum (0.5 ns)"
            )
        if clock is not None and not isinstance(clock, Clock):
            raise TypeError(
                f"clock must implement the Clock protocol, got {type(clock).__name__}"
            )
        if waiter is not None and not isinstance(waiter, Waiter):
            raise TypeError(
                f"waiter must implement the Waiter protocol, got "
                f"{type(waiter).__name__}"
            )
        self._target_s = target_interval_s
        self._interval_ns = interval_ns
        self._clock = clock if clock is not None else SystemClock()
        self._waiter = waiter if waiter is not None else EventWaiter()
        self._lock = threading.Lock()
        self._state = SchedulerState.IDLE
        self._deadline: MonotonicNs | None = None
        self._prev_start: MonotonicNs | None = None
        self._in_flight = False
        self._current_start: MonotonicNs | None = None
        self._current_deadline: MonotonicNs | None = None

    @property
    def state(self) -> SchedulerState:
        with self._lock:
            return self._state

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Anchor the schedule at the current monotonic instant (IDLE only).

        The first sweep is due immediately (``deadline == anchor``); each
        completed sweep advances the absolute deadline by one interval.
        """
        with self._lock:
            if self._state is not SchedulerState.IDLE:
                raise SchedulerStateError(
                    "start requires an idle scheduler",
                    operation="start",
                    state=self._state.value,
                    allowed_states=[SchedulerState.IDLE.value],
                )
            now = self._clock.monotonic_ns()
            self._state = SchedulerState.RUNNING
            self._deadline = now
            self._prev_start = None
            self._in_flight = False

    def pause(self) -> None:
        """Pause scheduling; interrupts any in-flight wait (idempotent).

        An in-flight sweep keeps its right to finish and be recorded; the
        next ``wait_for_next`` returns ``False``.  Paused time is never
        compensated: :meth:`resume` re-anchors.
        """
        with self._lock:
            if self._state is SchedulerState.IDLE:
                raise SchedulerStateError(
                    "pause requires a started scheduler",
                    operation="pause",
                    state=self._state.value,
                    allowed_states=[
                        SchedulerState.RUNNING.value,
                        SchedulerState.PAUSED.value,
                        SchedulerState.CANCELLED.value,
                    ],
                )
            if self._state is SchedulerState.RUNNING:
                self._state = SchedulerState.PAUSED
        self._waiter.wake()

    def resume(self) -> None:
        """Resume with a fresh anchor: ``deadline = now + interval``.

        The next sweep is due exactly one interval after resume — no burst,
        no compensation of the paused period.  Idempotent no-op when already
        running (never re-anchors a live schedule); rejected after cancel
        (terminal).
        """
        with self._lock:
            if self._state is SchedulerState.PAUSED:
                now = self._clock.monotonic_ns()
                self._deadline = MonotonicNs(now.ns + self._interval_ns)
                self._state = SchedulerState.RUNNING
            elif self._state is SchedulerState.RUNNING:
                return
            else:
                raise SchedulerStateError(
                    "resume requires a paused (or running) scheduler",
                    operation="resume",
                    state=self._state.value,
                    allowed_states=[
                        SchedulerState.PAUSED.value,
                        SchedulerState.RUNNING.value,
                    ],
                )

    def cancel(self) -> None:
        """Cancel the schedule permanently (any state, idempotent).

        Interrupts any in-flight wait: ``wait_for_next`` returns ``False``
        immediately.  An in-flight sweep may still finish and be recorded
        via :meth:`sweep_finished`.
        """
        with self._lock:
            self._state = SchedulerState.CANCELLED
        self._waiter.wake()

    # -- scheduling ---------------------------------------------------------

    def wait_for_next(self) -> bool:
        """Wait until the next absolute deadline; ``True`` when due now.

        Returns ``False`` immediately when paused or cancelled.  The wait is
        interruptible: ``pause()``/``cancel()`` wake it from another thread,
        and spurious wakes are absorbed by re-checking state and the
        remaining time.  Rejected while a sweep is in flight (single sweep
        serialization, ``busy=True``) and before :meth:`start`.
        """
        while True:
            with self._lock:
                if self._state is SchedulerState.IDLE:
                    raise SchedulerStateError(
                        "wait_for_next requires a started scheduler",
                        operation="wait_for_next",
                        state=self._state.value,
                        allowed_states=[
                            SchedulerState.RUNNING.value,
                            SchedulerState.PAUSED.value,
                            SchedulerState.CANCELLED.value,
                        ],
                    )
                if self._state is not SchedulerState.RUNNING:
                    return False
                if self._in_flight:
                    raise SchedulerStateError(
                        "wait_for_next is not allowed while a sweep is in flight",
                        operation="wait_for_next",
                        state=self._state.value,
                        busy=True,
                    )
                deadline = self._deadline
                assert deadline is not None  # RUNNING implies started
                remaining_ns = deadline.ns - self._clock.monotonic_ns().ns
            if remaining_ns <= 0:
                with self._lock:
                    if self._state is SchedulerState.RUNNING:
                        # P3-1 hardening: re-check against the *current*
                        # deadline and clock.  A pause+resume re-anchor (or
                        # any deadline mutation) between the first clock read
                        # and this check must not let a late caller start a
                        # sweep early; wait again when the new anchor is
                        # still in the future.
                        deadline = self._deadline
                        assert deadline is not None  # RUNNING implies started
                        remaining_ns = deadline.ns - self._clock.monotonic_ns().ns
                        if remaining_ns <= 0:
                            return True
                continue
            if self._waiter.wait(remaining_ns):
                continue  # woken (pause/cancel/spurious): re-check state
            with self._lock:
                if self._state is SchedulerState.RUNNING:
                    return True

    def sweep_started(self) -> None:
        """Record that a sweep begins now (RUNNING only).

        Reads the monotonic clock for the sweep start instant and captures
        the absolute deadline this sweep is due at.  A second start while a
        sweep is in flight is rejected (single sweep serialization).
        """
        with self._lock:
            if self._state is not SchedulerState.RUNNING:
                raise SchedulerStateError(
                    "sweep_started requires a running scheduler",
                    operation="sweep_started",
                    state=self._state.value,
                    allowed_states=[SchedulerState.RUNNING.value],
                )
            if self._in_flight:
                raise SchedulerStateError(
                    "a sweep is already in flight (single sweep serialization)",
                    operation="sweep_started",
                    state=self._state.value,
                    busy=True,
                )
            now = self._clock.monotonic_ns()
            assert self._deadline is not None  # RUNNING implies started
            self._in_flight = True
            self._current_start = now
            self._current_deadline = self._deadline

    def sweep_finished(self) -> ScheduleObservation:
        """Complete the in-flight sweep and return its observation.

        Works in RUNNING, PAUSED and CANCELLED so an in-flight sweep that
        finishes after pause/cancel is still recorded honestly.  Advances
        the absolute deadline by exactly one interval; the interval chain
        (``actual_interval_s``) references the previous sweep start.
        """
        with self._lock:
            if not self._in_flight:
                raise SchedulerStateError(
                    "sweep_finished requires an in-flight sweep",
                    operation="sweep_finished",
                    state=self._state.value,
                )
            start = self._current_start
            deadline = self._current_deadline
            assert start is not None and deadline is not None  # in flight
            finish = self._clock.monotonic_ns()
            duration_ns = finish.ns - start.ns
            if duration_ns < 0:
                raise SchedulerStateError(
                    "monotonic clock went backwards during a sweep",
                    operation="sweep_finished",
                    state=self._state.value,
                )
            previous = self._prev_start
            if previous is None:
                actual_interval: float | None = None
                schedule_error: float | None = None
            else:
                if start.ns < previous.ns:
                    raise SchedulerStateError(
                        "monotonic clock went backwards between sweeps",
                        operation="sweep_finished",
                        state=self._state.value,
                    )
                actual_interval = (
                    start.ns - previous.ns
                ) / _NANOSECONDS_PER_SECOND
                schedule_error = actual_interval - self._target_s
            overrun = max(
                0.0, (duration_ns - self._interval_ns) / _NANOSECONDS_PER_SECOND
            )
            assert self._deadline is not None  # RUNNING implies started
            self._prev_start = start
            self._deadline = MonotonicNs(self._deadline.ns + self._interval_ns)
            self._in_flight = False
            self._current_start = None
            self._current_deadline = None
        return ScheduleObservation(
            target_interval_s=self._target_s,
            actual_interval_s=actual_interval,
            schedule_error_s=schedule_error,
            overrun_s=overrun,
            sweep_started_monotonic_ns=start,
            sweep_finished_monotonic_ns=finish,
            deadline_monotonic_ns=deadline,
        )
