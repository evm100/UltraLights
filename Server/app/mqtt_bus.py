import asyncio
import threading
import json
import time
from typing import Any, Optional, List, Dict, Union, Tuple
import paho.mqtt.client as paho
from .config import settings
from . import registry
from .status_monitor import status_monitor

def topic_cmd(node_id: str, path: str) -> str:
    return f"ul/{node_id}/cmd/{path}"

# Limit outbound command traffic per node to 5 Hz so firmware isn't flooded.
NODE_COMMAND_RATE_HZ = 5.0
NODE_COMMAND_INTERVAL = 1.0 / NODE_COMMAND_RATE_HZ


PendingCommand = Tuple[str, str, bool]


class MqttBus:
    def __init__(self, client_id: str = "ultralights-ui"):
        self.client = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id=client_id)
        # Allow a larger number of QoS>0 messages to be in-flight before blocking
        self.client.max_inflight_messages_set(200)
        # Ensure queued messages are buffered rather than rejected when under load
        self.client.max_queued_messages_set(0)
        self.client.connect(settings.BROKER_HOST, settings.BROKER_PORT, keepalive=30)
        self._node_next_publish: Dict[str, float] = {}
        self._pending_commands: Dict[str, PendingCommand] = {}
        self._rate_condition = threading.Condition()
        self._shutdown = False
        self._rate_thread = threading.Thread(target=self._rate_worker, daemon=True)
        self.thread = threading.Thread(target=self.client.loop_forever, daemon=True)
        self._rate_thread.start()
        self.thread.start()

    def pub(self, topic: str, payload: Dict[str, object], retain: bool = False):
        """Publish a command payload to the given topic.

        Most commands should *not* be retained to prevent them from being
        re-applied after a reboot.  Callers can enable the ``retain`` flag for
        those commands, such as ``ws/set`` and ``white/set``, where remembering
        the last value is desirable so lights resume their previous state after
        reconnecting.
        """
        node_id = self._node_from_topic(topic)
        payload_json = json.dumps(payload)
        if not node_id:
            self.client.publish(topic, payload=payload_json, qos=1, retain=retain)
            return
        with self._rate_condition:
            self._pending_commands[node_id] = (topic, payload_json, retain)
            self._rate_condition.notify()

    def _rate_worker(self) -> None:
        while True:
            command: Optional[PendingCommand] = None
            with self._rate_condition:
                while not self._shutdown and command is None:
                    now = time.monotonic()
                    ready_node: Optional[str] = None
                    next_ready_time: Optional[float] = None
                    for node_id in self._pending_commands:
                        node_ready = self._node_next_publish.get(node_id, 0.0)
                        if node_ready <= now:
                            ready_node = node_id
                            break
                        if next_ready_time is None or node_ready < next_ready_time:
                            next_ready_time = node_ready
                    if ready_node is not None:
                        command = self._pending_commands.pop(ready_node)
                        self._node_next_publish[ready_node] = now + NODE_COMMAND_INTERVAL
                        break
                    if not self._pending_commands:
                        self._rate_condition.wait()
                    else:
                        wait_time = NODE_COMMAND_INTERVAL
                        if next_ready_time is not None:
                            wait_time = min(
                                NODE_COMMAND_INTERVAL,
                                max(0.0, next_ready_time - now),
                            )
                        self._rate_condition.wait(timeout=wait_time)
                if self._shutdown:
                    return
            if command is None:
                continue
            topic, payload, retain = command
            self.client.publish(topic, payload=payload, qos=1, retain=retain)

    @staticmethod
    def _node_from_topic(topic: str) -> Optional[str]:
        parts = topic.split("/")
        if len(parts) >= 3 and parts[0] == "ul" and parts[2] == "cmd":
            return parts[1]
        return None

    def shutdown(self) -> None:
        with self._rate_condition:
            self._shutdown = True
            self._rate_condition.notify_all()
        self.client.disconnect()
        self._rate_thread.join(timeout=5.0)
        self.thread.join(timeout=5.0)

_BUS: Optional["MqttBus"] = None
_BUS_LOCK = threading.Lock()


def get_bus() -> "MqttBus":
    global _BUS
    if _BUS is not None:
        return _BUS
    with _BUS_LOCK:
        if _BUS is None:
            _BUS = MqttBus()
    return _BUS


async def request_status_snapshot(node_id: str, timeout: float = 1.0) -> Dict[str, Any]:
    """Request a live status snapshot and return capability metadata."""

    bus = get_bus()
    before = status_monitor.capabilities_for(node_id)
    previous_ts = before.get("timestamp")

    bus.pub(topic_cmd(node_id, "status"), {})

    deadline = time.monotonic() + max(0.0, float(timeout))
    poll_delay = 0.05

    while True:
        await asyncio.sleep(poll_delay)
        current = status_monitor.capabilities_for(node_id)
        current_ts = current.get("timestamp")
        if current_ts is not None and (previous_ts is None or current_ts > previous_ts):
            current["fresh"] = True
            return current
        if time.monotonic() >= deadline:
            fallback = current if current.get("module_keys") or current.get("modules") else before
            if not isinstance(fallback, dict):
                fallback = {}
            modules = fallback.get("modules")
            if not isinstance(modules, dict):
                modules = {}
            result: Dict[str, Any] = {
                "module_keys": list(fallback.get("module_keys", [])),
                "modules": modules,
                "timestamp": fallback.get("timestamp"),
                "payload": fallback.get("payload"),
                "fresh": False,
            }
            return result

    # ---- WS strip commands ----
    def ws_set(
        self,
        node_id: str,
        strip: int,
        effect: str,
        brightness: int,
        params: Optional[List[Union[float, str]]] = None,
    ):
        msg: Dict[str, object] = {
            "strip": int(strip),
            "effect": effect,
            "brightness": int(brightness),
            "params": params if params is not None else [],
        }
        # Retain state per-strip by publishing to a unique sub-topic.  The
        # ``strip`` field is kept in the payload for compatibility with older
        # firmware that still expects it.
        self.pub(topic_cmd(node_id, f"ws/set/{strip}"), msg, retain=True)

    # ---- RGB strip commands ----
    def rgb_set(
        self,
        node_id: str,
        strip: int,
        effect: str,
        brightness: int,
        params: Optional[List[int]] = None,
    ):
        msg: Dict[str, object] = {
            "strip": int(strip),
            "effect": effect,
            "brightness": int(brightness),
            "params": params if params is not None else [],
        }
        self.pub(topic_cmd(node_id, f"rgb/set/{strip}"), msg, retain=True)

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
            "params": params or [],
        }
        # Publish to a channel-specific topic so each retained message stores
        # the last state for that channel independently.
        self.pub(topic_cmd(node_id, f"white/set/{channel}"), msg, retain=True)

    # ---- Sensor commands ----
    def sensor_cooldown(self, node_id: str, seconds: int):
        msg = {"seconds": int(seconds)}
        self.pub(topic_cmd(node_id, "sensor/cooldown"), msg)

    def sensor_motion_program(self, node_id: str, states: Dict[str, object]):
        """Program motion state commands on the node."""
        self.pub(topic_cmd(node_id, "sensor/motion"), states)

    # ---- OTA ----
    def ota_check(self, node_id: str):
        """Trigger an OTA update check without retaining the command."""
        self.pub(topic_cmd(node_id, "ota/check"), {}, retain=False)

    def all_off(self):
        """Turn off all known nodes."""
        for _, _, n in registry.iter_nodes():
            nid = n["id"]
            for i in range(4):
                self.ws_set(nid, i, "solid", 255, [0, 0, 0])
                self.rgb_set(nid, i, "solid", 0, [0, 0, 0])
                self.white_set(nid, i, "solid", 0)

