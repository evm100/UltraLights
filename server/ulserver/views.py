"""Read models shared by REST, the WebSocket and the bridge, filtered through access."""
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .access import OUTPUT_ABILITIES, Access, Out
from .models import Node, Output


def output_view(o: Output, can: frozenset[str] = frozenset(OUTPUT_ABILITIES)) -> dict[str, Any]:
    return {
        "key": o.key,
        "kind": o.kind,
        "name": o.name or o.key,
        "caps": o.caps or [],
        "room_id": o.room_id,
        "meta": o.meta or {},
        "state": o.actual,
        "desired": o.desired,
        "can": [a for a in OUTPUT_ABILITIES if a in can],
    }


def _output_order(n: Node):
    """Nodes: by key. Bridges: the order the agent lists its parts in (the UI's part order)."""
    if n.kind == "bridge":
        pos = {od["key"]: i for i, od in enumerate((n.info or {}).get("outputs", []))}
        return lambda o: (pos.get(o.key, len(pos)), o.key)
    return lambda o: o.key


def target(n: Node, o: Output) -> Out:
    return Out(home_id=n.home_id, kind=o.kind, dev=n.device_id, room_id=o.room_id)


def device_view(n: Node, outputs: list[Output], access: Access) -> dict[str, Any] | None:
    """The device as this person may see it: only outputs they can view, each with what they
    can do to it. None when they can see none of it. Owners, members and admins see every
    device in their homes, even one that hasn't reported outputs yet."""
    shown = [(o, access.output_can(target(n, o))) for o in sorted(outputs, key=_output_order(n))]
    shown = [(o, can) for o, can in shown if "view" in can]
    if not shown and not access.sees_home(n.home_id):
        return None
    v: dict[str, Any] = {
        "id": n.device_id,
        "name": n.name,
        "chip": n.chip,
        "kind": n.kind,
        "combined": n.kind == "bridge" and bool((n.info or {}).get("combine")),
        "home_id": n.home_id,
        "online": n.online,
        "outputs": [output_view(o, can) for o, can in shown],
    }
    if access.admin:
        v["admin"] = {
            "board": (n.board or {}).get("board"),
            "bridge": n.bridge,
            "host": (n.info or {}).get("host"),
            "sources": (n.info or {}).get("sources", []),
            "fw": (n.info or {}).get("fw"),
            "profile": (n.info or {}).get("profile"),
            "board_crc": n.board_crc,
            "running_crc": (n.info or {}).get("board_crc"),
            "config_rev": n.config_rev,
            "running_config_rev": (n.info or {}).get("config_rev"),
            "config_error": (n.info or {}).get("config_error"),
            "enrolled": n.enrolled_at is not None,
            "last_seen": n.last_seen.isoformat() if n.last_seen else None,
            "health": n.health,
        }
    return v


async def devices_for(db: AsyncSession, access: Access) -> list[dict[str, Any]]:
    homes = access.home_ids()
    q = select(Node).order_by(Node.name)
    if homes is not None:
        q = q.where(Node.home_id.in_(homes))
    nodes = (await db.execute(q)).scalars().all()
    if not nodes:
        return []
    outs = (await db.execute(select(Output).where(Output.node_id.in_([n.id for n in nodes])))).scalars().all()
    by: dict[int, list[Output]] = {}
    for o in outs:
        by.setdefault(o.node_id, []).append(o)
    views = (device_view(n, by.get(n.id, []), access) for n in nodes)
    return [v for v in views if v is not None]
