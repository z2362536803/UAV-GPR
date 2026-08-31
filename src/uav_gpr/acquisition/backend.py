"""Acquisition backend contract and deterministic simulator (ISSUE-015).

One interface for real, simulated and replay backends (docs/ACQUISITION.md
section 1/2):

- ``AcquisitionBackend`` enforces the strict lifecycle
  ``open -> configure -> acquire* -> close`` (plus idempotent ``cancel`` and
  ``close``); illegal transitions raise structured ``BackendStateError``.
- ``Capabilities`` describes the opened device (identity, supported channel
  set, fault-injection/GNSS support).
- ``AppliedConfig`` pairs the device-effective configuration with the
  requested/applied ``ConfigDiff`` (docs/ACQUISITION.md section 4).
- ``SimulatedBackend`` produces deterministic multi-channel
  ``FrequencySweep`` objects from a seed/config/injected ``Clock``, with
  deterministic fault injection (timeout, half sweep, configuration
  rejection, disconnect, delay) and cancellable blocking waits.

All failures are core structured errors (``DomainError`` with
``ErrorCode.INVALID_ARGUMENT`` plus a stable ``reason`` context key; the core
``ErrorCode`` enum is read-only, so backend-specific discrimination uses the
typed subclasses and the ``reason`` context).
"""

from __future__ import annotations

import hashlib
import math
import threading
import uuid
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime

import numpy as np

from uav_gpr.core import (
    ChannelSpec,
    ConfigDiff,
    DeviceId,
    FrequencySweep,
    GnssFix,
    GnssFixQuality,
    GnssMatch,
    GnssMatchMethod,
    MissionConfig,
    MissionId,
    MonotonicNs,
    RawHashSpec,
    TraceMetadata,
    TraceQualityReason,
    TraceQualityStatus,
    TraceUid,
)
from uav_gpr.core.enums import StableStrEnum
from uav_gpr.core.errors import DomainError, ErrorCode, JsonValue
from uav_gpr.core.timeutil import Clock, SystemClock


class BackendState(StableStrEnum):
    """Lifecycle states of an :class:`AcquisitionBackend`."""

    CLOSED = "closed"
    OPEN = "open"
    CONFIGURED = "configured"


@dataclass(frozen=True, slots=True)
class Capabilities:
    """Static capabilities of an opened backend (identity + supported channels).

    ``channels`` is the ordered set of channel bindings the device can
    provide; a mission may configure an order-preserving subset of them.
    """

    device_id: DeviceId
    channels: tuple[ChannelSpec, ...]
    fault_injection: bool
    gnss: bool

    @property
    def supports_dual_channel(self) -> bool:
        """Whether the device supports at least two channels."""
        return len(self.channels) >= 2


@dataclass(frozen=True, slots=True)
class AppliedConfig:
    """The device-effective configuration and its requested/applied diff."""

    config: MissionConfig
    diff: ConfigDiff


class BackendError(DomainError):
    """Acquisition backend failure: ``DomainError`` with a stable reason.

    Business logic branches on ``code`` plus ``context["reason"]`` (the core
    ``ErrorCode`` enum is read-only; backend faults reuse
    ``INVALID_ARGUMENT`` with a machine-stable reason discriminator).
    """

    _reason: str = "backend_error"

    def __init__(self, message: str, **context: JsonValue) -> None:
        super().__init__(
            ErrorCode.INVALID_ARGUMENT,
            message,
            {"reason": self._reason, **context},
        )

    @property
    def reason(self) -> str:
        return self._reason


class BackendStateError(BackendError):
    """Illegal lifecycle transition or a busy (concurrent) acquire."""

    _reason = "illegal_state"


class BackendTimeoutError(BackendError):
    """Simulated device timeout (planned fault or caller timeout expiry)."""

    _reason = "device_timeout"


class BackendHalfSweepError(BackendError):
    """Incomplete (half) sweep rejected fail-closed, never zero-filled."""

    _reason = "half_sweep"


class BackendDisconnectedError(BackendError):
    """Simulated device disconnect; ``connection_generation`` increments."""

    _reason = "device_disconnected"


class BackendConfigRejectedError(BackendError):
    """Configuration rejected by the (simulated) device."""

    _reason = "config_rejected"


class BackendCancelledError(BackendError):
    """A blocking wait was interrupted by :meth:`AcquisitionBackend.cancel`."""

    _reason = "cancelled"


class BackendClosedError(BackendError):
    """A blocking wait was interrupted by :meth:`AcquisitionBackend.close`."""

    _reason = "closed"


class AcquisitionBackend(ABC):
    """Strict lifecycle contract shared by every backend.

    State machine::

        CLOSED --open()--> OPEN --configure()--> CONFIGURED --acquire()*--> CONFIGURED
                                            \\--cancel()--> CONFIGURED (no-op if idle)
        OPEN/CONFIGURED --close()--> CLOSED;  CLOSED --close()/cancel()--> CLOSED (no-op)

    ``acquire()`` is a blocking call that returns one complete sweep; at most
    one acquire may be in flight (single device, serialized).  ``cancel()``
    interrupts a blocked ``acquire`` wait; ``close()`` releases the backend,
    wakes any blocked acquire and is idempotent.  Concrete backends implement
    the ``_do_*`` hooks; the base class owns the state machine, the
    connection generation, the cancellation signal and the ``acquire_started``
    observability event (set while a blocking acquire is inside ``_do_acquire``).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()
        self._state = BackendState.CLOSED
        self._generation = 0
        self._acquiring = False
        self.acquire_started = threading.Event()

    # -- observable state ---------------------------------------------------

    @property
    def state(self) -> BackendState:
        with self._lock:
            return self._state

    @property
    def connection_generation(self) -> int:
        """Device reconnect generation (1 after open; +1 per disconnect)."""
        with self._lock:
            return self._generation

    @property
    def acquiring(self) -> bool:
        """Whether an acquire is currently in flight."""
        with self._lock:
            return self._acquiring

    # -- lifecycle ----------------------------------------------------------

    def open(self) -> Capabilities:
        """Open the device (only from ``CLOSED``; reopen after close allowed).

        A reopen must only happen after every in-flight acquire has
        terminated: ``close()`` wakes blocked acquires (they raise
        ``BackendClosedError``), and the caller must join the workers before
        calling ``open()`` again.
        """
        with self._lock:
            if self._state is not BackendState.CLOSED:
                raise BackendStateError(
                    "open requires a closed backend",
                    operation="open",
                    state=self._state.value,
                    allowed_states=[BackendState.CLOSED.value],
                )
            self._state = BackendState.OPEN
            self._generation = 1
            self._cancel_event.clear()
        return self._do_open()

    def configure(self, config: MissionConfig) -> AppliedConfig:
        """Validate and apply a frozen mission config (OPEN or CONFIGURED).

        Configure and acquire are mutually exclusive: a re-configure while an
        acquire is in flight is rejected structurally (``busy=True``), never
        silently applied underneath an in-flight sweep.  A re-configure starts
        a new acquisition task (trace/attempt counters reset); the caller is
        responsible for rotating ``mission_id`` before re-configuring
        (ISSUE-017/043 contract).
        """
        if not isinstance(config, MissionConfig):
            raise TypeError(
                f"config must be a MissionConfig, got {type(config).__name__}"
            )
        with self._lock:
            if self._state not in (BackendState.OPEN, BackendState.CONFIGURED):
                raise BackendStateError(
                    "configure requires an open backend",
                    operation="configure",
                    state=self._state.value,
                    allowed_states=[
                        BackendState.OPEN.value,
                        BackendState.CONFIGURED.value,
                    ],
                )
            if self._acquiring:
                raise BackendStateError(
                    "configure is not allowed while an acquire is in progress",
                    operation="configure",
                    state=self._state.value,
                    busy=True,
                )
        applied = self._do_configure(config)
        with self._lock:
            self._state = BackendState.CONFIGURED
        return applied

    def acquire(self, timeout_s: float | None = None) -> FrequencySweep:
        """Acquire one complete sweep (only from ``CONFIGURED``).

        ``timeout_s`` caps a blocking wait (device latency / cancel-and-wait
        fault); it is ignored when the sweep completes immediately.
        """
        self._require_timeout(timeout_s)
        with self._lock:
            if self._state is not BackendState.CONFIGURED:
                raise BackendStateError(
                    "acquire requires a configured backend",
                    operation="acquire",
                    state=self._state.value,
                    allowed_states=[BackendState.CONFIGURED.value],
                )
            if self._acquiring:
                raise BackendStateError(
                    "acquire is already in progress (single device, serialized)",
                    operation="acquire",
                    state=self._state.value,
                    busy=True,
                )
            self._acquiring = True
            self._cancel_event.clear()
            self.acquire_started.set()
        try:
            return self._do_acquire(timeout_s)
        finally:
            with self._lock:
                self._acquiring = False
            self.acquire_started.clear()

    def cancel(self) -> None:
        """Interrupt any in-flight blocking wait; idempotent no-op otherwise."""
        self._cancel_event.set()

    def close(self) -> None:
        """Release the backend; idempotent; wakes any blocked acquire.

        A blocked ``acquire`` wakes and raises ``BackendClosedError``.
        Callers must not reopen until every in-flight acquire has terminated
        (join the workers before the next ``open()``).
        """
        with self._lock:
            if self._state is BackendState.CLOSED:
                return
            self._state = BackendState.CLOSED
        self._cancel_event.set()
        self._do_close()

    # -- shared helpers for concrete backends -------------------------------

    @staticmethod
    def _require_timeout(timeout_s: float | None) -> None:
        if timeout_s is None:
            return
        if (
            isinstance(timeout_s, bool)
            or not isinstance(timeout_s, float)
            or not math.isfinite(timeout_s)
            or timeout_s < 0.0
        ):
            raise BackendStateError(
                "timeout_s must be a finite non-negative float or None",
                operation="acquire",
                timeout_s=timeout_s,
            )

    def _raise_interrupted(self, attempt: int) -> None:
        """After a cancel/close wake: distinguish closed vs cancelled."""
        with self._lock:
            state = self._state
        if state is BackendState.CLOSED:
            raise BackendClosedError(
                "backend closed during acquire", attempt=attempt
            )
        raise BackendCancelledError("acquire cancelled", attempt=attempt)

    def _wait_cancellable(
        self, *, seconds: float | None, attempt: int, timeout_s: float | None
    ) -> None:
        """Block simulating device latency; cancellable and timeout-bounded.

        ``seconds`` is how long the simulated device needs (``None`` means
        until cancelled/closed).  ``timeout_s`` caps the caller's wait: when
        it expires before the device finishes, a ``BackendTimeoutError`` is
        raised.  Returns normally only when the device latency elapsed.
        """
        if seconds is None:
            wait_for = timeout_s
        elif timeout_s is None:
            wait_for = seconds
        else:
            wait_for = min(seconds, timeout_s)
        woke = self._cancel_event.wait(wait_for)
        if woke:
            self._raise_interrupted(attempt)
        if seconds is None or (timeout_s is not None and timeout_s < seconds):
            raise BackendTimeoutError(
                "device wait timed out",
                attempt=attempt,
                timeout_s=timeout_s,
                device_seconds=seconds,
            )

    # -- hooks --------------------------------------------------------------

    @abstractmethod
    def _do_open(self) -> Capabilities:
        """Perform device open; return the device capabilities."""

    @abstractmethod
    def _do_configure(self, config: MissionConfig) -> AppliedConfig:
        """Validate/apply the config and return the effective AppliedConfig."""

    @abstractmethod
    def _do_acquire(self, timeout_s: float | None) -> FrequencySweep:
        """Acquire one complete sweep (may block; must honour cancel/close)."""

    @abstractmethod
    def _do_close(self) -> None:
        """Release device resources (idempotent)."""


@dataclass(frozen=True, slots=True)
class SimulationFaults:
    """Deterministic fault plan keyed by zero-based acquire attempt ordinals.

    Attempt ordinals count every ``acquire()`` call (faulted or not);
    ``trace_index`` only advances on successful sweeps, so a fault at
    attempt ``n`` always fires on the same call for a given plan.

    ``delay_s`` is a construction-time snapshot contract: the mapping must
    not be mutated after the object is built (frozen dataclass holds the
    caller's reference).
    """

    timeout_at: tuple[int, ...] = ()
    half_sweep_at: tuple[int, ...] = ()
    disconnect_at: tuple[int, ...] = ()
    delay_s: Mapping[int, float] = field(default_factory=dict)
    reject_config: bool = False
    applied_if_bw_hz: float | None = None
    block_until_cancelled: bool = False

    def __post_init__(self) -> None:
        for name in ("timeout_at", "half_sweep_at", "disconnect_at"):
            values = getattr(self, name)
            if not all(
                isinstance(value, int)
                and not isinstance(value, bool)
                and value >= 0
                for value in values
            ):
                raise ValueError(f"{name} must contain only non-negative ints")
        if not all(
            isinstance(key, int)
            and not isinstance(key, bool)
            and key >= 0
            and isinstance(value, float)
            and math.isfinite(value)
            and value >= 0.0
            for key, value in self.delay_s.items()
        ):
            raise ValueError(
                "delay_s must map non-negative ints to finite non-negative floats"
            )
        if self.applied_if_bw_hz is not None and (
            not isinstance(self.applied_if_bw_hz, float)
            or not math.isfinite(self.applied_if_bw_hz)
            or self.applied_if_bw_hz <= 0.0
        ):
            raise ValueError(
                "applied_if_bw_hz must be a positive finite float or None"
            )
        if not isinstance(self.reject_config, bool) or not isinstance(
            self.block_until_cancelled, bool
        ):
            raise TypeError("reject_config and block_until_cancelled must be bools")


def _validate_backend_channels(
    channels: Sequence[ChannelSpec],
) -> tuple[ChannelSpec, ...]:
    result = tuple(channels)
    if not result:
        raise ValueError("a backend requires at least one supported channel")
    if not all(isinstance(channel, ChannelSpec) for channel in result):
        raise TypeError("channels must contain only ChannelSpec values")
    ids = [channel.channel_id for channel in result]
    if len(set(ids)) != len(ids):
        raise ValueError("backend channels must be unique")
    return result


def _derive_rng_seed(seed: int, config: MissionConfig) -> int:
    """Deterministic RNG seed from the caller seed and the config digest.

    Same seed + same config -> same sweeps; different seed or different
    config -> different sweeps, independent of configure history.
    """
    digest = hashlib.sha256(
        f"{seed}:{config.config_sha256}".encode()
    ).hexdigest()
    return int(digest[:16], 16)


class SimulatedBackend(AcquisitionBackend):
    """Deterministic multi-channel simulator (no hardware, no threads).

    Generates complex ``channel x frequency`` sweeps from a seeded RNG and an
    injected :class:`~uav_gpr.core.timeutil.Clock` (``ManualClock`` in tests);
    metadata carries real UTC+monotonic sweep times, per-trace identity, the
    canonical raw hash (ISSUE-009) and either a deterministic GNSS match or an
    explicit ``gnss_missing`` reason.  Faults are injected deterministically
    via :class:`SimulationFaults`.
    """

    def __init__(
        self,
        *,
        mission_id: MissionId,
        device_id: DeviceId,
        channels: Sequence[ChannelSpec],
        seed: int = 0,
        clock: Clock | None = None,
        faults: SimulationFaults | None = None,
        gnss_enabled: bool = False,
    ) -> None:
        super().__init__()
        if not isinstance(mission_id, MissionId):
            raise TypeError(
                f"mission_id must be a MissionId, got {type(mission_id).__name__}"
            )
        if not isinstance(device_id, DeviceId):
            raise TypeError(
                f"device_id must be a DeviceId, got {type(device_id).__name__}"
            )
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise TypeError(f"seed must be an int, got {type(seed).__name__}")
        if clock is not None and not isinstance(clock, Clock):
            raise TypeError(
                f"clock must implement the Clock protocol, got {type(clock).__name__}"
            )
        channels = _validate_backend_channels(channels)
        self._mission_id = mission_id
        self._device_id = device_id
        self._channels = channels
        self._seed = seed
        self._clock = clock if clock is not None else SystemClock()
        self._faults = faults if faults is not None else SimulationFaults()
        self._gnss_enabled = bool(gnss_enabled)
        self._applied: AppliedConfig | None = None
        self._rng: np.random.Generator | None = None
        self._trace_index = 0
        self._attempt = 0
        self._prev_start_mono: MonotonicNs | None = None

    @property
    def capabilities(self) -> Capabilities:
        """The capabilities this simulator would report after ``open()``."""
        return Capabilities(
            device_id=self._device_id,
            channels=self._channels,
            fault_injection=True,
            gnss=self._gnss_enabled,
        )

    # -- hooks --------------------------------------------------------------

    def _do_open(self) -> Capabilities:
        return self.capabilities

    def _do_configure(self, config: MissionConfig) -> AppliedConfig:
        faults = self._faults
        if faults.reject_config:
            raise BackendConfigRejectedError(
                "simulated configuration rejection", config_sha256=config.config_sha256
            )
        self._require_supported_channels(config.channels)
        if faults.applied_if_bw_hz is not None:
            applied_config = replace(config, if_bw_hz=faults.applied_if_bw_hz)
        else:
            applied_config = config
        applied = AppliedConfig(
            config=applied_config,
            diff=ConfigDiff.compute(config, applied_config),
        )
        self._applied = applied
        self._rng = np.random.default_rng(_derive_rng_seed(self._seed, config))
        self._trace_index = 0
        self._attempt = 0
        self._prev_start_mono = None
        return applied

    def _do_acquire(self, timeout_s: float | None) -> FrequencySweep:
        applied = self._applied
        assert applied is not None  # base state machine guarantees CONFIGURED
        faults = self._faults
        attempt = self._attempt
        self._attempt = attempt + 1

        if attempt in faults.timeout_at:
            raise BackendTimeoutError("simulated device timeout", attempt=attempt)
        if attempt in faults.half_sweep_at:
            raise BackendHalfSweepError(
                "simulated half sweep rejected (incomplete sweep is fail-closed)",
                attempt=attempt,
            )
        if attempt in faults.disconnect_at:
            with self._lock:
                self._generation += 1
            raise BackendDisconnectedError(
                "simulated device disconnect", attempt=attempt
            )
        if faults.block_until_cancelled:
            self._wait_cancellable(seconds=None, attempt=attempt, timeout_s=timeout_s)
        delay = faults.delay_s.get(attempt, 0.0)
        if delay > 0.0:
            self._wait_cancellable(seconds=delay, attempt=attempt, timeout_s=timeout_s)

        return self._produce_sweep(applied.config)

    def _do_close(self) -> None:
        return None

    # -- simulation ---------------------------------------------------------

    def _require_supported_channels(self, requested: Sequence[ChannelSpec]) -> None:
        device_ids = [channel.channel_id for channel in self._channels]
        position = -1
        for channel in requested:
            try:
                index = device_ids.index(channel.channel_id)
            except ValueError:
                raise BackendConfigRejectedError(
                    "unsupported channel in configuration",
                    channel_id=channel.channel_id,
                ) from None
            if index <= position:
                raise BackendConfigRejectedError(
                    "requested channel order must match device capabilities",
                    channel_id=channel.channel_id,
                )
            position = index

    def _produce_sweep(self, config: MissionConfig) -> FrequencySweep:
        rng = self._rng
        assert rng is not None  # set by _do_configure
        channels = config.channels
        frequencies = config.frequency_axis_hz
        shape = (len(channels), int(frequencies.size))
        real = rng.standard_normal(shape)
        imag = rng.standard_normal(shape)
        top = float(frequencies[-1])
        envelope = 1.0 / (1.0 + (frequencies / top) ** 2)
        data = (real + 1j * imag) * envelope[np.newaxis, :]

        index = self._trace_index
        self._trace_index = index + 1
        clock = self._clock
        start_utc = clock.utc_now()
        start_mono = clock.monotonic_ns()
        finish_utc = clock.utc_now()
        finish_mono = clock.monotonic_ns()
        midpoint_utc = start_utc + (finish_utc - start_utc) / 2
        midpoint_mono = MonotonicNs(
            start_mono.ns + (finish_mono.ns - start_mono.ns) // 2
        )
        previous = self._prev_start_mono
        if previous is None:
            actual_interval: float | None = None
            schedule_error: float | None = None
        else:
            actual_interval = (start_mono.ns - previous.ns) / 1_000_000_000.0
            schedule_error = actual_interval - config.target_interval_s
        self._prev_start_mono = start_mono

        uid = TraceUid(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"uav-gpr:sim:{self._mission_id.to_json()}:{index}",
            )
        )
        raw_hash = RawHashSpec(
            mission_id=self._mission_id,
            trace_index=index,
            trace_uid=uid,
            channels=channels,
            frequencies_hz=frequencies,
            data=data,
        ).compute()
        with self._lock:
            generation = self._generation

        if self._gnss_enabled:
            match = self._build_gnss_match(midpoint_utc, midpoint_mono, rng)
            quality_status = TraceQualityStatus.NOMINAL
            quality_reasons: tuple[TraceQualityReason, ...] = ()
        else:
            match = None
            quality_status = TraceQualityStatus.DEGRADED
            quality_reasons = (TraceQualityReason.GNSS_MISSING,)

        metadata = TraceMetadata(
            mission_id=self._mission_id,
            trace_index=index,
            trace_uid=uid,
            device_id=self._device_id,
            sweep_started_utc=start_utc,
            sweep_midpoint_utc=midpoint_utc,
            sweep_finished_utc=finish_utc,
            sweep_started_monotonic_ns=start_mono,
            sweep_midpoint_monotonic_ns=midpoint_mono,
            sweep_finished_monotonic_ns=finish_mono,
            target_interval_s=config.target_interval_s,
            actual_interval_s=actual_interval,
            schedule_error_s=schedule_error,
            connection_generation=generation,
            raw_trace_sha256=raw_hash,
            gnss_match=match,
            quality_status=quality_status,
            quality_reasons=quality_reasons,
        )
        return FrequencySweep(
            channels=channels,
            frequencies_hz=frequencies,
            data=data,
            metadata=metadata,
        )

    def _build_gnss_match(
        self,
        midpoint_utc: datetime,
        midpoint_mono: MonotonicNs,
        rng: np.random.Generator,
    ) -> GnssMatch:
        """Deterministic valid fix matched to the sweep midpoint."""
        fix = GnssFix(
            received_utc=midpoint_utc,
            nmea_utc=None,
            received_monotonic_ns=midpoint_mono,
            latitude_deg=float(rng.uniform(25.0, 45.0)),
            longitude_deg=float(rng.uniform(105.0, 125.0)),
            altitude_msl_m=float(rng.uniform(0.0, 500.0)),
            geoid_separation_m=None,
            fix_quality=GnssFixQuality.GPS_FIX,
            satellites=int(rng.integers(8, 13)),
            hdop=float(rng.uniform(0.8, 2.0)),
            ground_speed_mps=None,
            course_deg=None,
            valid=True,
            invalid_reason=None,
        )
        return GnssMatch(
            fix=fix,
            trace_midpoint_utc=midpoint_utc,
            age_s=0.0,
            method=GnssMatchMethod.NEAREST_MIDPOINT,
            usable_for_map=True,
            reason=None,
        )
