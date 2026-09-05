"""Dewow time-domain stage: centered moving-average removal along the time axis.

ISSUE-034 (docs/issues/M06_CALIBRATION_PROCESSING.md, docs/PROCESSING.md
sections 2/5, docs/DATA_MODEL.md L152, t1 baseline report section 3,
docs/plans/2026-09-05-issue-034-dewow.md decisions D1-D9).

Migrated contract source (algorithm origin only — this module never imports,
modifies or moves it): rebar-inspector ``src/rebar_inspector/processing/
dewow.py`` SHA-256 ``eb6690e7fabf0bc80e051831ab6264e6e6d112b6568fb6dc30556a3
a7f030e2c`` and ``processing/_time_stage_common.py`` SHA-256 ``e0c201b55acba
ece0edb1546bbb8a00492874bb79fb9caf789d5ba416d333c81`` (both frozen in
docs/reference-baselines/manifest.json, hash-verified against the local
read-only copy in t1).  The reference's ``[time, trace]`` layout and float64
assumptions are deliberately NOT migrated: the formal model here is an
immutable ``TimeDomainScan`` of shape ``trace x channel x time`` in
complex128 whose LAST axis is always time, so dewow is exactly equivalent to
processing real and imaginary parts independently.

Contract surface:

- :class:`DewowStage` — structurally satisfies the frozen ISSUE-030
  :class:`~uav_gpr.processing.bandpass.ProcessingStage` protocol with
  ``stage_name="dewow"``, accepted input domains ``{time_base,
  time_processed}`` and ``time_processed`` output: subtracts a reflect-padded
  centered moving average (O(N) cumulative sums, complex128 preserved) along
  the last axis, returning a brand-new immutable ``TimeDomainScan``
  (``kind=time_processed``, channels / time axis / per-trace metadata fully
  preserved) plus one appended provenance record via the ISSUE-031 sibling
  result type :class:`~uav_gpr.processing.time_domain.TimeDomainStageResult`.
- Pure function face (single implementations, exported for golden pinning):
  :func:`derive_sample_interval_s` (dt from the input axis only — median
  step with the same 1e-6 relative uniform-grid tolerance posture as the
  ISSUE-031 IFFT grid), :func:`window_samples_for` (the seconds->samples
  rounding chain: round -> clamp >=1 -> oddify upward -> reject ==1 -> reject
  > n_time) and :func:`centered_moving_mean` (verbatim migration of the
  reference kernel; the boundary mode is fixed to ``"reflect"`` by contract).
- Fail-closed entry guards: non-finite data rejected, duplicate dewow refused
  twice (explicit stage gate + core history uniqueness which a bumped
  ``stage_version`` cannot bypass), and the fixed recommended order
  ``dewow -> flat_reflection_filter`` enforced by refusing any history that
  already contains the (ISSUE-035-owned) flat stage name.

This module implements neither Flat Reflection (ISSUE-035, trace-axis
statistics) nor any UI / parameter dialog / display crop behaviour
(ISSUE-031 owns the display window and keeps refusing ``time_processed``
scans); it touches no file formats, hardware or threads.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Final

import numpy as np

from uav_gpr.core.enums import DataDomain, TimeDomainKind
from uav_gpr.core.errors import DomainError, ErrorCode, JsonValue
from uav_gpr.core.time_domain import ProcessingHistory, TimeDomainScan
from uav_gpr.core.timeutil import Clock, SystemClock, ensure_utc
from uav_gpr.processing.bandpass import ProcessingStage, _input_domain_of, _record_for
from uav_gpr.processing.time_domain import TimeDomainStageResult

__all__ = [
    "DEFAULT_DEWOW_WINDOW_S",
    "DEWOW_AXIS_TOLERANCE_REL",
    "DEWOW_PADDING",
    "DEWOW_STAGE_NAME",
    "DEWOW_STAGE_VERSION",
    "DewowStage",
    "centered_moving_mean",
    "derive_sample_interval_s",
    "window_samples_for",
]

#: Stable snake_case stage name (never reused by any other stage; identical
#: token to the frozen reference and to the ISSUE-031 test fixtures).
DEWOW_STAGE_NAME: Final = "dewow"

#: Version token of the migrated centered-moving-average contract.
DEWOW_STAGE_VERSION: Final = "1.0"

#: Default dewow window in SECONDS.  Numerically equal to the frozen
#: reference default of 4.0 ns; the migrated contract expresses physical
#: seconds because the formal ``time_axis_s`` unit is seconds (plan D2).
DEFAULT_DEWOW_WINDOW_S: Final = 4e-9

#: Relative uniform-grid tolerance for the derived sample interval, the same
#: posture as ISSUE-031's IFFT grid check (plan D3).
DEWOW_AXIS_TOLERANCE_REL: Final = 1e-6

#: The ONLY boundary strategy this stage ever applies (plan D5): reflecting
#: padding at both ends of the time axis.  It is a contract constant, not a
#: caller option; the flat-stage "edge" mode belongs to ISSUE-035.
DEWOW_PADDING: Final = "reflect"

#: Fixed recommended time-domain order (reference semantics): dewow runs
#: before flat_reflection_filter; a history already containing the latter is
#: an illegal ordering for this stage.
_FLAT_STAGE_NAME: Final = "flat_reflection_filter"

#: Frozen reference-source digests carried into every provenance record so an
#: audit client can pin the exact algorithm origin without external tables
#: (plan D8/D9 leg 1).
_REFERENCE_SOURCE_SHA256: Final[Mapping[str, str]] = MappingProxyType(
    {
        "rebar_processing_dewow_py": (
            "eb6690e7fabf0bc80e051831ab6264e6e6d112b6568fb6dc30556a3a7f030e2c"
        ),
        "rebar_time_stage_common_py": (
            "e0c201b55acbaece0edb1546bbb8a00492874bb79fb9caf789d5ba416d333c81"
        ),
    }
)

_TIME_INPUT_DOMAINS: Final[frozenset[DataDomain]] = frozenset(
    {DataDomain.TIME_BASE, DataDomain.TIME_PROCESSED}
)


def _validate_window_s(window_s: object) -> float:
    """Validate ``window_s``: finite positive real scalar (bool rejected)."""
    if isinstance(window_s, bool) or not isinstance(window_s, (int, float)):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "dewow window_s must be a real scalar number",
            {"window_s": repr(window_s)},
        )
    value = float(window_s)
    if not math.isfinite(value) or value <= 0.0:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "dewow window_s must be a positive finite number of seconds",
            {"window_s": repr(value)},
        )
    return value


def derive_sample_interval_s(time_axis_s: np.ndarray) -> float:
    """Return the uniform sampling interval ``dt_s`` of a time axis in seconds.

    Migrated contract of the reference ``_compute_dt``: the axis needs at
    least two samples (else ``INVALID_ARGUMENT``), must be strictly
    increasing (defensive double-check, ``NON_INCREASING_AXIS``) and finite
    (``NON_FINITE_AXIS``); the interval is the median consecutive-step value
    and every step must stay within ``|dt| * DEWOW_AXIS_TOLERANCE_REL`` of it
    (otherwise ``NON_UNIFORM_AXIS`` — dewow's sliding window assumes an
    equally spaced grid and never silently approximates one).  Callers must
    not pass dt themselves: it belongs to the archived axis.
    """
    axis = np.asarray(time_axis_s, dtype=np.float64)
    if axis.size < 2:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "dewow requires a time axis with at least 2 samples",
            {"time_sample_count": int(axis.size)},
        )
    if not np.all(np.isfinite(axis)):
        raise DomainError(
            ErrorCode.NON_FINITE_AXIS,
            "dewow time axis must contain only finite values",
        )
    diffs = np.diff(axis)
    if not np.all(diffs > 0):
        # Defensive: the core model already guarantees strict increase, but
        # this stage stays self-contained and re-verifies its own premise.
        raise DomainError(
            ErrorCode.NON_INCREASING_AXIS,
            "dewow time axis must be strictly increasing",
        )
    dt_s = float(np.median(diffs))
    max_dev = float(np.max(np.abs(diffs - dt_s)))
    if max_dev > abs(dt_s) * DEWOW_AXIS_TOLERANCE_REL:
        raise DomainError(
            ErrorCode.NON_UNIFORM_AXIS,
            "dewow requires an approximately uniform time axis",
            {
                "median_step_s": dt_s,
                "max_step_deviation_s": max_dev,
                "relative_tolerance": DEWOW_AXIS_TOLERANCE_REL,
            },
        )
    return dt_s


def window_samples_for(window_s: float, dt_s: float, n_time: int) -> int:
    """Convert a physical window in seconds to an odd sample count in range.

    Verbatim migration of the reference rounding chain and its order:
    ``round(window_s / dt_s)`` -> clamp up to >= 1 -> even counts grow by one
    (oddify upward) -> a resulting count of 1 is refused (the window would be
    a no-op; tell the caller to enlarge ``window_s``) -> a count exceeding
    ``n_time`` is refused (tell the caller to shrink ``window_s``).  Python's
    ``round`` IEEE half-to-even semantics are part of the pinned contract.
    The returned window is always odd, >= 3 and <= ``n_time``.
    """
    samples = round(window_s / dt_s)
    samples = max(1, samples)
    if samples % 2 == 0:
        samples += 1
    if samples == 1:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            f"dewow window_s={window_s} resolves to a single sample at "
            f"dt_s={dt_s}; enlarge window_s",
            {"window_s": float(window_s), "dt_s": float(dt_s), "n_time": int(n_time)},
        )
    if samples > n_time:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            f"dewow window of {samples} samples exceeds the {n_time}-sample "
            f"time axis; reduce window_s",
            {"window_s": float(window_s), "dt_s": float(dt_s), "n_time": int(n_time)},
        )
    return samples


def centered_moving_mean(data: np.ndarray, *, window: int) -> np.ndarray:
    """Centered moving average along the LAST axis, O(N), complex-preserving.

    Verbatim port of the frozen reference kernel (cumulative-sum variant):
    pad ``window // 2`` samples on both sides of the last axis using the
    contractual :data:`DEWOW_PADDING` ("reflect") mode, take a complex128
    ``np.cumsum`` along it prefixed by a zero slice, difference
    ``cum[window:] - cum[:-window]`` and divide by ``window``.  Both the real
    and imaginary accumulations happen independently inside complex128, so
    the result equals processing real and imaginary parts separately.  No
    per-sample Python loop exists anywhere: cost is linear in the total
    element count.

    Guards (fail closed, ``INVALID_ARGUMENT`` unless noted): ``data`` must be
    a complex128 ndarray with at least one dimension; ``window`` must be an
    odd integer >= 3 (even/undersized values are never silently adjusted
    here — :func:`window_samples_for` owns the oddification policy); the last
    axis must be long enough to hold the window.
    """
    if not isinstance(data, np.ndarray):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "centered_moving_mean data must be a numpy ndarray",
            {"got": type(data).__name__},
        )
    if data.dtype != np.complex128 or data.ndim < 1:
        raise DomainError(
            ErrorCode.DTYPE_MISMATCH,
            "centered_moving_mean requires a >=1-D complex128 array",
            {"dtype": str(data.dtype), "ndim": int(data.ndim)},
        )
    if isinstance(window, bool) or not isinstance(window, int):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "dewow window must be an integer sample count",
            {"window": repr(window)},
        )
    if window < 3 or window % 2 == 0:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "dewow window must be an odd sample count >= 3",
            {"window": int(window)},
        )
    n_time = int(data.shape[-1])
    if window > n_time:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "dewow window exceeds the time axis length",
            {"window": int(window), "n_time": n_time},
        )
    half = window // 2
    pad_width = [(0, 0)] * (data.ndim - 1) + [(half, half)]
    padded = np.pad(data, pad_width=pad_width, mode=DEWOW_PADDING)
    cumulative = np.cumsum(padded, axis=-1, dtype=np.complex128)
    zero_shape = list(cumulative.shape)
    zero_shape[-1] = 1
    cumulative = np.concatenate(
        [np.zeros(zero_shape, dtype=np.complex128), cumulative], axis=-1
    )
    local_sum = cumulative[..., window:] - cumulative[..., :-window]
    return local_sum / window


class DewowStage:
    """Dewow stage: subtract a centered moving average along the time axis.

    Constructed with a physical ``window_s`` (seconds, validated eagerly);
    the sample interval is derived from the INPUT time axis at apply time
    (never passed by callers), then converted to an odd sample count through
    the pinned rounding chain (:func:`window_samples_for`).  ``apply`` runs
    the fail-closed guard sequence (history predecessor domain, duplicate
    dewow, flat-before-dewow ordering, complex finiteness, axis regularity),
    subtracts the reflect-padded centered moving average in one vectorized
    O(N) pass and returns a fresh immutable ``TimeDomainScan`` with
    ``kind=time_processed`` plus exactly one appended record describing the
    FULLY REPRODUCIBLE configuration (window_s / dt_s / window_samples /
    padding / operation / axis / sample count / frozen reference digests).

    Inputs are never mutated.  Re-applying dewow inside one history fails
    closed twice over: this stage refuses it directly and
    :meth:`ProcessingHistory.append` refuses the repeated stable stage name
    even at a bumped ``stage_version``.  A history that already applied the
    ISSUE-035 flat stage is an illegal ordering and is refused before any
    numeric work.
    """

    def __init__(self, window_s: float = DEFAULT_DEWOW_WINDOW_S) -> None:
        self._window_s = _validate_window_s(window_s)

    @property
    def window_s(self) -> float:
        """The validated dewow window in seconds."""
        return self._window_s

    @property
    def stage_name(self) -> str:
        return DEWOW_STAGE_NAME

    @property
    def stage_version(self) -> str:
        return DEWOW_STAGE_VERSION

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
                "operation": "subtract_centered_moving_average",
                "axis": "time_last",
                "padding": DEWOW_PADDING,
                "window_s": self._window_s,
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
        """Dewow one time-domain scan and append the provenance record.

        ``source`` is typed ``object`` (widened against the ISSUE-030
        protocol signature) so the runtime ``isinstance`` gate below stays
        the single fail-closed authority: anything but a ``TimeDomainScan``
        raises ``TypeError`` before any work happens.  ``history`` defaults
        to ``source.history`` (the archived provenance of the snapshot); an
        explicitly supplied one must still end in a legal time-domain
        predecessor.  ``executed_utc`` wins when given; otherwise the
        injected ``clock`` (default: the system UTC clock) stamps the record
        once — no sleeping, no polling.
        """
        if not isinstance(source, TimeDomainScan):
            raise TypeError(
                "dewow input must be a TimeDomainScan, "
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
                "dewow input domain is not a legal time-domain predecessor",
                {
                    "stage_name": DEWOW_STAGE_NAME,
                    "input_domain": input_domain.value,
                    "allowed_input_domains": [
                        domain.value
                        for domain in sorted(
                            _TIME_INPUT_DOMAINS, key=lambda d: d.value
                        )
                    ],
                },
            )

        # --- guard 2: duplicate dewow (stage-level first gate) -------------
        if any(record.stage_name == DEWOW_STAGE_NAME for record in effective_history.records):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "dewow may be applied only once per history; re-processing "
                "requires a new history/revision",
                {
                    "stage_name": DEWOW_STAGE_NAME,
                    "stage_version": DEWOW_STAGE_VERSION,
                },
            )

        # --- guard 3: fixed recommended order dewow -> flat ----------------
        if any(
            record.stage_name == _FLAT_STAGE_NAME
            for record in effective_history.records
        ):
            raise DomainError(
                ErrorCode.PROCESSING_DOMAIN_MISMATCH,
                "the fixed recommended order is dewow -> "
                "flat_reflection_filter: the processing history already "
                "contains flat_reflection_filter, dewow is refused",
                {"stage_name": DEWOW_STAGE_NAME},
            )

        # --- guard 4: kind/predecessor coherence (defense-in-depth) --------
        expected_kind = TimeDomainKind(input_domain.value)
        if source.kind is not expected_kind:
            raise DomainError(
                ErrorCode.PROCESSING_DOMAIN_MISMATCH,
                "dewow scan kind does not match the history predecessor domain",
                {
                    "kind": source.kind.value,
                    "input_domain": input_domain.value,
                },
            )

        # --- guard 5: shape self-consistency double-check ------------------
        n_time = int(source.data.shape[-1])
        if source.data.ndim != 3 or n_time != int(source.time_axis_s.size):
            raise DomainError(
                ErrorCode.SHAPE_MISMATCH,
                "dewow input trailing time length differs from time_axis_s",
                {
                    "data_last_dim": n_time,
                    "time_axis_s_size": int(source.time_axis_s.size),
                },
            )

        # --- guard 6: complex finiteness entry gate -------------------------
        if not np.all(np.isfinite(source.data)):
            bad = int(np.flatnonzero(~np.isfinite(source.data))[0])
            raise DomainError(
                ErrorCode.NON_FINITE_AXIS,
                "dewow input data contains NaN or infinity",
                {"flat_index": bad},
            )

        # --- configuration resolution from the archived axis ---------------
        dt_s = derive_sample_interval_s(source.time_axis_s)
        window_samples = window_samples_for(self._window_s, dt_s, n_time)

        # One vectorized pass over the whole buffer; source.data is the
        # input model's read-only view, the subtraction produces a fresh
        # writable array that the rebuilt core model defensively copies
        # into its own write-protected snapshot — the input is never touched.
        local_mean = centered_moving_mean(source.data, window=window_samples)
        filtered = source.data - local_mean

        record = _record_for(
            stage_name=DEWOW_STAGE_NAME,
            stage_version=DEWOW_STAGE_VERSION,
            parameters={
                **dict(self.parameters),
                "window_s": self._window_s,
                "dt_s": dt_s,
                "window_samples": int(window_samples),
                "time_sample_count": n_time,
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
assert isinstance(DewowStage(), ProcessingStage)
