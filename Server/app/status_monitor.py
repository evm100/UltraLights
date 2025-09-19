"""Track node heartbeat/status messages from MQTT."""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, Optional

import paho.mqtt.client as mqtt

from .config import settings
from .node_capabilities import (
    FALSEY_STRINGS,
    copy_capability_indexes,
    coerce_index,
)


class StatusMonitor:
    """Subscribe to node status topics and track their last "ok" heartbeat."""

    def __init__(self, timeout: int = 30) -> None:
        self.timeout = timeout
        self.client = mqtt.Client()
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._last_seen: Dict[str, float] = {}
        self._last_ok: Dict[str, float] = {}
        self._last_payload: Dict[str, Any] = {}
        self._capabilities: Dict[str, Dict[str, Any]] = {}
        self._capability_ts: Dict[str, float] = {}
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
        modules: list[str] = []
        indexes: Dict[str, Dict[str, Any]] = {}
        if isinstance(payload, dict):
            modules, indexes = self._extract_capabilities(payload)
        with self._condition:
            self._last_seen[node_id] = now
            self._last_payload[node_id] = payload
            if status_value == "ok":
                self._last_ok[node_id] = now
            if modules or indexes:
                self._capabilities[node_id] = {
                    "modules": modules,
                    "indexes": copy_capability_indexes(indexes),
                }
                self._capability_ts[node_id] = now
            self._condition.notify_all()

    # ------------------------------------------------------------------
    # Public helpers
    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        """Return a shallow copy of the current status information."""
        now = time.time()
        with self._condition:
            keys = set(self._last_seen) | set(self._last_ok)
            data: Dict[str, Dict[str, Any]] = {}
            for node_id in keys:
                last_seen = self._last_seen.get(node_id)
                last_ok = self._last_ok.get(node_id)
                payload = self._last_payload.get(node_id)
                status_value = None
                if isinstance(payload, dict):
                    status_value = payload.get("status")
                data[node_id] = {
                    "online": bool(last_ok and now - last_ok <= self.timeout),
                    "last_seen": last_seen,
                    "last_ok": last_ok,
                    "status": status_value,
                    "payload": payload,
                }
        return data

    def status_for(self, node_id: str) -> Dict[str, Any]:
        """Return status information for ``node_id``."""
        snapshot = self.snapshot()
        return snapshot.get(
            node_id,
            {"online": False, "last_seen": None, "last_ok": None, "status": None, "payload": None},
        )

    def capabilities_for(self, node_id: str) -> Dict[str, Any]:
        """Return the latest parsed module capability data for ``node_id``."""

        with self._condition:
            return self._current_capabilities_locked(node_id)

    def wait_for_capabilities(
        self, node_id: str, after: Optional[float], timeout: float
    ) -> Dict[str, Any]:
        """Block until capabilities newer than ``after`` are observed or timeout."""

        deadline = time.time() + max(0.0, timeout)
        with self._condition:
            while True:
                updated_at = self._capability_ts.get(node_id)
                if updated_at is not None and (after is None or updated_at > after):
                    break
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                self._condition.wait(timeout=remaining)
            return self._current_capabilities_locked(node_id)

    # ------------------------------------------------------------------
    # Internal helpers
    def _current_capabilities_locked(self, node_id: str) -> Dict[str, Any]:
        payload = self._last_payload.get(node_id)
        updated_at = self._capability_ts.get(node_id)
        data = self._capabilities.get(node_id, {})
        modules = list(data.get("modules", []))
        indexes = copy_capability_indexes(data.get("indexes", {})) if data else {}
        return {
            "modules": modules,
            "indexes": indexes,
            "payload": payload,
            "updated_at": updated_at,
        }

    def _extract_capabilities(
        self, payload: Dict[str, Any]
    ) -> tuple[list[str], Dict[str, Dict[str, Any]]]:
        modules: list[str] = []
        indexes: Dict[str, Dict[str, Any]] = {}

        def handle(module: str, key: str, index_field: str) -> None:
            entries = payload.get(key)
            data = self._collect_indexes(entries, index_field)
            if data["available"] or data["enabled"] or (
                isinstance(entries, list) and entries
            ):
                indexes[module] = data
                modules.append(module)

        handle("ws", "ws", "strip")
        handle("rgb", "rgb", "strip")
        handle("white", "white", "channel")

        if isinstance(payload.get("ota"), dict):
            modules.append("ota")
            indexes.setdefault("ota", {"available": [], "enabled": []})

        seen: set[str] = set()
        ordered_modules: list[str] = []
        for module in modules:
            if module in seen:
                continue
            seen.add(module)
            ordered_modules.append(module)
        return ordered_modules, indexes

    def _collect_indexes(self, entries: Any, index_field: str) -> Dict[str, list[int]]:
        available: set[int] = set()
        enabled: set[int] = set()
        if isinstance(entries, list):
            for item in entries:
                if not isinstance(item, dict):
                    continue
                idx = coerce_index(item.get(index_field))
                if idx is None:
                    continue
                available.add(idx)
                enabled_value = item.get("enabled", True)
                if isinstance(enabled_value, str):
                    flag = enabled_value.strip().lower() not in FALSEY_STRINGS
                else:
                    flag = bool(enabled_value)
                if flag:
                    enabled.add(idx)
        return {
            "available": sorted(available),
            "enabled": sorted(enabled),
        }


status_monitor = StatusMonitor()
