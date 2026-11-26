"""When a schedule (contracts/schemas/schedule) runs: its start and until events, in the
home's time zone. Pure: tested in tests/test_schedules.py."""
import datetime as dt
from typing import Any
from zoneinfo import ZoneInfo

from .sun import sun_times

UTC = dt.timezone.utc


def resolve(when: dict[str, Any], day: dt.date, tz: ZoneInfo, lat: float | None, lon: float | None) -> dt.datetime | None:
    """The moment `when` means on local date `day`, or None (no location, or no sunrise)."""
    if "time" in when:
        h, m = map(int, when["time"].split(":"))
        local = dt.datetime.combine(day, dt.time(h, m), tzinfo=tz)
        return local.astimezone(UTC).astimezone(tz)  # normalises a time skipped by a DST change
    if lat is None or lon is None:
        return None
    rise, set_ = sun_times(day, lat, lon)
    t = rise if when["sun"] == "sunrise" else set_
    if t is None:
        return None
    return (t + dt.timedelta(minutes=when.get("offset_min", 0))).astimezone(tz)


def occurrences(doc: dict[str, Any], tz: ZoneInfo, lat: float | None, lon: float | None,
                start: dt.datetime, end: dt.datetime) -> list[tuple[dt.datetime, str]]:
    """Events in (start, end], oldest first: (when, "start" | "until")."""
    out: list[tuple[dt.datetime, str]] = []
    days = set(doc.get("days") or range(7))
    d = start.astimezone(tz).date() - dt.timedelta(days=1)  # an until may belong to yesterday's start
    last = end.astimezone(tz).date()
    while d <= last:
        if d.weekday() in days:
            s = resolve(doc["at"], d, tz, lat, lon)
            if s is not None:
                if start < s <= end:
                    out.append((s, "start"))
                if "until" in doc:
                    u = resolve(doc["until"], d, tz, lat, lon)
                    if u is not None and u <= s:
                        u = resolve(doc["until"], d + dt.timedelta(days=1), tz, lat, lon)
                    if u is not None and start < u <= end:
                        out.append((u, "until"))
        d += dt.timedelta(days=1)
    return sorted(out)


def next_event(doc: dict[str, Any], tz: ZoneInfo, lat: float | None, lon: float | None,
               after: dt.datetime) -> tuple[dt.datetime, str] | None:
    ev = occurrences(doc, tz, lat, lon, after, after + dt.timedelta(days=8))
    return ev[0] if ev else None
