"""GNSS serial reader worker with bounded reconnect and fix cache (ISSUE-025).

Contract summary (docs/GNSS.md §3/§4, docs/issues/M05_GNSS.md ISSUE-025,
docs/reports/ISSUE_025_BASELINE_CONFIRMATION.md):

- ``GnssReader`` runs one independent worker thread: it opens the port through
  an injected serial factory, reads incrementally, splits lines across
  arbitrary read boundaries, calls the ISSUE-024 parser, assembles immutable
  ``GnssFix`` objects with caller-injected receive-side timestamps, publishes
  the six ``GnssStatus`` states (disconnected/no_sentence/no_fix/valid/stale/
  invalid) and feeds a time/capacity bounded thread-safe ``GnssFixCache``.
- I/O errors trigger deterministic bounded-backoff reconnects (same schedule
  convention as the LibreVNA reconnect policy, duplicated to keep the
  positioning -> core dependency direction) and bump ``generation`` on every
  successful (re)connect.  Parse/decode errors are counted and never stop the
  loop; GNSS failures are only reported through status and metrics -- they
  never propagate into radar acquisition.
- ``stop()`` is idempotent: it cancels a blocked read via ``adapter.close()``,
  joins the worker and releases the port exactly once.  Each opened adapter is
  closed exactly once by the reader (single ownership flag).
- No Qt, no serial import at module scope (pyserial is imported lazily inside
  ``PyserialSerialFactory`` only), no sweep matching, no map code, no fixed
  sleeps: every wait is either a cancellable ``Event.wait`` backoff, an
  adapter-level read timeout or a ``Condition`` wait.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Callable
from dataclasses import dataclass
from types import TracebackType
from typing import Protocol

from uav_gpr.core.enums import GnssStatus
from uav_gpr.core.gnss import GnssFix
from uav_gpr.core.timeutil import Clock, SystemClock
from uav_gpr.positioning.nmea import (
    MAX_NMEA_LINE_LEN,
    GgaResult,
    NmeaError,
    RmcResult,
    assemble_gnss_fix,
    parse_nmea,
)

__all__ = [
    "GnssFixCache",
    "GnssReader",
    "GnssReaderMetrics",
    "GnssReaderStatus",
    "GnssReconnectPolicy",
    "PyserialSerialAdapter",
    "PyserialSerialConfig",
    "PyserialSerialFactory",
    "SerialAdapter",
    "SerialAdapterClosedError",
    "SerialAdapterError",
]


class SerialAdapterError(Exception):
    """Serial I/O failure (device gone, driver error) -> reconnect path."""


class SerialAdapterClosedError(SerialAdapterError):
    """The adapter was closed underneath a pending read -> stop/disconnect path."""


class SerialAdapter(Protocol):
    """Minimal blocking serial surface the reader needs (fake friendly)."""

    def read(self, max_bytes: int) -> bytes:
        """Block until data is available or the adapter timeout elapses.

        Returns up to ``max_bytes`` bytes; ``b""`` means the adapter timeout
        elapsed without data.  Raises ``SerialAdapterClosedError`` once
        ``close()`` has been called and ``SerialAdapterError`` on I/O failure.
        """
        ...

    def close(self) -> None:
        """Release the port (idempotent) and unblock any pending read."""
        ...


SerialAdapterFactory = Callable[[], SerialAdapter]
"""Returns a connected adapter or raises ``SerialAdapterError``."""


@dataclass(frozen=True)
class GnssReconnectPolicy:
    """Deterministic exponential backoff, bounded by ``max_delay_s``.

    Same schedule convention as the LibreVNA reconnect policy (ISSUE-023):
    ``delay(n) = min(initial_delay_s * backoff_factor ** (n - 1), max_delay_s)``
    for the n-th consecutive failed connect attempt.  Duplicated on purpose:
    positioning must not import acquisition (AGENTS.md §9).
    """

    initial_delay_s: float = 0.5
    backoff_factor: float = 2.0
    max_delay_s: float = 8.0

    def __post_init__(self) -> None:
        if not (self.initial_delay_s > 0.0 and math.isfinite(self.initial_delay_s)):
            raise ValueError("initial_delay_s must be a positive finite float")
        if self.backoff_factor < 1.0 or not math.isfinite(self.backoff_factor):
            raise ValueError("backoff_factor must be a finite float >= 1.0")
        if not (self.max_delay_s >= self.initial_delay_s):
            raise ValueError("max_delay_s must be >= initial_delay_s")

    def delay_after_failed_attempt(self, failed_attempt: int) -> float:
        """Backoff seconds after the n-th consecutive failed attempt."""
        if failed_attempt < 1:
            raise ValueError(f"failed_attempt must be >= 1, got {failed_attempt}")
        return min(
            self.initial_delay_s * self.backoff_factor ** (failed_attempt - 1),
            self.max_delay_s,
        )


@dataclass(frozen=True)
class PyserialSerialConfig:
    """Connection parameters for :class:`PyserialSerialFactory` (validated only)."""

    port: str
    baudrate: int = 9600
    read_timeout_s: float = 2.0

    def __post_init__(self) -> None:
        if not isinstance(self.port, str) or not self.port:
            raise ValueError("port must be a non-empty string")
        if isinstance(self.baudrate, bool) or not isinstance(self.baudrate, int):
            raise ValueError("baudrate must be an int")
        if self.baudrate <= 0:
            raise ValueError("baudrate must be positive")
        if isinstance(self.read_timeout_s, bool) or (
            self.read_timeout_s <= 0.0
        ) or not math.isfinite(self.read_timeout_s):
            raise ValueError("read_timeout_s must be a positive finite float")


class _SerialPortLike(Protocol):
    """The slice of a pyserial ``Serial`` object the wrapper uses."""

    def read(self, size: int = 1) -> bytes:
        """pyserial blocking read."""
        ...

    def close(self) -> None:
        """pyserial port close."""
        ...


class PyserialSerialAdapter:
    """Wrap an already-opened pyserial port as a ``SerialAdapter``.

    The wrapper itself never imports pyserial: it works on any object with
    ``read``/``close`` (structural typing), so the default test suite can
    exercise it with a duck-typed fake.  ``close`` is idempotent.
    """

    def __init__(self, port: _SerialPortLike) -> None:
        self._port = port
        self._closed = False

    def read(self, max_bytes: int) -> bytes:
        if self._closed:
            raise SerialAdapterClosedError("serial port closed")
        try:
            return self._port.read(max_bytes)
        except Exception as exc:  # worker-boundary: mapped, never propagated
            if self._closed:
                raise SerialAdapterClosedError(
                    "serial port closed during read"
                ) from exc
            raise SerialAdapterError(f"serial read failed: {exc}") from exc

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._port.close()
        except Exception:
            # Best-effort cleanup (P3, ISSUE-025 review §10): the adapter is
            # already marked closed and the port handle is unusable either
            # way; a failing close must never propagate into the reader's
            # stop path.
            pass


class PyserialSerialFactory:
    """Open a real port via pyserial (lazy import); never called by default tests.

    Constructing the factory is side-effect free; calling it opens the COM
    port configured by :class:`PyserialSerialConfig`.
    """

    def __init__(self, config: PyserialSerialConfig) -> None:
        self._config = config

    def __call__(self) -> SerialAdapter:
        import serial  # type: ignore[import-untyped]

        port = serial.Serial(
            port=self._config.port,
            baudrate=self._config.baudrate,
            timeout=self._config.read_timeout_s,
        )
        return PyserialSerialAdapter(port)


@dataclass(frozen=True)
class GnssReaderMetrics:
    """Cumulative reader counters (monotonic, JSON-safe)."""

    gga_count: int = 0
    """Successfully parsed GGA sentences (each assembled into a GnssFix)."""

    rmc_count: int = 0
    """Successfully parsed RMC sentences."""

    invalid_count: int = 0
    """Rejected lines: decode failures, checksum/field errors, overlong lines."""

    overlong_line_count: int = 0
    """Lines dropped by the reader-side length guard (subset of invalid_count)."""

    io_error_count: int = 0
    """Read-side I/O failures including device-initiated closes."""

    open_error_count: int = 0
    """Failed connect attempts (factory raised SerialAdapterError)."""

    fixes_published: int = 0
    """All assembled GnssFix objects (valid and invalid) added to the cache."""


@dataclass(frozen=True)
class GnssReaderStatus:
    """Immutable status snapshot (pull model; safe for UI threads)."""

    status: GnssStatus
    generation: int
    """Number of successful (re)connects; 0 = never connected."""
    metrics: GnssReaderMetrics
    last_valid_fix_age_s: float | None
    last_invalid_reason: str | None


class GnssFixCache:
    """Thread-safe cache of immutable fixes, bounded by time and capacity.

    Insertion prunes entries older than ``max_age_s`` (measured on the
    injected clock's monotonic domain) and then evicts the oldest entries
    while ``max_items`` is exceeded.  ``snapshot`` prunes again and returns a
    fresh tuple ordered by ``received_monotonic_ns`` -- the internal list and
    its (frozen) elements are never exposed as mutable state.
    """

    def __init__(self, *, max_items: int, max_age_s: float, clock: Clock) -> None:
        if max_items < 1:
            raise ValueError("max_items must be >= 1")
        if not (max_age_s > 0.0 and math.isfinite(max_age_s)):
            raise ValueError("max_age_s must be a positive finite float")
        self._max_items = max_items
        self._max_age_ns = int(max_age_s * 1_000_000_000)
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: list[GnssFix] = []

    def add(self, fix: GnssFix) -> None:
        """Append one fix, then enforce the time and capacity bounds."""
        if not isinstance(fix, GnssFix):
            raise TypeError(f"fix must be a GnssFix, got {type(fix).__name__}")
        with self._lock:
            self._prune_locked()
            self._entries.append(fix)
            while len(self._entries) > self._max_items:
                self._entries.pop(0)

    def snapshot(self) -> tuple[GnssFix, ...]:
        """Prune expired entries and return the remaining fixes (oldest first)."""
        with self._lock:
            self._prune_locked()
            return tuple(
                sorted(self._entries, key=lambda fix: fix.received_monotonic_ns.ns)
            )

    def _prune_locked(self) -> None:
        now_ns = self._clock.monotonic_ns().ns
        self._entries = [
            fix
            for fix in self._entries
            if now_ns - fix.received_monotonic_ns.ns <= self._max_age_ns
        ]


class GnssReader:
    """Independent GNSS worker: serial -> lines -> parser -> fixes/status/cache.

    Lifecycle: construct, ``start()`` (or use as a context manager), poll
    ``status()``/``fixes()`` or use ``wait_for*``, ``stop()``.  Single
    lifecycle: a stopped reader cannot be restarted.  GNSS failures are only
    reported through status/metrics and never raised into the caller's
    acquisition path.

    The GNSS data-semantics thresholds are required constructor arguments
    with no silent defaults (captain ruling on ISSUE-025 decision points):
    ``stale_after_s`` is provided by the caller (the application layer later
    wires it from MissionConfig's GNSS max-age field; this module never
    imports config) and ``rmc_pair_window_s`` is validated to ``[0.0, 2.0]``
    (pairing happens within the same second or at most a 2 s window).
    """

    def __init__(
        self,
        factory: SerialAdapterFactory,
        *,
        clock: Clock | None = None,
        stale_after_s: float,
        rmc_pair_window_s: float,
        read_chunk_size: int = 1024,
        backoff: GnssReconnectPolicy | None = None,
        cache_max_items: int = 256,
        cache_max_age_s: float = 120.0,
        name: str = "gnss-reader",
    ) -> None:
        if not (stale_after_s > 0.0 and math.isfinite(stale_after_s)):
            raise ValueError("stale_after_s must be a positive finite float")
        if not (
            0.0 <= rmc_pair_window_s <= 2.0 and math.isfinite(rmc_pair_window_s)
        ):
            raise ValueError(
                "rmc_pair_window_s must be a finite float in [0.0, 2.0]"
            )
        if read_chunk_size < 1:
            raise ValueError("read_chunk_size must be >= 1")
        self._factory = factory
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._stale_after_s = stale_after_s
        self._stale_after_ns = int(stale_after_s * 1_000_000_000)
        self._rmc_pair_window_ns = int(rmc_pair_window_s * 1_000_000_000)
        self._read_chunk_size = read_chunk_size
        self._backoff = backoff if backoff is not None else GnssReconnectPolicy()
        self._cache = GnssFixCache(
            max_items=cache_max_items, max_age_s=cache_max_age_s, clock=self._clock
        )
        self._name = name

        self._cond = threading.Condition()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = False

        # Shared state, guarded by ``self._cond``.
        self._connected = False
        self._generation = 0
        self._gga_count = 0
        self._rmc_count = 0
        self._invalid_count = 0
        self._overlong_line_count = 0
        self._io_error_count = 0
        self._open_error_count = 0
        self._fixes_published = 0
        self._parsed_since_connect = False
        self._last_event_invalid = False
        self._last_invalid_reason: str | None = None
        self._last_valid_fix_mono_ns: int | None = None
        self._adapter: SerialAdapter | None = None
        self._adapter_open = False
        self._status: GnssStatus = GnssStatus.DISCONNECTED
        self._snapshot = self._build_snapshot_locked()

        # Worker-thread-only state (no lock needed).
        self._buffer = bytearray()
        self._overflow = False
        self._last_rmc: RmcResult | None = None
        self._last_rmc_mono_ns: int | None = None

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Spawn the worker thread (single lifecycle; rejects restarts)."""
        with self._cond:
            if self._started:
                raise RuntimeError(
                    "GnssReader already started; create a new reader after stop"
                )
            self._started = True
        self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
        self._thread.start()

    def stop(self, join_timeout_s: float = 5.0) -> None:
        """Idempotently cancel the worker and release the port exactly once."""
        if join_timeout_s < 0.0:
            raise ValueError("join_timeout_s must be >= 0")
        self._stop_event.set()
        self._close_adapter()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(join_timeout_s)

    def __enter__(self) -> GnssReader:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop()

    # -- observation --------------------------------------------------------

    def status(self) -> GnssReaderStatus:
        """Thread-safe immutable status snapshot."""
        with self._cond:
            return self._snapshot

    def fixes(self) -> tuple[GnssFix, ...]:
        """Thread-safe cache snapshot (oldest first, frozen elements)."""
        return self._cache.snapshot()

    def is_alive(self) -> bool:
        """True while the worker thread is running."""
        thread = self._thread
        return thread is not None and thread.is_alive()

    def wait_for(
        self, predicate: Callable[[GnssReaderStatus], bool], timeout_s: float = 5.0
    ) -> bool:
        """Block until ``predicate(status)`` holds or the timeout elapses."""
        with self._cond:
            return self._cond.wait_for(
                lambda: predicate(self._snapshot), timeout=timeout_s
            )

    def wait_for_status(self, target: GnssStatus, timeout_s: float = 5.0) -> bool:
        """Block until the published status equals ``target`` or timeout."""
        return self.wait_for(lambda snapshot: snapshot.status is target, timeout_s)

    # -- worker loop --------------------------------------------------------

    def _run(self) -> None:
        # Consecutive failures since the last non-empty read (open failures,
        # read I/O errors, unexpected errors).  Reset only by real progress:
        # a connect into a dead device fails on the first read and keeps
        # backing off, so continuous I/O errors never tight-loop (GNSS.md §3).
        failed_attempts = 0
        try:
            while not self._stop_event.is_set():
                if self._adapter is None:
                    if self._try_connect() is None:
                        failed_attempts += 1
                        self._record_open_error()
                        if self._backoff_wait(failed_attempts):
                            break
                    continue
                adapter = self._adapter
                try:
                    chunk = adapter.read(self._read_chunk_size)
                except SerialAdapterClosedError:
                    if self._stop_event.is_set():
                        break
                    failed_attempts += 1
                    self._record_io_error()
                    self._close_adapter()
                    if self._backoff_wait(failed_attempts):
                        break
                    continue
                except SerialAdapterError:
                    failed_attempts += 1
                    self._record_io_error()
                    self._close_adapter()
                    if self._backoff_wait(failed_attempts):
                        break
                    continue
                except Exception as exc:  # never propagate into radar acquisition
                    failed_attempts += 1
                    self._record_unexpected(exc)
                    self._close_adapter()
                    if self._backoff_wait(failed_attempts):
                        break
                    continue
                if chunk:
                    failed_attempts = 0
                    self._ingest(chunk)
                else:
                    self._on_read_timeout_tick()
        finally:
            self._close_adapter()
            with self._cond:
                self._publish_status_locked()

    def _backoff_wait(self, failed_attempt: int) -> bool:
        """Cancellable backoff pause; ``True`` means stop was requested."""
        return self._stop_event.wait(
            self._backoff.delay_after_failed_attempt(failed_attempt)
        )

    def _try_connect(self) -> SerialAdapter | None:
        try:
            adapter = self._factory()
        except SerialAdapterError:
            return None
        except Exception as exc:  # factory bugs are connect failures, not crashes
            self._record_unexpected(exc)
            return None
        with self._cond:
            self._adapter = adapter
            self._adapter_open = True
            self._connected = True
            self._generation += 1
            self._parsed_since_connect = False
            self._publish_status_locked()
        return adapter

    def _close_adapter(self) -> None:
        with self._cond:
            adapter = self._adapter
            should_close = self._adapter_open
            self._adapter = None
            self._adapter_open = False
            self._connected = False
            self._publish_status_locked()
        if adapter is not None and should_close:
            adapter.close()

    # -- line assembly and parsing (worker thread only) ----------------------

    def _ingest(self, chunk: bytes) -> None:
        self._buffer.extend(chunk)
        while True:
            if self._overflow:
                newline = self._buffer.find(b"\n")
                if newline < 0:
                    self._buffer.clear()
                    return
                del self._buffer[: newline + 1]
                self._overflow = False
                continue
            newline = self._buffer.find(b"\n")
            if newline < 0:
                if len(self._buffer) > MAX_NMEA_LINE_LEN:
                    self._overflow = True
                    self._buffer.clear()
                    self._record_invalid("overlong_line", overlong=True)
                return
            raw = bytes(self._buffer[:newline])
            del self._buffer[: newline + 1]
            line = raw.rstrip(b"\r")
            if not line:
                continue
            self._handle_line(line)

    def _handle_line(self, line: bytes) -> None:
        try:
            text = line.decode("ascii")
        except UnicodeDecodeError:
            self._record_invalid("non_ascii")
            return
        try:
            result = parse_nmea(text)
        except NmeaError as exc:
            self._record_invalid(exc.reason.value)
            return
        now = self._clock.monotonic_ns()
        if isinstance(result, GgaResult):
            rmc: RmcResult | None = None
            if (
                self._last_rmc is not None
                and self._last_rmc_mono_ns is not None
                and now.ns - self._last_rmc_mono_ns <= self._rmc_pair_window_ns
            ):
                rmc = self._last_rmc
            fix = assemble_gnss_fix(
                result,
                received_utc=self._clock.utc_now(),
                received_monotonic_ns=now,
                rmc=rmc,
            )
            self._publish_fix(fix)
            return
        if isinstance(result, RmcResult):
            self._record_rmc(result, now.ns)

    def _publish_fix(self, fix: GnssFix) -> None:
        self._cache.add(fix)
        with self._cond:
            self._gga_count += 1
            self._fixes_published += 1
            self._parsed_since_connect = True
            self._last_event_invalid = False
            if fix.valid:
                self._last_valid_fix_mono_ns = fix.received_monotonic_ns.ns
            self._publish_status_locked()

    def _record_rmc(self, result: RmcResult, now_ns: int) -> None:
        if result.status_valid:
            self._last_rmc = result
            self._last_rmc_mono_ns = now_ns
        with self._cond:
            self._rmc_count += 1
            self._parsed_since_connect = True
            self._last_event_invalid = False
            self._publish_status_locked()

    # -- state recording and publication -------------------------------------

    def _record_invalid(self, reason: str, *, overlong: bool = False) -> None:
        with self._cond:
            self._invalid_count += 1
            if overlong:
                self._overlong_line_count += 1
            self._last_invalid_reason = reason
            self._last_event_invalid = True
            self._publish_status_locked()

    def _record_io_error(self) -> None:
        with self._cond:
            self._io_error_count += 1
            self._publish_status_locked()

    def _record_open_error(self) -> None:
        with self._cond:
            self._open_error_count += 1
            self._publish_status_locked()

    def _record_unexpected(self, exc: Exception) -> None:
        with self._cond:
            self._io_error_count += 1
            self._last_invalid_reason = f"unexpected: {exc!r}"
            self._last_event_invalid = True
            self._publish_status_locked()

    def _on_read_timeout_tick(self) -> None:
        with self._cond:
            self._publish_status_locked()

    def _publish_status_locked(self) -> None:
        self._status = self._recompute_status_locked()
        self._snapshot = self._build_snapshot_locked()
        self._cond.notify_all()

    def _recompute_status_locked(self) -> GnssStatus:
        """Six-state mapping per docs/GNSS.md §4 (deterministic precedence)."""
        if not self._connected:
            return GnssStatus.DISCONNECTED
        now_ns = self._clock.monotonic_ns().ns
        if self._last_valid_fix_mono_ns is not None:
            if now_ns - self._last_valid_fix_mono_ns > self._stale_after_ns:
                return GnssStatus.STALE
            return GnssStatus.VALID
        if self._last_event_invalid:
            return GnssStatus.INVALID
        if self._parsed_since_connect:
            return GnssStatus.NO_FIX
        return GnssStatus.NO_SENTENCE

    def _build_snapshot_locked(self) -> GnssReaderStatus:
        now_ns = self._clock.monotonic_ns().ns
        age: float | None = None
        if self._last_valid_fix_mono_ns is not None:
            age = max((now_ns - self._last_valid_fix_mono_ns) / 1_000_000_000, 0.0)
        return GnssReaderStatus(
            status=self._status,
            generation=self._generation,
            metrics=GnssReaderMetrics(
                gga_count=self._gga_count,
                rmc_count=self._rmc_count,
                invalid_count=self._invalid_count,
                overlong_line_count=self._overlong_line_count,
                io_error_count=self._io_error_count,
                open_error_count=self._open_error_count,
                fixes_published=self._fixes_published,
            ),
            last_valid_fix_age_s=age,
            last_invalid_reason=self._last_invalid_reason,
        )
