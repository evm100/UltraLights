import threading
from typing import Dict, Any
import paho.mqtt.client as mqtt
from .config import settings
from .mqtt_bus import MqttBus
from .presets import get_preset, apply_preset
from . import registry

class MotionManager:
    def __init__(self) -> None:
        self.bus = MqttBus()
        self.client = mqtt.Client()
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.active: Dict[str, Dict[str, Any]] = {}
        self.config: Dict[str, Dict[str, Any]] = {}

    def start(self) -> None:
        self.client.connect(settings.BROKER_HOST, settings.BROKER_PORT, keepalive=30)
        self.client.loop_start()

    def stop(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()
        for info in list(self.active.values()):
            info["timer"].cancel()
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
        cfg = self.config.get(node_id, {"enabled": True, "duration": 30})
        if not cfg.get("enabled", True):
            return
        house, room, _ = registry.find_node(node_id)
        if not room or not house:
            return
        room_id = room["id"]
        active = self.active.get(room_id)
        if active:
            if active["sensor"] == "ultra" and sensor == "pir":
                # Ultrasonic overrides PIR; ignore PIR while active.
                return
            repeat = active["sensor"] == sensor
            active["timer"].cancel()
        else:
            repeat = False
        duration = int(cfg.get("duration", 30))
        timer = threading.Timer(duration, self._clear_room, args=(room_id,))
        timer.start()
        self.active[room_id] = {"sensor": sensor, "timer": timer}
        effect = "Motion Near" if sensor == "ultra" else "Motion Far"
        preset_id = effect.lower().replace(" ", "-") + ("-repeat" if repeat else "")
        preset = get_preset(house["id"], room_id, preset_id)
        if preset:
            apply_preset(self.bus, preset)

    def _clear_room(self, room_id: str) -> None:
        info = self.active.pop(room_id, None)
        if not info:
            return

motion_manager = MotionManager()
