"""Per-room 24-hour brightness curves with background applicator."""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import paho.mqtt.client as mqtt

from .config import settings
from .mqtt_tls import connect_mqtt_client

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
                clean = self._normalize_room_data(curve)
                if clean is not None:
                    house_entry[str(room_id)] = clean
            if house_entry:
                data[str(house_id)] = house_entry
        return data

    @staticmethod
    def _normalize_points(raw_points: Any) -> Optional[List[Dict[str, Any]]]:
        """Validate and normalize a list of curve points."""
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
        return points

    @staticmethod
    def _normalize_room_data(curve: Any) -> Optional[Dict[str, Any]]:
        """Normalize room curve data, handling both legacy and new formats."""
        if not isinstance(curve, dict):
            return None
        enabled = bool(curve.get("enabled", False))

        # Legacy format: top-level "points" without "channels"
        if "points" in curve and "channels" not in curve:
            pts = BrightnessCurveStore._normalize_points(curve.get("points"))
            if pts is None:
                return None
            return {
                "enabled": enabled,
                "mode": "sync",
                "channels": {"_sync": {"points": pts}},
            }

        # New format
        channels_raw = curve.get("channels")
        if not isinstance(channels_raw, dict):
            return None
        mode = curve.get("mode", "sync")
        if mode not in ("sync", "per_channel"):
            mode = "sync"

        channels: Dict[str, Dict[str, Any]] = {}
        for ch_key, ch_data in channels_raw.items():
            if not isinstance(ch_data, dict):
                continue
            pts = BrightnessCurveStore._normalize_points(ch_data.get("points"))
            if pts is not None:
                channels[str(ch_key)] = {"points": pts}

        if "_sync" not in channels:
            return None

        return {"enabled": enabled, "mode": mode, "channels": channels}

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
            return {
                "enabled": curve["enabled"],
                "mode": curve.get("mode", "sync"),
                "channels": {
                    k: {"points": list(v["points"])}
                    for k, v in curve.get("channels", {}).items()
                },
            }

    def set_curve(
        self,
        house_id: str,
        room_id: str,
        channels: Any,
        enabled: bool,
        mode: str = "sync",
    ) -> Dict[str, Any]:
        # Backward compat: if called with a plain points list, wrap as sync
        if isinstance(channels, list):
            channels = {"_sync": {"points": channels}}
            mode = "sync"

        clean = self._normalize_room_data(
            {"enabled": enabled, "mode": mode, "channels": channels}
        )
        if clean is None:
            raise ValueError("invalid curve data")
        with self._lock:
            house = self._data.setdefault(str(house_id), {})
            house[str(room_id)] = clean
            self._save()
            return {
                "enabled": clean["enabled"],
                "mode": clean["mode"],
                "channels": {
                    k: {"points": list(v["points"])}
                    for k, v in clean["channels"].items()
                },
            }

    def get_brightness(
        self, house_id: str, room_id: str, when: Optional[datetime] = None
    ) -> Optional[int]:
        """Return _sync channel brightness for backward compat."""
        curve = self.get_curve(house_id, room_id)
        if curve is None or not curve["enabled"]:
            return None
        if when is None:
            when = datetime.now()
        sync_pts = curve.get("channels", {}).get("_sync", {}).get("points", [])
        return interpolate_brightness(sync_pts, when.hour + when.minute / 60.0)

    def iter_enabled(self) -> List[Tuple[str, str, Dict[str, Any]]]:
        with self._lock:
            result = []
            for house_id, rooms in self._data.items():
                for room_id, curve in rooms.items():
                    if curve.get("enabled"):
                        result.append(
                            (
                                house_id,
                                room_id,
                                {
                                    "enabled": True,
                                    "mode": curve.get("mode", "sync"),
                                    "channels": {
                                        k: {"points": list(v["points"])}
                                        for k, v in curve.get("channels", {}).items()
                                    },
                                },
                            )
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


# Key: (node_id, module, strip_or_channel)
_StateKey = Tuple[str, str, int]


class BrightnessCurveApplicator:
    TICK_INTERVAL = 30

    def __init__(self, store: BrightnessCurveStore) -> None:
        self.store = store
        self._last: Dict[Tuple[str, str], Dict[str, int]] = {}
        self._last_sent: Dict[_StateKey, Dict[str, Any]] = {}
        self._shutdown = False
        self._thread: Optional[threading.Thread] = None

        # MQTT state tracker — learns current effect state from retained cmd topics
        self._state_lock = threading.Lock()
        self._node_state: Dict[_StateKey, Dict[str, Any]] = {}
        self._state_client: Optional[mqtt.Client] = None

    # ------------------------------------------------------------------
    # MQTT state tracker
    # ------------------------------------------------------------------

    def _start_state_tracker(self) -> None:
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id="brightness-curve-state"
        )
        enable_logger = getattr(client, "enable_logger", None)
        if callable(enable_logger):
            enable_logger()
        client.on_connect = self._state_on_connect
        client.on_message = self._state_on_message
        connect_mqtt_client(
            client, keepalive=30, start_async=True, raise_on_failure=False
        )
        loop_start = getattr(client, "loop_start", None)
        if callable(loop_start):
            loop_start()
        self._state_client = client

    def _stop_state_tracker(self) -> None:
        if self._state_client is None:
            return
        loop_stop = getattr(self._state_client, "loop_stop", None)
        if callable(loop_stop):
            loop_stop()
        self._state_client.disconnect()
        self._state_client = None

    def _state_on_connect(
        self, client: mqtt.Client, userdata, flags, reason_code, properties=None
    ) -> None:
        client.subscribe("ul/+/cmd/ws/set/+")
        client.subscribe("ul/+/cmd/rgb/set/+")
        client.subscribe("ul/+/cmd/white/set/+")

    def _state_on_message(
        self, client: mqtt.Client, userdata, msg: mqtt.MQTTMessage
    ) -> None:
        # Topic format: ul/<node_id>/cmd/<module>/set/<strip>
        parts = (msg.topic or "").split("/")
        if len(parts) < 6 or parts[0] != "ul" or parts[2] != "cmd" or parts[4] != "set":
            return
        node_id = parts[1]
        module = parts[3]  # ws, rgb, or white
        try:
            strip = int(parts[5])
        except (ValueError, IndexError):
            return
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        key: _StateKey = (node_id, module, strip)
        with self._state_lock:
            self._node_state[key] = payload

    def get_node_state(self, node_id: str, module: str, strip: int) -> Optional[Dict[str, Any]]:
        with self._state_lock:
            return self._node_state.get((node_id, module, strip))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._start_state_tracker()
        self._thread = threading.Thread(target=self._worker, daemon=True, name="brightness-curve")
        self._thread.start()

    def stop(self) -> None:
        self._shutdown = True
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._stop_state_tracker()

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

    # ------------------------------------------------------------------
    # Core logic — apply brightness to a room's nodes
    # ------------------------------------------------------------------

    def _build_command(
        self, module: str, state: Optional[Dict[str, Any]], brightness: int
    ) -> Dict[str, Any]:
        """Build a solid command for the brightness curve.

        White channels always get ``solid`` with no params.
        RGB/WS channels get ``solid`` preserving the current color from state.
        """
        if module == "white":
            return {"effect": "solid", "brightness": brightness, "params": []}
        # RGB / WS — extract current color or fall back to warm white
        color = [255, 200, 150]
        if state:
            params = state.get("params", [])
            if isinstance(params, list) and len(params) >= 3:
                try:
                    rgb = [int(params[0]), int(params[1]), int(params[2])]
                    if all(0 <= c <= 255 for c in rgb):
                        color = rgb
                except (TypeError, ValueError):
                    pass
        return {"effect": "solid", "brightness": brightness, "params": color}

    def _known_strips(self, node_id: str, module: str) -> List[int]:
        """Return all strip/channel indices known for this node+module.

        Merges two sources:
        1. channel_names config (explicitly named channels)
        2. MQTT state tracker (observed traffic)
        Falls back to [0] when neither source has data.
        """
        from .channel_names import channel_names

        strips: set = set()

        # Source 1: channel_names (user-configured, most reliable)
        node_channels = channel_names.get_names_for_node(node_id)
        for ch_str in node_channels.get(module, {}):
            try:
                strips.add(int(ch_str))
            except (TypeError, ValueError):
                pass

        # Source 2: MQTT state tracker (observed traffic)
        with self._state_lock:
            for key in self._node_state:
                if key[0] == node_id and key[1] == module:
                    strips.add(key[2])

        return sorted(strips) if strips else [0]

    def _observed_strips(self, node_id: str, module: str) -> List[int]:
        """Return strip indices with actual MQTT state (no fallback).

        Used for the UI channel list — only shows channels we've actually
        seen traffic for, avoiding phantom entries.
        """
        strips = []
        with self._state_lock:
            for key in self._node_state:
                if key[0] == node_id and key[1] == module:
                    strips.append(key[2])
        return sorted(strips)

    def clear_room_cache(self, house_id: str, room_id: str) -> None:
        """Clear sent-command and tick caches for a room.

        Call this when the curve is enabled or disabled so the next tick
        (or immediate apply) sends fresh commands instead of being
        suppressed by the dedup cache.
        """
        from . import registry

        key = (house_id, room_id)
        self._last.pop(key, None)

        _, room = registry.find_room(house_id, room_id)
        if not room:
            return
        node_ids = set()
        for node in room.get("nodes", []):
            if isinstance(node, dict) and node.get("id"):
                node_ids.add(node["id"])
        for cache_key in list(self._last_sent.keys()):
            if cache_key[0] in node_ids:
                self._last_sent.pop(cache_key, None)

    def disable_for_room(self, house_id: str, room_id: str) -> bool:
        """Disable the brightness curve for a room and clear caches.

        Returns True if the curve was previously enabled.
        """
        curve = self.store.get_curve(house_id, room_id)
        if not curve or not curve.get("enabled"):
            return False
        self.store.set_curve(
            house_id, room_id,
            curve["channels"], False, curve.get("mode", "sync"),
        )
        self.clear_room_cache(house_id, room_id)
        return True

    def _send_command(self, node_id: str, module: str, strip: int, brightness: int) -> None:
        """Build and send a brightness command for a single strip."""
        from .routes_api import get_bus

        try:
            bus = get_bus()
        except Exception:
            return

        state = self.get_node_state(node_id, module, strip)
        cmd = self._build_command(module, state, brightness)
        cache_key: _StateKey = (node_id, module, strip)
        if self._last_sent.get(cache_key) == cmd:
            return
        self._last_sent[cache_key] = cmd
        if module == "white":
            bus.white_set(
                node_id, strip, cmd["effect"], cmd["brightness"],
                cmd["params"] or None,
            )
        elif module == "ws":
            bus.ws_set(
                node_id, strip, cmd["effect"], cmd["brightness"],
                cmd["params"] or None,
            )
        elif module == "rgb":
            bus.rgb_set(
                node_id, strip, cmd["effect"], cmd["brightness"],
                cmd["params"] or None,
            )

    def apply_brightness_to_room(
        self, house_id: str, room_id: str, brightnesses: Any, mode: str = "sync"
    ) -> None:
        """Apply brightness values to nodes in a room.

        brightnesses: Dict[str, int] — channel_key -> brightness value
          sync mode: {"_sync": N} -> apply N to all nodes
          per_channel mode: {"nodeId:mod:strip": N, ...} -> apply per strip
        Also accepts a plain int for backward compat (treated as sync).

        Note: does NOT update ``_last`` — only ``_tick`` manages that cache
        so the comparison format stays consistent.
        """
        # Backward compat: plain int
        if isinstance(brightnesses, int):
            brightnesses = {"_sync": brightnesses}
            mode = "sync"

        from . import registry

        house, room = registry.find_room(house_id, room_id)
        if not room:
            return

        if mode == "sync":
            brightness = brightnesses.get("_sync", 0)
            for node in room.get("nodes", []):
                if not isinstance(node, dict):
                    continue
                node_id = node.get("id")
                if not node_id:
                    continue
                modules = node.get("modules") or []
                for module in ("ws", "rgb", "white"):
                    if module not in modules:
                        continue
                    for strip in self._known_strips(node_id, module):
                        self._send_command(node_id, module, strip, brightness)
        else:
            # per_channel mode
            sync_brightness = brightnesses.get("_sync")
            for node in room.get("nodes", []):
                if not isinstance(node, dict):
                    continue
                node_id = node.get("id")
                if not node_id:
                    continue
                modules = node.get("modules") or []
                for module in ("ws", "rgb", "white"):
                    if module not in modules:
                        continue
                    for strip in self._known_strips(node_id, module):
                        ch_key = f"{node_id}:{module}:{strip}"
                        brightness = brightnesses.get(ch_key)
                        if brightness is None and sync_brightness is not None:
                            brightness = sync_brightness
                        if brightness is None:
                            continue
                        self._send_command(node_id, module, strip, brightness)

    def _tick(self) -> None:
        now = datetime.now()
        hour = now.hour + now.minute / 60.0
        enabled = self.store.iter_enabled()
        active_keys: set[Tuple[str, str]] = set()
        active_node_ids: set[str] = set()

        from . import registry

        for house_id, room_id, room_data in enabled:
            key = (house_id, room_id)
            active_keys.add(key)
            mode = room_data.get("mode", "sync")
            channels = room_data.get("channels", {})
            brightnesses: Dict[str, int] = {}

            if mode == "sync":
                pts = channels.get("_sync", {}).get("points", [])
                brightnesses["_sync"] = interpolate_brightness(pts, hour)
            else:
                # per_channel: resolve every actual strip to a concrete
                # brightness so the cache dict is stable and complete.
                sync_pts = channels.get("_sync", {}).get("points", [])
                sync_b = interpolate_brightness(sync_pts, hour) if sync_pts else 0
                _, room_entry = registry.find_room(house_id, room_id)
                if room_entry:
                    for node in room_entry.get("nodes", []):
                        if not isinstance(node, dict):
                            continue
                        node_id = node.get("id")
                        if not node_id:
                            continue
                        for module in ("ws", "rgb", "white"):
                            if module not in (node.get("modules") or []):
                                continue
                            for strip in self._known_strips(node_id, module):
                                ch_key = f"{node_id}:{module}:{strip}"
                                ch_data = channels.get(ch_key)
                                if ch_data:
                                    brightnesses[ch_key] = interpolate_brightness(
                                        ch_data.get("points", []), hour
                                    )
                                else:
                                    brightnesses[ch_key] = sync_b

            if self._last.get(key) == brightnesses:
                continue
            self._last[key] = dict(brightnesses)
            self.apply_brightness_to_room(house_id, room_id, brightnesses, mode)

            # Track active node IDs for stale cache cleanup
            _, room_entry = registry.find_room(house_id, room_id)
            if room_entry:
                for node in room_entry.get("nodes", []):
                    if isinstance(node, dict) and node.get("id"):
                        active_node_ids.add(node["id"])

        for stale_key in list(self._last.keys()):
            if stale_key not in active_keys:
                self._last.pop(stale_key, None)

        # Clean stale sent-command cache for rooms no longer enabled
        for cache_key in list(self._last_sent.keys()):
            if cache_key[0] not in active_node_ids:
                self._last_sent.pop(cache_key, None)


brightness_curve_store = BrightnessCurveStore(settings.BRIGHTNESS_CURVE_FILE)
brightness_curve_applicator = BrightnessCurveApplicator(brightness_curve_store)
