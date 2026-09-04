"""ProcessingStage contract and the migrated sin^2 four-edge frequency bandpass.

ISSUE-030 (docs/issues/M06_CALIBRATION_PROCESSING.md, docs/PROCESSING.md
sections 1-3, docs/reports/ISSUE_030_BASELINE_CONFIRMATION.md section 3,
docs/plans/2026-09-05-issue-030-bandpass.md decisions D1-D9).

The reference source is ``rebar_inspector/processing/bandpass.py`` (local
read-only copy ``D:/博士任务/rebar-inspector``, SHA-256
``3ee559e33e95c71702b04fe19eb9a24d2f676206d0b5471ec1e5038e17c38d51``, equal to
the frozen value in ``docs/reference-baselines/manifest.md``; see plan section
7 for what was migrated, tightened and excluded).  The window formula, edge
constraints and default edges are transcribed from that contract; the input
extraction, FilteredFrequency carrier and re-filtering path are not (D2/D3).

Contract surface:

- :class:`ProcessingStage` — runtime-checkable protocol every independent
  stage implements: stable ``stage_name`` / ``stage_version``, explicit
  accepted input domains and a single ``output_domain``, plus ``apply``.
  Stages never build a parallel history type: appending goes through the
  frozen ISSUE-007 :class:`~uav_gpr.core.time_domain.ProcessingHistory`, so
  chain validation, the raw start rule and per-history stage uniqueness
  (a new version does not bypass them) stay enforced centrally.
- :func:`build_bandpass_window` — pure sin^2 four-edge window on the Hz axis
  (fail-closed on malformed edges and bands disjoint from the axis).
- :class:`BandpassStage` — applies the window along the last (frequency)
  axis of a :class:`~uav_gpr.core.frequency.FrequencySweep` or
  :class:`~uav_gpr.core.frequency.FrequencyScan`, preserving complex dtype
  and the channel/trace axes, returning a brand-new immutable model plus one
  appended ``ProcessingRecord`` (``frequency_filtered`` output domain).

Domain semantics (PROCESSING.md section 2): the *data domain* of the input
array is whatever the supplied history currently ends in (an empty history
means ``frequency_raw``); core frequency models carry no domain field, so
provenance — not array shape — decides it.  Legal predecessors of the
bandpass are ``frequency_raw``, ``frequency_calibrated`` and
``frequency_background_applied``; ``frequency_filtered`` (re-filtering) and
any time domain are rejected fail-closed with
``ErrorCode.PROCESSING_DOMAIN_MISMATCH``.  Raw arrays can never be modified:
inputs are write-protected core snapshots and the output is built from a
fresh product array.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Final, Protocol, runtime_checkable

import numpy as np

from uav_gpr import __version__ as _SOFTWARE_VERSION
from uav_gpr.core.enums import DataDomain
from uav_gpr.core.errors import DomainError, ErrorCode, JsonValue
from uav_gpr.core.frequency import FrequencyScan, FrequencySweep
from uav_gpr.core.time_domain import ProcessingHistory, ProcessingRecord
from uav_gpr.core.timeutil import Clock, SystemClock, ensure_utc

__all__ = [
    "BANDPASS_STAGE_NAME",
    "BANDPASS_STAGE_VERSION",
    "DEFAULT_BANDPASS_EDGES_HZ",
    "BandpassStage",
    "ProcessingStage",
    "StageResult",
    "build_bandpass_window",
]

#: Stable snake_case stage name (never reused by any other stage).
BANDPASS_STAGE_NAME: Final = "frequency_bandpass"

#: Version token of the migrated window contract.
BANDPASS_STAGE_VERSION: Final = "1.0"

#: Default edge frequencies in Hz (0.5 / 1.0 / 1.5 / 2.5 GHz), identical to
#: the frozen reference implementation and MATLAB ``BPF_freq.m``.
DEFAULT_BANDPASS_EDGES_HZ: Final[tuple[float, float, float, float]] = (
    0.5e9,
    1.0e9,
    1.5e9,
    2.5e9,
)

#: Frequency domains a stage may legally consume before producing
#: ``frequency_filtered`` (docs/PROCESSING.md section 2 derivation chain).
_FREQUENCY_INPUT_DOMAINS: Final[frozenset[DataDomain]] = frozenset(
    {
        DataDomain.FREQUENCY_RAW,
        DataDomain.FREQUENCY_CALIBRATED,
        DataDomain.FREQUENCY_BACKGROUND_APPLIED,
    }
)


@runtime_checkable
class ProcessingStage(Protocol):
    """Independent, provenance-preserving processing stage (plan D1).

    Implementations declare their identity (``stage_name`` /
    ``stage_version``) and the exact data-domain transition they perform;
    ``apply`` consumes a core model together with the caller-supplied
    processing history and returns a fresh :class:`StageResult`.  Appending
    must go through :meth:`ProcessingHistory.append` so the ISSUE-007
    fail-closed rules keep holding.
    """

    @property
    def stage_name(self) -> str: ...

    @property
    def stage_version(self) -> str: ...

    @property
    def input_domain(self) -> frozenset[DataDomain]: ...

    @property
    def output_domain(self) -> DataDomain: ...

    def apply(
        self,
        source: FrequencySweep | FrequencyScan,
        *,
        history: ProcessingHistory,
        executed_utc: datetime | None = None,
        clock: Clock | None = None,
    ) -> StageResult: ...


@dataclass(frozen=True, slots=True)
class StageResult:
    """One stage application: new model + appended history + resulting domain.

    ``source`` is always a brand-new immutable object (never the input);
    ``history`` is the appended result and ``domain`` its ending data domain
    (here always ``frequency_filtered``).
    """

    source: FrequencySweep | FrequencyScan
    history: ProcessingHistory
    domain: DataDomain

    def __post_init__(self) -> None:
        if not isinstance(self.source, (FrequencySweep, FrequencyScan)):
            raise TypeError(
                "StageResult.source must be a FrequencySweep or FrequencyScan, "
                f"got {type(self.source).__name__}"
            )
        if not isinstance(self.history, ProcessingHistory):
            raise TypeError(
                "StageResult.history must be a ProcessingHistory, "
                f"got {type(self.history).__name__}"
            )
        if not isinstance(self.domain, DataDomain):
            raise TypeError(
                f"StageResult.domain must be a DataDomain, got {type(self.domain).__name__}"
            )


def _edges_context(edges: Sequence[object]) -> dict[str, JsonValue]:
    """JSON-safe echo of the rejected edges.

    Non-scalars and non-finite values become repr strings so the structured
    context stays JSON-safe (DomainError forbids NaN/Inf payload floats).
    """
    echoed: list[JsonValue] = []
    for edge in edges:
        if isinstance(edge, bool) or not isinstance(edge, (int, float)):
            echoed.append(repr(edge))
        elif not np.isfinite(float(edge)):
            echoed.append(repr(edge))
        else:
            echoed.append(float(edge))
    return {"edges_hz": echoed}


def _validate_edges(edges_hz: Sequence[float]) -> tuple[float, float, float, float]:
    """Fail-closed four-edge check, identical constraint set to the reference.

    Requires exactly four real scalar values with ``0 <= f1 < f2 <= f3 < f4``
    (so ``f2 == f3`` collapses the passband but stays legal, matching the
    frozen reference); non-finite, negative-first, unordered, bool or
    non-scalar entries are rejected with ``INVALID_ARGUMENT``.
    """
    edges = tuple(edges_hz)
    if len(edges) != 4:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "bandpass edges must contain exactly f1, f2, f3, f4",
            _edges_context(edges),
        )
    values: list[float] = []
    for edge in edges:
        if isinstance(edge, bool) or not isinstance(edge, (int, float)):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "bandpass edges must be real scalar numbers",
                _edges_context(edges),
            )
        values.append(float(edge))
    if not all(np.isfinite(values)):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "bandpass edges must be finite",
            _edges_context(edges),
        )
    f1, f2, f3, f4 = values
    if not (0.0 <= f1 < f2 <= f3 < f4):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "bandpass edges must satisfy 0 <= f1 < f2 <= f3 < f4",
            _edges_context(edges),
        )
    return (f1, f2, f3, f4)


def _require_axis_compatible(frequency: np.ndarray, edges: tuple[float, ...]) -> None:
    """Reject bands fully disjoint from the acquisition axis (plan M2 tightening).

    A window that is zero everywhere silently destroys the trace; the
    contract requires an explicit refusal instead.  Partial overlap stays
    legal (reference behaviour).
    """
    f1, _, _, f4 = edges
    if f4 <= float(frequency[0]) or float(frequency[-1]) <= f1:
        raise DomainError(
            ErrorCode.OUT_OF_RANGE,
            "bandpass edges are disjoint from the acquisition frequency band",
            {
                "edges_hz": [float(e) for e in edges],
                "axis_min_hz": float(frequency[0]),
                "axis_max_hz": float(frequency[-1]),
            },
        )


def build_bandpass_window(
    frequency_hz: Sequence[float] | np.ndarray,
    edges_hz: Sequence[float] = DEFAULT_BANDPASS_EDGES_HZ,
) -> np.ndarray:
    """Return the sin^2 four-edge bandpass window for one Hz frequency axis.

    Transcribed from the frozen reference (module docstring): below ``f1``
    and above ``f4`` the response is 0; ``f1 -> f2`` rises as
    ``sin^2(0.5*pi*(f-f1)/(f2-f1))``; ``f2 -> f3`` is the unit passband;
    ``f3 -> f4`` falls as ``sin^2(0.5*pi*(f4-f)/(f4-f3))``.  Boundary points
    belong to both adjacent segments exactly like the reference.  The axis
    itself is validated only for finiteness/monotonicity requirements that
    the core models already enforce; this function additionally rejects
    non-one-dimensional axes and bands disjoint from the axis.
    """
    edges = _validate_edges(edges_hz)
    frequency = np.asarray(frequency_hz, dtype=np.float64)
    if frequency.ndim != 1:
        raise DomainError(
            ErrorCode.AXIS_MISMATCH,
            "frequency axis must be one-dimensional",
            {"ndim": frequency.ndim},
        )
    if frequency.size == 0:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT, "frequency axis must not be empty"
        )
    if not np.all(np.isfinite(frequency)):
        raise DomainError(
            ErrorCode.NON_FINITE_AXIS,
            "frequency axis contains NaN or infinity",
        )
    if frequency.size > 1 and not np.all(np.diff(frequency) > 0):
        raise DomainError(
            ErrorCode.NON_INCREASING_AXIS,
            "frequency axis must be strictly increasing",
        )
    _require_axis_compatible(frequency, edges)

    f1, f2, f3, f4 = edges
    window = np.zeros(frequency.shape, dtype=np.float64)

    rising = (frequency >= f1) & (frequency <= f2)
    window[rising] = np.sin(0.5 * np.pi * (frequency[rising] - f1) / (f2 - f1)) ** 2

    passband = (frequency >= f2) & (frequency <= f3)
    window[passband] = 1.0

    falling = (frequency >= f3) & (frequency <= f4)
    window[falling] = np.sin(0.5 * np.pi * (f4 - frequency[falling]) / (f4 - f3)) ** 2
    return window


def _input_domain_of(history: ProcessingHistory) -> DataDomain:
    """The data domain implied by a history (empty history = frequency_raw)."""
    if len(history) == 0:
        return DataDomain.FREQUENCY_RAW
    return history.records[-1].output_domain


def _record_for(
    *,
    stage_name: str,
    stage_version: str,
    parameters: dict[str, JsonValue],
    input_domain: DataDomain,
    output_domain: DataDomain,
    executed_utc: datetime,
) -> ProcessingRecord:
    return ProcessingRecord(
        stage_name=stage_name,
        stage_version=stage_version,
        parameters=parameters,
        input_domain=input_domain,
        output_domain=output_domain,
        executed_utc=executed_utc,
        software_version=_SOFTWARE_VERSION,
    )


class BandpassStage:
    """sin^2 four-edge frequency bandpass stage (satisfies :class:`ProcessingStage`).

    ``apply`` multiplies the input complex spectrum by the window along the
    last (frequency) axis — vectorized over channels and traces in one
    broadcast — and appends exactly one ``frequency_bandpass`` record to the
    supplied history.  Input models are never mutated; the output keeps the
    input's container type, shape, channels and per-trace metadata.
    Re-applying the stage inside one history fails closed via
    :meth:`ProcessingHistory.append` (a bumped ``stage_version`` does not
    bypass it), and feeding data whose history ends in a domain outside
    ``{frequency_raw, frequency_calibrated, frequency_background_applied}``
    is refused with ``PROCESSING_DOMAIN_MISMATCH``.
    """

    def __init__(
        self,
        edges_hz: Sequence[float] = DEFAULT_BANDPASS_EDGES_HZ,
    ) -> None:
        self._edges_hz = _validate_edges(edges_hz)

    @property
    def edges_hz(self) -> tuple[float, float, float, float]:
        """The validated, normalized edge frequencies in Hz."""
        return self._edges_hz

    @property
    def stage_name(self) -> str:
        return BANDPASS_STAGE_NAME

    @property
    def stage_version(self) -> str:
        return BANDPASS_STAGE_VERSION

    @property
    def input_domain(self) -> frozenset[DataDomain]:
        return _FREQUENCY_INPUT_DOMAINS

    @property
    def output_domain(self) -> DataDomain:
        return DataDomain.FREQUENCY_FILTERED

    @property
    def parameters(self) -> Mapping[str, JsonValue]:
        """Canonical JSON-safe stage parameters recorded into every history entry."""
        return MappingProxyType(
            {
                "edges_hz": [float(edge) for edge in self._edges_hz],
                "window": "sin_squared",
            }
        )

    def apply(
        self,
        source: FrequencySweep | FrequencyScan,
        *,
        history: ProcessingHistory,
        executed_utc: datetime | None = None,
        clock: Clock | None = None,
    ) -> StageResult:
        """Filter one sweep/scan and append the provenance record.

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
                "bandpass input must be a FrequencySweep or FrequencyScan, "
                f"got {type(source).__name__}"
            )
        if executed_utc is not None:
            # Fail closed on naive/offset-less stamps before any work.
            stamp = ensure_utc(executed_utc)
        else:
            stamp = (clock or SystemClock()).utc_now()

        input_domain = _input_domain_of(history)
        if input_domain not in _FREQUENCY_INPUT_DOMAINS:
            raise DomainError(
                ErrorCode.PROCESSING_DOMAIN_MISMATCH,
                "bandpass input domain is not a legal frequency predecessor",
                {
                    "stage_name": BANDPASS_STAGE_NAME,
                    "input_domain": input_domain.value,
                    "allowed_input_domains": [
                        domain.value
                        for domain in sorted(
                            _FREQUENCY_INPUT_DOMAINS, key=lambda d: d.value
                        )
                    ],
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

        window = build_bandpass_window(source.frequencies_hz, self._edges_hz)
        # window is 1-D (n_freq,) and broadcasts over the leading channel
        # (and trace) axes; the product is a fresh writable array that the
        # rebuilt core model defensively copies into its own write-protected
        # snapshot, so neither the raw input nor the window can alias it.
        filtered = source.data * window

        record = _record_for(
            stage_name=BANDPASS_STAGE_NAME,
            stage_version=BANDPASS_STAGE_VERSION,
            parameters=dict(self.parameters),
            input_domain=input_domain,
            output_domain=DataDomain.FREQUENCY_FILTERED,
            executed_utc=stamp,
        )
        new_history = history.append(record)
        output_source: FrequencySweep | FrequencyScan
        if isinstance(source, FrequencySweep):
            output_source = FrequencySweep(
                channels=source.channels,
                frequencies_hz=source.frequencies_hz,
                data=filtered,
                metadata=source.metadata,
            )
        else:
            output_source = FrequencyScan(
                channels=source.channels,
                frequencies_hz=source.frequencies_hz,
                data=filtered,
                metadata=source.metadata,
            )
        return StageResult(
            source=output_source,
            history=new_history,
            domain=DataDomain.FREQUENCY_FILTERED,
        )
