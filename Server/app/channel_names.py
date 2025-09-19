"""Persistence helpers for per-channel custom names."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Dict, Optional

from .config import settings


class ChannelNameStore:
    """Persistence helper for per-channel user-provided names."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._data = self._load()

    def _load(self) -> Dict[str, Dict[str, Dict[str, str]]]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text())
        except Exception:
            return {}

        data: Dict[str, Dict[str, Dict[str, str]]] = {}
        if not isinstance(payload, dict):
            return data

        for node_id, modules in payload.items():
            if not isinstance(modules, dict):
                continue
            node_key = str(node_id)
            node_entries: Dict[str, Dict[str, str]] = {}
            for module, channels in modules.items():
                if not isinstance(channels, dict):
                    continue
                module_entries: Dict[str, str] = {}
                for channel, name in channels.items():
                    if not isinstance(name, str):
                        continue
                    clean = name.strip()
                    if not clean:
                        continue
                    module_entries[str(channel)] = clean
                if module_entries:
                    node_entries[str(module)] = module_entries
            if node_entries:
                data[node_key] = node_entries
        return data

    def _save_locked(self) -> None:
        serialized = json.dumps(self._data, indent=2, ensure_ascii=False)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(serialized)
        tmp_path.replace(self.path)

    def save(self) -> None:
        with self._lock:
            self._save_locked()

    def _cleanup(self, node_key: str, module_key: str) -> None:
        node_entries = self._data.get(node_key)
        if not node_entries:
            return
        module_entries = node_entries.get(module_key)
        if module_entries is not None and not module_entries:
            node_entries.pop(module_key, None)
        if not node_entries:
            self._data.pop(node_key, None)

    def get_name(self, node_id: str, module: str, channel: int) -> Optional[str]:
        with self._lock:
            module_entries = self._data.get(str(node_id), {}).get(str(module), {})
            value = module_entries.get(str(channel))
            if value is None:
                return None
            return str(value)

    def set_name(
        self, node_id: str, module: str, channel: int, name: Optional[str]
    ) -> Optional[str]:
        node_key = str(node_id)
        module_key = str(module)
        channel_key = str(channel)
        with self._lock:
            if name is None:
                module_entries = self._data.get(node_key, {}).get(module_key)
                if not module_entries or channel_key not in module_entries:
                    return None
                module_entries.pop(channel_key, None)
                self._cleanup(node_key, module_key)
                self._save_locked()
                return None

            clean = str(name).strip()
            if not clean:
                # Empty strings behave the same as clearing the name.
                module_entries = self._data.get(node_key, {}).get(module_key)
                if module_entries and channel_key in module_entries:
                    module_entries.pop(channel_key, None)
                    self._cleanup(node_key, module_key)
                    self._save_locked()
                return None

            if len(clean) > 80:
                clean = clean[:80]

            node_entries = self._data.setdefault(node_key, {})
            module_entries = node_entries.setdefault(module_key, {})
            module_entries[channel_key] = clean
            self._save_locked()
            return clean

    def get_names_for_node(self, node_id: str) -> Dict[str, Dict[str, str]]:
        with self._lock:
            modules = self._data.get(str(node_id))
            if not modules:
                return {}
            return {
                module: dict(channels)
                for module, channels in modules.items()
            }


channel_names = ChannelNameStore(settings.CHANNEL_NAMES_FILE)

