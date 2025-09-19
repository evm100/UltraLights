"""Persistence helpers for custom room presets."""

from __future__ import annotations

import json
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional


Preset = Dict[str, Any]
PresetList = List[Preset]
PresetMap = Dict[str, Dict[str, PresetList]]
ActionList = List[Dict[str, Any]]


class CustomPresetStore:
    """JSON-backed storage for per-room custom presets."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._data: PresetMap = self._load()

    # ------------------------------------------------------------------
    # Internal helpers
    def _load(self) -> PresetMap:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text())
        except Exception:
            return {}

        if not isinstance(payload, dict):
            return {}

        data: PresetMap = {}
        for raw_house_id, rooms in payload.items():
            if not isinstance(rooms, dict):
                continue
            house_id = str(raw_house_id)
            clean_rooms: Dict[str, PresetList] = {}
            for raw_room_id, presets in rooms.items():
                clean = self._normalize_presets(presets)
                if clean:
                    clean_rooms[str(raw_room_id)] = clean
            if clean_rooms:
                data[house_id] = clean_rooms
        return data

    def _normalize_presets(self, presets: Any) -> PresetList:
        clean: PresetList = []
        if not isinstance(presets, list):
            return clean
        for preset in presets:
            clean_preset = self._normalize_preset(preset)
            if clean_preset is not None:
                clean.append(clean_preset)
        return clean

    def _normalize_preset(self, preset: Any) -> Optional[Preset]:
        if not isinstance(preset, dict):
            return None
        preset_id = preset.get("id")
        if preset_id is None:
            return None
        preset_id_str = str(preset_id)
        if not preset_id_str:
            return None

        name_value = preset.get("name")
        name = str(name_value) if name_value is not None else preset_id_str
        if not name:
            name = preset_id_str

        raw_actions = preset.get("actions")
        actions: ActionList = []
        if isinstance(raw_actions, list):
            for action in raw_actions:
                if isinstance(action, dict):
                    actions.append(deepcopy(action))

        clean: Dict[str, Any] = {
            key: deepcopy(value)
            for key, value in preset.items()
            if key != "actions"
        }
        clean["id"] = preset_id_str
        clean["name"] = name
        clean["actions"] = actions
        return clean

    def _persist(self) -> None:
        payload = json.dumps(self._data, indent=2)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(payload)
        tmp_path.replace(self.path)

    # ------------------------------------------------------------------
    # Public API
    def list_presets(self, house_id: str, room_id: str) -> PresetList:
        with self._lock:
            house = self._data.get(str(house_id))
            if not house:
                return []
            presets = house.get(str(room_id))
            if presets is None:
                return []
            return [deepcopy(preset) for preset in presets]

    def save_preset(self, house_id: str, room_id: str, preset: Dict[str, Any]) -> Dict[str, Any]:
        clean = self._normalize_preset(preset)
        if clean is None:
            raise ValueError("preset must be a dictionary with an 'id'")

        with self._lock:
            house = self._data.setdefault(str(house_id), {})
            room_presets = house.setdefault(str(room_id), [])
            preset_id = clean["id"]
            for index, existing in enumerate(room_presets):
                if existing.get("id") == preset_id:
                    room_presets[index] = deepcopy(clean)
                    self._persist()
                    return deepcopy(clean)
            room_presets.append(deepcopy(clean))
            self._persist()
        return deepcopy(clean)

    def delete_preset(self, house_id: str, room_id: str, preset_id: str) -> bool:
        with self._lock:
            house = self._data.get(str(house_id))
            if not house:
                return False
            room_presets = house.get(str(room_id))
            if not room_presets:
                return False
            for index, preset in enumerate(room_presets):
                if preset.get("id") == preset_id:
                    room_presets.pop(index)
                    if not room_presets:
                        house.pop(str(room_id), None)
                        if not house:
                            self._data.pop(str(house_id), None)
                    self._persist()
                    return True
            return False


__all__ = ["CustomPresetStore"]

