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
    def ws_set(
        self,
        node_id: str,
        strip: int,
        effect: str,
        brightness: int,
        speed: float,
        params: Optional[List[float]] = None,
    ):
        msg: Dict[str, object] = {
            "strip": int(strip),
            "effect": effect,
            "brightness": int(brightness),
            "speed": float(speed),
        }
        if params:
            msg["params"] = params
        self.pub(topic_cmd(node_id, "ws/set"), msg)

    def ws_power(self, node_id: str, strip: int, on: bool):
        msg = {"strip": int(strip), "on": bool(on)}
        self.pub(topic_cmd(node_id, "ws/power"), msg)

    # ---- White channel commands ----
    def white_set(
        self,
        node_id: str,
        channel: int,
        effect: str,
        brightness: int,
        params: Optional[List[float]] = None,
    ):
        msg: Dict[str, object] = {
            "channel": int(channel),
            "effect": effect,
            "brightness": int(brightness),
        }
        if params:
            msg["params"] = params
        self.pub(topic_cmd(node_id, "white/set"), msg)

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
                self.white_set(nid, i, "solid", 0)
