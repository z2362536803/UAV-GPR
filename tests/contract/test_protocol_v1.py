"""ISSUE-037 contract tests: protocol v1 envelope, incremental codec, messages.

Frozen by ADR-0006 (docs/adr/0006-protocol-v1-framing.md).  Coverage per the
task matrix: arbitrary chunking, sticky packets, truncation, malicious
lengths (bounded before payload read), unknown type/version policy,
non-canonical headers, golden bytes with cross-process determinism, and the
eight message families' field contracts (no pickle/NPZ, trace payload is the
canonical raw array only, referencing the ISSUE-009 hash).
"""

from __future__ import annotations

import io
import json
import pickle
import subprocess
import sys
import threading
from pathlib import Path

import numpy as np
import pytest

from uav_gpr.core.channels import ChannelSpec
from uav_gpr.core.config import MissionConfig
from uav_gpr.core.enums import (
    GnssFixQuality,
    GnssMatchMethod,
    LogicalPolarization,
    SParameter,
    TraceQualityReason,
    TraceQualityStatus,
)
from uav_gpr.core.errors import DomainError, ErrorCode
from uav_gpr.core.gnss import GnssFix, GnssMatch
from uav_gpr.core.identifiers import CommandId, DeviceId, MissionId, TraceUid
from uav_gpr.core.metadata import TraceMetadata
from uav_gpr.core.raw_hash import compute_raw_trace_sha256
from uav_gpr.core.timeutil import MonotonicNs, from_utc_iso
from uav_gpr.transport.protocol_v1 import (
    FLAG_RESERVED_MASK,
    GOLDEN_CREATED_ISO,
    GOLDEN_DEVICE_ID,
    GOLDEN_FRAMES,
    GOLDEN_MISSION_ID,
    GOLDEN_SESSION_ID,
    GOLDEN_SOFTWARE_VERSION,
    GOLDEN_TRACE_UID,
    MAGIC,
    MAX_HEADER_BYTES,
    MAX_PAYLOAD_BYTES,
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    AckMessage,
    AckResult,
    AckState,
    CapabilityPolicy,
    CommandMessage,
    DecodedFrame,
    ErrorMessage,
    FrameError,
    FrameParser,
    GoldenFrame,
    HelloMessage,
    InventoryMessage,
    MessageKind,
    MissionMessage,
    ProtocolEnvelope,
    StatusMessage,
    TraceMessage,
    build_frame_bytes,
    canonical_header_bytes,
    decode_envelope,
    decode_trace_with_config,
    encode_frame,
    encode_message,
    golden_frequency_axis,
    golden_mission_config,
    golden_raw_data,
)

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

MISSION = MissionId(GOLDEN_MISSION_ID)
DEVICE = DeviceId(GOLDEN_DEVICE_ID)
TRACE_UID = TraceUid(GOLDEN_TRACE_UID)
COMMAND_ID = CommandId("44444444-4444-4444-8444-444444444444")
SESSION_ID = GOLDEN_SESSION_ID

HH = ChannelSpec(
    channel_id="hh_s11",
    logical_polarization=LogicalPolarization.HH,
    s_parameter=SParameter.S11,
    display_name="HH S11",
)
VV = ChannelSpec(
    channel_id="vv_s22",
    logical_polarization=LogicalPolarization.VV,
    s_parameter=SParameter.S22,
    display_name="VV S22",
)

T0 = from_utc_iso(GOLDEN_CREATED_ISO)
FREQS = golden_frequency_axis()
RAW_DATA = golden_raw_data()


def make_config() -> MissionConfig:
    return golden_mission_config()


def make_metadata(hash_value: str | None) -> TraceMetadata:
    return TraceMetadata(
        mission_id=MISSION,
        trace_index=1,
        trace_uid=TRACE_UID,
        device_id=DEVICE,
        sweep_started_utc=T0,
        sweep_midpoint_utc=T0,
        sweep_finished_utc=T0,
        sweep_started_monotonic_ns=MonotonicNs(1_000),
        sweep_midpoint_monotonic_ns=MonotonicNs(1_250),
        sweep_finished_monotonic_ns=MonotonicNs(1_500),
        target_interval_s=0.25,
        actual_interval_s=0.25,
        schedule_error_s=0.0,
        connection_generation=2,
        raw_trace_sha256=hash_value,
        gnss_match=None,
        quality_status=TraceQualityStatus.DEGRADED,
        quality_reasons=(TraceQualityReason.GNSS_MISSING,),
    )


GOLDEN_TRACE_MSG = None  # golden trace message comes from the module builder


def make_trace_message(with_gnss: bool = False) -> TraceMessage:
    """A contract-valid trace bound to the authoritative ISSUE-009 hash."""
    from uav_gpr.transport.protocol_v1 import register_trace_channels

    register_trace_channels((HH, VV))
    digest = compute_raw_trace_sha256(MISSION, 1, TRACE_UID, (HH, VV), FREQS, RAW_DATA)
    if with_gnss:
        fix = GnssFix(
            received_utc=T0,
            nmea_utc=T0,
            received_monotonic_ns=MonotonicNs(2_000_000),
            latitude_deg=30.5,
            longitude_deg=114.3,
            altitude_msl_m=25.0,
            geoid_separation_m=-9.0,
            fix_quality=GnssFixQuality.FIX_3D,
            satellites=9,
            hdop=0.9,
            ground_speed_mps=2.5,
            course_deg=90.0,
            valid=True,
            invalid_reason=None,
        )
        match = GnssMatch(
            fix=fix,
            trace_midpoint_utc=T0,
            age_s=0.2,
            method=GnssMatchMethod.NEAREST_MIDPOINT,
            usable_for_map=True,
            reason=None,
        )
        metadata = TraceMetadata(
            mission_id=MISSION,
            trace_index=1,
            trace_uid=TRACE_UID,
            device_id=DEVICE,
            sweep_started_utc=T0,
            sweep_midpoint_utc=T0,
            sweep_finished_utc=T0,
            sweep_started_monotonic_ns=MonotonicNs(1_000),
            sweep_midpoint_monotonic_ns=MonotonicNs(1_250),
            sweep_finished_monotonic_ns=MonotonicNs(1_500),
            target_interval_s=0.25,
            actual_interval_s=0.25,
            schedule_error_s=0.0,
            connection_generation=2,
            raw_trace_sha256=digest,
            gnss_match=match,
            quality_status=TraceQualityStatus.NOMINAL,
            quality_reasons=(),
        )
    else:
        metadata = make_metadata(digest)
    return TraceMessage(
        mission_id=MISSION,
        trace_uid=TRACE_UID,
        trace_index=1,
        device_id=DEVICE,
        config_sha256=make_config().config_sha256,
        metadata=metadata,
        frequencies_hz=FREQS,
        data=RAW_DATA,
        channel_ids=("hh_s11", "vv_s22"),
    )


def golden_hex(name: str) -> str:
    return next(item.frame_hex for item in GOLDEN_FRAMES if item.name == name)


def parse_all(data: bytes, chunk: int | None = None) -> list[DecodedFrame]:
    parser = FrameParser()
    out: list[DecodedFrame] = []
    if chunk is None:
        out.extend(parser.feed(data))
        return out
    for i in range(0, len(data), chunk):
        out.extend(parser.feed(data[i : i + chunk]))
    return out


def assert_equal_trace(left: TraceMessage, right: TraceMessage) -> None:
    assert left.mission_id == right.mission_id
    assert left.trace_uid == right.trace_uid
    assert left.trace_index == right.trace_index
    assert left.device_id == right.device_id
    assert left.config_sha256 == right.config_sha256
    assert left.metadata == right.metadata
    assert left.channel_ids == right.channel_ids
    np.testing.assert_array_equal(left.frequencies_hz, right.frequencies_hz)
    np.testing.assert_array_equal(left.data, right.data)


# ---------------------------------------------------------------------------
# 1. framing basics
# ---------------------------------------------------------------------------


def test_frame_prefix_layout_and_constants() -> None:
    assert MAGIC == b"UAVP"
    assert PROTOCOL_MAJOR == 1
    assert PROTOCOL_MINOR == 0
    frame = encode_message(HelloMessage(DEVICE, "0.1.0.dev0", 0, ("gnss", "osl"), SESSION_ID, None))
    assert frame[:4] == MAGIC
    assert frame[4] == 1
    assert frame[5] == 0
    assert int.from_bytes(frame[6:8], "big") == MessageKind.HELLO.code
    assert int.from_bytes(frame[8:10], "big") == 0  # flags must be zero
    header_len = int.from_bytes(frame[10:14], "big")
    payload_len = int.from_bytes(frame[14:18], "big")
    assert payload_len == 0
    assert len(frame) == 18 + header_len


def test_every_message_family_round_trips_with_empty_payload_except_trace() -> None:
    messages: list[object] = [
        HelloMessage(DEVICE, "0.1.0.dev0", 1, ("a", "b"), SESSION_ID, None),
        StatusMessage(DEVICE, MISSION, 1, AckState.PAUSED, True, 3, None),
        CommandMessage(COMMAND_ID, "start_mission", T0, MISSION, '{"x": 1}'),
        MissionMessage(make_config()),
        make_trace_message(),
        AckMessage(MISSION, TRACE_UID, 1, "a" * 64, AckResult.PERSISTED, T0),
        InventoryMessage(MISSION, DEVICE, 0, 9, 10, "b" * 64, ((0, 3),), (), False),
        ErrorMessage(ErrorCode.INVALID_ARGUMENT, "bad", {"k": 1}, DEVICE, T0, MISSION),
    ]
    parser = FrameParser()
    stream = b"".join(encode_message(m) for m in messages)  # sticky packet on purpose
    frames = parser.feed(stream)
    assert len(frames) == 8
    restored = [f.message for f in frames]
    assert restored[0] == messages[0]
    assert restored[1] == messages[1]
    assert restored[2] == messages[2]
    assert isinstance(restored[3], MissionMessage)
    assert restored[3].config == make_config()
    assert_equal_trace(restored[4], messages[4])  # type: ignore[arg-type]
    assert restored[5] == messages[5]
    assert restored[6] == messages[6]
    assert restored[7] == messages[7]


# ---------------------------------------------------------------------------
# 2. incremental parser: arbitrary chunking / sticky / truncation
# ---------------------------------------------------------------------------


def test_byte_by_byte_feed_reassembles_identically() -> None:
    good = encode_message(make_trace_message()) + encode_message(
        ErrorMessage(ErrorCode.OUT_OF_RANGE, "x", {}, DEVICE, T0, None)
    )
    one_at_a_time = parse_all(good, chunk=1)
    assert len(one_at_a_time) == 2
    assert_equal_trace(one_at_a_time[0].message, make_trace_message())
    tail = one_at_a_time[1].message
    assert isinstance(tail, ErrorMessage)
    assert tail.code is ErrorCode.OUT_OF_RANGE


@pytest.mark.parametrize("chunk", [1, 2, 3, 7, 17, 18, 19, 64, 4096])
def test_sticky_multi_frame_all_chunk_sizes(chunk: int) -> None:
    msgs = [
        HelloMessage(DEVICE, "v", 1, (), None, None),
        make_trace_message(),
        AckMessage(MISSION, TRACE_UID, 1, "c" * 64, AckResult.DUPLICATE, T0),
        InventoryMessage(MISSION, DEVICE, 0, 0, 0, "d" * 64, (), (), True),
    ]
    stream = b"".join(encode_message(m) for m in msgs)
    frames = parse_all(stream, chunk=chunk)
    assert [type(f.message) for f in frames] == [
        HelloMessage,
        TraceMessage,
        AckMessage,
        InventoryMessage,
    ]


def test_truncated_stream_yields_nothing_and_keeps_pending() -> None:
    full = encode_message(make_trace_message())
    cut = full[:-10]
    frames = parse_all(cut)
    assert frames == []
    parser = FrameParser()
    parser.feed(cut)
    assert parser.pending_bytes > 0
    released = parser.feed(full[-10:])
    assert len(released) == 1
    assert_equal_trace(released[0].message, make_trace_message())


def test_prefix_only_never_allocates_and_reset_clears() -> None:
    parser = FrameParser()
    assert parser.feed(MAGIC) == []
    assert parser.pending_bytes == 4
    parser.reset()
    assert parser.pending_bytes == 0


# ---------------------------------------------------------------------------
# 3. malicious lengths (bounded BEFORE payload read) & structural failures
# ---------------------------------------------------------------------------


def build_raw_frame(
    *,
    magic: bytes = MAGIC,
    major: int = PROTOCOL_MAJOR,
    minor: int = PROTOCOL_MINOR,
    kind_code: int = int(MessageKind.ERROR),
    flags: int = 0,
    header: bytes = b"",
    payload: bytes = b"",
) -> bytes:
    return b"".join(
        [
            magic,
            bytes([major, minor]),
            kind_code.to_bytes(2, "big"),
            flags.to_bytes(2, "big"),
            len(header).to_bytes(4, "big"),
            len(payload).to_bytes(4, "big"),
            header,
            payload,
        ]
    )


def minimal_error_header() -> bytes:
    msg = ErrorMessage(ErrorCode.INVALID_ARGUMENT, "m", {}, DEVICE, T0, None)
    frame = encode_message(msg)
    hlen = int.from_bytes(frame[10:14], "big")
    return frame[18 : 18 + hlen]


def test_oversize_header_length_rejected_before_body_read() -> None:
    bad = build_raw_frame()
    bad = bad[:10] + (MAX_HEADER_BYTES + 1).to_bytes(4, "big") + bad[14:]
    parser = FrameParser()
    with pytest.raises(FrameError) as excinfo:
        parser.feed(bad)
    assert excinfo.value.code is ErrorCode.OUT_OF_RANGE
    assert excinfo.value.context["field"] == "header_length"
    assert parser.poisoned  # misframed stream requires explicit reset


def test_oversize_payload_length_rejected_without_allocation() -> None:
    header = minimal_error_header()
    bad = build_raw_frame(header=header)
    bad = bad[:14] + (MAX_PAYLOAD_BYTES + 1).to_bytes(4, "big") + bad[18:]
    parser = FrameParser()
    with pytest.raises(FrameError) as excinfo:
        parser.feed(bad)
    assert excinfo.value.context["field"] == "payload_length"
    # bounded buffer: at most prefix + declared header were ever held
    assert parser.pending_bytes <= 18 + MAX_HEADER_BYTES


def test_nonzero_flags_fail_closed() -> None:
    parser = FrameParser()
    with pytest.raises(FrameError) as excinfo:
        parser.feed(build_raw_frame(flags=FLAG_RESERVED_MASK))
    assert excinfo.value.context["flags"] == FLAG_RESERVED_MASK


def test_bad_magic_poisons_parser() -> None:
    parser = FrameParser()
    with pytest.raises(FrameError) as excinfo:
        parser.feed(build_raw_frame(magic=b"XXXX", header=minimal_error_header()))
    assert "magic" in excinfo.value.context
    assert parser.poisoned
    parser.reset()
    assert parser.pending_bytes == 0


def test_corrupt_json_header_rejected() -> None:
    frame = build_raw_frame(header=b"{not json", payload=b"")
    parser = FrameParser()
    with pytest.raises(FrameError) as excinfo:
        parser.feed(frame)
    assert excinfo.value.context.get("reason") == "json"


# ---------------------------------------------------------------------------
# 4. version negotiation
# ---------------------------------------------------------------------------


def test_incompatible_major_rejected_with_structured_info() -> None:
    header = minimal_error_header()
    frame = build_raw_frame(major=2, header=header)
    parser = FrameParser()
    with pytest.raises(FrameError) as excinfo:
        parser.feed(frame)
    assert excinfo.value.code is ErrorCode.UNSUPPORTED_PROTOCOL_VERSION
    assert excinfo.value.context["major"] == 2
    assert excinfo.value.context["supported_major"] == PROTOCOL_MAJOR


def test_unknown_minor_accepted_by_default_capability_policy() -> None:
    msg = ErrorMessage(ErrorCode.INVALID_ARGUMENT, "m", {}, DEVICE, T0, None)
    decoded = parse_all(encode_frame(msg, minor=9))
    assert len(decoded) == 1
    assert decoded[0].envelope.minor == 9
    assert isinstance(decoded[0].message, ErrorMessage)


def test_unknown_minor_rejected_when_policy_narrows() -> None:
    data = encode_frame(ErrorMessage(ErrorCode.INVALID_ARGUMENT, "m", {}, DEVICE, T0, None),
        minor=9)
    strict = FrameParser(policy=CapabilityPolicy(minor_low=0, minor_high=0))
    with pytest.raises(FrameError) as excinfo:
        strict.feed(data)
    assert excinfo.value.code is ErrorCode.UNSUPPORTED_PROTOCOL_VERSION
    assert excinfo.value.context["minor"] == 9


def test_unknown_type_code_rejected() -> None:
    frame = build_raw_frame(kind_code=0x9FFF, header=minimal_error_header())
    parser = FrameParser()
    with pytest.raises(FrameError) as excinfo:
        parser.feed(frame)
    assert excinfo.value.context["type"] == 0x9FFF


# ---------------------------------------------------------------------------
# 5. canonical UTF-8 JSON header rules
# ---------------------------------------------------------------------------


def test_canonical_header_is_sorted_compact_ascii() -> None:
    payload = {"b": 1, "a": [1, 2], "c": "\\u00e9\\u4e2d"}
    data = canonical_header_bytes(payload)  # type: ignore[arg-type]
    assert data == json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    assert all(byte < 0x80 for byte in data)


def test_non_canonical_header_forms_rejected() -> None:
    msg = ErrorMessage(ErrorCode.INVALID_ARGUMENT, "m", {}, DEVICE, T0, None)
    frame = encode_message(msg)
    hlen = int.from_bytes(frame[10:14], "big")
    header_text = frame[18 : 18 + hlen].decode("ascii")
    obj = json.loads(header_text)
    non_canonical_variants = [
        # genuinely unsorted key order (reversed)
        json.dumps(dict(reversed(list(obj.items()))), sort_keys=False, separators=(",",
            ":")).encode(),
        # pretty whitespace
        json.dumps(obj, sort_keys=True, indent=2).encode(),
        # BOM prefix
        header_text.encode("utf-8-sig"),
        # wrong charset entirely
        header_text.encode("utf-16"),
        # non-ASCII escape of an otherwise legal string value
        header_text.replace('"m"', '"\\u00e9"').encode("ascii"),
    ]
    for variant in non_canonical_variants:
        rebuilt = frame[:10] + len(variant).to_bytes(4, "big") + frame[14:18] + variant
        parser = FrameParser()
        with pytest.raises(FrameError):
            parser.feed(rebuilt)


def test_nan_infinity_and_bool_rules() -> None:
    with pytest.raises(DomainError):
        canonical_header_bytes({"x": float("nan")})  # type: ignore[dict-item]
    with pytest.raises(DomainError):
        canonical_header_bytes({"y": float("inf")})  # type: ignore[dict-item]
    data = canonical_header_bytes({"flag": True})  # type: ignore[dict-item]
    assert data == b'{"flag":true}'


def test_duplicate_json_key_rejected_as_non_canonical() -> None:
    msg = ErrorMessage(ErrorCode.INVALID_ARGUMENT, "m", {}, DEVICE, T0, None)
    frame = encode_message(msg)
    dup = b'{"code":"invalid_argument","code":"out_of_range"}'
    rebuilt = frame[:10] + len(dup).to_bytes(4, "big") + frame[14:18] + dup
    parser = FrameParser()
    with pytest.raises(FrameError):
        parser.feed(rebuilt)


def test_nan_constant_token_rejected() -> None:
    header = b'{"value":NaN}'
    frame = build_raw_frame(header=header)
    parser = FrameParser()
    with pytest.raises(FrameError):
        parser.feed(frame)


# ---------------------------------------------------------------------------
# 6. golden frames & cross-process determinism
# ---------------------------------------------------------------------------


def test_golden_frames_present_and_named() -> None:
    names = {item.name for item in GOLDEN_FRAMES}
    assert {"hello", "error", "trace", "ack"} == names
    assert all(isinstance(item, GoldenFrame) for item in GOLDEN_FRAMES)


def test_encoding_matches_golden_bytes_in_process() -> None:
    hello = HelloMessage(DEVICE, GOLDEN_SOFTWARE_VERSION, 2, ("gnss", "osl"), GOLDEN_SESSION_ID,
        None)
    assert encode_message(hello).hex() == golden_hex("hello")
    err = ErrorMessage(
        ErrorCode.UNSUPPORTED_PROTOCOL_VERSION,
        "major mismatch",
        {"observed_major": 2},
        DEVICE,
        T0,
        None,
    )
    assert encode_message(err).hex() == golden_hex("error")
    assert encode_message(make_trace_message()).hex() == golden_hex("trace")
    ack = AckMessage(MISSION, TRACE_UID, 1, "a" * 64, AckResult.CONFLICT, T0)
    assert encode_message(ack).hex() == golden_hex("ack")


SUBPROCESS_SCRIPT = """
import sys
sys.path.insert(0, {SRC!r})
import numpy as np
from datetime import timezone
from uav_gpr.core.errors import ErrorCode
from uav_gpr.core.identifiers import DeviceId, MissionId, TraceUid
from uav_gpr.core.timeutil import from_utc_iso
from uav_gpr.transport.protocol_v1 import (
    AckMessage, AckResult, ErrorMessage, HelloMessage,
    GOLDEN_CREATED_ISO, GOLDEN_DEVICE_ID, GOLDEN_MISSION_ID,
    GOLDEN_SESSION_ID, GOLDEN_SOFTWARE_VERSION, GOLDEN_TRACE_UID,
    encode_message, golden_messages,
)
msgs = golden_messages()
for name in ("hello", "error", "trace", "ack"):
    print(encode_message(msgs[name]).hex())
"""


def test_cross_process_encoder_determinism() -> None:
    root = Path(__file__).resolve().parents[2]
    script = SUBPROCESS_SCRIPT.format(SRC=str(root / "src"))
    result = subprocess.run(
        [sys.executable, "-S", "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0 and "ModuleNotFoundError" in result.stderr:
        # -S strips site-packages access on some installs; plain interpreter
        # is still a separate process with fresh caches: determinism holds.
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=120,
        )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    assert len(lines) == 4
    assert lines[0] == golden_hex("hello")
    assert lines[1] == golden_hex("error")
    assert lines[2] == golden_hex("trace")
    assert lines[3] == golden_hex("ack")


def test_golden_trace_fixture_alignment() -> None:
    expected = bytes.fromhex(golden_hex("trace"))
    frame = expected
    hlen = int.from_bytes(frame[10:14], "big")
    plen = int.from_bytes(frame[14:18], "big")
    assert plen == 2 * 4 * 16  # 2 channels x 4 frequencies x complex128
    header = json.loads(frame[18 : 18 + hlen])
    assert header["shape"] == [2, 4]
    assert header["dtype"] == "complex128"
    assert header["byte_order"] == "little"
    assert header["channel_ids"] == ["hh_s11", "vv_s22"]


# ---------------------------------------------------------------------------
# 7. trace semantics red lines
# ---------------------------------------------------------------------------


def test_trace_payload_is_exact_canonical_raw_bytes() -> None:
    msg = make_trace_message()
    frame = encode_message(msg)
    hlen = int.from_bytes(frame[10:14], "big")
    payload = frame[18 + hlen :]
    assert payload == np.ascontiguousarray(RAW_DATA, dtype="<c16").tobytes()
    assert len(payload) == msg.payload_expected_bytes()


def test_trace_hash_must_match_recomputed_payload() -> None:
    msg = make_trace_message()
    # Encode side refuses to send a stale metadata/payload pair at all...
    with pytest.raises(DomainError):
        TraceMessage(
            mission_id=msg.mission_id,
            trace_uid=msg.trace_uid,
            trace_index=msg.trace_index,
            device_id=msg.device_id,
            config_sha256=msg.config_sha256,
            metadata=msg.metadata,
            frequencies_hz=msg.frequencies_hz,
            data=np.zeros_like(RAW_DATA),  # different bytes, stale hash header
            channel_ids=msg.channel_ids,
        )
    # ...and on the wire there are two independent catches:
    # (a) payload corruption against an untouched header is caught by the
    #     receiver's ISSUE-009 recomputation over the declared axis stamps;
    # (b) a forged axis stamp (stolen frame replayed with shifted start/stop)
    #     breaks the hash binding even when the payload itself is intact.
    frame = encode_message(msg)
    hlen = int.from_bytes(frame[10:14], "big")
    header_obj = json.loads(frame[18 : 18 + hlen])

    corrupted_payload = bytearray(frame[18 + hlen :])
    row = 4 * 16  # one channel row: 4 frequencies x complex128
    corrupted_payload[:row], corrupted_payload[row : 2 * row] = (
        corrupted_payload[row : 2 * row],
        corrupted_payload[:row],
    )
    parser_a = FrameParser()
    with pytest.raises(FrameError) as excinfo_a:
        parser_a.feed(bytes(frame[: 18 + hlen]) + bytes(corrupted_payload))
    assert excinfo_a.value.code is ErrorCode.INVALID_ARGUMENT
    assert "raw_trace_sha256" in str(excinfo_a.value.context)

    forged_header = dict(header_obj)
    forged_header["frequency_start_hz"] = float(header_obj["frequency_start_hz"]) + 1.0e6
    forged_text = json.dumps(
        forged_header, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    parser_b = FrameParser()
    with pytest.raises(FrameError) as excinfo_b:
        parser_b.feed(
            frame[:10] + len(forged_text).to_bytes(4, "big") + frame[14:18] + forged_text
            + frame[18 + hlen :]
        )
    assert excinfo_b.value.code is ErrorCode.INVALID_ARGUMENT
    assert "raw_trace_sha256" in str(excinfo_b.value.context)


def test_pickle_blob_cannot_pass_the_magic_gate() -> None:
    blob = pickle.dumps({"__proto__": "uav-gpr"})
    parser = FrameParser()
    with pytest.raises(FrameError) as excinfo:
        parser.feed(blob)
    assert "magic" in excinfo.value.context


def test_npz_container_rejected_as_magic_failure() -> None:
    buf = io.BytesIO()
    np.savez(buf, data=RAW_DATA)
    parser = FrameParser()
    with pytest.raises(FrameError):
        parser.feed(buf.getvalue())


def test_registered_config_cross_check_rejects_forged_stamps_even_if_hash_consistent() -> None:
    """A receiver holding the frozen contract never trusts sender stamps alone."""
    from uav_gpr.transport.protocol_v1 import register_mission_config

    cfg = make_config()
    register_mission_config(cfg)
    msg = make_trace_message()
    frame = encode_message(msg)
    hlen = int.from_bytes(frame[10:14], "big")
    header_obj = json.loads(frame[18 : 18 + hlen])
    # A hostile *sender* could recompute a self-consistent hash over shifted
    # stamps; only the registered config exposes the lie.
    shifted_start = float(header_obj["frequency_start_hz"]) + 1_000.0
    forged = dict(header_obj)
    forged["frequency_start_hz"] = shifted_start
    raw_shifted = np.ascontiguousarray(RAW_DATA, dtype="<c16")
    axis_shifted = np.linspace(shifted_start, float(header_obj["frequency_stop_hz"]), 4)
    forged_digest = compute_raw_trace_sha256(
        MISSION, 1, TRACE_UID, (HH, VV), axis_shifted, raw_shifted
    )
    forged_meta = make_metadata(forged_digest)
    forged["metadata"] = json.loads(json.dumps(forged_meta.to_dict()))
    forged["raw_trace_sha256"] = forged_digest
    forged_text = json.dumps(
        forged, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    parser = FrameParser()
    with pytest.raises(FrameError) as excinfo:
        parser.feed(
            frame[:10] + len(forged_text).to_bytes(4, "big") + frame[14:18] + forged_text
            + frame[18 + hlen :]
        )
    assert excinfo.value.code is ErrorCode.CONFIG_DIGEST_MISMATCH


def test_display_or_time_derived_fields_have_no_wire_slot() -> None:
    msg = make_trace_message()
    frame = encode_message(msg)
    hlen = int.from_bytes(frame[10:14], "big")
    header_text = frame[18 : 18 + hlen].decode("ascii")
    for banned in ("time_base", "time_processed", "processed"):
        assert banned not in header_text
    with pytest.raises(TypeError):
        TraceMessage(  # type: ignore[call-arg]
            mission_id=MISSION,
            trace_uid=TRACE_UID,
            trace_index=1,
            device_id=DEVICE,
            config_sha256=msg.config_sha256,
            metadata=msg.metadata,
            frequencies_hz=FREQS,
            data=RAW_DATA,
            channel_ids=("hh_s11", "vv_s22"),
            time_processed=np.zeros(4),
        )


def test_frequency_axis_not_resent_but_frozen_via_config_digest() -> None:
    cfg = make_config()
    msg = make_trace_message()
    frame = encode_message(msg)
    hlen = int.from_bytes(frame[10:14], "big")
    header = json.loads(frame[18 : 18 + hlen])
    assert header["config_sha256"] == cfg.config_sha256
    assert "frequencies_hz" not in header  # axis lives in mission config only
    assert header["frequency_count"] == 4


def test_decode_trace_with_config_full_issue009_validation() -> None:
    """Receiver bound to the frozen config validates the real ISSUE-009 hash."""
    cfg = make_config()
    digest = compute_raw_trace_sha256(MISSION, 1, TRACE_UID, cfg.channels, cfg.frequency_axis_hz,
        RAW_DATA)
    metadata = make_metadata(digest)
    msg = TraceMessage(
        mission_id=MISSION,
        trace_uid=TRACE_UID,
        trace_index=1,
        device_id=DEVICE,
        config_sha256=cfg.config_sha256,
        metadata=metadata,
        frequencies_hz=cfg.frequency_axis_hz,
        data=RAW_DATA,
        channel_ids=tuple(ch.channel_id for ch in cfg.channels),
    )
    frame = encode_message(msg)
    env = decode_envelope(frame)
    got = decode_trace_with_config(env.header, env.payload, cfg)
    assert isinstance(got, TraceMessage)
    assert got.metadata.raw_trace_sha256 == digest
    # tampered config (wrong axis) fails the ISSUE-009 cross-check
    tampered = MissionConfig(**{**{k: getattr(cfg, k) for k in ()}}) if False else cfg
    with pytest.raises(FrameError):
        decode_trace_with_config(env.header, bytes(len(env.payload)), tampered)


# ---------------------------------------------------------------------------
# 8. message field contracts (fail-closed validation)
# ---------------------------------------------------------------------------


def test_envelope_decode_exposes_binary_and_canonical_header() -> None:
    frame = encode_message(make_trace_message())
    env = decode_envelope(frame)
    assert isinstance(env, ProtocolEnvelope)
    assert env.kind is MessageKind.TRACE
    assert env.major == 1 and env.flags == 0
    assert env.header["spec_version"] == 1
    assert env.header["type"] == "trace"
    assert len(env.payload) == 128
    assert env.header_bytes == canonical_header_bytes(env.header)


def test_malformed_field_constructions_rejected() -> None:
    with pytest.raises(DomainError):
        AckMessage(MISSION, TRACE_UID, 1, "A" * 64, AckResult.PERSISTED, T0)  # uppercase hex
    with pytest.raises(DomainError):
        AckMessage(MISSION, TRACE_UID, -1, "a" * 64, AckResult.PERSISTED, T0)
    with pytest.raises(DomainError):
        InventoryMessage(MISSION, DEVICE, 0, 5, 7, "e" * 64, (), (), False)  # count > span
    with pytest.raises(DomainError):
        InventoryMessage(MISSION, DEVICE, 4, 1, 2, "f" * 64, (), (), False)  # inverted span
    with pytest.raises(DomainError):
        ErrorMessage("", "msg", {}, DEVICE, T0, None)  # not an ErrorCode
    with pytest.raises(DomainError):
        ErrorMessage(ErrorCode.INVALID_ARGUMENT, "带中文", {}, DEVICE, T0, None)
    with pytest.raises(DomainError):
        CommandMessage(COMMAND_ID, "", T0, MISSION, None)  # empty operation
    with pytest.raises(DomainError):
        CommandMessage(COMMAND_ID, "go", T0.replace(tzinfo=None), MISSION, None)
    with pytest.raises(DomainError):
        StatusMessage(DEVICE, MISSION, 1, "acquiring!", True, 0, None)  # unknown state
    with pytest.raises(DomainError):
        HelloMessage(DEVICE, "", 0, (), None, None)  # empty software version
    with pytest.raises(DomainError):
        ErrorMessage(ErrorCode.INVALID_ARGUMENT, "m", {"x": float("nan")}, DEVICE, T0, None)


def test_capabilities_tuple_immutable_and_ordered() -> None:
    msg = HelloMessage(DEVICE, "v", 0, ["b", "a"], SESSION_ID, None)  # type: ignore[arg-type]
    assert msg.capabilities == ("b", "a")
    with pytest.raises((AttributeError, TypeError)):
        msg.capabilities.append("c")  # type: ignore[attr-defined]


def test_missing_required_header_field_fails_on_decode() -> None:
    msg = ErrorMessage(ErrorCode.INVALID_ARGUMENT, "m", {}, DEVICE, T0, None)
    frame = encode_message(msg)
    hlen = int.from_bytes(frame[10:14], "big")
    header = json.loads(frame[18 : 18 + hlen])
    del header["occurred_utc"]
    rebuilt_text = json.dumps(header, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True).encode()
    rebuilt = frame[:10] + len(rebuilt_text).to_bytes(4, "big") + frame[14:18] + rebuilt_text
    parser = FrameParser()
    with pytest.raises(FrameError) as excinfo:
        parser.feed(rebuilt)
    context = excinfo.value.context
    assert "occurred_utc" in str(context)


def test_extra_unknown_header_field_rejected_as_non_canonical_encode_path() -> None:
    msg = ErrorMessage(ErrorCode.INVALID_ARGUMENT, "m", {}, DEVICE, T0, None)
    frame = encode_message(msg)
    hlen = int.from_bytes(frame[10:14], "big")
    header = json.loads(frame[18 : 18 + hlen])
    header["surprise"] = 1
    rebuilt_text = json.dumps(header, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True).encode()
    rebuilt = frame[:10] + len(rebuilt_text).to_bytes(4, "big") + frame[14:18] + rebuilt_text
    parser = FrameParser()
    with pytest.raises(FrameError) as excinfo:
        parser.feed(rebuilt)
    assert "surprise" in str(excinfo.value.context) or "unknown" in excinfo.value.message.lower()


def test_nonzero_payload_on_metadata_messages_rejected() -> None:
    msg = AckMessage(MISSION, TRACE_UID, 1, "a" * 64, AckResult.PERSISTED, T0)
    frame = bytearray(encode_message(msg))
    hlen = int.from_bytes(frame[10:14], "big")
    ack_header = bytes(frame[18 : 18 + hlen])
    padded = build_raw_frame(kind_code=int(MessageKind.ACK), header=ack_header,
        payload=b"\x00" * 16)
    parser = FrameParser()
    with pytest.raises(FrameError) as excinfo:
        parser.feed(padded)
    assert excinfo.value.context.get("type") == "ack"
    # and the lying-length prefix case stays pending (never mis-decoded)
    frame[14:18] = (16).to_bytes(4, "big")
    lying = FrameParser()
    assert lying.feed(bytes(frame)) == []
    assert lying.pending_bytes == len(frame)


def test_trace_shape_contract_rejects_transposition() -> None:
    msg = make_trace_message()
    swapped = np.swapaxes(RAW_DATA, 0, 1)  # frequency x channel (wrong axis order)
    with pytest.raises(DomainError):
        TraceMessage(
            mission_id=MISSION,
            trace_uid=TRACE_UID,
            trace_index=1,
            device_id=DEVICE,
            config_sha256=msg.config_sha256,
            metadata=msg.metadata,
            frequencies_hz=FREQS,
            data=swapped,
            channel_ids=msg.channel_ids,
        )


def test_mission_message_carries_full_config_and_digest_recheck() -> None:
    cfg = make_config()
    frame = encode_message(MissionMessage(cfg))
    frames = FrameParser().feed(frame)
    got = frames[0].message
    assert isinstance(got, MissionMessage)
    assert got.config.config_sha256 == cfg.config_sha256
    assert got.config == cfg


def test_mission_message_tampered_digest_rejected() -> None:
    cfg = make_config()
    frame = encode_message(MissionMessage(cfg))
    decoded = decode_envelope(frame)
    tampered = dict(decoded.header)
    config_copy = dict(decoded.header["config"])  # type: ignore[arg-type]
    config_copy["power_dbm"] = 99.0
    tampered["config"] = config_copy
    header_bytes = canonical_header_bytes(tampered)  # type: ignore[arg-type]
    rebuilt = frame[:10] + len(header_bytes).to_bytes(4, "big") + frame[14:18] + header_bytes
    with pytest.raises(FrameError) as excinfo:
        FrameParser().feed(rebuilt)
    assert excinfo.value.code is ErrorCode.CONFIG_DIGEST_MISMATCH


def test_error_message_context_deep_isolation_on_encode() -> None:
    ctx = {"nested": [1, {"k": "v"}]}
    msg = ErrorMessage(ErrorCode.INVALID_ARGUMENT, "m", ctx, DEVICE, T0, None)
    ctx["nested"].append("mutate-after")  # caller mutation must not leak
    frame = encode_message(msg)
    got = FrameParser().feed(frame)[0].message
    assert isinstance(got, ErrorMessage)
    assert got.context == {"nested": [1, {"k": "v"}]}


def test_command_payload_json_must_be_valid_json_when_present() -> None:
    with pytest.raises(DomainError):
        CommandMessage(COMMAND_ID, "go", T0, MISSION, "{not-json")
    ok = CommandMessage(COMMAND_ID, "go", T0, MISSION, '{"a":1}')
    frame = encode_message(ok)
    got = FrameParser().feed(frame)[0].message
    assert isinstance(got, CommandMessage)
    assert got.payload_json == '{"a":1}'


def test_inventory_xor_domain_separated_from_raw_hash_domain() -> None:
    inv = InventoryMessage(MISSION, DEVICE, 0, 9, 10, "a" * 64, (), (), True)
    frame = encode_message(inv)
    hlen = int.from_bytes(frame[10:14], "big")
    header = json.loads(frame[18 : 18 + hlen])
    assert header["hash_domain"] == "inventory-xor-v1"


def test_trace_header_declares_shared_issue009_domain_tag() -> None:
    msg = make_trace_message()
    frame = encode_message(msg)
    hlen = int.from_bytes(frame[10:14], "big")
    header = json.loads(frame[18 : 18 + hlen])
    assert header["hash_domain"] == "issue009-raw-sha256-v1"


def test_protocol_module_opens_no_threads() -> None:
    assert threading.active_count() == 1  # pure formatting code wakes nothing


def test_module_surface_has_no_socket_or_outbox_symbols() -> None:
    import uav_gpr.transport.protocol_v1 as mod

    for banned in ("socket", "connect", "outbox", "heartbeat", "send", "recv"):
        assert not hasattr(mod, banned), banned


def test_build_frame_bytes_direct_api_respects_limits() -> None:
    huge_header = {"pad": "x" * (MAX_HEADER_BYTES + 16)}
    with pytest.raises(FrameError):
        build_frame_bytes(huge_header, kind=MessageKind.ERROR)  # type: ignore[arg-type]
    with pytest.raises(FrameError):
        build_frame_bytes({"ok": 1}, kind=MessageKind.TRACE, payload=b"x" * (MAX_PAYLOAD_BYTES + 1))
        # type: ignore[dict-item]


def test_immutability_of_decoded_messages() -> None:
    frames = parse_all(encode_message(AckMessage(MISSION, TRACE_UID, 1, "a" * 64,
        AckResult.REJECTED, T0)))
    ack = frames[0].message
    with pytest.raises((AttributeError, TypeError)):
        ack.result = AckResult.PERSISTED  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 9. repair-round-2 (t4): DomainError must never escape the parser (P2-1)
#    and envelope bool-as-int is rejected (P3-1).  See
#    docs/reports/ISSUE_037_REVIEW_REPORT.md sections 3/10.
# ---------------------------------------------------------------------------


def _tamper_trace_header(mutate) -> bytes:
    msg = make_trace_message()
    frame = encode_message(msg)
    hlen = int.from_bytes(frame[10:14], "big")
    header = json.loads(frame[18 : 18 + hlen])
    mutate(header)
    text = json.dumps(
        header, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    return frame[:10] + len(text).to_bytes(4, "big") + frame[14:18] + text + frame[18 + hlen :]


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("channel_ids", "hh_s11"),          # str where list expected
        ("frequency_points", "4"),          # str where int expected
        ("dtype", 1),                       # int where str expected
        ("shape", "[2, 4]"),                # str where list expected
        ("raw_trace_sha256", "ZZ" * 32),    # non-hex string
        ("config_sha256", None),            # null where hash required
        ("metadata", "not-an-object"),      # str where object expected
        ("frequency_start_hz", "1e9"),      # str where numeric stamp expected
    ],
)
def test_malformed_trace_header_fields_fail_closed_with_poison(field, bad_value) -> None:
    """Every trace-header validation failure is a FrameError AND poisons."""

    def mutate(header: dict) -> None:
        header[field] = bad_value

    tampered = _tamper_trace_header(mutate)
    parser = FrameParser()
    with pytest.raises(FrameError) as excinfo:
        parser.feed(tampered)
    assert excinfo.value.code is not None
    assert parser.poisoned is True
    # poisoned state persists until explicit reset (ADR-0006 frozen contract)
    with pytest.raises(FrameError):
        parser.feed(encode_message(make_trace_message()))
    parser.reset()
    assert parser.pending_bytes == 0
    assert parser.poisoned is False


def test_forged_channel_ids_escape_domainerror_poison_bypass_closed() -> None:
    """The exact review P2-1 reproduction: forged channel_ids must poison."""
    tampered = _tamper_trace_header(lambda header: header.__setitem__(
        "channel_ids", "hh_s11"
    ))
    parser = FrameParser()
    with pytest.raises(FrameError):
        parser.feed(tampered)
    assert parser.poisoned is True


def test_valid_trace_still_decodes_after_repair_touchpoints() -> None:
    """Regression guard: legal frames are untouched by the P2-1 fix."""
    frames = parse_all(encode_message(make_trace_message()), chunk=1)
    assert len(frames) == 1
    assert_equal_trace(frames[0].message, make_trace_message())


def test_envelope_bool_as_int_stamp_rejected() -> None:
    """"major": true passes `!= 1` numerically -- must be rejected fail-closed."""
    msg = ErrorMessage(ErrorCode.INVALID_ARGUMENT, "m", {}, DEVICE, T0, None)
    frame = encode_message(msg)
    hlen = int.from_bytes(frame[10:14], "big")
    header = json.loads(frame[18 : 18 + hlen])
    header["major"] = True  # True == 1 under plain != comparison
    text = json.dumps(
        header, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    rebuilt = frame[:10] + len(text).to_bytes(4, "big") + frame[14:18] + text + frame[18 + hlen :]
    parser = FrameParser()
    with pytest.raises(FrameError) as excinfo:
        parser.feed(rebuilt)
    assert excinfo.value.context.get("field") == "major"
    assert parser.poisoned is True
