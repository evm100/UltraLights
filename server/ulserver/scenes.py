"""Scenes (contracts/schemas/scene): checking, capturing, applying and undoing.

One place for every caller: the API (a person tapping), the scheduler and, later, the AI
worker. A caller with an Access is limited to what that person may control; access None is
the system acting on an owner's behalf (a schedule), still limited to the scene's home.
"""
import datetime as dt
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import contracts
from .access import CONTROL, LIGHT_KINDS, Access, abilities_for
from .bridge import NotConnected
from .control import STATE_FIELDS, Control, ControlError
from .models import AuditLog, Node, Output, Room, Scene, SceneRun
from .views import target

NOT_IN_SCENES = frozenset({"solenoid", "motion"})  # valves only open for a set time; sensors are inputs
SCENE_FIELDS = ("on", "level", "cct", "color", "white", "effect")
FIELD_CAP = {"level": "level", "cct": "cct", "color": "color", "white": "white", "effect": "effect"}


class SceneError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


async def check(db: AsyncSession, control: Control, home_id: int, doc: dict[str, Any]) -> dict[str, Any]:
    """The contract plus what it can't say: items are outputs of this home, once each, and
    can be set; the room is in this home. Returns the doc; raises SceneError."""
    try:
        contracts.validate("scene", doc)
    except contracts.ContractError as e:
        raise SceneError([e.message]) from None
    errors: list[str] = []
    if doc.get("room_id") is not None:
        r = await db.get(Room, doc["room_id"])
        if r is None or r.home_id != home_id:
            errors.append("room: not a room of this home")
    seen: set[tuple[str, str]] = set()
    for i, it in enumerate(doc["items"]):
        k = (it["dev"], it["out"])
        if k in seen:
            errors.append(f"items[{i}]: {k[0]}/{k[1]} appears twice")
        seen.add(k)
        info = control.outputs.get(k)
        if info is None or info.home_id != home_id:
            errors.append(f"items[{i}]: {k[0]}/{k[1]} is not an output of this home")
        elif info.kind in NOT_IN_SCENES:
            errors.append(f"items[{i}]: a {info.kind} can't be in a scene")
    if errors:
        raise SceneError(errors)
    return doc


def _scene_state(state: dict[str, Any] | None, caps: list[str]) -> dict[str, Any] | None:
    if not state:
        return None
    s = {k: state[k] for k in SCENE_FIELDS if k in state and (k not in FIELD_CAP or FIELD_CAP[k] in caps)}
    if s.get("effect") is None:
        s.pop("effect", None)
    return s if "on" in s else None


async def capture(db: AsyncSession, home_id: int, room_id: int | None, access: Access | None) -> list[dict[str, Any]]:
    """The current look of the lights in a room (or the whole home) as scene items."""
    q = (select(Node, Output).join(Output, Output.node_id == Node.id)
         .where(Node.home_id == home_id).order_by(Node.name, Output.key))
    if room_id is not None:
        q = q.where(Output.room_id == room_id)
    items = []
    for n, o in (await db.execute(q)).all():
        if o.kind not in LIGHT_KINDS:
            continue
        if access is not None and not access.sees(target(n, o)):
            continue
        s = _scene_state(o.actual, o.caps or []) or _scene_state(o.desired, o.caps or [])
        if s is not None:
            items.append({"dev": n.device_id, "out": o.key, "state": s})
    return items


async def apply(db: AsyncSession, control: Control, scene: Scene, access: Access | None, src: str) -> dict[str, Any]:
    """Set every item the caller may control. Records a run with the previous states so it
    can be undone. Returns {run_id, set, skipped, broker_offline}."""
    doc = scene.doc
    done: list[str] = []
    skipped: list[dict[str, str]] = []
    previous: list[dict[str, Any]] = []
    offline = False
    for it in doc["items"]:
        key = f"{it['dev']}/{it['out']}"
        info = control.outputs.get((it["dev"], it["out"]))
        if info is None or info.home_id != scene.home_id:
            skipped.append({"out": key, "reason": "gone"})
            continue
        if access is not None:
            can = access.output_can(info)
            if not ({CONTROL} | abilities_for(it["state"])) <= can:
                skipped.append({"out": key, "reason": "not_allowed"})
                continue
        prev = {k: v for k, v in info.desired.items() if k in STATE_FIELDS}
        fields = dict(it["state"])
        if "fade_ms" in doc:
            fields["fade_ms"] = doc["fade_ms"]
        try:
            await control.set(it["dev"], it["out"], fields, f"scene:{scene.id}")
        except ControlError as e:
            skipped.append({"out": key, "reason": e.code})
            continue
        except NotConnected:
            offline = True
            break
        done.append(key)
        if prev:
            previous.append({"dev": it["dev"], "out": it["out"], "state": prev})
    run = SceneRun(scene_id=scene.id, home_id=scene.home_id, src=src, previous=previous)
    db.add(run)
    db.add(AuditLog(src=src, action="scene_applied", target=f"scene:{scene.id}",
                    detail={"set": len(done), "skipped": len(skipped), "broker_offline": offline}))
    await db.commit()
    return {"run_id": run.id, "set": done, "skipped": skipped, "broker_offline": offline}


async def undo(db: AsyncSession, control: Control, run: SceneRun, access: Access | None, src: str) -> dict[str, Any]:
    """Put the outputs back the way they were before the run. Once per run."""
    if run.undone_at is not None:
        raise SceneError(["this change was already undone"])
    done, skipped = [], []
    for p in run.previous:
        key = f"{p['dev']}/{p['out']}"
        info = control.outputs.get((p["dev"], p["out"]))
        if info is None or info.home_id != run.home_id:
            skipped.append({"out": key, "reason": "gone"})
            continue
        if access is not None and not ({CONTROL} | abilities_for(p["state"])) <= access.output_can(info):
            skipped.append({"out": key, "reason": "not_allowed"})
            continue
        try:
            await control.set(p["dev"], p["out"], p["state"], src)
            done.append(key)
        except ControlError as e:
            skipped.append({"out": key, "reason": e.code})
    run.undone_at = dt.datetime.now(dt.timezone.utc)
    db.add(AuditLog(src=src, action="scene_undone", target=f"run:{run.id}", detail={"set": len(done)}))
    await db.commit()
    return {"set": done, "skipped": skipped}
