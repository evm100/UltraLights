"""Persistence helpers for motion automation preferences.

The :class:`MotionPreferencesStore` keeps track of per-room node immunity
settings for motion automation.  Immunity is represented as the set of node IDs
that should be ignored whenever a motion preset is applied.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Dict, Iterable, Set

from .config import settings


class MotionPreferencesStore:
    """Persist motion automation preferences to disk."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._data = self._load()

    def _load(self) -> Dict[str, Dict[str, Set[str]]]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text())
        except Exception:
            return {}

        data: Dict[str, Dict[str, Set[str]]] = {}
        if not isinstance(raw, dict):
            return data

        for house_id, rooms in raw.items():
            if not isinstance(rooms, dict):
                continue
            house_key = str(house_id)
            house_entry: Dict[str, Set[str]] = {}
            for room_id, nodes in rooms.items():
                node_set: Set[str] = set()
                if isinstance(nodes, list):
                    for node in nodes:
                        node_text = str(node).strip()
                        if node_text:
                            node_set.add(node_text)
                elif isinstance(nodes, set):
                    for node in nodes:
                        node_text = str(node).strip()
                        if node_text:
                            node_set.add(node_text)
                if node_set:
                    house_entry[str(room_id)] = node_set
            if house_entry:
                data[house_key] = house_entry
        return data

    def _serialize(self) -> Dict[str, Dict[str, list[str]]]:
        serialized: Dict[str, Dict[str, list[str]]] = {}
        for house_id, rooms in self._data.items():
            clean_rooms: Dict[str, list[str]] = {}
            for room_id, nodes in rooms.items():
                if not nodes:
                    continue
                clean_rooms[room_id] = sorted(nodes)
            if clean_rooms:
                serialized[house_id] = clean_rooms
        return serialized

    def _save_locked(self) -> None:
        payload = json.dumps(self._serialize(), indent=2)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(payload)
        tmp_path.replace(self.path)

    def save(self) -> None:
        with self._lock:
            self._save_locked()

    def get_room_immune_nodes(self, house_id: str, room_id: str) -> Set[str]:
        house_key = str(house_id)
        room_key = str(room_id)
        with self._lock:
            rooms = self._data.get(house_key)
            if not rooms:
                return set()
            nodes = rooms.get(room_key)
            if not nodes:
                return set()
            return set(nodes)

    def set_room_immune_nodes(
        self, house_id: str, room_id: str, nodes: Iterable[str]
    ) -> Set[str]:
        house_key = str(house_id)
        room_key = str(room_id)
        clean: Set[str] = set()
        for node in nodes:
            node_text = str(node).strip()
            if node_text:
                clean.add(node_text)

        with self._lock:
            if not clean:
                rooms = self._data.get(house_key)
                if rooms and room_key in rooms:
                    rooms.pop(room_key, None)
                    if not rooms:
                        self._data.pop(house_key, None)
                    self._save_locked()
                return set()

            rooms = self._data.setdefault(house_key, {})
            rooms[room_key] = set(clean)
            self._save_locked()
            return set(clean)

    def add_room_immune_node(
        self, house_id: str, room_id: str, node_id: str
    ) -> Set[str]:
        node_key = str(node_id).strip()
        if not node_key:
            return self.get_room_immune_nodes(house_id, room_id)
        with self._lock:
            rooms = self._data.setdefault(str(house_id), {})
            nodes = rooms.setdefault(str(room_id), set())
            nodes.add(node_key)
            self._save_locked()
            return set(nodes)

    def remove_room_immune_node(
        self, house_id: str, room_id: str, node_id: str
    ) -> Set[str]:
        node_key = str(node_id).strip()
        with self._lock:
            rooms = self._data.get(str(house_id))
            if not rooms:
                return set()
            nodes = rooms.get(str(room_id))
            if not nodes or node_key not in nodes:
                return set(nodes or set())
            nodes.discard(node_key)
            if not nodes:
                rooms.pop(str(room_id), None)
            if not rooms:
                self._data.pop(str(house_id), None)
            self._save_locked()
            return set(nodes)

    def remove_node(self, node_id: str) -> None:
        node_key = str(node_id).strip()
        if not node_key:
            return
        changed = False
        with self._lock:
            for house_id in list(self._data.keys()):
                rooms = self._data[house_id]
                for room_id in list(rooms.keys()):
                    nodes = rooms[room_id]
                    if node_key in nodes:
                        nodes.discard(node_key)
                        changed = True
                    if not nodes:
                        rooms.pop(room_id, None)
                        changed = True
                if not rooms:
                    self._data.pop(house_id, None)
                    changed = True
            if changed:
                self._save_locked()


motion_preferences = MotionPreferencesStore(settings.MOTION_PREFS_FILE)

