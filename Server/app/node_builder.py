"""Utilities for building firmware artifacts from node registrations."""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlmodel import Session, select

from . import node_credentials, registry
from .auth.models import NodeRegistration
from .config import settings


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIRMWARE_ROOT = PROJECT_ROOT / "UltraNodeV5"
FIRMWARE_ARCHIVE_ROOT = PROJECT_ROOT / "firmware_artifacts"
SDKCONFIG_TEMPLATE = FIRMWARE_ROOT / "sdkconfig.defaults"
SDKCONFIG_WORK_DIR = FIRMWARE_ROOT / "node_configs"


@dataclass
class CommandResult:
    """Outcome from invoking a command line helper."""

    command: List[str]
    returncode: int
    stdout: str
    stderr: str
    cwd: Path


@dataclass
class BuildResult(CommandResult):
    """Return value for individual node builds."""

    node_id: str
    sdkconfig_path: Path
    manifest_url: str
    download_id: str
    target: str
    metadata: Dict[str, Any]
    ota_token: str
    sdkconfig_values: Dict[str, str]
    project_configs: Tuple[Path, ...] = field(default_factory=tuple)


@dataclass
class ArtifactRecord:
    """Paths and metadata produced when archiving a build."""

    node_id: str
    download_id: str
    version: str
    latest_binary: Path
    archive_binary: Optional[Path]
    manifest_path: Path
    versioned_manifest_path: Optional[Path]
    size: int
    sha256_hex: str


class NodeBuilderError(RuntimeError):
    """Raised when a firmware helper fails."""


SUPPORTED_TARGETS: Dict[str, str] = {
    "esp32": "esp32",
    "esp32c3": "esp32c3",
    "esp32s3": "esp32s3",
}


def _sanitize_node_for_path(node_id: str) -> str:
    safe = [ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in node_id]
    return "".join(safe).strip() or "node"


def clean_build_dir() -> None:
    """Remove the ``build`` directory inside the firmware tree."""

    build_dir = FIRMWARE_ROOT / "build"
    if not build_dir.exists():
        return
    if not build_dir.is_dir():
        raise NodeBuilderError("build path exists but is not a directory")
    if build_dir.resolve().parent != FIRMWARE_ROOT.resolve():
        raise NodeBuilderError("refusing to remove unexpected build directory")
    shutil.rmtree(build_dir)


def _sanitize_version(version: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", version.strip())


def _sha256_file(path: Path) -> str:
    h = sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_manifest_version(manifest_path: Path) -> Optional[str]:
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None
    version = data.get("version")
    if isinstance(version, str):
        cleaned = version.strip()
        return cleaned or None
    return None


def embed_firmware_version(sdkconfig_path: Path, version: str) -> None:
    """Ensure ``sdkconfig`` records the supplied firmware version."""

    cleaned = (version or "").strip()
    if not cleaned:
        return

    escaped = cleaned.replace("\\", "\\\\").replace('"', '\\"')
    lines = sdkconfig_path.read_text(encoding="utf-8").splitlines()
    flag_written = False
    version_written = False
    output: List[str] = []

    for line in lines:
        if line.startswith("CONFIG_APP_PROJECT_VER_FROM_CONFIG"):
            if not flag_written:
                output.append("CONFIG_APP_PROJECT_VER_FROM_CONFIG=y")
                flag_written = True
            continue
        if line.startswith("# CONFIG_APP_PROJECT_VER_FROM_CONFIG"):
            if not flag_written:
                output.append("CONFIG_APP_PROJECT_VER_FROM_CONFIG=y")
                flag_written = True
            continue
        if line.startswith("CONFIG_APP_PROJECT_VER="):
            if not version_written:
                output.append(f'CONFIG_APP_PROJECT_VER="{escaped}"')
                version_written = True
            continue
        output.append(line)

    if not flag_written:
        output.append("CONFIG_APP_PROJECT_VER_FROM_CONFIG=y")
    if not version_written:
        output.append(f'CONFIG_APP_PROJECT_VER="{escaped}"')

    sdkconfig_path.write_text("\n".join(output) + "\n", encoding="utf-8")


def store_build_artifacts(
    *,
    node_id: str,
    download_id: str,
    firmware_version: str,
    binary_path: Optional[Path] = None,
    firmware_dir: Optional[Path] = None,
    archive_root: Optional[Path] = None,
) -> ArtifactRecord:
    """Copy build outputs into the public firmware tree and archive."""

    binary_path = Path(binary_path or (FIRMWARE_ROOT / "build" / "ultralights.bin"))
    if not binary_path.exists():
        raise FileNotFoundError(f"build output not found: {binary_path}")

    target_firmware_dir = Path(firmware_dir or settings.FIRMWARE_DIR)
    target_firmware_dir.mkdir(parents=True, exist_ok=True)
    download_dir = target_firmware_dir / download_id
    download_dir.mkdir(parents=True, exist_ok=True)

    archive_root = Path(archive_root or FIRMWARE_ARCHIVE_ROOT)
    archive_root.mkdir(parents=True, exist_ok=True)
    archive_dir = archive_root / _sanitize_node_for_path(node_id)
    archive_dir.mkdir(parents=True, exist_ok=True)

    latest_path = download_dir / "latest.bin"
    previous_manifest = download_dir / "manifest.json"
    previous_version = _read_manifest_version(previous_manifest)
    if latest_path.exists() and previous_version:
        safe_prev = _sanitize_version(previous_version)
        if safe_prev:
            shutil.copy2(latest_path, archive_dir / f"{safe_prev}.bin")

    shutil.copy2(binary_path, latest_path)

    safe_current = _sanitize_version(firmware_version)
    archive_binary: Optional[Path] = None
    if safe_current:
        archive_binary = archive_dir / f"{safe_current}.bin"
        shutil.copy2(binary_path, archive_binary)

    size = binary_path.stat().st_size
    checksum = _sha256_file(binary_path)
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    manifest = {
        "device_id": node_id,
        "version": firmware_version,
        "size": size,
        "sha256_hex": checksum,
        "binary_url": "latest.bin",
        "generated_at": generated_at,
    }

    manifest_path = download_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    versioned_manifest_path: Optional[Path] = None
    if safe_current:
        versioned_manifest_path = download_dir / f"manifest_{safe_current}.json"
        versioned_manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    return ArtifactRecord(
        node_id=node_id,
        download_id=download_id,
        version=firmware_version,
        latest_binary=latest_path,
        archive_binary=archive_binary,
        manifest_path=manifest_path,
        versioned_manifest_path=versioned_manifest_path,
        size=size,
        sha256_hex=checksum,
    )


def _config_value(value: Any, quoted: bool = False) -> Tuple[Any, bool]:
    return value, quoted


def _bool_flag(value: bool) -> Tuple[str, bool]:
    return ("y" if value else "n"), False


def _coerce_numeric(value: Any) -> Tuple[Any, bool]:
    if value in (None, ""):
        return "", False
    if isinstance(value, bool):
        return value, False
    if isinstance(value, (int, float)):
        return int(value), False
    if isinstance(value, str):
        trimmed = value.strip()
        if not trimmed:
            return "", False
        try:
            numeric = int(trimmed, 0)
            return numeric, False
        except ValueError:
            return trimmed, True
    return value, True


def _ensure_work_dir() -> Path:
    SDKCONFIG_WORK_DIR.mkdir(parents=True, exist_ok=True)
    return SDKCONFIG_WORK_DIR


def _extract_key(line: str) -> Optional[str]:
    line = line.rstrip()
    if not line:
        return None
    if line.startswith("CONFIG_"):
        return line.split("=", 1)[0]
    if line.startswith("# CONFIG_") and line.endswith(" is not set"):
        parts = line.split()
        if len(parts) >= 3:
            return parts[1]
    return None


def _format_value(key: str, entry: Tuple[Any, bool]) -> str:
    value, quoted = entry
    if isinstance(value, tuple) and len(value) == 2:
        # Allow nested tuple unpacking
        value, quoted = value

    if isinstance(value, bool):
        return f"{key}={'y' if value else 'n'}"

    if value is None:
        return f"{key}="

    if isinstance(value, (int, float)) and not quoted:
        return f"{key}={value}"

    text = str(value)
    if not quoted and text.isdigit():
        return f"{key}={text}"

    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'{key}="{escaped}"'


def _merge_sdkconfig(base_lines: Iterable[str], overrides: Dict[str, Tuple[Any, bool]]) -> List[str]:
    applied: set[str] = set()
    merged: List[str] = []

    for raw_line in base_lines:
        key = _extract_key(raw_line)
        if key and key in overrides:
            merged.append(_format_value(key, overrides[key]))
            applied.add(key)
        else:
            merged.append(raw_line.rstrip())

    for key, entry in overrides.items():
        if key in applied:
            continue
        merged.append(_format_value(key, entry))

    merged.append("")
    return merged


def _int_or_none(value: Any) -> Optional[int]:
    """Best-effort conversion of ``value`` to an integer."""

    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        trimmed = value.strip()
        if not trimmed:
            return None
        try:
            return int(trimmed, 0)
        except ValueError:
            return None
    return None


def _next_ledc_channel(used: set[int]) -> int:
    """Return the next unassigned LEDC channel index."""

    candidate = 0
    while candidate in used:
        candidate += 1
    used.add(candidate)
    return candidate


def _board_overrides(board: str) -> Dict[str, Tuple[Any, bool]]:
    board = board.lower()
    target = SUPPORTED_TARGETS.get(board, "esp32")
    overrides: Dict[str, Tuple[Any, bool]] = {
        "CONFIG_UL_TARGET_CHIP": _config_value(target, quoted=True),
        "CONFIG_UL_IS_ESP32C3": _bool_flag(board == "esp32c3"),
        "CONFIG_UL_IS_ESP32S3": _bool_flag(board == "esp32s3"),
    }
    return overrides


def normalize_hardware_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Apply defaults for hardware metadata used by node registrations."""

    if not isinstance(metadata, dict):
        return {}

    normalized = copy.deepcopy(metadata)
    board = str(normalized.get("board", "esp32")).lower()
    normalized["board"] = board

    used_ledc: set[int] = set()

    white_entries = []
    for raw in normalized.get("white") or []:
        if not isinstance(raw, dict):
            continue
        idx = _int_or_none(raw.get("index"))
        if idx is None or idx < 0 or idx > 3:
            continue
        enabled = bool(raw.get("enabled"))
        gpio = _int_or_none(raw.get("gpio"))
        entry: Dict[str, Any] = {
            "index": idx,
            "enabled": enabled,
        }
        if gpio is not None:
            entry["gpio"] = gpio

        active = enabled or gpio is not None
        pwm_hz = _int_or_none(raw.get("pwm_hz")) or 3000
        minimum = _int_or_none(raw.get("minimum"))
        maximum = _int_or_none(raw.get("maximum"))
        entry["pwm_hz"] = pwm_hz
        entry["minimum"] = 0 if minimum is None else minimum
        entry["maximum"] = 255 if maximum is None else maximum

        if active:
            ledc = _int_or_none(raw.get("ledc_channel"))
            if ledc is None or ledc in used_ledc:
                ledc = _next_ledc_channel(used_ledc)
            else:
                used_ledc.add(ledc)
            entry["ledc_channel"] = ledc

        white_entries.append(entry)

    white_entries.sort(key=lambda item: item["index"])
    normalized["white"] = white_entries

    rgb_mode_default = 0 if board == "esp32c3" else 1
    rgb_entries = []
    for raw in normalized.get("rgb") or []:
        if not isinstance(raw, dict):
            continue
        idx = _int_or_none(raw.get("index"))
        if idx is None or idx < 0 or idx > 3:
            continue
        enabled = bool(raw.get("enabled"))
        entry: Dict[str, Any] = {
            "index": idx,
            "enabled": enabled,
            "pwm_hz": _int_or_none(raw.get("pwm_hz")) or 3000,
            "ledc_mode": rgb_mode_default,
        }

        colors_with_gpio: list[str] = []
        for color in ("r", "g", "b"):
            gpio_value = _int_or_none(raw.get(f"{color}_gpio"))
            if gpio_value is not None:
                entry[f"{color}_gpio"] = gpio_value
                colors_with_gpio.append(color)

        active = enabled or bool(colors_with_gpio)
        if not active:
            rgb_entries.append(entry)
            continue

        for color in ("r", "g", "b"):
            ledc_field = f"{color}_ledc_ch"
            if color not in colors_with_gpio and not enabled:
                continue
            ledc_value = _int_or_none(raw.get(ledc_field))
            if ledc_value is None or ledc_value in used_ledc:
                ledc_value = _next_ledc_channel(used_ledc)
            else:
                used_ledc.add(ledc_value)
            entry[ledc_field] = ledc_value

        rgb_entries.append(entry)

    rgb_entries.sort(key=lambda item: item["index"])
    normalized["rgb"] = rgb_entries

    return normalized


def _ws_overrides(metadata: Dict[str, Any]) -> Dict[str, Tuple[Any, bool]]:
    channels = metadata.get("ws2812") or []
    overrides: Dict[str, Tuple[Any, bool]] = {}
    indexed = {int(entry.get("index", -1)): entry for entry in channels if isinstance(entry, dict)}
    for idx in range(2):
        entry = indexed.get(idx) or {}
        enabled = bool(entry.get("enabled"))
        overrides[f"CONFIG_UL_WS{idx}_ENABLED"] = _bool_flag(enabled)
        gpio = entry.get("gpio")
        pixels = entry.get("pixels")
        overrides[f"CONFIG_UL_WS{idx}_GPIO"] = _config_value(*_coerce_numeric(gpio))
        overrides[f"CONFIG_UL_WS{idx}_PIXELS"] = _config_value(*_coerce_numeric(pixels))
    return overrides


def _white_overrides(metadata: Dict[str, Any]) -> Dict[str, Tuple[Any, bool]]:
    channels = metadata.get("white") or []
    overrides: Dict[str, Tuple[Any, bool]] = {}
    indexed = {int(entry.get("index", -1)): entry for entry in channels if isinstance(entry, dict)}
    for idx in range(4):
        entry = indexed.get(idx)
        if entry is None:
            overrides[f"CONFIG_UL_WHT{idx}_ENABLED"] = _bool_flag(False)
            continue

        enabled = bool(entry.get("enabled"))
        overrides[f"CONFIG_UL_WHT{idx}_ENABLED"] = _bool_flag(enabled)

        field_map = {
            "GPIO": "gpio",
            "LEDC_CH": "ledc_channel",
            "PWM_HZ": "pwm_hz",
            "MIN": "minimum",
            "MAX": "maximum",
        }

        for suffix, field_key in field_map.items():
            value = entry.get(field_key)
            overrides[f"CONFIG_UL_WHT{idx}_{suffix}"] = _config_value(
                *_coerce_numeric(value)
            )
    return overrides


def _rgb_overrides(metadata: Dict[str, Any]) -> Dict[str, Tuple[Any, bool]]:
    channels = metadata.get("rgb") or []
    overrides: Dict[str, Tuple[Any, bool]] = {}
    indexed = {int(entry.get("index", -1)): entry for entry in channels if isinstance(entry, dict)}
    for idx in range(4):
        entry = indexed.get(idx) or {}
        enabled = bool(entry.get("enabled"))
        overrides[f"CONFIG_UL_RGB{idx}_ENABLED"] = _bool_flag(enabled)
        pwm_hz = entry.get("pwm_hz")
        ledc_mode = entry.get("ledc_mode")
        overrides[f"CONFIG_UL_RGB{idx}_PWM_HZ"] = _config_value(*_coerce_numeric(pwm_hz))
        overrides[f"CONFIG_UL_RGB{idx}_LEDC_MODE"] = _config_value(*_coerce_numeric(ledc_mode))
        for channel, suffix in (("r", "R"), ("g", "G"), ("b", "B")):
            gpio = entry.get(f"{channel}_gpio")
            ledc = entry.get(f"{channel}_ledc_ch")
            overrides[f"CONFIG_UL_RGB{idx}_{suffix}_GPIO"] = _config_value(*_coerce_numeric(gpio))
            overrides[f"CONFIG_UL_RGB{idx}_{suffix}_LEDC_CH"] = _config_value(*_coerce_numeric(ledc))
    return overrides


def _pir_overrides(metadata: Dict[str, Any]) -> Dict[str, Tuple[Any, bool]]:
    pir = metadata.get("pir") or {}
    if not isinstance(pir, dict):
        return {
            "CONFIG_UL_PIR_ENABLED": _bool_flag(False),
            "CONFIG_UL_PIR_GPIO": _config_value("", quoted=False),
        }
    enabled = bool(pir.get("enabled"))
    gpio = pir.get("gpio")
    return {
        "CONFIG_UL_PIR_ENABLED": _bool_flag(enabled),
        "CONFIG_UL_PIR_GPIO": _config_value(*_coerce_numeric(gpio)),
    }


def _override_entries(metadata: Dict[str, Any]) -> Dict[str, Tuple[Any, bool]]:
    overrides = metadata.get("overrides") or {}
    result: Dict[str, Tuple[Any, bool]] = {}
    if isinstance(overrides, dict):
        for key, value in overrides.items():
            if not isinstance(key, str) or not key.startswith("CONFIG_"):
                continue
            if isinstance(value, bool):
                result[key] = _bool_flag(value)
            else:
                coerced, quoted = _coerce_numeric(value)
                result[key] = _config_value(coerced, quoted)
    return result


def metadata_to_overrides(metadata: Dict[str, Any]) -> Dict[str, Tuple[Any, bool]]:
    metadata = normalize_hardware_metadata(metadata or {})
    overrides: Dict[str, Tuple[Any, bool]] = {}
    overrides.update(_board_overrides(str(metadata.get("board", "esp32"))))
    overrides.update(_ws_overrides(metadata))
    overrides.update(_white_overrides(metadata))
    overrides.update(_rgb_overrides(metadata))
    overrides.update(_pir_overrides(metadata))
    overrides.update(_override_entries(metadata))
    return overrides


def render_sdkconfig(
    *,
    node_id: str,
    download_id: str,
    token: str,
    metadata: Dict[str, Any],
    manifest_url: Optional[str] = None,
    base_config: Path = SDKCONFIG_TEMPLATE,
) -> Path:
    """Write a node-specific sdkconfig and return its path."""

    if manifest_url is None:
        manifest_url = f"{settings.PUBLIC_BASE}/firmware/{download_id}/manifest.json"

    work_dir = _ensure_work_dir()
    target = _sanitize_node_for_path(node_id)
    output_path = work_dir / f"sdkconfig.{target}"

    overrides = metadata_to_overrides(metadata)
    overrides.update(
        {
            "CONFIG_UL_NODE_ID": _config_value(node_id, quoted=True),
            "CONFIG_UL_OTA_MANIFEST_URL": _config_value(manifest_url, quoted=True),
            "CONFIG_UL_OTA_BEARER_TOKEN": _config_value(token, quoted=True),
            "CONFIG_UL_NODE_METADATA": _config_value(
                json.dumps(metadata, separators=(",", ":"), sort_keys=True),
                quoted=True,
            ),
        }
    )

    if not base_config.exists():
        raise FileNotFoundError(f"base sdkconfig not found: {base_config}")

    base_lines = base_config.read_text().splitlines()
    merged = _merge_sdkconfig(base_lines, overrides)
    output_path.write_text("\n".join(merged))
    return output_path


def update_sdkconfig_files(
    values: Dict[str, str],
    *,
    config_paths: Iterable[Path],
) -> List[Path]:
    """Persist ``values`` into each sdkconfig file listed in ``config_paths``."""

    overrides = {
        key: _config_value(value, quoted=True)
        for key, value in values.items()
        if value is not None
    }

    updated: List[Path] = []
    for path in config_paths:
        resolved = Path(path).expanduser().resolve()
        if not resolved.exists():
            raise FileNotFoundError(f"sdkconfig file not found: {resolved}")
        lines = resolved.read_text(encoding="utf-8").splitlines()
        merged = _merge_sdkconfig(lines, overrides)
        resolved.write_text("\n".join(merged), encoding="utf-8")
        updated.append(resolved)
    return updated


def _prepare_environment(board: str, extra_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    env = os.environ.copy()
    target = SUPPORTED_TARGETS.get(board.lower(), "esp32")
    env.setdefault("IDF_TARGET", target)
    if extra_env:
        env.update(extra_env)
    return env


def _run_command(
    args: List[str],
    *,
    env: Optional[Dict[str, str]] = None,
    cwd: Path = FIRMWARE_ROOT,
) -> CommandResult:
    completed = subprocess.run(
        args,
        cwd=str(cwd),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    return CommandResult(
        command=list(args),
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        cwd=cwd,
    )



def build_individual_node(
    session: Session,
    node_id: str,
    *,
    metadata: Optional[Dict[str, Any]] = None,
    board: Optional[str] = None,
    regenerate_token: bool = False,
    run_build: bool = True,
    ota_token: Optional[str] = None,
    firmware_version: Optional[str] = None,
    clean_build: bool = True,
    sdkconfig_paths: Optional[Iterable[Path]] = None,
) -> BuildResult:
    """Generate an sdkconfig for ``node_id`` and optionally run ``idf.py build``."""

    node_credentials.sync_registry_nodes(session)
    registration = node_credentials.get_registration_by_node_id(session, node_id)
    if registration is None:
        raise NodeBuilderError(f"Unknown node id: {node_id}")

    token_value: Optional[str] = None

    if ota_token:
        supplied = ota_token.strip()
        if not supplied:
            raise NodeBuilderError("OTA token must not be empty")
        expected_hash = registry.hash_node_token(supplied)
        if registration.token_hash and registration.token_hash != expected_hash:
            raise NodeBuilderError(
                "Provided OTA token does not match the stored hash for this node"
            )
        if registration.token_hash != expected_hash:
            node_credentials.rotate_token(session, node_id, token=supplied)
            registration = node_credentials.get_registration_by_node_id(session, node_id)
        token_value = supplied
    else:
        _, token_value = node_credentials.rotate_token(session, node_id)
        registration = node_credentials.get_registration_by_node_id(session, node_id)

    if not token_value:
        raise NodeBuilderError("Failed to resolve OTA token for node")

    download_id = registration.download_id
    manifest_url = f"{settings.PUBLIC_BASE}/firmware/{download_id}/manifest.json"

    metadata_payload = metadata or dict(registration.hardware_metadata or {})
    if board:
        metadata_payload["board"] = board
    else:
        metadata_payload.setdefault("board", "esp32")

    metadata_serialized = json.dumps(
        metadata_payload or {}, separators=(",", ":"), sort_keys=True
    )

    config_values: Dict[str, str] = {
        "CONFIG_UL_NODE_ID": node_id,
        "CONFIG_UL_OTA_MANIFEST_URL": manifest_url,
        "CONFIG_UL_OTA_BEARER_TOKEN": token_value,
        "CONFIG_UL_NODE_METADATA": metadata_serialized,
    }

    updated_configs: Tuple[Path, ...] = tuple()
    if sdkconfig_paths:
        try:
            updated = update_sdkconfig_files(config_values, config_paths=sdkconfig_paths)
        except FileNotFoundError as exc:  # pragma: no cover - defensive
            raise NodeBuilderError(str(exc)) from exc
        updated_configs = tuple(updated)

    sdkconfig_path = render_sdkconfig(
        node_id=node_id,
        download_id=download_id,
        token=token_value,
        metadata=metadata_payload,
        manifest_url=manifest_url,
    )

    if firmware_version:
        embed_firmware_version(sdkconfig_path, firmware_version)

    env = _prepare_environment(str(metadata_payload.get("board", "esp32")))
    env["SDKCONFIG"] = str(sdkconfig_path)

    if clean_build:
        clean_build_dir()

    if run_build:
        result = _run_command(["idf.py", "build"], env=env)
    else:
        result = CommandResult(command=["idf.py", "build"], returncode=0, stdout="", stderr="", cwd=FIRMWARE_ROOT)

    return BuildResult(
        command=result.command,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        cwd=result.cwd,
        node_id=node_id,
        sdkconfig_path=sdkconfig_path,
        manifest_url=manifest_url,
        download_id=download_id,
        target=SUPPORTED_TARGETS.get(str(metadata_payload.get("board", "esp32")).lower(), "esp32"),
        metadata=dict(metadata_payload),
        ota_token=token_value,
        sdkconfig_values=config_values,
        project_configs=updated_configs,
    )


def first_time_flash(
    session: Session,
    node_id: str,
    *,
    port: str,
    metadata: Optional[Dict[str, Any]] = None,
    board: Optional[str] = None,
    ota_token: Optional[str] = None,
    firmware_version: Optional[str] = None,
    clean_build: bool = True,
    sdkconfig_paths: Optional[Iterable[Path]] = None,
) -> BuildResult:
    """Perform ``idf.py -p <port> build flash`` for ``node_id``."""

    build_result = build_individual_node(
        session,
        node_id,
        metadata=metadata,
        board=board,
        regenerate_token=False,
        run_build=False,
        ota_token=ota_token,
        firmware_version=firmware_version,
        clean_build=clean_build,
        sdkconfig_paths=sdkconfig_paths,
    )

    metadata_payload = dict(metadata) if isinstance(metadata, dict) else {}
    board_name = board or metadata_payload.get("board") or build_result.target or "esp32"
    if not isinstance(board_name, str) or not board_name.strip():
        board_name = build_result.target or "esp32"
    env = _prepare_environment(str(board_name))
    env["SDKCONFIG"] = str(build_result.sdkconfig_path)
    if clean_build:
        clean_build_dir()
    command = ["idf.py", "-p", port, "build", "flash"]
    result = _run_command(command, env=env)

    return BuildResult(
        command=result.command,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        cwd=result.cwd,
        node_id=build_result.node_id,
        sdkconfig_path=build_result.sdkconfig_path,
        manifest_url=build_result.manifest_url,
        download_id=build_result.download_id,
        target=build_result.target,
        metadata=build_result.metadata,
        ota_token=build_result.ota_token,
        sdkconfig_values=build_result.sdkconfig_values,
        project_configs=build_result.project_configs,
    )


def ensure_test_registration(
    session: Session,
    *,
    display_name: str = "Firmware Test Node",
    metadata: Optional[Dict[str, Any]] = None,
) -> NodeRegistration:
    """Return a persistent registration used for firmware testing."""

    existing = session.exec(
        select(NodeRegistration).where(NodeRegistration.display_name == display_name)
    ).first()
    if existing:
        if metadata:
            merged = dict(existing.hardware_metadata or {})
            merged.update(metadata)
            if merged != existing.hardware_metadata:
                existing.hardware_metadata = merged
                session.add(existing)
                session.commit()
                session.refresh(existing)
        return existing

    batch = node_credentials.create_batch(session, 1, metadata=[metadata or {}])
    registration = batch[0].registration
    registration.display_name = display_name
    session.add(registration)
    session.commit()
    session.refresh(registration)
    return registration

