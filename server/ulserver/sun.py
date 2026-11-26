"""Sunrise and sunset (the sunrise equation as NOAA gives it; about a minute of accuracy).
Pure: tested in tests/test_schedules.py."""
import datetime as dt
import math

UTC = dt.timezone.utc


def _jd_to_utc(jd: float) -> dt.datetime:
    return dt.datetime.fromtimestamp((jd - 2440587.5) * 86400, UTC)


def sun_times(day: dt.date, lat: float, lon: float) -> tuple[dt.datetime | None, dt.datetime | None]:
    """UTC sunrise and sunset around local solar noon of day at (lat, lon east-positive).
    (None, None) when the sun doesn't rise or doesn't set that day."""
    jd = day.toordinal() + 1721424.5  # Julian date at 00:00 UTC
    n = math.ceil(jd - 2451545.0 + 0.0008)  # days since J2000
    j_star = n - lon / 360.0
    m = math.radians((357.5291 + 0.98560028 * j_star) % 360)
    c = 1.9148 * math.sin(m) + 0.0200 * math.sin(2 * m) + 0.0003 * math.sin(3 * m)
    lam = math.radians((math.degrees(m) + c + 180 + 102.9372) % 360)
    transit = 2451545.0 + j_star + 0.0053 * math.sin(m) - 0.0069 * math.sin(2 * lam)
    decl = math.asin(math.sin(lam) * math.sin(math.radians(23.4397)))
    phi = math.radians(lat)
    cos_w = (math.sin(math.radians(-0.833)) - math.sin(phi) * math.sin(decl)) / (math.cos(phi) * math.cos(decl))
    if not -1.0 <= cos_w <= 1.0:
        return None, None
    w = math.degrees(math.acos(cos_w))
    return _jd_to_utc(transit - w / 360), _jd_to_utc(transit + w / 360)
