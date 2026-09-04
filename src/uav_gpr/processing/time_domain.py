"""Zero-padded IFFT: uniform frequency axis -> full ``time_base`` + read-only display crop.

ISSUE-031 (docs/issues/M06_CALIBRATION_PROCESSING.md ISSUE-031,
docs/PROCESSING.md section 4, docs/ACQUISITION.md section 6,
docs/DATA_MODEL.md section 8, docs/plans/2026-09-05-issue-031-ifft.md D1-D9).

The reference source is ``rebar_inspector/processing/ifft.py`` (local
read-only copy ``D:/博士任务/rebar-inspector``, SHA-256
``9496288e9e918f788b88f41945ea5e43889cfb3c298cccf7543a33b5a41d297a``, equal to
the frozen value in ``docs/reference-baselines/manifest.md``; the stage-skeleton
cross-check additionally pins ``_time_stage_common.py`` SHA-256
``e0c201b55acbaece0edb1546bbb8a00492874bb79fb9caf789d5ba416d333c81``).
Migrated contract, not copied code: the uniform-step tolerance rule
(``allowed_error = max(1 Hz, df * 1e-6)``), DC->start low-frequency zero
padding with nearest-bin alignment of the first measured point, restoration of
the residual sub-bin start-frequency offset after the IFFT via the phase ramp
``exp(2j*pi*df0*t)``, the next-power-of-two FFT length scaled by an explicit
oversampling factor, and the seconds time axis ``arange(N) / (N * df)``.
Deliberately NOT migrated (plan M2 tightening): the reference's ``max_time_s``
output truncation — this project archives the FULL physical unambiguous window
in ``time_base`` and crops only through the read-only
:class:`DisplayTimeWindowView`, which never touches the archived arrays.  The
reference's embedded-history carrier is also excluded: like ISSUE-030, UAV-GPR
passes the :class:`~uav_gpr.core.time_domain.ProcessingHistory` explicitly.

Contract surface:

- :func:`compute_ifft_grid` — pure grid computation from a uniform Hz axis
  (fail-closed on non-uniform spacing, duplicate/decreasing points, negative
  frequencies, non-finite values, undersized axes, malformed parameters).
- :class:`FrequencyToTimeStage` — implements the frozen ISSUE-030
  :class:`~uav_gpr.processing.bandpass.ProcessingStage` protocol over the
  ISSUE-007 history: consumes a :class:`~uav_gpr.core.frequency.FrequencySweep`
  (``channel x frequency``) or :class:`~uav_gpr.core.frequency.FrequencyScan`
  (``trace x channel x frequency``), vectorizes ``np.fft.ifft`` along the last
  axis, returns a brand-new immutable
  :class:`~uav_gpr.core.time_domain.TimeDomainScan` (``kind=time_base``; core
  has no single-sweep time container, so sweep input yields a one-trace scan)
  plus a fresh :class:`TimeDomainStageResult` whose output domain is
  ``time_base``.
- :class:`DisplayCropConfig` + :class:`DisplayTimeWindowView` — the independent
  display-time-window layer (AGENTS.md section 8): a validated serializable
  config and a read-only view that never copies, mutates or replaces the
  archived ``time_base`` arrays; out-of-range crops fail closed.

Physical semantics (ACQUISITION.md section 6): the uniform step ``df`` sets the
unambiguous time period ``T = 1/df``; bandwidth shapes the time-resolution
capability only; zero padding interpolates displayed samples and NEVER creates
physical resolution.  No depth is computed anywhere in this module (no
velocity/distance field exists), bandpass is not embedded — it stays an
optional independent predecessor stage (ISSUE-030).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Final

import numpy as np

from uav_gpr.core.enums import DataDomain, TimeDomainKind
from uav_gpr.core.errors import DomainError, ErrorCode, JsonValue
from uav_gpr.core.frequency import FrequencyScan, FrequencySweep
from uav_gpr.core.time_domain import ProcessingHistory, TimeDomainScan
from uav_gpr.core.timeutil import Clock, SystemClock, ensure_utc
from uav_gpr.processing.bandpass import ProcessingStage, _record_for

__all__ = [
    "DEFAULT_IFFT_OVERSAMPLING",
    "IFFT_GRID_TOLERANCE_REL",
    "IFFT_STAGE_NAME",
    "IFFT_STAGE_VERSION",
    "AxisSpan",
    "DisplayCropConfig",
    "DisplayTimeWindowView",
    "FrequencyToTimeStage",
    "TimeDomainStageResult",
    "compute_ifft_grid",
]

#: Stable snake_case stage name (never reused by any other stage).
IFFT_STAGE_NAME: Final = "frequency_to_time_ifft"

#: Version token of the migrated zero-padding IFFT contract.
IFFT_STAGE_VERSION: Final = "1.0"

#: Default explicit FFT interpolation factor: the power-of-two grid is
#: upsampled by this factor (display sampling only, never physical resolution).
DEFAULT_IFFT_OVERSAMPLING: Final = 16

#: Relative tolerance of the uniform-step check, identical to the frozen
#: reference: ``allowed_error = max(1.0 Hz, |df| * 1e-6)``.
IFFT_GRID_TOLERANCE_REL: Final = 1e-6

#: Frequency domains an IFFT stage may legally consume: every predecessor of
#: ``time_base`` in the docs/PROCESSING.md section 2 derivation chain
#: (bandpass stays optional; skipping it is a legal chain, embedding it here
#: would violate stage independence).
_FREQUENCY_INPUT_DOMAINS: Final[frozenset[DataDomain]] = frozenset(
    {
        DataDomain.FREQUENCY_RAW,
        DataDomain.FREQUENCY_CALIBRATED,
        DataDomain.FREQUENCY_BACKGROUND_APPLIED,
        DataDomain.FREQUENCY_FILTERED,
    }
)


def _require_positive_int(value: object, name: str) -> int:
    """Strict positive ``int`` (bool rejected even though it subclasses int)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            f"{name} must be a positive integer",
            {name: repr(value)},
        )
    if value < 1:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            f"{name} must be >= 1",
            {name: value},
        )
    return value


def _next_power_of_two(value: int) -> int:
    """Smallest power of two >= ``value`` (transcribed from the reference)."""
    if value < 1:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT, "FFT input length must be positive"
        )
    return 1 << (value - 1).bit_length()


@dataclass(frozen=True, slots=True)
class AxisSpan:
    """Immutable echo of a validated uniform frequency axis."""

    delta_hz: float
    count: int
    min_hz: float
    max_hz: float
    max_step_error_hz: float
    allowed_error_hz: float

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "delta_hz": self.delta_hz,
            "count": self.count,
            "min_hz": self.min_hz,
            "max_hz": self.max_hz,
            "max_step_error_hz": self.max_step_error_hz,
            "allowed_error_hz": self.allowed_error_hz,
        }


def validate_uniform_axis(frequencies_hz: object) -> tuple[np.ndarray, AxisSpan]:
    """Return ``(float64 axis, span facts)`` or fail closed.

    Core models already guarantee a finite, strictly increasing real 1-D axis
    at construction time; this adds the ISSUE-031 grid rules (PROCESSING.md
    section 4): at least two points, no negative frequencies, uniqueness
    re-checked on raw input (callers bypassing the models stay safe) and every
    adjacent step within ``max(1 Hz, df * 1e-6)`` of the median step.  A
    non-uniform axis must never be silently fed into a plain IFFT.
    """
    raw = np.asarray(frequencies_hz)
    if raw.ndim != 1:
        raise DomainError(
            ErrorCode.AXIS_MISMATCH,
            "frequency axis must be one-dimensional",
            {"ndim": raw.ndim},
        )
    if raw.size < 2:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "IFFT requires at least two frequency points",
            {"count": int(raw.size)},
        )
    if raw.dtype.kind not in "iuf":
        raise DomainError(
            ErrorCode.DTYPE_MISMATCH,
            "frequency axis must be real-valued numeric",
            {"dtype": str(raw.dtype)},
        )
    frequency = np.asarray(raw, dtype=np.float64)
    if not np.all(np.isfinite(frequency)):
        raise DomainError(
            ErrorCode.NON_FINITE_AXIS,
            "frequency axis contains NaN or infinity",
        )
    if frequency[0] < 0.0:
        raise DomainError(
            ErrorCode.OUT_OF_RANGE,
            "negative frequencies are not supported",
            {"min_hz": float(frequency[0])},
        )
    steps = np.diff(frequency)
    if np.any(steps <= 0.0):
        raise DomainError(
            ErrorCode.NON_INCREASING_AXIS,
            "frequency points must be unique and strictly increasing",
            {"bad_steps": int(np.count_nonzero(steps <= 0.0))},
        )
    df = float(np.median(steps))
    allowed_error = max(1.0, abs(df) * IFFT_GRID_TOLERANCE_REL)
    max_error = float(np.max(np.abs(steps - df)))
    if max_error > allowed_error:
        raise DomainError(
            ErrorCode.NON_UNIFORM_AXIS,
            "IFFT requires an equally spaced frequency axis",
            {
                "delta_hz": df,
                "max_step_error_hz": max_error,
                "allowed_error_hz": allowed_error,
            },
        )
    span = AxisSpan(
        delta_hz=df,
        count=int(frequency.size),
        min_hz=float(frequency[0]),
        max_hz=float(frequency[-1]),
        max_step_error_hz=max_error,
        allowed_error_hz=allowed_error,
    )
    return frequency, span


def compute_ifft_grid(
    frequencies_hz: Sequence[float] | np.ndarray,
    *,
    oversampling: int = DEFAULT_IFFT_OVERSAMPLING,
    fft_size: int | None = None,
) -> tuple[np.ndarray, float, int, int, float]:
    """Compute the IFFT grid from a uniform Hz axis (reference-aligned).

    Returns ``(time_axis_s, delta_hz, resolved_fft_size, first_bin,
    start_frequency_offset_hz)`` where:

    - ``first_bin`` aligns the first measured point to the nearest DFT bin of
      the ``df``-spaced grid that starts at DC; bins below it are the explicit
      missing-low-frequency zero-fill policy (PROCESSING.md section 4);
    - with no absolute ``fft_size``, the length is
      ``next_power_of_two(first_bin + n) * oversampling`` (explicit recorded
      interpolation); an absolute ``fft_size`` must be a power of two and must
      cover the padded spectrum;
    - ``time_axis_s = arange(N) / (N * df)`` spans the full unambiguous
      physical period ``T = 1/df`` (ACQUISITION.md section 6);
    - ``start_frequency_offset_hz`` is the residual sub-bin shift of the true
      start frequency against the aligned grid; the caller restores it after
      the IFFT with the phase ramp ``exp(2j*pi*offset*t)`` (verbatim reference
      behaviour, keeps scientific results identical).
    """
    frequency, span = validate_uniform_axis(frequencies_hz)
    oversampling_value = _require_positive_int(oversampling, "oversampling")
    df = span.delta_hz
    first_bin = max(0, round(float(frequency[0]) / df))
    grid_start_hz = first_bin * df
    offset = float(frequency[0]) - grid_start_hz
    padded_length = first_bin + span.count
    if fft_size is None:
        resolved = _next_power_of_two(padded_length) * oversampling_value
    else:
        resolved = _require_positive_int(fft_size, "fft_size")
        if resolved & (resolved - 1) != 0:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "fft_size must be a power of two",
                {"fft_size": resolved},
            )
        if resolved < padded_length:
            raise DomainError(
                ErrorCode.OUT_OF_RANGE,
                "fft_size cannot hold the measured spectrum after "
                "DC-to-start zero padding",
                {"fft_size": resolved, "required_min": padded_length},
            )
    time_axis = np.arange(resolved, dtype=np.float64) / (resolved * df)
    return time_axis, df, resolved, first_bin, offset


def _input_domain_of(history: ProcessingHistory) -> DataDomain:
    """The data domain implied by a history (empty history = frequency_raw)."""
    if len(history) == 0:
        return DataDomain.FREQUENCY_RAW
    return history.records[-1].output_domain


def _window_context(start_s: object, end_s: object) -> dict[str, JsonValue]:
    """JSON-safe echo of rejected crop bounds (non-finite -> repr string)."""

    def _echo(value: object) -> JsonValue:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return repr(value)
        number = float(value)
        return number if math.isfinite(number) else repr(value)

    return {"start_s": _echo(start_s), "end_s": _echo(end_s)}


@dataclass(frozen=True, slots=True)
class DisplayCropConfig:
    """Validated display time-window crop bounds in seconds (immutable).

    Pure configuration — constructing it touches no data (AGENTS.md section 8:
    the display window is a concept separate from the physical unambiguous
    window).  Both bounds must be finite numbers with ``0 <= start <= end``;
    fitting against the archived axis happens fail-closed in
    :meth:`DisplayTimeWindowView.for_scan`.
    """

    start_s: float
    end_s: float

    def __post_init__(self) -> None:
        start = self.start_s
        end = self.end_s
        for value, name in ((start, "start_s"), (end, "end_s")):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    f"display crop {name} must be a real scalar number",
                    _window_context(start, end),
                )
        start = float(start)
        end = float(end)
        if not (math.isfinite(start) and math.isfinite(end)):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "display crop bounds must be finite",
                _window_context(start, end),
            )
        if start < 0.0:
            raise DomainError(
                ErrorCode.OUT_OF_RANGE,
                "display crop start must not be negative",
                _window_context(start, end),
            )
        if end < start:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "display crop end must not precede start",
                _window_context(start, end),
            )
        object.__setattr__(self, "start_s", start)
        object.__setattr__(self, "end_s", end)

    @property
    def parameters(self) -> Mapping[str, JsonValue]:
        """Read-only JSON-safe canonical form (unit-bearing keys)."""
        return MappingProxyType({"start_s": self.start_s, "end_s": self.end_s})

    def to_json(self) -> dict[str, JsonValue]:
        return {"start_s": self.start_s, "end_s": self.end_s}


@dataclass(frozen=True, slots=True)
class DisplayTimeWindowView:
    """Read-only cropped window over an archived ``time_base`` scan.

    The view holds references to the ORIGINAL write-protected arrays plus
    precomputed index bounds — it never copies, eagerly slices, mutates or
    replaces the archived ``time_base`` (acceptance: display crop does not
    modify/truncate the archive).  It binds to one exact scan object and
    refuses anything but ``kind=time_base`` so a stale view can never alias
    another result.
    """

    scan: TimeDomainScan
    config: DisplayCropConfig
    start_index: int
    stop_index: int

    def __post_init__(self) -> None:
        if not isinstance(self.scan, TimeDomainScan):
            raise TypeError(
                "view scan must be a TimeDomainScan, "
                f"got {type(self.scan).__name__}"
            )
        if self.scan.kind is not TimeDomainKind.TIME_BASE:
            raise DomainError(
                ErrorCode.PROCESSING_DOMAIN_MISMATCH,
                "display crop views bind to time_base scans only",
                {"kind": self.scan.kind.value},
            )
        axis = self.scan.time_axis_s
        if not (0 <= self.start_index <= self.stop_index <= int(axis.size)):
            raise DomainError(
                ErrorCode.OUT_OF_RANGE,
                "display crop indices must fit the archived time axis",
                {
                    "start_index": self.start_index,
                    "stop_index": self.stop_index,
                    "axis_size": int(axis.size),
                },
            )
        if self.start_index == self.stop_index:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "display crop must contain at least one sample",
                {"start_index": self.start_index, "stop_index": self.stop_index},
            )
        if (
            float(axis[self.start_index]) < self.config.start_s
            or float(axis[self.stop_index - 1]) > self.config.end_s
        ):
            raise DomainError(
                ErrorCode.OUT_OF_RANGE,
                "display crop indices must stay inside the configured window",
                {
                    "config": self.config.to_json(),
                    "first_sample_s": float(axis[self.start_index]),
                    "last_sample_s": float(axis[self.stop_index - 1]),
                },
            )

    @classmethod
    def for_scan(
        cls, scan: TimeDomainScan, config: DisplayCropConfig
    ) -> DisplayTimeWindowView:
        """Resolve ``config`` against ``scan``'s archived axis (read-only).

        Fail-closed: bounds beyond the axis span (which for a full
        ``time_base`` is the physical unambiguous window minus one sample
        step) are rejected with ``OUT_OF_RANGE`` instead of silently clamped.
        """
        axis = scan.time_axis_s
        if axis.size == 0:  # pragma: no cover - core forbids empty axes
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT, "time axis must not be empty"
            )
        if config.end_s > float(axis[-1]):
            raise DomainError(
                ErrorCode.OUT_OF_RANGE,
                "display crop exceeds the archived time_base window",
                {**config.to_json(), "axis_max_s": float(axis[-1])},
            )
        start_index = int(np.searchsorted(axis, config.start_s, side="left"))
        stop_index = int(np.searchsorted(axis, config.end_s, side="right"))
        return cls(
            scan=scan,
            config=config,
            start_index=start_index,
            stop_index=stop_index,
        )

    @property
    def time_axis_s(self) -> np.ndarray:
        """Read-only window slice of the ARCHIVED axis buffer (no copy)."""
        return self.scan.time_axis_s[self.start_index : self.stop_index]

    @property
    def data(self) -> np.ndarray:
        """Read-only window slice of the ARCHIVED data buffer (no copy)."""
        return self.scan.data[..., self.start_index : self.stop_index]

    @property
    def sample_count(self) -> int:
        return self.stop_index - self.start_index

    def to_config_json(self) -> dict[str, JsonValue]:
        """Serializable description (bounds + resolved indices, no payload)."""
        return {
            "crop": self.config.to_json(),
            "start_index": self.start_index,
            "stop_index": self.stop_index,
            "archived_sample_count": int(self.scan.time_axis_s.size),
        }


@dataclass(frozen=True, slots=True)
class TimeDomainStageResult:
    """One time-domain stage application (ISSUE-030 StageResult analogue).

    ``source`` is always a brand-new immutable :class:`TimeDomainScan` (never
    the input); ``history`` is the appended result and ``domain`` its ending
    data domain (here always ``time_base``).  ISSUE-030's ``StageResult``
    restricts ``source`` to frequency containers, so the time-domain stage
    returns this sibling type; both keep the same three-field shape.
    """

    source: TimeDomainScan
    history: ProcessingHistory
    domain: DataDomain

    def __post_init__(self) -> None:
        if not isinstance(self.source, TimeDomainScan):
            raise TypeError(
                "TimeDomainStageResult.source must be a TimeDomainScan, "
                f"got {type(self.source).__name__}"
            )
        if not isinstance(self.history, ProcessingHistory):
            raise TypeError(
                "TimeDomainStageResult.history must be a ProcessingHistory, "
                f"got {type(self.history).__name__}"
            )
        if not isinstance(self.domain, DataDomain):
            raise TypeError(
                "TimeDomainStageResult.domain must be a DataDomain, "
                f"got {type(self.domain).__name__}"
            )


class FrequencyToTimeStage:
    """Zero-padded IFFT stage (structurally satisfies :class:`ProcessingStage`).

    ``apply`` validates the uniform frequency axis, aligns the first measured
    point to its nearest DFT bin, zero-fills DC->start, upsamples to the
    explicit FFT length, runs ``np.fft.ifft`` vectorized over the leading
    trace/channel axes in one call, restores the sub-bin start offset with the
    reference phase ramp, and returns a fresh :class:`TimeDomainScan` covering
    the FULL physical unambiguous period ``T = 1/df`` (sweep input yields a
    one-trace scan) with exactly one appended provenance record capturing the
    complete reproducible grid (Hz / seconds unit-bearing keys).

    Inputs are never mutated.  A history ending outside the legal frequency
    predecessors fails closed with ``PROCESSING_DOMAIN_MISMATCH``; repeated
    application inside one history is refused by
    :meth:`ProcessingHistory.append` (a bumped ``stage_version`` does not
    bypass it).  This stage embeds no bandpass, computes no depth and makes no
    resolution claim: oversampling only interpolates the display samples.
    """

    def __init__(
        self,
        *,
        oversampling: int = DEFAULT_IFFT_OVERSAMPLING,
        fft_size: int | None = None,
    ) -> None:
        self._oversampling = _require_positive_int(oversampling, "oversampling")
        if fft_size is None:
            self._fft_size: int | None = None
        else:
            resolved = _require_positive_int(fft_size, "fft_size")
            if resolved & (resolved - 1) != 0:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "fft_size must be a power of two",
                    {"fft_size": resolved},
                )
            self._fft_size = resolved

    @property
    def stage_name(self) -> str:
        return IFFT_STAGE_NAME

    @property
    def stage_version(self) -> str:
        return IFFT_STAGE_VERSION

    @property
    def input_domain(self) -> frozenset[DataDomain]:
        return _FREQUENCY_INPUT_DOMAINS

    @property
    def output_domain(self) -> DataDomain:
        return DataDomain.TIME_BASE

    @property
    def oversampling(self) -> int:
        return self._oversampling

    @property
    def fft_size(self) -> int | None:
        return self._fft_size

    @property
    def parameters(self) -> Mapping[str, JsonValue]:
        """Canonical stage configuration (JSON-safe, unit-bearing keys)."""
        return MappingProxyType(
            {
                "grid": "uniform_dft",
                "zero_padding_policy": "dc_to_start_low_frequency_zeros",
                "oversampling_factor": self._oversampling,
                "fft_size_mode": (
                    "power_of_two_times_oversampling"
                    if self._fft_size is None
                    else "explicit_fft_size"
                ),
                "explicit_fft_size": self._fft_size,
                "interpolation_only_no_physical_resolution_gain": True,
                "time_axis_unit": "s",
                "archived_window": "full_unambiguous_period",
                "physical_period_formula": "1/delta_f_hz",
                "depth_calculation": False,
            }
        )

    def apply(
        self,
        source: object,
        *,
        history: ProcessingHistory,
        executed_utc: datetime | None = None,
        clock: Clock | None = None,
    ) -> TimeDomainStageResult:
        """Transform one sweep/scan to the full ``time_base`` and append history.

        ``source`` is typed ``object`` (widened against the ISSUE-030 protocol
        signature) so the runtime ``isinstance`` gate below stays the single
        fail-closed authority: passing anything else raises ``TypeError``
        before any work happens.  ``executed_utc`` wins when given; otherwise
        the injected ``clock`` (default: the system UTC clock) stamps the
        record once — no sleeping, no polling.
        """
        if not isinstance(history, ProcessingHistory):
            raise TypeError(
                f"history must be a ProcessingHistory, got {type(history).__name__}"
            )
        if not isinstance(source, (FrequencySweep, FrequencyScan)):
            raise TypeError(
                "frequency-to-time input must be a FrequencySweep or "
                f"FrequencyScan, got {type(source).__name__}"
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
                "ifft input domain is not a legal frequency predecessor",
                {
                    "stage_name": IFFT_STAGE_NAME,
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

        time_axis_full, df, fft_size, first_bin, offset = compute_ifft_grid(
            source.frequencies_hz,
            oversampling=self._oversampling,
            fft_size=self._fft_size,
        )
        n_freq = int(source.frequencies_hz.size)

        # Vectorized over every leading trace/channel axis in one np.fft call.
        padded = np.zeros((*source.data.shape[:-1], fft_size), dtype=np.complex128)
        padded[..., first_bin : first_bin + n_freq] = source.data
        time_response = np.fft.ifft(padded, axis=-1)
        if offset != 0.0:
            # Restore the constant sub-bin start-frequency shift lost by the
            # nearest-bin alignment (verbatim reference correction).
            time_response = time_response * np.exp(2j * np.pi * offset * time_axis_full)
        if not np.all(np.isfinite(time_response)):
            raise DomainError(
                ErrorCode.NON_FINITE_AXIS,
                "ifft produced non-finite samples",
            )

        record = _record_for(
            stage_name=IFFT_STAGE_NAME,
            stage_version=IFFT_STAGE_VERSION,
            parameters={
                **dict(self.parameters),
                "frequency_delta_hz": df,
                "frequency_point_count": n_freq,
                "start_frequency_hz": float(source.frequencies_hz[0]),
                "stop_frequency_hz": float(source.frequencies_hz[-1]),
                "low_frequency_zero_bins": first_bin,
                "aligned_grid_start_hz": first_bin * df,
                "start_frequency_offset_hz": offset,
                "fft_size": fft_size,
                "time_sample_interval_s": float(1.0 / (fft_size * df)),
                "physical_unambiguous_period_s": float(1.0 / df),
            },
            input_domain=input_domain,
            output_domain=DataDomain.TIME_BASE,
            executed_utc=stamp,
        )
        new_history = history.append(record)

        from uav_gpr.core.metadata import TraceMetadata

        metadata: tuple[TraceMetadata | None, ...]
        if isinstance(source, FrequencySweep):
            trace_data = time_response[np.newaxis, ...]
            metadata = ()
        else:
            trace_data = time_response
            metadata = source.metadata
        scan = TimeDomainScan(
            channels=source.channels,
            time_axis_s=time_axis_full,
            data=trace_data,
            kind=TimeDomainKind.TIME_BASE,
            history=new_history,
            metadata=metadata,
        )
        return TimeDomainStageResult(
            source=scan,
            history=new_history,
            domain=DataDomain.TIME_BASE,
        )


# Structural conformance to the frozen ISSUE-030 protocol, checked statically
# so a future refactor that breaks the shape fails at import time.
assert isinstance(FrequencyToTimeStage(), ProcessingStage)
