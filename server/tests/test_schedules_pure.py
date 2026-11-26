"""Sun times and schedule occurrences (pure)."""
import datetime as dt
from zoneinfo import ZoneInfo

from ulserver.schedule_times import next_event, occurrences, resolve
from ulserver.sun import sun_times

UTC = dt.timezone.utc


def near(a, b, minutes=3):
    return abs((a - b).total_seconds()) <= minutes * 60


def test_sun_times_known_values():
    rise, set_ = sun_times(dt.date(2024, 6, 21), 51.5074, -0.1278)  # London, midsummer
    assert near(rise, dt.datetime(2024, 6, 21, 3, 43, tzinfo=UTC)) and near(set_, dt.datetime(2024, 6, 21, 20, 21, tzinfo=UTC))
    rise, set_ = sun_times(dt.date(2024, 12, 21), 40.7128, -74.0060)  # New York, midwinter
    assert near(rise, dt.datetime(2024, 12, 21, 12, 16, tzinfo=UTC)) and near(set_, dt.datetime(2024, 12, 21, 21, 32, tzinfo=UTC))
    # Sydney, equinox (east of Greenwich, so sunrise falls on the previous UTC date). Reference:
    # the full NOAA spreadsheet formulas (equation of time, eccentricity), computed independently.
    rise, set_ = sun_times(dt.date(2024, 3, 20), -33.8688, 151.2093)
    assert near(rise, dt.datetime(2024, 3, 19, 19, 58, tzinfo=UTC)) and near(set_, dt.datetime(2024, 3, 20, 8, 6, tzinfo=UTC))
    assert sun_times(dt.date(2024, 6, 21), 69.6492, 18.9553) == (None, None)  # Tromsø: midnight sun


def test_clock_times_follow_dst():
    tz = ZoneInfo("America/Vancouver")
    w = {"time": "07:00"}
    assert resolve(w, dt.date(2024, 3, 9), tz, None, None).astimezone(UTC).hour == 15   # PST
    assert resolve(w, dt.date(2024, 3, 10), tz, None, None).astimezone(UTC).hour == 14  # PDT
    gap = resolve({"time": "02:30"}, dt.date(2024, 3, 10), tz, None, None)              # skipped hour
    assert gap.hour == 3 and gap.minute == 30


def test_occurrences_days_and_windows():
    tz = ZoneInfo("Europe/London")
    doc = {"v": 1, "name": "x", "days": [0, 2], "at": {"time": "22:00"}, "until": {"time": "01:30"}, "do": {"scene": 1}}
    start = dt.datetime(2024, 1, 1, 0, 0, tzinfo=tz)  # Monday
    ev = occurrences(doc, tz, None, None, start, start + dt.timedelta(days=7))
    got = [(e.strftime("%a %H:%M"), p) for e, p in ev]
    assert got == [("Mon 22:00", "start"), ("Tue 01:30", "until"), ("Wed 22:00", "start"), ("Thu 01:30", "until")]


def test_sun_needs_a_location_and_next_event():
    tz = ZoneInfo("Europe/London")
    doc = {"v": 1, "name": "x", "at": {"sun": "sunset", "offset_min": -15}, "do": {"scene": 1}}
    after = dt.datetime(2024, 6, 21, 12, 0, tzinfo=tz)
    assert next_event(doc, tz, None, None, after) is None
    when, phase = next_event(doc, tz, 51.5074, -0.1278, after)
    assert phase == "start" and near(when, dt.datetime(2024, 6, 21, 21, 6, tzinfo=tz))
