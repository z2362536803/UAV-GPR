"""Pure, side-effect-free NMEA GGA/RMC parser (ISSUE-024).

Contract summary (see docs/GNSS.md, docs/issues/M05_GNSS.md and
docs/plans/2026-09-02-issue-024-nmea.md):

- ``parse_nmea`` is a pure function: one sentence in, one immutable parse
  result or a structured ``NmeaError`` out.  No serial I/O, no clock access,
  no caching, no trace matching and no map/UI code lives here.
- Talker-independent GGA/RMC with strict checksum / line-length / non-ASCII /
  range / unit validation.  Empty fields stay ``None`` (never 0).
- GGA fix quality maps to the ISSUE-005 ``GnssFixQuality`` enum; quality 3
  (PPS) and 7 (manual) have no enum member and are rejected fail-closed.
- GGA altitude is MSL by NMEA definition and is stored in
  ``GnssFix.altitude_msl_m``; geoid separation is stored separately.  MSL is
  never labelled AGL and no AGL value is ever derived.
- RMC date combines with GGA time-of-day using a 12-hour midnight-crossing
  policy; when no NMEA date exists the caller may inject a trusted date, and
  if none is available ``nmea_utc`` stays ``None``.  The local receive time
  is never used as NMEA time.
- ``assemble_gnss_fix`` builds the immutable ``GnssFix``; the receive-side
  facts (``received_utc``/``received_monotonic_ns``) are injected by the
  caller (the ISSUE-025 reader) and never fabricated here.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum

from uav_gpr.core.enums import GnssFixQuality, GnssUnavailableReason
from uav_gpr.core.errors import DomainError, ErrorCode, JsonValue
from uav_gpr.core.gnss import GnssFix
from uav_gpr.core.timeutil import MonotonicNs

MAX_NMEA_LINE_LEN = 256
"""Hard upper bound on one NMEA line (defence against pathological input)."""

KNOTS_TO_MPS = 1852.0 / 3600.0
"""One knot is one nautical mile (1852 m) per hour."""

_MIDNIGHT_TOLERANCE_S = 12.0 * 3600.0
"""12-hour tolerance for the RMC-date/GGA-time midnight-crossing policy."""

_TALKER_RE = re.compile(r"[A-Z0-9]{2}")
_COORD_RE = re.compile(r"\d+(\.\d+)?")
_TIME_RE = re.compile(r"\d{6}(\.\d+)?")
_DATE_RE = re.compile(r"\d{6}")

_LAT_MAX_DEG = 90.0
_LON_MAX_DEG = 180.0
_SATS_MIN, _SATS_MAX = 0, 99
_HDOP_MAX = 99.9
_COURSE_MAX_DEG = 360.0


class NmeaErrorReason(StrEnum):
    """Stable structured reason why a sentence was rejected."""

    BAD_CHECKSUM = "bad_checksum"
    MISSING_CHECKSUM = "missing_checksum"
    LINE_TOO_LONG = "line_too_long"
    NON_ASCII = "non_ascii"
    NOT_A_SENTENCE = "not_a_sentence"
    BAD_TALKER = "bad_talker"
    UNSUPPORTED_SENTENCE = "unsupported_sentence"
    TOO_FEW_FIELDS = "too_few_fields"
    MALFORMED_FIELD = "malformed_field"
    OUT_OF_RANGE = "out_of_range"
    UNSUPPORTED_FIX_QUALITY = "unsupported_fix_quality"


class NmeaError(DomainError):
    """Structured NMEA rejection.

    ``code`` is always ``invalid_argument``; ``reason`` (an
    ``NmeaErrorReason``) discriminates the rejection and further JSON-safe
    context (``field``/``expected``/``got``/...) is attached per case.
    """

    def __init__(
        self,
        reason: NmeaErrorReason,
        message: str,
        context: Mapping[str, JsonValue] | None = None,
    ) -> None:
        if not isinstance(reason, NmeaErrorReason):
            raise TypeError(
                f"reason must be an NmeaErrorReason, got {type(reason).__name__}"
            )
        merged: dict[str, JsonValue] = {"reason": reason.value}
        merged.update(context or {})
        super().__init__(ErrorCode.INVALID_ARGUMENT, message, merged)

    @property
    def reason(self) -> NmeaErrorReason:
        raw = self.context.get("reason")
        if not isinstance(raw, str):
            raise RuntimeError("NmeaError context lost its reason")
        return NmeaErrorReason(raw)


@dataclass(frozen=True, slots=True)
class NmeaTimeOfDay:
    """Validated UTC time-of-day parsed from an ``hhmmss(.ss)`` field."""

    hour: int
    minute: int
    second: float

    def __post_init__(self) -> None:
        if isinstance(self.hour, bool) or not isinstance(self.hour, int):
            raise TypeError("hour must be an int")
        if isinstance(self.minute, bool) or not isinstance(self.minute, int):
            raise TypeError("minute must be an int")
        if not isinstance(self.second, float):
            raise TypeError("second must be a float")
        if not 0 <= self.hour <= 23:
            raise NmeaError(
                NmeaErrorReason.OUT_OF_RANGE,
                "hour out of range",
                {"field": "time", "hour": self.hour},
            )
        if not 0 <= self.minute <= 59:
            raise NmeaError(
                NmeaErrorReason.OUT_OF_RANGE,
                "minute out of range",
                {"field": "time", "minute": self.minute},
            )
        if not 0.0 <= self.second < 60.0:
            raise NmeaError(
                NmeaErrorReason.OUT_OF_RANGE,
                "second out of range",
                {"field": "time", "second": self.second},
            )

    def to_seconds(self) -> float:
        """Seconds since local midnight (0 <= result < 86400)."""
        return self.hour * 3600.0 + self.minute * 60.0 + self.second


@dataclass(frozen=True, slots=True)
class GgaResult:
    """Immutable parse result of one ``$xxGGA`` sentence.

    ``latitude_deg``/``longitude_deg`` are WGS84 decimal degrees (S/W
    negative) when the sentence carries coordinates.  A quality-0 sentence
    may still carry parsed coordinates: the parser is lossless; the
    ``assemble_gnss_fix`` step applies the no-fix rule (coordinates dropped,
    ``invalid_reason=no_fix``) per AGENTS.md §5.
    """

    talker: str
    utc_time: NmeaTimeOfDay | None
    latitude_deg: float | None
    longitude_deg: float | None
    fix_quality: GnssFixQuality
    satellites: int | None
    hdop: float | None
    altitude_msl_m: float | None
    geoid_separation_m: float | None
    raw_line: str


@dataclass(frozen=True, slots=True)
class RmcResult:
    """Immutable parse result of one ``$xxRMC`` sentence.

    ``status_valid`` is True only for status ``A``.  ``ground_speed_mps`` is
    already converted from knots; ``utc_date`` is None when the sentence
    carries no date (or an empty one).  A void (``V``) sentence may still
    carry parsed values: consumers must gate on ``status_valid``.
    """

    talker: str
    utc_time: NmeaTimeOfDay | None
    status_valid: bool
    utc_date: date | None
    latitude_deg: float | None
    longitude_deg: float | None
    ground_speed_mps: float | None
    course_deg: float | None
    raw_line: str


def parse_nmea(line: str) -> GgaResult | RmcResult:
    """Parse one NMEA sentence into an immutable result.

    Raises ``NmeaError`` (fail-closed) for a bad checksum, missing checksum,
    over-long line, non-ASCII content, missing ``$`` prefix, bad talker,
    unsupported sentence type, too few fields, malformed fields and
    out-of-range values.  Raises ``TypeError`` for non-str input.
    """
    if not isinstance(line, str):
        raise TypeError(f"line must be a str, got {type(line).__name__}")
    line = line.rstrip("\r\n")
    if len(line) > MAX_NMEA_LINE_LEN:
        raise NmeaError(
            NmeaErrorReason.LINE_TOO_LONG,
            "NMEA line exceeds the maximum length",
            {"max_length": MAX_NMEA_LINE_LEN, "length": len(line)},
        )
    if not line.isascii():
        raise NmeaError(
            NmeaErrorReason.NON_ASCII,
            "NMEA line must be ASCII-only",
            {"length": len(line)},
        )
    if not line.startswith("$"):
        raise NmeaError(
            NmeaErrorReason.NOT_A_SENTENCE,
            "NMEA line must start with '$'",
        )
    star = line.find("*")
    if star < 0:
        raise NmeaError(
            NmeaErrorReason.MISSING_CHECKSUM,
            "NMEA sentence is missing its checksum",
        )
    body = line[1:star]
    checksum_field = line[star + 1 :]
    if len(checksum_field) != 2 or not re.fullmatch(r"[0-9A-Fa-f]{2}", checksum_field):
        raise NmeaError(
            NmeaErrorReason.MALFORMED_FIELD,
            "checksum must be two hexadecimal digits",
            {"field": "checksum", "got": checksum_field},
        )
    expected = _checksum_hex(body)
    if checksum_field.upper() != expected:
        raise NmeaError(
            NmeaErrorReason.BAD_CHECKSUM,
            "NMEA checksum mismatch",
            {"field": "checksum", "expected": expected, "got": checksum_field.upper()},
        )

    fields = body.split(",")
    if not fields or len(fields[0]) != 5:
        raise NmeaError(
            NmeaErrorReason.BAD_TALKER,
            "sentence identifier must be a talker plus sentence type",
        )
    talker, sentence = fields[0][:2], fields[0][2:]
    if not _TALKER_RE.fullmatch(talker):
        raise NmeaError(
            NmeaErrorReason.BAD_TALKER,
            "invalid NMEA talker identifier",
            {"field": "talker", "got": talker},
        )
    data = fields[1:]
    if sentence == "GGA":
        return _parse_gga(talker, data, line)
    if sentence == "RMC":
        return _parse_rmc(talker, data, line)
    raise NmeaError(
        NmeaErrorReason.UNSUPPORTED_SENTENCE,
        "unsupported NMEA sentence type",
        {"field": "sentence", "got": sentence},
    )


def combine_nmea_utc(
    gga_time: NmeaTimeOfDay | None,
    rmc: RmcResult | None = None,
    trusted_date: date | None = None,
) -> datetime | None:
    """Combine GGA time-of-day with a date source into an aware UTC datetime.

    Policy (GNSS.md §2, plan D6): a status-A RMC provides the date; the GGA
    time-of-day is placed on that date with a 12-hour midnight-crossing
    tolerance (GGA earlier than RMC by >12 h rolls to the next day, later by
    >12 h rolls to the previous day).  When the RMC date is unavailable, an
    explicitly injected ``trusted_date`` may be used.  When no date source
    exists, or the GGA carries no time, the RMC's own full datetime is used
    if available; otherwise the result is ``None`` — never a fabricated time.
    """
    if gga_time is not None:
        base_date: date | None = None
        rmc_tod_seconds: float | None = None
        if rmc is not None and rmc.status_valid and rmc.utc_date is not None:
            base_date = rmc.utc_date
            if rmc.utc_time is not None:
                rmc_tod_seconds = rmc.utc_time.to_seconds()
        if base_date is None:
            base_date = trusted_date
        if base_date is None:
            return None
        day_offset = 0
        if rmc_tod_seconds is not None:
            delta = gga_time.to_seconds() - rmc_tod_seconds
            if delta < -_MIDNIGHT_TOLERANCE_S:
                day_offset = 1
            elif delta > _MIDNIGHT_TOLERANCE_S:
                day_offset = -1
        return _combine_date_and_seconds(
            base_date + timedelta(days=day_offset), gga_time.to_seconds()
        )
    if (
        rmc is not None
        and rmc.status_valid
        and rmc.utc_date is not None
        and rmc.utc_time is not None
    ):
        return _combine_date_and_seconds(rmc.utc_date, rmc.utc_time.to_seconds())
    return None


def assemble_gnss_fix(
    gga: GgaResult,
    received_utc: datetime,
    received_monotonic_ns: MonotonicNs,
    *,
    rmc: RmcResult | None = None,
    trusted_date: date | None = None,
) -> GnssFix:
    """Build the immutable ISSUE-005 ``GnssFix`` from a parsed GGA.

    Receive-side facts (``received_utc``, ``received_monotonic_ns``) are
    injected by the caller — never read from a clock here.  A quality-0 GGA
    yields an invalid fix with ``invalid_reason=no_fix`` and no coordinates;
    a status-V RMC contributes no date, speed or course.
    """
    if not isinstance(gga, GgaResult):
        raise TypeError(f"gga must be a GgaResult, got {type(gga).__name__}")
    speed = _rmc_speed_mps(rmc)
    course = _rmc_course_deg(rmc)
    nmea_utc = combine_nmea_utc(gga.utc_time, rmc, trusted_date)
    if gga.fix_quality is GnssFixQuality.INVALID:
        return GnssFix(
            received_utc=received_utc,
            nmea_utc=nmea_utc,
            received_monotonic_ns=received_monotonic_ns,
            latitude_deg=None,
            longitude_deg=None,
            altitude_msl_m=gga.altitude_msl_m,
            geoid_separation_m=gga.geoid_separation_m,
            fix_quality=GnssFixQuality.INVALID,
            satellites=gga.satellites,
            hdop=gga.hdop,
            ground_speed_mps=speed,
            course_deg=course,
            valid=False,
            invalid_reason=GnssUnavailableReason.NO_FIX,
        )
    return GnssFix(
        received_utc=received_utc,
        nmea_utc=nmea_utc,
        received_monotonic_ns=received_monotonic_ns,
        latitude_deg=gga.latitude_deg,
        longitude_deg=gga.longitude_deg,
        altitude_msl_m=gga.altitude_msl_m,
        geoid_separation_m=gga.geoid_separation_m,
        fix_quality=gga.fix_quality,
        satellites=gga.satellites,
        hdop=gga.hdop,
        ground_speed_mps=speed,
        course_deg=course,
        valid=True,
        invalid_reason=None,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _checksum_hex(body: str) -> str:
    xor = 0
    for char in body:
        xor ^= ord(char)
    return f"{xor:02X}"


def _combine_date_and_seconds(day: date, seconds: float) -> datetime:
    midnight = datetime.combine(day, time(0, 0), tzinfo=UTC)
    return midnight + timedelta(seconds=seconds)


def _parse_gga(talker: str, data: list[str], raw: str) -> GgaResult:
    if len(data) < 6:
        raise NmeaError(
            NmeaErrorReason.TOO_FEW_FIELDS,
            "GGA requires at least six data fields",
            {"sentence": "GGA", "got": len(data)},
        )
    utc_time = _parse_time(data[0])
    latitude, longitude = _parse_latlon_pair(data[1], data[2], data[3], data[4])
    quality_raw = data[5]
    if quality_raw == "":
        raise NmeaError(
            NmeaErrorReason.MALFORMED_FIELD,
            "GGA fix quality must not be empty",
            {"field": "fix_quality"},
        )
    quality = _parse_fix_quality(quality_raw)
    if quality is not GnssFixQuality.INVALID and (latitude is None or longitude is None):
        raise NmeaError(
            NmeaErrorReason.MALFORMED_FIELD,
            "GGA fix quality requires latitude and longitude",
            {"field": "latitude" if latitude is None else "longitude"},
        )
    satellites = _parse_optional_int(
        data[6] if len(data) > 6 else "", _SATS_MIN, _SATS_MAX, "satellites"
    )
    hdop = _parse_optional_float(
        data[7] if len(data) > 7 else "",
        0.0,
        _HDOP_MAX,
        "hdop",
        high_inclusive=True,
    )
    altitude_msl = _parse_optional_float(
        data[8] if len(data) > 8 else "",
        None,
        None,
        "altitude_msl",
        signed=True,
    )
    _require_metres_units(data[9] if len(data) > 9 else "", "altitude_units")
    geoid_separation = _parse_optional_float(
        data[10] if len(data) > 10 else "",
        None,
        None,
        "geoid_separation",
        signed=True,
    )
    _require_metres_units(data[11] if len(data) > 11 else "", "geoid_units")
    return GgaResult(
        talker=talker,
        utc_time=utc_time,
        latitude_deg=latitude,
        longitude_deg=longitude,
        fix_quality=quality,
        satellites=satellites,
        hdop=hdop,
        altitude_msl_m=altitude_msl,
        geoid_separation_m=geoid_separation,
        raw_line=raw,
    )


def _parse_rmc(talker: str, data: list[str], raw: str) -> RmcResult:
    if len(data) < 6:
        raise NmeaError(
            NmeaErrorReason.TOO_FEW_FIELDS,
            "RMC requires at least six data fields",
            {"sentence": "RMC", "got": len(data)},
        )
    utc_time = _parse_time(data[0])
    status = data[1]
    if status not in ("A", "V"):
        raise NmeaError(
            NmeaErrorReason.MALFORMED_FIELD,
            "RMC status must be A or V",
            {"field": "status", "got": status},
        )
    latitude, longitude = _parse_latlon_pair(data[2], data[3], data[4], data[5])
    speed_knots = _parse_optional_float(
        data[6] if len(data) > 6 else "", 0.0, None, "ground_speed"
    )
    course = _parse_optional_float(
        data[7] if len(data) > 7 else "",
        0.0,
        _COURSE_MAX_DEG,
        "course",
        high_inclusive=False,
    )
    utc_date = _parse_date(data[8] if len(data) > 8 else "")
    speed_mps = speed_knots * KNOTS_TO_MPS if speed_knots is not None else None
    return RmcResult(
        talker=talker,
        utc_time=utc_time,
        status_valid=status == "A",
        utc_date=utc_date,
        latitude_deg=latitude,
        longitude_deg=longitude,
        ground_speed_mps=speed_mps,
        course_deg=course,
        raw_line=raw,
    )


def _parse_time(raw: str) -> NmeaTimeOfDay | None:
    if raw == "":
        return None
    if not _TIME_RE.fullmatch(raw):
        raise NmeaError(
            NmeaErrorReason.MALFORMED_FIELD,
            "NMEA time must be hhmmss(.ss)",
            {"field": "time", "got": raw},
        )
    hour = int(raw[0:2])
    minute = int(raw[2:4])
    second = float(raw[4:])
    return NmeaTimeOfDay(hour, minute, second)


def _parse_latlon_pair(
    lat_raw: str, ns: str, lon_raw: str, ew: str
) -> tuple[float | None, float | None]:
    latitude = _parse_coordinate(
        lat_raw, ns, "latitude", "latitude_hemisphere", _LAT_MAX_DEG, 4
    )
    longitude = _parse_coordinate(
        lon_raw, ew, "longitude", "longitude_hemisphere", _LON_MAX_DEG, 5
    )
    return latitude, longitude


def _parse_coordinate(
    raw: str,
    hemisphere: str,
    coord_field: str,
    hemi_field: str,
    max_abs: float,
    degree_digits: int,
) -> float | None:
    if raw == "" and hemisphere == "":
        return None
    if raw == "":
        raise NmeaError(
            NmeaErrorReason.MALFORMED_FIELD,
            "hemisphere present without coordinates",
            {"field": hemi_field},
        )
    if hemisphere == "":
        raise NmeaError(
            NmeaErrorReason.MALFORMED_FIELD,
            "coordinates present without hemisphere",
            {"field": coord_field},
        )
    if hemisphere not in ("N", "S", "E", "W"):
        raise NmeaError(
            NmeaErrorReason.MALFORMED_FIELD,
            "invalid hemisphere letter",
            {"field": hemi_field, "got": hemisphere},
        )
    if not _COORD_RE.fullmatch(raw):
        raise NmeaError(
            NmeaErrorReason.MALFORMED_FIELD,
            "coordinate must be ddmm.mmmm",
            {"field": coord_field, "got": raw},
        )
    whole, dot, fraction = raw.partition(".")
    if len(whole) != degree_digits:
        raise NmeaError(
            NmeaErrorReason.MALFORMED_FIELD,
            "coordinate must be ddmm.mmmm",
            {"field": coord_field, "got": raw},
        )
    degrees = int(whole[: degree_digits - 2])
    minutes_text = whole[degree_digits - 2 :]
    if dot:
        minutes_text = f"{minutes_text}.{fraction}"
    minutes = float(minutes_text)
    if minutes >= 60.0:
        raise NmeaError(
            NmeaErrorReason.OUT_OF_RANGE,
            "coordinate minutes must be below 60",
            {"field": coord_field, "got": raw},
        )
    value = degrees + minutes / 60.0
    if value > max_abs:
        raise NmeaError(
            NmeaErrorReason.OUT_OF_RANGE,
            "coordinate degrees out of range",
            {"field": coord_field, "got": raw},
        )
    if hemisphere in ("S", "W"):
        value = -value
    return value


def _parse_fix_quality(raw: str) -> GnssFixQuality:
    mapping = {
        "0": GnssFixQuality.INVALID,
        "1": GnssFixQuality.GPS_FIX,
        "2": GnssFixQuality.DGPS,
        "4": GnssFixQuality.RTK_FIXED,
        "5": GnssFixQuality.RTK_FLOAT,
        "6": GnssFixQuality.ESTIMATED,
        "8": GnssFixQuality.SIMULATED,
    }
    if raw in mapping:
        return mapping[raw]
    if raw in ("3", "7"):
        raise NmeaError(
            NmeaErrorReason.UNSUPPORTED_FIX_QUALITY,
            "GGA fix quality has no GnssFixQuality member",
            {"field": "fix_quality", "got": raw},
        )
    raise NmeaError(
        NmeaErrorReason.OUT_OF_RANGE,
        "GGA fix quality out of range",
        {"field": "fix_quality", "got": raw},
    )


def _parse_optional_int(
    raw: str, low: int, high: int, field: str
) -> int | None:
    if raw == "":
        return None
    if not raw.isdigit():
        raise NmeaError(
            NmeaErrorReason.MALFORMED_FIELD,
            "integer field must be digits",
            {"field": field, "got": raw},
        )
    value = int(raw)
    if not low <= value <= high:
        raise NmeaError(
            NmeaErrorReason.OUT_OF_RANGE,
            "integer field out of range",
            {"field": field, "got": raw},
        )
    return value


def _parse_optional_float(
    raw: str,
    low: float | None,
    high: float | None,
    field: str,
    *,
    high_inclusive: bool = False,
    signed: bool = False,
) -> float | None:
    if raw == "":
        return None
    pattern = r"-?\d+(\.\d+)?" if signed else r"\d+(\.\d+)?"
    if not re.fullmatch(pattern, raw):
        raise NmeaError(
            NmeaErrorReason.MALFORMED_FIELD,
            "numeric field is malformed",
            {"field": field, "got": raw},
        )
    value = float(raw)
    if low is not None and value < low:
        raise NmeaError(
            NmeaErrorReason.OUT_OF_RANGE,
            "numeric field below the allowed minimum",
            {"field": field, "got": raw},
        )
    if high is not None and (
        value > high or (value == high and not high_inclusive)
    ):
        raise NmeaError(
            NmeaErrorReason.OUT_OF_RANGE,
            "numeric field above the allowed maximum",
            {"field": field, "got": raw},
        )
    return value


def _parse_date(raw: str) -> date | None:
    if raw == "":
        return None
    if not _DATE_RE.fullmatch(raw):
        raise NmeaError(
            NmeaErrorReason.MALFORMED_FIELD,
            "RMC date must be ddmmyy",
            {"field": "date", "got": raw},
        )
    day = int(raw[0:2])
    month = int(raw[2:4])
    two_digit_year = int(raw[4:6])
    # NMEA 0183 convention: 80-99 -> 19xx, 00-79 -> 20xx.
    year = 1900 + two_digit_year if two_digit_year >= 80 else 2000 + two_digit_year
    try:
        return date(year, month, day)
    except ValueError:
        raise NmeaError(
            NmeaErrorReason.OUT_OF_RANGE,
            "RMC date is not a valid calendar date",
            {"field": "date", "got": raw},
        ) from None


def _require_metres_units(raw: str, field: str) -> None:
    if raw not in ("", "M"):
        raise NmeaError(
            NmeaErrorReason.MALFORMED_FIELD,
            "units must be M (metres)",
            {"field": field, "got": raw},
        )


def _rmc_speed_mps(rmc: RmcResult | None) -> float | None:
    if rmc is None or not rmc.status_valid:
        return None
    return rmc.ground_speed_mps


def _rmc_course_deg(rmc: RmcResult | None) -> float | None:
    if rmc is None or not rmc.status_valid:
        return None
    return rmc.course_deg
