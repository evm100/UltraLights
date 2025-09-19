"""Track node heartbeat/status messages from MQTT."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any, Dict, Optional, Sequence

import paho.mqtt.client as mqtt

from .config import settings
from .node_capabilities import NodeCapabilities


class StatusMonitor:
    """Subscribe to node status topics and track their last "ok" heartbeat."""

    def __init__(self, timeout: int = 30) -> None:
        self.timeout = timeout
        self.client = mqtt.Client()
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self._lock = threading.Lock()
        self._last_seen: Dict[str, float] = {}
        self._last_ok: Dict[str, float] = {}
        self._last_payload: Dict[str, Any] = {}
        self._metadata: Dict[str, NodeCapabilities] = {}
        self._running = False

    # ------------------------------------------------------------------
    # MQTT lifecycle
    def start(self) -> None:
        if self._running:
            return
        self.client.connect(settings.BROKER_HOST, settings.BROKER_PORT, keepalive=30)
        self.client.loop_start()
        self._running = True

    def stop(self) -> None:
        if not self._running:
            return
        self.client.loop_stop()
        self.client.disconnect()
        self._running = False

    # ------------------------------------------------------------------
    # MQTT callbacks
    def _on_connect(self, client: mqtt.Client, userdata, flags, rc) -> None:  # type: ignore[override]
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
            if isinstance(payload, dict):
                capabilities = NodeCapabilities.from_payload(payload)
                if capabilities:
                    self._metadata[node_id] = capabilities
            if status_value == "ok":
                self._last_ok[node_id] = now

    # ------------------------------------------------------------------
    # Public helpers
    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        """Return a shallow copy of the current status information."""
        now = time.time()
        with self._lock:
            keys = set(self._last_seen) | set(self._last_ok)
            data: Dict[str, Dict[str, Any]] = {}
            for node_id in keys:
                last_seen = self._last_seen.get(node_id)
                last_ok = self._last_ok.get(node_id)
                payload = self._last_payload.get(node_id)
                status_value = None
                signal_value = None
                if isinstance(payload, dict):
                    status_value = payload.get("status")
                    signal = payload.get("signal_dbi")
                    if isinstance(signal, (int, float)):
                        signal_value = float(signal)
                modules_data = None
                module_channels_data = None
                metadata_source = "registry"
                capabilities = self._metadata.get(node_id)
                if capabilities:
                    modules_data = list(capabilities.modules)
                    module_channels_data = {
                        key: list(indexes)
                        for key, indexes in capabilities.module_channels.items()
                    }
                    metadata_source = capabilities.source
                data[node_id] = {
                    "online": bool(last_ok and now - last_ok <= self.timeout),
                    "last_seen": last_seen,
                    "last_ok": last_ok,
                    "status": status_value,
                    "signal_dbi": signal_value,
                    "payload": payload,
                    "modules": modules_data,
                    "module_channels": module_channels_data,
                    "metadata_source": metadata_source,
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
                "status": None,
                "signal_dbi": None,
                "payload": None,
                "modules": None,
                "module_channels": None,
                "metadata_source": "registry",
            },
        )

    def capabilities_for(
        self, node_id: str, *, fallback_modules: Sequence[Any] | None = None
    ) -> NodeCapabilities:
        """Return cached module metadata for ``node_id``.

        ``fallback_modules`` should be the module definitions from the registry and
        will be used whenever no live MQTT snapshot is available.
        """

        fallback = NodeCapabilities.from_modules(fallback_modules)
        with self._lock:
            live = self._metadata.get(node_id)
        if live:
            return live.merged_with(fallback)
        return fallback

    async def wait_for_payload(
        self,
        node_id: str,
        *,
        after: Optional[float] = None,
        timeout: float = 2.0,
    ) -> Optional[Dict[str, Any]]:
        """Wait for a new status payload for ``node_id``.

        Returns the latest payload when a fresh message arrives after the ``after``
        timestamp or ``None`` when the timeout elapses.
        """

        deadline = time.monotonic() + max(timeout, 0.0)
        while True:
            with self._lock:
                payload = self._last_payload.get(node_id)
                last_seen = self._last_seen.get(node_id)
            if payload is not None and isinstance(last_seen, (int, float)):
                if after is None or last_seen > after:
                    return payload
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(0.05)

    def forget(self, node_id: str) -> None:
        """Drop any cached status information for ``node_id``."""
        with self._lock:
            self._last_seen.pop(node_id, None)
            self._last_ok.pop(node_id, None)
            self._last_payload.pop(node_id, None)
            self._metadata.pop(node_id, None)


status_monitor = StatusMonitor()
