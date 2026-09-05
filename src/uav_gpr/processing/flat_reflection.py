"""Flat Reflection time-domain stage: local trace-axis moving-average removal.

ISSUE-035 (docs/issues/M06_CALIBRATION_PROCESSING.md, docs/PROCESSING.md
sections 2/6, docs/CALIBRATION.md L9-10 concept boundary, t1 baseline report
section 3, docs/plans/2026-09-05-issue-035-flat-reflection.md D1-D9).

Migrated contract source (algorithm origin only — this module never imports,
modifies or moves it): rebar-inspector ``src/rebar_inspector/processing/
flat_reflection.py`` SHA-256 ``89e3c01b3ce4135fd96495b27a67ff69760224bdc80c91
44fd9aeeaf4ca87df0`` and ``processing/_time_stage_common.py`` SHA-256
``e0c201b55acbaece0edb1546bbb8a00492874bb79fb9caf789d5ba416d333c81`` (both
frozen in docs/reference-baselines/manifest.json, hash-verified against the
local read-only copy in t1).  The reference's ``[time, trace]`` layout and
float64 assumptions are deliberately NOT migrated: the formal model here is an
immutable ``TimeDomainScan`` of shape ``trace x channel x time`` in
complex128 whose FIRST axis is always trace, so flat filtering is exactly
equivalent to processing real and imaginary parts independently.

CONCEPT BOUNDARY (docs/CALIBRATION.md L9-10, mandatory reading for callers):

- Air background subtraction (ISSUE-033, ``air_background_subtraction``)
  subtracts an EXTERNAL complex frequency-domain survey reference vector
  along the FREQUENCY axis in the frequency chain.
- Flat Reflection (this stage, ``flat_reflection_filter``) is a LOCAL
  time-domain background removal computed ALONG THE SURVEY LINE (trace axis,
  dimension 0) from the data itself.  It is NEVER equivalent to, a substitute
  for, or interchangeable with air background subtraction; the two stages
  differ in name, history token, domain transition and mathematical object.

KNOWN LIMITATION / RISK STATEMENT (docs/PROCESSING.md section 6): by design
this stage may attenuate laterally continuous layered reflections and targets
aligned with the survey line, because such responses are close to the local
trace-axis mean it removes.  It must therefore stay optional in any pipeline
(ISSUE-036 orchestration); no UI auto-enable and no realtime incremental
approximation are implemented here.

Contract surface:

- :class:`FlatReflectionFilterStage` — structurally satisfies the frozen
  ISSUE-030 :class:`~uav_gpr.processing.bandpass.ProcessingStage` protocol
  with ``stage_name="flat_reflection_filter"`` (the exact token the ISSUE-034
  dewow ordering guard refuses), accepted input domains ``{time_base,
  time_processed}`` and ``time_processed`` output: subtracts an edge-padded
  centered moving average (O(N) cumulative sums, complex128 preserved) along
  axis 0 (trace), returning a brand-new immutable ``TimeDomainScan``
  (``kind=time_processed``, channels / time axis / per-trace metadata fully
  preserved) plus one appended provenance record via the ISSUE-031 sibling
  result type :class:`~uav_gpr.processing.time_domain.TimeDomainStageResult`.
- Pure function face (single implementations, exported for golden pinning):
  :func:`validate_window_traces` (odd int >= 3 policy, refusal reasons
  pinned) and :func:`centered_moving_mean_along_axis` (verbatim migration of
  the reference kernel; the contractual call site fixes axis=0/padding="edge",
  generalised signature exists for independent-transcription golden checks).
- Fail-closed entry guards: non-finite data rejected, duplicate flat refused
  twice (explicit stage gate + core history uniqueness which a bumped
  ``stage_version`` cannot bypass), and the fixed recommended order
  ``dewow -> flat_reflection_filter`` completed on this side by accepting a
  dewow predecessor while ISSUE-034 refuses the inverse.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Final, Literal

import numpy as np

from uav_gpr.core.enums import DataDomain, TimeDomainKind
from uav_gpr.core.errors import DomainError, ErrorCode, JsonValue
from uav_gpr.core.time_domain import ProcessingHistory, TimeDomainScan
from uav_gpr.core.timeutil import Clock, SystemClock, ensure_utc
from uav_gpr.processing.bandpass import ProcessingStage, _input_domain_of, _record_for
from uav_gpr.processing.time_domain import TimeDomainStageResult

__all__ = [
    "DEFAULT_FLAT_REFLECTION_WINDOW_TRACES",
    "FLAT_AXIS",
    "FLAT_PADDING",
    "FLAT_STAGE_NAME",
    "FLAT_STAGE_VERSION",
    "FlatReflectionFilterStage",
    "centered_moving_mean_along_axis",
    "validate_window_traces",
]

#: Stable snake_case stage name (never reused by any other stage; identical
#: token to the frozen reference and to the ISSUE-034 dewow ordering guard
#: ``_FLAT_STAGE_NAME``).
FLAT_STAGE_NAME: Final = "flat_reflection_filter"

#: Version token of the migrated trace-axis moving-average contract.
FLAT_STAGE_VERSION: Final = "1.0"

#: Default flat window in TRACE COUNTS, carried over verbatim from the frozen
#: reference default (plan D2).  The unit is traces, not seconds: trace
#: spacing is flight-dynamics dependent and irregular, so the statistic is
#: defined over trace ORDER, never over physical distance.
DEFAULT_FLAT_REFLECTION_WINDOW_TRACES: Final = 101

#: The ONLY axis this stage ever filters along (plan D3): dimension 0 of the
#: formal ``trace x channel x time`` model — the survey-line direction.  It is
#: a contract constant, not a caller option.
FLAT_AXIS: Final = 0

#: The ONLY boundary strategy this stage ever applies (plan D3): the first and
#: last trace values are tiled outward.  Contract constant, not an option;
#: the dewow "reflect" mode belongs to ISSUE-034.
FLAT_PADDING: Final = "edge"

#: The recommended predecessor stage (ISSUE-034).  Its presence in a history
#: is legal and expected (dewow -> flat); the inverse ordering is already
#: refused by the dewow side (guard 3 there), closing the order loop.
_DEWOW_STAGE_NAME: Final = "dewow"

#: Frozen reference-source digests carried into every provenance record so an
#: audit client can pin the exact algorithm origin without external tables
#: (plan D8/D9 leg 1).
_REFERENCE_SOURCE_SHA256: Final[Mapping[str, str]] = MappingProxyType(
    {
        "rebar_processing_flat_reflection_py": (
            "89e3c01b3ce4135fd96495b27a67ff69760224bdc80c9144fd9aeeaf4ca87df0"
        ),
        "rebar_time_stage_common_py": (
            "e0c201b55acbaece0edb1546bbb8a00492874bb79fb9caf789d5ba416d333c81"
        ),
    }
)

_TIME_INPUT_DOMAINS: Final[frozenset[DataDomain]] = frozenset(
    {DataDomain.TIME_BASE, DataDomain.TIME_PROCESSED}
)


def validate_window_traces(window_traces: object) -> int:
    """Validate the trace-axis window: true int, odd, >= 3 (plan D2).

    Fails closed with structured :class:`DomainError`\\ s (the UAV error
    discipline replaces the reference's bare TypeError/ValueError surface;
    the refusal semantics are verbatim): bool and non-int are rejected,
    ``< 3`` is rejected because window=1 would subtract the signal from
    itself and zero the whole output (a deliberate safety hardening over the
    upstream prototype), and even windows are rejected so the current trace
    sits at the centre of the window.  ``<= n_traces`` depends on the actual
    input and is checked by :meth:`FlatReflectionFilterStage.apply`.
    """
    if isinstance(window_traces, bool) or not isinstance(window_traces, int):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "window_traces must be an int (bool rejected)",
            {"got": type(window_traces).__name__, "value": repr(window_traces)},
        )
    if window_traces < 3:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            f"window_traces must be >= 3 (got {window_traces}); window=1 makes "
            "the output identically zero (the local mean IS the signal) -- "
            "refused as a safety hardening",
            {"window_traces": int(window_traces)},
        )
    if window_traces % 2 == 0:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            f"window_traces must be odd (got {window_traces}) so the current "
            "trace sits at the window centre",
            {"window_traces": int(window_traces)},
        )
    return window_traces


def centered_moving_mean_along_axis(
    data: np.ndarray,
    *,
    axis: int,
    window: int,
    padding: str,
) -> np.ndarray:
    """Centered moving average along ``axis``, O(N), complex-preserving.

    Verbatim port of the frozen reference kernel (cumulative-sum variant):
    move the target axis to position 0, pad ``half = window // 2`` entries on
    both sides using the given mode, take a complex128 ``np.cumsum`` prefixed
    by a zero slice, difference ``cum[window:] - cum[:-window]`` and divide by
    ``window``, then move the axis back.  Both the real and imaginary
    accumulations happen independently inside complex128, so the result
    equals processing real and imaginary parts separately.  No per-sample
    Python loop exists anywhere: cost is linear in the total element count.

    The contractual flat call site is always ``axis=FLAT_AXIS (0)`` with
    ``padding=FLAT_PADDING ("edge")``; the generalised signature exists
    because the kernel is the single shared implementation and the golden
    transcription check exercises it directly (plan D3/D9).

    Guards (fail closed): ``data`` must be a complex128 ndarray with at least
    one dimension (``DTYPE_MISMATCH``); ``window`` must be an odd integer
    >= 3 (``INVALID_ARGUMENT`` — undersized/even values are never silently
    adjusted; :func:`validate_window_traces` owns the caller-facing policy);
    ``padding`` must be ``"edge"`` or ``"reflect"`` (``INVALID_ARGUMENT``,
    the two pinned modes of this project family — flat uses edge, dewow's
    reflect lives in its own module); the target axis must be long enough to
    hold the window (``INVALID_ARGUMENT``).
    """
    if not isinstance(data, np.ndarray):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "centered_moving_mean_along_axis data must be a numpy ndarray",
            {"got": type(data).__name__},
        )
    if data.dtype != np.complex128 or data.ndim < 1:
        raise DomainError(
            ErrorCode.DTYPE_MISMATCH,
            "centered_moving_mean_along_axis requires a >=1-D complex128 array",
            {"dtype": str(data.dtype), "ndim": int(data.ndim)},
        )
    if isinstance(window, bool) or not isinstance(window, int):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "flat window must be an integer trace count",
            {"window": repr(window)},
        )
    if window < 3 or window % 2 == 0:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "flat window must be an odd trace count >= 3",
            {"window": int(window)},
        )
    if padding not in ("edge", "reflect"):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            'flat kernel padding must be "edge" or "reflect"',
            {"padding": repr(padding)},
        )
    if not isinstance(axis, int) or isinstance(axis, bool):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "flat kernel axis must be an int",
            {"axis": repr(axis)},
        )
    norm_axis = axis if axis >= 0 else data.ndim + axis
    if norm_axis < 0 or norm_axis >= data.ndim:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "flat kernel axis out of range for the data rank",
            {"axis": int(axis), "ndim": int(data.ndim)},
        )
    n_axis = int(data.shape[norm_axis])
    if window > n_axis:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "flat window exceeds the target axis length",
            {"window": int(window), "axis_length": n_axis},
        )
    half = window // 2
    moved = np.moveaxis(data, norm_axis, 0)
    pad_width: list[tuple[int, int]] = [(half, half)] + [(0, 0)] * (moved.ndim - 1)
    # The guard above refuses every mode outside the two pinned literals;
    # re-binding through a Literal-typed local mirrors that runtime fact for
    # numpy's _ModeKind overload resolution (the dead branch can never run).
    if padding == "reflect":
        mode: Literal["edge", "reflect"] = "reflect"
    else:
        mode = "edge"
    padded = np.pad(moved, pad_width=pad_width, mode=mode)
    cumulative = np.cumsum(padded, axis=0, dtype=np.complex128)
    zero_shape = list(cumulative.shape)
    zero_shape[0] = 1
    cumulative = np.concatenate(
        [np.zeros(zero_shape, dtype=np.complex128), cumulative], axis=0
    )
    local_sum = cumulative[window:] - cumulative[:-window]
    return np.moveaxis(local_sum / window, 0, norm_axis)


class FlatReflectionFilterStage:
    """Flat Reflection stage: subtract a local moving average along the trace axis.

    Constructed with ``window_traces`` (odd int >= 3, default 101, validated
    eagerly); ``apply`` runs the fail-closed guard sequence (legal time-domain
    predecessor domain, duplicate flat, kind coherence, shape coherence,
    complex finiteness, short-line window bound), filters the input along
    axis 0 with fixed edge padding and returns a fresh
    ``TimeDomainScan(kind=time_processed)`` plus
    :class:`TimeDomainStageResult`.  Input arrays are never mutated.

    Semantics are FIXED and distinct from air background subtraction (see the
    module docstring concept boundary): this is a within-data local statistic
    along the survey line, not a subtraction of an external frequency-domain
    reference.  It may weaken laterally continuous layered reflections and
    targets aligned with the survey line (docs/PROCESSING.md section 6) and
    must remain optional in orchestration.

    Recommended order is ``dewow -> flat_reflection_filter``: a TIME_PROCESSED
    scan produced by ISSUE-034 dewow is a legal input, while applying flat
    twice inside one history fails closed at both the stage gate and the core
    history-uniqueness layer.  This module implements no realtime
    incremental approximation and no UI default-enable path.
    """

    def __init__(
        self, window_traces: int = DEFAULT_FLAT_REFLECTION_WINDOW_TRACES
    ) -> None:
        self._window_traces = validate_window_traces(window_traces)

    @property
    def window_traces(self) -> int:
        return self._window_traces

    @property
    def stage_name(self) -> str:
        return FLAT_STAGE_NAME

    @property
    def stage_version(self) -> str:
        return FLAT_STAGE_VERSION

    @property
    def input_domain(self) -> frozenset[DataDomain]:
        return _TIME_INPUT_DOMAINS

    @property
    def output_domain(self) -> DataDomain:
        return DataDomain.TIME_PROCESSED

    @property
    def parameters(self) -> Mapping[str, JsonValue]:
        """Canonical stage configuration (JSON-safe, unit-bearing keys)."""
        return MappingProxyType(
            {
                "operation": "subtract_local_trace_mean",
                "axis": "trace_first",
                "padding": FLAT_PADDING,
                "window_traces": self._window_traces,
                "reference_source_sha256": dict(_REFERENCE_SOURCE_SHA256),
            }
        )

    def apply(
        self,
        source: object,
        *,
        history: ProcessingHistory | None = None,
        executed_utc: datetime | None = None,
        clock: Clock | None = None,
    ) -> TimeDomainStageResult:
        """Flat-filter one time-domain scan and append the provenance record.

        ``source`` is typed ``object`` (widened against the ISSUE-030
        protocol signature) so the runtime ``isinstance`` gate below stays
        the single fail-closed authority: anything but a ``TimeDomainScan``
        raises ``TypeError`` before any work happens (the reference refuses
        single-sweep containers the same way — a lone sweep has no survey
        line to average over).  ``history`` defaults to ``source.history``;
        an explicitly supplied one must still end in a legal time-domain
        predecessor.  ``executed_utc`` wins when given; otherwise the
        injected ``clock`` (default: the system UTC clock) stamps the record
        once — no sleeping, no polling.
        """
        if not isinstance(source, TimeDomainScan):
            raise TypeError(
                "flat reflection filter input must be a TimeDomainScan, "
                f"got {type(source).__name__}"
            )
        effective_history = source.history if history is None else history
        if not isinstance(effective_history, ProcessingHistory):
            raise TypeError(
                "history must be a ProcessingHistory, "
                f"got {type(effective_history).__name__}"
            )
        if executed_utc is not None:
            # Fail closed on naive/offset-less stamps before any work.
            stamp = ensure_utc(executed_utc)
        else:
            stamp = (clock or SystemClock()).utc_now()

        # --- guard 1: legal time-domain predecessor domain -----------------
        input_domain = _input_domain_of(effective_history)
        if input_domain not in _TIME_INPUT_DOMAINS:
            raise DomainError(
                ErrorCode.PROCESSING_DOMAIN_MISMATCH,
                "flat reflection filter input domain is not a legal "
                "time-domain predecessor",
                {
                    "stage_name": FLAT_STAGE_NAME,
                    "input_domain": input_domain.value,
                    "allowed_input_domains": [
                        domain.value
                        for domain in sorted(
                            _TIME_INPUT_DOMAINS, key=lambda d: d.value
                        )
                    ],
                },
            )

        # --- guard 2: duplicate flat (stage-level first gate) --------------
        if any(
            record.stage_name == FLAT_STAGE_NAME
            for record in effective_history.records
        ):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "flat_reflection_filter may be applied only once per history;"
                " re-processing requires a new history/revision",
                {
                    "stage_name": FLAT_STAGE_NAME,
                    "stage_version": FLAT_STAGE_VERSION,
                },
            )

        # --- guard 3: kind/predecessor coherence (defense-in-depth) --------
        expected_kind = TimeDomainKind(input_domain.value)
        if source.kind is not expected_kind:
            raise DomainError(
                ErrorCode.PROCESSING_DOMAIN_MISMATCH,
                "flat reflection filter scan kind does not match the history "
                "predecessor domain",
                {
                    "kind": source.kind.value,
                    "input_domain": input_domain.value,
                },
            )

        # --- guard 4: shape self-consistency double-check ------------------
        if source.data.ndim != 3:
            raise DomainError(
                ErrorCode.SHAPE_MISMATCH,
                "flat reflection filter input must be trace x channel x time",
                {"ndim": int(source.data.ndim)},
            )
        n_traces = int(source.data.shape[FLAT_AXIS])

        # --- guard 5: complex finiteness entry gate -------------------------
        if not np.all(np.isfinite(source.data)):
            bad = int(np.flatnonzero(~np.isfinite(source.data))[0])
            raise DomainError(
                ErrorCode.NON_FINITE_AXIS,
                "flat reflection filter input data contains NaN or infinity",
                {"flat_index": bad},
            )

        # --- guard 6: short-line protection ---------------------------------
        if self._window_traces > n_traces:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                f"window_traces={self._window_traces} exceeds the total trace "
                f"count {n_traces}; reduce window_traces to at most the survey"
                " line length",
                {"window_traces": self._window_traces, "n_traces": n_traces},
            )

        # One vectorized pass over the whole buffer; source.data is the
        # input model's read-only view, the subtraction produces a fresh
        # writable array that the rebuilt core model defensively copies
        # into its own write-protected snapshot — the input is never touched.
        local_mean = centered_moving_mean_along_axis(
            source.data,
            axis=FLAT_AXIS,
            window=self._window_traces,
            padding=FLAT_PADDING,
        )
        filtered = source.data - local_mean

        record = _record_for(
            stage_name=FLAT_STAGE_NAME,
            stage_version=FLAT_STAGE_VERSION,
            parameters={
                **dict(self.parameters),
                "trace_sample_count": n_traces,
            },
            input_domain=input_domain,
            output_domain=DataDomain.TIME_PROCESSED,
            executed_utc=stamp,
        )
        new_history = effective_history.append(record)

        scan = TimeDomainScan(
            channels=source.channels,
            time_axis_s=source.time_axis_s,
            data=filtered,
            kind=TimeDomainKind.TIME_PROCESSED,
            history=new_history,
            metadata=source.metadata,
        )
        return TimeDomainStageResult(
            source=scan,
            history=new_history,
            domain=DataDomain.TIME_PROCESSED,
        )


# Structural conformance to the frozen ISSUE-030 protocol, checked statically
# so a future refactor that breaks the shape fails at import time.
assert isinstance(FlatReflectionFilterStage(), ProcessingStage)
