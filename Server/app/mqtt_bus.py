import threading
import json
from typing import Optional, List, Dict
import paho.mqtt.client as paho
from .config import settings
from . import registry

def topic_cmd(node_id: str, path: str) -> str:
    return f"ul/{node_id}/cmd/{path}"

class MqttBus:
    def __init__(self):
        self.client = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id="ultralights-ui")
        self.client.connect(settings.BROKER_HOST, settings.BROKER_PORT, keepalive=30)
        self.thread = threading.Thread(target=self.client.loop_forever, daemon=True)
        self.thread.start()

    def pub(self, topic: str, payload: Dict[str, object]):
        self.client.publish(topic, payload=json.dumps(payload), qos=1, retain=False)

    # ---- WS strip commands ----
    def ws_set(self, node_id: str, strip: int, effect: Optional[str] = None,
               color: Optional[List[int]] = None, brightness: Optional[int] = None):
        msg: Dict[str, object] = {"strip": int(strip)}
        if effect:
            msg["effect"] = effect
        if color:
            msg["color"] = [int(x) for x in color]
        if brightness is not None:
            msg["brightness"] = int(brightness)
        self.pub(topic_cmd(node_id, "ws/set"), msg)

    def ws_power(self, node_id: str, strip: int, on: bool):
        msg = {"strip": int(strip), "on": bool(on)}
        self.pub(topic_cmd(node_id, "ws/power"), msg)

    # ---- White channel commands ----
    def white_set(self, node_id: str, channel: int, effect: Optional[str] = None,
                  brightness: Optional[int] = None):
        msg: Dict[str, object] = {"channel": int(channel)}
        if effect:
            msg["effect"] = effect
        if brightness is not None:
            msg["brightness"] = int(brightness)
        self.pub(topic_cmd(node_id, "white/set"), msg)

    def white_power(self, node_id: str, channel: int, on: bool):
        msg = {"channel": int(channel), "on": bool(on)}
        self.pub(topic_cmd(node_id, "white/power"), msg)

    # ---- Sensor commands ----
    def sensor_cooldown(self, node_id: str, seconds: int):
        msg = {"seconds": int(seconds)}
        self.pub(topic_cmd(node_id, "sensor/cooldown"), msg)

    # ---- OTA ----
    def ota_check(self, node_id: str):
        self.pub(topic_cmd(node_id, "ota/check"), {})

    def all_off(self):
        """Turn off all known nodes."""
        for _, _, n in registry.iter_nodes():
            nid = n["id"]
            for i in range(4):
                self.ws_power(nid, i, False)
                self.white_power(nid, i, False)
