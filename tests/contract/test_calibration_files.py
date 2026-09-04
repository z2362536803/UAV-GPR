"""Contract tests for versioned .rcal/.rcbg storage and compatibility (ISSUE-029).

Covers the T1-T10 matrix from docs/plans/2026-09-02-issue-029-rcal-storage.md:
round-trip bit fidelity (values/metadata/digest), deterministic canonical
bytes, digest tamper rejection, unknown format/schema fail-closed, corrupted
JSON rejection, double-channel order mismatch as a hard incompatibility,
near-miss frequency axes as hard mismatches, raw vs osl_calibrated domain and
profile-id binding rules, quality-anomaly rejection on both writer and
reader sides, and the field-level compatibility verdicts
(compatible / compatible_with_warnings / incompatible) including the rule
that reading or selecting a file never enables anything.  All data is
deterministic (seeded RNG); there are no sleeps; every artifact lives under
``tmp_path``.
"""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from uav_gpr.calibration.osl import build_osl_calibration
from uav_gpr.calibration.reference import AirBackgroundReference, ReferenceDomain
from uav_gpr.core import (
    CalibrationProfileId,
    ChannelSpec,
    DeviceId,
    DomainError,
    ErrorCode,
    LogicalPolarization,
    SParameter,
)
from uav_gpr.storage.calibration_files import (
    BACKGROUND_FORMAT_NAME,
    CALIBRATION_FORMAT_NAME,
    SCHEMA_VERSION,
    AirBackgroundFilePayload,
    CompatibilityContext,
    CompatibilityVerdict,
    OslCalibrationFilePayload,
    StoredOslProfile,
    WriteConflictError,
    check_air_background_compatibility,
    check_osl_compatibility,
    read_air_background_file,
    read_osl_calibration_file,
    write_air_background_file,
    write_osl_calibration_file,
)

CREATED_UTC = datetime(2026, 1, 1, tzinfo=UTC)
DEVICE = DeviceId("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
CONFIG_SHA = "a" * 64

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


def make_axis(points: int = 7) -> np.ndarray:
    return np.linspace(1.0e9, 2.0e9, points, dtype=np.float64)


def _captures(seed: int, axis: np.ndarray, count: int = 3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    clean = rng.standard_normal((count, axis.size)) + 1j * rng.standard_normal(
        (count, axis.size)
    )
    # keep |gamma| well inside the unit disk so the solve stays non-degenerate
    return clean / (8.0 + abs(clean.imag))


def make_profile(channel: ChannelSpec = HH_S11, seed: int = 1) -> object:
    axis = make_axis()
    return build_osl_calibration(
        channel=channel,
        frequency_hz=axis,
        open_measured=_captures(seed, axis),
        short_measured=_captures(seed + 1, axis),
        load_measured=_captures(seed + 2, axis),
    )


def make_osl_payload(channels: tuple[ChannelSpec, ...] = (HH_S11, VV_S22)) -> object:
    profiles = tuple(
        StoredOslProfile.from_profile(make_profile(channel, seed=5 + 2 * index))
        for index, channel in enumerate(channels)
    )
    return OslCalibrationFilePayload(
        profiles=profiles,
        created_utc=CREATED_UTC,
        software_version="0.1.0.dev0",
        device_id=DEVICE,
        config_sha256=CONFIG_SHA,
    )


def make_background(
    channels: tuple[ChannelSpec, ...] = (HH_S11, VV_S22),
    *,
    domain: ReferenceDomain = ReferenceDomain.RAW,
    calibration_profile_id: CalibrationProfileId | None = None,
    points: int = 7,
) -> AirBackgroundReference:
    rng = np.random.default_rng(7)
    shape = (len(channels), points)
    data = rng.standard_normal(shape) + 1j * rng.standard_normal(shape)
    return AirBackgroundReference(
        channels=channels,
        frequency_hz=make_axis(points),
        mean_data=data,
        trace_count=12,
        domain=domain,
        calibration_profile_id=calibration_profile_id,
    )


def make_bg_payload(**kwargs: object) -> object:
    reference = kwargs.pop("reference", None) or make_background()
    magnitude = np.abs(reference.mean_data)  # channel x frequency
    stability = magnitude - magnitude.mean(axis=0, keepdims=True)
    outliers = np.abs(reference.mean_data - reference.mean_data.mean(axis=0, keepdims=True))
    return AirBackgroundFilePayload(
        reference=reference,
        created_utc=CREATED_UTC,
        software_version="0.1.0.dev0",
        device_id=DEVICE,
        config_sha256=CONFIG_SHA,
        stability_mad_hz=np.round(np.abs(stability), 12),
        outlier_max_deviation=np.round(outliers, 12),
        non_finite_rejected_traces=0,
    )


def context_for(
    path: Path,
    *,
    now: datetime | None = None,
    environment_note: str | None = None,
    max_age_days: float | None = 30.0,
) -> CompatibilityContext:
    payload = (
        read_osl_calibration_file(path)
        if path.suffix == ".rcal"
        else read_air_background_file(path)
    )
    return CompatibilityContext.from_payload(
        payload,
        now=now or (datetime(2026, 1, 2, tzinfo=UTC) if max_age_days is not None else CREATED_UTC),
        environment_note=environment_note,
        max_age_days=max_age_days,
    )


@pytest.fixture()
def rcal_path(tmp_path: Path) -> Path:
    return tmp_path / "profile.rcal"


@pytest.fixture()
def rcbg_path(tmp_path: Path) -> Path:
    return tmp_path / "background.rcbg"


# ---------------------------------------------------------------------------
# T1/T2 round-trip fidelity and deterministic bytes
# ---------------------------------------------------------------------------


def test_rcal_round_trip_is_bit_exact(rcal_path: Path) -> None:
    payload = make_osl_payload()
    write_osl_calibration_file(payload, rcal_path)
    loaded = read_osl_calibration_file(rcal_path)
    assert loaded.digest == payload.digest
    assert len(loaded.profiles) == 2
    first, second = loaded.profiles
    assert first.channel == HH_S11
    assert second.channel == VV_S22
    source = payload.profiles[0]
    for name in (
        "frequency_hz",
        "open_measured_mean",
        "short_measured_mean",
        "load_measured_mean",
        "open_actual",
        "short_actual",
        "load_actual",
        "directivity",
        "reflection_tracking",
        "source_match",
    ):
        saved = getattr(source, name)
        restored = getattr(first, name)
        assert restored.dtype == np.complex128 or name == "frequency_hz"
        assert np.array_equal(saved.view(np.ndarray), restored.view(np.ndarray))
        assert restored.flags["WRITEABLE"] is False
    assert first.open_capture_count == source.open_capture_count
    assert first.quality == source.quality


def test_rcal_provenance_and_identity_survive(rcal_path: Path) -> None:
    payload = make_osl_payload()
    write_osl_calibration_file(payload, rcal_path)
    loaded = read_osl_calibration_file(rcal_path)
    assert loaded.created_utc == payload.created_utc
    assert loaded.software_version == payload.software_version
    assert loaded.device_id == payload.device_id
    assert loaded.config_sha256 == payload.config_sha256
    assert [p.profile_id for p in loaded.profiles] == [
        p.profile_id for p in payload.profiles
    ]
    # full structural equality of every persisted profile
    assert loaded.profiles == payload.profiles
    # auditable without the original session objects: channel/S-parameter bind
    assert [(p.channel.s_parameter, p.channel.channel_id) for p in loaded.profiles] == [
        (SParameter.S11, "hh_s11"),
        (SParameter.S22, "vv_s22"),
    ]


def test_rcbg_round_trip_is_bit_exact(rcbg_path: Path) -> None:
    payload = make_bg_payload()
    write_air_background_file(payload, rcbg_path)
    loaded = read_air_background_file(rcbg_path)
    assert loaded.digest == payload.digest
    src = payload.reference
    dst = loaded.reference
    assert np.array_equal(src.mean_data, dst.mean_data)
    assert dst.mean_data.flags["WRITEABLE"] is False
    assert np.array_equal(src.frequency_hz, dst.frequency_hz)
    assert dst.channels == src.channels
    assert dst.trace_count == 12
    assert dst.domain is ReferenceDomain.RAW
    assert loaded.non_finite_rejected_traces == 0
    assert np.array_equal(
        loaded.stability_mad_hz, np.asarray(payload.stability_mad_hz, dtype=np.float64)
    )


def test_rcbg_calibrated_domain_round_trip(rcbg_path: Path) -> None:
    profile_id = CalibrationProfileId.new()
    payload = make_bg_payload(
        reference=make_background(
            domain=ReferenceDomain.OSL_CALIBRATED,
            calibration_profile_id=profile_id,
        )
    )
    write_air_background_file(payload, rcbg_path)
    loaded = read_air_background_file(rcbg_path)
    assert loaded.reference.domain is ReferenceDomain.OSL_CALIBRATED
    assert loaded.reference.calibration_profile_id == profile_id


def test_writer_output_is_deterministic(tmp_path: Path) -> None:
    payload = make_osl_payload()
    first = tmp_path / "a.rcal"
    second = tmp_path / "b.rcal"
    write_osl_calibration_file(payload, first)
    write_osl_calibration_file(payload, second)
    assert first.read_bytes() == second.read_bytes()
    document = json.loads(first.read_text(encoding="utf-8"))
    assert document["format_name"] == CALIBRATION_FORMAT_NAME
    assert document["schema_version"] == SCHEMA_VERSION
    assert document["content_sha256"] == payload.digest


# ---------------------------------------------------------------------------
# T4 digest tampering and corruption
# ---------------------------------------------------------------------------


def _tamper(path: Path, mutate) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    mutate(document)
    path.write_text(json.dumps(document), encoding="utf-8")


def _first_directivity(doc: dict) -> dict:
    return doc["payload"]["profiles"][0]["error_terms"]["directivity"]


def test_digest_tamper_on_payload_is_rejected(rcal_path: Path) -> None:
    write_osl_calibration_file(make_osl_payload(), rcal_path)
    original = json.loads(rcal_path.read_text(encoding="utf-8"))

    def bump(doc: dict) -> None:
        _first_directivity(doc)["re"][0] += 1.0

    _tamper(rcal_path, bump)
    with pytest.raises(DomainError) as excinfo:
        read_osl_calibration_file(rcal_path)
    assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT
    assert "content_sha256" in str(excinfo.value.context.get("field", ""))
    assert excinfo.value.context.get("stored_digest") == original["content_sha256"]


def test_digest_field_tamper_is_rejected(rcbg_path: Path) -> None:
    write_air_background_file(make_bg_payload(), rcbg_path)

    def flip(doc: dict) -> None:
        digest = doc["content_sha256"]
        doc["content_sha256"] = ("0" if digest[0] != "0" else "1") + digest[1:]

    _tamper(rcbg_path, flip)
    with pytest.raises(DomainError) as excinfo:
        read_air_background_file(rcbg_path)
    assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT


def test_missing_digest_node_is_rejected(rcal_path: Path) -> None:
    write_osl_calibration_file(make_osl_payload(), rcal_path)
    _tamper(rcal_path, lambda doc: doc.pop("content_sha256"))
    with pytest.raises(DomainError) as excinfo:
        read_osl_calibration_file(rcal_path)
    assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT


def test_corrupted_json_is_rejected(rcal_path: Path) -> None:
    write_osl_calibration_file(make_osl_payload(), rcal_path)
    rcal_path.write_text('{"format_name": ', encoding="utf-8")
    with pytest.raises(DomainError) as excinfo:
        read_osl_calibration_file(rcal_path)
    assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT


def test_non_utf8_bytes_are_rejected(rcal_path: Path) -> None:
    write_osl_calibration_file(make_osl_payload(), rcal_path)
    rcal_path.write_bytes(b"\xff\xfe\x00garbage")
    with pytest.raises(DomainError):
        read_osl_calibration_file(rcal_path)


# ---------------------------------------------------------------------------
# F1 repair: out-of-range numeric literals must not leak bare ValueError
# ---------------------------------------------------------------------------


def _corrupt_with_literal(path: Path, literal: str) -> None:
    """Build a .rcal by hand from raw bytes (bypasses every writer guard).

    The out-of-range token is spliced into the serialized text so it appears
    as a genuine JSON number literal (``json.dumps`` would refuse inf).
    """
    document = {
        "format_name": CALIBRATION_FORMAT_NAME,
        "schema_version": SCHEMA_VERSION,
        "payload": {
            "profile_kind": "osl_set",
            "axis_unit": "Hz",
            "channels": [],
            "frequency_hz": {"dtype": "float64", "shape": [1], "re": [0.0]},
            "profiles": [],
            "provenance": {
                "created_utc": "2026-01-01T00:00:00.000000Z",
                "software_version": "0.1.0.dev0",
                "device_id": None,
                "config_sha256": None,
                "algorithm": "osl_one_port_v1",
            },
        },
        "content_sha256": "0" * 64,
    }
    text = json.dumps(document).replace('"re": [0.0]', f'"re": [{literal}]')
    assert literal in text and '"re": [0.0]' not in text
    path.write_text(text, encoding="utf-8")


@pytest.mark.parametrize("literal", ["1e999", "-1e999"])
def test_out_of_range_numeric_literal_is_domain_error(
    tmp_path: Path, literal: str
) -> None:
    broken = tmp_path / "corrupt.rcal"
    _corrupt_with_literal(broken, literal)
    with pytest.raises(DomainError) as excinfo:
        read_osl_calibration_file(broken)
    assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT
    context = excinfo.value.context
    assert context.get("kind") == "unparseable_numeric"
    assert isinstance(context.get("field"), str) and context["field"]


@pytest.mark.parametrize("literal", ["1e999", "-1e999"])
def test_out_of_range_literal_rejected_on_background_file(
    tmp_path: Path, literal: str
) -> None:
    broken = tmp_path / "corrupt.rcbg"
    _corrupt_with_literal(broken, literal)
    # the .rcbg reader rejects this file too (format mismatch or unparseable
    # numeric), but always as a DomainError — never a bare ValueError
    with pytest.raises(DomainError):
        read_air_background_file(broken)


# ---------------------------------------------------------------------------
# T3 unknown format / schema versions fail closed
# ---------------------------------------------------------------------------


def test_unknown_schema_version_is_rejected(rcal_path: Path) -> None:
    write_osl_calibration_file(make_osl_payload(), rcal_path)
    _tamper(rcal_path, lambda doc: doc.__setitem__("schema_version", 2))
    with pytest.raises(DomainError) as excinfo:
        read_osl_calibration_file(rcal_path)
    assert excinfo.value.code is ErrorCode.UNSUPPORTED_SCHEMA_VERSION
    assert excinfo.value.context.get("found") == 2


@pytest.mark.parametrize("bogus", [1.0, "1", True])
def test_non_integer_schema_version_forms_are_rejected(
    rcal_path: Path, bogus: object
) -> None:
    write_osl_calibration_file(make_osl_payload(), rcal_path)
    _tamper(rcal_path, lambda doc: doc.__setitem__("schema_version", bogus))
    with pytest.raises(DomainError) as excinfo:
        read_osl_calibration_file(rcal_path)
    assert excinfo.value.code is ErrorCode.UNSUPPORTED_SCHEMA_VERSION


def test_swapped_format_names_are_rejected(
    rcal_path: Path, rcbg_path: Path
) -> None:
    write_osl_calibration_file(make_osl_payload(), rcal_path)
    write_air_background_file(make_bg_payload(), rcbg_path)
    _tamper(
        rcal_path,
        lambda doc: doc.__setitem__("format_name", BACKGROUND_FORMAT_NAME),
    )
    _tamper(
        rcbg_path,
        lambda doc: doc.__setitem__("format_name", CALIBRATION_FORMAT_NAME),
    )
    with pytest.raises(DomainError) as excinfo:
        read_osl_calibration_file(rcal_path)
    assert excinfo.value.code is ErrorCode.UNSUPPORTED_SCHEMA_VERSION
    with pytest.raises(DomainError):
        read_air_background_file(rcbg_path)


# ---------------------------------------------------------------------------
# structural validation re-runs on read (digest-independent fields)
# ---------------------------------------------------------------------------


def test_module_respects_layering_boundary() -> None:
    # AGENTS.md §9: storage imports neither UI nor network; it consumes core
    # and calibration models only.
    import ast

    from uav_gpr.storage import calibration_files

    source = Path(calibration_files.__file__).read_text(encoding="utf-8")
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    assert "PySide6" not in roots and "h5py" not in roots
    assert "socket" not in roots and "urllib" not in roots
    top_level = {
        name.split(".")[1] for name in roots if name.startswith("uav_gpr.")
    }
    assert top_level <= {"core", "calibration"}, top_level


def test_non_finite_stored_value_never_round_trips(rcal_path: Path) -> None:
    payload = make_osl_payload()
    with pytest.raises((DomainError, ValueError)):
        write_osl_calibration_file(
            OslCalibrationFilePayload(
                profiles=(
                    StoredOslProfile(
                        profile_id=payload.profiles[0].profile_id,
                        channel=payload.profiles[0].channel,
                        frequency_hz=np.array([np.nan]),
                        open_measured_mean=payload.profiles[0].open_measured_mean,
                        short_measured_mean=payload.profiles[0].short_measured_mean,
                        load_measured_mean=payload.profiles[0].load_measured_mean,
                        open_actual=payload.profiles[0].open_actual,
                        short_actual=payload.profiles[0].short_actual,
                        load_actual=payload.profiles[0].load_actual,
                        directivity=payload.profiles[0].directivity,
                        reflection_tracking=payload.profiles[0].reflection_tracking,
                        source_match=payload.profiles[0].source_match,
                        open_capture_count=1,
                        short_capture_count=1,
                        load_capture_count=1,
                        quality=payload.profiles[0].quality,
                    ),
                ),
                created_utc=payload.created_utc,
                software_version=payload.software_version,
                device_id=payload.device_id,
                config_sha256=payload.config_sha256,
            ),
            rcal_path,
        )
    assert not rcal_path.exists()


def test_writer_refuses_overwrite(rcal_path: Path) -> None:
    write_osl_calibration_file(make_osl_payload(), rcal_path)
    before = rcal_path.read_bytes()
    with pytest.raises(WriteConflictError):
        write_osl_calibration_file(make_osl_payload(), rcal_path)
    assert rcal_path.read_bytes() == before


def test_wrong_suffix_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(DomainError) as excinfo:
        write_osl_calibration_file(make_osl_payload(), tmp_path / "bad.hdf5")
    assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT
    assert not (tmp_path / "bad.hdf5").exists()


# ---------------------------------------------------------------------------
# T9 quality anomalies (both directions)
# ---------------------------------------------------------------------------


def test_nan_mean_data_is_rejected_by_writer(tmp_path: Path) -> None:
    bad = make_background()
    broken = AirBackgroundReference(
        channels=bad.channels,
        frequency_hz=bad.frequency_hz,
        mean_data=np.where(
            np.arange(bad.mean_data.size).reshape(bad.mean_data.shape) == 3,
            np.nan + 0j,
            bad.mean_data,
        ),
        trace_count=bad.trace_count,
        domain=bad.domain,
        calibration_profile_id=bad.calibration_profile_id,
    )
    with pytest.raises(DomainError):
        write_air_background_file(make_bg_payload(reference=broken), tmp_path / "x.rcbg")
    assert not (tmp_path / "x.rcbg").exists()


def test_negative_trace_count_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(DomainError):
        make_bg_payload(
            reference=AirBackgroundReference(
                channels=(HH_S11,),
                frequency_hz=make_axis(),
                mean_data=np.zeros((1, 7), dtype=np.complex128),
                trace_count=-1,
                domain=ReferenceDomain.RAW,
                calibration_profile_id=None,
            )
        )


def test_non_finite_stability_is_rejected(tmp_path: Path) -> None:
    # the typed constructor fails closed before any file is created
    payload = make_bg_payload()
    with pytest.raises(DomainError):
        replace(payload, stability_mad_hz=np.full(
            np.asarray(payload.stability_mad_hz).shape, np.nan
        ))
    # a payload that bypassed __post_init__ still cannot serialize: the
    # canonical encoder rejects NaN (allow_nan=False) and no partial file
    # survives
    smuggled = object.__new__(AirBackgroundFilePayload)
    object.__setattr__(smuggled, "reference", payload.reference)
    object.__setattr__(smuggled, "created_utc", payload.created_utc)
    object.__setattr__(smuggled, "software_version", payload.software_version)
    object.__setattr__(smuggled, "device_id", payload.device_id)
    object.__setattr__(smuggled, "config_sha256", payload.config_sha256)
    object.__setattr__(smuggled, "stability_mad_hz", np.full((2, 7), np.nan))
    object.__setattr__(smuggled, "outlier_max_deviation", payload.outlier_max_deviation)
    object.__setattr__(smuggled, "non_finite_rejected_traces", 0)
    broken_target = tmp_path / "broken.rcbg"
    with pytest.raises(DomainError):
        write_air_background_file(smuggled, broken_target)
    assert not broken_target.exists()
    assert not list(tmp_path.glob(".broken.rcbg.tmp-*"))


def test_reader_rejects_broken_quality_block(rcbg_path: Path) -> None:
    write_air_background_file(make_bg_payload(), rcbg_path)
    document = json.loads(rcbg_path.read_text(encoding="utf-8"))
    # drop a required quality node entirely: digest still covers payload, so
    # repair the digest to isolate the structural check from the digest check
    del document["payload"]["quality"]["non_finite_rejected_traces"]
    document["content_sha256"] = "0" * 64  # will be recomputed below
    payload_bytes = json.dumps(
        document["payload"], sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    import hashlib

    document["content_sha256"] = hashlib.sha256(payload_bytes).hexdigest()
    rcbg_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(DomainError) as excinfo:
        read_air_background_file(rcbg_path)
    assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT


# ---------------------------------------------------------------------------
# T5/T6/T7/T8 compatibility matrix
# ---------------------------------------------------------------------------


def _osl_context(payload, **overrides):
    base = {
        "channels": (HH_S11, VV_S22),
        "frequency_hz": make_axis(),
        "config_sha256": CONFIG_SHA,
        "device_id": DEVICE,
        "software_version": "0.1.0.dev0",
        "created_utc": CREATED_UTC,
        "now": datetime(2026, 1, 2, tzinfo=UTC),
        "max_age_days": 30.0,
        "environment_note": None,
    }
    base.update(overrides)
    return CompatibilityContext(**base)


def test_osl_compatible_full_match(rcal_path: Path) -> None:
    write_osl_calibration_file(make_osl_payload(), rcal_path)
    loaded = read_osl_calibration_file(rcal_path)
    result = check_osl_compatibility(loaded, _osl_context(loaded))
    assert result.verdict is CompatibilityVerdict.COMPATIBLE
    assert result.hard_mismatches == ()
    assert result.warnings == ()
    names = {check.field for check in result.checks}
    assert {"channels", "frequency_hz", "config_sha256"} <= names


def test_channel_order_swap_is_incompatible(rcal_path: Path) -> None:
    write_osl_calibration_file(make_osl_payload(), rcal_path)
    loaded = read_osl_calibration_file(rcal_path)
    result = check_osl_compatibility(
        loaded, _osl_context(loaded, channels=(VV_S22, HH_S11))
    )
    assert result.verdict is CompatibilityVerdict.INCOMPATIBLE
    hard = {check.field for check in result.hard_mismatches}
    assert "channels" in hard
    assert all(check.severity == "hard" for check in result.hard_mismatches)


def test_frequency_near_miss_is_incompatible(rcal_path: Path) -> None:
    write_osl_calibration_file(make_osl_payload(), rcal_path)
    loaded = read_osl_calibration_file(rcal_path)
    shifted = make_axis().copy()
    shifted[-1] += 1.0
    result = check_osl_compatibility(
        loaded, _osl_context(loaded, frequency_hz=shifted)
    )
    assert result.verdict is CompatibilityVerdict.INCOMPATIBLE
    assert any(c.field == "frequency_hz" for c in result.hard_mismatches)
    shorter = check_osl_compatibility(
        loaded, _osl_context(loaded, frequency_hz=make_axis(6))
    )
    assert shorter.verdict is CompatibilityVerdict.INCOMPATIBLE


def test_soft_differences_warn_only(rcal_path: Path) -> None:
    write_osl_calibration_file(make_osl_payload(), rcal_path)
    loaded = read_osl_calibration_file(rcal_path)
    result = check_osl_compatibility(
        loaded,
        _osl_context(
            loaded,
            device_id=DeviceId("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
            software_version="0.2.0.dev0",
            now=CREATED_UTC + timedelta(days=45),
            environment_note="antenna mount B",
        ),
    )
    assert result.verdict is CompatibilityVerdict.COMPATIBLE_WITH_WARNINGS
    assert result.hard_mismatches == ()
    warned = {check.field for check in result.warnings}
    assert {"device_id", "software_version", "age_days", "environment_note"} <= warned


def test_hard_and_soft_mix_reports_both_lists(rcal_path: Path) -> None:
    write_osl_calibration_file(make_osl_payload(), rcal_path)
    loaded = read_osl_calibration_file(rcal_path)
    result = check_osl_compatibility(
        loaded,
        _osl_context(
            loaded,
            channels=(VV_S22, HH_S11),
            software_version="9.9.9",
        ),
    )
    assert result.verdict is CompatibilityVerdict.INCOMPATIBLE
    assert any(c.field == "channels" for c in result.hard_mismatches)
    assert any(c.field == "software_version" for c in result.warnings)


def _bg_context(payload, **overrides):
    ref = payload.reference
    base = {
        "channels": ref.channels,
        "frequency_hz": ref.frequency_hz,
        "domain": ref.domain,
        "calibration_profile_id": ref.calibration_profile_id,
        "config_sha256": CONFIG_SHA,
        "device_id": DEVICE,
        "software_version": "0.1.0.dev0",
        "created_utc": CREATED_UTC,
        "now": datetime(2026, 1, 2, tzinfo=UTC),
        "max_age_days": 30.0,
        "environment_note": None,
    }
    base.update(overrides)
    return CompatibilityContext(**base)


def test_background_domain_mismatch_is_incompatible(rcbg_path: Path) -> None:
    write_air_background_file(make_bg_payload(), rcbg_path)
    loaded = read_air_background_file(rcbg_path)
    result = check_air_background_compatibility(
        loaded, _bg_context(loaded, domain=ReferenceDomain.OSL_CALIBRATED)
    )
    assert result.verdict is CompatibilityVerdict.INCOMPATIBLE
    assert any(c.field == "domain" for c in result.hard_mismatches)


def test_background_profile_id_binding_rules(rcbg_path: Path) -> None:
    profile_id = CalibrationProfileId.new()
    calibrated = make_bg_payload(
        reference=make_background(
            domain=ReferenceDomain.OSL_CALIBRATED,
            calibration_profile_id=profile_id,
        )
    )
    write_air_background_file(calibrated, rcbg_path)
    loaded = read_air_background_file(rcbg_path)
    same = check_air_background_compatibility(loaded, _bg_context(loaded))
    assert same.verdict is CompatibilityVerdict.COMPATIBLE
    other = check_air_background_compatibility(
        loaded,
        _bg_context(
            loaded,
            calibration_profile_id=CalibrationProfileId.new(),
        ),
    )
    assert other.verdict is CompatibilityVerdict.INCOMPATIBLE
    assert any(c.field == "calibration_profile_id" for c in other.hard_mismatches)


def test_selecting_a_file_has_no_enable_side_effect(rcal_path: Path) -> None:
    # reading produces a passive snapshot only: no application API exists and
    # constructing a context never mutates the payload or its arrays.
    write_osl_calibration_file(make_osl_payload(), rcal_path)
    loaded = read_osl_calibration_file(rcal_path)
    assert not any(
        name.startswith(("apply", "enable", "activate", "use"))
        for name in dir(loaded)
    )
    snapshot = loaded.profiles[0].directivity.copy()
    ctx = CompatibilityContext.from_payload(loaded, now=datetime(2026, 1, 2, tzinfo=UTC))
    check_osl_compatibility(loaded, ctx)
    assert np.array_equal(snapshot, loaded.profiles[0].directivity)


def test_result_is_immutable(rcal_path: Path) -> None:
    write_osl_calibration_file(make_osl_payload(), rcal_path)
    loaded = read_osl_calibration_file(rcal_path)
    result = check_osl_compatibility(loaded, _osl_context(loaded))
    with pytest.raises(FrozenInstanceError):
        result.verdict = CompatibilityVerdict.INCOMPATIBLE  # type: ignore[misc]
