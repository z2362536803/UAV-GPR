"""LibreVNA reconnect policy and controller hook (ISSUE-023).

ISSUE-017 froze the controller collaboration surface
(``AcquisitionController(reconnect_hook=...)`` +
``AcquisitionController.connection_generation``, see
``acquisition/controller.py`` ``_handle_disconnect``): when a worker acquire
raises ``BackendDisconnectedError``, the controller records the generation,
calls the hook, and requires that afterwards the backend is ``CONFIGURED``
with a *changed* ``connection_generation`` (else the controller fails with a
``ReconnectContract`` failure).  This module provides that hook for the
LibreVNA backend together with a deterministic backoff policy:

- :class:`LibreVnaReconnectPolicy` -- fixed exponential backoff schedule
  (``initial_delay_s * backoff_factor ** (attempt - 1)`` capped at
  ``max_delay_s``); deterministic, no jitter, so tests and CI reproduce the
  exact schedule;
- :class:`LibreVnaReconnectError` -- structured failure after the attempts
  are exhausted (``BackendError`` with ``reason="reconnect_failed"``);
- :class:`LibreVnaReconnector` -- callable usable directly as the
  controller ``reconnect_hook``: retries
  ``LibreVnaUsbBackend.reconnect_session(config)`` with the policy's delays,
  propagates ``BackendCancelledError``/``BackendClosedError`` immediately
  (controller close / emergency stop must never be swallowed), and raises
  :class:`LibreVnaReconnectError` when every attempt failed.

Generation semantics (P3-03 note in controller.py, resolved here): a
successful physical reconnect increments ``connection_generation``
(disconnect +1, successful reconnect +1) while the base lifecycle state
stays ``CONFIGURED`` and the trace counters are preserved -- no trace index
or UID is ever repeated, and unconfirmed configuration is never reused
(docs/plans/2026-09-02-issue-023-librevna-reconnect.md decision D1/D4/D5).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from uav_gpr.acquisition.backend import (
    AppliedConfig,
    BackendCancelledError,
    BackendClosedError,
    BackendError,
)
from uav_gpr.acquisition.librevna.backend import LibreVnaUsbBackend
from uav_gpr.core import MissionConfig


class LibreVnaReconnectError(BackendError):
    """Reconnect attempts exhausted; the backend session is fail-closed."""

    _reason = "reconnect_failed"


@dataclass(frozen=True)
class LibreVnaReconnectPolicy:
    """Deterministic exponential backoff schedule for LibreVNA reconnects.

    ``delay_after_failed_attempt(n)`` is
    ``min(initial_delay_s * backoff_factor ** (n - 1), max_delay_s)``.
    """

    max_attempts: int = 5
    initial_delay_s: float = 0.5
    backoff_factor: float = 2.0
    max_delay_s: float = 8.0

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_attempts, bool)
            or not isinstance(self.max_attempts, int)
            or self.max_attempts < 1
        ):
            raise ValueError("max_attempts must be an int >= 1")
        for name in ("initial_delay_s", "backoff_factor", "max_delay_s"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                raise ValueError(f"{name} must be a finite number")
        if self.initial_delay_s <= 0.0:
            raise ValueError("initial_delay_s must be positive")
        if self.backoff_factor < 1.0:
            raise ValueError("backoff_factor must be >= 1.0")
        if self.max_delay_s < self.initial_delay_s:
            raise ValueError("max_delay_s must be >= initial_delay_s")

    def delay_after_failed_attempt(self, failed_attempt: int) -> float:
        """Backoff delay after the 1-based ``failed_attempt`` failed."""
        if (
            isinstance(failed_attempt, bool)
            or not isinstance(failed_attempt, int)
            or failed_attempt < 1
        ):
            raise ValueError("failed_attempt must be an int >= 1")
        return min(
            self.initial_delay_s * (self.backoff_factor ** (failed_attempt - 1)),
            self.max_delay_s,
        )


class LibreVnaReconnector:
    """Controller ``reconnect_hook`` implementation for the LibreVNA backend.

    ``config`` is the frozen ``MissionConfig`` (or a provider callable, for
    applications that only know the config at ``configure`` time).  Calling
    the instance re-establishes the backend session with backoff and returns
    the re-applied :class:`AppliedConfig`.
    """

    def __init__(
        self,
        backend: LibreVnaUsbBackend,
        config: MissionConfig | Callable[[], MissionConfig],
        *,
        policy: LibreVnaReconnectPolicy | None = None,
        wait: Callable[[float], None] | None = None,
    ) -> None:
        if not isinstance(backend, LibreVnaUsbBackend):
            raise TypeError(
                f"backend must be a LibreVnaUsbBackend, got "
                f"{type(backend).__name__}"
            )
        if not isinstance(config, MissionConfig) and not callable(config):
            raise TypeError(
                "config must be a MissionConfig or a callable returning one"
            )
        if policy is not None and not isinstance(policy, LibreVnaReconnectPolicy):
            raise TypeError(
                f"policy must be a LibreVnaReconnectPolicy, got "
                f"{type(policy).__name__}"
            )
        if wait is not None and not callable(wait):
            raise TypeError(f"wait must be callable, got {type(wait).__name__}")
        self._backend = backend
        self._config: MissionConfig | Callable[[], MissionConfig] = config
        self._policy = policy if policy is not None else LibreVnaReconnectPolicy()
        # Default wait: the backend's cancellable wait (event-based), so a
        # close()/emergency_stop() during a backoff pause aborts the pause
        # immediately instead of sleeping through up to max_delay_s (P2-1).
        self._wait = wait if wait is not None else backend.wait_cancellable

    def _frozen_config(self) -> MissionConfig:
        config = self._config
        if callable(config):
            resolved = config()
        else:
            resolved = config
        if not isinstance(resolved, MissionConfig):
            raise TypeError("config provider must return a MissionConfig")
        return resolved

    def __call__(self) -> AppliedConfig:
        """Reconnect with backoff; return the re-applied configuration.

        Raises ``BackendCancelledError``/``BackendClosedError`` immediately
        (controller close / emergency stop), ``LibreVnaReconnectError`` after
        all attempts are exhausted (fail-closed).
        """
        backend = self._backend
        policy = self._policy
        wait = self._wait
        last_error: Exception | None = None
        for attempt in range(1, policy.max_attempts + 1):
            if backend.cancel_requested:
                raise BackendCancelledError(
                    "reconnect cancelled by the controller", attempt=attempt
                )
            try:
                return backend.reconnect_session(self._frozen_config())
            except (BackendCancelledError, BackendClosedError):
                raise  # controller close/emergency stop: never swallowed
            except Exception as exc:
                last_error = exc
                if attempt < policy.max_attempts:
                    wait(policy.delay_after_failed_attempt(attempt))
        assert last_error is not None  # max_attempts >= 1 guarantees a failure
        raise LibreVnaReconnectError(
            "LibreVNA reconnect failed after exhausting all attempts",
            attempts=policy.max_attempts,
            last_reason=type(last_error).__name__,
            last_message=str(last_error) or type(last_error).__name__,
        ) from last_error
