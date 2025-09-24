#!/usr/bin/env python3
"""Provision firmware defaults for an opaque node identifier."""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlmodel import Session, select  # noqa: E402

from app import database, node_credentials, registry  # noqa: E402
from app.auth.models import AuditLog, NodeCredential, User  # noqa: E402
from app.auth.service import init_auth_storage  # noqa: E402
from app.config import settings  # noqa: E402


def _remove_download_directory(download_id: Optional[str]) -> None:
    if not download_id:
        return

    path = settings.FIRMWARE_DIR / download_id
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _copy_tree(src: Path, dest: Path) -> None:
    for child in src.iterdir():
        target = dest / child.name
        if child.is_dir():
            shutil.copytree(child, target, dirs_exist_ok=True)
        else:
            shutil.copy2(child, target)


def _ensure_download_directory(node_id: str, download_id: str) -> Path:
    firmware_dir = settings.FIRMWARE_DIR
    download_path = firmware_dir / download_id
    legacy_dir = firmware_dir / node_id

    if download_path.is_symlink():
        try:
            target = download_path.resolve(strict=True)
        except FileNotFoundError:
            target = None
        download_path.unlink()
        if target and target.exists() and target.is_dir():
            try:
                target.rename(download_path)
            except OSError:
                download_path.mkdir(parents=True, exist_ok=True)
                _copy_tree(target, download_path)
                if target == legacy_dir and legacy_dir.exists():
                    shutil.rmtree(legacy_dir)
            else:
                return download_path

    if download_path.exists() and not download_path.is_dir():
        raise RuntimeError(f"Firmware path is not a directory: {download_path}")

    download_path.mkdir(parents=True, exist_ok=True)

    if legacy_dir.exists() and legacy_dir.is_dir() and legacy_dir != download_path:
        _copy_tree(legacy_dir, download_path)
        try:
            shutil.rmtree(legacy_dir)
        except OSError:
            pass

    return download_path


def _update_sdkconfig(path: Path, values: Dict[str, str]) -> None:
    path = path.expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"sdkconfig file not found: {path}")

    lines = path.read_text().splitlines()
    updated: List[str] = []
    seen: Dict[str, bool] = {key: False for key in values}

    for line in lines:
        replaced = False
        stripped = line.strip()
        for key, value in values.items():
            if stripped.startswith(f"{key}="):
                updated.append(f'{key}="{value}"')
                seen[key] = True
                replaced = True
                break
        if not replaced:
            updated.append(line)

    for key, present in seen.items():
        if not present:
            updated.append(f'{key}="{values[key]}"')

    updated.append("")
    path.write_text("\n".join(updated))


def _format_timestamp(value: Optional[datetime]) -> str:
    if not value:
        return "—"
    return value.isoformat(timespec="seconds")


def _actor_lookup(session: Session) -> Dict[int, str]:
    """Return a mapping of user ids to usernames."""

    users = session.exec(select(User.id, User.username)).all()
    mapping: Dict[int, str] = {}
    for user_id, username in users:
        if user_id is None:
            continue
        mapping[user_id] = str(username)
    return mapping


def _extract_node_id(data: Any) -> Optional[str]:
    """Best-effort attempt to find a node id within ``data``."""

    if isinstance(data, dict):
        for key in ("node_id", "nodeId", "node"):
            value = data.get(key)
            if isinstance(value, str):
                clean = value.strip()
                if clean:
                    return clean

        nodes_value = data.get("nodes")
        if isinstance(nodes_value, dict):
            candidate = _extract_node_id(nodes_value)
            if candidate:
                return candidate
        elif isinstance(nodes_value, Iterable) and not isinstance(
            nodes_value, (str, bytes)
        ):
            for item in nodes_value:
                candidate = _extract_node_id(item)
                if candidate:
                    return candidate

    elif isinstance(data, (list, tuple)):
        for item in data:
            candidate = _extract_node_id(item)
            if candidate:
                return candidate

    return None


def _load_node_creators(session: Session) -> Dict[str, str]:
    """Return a mapping of node ids to usernames based on audit logs."""

    actor_names = _actor_lookup(session)
    creators: Dict[str, str] = {}

    audit_entries = session.exec(select(AuditLog).order_by(AuditLog.created_at)).all()
    for entry in audit_entries:
        node_id = _extract_node_id(entry.data)
        if not node_id:
            continue

        action = (entry.action or "").lower()
        if not any(keyword in action for keyword in ("create", "add", "register")):
            continue

        if node_id in creators:
            continue

        if entry.actor_id is None:
            creators[node_id] = "—"
        else:
            creators[node_id] = actor_names.get(entry.actor_id, f"User #{entry.actor_id}")

    return creators


def _list_nodes() -> int:
    init_auth_storage()
    with database.SessionLocal() as session:
        node_credentials.sync_registry_nodes(session)
        entries = session.exec(select(NodeCredential)).all()
        creators = _load_node_creators(session)

    if not entries:
        print("No nodes registered.")
        return 0

    print(
        "Node ID                          Name                         Created By                  Provisioned"
    )
    print("-" * 100)
    for entry in entries:
        mark = "" if entry.provisioned_at is None else "*"
        name = entry.display_name or "—"
        created_by = creators.get(entry.node_id, "—")
        print(
            f"{entry.node_id:<30} {name:<27} {created_by:<27} {_format_timestamp(entry.provisioned_at)}{mark}"
        )
    print("\n* indicates firmware already provisioned")
    return 0


def _provision(args: argparse.Namespace) -> int:
    if not args.node_id:
        print("node_id required unless --list specified", file=sys.stderr)
        return 1

    config_paths = args.config or [PROJECT_ROOT / "UltraNodeV5/sdkconfig"]
    normalized_configs: List[Path] = []
    for raw in config_paths:
        path = Path(raw)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        normalized_configs.append(path)
    normalized_configs = list(dict.fromkeys(normalized_configs))

    init_auth_storage()
    registry.ensure_house_external_ids()

    with database.SessionLocal() as session:
        node_credentials.sync_registry_nodes(session)
        credential = node_credentials.get_by_node_id(session, args.node_id)
        if credential is None:
            print(f"Unknown node id: {args.node_id}", file=sys.stderr)
            return 1

        if (
            credential.provisioned_at is not None
            and not args.allow_reprovision
            and not args.no_mark_provisioned
        ):
            print(
                "Node already marked as provisioned. Use --allow-reprovision to override.",
                file=sys.stderr,
            )
            return 1

        previous_download = credential.download_id
        if args.rotate_download:
            credential = node_credentials.update_download_id(session, args.node_id)

        download_id = credential.download_id

        if not args.no_symlink:
            if previous_download and previous_download != download_id:
                _remove_download_directory(previous_download)
            try:
                storage_path = _ensure_download_directory(
                    args.node_id, download_id
                )
            except RuntimeError as exc:  # pragma: no cover - defensive
                print(f"Warning: {exc}", file=sys.stderr)
                storage_path = None
            else:
                print(f"Firmware directory: {storage_path}")
        else:
            storage_path = None

        credential, token = node_credentials.rotate_token(session, args.node_id)

        manifest_url = f"{settings.PUBLIC_BASE}/firmware/{download_id}/manifest.json"
        values = {
            "CONFIG_UL_NODE_ID": args.node_id,
            "CONFIG_UL_OTA_MANIFEST_URL": manifest_url,
            "CONFIG_UL_OTA_BEARER_TOKEN": token,
        }

        updated_files: List[Path] = []
        for cfg in normalized_configs:
            try:
                _update_sdkconfig(cfg, values)
            except FileNotFoundError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            updated_files.append(cfg)

        if not args.no_mark_provisioned:
            node_credentials.mark_provisioned(session, args.node_id)

        node_credentials.sync_registry_nodes(session)

    _, _, node = registry.find_node(args.node_id)
    name = node.get("name") if isinstance(node, dict) else ""

    print("\n--- Firmware provisioning ---")
    if name:
        print(f"Node: {args.node_id} ({name})")
    else:
        print(f"Node: {args.node_id}")
    print(f"Download ID: {download_id}")
    print(f"Manifest URL: {manifest_url}")
    print(f"Bearer Token: {token}")
    if updated_files:
        print("Updated configuration files:")
        for cfg in updated_files:
            print(f"  - {cfg}")
    if storage_path:
        print(f"Firmware directory: {storage_path}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("node_id", nargs="?", help="Opaque node identifier to provision")
    parser.add_argument(
        "--config",
        action="append",
        metavar="PATH",
        help="sdkconfig file to update (defaults to UltraNodeV5/sdkconfig)",
    )
    parser.add_argument(
        "--rotate-download",
        action="store_true",
        help="Issue a new download identifier before provisioning.",
    )
    parser.add_argument(
        "--no-symlink",
        action="store_true",
        help="Skip managing the firmware download directory on disk.",
    )
    parser.add_argument(
        "--allow-reprovision",
        action="store_true",
        help="Allow provisioning even if the node is already marked provisioned.",
    )
    parser.add_argument(
        "--no-mark-provisioned",
        action="store_true",
        help="Do not update the provisioned timestamp in the database.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List nodes and their provisioning status instead of updating firmware.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.list:
        return _list_nodes()
    return _provision(args)


if __name__ == "__main__":
    raise SystemExit(main())
