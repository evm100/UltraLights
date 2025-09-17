from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Dict, Optional

from .config import settings


class BrightnessLimitsStore:
    """Persistence helper for per-channel brightness limits."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._data = self._load()

    def _load(self) -> Dict[str, Dict[str, Dict[str, int]]]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text())
        except Exception:
            return {}

        data: Dict[str, Dict[str, Dict[str, int]]] = {}
        if not isinstance(payload, dict):
            return data

        for node_id, modules in payload.items():
            if not isinstance(modules, dict):
                continue
            node_key = str(node_id)
            node_limits: Dict[str, Dict[str, int]] = {}
            for module, channels in modules.items():
                if not isinstance(channels, dict):
                    continue
                module_limits: Dict[str, int] = {}
                for channel, value in channels.items():
                    if not isinstance(value, (int, float)):
                        continue
                    limit = int(value)
                    if 0 <= limit <= 255:
                        module_limits[str(channel)] = limit
                if module_limits:
                    node_limits[str(module)] = module_limits
            if node_limits:
                data[node_key] = node_limits
        return data

    def _save_locked(self) -> None:
        serialized = json.dumps(self._data, indent=2)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(serialized)
        tmp_path.replace(self.path)

    def save(self) -> None:
        with self._lock:
            self._save_locked()

    def get_limit(self, node_id: str, module: str, channel: int) -> Optional[int]:
        channel_key = str(channel)
        with self._lock:
            module_limits = (
                self._data.get(str(node_id), {}).get(str(module), {})
            )
            value = module_limits.get(channel_key)
            if value is None:
                return None
            return int(value)

    def set_limit(
        self, node_id: str, module: str, channel: int, limit: Optional[int]
    ) -> Optional[int]:
        node_key = str(node_id)
        module_key = str(module)
        channel_key = str(channel)
        with self._lock:
            if limit is None:
                node_limits = self._data.get(node_key)
                if not node_limits:
                    return None
                module_limits = node_limits.get(module_key)
                if not module_limits or channel_key not in module_limits:
                    return None
                module_limits.pop(channel_key, None)
                if not module_limits:
                    node_limits.pop(module_key, None)
                if not node_limits:
                    self._data.pop(node_key, None)
                self._save_locked()
                return None

            limit = max(0, min(255, int(limit)))
            node_limits = self._data.setdefault(node_key, {})
            module_limits = node_limits.setdefault(module_key, {})
            module_limits[channel_key] = limit
            self._save_locked()
            return limit

    def get_limits_for_node(self, node_id: str) -> Dict[str, Dict[str, int]]:
        with self._lock:
            modules = self._data.get(str(node_id))
            if not modules:
                return {}
            return {
                module: dict(channels)
                for module, channels in modules.items()
            }


brightness_limits = BrightnessLimitsStore(settings.BRIGHTNESS_LIMITS_FILE)

