#!/usr/bin/env python3
"""MQTT bridge between UltraLights and an external RGB endpoint.

This service makes a non-ESP32 device (e.g. an OpenRGB PC) behave as a
first-class UltraLights node.  It subscribes to the same MQTT command
topics as ESP32 firmware, tracks its own state, publishes heartbeats so
the server shows the node as online, and responds to status snapshot
requests with current values.

The actual light control is delegated to a local HTTP endpoint (e.g. a
FastAPI app wrapping OpenRGB).  Only the MQTT ↔ HTTP translation lives
here — all fine-grained OpenRGB logic belongs in that separate app.

Usage:
    python openrgb_bridge.py --node-id <id> [options]

Environment variables (override CLI flags):
    UL_NODE_ID          Node ID assigned by the UltraLights server
    UL_BROKER_HOST      MQTT broker hostname   (default: 127.0.0.1)
    UL_BROKER_PORT      MQTT broker port        (default: 1883)
    UL_BROKER_USER      MQTT username            (optional)
    UL_BROKER_PASS      MQTT password            (optional)
    UL_RGB_ENDPOINT     Local RGB control URL    (default: http://127.0.0.1:9100)
    UL_HEARTBEAT_SEC    Heartbeat interval       (default: 10)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
from typing import Any, Dict, Optional

import paho.mqtt.client as mqtt

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("openrgb_bridge")

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class NodeState:
    """Thread-safe current state of the single RGB strip."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.effect: str = "solid"
        self.brightness: int = 255
        self.params: list = [255, 255, 255]
        self.start_time: float = time.time()
        self._fade_timer: Optional[threading.Timer] = None

    def update(self, effect: str, brightness: int, params: list) -> None:
        with self._lock:
            self.effect = effect
            self.brightness = brightness
            self.params = list(params)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "effect": self.effect,
                "brightness": self.brightness,
                "params": list(self.params),
            }

    @property
    def uptime_s(self) -> int:
        return int(time.time() - self.start_time)

    def cancel_fade(self) -> None:
        with self._lock:
            if self._fade_timer is not None:
                self._fade_timer.cancel()
                self._fade_timer = None

    def set_fade_timer(self, timer: threading.Timer) -> None:
        with self._lock:
            if self._fade_timer is not None:
                self._fade_timer.cancel()
            self._fade_timer = timer


state = NodeState()

# ---------------------------------------------------------------------------
# MQTT helpers
# ---------------------------------------------------------------------------

def topic_cmd(node_id: str, path: str) -> str:
    return f"ul/{node_id}/cmd/{path}"


def topic_evt(node_id: str) -> str:
    return f"ul/{node_id}/evt/status"


def publish_ack(
    client: mqtt.Client,
    node_id: str,
    strip: int,
    effect: str,
    brightness: int,
    params: list,
) -> None:
    payload = {
        "event": "ack",
        "status": "ok",
        "strip": strip,
        "brightness": brightness,
        "effect": effect,
        "params": params,
    }
    client.publish(topic_evt(node_id), json.dumps(payload), qos=1)


def publish_heartbeat(client: mqtt.Client, node_id: str) -> None:
    payload = {"event": "ack", "status": "ok"}
    client.publish(topic_evt(node_id), json.dumps(payload), qos=1)


def publish_snapshot(client: mqtt.Client, node_id: str) -> None:
    s = state.snapshot()
    payload = {
        "event": "snapshot",
        "node": node_id,
        "pir_enabled": False,
        "uptime_s": state.uptime_s,
        "ws": [],
        "rgb": [
            {
                "strip": 0,
                "enabled": True,
                "effect": s["effect"],
                "brightness": s["brightness"],
                "params": s["params"],
                "pwm_hz": 0,
                "channels": [],
                "color": s["params"][:3] if len(s["params"]) >= 3 else [0, 0, 0],
            }
        ],
        "white": [],
    }
    client.publish(topic_evt(node_id), json.dumps(payload), qos=1)

# ---------------------------------------------------------------------------
# RGB endpoint forwarding
# ---------------------------------------------------------------------------

def forward_to_endpoint(endpoint: str, effect: str, brightness: int, params: list) -> None:
    """POST the colour command to the local FastAPI RGB controller."""
    if requests is None:
        log.warning("requests library not installed — skipping endpoint forwarding")
        return
    body = {"effect": effect, "brightness": brightness, "params": params}
    try:
        resp = requests.post(f"{endpoint}/rgb", json=body, timeout=2)
        if resp.status_code >= 400:
            log.warning("RGB endpoint returned %d: %s", resp.status_code, resp.text[:200])
    except requests.RequestException as exc:
        log.warning("RGB endpoint unreachable: %s", exc)

# ---------------------------------------------------------------------------
# MQTT callbacks
# ---------------------------------------------------------------------------

def make_on_connect(node_id: str):
    def on_connect(client: mqtt.Client, userdata, flags, rc, properties=None):
        log.info("Connected to broker (rc=%s), subscribing to ul/%s/cmd/#", rc, node_id)
        client.subscribe(f"ul/{node_id}/cmd/#", qos=1)
        # Publish an initial snapshot so the server knows we're online
        publish_snapshot(client, node_id)
    return on_connect


def make_on_message(node_id: str, endpoint: str):
    def on_message(client: mqtt.Client, userdata, msg: mqtt.MQTTMessage):
        topic = msg.topic
        parts = topic.split("/")
        # Expected: ul/<node_id>/cmd/<module>/...
        if len(parts) < 4 or parts[0] != "ul" or parts[1] != node_id:
            return

        cmd_path = "/".join(parts[3:])

        # --- Status request ---
        if cmd_path == "status":
            log.info("Status request received")
            publish_snapshot(client, node_id)
            return

        # --- RGB set ---
        if cmd_path.startswith("rgb/set"):
            try:
                data = json.loads(msg.payload.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                log.warning("Invalid JSON payload on %s", topic)
                return

            effect = data.get("effect", "solid")
            brightness = int(data.get("brightness", 255))
            params = data.get("params", [0, 0, 0])
            strip = int(data.get("strip", 0))

            state.cancel_fade()
            state.update(effect, brightness, params)
            log.info("RGB set: effect=%s brightness=%d params=%s", effect, brightness, params)

            forward_to_endpoint(endpoint, effect, brightness, params)
            publish_ack(client, node_id, strip, effect, brightness, params)
            return

        # --- Motion off (fade to black) ---
        if cmd_path == "motion/off":
            try:
                data = json.loads(msg.payload.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                data = {}
            fade = data.get("fade", {})
            duration_ms = int(fade.get("duration_ms", 2000))
            if duration_ms <= 0:
                # Immediate off
                state.cancel_fade()
                state.update("solid", 0, [0, 0, 0])
                forward_to_endpoint(endpoint, "solid", 0, [0, 0, 0])
            else:
                duration_s = duration_ms / 1000.0

                def _fade_done():
                    state.update("solid", 0, [0, 0, 0])
                    forward_to_endpoint(endpoint, "solid", 0, [0, 0, 0])
                    log.info("Motion fade complete — lights off")

                timer = threading.Timer(duration_s, _fade_done)
                state.set_fade_timer(timer)
                timer.start()
                log.info("Motion off: fading over %dms", duration_ms)
            return

        # --- Motion on (cancel fade) ---
        if cmd_path == "motion/on":
            state.cancel_fade()
            log.info("Motion on: fade cancelled")
            return

    return on_message

# ---------------------------------------------------------------------------
# Heartbeat thread
# ---------------------------------------------------------------------------

def heartbeat_loop(client: mqtt.Client, node_id: str, interval: float, stop_event: threading.Event):
    while not stop_event.is_set():
        publish_heartbeat(client, node_id)
        stop_event.wait(interval)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="UltraLights MQTT bridge for external RGB devices")
    p.add_argument("--node-id", default=os.getenv("UL_NODE_ID", ""), help="Node ID from UltraLights server")
    p.add_argument("--broker-host", default=os.getenv("UL_BROKER_HOST", "127.0.0.1"))
    p.add_argument("--broker-port", type=int, default=int(os.getenv("UL_BROKER_PORT", "1883")))
    p.add_argument("--broker-user", default=os.getenv("UL_BROKER_USER", ""))
    p.add_argument("--broker-pass", default=os.getenv("UL_BROKER_PASS", ""))
    p.add_argument("--rgb-endpoint", default=os.getenv("UL_RGB_ENDPOINT", "http://127.0.0.1:9100"))
    p.add_argument("--heartbeat-sec", type=float, default=float(os.getenv("UL_HEARTBEAT_SEC", "10")))
    return p.parse_args()


def main() -> None:
    args = parse_args()

    if not args.node_id:
        print("Error: --node-id is required (or set UL_NODE_ID)", file=sys.stderr)
        sys.exit(1)

    node_id = args.node_id
    log.info("Starting bridge for node %s", node_id)
    log.info("Broker: %s:%d  RGB endpoint: %s", args.broker_host, args.broker_port, args.rgb_endpoint)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if args.broker_user:
        client.username_pw_set(args.broker_user, args.broker_pass)

    client.on_connect = make_on_connect(node_id)
    client.on_message = make_on_message(node_id, args.rgb_endpoint)

    client.connect(args.broker_host, args.broker_port, keepalive=30)

    stop = threading.Event()
    hb_thread = threading.Thread(
        target=heartbeat_loop,
        args=(client, node_id, args.heartbeat_sec, stop),
        daemon=True,
    )
    hb_thread.start()

    def shutdown(signum, frame):
        log.info("Shutting down…")
        stop.set()
        client.disconnect()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    client.loop_forever()


if __name__ == "__main__":
    main()
