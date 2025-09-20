"""Utility helpers for working with the device registry.

The registry is expected to be a list of houses, each containing rooms and
nodes, e.g.::

    [
        {
            "id": "del-sur",
            "name": "Del Sur",
            "rooms": [
                {
                    "id": "kitchen",
                    "name": "Kitchen",
                    "nodes": [
                        {"id": "del-sur-kitchen-node1", "name": "Kitchen Node", "kind": "rgb"}
                    ]
                }
            ]
        }
    ]

These helpers simplify finding houses, rooms and nodes in the registry.
"""
from __future__ import annotations
import json
import re

from typing import Any, Dict, Iterable, Iterator, Optional, Tuple
from .config import settings

Registry = list[Dict[str, Any]]
House = Dict[str, Any]
Room = Dict[str, Any]
Node = Dict[str, Any]


def slugify(text: str) -> str:
    """Return a URL-friendly identifier for ``text``."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")

def save_registry() -> None:
    """Persist the in-memory registry to ``REGISTRY_FILE``."""
    settings.REGISTRY_FILE.write_text(
        json.dumps(settings.DEVICE_REGISTRY, indent=2)
    )

def iter_nodes(registry: Optional[Registry] = None) -> Iterator[Tuple[House, Room, Node]]:
    """Yield (house, room, node) for every node in the registry."""
    if registry is None:
        registry = settings.DEVICE_REGISTRY
    for house in registry:
        for room in house.get("rooms", []):
            for node in room.get("nodes", []):
                yield house, room, node


def find_house(house_id: str) -> Optional[House]:
    for house in settings.DEVICE_REGISTRY:
        if house.get("id") == house_id:
            return house
    return None


def find_room(house_id: str, room_id: str) -> Tuple[Optional[House], Optional[Room]]:
    house = find_house(house_id)
    if not house:
        return None, None
    for room in house.get("rooms", []):
        if room.get("id") == room_id:
            return house, room
    return house, None


def find_node(node_id: str) -> Tuple[Optional[House], Optional[Room], Optional[Node]]:
    for house, room, node in iter_nodes():
        if node.get("id") == node_id:
            return house, room, node
    return None, None, None


def add_room(house_id: str, name: str) -> Room:
    """Create and attach a new room to ``house_id``."""
    house = find_house(house_id)
    if not house:
        raise KeyError("house not found")
    room = {"id": slugify(name), "name": name, "nodes": []}
    house.setdefault("rooms", []).append(room)
    save_registry()
    return room


def reorder_rooms(house_id: str, room_order: Iterable[str]) -> list[Room]:
    """Rearrange rooms under ``house_id`` to match ``room_order``."""

    house = find_house(house_id)
    if not house:
        raise KeyError("house not found")

    rooms = house.get("rooms")
    if not isinstance(rooms, list):
        raise KeyError("room not found")

    id_to_room: Dict[str, Room] = {}
    for entry in rooms:
        if not isinstance(entry, dict):
            raise ValueError("room entry must be a mapping")
        raw_id = entry.get("id")
        if raw_id is None:
            raise ValueError("room missing id")
        room_id = str(raw_id)
        if room_id in id_to_room:
            raise ValueError(f"duplicate room id: {room_id}")
        id_to_room[room_id] = entry

    normalized_order: list[Room] = []
    seen: set[str] = set()
    for raw_room_id in room_order:
        room_id = str(raw_room_id)
        if room_id in seen:
            raise ValueError(f"duplicate room id: {room_id}")
        seen.add(room_id)
        room = id_to_room.get(room_id)
        if room is None:
            raise ValueError(f"unknown room id: {room_id}")
        normalized_order.append(room)

    if len(normalized_order) != len(id_to_room):
        missing = sorted(room_id for room_id in id_to_room.keys() if room_id not in seen)
        raise ValueError(f"missing rooms: {', '.join(missing)}")

    house["rooms"] = normalized_order
    save_registry()
    return normalized_order


MAX_NODE_ID_LENGTH = 31


def add_node(
    house_id: str,
    room_id: str,
    name: str,
    kind: str = "ultranode",
    modules: Optional[list[str]] = None,
) -> Node:
    """Create and attach a new node under ``house_id``/``room_id``."""
    _, room = find_room(house_id, room_id)
    if not room:
        raise KeyError("room not found")

    node_slug = slugify(name)
    if not node_slug:
        raise ValueError("node name produces empty slug")

    house_slug = slugify(str(house_id))
    node_id = f"{house_slug}-{node_slug}" if house_slug else node_slug

    if len(node_id) > MAX_NODE_ID_LENGTH:
        raise ValueError(f"node id too long (max {MAX_NODE_ID_LENGTH} characters)")

    for _, _, existing in iter_nodes():
        existing_id = existing.get("id")
        if isinstance(existing_id, str) and existing_id == node_id:
            raise ValueError(f"node id already exists: {node_id}")

    node = {"id": node_id, "name": name, "kind": kind}
    node["modules"] = modules or ["ws", "rgb", "white", "ota"]
    room.setdefault("nodes", []).append(node)
    save_registry()
    return node


def set_node_name(node_id: str, name: str) -> Node:
    """Update the display name for ``node_id`` and persist the registry."""

    for house in settings.DEVICE_REGISTRY:
        rooms = house.get("rooms")
        if not isinstance(rooms, list):
            continue
        for room in rooms:
            if not isinstance(room, dict):
                continue
            nodes = room.get("nodes")
            if not isinstance(nodes, list):
                continue
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                if node.get("id") == node_id:
                    node["name"] = name
                    save_registry()
                    return node
    raise KeyError("node not found")


def remove_node(node_id: str) -> Node:
    """Remove the node identified by ``node_id`` from the registry."""
    for house in settings.DEVICE_REGISTRY:
        for room in house.get("rooms", []):
            nodes = room.get("nodes", [])
            for idx, node in enumerate(nodes):
                if node.get("id") == node_id:
                    removed = nodes.pop(idx)
                    save_registry()
                    return removed
    raise KeyError("node not found")


def remove_room(house_id: str, room_id: str) -> Room:
    """Remove ``room_id`` from ``house_id`` and return the removed room."""

    house = find_house(house_id)
    if not house:
        raise KeyError("house not found")

    rooms = house.get("rooms")
    if not isinstance(rooms, list):
        raise KeyError("room not found")

    for idx, room in enumerate(rooms):
        if room.get("id") == room_id:
            removed = rooms.pop(idx)
            save_registry()
            return removed

    raise KeyError("room not found")
