"""Contract tests for the ISSUE-020 LibreVNA datapoint stream and strict
sweep assembler.

Covers: VNADatapoint payload parsing (golden reference vectors), the
incremental :class:`LibreVnaPacketStream` (any byte chunk boundary, bounded
cache, noise/corruption resync), and the :class:`StrictSweepAssembler`
(strict sweep/point/channel validation: range, duplicates, missing points,
out-of-order, cross-sweep, reference denominator, non-finite values --
assembled sweeps are only ever produced when complete and consistent).

Golden vectors and the state-machine semantics are taken from the audited
reference project (``tests/test_librevna_protocol.py`` and
``tests/test_librevna_usb_backend.py`` ``ContinuousSweepAssemblerTests``);
provenance and hashes are recorded in
``docs/plans/2026-09-02-issue-020-librevna-stream.md`` section 4.

No real USB is ever touched: everything runs on synthetic byte streams.
"""

from __future__ import annotations

import random
import struct

import pytest

from uav_gpr.acquisition.librevna.stream import (
    DESC_MASK_PORT1,
    DESC_MASK_PORT2,
    DESC_MASK_REFERENCE,
    S11_RECEIVER_PLAN,
    AssembledSweep,
    LibreVnaDatapointError,
    LibreVnaPacketStream,
    LibreVnaStreamError,
    LibreVnaSweepError,
    LibreVnaSweepTimeoutError,
    PacketStreamStats,
    ReceiverSlot,
    StrictSweepAssembler,
    SweepAssemblerStats,
    VNADatapoint,
    datapoint_matches_plan,
    parse_vna_datapoint,
)
from uav_gpr.acquisition.librevna.transport import (
    MAX_PACKET_LENGTH,
    SWEEP_SETTINGS,
    VNA_DATAPOINT,
    encode_packet,
)

# ---- Golden vectors (reference tests/test_librevna_protocol.py L50-56) ----

# freq=500MHz, cdbm=-1000, point=0, reals=[1.0(ref), -0.5(port1)],
# imags=[0.0(ref), 0.25(port1)], descs=[0x10(ref), 0x01(port1)]
VNA_DATAPOINT_PAYLOAD_HEX = "0065cd1d0000000018fc00000000803f000000bf000000000000803e1001"
VNA_DATAPOINT_PACKET_HEX = (
    "5a26001b0065cd1d0000000018fc00000000803f000000bf000000000000803e1001fecc9f61"
)

ACK_PACKET_BYTES = bytes.fromhex("5a080007c1f48315")


# ---------------------------------------------------------------------------
# Helpers (synthetic byte streams and datapoints; reference _vna_payload layout)
# ---------------------------------------------------------------------------


def _vna_payload(
    *,
    freq_hz: int = 500_000_000,
    point_number: int = 0,
    ref: complex = 1.0 + 0j,
    port1: complex = -0.5 + 0.25j,
) -> bytes:
    """A stage-0 S11 datapoint payload (BLOCKED layout: reals, imags, descs)."""
    payload = struct.pack("<QhH", freq_hz, -1000, point_number)
    payload += struct.pack("<ff", ref.real, port1.real)
    payload += struct.pack("<ff", ref.imag, port1.imag)
    payload += bytes([DESC_MASK_REFERENCE, DESC_MASK_PORT1])
    return payload


def _vna_packet(
    *,
    freq_hz: int = 500_000_000,
    point_number: int = 0,
    ref: complex = 1.0 + 0j,
    port1: complex = -0.5 + 0.25j,
) -> bytes:
    return encode_packet(VNA_DATAPOINT, _vna_payload(
        freq_hz=freq_hz, point_number=point_number, ref=ref, port1=port1
    ))


def _payload_with_receivers(
    *,
    freq_hz: int,
    point_number: int,
    receivers: list[tuple[int, complex]],
) -> bytes:
    """A datapoint payload with an arbitrary receiver (desc, value) list."""
    payload = struct.pack("<QhH", freq_hz, -1000, point_number)
    payload += struct.pack("<" + "f" * len(receivers), *(v.real for _, v in receivers))
    payload += struct.pack("<" + "f" * len(receivers), *(v.imag for _, v in receivers))
    payload += bytes(desc for desc, _ in receivers)
    return payload


def _s11_dp(
    point_number: int,
    freq_hz: int,
    ref: complex = 1.0 + 0j,
    port1: complex = 0.5 - 0.2j,
) -> VNADatapoint:
    return VNADatapoint(
        point_number=point_number,
        frequency_hz=freq_hz,
        cdbm=-1000,
        receivers=((DESC_MASK_REFERENCE, ref), (DESC_MASK_PORT1, port1)),
    )


def _sweep_bytes(n_points: int, base_freq: int = 1_000_000_000) -> bytes:
    return b"".join(
        _vna_packet(freq_hz=base_freq + i, point_number=i) for i in range(n_points)
    )


def _chunk(data: bytes, rng: random.Random, max_chunk: int) -> list[bytes]:
    """Split ``data`` into random chunks of 1..max_chunk bytes (exhaustive)."""
    chunks: list[bytes] = []
    i = 0
    while i < len(data):
        size = rng.randint(1, min(max_chunk, len(data) - i))
        chunks.append(data[i : i + size])
        i += size
    return chunks


class _Clock:
    """Deterministic injected clock (no time.sleep anywhere in these tests)."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, delta: float) -> None:
        self.now += delta


def _make_assembler(
    expected_points: int = 5,
    *,
    timeout_ms: float | None = None,
    clock: _Clock | None = None,
) -> StrictSweepAssembler:
    return StrictSweepAssembler(
        expected_points, timeout_ms=timeout_ms, clock=clock if clock is not None else _Clock()
    )


# ---------------------------------------------------------------------------
# VNADatapoint payload parsing
# ---------------------------------------------------------------------------


class TestParseVnaDatapoint:
    def test_parse_golden_payload_vector(self) -> None:
        dp = parse_vna_datapoint(bytes.fromhex(VNA_DATAPOINT_PAYLOAD_HEX))
        assert dp.point_number == 0
        assert dp.frequency_hz == 500_000_000
        assert dp.cdbm == -1000
        assert dp.receivers == (
            (DESC_MASK_REFERENCE, 1.0 + 0.0j),
            (DESC_MASK_PORT1, -0.5 + 0.25j),
        )

    def test_parse_rejects_short_payload(self) -> None:
        with pytest.raises(LibreVnaDatapointError):
            parse_vna_datapoint(b"\x00" * 11)

    def test_parse_rejects_truncated_group(self) -> None:
        # 12-byte header + 10 bytes: not 12 + 9*k
        with pytest.raises(LibreVnaDatapointError):
            parse_vna_datapoint(b"\x00" * 22)

    def test_parse_rejects_trailing_bytes(self) -> None:
        valid = bytes.fromhex(VNA_DATAPOINT_PAYLOAD_HEX)
        with pytest.raises(LibreVnaDatapointError):
            parse_vna_datapoint(valid + b"\x00")

    def test_parse_empty_receivers_allowed(self) -> None:
        # A 12-byte header with no receiver groups parses (reference behavior);
        # the receiver plan validation rejects it later.
        dp = parse_vna_datapoint(struct.pack("<QhH", 500_000_000, -1000, 3))
        assert dp.point_number == 3
        assert dp.receivers == ()


# ---------------------------------------------------------------------------
# LibreVnaPacketStream: framing, bounded cache, noise/corruption resync
# ---------------------------------------------------------------------------


class TestLibreVnaPacketStream:
    def test_stream_golden_packet_vector(self) -> None:
        stream = LibreVnaPacketStream()
        dps = stream.feed(bytes.fromhex(VNA_DATAPOINT_PACKET_HEX))
        assert len(dps) == 1
        assert dps[0].point_number == 0
        assert dps[0].frequency_hz == 500_000_000
        assert dps[0].receivers == (
            (DESC_MASK_REFERENCE, 1.0 + 0.0j),
            (DESC_MASK_PORT1, -0.5 + 0.25j),
        )
        assert stream.stats == PacketStreamStats()

    def test_stream_corrupted_datapoint_crc_still_parses(self) -> None:
        # Reference protocol behavior: VNA_DATAPOINT (type 27) skips CRC
        # validation; the frame layer still delivers the packet. Not "fixed".
        bad = bytearray(bytes.fromhex(VNA_DATAPOINT_PACKET_HEX))
        bad[-1] ^= 0xFF
        stream = LibreVnaPacketStream()
        dps = stream.feed(bytes(bad))
        assert len(dps) == 1
        assert dps[0].point_number == 0

    def test_noise_prefix_ignored(self) -> None:
        stream = LibreVnaPacketStream()
        dps = stream.feed(b"\xde\xad\xbe\xef" + bytes.fromhex(VNA_DATAPOINT_PACKET_HEX))
        assert len(dps) == 1
        assert stream.stats == PacketStreamStats()

    def test_non_datapoint_packet_counted_ignored(self) -> None:
        stream = LibreVnaPacketStream()
        dps = stream.feed(ACK_PACKET_BYTES)
        assert dps == []
        assert stream.stats.ignored_packets == 1
        assert stream.stats.malformed_datapoints == 0

    def test_bad_crc_non_datapoint_dropped(self) -> None:
        bad = bytearray(ACK_PACKET_BYTES)
        bad[-1] ^= 0xFF
        stream = LibreVnaPacketStream()
        dps = stream.feed(bytes(bad) + bytes.fromhex(VNA_DATAPOINT_PACKET_HEX))
        assert len(dps) == 1
        assert stream.stats.ignored_packets == 0  # CRC-bad packet fully dropped

    def test_malformed_datapoint_payload_counted(self) -> None:
        stream = LibreVnaPacketStream()
        dps = stream.feed(encode_packet(VNA_DATAPOINT, b"\x00\x01"))
        assert dps == []
        assert stream.stats.malformed_datapoints == 1
        # stream stays synchronized: the next valid packet still parses
        dps = stream.feed(bytes.fromhex(VNA_DATAPOINT_PACKET_HEX))
        assert len(dps) == 1
        assert stream.stats.malformed_datapoints == 1

    def test_invalid_length_realigns(self) -> None:
        # length=7 (< MIN_PACKET_LENGTH): header byte dropped, stream realigns
        stream = LibreVnaPacketStream()
        dps = stream.feed(b"\x5a\x07\x00" + bytes.fromhex(VNA_DATAPOINT_PACKET_HEX))
        assert len(dps) == 1

    def test_split_across_reads(self) -> None:
        data = bytes.fromhex(VNA_DATAPOINT_PACKET_HEX)
        stream = LibreVnaPacketStream()
        assert stream.feed(data[:5]) == []
        dps = stream.feed(data[5:])
        assert len(dps) == 1

    def test_reset_clears_buffer_keeps_stats(self) -> None:
        data = bytes.fromhex(VNA_DATAPOINT_PACKET_HEX)
        stream = LibreVnaPacketStream()
        stream.feed(encode_packet(VNA_DATAPOINT, b"\x00\x01"))  # malformed
        assert stream.feed(data[:5]) == []
        stream.reset()
        assert stream.feed(data[5:]) == []  # half packet was dropped by reset
        assert stream.stats.malformed_datapoints == 1  # stats survive reset

    def test_malicious_length_field_bounded_buffer(self) -> None:
        stream = LibreVnaPacketStream()
        # length=0xFFFF would claim 65535 bytes; the frame layer drops the
        # header byte and realigns -- the buffer can never grow unbounded.
        for _ in range(50):
            stream.feed(b"\x5a\xff\xff\x01" + b"\x00" * 100)
        assert len(stream.frame_stream.buffer) <= MAX_PACKET_LENGTH + 8
        dps = stream.feed(bytes.fromhex(VNA_DATAPOINT_PACKET_HEX))
        assert len(dps) == 1

    def test_garbage_flood_buffer_bounded(self) -> None:
        rng = random.Random(20260902)
        garbage = bytes(rng.randrange(256) for _ in range(1_000_000))
        stream = LibreVnaPacketStream()
        for chunk in _chunk(garbage, rng, 512):
            stream.feed(chunk)
        assert len(stream.frame_stream.buffer) <= MAX_PACKET_LENGTH + 8
        dps = stream.feed(bytes.fromhex(VNA_DATAPOINT_PACKET_HEX))
        assert len(dps) == 1

    def test_max_length_packet_accepted_at_frame_cap(self) -> None:
        # A 4096-byte frame (max inclusive bound) is accepted by the frame
        # layer; a 4097-byte frame is dropped and the stream realigns.
        payload = b"\x00" * (MAX_PACKET_LENGTH - 8)
        stream = LibreVnaPacketStream()
        stream.feed(encode_packet(SWEEP_SETTINGS, payload))
        assert stream.stats.ignored_packets == 1
        stream.feed(encode_packet(SWEEP_SETTINGS, payload + b"\x00"))
        assert stream.stats.ignored_packets == 1  # oversized frame dropped
        dps = stream.feed(bytes.fromhex(VNA_DATAPOINT_PACKET_HEX))
        assert len(dps) == 1


# ---------------------------------------------------------------------------
# Generative chunking: any byte chunk boundary yields the same sequence
# ---------------------------------------------------------------------------


class TestGenerativeChunking:
    def _mixed_stream(self) -> bytes:
        return b"".join(
            [
                b"\xde\xad\xbe\xef",  # noise prefix
                _vna_packet(freq_hz=500_000_000, point_number=0),
                _vna_packet(freq_hz=500_000_001, point_number=1),
                ACK_PACKET_BYTES,  # ignored control packet
                encode_packet(VNA_DATAPOINT, b"\x00\x01"),  # malformed payload
                _vna_packet(freq_hz=500_000_002, point_number=2),
            ]
        )

    def test_generative_chunking_same_datapoint_sequence(self) -> None:
        data = self._mixed_stream()
        # Reference: single-shot feed
        ref_stream = LibreVnaPacketStream()
        ref_dps = ref_stream.feed(data)
        ref_stats = ref_stream.stats
        assert len(ref_dps) == 3
        assert ref_stats.malformed_datapoints == 1
        assert ref_stats.ignored_packets == 1
        # Generative: 20 seeds x several max chunk sizes
        for seed in range(20):
            for max_chunk in (1, 2, 7, 64):
                rng = random.Random(seed * 1000 + max_chunk)
                stream = LibreVnaPacketStream()
                dps: list[VNADatapoint] = []
                for chunk in _chunk(data, rng, max_chunk):
                    dps.extend(stream.feed(chunk))
                assert dps == ref_dps
                assert stream.stats == ref_stats

    def test_generative_chunking_one_byte_granularity(self) -> None:
        data = _sweep_bytes(5)
        rng = random.Random(42)
        stream = LibreVnaPacketStream()
        dps: list[VNADatapoint] = []
        for chunk in _chunk(data, rng, 1):
            assert len(chunk) == 1
            dps.extend(stream.feed(chunk))
        assert [dp.point_number for dp in dps] == [0, 1, 2, 3, 4]

    def test_generative_chunking_same_assembled_sweep(self) -> None:
        data = _sweep_bytes(5)
        clock = _Clock()
        ref_stream = LibreVnaPacketStream()
        ref_assembler = _make_assembler(5, clock=clock)
        clock.now = 10.0
        ref_sweep = None
        for dp in ref_stream.feed(data):
            result = ref_assembler.feed_datapoint(dp)
            if result is not None:
                ref_sweep = result
        assert ref_sweep is not None
        assert ref_sweep.started_at == 10.0
        for seed in range(15):
            rng = random.Random(seed)
            stream = LibreVnaPacketStream()
            assembler = _make_assembler(5, clock=clock)
            clock.now = 10.0
            sweeps: list[AssembledSweep] = []
            for chunk in _chunk(data, rng, 11):
                for dp in stream.feed(chunk):
                    result = assembler.feed_datapoint(dp)
                    if result is not None:
                        sweeps.append(result)
            assert sweeps == [ref_sweep]

    def test_generative_chunking_across_sweep_boundary(self) -> None:
        data = _sweep_bytes(5) + _sweep_bytes(5, base_freq=2_000_000_000)
        clock = _Clock()
        for seed in range(10):
            rng = random.Random(seed + 7)
            stream = LibreVnaPacketStream()
            assembler = _make_assembler(5, clock=clock)
            clock.now = 0.0
            sweeps: list[AssembledSweep] = []
            for chunk in _chunk(data, rng, 9):
                for dp in stream.feed(chunk):
                    result = assembler.feed_datapoint(dp)
                    if result is not None:
                        sweeps.append(result)
            assert len(sweeps) == 2
            assert sweeps[0].points[0].frequency_hz == 1_000_000_000
            assert sweeps[1].points[0].frequency_hz == 2_000_000_000
            assert sweeps[0].points[4].frequency_hz == 1_000_000_004


# ---------------------------------------------------------------------------
# StrictSweepAssembler: constructor validation
# ---------------------------------------------------------------------------


class TestConstructorValidation:
    def test_constructor_rejects_invalid_expected_points(self) -> None:
        with pytest.raises(ValueError):
            StrictSweepAssembler(1)
        with pytest.raises(ValueError):
            StrictSweepAssembler(0)
        with pytest.raises(ValueError):
            StrictSweepAssembler(True)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            StrictSweepAssembler(5.0)  # type: ignore[arg-type]

    def test_constructor_rejects_invalid_receiver_plan(self) -> None:
        with pytest.raises(ValueError):
            StrictSweepAssembler(5, receiver_plan=())
        with pytest.raises(TypeError):
            StrictSweepAssembler(5, receiver_plan=(ReceiverSlot(0, 0x10), "port1"))  # type: ignore[arg-type]

    def test_constructor_rejects_invalid_timeout(self) -> None:
        for bad in (0, -1, float("nan"), float("inf"), True):
            with pytest.raises(ValueError):
                StrictSweepAssembler(5, timeout_ms=bad)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# StrictSweepAssembler: state machine (reference ContinuousSweepAssembler
# semantics, ported to VNADatapoint level)
# ---------------------------------------------------------------------------


class TestStrictSweepAssembler:
    def test_complete_sweep_from_point_zero(self) -> None:
        assembler = _make_assembler(5)
        sweep = None
        for i in range(5):
            result = assembler.feed_datapoint(_s11_dp(i, 1_000_000_000 + i))
            if result is not None:
                sweep = result
        assert sweep is not None
        assert [dp.point_number for dp in sweep.points] == [0, 1, 2, 3, 4]
        assert assembler.stats.dropped_sweeps == 0
        assert sweep.started_at == 0.0  # clock returned 0.0 at point 0

    def test_only_syncs_from_point_zero(self) -> None:
        assembler = _make_assembler(5)
        assert assembler.feed_datapoint(_s11_dp(2, 1_000_000_000)) is None
        assert assembler.stats.dropped_sweeps == 1
        for i in range(5):
            assembler.feed_datapoint(_s11_dp(i, 1_000_000_000 + i))
        assert assembler.stats.dropped_sweeps == 1

    def test_new_point_zero_interrupts_drops_current(self) -> None:
        assembler = _make_assembler(5)
        for i in range(3):
            assembler.feed_datapoint(_s11_dp(i, 1_000_000_000 + i, port1=0.5 - 0.2j))
        assembler.feed_datapoint(_s11_dp(0, 2_000_000_000, port1=0.3 - 0.1j))
        assert assembler.stats.dropped_sweeps == 1
        assert assembler.stats.incomplete_sweeps == 1
        for i in range(1, 5):
            result = assembler.feed_datapoint(_s11_dp(i, 2_000_000_000 + i, port1=0.3 - 0.1j))
        assert result is not None
        assert result.points[0].frequency_hz == 2_000_000_000

    def test_never_stitches_two_sweeps(self) -> None:
        assembler = _make_assembler(5)
        for i in range(3):  # sweep A: points 0..2
            assembler.feed_datapoint(_s11_dp(i, 1_000_000_000 + i, port1=0.5 + 0j))
        for i in range(2):  # sweep B: points 0..1 (point 0 interrupts A)
            assembler.feed_datapoint(_s11_dp(i, 2_000_000_000 + i, port1=0.9 + 0j))
        assert assembler.stats.dropped_sweeps == 1
        sweep = None
        for i in range(2, 5):
            result = assembler.feed_datapoint(_s11_dp(i, 2_000_000_000 + i, port1=0.9 + 0j))
            if result is not None:
                sweep = result
        assert sweep is not None
        assert [dp.point_number for dp in sweep.points] == [0, 1, 2, 3, 4]
        assert sweep.points[0].frequency_hz == 2_000_000_000  # only sweep B

    def test_duplicate_point_drops_current(self) -> None:
        # No sweep_id in the protocol: a repeated old point is conservatively
        # treated as a duplicate -> current sweep invalidated.
        assembler = _make_assembler(5)
        assembler.feed_datapoint(_s11_dp(0, 1_000_000_000))
        assembler.feed_datapoint(_s11_dp(1, 1_000_000_001))
        assert assembler.feed_datapoint(_s11_dp(1, 999_000_000, port1=0.1 + 0j)) is None
        assert assembler.stats.duplicate_points == 1
        assert assembler.stats.dropped_sweeps == 1
        assert assembler.stats.incomplete_sweeps == 1
        for i in range(5):
            result = assembler.feed_datapoint(_s11_dp(i, 1_000_000_000 + i))
        assert result is not None

    def test_stitching_partial_a_then_b_without_zero_rejected(self) -> None:
        # Regression: A:0,1,2 + B missing 0 -> 1,2,3,4 must never stitch.
        assembler = _make_assembler(5)
        for i in range(3):
            assembler.feed_datapoint(_s11_dp(i, 1_000_000_000 + i * 100_000_000, port1=0.5 + 0j))
        assembler.feed_datapoint(_s11_dp(1, 2_100_000_000, port1=0.9 + 0j))  # B point 1
        assembler.feed_datapoint(_s11_dp(2, 2_200_000_000, port1=0.9 + 0j))
        assembler.feed_datapoint(_s11_dp(3, 2_300_000_000, port1=0.9 + 0j))
        assembler.feed_datapoint(_s11_dp(4, 2_400_000_000, port1=0.9 + 0j))
        assert assembler.stats.dropped_sweeps == 1
        assert assembler.stats.duplicate_points >= 1
        sweep = None
        for i in range(5):  # complete sweep C
            result = assembler.feed_datapoint(
                _s11_dp(i, 3_000_000_000 + i * 100_000_000, port1=0.7 + 0j)
            )
            if result is not None:
                sweep = result
        assert sweep is not None
        assert sweep.points[0].frequency_hz == 3_000_000_000

    def test_initial_unsynced_nonzero_counts_one_drop(self) -> None:
        assembler = _make_assembler(5)
        for i in (2, 3, 4):
            assembler.feed_datapoint(_s11_dp(i, 1_000_000_000 + i))
        assert assembler.stats.dropped_sweeps == 1
        assert assembler.stats.incomplete_sweeps == 0  # never saw point 0
        for i in range(5):
            assembler.feed_datapoint(_s11_dp(i, 1_000_000_000 + i))
        assert assembler.stats.dropped_sweeps == 1  # still one

    def test_forward_jump_drops_current(self) -> None:
        assembler = _make_assembler(5)
        assembler.feed_datapoint(_s11_dp(0, 1_000_000_000))
        assembler.feed_datapoint(_s11_dp(1, 1_000_000_001))
        assert assembler.feed_datapoint(_s11_dp(3, 1_000_000_003)) is None
        assert assembler.stats.dropped_sweeps == 1
        assert assembler.stats.incomplete_sweeps == 1

    def test_backward_point_drops_current(self) -> None:
        assembler = _make_assembler(5)
        assembler.feed_datapoint(_s11_dp(0, 1_000_000_000))
        assembler.feed_datapoint(_s11_dp(1, 1_000_000_001))
        assembler.feed_datapoint(_s11_dp(2, 1_000_000_002))
        assert assembler.feed_datapoint(_s11_dp(1, 1_050_000_000)) is None
        assert assembler.stats.duplicate_points == 1
        assert assembler.stats.dropped_sweeps == 1

    def test_two_complete_consecutive_sweeps(self) -> None:
        assembler = _make_assembler(5)
        sweeps: list[AssembledSweep] = []
        for i in range(5):
            result = assembler.feed_datapoint(_s11_dp(i, 1_000_000_000 + i, port1=0.5 + 0j))
            if result is not None:
                sweeps.append(result)
        for i in range(5):
            result = assembler.feed_datapoint(_s11_dp(i, 2_000_000_000 + i, port1=0.9 + 0j))
            if result is not None:
                sweeps.append(result)
        assert len(sweeps) == 2
        assert sweeps[0].points[0].frequency_hz == 1_000_000_000
        assert sweeps[1].points[0].frequency_hz == 2_000_000_000
        assert assembler.stats.dropped_sweeps == 0

    def test_out_of_range_point_drops_current(self) -> None:
        assembler = _make_assembler(5)
        assembler.feed_datapoint(_s11_dp(0, 1_000_000_000))
        assert assembler.feed_datapoint(_s11_dp(7, 1_000_000_007)) is None
        assert assembler.stats.out_of_range_points == 1
        assert assembler.stats.dropped_sweeps == 1
        assert assembler.stats.incomplete_sweeps == 1

    def test_non_monotonic_frequency_raises(self) -> None:
        assembler = _make_assembler(5)
        with pytest.raises(LibreVnaSweepError):
            for i in range(5):
                assembler.feed_datapoint(_s11_dp(i, 1_000_000_000 + (4 - i)))
        assert assembler.stats.dropped_sweeps == 0  # complete but inconsistent

    def test_no_partial_output_ever(self) -> None:
        # Partial sweeps (missing points) must never yield any output.
        assembler = _make_assembler(5)
        for i in (0, 1, 2):  # only 3 of 5 points
            assert assembler.feed_datapoint(_s11_dp(i, 1_000_000_000 + i)) is None
        assert assembler.stats.incomplete_sweeps == 0  # still active, not dropped
        # A jump invalidates it; still no output
        assert assembler.feed_datapoint(_s11_dp(4, 1_000_000_004)) is None
        assert assembler.stats.dropped_sweeps == 1
        # Feed the missing point afterwards: sweep B never starts without point 0
        assert assembler.feed_datapoint(_s11_dp(3, 1_000_000_003)) is None

    def test_assembled_sweep_ordered_and_started_at(self) -> None:
        clock = _Clock()
        assembler = _make_assembler(5, clock=clock)
        clock.now = 123.5
        sweep = None
        for i in range(5):
            result = assembler.feed_datapoint(_s11_dp(i, 1_000_000_000 + i))
            if result is not None:
                sweep = result
        assert sweep is not None
        assert sweep.started_at == 123.5
        assert [dp.frequency_hz for dp in sweep.points] == [
            1_000_000_000 + i for i in range(5)
        ]

    def test_drop_stats_subsets_after_timeout(self) -> None:
        clock = _Clock()
        assembler = _make_assembler(5, timeout_ms=50, clock=clock)
        assembler.feed_datapoint(_s11_dp(0, 1_000_000_000))
        clock.advance(100)
        with pytest.raises(LibreVnaSweepTimeoutError):
            assembler.check_timeout()
        stats = assembler.stats
        assert stats.timeouts == 1
        assert stats.incomplete_sweeps == 1
        assert stats.dropped_sweeps == 1
        # subset invariant: timeouts <= incomplete <= dropped
        assert stats.timeouts <= stats.incomplete_sweeps <= stats.dropped_sweeps

    def test_stats_reset(self) -> None:
        assembler = _make_assembler(5)
        assembler.feed_datapoint(_s11_dp(2, 1_000_000_000))
        assert assembler.stats.dropped_sweeps == 1
        assembler.reset_stats()
        assert assembler.stats == SweepAssemblerStats()

    def test_reset_keeps_stats(self) -> None:
        assembler = _make_assembler(5)
        assembler.feed_datapoint(_s11_dp(2, 1_000_000_000))
        assert assembler.stats.dropped_sweeps == 1
        assembler.reset()
        assert assembler.stats.dropped_sweeps == 1
        for i in range(5):
            result = assembler.feed_datapoint(_s11_dp(i, 1_000_000_000 + i))
        assert result is not None

    def test_timeout_drops_and_raises_structured_error(self) -> None:
        clock = _Clock()
        assembler = _make_assembler(5, timeout_ms=50, clock=clock)
        assembler.feed_datapoint(_s11_dp(0, 1_000_000_000))
        assembler.feed_datapoint(_s11_dp(1, 1_000_000_001))
        clock.advance(60)
        with pytest.raises(LibreVnaSweepTimeoutError) as ctx:
            assembler.check_timeout()
        assert ctx.value.reason == "sweep_timeout"
        assert ctx.value.code.value == "invalid_argument"
        # after the timeout drop, a fresh point 0 resyncs and completes
        sweep = None
        for i in range(5):
            result = assembler.feed_datapoint(_s11_dp(i, 1_000_000_000 + i))
            if result is not None:
                sweep = result
        assert sweep is not None

    def test_timeout_disabled_when_none(self) -> None:
        clock = _Clock()
        assembler = _make_assembler(5, timeout_ms=None, clock=clock)
        assembler.feed_datapoint(_s11_dp(0, 1_000_000_000))
        clock.advance(10_000)
        assert assembler.check_timeout() is None
        assert assembler.stats.timeouts == 0

    def test_timeout_before_deadline_noop(self) -> None:
        clock = _Clock()
        assembler = _make_assembler(5, timeout_ms=50, clock=clock)
        assembler.feed_datapoint(_s11_dp(0, 1_000_000_000))
        clock.advance(49)
        assert assembler.check_timeout() is None
        assert assembler.stats.timeouts == 0

    def test_timeout_no_active_sweep_noop(self) -> None:
        clock = _Clock()
        assembler = _make_assembler(5, timeout_ms=50, clock=clock)
        clock.advance(1000)
        assert assembler.check_timeout() is None
        assert assembler.stats.timeouts == 0


# ---------------------------------------------------------------------------
# Receiver plan: channel/receiver fields, reference denominator, finiteness
# ---------------------------------------------------------------------------


class TestReceiverPlan:
    def test_plan_valid_s11_datapoint(self) -> None:
        dp = _s11_dp(0, 500_000_000)
        assert datapoint_matches_plan(dp, S11_RECEIVER_PLAN)

    def test_missing_reference_slot_invalid(self) -> None:
        dp = VNADatapoint(
            point_number=0, frequency_hz=500_000_000, cdbm=-1000,
            receivers=((DESC_MASK_PORT1, 0.5 + 0j),),
        )
        assert not datapoint_matches_plan(dp, S11_RECEIVER_PLAN)

    def test_missing_port1_slot_invalid(self) -> None:
        dp = VNADatapoint(
            point_number=0, frequency_hz=500_000_000, cdbm=-1000,
            receivers=((DESC_MASK_REFERENCE, 1.0 + 0j),),
        )
        assert not datapoint_matches_plan(dp, S11_RECEIVER_PLAN)

    def test_duplicate_reference_receiver_invalid(self) -> None:
        # Two reference receivers (desc 0x10 twice): ambiguous -> invalid
        dp = VNADatapoint(
            point_number=0, frequency_hz=500_000_000, cdbm=-1000,
            receivers=(
                (DESC_MASK_REFERENCE, 1.0 + 0j),
                (DESC_MASK_REFERENCE, 2.0 + 0j),
                (DESC_MASK_PORT1, 0.5 + 0j),
            ),
        )
        assert not datapoint_matches_plan(dp, S11_RECEIVER_PLAN)

    def test_zero_reference_denominator_invalid(self) -> None:
        dp = _s11_dp(0, 500_000_000, ref=0.0 + 0j)
        assert not datapoint_matches_plan(dp, S11_RECEIVER_PLAN)

    def test_nan_receiver_value_invalid(self) -> None:
        dp = _s11_dp(0, 500_000_000, ref=complex(float("nan"), 0.0))
        assert not datapoint_matches_plan(dp, S11_RECEIVER_PLAN)
        dp = _s11_dp(0, 500_000_000, port1=complex(0.0, float("inf")))
        assert not datapoint_matches_plan(dp, S11_RECEIVER_PLAN)

    def test_inf_receiver_value_invalid(self) -> None:
        dp = _s11_dp(0, 500_000_000, port1=complex(float("inf"), 0.0))
        assert not datapoint_matches_plan(dp, S11_RECEIVER_PLAN)

    def test_extra_receivers_ignored(self) -> None:
        # Port2 (0x02) and stage-1 receivers are not part of the S11 plan
        # and must not invalidate the datapoint (ISSUE-022 extends the plan).
        dp = VNADatapoint(
            point_number=0, frequency_hz=500_000_000, cdbm=-1000,
            receivers=(
                (DESC_MASK_REFERENCE, 1.0 + 0j),
                (DESC_MASK_PORT1, 0.5 + 0j),
                (DESC_MASK_PORT2, 0.2 + 0j),
                (0x21, 0.3 + 0j),  # stage 1, port 1
            ),
        )
        assert datapoint_matches_plan(dp, S11_RECEIVER_PLAN)

    def test_empty_receivers_invalid_for_s11_plan(self) -> None:
        dp = VNADatapoint(
            point_number=0, frequency_hz=500_000_000, cdbm=-1000, receivers=(),
        )
        assert not datapoint_matches_plan(dp, S11_RECEIVER_PLAN)

    def test_dual_stage_payload_valid_under_s11_plan(self) -> None:
        # The real device in dual-stage mode sends one payload per point with
        # all receivers (0x10, 0x01, 0x33, 0x22 per the protocol doc); under
        # the S11 plan the stage-1 receivers are ignored and the point is
        # valid.
        dp = VNADatapoint(
            point_number=0, frequency_hz=500_000_000, cdbm=-1000,
            receivers=(
                (DESC_MASK_REFERENCE, 1.0 + 0j),      # stage 0 ref
                (DESC_MASK_PORT1, 0.5 + 0j),          # stage 0 port1
                (0x33, 1.0 + 0j),                     # stage 1 ref+port1+port2
                (0x22, 0.4 + 0j),                     # stage 1 port2
            ),
        )
        assert datapoint_matches_plan(dp, S11_RECEIVER_PLAN)

    def test_invalid_datapoint_drops_current_sweep(self) -> None:
        assembler = _make_assembler(5)
        assembler.feed_datapoint(_s11_dp(0, 1_000_000_000))
        assembler.feed_datapoint(_s11_dp(1, 1_000_000_001))
        # point 2 with a zero reference denominator
        assert assembler.feed_datapoint(_s11_dp(2, 1_000_000_002, ref=0.0 + 0j)) is None
        assert assembler.stats.invalid_points == 1
        assert assembler.stats.dropped_sweeps == 1
        sweep = None
        for i in range(5):
            result = assembler.feed_datapoint(_s11_dp(i, 1_000_000_000 + i))
            if result is not None:
                sweep = result
        assert sweep is not None


# ---------------------------------------------------------------------------
# Stream + assembler integration: no fake complete sweep from bad data
# ---------------------------------------------------------------------------


class TestStreamAssemblerIntegration:
    def test_full_pipeline_generative_chunking(self) -> None:
        data = b"\x00\x01" + _sweep_bytes(5)  # junk prefix + one full sweep
        clock = _Clock()
        for seed in range(10):
            rng = random.Random(seed)
            stream = LibreVnaPacketStream()
            assembler = _make_assembler(5, clock=clock)
            clock.now = 5.0
            sweeps: list[AssembledSweep] = []
            for chunk in _chunk(data, rng, 13):
                for dp in stream.feed(chunk):
                    result = assembler.feed_datapoint(dp)
                    if result is not None:
                        sweeps.append(result)
            assert len(sweeps) == 1
            assert sweeps[0].started_at == 5.0
            assert [dp.point_number for dp in sweeps[0].points] == [0, 1, 2, 3, 4]

    def test_corrupted_datapoint_payload_no_fake_sweep(self) -> None:
        # Point 2's packet is replaced by a structurally-broken payload: the
        # stream counts it and the sweep can never complete (jump -> drop).
        data = b"".join(
            [
                _vna_packet(freq_hz=1_000_000_000, point_number=0),
                _vna_packet(freq_hz=1_000_000_001, point_number=1),
                encode_packet(VNA_DATAPOINT, b"\x00\x01"),  # malformed point 2
                _vna_packet(freq_hz=1_000_000_003, point_number=3),
                _vna_packet(freq_hz=1_000_000_004, point_number=4),
            ]
        )
        stream = LibreVnaPacketStream()
        assembler = _make_assembler(5)
        sweeps: list[AssembledSweep] = []
        for dp in stream.feed(data):
            result = assembler.feed_datapoint(dp)
            if result is not None:
                sweeps.append(result)
        assert sweeps == []  # no fake complete sweep
        assert stream.stats.malformed_datapoints == 1
        assert assembler.stats.dropped_sweeps == 1
        # A clean sweep afterwards still assembles
        sweep = None
        for dp in stream.feed(_sweep_bytes(5)):
            result = assembler.feed_datapoint(dp)
            if result is not None:
                sweep = result
        assert sweep is not None

    def test_reference_zero_mid_sweep_drops_and_resyncs(self) -> None:
        # point 1 has reference=0 -> invalid -> the whole sweep is dropped;
        # the next complete sweep is returned (reference test semantics).
        data = b"".join(
            [
                _vna_packet(freq_hz=1_000_000_000, point_number=0),
                _vna_packet(freq_hz=1_000_000_001, point_number=1, ref=0.0 + 0j),
                _sweep_bytes(5, base_freq=2_000_000_000),
            ]
        )
        stream = LibreVnaPacketStream()
        assembler = _make_assembler(5)
        sweeps: list[AssembledSweep] = []
        for dp in stream.feed(data):
            result = assembler.feed_datapoint(dp)
            if result is not None:
                sweeps.append(result)
        assert len(sweeps) == 1
        assert sweeps[0].points[0].frequency_hz == 2_000_000_000
        assert assembler.stats.invalid_points == 1
        assert assembler.stats.dropped_sweeps == 1

    def test_bad_crc_mid_sweep_still_assembles_reference_behavior(self) -> None:
        # VNA_DATAPOINT skips CRC validation (reference protocol behavior):
        # a corrupted datapoint CRC still assembles. Documented, not "fixed".
        data = _sweep_bytes(5)
        bad = bytearray(data)
        # flip a byte inside the third packet's CRC (frame layer still parses)
        offset = 2 * len(_vna_packet(freq_hz=1_000_000_000, point_number=0))
        bad[offset - 1] ^= 0xFF
        stream = LibreVnaPacketStream()
        assembler = _make_assembler(5)
        sweep = None
        for dp in stream.feed(bytes(bad)):
            result = assembler.feed_datapoint(dp)
            if result is not None:
                sweep = result
        assert sweep is not None
        assert len(sweep.points) == 5

    def test_stream_and_assembler_stats_observable(self) -> None:
        data = b"".join(
            [
                ACK_PACKET_BYTES,  # ignored control packet
                encode_packet(VNA_DATAPOINT, b"\x00\x01"),  # malformed
                _vna_packet(freq_hz=1_000_000_000, point_number=0),
                _vna_packet(freq_hz=1_000_000_001, point_number=1),
                _vna_packet(freq_hz=1_000_000_002, point_number=2, ref=0.0 + 0j),
                _sweep_bytes(5, base_freq=3_000_000_000),
            ]
        )
        stream = LibreVnaPacketStream()
        assembler = _make_assembler(5)
        sweeps: list[AssembledSweep] = []
        for dp in stream.feed(data):
            result = assembler.feed_datapoint(dp)
            if result is not None:
                sweeps.append(result)
        assert len(sweeps) == 1
        assert sweeps[0].points[0].frequency_hz == 3_000_000_000
        assert stream.stats.ignored_packets == 1
        assert stream.stats.malformed_datapoints == 1
        assert assembler.stats.invalid_points == 1
        assert assembler.stats.dropped_sweeps == 1
        assert assembler.stats.incomplete_sweeps == 1
        # error taxonomy sanity
        assert issubclass(LibreVnaDatapointError, LibreVnaStreamError)
        assert issubclass(LibreVnaSweepError, LibreVnaStreamError)
        assert issubclass(LibreVnaSweepTimeoutError, LibreVnaStreamError)
        assert LibreVnaStreamError.__mro__[1].__name__ == "LibreVnaTransportError"
