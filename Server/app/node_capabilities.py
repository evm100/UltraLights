from __future__ import annotations

"""Helpers for normalizing node module capability metadata."""

from typing import Any, Iterable, Mapping, MutableMapping, Sequence

FALSEY_STRINGS = {"", "0", "false", "no", "off", "disabled", "inactive"}

# Default index ranges when no live capability data is available.
DEFAULT_INDEX_RANGES: Mapping[str, Sequence[int]] = {
    "ws": range(4),
    "rgb": range(4),
    "white": range(4),
}


def _normalize_key(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    return str(value).strip()


def _coerce_enabled(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in FALSEY_STRINGS
    return bool(value)


def _module_entry_enabled(config: Any) -> bool:
    if isinstance(config, Mapping):
        if "enabled" in config:
            return _coerce_enabled(config.get("enabled"))
        return True
    return _coerce_enabled(config)


def registry_enabled_modules(node: Mapping[str, Any]) -> list[str]:
    """Return enabled module identifiers from a registry ``node`` entry."""

    modules = node.get("modules")
    if not modules:
        return []

    result: list[str] = []
    seen: set[str] = set()

    def _push(name: Any, cfg: Any = True) -> None:
        key = _normalize_key(name)
        if not key or key in seen:
            return
        if _module_entry_enabled(cfg):
            result.append(key)
            seen.add(key)

    if isinstance(modules, Mapping):
        for key, cfg in modules.items():
            _push(key, cfg)
        return result

    if isinstance(modules, (list, tuple, set)):
        for entry in modules:
            if isinstance(entry, Mapping):
                name = (
                    entry.get("key")
                    or entry.get("module")
                    or entry.get("id")
                    or entry.get("name")
                    or entry.get("slug")
                    or entry.get("template")
                )
                _push(name, entry)
            else:
                _push(entry)
        return result

    if isinstance(modules, str):
        _push(modules)
        return result

    _push(modules)
    return result


def merge_module_lists(primary: Sequence[str], fallback: Sequence[str]) -> list[str]:
    """Combine two module lists preserving the order of each input."""

    result: list[str] = []
    seen: set[str] = set()

    for source in (primary, fallback):
        for item in source:
            key = _normalize_key(item)
            if not key or key in seen:
                continue
            result.append(key)
            seen.add(key)
    return result


def coerce_index(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        idx = int(value)
        return idx if idx >= 0 else None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            idx = int(value, 10)
        except ValueError:
            return None
        return idx if idx >= 0 else None
    return None


def sanitize_index_list(values: Any) -> list[int]:
    if not isinstance(values, Iterable) or isinstance(values, (str, bytes)):
        return []
    result: list[int] = []
    seen: set[int] = set()
    for item in values:
        idx = coerce_index(item)
        if idx is None or idx in seen:
            continue
        seen.add(idx)
        result.append(idx)
    result.sort()
    return result


def build_index_options(
    capability_indexes: Mapping[str, Mapping[str, Any]],
    defaults: Mapping[str, Iterable[int]] = DEFAULT_INDEX_RANGES,
) -> dict[str, list[int]]:
    """Return a mapping of module -> preferred index list."""

    result: dict[str, list[int]] = {}
    for module, default_values in defaults.items():
        result[module] = list(default_values)

    for module, data in capability_indexes.items():
        enabled = sanitize_index_list(data.get("enabled"))
        available = sanitize_index_list(data.get("available"))
        if enabled:
            result[module] = enabled
        elif available:
            result[module] = available
        elif module not in result:
            result[module] = []

    return result


def copy_capability_indexes(indexes: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, list[int]]]:
    """Return a sanitized deep copy of capability index data."""

    result: dict[str, dict[str, list[int]]] = {}
    for module, data in indexes.items():
        result[module] = {
            "enabled": sanitize_index_list(data.get("enabled")),
            "available": sanitize_index_list(data.get("available")),
        }
    return result

