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
    node = {"id": slugify(name), "name": name, "kind": kind}
    node["modules"] = modules or ["ws", "rgb", "white", "sensor", "ota"]
    room.setdefault("nodes", []).append(node)
    save_registry()
    return node


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
