"""ISSUE-009 contract tests: canonical raw trace hash framing and golden vectors.

The tests pin down the versioned framing frozen in ``docs/DATA_FORMAT.md``
section 5.1 and the golden vectors in ``raw_trace_hash_golden.json``:

- golden digest replication for all four synthetic vectors;
- memory-layout (C/Fortran) and byte-order (little/big endian) equivalence;
- digest sensitivity to any identity/axis/channel/raw field change;
- ambiguity elimination via length-prefixed framing;
- fail-closed rejection of non-canonical IDs, dtypes, shapes and axes;
- GNSS exclusion (GNSS never enters the raw hash);
- input immutability for both the immediate function and ``RawHashSpec``;
- ISSUE-008 ``/trace_metadata/raw_trace_sha256`` 64-ASCII column compatibility.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from uav_gpr.core import (
    ChannelSpec,
    DomainError,
    ErrorCode,
    LogicalPolarization,
    RawHashSpec,
    SParameter,
    compute_raw_trace_sha256,
    validate_raw_hash,
)
from uav_gpr.core.identifiers import MissionId, TraceUid
from uav_gpr.core.raw_hash import RAW_HASH_MAGIC, RAW_HASH_VERSION

pytestmark = pytest.mark.contract

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

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

_MISSION_ID = MissionId("0f0e8a3b-6f2d-4c1e-9a7b-112233445566")
_TRACE_UID = TraceUid("a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d")


def load_golden_manifest() -> dict[str, object]:
    path = Path(__file__).with_name("raw_trace_hash_golden.json")
    result = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(result, dict)
    return result


def _as_complex(data: object) -> np.ndarray:
    """Convert golden ``[channel][freq]`` of ``[re, im]`` to complex."""
    arr = np.asarray(data, dtype=np.float64)
    return arr[..., 0] + 1j * arr[..., 1]


def _golden_channel_specs(channels: list[str]) -> tuple[ChannelSpec, ...]:
    return tuple(
        ChannelSpec(
            channel_id=channel,
            logical_polarization=LogicalPolarization.HH,
            s_parameter=SParameter.S11,
            display_name=channel,
        )
        for channel in channels
    )


# ---------------------------------------------------------------------------
# Golden vectors
# ---------------------------------------------------------------------------


class TestGoldenVectors:
    def test_manifest_has_four_synthetic_vectors(self) -> None:
        manifest = load_golden_manifest()
        vectors = manifest["vectors"]
        assert isinstance(vectors, list)
        assert len(vectors) == 4
        assert manifest["format_name"] == "rcscan-raw-trace-hash"
        assert manifest["algorithm"] == "sha256"
        assert manifest["magic"] == "UAVGPR-RAW-SHA256"
        assert manifest["hash_version"] == 1
        assert manifest["spec_version"] == 1

    def test_single_channel_4pt(self) -> None:
        vector = load_golden_manifest()["vectors"][0]
        digest = compute_raw_trace_sha256(
            mission_id=vector["mission_id"],
            trace_index=vector["trace_index"],
            trace_uid=vector["trace_uid"],
            channels=_golden_channel_specs(vector["channels"]),
            frequencies_hz=vector["frequencies_hz"],
            data=_as_complex(vector["data"]),
        )
        assert digest == vector["expected_sha256"]

    def test_dual_channel_16pt(self) -> None:
        vector = load_golden_manifest()["vectors"][1]
        digest = compute_raw_trace_sha256(
            mission_id=vector["mission_id"],
            trace_index=vector["trace_index"],
            trace_uid=vector["trace_uid"],
            channels=_golden_channel_specs(vector["channels"]),
            frequencies_hz=vector["frequencies_hz"],
            data=_as_complex(vector["data"]),
        )
        assert digest == vector["expected_sha256"]

    def test_single_channel_boundary(self) -> None:
        vector = load_golden_manifest()["vectors"][2]
        digest = compute_raw_trace_sha256(
            mission_id=vector["mission_id"],
            trace_index=vector["trace_index"],
            trace_uid=vector["trace_uid"],
            channels=_golden_channel_specs(vector["channels"]),
            frequencies_hz=vector["frequencies_hz"],
            data=_as_complex(vector["data"]),
        )
        assert digest == vector["expected_sha256"]

    def test_dual_channel_nonuniform(self) -> None:
        vector = load_golden_manifest()["vectors"][3]
        digest = compute_raw_trace_sha256(
            mission_id=vector["mission_id"],
            trace_index=vector["trace_index"],
            trace_uid=vector["trace_uid"],
            channels=_golden_channel_specs(vector["channels"]),
            frequencies_hz=vector["frequencies_hz"],
            data=_as_complex(vector["data"]),
        )
        assert digest == vector["expected_sha256"]

    def test_golden_digests_are_64_lowercase_hex(self) -> None:
        for vector in load_golden_manifest()["vectors"]:
            expected = vector["expected_sha256"]
            assert len(expected) == 64
            assert expected == expected.lower()


# ---------------------------------------------------------------------------
# Layout and byte-order equivalence
# ---------------------------------------------------------------------------


class TestLayoutEquivalence:
    def test_c_order_and_fortran_order_raw_produce_same_digest(self) -> None:
        axis = np.linspace(800e6, 2600e6, 16)
        raw = np.arange(32, dtype=np.complex128).reshape(2, 16)
        c_order = np.ascontiguousarray(raw)
        f_order = np.asfortranarray(raw)
        assert c_order.flags["C_CONTIGUOUS"]
        assert f_order.flags["F_CONTIGUOUS"]
        digest_c = compute_raw_trace_sha256(
            mission_id=_MISSION_ID,
            trace_index=7,
            trace_uid=_TRACE_UID,
            channels=(HH_S11, VV_S22),
            frequencies_hz=axis,
            data=c_order,
        )
        digest_f = compute_raw_trace_sha256(
            mission_id=_MISSION_ID,
            trace_index=7,
            trace_uid=_TRACE_UID,
            channels=(HH_S11, VV_S22),
            frequencies_hz=axis,
            data=f_order,
        )
        assert digest_c == digest_f

    def test_little_and_big_endian_inputs_produce_same_digest(self) -> None:
        axis = np.linspace(1.0e9, 2.0e9, 8)
        raw = (np.arange(16, dtype=np.complex128) + 1j).reshape(2, 8)
        digest_le = compute_raw_trace_sha256(
            mission_id=_MISSION_ID,
            trace_index=0,
            trace_uid=_TRACE_UID,
            channels=(HH_S11, VV_S22),
            frequencies_hz=axis.astype("<f8"),
            data=raw.astype("<c16"),
        )
        digest_be = compute_raw_trace_sha256(
            mission_id=_MISSION_ID,
            trace_index=0,
            trace_uid=_TRACE_UID,
            channels=(HH_S11, VV_S22),
            frequencies_hz=axis.astype(">f8"),
            data=raw.astype(">c16"),
        )
        assert digest_le == digest_be

    def test_integer_axis_and_raw_are_canonicalized(self) -> None:
        axis = np.array([100, 200, 300, 400], dtype=np.int64)
        raw = np.array([[1, 2, 3, 4]], dtype=np.int64)
        digest_int = compute_raw_trace_sha256(
            mission_id=_MISSION_ID,
            trace_index=0,
            trace_uid=_TRACE_UID,
            channels=(HH_S11,),
            frequencies_hz=axis,
            data=raw,
        )
        digest_float = compute_raw_trace_sha256(
            mission_id=_MISSION_ID,
            trace_index=0,
            trace_uid=_TRACE_UID,
            channels=(HH_S11,),
            frequencies_hz=np.asarray(axis, dtype="<f8"),
            data=np.asarray(raw, dtype="<c16"),
        )
        assert digest_int == digest_float


# ---------------------------------------------------------------------------
# Field-change sensitivity
# ---------------------------------------------------------------------------


class TestFieldSensitivity:
    @pytest.fixture()
    def base_digest(self) -> str:
        axis = np.linspace(1.0e9, 2.0e9, 8)
        raw = (np.arange(16, dtype=np.complex128) + 1j).reshape(2, 8)
        return compute_raw_trace_sha256(
            mission_id=_MISSION_ID,
            trace_index=0,
            trace_uid=_TRACE_UID,
            channels=(HH_S11, VV_S22),
            frequencies_hz=axis,
            data=raw,
        )

    def test_mission_id_change_changes_digest(self, base_digest: str) -> None:
        digest = compute_raw_trace_sha256(
            mission_id="11111111-2222-4333-8444-555555555555",
            trace_index=0,
            trace_uid=_TRACE_UID,
            channels=(HH_S11, VV_S22),
            frequencies_hz=np.linspace(1.0e9, 2.0e9, 8),
            data=(np.arange(16, dtype=np.complex128) + 1j).reshape(2, 8),
        )
        assert digest != base_digest

    def test_trace_index_change_changes_digest(self, base_digest: str) -> None:
        digest = compute_raw_trace_sha256(
            mission_id=_MISSION_ID,
            trace_index=1,
            trace_uid=_TRACE_UID,
            channels=(HH_S11, VV_S22),
            frequencies_hz=np.linspace(1.0e9, 2.0e9, 8),
            data=(np.arange(16, dtype=np.complex128) + 1j).reshape(2, 8),
        )
        assert digest != base_digest

    def test_trace_uid_change_changes_digest(self, base_digest: str) -> None:
        digest = compute_raw_trace_sha256(
            mission_id=_MISSION_ID,
            trace_index=0,
            trace_uid="bbbbbbbb-cccc-4ddd-8eee-ffffffffffff",
            channels=(HH_S11, VV_S22),
            frequencies_hz=np.linspace(1.0e9, 2.0e9, 8),
            data=(np.arange(16, dtype=np.complex128) + 1j).reshape(2, 8),
        )
        assert digest != base_digest

    def test_channel_order_change_changes_digest(self, base_digest: str) -> None:
        digest = compute_raw_trace_sha256(
            mission_id=_MISSION_ID,
            trace_index=0,
            trace_uid=_TRACE_UID,
            channels=(VV_S22, HH_S11),
            frequencies_hz=np.linspace(1.0e9, 2.0e9, 8),
            data=(np.arange(16, dtype=np.complex128) + 1j).reshape(2, 8),
        )
        assert digest != base_digest

    def test_axis_value_change_changes_digest(self, base_digest: str) -> None:
        axis = np.linspace(1.0e9, 2.0e9, 8)
        axis[3] += 12345.0
        digest = compute_raw_trace_sha256(
            mission_id=_MISSION_ID,
            trace_index=0,
            trace_uid=_TRACE_UID,
            channels=(HH_S11, VV_S22),
            frequencies_hz=axis,
            data=(np.arange(16, dtype=np.complex128) + 1j).reshape(2, 8),
        )
        assert digest != base_digest

    def test_raw_value_change_changes_digest(self, base_digest: str) -> None:
        raw = (np.arange(16, dtype=np.complex128) + 1j).reshape(2, 8)
        raw[0, 0] += 1.0
        digest = compute_raw_trace_sha256(
            mission_id=_MISSION_ID,
            trace_index=0,
            trace_uid=_TRACE_UID,
            channels=(HH_S11, VV_S22),
            frequencies_hz=np.linspace(1.0e9, 2.0e9, 8),
            data=raw,
        )
        assert digest != base_digest

    def test_channel_id_content_change_changes_digest(self, base_digest: str) -> None:
        # P3-01: same channel ORDER, different channel_id content.  Only the
        # second channel's id changes ("vv_s22" -> "vv_s21").
        digest = compute_raw_trace_sha256(
            mission_id=_MISSION_ID,
            trace_index=0,
            trace_uid=_TRACE_UID,
            channels=(
                HH_S11,
                ChannelSpec(
                    channel_id="vv_s21",
                    logical_polarization=LogicalPolarization.VV,
                    s_parameter=SParameter.S22,
                    display_name="VV S22",
                ),
            ),
            frequencies_hz=np.linspace(1.0e9, 2.0e9, 8),
            data=(np.arange(16, dtype=np.complex128) + 1j).reshape(2, 8),
        )
        assert digest != base_digest

    def test_frequency_point_count_change_changes_digest(
        self, base_digest: str
    ) -> None:
        # P3-01: 8 -> 9 frequency points (raw shape follows: (2, 8) -> (2, 9)).
        digest = compute_raw_trace_sha256(
            mission_id=_MISSION_ID,
            trace_index=0,
            trace_uid=_TRACE_UID,
            channels=(HH_S11, VV_S22),
            frequencies_hz=np.linspace(1.0e9, 2.0e9, 9),
            data=(np.arange(18, dtype=np.complex128) + 1j).reshape(2, 9),
        )
        assert digest != base_digest

    def test_raw_imaginary_value_change_changes_digest(
        self, base_digest: str
    ) -> None:
        # P3-01: only the imaginary part of a single raw element changes.
        raw = (np.arange(16, dtype=np.complex128) + 1j).reshape(2, 8)
        raw[0, 0] += 1.0j
        digest = compute_raw_trace_sha256(
            mission_id=_MISSION_ID,
            trace_index=0,
            trace_uid=_TRACE_UID,
            channels=(HH_S11, VV_S22),
            frequencies_hz=np.linspace(1.0e9, 2.0e9, 8),
            data=raw,
        )
        assert digest != base_digest


# ---------------------------------------------------------------------------
# Framing ambiguity elimination
# ---------------------------------------------------------------------------


class TestFramingAmbiguity:
    def test_channel_split_ambiguity_is_eliminated(self) -> None:
        axis = np.linspace(1.0e9, 2.0e9, 4)
        raw = np.zeros((2, 4), dtype=np.complex128)
        ab_c = compute_raw_trace_sha256(
            mission_id=_MISSION_ID,
            trace_index=0,
            trace_uid=_TRACE_UID,
            channels=(
                ChannelSpec("ab", LogicalPolarization.HH, SParameter.S11, "ab"),
                ChannelSpec("c", LogicalPolarization.HH, SParameter.S11, "c"),
            ),
            frequencies_hz=axis,
            data=raw,
        )
        a_bc = compute_raw_trace_sha256(
            mission_id=_MISSION_ID,
            trace_index=0,
            trace_uid=_TRACE_UID,
            channels=(
                ChannelSpec("a", LogicalPolarization.HH, SParameter.S11, "a"),
                ChannelSpec("bc", LogicalPolarization.HH, SParameter.S11, "bc"),
            ),
            frequencies_hz=axis,
            data=raw,
        )
        assert ab_c != a_bc

    def test_mission_and_trace_uid_are_length_prefixed(self) -> None:
        # Two different (mission, uid) pairs whose naive concatenation would
        # collide must still produce distinct digests under length framing.
        axis = np.linspace(1.0e9, 2.0e9, 4)
        raw = np.zeros((1, 4), dtype=np.complex128)
        first = compute_raw_trace_sha256(
            mission_id="11111111-2222-4333-8444-555555555555",
            trace_index=0,
            trace_uid="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            channels=(HH_S11,),
            frequencies_hz=axis,
            data=raw,
        )
        second = compute_raw_trace_sha256(
            mission_id="11111111-2222-4333-8444-555555555556",
            trace_index=0,
            trace_uid="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeef",
            channels=(HH_S11,),
            frequencies_hz=axis,
            data=raw,
        )
        assert first != second

    def test_framing_constant_surface(self) -> None:
        assert RAW_HASH_MAGIC == "UAVGPR-RAW-SHA256"
        assert RAW_HASH_VERSION == 1


# ---------------------------------------------------------------------------
# Fail-closed validation
# ---------------------------------------------------------------------------


class TestFailClosed:
    def test_non_canonical_mission_id_rejected(self) -> None:
        with pytest.raises(DomainError) as excinfo:
            compute_raw_trace_sha256(
                mission_id="NOT-A-UUID",
                trace_index=0,
                trace_uid=_TRACE_UID,
                channels=(HH_S11,),
                frequencies_hz=np.array([1.0e9, 2.0e9]),
                data=np.zeros((1, 2), dtype=np.complex128),
            )
        assert excinfo.value.code is ErrorCode.INVALID_UUID

    def test_non_canonical_trace_uid_rejected(self) -> None:
        with pytest.raises(DomainError) as excinfo:
            compute_raw_trace_sha256(
                mission_id=_MISSION_ID,
                trace_index=0,
                trace_uid="bad-uid",
                channels=(HH_S11,),
                frequencies_hz=np.array([1.0e9, 2.0e9]),
                data=np.zeros((1, 2), dtype=np.complex128),
            )
        assert excinfo.value.code is ErrorCode.INVALID_UUID

    def test_mission_id_wrong_type_rejected(self) -> None:
        with pytest.raises(DomainError) as excinfo:
            compute_raw_trace_sha256(
                mission_id=12345,  # type: ignore[arg-type]
                trace_index=0,
                trace_uid=_TRACE_UID,
                channels=(HH_S11,),
                frequencies_hz=np.array([1.0e9, 2.0e9]),
                data=np.zeros((1, 2), dtype=np.complex128),
            )
        assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT

    def test_trace_uid_wrong_type_rejected(self) -> None:
        with pytest.raises(DomainError) as excinfo:
            compute_raw_trace_sha256(
                mission_id=_MISSION_ID,
                trace_index=0,
                trace_uid=object(),  # type: ignore[arg-type]
                channels=(HH_S11,),
                frequencies_hz=np.array([1.0e9, 2.0e9]),
                data=np.zeros((1, 2), dtype=np.complex128),
            )
        assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT

    def test_negative_trace_index_rejected(self) -> None:
        with pytest.raises(DomainError) as excinfo:
            compute_raw_trace_sha256(
                mission_id=_MISSION_ID,
                trace_index=-1,
                trace_uid=_TRACE_UID,
                channels=(HH_S11,),
                frequencies_hz=np.array([1.0e9, 2.0e9]),
                data=np.zeros((1, 2), dtype=np.complex128),
            )
        assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT

    def test_bool_trace_index_rejected(self) -> None:
        with pytest.raises(DomainError):
            compute_raw_trace_sha256(
                mission_id=_MISSION_ID,
                trace_index=True,  # type: ignore[arg-type]
                trace_uid=_TRACE_UID,
                channels=(HH_S11,),
                frequencies_hz=np.array([1.0e9, 2.0e9]),
                data=np.zeros((1, 2), dtype=np.complex128),
            )

    def test_empty_channels_rejected(self) -> None:
        with pytest.raises(DomainError) as excinfo:
            compute_raw_trace_sha256(
                mission_id=_MISSION_ID,
                trace_index=0,
                trace_uid=_TRACE_UID,
                channels=(),
                frequencies_hz=np.array([1.0e9, 2.0e9]),
                data=np.zeros((1, 2), dtype=np.complex128),
            )
        assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT

    def test_duplicate_channel_ids_rejected(self) -> None:
        with pytest.raises(DomainError) as excinfo:
            compute_raw_trace_sha256(
                mission_id=_MISSION_ID,
                trace_index=0,
                trace_uid=_TRACE_UID,
                channels=(HH_S11, HH_S11),
                frequencies_hz=np.array([1.0e9, 2.0e9]),
                data=np.zeros((2, 2), dtype=np.complex128),
            )
        assert excinfo.value.code is ErrorCode.DUPLICATE_CHANNEL

    def test_non_channelspec_element_rejected(self) -> None:
        # P3-03: every validation path must raise a structured DomainError so
        # callers branch on ErrorCode instead of Python exception types.  The
        # offending element type must survive in the error context.
        with pytest.raises(DomainError) as excinfo:
            compute_raw_trace_sha256(
                mission_id=_MISSION_ID,
                trace_index=0,
                trace_uid=_TRACE_UID,
                channels=("hh_s11",),  # type: ignore[list-type]
                frequencies_hz=np.array([1.0e9, 2.0e9]),
                data=np.zeros((1, 2), dtype=np.complex128),
            )
        assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT
        assert excinfo.value.context["channel_type"] == "str"

    def test_empty_frequency_axis_rejected(self) -> None:
        with pytest.raises(DomainError):
            compute_raw_trace_sha256(
                mission_id=_MISSION_ID,
                trace_index=0,
                trace_uid=_TRACE_UID,
                channels=(HH_S11,),
                frequencies_hz=np.array([], dtype=np.float64),
                data=np.zeros((1, 0), dtype=np.complex128),
            )

    def test_2d_frequency_axis_rejected(self) -> None:
        with pytest.raises(DomainError) as excinfo:
            compute_raw_trace_sha256(
                mission_id=_MISSION_ID,
                trace_index=0,
                trace_uid=_TRACE_UID,
                channels=(HH_S11,),
                frequencies_hz=np.zeros((2, 2)),
                data=np.zeros((1, 2), dtype=np.complex128),
            )
        assert excinfo.value.code is ErrorCode.AXIS_MISMATCH

    def test_nan_frequency_axis_rejected(self) -> None:
        with pytest.raises(DomainError) as excinfo:
            compute_raw_trace_sha256(
                mission_id=_MISSION_ID,
                trace_index=0,
                trace_uid=_TRACE_UID,
                channels=(HH_S11,),
                frequencies_hz=np.array([1.0e9, np.nan]),
                data=np.zeros((1, 2), dtype=np.complex128),
            )
        assert excinfo.value.code is ErrorCode.NON_FINITE_AXIS

    def test_descending_frequency_axis_rejected(self) -> None:
        with pytest.raises(DomainError) as excinfo:
            compute_raw_trace_sha256(
                mission_id=_MISSION_ID,
                trace_index=0,
                trace_uid=_TRACE_UID,
                channels=(HH_S11,),
                frequencies_hz=np.array([2.0e9, 1.0e9]),
                data=np.zeros((1, 2), dtype=np.complex128),
            )
        assert excinfo.value.code is ErrorCode.NON_INCREASING_AXIS

    def test_unsigned_descending_axis_rejected(self) -> None:
        # P1-01: np.diff on the original unsigned dtype underflows (2-1 wraps
        # to 2**64-1) and must NOT bypass the strictly-increasing check.
        axis = np.array([2, 1], dtype=np.uint64)
        axis_before = axis.copy()
        with pytest.raises(DomainError) as excinfo:
            compute_raw_trace_sha256(
                mission_id=_MISSION_ID,
                trace_index=0,
                trace_uid=_TRACE_UID,
                channels=(HH_S11,),
                frequencies_hz=axis,
                data=np.zeros((1, 2), dtype=np.complex128),
            )
        assert excinfo.value.code is ErrorCode.NON_INCREASING_AXIS
        np.testing.assert_array_equal(axis, axis_before)

    def test_unsigned_extreme_descending_axis_rejected(self) -> None:
        # Values near 2**64-1 collapse to the same float64 after canonical
        # conversion; the axis is not strictly increasing and must be rejected.
        with pytest.raises(DomainError) as excinfo:
            compute_raw_trace_sha256(
                mission_id=_MISSION_ID,
                trace_index=0,
                trace_uid=_TRACE_UID,
                channels=(HH_S11,),
                frequencies_hz=np.array([2**64 - 2, 2**64 - 1], dtype=np.uint64),
                data=np.zeros((1, 2), dtype=np.complex128),
            )
        assert excinfo.value.code is ErrorCode.NON_INCREASING_AXIS

    def test_signed_overflow_descending_axis_rejected(self) -> None:
        # P1-01: int64 np.diff of (2**63-1, -2**63) wraps to a positive value;
        # validation on the canonical float64 values must reject the descent.
        with pytest.raises(DomainError) as excinfo:
            compute_raw_trace_sha256(
                mission_id=_MISSION_ID,
                trace_index=0,
                trace_uid=_TRACE_UID,
                channels=(HH_S11,),
                frequencies_hz=np.array([2**63 - 1, -(2**63)], dtype=np.int64),
                data=np.zeros((1, 2), dtype=np.complex128),
            )
        assert excinfo.value.code is ErrorCode.NON_INCREASING_AXIS

    def test_conversion_collapse_axis_rejected(self) -> None:
        # P1-01: 2**53 and 2**53+1 both convert to the same float64; the
        # canonical axis is flat, so it is not strictly increasing.
        with pytest.raises(DomainError) as excinfo:
            compute_raw_trace_sha256(
                mission_id=_MISSION_ID,
                trace_index=0,
                trace_uid=_TRACE_UID,
                channels=(HH_S11,),
                frequencies_hz=np.array([2**53, 2**53 + 1], dtype=np.int64),
                data=np.zeros((1, 2), dtype=np.complex128),
            )
        assert excinfo.value.code is ErrorCode.NON_INCREASING_AXIS

    def test_trace_index_int64_bound_rejected(self) -> None:
        # P2-01: trace_index must fit the ISSUE-008 <i8 storage column
        # (signed 64-bit), so the domain bound is 0 <= v < 2**63.
        with pytest.raises(DomainError) as excinfo:
            compute_raw_trace_sha256(
                mission_id=_MISSION_ID,
                trace_index=2**63,
                trace_uid=_TRACE_UID,
                channels=(HH_S11,),
                frequencies_hz=np.array([1.0e9, 2.0e9]),
                data=np.zeros((1, 2), dtype=np.complex128),
            )
        assert excinfo.value.code is ErrorCode.OUT_OF_RANGE

    def test_trace_index_uint64_overflow_rejected(self) -> None:
        # P2-01: 2**64 no longer reaches struct.pack(">Q") as struct.error;
        # it must fail at the domain boundary with a stable code.
        with pytest.raises(DomainError) as excinfo:
            compute_raw_trace_sha256(
                mission_id=_MISSION_ID,
                trace_index=2**64,
                trace_uid=_TRACE_UID,
                channels=(HH_S11,),
                frequencies_hz=np.array([1.0e9, 2.0e9]),
                data=np.zeros((1, 2), dtype=np.complex128),
            )
        assert excinfo.value.code is ErrorCode.OUT_OF_RANGE

    def test_trace_index_int64_max_accepted(self) -> None:
        digest = compute_raw_trace_sha256(
            mission_id=_MISSION_ID,
            trace_index=2**63 - 1,
            trace_uid=_TRACE_UID,
            channels=(HH_S11,),
            frequencies_hz=np.array([1.0e9, 2.0e9]),
            data=np.zeros((1, 2), dtype=np.complex128),
        )
        assert isinstance(digest, str) and len(digest) == 64

    def test_non_numeric_raw_dtype_rejected(self) -> None:
        with pytest.raises(DomainError) as excinfo:
            compute_raw_trace_sha256(
                mission_id=_MISSION_ID,
                trace_index=0,
                trace_uid=_TRACE_UID,
                channels=(HH_S11,),
                frequencies_hz=np.array([1.0e9, 2.0e9]),
                data=np.array([["a", "b"]], dtype=object),
            )
        assert excinfo.value.code is ErrorCode.DTYPE_MISMATCH

    def test_wrong_raw_shape_rejected(self) -> None:
        with pytest.raises(DomainError) as excinfo:
            compute_raw_trace_sha256(
                mission_id=_MISSION_ID,
                trace_index=0,
                trace_uid=_TRACE_UID,
                channels=(HH_S11,),
                frequencies_hz=np.array([1.0e9, 2.0e9]),
                data=np.zeros((2, 2), dtype=np.complex128),
            )
        assert excinfo.value.code is ErrorCode.SHAPE_MISMATCH

    def test_1d_raw_rejected(self) -> None:
        with pytest.raises(DomainError) as excinfo:
            compute_raw_trace_sha256(
                mission_id=_MISSION_ID,
                trace_index=0,
                trace_uid=_TRACE_UID,
                channels=(HH_S11,),
                frequencies_hz=np.array([1.0e9, 2.0e9]),
                data=np.zeros(2, dtype=np.complex128),
            )
        assert excinfo.value.code is ErrorCode.SHAPE_MISMATCH


# ---------------------------------------------------------------------------
# GNSS exclusion
# ---------------------------------------------------------------------------


class TestGnssExclusion:
    def test_gnss_never_enters_the_raw_hash(self) -> None:
        # The public signature has no GNSS parameter at all; two traces with
        # identical identity/axis/channel/raw but different GNSS availability
        # must produce the same digest (GNSS is not part of the framing).
        axis = np.linspace(1.0e9, 2.0e9, 4)
        raw = np.zeros((1, 4), dtype=np.complex128)
        digest = compute_raw_trace_sha256(
            mission_id=_MISSION_ID,
            trace_index=0,
            trace_uid=_TRACE_UID,
            channels=(HH_S11,),
            frequencies_hz=axis,
            data=raw,
        )
        assert isinstance(digest, str)
        assert len(digest) == 64

    def test_gnss_documented_exclusion(self) -> None:
        # GNSS never enters the raw hash: no vector carries GNSS fields and
        # the generator note declares "no GNSS".
        manifest = load_golden_manifest()
        assert all("gnss" not in vector for vector in manifest["vectors"])
        assert "no GNSS" in str(manifest["generator"]["note"])


# ---------------------------------------------------------------------------
# Input immutability
# ---------------------------------------------------------------------------


class TestInputImmutability:
    def test_compute_does_not_mutate_axis_or_raw(self) -> None:
        axis = np.linspace(1.0e9, 2.0e9, 8)
        raw = (np.arange(16, dtype=np.complex128) + 1j).reshape(2, 8)
        axis_before = axis.copy()
        raw_before = raw.copy()
        compute_raw_trace_sha256(
            mission_id=_MISSION_ID,
            trace_index=0,
            trace_uid=_TRACE_UID,
            channels=(HH_S11, VV_S22),
            frequencies_hz=axis,
            data=raw,
        )
        np.testing.assert_array_equal(axis, axis_before)
        np.testing.assert_array_equal(raw, raw_before)

    def test_spec_compute_does_not_mutate_inputs(self) -> None:
        axis = np.linspace(1.0e9, 2.0e9, 8)
        raw = (np.arange(16, dtype=np.complex128) + 1j).reshape(2, 8)
        axis_before = axis.copy()
        raw_before = raw.copy()
        spec = RawHashSpec(
            mission_id=_MISSION_ID,
            trace_index=0,
            trace_uid=_TRACE_UID,
            channels=(HH_S11, VV_S22),
            frequencies_hz=axis,
            data=raw,
        )
        spec.compute()
        np.testing.assert_array_equal(axis, axis_before)
        np.testing.assert_array_equal(raw, raw_before)


# ---------------------------------------------------------------------------
# P1-02: RawHashSpec owns an immutable, source-isolated snapshot
# ---------------------------------------------------------------------------


class TestRawHashSpecImmutability:
    def test_frequencies_hz_and_data_are_owned_read_only_snapshots(self) -> None:
        axis = np.array([1.0e9, 2.0e9, 3.0e9])
        raw = np.zeros((1, 3), dtype=np.complex128)
        spec = RawHashSpec(
            mission_id=_MISSION_ID,
            trace_index=0,
            trace_uid=_TRACE_UID,
            channels=(HH_S11,),
            frequencies_hz=axis,
            data=raw,
        )
        assert not np.shares_memory(spec.frequencies_hz, axis)
        assert not np.shares_memory(spec.data, raw)
        assert spec.frequencies_hz.flags.writeable is False
        assert spec.data.flags.writeable is False

    def test_source_mutation_does_not_change_digest_or_hash(self) -> None:
        axis = np.array([1.0e9, 2.0e9, 3.0e9])
        raw = np.zeros((1, 3), dtype=np.complex128)
        spec = RawHashSpec(
            mission_id=_MISSION_ID,
            trace_index=0,
            trace_uid=_TRACE_UID,
            channels=(HH_S11,),
            frequencies_hz=axis,
            data=raw,
        )
        digest_before = spec.compute()
        hash_before = hash(spec)
        axis[0] = 1.5e9
        raw[0, 1] = 7.0 + 3.0j
        assert spec.compute() == digest_before
        assert hash(spec) == hash_before

    def test_direct_write_to_spec_arrays_rejected(self) -> None:
        spec = RawHashSpec(
            mission_id=_MISSION_ID,
            trace_index=0,
            trace_uid=_TRACE_UID,
            channels=(HH_S11,),
            frequencies_hz=np.array([1.0e9, 2.0e9]),
            data=np.zeros((1, 2), dtype=np.complex128),
        )
        with pytest.raises(ValueError):
            spec.frequencies_hz[0] = 3.0e9
        with pytest.raises(ValueError):
            spec.data[0, 0] = 1.0 + 2.0j

    def test_setflags_write_attack_rejected(self) -> None:
        spec = RawHashSpec(
            mission_id=_MISSION_ID,
            trace_index=0,
            trace_uid=_TRACE_UID,
            channels=(HH_S11,),
            frequencies_hz=np.array([1.0e9, 2.0e9]),
            data=np.zeros((1, 2), dtype=np.complex128),
        )
        with pytest.raises(ValueError):
            spec.frequencies_hz.setflags(write=True)
        with pytest.raises(ValueError):
            spec.data.setflags(write=True)


# ---------------------------------------------------------------------------
# validate_raw_hash and ISSUE-008 column compatibility
# ---------------------------------------------------------------------------


class TestHashValidation:
    def test_valid_hash_round_trips(self) -> None:
        digest = compute_raw_trace_sha256(
            mission_id=_MISSION_ID,
            trace_index=0,
            trace_uid=_TRACE_UID,
            channels=(HH_S11,),
            frequencies_hz=np.array([1.0e9, 2.0e9]),
            data=np.zeros((1, 2), dtype=np.complex128),
        )
        assert validate_raw_hash(digest) == digest

    def test_uppercase_hash_rejected(self) -> None:
        digest = compute_raw_trace_sha256(
            mission_id=_MISSION_ID,
            trace_index=0,
            trace_uid=_TRACE_UID,
            channels=(HH_S11,),
            frequencies_hz=np.array([1.0e9, 2.0e9]),
            data=np.zeros((1, 2), dtype=np.complex128),
        )
        with pytest.raises(DomainError):
            validate_raw_hash(digest.upper())

    def test_short_hash_rejected(self) -> None:
        with pytest.raises(DomainError):
            validate_raw_hash("abc123")

    def test_non_string_hash_rejected(self) -> None:
        with pytest.raises(DomainError):
            validate_raw_hash(123)  # type: ignore[arg-type]

    def test_digest_matches_trace_metadata_field_contract(self) -> None:
        # ISSUE-008 freezes a 64-byte fixed ASCII column; the digest must fit.
        digest = compute_raw_trace_sha256(
            mission_id=_MISSION_ID,
            trace_index=0,
            trace_uid=_TRACE_UID,
            channels=(HH_S11,),
            frequencies_hz=np.array([1.0e9, 2.0e9]),
            data=np.zeros((1, 2), dtype=np.complex128),
        )
        encoded = digest.encode("ascii")
        assert len(encoded) == 64
        encoded.decode("ascii")  # must be pure ASCII


# ---------------------------------------------------------------------------
# RawHashSpec JSON round trip
# ---------------------------------------------------------------------------


class TestRawHashSpec:
    def test_to_dict_from_dict_round_trip(self) -> None:
        axis = np.linspace(1.0e9, 2.0e9, 4)
        raw = (np.arange(8, dtype=np.complex128) + 1j).reshape(2, 4)
        spec = RawHashSpec(
            mission_id=_MISSION_ID,
            trace_index=0,
            trace_uid=_TRACE_UID,
            channels=(HH_S11, VV_S22),
            frequencies_hz=axis,
            data=raw,
        )
        restored = RawHashSpec.from_dict(spec.to_dict())
        assert restored == spec
        assert restored.compute() == spec.compute()

    def test_spec_compute_matches_direct_function(self) -> None:
        axis = np.linspace(1.0e9, 2.0e9, 4)
        raw = (np.arange(8, dtype=np.complex128) + 1j).reshape(2, 4)
        spec = RawHashSpec(
            mission_id=_MISSION_ID,
            trace_index=0,
            trace_uid=_TRACE_UID,
            channels=(HH_S11, VV_S22),
            frequencies_hz=axis,
            data=raw,
        )
        direct = compute_raw_trace_sha256(
            mission_id=_MISSION_ID,
            trace_index=0,
            trace_uid=_TRACE_UID,
            channels=(HH_S11, VV_S22),
            frequencies_hz=axis,
            data=raw,
        )
        assert spec.compute() == direct

    def test_spec_json_is_plain_json_serializable(self) -> None:
        axis = np.linspace(1.0e9, 2.0e9, 4)
        raw = (np.arange(8, dtype=np.complex128) + 1j).reshape(2, 4)
        spec = RawHashSpec(
            mission_id=_MISSION_ID,
            trace_index=0,
            trace_uid=_TRACE_UID,
            channels=(HH_S11, VV_S22),
            frequencies_hz=axis,
            data=raw,
        )
        payload = spec.to_dict()
        encoded = json.dumps(payload)
        decoded = json.loads(encoded)
        assert decoded["mission_id"] == _MISSION_ID.to_json()
        assert decoded["data_shape"] == [2, 4]

    def test_spec_frozen_attributes(self) -> None:
        import dataclasses

        spec = RawHashSpec(
            mission_id=_MISSION_ID,
            trace_index=0,
            trace_uid=_TRACE_UID,
            channels=(HH_S11,),
            frequencies_hz=np.array([1.0e9, 2.0e9]),
            data=np.zeros((1, 2), dtype=np.complex128),
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            spec.trace_index = 5  # type: ignore[misc]


# ---------------------------------------------------------------------------
# P2-02: RawHashSpec JSON is strict (exact key sets, v1-only versions)
# ---------------------------------------------------------------------------


class TestHashMetadata:
    def _spec_payload(self) -> dict[str, object]:
        axis = np.array([1.0e9, 2.0e9, 3.0e9])
        raw = np.zeros((1, 3), dtype=np.complex128)
        spec = RawHashSpec(
            mission_id=_MISSION_ID,
            trace_index=0,
            trace_uid=_TRACE_UID,
            channels=(HH_S11,),
            frequencies_hz=axis,
            data=raw,
        )
        return spec.to_dict()

    def test_round_trip_carries_spec_and_hash_version(self) -> None:
        payload = self._spec_payload()
        assert payload["spec_version"] == 1
        assert payload["hash_version"] == 1
        restored = RawHashSpec.from_dict(payload)
        assert restored.compute() == RawHashSpec.from_dict(
            self._spec_payload()
        ).compute()

    def test_unknown_top_level_key_rejected(self) -> None:
        payload = self._spec_payload()
        payload["unexpected"] = "silently ignored"
        with pytest.raises(DomainError) as excinfo:
            RawHashSpec.from_dict(payload)
        assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT

    def test_missing_required_key_rejected(self) -> None:
        payload = self._spec_payload()
        del payload["mission_id"]
        with pytest.raises(DomainError) as excinfo:
            RawHashSpec.from_dict(payload)
        assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT

    def test_unknown_channel_key_rejected(self) -> None:
        payload = self._spec_payload()
        assert isinstance(payload["channels"], list)
        channel = payload["channels"][0]
        assert isinstance(channel, dict)
        channel["extra"] = "ignored"
        with pytest.raises(DomainError) as excinfo:
            RawHashSpec.from_dict(payload)
        assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT

    def test_missing_channel_key_rejected(self) -> None:
        payload = self._spec_payload()
        assert isinstance(payload["channels"], list)
        channel = payload["channels"][0]
        assert isinstance(channel, dict)
        del channel["antenna_note"]
        with pytest.raises(DomainError) as excinfo:
            RawHashSpec.from_dict(payload)
        assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT

    def test_data_shape_non_int_rejected(self) -> None:
        for bad in ([2.0, 4], [True, 4], [2, 4.0]):
            payload = self._spec_payload()
            payload["data_shape"] = bad
            with pytest.raises(DomainError) as excinfo:
                RawHashSpec.from_dict(payload)
            assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT

    def test_data_shape_product_mismatch_rejected(self) -> None:
        payload = self._spec_payload()
        payload["data_shape"] = [2, 4]
        with pytest.raises(DomainError) as excinfo:
            RawHashSpec.from_dict(payload)
        assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT

    def test_unknown_spec_version_rejected(self) -> None:
        payload = self._spec_payload()
        payload["spec_version"] = 2
        with pytest.raises(DomainError) as excinfo:
            RawHashSpec.from_dict(payload)
        assert excinfo.value.code is ErrorCode.UNSUPPORTED_SCHEMA_VERSION

    def test_unknown_hash_version_rejected(self) -> None:
        payload = self._spec_payload()
        payload["hash_version"] = 2
        with pytest.raises(DomainError) as excinfo:
            RawHashSpec.from_dict(payload)
        assert excinfo.value.code is ErrorCode.UNSUPPORTED_SCHEMA_VERSION

    def test_missing_version_fields_rejected(self) -> None:
        payload = self._spec_payload()
        del payload["spec_version"]
        with pytest.raises(DomainError) as excinfo:
            RawHashSpec.from_dict(payload)
        assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT

    def test_wrong_type_version_fields_rejected(self) -> None:
        payload = self._spec_payload()
        payload["spec_version"] = "1"
        with pytest.raises(DomainError) as excinfo:
            RawHashSpec.from_dict(payload)
        assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT

    def test_non_numeric_frequency_entries_rejected(self) -> None:
        payload = self._spec_payload()
        payload["frequencies_hz"] = ["1e9", "2e9"]
        with pytest.raises(DomainError) as excinfo:
            RawHashSpec.from_dict(payload)
        assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT

    def test_non_numeric_data_pair_rejected(self) -> None:
        payload = self._spec_payload()
        assert isinstance(payload["data"], list)
        payload["data"][0] = ["1", 0]
        with pytest.raises(DomainError) as excinfo:
            RawHashSpec.from_dict(payload)
        assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT

    def test_trace_index_bool_in_payload_rejected(self) -> None:
        payload = self._spec_payload()
        payload["trace_index"] = True
        with pytest.raises(DomainError) as excinfo:
            RawHashSpec.from_dict(payload)
        assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT
