"""OSL calibration processing stage: frequency_raw -> frequency_calibrated.

ISSUE-032 (docs/issues/M06_CALIBRATION_PROCESSING.md, docs/CALIBRATION.md
section 5, docs/PROCESSING.md sections 1-2, t1 baseline report section 3,
docs/plans/2026-09-05-issue-032-osl-stage.md decisions D1-D9).

No new reference source is migrated here: the OSL algebra was migrated and
golden-checked in ISSUE-027 (:mod:`uav_gpr.calibration.osl`), which this
module consumes strictly read-only.  The stage contract surface
(:class:`~uav_gpr.processing.bandpass.ProcessingStage`, ``StageResult``) is
reused from ISSUE-030 unchanged; the record construction follows the same
shape as the ISSUE-030/031 stages but passes through the frozen core
:class:`ProcessingRecord` directly, because this stage must attach the
calibration reference the generic helper does not carry.

Contract surface:

- :class:`OslCalibrationStage` — implements the frozen ISSUE-030 protocol
  with ``stage_name="osl_calibration"``, a raw-only accepted input domain
  and ``frequency_calibrated`` output: applies each bound profile to its own
  channel row-by-row through :meth:`OslCalibrationSet.apply` (the single
  numeric authority shared with sweep and scan paths), returns a brand-new
  immutable model plus one appended ``ProcessingRecord``.
- :func:`osl_profile_digest` / :func:`osl_set_digest` — content digests:
  SHA-256 over the canonical JSON of a storage-mirrored profile payload
  (field structure identical to ``StoredOslProfile.to_payload`` but built
  locally; processing must not import storage per AGENTS.md section 9), and
  a set digest over the ordered channel-to-profile binding that also covers
  the binding order itself.
- :func:`check_safe_reuse` / :func:`require_safe_reuse` — safe-reuse
  judgement (CALIBRATION.md section 6 field-level-difference discipline): a
  calibrated dataset may be reused only when its history's current
  ``frequency_calibrated`` provenance matches the requested calibration
  strictly on every ordered ``{channel_id, s_parameter, profile_id,
  content_sha256}`` entry and the set digest.  Same ID with different
  content (a re-solve or tampering) is rejected; legacy records without
  digests are rejected (no lenient anchor exists).

Domain semantics (CALIBRATION.md section 5): ``frequency_calibrated`` is
fixed as *after OSL, before air background*.  The stage therefore accepts
only a history ending in ``frequency_raw`` (an empty history); any already-
calibrated, background-applied, filtered or time-domain predecessor means a
second calibration or an out-of-order pipeline and fails closed with
``PROCESSING_DOMAIN_MISMATCH``.  A duplicate ``osl_calibration`` stage name
inside one history is additionally refused by the core history guard (a
bumped ``stage_version`` does not bypass it).  Raw arrays can never be
modified: inputs are write-protected core snapshots and outputs are fresh
defensive copies made by the rebuilt core models.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Final

import numpy as np

from uav_gpr import __version__ as _SOFTWARE_VERSION
from uav_gpr.calibration.osl import OslCalibrationProfile, OslCalibrationSet
from uav_gpr.core.channels import ChannelSpec
from uav_gpr.core.enums import DataDomain, LogicalPolarization, SParameter
from uav_gpr.core.errors import DomainError, ErrorCode, JsonValue
from uav_gpr.core.frequency import FrequencyScan, FrequencySweep
from uav_gpr.core.identifiers import CalibrationProfileId
from uav_gpr.core.time_domain import ProcessingHistory, ProcessingRecord
from uav_gpr.core.timeutil import Clock, SystemClock, ensure_utc
from uav_gpr.processing.bandpass import (
    ProcessingStage,
    StageResult,
    _input_domain_of,
)

__all__ = [
    "OSL_CALIBRATION_ALGORITHM",
    "OSL_CALIBRATION_STAGE_NAME",
    "OSL_CALIBRATION_STAGE_VERSION",
    "OslCalibrationStage",
    "OslProfileProvenance",
    "SafeReuseResult",
    "check_safe_reuse",
    "osl_profile_digest",
    "osl_provenance_of",
    "osl_set_digest",
    "require_safe_reuse",
]

OSL_CALIBRATION_STAGE_NAME: Final = "osl_calibration"
OSL_CALIBRATION_STAGE_VERSION: Final = "1.0"
OSL_CALIBRATION_ALGORITHM: Final = "osl_one_port_v1"

#: Provenance key documenting how the single core record field is chosen for
#: multi-channel sets (plan decision D2): the ordered set's first profile id.
_PROFILE_ID_FIELD_SEMANTICS: Final = "first_profile_of_ordered_set"

_INPUT_DOMAINS: Final = frozenset({DataDomain.FREQUENCY_RAW})


# ---------------------------------------------------------------------------
# Canonical digests (D3/D3a): storage-mirrored payload, local transcription.
# ---------------------------------------------------------------------------


def _canonical(payload: object) -> bytes:
    """Canonical JSON bytes: sorted keys, tight separators, ASCII, no NaN."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _encode_array(values: np.ndarray, dtype_tag: str) -> dict[str, JsonValue]:
    """Array node identical in shape to the storage codec's encoding."""
    arr = np.asarray(values)
    expected = np.complex128 if dtype_tag == "complex128" else np.float64
    if arr.dtype != expected:
        arr = np.asarray(arr, dtype=expected)
    if arr.ndim == 0 or arr.size == 0:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "digest array must have at least one element",
            {"dtype": dtype_tag},
        )
    flat = arr.reshape(-1)
    if not np.all(np.isfinite(flat)):
        bad = int(np.argmax(~np.isfinite(flat)))
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            f"digest array contains a non-finite value at flat index {bad}",
            {"dtype": dtype_tag},
        )
    payload: dict[str, JsonValue] = {
        "dtype": dtype_tag,
        "shape": [int(n) for n in arr.shape],
        "re": [float(v) for v in flat.real],
    }
    if dtype_tag == "complex128":
        payload["im"] = [float(v) for v in flat.imag]
    return payload


def _profile_payload(profile: OslCalibrationProfile) -> dict[str, JsonValue]:
    """Storage-mirrored profile payload (fields aligned 1:1 with the .rcal
    ``StoredOslProfile.to_payload`` layout; built locally so processing never
    imports storage — see plan decision D3a)."""
    quality = profile.quality
    return {
        "profile_id": str(profile.profile_id),
        "channel": {
            "channel_id": profile.channel.channel_id,
            "logical_polarization": (
                profile.channel.logical_polarization.value
            ),
            "s_parameter": profile.channel.s_parameter.value,
            "display_name": profile.channel.display_name,
            "antenna_note": profile.channel.antenna_note,
        },
        "s_parameter": profile.s_parameter.value,
        "frequency_hz": _encode_array(profile.frequency_hz, "float64"),
        "standards": {
            standard: {
                "measured_mean": _encode_array(
                    getattr(profile, f"{standard}_measured_mean"), "complex128"
                ),
                "actual": _encode_array(
                    getattr(profile, f"{standard}_actual"), "complex128"
                ),
                "capture_count": getattr(profile, f"{standard}_capture_count"),
            }
            for standard in ("open", "short", "load")
        },
        "error_terms": {
            "directivity": _encode_array(profile.directivity, "complex128"),
            "reflection_tracking": _encode_array(
                profile.reflection_tracking, "complex128"
            ),
            "source_match": _encode_array(profile.source_match, "complex128"),
        },
        "quality": {
            "open_rms_abs_error": float(quality.open_rms_abs_error),
            "open_max_abs_error": float(quality.open_max_abs_error),
            "short_rms_abs_error": float(quality.short_rms_abs_error),
            "short_max_abs_error": float(quality.short_max_abs_error),
            "load_rms_abs_error": float(quality.load_rms_abs_error),
            "load_max_abs_error": float(quality.load_max_abs_error),
            "worst_max_abs_error": float(quality.worst_max_abs_error),
            "solve_degenerate": False,
        },
    }


def osl_profile_digest(profile: OslCalibrationProfile) -> str:
    """SHA-256 hex of the canonical JSON of one solved profile's payload.

    Covers identity, channel binding, frequency axis, all six standard
    vectors, the three error terms, capture counts and quality metrics: any
    content change flips the digest while the UUID may stay the same (which
    is exactly what strict safe reuse must catch).
    """
    if not isinstance(profile, OslCalibrationProfile):
        raise TypeError(
            f"profile must be an OslCalibrationProfile, got {type(profile).__name__}"
        )
    return hashlib.sha256(_canonical(_profile_payload(profile))).hexdigest()


def osl_set_digest(calibration: OslCalibrationSet) -> str:
    """SHA-256 hex over the ordered channel-to-profile binding of a set.

    Entries carry ``channel_id``, ``profile_id`` and the profile content
    digest in container order, so swapping two correctly-bound profiles
    between channels changes this digest even though the multiset of
    individual digests does not.
    """
    if not isinstance(calibration, OslCalibrationSet):
        raise TypeError(
            "calibration must be an OslCalibrationSet, "
            f"got {type(calibration).__name__}"
        )
    binding = [
        {
            "channel_id": profile.channel.channel_id,
            "profile_id": str(profile.profile_id),
            "content_sha256": osl_profile_digest(profile),
        }
        for profile in calibration.profiles
    ]
    return hashlib.sha256(_canonical(binding)).hexdigest()


# ---------------------------------------------------------------------------
# Ordered per-channel provenance entries (D2).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OslProfileProvenance:
    """One ordered channel-to-profile reference recorded into history."""

    channel_id: str
    s_parameter: str
    profile_id: str
    content_sha256: str

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "channel_id": self.channel_id,
            "s_parameter": self.s_parameter,
            "profile_id": self.profile_id,
            "content_sha256": self.content_sha256,
        }

    @classmethod
    def from_profile(cls, profile: OslCalibrationProfile) -> OslProfileProvenance:
        return cls(
            channel_id=profile.channel.channel_id,
            s_parameter=profile.s_parameter.value,
            profile_id=str(profile.profile_id),
            content_sha256=osl_profile_digest(profile),
        )

    @classmethod
    def from_json(cls, node: object) -> OslProfileProvenance | None:
        """Parse one recorded entry; malformed or partial entries yield None."""
        if not isinstance(node, Mapping):
            return None
        channel_id = node.get("channel_id")
        s_parameter = node.get("s_parameter")
        profile_id = node.get("profile_id")
        content_sha256 = node.get("content_sha256")
        fields = (channel_id, s_parameter, profile_id, content_sha256)
        if not all(isinstance(value, str) and value for value in fields):
            return None
        return cls(
            channel_id=str(channel_id),
            s_parameter=str(s_parameter),
            profile_id=str(profile_id),
            content_sha256=str(content_sha256),
        )


def _provenance_list(calibration: OslCalibrationSet) -> list[dict[str, JsonValue]]:
    return [
        OslProfileProvenance.from_profile(profile).to_json()
        for profile in calibration.profiles
    ]


def osl_provenance_of(
    history: ProcessingHistory,
) -> tuple[OslProfileProvenance, ...] | None:
    """Ordered provenance of the CURRENT calibrated record, or None.

    Returns None when the history ends outside ``frequency_calibrated`` or
    the producing record carries no complete ordered provenance list (legacy
    writers): callers must treat both as "strict provenance unavailable".
    """
    if not isinstance(history, ProcessingHistory):
        raise TypeError(
            f"history must be a ProcessingHistory, got {type(history).__name__}"
        )
    record = _calibrated_record_of(history)
    if record is None:
        return None
    return _provenance_from_record(record)


def _calibrated_record_of(
    history: ProcessingHistory,
) -> ProcessingRecord | None:
    """The last record iff the history currently ends in frequency_calibrated."""
    if not history.records:
        return None
    last = history.records[-1]
    if last.output_domain is not DataDomain.FREQUENCY_CALIBRATED:
        return None
    return last


def _provenance_from_record(
    record: ProcessingRecord,
) -> tuple[OslProfileProvenance, ...] | None:
    profiles = record.parameters.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        return None
    entries: list[OslProfileProvenance] = []
    for node in profiles:
        entry = OslProfileProvenance.from_json(node)
        if entry is None:
            return None
        entries.append(entry)
    return tuple(entries)


# ---------------------------------------------------------------------------
# The stage itself (D1/D4/D5/D6/D8).
# ---------------------------------------------------------------------------


class OslCalibrationStage:
    """Strict ``frequency_raw`` -> ``frequency_calibrated`` domain conversion.

    Constructed with one :class:`~uav_gpr.calibration.osl.OslCalibrationSet`
    (ordered channel binding produced by the ISSUE-027 solver).  ``apply``
    validates the input against the set channel-by-channel (exact ordered
    channel specs, pointwise-equal shared frequency axis, reflection S
    parameter), corrects every channel row with its own bound profile via
    the set's single numeric path, and appends one provenance record whose
    parameters carry the ordered per-channel references and the set digest
    (plan decision D2).  Input models are never mutated; the output keeps
    the input's container type, shape, channels and per-trace metadata.
    Re-applying inside one history fails closed twice over (raw-only input
    gate + core stage-name uniqueness), and any non-raw predecessor domain
    is refused with ``PROCESSING_DOMAIN_MISMATCH``.
    """

    def __init__(self, calibration: OslCalibrationSet) -> None:
        if not isinstance(calibration, OslCalibrationSet):
            raise TypeError(
                "calibration must be an OslCalibrationSet, "
                f"got {type(calibration).__name__}"
            )
        self._calibration = calibration

    @property
    def calibration(self) -> OslCalibrationSet:
        """The frozen calibration set this stage applies (read-only view)."""
        return self._calibration

    @property
    def stage_name(self) -> str:
        return OSL_CALIBRATION_STAGE_NAME

    @property
    def stage_version(self) -> str:
        return OSL_CALIBRATION_STAGE_VERSION

    @property
    def input_domain(self) -> frozenset[DataDomain]:
        return _INPUT_DOMAINS

    @property
    def output_domain(self) -> DataDomain:
        return DataDomain.FREQUENCY_CALIBRATED

    @property
    def parameters(self) -> Mapping[str, JsonValue]:
        """Canonical JSON-safe stage parameters recorded into every entry."""
        return MappingProxyType(
            {
                "algorithm": OSL_CALIBRATION_ALGORITHM,
                "profile_id_field_semantics": _PROFILE_ID_FIELD_SEMANTICS,
                "channel_order": [
                    profile.channel.channel_id
                    for profile in self._calibration.profiles
                ],
            }
        )

    # -- validation (D4) ------------------------------------------------------

    def _validate_binding(self, source: FrequencySweep | FrequencyScan) -> None:
        set_channels = self._calibration.channels
        source_channels = tuple(source.channels)
        if len(source_channels) != len(set_channels):
            raise DomainError(
                ErrorCode.CHANNEL_CONTRACT_MISMATCH,
                "input channel count differs from the calibration set",
                {
                    "stage_name": OSL_CALIBRATION_STAGE_NAME,
                    "input_channels": [c.channel_id for c in source_channels],
                    "calibration_channels": [
                        c.channel_id for c in set_channels
                    ],
                },
            )
        for index, (have, want) in enumerate(
            zip(source_channels, set_channels, strict=True)
        ):
            if have != want:
                raise DomainError(
                    ErrorCode.CHANNEL_CONTRACT_MISMATCH,
                    "input channel order/spec does not match the calibration "
                    "binding exactly",
                    {
                        "stage_name": OSL_CALIBRATION_STAGE_NAME,
                        "index": index,
                        "expected_channel_id": want.channel_id,
                        "expected_s_parameter": want.s_parameter.value,
                        "found_channel_id": have.channel_id,
                        "found_s_parameter": have.s_parameter.value,
                    },
                )
        axis = source.frequencies_hz
        for index, profile in enumerate(self._calibration.profiles):
            if profile.s_parameter is not source_channels[index].s_parameter:
                raise DomainError(
                    ErrorCode.CHANNEL_CONTRACT_MISMATCH,
                    "profile S parameter differs from its bound channel",
                    {
                        "stage_name": OSL_CALIBRATION_STAGE_NAME,
                        "index": index,
                        "profile_s_parameter": profile.s_parameter.value,
                        "channel_s_parameter": (
                            source_channels[index].s_parameter.value
                        ),
                    },
                )
            if not np.array_equal(profile.frequency_hz, axis):
                raise DomainError(
                    ErrorCode.AXIS_MISMATCH,
                    "profile frequency axis differs from the input axis",
                    {
                        "stage_name": OSL_CALIBRATION_STAGE_NAME,
                        "index": index,
                        "profile_points": int(profile.frequency_hz.size),
                        "input_points": int(axis.size),
                    },
                )
        if source.data.shape[-1] != axis.size:
            raise DomainError(
                ErrorCode.SHAPE_MISMATCH,
                "input data frequency axis length differs from frequencies_hz",
                {
                    "data_last_dim": int(source.data.shape[-1]),
                    "frequencies_hz_size": int(axis.size),
                },
            )

    # -- application ----------------------------------------------------------

    def apply(
        self,
        source: object,
        *,
        history: ProcessingHistory,
        executed_utc: datetime | None = None,
        clock: Clock | None = None,
    ) -> StageResult:
        """Calibrate one sweep/scan and append the provenance record.

        ``executed_utc`` wins when given; otherwise the injected ``clock``
        (default: the system UTC clock) stamps the record once.  No sleeping,
        no polling.  The returned :class:`StageResult.domain` is always
        ``frequency_calibrated``.
        """
        if not isinstance(history, ProcessingHistory):
            raise TypeError(
                f"history must be a ProcessingHistory, got {type(history).__name__}"
            )
        if not isinstance(source, (FrequencySweep, FrequencyScan)):
            raise TypeError(
                "osl calibration input must be a FrequencySweep or "
                f"FrequencyScan, got {type(source).__name__}"
            )
        if executed_utc is not None:
            # Fail closed on naive/offset-less stamps before any work.
            stamp = ensure_utc(executed_utc)
        else:
            stamp = (clock or SystemClock()).utc_now()

        input_domain = _input_domain_of(history)
        if input_domain not in _INPUT_DOMAINS:
            raise DomainError(
                ErrorCode.PROCESSING_DOMAIN_MISMATCH,
                "osl calibration input domain is not frequency_raw: "
                "recalibrating derived data would double-apply OSL "
                "(frequency_calibrated means after-OSL, pre-subtraction)",
                {
                    "stage_name": OSL_CALIBRATION_STAGE_NAME,
                    "input_domain": input_domain.value,
                    "allowed_input_domains": [
                        DataDomain.FREQUENCY_RAW.value
                    ],
                },
            )

        self._validate_binding(source)

        # Single numeric authority: OslCalibrationSet.apply corrects one
        # (channel, frequency) array row by row with each bound profile and
        # is already fully contract-tested in ISSUE-027.  A scan runs the
        # exact same call along its trace axis (identical math, no second
        # implementation); outputs are fresh arrays copied again by the
        # rebuilt core models into never-writable snapshots.
        if isinstance(source, FrequencySweep):
            corrected = self._calibration.apply(source.data, source.channels)
        else:
            corrected = np.stack(
                [
                    self._calibration.apply(trace, source.channels)
                    for trace in source.data
                ],
                axis=0,
            )

        provenance_entries: list[JsonValue] = [
            dict(entry) for entry in _provenance_list(self._calibration)
        ]
        parameters: dict[str, JsonValue] = {
            **dict(self.parameters),
            "profiles": provenance_entries,
            "set_content_sha256": osl_set_digest(self._calibration),
        }
        record = ProcessingRecord(
            stage_name=OSL_CALIBRATION_STAGE_NAME,
            stage_version=OSL_CALIBRATION_STAGE_VERSION,
            parameters=parameters,
            input_domain=input_domain,
            output_domain=DataDomain.FREQUENCY_CALIBRATED,
            executed_utc=stamp,
            software_version=_SOFTWARE_VERSION,
            # Core ProcessingRecord requires the calibrated output to carry a
            # calibration reference; for ordered sets the first profile id
            # stands in (semantics recorded under profile_id_field_semantics,
            # plan decision D2).
            calibration_profile_id=self._calibration.profiles[0].profile_id,
        )
        new_history = history.append(record)

        output_source: FrequencySweep | FrequencyScan
        if isinstance(source, FrequencySweep):
            output_source = FrequencySweep(
                channels=source.channels,
                frequencies_hz=source.frequencies_hz,
                data=corrected,
                metadata=source.metadata,
            )
        else:
            output_source = FrequencyScan(
                channels=source.channels,
                frequencies_hz=source.frequencies_hz,
                data=corrected,
                metadata=source.metadata,
            )
        return StageResult(
            source=output_source,
            history=new_history,
            domain=DataDomain.FREQUENCY_CALIBRATED,
        )


# ---------------------------------------------------------------------------
# Safe reuse judgement (D7): strict identical provenance only.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SafeReuseResult:
    """Verdict of a safe-reuse check: compatible plus field-level diffs."""

    compatible: bool
    mismatches: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "mismatches", tuple(self.mismatches))


def check_safe_reuse(
    history: ProcessingHistory, calibration: OslCalibrationSet
) -> SafeReuseResult:
    """Judge whether ``calibration`` may be safely reused for ``history``.

    Strict rule (acceptance: only identical profile provenance passes): the
    history must currently end in ``frequency_calibrated`` and its producing
    record must carry the complete ordered provenance list equal — position
    by position on ``channel_id``/``s_parameter``/``profile_id``/
    ``content_sha256`` — to the requested calibration, with an equal set
    digest and the recorded profile-id-field semantics.  Missing (legacy)
    provenance, differing IDs, same-ID-different-content, swapped bindings
    or extra/missing channels all fail, listing field-level differences.
    Never raises for a business mismatch: use :func:`require_safe_reuse`
    for the fail-closed variant.
    """
    if not isinstance(history, ProcessingHistory):
        raise TypeError(
            f"history must be a ProcessingHistory, got {type(history).__name__}"
        )
    if not isinstance(calibration, OslCalibrationSet):
        raise TypeError(
            "calibration must be an OslCalibrationSet, "
            f"got {type(calibration).__name__}"
        )
    mismatches: list[str] = []
    record = _calibrated_record_of(history)
    if record is None:
        ending = (
            history.records[-1].output_domain.value
            if history.records
            else "frequency_raw (empty history)"
        )
        mismatches.append(
            f"history does not end in frequency_calibrated (last output "
            f"domain: {ending}); the data is not currently calibrated"
        )
        return SafeReuseResult(compatible=False, mismatches=tuple(mismatches))

    recorded = _provenance_from_record(record)
    if recorded is None:
        mismatches.append(
            "calibrated record carries no complete ordered profile digest "
            "provenance (legacy writer); strict reuse is refused"
        )
        return SafeReuseResult(compatible=False, mismatches=tuple(mismatches))

    requested = tuple(
        OslProfileProvenance.from_profile(profile)
        for profile in calibration.profiles
    )
    if len(recorded) != len(requested):
        mismatches.append(
            f"profile count differs: history has {len(recorded)}, "
            f"requested calibration has {len(requested)}"
        )
    for index, want in enumerate(requested):
        have = recorded[index] if index < len(recorded) else None
        if have is None:
            mismatches.append(
                f"positions[{index}]: history lacks a profile for channel "
                f"{want.channel_id!r}"
            )
            continue
        if have.channel_id != want.channel_id:
            mismatches.append(
                f"positions[{index}]: channel binding differs "
                f"(history {have.channel_id!r} vs requested "
                f"{want.channel_id!r})"
            )
        if have.s_parameter != want.s_parameter:
            mismatches.append(
                f"positions[{index}]: s_parameter differs "
                f"(history {have.s_parameter!r} vs requested "
                f"{want.s_parameter!r})"
            )
        if have.profile_id != want.profile_id:
            mismatches.append(
                f"positions[{index}]: profile_id differs "
                f"(history {have.profile_id!r} vs requested "
                f"{want.profile_id!r})"
            )
        if have.content_sha256 != want.content_sha256:
            mismatches.append(
                f"positions[{index}]: content_sha256 differs for channel "
                f"{want.channel_id!r} (same profile_id with different "
                "content means a re-solved or altered profile)"
            )
    if record.parameters.get("set_content_sha256") != osl_set_digest(calibration):
        mismatches.append(
            "set_content_sha256 differs (ordered channel-to-profile binding "
            "is not the same calibration)"
        )
    if (
        record.parameters.get("profile_id_field_semantics")
        != _PROFILE_ID_FIELD_SEMANTICS
    ):
        mismatches.append(
            "profile_id_field_semantics differs from "
            f"{_PROFILE_ID_FIELD_SEMANTICS!r}; the record's "
            "calibration_profile_id cannot be interpreted identically"
        )
    return SafeReuseResult(
        compatible=not mismatches,
        mismatches=tuple(mismatches),
    )


def require_safe_reuse(
    history: ProcessingHistory, calibration: OslCalibrationSet
) -> None:
    """Fail-closed variant of :func:`check_safe_reuse`."""
    verdict = check_safe_reuse(history, calibration)
    if not verdict.compatible:
        mismatch_items: list[JsonValue] = [str(item) for item in verdict.mismatches[:16]]
        context: dict[str, JsonValue] = {
            "stage_name": OSL_CALIBRATION_STAGE_NAME,
            "mismatch_count": len(verdict.mismatches),
            "mismatches": mismatch_items,
        }
        raise DomainError(
            ErrorCode.PROCESSING_DOMAIN_MISMATCH,
            "calibration provenance does not match strictly; safe reuse "
            "refused",
            context,
        )


# Structural conformance to the frozen ISSUE-030 protocol, checked statically
# so a future refactor that breaks the shape fails at import time.  The probe
# stage below exists only for that check: it assembles one trivial ideal
# profile directly through the ISSUE-027 value constructor (a 3-point axis,
# ideal standards, unit error terms) — the solver is never invoked anywhere
# in this module (ISSUE-032 exclusion: no OSL acquisition or re-solve).
def _protocol_probe_stage() -> OslCalibrationStage:
    from uav_gpr.calibration.osl import OslCalibrationQuality

    probe_axis = np.linspace(1.0e8, 2.0e8, 3)
    probe_channel = ChannelSpec(
        "probe", LogicalPolarization.HH, SParameter.S11, "protocol probe"
    )
    ones = np.ones(3, dtype=np.complex128)
    zeros = np.zeros(3, dtype=np.complex128)
    quality = OslCalibrationQuality(
        open_rms_abs_error=0.0,
        open_max_abs_error=0.0,
        short_rms_abs_error=0.0,
        short_max_abs_error=0.0,
        load_rms_abs_error=0.0,
        load_max_abs_error=0.0,
    )
    profile = OslCalibrationProfile(
        profile_id=CalibrationProfileId("00000000-0000-4000-8000-000000000000"),
        channel=probe_channel,
        frequency_hz=probe_axis,
        open_measured_mean=ones,
        short_measured_mean=-ones,
        load_measured_mean=zeros,
        open_actual=ones,
        short_actual=-ones,
        load_actual=zeros,
        directivity=zeros,
        reflection_tracking=ones,
        source_match=zeros,
        open_capture_count=1,
        short_capture_count=1,
        load_capture_count=1,
        quality=quality,
    )
    return OslCalibrationStage(OslCalibrationSet((profile,)))


assert isinstance(_protocol_probe_stage(), ProcessingStage)
