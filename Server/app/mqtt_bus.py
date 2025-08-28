import threading
import json
import paho.mqtt.client as paho
from .config import settings
from . import registry

# New per-node topics (clean naming)
# lights/{id}/cmd/{color,effect,spacey,white,brightness,ota}
def topic_node(id_: str, leaf: str) -> str:
    return f"lights/{id_}/cmd/{leaf}"

def topic_status(id_: str, leaf: str) -> str:
    return f"lights/{id_}/status/{leaf}"

class MqttBus:
    def __init__(self):
        self.client = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id="ultralights-ui")
        self.client.connect(settings.BROKER_HOST, settings.BROKER_PORT, keepalive=30)
        self.thread = threading.Thread(target=self.client.loop_forever, daemon=True)
        self.thread.start()

    def pub(self, topic: str, payload: str, qos: int = 1, retain: bool = True):
        self.client.publish(topic, payload=payload, qos=qos, retain=retain)

    # --------- Compatibility helpers ----------
    def send_color(self, node_id: str, r: int, g: int, b: int):
        msg_legacy = f"{r},{g},{b}"
        msg = json.dumps({"r": int(r), "g": int(g), "b": int(b)})
        if settings.MQTT_COMPAT_PUBLISH_BOTH:
            self.pub(settings.LEGACY_TOPIC_COLOR, msg_legacy, retain=True)
        self.pub(topic_node(node_id, "color"), msg, retain=True)

    def send_effect(self, node_id: str, name: str):
        msg = json.dumps({"name": name})
        if settings.MQTT_COMPAT_PUBLISH_BOTH:
            self.pub(settings.LEGACY_TOPIC_EFFECT, name, retain=True)
        self.pub(topic_node(node_id, "effect"), msg, retain=True)

    def send_spacey(self, node_id: str, c1: str, c2: str, c3: str):
        # c1,c2,c3 are "r,g,b"
        msg = json.dumps({"c1": c1, "c2": c2, "c3": c3})
        if settings.MQTT_COMPAT_PUBLISH_BOTH:
            self.pub(settings.LEGACY_TOPIC_SPACEY, f"{c1}|{c2}|{c3}", retain=True)
        self.pub(topic_node(node_id, "spacey"), msg, retain=True)

    def send_brightness(self, node_id: str, level: int):
        # 0-255
        msg = json.dumps({"level": int(level)})
        self.pub(topic_node(node_id, "brightness"), msg, retain=True)

    def send_white(self, node_id: str, level: int):
        # level 0-255 for simple white strips
        msg = json.dumps({"level": int(level)})
        self.pub(topic_node(node_id, "white"), msg, retain=True)

    def send_ota(self, node_id: str, url: str, retain: bool = False):
        msg = json.dumps({"now": url})
        if settings.MQTT_COMPAT_PUBLISH_BOTH:
            self.pub(settings.LEGACY_TOPIC_OTA, url, retain=retain)
        self.pub(topic_node(node_id, "ota"), msg, retain=retain)

    def send_motion(self, node_id: str, enabled: bool):
        msg = json.dumps({"enabled": bool(enabled)})
        self.pub(topic_node(node_id, "motion"), msg, retain=True)

    def all_off(self):
        """Turn off all known nodes regardless of location."""
        # Broadcast-style: set RGB to 0 and brightness to 0 for each node
        for _, _, n in registry.iter_nodes():
            nid = n["id"]
            self.send_color(nid, 0, 0, 0)
            self.send_brightness(nid, 0)
            self.send_effect(nid, "static")  # ensure static black holds
