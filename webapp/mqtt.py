"""MQTT helper used by the web application.

It maintains a connection to the broker and tracks status reports from
UltraNodes so the web UI can show connectivity information.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Dict, Any

import paho.mqtt.client as mqtt

# Broker host can be overridden via environment variable if desired.
MQTT_BROKER = "localhost"

# Map of node-id -> {"last_seen": datetime, "payload": dict}
_status: Dict[str, Dict[str, Any]] = {}
_client: mqtt.Client | None = None


def _on_connect(client: mqtt.Client, userdata, flags, rc):
    client.subscribe("ul/+/evt/status")


def _on_message(client: mqtt.Client, userdata, msg: mqtt.MQTTMessage):
    parts = msg.topic.split("/")
    if len(parts) >= 4 and parts[3] == "status":
        node_id = parts[1]
        try:
            payload = json.loads(msg.payload.decode())
        except json.JSONDecodeError:
            payload = {}
        _status[node_id] = {
            "last_seen": datetime.utcnow(),
            "payload": payload,
        }


def init_client() -> mqtt.Client:
    """Initialise and start the MQTT client."""
    global _client
    client = mqtt.Client()
    client.on_connect = _on_connect
    client.on_message = _on_message
    client.connect(MQTT_BROKER)
    client.loop_start()
    _client = client
    return client


def stop_client():
    """Shut down the MQTT client."""
    if _client is not None:
        _client.loop_stop()
        _client.disconnect()


def publish(node_id: str, cmd: str, payload: Dict[str, Any]):
    """Publish a command to the given node."""
    if _client is None:
        raise RuntimeError("MQTT client not initialised")
    topic = f"ul/{node_id}/cmd/{cmd}"
    _client.publish(topic, json.dumps(payload), qos=1)


def get_status(node_id: str) -> Dict[str, Any]:
    """Return latest status info for a node."""
    info = _status.get(node_id)
    if not info:
        return {"connected": False}
    connected = datetime.utcnow() - info["last_seen"] < timedelta(seconds=30)
    return {"connected": connected, "payload": info["payload"]}
