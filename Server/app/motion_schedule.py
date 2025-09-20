from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import settings


class MotionScheduleStore:
    """Persistence helper for per-room motion presets."""

    def __init__(self, path: Path, slot_minutes: int = 60) -> None:
        self.path = path
        self.slot_minutes = max(1, int(slot_minutes))
        self._lock = threading.RLock()
        self._data, self._colors = self._load()

    @property
    def slot_count(self) -> int:
        """Return the number of slots in a 24 hour day."""
        return max(1, (1440 + self.slot_minutes - 1) // self.slot_minutes)

    def _load(
        self,
    ) -> Tuple[
        Dict[str, Dict[str, List[Optional[str]]]],
        Dict[str, Dict[str, Dict[str, str]]],
    ]:
        if not self.path.exists():
            return {}, {}
        try:
            payload = json.loads(self.path.read_text())
        except Exception:
            return {}, {}

        if isinstance(payload, dict):
            slot_minutes = payload.get("slot_minutes")
            if isinstance(slot_minutes, int) and slot_minutes > 0:
                self.slot_minutes = slot_minutes
            schedules = payload.get("schedules", {})
            raw_colors = payload.get("preset_colors", {})
        else:
            schedules = payload
            raw_colors = {}

        data: Dict[str, Dict[str, List[Optional[str]]]] = {}
        if isinstance(schedules, dict):
            for house_id, rooms in schedules.items():
                if not isinstance(rooms, dict):
                    continue
                clean_rooms: Dict[str, List[Optional[str]]] = {}
                for room_id, schedule in rooms.items():
                    clean = self._normalize(schedule)
                    if clean is not None:
                        clean_rooms[str(room_id)] = clean
                if clean_rooms:
                    data[str(house_id)] = clean_rooms

        colors = self._normalize_colors(raw_colors)
        return data, colors

    def _normalize(self, schedule: Any) -> Optional[List[Optional[str]]]:
        if not isinstance(schedule, list):
            return None
        clean: List[Optional[str]] = [None] * self.slot_count
        for idx, value in enumerate(schedule[: self.slot_count]):
            if value in (None, "", "none"):
                clean[idx] = None
            else:
                clean[idx] = str(value)
        return clean

    def _normalize_color(self, color: Any) -> Optional[str]:
        if not isinstance(color, str):
            return None
        text = color.strip()
        if not text:
            return None
        if not text.startswith("#"):
            text = f"#{text}"
        hex_part = text[1:]
        if len(hex_part) == 3 and all(ch in "0123456789abcdefABCDEF" for ch in hex_part):
            hex_part = "".join(ch * 2 for ch in hex_part)
        if len(hex_part) != 6:
            return None
        if not all(ch in "0123456789abcdefABCDEF" for ch in hex_part):
            return None
        try:
            int(hex_part, 16)
        except ValueError:
            return None
        return f"#{hex_part.upper()}"

    def _normalize_colors(
        self, raw_colors: Any
    ) -> Dict[str, Dict[str, Dict[str, str]]]:
        cleaned: Dict[str, Dict[str, Dict[str, str]]] = {}
        if not isinstance(raw_colors, dict):
            return cleaned
        for house_id, rooms in raw_colors.items():
            if not isinstance(rooms, dict):
                continue
            house_key = str(house_id)
            house_entry: Dict[str, Dict[str, str]] = {}
            for room_id, presets in rooms.items():
                if not isinstance(presets, dict):
                    continue
                room_key = str(room_id)
                room_colors: Dict[str, str] = {}
                for preset_id, color in presets.items():
                    normalized = self._normalize_color(color)
                    if normalized:
                        room_colors[str(preset_id)] = normalized
                if room_colors:
                    house_entry[room_key] = room_colors
            if house_entry:
                cleaned[house_key] = house_entry
        return cleaned

    def _serialize_colors(self) -> Dict[str, Dict[str, Dict[str, str]]]:
        serialized: Dict[str, Dict[str, Dict[str, str]]] = {}
        for house_id, rooms in self._colors.items():
            house_entry: Dict[str, Dict[str, str]] = {}
            for room_id, presets in rooms.items():
                if not presets:
                    continue
                house_entry[room_id] = dict(sorted(presets.items()))
            if house_entry:
                serialized[house_id] = house_entry
        return serialized

    def save(self) -> None:
        payload = {
            "slot_minutes": self.slot_minutes,
            "schedules": self._data,
            "preset_colors": self._serialize_colors(),
        }
        serialized = json.dumps(payload, indent=2)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.write_text(serialized)
            tmp_path.replace(self.path)

    def get_schedule(
        self, house_id: str, room_id: str
    ) -> Optional[List[Optional[str]]]:
        with self._lock:
            house = self._data.get(str(house_id))
            if not house:
                return None
            schedule = house.get(str(room_id))
            if schedule is None:
                return None
            return list(schedule)

    def get_schedule_or_default(
        self, house_id: str, room_id: str, default: Optional[str] = None
    ) -> List[Optional[str]]:
        schedule = self.get_schedule(house_id, room_id)
        if schedule is None:
            if default:
                return [default for _ in range(self.slot_count)]
            return [None for _ in range(self.slot_count)]
        return schedule

    def get_room_colors(self, house_id: str, room_id: str) -> Dict[str, str]:
        with self._lock:
            rooms = self._colors.get(str(house_id))
            if not rooms:
                return {}
            presets = rooms.get(str(room_id))
            if not presets:
                return {}
            return dict(presets)

    def set_schedule(
        self, house_id: str, room_id: str, schedule: List[Optional[str]]
    ) -> List[Optional[str]]:
        clean = self._normalize(schedule)
        if clean is None:
            raise ValueError("schedule must be a list")
        with self._lock:
            house = self._data.setdefault(str(house_id), {})
            house[str(room_id)] = clean
            self.save()
            return list(clean)

    def set_preset_color(
        self,
        house_id: str,
        room_id: str,
        preset_id: str,
        color: Optional[str],
    ) -> Optional[str]:
        preset_key = str(preset_id).strip()
        if not preset_key:
            raise ValueError("preset_id must be a non-empty string")
        if color is None or (isinstance(color, str) and not color.strip()):
            with self._lock:
                rooms = self._colors.get(str(house_id))
                if not rooms:
                    return None
                presets = rooms.get(str(room_id))
                if not presets or preset_key not in presets:
                    return None
                presets.pop(preset_key, None)
                if not presets:
                    rooms.pop(str(room_id), None)
                if not rooms:
                    self._colors.pop(str(house_id), None)
                self.save()
                return None
        normalized = self._normalize_color(color)
        if not normalized:
            raise ValueError("color must be a valid hex value")
        with self._lock:
            rooms = self._colors.setdefault(str(house_id), {})
            presets = rooms.setdefault(str(room_id), {})
            presets[preset_key] = normalized
            self.save()
            return normalized

    def remove_room(self, house_id: str, room_id: str) -> None:
        """Forget any stored schedule for ``house_id``/``room_id``."""

        with self._lock:
            house = self._data.get(str(house_id))
            if not house:
                return
            removed = house.pop(str(room_id), None)
            if removed is None:
                return
            colors = self._colors.get(str(house_id))
            if colors:
                colors.pop(str(room_id), None)
                if not colors:
                    self._colors.pop(str(house_id), None)
            if not house:
                self._data.pop(str(house_id), None)
            self.save()

    def active_preset(
        self,
        house_id: str,
        room_id: str,
        default: Optional[str] = None,
        when: Optional[datetime] = None,
    ) -> Optional[str]:
        if when is None:
            when = datetime.now()
        total_minutes = when.hour * 60 + when.minute
        idx = (total_minutes // self.slot_minutes) % self.slot_count
        schedule = self.get_schedule(house_id, room_id)
        if schedule is None:
            return default
        preset = schedule[idx]
        if not preset:
            return None
        return preset


motion_schedule = MotionScheduleStore(settings.MOTION_SCHEDULE_FILE)
