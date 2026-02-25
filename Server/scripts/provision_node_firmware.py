#!/usr/bin/env python3
"""Provision firmware defaults for an opaque node identifier."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlmodel import Session, select

from app import database, node_builder, node_credentials, registry
from app.auth.models import AuditLog, NodeCredential, NodeRegistration, User
from app.auth.service import init_auth_storage
from app.config import settings


def _ensure_download_dir(download_id: str) -> Path:
    download_dir = settings.FIRMWARE_DIR / download_id
    download_dir.mkdir(parents=True, exist_ok=True)
    return download_dir


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
    users = session.exec(select(User.id, User.username)).all()
    mapping: Dict[int, str] = {}
    for user_id, username in users:
        if user_id is None:
            continue
        mapping[user_id] = str(username)
    return mapping


def _extract_node_id(data: Any) -> Optional[str]:
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
        registrations = session.exec(select(NodeRegistration)).all()
        credential_rows = session.exec(select(NodeCredential)).all()
        creators = _load_node_creators(session)

    if not registrations:
        print("No nodes registered.")
        return 0

    credential_map = {entry.node_id: entry for entry in credential_rows}
    print(
        f"{'Node ID':<30} {'Status':<12} {'Display Name':<24} {'Assignment':<24} {'Provisioned':<20} Creator"
    )
    print("-" * 125)

    def _status_for(reg: NodeRegistration, cred: Optional[NodeCredential]) -> str:
        if reg.provisioned_at or (cred and cred.provisioned_at):
            return "provisioned"
        if reg.assigned_at or cred:
            return "assigned"
        return "available"

    for registration in sorted(registrations, key=lambda r: r.node_id):
        credential = credential_map.get(registration.node_id)
        status = _status_for(registration, credential)
        display_name = registration.display_name or (
            credential.display_name if credential else "—"
        )
        house = registration.house_slug or (credential.house_slug if credential else None)
        room = registration.room_id or (credential.room_id if credential else None)
        if house or room:
            assignment = f"{house or '—'} / {room or '—'}"
        else:
            assignment = "—"
        provisioned = registration.provisioned_at or (
            credential.provisioned_at if credential else None
        )
        creator = creators.get(registration.node_id, "—")
        print(
            f"{registration.node_id:<30} {status:<12} {display_name:<24} {assignment:<24} {_format_timestamp(provisioned):<20} {creator}"
        )
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

    token: Optional[str] = None
    download_id: Optional[str] = None
    metadata_payload: Dict[str, Any] = {}
    previous_download: Optional[str] = None
    final_registration: Optional[NodeRegistration] = None
    final_credential: Optional[NodeCredential] = None
    manifest_url: Optional[str] = None
    download_dir: Optional[Path] = None

    with database.SessionLocal() as session:
        node_credentials.sync_registry_nodes(session)
        registration = node_credentials.get_registration_by_node_id(
            session, args.node_id
        )
        if registration is None:
            print(f"Unknown node id: {args.node_id}", file=sys.stderr)
            return 1

        credential = node_credentials.get_by_node_id(session, args.node_id)

        provisioned_at = registration.provisioned_at or (
            credential.provisioned_at if credential else None
        )
        if (
            provisioned_at is not None
            and not args.allow_reprovision
            and not args.no_mark_provisioned
        ):
            print(
                "Node already marked as provisioned. Use --allow-reprovision to override.",
                file=sys.stderr,
            )
            return 1

        previous_download = registration.download_id
        if args.rotate_download:
            node_credentials.update_download_id(session, args.node_id)
            registration = node_credentials.get_registration_by_node_id(
                session, args.node_id
            )
            credential = node_credentials.get_by_node_id(session, args.node_id)

        download_id = registration.download_id
        download_dir = _ensure_download_dir(download_id)

        provided_token = (args.ota_token or "").strip() if args.ota_token else None
        token_source = ""

        if args.rotate_token:
            credential, token = node_credentials.rotate_token(session, args.node_id)
            registration = node_credentials.get_registration_by_node_id(
                session, args.node_id
            )
            token_source = "rotated"
        elif provided_token:
            expected_hash = registry.hash_node_token(provided_token)
            if registration.token_hash and registration.token_hash != expected_hash:
                print(
                    "Provided OTA token does not match stored hash. Use --rotate-token to"
                    " generate a replacement or supply the correct token.",
                    file=sys.stderr,
                )
                return 1
            if registration.token_hash != expected_hash:
                node_credentials.rotate_token(
                    session, args.node_id, token=provided_token
                )
                registration = node_credentials.get_registration_by_node_id(
                    session, args.node_id
                )
                credential = node_credentials.get_by_node_id(session, args.node_id)
            token = provided_token
            token_source = "provided"
        elif registration.provisioning_token:
            token = registration.provisioning_token
            node_credentials.clear_stored_provisioning_token(session, args.node_id)
            registration = node_credentials.get_registration_by_node_id(
                session, args.node_id
            )
            credential = node_credentials.get_by_node_id(session, args.node_id)
            token_source = "legacy"
        else:
            print(
                "No OTA token available. Provide --ota-token with the pre-generated token"
                " or use --rotate-token to mint a new one.",
                file=sys.stderr,
            )
            return 1

        manifest_url = f"{settings.PUBLIC_BASE}/firmware/{download_id}/manifest.json"
        values = {
            "CONFIG_UL_NODE_ID": args.node_id,
            "CONFIG_UL_OTA_MANIFEST_URL": manifest_url,
            "CONFIG_UL_OTA_BEARER_TOKEN": token,
        }

        metadata_payload = registration.hardware_metadata or {}
        if metadata_payload:
            values["CONFIG_UL_NODE_METADATA"] = json.dumps(
                metadata_payload, separators=(",", ":"), sort_keys=True
            )

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

        final_registration = node_credentials.get_registration_by_node_id(
            session, args.node_id
        )
        final_credential = node_credentials.get_by_node_id(session, args.node_id)

        expected_hash = registry.hash_node_token(token)
        needs_sync = False
        if final_registration and final_registration.token_hash != expected_hash:
            needs_sync = True
        if final_credential and final_credential.token_hash != expected_hash:
            needs_sync = True

        if needs_sync:
            node_credentials.rotate_token(session, args.node_id, token=token)
            final_registration = node_credentials.get_registration_by_node_id(
                session, args.node_id
            )
            final_credential = node_credentials.get_by_node_id(session, args.node_id)

    _, _, node = registry.find_node(args.node_id)
    name = ""
    if isinstance(node, dict):
        name = str(node.get("name") or "")
    if not name and final_registration:
        name = final_registration.display_name or ""
    if not name and final_credential:
        name = final_credential.display_name or ""

    assignment_parts: List[str] = []
    if final_registration:
        if final_registration.house_slug:
            assignment_parts.append(final_registration.house_slug)
        if final_registration.room_id:
            assignment_parts.append(final_registration.room_id)
    if not assignment_parts and final_credential:
        assignment_parts.append(final_credential.house_slug)
        assignment_parts.append(final_credential.room_id)
    assignment_display = " / ".join(part for part in assignment_parts if part) or "—"

    status = "available"
    if final_registration and final_registration.provisioned_at:
        status = "provisioned"
    elif final_registration and final_registration.assigned_at:
        status = "assigned"

    print("\n--- Firmware provisioning ---")
    if name:
        print(f"Node: {args.node_id} ({name})")
    else:
        print(f"Node: {args.node_id}")
    print(f"Status: {status}")
    print(f"Assignment: {assignment_display}")
    print(f"Download ID: {download_id}")
    print(f"Manifest URL: {manifest_url}")
    print(f"Bearer Token: {token}")
    if token_source == "legacy":
        print(
            "Warning: consumed legacy stored OTA token; future runs must provide"
            " --ota-token or --rotate-token.",
            file=sys.stderr,
        )
    elif token_source == "rotated":
        print("Generated new OTA token via --rotate-token.")
    if metadata_payload:
        print("Hardware metadata:")
        print(json.dumps(metadata_payload, indent=2, sort_keys=True))
    if updated_files:
        print("Updated configuration files:")
        for cfg in updated_files:
            print(f"  - {cfg}")
    print(f"Firmware directory: {download_dir}")
    if previous_download and previous_download != download_id:
        print(f"Previous download id was {previous_download}")
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
        "--ota-token",
        dest="ota_token",
        metavar="TOKEN",
        help="Pre-generated OTA bearer token to embed in the firmware.",
    )
    parser.add_argument(
        "--rotate-token",
        action="store_true",
        help="Generate a new OTA token instead of using a pre-generated one.",
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
