"""Acquisition controller with a central pause/stop state machine (ISSUE-017).

One UI-free controller owns the acquisition worker (docs/ACQUISITION.md
sections 8/9, ARCHITECTURE.md sections 5/6):

- ``AcquisitionController`` concentrates the mission state machine
  ``IDLE -> PREPARING -> READY -> RUNNING <-> PAUSED -> STOPPING -> STOPPED``
  plus ``FAILED`` (structured error) and ``CLOSED`` (resources released) and
  is the only owner of the backend worker thread.  It orchestrates
  configure/scheduler/acquire and exposes ``start/pause/resume/stop/
  emergency_stop/close``; every command is deterministic from every state
  (illegal commands raise ``ControllerStateError``, repeated commands are
  idempotent no-ops).
- ``BoundedSweepBuffer`` is the bounded consumer interface for completed
  sweeps.  ``BackpressurePolicy.BLOCK`` throttles the worker on a full
  buffer (slow consumer back-pressure) and guarantees ``stop`` drains
  completed sweeps; ``DROP_NEWEST`` discards and counts instead.  The queue
  can never exceed its capacity; the abort path is Condition-driven, so
  ``close()`` never leaves a worker behind (zero polling, no fixed sleeps).
- Pause/stop take effect at the *safe boundary* via the ISSUE-016 scheduler:
  an in-flight sweep still finishes and is published (``sweep_finished`` is
  legal in PAUSED/CANCELLED), while ``wait_for_next``/``sweep_started``
  refuse to start a new sweep.  ``emergency_stop`` additionally interrupts
  hardware I/O with ``backend.cancel()``; an interrupted sweep is never
  published (fail-closed).  Errors transition to ``FAILED`` with a
  structured ``ControllerFailure`` and release resources in order: stop
  scheduling -> mark failed -> close the backend -> exit the worker.
- ``connection_generation`` is exposed and a ``reconnect_hook`` may
  re-establish a disconnected backend (the hook must leave it CONFIGURED
  with a changed generation); the controller re-anchors the schedule at the
  reconnect instant.  Concrete USB reconnect logic is out of scope.

Threading: the worker thread is the only caller of the backend lifecycle
and scheduler sweep methods; command threads may only call the
cross-thread-safe interruption paths (``backend.cancel()``,
``scheduler.pause/resume/cancel()``, buffer ``abort()``, event sets).
All worker blocking points are interruptible, so ``close()`` joins the
worker and never leaks it.  Backends must honour ``cancel()``/``close()``
(ISSUE-015 contract).

All failures are core structured errors (``DomainError`` with
``ErrorCode.INVALID_ARGUMENT`` plus a stable ``reason`` context key and
typed subclasses), mirroring ``BackendError``/``SchedulerError``.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from uav_gpr.acquisition.backend import (
    AcquisitionBackend,
    AppliedConfig,
    BackendCancelledError,
    BackendClosedError,
    BackendDisconnectedError,
    BackendState,
    Capabilities,
)
from uav_gpr.acquisition.scheduler import (
    MonotonicAcquisitionScheduler,
    SchedulerState,
    SchedulerStateError,
    Waiter,
)
from uav_gpr.core import FrequencySweep, MissionConfig
from uav_gpr.core.enums import StableStrEnum
from uav_gpr.core.errors import DomainError, ErrorCode, JsonValue
from uav_gpr.core.timeutil import Clock


class ControllerState(StableStrEnum):
    """Mission states of an :class:`AcquisitionController`.

    ``IDLE -> PREPARING -> READY -> RUNNING <-> PAUSED -> STOPPING ->
    STOPPED``; every non-terminal state may transition to ``FAILED``
    (structured error) or ``CLOSED`` (``close()`` releases resources).
    Aligned with ARCHITECTURE.md section 5 (FINALIZING/COMPLETED belong to
    the ISSUE-043+ mission layer and are not controller states).
    """

    IDLE = "idle"
    PREPARING = "preparing"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"
    CLOSED = "closed"


class BackpressurePolicy(StableStrEnum):
    """Behaviour when the bounded publish buffer is full (slow consumer)."""

    BLOCK = "block"
    """The worker waits for room (natural acquisition throttling).  With
    this policy ``stop`` drains every completed sweep before finishing."""

    DROP_NEWEST = "drop_newest"
    """Discard the newest sweep and count it in ``dropped_sweeps``; the
    worker never blocks.  Guaranteed drain requires ``BLOCK``."""


class StopReason(StableStrEnum):
    """Why the controller ended in ``STOPPED`` (AGENTS.md section 4)."""

    USER_STOP = "user_stop"
    EMERGENCY = "emergency"


class ControllerError(DomainError):
    """Acquisition controller failure: ``DomainError`` with a stable reason.

    Mirrors the ``BackendError``/``SchedulerError`` pattern: business logic
    branches on ``code`` plus ``context["reason"]`` (the core ``ErrorCode``
    enum is read-only, so controller faults reuse ``INVALID_ARGUMENT`` with
    a stable reason discriminator).
    """

    _reason: str = "controller_error"

    def __init__(self, message: str, **context: JsonValue) -> None:
        super().__init__(
            ErrorCode.INVALID_ARGUMENT,
            message,
            {"reason": self._reason, **context},
        )

    @property
    def reason(self) -> str:
        return self._reason


class ControllerStateError(ControllerError):
    """An illegal command for the current state (deterministic rejection)."""

    _reason = "illegal_state"


class ControllerFailure(ControllerError):
    """Terminal acquisition failure (``FAILED``), wrapping the cause.

    The original exception is described in ``context["cause_type"]`` and
    ``context["cause_message"]`` so business logic can classify without
    losing the structured error chain.
    """

    _reason = "controller_failure"


def _as_failure(exc: Exception) -> ControllerFailure:
    if isinstance(exc, ControllerFailure):
        return exc
    return ControllerFailure(
        "acquisition controller failed",
        cause_type=type(exc).__name__,
        cause_message=str(exc) or type(exc).__name__,
    )


def _require_capacity(capacity: int) -> None:
    if isinstance(capacity, bool) or not isinstance(capacity, int):
        raise TypeError(f"capacity must be an int, got {type(capacity).__name__}")
    if capacity < 1:
        raise ValueError(f"capacity must be at least 1, got {capacity}")


def _require_timeout(timeout_s: float | None) -> None:
    if timeout_s is None:
        return
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, float):
        raise TypeError(
            f"timeout_s must be a float or None, got {type(timeout_s).__name__}"
        )
    if not math.isfinite(timeout_s) or timeout_s < 0.0:
        raise ValueError("timeout_s must be a finite non-negative float or None")


class BoundedSweepBuffer:
    """Thread-safe bounded FIFO of completed sweeps (consumer interface).

    Producers block on a full buffer (``put``, BLOCK policy) or drop
    (``try_put``, DROP_NEWEST); ``abort()`` wakes every blocked producer so
    ``close()`` never leaves a worker behind.  All waits are Condition
    driven (zero polling); consumers ``get`` with an optional timeout.
    """

    def __init__(self, capacity: int) -> None:
        _require_capacity(capacity)
        self._capacity = capacity
        self._deque: deque[FrequencySweep] = deque()
        self._cond = threading.Condition()
        self._aborted = False
        self._published = 0
        self._dropped = 0

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def size(self) -> int:
        with self._cond:
            return len(self._deque)

    @property
    def published(self) -> int:
        """Sweeps successfully enqueued (never exceeds the capacity)."""
        with self._cond:
            return self._published

    @property
    def dropped(self) -> int:
        """Sweeps discarded: DROP_NEWEST on a full buffer, or aborted puts."""
        with self._cond:
            return self._dropped

    def put(self, sweep: FrequencySweep) -> bool:
        """Block until room is available; ``True`` when enqueued.

        Returns ``False`` (and counts a drop) only after :meth:`abort`,
        which is how ``close()`` releases a worker blocked on a full buffer.
        """
        with self._cond:
            while len(self._deque) >= self._capacity and not self._aborted:
                self._cond.wait()
            if self._aborted:
                self._dropped += 1
                return False
            self._deque.append(sweep)
            self._published += 1
            self._cond.notify()
            return True

    def try_put(self, sweep: FrequencySweep) -> bool:
        """Non-blocking enqueue; ``False`` (counted drop) when full/aborted."""
        with self._cond:
            if self._aborted or len(self._deque) >= self._capacity:
                self._dropped += 1
                return False
            self._deque.append(sweep)
            self._published += 1
            self._cond.notify()
            return True

    def get(self, timeout_s: float | None = None) -> FrequencySweep | None:
        """Block up to ``timeout_s`` for the next sweep (None blocks).

        Returns ``None`` on timeout, or after :meth:`abort` once the buffer
        is empty (the terminal signal for consumers).
        """
        _require_timeout(timeout_s)
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        with self._cond:
            while not self._deque:
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return None
                    self._cond.wait(remaining)
                elif self._aborted:
                    return None
                else:
                    self._cond.wait()
            sweep = self._deque.popleft()
            self._cond.notify()  # room available: wake a blocked producer
            return sweep

    def abort(self) -> None:
        """Wake every blocked producer; further puts/try_puts are dropped."""
        with self._cond:
            self._aborted = True
            self._cond.notify_all()


@dataclass(frozen=True, slots=True)
class ControllerMetrics:
    """Snapshot of the controller's observable state (back-pressure metrics)."""

    state: ControllerState
    published_sweeps: int
    dropped_sweeps: int
    queue_size: int
    capacity: int
    connection_generation: int
    stop_reason: StopReason | None


class AcquisitionController:
    """UI-free controller that owns the single backend worker.

    Lifecycle (see :class:`ControllerState`): ``configure`` opens and
    configures the backend synchronously (PREPARING observable from other
    threads), ``start`` spawns the worker, pause/resume/stop/emergency_stop
    steer the schedule, ``close`` releases everything and joins the worker.

    The worker loop is the only caller of backend lifecycle/sweep methods
    and scheduler sweep methods.  Command threads only use the
    cross-thread-safe interruption paths, so ``close()`` always terminates
    the worker (backend honouring cancel/close is the ISSUE-015 contract).
    """

    def __init__(
        self,
        backend: AcquisitionBackend,
        *,
        capacity: int = 16,
        backpressure: BackpressurePolicy = BackpressurePolicy.BLOCK,
        clock: Clock | None = None,
        waiter: Waiter | None = None,
        reconnect_hook: Callable[[], None] | None = None,
    ) -> None:
        if not isinstance(backend, AcquisitionBackend):
            raise TypeError(
                f"backend must be an AcquisitionBackend, got {type(backend).__name__}"
            )
        if not isinstance(backpressure, BackpressurePolicy):
            raise TypeError(
                f"backpressure must be a BackpressurePolicy, got "
                f"{type(backpressure).__name__}"
            )
        _require_capacity(capacity)
        if clock is not None and not isinstance(clock, Clock):
            raise TypeError(
                f"clock must implement the Clock protocol, got {type(clock).__name__}"
            )
        if waiter is not None and not isinstance(waiter, Waiter):
            raise TypeError(
                f"waiter must implement the Waiter protocol, got {type(waiter).__name__}"
            )
        if reconnect_hook is not None and not callable(reconnect_hook):
            raise TypeError(
                f"reconnect_hook must be callable or None, got "
                f"{type(reconnect_hook).__name__}"
            )
        self._backend = backend
        self._backpressure = backpressure
        self._clock = clock
        self._waiter = waiter
        self._reconnect_hook = reconnect_hook
        self._lock = threading.Lock()
        self._state = ControllerState.IDLE
        self._closing = False
        self._stop_reason: StopReason | None = None
        self._error: ControllerFailure | None = None
        self._worker: threading.Thread | None = None
        self._scheduler: MonotonicAcquisitionScheduler | None = None
        self._target_interval_s: float | None = None
        self._command_event = threading.Event()
        self._terminal_event = threading.Event()
        self._capabilities: Capabilities | None = None
        self._applied: AppliedConfig | None = None
        self.sweeps = BoundedSweepBuffer(capacity)

    # -- observable state ---------------------------------------------------

    @property
    def state(self) -> ControllerState:
        with self._lock:
            return self._state

    @property
    def error(self) -> ControllerFailure | None:
        """The structured failure behind ``FAILED`` (None otherwise)."""
        with self._lock:
            return self._error

    @property
    def capabilities(self) -> Capabilities | None:
        with self._lock:
            return self._capabilities

    @property
    def applied_config(self) -> AppliedConfig | None:
        with self._lock:
            return self._applied

    @property
    def stop_reason(self) -> StopReason | None:
        with self._lock:
            return self._stop_reason

    @property
    def connection_generation(self) -> int:
        """The backend's device connection generation (0 before open)."""
        return self._backend.connection_generation

    def metrics(self) -> ControllerMetrics:
        return ControllerMetrics(
            state=self.state,
            published_sweeps=self.sweeps.published,
            dropped_sweeps=self.sweeps.dropped,
            queue_size=self.sweeps.size,
            capacity=self.sweeps.capacity,
            connection_generation=self.connection_generation,
            stop_reason=self.stop_reason,
        )

    def join(self, timeout_s: float | None = None) -> bool:
        """Wait for the worker to exit; ``True`` when it is gone (no worker
        ever started counts as joined)."""
        with self._lock:
            worker = self._worker
        if worker is None:
            return True
        worker.join(timeout_s)
        return not worker.is_alive()

    def wait_finished(self, timeout_s: float | None = None) -> bool:
        """Wait for a terminal state (STOPPED/FAILED/CLOSED)."""
        return self._terminal_event.wait(timeout_s)

    # -- lifecycle commands -------------------------------------------------

    def configure(self, config: MissionConfig) -> AppliedConfig:
        """Open and configure the backend (IDLE only, synchronous).

        ``PREPARING`` is observable from other threads while the device
        opens.  A configure failure transitions to ``FAILED`` (structured
        ``ControllerFailure``) and releases the backend; a concurrent
        ``close()`` aborts configure with a structured failure.
        """
        if not isinstance(config, MissionConfig):
            raise TypeError(
                f"config must be a MissionConfig, got {type(config).__name__}"
            )
        with self._lock:
            if self._state is not ControllerState.IDLE:
                raise ControllerStateError(
                    "configure requires an idle controller",
                    operation="configure",
                    state=self._state.value,
                    allowed_states=[ControllerState.IDLE.value],
                )
            if self._backend.state is not BackendState.CLOSED:
                raise ControllerStateError(
                    "configure requires a closed backend",
                    operation="configure",
                    state=self._state.value,
                    backend_state=self._backend.state.value,
                )
            self._state = ControllerState.PREPARING
            self._target_interval_s = config.target_interval_s
        scheduler: MonotonicAcquisitionScheduler | None = None
        try:
            scheduler = MonotonicAcquisitionScheduler(
                target_interval_s=config.target_interval_s,
                clock=self._clock,
                waiter=self._waiter,
            )
            with self._lock:
                self._scheduler = scheduler
            capabilities = self._backend.open()
            applied = self._backend.configure(config)
        except Exception as exc:
            failure = _as_failure(exc)
            with self._lock:
                closing = self._closing
                state = self._state
            if closing or state is ControllerState.CLOSED:
                # close() raced configure: the terminal state stays CLOSED
                # (deterministic per the plan section 5.2 command table; this
                # guard mirrors _fail's terminal guard below).  Only release
                # the backend and re-raise; never overwrite CLOSED/error.
                self._backend.close()
                raise failure from exc
            with self._lock:
                self._state = ControllerState.FAILED
                self._error = failure
                self._terminal_event.set()
            if scheduler is not None:
                scheduler.cancel()
            self._backend.close()
            raise failure from exc
        with self._lock:
            if self._closing:
                self._backend.close()
                raise ControllerFailure(
                    "controller closed during configure",
                    cause_type="ControllerClose",
                    cause_message="close() raced configure()",
                )
            self._state = ControllerState.READY
            self._capabilities = capabilities
            self._applied = applied
        return applied

    def start(self) -> None:
        """Begin acquisition (READY only); spawns the backend worker.

        Repeated ``start`` while RUNNING is an idempotent no-op.
        """
        with self._lock:
            if self._state is ControllerState.RUNNING:
                return
            if self._state is not ControllerState.READY:
                raise ControllerStateError(
                    "start requires a ready controller",
                    operation="start",
                    state=self._state.value,
                    allowed_states=[
                        ControllerState.READY.value,
                        ControllerState.RUNNING.value,
                    ],
                )
            self._state = ControllerState.RUNNING
            worker = threading.Thread(
                target=self._run, name="uav-gpr-acquisition-controller-worker"
            )
            self._worker = worker
        worker.start()

    def pause(self) -> None:
        """Stop starting new sweeps at the safe boundary (idempotent).

        An in-flight sweep finishes and is published; the next sweep only
        starts after :meth:`resume` (which re-anchors, no burst).
        """
        with self._lock:
            state = self._state
            if state in (
                ControllerState.PAUSED,
                ControllerState.STOPPING,
                ControllerState.STOPPED,
                ControllerState.FAILED,
            ):
                return  # repeated/terminal command: idempotent no-op
            if state is not ControllerState.RUNNING:
                raise ControllerStateError(
                    "pause requires a running controller",
                    operation="pause",
                    state=state.value,
                    allowed_states=[
                        ControllerState.RUNNING.value,
                        ControllerState.PAUSED.value,
                        ControllerState.STOPPING.value,
                        ControllerState.STOPPED.value,
                        ControllerState.FAILED.value,
                    ],
                )
            self._state = ControllerState.PAUSED
            scheduler = self._scheduler
        if scheduler is not None:
            try:
                scheduler.pause()
            except SchedulerStateError:
                # scheduler not started yet: the controller state is the
                # authoritative pause marker; the worker honours it at start
                pass

    def resume(self) -> None:
        """Resume from pause with a fresh schedule anchor (idempotent).

        Re-checks the device (fail-closed: a backend that is no longer
        CONFIGURED transitions to FAILED).
        """
        with self._lock:
            state = self._state
            if state is ControllerState.RUNNING:
                return
            if state is not ControllerState.PAUSED:
                raise ControllerStateError(
                    "resume requires a paused controller",
                    operation="resume",
                    state=state.value,
                    allowed_states=[
                        ControllerState.PAUSED.value,
                        ControllerState.RUNNING.value,
                    ],
                )
            if self._backend.state is not BackendState.CONFIGURED:
                need_fail = True
                scheduler = None
            else:
                self._state = ControllerState.RUNNING
                scheduler = self._scheduler
                need_fail = False
        if need_fail:
            self._fail(
                ControllerFailure(
                    "cannot resume: backend is not configured",
                    cause_type="BackendState",
                    cause_message=self._backend.state.value,
                )
            )
            return
        self._command_event.set()
        if scheduler is not None:
            try:
                scheduler.resume()
            except SchedulerStateError:
                # scheduler not started yet: resume is anchored by start()
                pass

    def stop(self) -> None:
        """Graceful stop: no new sweeps, drain completed sweeps.

        RUNNING/PAUSED transition to STOPPING (the worker finishes and
        publishes the in-flight sweep, then reaches STOPPED); READY stops
        immediately.  Repeated/terminal commands are idempotent no-ops.
        """
        with self._lock:
            state = self._state
            if state in (
                ControllerState.STOPPING,
                ControllerState.STOPPED,
                ControllerState.FAILED,
            ):
                return
            if state in (ControllerState.RUNNING, ControllerState.PAUSED):
                self._state = ControllerState.STOPPING
                self._stop_reason = StopReason.USER_STOP
                scheduler = self._scheduler
            elif state is ControllerState.READY:
                self._state = ControllerState.STOPPED
                self._stop_reason = StopReason.USER_STOP
                self._terminal_event.set()
                scheduler = self._scheduler
            else:
                raise ControllerStateError(
                    "stop requires an active controller",
                    operation="stop",
                    state=state.value,
                    allowed_states=[
                        ControllerState.READY.value,
                        ControllerState.RUNNING.value,
                        ControllerState.PAUSED.value,
                        ControllerState.STOPPING.value,
                        ControllerState.STOPPED.value,
                        ControllerState.FAILED.value,
                    ],
                )
        self._command_event.set()
        if scheduler is not None:
            scheduler.cancel()
        if state is ControllerState.READY:
            self._backend.close()

    def emergency_stop(self) -> None:
        """Stop hardware I/O first, preserve completed sweeps only.

        Interrupts an in-flight acquire via ``backend.cancel()``; the
        interrupted sweep is never published (fail-closed, no promise of an
        uncompleted sweep).  Already-published sweeps remain available to
        consumers.  May upgrade an in-flight STOPPING to EMERGENCY.
        """
        with self._lock:
            state = self._state
            if state in (ControllerState.STOPPED, ControllerState.FAILED):
                return
            if state in (
                ControllerState.READY,
                ControllerState.RUNNING,
                ControllerState.PAUSED,
                ControllerState.STOPPING,
            ):
                if state is ControllerState.READY:
                    self._state = ControllerState.STOPPED
                    self._terminal_event.set()
                else:
                    self._state = ControllerState.STOPPING
                self._stop_reason = StopReason.EMERGENCY
                scheduler = self._scheduler
            else:
                raise ControllerStateError(
                    "emergency_stop requires an active controller",
                    operation="emergency_stop",
                    state=state.value,
                    allowed_states=[
                        ControllerState.READY.value,
                        ControllerState.RUNNING.value,
                        ControllerState.PAUSED.value,
                        ControllerState.STOPPING.value,
                        ControllerState.STOPPED.value,
                        ControllerState.FAILED.value,
                    ],
                )
        self._command_event.set()
        if scheduler is not None:
            scheduler.cancel()
        self._backend.cancel()
        if state is ControllerState.READY:
            self._backend.close()

    def close(self) -> None:
        """Release everything and join the worker (idempotent).

        Order (ARCHITECTURE.md section 6): no new sweeps (closing flag +
        ``scheduler.cancel``) -> interrupt in-flight I/O
        (``backend.cancel``) -> unblock publish (``sweeps.abort``) -> wake
        the worker -> join it -> CLOSED.  After close the controller is
        terminal: every command is rejected deterministically.
        """
        with self._lock:
            state = self._state
            if state is ControllerState.CLOSED:
                return
            self._closing = True
            worker = (
                self._worker
                if state
                in (
                    ControllerState.RUNNING,
                    ControllerState.PAUSED,
                    ControllerState.STOPPING,
                )
                else None
            )
            scheduler = self._scheduler
        self._command_event.set()
        if scheduler is not None:
            scheduler.cancel()
        self._backend.cancel()
        self.sweeps.abort()
        if worker is not None:
            worker.join()
        with self._lock:
            if self._state is not ControllerState.CLOSED:
                self._state = ControllerState.CLOSED
            self._terminal_event.set()
        self._backend.close()

    # -- worker -------------------------------------------------------------

    def _run(self) -> None:
        scheduler = self._require_scheduler()
        try:
            scheduler.start()
        except SchedulerStateError as exc:
            # a stop/emergency/close raced the schedule start: honour it
            with self._lock:
                state = self._state
                closing = self._closing
            if closing:
                self._finish_closed()
            elif state is ControllerState.STOPPING:
                self._finish_stopped()
            else:
                self._fail(exc)
            return
        self._loop()

    def _loop(self) -> None:
        while True:
            with self._lock:
                state = self._state
                closing = self._closing
            if state in (
                ControllerState.STOPPED,
                ControllerState.FAILED,
                ControllerState.CLOSED,
            ):
                return
            if closing:
                self._finish_closed()
                return
            if state is ControllerState.RUNNING:
                self._tick()
            elif state is ControllerState.PAUSED:
                self._command_event.wait()
            elif state is ControllerState.STOPPING:
                self._finish_stopped()
                return
            else:
                return  # IDLE/PREPARING/READY are unreachable from the worker

    def _tick(self) -> None:
        scheduler = self._require_scheduler()
        try:
            due = scheduler.wait_for_next()
        except SchedulerStateError as exc:
            if scheduler.state is SchedulerState.RUNNING:
                self._fail(exc)  # unexpected (e.g. busy): fail closed
            return  # paused/cancelled raced: re-check at the loop top
        if not due:
            return
        try:
            scheduler.sweep_started()
        except SchedulerStateError:
            return  # safe boundary: pause/stop raced before this sweep began
        try:
            sweep = self._backend.acquire()
        except BackendDisconnectedError as exc:
            self._handle_disconnect(exc)
            return
        except BackendCancelledError as exc:
            with self._lock:
                closing = self._closing
                reason = self._stop_reason
            if closing or reason is StopReason.EMERGENCY:
                return  # loop top decides (closing / STOPPING)
            self._fail(exc)  # unexpected cancel: fail closed
            return
        except BackendClosedError as exc:
            if self._is_closing():
                return
            self._fail(exc)
            return
        except Exception as exc:
            self._fail(exc)
            return
        try:
            scheduler.sweep_finished()
        except SchedulerStateError as exc:
            self._fail(exc)
            return
        self._publish(sweep)

    def _publish(self, sweep: FrequencySweep) -> None:
        if self._backpressure is BackpressurePolicy.DROP_NEWEST:
            self.sweeps.try_put(sweep)  # drop counted inside the buffer
            return
        self.sweeps.put(sweep)  # BLOCK: throttles on a slow consumer

    def _handle_disconnect(self, exc: BackendDisconnectedError) -> None:
        """Route a disconnect through the reconnect hook (worker thread)."""
        generation = self._backend.connection_generation
        hook = self._reconnect_hook
        if hook is None:
            self._fail(exc)
            return
        try:
            hook()
        except Exception as hook_exc:
            self._fail(
                ControllerFailure(
                    "reconnect hook failed",
                    cause_type=type(hook_exc).__name__,
                    cause_message=str(hook_exc) or type(hook_exc).__name__,
                )
            )
            return
        # NOTE (P3-03, ISSUE-019/023): ``connection_generation`` is a
        # *per-open-session* counter (backend.py: open() resets it to 1,
        # every simulated disconnect increments it), so a reconnect that
        # closes+reopens the backend legitimately observes a *changed* value
        # rather than an ever-increasing one.  The definitive generation
        # semantics for real USB reconnect must be recorded in docs/ADR when
        # ISSUE-019/023 implement the physical reconnect path.
        if (
            self._backend.state is not BackendState.CONFIGURED
            or self._backend.connection_generation == generation
        ):
            self._fail(
                ControllerFailure(
                    "reconnect hook did not re-establish the backend",
                    cause_type="ReconnectContract",
                    cause_message=(
                        f"generation unchanged ({generation}) or state "
                        f"{self._backend.state.value} not configured"
                    ),
                )
            )
            return
        # Re-anchor the schedule at the reconnect instant (fresh scheduler:
        # the ISSUE-016 scheduler has no abort API, and a reconnect is a new
        # anchor anyway, ACQUISITION.md section 9).  No fake wall clock.
        with self._lock:
            if self._closing:
                return
            target = self._target_interval_s
            if target is None:
                self._fail(
                    ControllerFailure(
                        "scheduler target interval missing",
                        cause_type="InternalError",
                        cause_message="configure() did not complete",
                    )
                )
                return
            scheduler = MonotonicAcquisitionScheduler(
                target_interval_s=target,
                clock=self._clock,
                waiter=self._waiter,
            )
            self._scheduler = scheduler
        try:
            scheduler.start()
        except SchedulerStateError as exc2:
            with self._lock:
                closing = self._closing
            if not closing:
                self._fail(exc2)
            return

    def _fail(self, exc: Exception) -> None:
        """Transition to FAILED and release resources in order.

        Order: stop scheduling -> mark FAILED (structured error) -> close
        the backend -> wake/exit the worker.  First failure wins.
        """
        failure = _as_failure(exc)
        with self._lock:
            if self._state in (
                ControllerState.STOPPED,
                ControllerState.FAILED,
                ControllerState.CLOSED,
            ):
                return
            self._state = ControllerState.FAILED
            self._error = failure
            self._terminal_event.set()
        scheduler = self._scheduler
        if scheduler is not None:
            scheduler.cancel()
        self._backend.close()
        self._command_event.set()

    def _finish_stopped(self) -> None:
        with self._lock:
            if self._state in (
                ControllerState.STOPPED,
                ControllerState.FAILED,
                ControllerState.CLOSED,
            ):
                return
            self._state = ControllerState.STOPPED
            if self._stop_reason is None:
                self._stop_reason = StopReason.USER_STOP
            self._terminal_event.set()
        self._backend.close()

    def _finish_closed(self) -> None:
        with self._lock:
            if self._state is ControllerState.CLOSED:
                return
            self._state = ControllerState.CLOSED
            self._terminal_event.set()
        self._backend.close()

    def _is_closing(self) -> bool:
        with self._lock:
            return self._closing

    def _require_scheduler(self) -> MonotonicAcquisitionScheduler:
        with self._lock:
            scheduler = self._scheduler
        if scheduler is None:
            raise ControllerFailure(
                "controller has no scheduler",
                cause_type="InternalError",
                cause_message="configure() did not complete",
            )
        return scheduler
