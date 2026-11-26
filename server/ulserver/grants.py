"""Guest grants (contracts/schemas/grant) as pure rules: which outputs, which abilities, when.

A grant is turned into a Rule when access is loaded. The rule records whether it was in
force at that moment (active), and keeps what it needs to tell when that changes, so the
hub can close a guest's socket as their window opens or closes (Access.stale).
"""
import datetime as dt
from dataclasses import dataclass, replace
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# cycle: lights with their own controller, stepped by a servo pressing its button.
LIGHT_KINDS = frozenset({"white", "cct", "rgb", "rgbw", "rgbcct", "argb", "cycle"})
GRANT_KIND = {**{k: "light" for k in LIGHT_KINDS}, "relay": "switch", "solenoid": "solenoid", "motion": "sensor"}
UTC = dt.timezone.utc


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def zone(name: str | None) -> dt.tzinfo:
    if not name:
        return UTC
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return UTC


@dataclass(frozen=True)
class Rule:
    whole_home: bool
    rooms: frozenset[int]
    devices: frozenset[str]
    can: frozenset[str]  # always includes view
    kinds: frozenset[str]  # light | switch | solenoid | sensor
    days: tuple[int, ...] | None = None  # window start days, None = every day
    start: int | None = None  # window minutes from midnight, None = no window
    end: int | None = None
    expires: dt.date | None = None
    tz: str | None = None
    active: bool = True

    def covers(self, kind: str, dev: str, room_id: int | None) -> bool:
        if GRANT_KIND.get(kind) not in self.kinds:
            return False
        return self.whole_home or dev in self.devices or (room_id is not None and room_id in self.rooms)

    def active_at(self, now: dt.datetime) -> bool:
        local = now.astimezone(zone(self.tz))
        if self.expires is not None and local.date() > self.expires:
            return False
        if self.start is None or self.end is None:
            return True
        days = set(self.days) if self.days else set(range(7))
        mins = local.hour * 60 + local.minute
        today = local.weekday()
        if self.start == self.end:  # the whole day
            return today in days
        if self.start < self.end:
            return today in days and self.start <= mins < self.end
        # Past midnight: today's evening part, or the morning part of yesterday's window.
        return (today in days and mins >= self.start) or ((today - 1) % 7 in days and mins < self.end)


def rule_from(doc: dict[str, Any], tz: str | None, now: dt.datetime) -> Rule:
    scope = doc.get("scope", [])
    w = doc.get("window")
    r = Rule(
        whole_home="home" in scope,
        rooms=frozenset(int(s[5:]) for s in scope if s.startswith("room:")),
        devices=frozenset(s[7:] for s in scope if s.startswith("device:")),
        can=frozenset({"view", *doc.get("can", [])}),
        kinds=frozenset(doc.get("kinds", [])),
        days=tuple(sorted(w["days"])) if w and w.get("days") else None,
        start=_minutes(w["from"]) if w else None,
        end=_minutes(w["to"]) if w else None,
        expires=dt.date.fromisoformat(doc["expires"]) if doc.get("expires") else None,
        tz=tz,
    )
    return replace(r, active=r.active_at(now))


def describe(doc: dict[str, Any], tz: str | None, now: dt.datetime) -> dict[str, Any]:
    """For the people list and the guest's own view of the home."""
    r = rule_from(doc, tz, now)
    expired = r.expires is not None and now.astimezone(zone(tz)).date() > r.expires
    return {"active": r.active, "expired": expired}
