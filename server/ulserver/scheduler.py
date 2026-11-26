"""ultralights-scheduler: decides when schedules run; the API does the work.

Every TICK seconds it reads the enabled schedules, finds events that fell due in the last
GRACE (and haven't fired), and asks the API to fire each one over loopback with the token
the API wrote at start-up. The API marks an event fired before acting, so retries and
restarts never repeat one; anything missed by more than GRACE (server down) is skipped.
"""
import asyncio
import datetime as dt
import json
import logging
import signal
import urllib.error
import urllib.request
from typing import Any

from sqlalchemy import select

from . import db as dbm
from .actions import home_zone
from .api.schedules import INTERNAL_HEADER, TOKEN_FILE
from .config import get_settings
from .models import Home, Schedule
from .schedule_times import occurrences

log = logging.getLogger("ul.scheduler")
UTC = dt.timezone.utc
TICK = 15
GRACE = dt.timedelta(minutes=5)


def due(schedules: list[Schedule], homes: dict[int, Home], now: dt.datetime) -> list[tuple[int, str, dt.datetime]]:
    """(schedule id, phase, event time) for every event in (now - GRACE, now] not yet fired."""
    out = []
    for s in schedules:
        h = homes.get(s.home_id)
        tz = home_zone(h) if h else None
        if not s.enabled or tz is None:
            continue
        for at, phase in occurrences(s.doc, tz, h.latitude, h.longitude, now - GRACE, now):
            last = s.last_start if phase == "start" else s.last_until
            if last is None or at > last:
                out.append((s.id, phase, at))
    return sorted(out, key=lambda e: e[2])


def _post(url: str, token: str, body: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json", INTERNAL_HEADER: token})
    with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310 - loopback URL from settings
        return json.loads(r.read())


async def tick(sm, api_url: str, token_path) -> None:
    now = dt.datetime.now(UTC)
    async with sm() as db:
        schedules = list((await db.execute(select(Schedule).where(Schedule.enabled.is_(True)))).scalars())
        homes = {h.id: h for h in (await db.execute(select(Home).where(Home.id.in_({s.home_id for s in schedules})))).scalars()}
    events = due(schedules, homes, now)
    if not events:
        return
    token = token_path.read_text().strip()
    for sid, phase, at in events:
        try:
            r = await asyncio.to_thread(_post, f"{api_url}/api/internal/schedules/{sid}/fire", token,
                                        {"phase": phase, "at": at.isoformat()})
            log.info("schedule %s %s at %s: %s", sid, phase, at.isoformat(), r.get("result", r.get("reason")))
        except (urllib.error.URLError, OSError, ValueError) as e:
            log.warning("schedule %s %s: API unreachable (%s); will retry while within grace", sid, phase, e)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    s = get_settings()
    sm = dbm.sessionmaker()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    log.info("scheduler started (API %s)", s.api_url)
    while not stop.is_set():
        try:
            await tick(sm, s.api_url, s.data_dir / TOKEN_FILE)
        except Exception:  # noqa: BLE001 - keep the service alive; the next tick tries again
            log.exception("tick failed")
        try:
            await asyncio.wait_for(stop.wait(), TICK)
        except asyncio.TimeoutError:
            pass
    await dbm.dispose()
    log.info("scheduler stopped")


if __name__ == "__main__":
    asyncio.run(main())
