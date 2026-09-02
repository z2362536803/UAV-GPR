"""Sweep midpoint GNSS matcher: nearest fix in one monotonic domain (ISSUE-026).

Contract summary (docs/GNSS.md §5, docs/issues/M05_GNSS.md ISSUE-026,
docs/DATA_MODEL.md §6/§7, docs/reports/ISSUE_026_BASELINE_CONFIRMATION.md,
captain rulings D1-D8 in docs/plans/2026-09-02-issue-026-gnss-matcher.md):

- ``GnssTraceMatcher`` is a **pure** matcher: no clock, no threads, no serial,
  no I/O.  It consumes an immutable ``GnssFixCache.snapshot()`` tuple (D8) and
  never mutates its inputs; the thread-safety boundary stays with the caller's
  snapshot.

- Midpoint (D6, expression-for-expression identical to the acquisition
  backends so results attach to ``TraceMetadata`` without a
  ``gnss_midpoint_mismatch`` conflict)::

      midpoint_ns  = (started_ns + finished_ns) // 2
      midpoint_utc = started_utc + (finished_utc - started_utc) / 2

- Nearest fix over the **whole** snapshot (valid and invalid fixes alike, D2)
  with all distances compared as integer nanoseconds; an equidistant tie is
  broken in favour of the earlier fix (D4), then by snapshot order.

- Signed match difference (D1)::

      signed = fix.received_monotonic_ns.ns - midpoint_ns
      # signed > 0: fix after the sweep midpoint
      # signed < 0: fix before the sweep midpoint
      GnssMatch.age_s = abs(signed) / 1_000_000_000   # non-negative, core

  Both ``midpoint_ns`` (persisted as ``TraceMetadata.sweep_midpoint_monotonic_ns``
  when computed with the same formula) and ``fix.received_monotonic_ns`` are
  persisted per trace, so the signed difference is reconstructible from stored
  fields; ``age_s`` itself stays the clearly-defined absolute age required by
  the frozen core contract (``GnssMatch`` rejects negative ages).

- Reasons, in fixed precedence (D2/D3/D5)::

      1. shared_monotonic_domain is False -> clock_unavailable (no fix)
      2. empty snapshot                  -> no_fix (no fix)
      3. |diff| > window                 -> out_of_range (fix kept as evidence)
      4. fix invalid                     -> invalid (fix kept as evidence)
      5. |diff| > stale_after            -> stale (fix kept as evidence)
      6. otherwise                       -> usable for the map (reason None)

  ``stale_after_s`` (wired from ``MissionConfig.gnss_max_age_s`` by the
  application layer) and ``window_s`` are required constructor arguments with
  no silent defaults; ``window_s >= stale_after_s`` is enforced because the
  stale band must be reachable to distinguish stale from out_of_range (D3).
  Both thresholds are converted to integer nanoseconds by truncation
  (``int(seconds * 1_000_000_000)`` — floor for positive values), so every
  boundary comparison runs in exact integer nanoseconds.
  Without a shared monotonic domain the matcher refuses to match and never
  falls back to comparing UTC values (D5): UTC is carried for audit only.
"""

from __future__ import annotations

import math
from datetime import datetime

from uav_gpr.core.enums import GnssMatchMethod, GnssUnavailableReason
from uav_gpr.core.gnss import GnssFix, GnssMatch
from uav_gpr.core.timeutil import MonotonicNs, ensure_utc

__all__ = ["GnssTraceMatcher"]

_NS_PER_S = 1_000_000_000


def _require_positive_finite(value: float, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, float):
        raise TypeError(f"{field} must be a float, got {type(value).__name__}")
    if not (value > 0.0 and math.isfinite(value)):
        raise ValueError(f"{field} must be a positive finite float, got {value!r}")
    return value


class GnssTraceMatcher:
    """Match one sweep to the nearest cached fix at its midpoint (pure).

    The matcher owns only its configuration: ``stale_after_s`` and
    ``window_s`` are validated once at construction (D3) and ``match`` is a
    pure function of its arguments.  See the module docstring for the full
    contract, the midpoint formulas (D6), the signed-difference convention
    (D1) and the reason precedence (D2/D3/D5).
    """

    def __init__(self, *, stale_after_s: float, window_s: float) -> None:
        self._stale_after_s = _require_positive_finite(stale_after_s, "stale_after_s")
        self._window_s = _require_positive_finite(window_s, "window_s")
        if window_s < stale_after_s:
            raise ValueError(
                "window_s must be >= stale_after_s so the stale band stays "
                f"distinguishable from out_of_range (got window_s={window_s!r}, "
                f"stale_after_s={stale_after_s!r})"
            )
        # Integer-nanosecond thresholds: boundary comparisons stay exact
        # (same convention as the reader's stale clock, reader.py).
        self._stale_after_ns = int(stale_after_s * _NS_PER_S)
        self._window_ns = int(window_s * _NS_PER_S)

    @property
    def stale_after_s(self) -> float:
        """Configured staleness threshold in seconds (wired from config)."""
        return self._stale_after_s

    @property
    def window_s(self) -> float:
        """Configured reasonable past/future window in seconds."""
        return self._window_s

    def match(
        self,
        *,
        started_utc: datetime,
        finished_utc: datetime,
        started_monotonic_ns: MonotonicNs,
        finished_monotonic_ns: MonotonicNs,
        fixes: tuple[GnssFix, ...],
        shared_monotonic_domain: bool,
    ) -> GnssMatch:
        """Match one sweep to the nearest fix at its midpoint (no side effects).

        ``fixes`` is the immutable ``GnssFixCache.snapshot()`` tuple (D8) and
        is never mutated.  The returned ``GnssMatch`` is frozen; when a fix is
        selected its ``age_s`` is the absolute ``|fix - midpoint|`` in seconds
        and the signed difference follows the module-docstring convention (D1).
        """
        if not isinstance(started_monotonic_ns, MonotonicNs):
            raise TypeError(
                "started_monotonic_ns must be a MonotonicNs, "
                f"got {type(started_monotonic_ns).__name__}"
            )
        if not isinstance(finished_monotonic_ns, MonotonicNs):
            raise TypeError(
                "finished_monotonic_ns must be a MonotonicNs, "
                f"got {type(finished_monotonic_ns).__name__}"
            )
        if not isinstance(fixes, tuple):
            raise TypeError(
                "fixes must be the immutable GnssFixCache.snapshot() tuple, "
                f"got {type(fixes).__name__}"
            )
        for fix in fixes:
            if not isinstance(fix, GnssFix):
                raise TypeError(
                    f"every fix must be a GnssFix, got {type(fix).__name__}"
                )
        if not isinstance(shared_monotonic_domain, bool):
            raise TypeError(
                "shared_monotonic_domain must be a bool, "
                f"got {type(shared_monotonic_domain).__name__}"
            )
        start_utc = ensure_utc(started_utc)
        finish_utc = ensure_utc(finished_utc)
        if start_utc > finish_utc:
            raise ValueError("sweep UTC times must be ordered start <= finish")
        if started_monotonic_ns.ns > finished_monotonic_ns.ns:
            raise ValueError("sweep monotonic times must be ordered start <= finish")

        # D6: expression-for-expression identical to the acquisition backends.
        midpoint_utc = start_utc + (finish_utc - start_utc) / 2
        midpoint_ns = (started_monotonic_ns.ns + finished_monotonic_ns.ns) // 2

        if not shared_monotonic_domain:
            # D5: refuse to match without a common monotonic domain; UTC is
            # audit-only and must never fake a common time base.
            return self._unmatched(
                midpoint_utc, GnssUnavailableReason.CLOCK_UNAVAILABLE
            )
        if not fixes:
            return self._unmatched(midpoint_utc, GnssUnavailableReason.NO_FIX)

        nearest = self._nearest_fix(fixes, midpoint_ns)
        assert nearest is not None  # snapshot is non-empty here
        distance_ns = abs(nearest.received_monotonic_ns.ns - midpoint_ns)
        age_s = distance_ns / _NS_PER_S
        reason = self._reason_for(nearest, distance_ns)
        return GnssMatch(
            fix=nearest,
            trace_midpoint_utc=midpoint_utc,
            age_s=age_s,
            method=GnssMatchMethod.NEAREST_MIDPOINT,
            usable_for_map=reason is None,
            reason=reason,
        )

    def _reason_for(
        self, fix: GnssFix, distance_ns: int
    ) -> GnssUnavailableReason | None:
        """Reason precedence: window, validity, staleness, then usable (D2/D3)."""
        if distance_ns > self._window_ns:
            return GnssUnavailableReason.OUT_OF_RANGE
        if not fix.valid:
            return GnssUnavailableReason.INVALID
        if distance_ns > self._stale_after_ns:
            return GnssUnavailableReason.STALE
        return None

    def _nearest_fix(
        self, fixes: tuple[GnssFix, ...], midpoint_ns: int
    ) -> GnssFix | None:
        """Nearest fix over the whole snapshot; ties -> earlier fix (D2/D4)."""
        best: GnssFix | None = None
        best_key: tuple[int, int] | None = None
        for fix in fixes:
            distance_ns = abs(fix.received_monotonic_ns.ns - midpoint_ns)
            key = (distance_ns, fix.received_monotonic_ns.ns)
            # Strict '<' keeps the earlier fix on equidistant ties and the
            # first snapshot entry when both distance and timestamp coincide.
            if best_key is None or key < best_key:
                best = fix
                best_key = key
        return best

    def _unmatched(
        self, midpoint_utc: datetime, reason: GnssUnavailableReason
    ) -> GnssMatch:
        """Build the frozen fix-less match (core forbids age/STALE here)."""
        return GnssMatch(
            fix=None,
            trace_midpoint_utc=midpoint_utc,
            age_s=None,
            method=GnssMatchMethod.NEAREST_MIDPOINT,
            usable_for_map=False,
            reason=reason,
        )
