"""Track node heartbeat/status messages from MQTT."""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, Iterable, Optional

import paho.mqtt.client as mqtt

from .config import settings


_FALSEY_STRINGS = {"", "0", "false", "no", "off", "disabled", "inactive"}


def _coerce_enabled(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in _FALSEY_STRINGS
    return bool(value)


def _as_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except Exception:
            return None
    return None


def _unique_ordered(values: Iterable[int]) -> list[int]:
    seen: set[int] = set()
    result: list[int] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _indices_from_summary(summary: Any) -> list[int]:
    if not isinstance(summary, dict):
        return []
    candidates = [
        summary.get("indices"),
        summary.get("strips"),
        summary.get("channels"),
    ]
    indices: list[int] = []
    for candidate in candidates:
        if isinstance(candidate, list):
            for item in candidate:
                idx = _as_int(item)
                if idx is not None:
                    indices.append(idx)
        if indices:
            break
    return _unique_ordered(indices)


def _indices_from_entries(entries: Any, index_key: str) -> list[int]:
    if not isinstance(entries, list):
        return []
    result: list[int] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        idx = _as_int(entry.get(index_key))
        if idx is not None:
            result.append(idx)
    return _unique_ordered(result)


def _normalize_capabilities(payload: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return None

    modules_summary = payload.get("modules")
    if modules_summary is not None and not isinstance(modules_summary, dict):
        modules_summary = None

    candidate_keys: list[str] = []
    if isinstance(modules_summary, dict):
        for key in modules_summary.keys():
            key_str = str(key).strip()
            if key_str:
                candidate_keys.append(key_str)

    for fallback in ("ws", "rgb", "white"):
        if fallback in payload and fallback not in candidate_keys:
            candidate_keys.append(fallback)

    if not candidate_keys:
        return None

    modules: Dict[str, Dict[str, Any]] = {}
    module_keys: list[str] = []

    for key in candidate_keys:
        summary_info = modules_summary.get(key) if isinstance(modules_summary, dict) else None
        indices = _indices_from_summary(summary_info)

        entry_field = "strip"
        if key == "white":
            entry_field = "channel"

        entries = payload.get(key)
        if indices:
            entry_indices: list[int] = []
        else:
            entry_indices = _indices_from_entries(entries, entry_field)
        if entry_indices:
            indices = _unique_ordered(list(indices) + entry_indices)

        enabled_flag: Optional[bool] = None
        if isinstance(summary_info, dict) and "enabled" in summary_info:
            enabled_flag = _coerce_enabled(summary_info.get("enabled"))
        if enabled_flag is None:
            if isinstance(entries, list):
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    if "enabled" in entry:
                        enabled_flag = _coerce_enabled(entry.get("enabled"))
                        break
            if enabled_flag is None:
                enabled_flag = bool(indices)

        modules[key] = {
            "indices": indices,
            "enabled": bool(enabled_flag),
        }
        if isinstance(summary_info, dict):
            modules[key]["summary"] = summary_info
        if isinstance(entries, list):
            modules[key]["status"] = entries
        if modules[key]["enabled"]:
            module_keys.append(key)

    return {"module_keys": module_keys, "modules": modules}


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
        self._capabilities: Dict[str, Dict[str, Any]] = {}
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
            if status_value == "ok":
                self._last_ok[node_id] = now
            capabilities = _normalize_capabilities(payload)
            if capabilities is not None:
                self._capabilities[node_id] = {
                    "timestamp": now,
                    "module_keys": capabilities["module_keys"],
                    "modules": capabilities["modules"],
                }

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
                data[node_id] = {
                    "online": bool(last_ok and now - last_ok <= self.timeout),
                    "last_seen": last_seen,
                    "last_ok": last_ok,
                    "status": status_value,
                    "signal_dbi": signal_value,
                    "payload": payload,
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
            },
        )

    def capabilities_for(self, node_id: str) -> Dict[str, Any]:
        """Return normalized capability metadata for ``node_id``."""

        with self._lock:
            payload = self._last_payload.get(node_id)
            record = self._capabilities.get(node_id)
            if not record:
                return {
                    "timestamp": None,
                    "module_keys": [],
                    "modules": {},
                    "payload": payload,
                }
            modules_copy: Dict[str, Dict[str, Any]] = {}
            for key, meta in record.get("modules", {}).items():
                copy: Dict[str, Any] = {
                    "indices": list(meta.get("indices", [])),
                    "enabled": bool(meta.get("enabled", False)),
                }
                if "status" in meta:
                    status_entries = meta["status"]
                    if isinstance(status_entries, list):
                        copy["status"] = status_entries
                if "summary" in meta:
                    copy["summary"] = meta["summary"]
                modules_copy[key] = copy
            return {
                "timestamp": record.get("timestamp"),
                "module_keys": list(record.get("module_keys", [])),
                "modules": modules_copy,
                "payload": payload,
            }

    def forget(self, node_id: str) -> None:
        """Drop any cached status information for ``node_id``."""
        with self._lock:
            self._last_seen.pop(node_id, None)
            self._last_ok.pop(node_id, None)
            self._last_payload.pop(node_id, None)
            self._capabilities.pop(node_id, None)


status_monitor = StatusMonitor()
