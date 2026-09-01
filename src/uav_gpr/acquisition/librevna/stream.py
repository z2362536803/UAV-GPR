"""LibreVNA datapoint stream and strict sweep assembler (ISSUE-020).

Incremental packet stream (:class:`LibreVnaPacketStream`) and strict sweep
assembler (:class:`StrictSweepAssembler`) built on top of the ISSUE-019
frame layer (``transport.PacketStream``, read-only consumption).

Scope (``docs/issues/M04_LIBREVNA.md`` ISSUE-020):

- parse ``VNA_DATAPOINT`` (type 27) payloads into :class:`VNADatapoint`
  (header ``<QhH`` = frequency u64 / cdbm i16 / point_number u16, 12 bytes,
  then per-receiver groups of real(4,f32)+imag(4,f32)+desc(1) = 9 bytes,
  BLOCKED layout: all reals, then all imags, then all descs);
- feed raw USB bytes in arbitrary chunk boundaries and get the same
  datapoint sequence (frame validation, length caps 8..4096, noise
  discard and bad-CRC drop come from the frame layer; the stream adds a
  bounded datapoint parse and observable drop statistics);
- strictly assemble sweeps by point sequence: range, duplicates, missing
  points, out-of-order, cross-sweep, reference denominator and non-finite
  values -- an assembled sweep is only ever produced when complete and
  consistent (never zero-filled, never partial);
- timeout produces observable statistics plus a structured error
  (:class:`LibreVnaSweepTimeoutError`).

Behaviors audited from the reference project (``librevna_usb.py``
``ContinuousSweepAssembler`` and ``librevna_protocol.py``; provenance and
hashes in ``docs/plans/2026-09-02-issue-020-librevna-stream.md`` section 4):

- only ``point_number == 0`` starts a new sweep; points must arrive in
  strict sequence ``0..n_points-1``; duplicate/backward/forward-jump/
  out-of-range/invalid datapoints invalidate the active sweep and enter a
  desync state that only a new point 0 can leave; incomplete sweeps are
  never returned (never stitch two sweeps);
- a desync segment of non-zero points is counted once (per whole sweep),
  not per point; ``incomplete_sweeps`` is a subset of ``dropped_sweeps``
  (sweeps that saw point 0 but never completed); ``timeouts`` is a subset
  of ``incomplete_sweeps``;
- receiver descriptor bitmask (Device_protocol_v13.tex): bits 7-5 stage,
  bit 4 reference, bits 3-0 Port4..Port1; the reference bit wins over the
  port bits (desc 0x11 is a reference receiver); a required slot must
  appear exactly once (duplicates are rejected, never first/last
  silently), reference magnitude must be non-zero, required values must be
  finite; unrelated receivers (other stages/ports) are ignored;
- on completion the frequency axis must be strictly increasing (u64 ints
  are always finite), otherwise :class:`LibreVnaSweepError`;
- ``VNA_DATAPOINT`` skips CRC validation at the frame layer (existing
  reference protocol behavior -- not "fixed" here); structural validation
  of the payload is the sweep-integrity backstop.

Structured errors follow the ``transport.py`` pattern: ``DomainError`` +
``ErrorCode.INVALID_ARGUMENT`` + a class-level ``reason`` discriminator
(the core ``ErrorCode`` enum is read-only).
"""

from __future__ import annotations

import itertools
import math
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass

from uav_gpr.acquisition.librevna.transport import (
    VNA_DATAPOINT,
    LibreVnaTransportError,
    PacketStream,
)

# ---- VNADatapoint payload layout (reference protocol) ----

#: Header: frequency(8,u64) + cdbm(2,i16) + point_number(2,u16).
_VNA_DATAPOINT_HEADER = "<QhH"
_VNA_DATAPOINT_HEADER_SIZE = 12
#: Per-receiver group: real(4,f32) + imag(4,f32) + desc(1).
_VNA_DATAPOINT_GROUP_SIZE = 9

# ---- Receiver descriptor bitmask (Device_protocol_v13.tex) ----

DESC_STAGE_SHIFT = 5
DESC_MASK_REFERENCE = 0x10
DESC_MASK_PORT1 = 0x01
DESC_MASK_PORT2 = 0x02
DESC_MASK_PORT3 = 0x04
DESC_MASK_PORT4 = 0x08


# ---------------------------------------------------------------------------
# Structured errors (transport.py pattern; core ErrorCode is read-only)
# ---------------------------------------------------------------------------


class LibreVnaStreamError(LibreVnaTransportError):
    """Structured LibreVNA stream/assembly failure (payload/sweep layer)."""

    _reason = "librevna_stream_error"


class LibreVnaDatapointError(LibreVnaStreamError):
    """VNADatapoint payload is structurally invalid (length/truncation)."""

    _reason = "malformed_datapoint"


class LibreVnaSweepError(LibreVnaStreamError):
    """Completed sweep is inconsistent (e.g. non-monotonic frequency axis)."""

    _reason = "sweep_integrity"


class LibreVnaSweepTimeoutError(LibreVnaStreamError):
    """An active sweep did not complete within ``timeout_ms``."""

    _reason = "sweep_timeout"


# ---------------------------------------------------------------------------
# VNADatapoint parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VNADatapoint:
    """One parsed VNADatapoint (frame payload semantics, no S-parameter math).

    ``receivers`` keeps the payload order as ``(desc, complex)`` pairs;
    desc bits: bits7-5 stage, bit4 reference, bits3-0 Port4..Port1.
    """

    point_number: int
    frequency_hz: int
    cdbm: int
    receivers: tuple[tuple[int, complex], ...]


def parse_vna_datapoint(payload: bytes) -> VNADatapoint:
    """Parse one VNADatapoint payload into a :class:`VNADatapoint`.

    Raises :class:`LibreVnaDatapointError` for structurally invalid
    payloads (too short, or length not exactly ``12 + 9*k``).  An empty
    receiver list (12-byte header only) parses fine; the receiver plan
    validation rejects it later.
    """
    if len(payload) < _VNA_DATAPOINT_HEADER_SIZE:
        raise LibreVnaDatapointError("VNADatapoint payload too short")
    count = (len(payload) - _VNA_DATAPOINT_HEADER_SIZE) // _VNA_DATAPOINT_GROUP_SIZE
    if len(payload) != _VNA_DATAPOINT_HEADER_SIZE + count * _VNA_DATAPOINT_GROUP_SIZE:
        raise LibreVnaDatapointError(
            "VNADatapoint payload length is not 12 + 9*k"
        )
    frequency_hz, cdbm, point_number = struct.unpack_from(
        _VNA_DATAPOINT_HEADER, payload, 0
    )
    reals = struct.unpack_from("<" + "f" * count, payload, _VNA_DATAPOINT_HEADER_SIZE)
    imag_offset = _VNA_DATAPOINT_HEADER_SIZE + 4 * count
    imags = struct.unpack_from("<" + "f" * count, payload, imag_offset)
    desc_offset = imag_offset + 4 * count
    descs = payload[desc_offset : desc_offset + count]
    receivers = tuple(
        (descs[i], complex(reals[i], imags[i])) for i in range(count)
    )
    return VNADatapoint(
        point_number=point_number,
        frequency_hz=frequency_hz,
        cdbm=cdbm,
        receivers=receivers,
    )


# ---------------------------------------------------------------------------
# Incremental packet stream (frame layer reused, read-only)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PacketStreamStats:
    """Observable stream-level counters."""

    malformed_datapoints: int = 0
    ignored_packets: int = 0


class LibreVnaPacketStream:
    """Incremental VNADatapoint stream over arbitrary USB byte chunks.

    Composes the ISSUE-019 frame layer (:class:`PacketStream`): sticky
    de-framing, noise discard, out-of-range-length realignment and bad-CRC
    drop are inherited.  This layer adds:

    - ``VNA_DATAPOINT`` payload parsing with strict structure validation;
      a malformed payload is counted (``malformed_datapoints``) and the
      stream stays synchronized (a garbage-aligned fake frame can never
      produce a datapoint);
    - non-datapoint packets are counted as ``ignored_packets`` (control
      traffic is routed by the ISSUE-021 backend);
    - bounded cache: the frame buffer can never exceed one partial frame
      (``MAX_PACKET_LENGTH`` + a few header bytes), so a malicious length
      field or garbage flood cannot allocate unbounded memory.
    """

    def __init__(self, frame_stream: PacketStream | None = None) -> None:
        self._frames = frame_stream if frame_stream is not None else PacketStream()
        self._malformed_datapoints = 0
        self._ignored_packets = 0

    @property
    def frame_stream(self) -> PacketStream:
        """The underlying frame layer (exposed for ISSUE-021 control routing)."""
        return self._frames

    @property
    def stats(self) -> PacketStreamStats:
        return PacketStreamStats(
            malformed_datapoints=self._malformed_datapoints,
            ignored_packets=self._ignored_packets,
        )

    def feed(self, data: bytes) -> list[VNADatapoint]:
        """Append raw bytes; return every complete VNADatapoint decoded."""
        datapoints: list[VNADatapoint] = []
        for packet in self._frames.feed(data):
            if packet.packet_type != VNA_DATAPOINT:
                self._ignored_packets += 1
                continue
            try:
                datapoints.append(parse_vna_datapoint(packet.payload))
            except LibreVnaDatapointError:
                self._malformed_datapoints += 1
        return datapoints

    def reset(self) -> None:
        """Clear the frame buffer (session boundary); statistics survive."""
        self._frames.reset()

    def reset_stats(self) -> None:
        self._malformed_datapoints = 0
        self._ignored_packets = 0


# ---------------------------------------------------------------------------
# Receiver plan (channel/receiver field validation)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReceiverSlot:
    """One required receiver slot: (stage, descriptor bitmask).

    ``mask`` may carry the reference bit (0x10) for the reference receiver
    of a stage, or a port bit (0x01..0x08) for a port receiver.  The
    reference bit wins over port bits when matching (desc 0x11 is a
    reference receiver, mirroring the reference ``datapoint_to_s11``).
    """

    stage: int
    mask: int

    def __post_init__(self) -> None:
        if isinstance(self.stage, bool) or not isinstance(self.stage, int):
            raise ValueError("ReceiverSlot.stage must be a non-negative int")
        if self.stage < 0:
            raise ValueError("ReceiverSlot.stage must be a non-negative int")
        if isinstance(self.mask, bool) or not isinstance(self.mask, int):
            raise ValueError("ReceiverSlot.mask must be an int in 0..0xFF")
        if not 0 <= self.mask <= 0xFF:
            raise ValueError("ReceiverSlot.mask must be an int in 0..0xFF")


#: Default ISSUE-020 receiver plan: the S11 input set (stage 0 reference
#: and stage 0 Port 1).  ISSUE-022 extends this with stage-1 slots for S22.
S11_RECEIVER_PLAN: tuple[ReceiverSlot, ...] = (
    ReceiverSlot(0, DESC_MASK_REFERENCE),
    ReceiverSlot(0, DESC_MASK_PORT1),
)


def _slot_matches(desc: int, slot: ReceiverSlot) -> bool:
    if (desc >> DESC_STAGE_SHIFT) != slot.stage:
        return False
    if slot.mask & DESC_MASK_REFERENCE:
        return bool(desc & DESC_MASK_REFERENCE)
    return bool(desc & slot.mask) and not bool(desc & DESC_MASK_REFERENCE)


def datapoint_matches_plan(dp: VNADatapoint, plan: tuple[ReceiverSlot, ...]) -> bool:
    """Whether a datapoint satisfies every required receiver slot.

    A slot is satisfied when exactly one receiver matches it, its value is
    finite, and a reference slot's magnitude is non-zero (a zero reference
    denominator is invalid).  Duplicate matches, missing slots and
    non-finite values are rejected; unrelated receivers (other stages or
    ports) are ignored.
    """
    for slot in plan:
        matched = [
            value for desc, value in dp.receivers if _slot_matches(desc, slot)
        ]
        if len(matched) != 1:
            return False
        value = matched[0]
        if not (math.isfinite(value.real) and math.isfinite(value.imag)):
            return False
        if slot.mask & DESC_MASK_REFERENCE and abs(value) == 0.0:
            return False
    return True


# ---------------------------------------------------------------------------
# Strict sweep assembler
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SweepAssemblerStats:
    """Observable sweep-level counters (whole-sweep / event semantics).

    Subset invariant: ``timeouts`` <= ``incomplete_sweeps`` <=
    ``dropped_sweeps``.
    """

    dropped_sweeps: int = 0
    incomplete_sweeps: int = 0
    timeouts: int = 0
    duplicate_points: int = 0
    out_of_range_points: int = 0
    invalid_points: int = 0


@dataclass(frozen=True)
class AssembledSweep:
    """A complete, consistent intermediate sweep (no S-parameter math).

    ``points`` are ordered by point_number (0..n-1); ``started_at`` is the
    injected clock value at the sweep's point 0 (ISSUE-021 maps it to the
    real sweep boundary time).
    """

    points: tuple[VNADatapoint, ...]
    started_at: float


class StrictSweepAssembler:
    """Strictly assemble VNADatapoints into complete, ordered sweeps.

    State machine (reference ``ContinuousSweepAssembler`` semantics):

    1. only ``point_number == 0`` can start a new sweep;
    2. points must arrive in strict sequence ``0,1,...,n_points-1`` --
       duplicate/backward points, forward jumps, out-of-range points and
       plan-invalid datapoints immediately invalidate the active sweep and
       enter a desync state ("wait for the next point 0");
    3. a desync segment of non-zero points is counted once as a dropped
       sweep (never per point); incomplete sweeps are never returned and
       never stitched with later data;
    4. a new point 0 interrupting an incomplete sweep drops the old sweep
       once; only a complete, consistent sweep produces an
       :class:`AssembledSweep` (returned inline from :meth:`feed_datapoint`);
    5. on completion the frequency axis must be strictly increasing
       (u64 ints are always finite) else :class:`LibreVnaSweepError`;
    6. :meth:`check_timeout` drops an active sweep that exceeds
       ``timeout_ms`` (statistics updated, ``timeouts`` included) and
       raises :class:`LibreVnaSweepTimeoutError`.

    The half-sweep buffer is bounded by the frozen ``expected_points``
    (each point is one small frozen dataclass): a malicious stream cannot
    grow it, and nothing is ever zero-filled or partially emitted.
    """

    def __init__(
        self,
        expected_points: int,
        *,
        receiver_plan: tuple[ReceiverSlot, ...] = S11_RECEIVER_PLAN,
        timeout_ms: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if isinstance(expected_points, bool) or not isinstance(expected_points, int):
            raise ValueError("expected_points must be an int >= 2")
        if expected_points < 2:
            raise ValueError("expected_points must be an int >= 2")
        if not isinstance(receiver_plan, tuple) or not receiver_plan:
            raise ValueError("receiver_plan must be a non-empty tuple of ReceiverSlot")
        for slot in receiver_plan:
            if not isinstance(slot, ReceiverSlot):
                raise TypeError("receiver_plan entries must be ReceiverSlot")
        if timeout_ms is not None:
            if (
                isinstance(timeout_ms, bool)
                or not isinstance(timeout_ms, (int, float))
                or not math.isfinite(float(timeout_ms))
                or timeout_ms <= 0
            ):
                raise ValueError("timeout_ms must be None or a positive finite number")
        if not callable(clock):
            raise TypeError("clock must be callable")

        self._expected_points = expected_points
        self._receiver_plan = receiver_plan
        self._timeout_ms = timeout_ms
        self._clock = clock
        self.reset()
        self.reset_stats()

    # ---- query ----

    @property
    def expected_points(self) -> int:
        return self._expected_points

    @property
    def receiver_plan(self) -> tuple[ReceiverSlot, ...]:
        return self._receiver_plan

    @property
    def stats(self) -> SweepAssemblerStats:
        return SweepAssemblerStats(
            dropped_sweeps=self._dropped_sweeps,
            incomplete_sweeps=self._incomplete_sweeps,
            timeouts=self._timeouts,
            duplicate_points=self._duplicate_points,
            out_of_range_points=self._out_of_range_points,
            invalid_points=self._invalid_points,
        )

    # ---- reset ----

    def reset(self) -> None:
        """Reset the assembly state (statistics survive)."""
        self._active = False
        self._next_expected = 0
        self._points: list[VNADatapoint] = []
        self._started_at: float | None = None
        self._unsynced_dropped = False

    def reset_stats(self) -> None:
        self._dropped_sweeps = 0
        self._incomplete_sweeps = 0
        self._timeouts = 0
        self._duplicate_points = 0
        self._out_of_range_points = 0
        self._invalid_points = 0

    # ---- feed ----

    def feed_datapoint(self, dp: VNADatapoint) -> AssembledSweep | None:
        """Feed one parsed datapoint; return the completed sweep, if any.

        Data-driven problems (invalid datapoint, duplicates, jumps,
        out-of-range, desync) are drops with observable statistics -- this
        method never raises for them.  A completed-but-inconsistent sweep
        (non-monotonic frequency) raises :class:`LibreVnaSweepError`.
        """
        if not isinstance(dp, VNADatapoint):
            raise TypeError("feed_datapoint requires a VNADatapoint")
        if not datapoint_matches_plan(dp, self._receiver_plan):
            self._invalid_points += 1
            if self._active:
                self._drop_active()
            return None
        if dp.point_number == 0:
            self._start_new_sweep(dp)
            return None
        if not self._active:
            # Desync: only a point 0 can start; one drop per segment.
            if not self._unsynced_dropped:
                self._dropped_sweeps += 1
                self._unsynced_dropped = True
            return None
        if dp.point_number < 0 or dp.point_number >= self._expected_points:
            self._out_of_range_points += 1
            self._drop_active()
            return None
        if dp.point_number == self._next_expected:
            self._points.append(dp)
            self._next_expected += 1
            if self._next_expected == self._expected_points:
                return self._complete()
            return None
        if dp.point_number < self._next_expected:
            self._duplicate_points += 1
        # Forward jump (point_number > next_expected) has no dedicated
        # counter; the whole sweep is dropped either way.
        self._drop_active()
        return None

    def check_timeout(self) -> None:
        """Drop an active sweep that exceeded ``timeout_ms``.

        Updates statistics (``timeouts``, ``incomplete_sweeps``,
        ``dropped_sweeps``) and raises :class:`LibreVnaSweepTimeoutError`.
        No-op when timeout is disabled, no sweep is active, or the deadline
        has not elapsed.
        """
        if (
            not self._active
            or self._timeout_ms is None
            or self._started_at is None
        ):
            return
        if self._clock() - self._started_at >= self._timeout_ms:
            self._timeouts += 1
            self._drop_active()
            raise LibreVnaSweepTimeoutError(
                "LibreVNA sweep did not complete within the timeout"
            )

    # ---- internals ----

    def _start_new_sweep(self, dp: VNADatapoint) -> None:
        if self._active:
            # A new point 0 interrupts an incomplete sweep: drop it once.
            self._dropped_sweeps += 1
            self._incomplete_sweeps += 1
        self._active = True
        self._next_expected = 1
        self._points = [dp]
        self._started_at = self._clock()
        self._unsynced_dropped = False

    def _drop_active(self) -> None:
        self._dropped_sweeps += 1
        self._incomplete_sweeps += 1
        self._active = False
        self._next_expected = 0
        self._points = []
        self._started_at = None
        # Trailing non-zero points belong to the same desync segment.
        self._unsynced_dropped = True

    def _complete(self) -> AssembledSweep:
        ordered = tuple(self._points)
        started_at = self._started_at
        freqs = [dp.frequency_hz for dp in ordered]
        for lower, upper in itertools.pairwise(freqs):
            if upper <= lower:
                self._active = False
                self._points = []
                self._started_at = None
                raise LibreVnaSweepError(
                    f"LibreVNA sweep frequency axis is not strictly increasing "
                    f"({lower} Hz -> {upper} Hz)"
                )
        assert started_at is not None
        self._active = False
        self._next_expected = 0
        self._points = []
        self._started_at = None
        self._unsynced_dropped = False
        return AssembledSweep(points=ordered, started_at=started_at)
