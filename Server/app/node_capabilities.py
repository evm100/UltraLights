"""Helpers for deriving node module metadata from registry and MQTT status."""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Iterable, Mapping, MutableMapping, Sequence, Tuple


_FALSEY_STRINGS = {"", "0", "false", "no", "off", "disabled", "inactive"}

_DEFAULT_INDEXES: dict[str, Tuple[int, ...]] = {
    "ws": tuple(range(4)),
    "rgb": tuple(range(4)),
    "white": tuple(range(4)),
}

_INDEX_FIELDS: tuple[str, ...] = ("strip", "channel", "index")

_IGNORED_PAYLOAD_KEYS: set[str] = {
    "status",
    "node",
    "uptime_s",
    "signal_dbi",
    "last_ok",
    "last_seen",
    "online",
    "params",
    "strip",
    "effect",
    "brightness",
    "error",
    "now",
    "timeout",
    "motion_on_channel",
    "pir_motion_time",
    "state0",
    "state1",
    "state2",
    "state3",
    "modules",
    "module_channels",
    "detail",
}

_SEQUENCE_SCALAR_TYPES = (str, bytes, bytearray)


def _normalize_module_key(value: Any) -> str:
    if isinstance(value, str):
        key = value.strip().lower()
    else:
        key = str(value).strip().lower()
    return key


def _coerce_enabled(value: Any) -> bool:
    """Return ``True`` when ``value`` represents an enabled module."""

    if isinstance(value, str):
        return value.strip().lower() not in _FALSEY_STRINGS
    return bool(value)


def _module_entry_enabled(config: Any) -> bool:
    """Determine whether a module configuration marks the module as enabled."""

    if isinstance(config, Mapping):
        if "enabled" in config:
            return _coerce_enabled(config.get("enabled"))
        return True
    return _coerce_enabled(config)


def enabled_module_keys(node: Mapping[str, Any] | MutableMapping[str, Any]) -> list[str]:
    """Extract enabled module keys from ``node`` preserving declaration order."""

    modules = node.get("modules") if isinstance(node, Mapping) else None
    if not modules:
        return []

    result: list[str] = []

    def add_key(raw: Any) -> None:
        key = _normalize_module_key(raw)
        if key and key not in result:
            result.append(key)

    if isinstance(modules, Mapping):
        for key, cfg in modules.items():
            if not key:
                continue
            if _module_entry_enabled(cfg):
                add_key(key)
        return result

    if isinstance(modules, (list, tuple)):
        for entry in modules:
            if isinstance(entry, str):
                add_key(entry)
                continue
            if isinstance(entry, Mapping):
                name = (
                    entry.get("key")
                    or entry.get("module")
                    or entry.get("id")
                    or entry.get("name")
                )
                if name and _module_entry_enabled(entry):
                    add_key(name)
                continue
            if entry is None:
                continue
            add_key(entry)
        return result

    if isinstance(modules, set):
        for entry in modules:
            if entry is None:
                continue
            add_key(entry)
        return result

    if isinstance(modules, str):
        add_key(modules)
        return result

    add_key(modules)
    return result


def _clean_indexes(indexes: Iterable[Any]) -> list[int]:
    seen: set[int] = set()
    result: list[int] = []
    for value in indexes:
        try:
            index = int(value)
        except (TypeError, ValueError):
            continue
        if index not in seen:
            seen.add(index)
            result.append(index)
    return result


def _extract_index_from_mapping(entry: Mapping[str, Any]) -> int | None:
    for field in _INDEX_FIELDS:
        raw = entry.get(field)
        try:
            return int(raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
    return None


def _collect_payload_indexes(value: Any) -> list[int]:
    if isinstance(value, Mapping):
        for key in ("indexes", "indices", "channels", "strips"):
            nested = value.get(key)
            if nested is not None:
                indexes = _collect_payload_indexes(nested)
                if indexes:
                    return indexes
        return []

    if isinstance(value, Sequence) and not isinstance(value, _SEQUENCE_SCALAR_TYPES):
        collected: list[int] = []
        for item in value:
            if isinstance(item, Mapping):
                index = _extract_index_from_mapping(item)
                if index is None:
                    continue
                collected.append(index)
            elif isinstance(item, (int, float)):
                collected.append(int(item))
        return _clean_indexes(collected)

    return []


def _payload_declares_module(value: Any) -> bool:
    if isinstance(value, Mapping):
        return bool(value)
    if isinstance(value, Sequence) and not isinstance(value, _SEQUENCE_SCALAR_TYPES):
        return any(isinstance(item, Mapping) for item in value)
    return False


@dataclass(frozen=True)
class NodeCapabilities:
    """Normalized metadata describing a node's available modules."""

    modules: Tuple[str, ...]
    module_channels: Mapping[str, Tuple[int, ...]] = field(default_factory=dict)
    source: str = "registry"

    def __post_init__(self) -> None:  # pragma: no cover - simple normalization
        normalized_modules: list[str] = []
        for mod in self.modules:
            key = _normalize_module_key(mod)
            if key and key not in normalized_modules:
                normalized_modules.append(key)
        object.__setattr__(self, "modules", tuple(normalized_modules))

        cleaned: dict[str, Tuple[int, ...]] = {}
        for key, indexes in self.module_channels.items():
            norm_key = _normalize_module_key(key)
            if not norm_key:
                continue
            cleaned_indexes = tuple(_clean_indexes(indexes))
            if cleaned_indexes:
                cleaned[norm_key] = cleaned_indexes
        object.__setattr__(self, "module_channels", MappingProxyType(cleaned))

    @classmethod
    def from_modules(cls, modules: Sequence[Any] | None) -> "NodeCapabilities":
        module_list: list[str] = []
        channels: dict[str, Tuple[int, ...]] = {}
        if modules:
            for entry in modules:
                key = _normalize_module_key(entry)
                if not key or key in module_list:
                    continue
                module_list.append(key)
                default = _DEFAULT_INDEXES.get(key)
                if default:
                    channels[key] = default
        return cls(tuple(module_list), channels, source="registry")

    @classmethod
    def from_payload(cls, payload: Any) -> "NodeCapabilities | None":
        if not isinstance(payload, Mapping):
            return None

        modules: list[str] = []
        channels: dict[str, Tuple[int, ...]] = {}

        for raw_key, value in payload.items():
            key = _normalize_module_key(raw_key)
            if not key or key in _IGNORED_PAYLOAD_KEYS:
                continue

            indexes = _collect_payload_indexes(value)
            if indexes:
                if key not in modules:
                    modules.append(key)
                channels[key] = tuple(indexes)
                continue

            if _payload_declares_module(value):
                if key not in modules:
                    modules.append(key)

        if not modules:
            return None

        return cls(tuple(modules), channels, source="status")

    def merged_with(self, fallback: "NodeCapabilities") -> "NodeCapabilities":
        if not self.modules and fallback.modules:
            return fallback
        modules = list(self.modules)
        channels = dict(self.module_channels)
        for mod in fallback.modules:
            if mod not in modules:
                modules.append(mod)
            if mod not in channels:
                fallback_indexes = fallback.module_channels.get(mod, tuple())
                if fallback_indexes:
                    channels[mod] = tuple(fallback_indexes)
        source = self.source if self.modules else fallback.source
        return NodeCapabilities(tuple(modules), channels, source=source)

    def has_module(self, module: str) -> bool:
        key = _normalize_module_key(module)
        return key in self.modules

    def indexes(self, module: str) -> Tuple[int, ...]:
        key = _normalize_module_key(module)
        return self.module_channels.get(key, tuple())

    def valid_index(self, module: str, index: int) -> bool:
        key = _normalize_module_key(module)
        try:
            value = int(index)
        except (TypeError, ValueError):
            return False
        return value in self.module_channels.get(key, tuple())

    def max_index(self, module: str) -> int | None:
        indexes = self.indexes(module)
        if not indexes:
            return None
        return max(indexes)


__all__ = ["NodeCapabilities", "enabled_module_keys"]
