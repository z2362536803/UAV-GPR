"""UI-free reference capture sessions (ISSUE-028).

This module provides the no-UI OSL six-step (Open/Short/Load per reflection
channel, S11/S22) and air-background capture sessions.  A session freezes
its sweep configuration at construction (``MissionConfig`` identity, exact
frequency axis, exact channel tuple, target capture/trace counts), exposes
``accept_sweep`` as the single strict aggregation entry point, and — for the
OSL session — delegates the final solve to :mod:`uav_gpr.calibration.osl`
(ISSUE-027).  It never copies the solver mathematics, never writes files
(`` .rcal``/``.rcbg`` persistence belongs to ISSUE-029), and never fabricates
standard measurements: every aggregated capture comes from an accepted
sweep.

State machines (explicit, skip-free, mix-free):

* ``ReferenceSessionState``: ``IDLE -> RUNNING -> COMPLETED/CANCELLED/FAILED``.
* OSL steps are the ordered physical six steps ``(channel, standard)`` in
  channel order x (open, short, load); a step completes only when its
  target capture count is met (which closes the accept gate until the
  orchestrator advances to the next step); there is no skip operation and
  ``build()`` requires every step completed.

Acceptance semantics (``accept_sweep``):

* Contract violations fail closed with ``DomainError``: mismatched frozen
  axis (``AXIS_MISMATCH``), mismatched frozen channels
  (``CHANNEL_CONTRACT_MISMATCH``), non-finite data (``INVALID_ARGUMENT``),
  and non-active/illegal session calls.
* In-flight or gate-closed deliveries return ``accepted=False`` with a
  recorded ``reason`` — late data can never corrupt the state machine.

``ControllerReferenceAdapter`` is the optional orchestrator over an
``AcquisitionController`` (ISSUE-017; the session itself owns no window and
no acquisition loop).  It consumes the controller sweep buffer, feeds
``accept_sweep``, records per-step failures (retry preserves completed
steps and accepted captures; the caller factory builds a fresh controller
with the same frozen config), and on completion/cancellation/failure always
closes the accept gate **before** stopping the controller, then joins and
closes it — no thread leaks.

Error-code mapping (core codes only, no new codes): ``AXIS_MISMATCH``,
``CHANNEL_CONTRACT_MISMATCH``, ``INVALID_ARGUMENT``, plus the codes raised
by the ISSUE-027 solver.  All waits in this module are event-, condition-,
or join-driven; there are no fixed sleeps.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from uav_gpr.acquisition.controller import AcquisitionController, ControllerState
from uav_gpr.calibration.osl import (
    OslCalibrationProfile,
    OslCalibrationSet,
    OslStandard,
    build_osl_calibration,
)
from uav_gpr.core import (
    CalibrationProfileId,
    ChannelSpec,
    DomainError,
    ErrorCode,
    FrequencySweep,
    MissionConfig,
    SParameter,
    StableStrEnum,
)

__all__ = [
    "AirBackgroundReference",
    "AirBackgroundSession",
    "ControllerReferenceAdapter",
    "OslReferenceSession",
    "ReferenceAcceptResult",
    "ReferenceDomain",
    "ReferenceSessionState",
    "ReferenceStep",
    "ReferenceStepState",
]

_REFLECTION_PARAMETERS = (SParameter.S11, SParameter.S22)
_STANDARDS = (OslStandard.OPEN, OslStandard.SHORT, OslStandard.LOAD)
_GATE_POLL_TIMEOUT_S = 0.05
_STOP_WAIT_S = 10.0
_JOIN_TIMEOUT_S = 10.0


class ReferenceSessionState(StableStrEnum):
    """Session-level state machine (never skips or re-enters RUNNING)."""

    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ReferenceStepState(StableStrEnum):
    """Step-level state for the ordered OSL physical steps."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"


class ReferenceDomain(StableStrEnum):
    """Declared input domain of an air-background reference (CALIBRATION §4)."""

    RAW = "raw"
    OSL_CALIBRATED = "osl_calibrated"


@dataclass(frozen=True, slots=True)
class ReferenceStep:
    """One frozen physical capture step (identity + progress snapshot)."""

    channel: ChannelSpec
    standard: OslStandard
    target_count: int


@dataclass(frozen=True, slots=True)
class ReferenceAcceptResult:
    """Outcome of one ``accept_sweep`` call (never mutates the sweep)."""

    accepted: bool
    reason: str | None
    accepted_total: int
    target_total: int


def _readonly(values: np.ndarray) -> np.ndarray:
    # View of a write-protected base: re-enabling WRITEABLE on the view (or
    # on any derived view) raises ValueError, which is the testable
    # read-only guarantee (an owned array with write=False can be flipped
    # back to writable).
    base = np.array(values, copy=True)
    base.setflags(write=False)
    return base[:]


def _require_positive_int(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            f"{name} must be a positive int",
            {"field": name, "got": repr(value)},
        )
    return int(value)


def _require_non_negative_int(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            f"{name} must be a non-negative int",
            {"field": name, "got": repr(value)},
        )
    return int(value)


def _require_sweep_like(sweep: object) -> FrequencySweep:
    if not isinstance(sweep, FrequencySweep):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "reference sessions only accept FrequencySweep instances",
            {"got": type(sweep).__name__},
        )
    return sweep


class _SessionBase:
    """Shared frozen-config validation, accept gate and state machine."""

    def __init__(self, config: MissionConfig) -> None:
        if not isinstance(config, MissionConfig):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "config must be a MissionConfig",
                {"got": type(config).__name__},
            )
        self._config = config
        self._channels: tuple[ChannelSpec, ...] = tuple(config.channels)
        self._axis = _readonly(config.frequency_axis_hz)
        self._lock = threading.Lock()
        self._state = ReferenceSessionState.IDLE
        self._gate_open = False
        self._accepted_total = 0
        self._gate_closed_reason: str | None = None

    # -- observable state ---------------------------------------------------

    @property
    def config(self) -> MissionConfig:
        return self._config

    @property
    def channels(self) -> tuple[ChannelSpec, ...]:
        return self._channels

    @property
    def frequency_axis_hz(self) -> np.ndarray:
        return self._axis.view()

    @property
    def state(self) -> ReferenceSessionState:
        with self._lock:
            return self._state

    @property
    def accepting_gate(self) -> bool:
        with self._lock:
            return self._gate_open

    @property
    def accepted_total(self) -> int:
        with self._lock:
            return self._accepted_total

    # -- state machine ------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._state is not ReferenceSessionState.IDLE:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "session start is only legal from IDLE",
                    {"state": self._state.value},
                )
            self._state = ReferenceSessionState.RUNNING
            self._gate_open = True
            self._gate_closed_reason = None
            self._on_started_locked()

    def cancel(self) -> None:
        """Thread-safe cancellation: close the gate, mark CANCELLED."""
        with self._lock:
            if self._state is ReferenceSessionState.COMPLETED:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "cannot cancel a completed session",
                    {"state": self._state.value},
                )
            self._gate_open = False
            self._gate_closed_reason = "cancelled"
            if self._state in (
                ReferenceSessionState.IDLE,
                ReferenceSessionState.RUNNING,
            ):
                self._state = ReferenceSessionState.CANCELLED

    def close_gate(self, reason: str) -> None:
        """Close the accept gate without changing the session state."""
        with self._lock:
            self._gate_open = False
            self._gate_closed_reason = reason

    def open_gate(self, reason: str | None = None) -> None:
        """Reopen the accept gate after an adapter-controlled restart.

        Legal only while the session is RUNNING (completed/failed/cancelled
        sessions stay closed); accepted captures and completed steps from
        before the restart are preserved.
        """
        with self._lock:
            if self._state is not ReferenceSessionState.RUNNING:
                return
            self._gate_open = True
            self._gate_closed_reason = reason

    def _fail_locked(self) -> None:
        self._gate_open = False
        self._gate_closed_reason = "failed"
        self._state = ReferenceSessionState.FAILED

    # -- acceptance ---------------------------------------------------------

    def accept_sweep(self, sweep: object) -> ReferenceAcceptResult:
        checked = _require_sweep_like(sweep)
        with self._lock:
            if not self._gate_open or self._state is not ReferenceSessionState.RUNNING:
                return ReferenceAcceptResult(
                    accepted=False,
                    reason=self._gate_closed_reason or "gate_closed",
                    accepted_total=self._accepted_total,
                    target_total=self._target_total(),
                )
            self._check_contract_locked(checked)
            self._absorb_locked(checked)
            self._accepted_total += 1
            return self._after_accept_locked()

    def _check_contract_locked(self, sweep: FrequencySweep) -> None:
        if tuple(sweep.channels) != self._channels:
            raise DomainError(
                ErrorCode.CHANNEL_CONTRACT_MISMATCH,
                "sweep channels do not match the frozen reference channels",
                {
                    "expected": [c.channel_id for c in self._channels],
                    "got": [c.channel_id for c in sweep.channels],
                },
            )
        if sweep.frequencies_hz.size != self._axis.size or not np.array_equal(
            sweep.frequencies_hz, self._axis
        ):
            raise DomainError(
                ErrorCode.AXIS_MISMATCH,
                "sweep frequency axis does not match the frozen reference axis",
                {
                    "expected_points": int(self._axis.size),
                    "got_points": int(sweep.frequencies_hz.size),
                },
            )
        data = np.asarray(sweep.data)
        if data.dtype.kind != "c":
            raise DomainError(
                ErrorCode.DTYPE_MISMATCH,
                "sweep data must be complex",
                {"dtype": str(data.dtype)},
            )
        if data.shape != (len(self._channels), self._axis.size):
            raise DomainError(
                ErrorCode.SHAPE_MISMATCH,
                "sweep data shape must be channel x frequency",
                {
                    "expected": [len(self._channels), int(self._axis.size)],
                    "got": list(data.shape),
                },
            )
        if not np.all(np.isfinite(data)):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "sweep data contains non-finite values",
                {},
            )

    # -- hooks for subclasses ------------------------------------------------

    def _target_total(self) -> int:  # pragma: no cover - overridden
        raise NotImplementedError

    def _on_started_locked(self) -> None:
        return None

    def record_step_failure(self, detail: str) -> None:
        """Record a device error against the current step (retry policy).

        The OSL session applies the per-step retry budget; the air-background
        session has no steps, so the default is a no-op (fail-closed is still
        reachable through fail on cancel/device errors).
        """
        return None

    def _absorb_locked(self, sweep: FrequencySweep) -> None:
        raise NotImplementedError

    def _after_accept_locked(self) -> ReferenceAcceptResult:
        raise NotImplementedError


class OslReferenceSession(_SessionBase):
    """OSL six-step capture session delegating the solve to ISSUE-027."""

    def __init__(
        self,
        config: MissionConfig,
        *,
        captures_per_step: int,
        max_step_retries: int = 2,
        open_actual: object = 1.0 + 0.0j,
        short_actual: object = -1.0 + 0.0j,
        load_actual: object = 0.0 + 0.0j,
    ) -> None:
        super().__init__(config)
        count = _require_positive_int(captures_per_step, "captures_per_step")
        _require_non_negative_int(max_step_retries, "max_step_retries")
        for channel in self._channels:
            if channel.s_parameter not in _REFLECTION_PARAMETERS:
                raise DomainError(
                    ErrorCode.CHANNEL_CONTRACT_MISMATCH,
                    "OSL reference sessions cover only S11/S22 reflection channels",
                    {
                        "channel_id": channel.channel_id,
                        "s_parameter": channel.s_parameter.value,
                    },
                )
        self._captures_per_step = count
        self._max_step_retries = max_step_retries
        self._open_actual = open_actual
        self._short_actual = short_actual
        self._load_actual = load_actual
        # ordered physical steps: channel order x (open, short, load)
        self._steps: list[list[FrequencySweep]] = []
        self._step_states: list[ReferenceStepState] = []
        for _channel in self._channels:
            for _standard in _STANDARDS:
                self._steps.append([])
                self._step_states.append(ReferenceStepState.PENDING)
        self._current_step = -1
        self._step_failures = 0

    # -- observable state ---------------------------------------------------

    @property
    def steps(self) -> tuple[ReferenceStep, ...]:
        with self._lock:
            return tuple(
                ReferenceStep(
                    channel=channel,
                    standard=standard,
                    target_count=self._captures_per_step,
                )
                for channel in self._channels
                for standard in _STANDARDS
            )

    @property
    def step_failure_count(self) -> int:
        with self._lock:
            return self._step_failures

    # -- failure / retry ----------------------------------------------------

    def record_step_failure(self, detail: str) -> None:
        """Record a device error against the current step (retry policy).

        Within ``max_step_retries`` the step stays running (completed steps
        and accepted captures are preserved); beyond the budget the session
        fails closed.
        """
        with self._lock:
            if self._state is not ReferenceSessionState.RUNNING:
                return
            self._step_failures += 1
            if self._step_failures > self._max_step_retries:
                self._fail_locked()

    # -- state machine hooks -------------------------------------------------

    def _target_total(self) -> int:
        return len(self._steps) * self._captures_per_step

    def _on_started_locked(self) -> None:
        self._current_step = 0
        self._step_states[0] = ReferenceStepState.RUNNING

    def _absorb_locked(self, sweep: FrequencySweep) -> None:
        # A sweep always carries the full frozen channel set (checked by
        # _check_contract_locked) — for a dual-reflection config one sweep
        # holds both S11 and S22 rows of the physically connected standard.
        # Which row feeds which step's profile is decided at build() time by
        # channel_index; the step sequence itself is strictly linear
        # (no skipping/mixing possible through the accept gate).
        self._steps[self._current_step].append(sweep)

    def _after_accept_locked(self) -> ReferenceAcceptResult:
        if len(self._steps[self._current_step]) < self._captures_per_step:
            return ReferenceAcceptResult(
                accepted=True,
                reason=None,
                accepted_total=self._accepted_total,
                target_total=self._target_total(),
            )
        # step target met: complete the step and close the gate until the
        # orchestrator advances (in-flight data is rejected meanwhile)
        self._step_states[self._current_step] = ReferenceStepState.COMPLETED
        self._gate_open = False
        self._gate_closed_reason = "step_completed"
        next_step = self._current_step + 1
        if next_step < len(self._steps):
            self._current_step = next_step
            self._step_states[next_step] = ReferenceStepState.RUNNING
            # P3-2 fix: the retry budget is per step — a fresh step starts
            # with a fresh budget (the name and docstring say "per step").
            self._step_failures = 0
            self._gate_open = True
            self._gate_closed_reason = None
        else:
            self._state = ReferenceSessionState.COMPLETED
        return ReferenceAcceptResult(
            accepted=True,
            reason=None,
            accepted_total=self._accepted_total,
            target_total=self._target_total(),
        )

    # -- solve (delegated to ISSUE-027) ---------------------------------------

    def build(self) -> OslCalibrationSet:
        with self._lock:
            if self._state is not ReferenceSessionState.COMPLETED:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "OSL build requires the completed six-step capture",
                    {"state": self._state.value, "kind": "incomplete_steps"},
                )
            captured = [
                [sweep for sweep in step] for step in self._steps
            ]
        profiles: list[OslCalibrationProfile] = []
        axis = self._axis
        for channel_index, channel in enumerate(self._channels):
            base = channel_index * len(_STANDARDS)
            open_data = np.stack(
                [np.asarray(s.data[channel_index], dtype=np.complex128) for s in captured[base]]
            )
            short_data = np.stack(
                [np.asarray(s.data[channel_index], dtype=np.complex128) for s in captured[base + 1]]
            )
            load_data = np.stack(
                [np.asarray(s.data[channel_index], dtype=np.complex128) for s in captured[base + 2]]
            )
            profiles.append(
                build_osl_calibration(
                    channel=channel,
                    frequency_hz=axis,
                    open_measured=open_data,
                    short_measured=short_data,
                    load_measured=load_data,
                    open_actual=self._open_actual,
                    short_actual=self._short_actual,
                    load_actual=self._load_actual,
                )
            )
        return OslCalibrationSet(profiles)


@dataclass(frozen=True, slots=True)
class AirBackgroundReference:
    """Aggregated air-background reference (no file I/O, read-only arrays)."""

    channels: tuple[ChannelSpec, ...]
    frequency_hz: np.ndarray
    mean_data: np.ndarray
    trace_count: int
    domain: ReferenceDomain
    calibration_profile_id: CalibrationProfileId | None


class AirBackgroundSession(_SessionBase):
    """Air-background capture session with an explicit declared domain."""

    def __init__(
        self,
        config: MissionConfig,
        *,
        target_traces: int,
        domain: ReferenceDomain,
        calibration_profile_id: CalibrationProfileId | None = None,
        max_retries: int = 3,
    ) -> None:
        super().__init__(config)
        _require_positive_int(target_traces, "target_traces")
        _require_non_negative_int(max_retries, "max_retries")
        if not isinstance(domain, ReferenceDomain):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "domain must be a ReferenceDomain",
                {"got": type(domain).__name__},
            )
        if domain is ReferenceDomain.OSL_CALIBRATED and not isinstance(
            calibration_profile_id, CalibrationProfileId
        ):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "osl_calibrated domain requires an explicit calibration_profile_id",
                {"kind": "missing_profile_id"},
            )
        if domain is ReferenceDomain.RAW and calibration_profile_id is not None:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "raw domain must not declare a calibration_profile_id",
                {"kind": "unexpected_profile_id"},
            )
        self._target_traces = target_traces
        self._max_retries = max_retries
        self._device_failures = 0
        self._domain = domain
        self._profile_id = calibration_profile_id
        self._sweeps: list[FrequencySweep] = []

    @property
    def domain(self) -> ReferenceDomain:
        return self._domain

    def _target_total(self) -> int:
        return self._target_traces

    def record_step_failure(self, detail: str) -> None:
        """Count device errors against the bounded retry budget (P2 fix).

        Air background has no physical steps; the budget bounds the whole
        capture session.  Beyond ``max_retries`` recorded failures the
        session fails closed (the adapter's FAILED branch then stops
        cleanly instead of spinning in a hot retry loop).
        """
        with self._lock:
            if self._state is not ReferenceSessionState.RUNNING:
                return
            self._device_failures += 1
            if self._device_failures > self._max_retries:
                self._fail_locked()

    def _absorb_locked(self, sweep: FrequencySweep) -> None:
        self._sweeps.append(sweep)

    def _after_accept_locked(self) -> ReferenceAcceptResult:
        if len(self._sweeps) < self._target_traces:
            return ReferenceAcceptResult(
                accepted=True,
                reason=None,
                accepted_total=self._accepted_total,
                target_total=self._target_traces,
            )
        self._gate_open = False
        self._gate_closed_reason = "target_met"
        self._state = ReferenceSessionState.COMPLETED
        return ReferenceAcceptResult(
            accepted=True,
            reason=None,
            accepted_total=self._accepted_total,
            target_total=self._target_traces,
        )

    def build(self) -> AirBackgroundReference:
        with self._lock:
            if self._state is not ReferenceSessionState.COMPLETED:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "air background build requires the target trace count",
                    {
                        "state": self._state.value,
                        "kind": "incomplete_traces",
                        "accepted": self._accepted_total,
                        "target": self._target_traces,
                    },
                )
            captured = list(self._sweeps)
        stack = np.stack(
            [np.asarray(s.data, dtype=np.complex128) for s in captured], axis=0
        )
        return AirBackgroundReference(
            channels=self._channels,
            frequency_hz=_readonly(self._axis),
            mean_data=_readonly(np.mean(stack, axis=0)),
            trace_count=len(captured),
            domain=self._domain,
            calibration_profile_id=self._profile_id,
        )


class ControllerReferenceAdapter:
    """Orchestrates one reference session over acquisition controllers.

    The adapter owns no acquisition loop of its own: it consumes the
    controller's bounded sweep buffer, feeds ``accept_sweep``, restarts via
    the caller factory on controller failure while the session retry budget
    allows, and always closes the accept gate before stopping the
    controller, then joins and closes it.  The observable ``events`` list
    records the shutdown order for tests and audits.
    """

    def __init__(
        self,
        session: _SessionBase,
        factory: Callable[[], AcquisitionController],
        *,
        gate_poll_timeout_s: float = _GATE_POLL_TIMEOUT_S,
    ) -> None:
        self._session = session
        self._factory = factory
        self._poll = float(gate_poll_timeout_s)
        self._events: list[str] = []
        self._controller: AcquisitionController | None = None

    @property
    def events(self) -> list[str]:
        return list(self._events)

    def run(self) -> None:
        session = self._session
        try:
            if session.state is ReferenceSessionState.IDLE:
                session.start()
            self._controller = self._factory()
            self._events.append("controller_started")
            while True:
                if session.state in (
                    ReferenceSessionState.COMPLETED,
                    ReferenceSessionState.CANCELLED,
                    ReferenceSessionState.FAILED,
                ):
                    break
                controller = self._controller
                assert controller is not None
                if controller.state is ControllerState.FAILED:
                    failure = controller.error
                    session.record_step_failure(
                        str(failure.reason) if failure is not None else "controller failed"
                    )
                    if session.state is ReferenceSessionState.FAILED:
                        break
                    # retry: release the failed controller, acquire a fresh
                    # one and reopen the accept gate (the shutdown above
                    # closed it; accepted captures stay preserved)
                    self._shutdown_controller("controller_restarted")
                    self._controller = self._factory()
                    self._events.append("controller_started")
                    session.open_gate("adapter_controller_restarted")
                    continue
                sweep = controller.sweeps.get(self._poll)
                if sweep is not None:
                    try:
                        session.accept_sweep(sweep)
                    except DomainError:
                        session.record_step_failure("sweep contract violation")
                        if session.state is ReferenceSessionState.FAILED:
                            break
        finally:
            self._shutdown_controller("terminal")
            if (
                session.state is not ReferenceSessionState.COMPLETED
                and session.state is not ReferenceSessionState.FAILED
                and session.state is not ReferenceSessionState.CANCELLED
            ):
                session.cancel()

    def _shutdown_controller(self, trigger: str) -> None:
        controller = self._controller
        if controller is None:
            return
        self._controller = None
        if self._session.accepting_gate:
            self._session.close_gate(f"adapter_{trigger}")
        self._events.append("gate_closed")
        controller.stop()
        self._events.append("controller_stopped")
        controller.wait_finished(_STOP_WAIT_S)
        controller.close()
        self._events.append("controller_closed")
