"""Per-room 24-hour brightness curves with background applicator."""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import settings

logger = logging.getLogger(__name__)

DEFAULT_POINTS = [
    {"hour": 0, "brightness": 10},
    {"hour": 7, "brightness": 128},
    {"hour": 12, "brightness": 200},
    {"hour": 18, "brightness": 255},
    {"hour": 22, "brightness": 50},
]

NODE_COLORS = [
    "#ffffff",
    "#38bdf8",
    "#a78bfa",
    "#f472b6",
    "#34d399",
    "#fbbf24",
    "#fb923c",
    "#f87171",
]


def _catmull_rom(p0: float, p1: float, p2: float, p3: float, t: float) -> float:
    t2 = t * t
    t3 = t2 * t
    return 0.5 * (
        (2 * p1)
        + (-p0 + p2) * t
        + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2
        + (-p0 + 3 * p1 - 3 * p2 + p3) * t3
    )


def interpolate_brightness(points: List[Dict[str, Any]], hour: float) -> int:
    if not points:
        return 0
    pts = sorted(points, key=lambda p: p["hour"])
    hours = [float(p["hour"]) for p in pts]
    values = [float(p["brightness"]) for p in pts]
    n = len(pts)
    if n == 1:
        return max(0, min(255, int(values[0])))
    hour = hour % 24
    segment = -1
    for i in range(n - 1):
        if hours[i] <= hour <= hours[i + 1]:
            segment = i
            break
    if segment == -1:
        h0 = hours[-1]
        h1 = hours[0] + 24
        h_current = hour + 24 if hour < hours[0] else hour
        span = h1 - h0
        t = (h_current - h0) / span if span > 0 else 0
        p0 = values[-2] if n >= 2 else values[-1]
        p1 = values[-1]
        p2 = values[0]
        p3 = values[1] if n >= 2 else values[0]
        result = _catmull_rom(p0, p1, p2, p3, t)
    else:
        span = hours[segment + 1] - hours[segment]
        t = (hour - hours[segment]) / span if span > 0 else 0
        p0 = values[-1] if segment == 0 else values[segment - 1]
        p1 = values[segment]
        p2 = values[segment + 1]
        p3 = values[0] if segment + 2 >= n else values[segment + 2]
        result = _catmull_rom(p0, p1, p2, p3, t)
    return max(0, min(255, int(round(result))))


class BrightnessCurveStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._data = self._load()

    def _load(self) -> Dict[str, Dict[str, Dict[str, Any]]]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text())
        except Exception:
            return {}
        if not isinstance(payload, dict):
            return {}
        data: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for house_id, rooms in payload.items():
            if not isinstance(rooms, dict):
                continue
            house_entry: Dict[str, Dict[str, Any]] = {}
            for room_id, curve in rooms.items():
                clean = self._normalize_curve(curve)
                if clean is not None:
                    house_entry[str(room_id)] = clean
            if house_entry:
                data[str(house_id)] = house_entry
        return data

    @staticmethod
    def _normalize_curve(curve: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(curve, dict):
            return None
        enabled = bool(curve.get("enabled", False))
        raw_points = curve.get("points")
        if not isinstance(raw_points, list):
            return None
        points: List[Dict[str, Any]] = []
        for p in raw_points:
            if not isinstance(p, dict):
                continue
            h_raw, b_raw = p.get("hour"), p.get("brightness")
            if h_raw is None or b_raw is None:
                continue
            try:
                h, b = float(h_raw), int(b_raw)
            except (TypeError, ValueError):
                continue
            points.append({"hour": max(0.0, min(24.0, h)), "brightness": max(0, min(255, b))})
        if not points:
            return None
        points.sort(key=lambda p: p["hour"])
        return {"enabled": enabled, "points": points}

    def _save(self) -> None:
        serialized = json.dumps(self._data, indent=2)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(serialized)
        tmp.replace(self.path)

    def get_curve(self, house_id: str, room_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            house = self._data.get(str(house_id))
            if not house:
                return None
            curve = house.get(str(room_id))
            if curve is None:
                return None
            return {"enabled": curve["enabled"], "points": list(curve["points"])}

    def set_curve(
        self,
        house_id: str,
        room_id: str,
        points: List[Dict[str, Any]],
        enabled: bool,
    ) -> Dict[str, Any]:
        clean = self._normalize_curve({"enabled": enabled, "points": points})
        if clean is None:
            raise ValueError("invalid curve data")
        with self._lock:
            house = self._data.setdefault(str(house_id), {})
            house[str(room_id)] = clean
            self._save()
            return {"enabled": clean["enabled"], "points": list(clean["points"])}

    def get_brightness(
        self, house_id: str, room_id: str, when: Optional[datetime] = None
    ) -> Optional[int]:
        curve = self.get_curve(house_id, room_id)
        if curve is None or not curve["enabled"]:
            return None
        if when is None:
            when = datetime.now()
        return interpolate_brightness(curve["points"], when.hour + when.minute / 60.0)

    def iter_enabled(self) -> List[Tuple[str, str, Dict[str, Any]]]:
        with self._lock:
            result = []
            for house_id, rooms in self._data.items():
                for room_id, curve in rooms.items():
                    if curve.get("enabled"):
                        result.append(
                            (house_id, room_id, {"enabled": True, "points": list(curve["points"])})
                        )
            return result

    def remove_room(self, house_id: str, room_id: str) -> None:
        with self._lock:
            house = self._data.get(str(house_id))
            if not house:
                return
            if house.pop(str(room_id), None) is not None:
                if not house:
                    self._data.pop(str(house_id), None)
                self._save()


class BrightnessCurveApplicator:
    TICK_INTERVAL = 60

    def __init__(self, store: BrightnessCurveStore) -> None:
        self.store = store
        self._last: Dict[Tuple[str, str], int] = {}
        self._shutdown = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._worker, daemon=True, name="brightness-curve")
        self._thread.start()

    def stop(self) -> None:
        self._shutdown = True
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def _worker(self) -> None:
        while not self._shutdown:
            try:
                self._tick()
            except Exception:
                logger.exception("brightness curve tick failed")
            for _ in range(self.TICK_INTERVAL):
                if self._shutdown:
                    return
                time.sleep(1)

    def _tick(self) -> None:
        from . import registry
        from .routes_api import get_bus

        try:
            bus = get_bus()
        except Exception:
            return

        now = datetime.now()
        enabled = self.store.iter_enabled()
        active_keys: set[Tuple[str, str]] = set()

        for house_id, room_id, curve in enabled:
            brightness = interpolate_brightness(curve["points"], now.hour + now.minute / 60.0)
            key = (house_id, room_id)
            active_keys.add(key)
            if self._last.get(key) == brightness:
                continue
            self._last[key] = brightness
            house, room = registry.find_room(house_id, room_id)
            if not room:
                continue
            for node in room.get("nodes", []):
                if not isinstance(node, dict):
                    continue
                node_id = node.get("id")
                if not node_id:
                    continue
                modules = node.get("modules") or []
                if "white" in modules:
                    bus.white_set(node_id, 0, "solid", brightness)
                if "ws" in modules:
                    bus.ws_set(node_id, 0, "solid", brightness, [255, 200, 150])
                if "rgb" in modules:
                    bus.rgb_set(node_id, 0, "solid", brightness, [255, 200, 150])

        for stale_key in list(self._last.keys()):
            if stale_key not in active_keys:
                self._last.pop(stale_key, None)


brightness_curve_store = BrightnessCurveStore(settings.BRIGHTNESS_CURVE_FILE)
brightness_curve_applicator = BrightnessCurveApplicator(brightness_curve_store)
