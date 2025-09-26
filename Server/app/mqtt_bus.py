import json
import threading
import time
from typing import Dict, List, Optional, Tuple, Union

import paho.mqtt.client as paho

from .mqtt_tls import connect_mqtt_client


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
        enable_logger = getattr(self.client, "enable_logger", None)
        if callable(enable_logger):
            enable_logger()
        connect_mqtt_client(
            self.client,
            keepalive=30,
            start_async=True,
            raise_on_failure=False,
        )
        self._node_next_publish: Dict[str, float] = {}
        self._pending_commands: Dict[str, PendingCommand] = {}
        self._rate_condition = threading.Condition()
        self._shutdown = False
        self._rate_thread: Optional[threading.Thread] = None
        self._loop_thread: Optional[threading.Thread] = None
        self._loop_running = False
        self._rate_thread = threading.Thread(target=self._rate_worker, daemon=True)
        self._rate_thread.start()
        loop_start = getattr(self.client, "loop_start", None)
        if callable(loop_start):
            loop_start()
            self._loop_running = True
        else:
            self._loop_thread = threading.Thread(
                target=self.client.loop_forever,
                daemon=True,
            )
            self._loop_thread.start()

    def pub(
        self,
        topic: str,
        payload: Dict[str, object],
        retain: bool = False,
        rate_limited: bool = True,
    ):
        """Publish a command payload to the given topic.

        Most commands should *not* be retained to prevent them from being
        re-applied after a reboot.  Callers can enable the ``retain`` flag for
        those commands, such as ``ws/set`` and ``white/set``, where remembering
        the last value is desirable so lights resume their previous state after
        reconnecting.

        When ``rate_limited`` is ``True`` (the default), commands destined for a
        specific node are throttled so firmware is not flooded.  Setting
        ``rate_limited`` to ``False`` bypasses the queue entirely so commands
        publish immediately.
        """
        node_id = self._node_from_topic(topic)
        payload_json = json.dumps(payload)
        if not node_id:
            self.client.publish(topic, payload=payload_json, qos=1, retain=retain)
            return
        if not rate_limited:
            with self._rate_condition:
                self._pending_commands.pop(node_id, None)
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
        try:
            if self._loop_running:
                loop_stop = getattr(self.client, "loop_stop", None)
                if callable(loop_stop):
                    loop_stop()
                self._loop_running = False
            self.client.disconnect()
        except Exception:
            pass
        if self._rate_thread is not None:
            self._rate_thread.join(timeout=5.0)
        if self._loop_thread is not None:
            self._loop_thread.join(timeout=5.0)

    # ---- WS strip commands ----
    def ws_set(
        self,
        node_id: str,
        strip: int,
        effect: str,
        brightness: int,
        params: Optional[List[Union[float, str]]] = None,
        rate_limited: bool = True,
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
        self.pub(
            topic_cmd(node_id, f"ws/set/{strip}"),
            msg,
            retain=True,
            rate_limited=rate_limited,
        )

    # ---- RGB strip commands ----
    def rgb_set(
        self,
        node_id: str,
        strip: int,
        effect: str,
        brightness: int,
        params: Optional[List[int]] = None,
        rate_limited: bool = True,
    ):
        msg: Dict[str, object] = {
            "strip": int(strip),
            "effect": effect,
            "brightness": int(brightness),
            "params": params if params is not None else [],
        }
        self.pub(
            topic_cmd(node_id, f"rgb/set/{strip}"),
            msg,
            retain=True,
            rate_limited=rate_limited,
        )

    # ---- White channel commands ----
    def white_set(
        self,
        node_id: str,
        channel: int,
        effect: str,
        brightness: int,
        params: Optional[List[float]] = None,
        rate_limited: bool = True,
    ):
        msg: Dict[str, object] = {
            "channel": int(channel),
            "effect": effect,
            "brightness": int(brightness),
            "params": params or [],
        }
        # Publish to a channel-specific topic so each retained message stores
        # the last state for that channel independently.
        self.pub(
            topic_cmd(node_id, f"white/set/{channel}"),
            msg,
            retain=True,
            rate_limited=rate_limited,
        )

    # ---- Sensor commands ----
    def sensor_motion_program(self, node_id: str, states: Dict[str, object]):
        """Program motion state commands on the node."""
        self.pub(topic_cmd(node_id, "sensor/motion"), states)

    def motion_status_request(self, node_id: str, *, rate_limited: bool = False) -> None:
        """Request the motion module status from ``node_id``."""
        self.pub(
            topic_cmd(node_id, "motion/status"),
            {},
            retain=False,
            rate_limited=rate_limited,
        )

    def motion_off(self, node_id: str, payload: Dict[str, object]) -> None:
        """Publish a motion clear command for ``node_id`` without rate limiting."""

        self.pub(
            topic_cmd(node_id, "motion/off"),
            payload,
            retain=False,
            rate_limited=False,
        )

    def status_request(self, node_id: str) -> None:
        """Request a full status snapshot from ``node_id``."""
        self.pub(topic_cmd(node_id, "status"), {}, retain=False)

    # ---- OTA ----
    def ota_check(self, node_id: str):
        """Trigger an OTA update check without retaining the command."""
        self.pub(topic_cmd(node_id, "ota/check"), {}, retain=False)

