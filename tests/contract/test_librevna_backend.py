"""LibreVnaUsbBackend contract tests (ISSUE-021, no hardware).

The backend is exercised through a scripted fake USB adapter implementing the
ISSUE-019 ``UsbAdapter`` protocol: every ``read`` pops one scripted chunk
(bytes or an exception), every ``write`` is recorded.  All control/data
packets are built with the frozen frame codec (``transport.encode_packet``);
golden DeviceInfo/SweepSettings vectors are derived from the reference
implementation semantics (see the ISSUE-021 plan section 4 provenance).

Covered contract points (docs/issues/M04_LIBREVNA.md ISSUE-021):

- open/capabilities/configure (SweepSettings send + applied axis readback),
  acquire, cancel/close, connection generation;
- requested/applied comparison: int quantization is recorded in
  ``ConfigDiff``; device capability range checks and the first-sweep axis
  gate reject out-of-tolerance configurations before any trace is emitted;
- partial/bad sweeps never produce a ``FrequencySweep`` (no trace index is
  allocated); NACK/protocol failures are fail-closed;
- deterministic timeouts via an injected monotonic tick clock (no sleeps),
  UTC+monotonic metadata ordering, session statistics.

No ``usb``/``serial``/network imports (AST guard) and no real devices.
"""

from __future__ import annotations

import struct
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from uav_gpr.acquisition.backend import (
    BackendCancelledError,
    BackendClosedError,
    BackendConfigRejectedError,
    BackendDisconnectedError,
    BackendError,
    BackendState,
    BackendStateError,
    BackendTimeoutError,
    Capabilities,
)
from uav_gpr.acquisition.controller import (
    AcquisitionController,
    ControllerState,
    StopReason,
)
from uav_gpr.acquisition.librevna.backend import (
    S11_CHANNEL,
    S11_S22_STAGES_BITMAP,
    S22_CHANNEL,
    LibreVnaDeviceInfo,
    LibreVnaNackError,
    LibreVnaProtocolError,
    LibreVnaUsbBackend,
    LibreVnaUsbSettings,
    SweepSettings,
    decode_device_info,
    encode_sweep_settings,
)
from uav_gpr.acquisition.librevna.reconnect import (
    LibreVnaReconnectError,
    LibreVnaReconnector,
    LibreVnaReconnectPolicy,
)
from uav_gpr.acquisition.librevna.stream import (
    S11_RECEIVER_PLAN,
    datapoint_matches_plan,
    parse_vna_datapoint,
)
from uav_gpr.acquisition.librevna.transport import (
    ACK,
    DEVICE_INFO,
    NACK,
    REQUEST_DEVICE_INFO,
    SET_IDLE,
    SWEEP_SETTINGS,
    VNA_DATAPOINT,
    LibreVnaDeviceNotFoundError,
    LibreVnaDisconnectedError,
    LibreVnaTimeoutError,
    LibreVnaUsbTransport,
    encode_packet,
)
from uav_gpr.core import (
    AcquisitionMode,
    ChannelSpec,
    ConfigDiff,
    DeviceId,
    GnssNoFixPolicy,
    LogicalPolarization,
    MissionConfig,
    MissionId,
    RawHashSpec,
    SParameter,
    TraceQualityReason,
    TraceQualityStatus,
)
from uav_gpr.core.timeutil import ManualClock

UTC0 = datetime(2026, 9, 2, 0, 0, 0, tzinfo=UTC)
MISSION = MissionId("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
DEVICE = DeviceId("cccccccc-cccc-4ccc-8ccc-cccccccccccc")

# ---- golden vectors (reference semantics; see plan section 4) -------------

#: protocol v14 DeviceInfo payload (57 bytes): protocol 14, fw 1.2.3, hw 5/A,
#: 100 MHz..6 GHz, IFBW 1..1 MHz, 10001 points, -30..10 dBm, 2 ports.
DEVICE_INFO_PAYLOAD_HEX = (
    "0e00010203054100e1f5050000000000bca065010000000100000040420f00112748f4e803"
    "0100000040420f00c8007841cb0200000002ffff"
)

#: SweepSettings payload golden (31 bytes): 100 MHz->1 GHz, 101 points,
#: IFBW 100 kHz, -10 dBm, config 0x0C, stages 0x1240 (S11).
SWEEP_SETTINGS_PAYLOAD_HEX = "00e1f5050000000000ca9a3b000000006500a086010018fc0c401218fc0000"

#: SweepSettings payload golden (31 bytes), stages 0x1241 (dual S11/S22,
#: ISSUE-022): identical to the S11 golden except the stages field 0x1240 ->
#: 0x1241 ("4012" -> "4112").
DUAL_SWEEP_SETTINGS_PAYLOAD_HEX = "00e1f5050000000000ca9a3b000000006500a086010018fc0c411218fc0000"

#: Default test sweep: 100 MHz -> 200 MHz, 101 points (1 MHz step, exact ints).
SWEEP_START_HZ = 100_000_000
SWEEP_STOP_HZ = 200_000_000
SWEEP_POINTS = 101


def _device_info_payload() -> bytes:
    return bytes.fromhex(DEVICE_INFO_PAYLOAD_HEX)


def _point_payload(
    freq_hz: int,
    point_number: int,
    *,
    port1: complex = 0.5 - 0.2j,
    ref: complex = 1.0 + 0.0j,
    descs: tuple[int, int] = (0x10, 0x01),
) -> bytes:
    """BLOCKED layout: header + reals(ref, port1) + imags + descs."""
    payload = struct.pack("<QhH", int(freq_hz), -1000, int(point_number))
    payload += struct.pack("<ff", ref.real, port1.real)
    payload += struct.pack("<ff", ref.imag, port1.imag)
    payload += bytes(descs)
    return payload


def _point_packet(freq_hz: int, point_number: int, **kwargs: object) -> bytes:
    return encode_packet(VNA_DATAPOINT, _point_payload(freq_hz, point_number, **kwargs))


def _sweep_bytes(
    start_hz: int = SWEEP_START_HZ,
    stop_hz: int = SWEEP_STOP_HZ,
    points: int = SWEEP_POINTS,
    *,
    shift_hz: int = 0,
    **kwargs: object,
) -> bytes:
    """One complete sweep encoded as a single byte chunk (actual device axis)."""
    freqs = np.linspace(start_hz, stop_hz, points).round().astype(np.int64)
    packets = [
        _point_packet(int(freq) + shift_hz, i, **kwargs) for i, freq in enumerate(freqs)
    ]
    return b"".join(packets)


def _dual_point_payload(
    freq_hz: int,
    point_number: int,
    *,
    port1: complex = 0.5 - 0.2j,
    ref1: complex = 1.0 + 0.0j,
    port2: complex = 0.3 + 0.1j,
    ref2: complex = 1.5 + 0.0j,
    descs: tuple[int, int, int, int] = (0x10, 0x01, 0x30, 0x22),
) -> bytes:
    """Dual-reflection datapoint payload (ISSUE-022): both stages per point.

    BLOCKED layout: header + reals(stage0 ref, stage0 port1, stage1 ref,
    stage1 port2) + imags + descs.  desc bits: stage 0 ref 0x10 / port1 0x01;
    stage 1 ref 0x30 / port2 0x22 (stage = bits 7-5, reference bit 0x10).
    """
    payload = struct.pack("<QhH", int(freq_hz), -1000, int(point_number))
    payload += struct.pack(
        "<ffff", ref1.real, port1.real, ref2.real, port2.real
    )
    payload += struct.pack(
        "<ffff", ref1.imag, port1.imag, ref2.imag, port2.imag
    )
    payload += bytes(descs)
    return payload


def _dual_point_packet(freq_hz: int, point_number: int, **kwargs: object) -> bytes:
    return encode_packet(
        VNA_DATAPOINT, _dual_point_payload(freq_hz, point_number, **kwargs)
    )


def _dual_sweep_bytes(
    start_hz: int = SWEEP_START_HZ,
    stop_hz: int = SWEEP_STOP_HZ,
    points: int = SWEEP_POINTS,
    *,
    shift_hz: int = 0,
    **kwargs: object,
) -> bytes:
    """One complete dual-reflection sweep (stage-0 S11 + stage-1 S22)."""
    freqs = np.linspace(start_hz, stop_hz, points).round().astype(np.int64)
    packets = [
        _dual_point_packet(int(freq) + shift_hz, i, **kwargs)
        for i, freq in enumerate(freqs)
    ]
    return b"".join(packets)


class ScriptedAdapter:
    """``UsbAdapter`` protocol driven by a script (bytes or exceptions)."""

    def __init__(
        self, reads: list[object] | None = None, open_error: Exception | None = None
    ) -> None:
        self._reads: list[object] = list(reads or [])
        self.open_error = open_error
        self.writes: list[bytes] = []
        self.opened = False
        self.closed = False

    def extend(self, reads: list[object]) -> None:
        self._reads.extend(reads)

    @property
    def is_open(self) -> bool:
        return self.opened and not self.closed

    def open(self) -> None:
        if self.open_error is not None:
            raise self.open_error
        self.opened = True
        self.closed = False

    def read(self, max_length: int, timeout_ms: int) -> bytes:
        if not self._reads:
            raise LibreVnaTimeoutError("scripted adapter: no more data")
        item = self._reads.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def write(self, data: bytes) -> None:
        self.writes.append(bytes(data))

    def close(self) -> None:
        self.closed = True


class TickClock:
    """Monotonic float clock that advances on every call (deterministic ticks)."""

    def __init__(self, start: float = 1000.0, step_s: float = 0.05) -> None:
        self.t = start
        self.step_s = step_s

    def __call__(self) -> float:
        self.t += self.step_s
        return self.t


def make_config(**overrides: object) -> MissionConfig:
    base: dict[str, object] = dict(
        frequency_start_hz=float(SWEEP_START_HZ),
        frequency_stop_hz=float(SWEEP_STOP_HZ),
        frequency_points=SWEEP_POINTS,
        if_bw_hz=100.0e3,
        power_dbm=-10.0,
        channels=[S11_CHANNEL],
        acquisition_mode=AcquisitionMode.CONTINUOUS,
        planned_trace_count=None,
        target_interval_s=0.5,
        gnss_max_age_s=2.0,
        gnss_no_fix_policy=GnssNoFixPolicy.RECORD_WITHOUT_POSITION,
        calibration_profile_id=None,
        apply_calibration=False,
        background_reference_id=None,
        apply_background=False,
        created_utc=UTC0,
        note="librevna backend test",
        software_version="0.1.0.dev0",
    )
    base.update(overrides)
    return MissionConfig(**base)


def open_script() -> list[object]:
    return [encode_packet(DEVICE_INFO, _device_info_payload()), encode_packet(ACK)]


def configure_script(*extra_reads: object) -> list[object]:
    return [encode_packet(ACK), encode_packet(ACK), *extra_reads]


def make_backend(
    adapter: ScriptedAdapter,
    *,
    clock: ManualClock | None = None,
    mono_clock: TickClock | None = None,
    settings: LibreVnaUsbSettings | None = None,
) -> LibreVnaUsbBackend:
    return LibreVnaUsbBackend(
        LibreVnaUsbTransport(adapter),
        mission_id=MISSION,
        device_id=DEVICE,
        clock=clock if clock is not None else ManualClock(UTC0),
        mono_clock=mono_clock if mono_clock is not None else TickClock(),
        settings=settings if settings is not None else LibreVnaUsbSettings(),
    )


def open_and_configure(
    backend: LibreVnaUsbBackend, config: MissionConfig | None = None
) -> Capabilities:
    caps = backend.open()
    backend.configure(config if config is not None else make_config())
    return caps


def _expected_axis(config: MissionConfig) -> np.ndarray:
    applied = replace(
        config,
        frequency_start_hz=float(int(config.frequency_start_hz)),
        frequency_stop_hz=float(int(config.frequency_stop_hz)),
        if_bw_hz=float(int(config.if_bw_hz)),
        power_dbm=round(config.power_dbm * 100.0) / 100.0,
    )
    return applied.frequency_axis_hz


def _config_sweep_settings(config: MissionConfig) -> bytes:
    return encode_sweep_settings(
        int(config.frequency_start_hz),
        int(config.frequency_stop_hz),
        config.frequency_points,
        int(config.if_bw_hz),
        round(config.power_dbm * 100.0) / 100.0,
    )


def make_dual_config(**overrides: object) -> MissionConfig:
    """Default ISSUE-022 dual-reflection config (HH:S11 + VV:S22, in order)."""
    return make_config(channels=[S11_CHANNEL, S22_CHANNEL], **overrides)


def _config_dual_sweep_settings(config: MissionConfig) -> bytes:
    return encode_sweep_settings(
        int(config.frequency_start_hz),
        int(config.frequency_stop_hz),
        config.frequency_points,
        int(config.if_bw_hz),
        round(config.power_dbm * 100.0) / 100.0,
        stages_bitmap=S11_S22_STAGES_BITMAP,
    )


# ---------------------------------------------------------------------------
# Golden codec vectors
# ---------------------------------------------------------------------------


def test_golden_device_info_decode() -> None:
    info = decode_device_info(_device_info_payload())
    assert isinstance(info, LibreVnaDeviceInfo)
    assert info.protocol == 14
    assert info.firmware == "1.2.3"
    assert info.hardware_version == 5
    assert info.hardware_revision == "A"
    assert info.min_freq_hz == 100_000_000
    assert info.max_freq_hz == 6_000_000_000
    assert info.min_ifbw_hz == 1
    assert info.max_ifbw_hz == 1_000_000
    assert info.max_points == 10_001
    assert info.min_dbm == -30.0
    assert info.max_dbm == 10.0
    assert info.num_ports == 2
    assert info.max_dwell_time_us == 65_535


def test_golden_sweep_settings_encode() -> None:
    payload = encode_sweep_settings(
        100_000_000, 1_000_000_000, 101, 100_000, -10.0, stages_bitmap=0x1240
    )
    assert payload == bytes.fromhex(SWEEP_SETTINGS_PAYLOAD_HEX)
    settings = SweepSettings(
        start_hz=100_000_000,
        stop_hz=1_000_000_000,
        points=101,
        ifbw_hz=100_000,
        power_dbm=-10.0,
    )
    assert settings.encode() == bytes.fromhex(SWEEP_SETTINGS_PAYLOAD_HEX)


def test_sweep_settings_validation() -> None:
    with pytest.raises(ValueError):
        SweepSettings(100, 50, 101, 100_000, -10.0)  # stop <= start
    with pytest.raises(ValueError):
        SweepSettings(100, 200, 1, 100_000, -10.0)  # points < 2
    with pytest.raises(ValueError):
        SweepSettings(100, 200, 101, 0, -10.0)  # ifbw <= 0
    # 0x1241 (dual reflection, ISSUE-022) is now a production-verified value;
    # unverified bitmaps stay rejected.
    SweepSettings(100, 200, 101, 100_000, -10.0, stages_bitmap=0x1241)
    with pytest.raises(ValueError):
        SweepSettings(100, 200, 101, 100_000, -10.0, stages_bitmap=0x1242)


# ---------------------------------------------------------------------------
# open
# ---------------------------------------------------------------------------


def test_open_requests_device_info_and_set_idle() -> None:
    adapter = ScriptedAdapter(open_script())
    backend = make_backend(adapter)
    assert backend.state is BackendState.CLOSED
    caps = backend.open()
    assert isinstance(caps, Capabilities)
    assert caps.device_id == DEVICE
    assert caps.channels == (S11_CHANNEL, S22_CHANNEL)
    assert caps.supports_dual_channel
    assert not caps.fault_injection
    assert not caps.gnss
    assert backend.state is BackendState.OPEN
    assert backend.connection_generation == 1
    assert backend.device_info is not None
    assert backend.device_info.protocol == 14
    assert adapter.writes[0] == encode_packet(REQUEST_DEVICE_INFO)
    assert adapter.writes[1] == encode_packet(SET_IDLE)


def test_open_failure_closes_transport() -> None:
    adapter = ScriptedAdapter(open_error=LibreVnaDeviceNotFoundError("no device"))
    backend = make_backend(adapter)
    with pytest.raises(LibreVnaDeviceNotFoundError):
        backend.open()
    assert not adapter.opened
    # cleanup still works: close returns the backend to CLOSED without leaks
    backend.close()
    assert backend.state is BackendState.CLOSED
    assert not adapter.closed  # nothing was ever opened, nothing to release


def test_open_device_info_timeout() -> None:
    adapter = ScriptedAdapter([])  # silent device
    backend = make_backend(
        adapter,
        mono_clock=TickClock(),
        settings=LibreVnaUsbSettings(device_info_timeout_s=0.5),
    )
    with pytest.raises(BackendTimeoutError):
        backend.open()
    assert adapter.closed


def test_open_nack_fails_closed() -> None:
    adapter = ScriptedAdapter([encode_packet(NACK)])
    backend = make_backend(adapter)
    with pytest.raises(LibreVnaNackError):
        backend.open()
    assert adapter.closed


def test_reopen_after_close() -> None:
    adapter = ScriptedAdapter(open_script() + open_script())
    backend = make_backend(adapter)
    backend.open()
    backend.close()
    assert backend.state is BackendState.CLOSED
    backend.open()
    assert backend.state is BackendState.OPEN
    assert backend.connection_generation == 1


# ---------------------------------------------------------------------------
# configure
# ---------------------------------------------------------------------------


def test_configure_sends_set_idle_and_sweep_settings() -> None:
    adapter = ScriptedAdapter(open_script() + configure_script())
    backend = make_backend(adapter)
    backend.open()
    config = make_config()
    applied = backend.configure(config)
    assert backend.state is BackendState.CONFIGURED
    # writes: REQUEST_DEVICE_INFO, SET_IDLE(open), SET_IDLE(configure), SWEEP_SETTINGS
    assert adapter.writes[2] == encode_packet(SET_IDLE)
    assert adapter.writes[3] == encode_packet(SWEEP_SETTINGS, _config_sweep_settings(config))
    assert applied.config == config  # already int-valued: no quantization
    assert applied.diff == ConfigDiff.compute(config, config)
    assert backend.session_stats["traces"] == 0


def test_configure_applied_quantization_and_diff() -> None:
    adapter = ScriptedAdapter(open_script() + configure_script())
    backend = make_backend(adapter)
    backend.open()
    config = make_config(
        frequency_start_hz=100_000_000.4,
        frequency_stop_hz=200_000_000.9,
        if_bw_hz=99_999.6,
    )
    applied = backend.configure(config)
    assert applied.config.frequency_start_hz == 100_000_000.0
    assert applied.config.frequency_stop_hz == 200_000_000.0
    assert applied.config.if_bw_hz == 99_999.0
    diff = ConfigDiff.compute(config, applied.config)
    assert applied.diff == diff
    fields = {entry.field for entry in diff.fields}
    assert {"frequency_start_hz", "frequency_stop_hz", "if_bw_hz"} <= fields


def test_configure_rejects_unsupported_channels() -> None:
    adapter = ScriptedAdapter(open_script())
    backend = make_backend(adapter)
    backend.open()
    unsupported = ChannelSpec(
        channel_id="hh_s21",
        logical_polarization=LogicalPolarization.HH,
        s_parameter=SParameter.S21,
        display_name="HH S21",
    )
    with pytest.raises(BackendConfigRejectedError):
        backend.configure(make_config(channels=[unsupported]))


def test_configure_rejects_s22_only() -> None:
    adapter = ScriptedAdapter(open_script())
    backend = make_backend(adapter)
    backend.open()
    # S22-only is not a production-verified stage configuration: the dual
    # bitmap 0x1241 always measures both reflections in one sweep (ISSUE-022).
    with pytest.raises(BackendConfigRejectedError):
        backend.configure(make_config(channels=[S22_CHANNEL]))


def test_configure_rejects_out_of_device_range() -> None:
    adapter = ScriptedAdapter(open_script())
    backend = make_backend(adapter)
    backend.open()
    with pytest.raises(BackendConfigRejectedError):
        backend.configure(make_config(frequency_start_hz=1.0e6))  # < 100 MHz
    with pytest.raises(BackendConfigRejectedError):
        backend.configure(make_config(frequency_stop_hz=8.0e9))  # > 6 GHz
    with pytest.raises(BackendConfigRejectedError):
        backend.configure(make_config(if_bw_hz=5.0e6))  # > 1 MHz
    with pytest.raises(BackendConfigRejectedError):
        backend.configure(make_config(frequency_points=20_000))  # > 10001
    with pytest.raises(BackendConfigRejectedError):
        backend.configure(make_config(power_dbm=30.0))  # > 10 dBm


def test_configure_nack_fail_closed() -> None:
    adapter = ScriptedAdapter([*open_script(), encode_packet(ACK), encode_packet(NACK)])
    backend = make_backend(adapter)
    backend.open()
    with pytest.raises(LibreVnaNackError):
        backend.configure(make_config())
    assert backend.state is BackendState.OPEN  # configure failed: still OPEN
    # re-configure on a healthy script succeeds and acquires
    adapter.extend(
        [encode_packet(ACK), encode_packet(ACK), _sweep_bytes()]
    )
    backend.configure(make_config())
    assert backend.state is BackendState.CONFIGURED
    sweep = backend.acquire()
    assert sweep.metadata is not None
    assert sweep.metadata.trace_index == 0


def test_configure_ack_timeout_fail_closed() -> None:
    adapter = ScriptedAdapter([*open_script(), encode_packet(ACK)])  # no SWEEP_SETTINGS ACK
    backend = make_backend(
        adapter,
        mono_clock=TickClock(),
        settings=LibreVnaUsbSettings(command_timeout_s=0.5),
    )
    backend.open()
    with pytest.raises(BackendTimeoutError):
        backend.configure(make_config())
    assert backend.state is BackendState.OPEN


def test_reconfigure_resets_trace_index_and_stats() -> None:
    adapter = ScriptedAdapter(
        open_script()
        + configure_script()
        + [_sweep_bytes()]
        + configure_script()
        + [_sweep_bytes(stop_hz=190_000_000)]
    )
    backend = make_backend(adapter)
    backend.open()
    backend.configure(make_config())
    first = backend.acquire()
    assert first.metadata is not None
    assert first.metadata.trace_index == 0
    backend.configure(make_config(frequency_stop_hz=190.0e6))
    second = backend.acquire()
    assert second.metadata is not None
    assert second.metadata.trace_index == 0  # counters reset by re-configure


# ---------------------------------------------------------------------------
# acquire: happy paths
# ---------------------------------------------------------------------------


def test_acquire_complete_sweep_values_and_metadata() -> None:
    adapter = ScriptedAdapter(
        open_script() + configure_script() + [_sweep_bytes(port1=0.5 - 0.2j)]
    )
    clock = ManualClock(UTC0, monotonic_ns=1_000_000_000)
    backend = make_backend(adapter, clock=clock)
    backend.open()
    config = make_config()
    backend.configure(config)
    sweep = backend.acquire()
    assert sweep.channels == (S11_CHANNEL,)
    assert sweep.frequencies_hz.shape == (SWEEP_POINTS,)
    assert np.allclose(sweep.frequencies_hz, _expected_axis(config), atol=1.0)
    assert sweep.data.shape == (1, SWEEP_POINTS)
    expected_s11 = (0.5 - 0.2j) / (1.0 + 0.0j)
    assert np.allclose(sweep.data, expected_s11)
    metadata = sweep.metadata
    assert metadata is not None
    assert metadata.mission_id == MISSION
    assert metadata.trace_index == 0
    assert metadata.device_id == DEVICE
    assert metadata.connection_generation == 1
    assert (
        metadata.sweep_started_utc
        <= metadata.sweep_midpoint_utc
        <= metadata.sweep_finished_utc
    )
    assert (
        metadata.sweep_started_monotonic_ns.ns
        <= metadata.sweep_midpoint_monotonic_ns.ns
        <= metadata.sweep_finished_monotonic_ns.ns
    )
    assert metadata.raw_trace_sha256 is not None
    expected_hash = RawHashSpec(
        mission_id=MISSION,
        trace_index=0,
        trace_uid=metadata.trace_uid,
        channels=sweep.channels,
        frequencies_hz=sweep.frequencies_hz,
        data=sweep.data,
    ).compute()
    assert metadata.raw_trace_sha256 == expected_hash
    assert metadata.gnss_match is None
    assert metadata.quality_status is TraceQualityStatus.DEGRADED
    assert metadata.quality_reasons == (TraceQualityReason.GNSS_MISSING,)
    assert backend.session_stats["traces"] == 1


def test_acquire_two_sweeps_trace_index_and_interval() -> None:
    adapter = ScriptedAdapter(
        open_script()
        + configure_script()
        + [_sweep_bytes(), _sweep_bytes()]
    )
    clock = ManualClock(UTC0, monotonic_ns=1_000_000_000)
    backend = make_backend(adapter, clock=clock)
    backend.open()
    backend.configure(make_config(target_interval_s=0.5))
    first = backend.acquire()
    assert first.metadata is not None
    assert first.metadata.trace_index == 0
    assert first.metadata.actual_interval_s is None
    assert first.metadata.schedule_error_s is None
    clock.advance_monotonic(500_000_000)
    clock.advance_utc(timedelta(seconds=0.5))
    second = backend.acquire()
    assert second.metadata is not None
    assert second.metadata.trace_index == 1
    assert second.metadata.actual_interval_s == 0.5
    assert second.metadata.schedule_error_s == 0.0
    assert backend.session_stats["traces"] == 2


def test_acquire_across_read_boundary() -> None:
    sweep = _sweep_bytes()
    split = len(sweep) // 2
    adapter = ScriptedAdapter(
        open_script() + configure_script() + [sweep[:split], sweep[split:]]
    )
    backend = make_backend(adapter)
    backend.open()
    backend.configure(make_config())
    result = backend.acquire()
    assert result.data.shape == (1, SWEEP_POINTS)
    assert result.metadata is not None
    assert result.metadata.trace_index == 0


def test_acquire_datapoints_arriving_with_ack() -> None:
    sweep = _sweep_bytes()
    split = len(sweep) // 2
    adapter = ScriptedAdapter(
        [*open_script(), encode_packet(ACK), encode_packet(ACK) + sweep[:split], sweep[split:]]
    )
    backend = make_backend(adapter)
    backend.open()
    backend.configure(make_config())
    result = backend.acquire()
    assert result.data.shape == (1, SWEEP_POINTS)
    assert result.metadata is not None
    assert result.metadata.trace_index == 0


def test_acquire_two_sweeps_in_one_read() -> None:
    sweep1 = _sweep_bytes(points=11, port1=0.5 + 0.1j)
    sweep2 = _sweep_bytes(points=11, port1=0.2 - 0.3j)
    adapter = ScriptedAdapter(open_script() + configure_script() + [sweep1 + sweep2])
    backend = make_backend(adapter)
    backend.open()
    backend.configure(make_config(frequency_points=11))
    first = backend.acquire()
    second = backend.acquire()
    assert first.metadata is not None and first.metadata.trace_index == 0
    assert second.metadata is not None and second.metadata.trace_index == 1
    assert np.allclose(first.data, (0.5 + 0.1j) / 1.0)
    assert np.allclose(second.data, (0.2 - 0.3j) / 1.0)
    assert backend.session_stats["traces"] == 2


# ---------------------------------------------------------------------------
# requested/applied: axis gate before the first trace
# ---------------------------------------------------------------------------


def test_first_sweep_axis_mismatch_rejected_no_trace() -> None:
    adapter = ScriptedAdapter(
        open_script() + configure_script() + [_sweep_bytes(shift_hz=10_000)]
    )
    backend = make_backend(adapter)
    backend.open()
    backend.configure(make_config())
    with pytest.raises(BackendConfigRejectedError):
        backend.acquire()
    assert backend.session_stats["traces"] == 0  # no formal trace allocated


def test_axis_within_tolerance_accepted() -> None:
    adapter = ScriptedAdapter(open_script() + configure_script() + [_sweep_bytes()])
    backend = make_backend(adapter)
    backend.open()
    backend.configure(make_config())
    result = backend.acquire()
    assert result.data.shape == (1, SWEEP_POINTS)


# ---------------------------------------------------------------------------
# partial / bad sweeps never produce a trace
# ---------------------------------------------------------------------------


def test_incomplete_sweep_timeout_no_trace() -> None:
    partial = b"".join(
        _point_packet(SWEEP_START_HZ + i * 1_000_000, i) for i in range(50)
    )
    adapter = ScriptedAdapter(open_script() + configure_script() + [partial])
    backend = make_backend(
        adapter,
        mono_clock=TickClock(),
        settings=LibreVnaUsbSettings(sweep_timeout_s=0.5),
    )
    backend.open()
    backend.configure(make_config())
    with pytest.raises(BackendTimeoutError):
        backend.acquire()
    stats = backend.session_stats
    assert stats["traces"] == 0
    assert stats["timeouts"] == 1
    assert stats["incomplete_sweeps"] == 1
    assert stats["dropped_sweeps"] == 1


def test_no_data_sweep_timeout() -> None:
    adapter = ScriptedAdapter(open_script() + configure_script())  # device silent
    backend = make_backend(
        adapter,
        mono_clock=TickClock(),
        settings=LibreVnaUsbSettings(sweep_timeout_s=0.5),
    )
    backend.open()
    backend.configure(make_config())
    with pytest.raises(BackendTimeoutError):
        backend.acquire()
    assert backend.session_stats["traces"] == 0


def test_acquire_caller_timeout_cap() -> None:
    adapter = ScriptedAdapter(open_script() + configure_script())  # device silent
    backend = make_backend(
        adapter,
        mono_clock=TickClock(),
        settings=LibreVnaUsbSettings(sweep_timeout_s=60.0),
    )
    backend.open()
    backend.configure(make_config())
    with pytest.raises(BackendTimeoutError):
        backend.acquire(timeout_s=0.5)
    assert backend.session_stats["traces"] == 0


def test_duplicate_point_no_fake_trace_then_complete() -> None:
    sweep = _sweep_bytes(points=11)
    # a lone point 0 starts a sweep; the sweep's own point 0 interrupts it
    dup = _point_packet(SWEEP_START_HZ, 0) + sweep
    adapter = ScriptedAdapter(open_script() + configure_script() + [dup])
    backend = make_backend(adapter)
    backend.open()
    backend.configure(make_config(frequency_points=11))
    result = backend.acquire()
    assert result.metadata is not None
    assert result.metadata.trace_index == 0
    stats = backend.session_stats
    assert stats["dropped_sweeps"] == 1  # interrupted incomplete sweep
    assert stats["incomplete_sweeps"] == 1


def test_out_of_range_point_no_fake_trace() -> None:
    bad = _point_packet(SWEEP_START_HZ, 0) + _point_packet(SWEEP_START_HZ, 99)
    sweep = _sweep_bytes(points=11)
    adapter = ScriptedAdapter(open_script() + configure_script() + [bad + sweep])
    backend = make_backend(adapter)
    backend.open()
    backend.configure(make_config(frequency_points=11))
    result = backend.acquire()
    assert result.metadata is not None
    assert result.metadata.trace_index == 0
    stats = backend.session_stats
    assert stats["out_of_range_points"] == 1
    assert stats["dropped_sweeps"] == 1


def test_zero_reference_no_fake_trace() -> None:
    bad = _point_packet(SWEEP_START_HZ, 0, ref=0.0 + 0.0j)
    sweep = _sweep_bytes(points=11)
    adapter = ScriptedAdapter(open_script() + configure_script() + [bad + sweep])
    backend = make_backend(adapter)
    backend.open()
    backend.configure(make_config(frequency_points=11))
    result = backend.acquire()
    assert result.metadata is not None
    assert result.metadata.trace_index == 0
    assert backend.session_stats["invalid_points"] == 1


def test_malformed_datapoint_fail_closed() -> None:
    malformed = encode_packet(VNA_DATAPOINT, b"\x00" * 13)  # not 12 + 9*k
    adapter = ScriptedAdapter(open_script() + configure_script() + [malformed])
    backend = make_backend(adapter)
    backend.open()
    backend.configure(make_config())
    with pytest.raises(LibreVnaProtocolError):
        backend.acquire()
    assert backend.session_stats["traces"] == 0


# ---------------------------------------------------------------------------
# NACK / control packets
# ---------------------------------------------------------------------------


def test_nack_during_configure() -> None:
    adapter = ScriptedAdapter([*open_script(), encode_packet(ACK), encode_packet(NACK)])
    backend = make_backend(adapter)
    backend.open()
    with pytest.raises(LibreVnaNackError):
        backend.configure(make_config())


def test_nack_during_acquire_fails_closed() -> None:
    sweep = _sweep_bytes(points=11)
    adapter = ScriptedAdapter(
        open_script()
        + configure_script()
        + [_point_packet(SWEEP_START_HZ, 0) + encode_packet(NACK) + sweep]
    )
    backend = make_backend(adapter)
    backend.open()
    backend.configure(make_config(frequency_points=11))
    with pytest.raises(LibreVnaNackError):
        backend.acquire()
    assert backend.session_stats["traces"] == 0


def test_unexpected_ack_and_ignored_packets_stats() -> None:
    sweep = _sweep_bytes(points=11)
    noise = encode_packet(ACK) + encode_packet(DEVICE_INFO, b"\x00" * 8) + sweep
    adapter = ScriptedAdapter(open_script() + configure_script() + [noise])
    backend = make_backend(adapter)
    backend.open()
    backend.configure(make_config(frequency_points=11))
    result = backend.acquire()
    assert result.data.shape == (1, 11)
    stats = backend.session_stats
    assert stats["unexpected_acks"] == 1
    assert stats["ignored_packets"] == 1


# ---------------------------------------------------------------------------
# cancel / close / disconnect
# ---------------------------------------------------------------------------


def test_cancel_interrupts_acquire() -> None:
    # Real monotonic clock + a long sweep timeout: the acquire blocks on the
    # silent device until cancelled (no sleeps, event-driven).
    adapter = ScriptedAdapter(open_script() + configure_script())  # silent device
    backend = make_backend(
        adapter,
        mono_clock=time.monotonic,
        settings=LibreVnaUsbSettings(sweep_timeout_s=60.0),
    )
    backend.open()
    backend.configure(make_config())
    errors: list[BaseException] = []

    def run() -> None:
        try:
            backend.acquire()
        except BaseException as exc:  # captured for assertion
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert backend.acquire_started.wait(5.0)
    backend.cancel()
    thread.join(5.0)
    assert not thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], BackendCancelledError)


def test_close_interrupts_acquire() -> None:
    adapter = ScriptedAdapter(open_script() + configure_script())  # silent device
    backend = make_backend(
        adapter,
        mono_clock=time.monotonic,
        settings=LibreVnaUsbSettings(sweep_timeout_s=60.0),
    )
    backend.open()
    backend.configure(make_config())
    errors: list[BaseException] = []

    def run() -> None:
        try:
            backend.acquire()
        except BaseException as exc:  # captured for assertion
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert backend.acquire_started.wait(5.0)
    backend.close()
    thread.join(5.0)
    assert not thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], BackendClosedError)
    assert adapter.closed


def test_close_idempotent_no_leak_set_idle() -> None:
    adapter = ScriptedAdapter(open_script() + configure_script())
    backend = make_backend(adapter)
    backend.open()
    backend.configure(make_config())
    backend.close()
    backend.close()  # idempotent
    assert adapter.closed
    assert backend.state is BackendState.CLOSED
    # writes: REQUEST_DEVICE_INFO, SET_IDLE(open), SET_IDLE(cfg), SWEEP_SETTINGS,
    #         SET_IDLE(close)
    assert adapter.writes[-1] == encode_packet(SET_IDLE)
    set_idle_count = sum(1 for w in adapter.writes if w == encode_packet(SET_IDLE))
    assert set_idle_count == 3


def test_disconnect_during_acquire_bumps_generation() -> None:
    adapter = ScriptedAdapter(
        open_script() + configure_script() + [LibreVnaDisconnectedError("usb gone")]
    )
    backend = make_backend(adapter)
    backend.open()
    assert backend.connection_generation == 1
    backend.configure(make_config())
    with pytest.raises(BackendDisconnectedError):
        backend.acquire()
    assert backend.connection_generation == 2


# ---------------------------------------------------------------------------
# metadata / identity / stats
# ---------------------------------------------------------------------------


def test_metadata_trace_identity_raw_hash_and_gnss() -> None:
    adapter = ScriptedAdapter(
        open_script() + configure_script() + [_sweep_bytes(points=11, port1=-0.25 + 0.75j)]
    )
    backend = make_backend(adapter)
    backend.open()
    backend.configure(make_config(frequency_points=11))
    sweep = backend.acquire()
    metadata = sweep.metadata
    assert metadata is not None
    assert metadata.trace_uid is not None
    assert len(metadata.raw_trace_sha256 or "") == 64
    assert metadata.target_interval_s == 0.5
    assert np.allclose(sweep.data, (-0.25 + 0.75j) / 1.0)
    # the assembled datapoints satisfy the S11 receiver plan
    for i, freq in enumerate(sweep.frequencies_hz):
        dp = parse_vna_datapoint(_point_payload(int(freq), i, port1=-0.25 + 0.75j))
        assert datapoint_matches_plan(dp, S11_RECEIVER_PLAN)


def test_session_stats_observable() -> None:
    adapter = ScriptedAdapter(open_script() + configure_script())
    backend = make_backend(adapter)
    stats = backend.session_stats
    assert set(stats) == {
        "traces",
        "dropped_sweeps",
        "incomplete_sweeps",
        "timeouts",
        "duplicate_points",
        "out_of_range_points",
        "invalid_points",
        "unexpected_acks",
        "ignored_packets",
    }
    assert all(v == 0 for v in stats.values())


# ---------------------------------------------------------------------------
# lifecycle discipline
# ---------------------------------------------------------------------------


def test_lifecycle_illegal_transitions_structured() -> None:
    adapter = ScriptedAdapter(open_script())
    backend = make_backend(adapter)
    with pytest.raises(BackendError):
        backend.acquire()  # CLOSED
    with pytest.raises(BackendError):
        backend.configure(make_config())  # CLOSED
    backend.open()
    with pytest.raises(BackendError):
        backend.open()  # OPEN again
    with pytest.raises(BackendError):
        backend.acquire()  # OPEN, not configured
    backend.close()
    backend.close()  # no-op


# ---------------------------------------------------------------------------
# ISSUE-022: same-sweep S11/S22 dual reflection
# ---------------------------------------------------------------------------


def test_golden_dual_sweep_settings_encode() -> None:
    payload = encode_sweep_settings(
        100_000_000, 1_000_000_000, 101, 100_000, -10.0, stages_bitmap=0x1241
    )
    assert payload == bytes.fromhex(DUAL_SWEEP_SETTINGS_PAYLOAD_HEX)
    settings = SweepSettings(
        start_hz=100_000_000,
        stop_hz=1_000_000_000,
        points=101,
        ifbw_hz=100_000,
        power_dbm=-10.0,
        stages_bitmap=S11_S22_STAGES_BITMAP,
    )
    assert settings.encode() == bytes.fromhex(DUAL_SWEEP_SETTINGS_PAYLOAD_HEX)


def test_dual_configure_sends_dual_stages_bitmap() -> None:
    adapter = ScriptedAdapter(open_script() + configure_script())
    backend = make_backend(adapter)
    backend.open()
    config = make_dual_config()
    backend.configure(config)
    assert backend.state is BackendState.CONFIGURED
    # writes: REQUEST_DEVICE_INFO, SET_IDLE(open), SET_IDLE(configure),
    # SWEEP_SETTINGS with the dual stages bitmap 0x1241.
    assert adapter.writes[3] == encode_packet(
        SWEEP_SETTINGS, _config_dual_sweep_settings(config)
    )


def test_dual_acquire_values_shape_and_metadata() -> None:
    adapter = ScriptedAdapter(open_script() + configure_script() + [_dual_sweep_bytes()])
    clock = ManualClock(UTC0, monotonic_ns=1_000_000_000)
    backend = make_backend(adapter, clock=clock)
    backend.open()
    config = make_dual_config()
    backend.configure(config)
    sweep = backend.acquire()
    assert sweep.channels == (S11_CHANNEL, S22_CHANNEL)
    assert sweep.frequencies_hz.shape == (SWEEP_POINTS,)
    assert np.allclose(sweep.frequencies_hz, _expected_axis(config), atol=1.0)
    assert sweep.data.shape == (2, SWEEP_POINTS)
    assert np.allclose(sweep.data[0], (0.5 - 0.2j) / (1.0 + 0.0j))  # S11
    assert np.allclose(sweep.data[1], (0.3 + 0.1j) / (1.5 + 0.0j))  # S22
    metadata = sweep.metadata
    assert metadata is not None
    assert metadata.trace_index == 0
    assert metadata.connection_generation == 1
    assert (
        metadata.sweep_started_utc
        <= metadata.sweep_midpoint_utc
        <= metadata.sweep_finished_utc
    )
    assert (
        metadata.sweep_started_monotonic_ns.ns
        <= metadata.sweep_midpoint_monotonic_ns.ns
        <= metadata.sweep_finished_monotonic_ns.ns
    )
    expected_hash = RawHashSpec(
        mission_id=MISSION,
        trace_index=0,
        trace_uid=metadata.trace_uid,
        channels=sweep.channels,
        frequencies_hz=sweep.frequencies_hz,
        data=sweep.data,
    ).compute()
    assert metadata.raw_trace_sha256 == expected_hash
    assert backend.session_stats["traces"] == 1


def test_dual_channel_order_from_config() -> None:
    adapter = ScriptedAdapter(open_script() + configure_script() + [_dual_sweep_bytes()])
    backend = make_backend(adapter)
    backend.open()
    config = make_config(channels=[S22_CHANNEL, S11_CHANNEL])
    backend.configure(config)
    sweep = backend.acquire()
    assert sweep.channels == (S22_CHANNEL, S11_CHANNEL)
    assert sweep.data.shape == (2, SWEEP_POINTS)
    # row semantics come from each ChannelSpec's s_parameter, not the row
    # index (HH:S11 / VV:S22 default binding is configuration, not a
    # hardcoded array layout).
    assert np.allclose(sweep.data[0], (0.3 + 0.1j) / (1.5 + 0.0j))  # S22 row
    assert np.allclose(sweep.data[1], (0.5 - 0.2j) / (1.0 + 0.0j))  # S11 row


def test_dual_partial_channel_failure_no_trace() -> None:
    # S11-only payloads under a dual config: every datapoint lacks the
    # stage-1 slots -> plan-invalid -> no sweep is ever assembled -> the
    # whole trace is rejected (never a partial channel output).
    adapter = ScriptedAdapter(open_script() + configure_script() + [_sweep_bytes()])
    backend = make_backend(
        adapter,
        mono_clock=TickClock(),
        settings=LibreVnaUsbSettings(sweep_timeout_s=0.5),
    )
    backend.open()
    backend.configure(make_dual_config())
    with pytest.raises(BackendTimeoutError):
        backend.acquire()
    stats = backend.session_stats
    assert stats["traces"] == 0
    assert stats["invalid_points"] == SWEEP_POINTS


def test_dual_s22_zero_reference_no_trace() -> None:
    # stage-1 reference magnitude zero -> every datapoint plan-invalid
    # (bad denominator) -> whole trace rejected.
    adapter = ScriptedAdapter(
        open_script() + configure_script() + [_dual_sweep_bytes(ref2=0.0 + 0.0j)]
    )
    backend = make_backend(
        adapter,
        mono_clock=TickClock(),
        settings=LibreVnaUsbSettings(sweep_timeout_s=0.5),
    )
    backend.open()
    backend.configure(make_dual_config())
    with pytest.raises(BackendTimeoutError):
        backend.acquire()
    stats = backend.session_stats
    assert stats["traces"] == 0
    assert stats["invalid_points"] == SWEEP_POINTS


def test_dual_two_sweeps_in_one_read() -> None:
    # dual-reflection throughput: two complete dual sweeps in one USB read
    # -> two traces with per-sweep values and monotonic trace indices.
    sweep1 = _dual_sweep_bytes(points=11, port1=0.5 + 0.1j, port2=0.2 - 0.3j)
    sweep2 = _dual_sweep_bytes(points=11, port1=0.9 - 0.1j, port2=-0.4 + 0.2j)
    adapter = ScriptedAdapter(open_script() + configure_script() + [sweep1 + sweep2])
    backend = make_backend(adapter)
    backend.open()
    backend.configure(make_dual_config(frequency_points=11))
    first = backend.acquire()
    second = backend.acquire()
    assert first.metadata is not None and first.metadata.trace_index == 0
    assert second.metadata is not None and second.metadata.trace_index == 1
    assert np.allclose(first.data[0], (0.5 + 0.1j) / 1.0)
    assert np.allclose(first.data[1], (0.2 - 0.3j) / 1.5)
    assert np.allclose(second.data[0], (0.9 - 0.1j) / 1.0)
    assert np.allclose(second.data[1], (-0.4 + 0.2j) / 1.5)
    assert backend.session_stats["traces"] == 2


def test_dual_first_sweep_axis_mismatch_rejected() -> None:
    adapter = ScriptedAdapter(
        open_script() + configure_script() + [_dual_sweep_bytes(shift_hz=10_000)]
    )
    backend = make_backend(adapter)
    backend.open()
    backend.configure(make_dual_config())
    with pytest.raises(BackendConfigRejectedError):
        backend.acquire()
    assert backend.session_stats["traces"] == 0


# ---------------------------------------------------------------------------
# ISSUE-023: disconnect/reconnect, backoff, controller in-flight cooperation
# ---------------------------------------------------------------------------

INTERVAL_NS = 500_000_000  # matches make_config().target_interval_s (0.5 s)


def reconnect_script() -> list[object]:
    """Reads consumed by a successful ``reconnect_session``: DEVICE_INFO +
    ACK (SET_IDLE) + ACK (SET_IDLE) + ACK (SWEEP_SETTINGS)."""
    return [
        encode_packet(DEVICE_INFO, _device_info_payload()),
        encode_packet(ACK),
        encode_packet(ACK),
        encode_packet(ACK),
    ]


class ManualWaiter:
    """Event-based interruptible waiter (ISSUE-017 controller test pattern)."""

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


def advance_and_wake(clock: ManualClock, waiter: ManualWaiter) -> None:
    """Sync on the scheduler wait, advance one virtual interval, wake."""
    assert waiter.waiting_event.wait(10.0)
    clock.advance_monotonic(INTERVAL_NS)
    waiter.wake()


def test_reconnect_session_preserves_trace_and_bumps_generation() -> None:
    adapter = ScriptedAdapter(
        open_script()
        + configure_script()
        + [
            _sweep_bytes(),
            LibreVnaDisconnectedError("usb unplugged"),
            *reconnect_script(),
            _sweep_bytes(),
        ]
    )
    backend = make_backend(adapter)
    config = make_config()
    backend.open()
    backend.configure(config)
    first = backend.acquire()
    assert first.metadata.trace_index == 0
    assert first.metadata.connection_generation == 1
    with pytest.raises(BackendDisconnectedError):
        backend.acquire()
    assert backend.connection_generation == 2
    applied = backend.reconnect_session(config)
    assert backend.connection_generation == 3
    assert backend.state is BackendState.CONFIGURED
    assert applied.config.frequency_points == SWEEP_POINTS
    second = backend.acquire()
    assert second.metadata.trace_index == 1  # no duplicate index after reconnect
    assert second.metadata.connection_generation == 3
    assert second.metadata.trace_uid != first.metadata.trace_uid
    assert backend.session_stats["traces"] == 2


def test_reconnect_session_reapplies_axis_gate_on_first_sweep() -> None:
    adapter = ScriptedAdapter(
        open_script()
        + configure_script()
        + [
            _sweep_bytes(),
            LibreVnaDisconnectedError("usb unplugged"),
            *reconnect_script(),
            _sweep_bytes(shift_hz=500),
        ]
    )
    backend = make_backend(adapter)
    config = make_config()
    backend.open()
    backend.configure(config)
    assert backend.acquire().metadata.trace_index == 0
    with pytest.raises(BackendDisconnectedError):
        backend.acquire()
    backend.reconnect_session(config)
    with pytest.raises(BackendConfigRejectedError):
        backend.acquire()  # first post-reconnect sweep must pass the axis gate
    assert backend.session_stats["traces"] == 1  # no trace allocated


def test_reconnect_session_failure_fails_closed_without_resetting_trace() -> None:
    adapter = ScriptedAdapter(
        open_script()
        + configure_script()
        + [
            _sweep_bytes(),
            LibreVnaDisconnectedError("usb unplugged"),
            # no reconnect reads: the device-info phase times out
        ]
    )
    backend = make_backend(adapter)
    config = make_config()
    backend.open()
    backend.configure(config)
    assert backend.acquire().metadata.trace_index == 0
    with pytest.raises(BackendDisconnectedError):
        backend.acquire()
    with pytest.raises(BackendTimeoutError):
        backend.reconnect_session(config)
    assert backend.connection_generation == 2  # no bump on failure
    assert adapter.closed  # transport released on failure
    with pytest.raises(BackendStateError):
        backend.acquire()  # fail-closed: unconfigured session
    assert backend.session_stats["traces"] == 1  # trace counter preserved


def test_reconnect_policy_delays_are_exponential_and_capped() -> None:
    policy = LibreVnaReconnectPolicy(
        max_attempts=5, initial_delay_s=0.5, backoff_factor=2.0, max_delay_s=4.0
    )
    assert policy.delay_after_failed_attempt(1) == 0.5
    assert policy.delay_after_failed_attempt(2) == 1.0
    assert policy.delay_after_failed_attempt(3) == 2.0
    assert policy.delay_after_failed_attempt(4) == 4.0  # capped at max_delay_s
    assert policy.delay_after_failed_attempt(9) == 4.0
    with pytest.raises(ValueError):
        LibreVnaReconnectPolicy(max_attempts=0)
    with pytest.raises(ValueError):
        LibreVnaReconnectPolicy(initial_delay_s=-1.0)
    with pytest.raises(ValueError):
        LibreVnaReconnectPolicy(backoff_factor=0.5)
    with pytest.raises(ValueError):
        LibreVnaReconnectPolicy(max_delay_s=0.1, initial_delay_s=1.0)


def test_reconnector_retries_with_backoff_then_succeeds() -> None:
    adapter = ScriptedAdapter(
        open_script()
        + configure_script()
        + [_sweep_bytes(), LibreVnaDisconnectedError("usb unplugged")]
    )
    backend = make_backend(adapter)
    config = make_config()
    backend.open()
    backend.configure(config)
    assert backend.acquire().metadata.trace_index == 0
    with pytest.raises(BackendDisconnectedError):
        backend.acquire()
    # the device is gone: every reconnect open fails until it reappears
    adapter.open_error = LibreVnaDeviceNotFoundError("device not present yet")
    waits: list[float] = []

    def wait(delay: float) -> None:
        waits.append(delay)
        if len(waits) == 2:  # device reappears before attempt 3
            adapter.open_error = None
            adapter.extend([*reconnect_script(), _sweep_bytes()])

    reconnector = LibreVnaReconnector(
        backend,
        config,
        policy=LibreVnaReconnectPolicy(
            max_attempts=3, initial_delay_s=0.1, backoff_factor=2.0, max_delay_s=1.0
        ),
        wait=wait,
    )
    applied = reconnector()
    assert applied.config.frequency_points == SWEEP_POINTS
    assert waits == [0.1, 0.2]
    assert backend.connection_generation == 3
    sweep = backend.acquire()
    assert sweep.metadata.trace_index == 1
    assert sweep.metadata.connection_generation == 3


def test_reconnector_exhaustion_raises_structured_error() -> None:
    adapter = ScriptedAdapter(
        open_script()
        + configure_script()
        + [_sweep_bytes(), LibreVnaDisconnectedError("usb unplugged")]
    )
    backend = make_backend(adapter)
    config = make_config()
    backend.open()
    backend.configure(config)
    assert backend.acquire().metadata.trace_index == 0
    with pytest.raises(BackendDisconnectedError):
        backend.acquire()
    adapter.open_error = LibreVnaDeviceNotFoundError("never present")
    waits: list[float] = []
    reconnector = LibreVnaReconnector(
        backend,
        config,
        policy=LibreVnaReconnectPolicy(
            max_attempts=3, initial_delay_s=0.1, backoff_factor=2.0, max_delay_s=1.0
        ),
        wait=lambda delay: waits.append(delay),
    )
    with pytest.raises(LibreVnaReconnectError) as excinfo:
        reconnector()
    assert excinfo.value.reason == "reconnect_failed"
    assert excinfo.value.context["attempts"] == 3
    assert waits == [0.1, 0.2]
    assert backend.connection_generation == 2


def test_reconnector_propagates_cancellation() -> None:
    adapter = ScriptedAdapter(open_script() + configure_script())
    backend = make_backend(adapter)
    backend.open()
    backend.configure(make_config())
    backend.cancel()
    reconnector = LibreVnaReconnector(backend, make_config())
    with pytest.raises(BackendCancelledError):
        reconnector()


def test_controller_reconnect_hook_librevna_continues_without_duplicate_trace() -> None:
    adapter = ScriptedAdapter(
        open_script()
        + configure_script()
        + [
            _sweep_bytes(),
            LibreVnaDisconnectedError("usb unplugged"),
            *reconnect_script(),
            _sweep_bytes(),
            _sweep_bytes(),
        ]
    )
    backend = make_backend(adapter)
    config = make_config()
    clock = ManualClock(UTC0, 0)
    waiter = ManualWaiter()
    controller = AcquisitionController(
        backend,
        clock=clock,
        waiter=waiter,
        reconnect_hook=LibreVnaReconnector(
            backend,
            config,
            policy=LibreVnaReconnectPolicy(
                max_attempts=1, initial_delay_s=0.01, backoff_factor=2.0, max_delay_s=0.01
            ),
        ),
    )
    controller.configure(config)
    controller.start()
    first = controller.sweeps.get(2.0)
    assert first is not None
    assert first.metadata.trace_index == 0
    assert first.metadata.connection_generation == 1
    advance_and_wake(clock, waiter)  # next tick pops the disconnect; hook reconnects
    second = controller.sweeps.get(2.0)
    assert second is not None
    assert second.metadata.trace_index == 1
    assert second.metadata.connection_generation == 3
    assert controller.connection_generation == 3
    assert controller.state is ControllerState.RUNNING
    controller.stop()
    assert controller.wait_finished(2.0)
    assert controller.state is ControllerState.STOPPED
    controller.close()
    assert adapter.closed


def test_controller_pause_resume_stop_librevna_backend_no_leak() -> None:
    adapter = ScriptedAdapter(
        open_script() + configure_script() + [_sweep_bytes() for _ in range(6)]
    )
    backend = make_backend(adapter)
    config = make_config()
    clock = ManualClock(UTC0, 0)
    waiter = ManualWaiter()
    controller = AcquisitionController(backend, clock=clock, waiter=waiter)
    controller.configure(config)
    controller.start()
    first = controller.sweeps.get(2.0)
    assert first is not None
    controller.pause()
    assert controller.state is ControllerState.PAUSED
    # paused: clock advances + wakes produce no new sweep
    clock.advance_monotonic(INTERVAL_NS)
    waiter.wake()
    assert controller.sweeps.get(0.2) is None
    controller.resume()
    assert controller.state is ControllerState.RUNNING
    advance_and_wake(clock, waiter)
    second = controller.sweeps.get(2.0)
    assert second is not None
    controller.stop()
    assert controller.wait_finished(2.0)
    assert controller.state is ControllerState.STOPPED
    assert controller.join(2.0)
    controller.close()
    assert adapter.closed
    indices = [first.metadata.trace_index, second.metadata.trace_index]
    assert indices == sorted(indices)
    assert len(set(indices)) == len(indices)
    assert first.metadata.connection_generation == 1
    assert second.metadata.connection_generation == 1


def test_controller_emergency_stop_interrupts_in_flight_librevna() -> None:
    adapter = ScriptedAdapter(open_script() + configure_script())  # silent device
    backend = make_backend(
        adapter,
        mono_clock=time.monotonic,
        settings=LibreVnaUsbSettings(sweep_timeout_s=60.0),
    )
    config = make_config()
    clock = ManualClock(UTC0, 0)
    waiter = ManualWaiter()
    controller = AcquisitionController(backend, clock=clock, waiter=waiter)
    controller.configure(config)
    controller.start()
    # the first sweep is due immediately after start(): the worker blocks in
    # acquire on the silent device until interrupted
    assert backend.acquire_started.wait(5.0)
    controller.emergency_stop()
    assert controller.wait_finished(5.0)
    assert controller.state is ControllerState.STOPPED
    assert controller.stop_reason is StopReason.EMERGENCY
    assert controller.sweeps.size == 0  # interrupted sweep is never published
    controller.close()
    assert adapter.closed


# ---------------------------------------------------------------------------
# ISSUE-023 repair round 2 (t10): emergency_stop x reconnect race (P2-1),
# reconnect identity re-verification, cancellable backoff wait
# ---------------------------------------------------------------------------

#: Same golden DEVICE_INFO layout but firmware 2.2.3 (was 1.2.3): a different
#: physical unit must be rejected fail-closed on reconnect.
OTHER_DEVICE_INFO_PAYLOAD_HEX = (
    "0e00020203054100e1f5050000000000bca065010000000100000040420f00112748f4e803"
    "0100000040420f00c8007841cb0200000002ffff"
)


def test_controller_emergency_stop_races_reconnect_ends_stopped() -> None:
    """Probe A (P2-1): emergency_stop during the reconnect backoff must end
    STOPPED/EMERGENCY, never FAILED (the hook abort is a stop race, not a
    fault -- same pattern as the cancelled/closed acquire paths)."""
    adapter = ScriptedAdapter(
        open_script()
        + configure_script()
        + [_sweep_bytes(), LibreVnaDisconnectedError("usb unplugged")]
    )
    backend = make_backend(adapter)
    config = make_config()
    clock = ManualClock(UTC0, 0)
    waiter = ManualWaiter()
    entered = threading.Event()

    def hook() -> None:
        entered.set()
        # every reconnect attempt fails (no reads left); the default backoff
        # wait is cancellable so emergency_stop aborts it promptly
        LibreVnaReconnector(
            backend,
            config,
            policy=LibreVnaReconnectPolicy(
                max_attempts=5, initial_delay_s=1.0, backoff_factor=2.0, max_delay_s=8.0
            ),
        )()

    controller = AcquisitionController(
        backend, clock=clock, waiter=waiter, reconnect_hook=hook
    )
    controller.configure(config)
    controller.start()
    first = controller.sweeps.get(2.0)
    assert first is not None
    advance_and_wake(clock, waiter)  # next tick pops the disconnect: hook starts
    assert entered.wait(5.0)
    controller.emergency_stop()
    assert controller.wait_finished(5.0)
    assert controller.state is ControllerState.STOPPED
    assert controller.stop_reason is StopReason.EMERGENCY
    assert controller.error is None  # never overwritten with a hook-failure
    controller.close()
    assert adapter.closed


def test_reconnect_session_rejects_identity_change() -> None:
    """A reconnected device with a different firmware identity is rejected
    fail-closed: no generation bump, no trace counter reset (P2-1 identity)."""
    adapter = ScriptedAdapter(
        open_script()
        + configure_script()
        + [
            _sweep_bytes(),
            LibreVnaDisconnectedError("usb unplugged"),
            encode_packet(DEVICE_INFO, bytes.fromhex(OTHER_DEVICE_INFO_PAYLOAD_HEX)),
            encode_packet(ACK),
            encode_packet(ACK),
            encode_packet(ACK),
        ]
    )
    backend = make_backend(adapter)
    config = make_config()
    backend.open()
    backend.configure(config)
    assert backend.acquire().metadata.trace_index == 0
    with pytest.raises(BackendDisconnectedError):
        backend.acquire()
    with pytest.raises(LibreVnaProtocolError):
        backend.reconnect_session(config)
    assert backend.connection_generation == 2  # no bump on rejected identity
    assert adapter.closed  # released fail-closed
    assert backend.session_stats["traces"] == 1  # trace counter preserved


def test_backend_wait_cancellable_returns_without_cancel_and_validates() -> None:
    backend = make_backend(ScriptedAdapter(open_script() + configure_script()))
    backend.open()
    backend.configure(make_config())
    backend.wait_cancellable(0.001)  # no cancellation: returns normally
    with pytest.raises(ValueError):
        backend.wait_cancellable(0.0)
    with pytest.raises(ValueError):
        backend.wait_cancellable(-1.0)
