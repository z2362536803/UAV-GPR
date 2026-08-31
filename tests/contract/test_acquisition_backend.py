"""Contract tests for the acquisition backend contract and deterministic
simulator (ISSUE-015).

Covers: strict lifecycle (open/configure/acquire/cancel/close) with structured
rejection of illegal transitions; deterministic multi-channel sweeps
(seed/config/injected Clock); single/dual channel shared interface;
requested/applied config diff; fault injection (timeout, half sweep, config
rejection, disconnect, delay); cancellable blocking waits; idempotent
cancel/close with no leaked threads or waits (events/join only, no fixed
sleep); real UTC+monotonic metadata.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from uav_gpr.acquisition.backend import (
    AcquisitionBackend,
    BackendCancelledError,
    BackendClosedError,
    BackendConfigRejectedError,
    BackendDisconnectedError,
    BackendError,
    BackendHalfSweepError,
    BackendState,
    BackendStateError,
    BackendTimeoutError,
    Capabilities,
    SimulatedBackend,
    SimulationFaults,
)
from uav_gpr.core import (
    AcquisitionMode,
    ChannelSpec,
    DeviceId,
    DomainError,
    ErrorCode,
    FrequencySweep,
    GnssFixQuality,
    GnssMatchMethod,
    GnssNoFixPolicy,
    LogicalPolarization,
    ManualClock,
    MissionConfig,
    MissionId,
    MonotonicNs,
    RawHashSpec,
    SParameter,
    TraceQualityReason,
    TraceQualityStatus,
)

CREATED_UTC = datetime(2026, 1, 1, tzinfo=UTC)
MISSION = MissionId("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
DEVICE = DeviceId("cccccccc-cccc-4ccc-8ccc-cccccccccccc")

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


def make_config(**overrides: object) -> MissionConfig:
    base: dict[str, object] = dict(
        frequency_start_hz=1.0e9,
        frequency_stop_hz=2.0e9,
        frequency_points=11,
        if_bw_hz=1_000.0,
        power_dbm=-10.0,
        channels=[HH_S11, VV_S22],
        acquisition_mode=AcquisitionMode.FIXED_COUNT,
        planned_trace_count=100,
        target_interval_s=0.25,
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
    channels: tuple[ChannelSpec, ...] = (HH_S11, VV_S22),
    gnss_enabled: bool = False,
) -> SimulatedBackend:
    return SimulatedBackend(
        mission_id=MISSION,
        device_id=DEVICE,
        channels=channels,
        seed=seed,
        clock=clock,
        faults=faults,
        gnss_enabled=gnss_enabled,
    )


def open_configure(
    backend: AcquisitionBackend, config: MissionConfig | None = None
) -> Capabilities:
    caps = backend.open()
    backend.configure(config or make_config())
    return caps


# ---------------------------------------------------------------------------
# Lifecycle: happy path, illegal transitions, idempotent close, reopen
# ---------------------------------------------------------------------------


def test_lifecycle_open_configure_acquire_close() -> None:
    backend = make_backend()
    assert backend.state is BackendState.CLOSED
    caps = backend.open()
    assert isinstance(caps, Capabilities)
    assert caps.device_id == DEVICE
    assert caps.channels == (HH_S11, VV_S22)
    assert caps.supports_dual_channel
    assert caps.fault_injection
    assert backend.state is BackendState.OPEN
    assert backend.connection_generation == 1
    applied = backend.configure(make_config())
    assert backend.state is BackendState.CONFIGURED
    assert applied.config.channels == (HH_S11, VV_S22)
    sweep = backend.acquire()
    assert isinstance(sweep, FrequencySweep)
    backend.close()
    assert backend.state is BackendState.CLOSED


def test_illegal_lifecycle_transitions_structured_rejected() -> None:
    backend = make_backend()
    backend.open()
    with pytest.raises(BackendStateError) as exc:
        backend.open()
    assert isinstance(exc.value, DomainError)
    assert exc.value.code is ErrorCode.INVALID_ARGUMENT
    assert exc.value.context["reason"] == "illegal_state"
    assert exc.value.context["operation"] == "open"

    fresh = make_backend()
    with pytest.raises(BackendStateError):
        fresh.configure(make_config())  # configure before open
    with pytest.raises(BackendStateError):
        fresh.acquire()  # acquire before open/configure
    with pytest.raises(BackendStateError):
        fresh.acquire(timeout_s=0.05)

    closed = make_backend()
    closed.open()
    closed.configure(make_config())
    closed.close()
    with pytest.raises(BackendStateError):
        closed.acquire()  # acquire after close


def test_close_idempotent_and_reopen_allowed() -> None:
    backend = make_backend()
    backend.open()
    backend.configure(make_config())
    backend.close()
    backend.close()  # idempotent
    assert backend.state is BackendState.CLOSED
    caps = backend.open()  # reopen allowed
    assert caps.device_id == DEVICE
    assert backend.state is BackendState.OPEN
    assert backend.connection_generation == 1


def test_reconfigure_resets_acquisition_state() -> None:
    backend = make_backend()
    backend.open()
    backend.configure(make_config())
    first = backend.acquire()
    assert first.metadata is not None
    assert first.metadata.trace_index == 0
    backend.configure(make_config())  # re-configure (new task)
    second = backend.acquire()
    assert second.metadata is not None
    assert second.metadata.trace_index == 0


# ---------------------------------------------------------------------------
# Sweep shape/axis/channels: single and dual channel share one interface
# ---------------------------------------------------------------------------


def test_single_channel_sweep_shape_axis_channels() -> None:
    config = make_config(channels=[HH_S11])
    backend = make_backend(channels=(HH_S11,))
    open_configure(backend, config)
    sweep = backend.acquire()
    assert sweep.data.shape == (1, 11)
    assert sweep.data.dtype == np.complex128
    np.testing.assert_array_equal(sweep.frequencies_hz, config.frequency_axis_hz)
    assert sweep.frequencies_hz.ndim == 1
    assert sweep.channels == (HH_S11,)
    assert sweep.metadata is not None


def test_dual_channel_shared_interface() -> None:
    single = make_backend(channels=(HH_S11,))
    dual = make_backend(channels=(HH_S11, VV_S22))
    assert isinstance(single, AcquisitionBackend)
    assert isinstance(dual, AcquisitionBackend)
    assert type(single) is SimulatedBackend
    assert type(dual) is SimulatedBackend
    open_configure(dual)
    sweep = dual.acquire()
    assert sweep.data.shape == (2, 11)
    assert sweep.channels == (HH_S11, VV_S22)
    assert not np.array_equal(sweep.data[0], sweep.data[1])
    # A single-channel mission on a dual-channel device is a subset mission.
    subset = make_backend(channels=(HH_S11, VV_S22))
    open_configure(subset, make_config(channels=[HH_S11]))
    one = subset.acquire()
    assert one.data.shape == (1, 11)
    assert one.channels == (HH_S11,)


# ---------------------------------------------------------------------------
# Metadata: real UTC+monotonic, identities, intervals, raw hash
# ---------------------------------------------------------------------------


def test_metadata_real_times_identities_and_first_trace_intervals() -> None:
    clock = ManualClock(datetime(2026, 1, 1, tzinfo=UTC), monotonic_ns=5_000_000_000)
    backend = make_backend(clock=clock)
    open_configure(backend, make_config(channels=[HH_S11, VV_S22]))
    sweep = backend.acquire()
    md = sweep.metadata
    assert md is not None
    assert md.mission_id == MISSION
    assert md.trace_index == 0
    assert md.trace_uid.to_json() != MISSION.to_json()
    assert md.device_id == DEVICE
    assert md.sweep_started_utc <= md.sweep_midpoint_utc <= md.sweep_finished_utc
    assert (
        md.sweep_started_monotonic_ns.ns
        <= md.sweep_midpoint_monotonic_ns.ns
        <= md.sweep_finished_monotonic_ns.ns
    )
    assert md.sweep_started_utc == datetime(2026, 1, 1, tzinfo=UTC)
    assert md.sweep_started_monotonic_ns == MonotonicNs(5_000_000_000)
    assert md.target_interval_s == 0.25
    assert md.actual_interval_s is None
    assert md.schedule_error_s is None
    assert md.connection_generation == 1
    assert md.raw_trace_sha256 is not None
    assert len(md.raw_trace_sha256) == 64


def test_trace_indices_and_intervals_after_clock_advance() -> None:
    clock = ManualClock(datetime(2026, 1, 1, tzinfo=UTC), monotonic_ns=1_000_000_000)
    backend = make_backend(clock=clock)
    open_configure(backend, make_config(channels=[HH_S11]))
    first = backend.acquire()
    clock.advance_monotonic(250_000_000)
    clock.advance_utc(timedelta(milliseconds=250))
    second = backend.acquire()
    clock.advance_monotonic(300_000_000)
    clock.advance_utc(timedelta(milliseconds=300))
    third = backend.acquire()

    assert first.metadata is not None
    assert second.metadata is not None
    assert third.metadata is not None
    assert [first.metadata.trace_index, second.metadata.trace_index,
            third.metadata.trace_index] == [0, 1, 2]
    assert first.metadata.actual_interval_s is None
    assert first.metadata.schedule_error_s is None
    assert second.metadata.actual_interval_s == pytest.approx(0.25)
    assert second.metadata.schedule_error_s == pytest.approx(0.0)
    assert third.metadata.actual_interval_s == pytest.approx(0.30)
    assert third.metadata.schedule_error_s == pytest.approx(0.05)
    assert second.metadata.sweep_started_utc == datetime(
        2026, 1, 1, 0, 0, 0, 250_000, tzinfo=UTC
    )
    # trace uids are unique and monotonic in index
    uids = [first.metadata.trace_uid, second.metadata.trace_uid,
            third.metadata.trace_uid]
    assert len(set(uids)) == 3


def test_raw_hash_matches_core_spec() -> None:
    backend = make_backend()
    open_configure(backend, make_config(channels=[HH_S11]))
    sweep = backend.acquire()
    md = sweep.metadata
    assert md is not None
    expected = RawHashSpec(
        mission_id=md.mission_id,
        trace_index=md.trace_index,
        trace_uid=md.trace_uid,
        channels=sweep.channels,
        frequencies_hz=sweep.frequencies_hz,
        data=sweep.data,
    ).compute()
    assert md.raw_trace_sha256 == expected


# ---------------------------------------------------------------------------
# Determinism: same seed/config/clock -> same raw; different inputs -> different
# ---------------------------------------------------------------------------


def _acquired_pair(seed_a: int, seed_b: int, config_a: MissionConfig,
                   config_b: MissionConfig) -> tuple[FrequencySweep, FrequencySweep]:
    clock = ManualClock(datetime(2026, 1, 1, tzinfo=UTC), monotonic_ns=0)
    backend_a = make_backend(seed=seed_a, clock=clock)
    open_configure(backend_a, config_a)
    backend_b = make_backend(seed=seed_b, clock=clock)
    open_configure(backend_b, config_b)
    return backend_a.acquire(), backend_b.acquire()


def test_same_seed_same_config_same_raw() -> None:
    config = make_config()
    a, b = _acquired_pair(42, 42, config, config)
    np.testing.assert_array_equal(a.data, b.data)
    assert a.metadata == b.metadata  # same clock -> identical times/ids/hash
    assert a.metadata is not None and b.metadata is not None
    assert a.metadata.raw_trace_sha256 == b.metadata.raw_trace_sha256


def test_different_seed_different_raw() -> None:
    config = make_config()
    a, b = _acquired_pair(1, 2, config, config)
    assert a.data.shape == b.data.shape
    assert not np.array_equal(a.data, b.data)


def test_same_seed_different_config_different_raw() -> None:
    config_a = make_config(if_bw_hz=1_000.0)
    config_b = make_config(if_bw_hz=2_000.0)
    a, b = _acquired_pair(7, 7, config_a, config_b)
    assert a.data.shape == b.data.shape
    assert not np.array_equal(a.data, b.data)


# ---------------------------------------------------------------------------
# requested/applied config and config rejection
# ---------------------------------------------------------------------------


def test_applied_config_identical_by_default() -> None:
    backend = make_backend()
    backend.open()
    config = make_config()
    applied = backend.configure(config)
    assert applied.config == config
    assert applied.diff.is_identical
    assert applied.diff.changed_fields == ()


def test_requested_applied_diff_on_ifbw_quantization() -> None:
    backend = make_backend(faults=SimulationFaults(applied_if_bw_hz=2_000.0))
    backend.open()
    applied = backend.configure(make_config(if_bw_hz=1_000.0))
    assert applied.config.if_bw_hz == 2_000.0
    assert not applied.diff.is_identical
    entry = applied.diff.field("if_bw_hz")
    assert entry is not None
    assert entry.changed
    assert applied.diff.changed_fields == ("if_bw_hz",)


def test_config_rejected_unsupported_channel_and_recoverable() -> None:
    backend = make_backend(channels=(HH_S11,))
    backend.open()
    with pytest.raises(BackendConfigRejectedError) as exc:
        backend.configure(make_config(channels=[VV_S22]))
    assert exc.value.context["reason"] == "config_rejected"
    assert backend.state is BackendState.OPEN  # stays open; recoverable
    applied = backend.configure(make_config(channels=[HH_S11]))
    assert backend.state is BackendState.CONFIGURED
    assert applied.diff.is_identical


def test_config_reject_fault_flag_deterministic() -> None:
    backend = make_backend(faults=SimulationFaults(reject_config=True))
    backend.open()
    with pytest.raises(BackendConfigRejectedError):
        backend.configure(make_config())
    assert backend.state is BackendState.OPEN
    with pytest.raises(BackendConfigRejectedError):
        backend.configure(make_config())  # deterministic: still rejected


# ---------------------------------------------------------------------------
# Fault injection at planned attempts (deterministic, no trace consumed)
# ---------------------------------------------------------------------------


def test_timeout_fault_at_planned_attempt() -> None:
    backend = make_backend(faults=SimulationFaults(timeout_at=(1,)))
    open_configure(backend)
    good = backend.acquire()  # attempt 0 succeeds
    assert good.metadata is not None
    assert good.metadata.trace_index == 0
    with pytest.raises(BackendTimeoutError) as exc:
        backend.acquire()  # attempt 1: planned timeout
    assert exc.value.context["attempt"] == 1
    next_sweep = backend.acquire()  # attempt 2 succeeds; index not reused
    assert next_sweep.metadata is not None
    assert next_sweep.metadata.trace_index == 1


def test_half_sweep_fault_fail_closed_no_trace_consumed() -> None:
    backend = make_backend(faults=SimulationFaults(half_sweep_at=(0,)))
    open_configure(backend)
    with pytest.raises(BackendHalfSweepError) as exc:
        backend.acquire()
    assert exc.value.context["attempt"] == 0
    sweep = backend.acquire()  # half sweep never consumed a trace index
    assert sweep.metadata is not None
    assert sweep.metadata.trace_index == 0


def test_disconnect_fault_increments_generation() -> None:
    backend = make_backend(faults=SimulationFaults(disconnect_at=(1,)))
    open_configure(backend)
    first = backend.acquire()
    assert first.metadata is not None
    assert first.metadata.connection_generation == 1
    with pytest.raises(BackendDisconnectedError):
        backend.acquire()  # attempt 1: simulated disconnect
    assert backend.connection_generation == 2
    after = backend.acquire()
    assert after.metadata is not None
    assert after.metadata.connection_generation == 2


# ---------------------------------------------------------------------------
# Cancellable waits, idempotent cancel/close, no leaked threads
# ---------------------------------------------------------------------------


def _run_in_thread(backend: AcquisitionBackend, timeout_s: float | None = None
                   ) -> tuple[threading.Thread, list[object]]:
    results: list[object] = []

    def worker() -> None:
        try:
            results.append(backend.acquire(timeout_s=timeout_s))
        except BaseException as exc:
            results.append(exc)

    thread = threading.Thread(target=worker, name="backend-test-worker")
    thread.start()
    return thread, results


def test_delay_fault_cancel_interrupts_wait() -> None:
    backend = make_backend(faults=SimulationFaults(delay_s={0: 5.0}))
    open_configure(backend)
    thread, results = _run_in_thread(backend)
    assert backend.acquire_started.wait(2.0)
    backend.cancel()
    thread.join(2.0)
    assert not thread.is_alive()
    assert len(results) == 1
    assert isinstance(results[0], BackendCancelledError)
    assert backend.state is BackendState.CONFIGURED
    assert not backend.acquiring
    # a later acquire is unaffected: the delay fault applies to attempt 0 only
    sweep = backend.acquire()
    assert sweep.metadata is not None
    assert sweep.metadata.trace_index == 0


def test_cancel_without_pending_acquire_is_noop() -> None:
    backend = make_backend()
    open_configure(backend)
    backend.cancel()
    backend.cancel()  # idempotent
    assert backend.state is BackendState.CONFIGURED
    sweep = backend.acquire()  # cancel must not poison later acquires
    assert sweep.metadata is not None
    assert sweep.metadata.trace_index == 0


def test_configure_rejected_while_acquire_in_flight() -> None:
    backend = make_backend(faults=SimulationFaults(block_until_cancelled=True))
    open_configure(backend)
    thread, results = _run_in_thread(backend)
    try:
        assert backend.acquire_started.wait(2.0)
        # configure and acquire are mutually exclusive: an in-flight acquire
        # must be structurally rejected, never silently re-configured.
        with pytest.raises(BackendStateError) as exc:
            backend.configure(make_config())
        assert exc.value.context["reason"] == "illegal_state"
        assert exc.value.context["busy"] is True
        assert exc.value.context["operation"] == "configure"
        # the rejected configure must not disturb the in-flight acquire
        assert backend.acquiring
    finally:
        backend.cancel()
        thread.join(2.0)
    assert not thread.is_alive()
    assert isinstance(results[0], BackendCancelledError)
    assert backend.state is BackendState.CONFIGURED
    # after the acquire terminates, configure works again
    applied = backend.configure(make_config())
    assert applied.diff.is_identical
    # and acquire is functional again (block_until_cancelled is still armed,
    # so a bounded acquire proves the path works without hanging)
    with pytest.raises(BackendTimeoutError):
        backend.acquire(timeout_s=0.05)


def test_close_wakes_blocked_acquire_and_is_idempotent() -> None:
    backend = make_backend(faults=SimulationFaults(block_until_cancelled=True))
    open_configure(backend)
    thread, results = _run_in_thread(backend)
    assert backend.acquire_started.wait(2.0)
    backend.close()
    thread.join(2.0)
    assert not thread.is_alive()
    assert len(results) == 1
    assert isinstance(results[0], BackendClosedError)
    assert backend.state is BackendState.CLOSED
    backend.close()  # idempotent
    assert backend.state is BackendState.CLOSED
    with pytest.raises(BackendStateError):
        backend.acquire()


def test_acquire_timeout_s_expiry_on_blocking_wait() -> None:
    backend = make_backend(faults=SimulationFaults(block_until_cancelled=True))
    open_configure(backend)
    with pytest.raises(BackendTimeoutError) as exc:
        backend.acquire(timeout_s=0.05)
    assert exc.value.context["timeout_s"] == 0.05
    assert not backend.acquiring


def test_concurrent_acquire_rejected_busy() -> None:
    backend = make_backend(faults=SimulationFaults(block_until_cancelled=True))
    open_configure(backend)
    thread, results = _run_in_thread(backend)
    assert backend.acquire_started.wait(2.0)
    with pytest.raises(BackendStateError) as exc:
        backend.acquire(timeout_s=0.05)  # busy: second acquire rejected
    assert exc.value.context["reason"] == "illegal_state"
    backend.cancel()
    thread.join(2.0)
    assert not thread.is_alive()
    assert isinstance(results[0], BackendCancelledError)


# ---------------------------------------------------------------------------
# GNSS scenario metadata
# ---------------------------------------------------------------------------


def test_gnss_disabled_metadata_explicit_missing() -> None:
    backend = make_backend(gnss_enabled=False)
    caps = open_configure(backend)
    assert not caps.gnss
    sweep = backend.acquire()
    assert sweep.metadata is not None
    assert sweep.metadata.gnss_match is None
    assert TraceQualityReason.GNSS_MISSING in sweep.metadata.quality_reasons
    assert sweep.metadata.quality_status is TraceQualityStatus.DEGRADED


def test_gnss_enabled_metadata_usable_match() -> None:
    backend = make_backend(gnss_enabled=True)
    caps = open_configure(backend)
    assert caps.gnss
    sweep = backend.acquire()
    assert sweep.metadata is not None
    match = sweep.metadata.gnss_match
    assert match is not None
    assert match.usable_for_map
    assert match.method is GnssMatchMethod.NEAREST_MIDPOINT
    assert match.fix is not None
    assert match.fix.valid
    assert match.fix.fix_quality is GnssFixQuality.GPS_FIX
    assert match.trace_midpoint_utc == sweep.metadata.sweep_midpoint_utc
    assert sweep.metadata.quality_status is TraceQualityStatus.NOMINAL
    assert sweep.metadata.quality_reasons == ()


# ---------------------------------------------------------------------------
# Error contract shape
# ---------------------------------------------------------------------------


def test_backend_errors_are_structured_domain_errors() -> None:
    backend = make_backend()
    backend.open()
    backend.configure(make_config())
    with pytest.raises(BackendError) as exc:
        backend.acquire(timeout_s=-1.0)
    assert exc.value.code is ErrorCode.INVALID_ARGUMENT
    assert "reason" in exc.value.context
    payload = exc.value.to_dict()
    assert payload["code"] == "invalid_argument"
    assert isinstance(payload["context"]["reason"], str)
