"""Contract tests for the pure functional NMEA GGA/RMC parser (ISSUE-024).

Covers the ISSUE-024 acceptance matrix with anonymous/synthetic sentences and
hardcoded golden checksums (computed once by an independent script; the
classic ``$GPGGA,123519,...*47`` vector cross-checks the algorithm):

- normal GGA/RMC, talker independence (GP/GL/GA/GN), south/west hemispheres;
- strict checksum / line length / non-ASCII / missing-prefix guards;
- no-fix (GGA quality 0), empty fields stay None (never 0), out-of-range
  fields fail closed with structured errors;
- RMC date + GGA time-of-day combination with an explicit midnight-crossing
  policy, missing RMC date, trusted-date injection;
- knots -> m/s conversion, course validation, MSL/geoid separation (MSL is
  never labelled AGL);
- immutable GnssFix assembly reusing the ISSUE-005 model.

The parser is pure: no serial I/O, no clock access, no caching, no trace
matching, no map/UI code.  ``received_utc``/``received_monotonic_ns`` are
injected by the caller at assembly time and never fabricated here.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from uav_gpr.core.enums import GnssFixQuality, GnssUnavailableReason
from uav_gpr.core.errors import DomainError, ErrorCode
from uav_gpr.core.gnss import GnssFix
from uav_gpr.core.timeutil import MonotonicNs
from uav_gpr.positioning.nmea import (
    GgaResult,
    NmeaError,
    NmeaErrorReason,
    NmeaTimeOfDay,
    RmcResult,
    assemble_gnss_fix,
    combine_nmea_utc,
    parse_nmea,
)

# ---------------------------------------------------------------------------
# Golden fixtures (anonymous/synthetic; checksums hardcoded from an
# independent pre-computation; "*47" matches the widely published classic
# example).
# ---------------------------------------------------------------------------

GGA_GPS = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"
GGA_NO_FIX = "$GPGGA,123520,,,,,0,00,,,M,,M,,*61"
GGA_DGPS_WEST = "$GPGGA,183730,3907.356,N,12102.482,W,2,03,,,M,,M,,*43"
GGA_SOUTH_WEST = "$GPGGA,235958,3352.000,S,15112.000,E,1,07,1.2,12.3,M,-2.5,M,,*70"
GGA_GL = "$GLGGA,120000,5007.001,N,01426.002,E,4,10,0.8,200.0,M,45.0,M,,*52"
GGA_GN_EMPTY = "$GNGGA,000001,4807.038,N,01131.000,E,5,08,,545.4,M,,M,,*63"
GGA_GA_SIM = "$GAGGA,120001,4807.038,N,01131.000,E,8,06,1.5,100.0,M,40.0,M,,*5D"
RMC_OK = "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A"
RMC_SOUTH_WEST = "$GNRMC,235959,A,3352.000,S,15112.000,E,010.0,000.0,230394,,*10"
RMC_VOID = "$GPRMC,123521,V,,,,,0.0,0.0,230394,,*38"
RMC_NO_DATE = "$GPRMC,123522,A,4807.038,N,01131.000,E,022.4,084.4,,003.1,W*6D"
RMC_NO_SPEED = "$GPRMC,123523,A,4807.038,N,01131.000,E,,,230394,,*14"
RMC_MIDNIGHT = "$GPRMC,235959,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*66"
GGA_MIDNIGHT = "$GPGGA,000001,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*4B"
RMC_YEAR_1999 = "$GPRMC,120000,A,4807.038,N,01131.000,E,010.0,090.0,311299,,*15"
RMC_YEAR_2079 = "$GPRMC,120000,A,4807.038,N,01131.000,E,010.0,090.0,311279,,*1B"
GGA_LAT_MIN_60 = "$GPGGA,120000,4860.000,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*43"
GGA_HDOP_100 = "$GPGGA,120000,4807.038,N,01131.000,E,1,08,100.0,545.4,M,46.9,M,,*41"
GGA_SATS_100 = "$GPGGA,120000,4807.038,N,01131.000,E,1,100,0.9,545.4,M,46.9,M,,*70"
RMC_COURSE_360 = "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,360.0,230394,,*1C"
GGA_QUALITY_PPS = "$GPGGA,120000,4807.038,N,01131.000,E,3,08,0.9,545.4,M,46.9,M,,*4B"
GGA_HEMI_Z = "$GPGGA,120000,4807.038,Z,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*5D"
RMC_BAD_DATE = "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,310294,,*13"
GGA_TIME_24 = "$GPGGA,240000,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*4C"
GGA_SEC_60 = "$GPGGA,123560,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*49"
GGA_EMPTY_TIME = "$GPGGA,,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*4A"
GGA_UNIT_FEET = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,F,46.9,M,,*4C"

# Derived fixtures (checksums recomputed for the modified bodies).
GGA_BAD_TALKER = "$gpGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"
GSA_UNSUPPORTED = "$GPGSA,A,3,04,05,,09,12,,,24,,,,,2.5,1.3,2.1*39"
GGA_TOO_FEW = "$GPGGA,123519,4807.038,N,01131.000*27"
GGA_EMPTY_QUALITY = "$GPGGA,123519,4807.038,N,01131.000,E,,08,0.9,545.4,M,46.9,M,,*76"
GGA_QUALITY_MANUAL = "$GPGGA,123519,4807.038,N,01131.000,E,7,08,0.9,545.4,M,46.9,M,,*41"
GGA_QUALITY1_NO_COORDS = "$GPGGA,123519,,,,,1,08,0.9,545.4,M,46.9,M,,*7E"
GGA_HEMI_NO_COORDS = "$GPGGA,123519,,N,,E,1,08,0.9,545.4,M,46.9,M,,*75"
GGA_QUALITY0_WITH_COORDS = "$GPGGA,123520,4807.038,N,01131.000,E,0,00,,,M,,M,,*58"

RECEIVED_UTC = datetime(2026, 9, 2, 12, 35, 20, tzinfo=UTC)
RECEIVED_MONO = MonotonicNs(1_700_000_000_000_000_000)


def _parse_gga(line: str) -> GgaResult:
    result = parse_nmea(line)
    assert isinstance(result, GgaResult), f"expected GGA, got {type(result).__name__}"
    return result


def _parse_rmc(line: str) -> RmcResult:
    result = parse_nmea(line)
    assert isinstance(result, RmcResult), f"expected RMC, got {type(result).__name__}"
    return result


def _fix(
    gga_line: str,
    *,
    rmc_line: str | None = None,
    trusted_date: date | None = None,
    received_utc: datetime = RECEIVED_UTC,
    received_mono: MonotonicNs = RECEIVED_MONO,
) -> GnssFix:
    rmc = _parse_rmc(rmc_line) if rmc_line is not None else None
    return assemble_gnss_fix(
        _parse_gga(gga_line),
        received_utc,
        received_mono,
        rmc=rmc,
        trusted_date=trusted_date,
    )


def _expect_error(line: str, reason: NmeaErrorReason, field: str | None = None) -> None:
    with pytest.raises(NmeaError) as excinfo:
        parse_nmea(line)
    error = excinfo.value
    assert error.code is ErrorCode.INVALID_ARGUMENT
    assert error.reason is reason
    if field is not None:
        assert error.context.get("field") == field


# ---------------------------------------------------------------------------
# Normal GGA / RMC
# ---------------------------------------------------------------------------


class TestGgaNormal:
    def test_gps_fix_golden(self) -> None:
        result = _parse_gga(GGA_GPS)
        assert result.talker == "GP"
        assert result.utc_time == NmeaTimeOfDay(12, 35, 19.0)
        assert result.latitude_deg == pytest.approx(48.1173, abs=1e-9)
        assert result.longitude_deg == pytest.approx(11 + 31.0 / 60.0, abs=1e-9)
        assert result.fix_quality is GnssFixQuality.GPS_FIX
        assert result.satellites == 8
        assert result.hdop == pytest.approx(0.9)
        assert result.altitude_msl_m == pytest.approx(545.4)
        assert result.geoid_separation_m == pytest.approx(46.9)

    def test_dgps_west_hemisphere(self) -> None:
        result = _parse_gga(GGA_DGPS_WEST)
        assert result.fix_quality is GnssFixQuality.DGPS
        assert result.longitude_deg == pytest.approx(-(121 + 2.482 / 60.0), abs=1e-9)

    def test_south_west_hemisphere_negative(self) -> None:
        result = _parse_gga(GGA_SOUTH_WEST)
        assert result.latitude_deg == pytest.approx(-(33 + 52.0 / 60.0), abs=1e-9)
        assert result.longitude_deg == pytest.approx(151 + 12.0 / 60.0, abs=1e-9)

    def test_talker_independent(self) -> None:
        for line, talker, quality in (
            (GGA_GL, "GL", GnssFixQuality.RTK_FIXED),
            (GGA_GN_EMPTY, "GN", GnssFixQuality.RTK_FLOAT),
            (GGA_GA_SIM, "GA", GnssFixQuality.SIMULATED),
        ):
            result = _parse_gga(line)
            assert result.talker == talker
            assert result.fix_quality is quality

    def test_msl_altitude_and_geoid_separation_kept_separate(self) -> None:
        result = _parse_gga(GGA_SOUTH_WEST)
        # GGA altitude is MSL by NMEA definition; geoid separation is stored
        # separately and the altitude is never labelled AGL.
        assert result.altitude_msl_m == pytest.approx(12.3)
        assert result.geoid_separation_m == pytest.approx(-2.5)

    def test_empty_optional_fields_are_none(self) -> None:
        result = _parse_gga(GGA_GN_EMPTY)
        assert result.satellites == 8
        assert result.hdop is None
        assert result.altitude_msl_m == pytest.approx(545.4)
        assert result.geoid_separation_m is None

    def test_quality_zero_sentence(self) -> None:
        result = _parse_gga(GGA_NO_FIX)
        assert result.fix_quality is GnssFixQuality.INVALID
        assert result.latitude_deg is None
        assert result.longitude_deg is None
        assert result.satellites == 0

    def test_empty_time_is_none_not_error(self) -> None:
        result = _parse_gga(GGA_EMPTY_TIME)
        assert result.utc_time is None
        assert result.latitude_deg == pytest.approx(48.1173, abs=1e-9)


class TestRmcNormal:
    def test_valid_rmc_golden(self) -> None:
        result = _parse_rmc(RMC_OK)
        assert result.talker == "GP"
        assert result.status_valid is True
        assert result.utc_time == NmeaTimeOfDay(12, 35, 19.0)
        assert result.utc_date == date(1994, 3, 23)
        assert result.latitude_deg == pytest.approx(48.1173, abs=1e-9)
        assert result.longitude_deg == pytest.approx(11 + 31.0 / 60.0, abs=1e-9)
        assert result.ground_speed_mps == pytest.approx(22.4 * 1852.0 / 3600.0)
        assert result.course_deg == pytest.approx(84.4)

    def test_south_west_rmc(self) -> None:
        result = _parse_rmc(RMC_SOUTH_WEST)
        assert result.latitude_deg == pytest.approx(-(33 + 52.0 / 60.0), abs=1e-9)
        assert result.longitude_deg == pytest.approx(151 + 12.0 / 60.0, abs=1e-9)
        # 010.0 knots -> exact m/s conversion (1 knot = 1852/3600 m/s)
        assert result.ground_speed_mps == pytest.approx(10.0 * 1852.0 / 3600.0)
        assert result.course_deg == pytest.approx(0.0)

    def test_void_status(self) -> None:
        assert _parse_rmc(RMC_VOID).status_valid is False

    def test_missing_date_and_speed_fields_are_none(self) -> None:
        assert _parse_rmc(RMC_NO_DATE).utc_date is None
        no_speed = _parse_rmc(RMC_NO_SPEED)
        assert no_speed.ground_speed_mps is None
        assert no_speed.course_deg is None

    def test_two_digit_year_rollover(self) -> None:
        assert _parse_rmc(RMC_YEAR_1999).utc_date == date(1999, 12, 31)
        assert _parse_rmc(RMC_YEAR_2079).utc_date == date(2079, 12, 31)


# ---------------------------------------------------------------------------
# Strict guards: checksum, length, encoding, structure
# ---------------------------------------------------------------------------


class TestGuards:
    def test_bad_checksum_rejected(self) -> None:
        _expect_error(GGA_GPS[:-2] + "00", NmeaErrorReason.BAD_CHECKSUM)

    def test_checksum_case_insensitive_ok(self) -> None:
        lower = GGA_GA_SIM[:-2] + "5d"
        assert isinstance(parse_nmea(lower), GgaResult)

    def test_missing_checksum_rejected(self) -> None:
        _expect_error(GGA_GPS.rsplit("*", 1)[0], NmeaErrorReason.MISSING_CHECKSUM)

    def test_non_hex_checksum_rejected(self) -> None:
        _expect_error(GGA_GPS[:-2] + "ZZ", NmeaErrorReason.MALFORMED_FIELD, "checksum")

    def test_line_too_long_rejected(self) -> None:
        long_line = "$" + "GPGGA," + "1" * 400 + "*00"
        _expect_error(long_line, NmeaErrorReason.LINE_TOO_LONG)

    def test_non_ascii_rejected(self) -> None:
        _expect_error(
            "$GPGGA,12\u00e93519,4807.038,N,01131.000,E,1,08,*00",
            NmeaErrorReason.NON_ASCII,
        )

    def test_missing_prefix_rejected(self) -> None:
        _expect_error(
            "GPGGA,123519,4807.038,N,01131.000,E,1,08,*00",
            NmeaErrorReason.NOT_A_SENTENCE,
        )

    def test_bad_talker_rejected(self) -> None:
        _expect_error(GGA_BAD_TALKER, NmeaErrorReason.BAD_TALKER)

    def test_unsupported_sentence_rejected(self) -> None:
        _expect_error(GSA_UNSUPPORTED, NmeaErrorReason.UNSUPPORTED_SENTENCE)

    def test_too_few_fields_rejected(self) -> None:
        _expect_error(GGA_TOO_FEW, NmeaErrorReason.TOO_FEW_FIELDS)

    def test_empty_quality_rejected(self) -> None:
        _expect_error(GGA_EMPTY_QUALITY, NmeaErrorReason.MALFORMED_FIELD, "fix_quality")

    def test_crlf_tolerated(self) -> None:
        assert isinstance(parse_nmea(GGA_GPS + "\r\n"), GgaResult)


# ---------------------------------------------------------------------------
# Field semantics: out of range / malformed / unsupported
# ---------------------------------------------------------------------------


class TestFieldSemantics:
    @pytest.mark.parametrize(
        ("line", "field"),
        [
            (GGA_LAT_MIN_60, "latitude"),
            (GGA_HDOP_100, "hdop"),
            (GGA_SATS_100, "satellites"),
            (GGA_TIME_24, "time"),
            (GGA_SEC_60, "time"),
        ],
    )
    def test_out_of_range_rejected(self, line: str, field: str) -> None:
        _expect_error(line, NmeaErrorReason.OUT_OF_RANGE, field)

    def test_course_360_rejected(self) -> None:
        _expect_error(RMC_COURSE_360, NmeaErrorReason.OUT_OF_RANGE, "course")

    def test_invalid_calendar_date_rejected(self) -> None:
        _expect_error(RMC_BAD_DATE, NmeaErrorReason.OUT_OF_RANGE, "date")

    def test_bad_hemisphere_rejected(self) -> None:
        _expect_error(GGA_HEMI_Z, NmeaErrorReason.MALFORMED_FIELD, "latitude_hemisphere")

    def test_unsupported_fix_quality_pps_rejected(self) -> None:
        _expect_error(GGA_QUALITY_PPS, NmeaErrorReason.UNSUPPORTED_FIX_QUALITY)

    def test_unsupported_fix_quality_manual_rejected(self) -> None:
        _expect_error(GGA_QUALITY_MANUAL, NmeaErrorReason.UNSUPPORTED_FIX_QUALITY)

    def test_altitude_unit_not_metres_rejected(self) -> None:
        _expect_error(GGA_UNIT_FEET, NmeaErrorReason.MALFORMED_FIELD, "altitude_units")

    def test_valid_quality_requires_coordinates(self) -> None:
        _expect_error(GGA_QUALITY1_NO_COORDS, NmeaErrorReason.MALFORMED_FIELD, "latitude")

    def test_hemisphere_without_coordinates_rejected(self) -> None:
        _expect_error(GGA_HEMI_NO_COORDS, NmeaErrorReason.MALFORMED_FIELD, "latitude_hemisphere")


# ---------------------------------------------------------------------------
# RMC date + GGA time combination and midnight-crossing policy
# ---------------------------------------------------------------------------


class TestCombination:
    def test_rmc_date_with_gga_time_same_day(self) -> None:
        assert combine_nmea_utc(NmeaTimeOfDay(12, 35, 19.0), _parse_rmc(RMC_OK)) == datetime(
            1994, 3, 23, 12, 35, 19, tzinfo=UTC
        )

    def test_midnight_crossing_rolls_forward(self) -> None:
        # RMC 23:59:59 on 1994-03-23, GGA 00:00:01 -> GGA belongs to 03-24.
        assert combine_nmea_utc(NmeaTimeOfDay(0, 0, 1.0), _parse_rmc(RMC_MIDNIGHT)) == datetime(
            1994, 3, 24, 0, 0, 1, tzinfo=UTC
        )

    def test_midnight_crossing_rolls_backward(self) -> None:
        # RMC 00:00:01 on 1994-03-23, GGA 23:59:59 -> GGA belongs to 03-22.
        rmc_early = _parse_rmc(RMC_MIDNIGHT.replace("235959", "000001"))
        assert combine_nmea_utc(NmeaTimeOfDay(23, 59, 59.0), rmc_early) == datetime(
            1994, 3, 22, 23, 59, 59, tzinfo=UTC
        )

    def test_missing_rmc_date_yields_none_without_trusted_date(self) -> None:
        assert combine_nmea_utc(NmeaTimeOfDay(12, 35, 19.0), _parse_rmc(RMC_NO_DATE)) is None

    def test_trusted_date_injected_when_rmc_date_missing(self) -> None:
        assert combine_nmea_utc(
            NmeaTimeOfDay(12, 35, 19.0),
            _parse_rmc(RMC_NO_DATE),
            trusted_date=date(1994, 3, 23),
        ) == datetime(1994, 3, 23, 12, 35, 19, tzinfo=UTC)

    def test_void_rmc_does_not_provide_date(self) -> None:
        assert combine_nmea_utc(NmeaTimeOfDay(12, 35, 19.0), _parse_rmc(RMC_VOID)) is None

    def test_gga_time_none_uses_rmc_datetime(self) -> None:
        assert combine_nmea_utc(None, _parse_rmc(RMC_OK)) == datetime(
            1994, 3, 23, 12, 35, 19, tzinfo=UTC
        )

    def test_no_time_and_no_rmc_yields_none(self) -> None:
        assert combine_nmea_utc(None, None) is None

    def test_gga_alone_without_date_yields_none(self) -> None:
        assert combine_nmea_utc(NmeaTimeOfDay(12, 35, 19.0), None) is None

    def test_fractional_seconds_kept(self) -> None:
        result = _parse_gga("$GPGGA,123519.50,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*6C")
        assert result.utc_time is not None
        assert result.utc_time.second == pytest.approx(19.5)
        assert combine_nmea_utc(result.utc_time, _parse_rmc(RMC_OK)) == datetime(
            1994, 3, 23, 12, 35, 19, 500000, tzinfo=UTC
        )


# ---------------------------------------------------------------------------
# Immutable GnssFix assembly (ISSUE-005 model reuse)
# ---------------------------------------------------------------------------


class TestAssembly:
    def test_valid_fix_full_fields(self) -> None:
        fix = _fix(GGA_GPS, rmc_line=RMC_OK)
        assert fix.valid is True
        assert fix.invalid_reason is None
        assert fix.fix_quality is GnssFixQuality.GPS_FIX
        assert fix.latitude_deg == pytest.approx(48.1173, abs=1e-9)
        assert fix.longitude_deg == pytest.approx(11 + 31.0 / 60.0, abs=1e-9)
        assert fix.altitude_msl_m == pytest.approx(545.4)
        assert fix.geoid_separation_m == pytest.approx(46.9)
        assert fix.satellites == 8
        assert fix.hdop == pytest.approx(0.9)
        assert fix.ground_speed_mps == pytest.approx(22.4 * 1852.0 / 3600.0)
        assert fix.course_deg == pytest.approx(84.4)
        assert fix.received_utc == RECEIVED_UTC
        assert fix.received_monotonic_ns == RECEIVED_MONO
        assert fix.nmea_utc == datetime(1994, 3, 23, 12, 35, 19, tzinfo=UTC)

    def test_no_fix_quality_zero(self) -> None:
        fix = _fix(GGA_NO_FIX)
        assert fix.valid is False
        assert fix.invalid_reason is GnssUnavailableReason.NO_FIX
        assert fix.latitude_deg is None
        assert fix.longitude_deg is None
        # present fields are preserved as parsed; absent fields stay None
        assert fix.satellites == 0
        assert fix.hdop is None
        assert fix.altitude_msl_m is None

    def test_quality_zero_drops_sentence_coordinates(self) -> None:
        # quality 0 with coordinates in the sentence: the parser is lossless,
        # but assembly must not turn a no-fix sentence into a position.
        parsed = _parse_gga(GGA_QUALITY0_WITH_COORDS)
        assert parsed.latitude_deg is not None
        fix = assemble_gnss_fix(parsed, RECEIVED_UTC, RECEIVED_MONO)
        assert fix.valid is False
        assert fix.invalid_reason is GnssUnavailableReason.NO_FIX
        assert fix.latitude_deg is None
        assert fix.longitude_deg is None

    def test_void_rmc_contributes_no_speed_or_course(self) -> None:
        fix = _fix(GGA_GPS, rmc_line=RMC_VOID)
        assert fix.ground_speed_mps is None
        assert fix.course_deg is None
        assert fix.nmea_utc is None  # void RMC provides no date

    def test_midnight_crossing_assembled_fix(self) -> None:
        fix = _fix(GGA_MIDNIGHT, rmc_line=RMC_MIDNIGHT)
        assert fix.nmea_utc == datetime(1994, 3, 24, 0, 0, 1, tzinfo=UTC)

    def test_naive_received_utc_rejected_by_model(self) -> None:
        with pytest.raises(DomainError) as excinfo:
            assemble_gnss_fix(_parse_gga(GGA_GPS), datetime(2026, 9, 2, 12, 0, 0), RECEIVED_MONO)
        assert excinfo.value.code is ErrorCode.NAIVE_DATETIME

    def test_gnss_fix_json_roundtrip(self) -> None:
        fix = _fix(GGA_GPS, rmc_line=RMC_OK)
        assert GnssFix.from_dict(fix.to_dict()) == fix

    def test_result_dataclasses_are_frozen(self) -> None:
        result = _parse_gga(GGA_GPS)
        with pytest.raises(FrozenInstanceError):
            result.talker = "XX"  # type: ignore[misc]

    def test_msl_never_called_agl(self) -> None:
        # The assembled fix exposes altitude_msl_m only; no AGL field exists.
        fix = _fix(GGA_GPS)
        assert not hasattr(fix, "altitude_agl_m")
        assert fix.altitude_msl_m == pytest.approx(545.4)


# ---------------------------------------------------------------------------
# Purity guards
# ---------------------------------------------------------------------------


class TestPurity:
    def test_parser_module_has_no_qt_serial_network_imports(self) -> None:
        import ast

        source = (
            Path(__file__).resolve().parents[2]
            / "src"
            / "uav_gpr"
            / "positioning"
            / "nmea.py"
        )
        tree = ast.parse(source.read_text(encoding="utf-8"))
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imports.append(node.module)
        forbidden = {"serial", "usb", "socket", "PySide6", "PyQt", "qt"}
        assert not any(root.split(".")[0] in forbidden for root in imports), imports

    def test_parse_nmea_accepts_str_only(self) -> None:
        with pytest.raises(TypeError):
            parse_nmea(b"$GPGGA,123519,4807.038,N,01131.000,E,1,08,*47")  # type: ignore[arg-type]

    def test_error_payload_is_json_safe(self) -> None:
        with pytest.raises(NmeaError) as excinfo:
            parse_nmea(GGA_GPS[:-2] + "00")
        payload = excinfo.value.to_dict()
        assert payload["code"] == "invalid_argument"
        assert payload["context"]["reason"] == "bad_checksum"
        assert payload["context"]["expected"] == "47"
        assert payload["context"]["got"] == "00"
