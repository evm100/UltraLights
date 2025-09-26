"""Utilities for building firmware artifacts from node registrations."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlmodel import Session, select

from . import node_credentials, registry
from .auth.models import NodeRegistration
from .config import settings


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIRMWARE_ROOT = PROJECT_ROOT / "UltraNodeV5"
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


def _config_value(value: Any, *, quoted: bool = False) -> Tuple[Any, bool]:
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


def _board_overrides(board: str) -> Dict[str, Tuple[Any, bool]]:
    board = board.lower()
    target = SUPPORTED_TARGETS.get(board, "esp32")
    overrides: Dict[str, Tuple[Any, bool]] = {
        "CONFIG_UL_TARGET_CHIP": _config_value(target, quoted=True),
        "CONFIG_UL_IS_ESP32C3": _bool_flag(board == "esp32c3"),
        "CONFIG_UL_IS_ESP32S3": _bool_flag(board == "esp32s3"),
    }
    return overrides


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
        entry = indexed.get(idx) or {}
        enabled = bool(entry.get("enabled"))
        overrides[f"CONFIG_UL_WHT{idx}_ENABLED"] = _bool_flag(enabled)
        for key in ("GPIO", "LEDC_CH", "PWM_HZ", "MIN", "MAX"):
            field_key = key.lower()
            value = entry.get(field_key)
            overrides[f"CONFIG_UL_WHT{idx}_{key}"] = _config_value(*_coerce_numeric(value))
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
    metadata = metadata or {}
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


def update_all_nodes(firmware_version: str, *, env: Optional[Dict[str, str]] = None) -> CommandResult:
    """Invoke the bulk updater script."""

    script = FIRMWARE_ROOT / "updateAllNodes.sh"
    if not script.exists():
        raise FileNotFoundError(f"updateAllNodes.sh not found at {script}")
    command = [str(script), firmware_version]
    return _run_command(command, env=_prepare_environment("esp32", env))


def build_individual_node(
    session: Session,
    node_id: str,
    *,
    metadata: Optional[Dict[str, Any]] = None,
    board: Optional[str] = None,
    regenerate_token: bool = False,
    run_build: bool = True,
) -> BuildResult:
    """Generate an sdkconfig for ``node_id`` and optionally run ``idf.py build``."""

    node_credentials.sync_registry_nodes(session)
    registration = node_credentials.get_registration_by_node_id(session, node_id)
    if registration is None:
        raise NodeBuilderError(f"Unknown node id: {node_id}")

    if not registration.provisioning_token or regenerate_token:
        _, token = node_credentials.rotate_token(session, node_id)
        registration = node_credentials.get_registration_by_node_id(session, node_id)
        token_value = token
    else:
        token_value = registration.provisioning_token or ""

    download_id = registration.download_id
    manifest_url = f"{settings.PUBLIC_BASE}/firmware/{download_id}/manifest.json"

    metadata_payload = metadata or dict(registration.hardware_metadata or {})
    if board:
        metadata_payload["board"] = board
    else:
        metadata_payload.setdefault("board", "esp32")

    sdkconfig_path = render_sdkconfig(
        node_id=node_id,
        download_id=download_id,
        token=token_value,
        metadata=metadata_payload,
        manifest_url=manifest_url,
    )

    env = _prepare_environment(str(metadata_payload.get("board", "esp32")))
    env["SDKCONFIG"] = str(sdkconfig_path)

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
    )


def first_time_flash(
    session: Session,
    node_id: str,
    *,
    port: str,
    metadata: Optional[Dict[str, Any]] = None,
    board: Optional[str] = None,
) -> BuildResult:
    """Perform ``idf.py -p <port> build flash`` for ``node_id``."""

    build_result = build_individual_node(
        session,
        node_id,
        metadata=metadata,
        board=board,
        regenerate_token=False,
        run_build=False,
    )

    env = _prepare_environment(str(metadata or {}).get("board", board or "esp32"))
    env["SDKCONFIG"] = str(build_result.sdkconfig_path)
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

