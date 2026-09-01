"""LibreVNA S11/S22 production acquisition backend (ISSUE-021 + ISSUE-022).

The single production path ``LibreVnaUsbBackend`` implements the
``AcquisitionBackend`` contract (docs/ACQUISITION.md section 2) by composing
the frozen ISSUE-019 transport layer (``LibreVnaUsbTransport``: USB session,
frame codec, control packets) with the ISSUE-020 stream layer
(``PacketStream`` frame routing, ``parse_vna_datapoint``,
``StrictSweepAssembler``): only complete, plan-validated sweeps become
``FrequencySweep`` objects with real UTC+monotonic trace metadata; partial or
bad sweeps never allocate a formal trace.

Contract highlights (docs/issues/M04_LIBREVNA.md ISSUE-021/022):

- lifecycle hooks ``_do_open/_do_configure/_do_acquire/_do_close``; the base
  class owns the state machine, cancellation and ``connection_generation``
  (1 after open, +1 per observed USB disconnect);
- ``configure`` validates the device capability range, sends ``SET_IDLE``
  then exactly one ``SWEEP_SETTINGS``, and returns
  ``AppliedConfig(config, diff)`` with the int-quantized applied values; the
  stages bitmap follows the frozen channel configuration (single channel:
  ``0x1240`` S11 only; dual ``(hh_s11, vv_s22)``: ``0x1241`` measures both
  reflections in one sweep); the first completed sweep's actual frequency
  axis is compared against the applied axis (tolerance ``AXIS_TOLERANCE_HZ``)
  and rejected fail-closed before any trace is emitted
  (docs/ACQUISITION.md section 4);
- ISSUE-022 dual reflection: from the *same* ``VNADatapoint`` set,
  ``S11 = stage-0 Port1 receiver / Reference`` and
  ``S22 = stage-1 Port2 receiver / Reference``; the default ``HH:S11`` /
  ``VV:S22`` binding is configuration (the ordered ``ChannelSpec`` list of
  the frozen ``MissionConfig``), never a hardcoded array layout; the output
  is strictly ``channel x frequency`` in config order and the two channels
  share one ``TraceMetadata``/``trace_uid``/raw hash (a single sweep is a
  single trace).  A missing/bad/unsupported channel rejects the whole
  trace; two sequential sweeps are never used to fake a synchronized dual
  channel;
- ``acquire`` routes control packets itself (NACK is fail-closed -- ISSUE-020
  review residual risk 2), feeds datapoints into the strict assembler with
  the configured receiver plan, and computes the S-parameters per point;
- ISSUE-023: ``reconnect_session`` re-establishes the USB session in place
  after a disconnect (backoff/retry policy lives in ``reconnect.py``): the
  frozen config is re-validated and re-applied against a freshly read
  ``DEVICE_INFO``, ``connection_generation`` increments per successful
  reconnect (strictly increasing; the base lifecycle state stays
  ``CONFIGURED`` so the controller's reconnect hook contract holds), trace
  counters are preserved (no duplicate trace index/UID) and the first sweep
  after the reconnect must pass the requested/applied axis gate again;
- errors: lifecycle/state issues use the ``BackendError`` family
  (``BackendConfigRejectedError``/``BackendTimeoutError``/
  ``BackendDisconnectedError``/``BackendCancelledError``/
  ``BackendClosedError``); device/protocol semantics use
  ``LibreVnaNackError``/``LibreVnaProtocolError`` (structured ``DomainError``
  subclasses, the core ``ErrorCode`` enum is read-only);
- thread boundary: this module never creates threads (I/O runs on the
  caller's worker thread, docs/ACQUISITION.md section 1); blocking waits use
  short USB read timeouts plus injected clocks, no fixed sleeps;
- no real devices are enumerated: tests drive a scripted ``UsbAdapter``
  through ``LibreVnaUsbTransport`` (default tests never import ``usb``).
"""

from __future__ import annotations

import math
import struct
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime

import numpy as np

from uav_gpr.acquisition.backend import (
    AcquisitionBackend,
    AppliedConfig,
    BackendConfigRejectedError,
    BackendDisconnectedError,
    BackendState,
    BackendStateError,
    BackendTimeoutError,
    Capabilities,
)
from uav_gpr.acquisition.librevna.stream import (
    DESC_MASK_PORT1,
    DESC_MASK_PORT2,
    DESC_MASK_REFERENCE,
    DESC_STAGE_SHIFT,
    S11_RECEIVER_PLAN,
    AssembledSweep,
    LibreVnaDatapointError,
    LibreVnaStreamError,
    LibreVnaSweepTimeoutError,
    ReceiverSlot,
    StrictSweepAssembler,
    VNADatapoint,
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
    LibreVnaCancelledError,
    LibreVnaDisconnectedError,
    LibreVnaNotOpenError,
    LibreVnaTimeoutError,
    LibreVnaUsbTransport,
    Packet,
    PacketStream,
    encode_packet,
)
from uav_gpr.core import (
    ChannelSpec,
    ConfigDiff,
    DeviceId,
    FrequencySweep,
    LogicalPolarization,
    MissionConfig,
    MissionId,
    RawHashSpec,
    SParameter,
    TraceMetadata,
    TraceQualityReason,
    TraceQualityStatus,
    TraceUid,
)
from uav_gpr.core.errors import JsonValue
from uav_gpr.core.timeutil import Clock, MonotonicNs, SystemClock

# ---------------------------------------------------------------------------
# Protocol constants (reference protocol v14; ISSUE-019 excluded these
# codecs and deferred them to ISSUE-021)
# ---------------------------------------------------------------------------

#: ``DeviceInfo`` decode structure (protocol v14), reference field layout.
_DEVICE_INFO_FORMAT = "<HBBBBcQQIIHhhIIBQBH"

#: ``SweepSettings`` encode structure (protocol v14); ``cdbm`` appears twice
#: (reference format -- not "fixed" here).
_SWEEP_SETTINGS_FORMAT = "<QQHIhBHhH"

#: Single-reflection (S11) stages bitmap: Stages=0, excite port 1 / stage 0
#: (production-verified on LibreVNA firmware 1.6.5 / protocol 14 / hw 1/B).
S11_STAGES_BITMAP = 0x1240

#: Dual-reflection (S11/S22) stages bitmap: stage 0 = S11 input set
#: (Port1/Reference), stage 1 = S22 input set (Port2/Reference), measured in
#: one sweep (ISSUE-022).
S11_S22_STAGES_BITMAP = 0x1241

#: Production-allowed stages bitmaps for this backend: 0x1240 is only valid
#: for a single S11 channel, 0x1241 only for the dual S11/S22 channel set
#: (the binding is enforced by ``_validate_config``/``_build_sweep_settings``).
ALLOWED_STAGES_BITMAPS: tuple[int, ...] = (
    S11_STAGES_BITMAP,
    S11_S22_STAGES_BITMAP,
)

#: Bytes requested per USB bulk read (reference read size).
READ_SIZE = 512

#: Frequency-axis tolerance used by the first-sweep requested/applied gate.
AXIS_TOLERANCE_HZ = 1.0

#: Configure-time requested/applied tolerances (int/0.01 dBm quantization).
_FREQUENCY_TOLERANCE_HZ = 1.0
_POWER_TOLERANCE_DBM = 0.01

#: Completed-but-unreturned sweeps buffered between reads (bounded).
_MAX_PENDING_SWEEPS = 4

#: The S11 channel capability of this backend.
S11_CHANNEL = ChannelSpec(
    channel_id="hh_s11",
    logical_polarization=LogicalPolarization.HH,
    s_parameter=SParameter.S11,
    display_name="HH S11",
)

#: The S22 channel capability of this backend (ISSUE-022).  The default
#: ``HH:S11`` / ``VV:S22`` binding is carried by the frozen ``MissionConfig``
#: channel list (config), never by a hardcoded array layout.
S22_CHANNEL = ChannelSpec(
    channel_id="vv_s22",
    logical_polarization=LogicalPolarization.VV,
    s_parameter=SParameter.S22,
    display_name="VV S22",
)

#: ISSUE-022 dual-reflection receiver plan: stage 0 S11 input set plus
#: stage 1 S22 input set (frozen shape from the ISSUE-020 plan D5).
S11S22_RECEIVER_PLAN: tuple[ReceiverSlot, ...] = (
    *S11_RECEIVER_PLAN,
    ReceiverSlot(1, DESC_MASK_REFERENCE),
    ReceiverSlot(1, DESC_MASK_PORT2),
)

#: S-parameter -> (stage, port receiver mask) binding used to compute each
#: channel row from a datapoint.  The per-channel semantics come from the
#: ``ChannelSpec.s_parameter`` field of the frozen config, so the output
#: layout is config-driven, never a hardcoded array.
_S_PARAMETER_SLOTS: dict[SParameter, tuple[int, int]] = {
    SParameter.S11: (0, DESC_MASK_PORT1),
    SParameter.S22: (1, DESC_MASK_PORT2),
}


# ---------------------------------------------------------------------------
# Structured errors (transport.py/stream.py pattern; core ErrorCode read-only)
# ---------------------------------------------------------------------------


class LibreVnaNackError(LibreVnaStreamError):
    """The device answered a command or acquisition with NACK."""

    _reason = "nack"


class LibreVnaProtocolError(LibreVnaStreamError):
    """Protocol violation: malformed payload or inconsistent device response."""

    _reason = "protocol_error"


# ---------------------------------------------------------------------------
# DeviceInfo (protocol v14)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LibreVnaDeviceInfo:
    """Decoded RequestDeviceInfo response (protocol v14 fields)."""

    protocol: int
    firmware: str
    hardware_version: int
    hardware_revision: str
    min_freq_hz: int
    max_freq_hz: int
    min_ifbw_hz: int
    max_ifbw_hz: int
    max_points: int
    min_dbm: float
    max_dbm: float
    min_rbw_hz: int
    max_rbw_hz: int
    max_amplitude_points: int
    max_harmonic_freq_hz: int
    num_ports: int
    max_dwell_time_us: int


def decode_device_info(payload: bytes) -> LibreVnaDeviceInfo:
    """Decode a protocol v14 ``DEVICE_INFO`` payload (reference layout).

    The reference pads the payload with two zero bytes before unpacking;
    shorter payloads raise a structured :class:`LibreVnaProtocolError`.
    """
    try:
        fields = struct.unpack_from(_DEVICE_INFO_FORMAT, payload + b"\x00\x00", 0)
    except struct.error as exc:
        raise LibreVnaProtocolError(
            "malformed DEVICE_INFO payload",
            payload_length=len(payload),
        ) from exc
    return LibreVnaDeviceInfo(
        protocol=fields[0],
        firmware=f"{fields[1]}.{fields[2]}.{fields[3]}",
        hardware_version=fields[4],
        hardware_revision=fields[5].decode(errors="replace"),
        min_freq_hz=fields[6],
        max_freq_hz=fields[7],
        min_ifbw_hz=fields[8],
        max_ifbw_hz=fields[9],
        max_points=fields[10],
        min_dbm=fields[11] / 100.0,
        max_dbm=fields[12] / 100.0,
        min_rbw_hz=fields[13],
        max_rbw_hz=fields[14],
        max_amplitude_points=fields[15],
        max_harmonic_freq_hz=fields[16],
        num_ports=fields[17],
        max_dwell_time_us=fields[18],
    )


# ---------------------------------------------------------------------------
# SweepSettings (protocol v14 encode)
# ---------------------------------------------------------------------------


def _validate_stages_bitmap(stages_bitmap: int) -> None:
    """Validate ``stages_bitmap``: int, uint16 range, production-verified value.

    :data:`S11_STAGES_BITMAP` (0x1240, S11 only) and
    :data:`S11_S22_STAGES_BITMAP` (0x1241, same-sweep S11/S22 dual, ISSUE-022)
    are the production-verified values; the channel-set binding is enforced
    by ``LibreVnaUsbBackend._validate_config``.
    """
    if isinstance(stages_bitmap, bool) or not isinstance(stages_bitmap, int):
        raise TypeError(
            f"stages_bitmap must be an int, got {type(stages_bitmap).__name__}"
        )
    if stages_bitmap < 0 or stages_bitmap > 0xFFFF:
        raise ValueError(
            f"stages_bitmap must be in 0..0xFFFF, got {stages_bitmap:#x}"
        )
    if stages_bitmap not in ALLOWED_STAGES_BITMAPS:
        raise ValueError(
            f"stages_bitmap must be one of the production-verified values "
            f"{ALLOWED_STAGES_BITMAPS} "
            f"(0x1240 = S11 only, 0x1241 = dual S11/S22), got "
            f"{stages_bitmap:#x}"
        )


def encode_sweep_settings(
    start_hz: int,
    stop_hz: int,
    points: int,
    ifbw_hz: int,
    power_dbm: float,
    dwell_us: int = 0,
    *,
    stages_bitmap: int = S11_STAGES_BITMAP,
) -> bytes:
    """Encode a ``SweepSettings`` payload (protocol v14, reference layout).

    ``config`` is fixed to ``(1<<2) | (1<<3)`` (suppress peaks, fixed power);
    ``power_dbm`` is quantized to 0.01 dBm (int16 centi-dBm).  Only
    production-verified stages bitmaps are accepted.
    """
    cdbm = round(power_dbm * 100)
    config = (1 << 2) | (1 << 3)
    _validate_stages_bitmap(stages_bitmap)
    return struct.pack(
        _SWEEP_SETTINGS_FORMAT,
        int(start_hz),
        int(stop_hz),
        int(points),
        int(ifbw_hz),
        cdbm,
        config,
        int(stages_bitmap),
        cdbm,
        int(dwell_us),
    )


@dataclass(frozen=True)
class SweepSettings:
    """One sweep configuration encoded as a protocol v14 payload."""

    start_hz: int
    stop_hz: int
    points: int
    ifbw_hz: int
    power_dbm: float = -10.0
    dwell_us: int = 0
    stages_bitmap: int = S11_STAGES_BITMAP

    def __post_init__(self) -> None:
        for name in ("start_hz", "stop_hz", "points", "ifbw_hz", "dwell_us"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an int")
        if self.start_hz <= 0 or self.stop_hz <= self.start_hz:
            raise ValueError("SweepSettings start/stop frequencies are invalid")
        if self.points < 2:
            raise ValueError("SweepSettings points must be at least 2")
        if self.ifbw_hz <= 0:
            raise ValueError("SweepSettings ifbw_hz must be positive")
        if isinstance(self.power_dbm, bool) or not isinstance(self.power_dbm, float):
            raise TypeError("power_dbm must be a float")
        if not math.isfinite(self.power_dbm):
            raise ValueError("power_dbm must be finite")
        _validate_stages_bitmap(self.stages_bitmap)

    def encode(self) -> bytes:
        """Encode this settings object as a protocol payload."""
        return encode_sweep_settings(
            self.start_hz,
            self.stop_hz,
            self.points,
            self.ifbw_hz,
            self.power_dbm,
            self.dwell_us,
            stages_bitmap=self.stages_bitmap,
        )


# ---------------------------------------------------------------------------
# Backend settings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LibreVnaUsbSettings:
    """Tunable backend I/O parameters (all deterministic, no hardware)."""

    usb_read_timeout_ms: int = 50
    command_timeout_s: float = 2.0
    device_info_timeout_s: float = 3.0
    sweep_timeout_s: float | None = None
    dwell_us: int = 0
    sweep_timeout_min_s: float = 2.0
    sweep_timeout_factor: float = 5.0

    def __post_init__(self) -> None:
        for name in ("usb_read_timeout_ms", "dwell_us"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative int")
        for name in (
            "command_timeout_s",
            "device_info_timeout_s",
            "sweep_timeout_min_s",
            "sweep_timeout_factor",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or value <= 0
            ):
                raise ValueError(f"{name} must be a positive finite number")
        if self.sweep_timeout_s is not None and (
            isinstance(self.sweep_timeout_s, bool)
            or not isinstance(self.sweep_timeout_s, (int, float))
            or not math.isfinite(float(self.sweep_timeout_s))
            or self.sweep_timeout_s <= 0
        ):
            raise ValueError("sweep_timeout_s must be None or a positive finite number")


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _CompletedSweep:
    """A completed sweep plus the host-side boundary captured at its point 0."""

    sweep: AssembledSweep
    start_utc: datetime
    start_mono: MonotonicNs


class LibreVnaUsbBackend(AcquisitionBackend):
    """S11 production backend over ``LibreVnaUsbTransport`` (no threads).

    Composes the frozen frame layer (``transport.PacketStream``) with the
    ISSUE-020 datapoint parser and ``StrictSweepAssembler``; the backend
    routes every packet itself so NACK is fail-closed and control traffic is
    observable.  The base class owns the lifecycle state machine, the
    cancellation signal and ``connection_generation``.
    """

    def __init__(
        self,
        transport: LibreVnaUsbTransport,
        *,
        mission_id: MissionId,
        device_id: DeviceId,
        clock: Clock | None = None,
        mono_clock: Callable[[], float] | None = None,
        settings: LibreVnaUsbSettings | None = None,
    ) -> None:
        super().__init__()
        if not isinstance(transport, LibreVnaUsbTransport):
            raise TypeError(
                f"transport must be a LibreVnaUsbTransport, got {type(transport).__name__}"
            )
        if not isinstance(mission_id, MissionId):
            raise TypeError(
                f"mission_id must be a MissionId, got {type(mission_id).__name__}"
            )
        if not isinstance(device_id, DeviceId):
            raise TypeError(
                f"device_id must be a DeviceId, got {type(device_id).__name__}"
            )
        if clock is not None and not isinstance(clock, Clock):
            raise TypeError(
                f"clock must implement the Clock protocol, got {type(clock).__name__}"
            )
        if mono_clock is not None and not callable(mono_clock):
            raise TypeError("mono_clock must be callable")
        if settings is not None and not isinstance(settings, LibreVnaUsbSettings):
            raise TypeError(
                f"settings must be a LibreVnaUsbSettings, got {type(settings).__name__}"
            )
        self._transport = transport
        self._mission_id = mission_id
        self._device_id = device_id
        self._clock = clock if clock is not None else SystemClock()
        self._mono_clock = mono_clock if mono_clock is not None else time.monotonic
        self._settings = settings if settings is not None else LibreVnaUsbSettings()
        self._frame_stream = PacketStream()
        self._device_info: LibreVnaDeviceInfo | None = None
        self._assembler: StrictSweepAssembler | None = None
        self._applied: AppliedConfig | None = None
        self._pending_sweeps: list[_CompletedSweep] = []
        self._sweep_deadline: float | None = None
        self._sweep_start_utc: datetime | None = None
        self._sweep_start_mono: MonotonicNs | None = None
        self._trace_index = 0
        self._attempt = 0
        self._prev_start_mono: MonotonicNs | None = None
        self._unexpected_acks = 0
        self._ignored_packets = 0
        self._sweep_timeout_s = self._settings.sweep_timeout_min_s
        # ISSUE-023: set by ``reconnect_session`` so the first sweep after a
        # physical reconnect must pass the requested/applied axis gate again
        # (trace_index alone is no longer a fresh-session marker).
        self._require_axis_verify = False

    # ---- observable state -------------------------------------------------

    @property
    def device_info(self) -> LibreVnaDeviceInfo | None:
        """The decoded DeviceInfo of the current session (None after close)."""
        return self._device_info

    @property
    def session_stats(self) -> dict[str, int]:
        """Observable session counters (traces, drops, control traffic)."""
        assembler = self._assembler
        if assembler is None:
            dropped = incomplete = timeouts = duplicate = out_of_range = invalid = 0
        else:
            stats = assembler.stats
            dropped = stats.dropped_sweeps
            incomplete = stats.incomplete_sweeps
            timeouts = stats.timeouts
            duplicate = stats.duplicate_points
            out_of_range = stats.out_of_range_points
            invalid = stats.invalid_points
        return {
            "traces": self._trace_index,
            "dropped_sweeps": dropped,
            "incomplete_sweeps": incomplete,
            "timeouts": timeouts,
            "duplicate_points": duplicate,
            "out_of_range_points": out_of_range,
            "invalid_points": invalid,
            "unexpected_acks": self._unexpected_acks,
            "ignored_packets": self._ignored_packets,
        }

    @property
    def cancel_requested(self) -> bool:
        """Whether the controller requested cancellation (close/emergency).

        ISSUE-023: the reconnect loop checks this between attempts so a
        ``close()``/``emergency_stop()`` during reconnection aborts promptly
        instead of sleeping through the remaining backoff schedule.
        """
        return self._cancel_event.is_set()

    def wait_cancellable(self, seconds: float) -> None:
        """Block up to ``seconds``; abort early when cancellation is requested.

        ISSUE-023 repair round 2 (P2-1): the reconnect backoff wait uses
        this so a ``close()``/``emergency_stop()`` during a retry pause is
        honoured on the cancel-event wake-up instead of sleeping through the
        whole delay.  Raises ``BackendCancelledError``/``BackendClosedError``
        through the base interruption path when the cancel event is set.
        """
        if (
            isinstance(seconds, bool)
            or not isinstance(seconds, (int, float))
            or not math.isfinite(float(seconds))
            or seconds <= 0.0
        ):
            raise ValueError("seconds must be a finite positive number")
        if self._cancel_event.wait(seconds):
            self._raise_interrupted(self._attempt)

    def _stats_dict(self) -> dict[str, JsonValue]:
        """Session statistics as JSON-safe error context."""
        stats: dict[str, JsonValue] = {}
        for key, value in self.session_stats.items():
            stats[key] = value
        return stats

    # ---- lifecycle hooks --------------------------------------------------

    def _do_open(self) -> Capabilities:
        self._transport.open()
        try:
            info = self._wait_for_device_info(self._settings.device_info_timeout_s)
            self._device_info = info
            self._send_command(SET_IDLE, timeout_s=self._settings.command_timeout_s)
        except Exception:
            try:
                self._transport.close()
            except Exception:  # best-effort release on open failure
                pass
            raise
        self._frame_stream.reset()
        self._assembler = None
        self._applied = None
        self._pending_sweeps = []
        self._sweep_deadline = None
        self._sweep_start_utc = None
        self._sweep_start_mono = None
        self._trace_index = 0
        self._attempt = 0
        self._prev_start_mono = None
        self._unexpected_acks = 0
        self._ignored_packets = 0
        self._sweep_timeout_s = self._settings.sweep_timeout_min_s
        self._require_axis_verify = False
        return Capabilities(
            device_id=self._device_id,
            channels=(S11_CHANNEL, S22_CHANNEL),
            fault_injection=False,
            gnss=False,
        )

    def _do_configure(self, config: MissionConfig) -> AppliedConfig:
        info = self._device_info
        if info is None:
            raise BackendStateError(
                "device info missing: open before configure",
                operation="configure",
                state=self.state.value,
            )
        try:
            self._validate_config(config, info)
            applied_config = self._quantize_config(config)
            self._verify_contract_tolerance(config, applied_config)
            diff = ConfigDiff.compute(config, applied_config)
            self._send_command(SET_IDLE, timeout_s=self._settings.command_timeout_s)
            self._frame_stream.reset()
            self._assembler = None
            self._pending_sweeps = []
            self._sweep_deadline = None
            self._sweep_start_utc = None
            self._sweep_start_mono = None
            self._trace_index = 0
            self._attempt = 0
            self._prev_start_mono = None
            self._unexpected_acks = 0
            self._ignored_packets = 0
            self._require_axis_verify = False
            settings = self._build_sweep_settings(config)
            self._sweep_timeout_s = self._compute_sweep_timeout(config)
            # The receiver plan follows the frozen channel set: S11 only
            # uses the ISSUE-020 single-stage plan, the dual S11/S22 set uses
            # the ISSUE-022 stage-0 + stage-1 plan (same sweep, one
            # datapoint stream).
            plan = (
                S11_RECEIVER_PLAN
                if len(config.channels) == 1
                else S11S22_RECEIVER_PLAN
            )
            self._assembler = StrictSweepAssembler(
                config.frequency_points,
                receiver_plan=plan,
                # check_timeout compares clock units, not SI: our injected
                # mono clock returns seconds (like time.monotonic), so the
                # "timeout_ms" argument carries the same seconds value.
                timeout_ms=self._sweep_timeout_s,
                clock=self._mono_clock,
            )
            self._send_command(
                SWEEP_SETTINGS,
                payload=settings.encode(),
                timeout_s=self._settings.command_timeout_s,
            )
            applied = AppliedConfig(config=applied_config, diff=diff)
            self._applied = applied
            return applied
        except Exception:
            # Fail-closed: a failed command leaves the device state
            # unverifiable; clear local acquisition state (the base class
            # keeps the backend OPEN so a re-configure can recover).
            self._enter_fail_closed()
            raise

    def _do_acquire(self, timeout_s: float | None) -> FrequencySweep:
        applied = self._applied
        assembler = self._assembler
        if applied is None or assembler is None:
            raise BackendStateError(
                "backend is not configured",
                operation="acquire",
                state=self.state.value,
            )
        attempt = self._attempt
        self._attempt = attempt + 1
        started = self._mono_clock()
        never_started_deadline = started + self._sweep_timeout_s
        while True:
            self._raise_if_interrupted(attempt)
            if self._pending_sweeps:
                # A previous read may have completed more sweeps than were
                # returned (fast device, tiny sweeps): return them in order.
                return self._finalize_sweep(self._pending_sweeps.pop(0), attempt)
            if timeout_s is not None and self._mono_clock() - started >= timeout_s:
                raise BackendTimeoutError(
                    "acquire caller timeout expired",
                    attempt=attempt,
                    timeout_s=timeout_s,
                )
            try:
                data = self._transport.read(READ_SIZE, self._settings.usb_read_timeout_ms)
            except LibreVnaTimeoutError:
                data = b""
            except LibreVnaCancelledError:
                self._raise_interrupted(attempt)
            except (LibreVnaDisconnectedError, LibreVnaNotOpenError) as exc:
                self._bump_generation()
                raise BackendDisconnectedError(
                    "USB device disconnected during acquire",
                    attempt=attempt,
                    generation=self.connection_generation,
                ) from exc
            if data:
                for packet in self._frame_stream.feed(data):
                    completed = self._route_packet(packet)
                    if completed is not None:
                        self._pending_sweeps.append(completed)
                        if len(self._pending_sweeps) > _MAX_PENDING_SWEEPS:
                            raise LibreVnaProtocolError(
                                "completed sweeps piled up faster than consumed"
                            )
                if self._pending_sweeps:
                    return self._finalize_sweep(self._pending_sweeps.pop(0), attempt)
            try:
                assembler.check_timeout()
            except LibreVnaSweepTimeoutError as exc:
                raise BackendTimeoutError(
                    "sweep did not complete within the configured timeout",
                    attempt=attempt,
                    sweep_timeout_s=self._sweep_timeout_s,
                    stats=self._stats_dict(),
                ) from exc
            now = self._mono_clock()
            if self._sweep_deadline is not None and now >= self._sweep_deadline:
                raise BackendTimeoutError(
                    "sweep did not complete within the configured timeout",
                    attempt=attempt,
                    sweep_timeout_s=self._sweep_timeout_s,
                    stats=self._stats_dict(),
                )
            if self._sweep_deadline is None and now >= never_started_deadline:
                raise BackendTimeoutError(
                    "no sweep data arrived within the configured timeout",
                    attempt=attempt,
                    sweep_timeout_s=self._sweep_timeout_s,
                    stats=self._stats_dict(),
                )

    def _do_close(self) -> None:
        if self._transport.is_open:
            try:
                # Best-effort, fire-and-forget: close must stay fast and
                # idempotent; device idle confirmation is not close's job.
                self._transport.write(encode_packet(SET_IDLE))
            except Exception:  # device may already be gone; release anyway
                pass
        self._transport.close()
        self._device_info = None
        self._applied = None
        self._assembler = None
        self._require_axis_verify = False

    def reconnect_session(self, config: MissionConfig) -> AppliedConfig:
        """Re-establish the USB session in place after a disconnect (ISSUE-023).

        Contract (controller ``_handle_disconnect``, plan D1): the base
        lifecycle state stays ``CONFIGURED``, ``connection_generation``
        increments once per successful reconnect, and the trace counters are
        preserved -- no trace index/UID is ever repeated.  The frozen config
        is re-validated against a freshly read ``DEVICE_INFO`` and re-applied
        (requested/applied re-verified; unconfirmed config is never reused,
        docs/ACQUISITION.md section 4), and the first sweep after the
        reconnect must pass the requested/applied axis gate again
        (``_require_axis_verify``, plan D2).

        On failure the transport is released and local acquisition state is
        cleared fail-closed while the trace counters stay untouched (plan
        D5); the caller (``LibreVnaReconnector``) retries with backoff.
        """
        if not isinstance(config, MissionConfig):
            raise TypeError(
                f"config must be a MissionConfig, got {type(config).__name__}"
            )
        with self._lock:
            if self._state is not BackendState.CONFIGURED:
                raise BackendStateError(
                    "reconnect requires a configured backend",
                    operation="reconnect",
                    state=self._state.value,
                    allowed_states=[BackendState.CONFIGURED.value],
                )
        self._require_axis_verify = False
        try:
            # Fresh USB session: release the old (possibly broken) handle,
            # then reopen and re-read the device identity.
            try:
                self._transport.close()
            except Exception:  # best-effort: the session may already be gone
                pass
            self._transport.open()
            info = self._wait_for_device_info(self._settings.device_info_timeout_s)
            previous = self._device_info
            if previous is not None:
                # A reconnect must re-confirm the device identity, never
                # silently continue with a different unit (plan D10).
                self._verify_device_identity(previous, info)
            self._device_info = info
            self._send_command(SET_IDLE, timeout_s=self._settings.command_timeout_s)
            # Re-apply the frozen config against the fresh device info:
            # validate, quantize, verify requested/applied tolerance, rebuild
            # the strict assembler and re-send SWEEP_SETTINGS.  The device
            # state after a reconnect is unverifiable until the config is
            # confirmed again, so nothing is reused from before.
            self._validate_config(config, info)
            applied_config = self._quantize_config(config)
            self._verify_contract_tolerance(config, applied_config)
            diff = ConfigDiff.compute(config, applied_config)
            self._frame_stream.reset()
            self._assembler = None
            self._pending_sweeps = []
            self._sweep_deadline = None
            self._sweep_start_utc = None
            self._sweep_start_mono = None
            self._attempt = 0
            self._unexpected_acks = 0
            self._ignored_packets = 0
            settings = self._build_sweep_settings(config)
            self._sweep_timeout_s = self._compute_sweep_timeout(config)
            # The receiver plan follows the frozen channel set (same rule as
            # ``_do_configure``): S11 only vs same-sweep S11/S22 dual.
            plan = (
                S11_RECEIVER_PLAN
                if len(config.channels) == 1
                else S11S22_RECEIVER_PLAN
            )
            self._assembler = StrictSweepAssembler(
                config.frequency_points,
                receiver_plan=plan,
                # check_timeout compares clock units, not SI (see _do_configure).
                timeout_ms=self._sweep_timeout_s,
                clock=self._mono_clock,
            )
            self._send_command(
                SWEEP_SETTINGS,
                payload=settings.encode(),
                timeout_s=self._settings.command_timeout_s,
            )
            applied = AppliedConfig(config=applied_config, diff=diff)
            self._applied = applied
            # A successful reconnect is a new connection epoch: generation
            # strictly increments (P3-03 semantics, plan D1).  Trace counters
            # (_trace_index/_prev_start_mono) are intentionally preserved so
            # the next trace continues without duplication.
            self._bump_generation()
            self._require_axis_verify = True
            return applied
        except Exception:
            # Fail-closed: clear local acquisition state and release the
            # transport; trace counters stay untouched so a later retry can
            # succeed without repeating trace indices (plan D5).
            self._applied = None
            self._assembler = None
            self._frame_stream.reset()
            self._pending_sweeps = []
            self._sweep_deadline = None
            self._sweep_start_utc = None
            self._sweep_start_mono = None
            self._attempt = 0
            self._unexpected_acks = 0
            self._ignored_packets = 0
            try:
                self._transport.close()
            except Exception:  # best-effort release on reconnect failure
                pass
            raise

    # ---- command/response phase ------------------------------------------

    def _wait_for_device_info(self, timeout_s: float) -> LibreVnaDeviceInfo:
        self._transport.write(encode_packet(REQUEST_DEVICE_INFO))
        deadline = self._mono_clock() + timeout_s
        while self._mono_clock() < deadline:
            self._raise_if_interrupted(self._attempt)
            try:
                data = self._transport.read(READ_SIZE, self._settings.usb_read_timeout_ms)
            except LibreVnaTimeoutError:
                continue
            info: LibreVnaDeviceInfo | None = None
            for packet in self._frame_stream.feed(data):
                if packet.packet_type == DEVICE_INFO:
                    info = decode_device_info(packet.payload)
                elif packet.packet_type == NACK:
                    raise LibreVnaNackError(
                        "device returned NACK to REQUEST_DEVICE_INFO"
                    )
                else:
                    self._route_packet(packet)
            if info is not None:
                return info
        raise BackendTimeoutError(
            "timed out waiting for DEVICE_INFO",
            phase="open",
            timeout_s=timeout_s,
        )

    def _send_command(
        self, packet_type: int, *, payload: bytes = b"", timeout_s: float
    ) -> None:
        self._transport.write(encode_packet(packet_type, payload))
        self._wait_for_ack(packet_type, timeout_s=timeout_s)

    def _wait_for_ack(self, packet_type: int, *, timeout_s: float) -> None:
        deadline = self._mono_clock() + timeout_s
        while self._mono_clock() < deadline:
            self._raise_if_interrupted(self._attempt)
            try:
                data = self._transport.read(READ_SIZE, self._settings.usb_read_timeout_ms)
            except LibreVnaTimeoutError:
                continue
            acked = False
            for packet in self._frame_stream.feed(data):
                if packet.packet_type == ACK:
                    acked = True
                elif packet.packet_type == NACK:
                    raise LibreVnaNackError(
                        f"device returned NACK for command {packet_type}"
                    )
                else:
                    # Datapoints arriving with the ACK feed the inline
                    # assembler directly (never silently dropped).
                    self._route_packet(packet)
            if acked:
                return
        raise BackendTimeoutError(
            "timed out waiting for ACK",
            phase="command",
            command=packet_type,
            timeout_s=timeout_s,
        )

    # ---- packet routing ---------------------------------------------------

    def _route_packet(self, packet: Packet) -> _CompletedSweep | None:
        """Route one decoded frame; return a completed sweep, if any.

        - ``VNA_DATAPOINT``: parse (fail-closed on malformed payload), record
          the host-side sweep boundary on a plan-valid point 0, feed the
          strict assembler;
        - ``NACK``: fail-closed (ISSUE-020 review residual risk 2);
        - ``ACK``: counted as unexpected (it belongs to a command phase);
        - anything else: counted as ignored.
        """
        if packet.packet_type == VNA_DATAPOINT:
            assembler = self._assembler
            if assembler is None:
                # Datapoints before any configure are a protocol violation.
                self._ignored_packets += 1
                return None
            try:
                dp = parse_vna_datapoint(packet.payload)
            except LibreVnaDatapointError as exc:
                raise LibreVnaProtocolError(
                    "malformed VNADatapoint payload (fail-closed)",
                    payload_length=len(packet.payload),
                ) from exc
            starts_sweep = (
                dp.point_number == 0
                and datapoint_matches_plan(dp, assembler.receiver_plan)
            )
            if starts_sweep:
                self._sweep_start_utc = self._clock.utc_now()
                self._sweep_start_mono = self._clock.monotonic_ns()
            sweep = assembler.feed_datapoint(dp)
            if sweep is not None:
                start_utc = self._sweep_start_utc
                start_mono = self._sweep_start_mono
                if start_utc is None or start_mono is None:
                    raise LibreVnaProtocolError(
                        "internal error: completed sweep lacks a start boundary"
                    )
                self._sweep_deadline = None
                return _CompletedSweep(sweep=sweep, start_utc=start_utc, start_mono=start_mono)
            if starts_sweep:
                # Re-anchor the overall sweep deadline at each new point 0
                # (covers dropped-then-silent devices; no-sweep devices are
                # covered by the acquire-start deadline).
                self._sweep_deadline = self._mono_clock() + self._sweep_timeout_s
            return None
        if packet.packet_type == NACK:
            raise LibreVnaNackError("device returned NACK")
        if packet.packet_type == ACK:
            self._unexpected_acks += 1
            return None
        self._ignored_packets += 1
        return None

    # ---- sweep finalization -----------------------------------------------

    def _finalize_sweep(self, completed: _CompletedSweep, attempt: int) -> FrequencySweep:
        applied = self._applied
        if applied is None:
            raise LibreVnaProtocolError(
                "internal error: finalize without applied config"
            )
        config = applied.config
        if self._trace_index == 0 or self._require_axis_verify:
            # ISSUE-023: the axis gate runs on the first sweep of a session
            # AND on the first sweep after a physical reconnect (the device
            # may have changed while unplugged; unconfirmed config is never
            # trusted).  A rejection keeps the flag set so the next sweep
            # re-checks and no trace is emitted until the axis is confirmed.
            self._verify_first_axis(completed.sweep)
            self._require_axis_verify = False
        freqs = np.asarray(
            [dp.frequency_hz for dp in completed.sweep.points], dtype=np.float64
        )
        # ISSUE-022: one row per frozen channel, in config order.  The row
        # semantics come from each ChannelSpec.s_parameter (the default
        # HH:S11 / VV:S22 binding is configuration, not a hardcoded array
        # layout); a channel without a production-verified S-parameter slot
        # fails closed before any trace is emitted.
        rows: list[list[complex]] = []
        for channel in config.channels:
            try:
                stage, port_mask = _S_PARAMETER_SLOTS[channel.s_parameter]
            except KeyError:
                raise LibreVnaProtocolError(
                    "unsupported S-parameter in frozen channel configuration",
                    channel_id=channel.channel_id,
                    s_parameter=channel.s_parameter.value,
                ) from None
            rows.append(
                [
                    self._compute_s_parameter(dp, stage=stage, port_mask=port_mask)
                    for dp in completed.sweep.points
                ]
            )
        data = np.asarray(rows, dtype=np.complex128).reshape(
            (len(config.channels), freqs.size)
        )
        index = self._trace_index
        uid = TraceUid(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"uav-gpr:librevna:{self._mission_id.to_json()}:{index}",
            )
        )
        raw_hash = RawHashSpec(
            mission_id=self._mission_id,
            trace_index=index,
            trace_uid=uid,
            channels=config.channels,
            frequencies_hz=freqs,
            data=data,
        ).compute()
        finished_utc = self._clock.utc_now()
        finished_mono = self._clock.monotonic_ns()
        start_utc = completed.start_utc
        start_mono = completed.start_mono
        midpoint_utc = start_utc + (finished_utc - start_utc) / 2
        midpoint_mono = MonotonicNs(
            start_mono.ns + (finished_mono.ns - start_mono.ns) // 2
        )
        previous = self._prev_start_mono
        if previous is None:
            actual_interval: float | None = None
            schedule_error: float | None = None
        else:
            actual_interval = (start_mono.ns - previous.ns) / 1_000_000_000.0
            schedule_error = actual_interval - config.target_interval_s
        self._prev_start_mono = start_mono
        with self._lock:
            generation = self._generation
        metadata = TraceMetadata(
            mission_id=self._mission_id,
            trace_index=index,
            trace_uid=uid,
            device_id=self._device_id,
            sweep_started_utc=start_utc,
            sweep_midpoint_utc=midpoint_utc,
            sweep_finished_utc=finished_utc,
            sweep_started_monotonic_ns=start_mono,
            sweep_midpoint_monotonic_ns=midpoint_mono,
            sweep_finished_monotonic_ns=finished_mono,
            target_interval_s=config.target_interval_s,
            actual_interval_s=actual_interval,
            schedule_error_s=schedule_error,
            connection_generation=generation,
            raw_trace_sha256=raw_hash,
            gnss_match=None,
            quality_status=TraceQualityStatus.DEGRADED,
            quality_reasons=(TraceQualityReason.GNSS_MISSING,),
        )
        self._trace_index = index + 1
        return FrequencySweep(
            channels=config.channels,
            frequencies_hz=freqs,
            data=data,
            metadata=metadata,
        )

    # ---- validation -------------------------------------------------------

    def _validate_config(self, config: MissionConfig, info: LibreVnaDeviceInfo) -> None:
        channels = tuple(config.channels)
        supported_sets = (
            (S11_CHANNEL,),  # ISSUE-021: S11 only, stages bitmap 0x1240
            (S11_CHANNEL, S22_CHANNEL),  # ISSUE-022: same-sweep S11/S22, 0x1241
            (S22_CHANNEL, S11_CHANNEL),  # any frozen order: rows follow config
        )
        if channels not in supported_sets:
            raise BackendConfigRejectedError(
                "unsupported channel configuration: this backend supports "
                "S11 only ((hh_s11,)) or same-sweep S11/S22 dual reflection "
                "((hh_s11, vv_s22) in any order); S22-only, S21/S12 and other "
                "combinations are not production-verified",
                channels=[str(c.channel_id) for c in channels],
            )
        if (
            config.frequency_start_hz < info.min_freq_hz
            or config.frequency_stop_hz > info.max_freq_hz
        ):
            raise BackendConfigRejectedError(
                "frequency range outside the device range",
                requested_start_hz=config.frequency_start_hz,
                requested_stop_hz=config.frequency_stop_hz,
                device_min_hz=info.min_freq_hz,
                device_max_hz=info.max_freq_hz,
            )
        if config.if_bw_hz < info.min_ifbw_hz or config.if_bw_hz > info.max_ifbw_hz:
            raise BackendConfigRejectedError(
                "if_bw_hz outside the device range",
                requested_if_bw_hz=config.if_bw_hz,
                device_min_hz=info.min_ifbw_hz,
                device_max_hz=info.max_ifbw_hz,
            )
        if config.frequency_points > info.max_points:
            raise BackendConfigRejectedError(
                "frequency_points exceed the device maximum",
                requested_points=config.frequency_points,
                device_max_points=info.max_points,
            )
        if config.power_dbm < info.min_dbm or config.power_dbm > info.max_dbm:
            raise BackendConfigRejectedError(
                "power_dbm outside the device range",
                requested_power_dbm=config.power_dbm,
                device_min_dbm=info.min_dbm,
                device_max_dbm=info.max_dbm,
            )

    def _quantize_config(self, config: MissionConfig) -> MissionConfig:
        """Device-effective config: int Hz / IFBW, power quantized to 0.01 dBm."""
        q_start = int(config.frequency_start_hz)
        q_stop = int(config.frequency_stop_hz)
        q_ifbw = int(config.if_bw_hz)
        q_power = round(config.power_dbm * 100.0) / 100.0
        if q_stop <= q_start:
            raise BackendConfigRejectedError(
                "int-quantized frequency range collapses (stop <= start)",
                frequency_start_hz=config.frequency_start_hz,
                frequency_stop_hz=config.frequency_stop_hz,
            )
        if q_ifbw <= 0:
            raise BackendConfigRejectedError(
                "int-quantized if_bw_hz collapses to zero",
                if_bw_hz=config.if_bw_hz,
            )
        return replace(
            config,
            frequency_start_hz=float(q_start),
            frequency_stop_hz=float(q_stop),
            if_bw_hz=float(q_ifbw),
            power_dbm=q_power,
        )

    def _verify_contract_tolerance(
        self, requested: MissionConfig, applied: MissionConfig
    ) -> None:
        """Reject requested/applied deviations beyond the device quantization."""
        checks = (
            (
                "frequency_start_hz",
                abs(requested.frequency_start_hz - applied.frequency_start_hz),
                _FREQUENCY_TOLERANCE_HZ,
            ),
            (
                "frequency_stop_hz",
                abs(requested.frequency_stop_hz - applied.frequency_stop_hz),
                _FREQUENCY_TOLERANCE_HZ,
            ),
            ("if_bw_hz", abs(requested.if_bw_hz - applied.if_bw_hz), _FREQUENCY_TOLERANCE_HZ),
            (
                "power_dbm",
                abs(requested.power_dbm - applied.power_dbm),
                _POWER_TOLERANCE_DBM,
            ),
        )
        for field, deviation, tolerance in checks:
            if deviation > tolerance:
                raise BackendConfigRejectedError(
                    "requested/applied configuration deviates beyond tolerance",
                    field=field,
                    deviation=deviation,
                    tolerance=tolerance,
                )
        if requested.frequency_points != applied.frequency_points:
            raise BackendConfigRejectedError(
                "requested/applied frequency_points differ",
                requested_points=requested.frequency_points,
                applied_points=applied.frequency_points,
            )
        if tuple(requested.channels) != tuple(applied.channels):
            raise BackendConfigRejectedError(
                "requested/applied channels differ",
                requested_channels=[str(c.channel_id) for c in requested.channels],
                applied_channels=[str(c.channel_id) for c in applied.channels],
            )

    def _build_sweep_settings(self, config: MissionConfig) -> SweepSettings:
        # The stages bitmap follows the frozen channel set: a single S11
        # channel measures stage 0 only (0x1240); the dual S11/S22 set
        # measures both reflections in one sweep (0x1241, ISSUE-022).  The
        # binding is enforced here and in ``_validate_config``; the codec
        # itself accepts both production-verified values.
        stages_bitmap = (
            S11_STAGES_BITMAP if len(config.channels) == 1 else S11_S22_STAGES_BITMAP
        )
        return SweepSettings(
            start_hz=int(config.frequency_start_hz),
            stop_hz=int(config.frequency_stop_hz),
            points=config.frequency_points,
            ifbw_hz=int(config.if_bw_hz),
            power_dbm=round(config.power_dbm * 100.0) / 100.0,
            dwell_us=self._settings.dwell_us,
            stages_bitmap=stages_bitmap,
        )

    def _compute_sweep_timeout(self, config: MissionConfig) -> float:
        if self._settings.sweep_timeout_s is not None:
            return self._settings.sweep_timeout_s
        # Per-point measurement time ~ 1/IFBW per stage; the dual S11/S22
        # sweep (ISSUE-022) measures two stages, so its budget doubles.
        stages = 2 if len(config.channels) >= 2 else 1
        expected_s = (
            config.frequency_points * stages / max(float(config.if_bw_hz), 1.0)
        )
        return max(
            self._settings.sweep_timeout_min_s,
            expected_s * self._settings.sweep_timeout_factor,
        )

    def _verify_first_axis(self, sweep: AssembledSweep) -> None:
        """First-sweep axis gate: actual device axis vs applied config.

        docs/ACQUISITION.md section 4: the frequency axis is authoritative
        from the device; an out-of-tolerance axis rejects the task before the
        first trace (fail-closed, no trace allocated).
        """
        applied = self._applied
        if applied is None:
            raise LibreVnaProtocolError(
                "internal error: axis gate without applied config"
            )
        expected = applied.config.frequency_axis_hz
        actual = np.asarray(
            [dp.frequency_hz for dp in sweep.points], dtype=np.float64
        )
        if actual.shape != expected.shape:
            raise BackendConfigRejectedError(
                "device frequency axis length differs from the applied config",
                expected_points=int(expected.size),
                actual_points=int(actual.size),
                axis_tolerance_hz=AXIS_TOLERANCE_HZ,
            )
        deviation = np.abs(actual - expected)
        if float(np.max(deviation)) > AXIS_TOLERANCE_HZ:
            raise BackendConfigRejectedError(
                "device frequency axis deviates from the applied config "
                "beyond tolerance before the first trace",
                max_deviation_hz=float(np.max(deviation)),
                axis_tolerance_hz=AXIS_TOLERANCE_HZ,
            )

    @staticmethod
    def _verify_device_identity(
        previous: LibreVnaDeviceInfo, fresh: LibreVnaDeviceInfo
    ) -> None:
        """Fail-closed when the reconnected device identity changed.

        ISSUE-023 repair round 2 (P2-1): a reconnect must re-confirm the
        device, never silently continue with a different unit.  Protocol v14
        ``DEVICE_INFO`` carries no USB serial number, so the in-band identity
        fields (protocol, firmware, hardware version/revision, port count)
        are compared; a serial-to-``device_id`` binding is deferred to the
        real-device phase (plan D10).
        """
        if (
            previous.protocol != fresh.protocol
            or previous.firmware != fresh.firmware
            or previous.hardware_version != fresh.hardware_version
            or previous.hardware_revision != fresh.hardware_revision
            or previous.num_ports != fresh.num_ports
        ):
            raise LibreVnaProtocolError(
                "reconnected device identity differs from the session device",
                previous_firmware=previous.firmware,
                fresh_firmware=fresh.firmware,
            )

    @staticmethod
    def _compute_s_parameter(
        dp: VNADatapoint, *, stage: int, port_mask: int
    ) -> complex:
        """S-parameter = stage-``stage`` Port receiver / stage Reference.

        S11 uses stage 0 / Port1, S22 uses stage 1 / Port2 (ISSUE-022); the
        caller selects the slot from the frozen ``ChannelSpec.s_parameter``.
        The strict assembler already validated the receiver plan (exactly one
        reference with non-zero magnitude, exactly one port slot, finite
        values), so the division is safe; the check is defensive fail-closed.
        """
        reference: complex | None = None
        port: complex | None = None
        for desc, value in dp.receivers:
            if (desc >> DESC_STAGE_SHIFT) != stage:
                continue
            if desc & DESC_MASK_REFERENCE:
                reference = value
            elif desc & port_mask:
                port = value
        if reference is None or port is None or abs(reference) == 0.0:
            raise LibreVnaProtocolError(
                "internal error: plan-valid datapoint lacks S-parameter slots",
                stage=stage,
                port_mask=port_mask,
            )
        return port / reference

    # ---- shared helpers ---------------------------------------------------

    def _raise_if_interrupted(self, attempt: int) -> None:
        if self._cancel_event.is_set():
            self._raise_interrupted(attempt)

    def _bump_generation(self) -> None:
        with self._lock:
            self._generation += 1

    def _enter_fail_closed(self) -> None:
        """Clear local acquisition state after an unverifiable device state."""
        self._applied = None
        self._assembler = None
        self._frame_stream.reset()
        self._pending_sweeps = []
        self._sweep_deadline = None
        self._sweep_start_utc = None
        self._sweep_start_mono = None
        self._trace_index = 0
        self._attempt = 0
        self._prev_start_mono = None
        self._unexpected_acks = 0
        self._ignored_packets = 0
        self._sweep_timeout_s = self._settings.sweep_timeout_min_s
        self._require_axis_verify = False
