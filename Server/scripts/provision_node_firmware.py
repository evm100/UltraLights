#!/usr/bin/env python3
"""Provision firmware defaults for an opaque node identifier."""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlmodel import select  # noqa: E402

from app import database, node_credentials, registry  # noqa: E402
from app.auth.models import NodeCredential  # noqa: E402
from app.auth.service import init_auth_storage  # noqa: E402
from app.config import settings  # noqa: E402


def _ensure_symlink(node_id: str, download_id: str) -> Path:
    storage_root = settings.FIRMWARE_DIR
    link_root = settings.FIRMWARE_SYMLINK_DIR

    target_dir = storage_root / node_id
    target_dir.mkdir(parents=True, exist_ok=True)

    def _migrate_into_storage(path: Path) -> None:
        if not path.exists() and not path.is_symlink():
            return

        if path.is_symlink():
            path.unlink()
            return

        if path.is_file():
            raise RuntimeError(f"Unexpected firmware file at {path}")

        if not any(target_dir.iterdir()):
            try:
                path.rename(target_dir)
                return
            except OSError:
                pass

        for child in path.iterdir():
            dest = target_dir / child.name
            if dest.exists():
                continue
            if child.is_dir():
                shutil.copytree(child, dest)
            else:
                shutil.copy2(child, dest)
        shutil.rmtree(path)

    legacy_node_path = link_root / node_id
    _migrate_into_storage(legacy_node_path)
    if legacy_node_path.exists() or legacy_node_path.is_symlink():
        if legacy_node_path.is_dir():
            shutil.rmtree(legacy_node_path)
        else:
            legacy_node_path.unlink()

    link_path = link_root / download_id
    _migrate_into_storage(link_path)

    link_path.parent.mkdir(parents=True, exist_ok=True)

    if link_path.exists() or link_path.is_symlink():
        try:
            existing_target = link_path.resolve(strict=True)
        except FileNotFoundError:
            existing_target = None
        if existing_target == target_dir:
            return link_path
        if link_path.is_dir():
            shutil.rmtree(link_path)
        else:
            link_path.unlink()

    link_path.symlink_to(target_dir, target_is_directory=True)
    return link_path


def _remove_symlink(download_id: Optional[str]) -> None:
    if not download_id:
        return
    link_path = settings.FIRMWARE_SYMLINK_DIR / download_id
    if link_path.is_symlink():
        link_path.unlink()


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


def _list_nodes() -> int:
    init_auth_storage()
    with database.SessionLocal() as session:
        node_credentials.sync_registry_nodes(session)
        entries = session.exec(select(NodeCredential)).all()

    if not entries:
        print("No nodes registered.")
        return 0

    print("Node ID                          Name                         Provisioned")
    print("-" * 72)
    for entry in entries:
        mark = "" if entry.provisioned_at is None else "*"
        name = entry.display_name or "—"
        print(
            f"{entry.node_id:<30} {name:<27} {_format_timestamp(entry.provisioned_at)}{mark}"
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
                _remove_symlink(previous_download)
            try:
                link = _ensure_symlink(args.node_id, download_id)
            except RuntimeError as exc:  # pragma: no cover - defensive
                print(f"Warning: {exc}", file=sys.stderr)
                link = None
            else:
                print(
                    f"Symlink: {link} -> {link.resolve() if link.exists() else 'missing'}"
                )
        else:
            link = None

        credential, token = node_credentials.rotate_token(session, args.node_id)

        manifest_url = f"{settings.PUBLIC_BASE}/firmware/{download_id}/manifest"
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
    if link:
        print(f"Firmware symlink: {link}")
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
        help="Skip creating the firmware download symlink.",
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
