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


def _ensure_symlink(node_id: str, download_id: str) -> Path:
    storage_root = settings.FIRMWARE_DIR
    link_root = settings.FIRMWARE_SYMLINK_DIR

    target_dir = storage_root / node_id
    target_dir.mkdir(parents=True, exist_ok=True)

    def _ensure_link(link_path: Path) -> Path:
        link_path.parent.mkdir(parents=True, exist_ok=True)

        if link_path.exists() or link_path.is_symlink():
            try:
                existing_target = link_path.resolve(strict=True)
            except FileNotFoundError:
                existing_target = None
            if existing_target == target_dir:
                return link_path
            if link_path.is_symlink() or link_path.is_file():
                link_path.unlink()
            elif link_path.is_dir():
                if target_dir.exists():
                    try:
                        next(target_dir.iterdir())
                    except StopIteration:
                        target_dir.rmdir()
                        shutil.move(str(link_path), str(target_dir))
                    else:
                        for child in link_path.iterdir():
                            dest = target_dir / child.name
                            if dest.exists():
                                continue
                            if child.is_dir():
                                shutil.copytree(child, dest)
                            else:
                                shutil.copy2(child, dest)
                        shutil.rmtree(link_path)
                else:
                    shutil.move(str(link_path), str(target_dir))
            else:
                raise RuntimeError(
                    f"Refusing to replace existing directory at {link_path}"
                )

        link_path.symlink_to(target_dir, target_is_directory=True)
        return link_path

    _ensure_link(link_root / node_id)
    return _ensure_link(link_root / download_id)


def _remove_symlink(download_id: Optional[str]) -> None:
    if not download_id:
        return
    link_path = settings.FIRMWARE_SYMLINK_DIR / download_id
    if link_path.is_symlink():
        link_path.unlink()


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
                _remove_symlink(previous_download)
            try:
                link = _ensure_symlink(args.node_id, download_id)
            except RuntimeError as exc:  # pragma: no cover - defensive
                print(f"Warning: {exc}", file=sys.stderr)
            else:
                print(
                    f"Symlink: {link} -> {link.resolve() if link.exists() else 'missing'}"
                )

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
        help="Do not manage firmware symlinks for the download identifier.",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return _issue_credentials(args)


if __name__ == "__main__":
    raise SystemExit(main())
