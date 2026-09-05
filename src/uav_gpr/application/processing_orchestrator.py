"""ISSUE-036: the single ground-side processing orchestration chain.

This module is the **only** complete-chain implementation in the product
(ISSUE-036, docs/PROCESSING.md section 2).  It composes the frozen, independent
stages delivered by ISSUE-030/031/032/033/034/035 in the canonical order::

    frequency_raw
      -> optional OSL calibration            -> frequency_calibrated
      -> calibrated snapshot (materialized)  (OSL-after / background-before)
      -> optional air background subtraction -> frequency_background_applied
      -> optional frequency bandpass         -> frequency_filtered
      -> IFFT (always)                       -> time_base
      -> optional Dewow                      -> time_processed
      -> optional Flat Reflection filter     -> time_processed

It adds exactly three orchestration-level capabilities on top of those stages
and nothing else:

1. **Two strict entries** (:data:`ENTRY_FRESH_RAW` /
   :data:`ENTRY_SAFE_REPLAY_REUSE`).  Fresh processing must start from an empty
   history over raw frequency data (docs/PROCESSING.md section 1: a history's
   first record consumes ``frequency_raw``; starting from a derived snapshot
   would need a provenance anchor that does not exist yet).  Safe replay reuse
   accepts only a :class:`CalibratedSnapshot` whose recorded provenance matches
   the requested calibration strictly — the judgement is delegated verbatim to
   the ISSUE-032 / ISSUE-033 authorities (:func:`require_safe_reuse`,
   :func:`require_matching_calibration_provenance`) and never re-implemented
   here, so a reused chain provably performs no second OSL/background.
2. **Processing revision and cancellation** (:class:`ProcessingController`,
   :class:`ProcessingToken`, :class:`StaleProcessingResult`): parameter changes
   open a new monotonic revision, stale worker results are dropped at stage
   boundaries and late publications never overwrite a newer visible result.
   Dropping affects display/derived output only — raw storage is untouched.
3. **Controlled derived-data attachment** (:func:`attach_derived_result`,
   :class:`DerivedAttachmentWriter`): derived arrays and the serialized history
   are written back into a ground ``.rcscan`` through a narrow gate that may
   only create schema-declared *optional* datasets (ISSUE-008 contracts), reads
   the file with the strict ISSUE-011 reader before accepting anything, and
   verifies that ``/frequency/raw`` plus every trace-major required column are
   byte-identical before and after (AGENTS.md section 3, docs/DATA_FORMAT.md
   sections 2/3/3.1).

Boundaries (deliberate non-goals): no UI, no realtime incremental preview, no
time-zero or continuous-background stages (both still future per
docs/PROCESSING.md section 2), no thread ownership (the controller is bounded,
observable state; M09 hosts it), no ``.rcscan`` v2 schema extension — see
docs/plans/2026-09-05-issue-036-orchestration.md decisions D5/D6 for where the
calibrated-stage history is persisted instead.

Inputs are never mutated: every stage returns fresh immutable core models, this
module only sequences them, and arrays handed to storage are copied out of
read-only buffers.
"""

from __future__ import annotations

import base64
import hashlib
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

import h5py  # type: ignore[import-untyped]
import numpy as np

from uav_gpr.calibration.osl import OslCalibrationSet
from uav_gpr.calibration.reference import AirBackgroundReference, ReferenceDomain
from uav_gpr.core.enums import DataDomain, EndpointRole
from uav_gpr.core.errors import DomainError, ErrorCode, JsonValue
from uav_gpr.core.frequency import FrequencyScan, FrequencySweep
from uav_gpr.core.identifiers import BackgroundReferenceId
from uav_gpr.core.time_domain import ProcessingHistory, ProcessingRecord, TimeDomainScan
from uav_gpr.core.timeutil import Clock, SystemClock, ensure_utc
from uav_gpr.processing.background_subtraction import (
    AIR_BACKGROUND_STAGE_NAME,
    AirBackgroundSubtractionStage,
    require_matching_calibration_provenance,
)
from uav_gpr.processing.bandpass import (
    BANDPASS_STAGE_NAME,
    BandpassStage,
    StageResult,
)
from uav_gpr.processing.dewow import DEWOW_STAGE_NAME, DewowStage
from uav_gpr.processing.flat_reflection import FLAT_STAGE_NAME, FlatReflectionFilterStage
from uav_gpr.processing.osl_calibration import (
    OSL_CALIBRATION_STAGE_NAME,
    OslCalibrationStage,
    osl_set_digest,
    require_safe_reuse,
)
from uav_gpr.processing.time_domain import (
    IFFT_STAGE_NAME,
    DisplayCropConfig,
    DisplayTimeWindowView,
    FrequencyToTimeStage,
)
from uav_gpr.storage import rcscan_v2 as schema

__all__ = [
    "AIR_BACKGROUND_STAGE_NAME",
    "BANDPASS_STAGE_NAME",
    "DEWOW_STAGE_NAME",
    "ENTRY_FRESH_RAW",
    "ENTRY_SAFE_REPLAY_REUSE",
    "FLAT_STAGE_NAME",
    "IFFT_STAGE_NAME",
    "OSL_CALIBRATION_STAGE_NAME",
    "PROCESSING_ORDER",
    "AirBackgroundSelection",
    "AttachmentError",
    "CalibratedSnapshot",
    "DerivedAttachmentError",
    "DerivedAttachmentWriter",
    "DerivedWritePayload",
    "Entry",
    "ProcessedMission",
    "ProcessingController",
    "ProcessingProfile",
    "ProcessingRequest",
    "ProcessingRevision",
    "ProcessingToken",
    "StaleProcessingResult",
    "VisibleState",
    "archive_to_schema_grid",
    "archived_frequency_points",
    "assert_raw_bytes_unchanged",
    "attach_derived_result",
    "derived_contract_for",
    "derived_shapes_match_schema_default",
    "run_processing",
]

#: Canonical chain order (docs/PROCESSING.md section 2, plan decision D1).
PROCESSING_ORDER: Final[tuple[str, ...]] = (
    OSL_CALIBRATION_STAGE_NAME,
    AIR_BACKGROUND_STAGE_NAME,
    BANDPASS_STAGE_NAME,
    IFFT_STAGE_NAME,
    DEWOW_STAGE_NAME,
    FLAT_STAGE_NAME,
)

#: The two strict entries (plan decision D3/D4).
ENTRY_FRESH_RAW: Final = "fresh_raw"
ENTRY_SAFE_REPLAY_REUSE: Final = "safe_replay_reuse"

_FREQUENCY_CONTAINERS = (FrequencySweep, FrequencyScan)
_WRITER_VERSION: Final = "uav-gpr.issue036.1"

#: Lifecycle states a file must be in before derived data may be attached
#: (docs/DATA_FORMAT.md section 4: ``writing`` files have a live owner).
_SETTLED_LIFECYCLE_STATES: Final[frozenset[str]] = frozenset({"finalized", "recovered"})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class StaleProcessingResult(RuntimeError):
    """A worker result belongs to a superseded or cancelled revision.

    Expected control flow, not a fault: the payload is dropped before it can
    reach display or derived storage, and raw data is never involved.
    """

    def __init__(
        self,
        *,
        revision: int,
        current_revision: int,
        cancelled: bool,
        stage_name: str | None = None,
    ) -> None:
        super().__init__(
            f"processing result for revision {revision} is stale "
            f"(current revision {current_revision}"
            f"{', revision cancelled' if cancelled else ''})"
        )
        self.revision = revision
        self.current_revision = current_revision
        self.cancelled = cancelled
        self.stage_name = stage_name

    def to_dict(self) -> dict[str, JsonValue]:
        """JSON-safe description for logs and diagnostics."""
        return {
            "revision": self.revision,
            "current_revision": self.current_revision,
            "cancelled": self.cancelled,
            "stage_name": self.stage_name,
        }


class AttachmentError(DomainError):
    """Base class for controlled-storage refusals (fail-closed, no writes)."""


class DerivedAttachmentError(AttachmentError):
    """The derived payload may not be attached to this ground file."""


# ---------------------------------------------------------------------------
# Configuration model (plan decision D2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AirBackgroundSelection:
    """An air-background reference plus the live calibration it binds to.

    ``current_calibration`` is required for ``osl_calibrated`` references by
    the ISSUE-033 stage itself (strict ID + content-digest verification); it is
    carried here so the orchestrator can hand the same authority to both the
    safe-reuse check and the stage without letting callers diverge.
    """

    reference: AirBackgroundReference
    reference_id: BackgroundReferenceId
    current_calibration: OslCalibrationSet | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.reference, AirBackgroundReference):
            raise TypeError(
                "reference must be an AirBackgroundReference, "
                f"got {type(self.reference).__name__}"
            )
        if not isinstance(self.reference_id, BackgroundReferenceId):
            raise TypeError(
                "reference_id must be a BackgroundReferenceId, "
                f"got {type(self.reference_id).__name__}"
            )
        if self.current_calibration is not None and not isinstance(
            self.current_calibration, OslCalibrationSet
        ):
            raise TypeError("current_calibration must be an OslCalibrationSet or None")
        if (
            self.reference.domain is ReferenceDomain.OSL_CALIBRATED
            and self.current_calibration is None
        ):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "an osl_calibrated background selection requires the live "
                "calibration used to verify its profile provenance",
                {"kind": "missing_current_calibration"},
            )

    def describe(self) -> dict[str, JsonValue]:
        """Content-addressed description for the profile digest."""
        from uav_gpr.processing.background_subtraction import (
            background_reference_digest,
        )

        return {
            "reference_id": str(self.reference_id),
            "reference_content_sha256": background_reference_digest(self.reference),
            "reference_domain": self.reference.domain.value,
            "bound_calibration_profile_id": (
                None
                if self.reference.calibration_profile_id is None
                else str(self.reference.calibration_profile_id)
            ),
            "live_calibration_digest": (
                None
                if self.current_calibration is None
                else osl_set_digest(self.current_calibration)
            ),
        }


def _describe_calibration(calibration: OslCalibrationSet) -> dict[str, JsonValue]:
    entries: list[JsonValue] = [dict(item) for item in _osl_provenance(calibration)]
    return {"set_content_sha256": osl_set_digest(calibration), "profiles": entries}


@dataclass(frozen=True, slots=True)
class ProcessingProfile:
    """Immutable selection of enabled stages and their parameters.

    Every disabled stage is ``None``; enabling a stage means passing its
    configuration object.  ``profile_digest()`` covers only content addresses
    (calibration/set digests, reference digest, numeric stage parameters), so a
    re-solved profile with the same ID yields a different digest and can never
    masquerade as the same processing revision.
    """

    calibration: OslCalibrationSet | None = None
    background: AirBackgroundSelection | None = None
    bandpass_edges_hz: tuple[float, float, float, float] | None = None
    ifft_oversampling: int = 16
    dewow_window_s: float | None = None
    flat_window_traces: int | None = None

    def __post_init__(self) -> None:
        if self.calibration is not None and not isinstance(
            self.calibration, OslCalibrationSet
        ):
            raise TypeError(
                "calibration must be an OslCalibrationSet or None, "
                f"got {type(self.calibration).__name__}"
            )
        if self.background is not None and not isinstance(
            self.background, AirBackgroundSelection
        ):
            raise TypeError("background must be an AirBackgroundSelection or None")
        oversampling = self.ifft_oversampling
        if isinstance(oversampling, bool) or not isinstance(oversampling, int):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "ifft_oversampling must be an int",
                {"kind": "bad_oversampling", "got": repr(oversampling)},
            )
        if oversampling < 1:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "ifft_oversampling must be >= 1",
                {"kind": "bad_oversampling", "ifft_oversampling": oversampling},
            )
        if self.bandpass_edges_hz is not None:
            edges = tuple(self.bandpass_edges_hz)
            if len(edges) != 4:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "bandpass edges must contain exactly f1, f2, f3, f4",
                    {"kind": "bad_bandpass_edges", "count": len(edges)},
                )
            object.__setattr__(self, "bandpass_edges_hz", edges)
        if self.dewow_window_s is not None and (
            isinstance(self.dewow_window_s, bool)
            or not isinstance(self.dewow_window_s, (int, float))
        ):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "dewow_window_s must be a real number or None",
                {"kind": "bad_dewow_window"},
            )
        if self.flat_window_traces is not None and (
            isinstance(self.flat_window_traces, bool)
            or not isinstance(self.flat_window_traces, int)
        ):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "flat_window_traces must be an int or None",
                {"kind": "bad_flat_window"},
            )

    # -- enablement ---------------------------------------------------------

    @property
    def osl_enabled(self) -> bool:
        return self.calibration is not None

    @property
    def background_enabled(self) -> bool:
        return self.background is not None

    @property
    def bandpass_enabled(self) -> bool:
        return self.bandpass_edges_hz is not None

    @property
    def dewow_enabled(self) -> bool:
        return self.dewow_window_s is not None

    @property
    def flat_enabled(self) -> bool:
        return self.flat_window_traces is not None

    @property
    def time_stage_enabled(self) -> bool:
        """Whether any post-IFFT time-domain stage runs (acceptance 5)."""
        return self.dewow_enabled or self.flat_enabled

    @property
    def enabled_stages(self) -> tuple[str, ...]:
        """Enabled stage names in canonical chain order (IFFT always on)."""
        chosen = {
            OSL_CALIBRATION_STAGE_NAME: self.osl_enabled,
            AIR_BACKGROUND_STAGE_NAME: self.background_enabled,
            BANDPASS_STAGE_NAME: self.bandpass_enabled,
            IFFT_STAGE_NAME: True,
            DEWOW_STAGE_NAME: self.dewow_enabled,
            FLAT_STAGE_NAME: self.flat_enabled,
        }
        return tuple(name for name in PROCESSING_ORDER if chosen[name])

    def describe(self) -> dict[str, JsonValue]:
        """Canonical JSON-safe description (content addresses only)."""
        return {
            "schema": "uav_gpr.processing_profile.v1",
            "osl": None if self.calibration is None else _describe_calibration(self.calibration),
            "air_background": (
                None if self.background is None else self.background.describe()
            ),
            "bandpass": (
                None
                if self.bandpass_edges_hz is None
                else {"edges_hz": [float(edge) for edge in self.bandpass_edges_hz]}
            ),
            "ifft": {"oversampling_factor": int(self.ifft_oversampling)},
            "dewow": (
                None
                if self.dewow_window_s is None
                else {"window_s": float(self.dewow_window_s)}
            ),
            "flat_reflection": (
                None
                if self.flat_window_traces is None
                else {"window_traces": int(self.flat_window_traces)}
            ),
        }

    def profile_digest(self) -> str:
        """SHA-256 over the canonical description (revision identity input)."""
        return hashlib.sha256(_canonical_bytes(self.describe())).hexdigest()


def _canonical_json(value: object) -> str:
    """Strict canonical JSON text (sorted keys, no NaN/Infinity)."""
    return schema.dumps_utf8_json(value)


def _canonical_bytes(value: object) -> bytes:
    return _canonical_json(value).encode("utf-8")


# ---------------------------------------------------------------------------
# Calibrated snapshot (plan decisions D4/D6)
# ---------------------------------------------------------------------------

_ARRAY_DTYPES: Final[Mapping[str, str]] = MappingProxyType({"complex128": "<c16"})


def _encode_array(values: np.ndarray) -> dict[str, JsonValue]:
    dtype = np.dtype(values.dtype)
    tag = {np.dtype("<c16"): "complex128"}.get(dtype)
    if tag is None:
        raise DomainError(
            ErrorCode.DTYPE_MISMATCH,
            "snapshot arrays must be complex128",
            {"dtype": str(dtype)},
        )
    contiguous = np.ascontiguousarray(values, dtype=dtype)
    return {
        "dtype": tag,
        "shape": [int(size) for size in contiguous.shape],
        "bytes_b64": base64.b64encode(contiguous.tobytes()).decode("ascii"),
    }


def _decode_array(node: object) -> np.ndarray:
    if not isinstance(node, Mapping):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT, "encoded array must be an object", {}
        )
    tag = node.get("dtype")
    shape = node.get("shape")
    payload = node.get("bytes_b64")
    if not isinstance(tag, str) or tag not in _ARRAY_DTYPES:
        raise DomainError(
            ErrorCode.DTYPE_MISMATCH,
            "unsupported snapshot array dtype",
            {"dtype": repr(tag)},
        )
    if not isinstance(shape, list) or not all(
        isinstance(size, int) and not isinstance(size, bool) and size >= 0
        for size in shape
    ):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT, "snapshot shape must be an int list", {}
        )
    if not isinstance(payload, str):
        raise DomainError(ErrorCode.INVALID_ARGUMENT, "snapshot payload must be text", {})
    try:
        raw = base64.b64decode(payload.encode("ascii"), validate=True)
    except Exception as error:  # pragma: no cover - defensive
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT, "snapshot payload is not valid base64", {}
        ) from error
    dtype = np.dtype(_ARRAY_DTYPES[tag])
    expected = int(np.prod([1, *shape])) * dtype.itemsize
    if len(raw) != expected:
        raise DomainError(
            ErrorCode.SHAPE_MISMATCH,
            "snapshot payload length does not match its shape",
            {"expected_bytes": expected, "got_bytes": len(raw)},
        )
    return np.frombuffer(raw, dtype=dtype).reshape(tuple(int(s) for s in shape))


@dataclass(frozen=True, slots=True)
class CalibratedSnapshot:
    """The OSL-after / background-before frequency state plus its full history.

    The history is the *complete* chain from ``frequency_raw`` (core forbids
    starting a history from a derived snapshot), which is exactly what makes
    reuse auditable: reusing this snapshot skips recomputing OSL but keeps every
    provenance record that produced it.
    """

    source: FrequencySweep | FrequencyScan
    history: ProcessingHistory
    calibration_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.source, _FREQUENCY_CONTAINERS):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "a calibrated snapshot carries frequency-domain data only",
                {"got": type(self.source).__name__},
            )
        if not isinstance(self.history, ProcessingHistory):
            raise TypeError(
                f"history must be a ProcessingHistory, got {type(self.history).__name__}"
            )
        if not isinstance(self.calibration_digest, str) or len(
            self.calibration_digest
        ) != 64:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "calibration_digest must be a 64-character hex digest",
                {"kind": "bad_digest"},
            )
        if not self.history.records:
            raise DomainError(
                ErrorCode.PROCESSING_DOMAIN_MISMATCH,
                "a calibrated snapshot requires a non-empty history ending in "
                "frequency_calibrated",
                {"kind": "empty_history"},
            )
        last = self.history.records[-1]
        if last.output_domain is not DataDomain.FREQUENCY_CALIBRATED:
            raise DomainError(
                ErrorCode.PROCESSING_DOMAIN_MISMATCH,
                "a calibrated snapshot history must end in frequency_calibrated",
                {
                    "kind": "wrong_last_domain",
                    "last_output_domain": last.output_domain.value,
                },
            )
        recorded = last.parameters.get("set_content_sha256")
        if recorded is not None and recorded != self.calibration_digest:
            raise DomainError(
                ErrorCode.PROCESSING_DOMAIN_MISMATCH,
                "calibration_digest differs from the recorded set digest",
                {
                    "kind": "digest_mismatch",
                    "recorded": repr(recorded),
                    "given": self.calibration_digest,
                },
            )

    @property
    def record_count(self) -> int:
        return len(self.history.records)

    def to_dict(self) -> dict[str, JsonValue]:
        """Serializable snapshot (arrays inline, history as records)."""
        return {
            "source_kind": type(self.source).__name__,
            "channels": [
                {
                    "channel_id": channel.channel_id,
                    "logical_polarization": channel.logical_polarization.value,
                    "s_parameter": channel.s_parameter.value,
                    "display_name": channel.display_name,
                    "antenna_note": channel.antenna_note,
                }
                for channel in self.source.channels
            ],
            "frequencies_hz": [float(hz) for hz in self.source.frequencies_hz],
            "data": _encode_array(self.source.data),
            "history": [record.to_dict() for record in self.history.records],
            "calibration_digest": self.calibration_digest,
        }

    @classmethod
    def from_dict(cls, node: Mapping[str, object]) -> CalibratedSnapshot:
        """Rebuild a snapshot (fail-closed on any malformed field)."""
        if not isinstance(node, Mapping):
            raise TypeError("snapshot payload must be a mapping")
        try:
            frequencies = np.asarray(node["frequencies_hz"], dtype="<f8")
            data = _decode_array(node["data"])
            history_node = node["history"]
            if not isinstance(history_node, list):
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT, "history must be a list", {}
                )
            history = ProcessingHistory(
                tuple(ProcessingRecord.from_dict(entry) for entry in history_node)
            )
            channels_payload = node["channels"]
            digest = node["calibration_digest"]
        except KeyError as error:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "snapshot payload is missing a required field",
                {"field": str(error)},
            ) from error
        from uav_gpr.core.channels import ChannelSpec
        from uav_gpr.core.enums import LogicalPolarization, SParameter

        if not isinstance(channels_payload, list) or not channels_payload:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT, "channels must be a non-empty list", {}
            )
        channels: list[ChannelSpec] = []
        for item in channels_payload:
            if not isinstance(item, Mapping):
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT, "channel entry must be an object", {}
                )
            note = item.get("antenna_note")
            try:
                channels.append(
                    ChannelSpec(
                        channel_id=str(item["channel_id"]),
                        logical_polarization=LogicalPolarization.from_json(
                            str(item["logical_polarization"])
                        ),
                        s_parameter=SParameter.from_json(str(item["s_parameter"])),
                        display_name=str(item["display_name"]),
                        antenna_note=None if note is None else str(note),
                    )
                )
            except (KeyError, TypeError, ValueError) as error:
                raise DomainError(
                    ErrorCode.CHANNEL_CONTRACT_MISMATCH,
                    "snapshot channel entry does not round-trip a valid spec",
                    {"kind": "bad_channel_entry"},
                ) from error
        kind = str(node.get("source_kind", ""))
        rebuilt: FrequencySweep | FrequencyScan
        if kind == "FrequencySweep":
            rebuilt = FrequencySweep(
                channels=tuple(channels), frequencies_hz=frequencies, data=data
            )
        elif kind == "FrequencyScan":
            rebuilt = FrequencyScan(
                channels=tuple(channels), frequencies_hz=frequencies, data=data
            )
        else:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "snapshot source_kind must be FrequencySweep or FrequencyScan",
                {"source_kind": kind},
            )
        return cls(
            source=rebuilt,
            history=history,
            calibration_digest=str(digest),
        )


# ---------------------------------------------------------------------------
# Revision control (plan decision D7)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProcessingRevision:
    """A strictly positive, monotonically increasing processing revision."""

    value: int

    def __post_init__(self) -> None:
        if isinstance(self.value, bool) or not isinstance(self.value, int):
            raise TypeError(f"revision must be an int, got {type(self.value).__name__}")
        if self.value < 1:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "processing revisions start at 1",
                {"revision": self.value},
            )


class Entry(StrEnum):
    """The two strict processing entries (issue acceptance 2)."""

    FRESH_RAW = ENTRY_FRESH_RAW
    SAFE_REPLAY_REUSE = ENTRY_SAFE_REPLAY_REUSE


@dataclass(frozen=True, slots=True)
class VisibleState:
    """Bounded, serializable view of the controller (AGENTS.md section 7)."""

    current_revision: int
    visible_revision: int | None
    cancelled_revisions: tuple[int, ...]
    accepted: int
    dropped: int
    stale_publications: int

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "current_revision": self.current_revision,
            "visible_revision": self.visible_revision,
            "cancelled_revisions": list(self.cancelled_revisions),
            "accepted": self.accepted,
            "dropped": self.dropped,
            "stale_publications": self.stale_publications,
        }


@dataclass(slots=True)
class ProcessingToken:
    """One worker's claim on a revision; checked at every stage boundary."""

    controller: ProcessingController
    revision: int
    _finished: bool = False

    def checkpoint(self, stage_name: str | None = None) -> None:
        """Raise :class:`StaleProcessingResult` when superseded or cancelled."""
        self.controller.require_live(self.revision, stage_name=stage_name)

    def finish(self) -> None:
        """Mark the attempt finished (idempotent bookkeeping)."""
        self._finished = True
        self.controller._attempt_finished(self.revision)


class ProcessingController:
    """Owned state for revisions, publication and cancellation.

    Single-thread friendly and thread-safe: mutations happen under a lock, no
    polling, no sleeping, no unbounded growth (bookkeeping is bounded by
    ``history_limit``).
    """

    def __init__(self, *, initial_revision: int = 0, history_limit: int = 32) -> None:
        if isinstance(initial_revision, bool) or not isinstance(initial_revision, int):
            raise TypeError("initial_revision must be an int")
        if initial_revision < 0:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "initial_revision must be >= 0",
                {"initial_revision": initial_revision},
            )
        limit = history_limit
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "history_limit must be a positive int",
                {"history_limit": repr(limit)},
            )
        import threading

        self._lock = threading.Lock()
        self._current = initial_revision
        self._visible: ProcessedMission | None = None
        self._cancelled: set[int] = set()
        self._running: set[int] = set()
        self._history_limit = limit
        self._accepted = 0
        self._dropped = 0
        self._stale_publications = 0

    # -- revision lifecycle -------------------------------------------------

    @property
    def current_revision(self) -> int:
        with self._lock:
            return self._current

    def begin(self, revision: int | ProcessingRevision) -> ProcessingToken:
        """Open work for ``revision``; must be strictly newer than current."""
        wanted = revision.value if isinstance(revision, ProcessingRevision) else revision
        if isinstance(wanted, bool) or not isinstance(wanted, int):
            raise TypeError("revision must be an int or ProcessingRevision")
        if wanted < 1:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "processing revisions start at 1",
                {"revision": wanted},
            )
        with self._lock:
            if wanted < self._current or (
                wanted == self._current and wanted in self._running
            ):
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "a new processing run must use a live revision that no other "
                    "attempt currently owns",
                    {"revision": wanted, "current_revision": self._current},
                )
            self._current = max(self._current, wanted)
            self._cancelled.discard(wanted)
            self._running.add(wanted)
            self._trim_locked()
            return ProcessingToken(controller=self, revision=wanted)

    def cancel(self, revision: int | ProcessingRevision) -> bool:
        """Cancel one revision; returns False when it is already settled."""
        wanted = revision.value if isinstance(revision, ProcessingRevision) else revision
        with self._lock:
            if wanted > self._current or wanted in self._cancelled:
                return False
            self._cancelled.add(wanted)
            self._running.discard(wanted)
            self._dropped += 1
            self._trim_locked()
            return True

    def require_live(self, revision: int, *, stage_name: str | None = None) -> None:
        """Fail with :class:`StaleProcessingResult` unless ``revision`` is live."""
        with self._lock:
            current = self._current
            cancelled = revision in self._cancelled
            stale = revision != current or cancelled
            if stale:
                self._dropped += 1
        if stale:
            raise StaleProcessingResult(
                revision=revision,
                current_revision=current,
                cancelled=cancelled,
                stage_name=stage_name,
            )

    def accepts(self, revision: int) -> bool:
        """Whether a result of ``revision`` could currently be published."""
        with self._lock:
            return (
                revision == self._current
                and revision not in self._cancelled
                and (self._visible is None or revision >= self._visible.revision)
            )

    def publish(self, result: ProcessedMission) -> ProcessedMission | None:
        """Publish the newest visible result (older attempts never overwrite).

        Returns the currently visible mission: the freshly published one, the
        identical earlier publication (idempotent retry), or the newer result
        that won the race.
        """
        if not isinstance(result, ProcessedMission):
            raise TypeError("result must be a ProcessedMission")
        with self._lock:
            if self._visible is not None:
                if result.revision == self._visible.revision:
                    if result is self._visible:
                        return self._visible
                    self._stale_publications += 1
                    return self._visible
                if result.revision < self._visible.revision:
                    self._stale_publications += 1
                    self._dropped += 1
                    return self._visible
            if result.revision in self._cancelled or result.revision != self._current:
                self._stale_publications += 1
                self._dropped += 1
                return self._visible
            self._visible = result
            self._accepted += 1
            self._running.discard(result.revision)
            self._trim_locked()
            return result

    def _attempt_finished(self, revision: int) -> None:
        with self._lock:
            self._running.discard(revision)

    def _trim_locked(self) -> None:
        keep_from = self._current - self._history_limit + 1
        if keep_from <= 1:
            return
        self._cancelled = {value for value in self._cancelled if value >= keep_from}
        self._running = {value for value in self._running if value >= keep_from}

    def snapshot(self) -> VisibleState:
        with self._lock:
            return VisibleState(
                current_revision=self._current,
                visible_revision=None if self._visible is None else self._visible.revision,
                cancelled_revisions=tuple(sorted(self._cancelled)),
                accepted=self._accepted,
                dropped=self._dropped,
                stale_publications=self._stale_publications,
            )


# ---------------------------------------------------------------------------
# Request / result objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProcessingRequest:
    """One orchestration call: profile + input + entry + deterministic clocking.

    Entry-dependent meaning of ``history`` (documented deliberately, since both
    entries carry the field):

    * :data:`ENTRY_FRESH_RAW` — ``history`` **is** the history to start from and
      must be empty; a non-empty value is refused fail-closed
      (:meth:`_require_empty_history`).
    * :data:`ENTRY_SAFE_REPLAY_REUSE` — the authoritative history is always
      ``snapshot.history`` (the chain validated by the ISSUE-032/033 safe-reuse
      authorities).  ``request.history`` is therefore **not consulted at all on
      this path**: it exists so both entries share one request type.  Passing a
      different history here cannot inject provenance (the snapshot's own
      records are what the stages append to), but callers should leave it empty
      to keep the intent unambiguous.

    ``display_start_s`` / ``display_duration_s`` configure the *display* window
    only (AGENTS.md section 8): they never enter the processing history and
    never alter archived arrays.
    """

    profile: ProcessingProfile
    source: FrequencySweep | FrequencyScan
    history: ProcessingHistory = field(default_factory=ProcessingHistory)
    entry: str = ENTRY_FRESH_RAW
    snapshot: CalibratedSnapshot | None = None
    executed_utc: datetime | None = None
    clock: Clock | None = None
    display_start_s: float | None = None
    display_duration_s: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.profile, ProcessingProfile):
            raise TypeError(
                f"profile must be a ProcessingProfile, got {type(self.profile).__name__}"
            )
        if self.entry not in tuple(Entry):  # the two strict entries only
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "unknown processing entry; use ENTRY_FRESH_RAW or "
                "ENTRY_SAFE_REPLAY_REUSE",
                {"entry": self.entry},
            )
        if not isinstance(self.history, ProcessingHistory):
            raise TypeError(
                f"history must be a ProcessingHistory, got {type(self.history).__name__}"
            )
        if self.executed_utc is not None:
            ensure_utc(self.executed_utc)
        if (self.display_start_s is None) != (self.display_duration_s is None):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "display crop requires both display_start_s and "
                "display_duration_s",
                {"kind": "partial_display_crop"},
            )


@dataclass(frozen=True, slots=True)
class ProcessedMission:
    """The outcome of one orchestrated chain (plan decision D8).

    ``time_base`` always exists (IFFT is unconditional); ``time_processed``
    exists only when Dewow and/or Flat ran.  ``calibrated_snapshot`` exists only
    when OSL ran or was safely reused.
    """

    profile_digest: str
    entry: str
    revision: int
    source_input: FrequencySweep | FrequencyScan
    input_container_before_ifft: FrequencySweep | FrequencyScan
    history: ProcessingHistory
    time_base: TimeDomainScan
    time_processed: TimeDomainScan | None
    final_domain: DataDomain
    calibrated_snapshot: CalibratedSnapshot | None
    executed_utc: datetime
    applied_stages: tuple[str, ...]
    reused_calibrated: bool
    display_view: DisplayTimeWindowView | None
    _derived_override: Mapping[str, Any] | None = None

    @property
    def domain_of_history_last(self) -> DataDomain:
        """The ending domain of the authoritative history (parity assertion)."""
        return self.history.records[-1].output_domain

    @property
    def calibrated_record_count(self) -> int:
        """Records up to and including ``osl_calibration`` (0 when disabled)."""
        for index, record in enumerate(self.history.records, start=1):
            if record.stage_name == OSL_CALIBRATION_STAGE_NAME:
                return index
        return 0

    @property
    def input_frequency_source(self) -> FrequencySweep | FrequencyScan:
        """The last frequency-domain container produced by the chain.

        For a fresh raw request whose chain stopped before any frequency stage,
        this is the raw input; once OSL ran it is the calibrated data.  Handing
        it back through the fresh entry is refused by the empty-history gate,
        which is what makes a second OSL or a second background subtraction
        impossible through this module.
        """
        return self.input_container_before_ifft

    @property
    def time_axis_s(self) -> np.ndarray:
        """Read-only physical time axis of the archived ``time_base``."""
        return self.time_base.time_axis_s

    def history_json(self) -> str:
        """Canonical serialization of the full record list (storage payload)."""
        return _canonical_json([record.to_dict() for record in self.history.records])

    def to_dict(self) -> dict[str, JsonValue]:
        """Serializable summary (no payload arrays) for logs and diagnostics."""
        return {
            "profile_digest": self.profile_digest,
            "entry": self.entry,
            "revision": self.revision,
            "applied_stages": list(self.applied_stages),
            "reused_calibrated": self.reused_calibrated,
            "final_domain": self.final_domain.value,
            "executed_utc": self.executed_utc.isoformat(),
            "has_time_processed": self.time_processed is not None,
            "trace_count": int(self.time_base.data.shape[0]),
            "channel_count": len(self.time_base.channels),
            "time_samples": int(self.time_base.data.shape[2]),
        }

    def derived_payload(self) -> DerivedWritePayload:
        """The controlled storage view of this result (plan decision D5)."""
        if self._derived_override is not None:
            return DerivedWritePayload(
                groups=dict(self._derived_override),
                history_records=len(self.history.records),
                trace_count=int(self.time_base.data.shape[0]),
                time_samples=int(self.time_base.data.shape[2]),
                profile_digest=self.profile_digest,
            )
        records = [record.to_dict() for record in self.history.records]
        cut = self.calibrated_record_count + (
            1 if self.reused_calibrated and self.calibrated_record_count == 0 else 0
        )
        frequency_history = _canonical_json(records[:cut])
        full_history = _canonical_json(records)
        groups: dict[str, Any] = {}
        groups["/axes/time_base_s"] = np.asarray(
            self.time_base.time_axis_s, dtype="<f8"
        )
        groups["/time_base/data"] = np.asarray(self.time_base.data, dtype="<c16")
        groups["/time_base/history_json"] = np.array(
            [full_history], dtype=h5py.string_dtype(encoding="utf-8")
        )
        snapshot = self.calibrated_snapshot
        if snapshot is not None:
            groups["/frequency/calibrated"] = np.asarray(
                snapshot.source.data, dtype="<c16"
            )
            # v2 declares no /frequency/history_json (t1 §3.3(i), plan D6): the
            # calibrated-stage provenance travels with the file-level mission
            # attrs written by the attachment writer instead.
            groups["_frequency_history_json"] = frequency_history
        processed = self.time_processed
        if processed is not None:
            groups["/axes/time_processed_s"] = np.asarray(
                processed.time_axis_s, dtype="<f8"
            )
            groups["/time_processed/data"] = np.asarray(processed.data, dtype="<c16")
            groups["/time_processed/history_json"] = np.array(
                [full_history], dtype=h5py.string_dtype(encoding="utf-8")
            )
        return DerivedWritePayload(
            groups=groups,
            history_records=len(self.history.records),
            trace_count=int(self.time_base.data.shape[0]),
            time_samples=int(self.time_base.data.shape[2]),
            profile_digest=self.profile_digest,
        )


@dataclass(frozen=True, slots=True)
class DerivedWritePayload:
    """Validated path -> value mapping handed to the controlled writer.

    Two kinds of entries are legal and nothing else:

    * ``/<group>/<dataset>`` keys naming a schema-declared **optional** dataset
      (they are created with exactly their contracted physical parameters);
    * the single logical key :data:`FREQUENCY_HISTORY_KEY`, which is *not* a
      dataset — v2 declares no ``/frequency/history_json`` (t1 §3.3(i), plan
      D6) — and lands as a mission attribute instead.
    """

    groups: Mapping[str, Any]
    history_records: int
    trace_count: int
    time_samples: int
    profile_digest: str

    def paths(self) -> tuple[str, ...]:
        return tuple(sorted(self.groups))


#: Logical (non-dataset) payload key for the calibrated-stage history.
FREQUENCY_HISTORY_KEY: Final = "_frequency_history_json"


# ---------------------------------------------------------------------------
# Orchestration (plan decisions D1/D3/D4)
# ---------------------------------------------------------------------------


def _require_empty_history(history: ProcessingHistory) -> None:
    if history.records:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "fresh raw processing requires an empty history: the first record "
            "must consume frequency_raw (a non-empty history means re-processing "
            "over derived data, which needs a new revision and the safe replay "
            "entry)",
            {
                "kind": "non_empty_raw_history",
                "records": len(history.records),
                "first_stage": history.records[0].stage_name,
                "last_output_domain": history.records[-1].output_domain.value,
            },
        )


def _stamp(request: ProcessingRequest) -> datetime:
    if request.executed_utc is not None:
        return ensure_utc(request.executed_utc)
    return (request.clock or SystemClock()).utc_now()


def _validate_source(source: object) -> FrequencySweep | FrequencyScan:
    if not isinstance(source, _FREQUENCY_CONTAINERS):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "processing input must be a FrequencySweep or FrequencyScan of raw "
            "frequency data",
            {"got": type(source).__name__},
        )
    return source


def _check_fresh_entry(request: ProcessingRequest) -> None:
    _require_empty_history(request.history)
    _validate_source(request.source)


def _reuse_verified_snapshot(
    request: ProcessingRequest, stamp: datetime
) -> CalibratedSnapshot:
    """Validate a snapshot for reuse (delegating to 032/033 authorities)."""
    snapshot = request.snapshot
    if snapshot is None:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "safe replay reuse requires a calibrated snapshot",
            {"kind": "missing_snapshot"},
        )
    _validate_source(snapshot.source)
    if not isinstance(snapshot.history, ProcessingHistory) or not snapshot.history.records:
        raise DomainError(
            ErrorCode.PROCESSING_DOMAIN_MISMATCH,
            "safe replay reuse requires the complete history that produced the "
            "calibrated snapshot",
            {"kind": "missing_history"},
        )
    last = snapshot.history.records[-1]
    if last.output_domain is not DataDomain.FREQUENCY_CALIBRATED:
        raise DomainError(
            ErrorCode.PROCESSING_DOMAIN_MISMATCH,
            "safe replay reuse requires a history ending in frequency_calibrated",
            {
                "kind": "wrong_last_domain",
                "last_output_domain": last.output_domain.value,
            },
        )
    calibration = request.profile.calibration
    if calibration is None:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "safe replay reuse requires the same calibration profile in the "
            "processing profile (provenance cannot be verified without it)",
            {"kind": "missing_profile_calibration"},
        )
    # Authority 1: strict ordered provenance + set digest (ISSUE-032).
    require_safe_reuse(snapshot.history, calibration)
    # Authority 2: calibrated-domain background binding (ISSUE-033), when used.
    selection = request.profile.background
    if selection is not None:
        require_matching_calibration_provenance(
            snapshot.history,
            selection.reference,
            current_calibration=selection.current_calibration,
        )
    if snapshot.calibration_digest != osl_set_digest(calibration):
        raise DomainError(
            ErrorCode.PROCESSING_DOMAIN_MISMATCH,
            "snapshot calibration digest differs from the requested profile",
            {"kind": "digest_mismatch"},
        )
    # The reuse must be requested over the very data the snapshot describes.
    requested = request.source
    if type(requested) is not type(snapshot.source):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "safe replay reuse requires the same frequency container type the "
            "snapshot was taken from",
            {
                "kind": "container_mismatch",
                "request": type(requested).__name__,
                "snapshot": type(snapshot.source).__name__,
            },
        )
    if requested.channels != snapshot.source.channels:
        raise DomainError(
            ErrorCode.CHANNEL_CONTRACT_MISMATCH,
            "safe replay reuse channel contract differs from the snapshot's",
            {"kind": "channel_mismatch"},
        )
    if not np.array_equal(requested.frequencies_hz, snapshot.source.frequencies_hz):
        raise DomainError(
            ErrorCode.AXIS_MISMATCH,
            "safe replay reuse frequency axis differs from the snapshot's",
            {"kind": "axis_mismatch"},
        )
    if requested.data.shape != snapshot.source.data.shape or not np.array_equal(
        requested.data, snapshot.source.data
    ):
        raise DomainError(
            ErrorCode.SHAPE_MISMATCH,
            "safe replay reuse must be requested over the exact calibrated data "
            "the snapshot describes (a different buffer means different data)",
            {
                "kind": "data_mismatch",
                "request_shape": list(requested.data.shape),
                "snapshot_shape": list(snapshot.source.data.shape),
            },
        )
    # Reuse must not silently re-stamp existing records.
    if last.executed_utc > stamp:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "processing timestamp precedes the snapshot provenance",
            {"kind": "stamp_before_snapshot"},
        )
    return snapshot


def run_processing(
    request: ProcessingRequest,
    *,
    token: ProcessingToken | None = None,
) -> ProcessedMission:
    """Execute the single canonical processing chain once.

    ``token`` ties the run to a :class:`ProcessingController` revision: every
    stage boundary checks it, so a superseded or cancelled revision aborts with
    :class:`StaleProcessingResult` before any derived output is produced.  With
    ``token=None`` the chain runs standalone (replay/batch tooling).
    """
    if not isinstance(request, ProcessingRequest):
        raise TypeError(f"request must be a ProcessingRequest, got {type(request).__name__}")
    stamp = _stamp(request)
    clock = request.clock
    profile = request.profile

    if request.entry == ENTRY_FRESH_RAW:
        _check_fresh_entry(request)
        history = ProcessingHistory()
        current: Any = _validate_source(request.source)
        reused = False
        snapshot: CalibratedSnapshot | None = None
    else:
        snapshot = _reuse_verified_snapshot(request, stamp)
        history = snapshot.history
        current = snapshot.source
        reused = True
        # Authoritative history: snapshot.history (request.history is not read on
        # this path — see the ProcessingRequest docstring).  The chain resumes
        # exactly where the snapshot ended (calibrated), so the OSL link must not
        # run again here.
        profile = replace(profile, calibration=None)

    # profile may have been narrowed above (reuse drops the OSL link), so the
    # reported digest must still describe the *requested* profile: reuse and the
    # equivalent fresh run therefore share one identity.
    requested_digest = request.profile.profile_digest()
    applied = [record.stage_name for record in history.records]
    calibrated_snapshot = snapshot

    # -- link 1: optional OSL calibration -----------------------------------
    if profile.osl_enabled and profile.calibration is not None:
        calibration = profile.calibration
        if token is not None:
            token.checkpoint(OSL_CALIBRATION_STAGE_NAME)
        stage = OslCalibrationStage(calibration)
        result: StageResult = stage.apply(
            current, history=history, executed_utc=stamp, clock=clock
        )
        current = result.source
        history = result.history
        calibrated_snapshot = CalibratedSnapshot(
            source=result.source,
            history=result.history,
            calibration_digest=osl_set_digest(calibration),
        )
        applied.append(OSL_CALIBRATION_STAGE_NAME)

    # -- link 2: calibrated snapshot is materialized right here (D6) --------
    if calibrated_snapshot is not None and token is not None:
        token.checkpoint("calibrated_snapshot")

    # -- link 3: optional air background subtraction ------------------------
    if profile.background_enabled:
        assert profile.background is not None
        selection = profile.background
        if token is not None:
            token.checkpoint(AIR_BACKGROUND_STAGE_NAME)
        bg_stage = AirBackgroundSubtractionStage(
            selection.reference,
            selection.reference_id,
            current_calibration=selection.current_calibration,
        )
        bg_result: StageResult = bg_stage.apply(
            current, history=history, executed_utc=stamp, clock=clock
        )
        current = bg_result.source
        history = bg_result.history
        applied.append(AIR_BACKGROUND_STAGE_NAME)

    # -- link 4: optional frequency bandpass --------------------------------
    if profile.bandpass_enabled:
        assert profile.bandpass_edges_hz is not None
        if token is not None:
            token.checkpoint(BANDPASS_STAGE_NAME)
        bp_stage = BandpassStage(profile.bandpass_edges_hz)
        bp_result: StageResult = bp_stage.apply(
            current, history=history, executed_utc=stamp, clock=clock
        )
        current = bp_result.source
        history = bp_result.history
        applied.append(BANDPASS_STAGE_NAME)

    # -- link 5: IFFT -> time_base (unconditional) --------------------------
    if token is not None:
        token.checkpoint(IFFT_STAGE_NAME)
    ifft_stage = FrequencyToTimeStage(oversampling=profile.ifft_oversampling)
    ifft_result = ifft_stage.apply(
        current, history=history, executed_utc=stamp, clock=clock
    )
    # The v2 schema pins the optional derived time shapes with time_points
    # and defaults it to the frequency-point count; ISSUE-011's reader validates
    # any present derived group against that default.  Oversampling is pure
    # interpolation (docs/PROCESSING.md section 4), so archiving one sample per
    # frozen frequency point loses no information and keeps every attached file
    # strictly readable.  The display window stays a separate read-only view and
    # never enters history (AGENTS.md section 8).
    time_base = archive_to_schema_grid(ifft_result.source)
    history = ifft_result.history
    applied.append(IFFT_STAGE_NAME)
    frequency_container_before_ifft: FrequencySweep | FrequencyScan = current

    # -- link 6: optional Dewow --------------------------------------------
    time_processed: TimeDomainScan | None = None
    if profile.dewow_enabled:
        assert profile.dewow_window_s is not None
        if token is not None:
            token.checkpoint(DEWOW_STAGE_NAME)
        dewow_stage = DewowStage(profile.dewow_window_s)
        dewow_result = dewow_stage.apply(
            time_base, history=history, executed_utc=stamp, clock=clock
        )
        time_processed = dewow_result.source
        history = dewow_result.history
        applied.append(DEWOW_STAGE_NAME)

    # -- link 7: optional Flat Reflection filter ---------------------------
    if profile.flat_enabled:
        assert profile.flat_window_traces is not None
        if token is not None:
            token.checkpoint(FLAT_STAGE_NAME)
        flat_stage = FlatReflectionFilterStage(profile.flat_window_traces)
        flat_input = time_base if time_processed is None else time_processed
        flat_result = flat_stage.apply(
            flat_input, history=history, executed_utc=stamp, clock=clock
        )
        time_processed = flat_result.source
        history = flat_result.history
        applied.append(FLAT_STAGE_NAME)

    if token is not None:
        token.checkpoint("completed")

    display_view: DisplayTimeWindowView | None = None
    if request.display_duration_s is not None and request.display_start_s is not None:
        axis = time_base.time_axis_s
        config = DisplayCropConfig(
            start_s=float(request.display_start_s),
            end_s=min(
                float(axis[-1]),
                float(request.display_start_s)
                + float(request.display_duration_s),
            ),
        )
        display_view = DisplayTimeWindowView.for_scan(time_base, config)

    final_domain = history.records[-1].output_domain
    ordered = [name for name in PROCESSING_ORDER if name in set(applied)]
    if token is not None:
        token.finish()
    return ProcessedMission(
        profile_digest=requested_digest,
        entry=request.entry,
        revision=0 if token is None else token.revision,
        source_input=_validate_source(request.source),
        history=history,
        time_base=time_base,
        time_processed=time_processed,
        final_domain=final_domain,
        calibrated_snapshot=calibrated_snapshot,
        executed_utc=stamp,
        applied_stages=tuple(ordered),
        reused_calibrated=reused,
        display_view=display_view,
        input_container_before_ifft=frequency_container_before_ifft,
    )


# ---------------------------------------------------------------------------
# Controlled derived-data attachment (plan decisions D5/D6)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AttachmentReport:
    """Outcome of one attachment attempt (success or controlled refusal).

    refused_reason is None when data landed; otherwise it names why the
    writer refused to publish anything (the original file stayed byte-identical
    in every refusal case).
    """

    path: str
    derived_paths: tuple[str, ...]
    raw_fingerprint: str
    trace_count: int
    history_records: int
    profile_digest: str
    replaced_existing: tuple[str, ...]
    published: bool = True
    refused_reason: str | None = None

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "path": self.path,
            "derived_paths": list(self.derived_paths),
            "raw_fingerprint": self.raw_fingerprint,
            "trace_count": self.trace_count,
            "history_records": self.history_records,
            "profile_digest": self.profile_digest,
            "replaced_existing": list(self.replaced_existing),
            # The auditable refusal surface: a controlled non-publication must be
            # visible to log/diagnostic consumers, not only on the object.
            "published": self.published,
            "refused_reason": self.refused_reason,
        }


#: alias kept for readability at call sites
DerivedWriteResult = AttachmentReport


def _contract_map(
    channel_count: int, frequency_points: int, time_points: int | None = None
) -> dict[str, schema.DatasetContract]:
    contracts = schema.dataset_contracts(channel_count, frequency_points, time_points)
    return {contract.path: contract for contract in contracts}


def _required_row_paths(contracts: Mapping[str, schema.DatasetContract]) -> tuple[str, ...]:
    prefixes = ("/trace_metadata/", "/gnss/", "/acquisition/", "/transport/")
    return tuple(
        path
        for path, contract in contracts.items()
        if not contract.optional and path.startswith(prefixes)
    )


def derived_contract_for(
    channel_count: int, frequency_points: int, time_samples: int
) -> Mapping[str, schema.DatasetContract]:
    """Frozen contracts of the optional derived groups for one file shape.

    ISSUE-008 parameterizes the optional time-domain shapes with time_points
    and defaults it to the frequency-point count; the attaching producer passes
    the real archived time-axis length here (docs/DATA_FORMAT.md section 2).
    Consumers that re-validate an attached file must use the same value.
    """
    return _contract_map(channel_count, frequency_points, time_samples)


def raw_column_fingerprint(path: str | Path) -> str:
    """SHA-256 over ``/frequency/raw`` plus every present required row column.

    This is the invariant AGENTS.md section 3 demands of processing: derived
    work must never move raw bytes.  Column lengths are included so a resize of
    a required column is detected as well as a value change.
    """
    target = Path(path)
    probe = schema.probe_rcscan_v2(target)
    contracts = _contract_map(len(probe.channel_ids), _frequency_points(target))
    digest = hashlib.sha256()
    with h5py.File(target, "r") as h5:
        raw = h5["/frequency/raw"]
        digest.update(b"/frequency/raw")
        digest.update(str(raw.shape[0]).encode("ascii"))
        digest.update(np.ascontiguousarray(raw[()], dtype="<c16").tobytes())
        for row_path in _required_row_paths(contracts):
            dataset = h5.get(row_path)
            if dataset is None:
                continue
            digest.update(row_path.encode("utf-8"))
            digest.update(str(dataset.shape[0]).encode("ascii"))
            values = dataset[()]
            if values.dtype.kind in "iuf":
                digest.update(np.ascontiguousarray(values).tobytes())
            else:
                flat = np.atleast_1d(values)
                for item in flat:
                    if isinstance(item, bytes):
                        digest.update(item)
                    else:
                        digest.update(str(item).encode("utf-8"))
                    digest.update(b"\x00")
    return digest.hexdigest()


def assert_raw_bytes_unchanged(
    path: str | Path, fingerprint: str | None = None
) -> str:
    """Return the current raw fingerprint, or fail when it moved from ``fingerprint``."""
    target = Path(path)
    if not target.exists():
        raise DerivedAttachmentError(
            ErrorCode.INVALID_ARGUMENT,
            "ground rcscan file does not exist",
            {"path": str(target)},
        )
    current = raw_column_fingerprint(target)
    if fingerprint is not None and current != fingerprint:
        raise DerivedAttachmentError(
            ErrorCode.INVALID_ARGUMENT,
            "raw frequency data or required columns changed; refusing to touch "
            "a file whose raw bytes are not stable",
            {"path": str(target), "expected": fingerprint, "found": current},
        )
    return current


def _frequency_points(path: Path) -> int:
    with h5py.File(path, "r") as h5:
        return int(h5["/axes/frequencies_hz"].shape[0])


def derived_shapes_match_schema_default(
    channel_count: int, frequency_points: int, time_samples: int
) -> bool:
    """Whether an archived grid fits the reader's default derived contract.

    ISSUE-011 validates present optional groups with time_points defaulted
    to the frequency-point count, so a chain whose time_base grid differs
    cannot be attached to a file that must stay readable by that frozen reader.
    """
    default = _contract_map(channel_count, frequency_points)
    widened = _contract_map(channel_count, frequency_points, time_samples)
    for path, contract in default.items():
        if not contract.optional:
            continue
        if widened[path].initial_shape != contract.initial_shape:
            return False
    return True


def archived_frequency_points(native_samples: int) -> int:
    """Validate that an archived grid can satisfy the frozen derived-shape tie.

    The v2 schema ties the optional derived time shapes to the frequency-point
    count (ISSUE-008 time_points default; docs/DATA_FORMAT.md section 2;
    ISSUE-013 restates it as a hard migration gate), so only a power-of-two grid
    can be archived and read back by the strict ISSUE-011 reader.
    """
    samples = int(native_samples)
    if samples < 2 or samples & (samples - 1) != 0:
        raise DomainError(
            ErrorCode.SHAPE_MISMATCH,
            "the archived time grid must be a power of two: the frozen schema "
            "ties /axes/time_*_s length to the frequency-point count, which no "
            "strictly increasing uniform axis satisfies for other lengths",
            {"time_samples": samples},
        )
    return samples


def archive_to_schema_grid(scan: TimeDomainScan) -> TimeDomainScan:
    """Return the scan unchanged after checking it is archivable.

    Separation of concerns (AGENTS.md section 1: IFFT and windowing stay
    distinct): this orchestrator never truncates or resamples a stage's output —
    that would silently rewrite what a provenance record claims to have produced
    and would change dtype/sample semantics without a version bump.  A mission
    that needs a shorter archived window configures the display crop (a read-only
    view, never history) or plans its acquisition axis accordingly; anything not
    on an archivable grid fails closed here instead of being quietly rewritten.
    """
    archived_frequency_points(int(scan.data.shape[-1]))
    return scan


class DerivedAttachmentWriter:
    """The only sanctioned way to add derived data to a ground ``.rcscan``.

    Hard rules enforced here (never trusted to the caller):

    * only datasets declared ``optional=True`` by the frozen ISSUE-008 contract
      may be created, and each is created with exactly its contracted
      dtype/maxshape/chunks/compression;
    * the write happens in a staging copy next to the target and is published
      with one atomic ``os.replace``, so a mid-flight failure leaves the
      original file byte-identical;
    * the staged file must pass the strict ISSUE-011 reader before publishing,
      and the raw fingerprint must be unchanged before and after;
    * row counts must match ``committed_record_count`` (derived data may never
      imply traces that were not committed).
    """

    #: derived paths this writer understands (all schema-declared optionals)
    ALLOWED_PATHS: Final[frozenset[str]] = frozenset(
        {
            "/axes/time_base_s",
            "/axes/time_processed_s",
            "/frequency/calibrated",
            "/time_base/data",
            "/time_base/history_json",
            "/time_processed/data",
            "/time_processed/history_json",
        }
    )

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        if not self._path.exists():
            raise DerivedAttachmentError(
                ErrorCode.INVALID_ARGUMENT,
                "ground rcscan file does not exist",
                {"path": str(self._path)},
            )

    @property
    def path(self) -> Path:
        return self._path

    def inspect(self) -> tuple[schema.RcscanProbe, int, int]:
        """Probe, committed row count and frequency points (validated read).

        Gate first and always fail-closed (never trusted to the caller): derived
        data is a **ground-end** capability (AGENTS.md section 6;
        docs/DATA_FORMAT.md section 6 authorizes calibration/processing/history
        additions for the ground copy only), and only a settled file may be
        attached to — a ``writing`` partial belongs to a live incremental writer
        whose handle would silently fork if we replaced the file under it.
        """
        probe = schema.probe_rcscan_v2(self._path)
        if probe.file_role is not EndpointRole.GROUND:
            raise DerivedAttachmentError(
                ErrorCode.INVALID_ARGUMENT,
                "derived processing results attach to a ground rcscan only; the "
                "air end stores raw data and never calibration or derived data",
                {"file_role": probe.file_role.value, "path": str(self._path)},
            )
        if probe.lifecycle_state not in _SETTLED_LIFECYCLE_STATES:
            raise DerivedAttachmentError(
                ErrorCode.INVALID_ARGUMENT,
                "derived attachment requires a settled rcscan file (finalized or "
                "recovered); a writing partial belongs to a live incremental "
                "writer and must not be replaced",
                {
                    "lifecycle_state": probe.lifecycle_state,
                    "allowed": list(_SETTLED_LIFECYCLE_STATES),
                    "path": str(self._path),
                },
            )
        try:
            reader = RcScanReaderLite(self._path)
            committed = reader.committed_record_count
            reader.close()
        except DomainError as error:
            raise DerivedAttachmentError(
                error.code,
                "ground rcscan failed strict validation before attachment",
                dict(error.context),
            ) from error
        return probe, committed, _frequency_points(self._path)

    def write(self, payload: DerivedWritePayload) -> AttachmentReport:
        """Atomically attach ``payload``; returns what landed on disk."""
        if not isinstance(payload, DerivedWritePayload):
            raise TypeError("payload must be a DerivedWritePayload")
        probe, committed, frequency_points = self.inspect()
        channel_count = len(probe.channel_ids)
        # The frozen contract parameterizes derived (time-domain) shapes with
        # time_points; ISSUE-008 leaves the real archived time-axis length
        # to the attaching producer (docs/DATA_FORMAT.md section 2).
        contracts = _contract_map(
            channel_count, frequency_points, payload.time_samples
        )

        unknown = sorted(
            set(payload.groups) - self.ALLOWED_PATHS - {FREQUENCY_HISTORY_KEY}
        )
        if unknown:
            raise DerivedAttachmentError(
                ErrorCode.INVALID_ARGUMENT,
                "derived payload contains paths outside the controlled "
                "allow-list (only schema-declared optional groups may be added)",
                {"paths": [str(item) for item in unknown]},
            )
        dataset_keys = [
            path for path in payload.groups if path != FREQUENCY_HISTORY_KEY
        ]
        missing_contract = sorted(path for path in dataset_keys if path not in contracts)
        if missing_contract:
            raise DerivedAttachmentError(
                ErrorCode.INVALID_ARGUMENT,
                "derived payload paths are not part of the frozen v2 schema",
                {"paths": [str(item) for item in missing_contract]},
            )
        for path in dataset_keys:
            if not contracts[path].optional:
                raise DerivedAttachmentError(
                    ErrorCode.INVALID_ARGUMENT,
                    "refusing to write a required dataset: raw and trace-major "
                    "columns are off-limits to processing",
                    {"dataset": path},
                )
        if payload.trace_count != committed:
            raise DerivedAttachmentError(
                ErrorCode.SHAPE_MISMATCH,
                "derived rows do not match the committed trace count",
                {"derived_rows": payload.trace_count, "committed": committed},
            )

        # Fail-closed preflight: nothing on disk changes until every derived
        # array is known to fit its frozen contract.
        for group_path in dataset_keys:
            self._preflight(contracts[group_path], payload.groups[group_path], committed)
        before = assert_raw_bytes_unchanged(self._path)
        replaced: list[str] = []
        staging = self._path.with_name(self._path.name + ".derived.tmp")
        if staging.exists():
            raise DerivedAttachmentError(
                ErrorCode.INVALID_ARGUMENT,
                "a staged attachment already exists; resolve it first",
                {"path": str(staging)},
            )
        try:
            shutil.copy2(self._path, staging)
            with h5py.File(staging, "r+") as out:
                for group_path in sorted(dataset_keys):
                    contract = contracts[group_path]
                    if group_path in out:
                        replaced.append(group_path)
                        del out[group_path]
                    self._create(out, contract, payload.groups[group_path], committed)
                frequency_history = payload.groups.get(FREQUENCY_HISTORY_KEY)
                if isinstance(frequency_history, str):
                    mission = out["mission"]
                    mission.attrs["frequency_history_json"] = frequency_history
                    mission.attrs["derived_profile_digest"] = payload.profile_digest
                    mission.attrs["derived_writer_version"] = _WRITER_VERSION
                out.flush()
            # The staged file must satisfy the strict ISSUE-011 reader before it
            # ever replaces the original; a schema-incompatible derived shape is
            # refused here and the original stays untouched.
            staged_reader = RcScanReaderLite(staging)
            try:
                staged_committed = staged_reader.committed_record_count
            finally:
                staged_reader.close()
            if staged_committed != committed:
                raise DerivedAttachmentError(
                    ErrorCode.INVALID_ARGUMENT,
                    "staged attachment changed the committed record window",
                    {"before": committed, "after": staged_committed},
                )
            staging.replace(self._path)
        except DomainError as error:
            if isinstance(error, DerivedAttachmentError):
                raise
            # Strict ISSUE-011 validation refused the staged file (typically a
            # derived grid wider than the schema's default time_points).  The
            # original stays byte-identical and the caller gets an explicit,
            # inspectable refusal instead of a corrupted archive.
            return AttachmentReport(
                path=str(self._path),
                derived_paths=(),
                raw_fingerprint=before,
                trace_count=payload.trace_count,
                history_records=payload.history_records,
                profile_digest=payload.profile_digest,
                replaced_existing=(),
                published=False,
                refused_reason="strict_validation",
            )
        finally:
            if staging.exists():
                staging.unlink(missing_ok=True)
        after = assert_raw_bytes_unchanged(self._path, before)
        return AttachmentReport(
            path=str(self._path),
            derived_paths=tuple(sorted(payload.groups)),
            raw_fingerprint=after,
            trace_count=payload.trace_count,
            history_records=payload.history_records,
            profile_digest=payload.profile_digest,
            replaced_existing=tuple(sorted(replaced)),
        )

    @staticmethod
    def _preflight(
        contract: schema.DatasetContract, values: Any, rows: int
    ) -> None:
        """Validate one derived array without touching the file (fail-closed)."""
        if contract.kind is schema.ValueKind.VLEN_UTF8:
            text = str(values[0]) if hasattr(values, "__len__") and len(values) else ""
            try:
                schema.dumps_utf8_json(schema.loads_utf8_json(text))
            except Exception as error:
                raise DerivedAttachmentError(
                    ErrorCode.INVALID_ARGUMENT,
                    "derived history_json is not canonical JSON",
                    {"dataset": contract.path},
                ) from error
            return
        array = np.asarray(values)
        expected_rank = len(contract.initial_shape)
        if array.ndim != expected_rank:
            raise DerivedAttachmentError(
                ErrorCode.SHAPE_MISMATCH,
                "derived array rank does not match the frozen contract",
                {
                    "dataset": contract.path,
                    "expected_rank": expected_rank,
                    "got_rank": array.ndim,
                },
            )
        trailing = tuple(int(size) for size in array.shape[1:])
        want_trailing = tuple(
            int(size) for size in contract.maxshape[1:] if size is not None
        )
        if trailing != want_trailing:
            raise DerivedAttachmentError(
                ErrorCode.SHAPE_MISMATCH,
                "derived array axes do not match the frozen contract",
                {
                    "dataset": contract.path,
                    "expected_trailing": list(want_trailing),
                    "got_trailing": list(trailing),
                },
            )
        fixed_axis = contract.maxshape[0]
        if fixed_axis is not None:
            if int(array.shape[0]) != int(fixed_axis):
                raise DerivedAttachmentError(
                    ErrorCode.SHAPE_MISMATCH,
                    "fixed-length derived axis length does not match the contract",
                    {
                        "dataset": contract.path,
                        "expected": int(fixed_axis),
                        "got": int(array.shape[0]),
                    },
                )
        elif int(array.shape[0]) != rows:
            raise DerivedAttachmentError(
                ErrorCode.SHAPE_MISMATCH,
                "trace-major derived data must carry exactly one row per "
                "committed trace",
                {"dataset": contract.path, "expected_rows": rows, "got_rows": int(array.shape[0])},
            )

    @staticmethod
    def _create(
        out: h5py.File,
        contract: schema.DatasetContract,
        values: Any,
        rows: int,
    ) -> None:
        parent = contract.path.rsplit("/", 2)
        group = f"/{parent[1]}" if len(parent) > 2 else "/"
        if group != "/" and group not in out:
            out.create_group(group)
        if contract.kind is schema.ValueKind.VLEN_UTF8:
            if contract.maxshape == (1,):
                payload = np.array(
                    [str(values[0])], dtype=h5py.string_dtype(encoding="utf-8")
                )
                out.create_dataset(contract.path, data=payload, dtype=contract.dtype)
                return
            out.create_dataset(
                contract.path,
                data=values,
                maxshape=contract.maxshape,
                dtype=contract.dtype,
                chunks=contract.chunks,
                compression=contract.compression,
            )
            return
        array = np.asarray(values)
        expected_rank = len(contract.initial_shape)
        if array.ndim != expected_rank:
            raise DerivedAttachmentError(
                ErrorCode.SHAPE_MISMATCH,
                "derived array rank does not match the frozen contract",
                {
                    "dataset": contract.path,
                    "expected_rank": expected_rank,
                    "got_rank": array.ndim,
                },
            )
        trailing = tuple(int(size) for size in array.shape[1:])
        want_trailing = tuple(
            int(size) for size in contract.maxshape[1:] if size is not None
        )
        if trailing != want_trailing:
            raise DerivedAttachmentError(
                ErrorCode.SHAPE_MISMATCH,
                "derived array axes do not match the frozen contract",
                {
                    "dataset": contract.path,
                    "expected_trailing": list(want_trailing),
                    "got_trailing": list(trailing),
                },
            )
        fixed_axis = contract.maxshape[0]
        if fixed_axis is not None:
            if int(array.shape[0]) != int(fixed_axis):
                raise DerivedAttachmentError(
                    ErrorCode.SHAPE_MISMATCH,
                    "fixed-length derived axis length does not match the contract",
                    {
                        "dataset": contract.path,
                        "expected": int(fixed_axis),
                        "got": int(array.shape[0]),
                    },
                )
            out.create_dataset(
                contract.path,
                data=np.ascontiguousarray(array, dtype=contract.dtype),
                dtype=contract.dtype,
            )
            return
        if int(array.shape[0]) != rows:
            raise DerivedAttachmentError(
                ErrorCode.SHAPE_MISMATCH,
                "trace-major derived data must carry exactly one row per "
                "committed trace",
                {"dataset": contract.path, "expected_rows": rows, "got_rows": int(array.shape[0])},
            )
        out.create_dataset(
            contract.path,
            shape=array.shape,
            maxshape=contract.maxshape,
            dtype=contract.dtype,
            chunks=contract.chunks,
            compression=contract.compression,
        )
        out[contract.path][...] = np.ascontiguousarray(array, dtype=contract.dtype)


class RcScanReaderLite:
    """Narrow handle on the ISSUE-011 strict reader (open, validate, close).

    Kept private on purpose: callers get facts, never an HDF5 handle, so no one
    can smuggle a write through this module's validation seam.
    """

    def __init__(self, path: Path) -> None:
        from uav_gpr.storage.rcscan_reader import RcScanReader

        self._reader = RcScanReader(path)

    @property
    def committed_record_count(self) -> int:
        return int(self._reader.committed_record_count)

    @property
    def mission_id(self) -> Any:
        return self._reader.mission_id

    def close(self) -> None:
        self._reader.close()


def attach_derived_result(
    path: str | Path, result: ProcessedMission
) -> AttachmentReport:
    """Attach one orchestrated result's derived data + history to a ground file.

    Convenience wrapper over :class:`DerivedAttachmentWriter`; raises
    :class:`DerivedAttachmentError` (never a partial write) when the payload is
    not compatible with the frozen schema or the file's committed rows.
    """
    if not isinstance(result, ProcessedMission):
        raise TypeError("result must be a ProcessedMission")
    writer = DerivedAttachmentWriter(path)
    return writer.write(result.derived_payload())


# ---------------------------------------------------------------------------
# Internal helpers reused above
# ---------------------------------------------------------------------------


def _osl_provenance(calibration: OslCalibrationSet) -> list[dict[str, JsonValue]]:
    from uav_gpr.processing.osl_calibration import OslProfileProvenance

    return [
        OslProfileProvenance.from_profile(profile).to_json()
        for profile in calibration.profiles
    ]
