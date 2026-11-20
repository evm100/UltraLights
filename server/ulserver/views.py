"""Read models shared by REST, the WebSocket and the bridge, plus who may see what."""
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import HomeMember, Node, Output, User


async def visible_home_ids(db: AsyncSession, user: User) -> set[int] | None:
    """None means everything (admin)."""
    if user.role == "admin":
        return None
    rows = await db.execute(select(HomeMember.home_id).where(HomeMember.user_id == user.id))
    return {r[0] for r in rows}


def can_see(node: Node, homes: set[int] | None) -> bool:
    return homes is None or (node.home_id is not None and node.home_id in homes)


def output_view(o: Output) -> dict[str, Any]:
    return {
        "key": o.key,
        "kind": o.kind,
        "name": o.name or o.key,
        "caps": o.caps or [],
        "room_id": o.room_id,
        "meta": o.meta or {},
        "state": o.actual,
        "desired": o.desired,
    }


def device_view(n: Node, outputs: list[Output], admin: bool) -> dict[str, Any]:
    v: dict[str, Any] = {
        "id": n.device_id,
        "name": n.name,
        "chip": n.chip,
        "home_id": n.home_id,
        "online": n.online,
        "outputs": [output_view(o) for o in sorted(outputs, key=lambda o: o.key)],
    }
    if admin:
        v["admin"] = {
            "board": n.board.get("board"),
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


async def devices_for(db: AsyncSession, user: User) -> list[dict[str, Any]]:
    homes = await visible_home_ids(db, user)
    nodes = (await db.execute(select(Node).order_by(Node.name))).scalars().all()
    nodes = [n for n in nodes if can_see(n, homes)]
    if not nodes:
        return []
    outs = (await db.execute(select(Output).where(Output.node_id.in_([n.id for n in nodes])))).scalars().all()
    by: dict[int, list[Output]] = {}
    for o in outs:
        by.setdefault(o.node_id, []).append(o)
    return [device_view(n, by.get(n.id, []), user.role == "admin") for n in nodes]
