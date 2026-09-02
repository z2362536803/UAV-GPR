"""One-port OSL (Open/Short/Load) three-term error model and solver (ISSUE-027).

Pure computation layer (numpy + stdlib only, no UI/hardware/storage): the
immutable calibration model, the complex solve, the correction math, the
degenerate/singular guards and the numerical quality metrics live here, while
reference capture sessions (ISSUE-028), ``.rcal`` persistence (ISSUE-029) and
the calibrated-domain processing stage (ISSUE-032) consume this module.

Error model (identical to the frozen rebar-inspector reference):

    measured = directivity + gamma * reflection_tracking
                              / (1 - gamma * source_match)

    x = measured - directivity
    corrected = x / (reflection_tracking + source_match * x)

Default standards are ideal: Open=+1, Short=-1, Load=0.  Frequency-dependent
complex Cal-Kit models may be supplied for any standard (scalar or per-point
complex values).  Each profile binds a full ``ChannelSpec`` whose S parameter
must be S11 or S22; S11/S22 profiles are solved and applied independently and
never share error terms.  Ordered multi-channel binding is provided by
:class:`OslCalibrationSet`.

Fail-closed rules (migrated semantics, mapped onto core ``DomainError``):

- input arrays are validated (complex dtype, shape, finite values) and never
  mutated; profile arrays are defensive copies with write protection and are
  only reachable through views, so they cannot be re-enabled for writing;
- the frequency axis must be a finite strictly increasing 1-D grid; DUT data
  must share the profile axis exactly (no interpolation or extrapolation);
- degenerate standards (normalized denominator <=
  ``SOLVE_DEGENERACY_TOLERANCE``) and correction singularities
  (|T + S*x| <= ``CORRECTION_SINGULAR_TOLERANCE`` * (|T| + |S*x|)) raise
  ``DomainError(INVALID_ARGUMENT)`` with a structured ``kind`` context -
  no fabricated values are ever produced.

Error code map (core ``ErrorCode`` is frozen; no new codes):

- ``INVALID_ARGUMENT``: non-finite data, empty captures, degenerate solve,
  singular correction, empty profile set;
- ``DTYPE_MISMATCH``: non-complex measurements/arrays, complex axis dtype;
- ``SHAPE_MISMATCH``: wrong dimensionality or container row count;
- ``AXIS_MISMATCH``: frequency counts/axes differ between data and profile,
  or between profiles of one set;
- ``NON_FINITE_AXIS`` / ``NON_INCREASING_AXIS``: frequency axis content;
- ``CHANNEL_CONTRACT_MISMATCH``: S21/S12 channel, unknown or mismatched
  channel binding;
- ``DUPLICATE_CHANNEL``: repeated channel in one calibration set.

Migration audit (see docs/plans/2026-09-02-issue-027-osl-calibration.md
section 8): the reference closed-form solve (the model couples D and S, so it
is not jointly linear in the unknowns) is transcribed with an adapted
degeneracy guard - the reference denominator magnitude is compared with the
sum of its term magnitudes (scale-free normalized denominator) instead of the
reference absolute ``1e-15`` bound.  Quality metrics adopt the reference
per-capture repetition rms/max semantics.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import FrozenInstanceError, dataclass

import numpy as np

from uav_gpr.core.channels import ChannelSpec
from uav_gpr.core.enums import SParameter, StableStrEnum
from uav_gpr.core.errors import DomainError, ErrorCode
from uav_gpr.core.identifiers import CalibrationProfileId

__all__ = [
    "CORRECTION_SINGULAR_TOLERANCE",
    "SOLVE_DEGENERACY_TOLERANCE",
    "OslCalibrationProfile",
    "OslCalibrationQuality",
    "OslCalibrationSet",
    "OslStandard",
    "build_osl_calibration",
]

#: Normalized-denominator ratio below which standard measurements are
#: considered degenerate (no unique error model exists).
SOLVE_DEGENERACY_TOLERANCE = 1e-12

#: Relative guard for the correction denominator: |T + S*x| must exceed
#: ``CORRECTION_SINGULAR_TOLERANCE`` times (|T| + |S*x|) element-wise.
CORRECTION_SINGULAR_TOLERANCE = 1e-12

_REFLECTION_PARAMETERS = frozenset({SParameter.S11, SParameter.S22})


class OslStandard(StableStrEnum):
    """The three physical one-port calibration standards."""

    OPEN = "open"
    SHORT = "short"
    LOAD = "load"


# ---------------------------------------------------------------------------
# Canonical array helpers (defensive copies, read-only storage, fail-closed
# validation mapped to core DomainError codes).
# ---------------------------------------------------------------------------


def _readonly_copy(values: np.ndarray) -> np.ndarray:
    out = np.array(values, dtype=values.dtype, copy=True)
    out.setflags(write=False)
    return out


def _require_axis(values: object) -> np.ndarray:
    """Validate a frequency axis and return a read-only float64 copy."""
    arr = np.asarray(values)
    if (
        np.issubdtype(arr.dtype, np.bool_)
        or not np.issubdtype(arr.dtype, np.number)
        or np.issubdtype(arr.dtype, np.complexfloating)
    ):
        raise DomainError(
            ErrorCode.DTYPE_MISMATCH,
            "frequency axis must be a real numeric array",
            {"field": "frequency_hz"},
        )
    axis = np.asarray(arr, dtype=np.float64)
    if axis.ndim != 1 or axis.size == 0:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "frequency axis must be a non-empty one-dimensional array",
            {"field": "frequency_hz"},
        )
    if not np.all(np.isfinite(axis)):
        raise DomainError(
            ErrorCode.NON_FINITE_AXIS,
            "frequency axis contains a non-finite value",
            {"field": "frequency_hz"},
        )
    if np.any(np.diff(axis) <= 0.0):
        raise DomainError(
            ErrorCode.NON_INCREASING_AXIS,
            "frequency axis must be strictly increasing",
            {"field": "frequency_hz"},
        )
    out = np.array(axis, dtype=np.float64, copy=True)
    out.setflags(write=False)
    return out


def _require_complex_captures(
    values: object, name: str, frequency_count: int
) -> tuple[np.ndarray, int]:
    """Normalize ``(frequency,)`` or ``(capture, frequency)`` measurements.

    Returns a read-only ``(capture, frequency)`` complex128 copy and the
    capture count.  One-dimensional input counts as a single capture.
    """
    arr = np.asarray(values)
    if not np.issubdtype(arr.dtype, np.complexfloating):
        raise DomainError(
            ErrorCode.DTYPE_MISMATCH,
            "standard measurements must be a complex array",
            {"field": name},
        )
    if arr.ndim == 1:
        if arr.size != frequency_count:
            raise DomainError(
                ErrorCode.AXIS_MISMATCH,
                "standard measurement length does not match the frequency axis",
                {"field": name, "points": arr.size, "expected": frequency_count},
            )
        captures = arr.reshape(1, frequency_count).astype(np.complex128, copy=True)
    elif arr.ndim == 2:
        if arr.shape[1] != frequency_count:
            raise DomainError(
                ErrorCode.AXIS_MISMATCH,
                "standard measurement width does not match the frequency axis",
                {"field": name, "points": arr.shape[1], "expected": frequency_count},
            )
        if arr.shape[0] == 0:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "standard measurements need at least one capture",
                {"field": name},
            )
        captures = np.array(arr, dtype=np.complex128, copy=True)
    else:
        raise DomainError(
            ErrorCode.SHAPE_MISMATCH,
            "standard measurements must be (frequency,) or (capture, frequency)",
            {"field": name, "ndim": arr.ndim},
        )
    if not np.all(np.isfinite(captures)):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "standard measurements contain a non-finite value",
            {"field": name},
        )
    out = _readonly_copy(captures)
    return out, int(captures.shape[0])


def _require_complex_standard(values: object, name: str, count: int) -> np.ndarray:
    """Canonicalize a standard actual model (scalar or per-point complex)."""
    arr = np.asarray(values)
    if np.issubdtype(arr.dtype, np.bool_) or not np.issubdtype(
        arr.dtype, np.number
    ):
        raise DomainError(
            ErrorCode.DTYPE_MISMATCH,
            "standard actual model must be a scalar or numeric array",
            {"field": name},
        )
    if arr.ndim == 0:
        out = np.full(count, complex(arr.item()), dtype=np.complex128)
    elif arr.ndim == 1:
        if arr.size != count:
            raise DomainError(
                ErrorCode.AXIS_MISMATCH,
                "standard actual model length does not match the frequency axis",
                {"field": name, "points": arr.size, "expected": count},
            )
        out = np.array(arr, dtype=np.complex128, copy=True)
    else:
        raise DomainError(
            ErrorCode.SHAPE_MISMATCH,
            "standard actual model must be a scalar or one-dimensional array",
            {"field": name, "ndim": arr.ndim},
        )
    if not np.all(np.isfinite(out)):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "standard actual model contains a non-finite value",
            {"field": name},
        )
    return _readonly_copy(out)


def _require_complex_vector(values: object, name: str, count: int) -> np.ndarray:
    """Validate an internal per-frequency complex vector (read-only copy)."""
    arr = np.asarray(values)
    if not np.issubdtype(arr.dtype, np.complexfloating):
        raise DomainError(
            ErrorCode.DTYPE_MISMATCH,
            "expected a complex per-frequency array",
            {"field": name},
        )
    if arr.ndim != 1 or arr.size != count:
        raise DomainError(
            ErrorCode.AXIS_MISMATCH,
            "per-frequency array length does not match the frequency axis",
            {
                "field": name,
                "points": arr.size if arr.ndim == 1 else -1,
                "expected": count,
            },
        )
    if not np.all(np.isfinite(arr)):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "per-frequency array contains a non-finite value",
            {"field": name},
        )
    return _readonly_copy(arr)


def _require_capture_count(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "capture count must be a positive integer",
            {"field": name},
        )
    return value


# ---------------------------------------------------------------------------
# Core math: the audited reference closed form with a scale-free degeneracy
# guard, and the correction formula with a relative singularity guard.
# ---------------------------------------------------------------------------


def _solve_terms(
    open_mean: np.ndarray,
    short_mean: np.ndarray,
    load_mean: np.ndarray,
    open_actual: np.ndarray,
    short_actual: np.ndarray,
    load_actual: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Solve D / reflection_tracking / source_match per frequency point.

    The error model ``m = D + T*gamma/(1 - gamma*S)`` couples D and S, so it
    is not jointly linear in the unknowns; the audited reference closed form
    (rebar-inspector calibration/osl.py, standalone reference module) is the
    algebraic solution.  The degeneracy guard is scale-free: the reference
    denominator magnitude is compared with the sum of its own term
    magnitudes instead of the reference absolute ``1e-15`` bound.
    """
    open_a = open_actual
    short_a = short_actual
    load_a = load_actual
    open_m = open_mean
    short_m = short_mean
    load_m = load_mean

    denominator = (
        load_a * open_a * (open_m - load_m)
        + load_a * short_a * (load_m - short_m)
        + open_a * short_a * (short_m - open_m)
    )
    scale = (
        np.abs(load_a * open_a * (open_m - load_m))
        + np.abs(load_a * short_a * (load_m - short_m))
        + np.abs(open_a * short_a * (short_m - open_m))
    )
    normalized = np.divide(
        np.abs(denominator),
        scale,
        out=np.zeros_like(np.abs(denominator)),
        where=scale > 0.0,
    )
    if np.any(normalized <= SOLVE_DEGENERACY_TOLERANCE):
        first = int(np.argmax(normalized <= SOLVE_DEGENERACY_TOLERANCE))
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "OSL standard measurements are degenerate; no unique error model",
            {
                "kind": "degenerate_standards",
                "first_index": first,
                "normalized_denominator": float(normalized[first]),
            },
        )
    directivity = (
        load_a * open_m * (short_m * (open_a - short_a) + load_m * short_a)
        - load_a * open_a * load_m * short_m
        + open_a * load_m * short_a * (short_m - open_m)
    ) / denominator
    source_match = (
        load_a * (open_m - short_m)
        + open_a * (short_m - load_m)
        + short_a * (load_m - open_m)
    ) / denominator
    delta = (
        load_a * load_m * (open_m - short_m)
        + open_a * open_m * (short_m - load_m)
        + short_a * short_m * (load_m - open_m)
    ) / denominator
    reflection_tracking = directivity * source_match - delta
    return directivity, reflection_tracking, source_match


def _apply_terms(
    measured: np.ndarray,
    directivity: np.ndarray,
    reflection_tracking: np.ndarray,
    source_match: np.ndarray,
) -> np.ndarray:
    """Apply the correction formula along the last (frequency) axis.

    ``measured`` may be ``(frequency,)`` or ``(..., frequency)``; the error
    terms broadcast over leading dimensions.  Raises when the denominator is
    not significantly larger than its own term magnitudes (relative guard,
    covering exact 0/0 and amplification beyond ~1e12).
    """
    x = measured - directivity
    denominator = reflection_tracking + source_match * x
    scale = np.abs(reflection_tracking) + np.abs(source_match * x)
    bad = np.abs(denominator) <= CORRECTION_SINGULAR_TOLERANCE * scale
    if np.any(bad):
        first = int(np.argmax(bad))
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "OSL correction denominator is too small (numerically singular)",
            {"kind": "correction_singular", "first_index": first},
        )
    corrected = x / denominator
    out = np.array(corrected, dtype=np.complex128, copy=True)
    out.setflags(write=False)
    return out


def _capture_repetition_errors(
    captures: np.ndarray,
    directivity: np.ndarray,
    reflection_tracking: np.ndarray,
    source_match: np.ndarray,
    actual: np.ndarray,
) -> tuple[float, float]:
    """Correct every capture with the solved terms and compare with actual.

    Returns ``(rms, max)`` over all capture x frequency absolute errors -
    the reference per-capture repetition quality semantics.  The mean-fit
    residual is intentionally not reported: with three equations per
    frequency point the mean fit is exact to machine precision and carries no
    quality information.
    """
    corrected = _apply_terms(captures, directivity, reflection_tracking, source_match)
    errors = np.abs(corrected - actual)
    rms = float(np.sqrt(np.mean(errors**2)))
    maximum = float(np.max(errors))
    return rms, maximum


# ---------------------------------------------------------------------------
# Quality metrics (frozen, validated).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OslCalibrationQuality:
    """Per-standard capture repetition quality of a solved profile.

    For each standard the rms and the maximum absolute error are computed by
    correcting every captured measurement with the solved error terms and
    comparing with the standard's actual reflection coefficient.
    """

    open_rms_abs_error: float
    open_max_abs_error: float
    short_rms_abs_error: float
    short_max_abs_error: float
    load_rms_abs_error: float
    load_max_abs_error: float

    def __post_init__(self) -> None:
        for name, value in self._metrics():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "quality metric must be a number",
                    {"field": name},
                )
            if not np.isfinite(value) or value < 0.0:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "quality metric must be finite and non-negative",
                    {"field": name, "value": value},
                )

    def _metrics(self) -> tuple[tuple[str, float], ...]:
        return (
            ("open_rms_abs_error", self.open_rms_abs_error),
            ("open_max_abs_error", self.open_max_abs_error),
            ("short_rms_abs_error", self.short_rms_abs_error),
            ("short_max_abs_error", self.short_max_abs_error),
            ("load_rms_abs_error", self.load_rms_abs_error),
            ("load_max_abs_error", self.load_max_abs_error),
        )

    @property
    def worst_max_abs_error(self) -> float:
        """The largest per-standard max absolute error (headline metric)."""
        return max(
            self.open_max_abs_error,
            self.short_max_abs_error,
            self.load_max_abs_error,
        )


# ---------------------------------------------------------------------------
# Solved one-port OSL calibration profile (immutable model).
# ---------------------------------------------------------------------------


class OslCalibrationProfile:
    """Immutable solved one-port OSL calibration bound to one channel.

    Arrays are stored as write-protected defensive copies.  Every numeric
    property returns a fresh ``view()`` of its write-protected base: a view
    cannot be re-enabled for writing, so the stored data is unreachable for
    mutation.  Attribute assignment is rejected (``FrozenInstanceError``).
    All validation happens at construction.
    """

    _profile_id: CalibrationProfileId
    _channel: ChannelSpec
    _frequency_hz: np.ndarray
    _open_measured_mean: np.ndarray
    _short_measured_mean: np.ndarray
    _load_measured_mean: np.ndarray
    _open_actual: np.ndarray
    _short_actual: np.ndarray
    _load_actual: np.ndarray
    _directivity: np.ndarray
    _reflection_tracking: np.ndarray
    _source_match: np.ndarray
    _open_capture_count: int
    _short_capture_count: int
    _load_capture_count: int
    _quality: OslCalibrationQuality

    __slots__ = (
        "_channel",
        "_directivity",
        "_frequency_hz",
        "_load_actual",
        "_load_capture_count",
        "_load_measured_mean",
        "_open_actual",
        "_open_capture_count",
        "_open_measured_mean",
        "_profile_id",
        "_quality",
        "_reflection_tracking",
        "_short_actual",
        "_short_capture_count",
        "_short_measured_mean",
        "_source_match",
    )

    def __init__(
        self,
        *,
        profile_id: CalibrationProfileId,
        channel: ChannelSpec,
        frequency_hz: object,
        open_measured_mean: object,
        short_measured_mean: object,
        load_measured_mean: object,
        open_actual: object,
        short_actual: object,
        load_actual: object,
        directivity: object,
        reflection_tracking: object,
        source_match: object,
        open_capture_count: int,
        short_capture_count: int,
        load_capture_count: int,
        quality: OslCalibrationQuality,
    ) -> None:
        if not isinstance(profile_id, CalibrationProfileId):
            raise TypeError(
                "profile_id must be a CalibrationProfileId, "
                f"got {type(profile_id).__name__}"
            )
        if not isinstance(channel, ChannelSpec):
            raise TypeError(
                f"channel must be a ChannelSpec, got {type(channel).__name__}"
            )
        if channel.s_parameter not in _REFLECTION_PARAMETERS:
            raise DomainError(
                ErrorCode.CHANNEL_CONTRACT_MISMATCH,
                "OSL calibration covers only S11/S22 reflection channels",
                {
                    "channel_id": channel.channel_id,
                    "s_parameter": channel.s_parameter.value,
                },
            )
        axis = _require_axis(frequency_hz)
        count = int(axis.size)
        vector_fields = {
            "open_measured_mean": open_measured_mean,
            "short_measured_mean": short_measured_mean,
            "load_measured_mean": load_measured_mean,
            "open_actual": open_actual,
            "short_actual": short_actual,
            "load_actual": load_actual,
            "directivity": directivity,
            "reflection_tracking": reflection_tracking,
            "source_match": source_match,
        }
        canonical: dict[str, np.ndarray] = {}
        for name, value in vector_fields.items():
            canonical[name] = _require_complex_vector(value, name, count)
        for name, value in (
            ("open_capture_count", open_capture_count),
            ("short_capture_count", short_capture_count),
            ("load_capture_count", load_capture_count),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "capture count must be a positive integer",
                    {"field": name},
                )
        if not isinstance(quality, OslCalibrationQuality):
            raise TypeError(
                "quality must be an OslCalibrationQuality, "
                f"got {type(quality).__name__}"
            )
        object.__setattr__(self, "_profile_id", profile_id)
        object.__setattr__(self, "_channel", channel)
        object.__setattr__(self, "_frequency_hz", axis)
        for name, value in canonical.items():
            object.__setattr__(self, f"_{name}", value)
        object.__setattr__(self, "_open_capture_count", open_capture_count)
        object.__setattr__(self, "_short_capture_count", short_capture_count)
        object.__setattr__(self, "_load_capture_count", load_capture_count)
        object.__setattr__(self, "_quality", quality)

    def __setattr__(self, name: str, value: object) -> None:
        raise FrozenInstanceError(f"profile is immutable: cannot set {name!r}")

    # -- identity and binding ------------------------------------------------

    @property
    def profile_id(self) -> CalibrationProfileId:
        return self._profile_id

    @property
    def channel(self) -> ChannelSpec:
        return self._channel

    @property
    def s_parameter(self) -> SParameter:
        """The bound reflection parameter (S11 or S22)."""
        return self._channel.s_parameter

    # -- arrays (read-only views of write-protected bases) -------------------

    @property
    def frequency_hz(self) -> np.ndarray:
        return self._frequency_hz.view()

    @property
    def open_measured_mean(self) -> np.ndarray:
        return self._open_measured_mean.view()

    @property
    def short_measured_mean(self) -> np.ndarray:
        return self._short_measured_mean.view()

    @property
    def load_measured_mean(self) -> np.ndarray:
        return self._load_measured_mean.view()

    @property
    def open_actual(self) -> np.ndarray:
        return self._open_actual.view()

    @property
    def short_actual(self) -> np.ndarray:
        return self._short_actual.view()

    @property
    def load_actual(self) -> np.ndarray:
        return self._load_actual.view()

    @property
    def directivity(self) -> np.ndarray:
        return self._directivity.view()

    @property
    def reflection_tracking(self) -> np.ndarray:
        return self._reflection_tracking.view()

    @property
    def source_match(self) -> np.ndarray:
        return self._source_match.view()

    @property
    def open_capture_count(self) -> int:
        return self._open_capture_count

    @property
    def short_capture_count(self) -> int:
        return self._short_capture_count

    @property
    def load_capture_count(self) -> int:
        return self._load_capture_count

    @property
    def quality(self) -> OslCalibrationQuality:
        return self._quality

    @property
    def n_frequencies(self) -> int:
        return int(self._frequency_hz.size)

    # -- correction math -----------------------------------------------------

    def correct(self, measured: object) -> np.ndarray:
        """Correct raw reflection measurements with this profile.

        ``measured`` may be ``(frequency,)`` or ``(..., frequency)`` complex
        data sharing this profile's frequency axis exactly (no interpolation,
        no extrapolation).  Returns a read-only complex128 array; the input
        is never modified.
        """
        arr = np.asarray(measured)
        if not np.issubdtype(arr.dtype, np.complexfloating):
            raise DomainError(
                ErrorCode.DTYPE_MISMATCH,
                "measured data must be a complex array",
                {"field": "measured"},
            )
        if arr.ndim < 1:
            raise DomainError(
                ErrorCode.SHAPE_MISMATCH,
                "measured data must have a trailing frequency axis",
                {"field": "measured", "ndim": arr.ndim},
            )
        if arr.shape[-1] != self._frequency_hz.size:
            raise DomainError(
                ErrorCode.AXIS_MISMATCH,
                "measured data length does not match the profile frequency axis",
                {
                    "field": "measured",
                    "points": arr.shape[-1],
                    "expected": int(self._frequency_hz.size),
                },
            )
        if not np.all(np.isfinite(arr)):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "measured data contains a non-finite value",
                {"field": "measured"},
            )
        return _apply_terms(
            arr, self._directivity, self._reflection_tracking, self._source_match
        )

    # -- equality and representation -----------------------------------------

    def __eq__(self, other: object) -> bool:
        if type(self) is not type(other):
            return NotImplemented
        assert isinstance(other, OslCalibrationProfile)
        return (
            self._profile_id == other._profile_id
            and self._channel == other._channel
            and self._open_capture_count == other._open_capture_count
            and self._short_capture_count == other._short_capture_count
            and self._load_capture_count == other._load_capture_count
            and self._quality == other._quality
            and np.array_equal(self._frequency_hz, other._frequency_hz)
            and np.array_equal(self._open_measured_mean, other._open_measured_mean)
            and np.array_equal(self._short_measured_mean, other._short_measured_mean)
            and np.array_equal(self._load_measured_mean, other._load_measured_mean)
            and np.array_equal(self._open_actual, other._open_actual)
            and np.array_equal(self._short_actual, other._short_actual)
            and np.array_equal(self._load_actual, other._load_actual)
            and np.array_equal(self._directivity, other._directivity)
            and np.array_equal(self._reflection_tracking, other._reflection_tracking)
            and np.array_equal(self._source_match, other._source_match)
        )

    def __repr__(self) -> str:
        return (
            "OslCalibrationProfile("
            f"profile_id={self._profile_id!s}, "
            f"channel={self._channel.channel_id!r}, "
            f"s_parameter={self._channel.s_parameter.value}, "
            f"frequencies={self.n_frequencies})"
        )


# ---------------------------------------------------------------------------
# Builder: from Open/Short/Load captures to a solved profile.
# ---------------------------------------------------------------------------


def build_osl_calibration(
    *,
    channel: ChannelSpec,
    frequency_hz: object,
    open_measured: object,
    short_measured: object,
    load_measured: object,
    open_actual: object = 1.0 + 0.0j,
    short_actual: object = -1.0 + 0.0j,
    load_actual: object = 0.0 + 0.0j,
    profile_id: CalibrationProfileId | None = None,
) -> OslCalibrationProfile:
    """Solve a one-port OSL calibration from three standard measurements.

    ``open_measured``/``short_measured``/``load_measured`` accept
    ``(frequency,)`` or ``(capture, frequency)`` complex arrays; multiple
    captures are coherently averaged before the solve and every capture
    contributes to the repetition quality metrics.  ``*_actual`` accept ideal
    scalars (default) or per-frequency complex Cal-Kit models.  The bound
    ``channel`` must be an S11 or S22 reflection channel.
    """
    if not isinstance(channel, ChannelSpec):
        raise TypeError(f"channel must be a ChannelSpec, got {type(channel).__name__}")
    if channel.s_parameter not in _REFLECTION_PARAMETERS:
        raise DomainError(
            ErrorCode.CHANNEL_CONTRACT_MISMATCH,
            "OSL calibration covers only S11/S22 reflection channels",
            {
                "channel_id": channel.channel_id,
                "s_parameter": channel.s_parameter.value,
            },
        )
    axis = _require_axis(frequency_hz)
    count = int(axis.size)
    open_captures, open_count = _require_complex_captures(
        open_measured, "open_measured", count
    )
    short_captures, short_count = _require_complex_captures(
        short_measured, "short_measured", count
    )
    load_captures, load_count = _require_complex_captures(
        load_measured, "load_measured", count
    )
    open_mean = _readonly_copy(np.mean(open_captures, axis=0))
    short_mean = _readonly_copy(np.mean(short_captures, axis=0))
    load_mean = _readonly_copy(np.mean(load_captures, axis=0))
    open_actual_arr = _require_complex_standard(open_actual, "open_actual", count)
    short_actual_arr = _require_complex_standard(short_actual, "short_actual", count)
    load_actual_arr = _require_complex_standard(load_actual, "load_actual", count)

    directivity, tracking, source_match = _solve_terms(
        open_mean,
        short_mean,
        load_mean,
        open_actual_arr,
        short_actual_arr,
        load_actual_arr,
    )
    directivity = _readonly_copy(directivity)
    tracking = _readonly_copy(tracking)
    source_match = _readonly_copy(source_match)

    open_rms, open_max = _capture_repetition_errors(
        open_captures, directivity, tracking, source_match, open_actual_arr
    )
    short_rms, short_max = _capture_repetition_errors(
        short_captures, directivity, tracking, source_match, short_actual_arr
    )
    load_rms, load_max = _capture_repetition_errors(
        load_captures, directivity, tracking, source_match, load_actual_arr
    )
    quality = OslCalibrationQuality(
        open_rms_abs_error=open_rms,
        open_max_abs_error=open_max,
        short_rms_abs_error=short_rms,
        short_max_abs_error=short_max,
        load_rms_abs_error=load_rms,
        load_max_abs_error=load_max,
    )
    return OslCalibrationProfile(
        profile_id=profile_id if profile_id is not None else CalibrationProfileId.new(),
        channel=channel,
        frequency_hz=axis,
        open_measured_mean=open_mean,
        short_measured_mean=short_mean,
        load_measured_mean=load_mean,
        open_actual=open_actual_arr,
        short_actual=short_actual_arr,
        load_actual=load_actual_arr,
        directivity=directivity,
        reflection_tracking=tracking,
        source_match=source_match,
        open_capture_count=open_count,
        short_capture_count=short_count,
        load_capture_count=load_count,
        quality=quality,
    )


# ---------------------------------------------------------------------------
# Ordered multi-channel container (position = channel order).
# ---------------------------------------------------------------------------


class OslCalibrationSet:
    """Ordered binding of solved profiles to their channels.

    The tuple position is the channel order (never dict/iteration order).
    All profiles must share one frequency axis; duplicate channels are
    rejected.  ``apply`` corrects a ``(channel, frequency)`` complex array
    whose channel order is asserted against the container's order.  The
    container is immutable (``FrozenInstanceError`` on attribute assignment).
    """

    _profiles: tuple[OslCalibrationProfile, ...]

    __slots__ = ("_profiles",)

    def __init__(self, profiles: Sequence[OslCalibrationProfile]) -> None:
        profile_tuple = tuple(profiles)
        if len(profile_tuple) == 0:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "calibration set must contain at least one profile",
                {"field": "profiles"},
            )
        for index, profile in enumerate(profile_tuple):
            if not isinstance(profile, OslCalibrationProfile):
                raise TypeError(
                    "profiles must contain OslCalibrationProfile entries, "
                    f"got {type(profile).__name__} at index {index}"
                )
        seen: dict[str, int] = {}
        for index, profile in enumerate(profile_tuple):
            channel_id = profile.channel.channel_id
            if channel_id in seen:
                raise DomainError(
                    ErrorCode.DUPLICATE_CHANNEL,
                    "calibration set binds the same channel more than once",
                    {
                        "channel_id": channel_id,
                        "first": seen[channel_id],
                        "duplicate": index,
                    },
                )
            seen[channel_id] = index
        first_axis = profile_tuple[0].frequency_hz
        for index, profile in enumerate(profile_tuple):
            if not np.array_equal(profile.frequency_hz, first_axis):
                raise DomainError(
                    ErrorCode.AXIS_MISMATCH,
                    "all calibration profiles must share one frequency axis",
                    {"profile_index": index},
                )
        object.__setattr__(self, "_profiles", profile_tuple)

    def __setattr__(self, name: str, value: object) -> None:
        raise FrozenInstanceError(
            f"calibration set is immutable: cannot set {name!r}"
        )

    @property
    def profiles(self) -> tuple[OslCalibrationProfile, ...]:
        return self._profiles

    @property
    def channels(self) -> tuple[ChannelSpec, ...]:
        """The ordered channel binding (position = channel order)."""
        return tuple(profile.channel for profile in self._profiles)

    def profile_for(self, channel: ChannelSpec) -> OslCalibrationProfile:
        """Return the profile exactly bound to ``channel`` (full equality)."""
        for profile in self._profiles:
            if profile.channel == channel:
                return profile
        raise DomainError(
            ErrorCode.CHANNEL_CONTRACT_MISMATCH,
            "no calibration profile is bound to this channel",
            {
                "channel_id": channel.channel_id,
                "s_parameter": channel.s_parameter.value,
            },
        )

    def apply(self, measured: object, channels: Sequence[ChannelSpec]) -> np.ndarray:
        """Correct one ``(channel, frequency)`` complex array.

        The ``channels`` tuple must equal the container's ordered channels
        exactly (same entries in the same order); the measured row order is
        then corrected profile-by-profile.  Returns a read-only complex128
        array; the input is never modified.
        """
        arr = np.asarray(measured)
        if not np.issubdtype(arr.dtype, np.complexfloating):
            raise DomainError(
                ErrorCode.DTYPE_MISMATCH,
                "measured data must be a complex array",
                {"field": "measured"},
            )
        if arr.ndim != 2:
            raise DomainError(
                ErrorCode.SHAPE_MISMATCH,
                "measured data must be channel x frequency",
                {"field": "measured", "ndim": arr.ndim},
            )
        profile_count = len(self._profiles)
        if arr.shape[0] != profile_count:
            raise DomainError(
                ErrorCode.SHAPE_MISMATCH,
                "measured row count does not match the calibration set",
                {"rows": arr.shape[0], "expected": profile_count},
            )
        axis_count = self._profiles[0].n_frequencies
        if arr.shape[1] != axis_count:
            raise DomainError(
                ErrorCode.AXIS_MISMATCH,
                "measured data length does not match the shared frequency axis",
                {"points": arr.shape[1], "expected": axis_count},
            )
        if not np.all(np.isfinite(arr)):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "measured data contains a non-finite value",
                {"field": "measured"},
            )
        channel_tuple = tuple(channels)
        if channel_tuple != self.channels:
            raise DomainError(
                ErrorCode.CHANNEL_CONTRACT_MISMATCH,
                "measured channel order does not match the calibration set",
                {"field": "channels"},
            )
        corrected = np.stack(
            [
                profile.correct(row)
                for profile, row in zip(self._profiles, arr, strict=True)
            ],
            axis=0,
        )
        out = np.array(corrected, dtype=np.complex128, copy=True)
        out.setflags(write=False)
        return out

    def __eq__(self, other: object) -> bool:
        if type(self) is not type(other):
            return NotImplemented
        assert isinstance(other, OslCalibrationSet)
        return self._profiles == other._profiles

    def __repr__(self) -> str:
        return (
            "OslCalibrationSet("
            + ", ".join(profile.channel.channel_id for profile in self._profiles)
            + ")"
        )
