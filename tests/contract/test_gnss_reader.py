"""Contract tests for the ISSUE-025 GNSS reader, reconnect and bounded fix cache.

Everything runs against a scripted fake serial adapter and an injected test
clock: no test opens a real COM port and no test sleeps on fixed delays.
Synchronization uses the reader's condition-variable ``wait_for*`` helpers,
bounded joins and event-driven adapter scripts (bytes chunks, raised
exceptions or callables executed inside the worker's read call).
"""

from __future__ import annotations

import ast
import queue
import threading
from collections.abc import Callable
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from uav_gpr.core.enums import GnssFixQuality, GnssStatus, GnssUnavailableReason
from uav_gpr.core.gnss import GnssFix
from uav_gpr.core.timeutil import MonotonicNs
from uav_gpr.positioning.reader import (
    GnssFixCache,
    GnssReader,
    GnssReconnectPolicy,
    PyserialSerialAdapter,
    PyserialSerialConfig,
    PyserialSerialFactory,
    SerialAdapterClosedError,
    SerialAdapterError,
)

_READER_TIMEOUT_S = 5.0


# ---------------------------------------------------------------------------
# Test doubles: scripted clock, scripted serial adapter, scripted factory
# ---------------------------------------------------------------------------


class ScriptedClock:
    """Deterministic Clock advanced explicitly (also from worker-thread hooks)."""

    def __init__(self) -> None:
        self._utc = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)
        self._mono_ns = 1_000_000_000
        self._lock = threading.Lock()

    def utc_now(self) -> datetime:
        with self._lock:
            return self._utc

    def monotonic_ns(self) -> MonotonicNs:
        with self._lock:
            return MonotonicNs(self._mono_ns)

    def advance(self, *, seconds: float) -> None:
        with self._lock:
            self._mono_ns += int(seconds * 1_000_000_000)
            self._utc = self._utc + timedelta(seconds=seconds)


class ScriptedSerialAdapter:
    """Fake SerialAdapter driven by a queue script; close unblocks pending read.

    Fed byte chunks accumulate in an internal pending buffer and ``read``
    drains up to ``max_bytes`` per call, keeping the remainder for the next
    call -- exactly like a real serial port's OS buffer (a queued chunk larger
    than ``max_bytes`` is never truncated away).
    """

    def __init__(self, *, read_timeout_s: float = 0.05) -> None:
        self._script: queue.Queue[bytes | Exception | Callable[[], None] | None] = (
            queue.Queue()
        )
        self._closed = threading.Event()
        self._pending = bytearray()
        self.read_timeout_s = read_timeout_s
        self.close_count = 0
        self.read_calls = 0

    def feed(self, item: bytes | Exception | Callable[[], None] | None) -> None:
        self._script.put(item)

    def read(self, max_bytes: int) -> bytes:
        self.read_calls += 1
        while True:
            if self._closed.is_set():
                raise SerialAdapterClosedError("scripted adapter closed")
            if self._pending:
                out = bytes(self._pending[:max_bytes])
                del self._pending[:max_bytes]
                return out
            try:
                item = self._script.get(timeout=self.read_timeout_s)
            except queue.Empty:
                return b""
            if item is None:
                raise SerialAdapterClosedError("scripted adapter closed by sentinel")
            if isinstance(item, Exception):
                raise item
            if callable(item):
                item()
                continue
            self._pending.extend(item)

    def close(self) -> None:
        self.close_count += 1
        self._closed.set()
        self._script.put(None)


class ScriptedFactory:
    """Factory serving scripted adapters, then failing open attempts."""

    def __init__(self, *adapters: ScriptedSerialAdapter) -> None:
        self._adapters = list(adapters)
        self.calls = 0

    def __call__(self) -> ScriptedSerialAdapter:
        self.calls += 1
        if not self._adapters:
            raise SerialAdapterError("scripted factory exhausted")
        return self._adapters.pop(0)


class FlakyFactory:
    """Factory failing the first N open attempts, then serving one adapter."""

    def __init__(self, adapter: ScriptedSerialAdapter, failures: int) -> None:
        self._adapter = adapter
        self._failures = failures
        self.calls = 0

    def __call__(self) -> ScriptedSerialAdapter:
        self.calls += 1
        if self._failures > 0:
            self._failures -= 1
            raise SerialAdapterError("scripted open failure")
        return self._adapter


def make_reader(factory: Callable[[], object], clock: ScriptedClock, **overrides) -> GnssReader:
    """GnssReader with fast deterministic test defaults (tiny backoff)."""
    options: dict[str, object] = {
        "stale_after_s": 0.2,
        "rmc_pair_window_s": 2.0,
        "read_chunk_size": 64,
        "backoff": GnssReconnectPolicy(
            initial_delay_s=0.001, backoff_factor=2.0, max_delay_s=0.004
        ),
        "cache_max_items": 64,
        "cache_max_age_s": 60.0,
    }
    options.update(overrides)
    return GnssReader(factory, clock=clock, **options)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Anonymous synthetic NMEA fixtures (checksums computed, no reference files)
# ---------------------------------------------------------------------------


def _checksum(body: str) -> str:
    xor = 0
    for char in body:
        xor ^= ord(char)
    return f"{xor:02X}"


def _sentence(body: str) -> bytes:
    return f"${body}*{_checksum(body)}\r\n".encode("ascii")


def gga_line(
    *,
    time: str = "120000.00",
    quality: int = 1,
    sats: int = 8,
    hdop: str = "0.9",
    alt: str = "123.4",
) -> bytes:
    body = (
        f"GPGGA,{time},4807.038,N,01131.000,E,{quality},{sats},{hdop},"
        f"{alt},M,47.6,M,3.2,0123"
    )
    return _sentence(body)


def rmc_line(
    *,
    time: str = "120001.00",
    status: str = "A",
    speed: str = "4.5",
    course: str = "054.7",
    date: str = "020926",
) -> bytes:
    body = f"GPRMC,{time},{status},4807.038,N,01131.000,E,{speed},{course},{date},,,A"
    return _sentence(body)


def make_fix(
    *,
    mono_ns: int = 0,
    valid: bool = True,
) -> GnssFix:
    """Build a GnssFix directly for cache-level tests."""
    return GnssFix(
        received_utc=datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC),
        nmea_utc=None,
        received_monotonic_ns=MonotonicNs(mono_ns),
        latitude_deg=48.1 if valid else None,
        longitude_deg=11.5 if valid else None,
        altitude_msl_m=100.0,
        geoid_separation_m=47.0,
        fix_quality=GnssFixQuality.GPS_FIX if valid else GnssFixQuality.INVALID,
        satellites=8,
        hdop=0.9,
        ground_speed_mps=None,
        course_deg=None,
        valid=valid,
        invalid_reason=None if valid else GnssUnavailableReason.NO_FIX,
    )


# ---------------------------------------------------------------------------
# Reader state machine tests
# ---------------------------------------------------------------------------


def test_reader_is_disconnected_before_start_and_no_sentence_after_connect() -> None:
    clock = ScriptedClock()
    adapter = ScriptedSerialAdapter()
    reader = make_reader(ScriptedFactory(adapter), clock)

    status = reader.status()
    assert status.status is GnssStatus.DISCONNECTED
    assert status.generation == 0
    assert status.metrics.gga_count == 0
    assert status.last_valid_fix_age_s is None

    with reader:
        assert reader.wait_for_status(GnssStatus.NO_SENTENCE, _READER_TIMEOUT_S)
        assert reader.status().generation == 1


def test_arbitrary_chunk_splitting_assembles_lines_across_reads() -> None:
    clock = ScriptedClock()
    adapter = ScriptedSerialAdapter()
    payload = gga_line() + rmc_line()
    for chunk_start in range(0, len(payload), 3):
        adapter.feed(payload[chunk_start : chunk_start + 3])

    with make_reader(ScriptedFactory(adapter), clock) as reader:
        assert reader.wait_for(
            lambda s: s.metrics.gga_count == 1 and s.metrics.rmc_count == 1,
            _READER_TIMEOUT_S,
        )
        assert reader.status().status is GnssStatus.VALID


@pytest.mark.parametrize("chunk_size", [1, 2, 7, 4096])
def test_line_assembly_for_parametrized_chunk_sizes(chunk_size: int) -> None:
    clock = ScriptedClock()
    adapter = ScriptedSerialAdapter()
    payload = gga_line()
    for chunk_start in range(0, len(payload), chunk_size):
        adapter.feed(payload[chunk_start : chunk_start + chunk_size])

    with make_reader(
        ScriptedFactory(adapter), clock, read_chunk_size=chunk_size
    ) as reader:
        assert reader.wait_for(
            lambda s: s.metrics.gga_count == 1, _READER_TIMEOUT_S
        )
        assert reader.status().status is GnssStatus.VALID


def test_empty_lines_are_skipped_silently() -> None:
    clock = ScriptedClock()
    adapter = ScriptedSerialAdapter()
    adapter.feed(b"\r\n")
    adapter.feed(b"\n")
    adapter.feed(b"\r\n")
    adapter.feed(gga_line())

    with make_reader(ScriptedFactory(adapter), clock) as reader:
        assert reader.wait_for(lambda s: s.metrics.gga_count == 1, _READER_TIMEOUT_S)
        status = reader.status()
        assert status.metrics.invalid_count == 0
        assert status.status is GnssStatus.VALID


def test_bad_checksum_is_counted_and_reader_recovers() -> None:
    clock = ScriptedClock()
    adapter = ScriptedSerialAdapter()
    bad = gga_line().replace(b"4807", b"4808")
    adapter.feed(bad)
    adapter.feed(gga_line())

    with make_reader(ScriptedFactory(adapter), clock) as reader:
        assert reader.wait_for(
            lambda s: s.metrics.gga_count == 1 and s.metrics.invalid_count == 1,
            _READER_TIMEOUT_S,
        )
        status = reader.status()
        assert status.status is GnssStatus.VALID
        assert status.last_invalid_reason == "bad_checksum"


def test_invalid_state_while_only_bad_sentences_arrive() -> None:
    clock = ScriptedClock()
    adapter = ScriptedSerialAdapter()
    adapter.feed(gga_line().replace(b"4807", b"4808"))

    with make_reader(ScriptedFactory(adapter), clock) as reader:
        assert reader.wait_for_status(GnssStatus.INVALID, _READER_TIMEOUT_S)
        status = reader.status()
        assert status.metrics.gga_count == 0
        assert status.metrics.invalid_count == 1


def test_non_ascii_line_is_rejected_and_reader_recovers() -> None:
    clock = ScriptedClock()
    adapter = ScriptedSerialAdapter()
    adapter.feed(b"$GPGGA," + b"\xff" * 5 + b"\r\n")
    adapter.feed(gga_line())

    with make_reader(ScriptedFactory(adapter), clock) as reader:
        assert reader.wait_for(
            lambda s: s.metrics.invalid_count == 1 and s.metrics.gga_count == 1,
            _READER_TIMEOUT_S,
        )
        status = reader.status()
        assert status.last_invalid_reason == "non_ascii"
        assert status.status is GnssStatus.VALID


def test_overlong_line_without_newline_is_dropped_and_resyncs() -> None:
    clock = ScriptedClock()
    adapter = ScriptedSerialAdapter()
    adapter.feed(b"A" * 300)
    adapter.feed(b"BBB\r\n")
    adapter.feed(gga_line())

    with make_reader(ScriptedFactory(adapter), clock) as reader:
        assert reader.wait_for(
            lambda s: s.metrics.gga_count == 1, _READER_TIMEOUT_S
        )
        status = reader.status()
        assert status.metrics.overlong_line_count == 1
        assert status.metrics.invalid_count == 1
        assert status.last_invalid_reason == "overlong_line"
        assert status.status is GnssStatus.VALID


def test_quality_zero_gga_publishes_no_fix_status_and_invalid_fix() -> None:
    clock = ScriptedClock()
    adapter = ScriptedSerialAdapter()
    adapter.feed(gga_line(quality=0))

    with make_reader(ScriptedFactory(adapter), clock) as reader:
        assert reader.wait_for_status(GnssStatus.NO_FIX, _READER_TIMEOUT_S)
        status = reader.status()
        assert status.metrics.fixes_published == 1
        fixes = reader.fixes()
        assert len(fixes) == 1
        fix = fixes[0]
        assert fix.valid is False
        assert fix.invalid_reason is GnssUnavailableReason.NO_FIX
        assert fix.latitude_deg is None and fix.longitude_deg is None
        assert fix.altitude_msl_m == 123.4
        assert fix.satellites == 8
        assert fix.hdop == 0.9


def test_valid_gga_publishes_fix_with_parsed_fields() -> None:
    clock = ScriptedClock()
    adapter = ScriptedSerialAdapter()
    adapter.feed(gga_line())

    with make_reader(ScriptedFactory(adapter), clock) as reader:
        assert reader.wait_for_status(GnssStatus.VALID, _READER_TIMEOUT_S)
        fix = reader.fixes()[0]
        assert fix.valid is True
        assert fix.latitude_deg == pytest.approx(48.1173)
        assert fix.longitude_deg == pytest.approx(11.5166667, abs=1e-6)
        assert fix.altitude_msl_m == 123.4
        assert fix.geoid_separation_m == 47.6
        assert fix.satellites == 8
        assert fix.hdop == 0.9
        assert fix.fix_quality is GnssFixQuality.GPS_FIX
        # No RMC seen: no date source -> no fabricated NMEA UTC.
        assert fix.nmea_utc is None
        # Receive-side facts come from the injected clock, never fabricated.
        assert fix.received_monotonic_ns == clock.monotonic_ns()
        assert fix.received_utc == clock.utc_now()


def test_rmc_pairs_date_speed_and_course_into_the_fix() -> None:
    clock = ScriptedClock()
    adapter = ScriptedSerialAdapter()
    adapter.feed(rmc_line())
    adapter.feed(gga_line())

    with make_reader(ScriptedFactory(adapter), clock) as reader:
        assert reader.wait_for(
            lambda s: s.metrics.gga_count == 1 and s.metrics.rmc_count == 1,
            _READER_TIMEOUT_S,
        )
        fix = reader.fixes()[0]
        assert fix.ground_speed_mps == pytest.approx(2.315)
        assert fix.course_deg == pytest.approx(54.7)
        assert fix.nmea_utc == datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)


def test_rmc_pairs_at_exactly_the_2s_window_boundary() -> None:
    clock = ScriptedClock()
    adapter = ScriptedSerialAdapter()
    adapter.feed(rmc_line())

    with make_reader(ScriptedFactory(adapter), clock) as reader:
        assert reader.wait_for(lambda s: s.metrics.rmc_count == 1, _READER_TIMEOUT_S)
        adapter.feed(lambda: clock.advance(seconds=2.0))
        adapter.feed(gga_line())
        assert reader.wait_for(lambda s: s.metrics.gga_count == 1, _READER_TIMEOUT_S)
        fix = reader.fixes()[0]
        # delta == window (<=): still paired.
        assert fix.ground_speed_mps == pytest.approx(2.315)
        assert fix.nmea_utc is not None


def test_rmc_older_than_2s_window_is_not_paired() -> None:
    clock = ScriptedClock()
    adapter = ScriptedSerialAdapter()
    adapter.feed(rmc_line())

    with make_reader(ScriptedFactory(adapter), clock) as reader:
        assert reader.wait_for(lambda s: s.metrics.rmc_count == 1, _READER_TIMEOUT_S)
        adapter.feed(lambda: clock.advance(seconds=2.5))
        adapter.feed(gga_line())
        assert reader.wait_for(lambda s: s.metrics.gga_count == 1, _READER_TIMEOUT_S)
        fix = reader.fixes()[0]
        assert fix.ground_speed_mps is None
        assert fix.course_deg is None
        assert fix.nmea_utc is None


def test_rmc_only_stream_reports_no_fix() -> None:
    clock = ScriptedClock()
    adapter = ScriptedSerialAdapter()
    adapter.feed(rmc_line())

    with make_reader(ScriptedFactory(adapter), clock) as reader:
        assert reader.wait_for_status(GnssStatus.NO_FIX, _READER_TIMEOUT_S)
        status = reader.status()
        assert status.metrics.rmc_count == 1
        assert status.metrics.gga_count == 0
        assert status.metrics.fixes_published == 0
        assert reader.fixes() == ()


def test_fix_becomes_stale_without_new_data_and_recovers() -> None:
    clock = ScriptedClock()
    adapter = ScriptedSerialAdapter()
    adapter.feed(gga_line())

    with make_reader(ScriptedFactory(adapter), clock) as reader:
        assert reader.wait_for_status(GnssStatus.VALID, _READER_TIMEOUT_S)
        # Advance the injected clock from inside the worker's read call: each
        # empty read re-evaluates staleness deterministically.
        adapter.feed(lambda: clock.advance(seconds=0.15))
        adapter.feed(lambda: clock.advance(seconds=0.15))
        assert reader.wait_for_status(GnssStatus.STALE, _READER_TIMEOUT_S)
        age = reader.status().last_valid_fix_age_s
        assert age is not None and age > 0.2

        adapter.feed(gga_line(time="120100.00"))
        assert reader.wait_for_status(GnssStatus.VALID, _READER_TIMEOUT_S)


# ---------------------------------------------------------------------------
# Reconnect / generation / stop tests
# ---------------------------------------------------------------------------


def test_io_error_reconnects_with_generation_bump() -> None:
    clock = ScriptedClock()
    first = ScriptedSerialAdapter()
    first.feed(gga_line())
    first.feed(SerialAdapterError("cable unplugged"))
    second = ScriptedSerialAdapter()
    second.feed(gga_line(time="120100.00"))
    factory = ScriptedFactory(first, second)

    with make_reader(factory, clock) as reader:
        assert reader.wait_for(
            lambda s: s.metrics.gga_count == 2 and s.generation == 2,
            _READER_TIMEOUT_S,
        )
        status = reader.status()
        assert status.metrics.io_error_count == 1
        assert status.status is GnssStatus.VALID
        assert factory.calls == 2
        assert first.close_count == 1


def test_open_failures_back_off_then_connect_with_generation_one() -> None:
    clock = ScriptedClock()
    adapter = ScriptedSerialAdapter()
    factory = FlakyFactory(adapter, failures=2)

    with make_reader(factory, clock) as reader:
        assert reader.wait_for_status(GnssStatus.NO_SENTENCE, _READER_TIMEOUT_S)
        status = reader.status()
        assert status.generation == 1
        assert status.metrics.open_error_count == 2
        assert factory.calls == 3
        adapter.feed(gga_line())
        assert reader.wait_for_status(GnssStatus.VALID, _READER_TIMEOUT_S)


def test_reconnect_policy_delay_is_bounded_exponential() -> None:
    policy = GnssReconnectPolicy(
        initial_delay_s=0.5, backoff_factor=2.0, max_delay_s=8.0
    )
    assert policy.delay_after_failed_attempt(1) == pytest.approx(0.5)
    assert policy.delay_after_failed_attempt(2) == pytest.approx(1.0)
    assert policy.delay_after_failed_attempt(3) == pytest.approx(2.0)
    assert policy.delay_after_failed_attempt(4) == pytest.approx(4.0)
    assert policy.delay_after_failed_attempt(5) == pytest.approx(8.0)
    assert policy.delay_after_failed_attempt(100) == pytest.approx(8.0)
    with pytest.raises(ValueError):
        policy.delay_after_failed_attempt(0)
    with pytest.raises(ValueError):
        GnssReconnectPolicy(initial_delay_s=0.0, backoff_factor=2.0, max_delay_s=8.0)
    with pytest.raises(ValueError):
        GnssReconnectPolicy(initial_delay_s=0.5, backoff_factor=0.5, max_delay_s=8.0)
    with pytest.raises(ValueError):
        GnssReconnectPolicy(initial_delay_s=0.5, backoff_factor=2.0, max_delay_s=0.1)


def test_stop_cancels_blocked_read_and_releases_port_once() -> None:
    clock = ScriptedClock()
    adapter = ScriptedSerialAdapter(read_timeout_s=30.0)
    reader = make_reader(ScriptedFactory(adapter), clock)

    reader.start()
    assert reader.wait_for_status(GnssStatus.NO_SENTENCE, _READER_TIMEOUT_S)
    assert reader.is_alive()

    reader.stop(join_timeout_s=2.0)
    assert not reader.is_alive()
    assert adapter.close_count == 1
    reader.stop()
    assert adapter.close_count == 1


def test_stop_during_backoff_aborts_reconnect_loop() -> None:
    clock = ScriptedClock()
    factory = ScriptedFactory()  # always fails open
    reader = make_reader(
        factory,
        clock,
        backoff=GnssReconnectPolicy(
            initial_delay_s=0.05, backoff_factor=2.0, max_delay_s=0.2
        ),
    )

    reader.start()
    assert reader.wait_for(
        lambda s: s.metrics.open_error_count >= 1, _READER_TIMEOUT_S
    )
    reader.stop(join_timeout_s=2.0)
    assert not reader.is_alive()
    calls_after_stop = factory.calls
    reader.stop()
    assert factory.calls == calls_after_stop
    assert reader.status().status is GnssStatus.DISCONNECTED


def test_worker_never_propagates_errors_into_the_caller() -> None:
    clock = ScriptedClock()
    adapter = ScriptedSerialAdapter()
    adapter.feed(gga_line())
    adapter.feed(RuntimeError("unexpected worker bug"))
    factory = ScriptedFactory(adapter)

    with make_reader(factory, clock) as reader:
        assert reader.wait_for(lambda s: s.metrics.gga_count == 1, _READER_TIMEOUT_S)
        assert reader.wait_for(
            lambda s: s.metrics.io_error_count >= 1, _READER_TIMEOUT_S
        )
        status = reader.status()
        assert status.metrics.gga_count == 1
        # The unexpected error was only reported, never raised into the caller.
        assert status.last_invalid_reason is not None
        assert status.last_invalid_reason.startswith("unexpected: ")
        assert reader.is_alive()
        assert reader.wait_for_status(GnssStatus.DISCONNECTED, _READER_TIMEOUT_S)


def test_high_frequency_input_keeps_cache_bounded() -> None:
    clock = ScriptedClock()
    adapter = ScriptedSerialAdapter()
    total = 2000
    payload = b"".join(gga_line() for _ in range(total))
    chunk_size = 997
    for chunk_start in range(0, len(payload), chunk_size):
        adapter.feed(payload[chunk_start : chunk_start + chunk_size])

    with make_reader(ScriptedFactory(adapter), clock) as reader:
        # Safety bound only: parsing 2000 sentences normally completes in
        # <0.1 s; the generous bound avoids load-induced flakes in the full
        # suite while staying event-driven (no fixed sleeps).
        assert reader.wait_for(
            lambda s: s.metrics.gga_count == total, 30.0
        )
        status = reader.status()
        assert status.metrics.fixes_published == total
        assert status.metrics.invalid_count == 0
        assert status.status is GnssStatus.VALID
        fixes = reader.fixes()
        assert len(fixes) == 64  # cache_max_items
        monos = [fix.received_monotonic_ns.ns for fix in fixes]
        assert monos == sorted(monos)


def test_wait_for_times_out_without_matching_status() -> None:
    clock = ScriptedClock()
    adapter = ScriptedSerialAdapter()
    with make_reader(ScriptedFactory(adapter), clock) as reader:
        assert reader.wait_for_status(GnssStatus.NO_SENTENCE, _READER_TIMEOUT_S)
        assert reader.wait_for(lambda s: s.generation > 5, timeout_s=0.05) is False


def test_context_manager_stops_worker_on_exit() -> None:
    clock = ScriptedClock()
    adapter = ScriptedSerialAdapter()
    reader = make_reader(ScriptedFactory(adapter), clock)
    with reader:
        assert reader.wait_for_status(GnssStatus.NO_SENTENCE, _READER_TIMEOUT_S)
        assert reader.is_alive()
    assert not reader.is_alive()
    with pytest.raises(RuntimeError):
        reader.start()


def test_double_start_is_rejected() -> None:
    clock = ScriptedClock()
    adapter = ScriptedSerialAdapter()
    reader = make_reader(ScriptedFactory(adapter), clock)
    reader.start()
    try:
        assert reader.wait_for_status(GnssStatus.NO_SENTENCE, _READER_TIMEOUT_S)
        with pytest.raises(RuntimeError):
            reader.start()
    finally:
        reader.stop()
    assert not reader.is_alive()


def test_gnss_thresholds_are_required_without_silent_defaults() -> None:
    clock = ScriptedClock()
    adapter = ScriptedSerialAdapter()
    factory = ScriptedFactory(adapter)
    # Data-semantics thresholds must be passed explicitly (no silent defaults).
    with pytest.raises(TypeError):
        GnssReader(factory, clock=clock)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        GnssReader(factory, clock=clock, stale_after_s=10.0)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        GnssReader(factory, clock=clock, stale_after_s=0.0, rmc_pair_window_s=2.0)
    with pytest.raises(ValueError):
        GnssReader(factory, clock=clock, stale_after_s=10.0, rmc_pair_window_s=2.5)
    with pytest.raises(ValueError):
        GnssReader(factory, clock=clock, stale_after_s=10.0, rmc_pair_window_s=-0.1)


def test_status_snapshot_is_frozen() -> None:
    clock = ScriptedClock()
    adapter = ScriptedSerialAdapter()
    with make_reader(ScriptedFactory(adapter), clock) as reader:
        assert reader.wait_for_status(GnssStatus.NO_SENTENCE, _READER_TIMEOUT_S)
        status = reader.status()
        with pytest.raises(FrozenInstanceError):
            status.status = GnssStatus.VALID  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            status.metrics.gga_count = 5  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Cache tests
# ---------------------------------------------------------------------------


def test_cache_capacity_eviction_drops_oldest() -> None:
    clock = ScriptedClock()
    cache = GnssFixCache(max_items=3, max_age_s=60.0, clock=clock)
    fixes = [make_fix(mono_ns=ns) for ns in (1, 2, 3, 4, 5)]
    for fix in fixes:
        cache.add(fix)
    snapshot = cache.snapshot()
    assert snapshot == (fixes[2], fixes[3], fixes[4])
    assert all(
        entry is original
        for entry, original in zip(snapshot, fixes[2:], strict=True)
    )


def test_cache_time_window_eviction_on_snapshot() -> None:
    clock = ScriptedClock()
    cache = GnssFixCache(max_items=8, max_age_s=10.0, clock=clock)
    old = make_fix(mono_ns=clock.monotonic_ns().ns)
    cache.add(old)
    clock.advance(seconds=11.0)
    fresh = make_fix(mono_ns=clock.monotonic_ns().ns)
    cache.add(fresh)
    snapshot = cache.snapshot()
    assert snapshot == (fresh,)


def test_cache_snapshot_is_immutable_and_does_not_expose_internals() -> None:
    clock = ScriptedClock()
    cache = GnssFixCache(max_items=4, max_age_s=60.0, clock=clock)
    fix = make_fix(mono_ns=1)
    cache.add(fix)

    snapshot = cache.snapshot()
    assert isinstance(snapshot, tuple)
    assert snapshot == cache.snapshot()
    assert snapshot is not cache.snapshot()
    with pytest.raises(FrozenInstanceError):
        snapshot[0].latitude_deg = 1.0  # type: ignore[misc]


def test_cache_rejects_non_fix_and_invalid_configuration() -> None:
    clock = ScriptedClock()
    cache = GnssFixCache(max_items=2, max_age_s=60.0, clock=clock)
    with pytest.raises(TypeError):
        cache.add("not a fix")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        GnssFixCache(max_items=0, max_age_s=60.0, clock=clock)
    with pytest.raises(ValueError):
        GnssFixCache(max_items=2, max_age_s=0.0, clock=clock)
    with pytest.raises(ValueError):
        GnssFixCache(max_items=2, max_age_s=-1.0, clock=clock)


def test_reader_cache_honours_capacity_across_reconnect() -> None:
    clock = ScriptedClock()
    first = ScriptedSerialAdapter()
    first.feed(gga_line())
    first.feed(SerialAdapterError("unplugged"))
    second = ScriptedSerialAdapter()
    second.feed(gga_line(time="120100.00"))

    with make_reader(ScriptedFactory(first, second), clock) as reader:
        assert reader.wait_for(
            lambda s: s.metrics.gga_count == 2 and s.generation == 2,
            _READER_TIMEOUT_S,
        )
        fixes = reader.fixes()
        assert len(fixes) == 2  # cache survives reconnect (time-bounded window)
        assert fixes[0].received_monotonic_ns.ns <= fixes[1].received_monotonic_ns.ns


# ---------------------------------------------------------------------------
# pyserial integration surface (never opened in default tests)
# ---------------------------------------------------------------------------


class FakePort:
    """Duck-typed serial port for wrapper delegation tests."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)
        self.read_sizes: list[int] = []
        self.close_calls = 0

    def read(self, size: int = 1) -> bytes:
        self.read_sizes.append(size)
        return self._chunks.pop(0) if self._chunks else b""

    def close(self) -> None:
        self.close_calls += 1


def test_pyserial_adapter_delegates_and_closes_once() -> None:
    port = FakePort([b"$GPGGA", b",123\r\n"])
    adapter = PyserialSerialAdapter(port)

    assert adapter.read(10) == b"$GPGGA"
    assert adapter.read(10) == b",123\r\n"
    assert adapter.read(10) == b""
    assert port.read_sizes == [10, 10, 10]

    adapter.close()
    adapter.close()
    assert port.close_calls == 1
    with pytest.raises(SerialAdapterClosedError):
        adapter.read(10)


def test_pyserial_adapter_maps_port_errors() -> None:
    class BrokenPort:
        def read(self, size: int = 1) -> bytes:
            raise OSError("device gone")

        def close(self) -> None:
            return None

    adapter = PyserialSerialAdapter(BrokenPort())
    with pytest.raises(SerialAdapterError):
        adapter.read(8)


def test_pyserial_adapter_close_is_best_effort() -> None:
    """P3 (ISSUE-025 review §10): a failing port close must not propagate —
    the adapter is already marked closed and cleanup stays best-effort."""

    class ExplodingClosePort:
        def read(self, size: int = 1) -> bytes:
            raise OSError("device gone")

        def close(self) -> None:
            raise OSError("close failed")

    adapter = PyserialSerialAdapter(ExplodingClosePort())
    adapter.close()  # must not raise
    adapter.close()  # idempotent, still no raise
    with pytest.raises(SerialAdapterClosedError):
        adapter.read(8)


def test_pyserial_config_validates_without_opening() -> None:
    config = PyserialSerialConfig(port="COM3")
    assert config.baudrate == 9600
    assert config.read_timeout_s == 2.0
    with pytest.raises(ValueError):
        PyserialSerialConfig(port="")
    with pytest.raises(ValueError):
        PyserialSerialConfig(port="COM3", baudrate=0)
    with pytest.raises(ValueError):
        PyserialSerialConfig(port="COM3", read_timeout_s=-1.0)
    with pytest.raises(ValueError):
        # P3 (ISSUE-025 review §10): timeout 0 makes pyserial non-blocking,
        # which would busy-spin the reader loop; require strictly positive.
        PyserialSerialConfig(port="COM3", read_timeout_s=0.0)
    # The factory only opens a port when called; constructing it must not.
    assert callable(PyserialSerialFactory(config))


# ---------------------------------------------------------------------------
# Module guard: no top-level serial import in the reader module
# ---------------------------------------------------------------------------


def test_reader_module_has_no_top_level_serial_import() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "uav_gpr"
        / "positioning"
        / "reader.py"
    )
    tree = ast.parse(source.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Import):
            roots = {alias.name.split(".")[0] for alias in node.names}
            assert "serial" not in roots
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            assert node.module.split(".")[0] != "serial"
