"""Air-background subtraction stage: complex frequency-domain reference removal.

ISSUE-033 (docs/issues/M06_CALIBRATION_PROCESSING.md, docs/CALIBRATION.md
sections 4-5, docs/PROCESSING.md sections 1-2, t1 baseline report section 3,
docs/plans/2026-09-05-issue-033-bg-subtraction.md decisions D1-D9).

No new reference source is migrated here: the frozen air-background object
(:class:`~uav_gpr.calibration.reference.AirBackgroundReference`) was built and
contract-tested in ISSUE-028/029, which this module consumes strictly
read-only.  The stage contract surface (:class:`ProcessingStage`,
``StageResult``, ``_input_domain_of``) is reused from ISSUE-030 unchanged;
the record is built directly through the core :class:`ProcessingRecord`
because this stage must attach the background reference (and inherit the
calibration reference on calibrated inputs), following the ISSUE-032 pattern.

Contract surface:

- :class:`AirBackgroundSubtractionStage` — implements the frozen ISSUE-030
  protocol with ``stage_name="air_background_subtraction"``, accepted input
  domains ``{frequency_raw, frequency_calibrated}`` and
  ``frequency_background_applied`` output: subtracts the reference
  ``mean_data`` row-by-row along the channel/frequency axes (scans broadcast
  the SAME per-channel vector along the trace axis — never any trace-axis
  statistics, which is Flat Reflection, ISSUE-035), returning a brand-new
  immutable model plus one appended ``ProcessingRecord``.
- :func:`background_reference_digest` — SHA-256 over the canonical JSON of a
  storage-mirrored payload (field structure aligned with
  ``storage/calibration_files.AirBackgroundFilePayload.to_document``'s content
  domain but built locally; processing must not import storage per AGENTS.md
  section 9).  Covers domain, profile binding, channels *in order*, the full
  frequency axis and every mean value: any content or order change flips it.
- :func:`check_safe_reuse` / :func:`require_matching_calibration_provenance`
  — strict provenance judgement for calibrated inputs (CALIBRATION.md
  section 4): the reference's declared ``calibration_profile_id`` must equal
  the id recorded by the history's current ``frequency_calibrated`` producer
  AND that record's stored per-channel ``content_sha256`` must equal the
  digest recomputed from the live calibration set (same ID with different
  content — a re-solve or tampering — is rejected; legacy records without
  digests are rejected; no lenient anchor exists).

Domain semantics (CALIBRATION.md sections 4-5): an air-background reference is
captured in an explicit domain (``raw`` or ``osl_calibrated``) and application
must match data domain, ordered channel binding, S parameters and the full
frequency axis; a calibrated application must additionally match the
``calibration_profile_id``.  The legal predecessors are exactly
``frequency_raw`` (empty history) and ``frequency_calibrated`` (after OSL,
before background, per the fixed pipeline); anything else means an out-of-order
or double subtraction and fails closed with
``ErrorCode.PROCESSING_DOMAIN_MISMATCH``.  A duplicate
``air_background_subtraction`` stage name inside one history is additionally
refused by the core history guard (a bumped ``stage_version`` does not bypass
it).  Raw arrays can never be modified: inputs are write-protected snapshots
and outputs are fresh defensive copies made by the rebuilt core models.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Final

import numpy as np

from uav_gpr import __version__ as _SOFTWARE_VERSION
from uav_gpr.calibration.osl import OslCalibrationProfile, OslCalibrationSet
from uav_gpr.calibration.reference import AirBackgroundReference, ReferenceDomain
from uav_gpr.core.channels import ChannelSpec
from uav_gpr.core.enums import DataDomain, LogicalPolarization, SParameter
from uav_gpr.core.errors import DomainError, ErrorCode, JsonValue
from uav_gpr.core.frequency import FrequencyScan, FrequencySweep
from uav_gpr.core.identifiers import BackgroundReferenceId, CalibrationProfileId
from uav_gpr.core.time_domain import ProcessingHistory, ProcessingRecord
from uav_gpr.core.timeutil import Clock, SystemClock, ensure_utc
from uav_gpr.processing.bandpass import (
    ProcessingStage,
    StageResult,
    _input_domain_of,
)
from uav_gpr.processing.osl_calibration import osl_profile_digest

__all__ = [
    "AIR_BACKGROUND_ALGORITHM",
    "AIR_BACKGROUND_STAGE_NAME",
    "AIR_BACKGROUND_STAGE_VERSION",
    "AirBackgroundSubtractionStage",
    "SafeReuseResult",
    "background_reference_digest",
    "check_safe_reuse",
    "require_matching_calibration_provenance",
]

AIR_BACKGROUND_STAGE_NAME: Final = "air_background_subtraction"
AIR_BACKGROUND_STAGE_VERSION: Final = "1.0"
AIR_BACKGROUND_ALGORITHM: Final = "air_background_complex_subtract_v1"

#: Payload format tag pinned by the golden-digest contract test (D3).
_DIGEST_FORMAT: Final = "uav_gpr_rcbg_payload_v1"
_FREQUENCY_UNIT: Final = "Hz"

#: Legal predecessors (CALIBRATION.md section 5 fixed order: raw or the
#: after-OSL/before-background calibrated snapshot; nothing else).
_INPUT_DOMAINS: Final = frozenset(
    {DataDomain.FREQUENCY_RAW, DataDomain.FREQUENCY_CALIBRATED}
)

#: Bijective data-domain <-> reference-domain mapping (plan D1): a raw
#: reference only ever applies to raw data; an osl_calibrated reference only
#: ever applies to calibrated data carrying the matching profile.
_REFERENCE_DOMAIN_OF: Final[Mapping[DataDomain, ReferenceDomain]] = {
    DataDomain.FREQUENCY_RAW: ReferenceDomain.RAW,
    DataDomain.FREQUENCY_CALIBRATED: ReferenceDomain.OSL_CALIBRATED,
}


# ---------------------------------------------------------------------------
# Canonical digest (D3): storage-mirrored payload, local transcription.
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
            {"dtype": dtype_tag, "flat_index": bad},
        )
    payload: dict[str, JsonValue] = {
        "dtype": dtype_tag,
        "shape": [int(n) for n in arr.shape],
        "re": [float(v) for v in flat.real],
    }
    if dtype_tag == "complex128":
        payload["im"] = [float(v) for v in flat.imag]
    return payload


def _channel_to_dict(channel: ChannelSpec) -> dict[str, JsonValue]:
    return {
        "channel_id": channel.channel_id,
        "logical_polarization": channel.logical_polarization.value,
        "s_parameter": channel.s_parameter.value,
        "display_name": channel.display_name,
        "antenna_note": channel.antenna_note,
    }


def _reference_payload(reference: AirBackgroundReference) -> dict[str, JsonValue]:
    """Storage-mirrored content payload (fields aligned 1:1 with the .rcbg
    document's content domain; built locally so processing never imports
    storage — plan decision D3/D3a)."""
    return {
        "format": _DIGEST_FORMAT,
        "domain": reference.domain.value,
        "calibration_profile_id": (
            None
            if reference.calibration_profile_id is None
            else str(reference.calibration_profile_id)
        ),
        "axis_unit": _FREQUENCY_UNIT,
        "channels": [_channel_to_dict(c) for c in reference.channels],
        "frequency_hz": _encode_array(reference.frequency_hz, "float64"),
        "mean_data": _encode_array(reference.mean_data, "complex128"),
        "trace_count": int(reference.trace_count),
    }


def background_reference_digest(reference: AirBackgroundReference) -> str:
    """SHA-256 hex of the canonical JSON of one reference's content payload.

    Covers the declared domain, the calibration-profile binding, the ordered
    channel contracts, the full frequency axis, every complex mean value and
    the trace count: any content or channel-order change flips the digest
    while the UUID identity may stay the same (which is exactly what strict
    provenance checks must catch).
    """
    if not isinstance(reference, AirBackgroundReference):
        raise TypeError(
            "reference must be an AirBackgroundReference, "
            f"got {type(reference).__name__}"
        )
    return hashlib.sha256(_canonical(_reference_payload(reference))).hexdigest()


# ---------------------------------------------------------------------------
# Strict calibration-provenance judgement for calibrated inputs (D4).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SafeReuseResult:
    """Verdict of a safe-reuse check: compatible plus field-level diffs."""

    compatible: bool
    mismatches: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "mismatches", tuple(self.mismatches))


def _calibrated_record_of(history: ProcessingHistory) -> ProcessingRecord | None:
    """The last record iff the history currently ends in frequency_calibrated."""
    if not history.records:
        return None
    last = history.records[-1]
    if last.output_domain is not DataDomain.FREQUENCY_CALIBRATED:
        return None
    return last


def _live_profile_for_id(
    calibration: OslCalibrationSet, profile_id: CalibrationProfileId
) -> OslCalibrationProfile | None:
    """The live profile carrying ``profile_id`` (identity match), if any."""
    for profile in calibration.profiles:
        if profile.profile_id == profile_id:
            return profile
    return None


def check_safe_reuse(
    history: ProcessingHistory,
    reference: AirBackgroundReference,
    *,
    current_calibration: OslCalibrationSet | None = None,
) -> SafeReuseResult:
    """Judge whether ``reference`` may apply to the data behind ``history``.

    Strict rule (acceptance: calibrated applications require identical
    profile ID **and** content digest): the history must end in
    ``frequency_calibrated``; the reference must declare
    ``ReferenceDomain.OSL_CALIBRATED`` and carry a concrete
    ``calibration_profile_id``; that id must equal the id recorded by the
    producing record; and the producing record's ordered ``profiles``
    provenance must contain an entry whose ``profile_id`` equals the id with
    a ``content_sha256`` equal to the digest recomputed from the live
    calibration set (default authority: the profiles bound to the recorded
    channel ids via ``current_calibration``).  Missing (legacy) provenance,
    differing IDs, same-ID-different-content (a re-solve or tampering) and
    unbound channels all fail, listing field-level differences.  Never
    raises for a business mismatch: use
    :func:`require_matching_calibration_provenance` for the fail-closed
    variant.

    Digest verification requires a live :class:`OslCalibrationSet`: pass it
    as ``current_calibration`` here or as the stage keyword of the same name
    (the stage always carries one).  Without it, strict verification is
    impossible and the verdict is incompatible — never a silent pass.
    """
    if not isinstance(history, ProcessingHistory):
        raise TypeError(
            f"history must be a ProcessingHistory, got {type(history).__name__}"
        )
    if not isinstance(reference, AirBackgroundReference):
        raise TypeError(
            "reference must be an AirBackgroundReference, "
            f"got {type(reference).__name__}"
        )
    mismatches: list[str] = []
    record = _calibrated_record_of(history)
    if record is None:
        return SafeReuseResult(
            compatible=False,
            mismatches=("history does not currently end in frequency_calibrated",),
        )
    if reference.domain is not ReferenceDomain.OSL_CALIBRATED:
        mismatches.append(
            f"reference.domain is {reference.domain.value!r}, "
            "an osl_calibrated reference is required for calibrated data"
        )
    if not isinstance(reference.calibration_profile_id, CalibrationProfileId):
        mismatches.append(
            "reference declares no calibration_profile_id (osl_calibrated "
            "references must bind one)"
        )
    elif reference.calibration_profile_id != record.calibration_profile_id:
        mismatches.append(
            "profile_id: reference "
            f"{reference.calibration_profile_id.to_json()} != recorded "
            + (
                record.calibration_profile_id.to_json()
                if record.calibration_profile_id is not None
                else "None"
            )
        )
    if mismatches:
        return SafeReuseResult(compatible=False, mismatches=tuple(mismatches))
    assert reference.calibration_profile_id is not None  # narrowed above
    entries = record.parameters.get("profiles")
    if not isinstance(entries, list) or not entries:
        return SafeReuseResult(
            compatible=False,
            mismatches=(
                "recorded calibrated provenance carries no ordered profiles "
                "digest list (legacy record: strict provenance unavailable)",
            ),
        )
    matching: list[Mapping[str, JsonValue]] = []
    for node in entries:
        if (
            isinstance(node, Mapping)
            and node.get("profile_id") == reference.calibration_profile_id.to_json()
        ):
            matching.append(node)
    if not matching:
        mismatches.append(
            "profile_id: recorded provenance lists no entry for "
            f"{reference.calibration_profile_id.to_json()}"
        )
        return SafeReuseResult(compatible=False, mismatches=tuple(mismatches))
    entry = matching[0]
    recorded_digest = entry.get("content_sha256")
    if not isinstance(recorded_digest, str) or not recorded_digest:
        mismatches.append(
            "content_sha256: recorded provenance entry has no digest string "
            "(strict provenance unavailable)"
        )
        return SafeReuseResult(compatible=False, mismatches=tuple(mismatches))
    channel_node = entry.get("channel_id")
    channel_id = channel_node if isinstance(channel_node, str) else None
    live = current_calibration
    if live is None:
        mismatches.append(
            "content_sha256: no live OslCalibrationSet supplied to recompute "
            "the bound profile digest (strict verification impossible)"
        )
        return SafeReuseResult(compatible=False, mismatches=tuple(mismatches))
    live_profile = _live_profile_for_id(live, reference.calibration_profile_id)
    if live_profile is None:
        mismatches.append(
            "profile_id: live calibration binds no profile with "
            f"{reference.calibration_profile_id.to_json()}"
        )
        return SafeReuseResult(compatible=False, mismatches=tuple(mismatches))
    if channel_id is not None and live_profile.channel.channel_id != channel_id:
        mismatches.append(
            f"channel_id: recorded {channel_id!r} != live-bound "
            f"{live_profile.channel.channel_id!r}"
        )
    live_digest = osl_profile_digest(live_profile)
    if live_digest != recorded_digest:
        mismatches.append(
            "content_sha256: recorded "
            f"{recorded_digest[:16]}... != recomputed {live_digest[:16]}... "
            "(same profile id, different content — re-solve or tampering)"
        )
    return SafeReuseResult(compatible=not mismatches, mismatches=tuple(mismatches))


def require_matching_calibration_provenance(
    history: ProcessingHistory,
    reference: AirBackgroundReference,
    *,
    current_calibration: OslCalibrationSet | None = None,
) -> None:
    """Fail-closed variant of :func:`check_safe_reuse` (raises on mismatch)."""
    verdict = check_safe_reuse(
        history, reference, current_calibration=current_calibration
    )
    if not verdict.compatible:
        raise DomainError(
            ErrorCode.PROCESSING_DOMAIN_MISMATCH,
            "air background reference does not strictly match the "
            "calibration provenance of the data (ID + content digest)",
            {
                "kind": "calibration_provenance_mismatch",
                "mismatches": list(verdict.mismatches),
            },
        )


# ---------------------------------------------------------------------------
# The stage itself (D1/D2/D5/D6/D7/D8).
# ---------------------------------------------------------------------------


def _validate_reference_axis_finite(reference: AirBackgroundReference) -> None:
    """Constructor-time axis/dtype/finiteness validation of the reference.

    The channel-count-dependent shape check runs per source in
    :meth:`AirBackgroundSubtractionStage._validate_contract` (a reference is
    only meaningful against data carrying the same ordered channel binding).
    """
    axis = np.asarray(reference.frequency_hz)
    mean = np.asarray(reference.mean_data)
    if axis.ndim != 1 or axis.size == 0:
        raise DomainError(
            ErrorCode.AXIS_MISMATCH,
            "reference frequency axis must be a non-empty one-dimensional array",
            {"ndim": int(axis.ndim), "size": int(axis.size)},
        )
    if not np.all(np.isfinite(axis)):
        bad = int(np.argmax(~np.isfinite(axis)))
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "reference frequency axis contains a non-finite value",
            {"field": "frequency_hz", "flat_index": bad},
        )
    if mean.dtype != np.complex128:
        raise DomainError(
            ErrorCode.DTYPE_MISMATCH,
            "reference mean_data must be complex128 (the subtraction is a "
            "complex-domain contract; silent upcasting is not allowed)",
            {"dtype": str(mean.dtype)},
        )
    flat = mean.reshape(-1) if mean.ndim else mean[None]
    if not np.all(np.isfinite(flat)):
        bad = int(np.argmax(~np.isfinite(flat)))
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "reference mean_data contains a non-finite value",
            {"field": "mean_data", "flat_index": bad},
        )


class AirBackgroundSubtractionStage:
    """Complex frequency-domain air-background subtraction stage.

    Satisfies the frozen ISSUE-030 :class:`ProcessingStage` protocol.
    ``apply`` subtracts the reference ``mean_data`` from the input spectrum
    along the channel/frequency axes — vectorized over traces in one
    broadcast (every trace uses the SAME reference rows; no trace-axis
    statistic is ever computed, which is what separates this stage from
    Flat Reflection) — and appends exactly one ``air_background_subtraction``
    record.  Input models are never mutated; the output keeps the input's
    container type, shape, channels and per-trace metadata.  Data-domain
    protection: the reference's declared domain must match the data domain
    implied by the history (raw <-> raw, osl_calibrated <-> calibrated) and
    calibrated inputs additionally require strict calibration-profile
    provenance (id + recomputed content digest).  Re-applying the stage
    inside one history fails closed twice over (predecessor gate + core
    per-history stage-name uniqueness; a bumped ``stage_version`` does not
    bypass either).
    """

    def __init__(
        self,
        reference: AirBackgroundReference,
        reference_id: BackgroundReferenceId,
        *,
        current_calibration: OslCalibrationSet | None = None,
    ) -> None:
        if not isinstance(reference, AirBackgroundReference):
            raise TypeError(
                "reference must be an AirBackgroundReference, "
                f"got {type(reference).__name__}"
            )
        if not isinstance(reference_id, BackgroundReferenceId):
            raise TypeError(
                "reference_id must be a BackgroundReferenceId, "
                f"got {type(reference_id).__name__}"
            )
        if not isinstance(reference.domain, ReferenceDomain):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "reference.domain must be a ReferenceDomain",
                {"got": repr(reference.domain)},
            )
        if current_calibration is not None and not isinstance(
            current_calibration, OslCalibrationSet
        ):
            raise TypeError(
                "current_calibration must be an OslCalibrationSet or None, "
                f"got {type(current_calibration).__name__}"
            )
        if (
            reference.domain is ReferenceDomain.OSL_CALIBRATED
            and current_calibration is None
        ):
            # Strict calibrated-domain verification recomputes the bound
            # profile's content digest from a live set; without one the ID +
            # digest contract could silently degrade to ID-only matching, so
            # the requirement fails closed at construction time.
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "an osl_calibrated reference requires current_calibration: "
                "strict provenance verification must be able to recompute "
                "the bound profile's content digest",
                {"kind": "missing_current_calibration"},
            )
        _validate_reference_axis_finite(reference)
        self._reference = reference
        self._reference_id = reference_id
        self._current_calibration = current_calibration
        self._content_sha256 = background_reference_digest(reference)

    # -- ProcessingStage identity (D1) --------------------------------------

    @property
    def stage_name(self) -> str:
        return AIR_BACKGROUND_STAGE_NAME

    @property
    def stage_version(self) -> str:
        return AIR_BACKGROUND_STAGE_VERSION

    @property
    def input_domain(self) -> frozenset[DataDomain]:
        return _INPUT_DOMAINS

    @property
    def output_domain(self) -> DataDomain:
        return DataDomain.FREQUENCY_BACKGROUND_APPLIED

    # -- immutable configuration views --------------------------------------

    @property
    def reference(self) -> AirBackgroundReference:
        """The frozen air-background reference this stage applies (read-only)."""
        return self._reference

    @property
    def reference_id(self) -> BackgroundReferenceId:
        """The explicit identity recorded as ``background_reference_id`` (D2)."""
        return self._reference_id

    @property
    def parameters(self) -> Mapping[str, JsonValue]:
        """Canonical JSON-safe stage parameters recorded into every entry."""
        reference = self._reference
        return {
            "algorithm": AIR_BACKGROUND_ALGORITHM,
            "reference": {
                "reference_id": self._reference_id.to_json(),
                "domain": reference.domain.value,
                "calibration_profile_id": (
                    None
                    if reference.calibration_profile_id is None
                    else reference.calibration_profile_id.to_json()
                ),
                "channels": [
                    {
                        "channel_id": channel.channel_id,
                        "s_parameter": channel.s_parameter.value,
                    }
                    for channel in reference.channels
                ],
                "axis_content_sha256": hashlib.sha256(
                    _canonical(_encode_array(reference.frequency_hz, "float64"))
                ).hexdigest(),
                "mean_content_sha256": self._content_sha256,
                "trace_count": int(reference.trace_count),
            },
        }

    # -- application ---------------------------------------------------------

    def apply(
        self,
        source: FrequencySweep | FrequencyScan,
        *,
        history: ProcessingHistory,
        executed_utc: datetime | None = None,
        clock: Clock | None = None,
    ) -> StageResult:
        """Subtract the reference once and append the provenance record.

        ``executed_utc`` wins when given; otherwise the injected ``clock``
        (default: the system UTC clock) stamps the record.  No sleeping, no
        polling: the stamp is read once.
        """
        if not isinstance(history, ProcessingHistory):
            raise TypeError(
                f"history must be a ProcessingHistory, got {type(history).__name__}"
            )
        if not isinstance(source, (FrequencySweep, FrequencyScan)):
            raise TypeError(
                "air background input must be a FrequencySweep or "
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
                "air background input domain is not a legal predecessor: "
                "only frequency_raw or frequency_calibrated data may have an "
                "air background removed (background_applied/filtered/time "
                "domains mean an out-of-order or double subtraction)",
                {
                    "stage_name": AIR_BACKGROUND_STAGE_NAME,
                    "input_domain": input_domain.value,
                    "allowed_input_domains": [
                        domain.value for domain in sorted(_INPUT_DOMAINS, key=lambda d: d.value)
                    ],
                },
            )
        expected_reference_domain = _REFERENCE_DOMAIN_OF[input_domain]
        if self._reference.domain is not expected_reference_domain:
            raise DomainError(
                ErrorCode.PROCESSING_DOMAIN_MISMATCH,
                "air background reference domain does not match the data "
                "domain (a raw reference can never hit calibrated data and "
                "vice versa)",
                {
                    "stage_name": AIR_BACKGROUND_STAGE_NAME,
                    "input_domain": input_domain.value,
                    "reference_domain": self._reference.domain.value,
                    "required_reference_domain": expected_reference_domain.value,
                },
            )
        if source.data.shape[-1] != source.frequencies_hz.size:
            raise DomainError(
                ErrorCode.SHAPE_MISMATCH,
                "input data frequency axis length differs from frequencies_hz",
                {
                    "data_last_dim": int(source.data.shape[-1]),
                    "frequencies_hz_size": int(source.frequencies_hz.size),
                },
            )
        self._validate_contract(source)
        if input_domain is DataDomain.FREQUENCY_CALIBRATED:
            require_matching_calibration_provenance(
                history, self._reference, current_calibration=self._current_calibration
            )

        # Single numeric authority (D6): one complex subtraction broadcast
        # over the leading (trace/channel) axes.  Every trace row sees the
        # SAME reference rows — no trace-axis statistic is ever taken (that
        # would be Flat Reflection, excluded from ISSUE-033).
        reduced = source.data - self._reference.mean_data

        parameters = dict(self.parameters)
        reference_node_raw = parameters["reference"]
        assert isinstance(reference_node_raw, dict)
        reference_node = dict(reference_node_raw)
        calibration_profile_id: CalibrationProfileId | None = None
        if input_domain is DataDomain.FREQUENCY_CALIBRATED:
            assert self._reference.calibration_profile_id is not None
            calibration_profile_id = self._reference.calibration_profile_id
            # D8: calibrated-lineage records explicitly re-carry the matching
            # calibration reference (self-contained auditability; core
            # provenance continuity passes because the id is strictly equal).
            record_digest = self._recorded_profile_digest(history)
            reference_node["calibration_profile_content_sha256"] = record_digest
        parameters["reference"] = reference_node

        # The output model is rebuilt *before* any history mutation so a
        # rejected append can never leave half-written provenance behind.
        output_source: FrequencySweep | FrequencyScan
        if isinstance(source, FrequencySweep):
            output_source = FrequencySweep(
                channels=source.channels,
                frequencies_hz=source.frequencies_hz,
                data=reduced,
                metadata=source.metadata,
            )
        else:
            output_source = FrequencyScan(
                channels=source.channels,
                frequencies_hz=source.frequencies_hz,
                data=reduced,
                metadata=source.metadata,
            )

        record = ProcessingRecord(
            stage_name=AIR_BACKGROUND_STAGE_NAME,
            stage_version=AIR_BACKGROUND_STAGE_VERSION,
            parameters=parameters,
            input_domain=input_domain,
            output_domain=DataDomain.FREQUENCY_BACKGROUND_APPLIED,
            executed_utc=stamp,
            software_version=_SOFTWARE_VERSION,
            background_reference_id=self._reference_id,
            calibration_profile_id=calibration_profile_id,
        )
        new_history = history.append(record)

        return StageResult(
            source=output_source,
            history=new_history,
            domain=DataDomain.FREQUENCY_BACKGROUND_APPLIED,
        )

    # -- helpers --------------------------------------------------------------

    def _recorded_profile_digest(self, history: ProcessingHistory) -> str:
        """Digest of the profile entry matched during the provenance check.

        Called only after :func:`require_matching_calibration_provenance`
        passed, so the entry and the live recomputation exist and agree.
        """
        record = _calibrated_record_of(history)
        assert record is not None
        assert self._reference.calibration_profile_id is not None
        target = self._reference.calibration_profile_id.to_json()
        entries = record.parameters.get("profiles")
        assert isinstance(entries, list)
        for node in entries:
            if isinstance(node, Mapping) and node.get("profile_id") == target:
                digest = node.get("content_sha256")
                assert isinstance(digest, str)
                return digest
        raise AssertionError("matched provenance entry vanished")  # pragma: no cover

    def _validate_contract(self, source: FrequencySweep | FrequencyScan) -> None:
        """Ordered channel/axis equality between source and reference (D5 ①②)."""
        reference = self._reference
        if source.channels != tuple(reference.channels):
            first_diff = next(
                (
                    index
                    for index, (left, right) in enumerate(
                        zip(source.channels, reference.channels, strict=False)
                    )
                    if left != right
                ),
                min(len(source.channels), len(reference.channels)),
            )
            raise DomainError(
                ErrorCode.CHANNEL_CONTRACT_MISMATCH,
                "source channels do not exactly match the reference channel "
                "binding (order, ids and S parameters compared positionally)",
                {
                    "first_difference_index": int(first_diff),
                    "left_channel_ids": [c.channel_id for c in source.channels],
                    "right_channel_ids": [c.channel_id for c in reference.channels],
                },
            )
        if not np.array_equal(source.frequencies_hz, reference.frequency_hz):
            raise DomainError(
                ErrorCode.AXIS_MISMATCH,
                "source frequency axis differs from the reference axis "
                "(full element-wise equality required)",
                {
                    "source_size": int(source.frequencies_hz.size),
                    "reference_size": int(reference.frequency_hz.size),
                },
            )
        mean = np.asarray(reference.mean_data)
        expected = (len(source.channels), int(source.frequencies_hz.size))
        if mean.ndim != 2 or mean.shape != expected:
            raise DomainError(
                ErrorCode.SHAPE_MISMATCH,
                "reference mean_data shape must be channel x frequency "
                "matching the source channels and axis",
                {
                    "expected": [int(n) for n in expected],
                    "got": [int(n) for n in mean.shape],
                    "ndim": int(mean.ndim),
                },
            )


# Structural protocol assertion (module import fails fast on drift, D1).
def _protocol_probe_stage() -> ProcessingStage:
    probe = AirBackgroundSubtractionStage(
        AirBackgroundReference(
            channels=(
                ChannelSpec(
                    "protocol_probe",
                    LogicalPolarization.HH,
                    SParameter.S11,
                    "probe",
                ),
            ),
            frequency_hz=np.array([1.0e8, 2.0e8], dtype=np.float64),
            mean_data=np.zeros((1, 2), dtype=np.complex128),
            trace_count=1,
            domain=ReferenceDomain.RAW,
            calibration_profile_id=None,
        ),
        BackgroundReferenceId("00000000-0000-4000-8000-000000000000"),
    )
    assert isinstance(probe, ProcessingStage)
    return probe


_PROTOCOL_PROBE: Final[ProcessingStage] = _protocol_probe_stage()
