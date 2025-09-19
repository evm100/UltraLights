import json
import threading
import time
from typing import Any, Dict, Optional, Tuple

import paho.mqtt.client as mqtt
from .config import settings
from .mqtt_bus import MqttBus, topic_cmd
from .presets import get_preset, apply_preset
from .motion_schedule import motion_schedule
from . import registry

SPECIAL_ROOM_PRESETS = {
    ("del-sur", "kitchen"): {
        "node": "kitchen",
        "on": "swell-on",
        "off": "swell-off",
    },
    ("del-sur", "master"): {
        "node": "master-closet",
        "on": "swell-on",
        "off": "swell-off",
    },
}

class MotionManager:
    def __init__(self) -> None:
        self.bus = MqttBus(client_id="ultralights-motion")
        self.client = mqtt.Client()
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        # room_id -> {"house_id": str, "current": str|None, "timers": {sensor: Timer}}
        # Special-case entries may also include keys like "timer", "on",
        # "preset_on".
        self.active: Dict[str, Dict[str, Any]] = {}
        self.config: Dict[str, Dict[str, Any]] = {}
        # (house_id, room_id) -> {"house_id": str, "room_id": str,
        #   "room_name": str, "nodes": {node_id: {"node_id": str,
        #   "node_name": str, "config": {...}, "sensors": {...}}}}
        self.room_sensors: Dict[Tuple[str, str], Dict[str, Any]] = {}

    def start(self) -> None:
        self._seed_room_sensors_from_config()
        self.client.connect(settings.BROKER_HOST, settings.BROKER_PORT, keepalive=30)
        self.client.loop_start()

    def stop(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()
        for info in list(self.active.values()):
            for t in info.get("timers", {}).values():
                t.cancel()
            timer = info.get("timer")
            if timer:
                timer.cancel()
        self.active.clear()

    def configure_node(self, node_id: str, enabled: bool, duration: int) -> None:
        clean_duration = max(1, int(duration))
        config = {"enabled": bool(enabled), "duration": clean_duration}
        self.config[node_id] = config
        self._ensure_room_sensor_entry(node_id, config=config)

    def forget_node(self, node_id: str) -> None:
        self.config.pop(node_id, None)
        for key, entry in list(self.room_sensors.items()):
            nodes = entry.get("nodes", {})
            if node_id in nodes:
                nodes.pop(node_id, None)
                if not nodes:
                    self.room_sensors.pop(key, None)

    # MQTT callbacks -------------------------------------------------
    def _on_connect(self, client, userdata, flags, rc) -> None:
        client.subscribe("ul/+/evt/+/motion")
        client.subscribe("ul/+/evt/status")

    def _on_message(self, client, userdata, msg: mqtt.MQTTMessage) -> None:
        topic = msg.topic or ""
        parts = topic.split("/")
        if len(parts) < 4 or parts[0] != "ul" or parts[2] != "evt":
            return
        node_id = parts[1]
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
        special = SPECIAL_ROOM_PRESETS.get((house["id"], room_id))
        if special:
            preset_on = motion_schedule.active_preset(
                house["id"], room_id, default=special.get("on")
            )
            if not preset_on:
                return
            entry = self.active.get(room_id)
            duration = int(cfg.get("duration", 30))
            if entry and entry.get("timer"):
                entry["timer"].cancel()
            else:
                entry = {
                    "house_id": house["id"],
                    "timer": None,
                    "on": False,
                    "preset_on": preset_on,
                }
                self.active[room_id] = entry
            entry["preset_on"] = preset_on
            timer = threading.Timer(duration, self._turn_off_special, args=(room_id,))
            timer.start()
            entry["timer"] = timer
            if not entry.get("on"):
                preset = get_preset(house["id"], room_id, preset_on)
                if preset:
                    apply_preset(self.bus, preset)
                entry["on"] = True
            return

        entry = self.active.setdefault(
            room_id, {"house_id": house["id"], "current": None, "timers": {}}
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
        preset_id = "motion-far" + ("-repeat" if repeat else "")
        preset = get_preset(entry["house_id"], room_id, preset_id)
        if preset:
            apply_preset(self.bus, preset)

    def _turn_off_special(self, room_id: str) -> None:
        entry = self.active.get(room_id)
        if not entry:
            return
        house_id = entry["house_id"]
        special = SPECIAL_ROOM_PRESETS.get((house_id, room_id))
        preset_off = None
        off_hint = None
        node_id = None
        if special:
            node_id = special.get("node")
            off_hint = special.get("off")
            if off_hint:
                preset_off = get_preset(house_id, room_id, off_hint)
        if preset_off:
            apply_preset(self.bus, preset_off)
        elif node_id and off_hint:
            self.bus.pub(topic_cmd(node_id, "motion/hint"), {"hint": off_hint})
        self.active.pop(room_id, None)

    def _clear_sensor(self, room_id: str, sensor: str) -> None:
        entry = self.active.get(room_id)
        if not entry:
            return
        timers = entry["timers"]
        timers.pop(sensor, None)
        house_id = entry["house_id"]
        if sensor == "pir" and entry.get("current") == "pir":
            entry["current"] = None
            preset_id = "motion-far-off"
            preset = get_preset(house_id, room_id, preset_id)
            if preset:
                apply_preset(self.bus, preset)
        if not timers:
            self.active.pop(room_id, None)

    # Internal helpers -----------------------------------------------
    def _seed_room_sensors_from_config(self) -> None:
        for node_id in list(self.config.keys()):
            self._ensure_room_sensor_entry(node_id)

    def _handle_status_message(self, node_id: str, msg: mqtt.MQTTMessage) -> None:
        payload = self._decode_json(msg.payload)
        if not isinstance(payload, dict):
            return
        sensors = self._extract_sensor_states(payload)
        if not sensors:
            return
        for sensor_id, sensor_payload in sensors.items():
            self._record_sensor_status(node_id, str(sensor_id), sensor_payload)

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

    def _ensure_room_sensor_entry(
        self, node_id: str, *, config: Optional[Dict[str, Any]] = None
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
        node_entry = nodes.setdefault(
            node_id,
            {
                "node_id": node_id,
                "node_name": node.get("name") or node_id,
                "sensors": {},
            },
        )
        node_entry["node_name"] = node.get("name") or node_id
        config_data = config or self.config.get(node_id)
        if config_data:
            node_entry["config"] = {
                "enabled": bool(config_data.get("enabled", True)),
                "duration": int(config_data.get("duration", 30)),
            }
        else:
            node_entry.setdefault("config", {"enabled": True, "duration": 30})
        return node_entry

motion_manager = MotionManager()
