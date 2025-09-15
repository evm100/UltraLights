import threading
from typing import Dict, Any

import paho.mqtt.client as mqtt
from .config import settings
from .mqtt_bus import MqttBus
from .presets import get_preset, apply_preset
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
        self.bus = MqttBus()
        self.client = mqtt.Client()
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        # room_id -> {"house_id": str, "current": str|None, "timers": {sensor: Timer}}
        # Special-case entries may also include keys like "timer", "on",
        # "preset_on" and "preset_off".
        self.active: Dict[str, Dict[str, Any]] = {}
        self.config: Dict[str, Dict[str, Any]] = {}

    def start(self) -> None:
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
        self.config[node_id] = {"enabled": enabled, "duration": max(1, int(duration))}

    # MQTT callbacks -------------------------------------------------
    def _on_connect(self, client, userdata, flags, rc) -> None:
        client.subscribe("ul/+/evt/+/motion")

    def _on_message(self, client, userdata, msg: mqtt.MQTTMessage) -> None:
        parts = msg.topic.split("/")
        if len(parts) < 5:
            return
        node_id, sensor = parts[1], parts[3]
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
            entry = self.active.get(room_id)
            duration = int(cfg.get("duration", 30))
            if entry and entry.get("timer"):
                entry["timer"].cancel()
            else:
                entry = {
                    "house_id": house["id"],
                    "timer": None,
                    "on": False,
                    "preset_on": special["on"],
                    "preset_off": special["off"],
                }
                self.active[room_id] = entry
            entry["preset_on"] = special["on"]
            entry["preset_off"] = special["off"]
            timer = threading.Timer(duration, self._turn_off_special, args=(room_id,))
            timer.start()
            entry["timer"] = timer
            if not entry.get("on"):
                preset = get_preset(house["id"], room_id, special["on"])
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
        preset_id = entry.get("preset_off", "swell-off")
        preset = get_preset(house_id, room_id, preset_id)
        if preset:
            apply_preset(self.bus, preset)
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

motion_manager = MotionManager()
