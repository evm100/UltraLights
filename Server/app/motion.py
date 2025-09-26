import json
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import paho.mqtt.client as mqtt

from .mqtt_bus import MqttBus
from .mqtt_tls import connect_mqtt_client
from .presets import get_preset, apply_preset
from .motion_schedule import motion_schedule
from .motion_prefs import motion_preferences
from . import registry

MOTION_STATUS_REQUEST_INTERVAL = 30.0
# Matches the firmware's fade duration when clearing motion presets.
MOTION_OFF_FADE_MS = 5000

class MotionManager:
    def __init__(self) -> None:
        self.bus = MqttBus(client_id="ultralights-motion")
        self.client = mqtt.Client()
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        # room_id -> {"house_id": str, "current": str|None, "timers": {sensor: Timer}}
        # Entries may also track the active preset identifier in ``preset_on``.
        self.active: Dict[str, Dict[str, Any]] = {}
        self.config: Dict[str, Dict[str, Any]] = {}
        # (house_id, room_id) -> {"house_id": str, "room_id": str,
        #   "room_name": str, "nodes": {node_id: {"node_id": str,
        #   "node_name": str, "config": {...}, "sensors": {...}}}}
        self.room_sensors: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._status_request_lock = threading.Lock()
        self._status_request_times: Dict[str, float] = {}
        self.motion_preferences = motion_preferences

    def start(self) -> None:
        self._seed_room_sensors_from_config()
        self._request_status_for_registry()
        connect_mqtt_client(self.client, keepalive=30)
        self.client.loop_start()

    def stop(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()
        for info in list(self.active.values()):
            for t in info.get("timers", {}).values():
                try:
                    t.cancel()
                except Exception:
                    pass
        self.active.clear()

    def configure_node(self, node_id: str, enabled: bool, duration: int) -> None:
        clean_duration = max(1, int(duration))
        config = {"enabled": bool(enabled), "duration": clean_duration}
        self.config[node_id] = config
        self._ensure_room_sensor_entry(node_id, config=config)
        self._request_motion_status(node_id, force=True)

    def update_node_name(self, node_id: str, name: str) -> None:
        """Update cached sensor metadata for ``node_id`` with ``name``."""

        for entry in self.room_sensors.values():
            nodes = entry.get("nodes")
            if not isinstance(nodes, dict):
                continue
            node_entry = nodes.get(node_id)
            if isinstance(node_entry, dict):
                node_entry["node_name"] = name

    def forget_node(self, node_id: str) -> None:
        self.config.pop(node_id, None)
        for key, entry in list(self.room_sensors.items()):
            nodes = entry.get("nodes", {})
            if node_id in nodes:
                nodes.pop(node_id, None)
                if not nodes:
                    self.room_sensors.pop(key, None)
        with self._status_request_lock:
            self._status_request_times.pop(node_id, None)
        self.motion_preferences.remove_node(node_id)

    def forget_room(self, house_id: str, room_id: str) -> None:
        """Drop any cached state associated with ``house_id``/``room_id``."""

        self.room_sensors.pop((house_id, room_id), None)

        existing = self.active.get(room_id)
        if existing and existing.get("house_id") not in (None, house_id):
            return

        entry = self.active.pop(room_id, None)
        if not entry:
            return

        timers = entry.get("timers")
        if isinstance(timers, dict):
            for timer in timers.values():
                try:
                    timer.cancel()
                except Exception:
                    pass

    def ensure_room_loaded(self, house_id: str, room_id: str) -> None:
        house, room = registry.find_room(house_id, room_id)
        if not house or not room:
            return
        for node in room.get("nodes", []):
            node_id = node.get("id")
            if not node_id:
                continue
            if str(node_id) in self.config:
                self._ensure_room_sensor_entry(str(node_id))
            self._request_motion_status(str(node_id))

    def request_motion_status(self, node_id: str, *, force: bool = False) -> None:
        self._request_motion_status(node_id, force=force)

    # MQTT callbacks -------------------------------------------------
    def _on_connect(self, client, userdata, flags, rc) -> None:
        client.subscribe("ul/+/evt/+/motion")
        client.subscribe("ul/+/evt/status")
        client.subscribe("ul/+/evt/motion/status")
        self._request_status_for_registry(force=True)

    def _on_message(self, client, userdata, msg: mqtt.MQTTMessage) -> None:
        topic = msg.topic or ""
        parts = topic.split("/")
        if len(parts) < 4 or parts[0] != "ul" or parts[2] != "evt":
            return
        node_id = parts[1]
        if len(parts) >= 5 and parts[3] == "motion" and parts[4] == "status":
            self._handle_motion_status_message(node_id, msg)
            return
        if len(parts) == 4 and parts[3] == "status":
            self._handle_status_message(node_id, msg)
            return
        if len(parts) < 5:
            return
        sensor = parts[3]
        if parts[4] != "motion":
            return
        if sensor == "pir":
            self._record_motion_event(node_id, sensor, msg.payload)
        if sensor != "pir":
            return
        cfg = self.config.get(node_id, {"enabled": True, "duration": 30})
        if not cfg.get("enabled", True):
            return
        house, room, _ = registry.find_node(node_id)
        if not room or not house:
            return
        room_id = room["id"]
        entry = self.active.setdefault(
            room_id,
            {
                "house_id": house["id"],
                "current": None,
                "timers": {},
                "preset_on": None,
            },
        )
        timers = entry["timers"]
        repeat = sensor in timers
        if repeat:
            timers[sensor].cancel()
        duration = int(cfg.get("duration", 30))
        timer = threading.Timer(duration, self._clear_sensor, args=(room_id, sensor))
        timer.start()
        timers[sensor] = timer
        entry["current"] = "pir"
        preset_id = motion_schedule.active_preset(entry["house_id"], room_id)
        if not preset_id:
            return
        previous = entry.get("preset_on")
        if previous == preset_id:
            return
        preset = get_preset(entry["house_id"], room_id, preset_id)
        if not preset:
            return
        applied = self._apply_motion_preset(entry["house_id"], room_id, preset)
        if applied:
            entry["preset_on"] = preset_id

    def _clear_sensor(self, room_id: str, sensor: str) -> None:
        entry = self.active.get(room_id)
        if not entry:
            return
        timers = entry["timers"]
        timers.pop(sensor, None)
        house_id = entry["house_id"]
        if sensor == "pir" and entry.get("current") == "pir":
            entry["current"] = None
        if timers:
            return
        preset_id = entry.get("preset_on")
        self.active.pop(room_id, None)
        if not preset_id:
            return
        preset = get_preset(house_id, room_id, preset_id)
        if not preset:
            return
        nodes = self._eligible_motion_nodes(house_id, room_id, preset)
        if not nodes:
            return
        for node_id in nodes:
            self.bus.motion_off(
                node_id, {"fade": {"duration_ms": MOTION_OFF_FADE_MS}}
            )

    # Internal helpers -----------------------------------------------
    def _eligible_motion_nodes(
        self, house_id: str, room_id: str, preset: Dict[str, Any]
    ) -> List[str]:
        actions = preset.get("actions")
        if not isinstance(actions, list):
            return []

        immune_nodes = {
            node_id
            for node_id in self.motion_preferences.get_room_immune_nodes(
                house_id, room_id
            )
            if node_id
        }
        room_nodes = self._room_node_ids(house_id, room_id)
        seen: Set[str] = set()
        eligible: List[str] = []

        for action in actions:
            if not isinstance(action, dict):
                continue
            node_raw = action.get("node")
            module_raw = action.get("module")
            node_id = str(node_raw).strip() if node_raw is not None else ""
            if not node_id or node_id in immune_nodes:
                continue
            module = str(module_raw).strip().lower() if module_raw is not None else ""
            if module not in {"ws", "rgb", "white"}:
                continue
            if room_nodes and node_id not in room_nodes:
                continue
            if node_id in seen:
                continue
            seen.add(node_id)
            eligible.append(node_id)

        return eligible

    def _room_node_ids(self, house_id: str, room_id: str) -> Set[str]:
        nodes: Set[str] = set()
        entry = self.room_sensors.get((house_id, room_id))
        if isinstance(entry, dict):
            node_entries = entry.get("nodes")
            if isinstance(node_entries, dict):
                for node_id in node_entries.keys():
                    text = str(node_id).strip()
                    if text:
                        nodes.add(text)

        _, room = registry.find_room(house_id, room_id)
        if room and isinstance(room.get("nodes"), list):
            for node in room.get("nodes", []):
                if not isinstance(node, dict):
                    continue
                node_id = node.get("id")
                if node_id is None:
                    continue
                text = str(node_id).strip()
                if text:
                    nodes.add(text)

        return nodes

    def _request_motion_status(self, node_id: str, *, force: bool = False) -> None:
        if not node_id:
            return
        should_request = False
        now = time.monotonic()
        with self._status_request_lock:
            last = self._status_request_times.get(node_id)
            if force or last is None or now - last > MOTION_STATUS_REQUEST_INTERVAL:
                self._status_request_times[node_id] = now
                should_request = True
        if should_request:
            self.bus.motion_status_request(node_id)

    def _request_status_for_registry(self, *, force: bool = False) -> None:
        for house, room, node in registry.iter_nodes():
            node_id = node.get("id")
            if node_id:
                self._request_motion_status(str(node_id), force=force)

    def _seed_room_sensors_from_config(self) -> None:
        for node_id in list(self.config.keys()):
            self._ensure_room_sensor_entry(node_id)
            self._request_motion_status(node_id, force=True)

    def _handle_status_message(self, node_id: str, msg: mqtt.MQTTMessage) -> None:
        payload = self._decode_json(msg.payload)
        if not isinstance(payload, dict):
            return
        pir_enabled = payload.get("pir_enabled")
        if isinstance(pir_enabled, bool):
            entry = self.config.setdefault(node_id, {"enabled": True, "duration": 30})
            entry["pir_enabled"] = pir_enabled
            self._ensure_room_sensor_entry(node_id, config=entry, request_status=False)
        sensors = self._extract_sensor_states(payload)
        if not sensors:
            return
        for sensor_id, sensor_payload in sensors.items():
            self._record_sensor_status(node_id, str(sensor_id), sensor_payload)

    def _handle_motion_status_message(
        self, node_id: str, msg: mqtt.MQTTMessage
    ) -> None:
        payload = self._decode_json(msg.payload)
        if not isinstance(payload, dict):
            return
        pir_enabled = payload.get("pir_enabled")
        if isinstance(pir_enabled, bool):
            entry = self.config.setdefault(node_id, {"enabled": True, "duration": 30})
            entry["pir_enabled"] = pir_enabled
            self._ensure_room_sensor_entry(node_id, config=entry, request_status=False)

    def _record_sensor_status(self, node_id: str, sensor_id: str, payload: Any) -> None:
        node_entry = self._ensure_room_sensor_entry(node_id)
        if not node_entry:
            return
        sensors = node_entry.setdefault("sensors", {})
        entry = sensors.setdefault(sensor_id, {})
        normalized = self._normalize_sensor_payload(payload)
        entry["last_status"] = time.time()
        entry["raw"] = payload
        data = normalized.get("data")
        if data is not None:
            entry["data"] = data
        active = normalized.get("active")
        if active is not None:
            entry["active"] = active

    def _record_motion_event(self, node_id: str, sensor: str, payload: bytes) -> None:
        node_entry = self._ensure_room_sensor_entry(node_id)
        if not node_entry:
            return
        sensors = node_entry.setdefault("sensors", {})
        entry = sensors.setdefault(sensor, {})
        decoded = self._decode_json(payload)
        state_value: Optional[Any] = None
        if isinstance(decoded, dict):
            state_value = decoded.get("state")
            entry["event_payload"] = decoded
        else:
            try:
                text = payload.decode("utf-8")
            except Exception:
                text = ""
            if text:
                state_value = text
        entry["last_event"] = time.time()
        if state_value is not None:
            entry["state"] = state_value
            active = self._normalize_active_value(state_value)
            if active is not None:
                entry["active"] = active
                if active:
                    entry["last_detected"] = entry["last_event"]

    def _normalize_sensor_payload(self, payload: Any) -> Dict[str, Any]:
        data: Any = None
        if isinstance(payload, dict):
            data = dict(payload)
        active: Optional[bool] = None
        if isinstance(payload, dict):
            for key in ("active", "state", "status", "value", "motion", "present"):
                if key in payload:
                    active = self._normalize_active_value(payload[key])
                    if active is not None:
                        break
        else:
            active = self._normalize_active_value(payload)
        return {"active": active, "data": data if data is not None else payload}

    def _normalize_active_value(self, value: Any) -> Optional[bool]:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            text = value.strip().lower()
            if text in {
                "1",
                "true",
                "yes",
                "on",
                "active",
                "motion",
                "motion_detected",
                "motion_detect",
                "detected",
            }:
                return True
            if text in {
                "0",
                "false",
                "no",
                "off",
                "inactive",
                "clear",
                "motion_clear",
                "none",
            }:
                return False
        return None

    def _extract_sensor_states(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        sensors: Dict[str, Any] = {}
        raw_sensors = payload.get("sensors")
        if isinstance(raw_sensors, dict):
            for key, value in raw_sensors.items():
                sensors[str(key)] = value
        modules = payload.get("modules")
        if isinstance(modules, dict):
            motion_state = modules.get("motion")
            if isinstance(motion_state, dict):
                module_sensors = motion_state.get("sensors")
                if isinstance(module_sensors, dict):
                    for key, value in module_sensors.items():
                        sensors.setdefault(str(key), value)
        motion = payload.get("motion")
        if isinstance(motion, dict):
            for key, value in motion.items():
                sensors.setdefault(str(key), value)
        sensor_info = payload.get("sensor")
        if isinstance(sensor_info, dict):
            sensor_id = sensor_info.get("id") or sensor_info.get("sid")
            if sensor_id:
                sensors.setdefault(str(sensor_id), sensor_info)
        sid = payload.get("sid")
        state = payload.get("state")
        if sid is not None and state is not None:
            sensors.setdefault(str(sid), payload)
        if "pir" in payload and "pir" not in sensors:
            sensors["pir"] = payload.get("pir")
        return sensors

    def _decode_json(self, payload: bytes) -> Any:
        try:
            text = payload.decode("utf-8")
        except Exception:
            return None
        if not text:
            return None
        try:
            return json.loads(text)
        except Exception:
            return None

    def _apply_motion_preset(
        self, house_id: str, room_id: str, preset: Optional[Dict[str, Any]]
    ) -> bool:
        if not preset or not isinstance(preset, dict):
            return False

        immune = self.motion_preferences.get_room_immune_nodes(house_id, room_id)
        if not immune:
            apply_preset(self.bus, preset)
            return True

        actions = preset.get("actions")
        if not isinstance(actions, list):
            apply_preset(self.bus, preset)
            return True

        filtered_actions = []
        removed = False
        for action in actions:
            if isinstance(action, dict):
                node_raw = action.get("node")
                node_id = str(node_raw).strip() if node_raw is not None else ""
                if node_id and node_id in immune:
                    removed = True
                    continue
            filtered_actions.append(action)

        if not filtered_actions:
            return False

        if not removed:
            apply_preset(self.bus, preset)
            return True

        filtered_preset = dict(preset)
        filtered_preset["actions"] = filtered_actions
        apply_preset(self.bus, filtered_preset)
        return True

    def _ensure_room_sensor_entry(
        self,
        node_id: str,
        *,
        config: Optional[Dict[str, Any]] = None,
        request_status: bool = True,
    ) -> Optional[Dict[str, Any]]:
        house, room, node = registry.find_node(node_id)
        if not house or not room or not node:
            return None
        house_id = house.get("id")
        room_id = room.get("id")
        if not house_id or not room_id:
            return None
        key = (house_id, room_id)
        entry = self.room_sensors.setdefault(
            key,
            {
                "house_id": house_id,
                "room_id": room_id,
                "room_name": room.get("name") or room_id,
                "nodes": {},
            },
        )
        nodes = entry.setdefault("nodes", {})
        created = node_id not in nodes
        node_entry = nodes.setdefault(
            node_id,
            {
                "node_id": node_id,
                "node_name": node.get("name") or node_id,
                "sensors": {},
            },
        )
        node_entry["node_name"] = node.get("name") or node_id
        sensors = node_entry.setdefault("sensors", {})
        config_data = config or self.config.get(node_id)
        pir_enabled_flag: Optional[bool] = None
        if config_data:
            clean_config = {
                "enabled": bool(config_data.get("enabled", True)),
                "duration": int(config_data.get("duration", 30)),
            }
            if "pir_enabled" in config_data:
                pir_enabled_flag = bool(config_data.get("pir_enabled"))
                clean_config["pir_enabled"] = pir_enabled_flag
            node_entry["config"] = clean_config
        else:
            existing_config = node_entry.setdefault(
                "config", {"enabled": True, "duration": 30}
            )
            if isinstance(existing_config, dict) and "pir_enabled" in existing_config:
                pir_enabled_flag = bool(existing_config.get("pir_enabled"))
        if pir_enabled_flag is False and isinstance(sensors, dict):
            sensors.pop("pir", None)
            if not sensors:
                node_entry["sensors"] = {}
        if request_status and created:
            self._request_motion_status(node_id)
        return node_entry

motion_manager = MotionManager()
