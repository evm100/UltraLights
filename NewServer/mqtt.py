import json
import os
import threading

import paho.mqtt.client as mqtt

MQTT_BROKER = os.getenv("MQTT_BROKER", "localhost")
NODE_ID = os.getenv("ULTRALIGHT_NODE", "node")

_status_event = threading.Event()
_status_payload = {}


def _on_connect(client, userdata, flags, rc):
    client.subscribe(f"ul/{NODE_ID}/evt/status")


def _on_message(client, userdata, msg):
    global _status_payload
    if msg.topic == f"ul/{NODE_ID}/evt/status":
        try:
            _status_payload = json.loads(msg.payload.decode())
        except json.JSONDecodeError:
            _status_payload = {}
        _status_event.set()


client = mqtt.Client()
client.on_connect = _on_connect
client.on_message = _on_message
client.connect(MQTT_BROKER)
client.loop_start()


def publish(topic: str, payload: dict) -> None:
    client.publish(f"ul/{NODE_ID}/{topic}", json.dumps(payload), qos=1)


def request_status(timeout: float = 2.0) -> dict:
    """Request a status snapshot from the node via MQTT."""
    _status_event.clear()
    publish("cmd/status", {})
    if _status_event.wait(timeout):
        return _status_payload
    return {}
