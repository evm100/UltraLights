"""Track node heartbeat/status messages from MQTT."""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, Optional, Tuple

import paho.mqtt.client as mqtt

from .mqtt_tls import connect_mqtt_client


class StatusMonitor:
    """Subscribe to node status topics and track their last "ok" heartbeat."""

    def __init__(self, timeout: int = 30) -> None:
        self.timeout = timeout
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        enable_logger = getattr(self.client, "enable_logger", None)
        if callable(enable_logger):
            enable_logger()
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._last_seen: Dict[str, float] = {}
        self._last_ok: Dict[str, float] = {}
        self._last_snapshot: Dict[str, float] = {}
        self._last_payload: Dict[str, Any] = {}
        self._node_seq: Dict[str, int] = {}
        self._running = False
        self._loop_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # MQTT lifecycle
    def start(self) -> None:
        if self._running:
            return
        connect_mqtt_client(
            self.client,
            keepalive=30,
            start_async=True,
            raise_on_failure=False,
        )
        loop_start = getattr(self.client, "loop_start", None)
        if callable(loop_start):
            loop_start()
        else:
            self._loop_thread = threading.Thread(
                target=self.client.loop_forever,
                daemon=True,
            )
            self._loop_thread.start()
        self._running = True

    def stop(self) -> None:
        if not self._running:
            return
        loop_stop = getattr(self.client, "loop_stop", None)
        if callable(loop_stop):
            loop_stop()
        if self._loop_thread is not None:
            self._loop_thread.join(timeout=5.0)
            self._loop_thread = None
        self.client.disconnect()
        self._running = False

    # ------------------------------------------------------------------
    # MQTT callbacks
    def _on_connect(
        self, client: mqtt.Client, userdata, flags, reason_code, properties=None
    ) -> None:
        client.subscribe("ul/+/evt/status")

    def _on_message(self, client: mqtt.Client, userdata, msg: mqtt.MQTTMessage) -> None:  # type: ignore[override]
        topic = msg.topic or ""
        parts = topic.split("/")
        if len(parts) < 4 or parts[0] != "ul" or parts[2] != "evt":
            return
        node_id = parts[1]
        now = time.time()
        payload: Any = None
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception:
            payload = None
        status_value: Any = None
        if isinstance(payload, dict):
            status_value = payload.get("status")
        with self._lock:
            self._last_seen[node_id] = now
            self._last_payload[node_id] = payload
            if isinstance(payload, dict) and payload.get("event") == "snapshot":
                self._last_snapshot[node_id] = now
            if status_value == "ok":
                self._last_ok[node_id] = now
            seq = self._node_seq.get(node_id, 0) + 1
            self._node_seq[node_id] = seq
            self._condition.notify_all()

    # ------------------------------------------------------------------
    # Public helpers
    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        """Return a shallow copy of the current status information."""
        now = time.time()
        with self._lock:
            keys = set(self._last_seen) | set(self._last_ok) | set(self._last_snapshot)
            data: Dict[str, Dict[str, Any]] = {}
            for node_id in keys:
                last_seen = self._last_seen.get(node_id)
                last_ok = self._last_ok.get(node_id)
                last_snapshot = self._last_snapshot.get(node_id)
                payload = self._last_payload.get(node_id)
                status_value = None
                signal_value = None
                if isinstance(payload, dict):
                    status_value = payload.get("status")
                    signal = payload.get("signal_dbi")
                    if isinstance(signal, (int, float)):
                        signal_value = float(signal)
                online_by_status = bool(last_ok and now - last_ok <= self.timeout)
                online_by_snapshot = bool(
                    last_snapshot and now - last_snapshot <= self.timeout
                )
                data[node_id] = {
                    "online": online_by_status or online_by_snapshot,
                    "last_seen": last_seen,
                    "last_ok": last_ok,
                    "last_snapshot": last_snapshot,
                    "status": status_value,
                    "signal_dbi": signal_value,
                    "payload": payload,
                    "seq": self._node_seq.get(node_id, 0),
                }
        return data

    def status_for(self, node_id: str) -> Dict[str, Any]:
        """Return status information for ``node_id``."""
        snapshot = self.snapshot()
        return snapshot.get(
            node_id,
            {
                "online": False,
                "last_seen": None,
                "last_ok": None,
                "last_snapshot": None,
                "status": None,
                "signal_dbi": None,
                "payload": None,
                "seq": 0,
            },
        )

    def forget(self, node_id: str) -> None:
        """Drop any cached status information for ``node_id``."""
        with self._lock:
            self._last_seen.pop(node_id, None)
            self._last_ok.pop(node_id, None)
            self._last_snapshot.pop(node_id, None)
            self._last_payload.pop(node_id, None)
            self._node_seq.pop(node_id, None)

    def wait_for_snapshot(
        self, node_id: str, since_seq: int, timeout: float
    ) -> Tuple[int, Optional[Dict[str, Any]]]:
        """Block until a newer snapshot event is observed for ``node_id``.

        Returns a tuple of ``(sequence, payload)`` where ``payload`` is the
        decoded snapshot dictionary, or ``None`` when the wait times out.
        """

        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            while True:
                seq = self._node_seq.get(node_id, 0)
                payload = self._last_payload.get(node_id)
                if (
                    seq > since_seq
                    and isinstance(payload, dict)
                    and payload.get("event") == "snapshot"
                ):
                    return seq, payload

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    seq = self._node_seq.get(node_id, 0)
                    payload = self._last_payload.get(node_id)
                    if (
                        seq > since_seq
                        and isinstance(payload, dict)
                        and payload.get("event") == "snapshot"
                    ):
                        return seq, payload
                    return seq, None

                self._condition.wait(timeout=remaining)


status_monitor = StatusMonitor()
