#!/usr/bin/env python3
"""Generate per-node OTA credentials and download aliases."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import node_credentials, registry, database  # noqa: E402
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


def _issue_credentials(args: argparse.Namespace) -> int:
    registry.ensure_house_external_ids()
    init_auth_storage()

    with database.SessionLocal() as session:
        node_credentials.sync_registry_nodes(session)
        credential = node_credentials.get_by_node_id(session, args.node_id)
        if credential is None:
            print(f"Unknown node id: {args.node_id}", file=sys.stderr)
            return 1

        previous_download = credential.download_id

        if args.download_id:
            credential = node_credentials.update_download_id(
                session, args.node_id, args.download_id
            )
        elif args.rotate_download:
            credential = node_credentials.update_download_id(session, args.node_id)

        download_id = credential.download_id

        if not args.no_symlink:
            if previous_download and previous_download != download_id:
                _remove_download_directory(previous_download)
            try:
                storage_path = _ensure_download_directory(args.node_id, download_id)
            except RuntimeError as exc:  # pragma: no cover - defensive
                print(f"Warning: {exc}", file=sys.stderr)
            else:
                print(f"Firmware directory: {storage_path}")

        if args.token:
            credential, token = node_credentials.rotate_token(
                session, args.node_id, token=args.token
            )
        else:
            credential, token = node_credentials.rotate_token(session, args.node_id)

        node_credentials.sync_registry_nodes(session)

    _, _, node = registry.find_node(args.node_id)
    node_name = node.get("name") if isinstance(node, dict) else None

    manifest_url = f"{settings.PUBLIC_BASE}/firmware/{credential.download_id}/manifest"
    binary_url = f"{settings.PUBLIC_BASE}/firmware/{credential.download_id}/latest.bin"

    print("--- OTA credentials ---")
    print(f"Node: {args.node_id}")
    if isinstance(node_name, str) and node_name:
        print(f"Name: {node_name}")
    print(f"Download ID: {credential.download_id}")
    print(f"Manifest URL: {manifest_url}")
    print(f"Binary URL:   {binary_url}")
    print(f"Bearer Token: {token}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("node_id", help="Registry node identifier (e.g. house-room-node)")
    parser.add_argument(
        "--token",
        help="Use an explicit bearer token instead of generating a random one.",
    )
    parser.add_argument(
        "--download-id",
        help="Assign a specific download identifier (otherwise generated automatically).",
    )
    parser.add_argument(
        "--rotate-download",
        action="store_true",
        help="Force generation of a new download identifier even if one already exists.",
    )
    parser.add_argument(
        "--no-symlink",
        action="store_true",
        help="Do not manage the firmware download directory on disk.",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return _issue_credentials(args)


if __name__ == "__main__":
    raise SystemExit(main())
