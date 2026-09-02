"""Contract tests for the ISSUE-026 sweep midpoint GNSS matcher.

The matcher is a pure function of injected values: no clock, no threads, no
serial, no sleeps and no randomness.  Every boundary is exercised with exact
integer nanoseconds so the red/green evidence is deterministic.

Contract summary (docs/issues/M05_GNSS.md ISSUE-026, docs/GNSS.md §5,
docs/reports/ISSUE_026_BASELINE_CONFIRMATION.md, captain rulings D1-D8 in
docs/plans/2026-09-02-issue-026-gnss-matcher.md):

- midpoint formula matches acquisition expression-for-expression (D6): ns
  floor division and datetime half-to-even rounding, attachable to
  ``TraceMetadata`` without a midpoint conflict;
- nearest fix over the whole snapshot; equidistant tie-break picks the
  earlier fix on integer nanoseconds (D4);
- ``GnssMatch.age_s`` is the non-negative absolute age; the signed match
  difference is ``fix.received_monotonic_ns.ns - midpoint_ns`` (D1);
- ``stale_after_s``/``window_s`` are required, positive, finite and
  ``window_s >= stale_after_s`` (D3); boundaries are inclusive on the
  fresh/in-window side;
- reason precedence: clock_unavailable > no_fix > out_of_range > invalid
  > stale > usable (D2/D3/D5);
- an explicit ``shared_monotonic_domain=False`` never falls back to UTC
  matching (D5, acceptance 3).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest

from uav_gpr.core.enums import (
    GnssFixQuality,
    GnssMatchMethod,
    GnssStatus,
    GnssUnavailableReason,
    TraceQualityReason,
    TraceQualityStatus,
)
from uav_gpr.core.errors import DomainError, ErrorCode
from uav_gpr.core.gnss import GnssFix, GnssMatch
from uav_gpr.core.identifiers import DeviceId, MissionId, TraceUid
from uav_gpr.core.metadata import TraceMetadata
from uav_gpr.core.timeutil import MonotonicNs
from uav_gpr.positioning.matcher import GnssTraceMatcher

MISSION = MissionId("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
TRACE_UID = TraceUid("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
DEVICE = DeviceId("cccccccc-cccc-4ccc-8ccc-cccccccccccc")

# Sweep window: start 12:00:00.000, finish 12:00:00.400 (400 ms sweep).
START_UTC = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)
SWEEP_NS = 400_000_000
FINISH_UTC = START_UTC + timedelta(microseconds=SWEEP_NS // 1000)
START_NS = 1_000_000_000_000
FINISH_NS = START_NS + SWEEP_NS
MID_NS = (START_NS + FINISH_NS) // 2  # 1_000_000_200_000
MID_UTC = START_UTC + (FINISH_UTC - START_UTC) / 2

_STALE_S = 0.25
_WINDOW_S = 1.0
_STALE_NS = int(_STALE_S * 1_000_000_000)
_WINDOW_NS = int(_WINDOW_S * 1_000_000_000)


def _matcher(
    *, stale_after_s: float = _STALE_S, window_s: float = _WINDOW_S
) -> GnssTraceMatcher:
    return GnssTraceMatcher(stale_after_s=stale_after_s, window_s=window_s)


def _fix(
    *,
    mono_ns: int,
    utc: datetime | None = None,
    valid: bool = True,
) -> GnssFix:
    """Build a synthetic fix at the given monotonic instant."""
    if utc is None:
        utc = MID_UTC + timedelta(microseconds=(mono_ns - MID_NS) // 1000)
    if valid:
        return GnssFix(
            received_utc=utc,
            nmea_utc=None,
            received_monotonic_ns=MonotonicNs(mono_ns),
            latitude_deg=30.5,
            longitude_deg=120.1,
            altitude_msl_m=12.0,
            geoid_separation_m=None,
            fix_quality=GnssFixQuality.GPS_FIX,
            satellites=10,
            hdop=1.0,
            ground_speed_mps=None,
            course_deg=None,
            valid=True,
            invalid_reason=None,
        )
    return GnssFix(
        received_utc=utc,
        nmea_utc=None,
        received_monotonic_ns=MonotonicNs(mono_ns),
        latitude_deg=None,
        longitude_deg=None,
        altitude_msl_m=None,
        geoid_separation_m=None,
        fix_quality=GnssFixQuality.INVALID,
        satellites=None,
        hdop=None,
        ground_speed_mps=None,
        course_deg=None,
        valid=False,
        invalid_reason=GnssUnavailableReason.NO_FIX,
    )


def _times() -> dict[str, object]:
    return {
        "started_utc": START_UTC,
        "finished_utc": FINISH_UTC,
        "started_monotonic_ns": MonotonicNs(START_NS),
        "finished_monotonic_ns": MonotonicNs(FINISH_NS),
        "shared_monotonic_domain": True,
    }


def _match(fixes: tuple[GnssFix, ...], **overrides: object) -> GnssMatch:
    args = _times()
    args.update(overrides)
    matcher = _matcher()
    return matcher.match(fixes=fixes, **args)  # type: ignore[arg-type]


def _metadata_with_match(match: GnssMatch) -> TraceMetadata:
    """Attach a match to a first-trace metadata (core midpoint guard active)."""
    metadata = TraceMetadata(
        mission_id=MISSION,
        trace_index=0,
        trace_uid=TRACE_UID,
        device_id=DEVICE,
        sweep_started_utc=START_UTC,
        sweep_midpoint_utc=MID_UTC,
        sweep_finished_utc=FINISH_UTC,
        sweep_started_monotonic_ns=MonotonicNs(START_NS),
        sweep_midpoint_monotonic_ns=MonotonicNs(MID_NS),
        sweep_finished_monotonic_ns=MonotonicNs(FINISH_NS),
        target_interval_s=1.0,
        actual_interval_s=None,
        schedule_error_s=None,
        connection_generation=0,
        raw_trace_sha256=None,
        gnss_match=None,
        quality_status=TraceQualityStatus.DEGRADED,
        quality_reasons=(TraceQualityReason.GNSS_MISSING,),
    )
    return metadata.with_gnss_match(match)


# ---------------------------------------------------------------------------
# D6: midpoint formula (identical to acquisition, attachable to metadata)
# ---------------------------------------------------------------------------


def test_fix_at_computed_midpoint_matches_with_zero_age() -> None:
    fix = _fix(mono_ns=MID_NS)
    match = _match((fix,))
    assert match.fix is fix
    assert match.age_s == 0.0
    assert match.usable_for_map is True
    assert match.reason is None
    assert match.trace_midpoint_utc == MID_UTC


def test_midpoint_formulas_follow_the_acquisition_expressions() -> None:
    # Odd sweep duration: the ns midpoint floors and the datetime division
    # rounds half-to-even at microsecond resolution -- the matcher must use
    # the exact acquisition expressions so results stay attachable.
    finish_utc = START_UTC + timedelta(microseconds=300001)
    finish_ns = START_NS + 300_001_001
    fix = _fix(mono_ns=(START_NS + finish_ns) // 2)
    match = _match(
        (fix,),
        started_utc=START_UTC,
        finished_utc=finish_utc,
        started_monotonic_ns=MonotonicNs(START_NS),
        finished_monotonic_ns=MonotonicNs(finish_ns),
    )
    assert match.trace_midpoint_utc == START_UTC + (finish_utc - START_UTC) / 2
    assert match.usable_for_map is True  # fix sits exactly on the ns midpoint


def test_match_midpoint_attaches_to_trace_metadata_without_conflict() -> None:
    stale_match = _match((_fix(mono_ns=MID_NS + _STALE_NS + 1),))
    attached = _metadata_with_match(stale_match)
    assert attached.gnss_match is stale_match  # no GNSS_MIDPOINT_MISMATCH
    assert attached.quality_status is TraceQualityStatus.DEGRADED
    assert TraceQualityReason.GNSS_STALE in attached.quality_reasons

    usable_match = _match((_fix(mono_ns=MID_NS),))
    usable_metadata = _metadata_with_match(usable_match)
    assert usable_metadata.quality_status is TraceQualityStatus.NOMINAL
    assert usable_metadata.quality_reasons == ()


# ---------------------------------------------------------------------------
# Nearest selection and the D1 signed-difference convention
# ---------------------------------------------------------------------------


def test_selects_nearest_fix_after_midpoint_signed_difference_positive() -> None:
    before = _fix(mono_ns=MID_NS - 10_000_000_000)
    after = _fix(mono_ns=MID_NS + 5_000_000_000)
    match = _match((before, after))
    assert match.fix is after
    assert match.age_s == pytest.approx(5.0)
    # D1 sign convention: signed = fix.ns - midpoint_ns (positive = after).
    assert match.fix is not None
    signed = match.fix.received_monotonic_ns.ns - MID_NS
    assert signed == 5_000_000_000
    assert signed > 0


def test_selects_nearest_fix_before_midpoint_signed_difference_negative() -> None:
    before = _fix(mono_ns=MID_NS - 2_000_000_000)
    after = _fix(mono_ns=MID_NS + 7_000_000_000)
    match = _match((before, after))
    assert match.fix is before
    assert match.age_s == pytest.approx(2.0)
    assert match.fix is not None
    signed = match.fix.received_monotonic_ns.ns - MID_NS
    assert signed == -2_000_000_000
    assert signed < 0


def test_age_s_is_absolute_and_non_negative_on_both_sides() -> None:
    for delta in (-250_000_000, 250_000_000):
        match = _match((_fix(mono_ns=MID_NS + delta),))
        assert match.age_s is not None
        assert match.age_s >= 0.0
        assert match.age_s == pytest.approx(0.25)


# ---------------------------------------------------------------------------
# D4: equidistant tie-break (earlier fix, integer nanoseconds)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("delta_ns", [2_000_000_000, 7_500_000_003])
def test_equidistant_tie_prefers_the_earlier_fix(delta_ns: int) -> None:
    earlier = _fix(mono_ns=MID_NS - delta_ns)
    later = _fix(mono_ns=MID_NS + delta_ns)
    for order in ((earlier, later), (later, earlier)):
        match = _match(order)
        assert match.fix is earlier
        assert match.age_s == pytest.approx(delta_ns / 1_000_000_000)


def test_same_timestamp_ties_resolve_by_snapshot_order() -> None:
    first = _fix(mono_ns=MID_NS - 3_000_000_000)
    second = _fix(mono_ns=MID_NS - 3_000_000_000)
    assert _match((first, second)).fix is first
    assert _match((second, first)).fix is second


# ---------------------------------------------------------------------------
# D3: stale threshold and window boundaries (inclusive fresh/in-window side)
# ---------------------------------------------------------------------------


def test_exactly_at_stale_threshold_is_usable() -> None:
    match = _match((_fix(mono_ns=MID_NS + _STALE_NS),))
    assert match.usable_for_map is True
    assert match.reason is None


def test_one_ns_beyond_stale_threshold_is_stale_with_fix_retained() -> None:
    match = _match((_fix(mono_ns=MID_NS + _STALE_NS + 1),))
    assert match.usable_for_map is False
    assert match.reason is GnssUnavailableReason.STALE
    assert match.fix is not None  # fix/history kept, position not usable
    assert match.age_s == pytest.approx((_STALE_NS + 1) / 1_000_000_000)


def test_before_side_stale_boundary_is_symmetric() -> None:
    usable = _match((_fix(mono_ns=MID_NS - _STALE_NS),))
    assert usable.usable_for_map is True
    stale = _match((_fix(mono_ns=MID_NS - _STALE_NS - 1),))
    assert stale.reason is GnssUnavailableReason.STALE
    assert stale.fix is not None


def test_exactly_at_window_boundary_is_stale_still_in_window() -> None:
    match = _match((_fix(mono_ns=MID_NS + _WINDOW_NS),))
    assert match.reason is GnssUnavailableReason.STALE
    assert match.fix is not None


def test_one_ns_beyond_window_is_out_of_range_with_fix_evidence() -> None:
    match = _match((_fix(mono_ns=MID_NS + _WINDOW_NS + 1),))
    assert match.reason is GnssUnavailableReason.OUT_OF_RANGE
    assert match.fix is not None
    assert match.usable_for_map is False
    assert match.age_s == pytest.approx((_WINDOW_NS + 1) / 1_000_000_000)


def test_degenerate_equal_window_and_threshold_has_empty_stale_band() -> None:
    matcher = GnssTraceMatcher(stale_after_s=0.25, window_s=0.25)
    ok = matcher.match(fixes=(_fix(mono_ns=MID_NS),), **_times())  # type: ignore[arg-type]
    assert ok.usable_for_map is True
    far = matcher.match(
        fixes=(_fix(mono_ns=MID_NS + _STALE_NS + 1),), **_times()  # type: ignore[arg-type]
    )
    # Beyond the (equal) window: out_of_range -- the stale band is unreachable.
    assert far.reason is GnssUnavailableReason.OUT_OF_RANGE


# ---------------------------------------------------------------------------
# D2: invalid nearest fix (nearest-over-all, honest reason)
# ---------------------------------------------------------------------------


def test_invalid_nearest_fix_is_selected_and_reported_invalid() -> None:
    invalid = _fix(mono_ns=MID_NS + 1_000_000, valid=False)
    valid = _fix(mono_ns=MID_NS + 5_000_000_000)
    match = _match((invalid, valid))
    assert match.fix is invalid  # farther valid fix must not silently win
    assert match.reason is GnssUnavailableReason.INVALID
    assert match.usable_for_map is False


def test_invalid_fix_beyond_window_reports_out_of_range() -> None:
    invalid = _fix(mono_ns=MID_NS + _WINDOW_NS + 1, valid=False)
    match = _match((invalid,))
    assert match.reason is GnssUnavailableReason.OUT_OF_RANGE
    assert match.fix is invalid


def test_nearest_is_chosen_over_all_fixes_even_when_invalid() -> None:
    valid = _fix(mono_ns=MID_NS - 2_000_000_000)
    invalid = _fix(mono_ns=MID_NS + 1_000_000, valid=False)
    match = _match((valid, invalid))
    assert match.fix is invalid  # nearest-over-all, even when invalid
    assert match.reason is GnssUnavailableReason.INVALID


# ---------------------------------------------------------------------------
# D5: explicit monotonic domain declaration, no UTC fallback
# ---------------------------------------------------------------------------


def test_unshared_domain_reports_clock_unavailable_without_utc_fallback() -> None:
    # The fix sits exactly on the midpoint in UTC; without a shared monotonic
    # domain it must NOT be matched (acceptance 3: no fake common time base).
    fix = _fix(mono_ns=MID_NS, utc=MID_UTC)
    match = _match((fix,), shared_monotonic_domain=False)
    assert match.fix is None
    assert match.age_s is None
    assert match.reason is GnssUnavailableReason.CLOCK_UNAVAILABLE
    assert match.usable_for_map is False


def test_unshared_domain_takes_precedence_over_empty_cache() -> None:
    match = _match((), shared_monotonic_domain=False)
    assert match.reason is GnssUnavailableReason.CLOCK_UNAVAILABLE


# ---------------------------------------------------------------------------
# Empty cache -> no_fix
# ---------------------------------------------------------------------------


def test_empty_snapshot_reports_no_fix() -> None:
    match = _match(())
    assert match.fix is None
    assert match.age_s is None
    assert match.reason is GnssUnavailableReason.NO_FIX
    assert match.usable_for_map is False


# ---------------------------------------------------------------------------
# Cross-generation matching (reconnect gap is just distance)
# ---------------------------------------------------------------------------


def test_fixes_across_reader_generation_gap_match_by_distance() -> None:
    # A reconnect-style monotonic gap: the post-gap fix lands far away, so the
    # pre-gap fix is the honest nearest even though it predates the reconnect.
    pre_gap = _fix(mono_ns=MID_NS - 100_000_000)
    post_gap = _fix(mono_ns=MID_NS + 30_000_000_000)
    assert _match((pre_gap, post_gap)).fix is pre_gap
    # Reversed geometry: the post-gap fix is nearer and must win.
    near_post = _fix(mono_ns=MID_NS + 50_000_000)
    far_pre = _fix(mono_ns=MID_NS - 30_000_000_000)
    assert _match((far_pre, near_post)).fix is near_post


# ---------------------------------------------------------------------------
# Purity, immutability and stable method/status surfaces
# ---------------------------------------------------------------------------


def test_match_is_pure_inputs_unchanged_result_frozen_and_repeatable() -> None:
    fixes = (_fix(mono_ns=MID_NS - 1), _fix(mono_ns=MID_NS + 1))
    snapshot_before = tuple(f.received_monotonic_ns.ns for f in fixes)
    matcher = _matcher()
    args = _times()
    first = matcher.match(fixes=fixes, **args)  # type: ignore[arg-type]
    second = matcher.match(fixes=fixes, **args)  # type: ignore[arg-type]
    assert first == second
    assert tuple(f.received_monotonic_ns.ns for f in fixes) == snapshot_before
    with pytest.raises(FrozenInstanceError):
        first.age_s = 1.0  # type: ignore[misc]


def test_method_is_always_nearest_midpoint() -> None:
    cases = (
        _match(()),
        _match((_fix(mono_ns=MID_NS),)),
        _match((_fix(mono_ns=MID_NS + _WINDOW_NS + 1),)),
        _match(
            (_fix(mono_ns=MID_NS, utc=MID_UTC),), shared_monotonic_domain=False
        ),
    )
    for match in cases:
        assert match.method is GnssMatchMethod.NEAREST_MIDPOINT


def test_status_property_derives_the_expected_states() -> None:
    assert _match((_fix(mono_ns=MID_NS),)).status is GnssStatus.VALID
    assert (
        _match((_fix(mono_ns=MID_NS + _STALE_NS + 1),)).status is GnssStatus.STALE
    )
    assert (
        _match((_fix(mono_ns=MID_NS + 1, valid=False),)).status is GnssStatus.INVALID
    )
    assert _match(()).status is GnssStatus.NO_FIX
    assert (
        _match((_fix(mono_ns=MID_NS + _WINDOW_NS + 1),)).status is GnssStatus.NO_FIX
    )
    unshared = _match(
        (_fix(mono_ns=MID_NS, utc=MID_UTC),), shared_monotonic_domain=False
    )
    assert unshared.status is GnssStatus.NO_FIX


# ---------------------------------------------------------------------------
# Parameter and input validation (fail-closed)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stale_after_s", "window_s"),
    [
        (0.0, 1.0),
        (-0.25, 1.0),
        (float("nan"), 1.0),
        (float("inf"), 1.0),
        (0.25, 0.0),
        (0.25, -1.0),
        (0.25, float("nan")),
        (0.25, float("inf")),
        (0.5, 0.25),  # window < stale is incoherent (stale unreachable)
    ],
)
def test_invalid_thresholds_are_rejected(stale_after_s: float, window_s: float) -> None:
    with pytest.raises(ValueError):
        GnssTraceMatcher(stale_after_s=stale_after_s, window_s=window_s)


def test_equal_window_and_threshold_is_accepted() -> None:
    GnssTraceMatcher(stale_after_s=0.25, window_s=0.25)


def test_reversed_sweep_times_are_rejected() -> None:
    matcher = _matcher()
    with pytest.raises(ValueError):
        matcher.match(
            fixes=(),
            started_utc=FINISH_UTC,
            finished_utc=START_UTC,
            started_monotonic_ns=MonotonicNs(FINISH_NS),
            finished_monotonic_ns=MonotonicNs(START_NS),
            shared_monotonic_domain=True,
        )
    with pytest.raises(ValueError):
        matcher.match(
            fixes=(),
            started_utc=START_UTC,
            finished_utc=FINISH_UTC,
            started_monotonic_ns=MonotonicNs(FINISH_NS),
            finished_monotonic_ns=MonotonicNs(START_NS),
            shared_monotonic_domain=True,
        )


def test_non_tuple_and_foreign_elements_are_rejected() -> None:
    with pytest.raises(TypeError):
        _match([_fix(mono_ns=MID_NS)])  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        _match((_fix(mono_ns=MID_NS), "not-a-fix"))  # type: ignore[arg-type]


def test_shared_monotonic_domain_must_be_a_bool() -> None:
    """P3 (ISSUE-026 review §10): the explicit domain declaration must be a
    real bool — ints/None must not silently coerce into either branch."""
    fix = _fix(mono_ns=MID_NS)
    for bad in (1, 0, None, "yes"):  # type: ignore[assignment]
        with pytest.raises(TypeError):
            _match((fix,), shared_monotonic_domain=bad)  # type: ignore[arg-type]


def test_naive_datetimes_are_rejected() -> None:
    with pytest.raises(DomainError) as excinfo:
        _match(
            (),
            started_utc=datetime(2026, 9, 2, 12, 0, 0),  # naive
        )
    assert excinfo.value.code is ErrorCode.NAIVE_DATETIME
