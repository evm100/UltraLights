"""Things that happen to a home's outputs on someone's behalf: the room toggle and the
actions a schedule runs (contracts/schemas/schedule). Shared by the API and the internal
fire endpoint the scheduler calls, so a scheduled "room off" behaves exactly like the button.
"""
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import contracts
from .access import CONTROL, LIGHT_KINDS, Access
from .bridge import NotConnected
from .control import Control, ControlError
from .models import Home, Node, Output, Room, Scene, Schedule
from .scenes import NOT_IN_SCENES, apply as apply_scene
from .views import target


class ActionError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


async def room_power(db: AsyncSession, control: Control, home_id: int, room_id: int, on: bool,
                     access: Access | None, src: str) -> dict[str, Any]:
    """Every light in the room (never relays or valves) the caller can control.
    Raises NotConnected if the broker is down."""
    rows = (await db.execute(select(Node, Output).join(Output, Output.node_id == Node.id)
                             .where(Output.room_id == room_id, Node.home_id == home_id))).all()
    done, skipped = [], []
    for n, o in rows:
        if o.kind not in LIGHT_KINDS:
            continue
        if access is not None and CONTROL not in access.output_can(target(n, o)):
            continue
        try:
            await control.set(n.device_id, o.key, {"on": on}, src)
            done.append(f"{n.device_id}/{o.key}")
        except ControlError as e:
            skipped.append({"out": f"{n.device_id}/{o.key}", "reason": e.code})
    return {"set": done, "skipped": skipped}


# ---------------------------------------------------------------- schedules
def home_zone(home: Home) -> ZoneInfo | None:
    try:
        return ZoneInfo(home.timezone) if home.timezone else None
    except ZoneInfoNotFoundError:
        return None


async def check_schedule(db: AsyncSession, control: Control, home: Home, doc: dict[str, Any]) -> dict[str, Any]:
    """The contract plus what it can't say: targets belong to this home and fit the action,
    the home has a time zone, and a location if the schedule uses the sun."""
    try:
        contracts.validate("schedule", doc)
    except contracts.ContractError as e:
        raise ActionError([e.message]) from None
    errors: list[str] = []
    if home_zone(home) is None:
        errors.append("set the home's time zone in settings first")
    uses_sun = "sun" in doc["at"] or "sun" in doc.get("until", {})
    if uses_sun and (home.latitude is None or home.longitude is None):
        errors.append("sunrise and sunset need the home's location (settings)")
    do = doc["do"]
    if "scene" in do:
        s = await db.get(Scene, do["scene"])
        if s is None or s.home_id != home.id:
            errors.append("do.scene: not a scene of this home")
    elif "room" in do:
        r = await db.get(Room, do["room"])
        if r is None or r.home_id != home.id:
            errors.append("do.room: not a room of this home")
    else:
        info = control.outputs.get((do["dev"], do["out"]))
        if info is None or info.home_id != home.id:
            errors.append(f"do: {do['dev']}/{do['out']} is not an output of this home")
        elif "run_ms" in do and info.kind != "solenoid":
            errors.append("do.run_ms: only a valve runs for a time")
        elif "on" in do and info.kind in NOT_IN_SCENES:
            errors.append(f"do.on: a {info.kind} can't be switched on or off" + (" (give it run_ms)" if info.kind == "solenoid" else ""))
        if "run_ms" in do and "until" in doc:
            errors.append("until: a valve closes on its own; leave until out")
    if errors:
        raise ActionError(errors)
    return doc


async def run_schedule(db: AsyncSession, control: Control, sched: Schedule, phase: str) -> dict[str, Any]:
    """Do the schedule's action (phase start) or switch its targets off again (phase until),
    as the home itself: limited to the home, not to whoever created it."""
    do, src = sched.doc["do"], f"sched:{sched.id}"
    try:
        if "scene" in do:
            scene = await db.get(Scene, do["scene"])
            if scene is None or scene.home_id != sched.home_id:
                return {"error": "scene_gone"}
            if phase == "start":
                return await apply_scene(db, control, scene, None, src)
            done = []
            for it in scene.doc["items"]:
                info = control.outputs.get((it["dev"], it["out"]))
                if info is not None and info.home_id == sched.home_id:
                    await control.set(it["dev"], it["out"], {"on": False}, src)
                    done.append(f"{it['dev']}/{it['out']}")
            return {"set": done}
        if "room" in do:
            return await room_power(db, control, sched.home_id, do["room"], do["on"] if phase == "start" else False, None, src)
        info = control.outputs.get((do["dev"], do["out"]))
        if info is None or info.home_id != sched.home_id:
            return {"error": "output_gone"}
        if "run_ms" in do:
            if phase != "start":
                return {"set": []}
            limit = info.meta.get("max_on_ms")
            ms = min(do["run_ms"], limit) if isinstance(limit, int) else do["run_ms"]
            r = await control.cmd(do["dev"], "pulse", {"out": do["out"], "ms": ms}, src, wait=5)
            return {"pulse_ms": ms, "result": r.get("result")}
        await control.set(do["dev"], do["out"], {"on": do["on"] if phase == "start" else False}, src)
        return {"set": [f"{do['dev']}/{do['out']}"]}
    except NotConnected:
        return {"error": "broker_offline"}
    except ControlError as e:
        return {"error": e.code}
