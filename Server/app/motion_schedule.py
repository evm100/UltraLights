from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import settings


class MotionScheduleStore:
    """Persistence helper for per-room motion presets."""

    def __init__(self, path: Path, slot_minutes: int = 60) -> None:
        self.path = path
        self.slot_minutes = max(1, int(slot_minutes))
        self._data = self._load()

    @property
    def slot_count(self) -> int:
        """Return the number of slots in a 24 hour day."""
        return max(1, (1440 + self.slot_minutes - 1) // self.slot_minutes)

    def _load(self) -> Dict[str, Dict[str, List[Optional[str]]]]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text())
        except Exception:
            return {}

        if isinstance(payload, dict):
            slot_minutes = payload.get("slot_minutes")
            if isinstance(slot_minutes, int) and slot_minutes > 0:
                self.slot_minutes = slot_minutes
            schedules = payload.get("schedules", {})
        else:
            schedules = payload

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
        return data

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

    def save(self) -> None:
        payload = {"slot_minutes": self.slot_minutes, "schedules": self._data}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2))

    def get_schedule(
        self, house_id: str, room_id: str
    ) -> Optional[List[Optional[str]]]:
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

    def set_schedule(
        self, house_id: str, room_id: str, schedule: List[Optional[str]]
    ) -> List[Optional[str]]:
        clean = self._normalize(schedule)
        if clean is None:
            raise ValueError("schedule must be a list")
        house = self._data.setdefault(str(house_id), {})
        house[str(room_id)] = clean
        self.save()
        return list(clean)

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
