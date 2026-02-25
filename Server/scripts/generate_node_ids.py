#!/usr/bin/env python3
"""Batch-generate opaque node registrations for manufacturing."""

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

from app import database, node_credentials  # noqa: E402
from app.auth.service import init_auth_storage  # noqa: E402


def _load_metadata_entries(path: Optional[str]) -> Optional[List[Dict[str, Any]]]:
    if not path:
        return None

    metadata_path = Path(path).expanduser()
    if not metadata_path.exists():
        raise FileNotFoundError(f"metadata file not found: {metadata_path}")

    payload = json.loads(metadata_path.read_text())
    if isinstance(payload, list):
        entries: List[Dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                raise ValueError("metadata list entries must be objects")
            entries.append(dict(item))
        return entries
    if isinstance(payload, dict):
        return [dict(payload)]

    raise ValueError("metadata file must contain a JSON object or list of objects")


def _emit_json(records: Iterable[Dict[str, Any]]) -> None:
    print(json.dumps(list(records), indent=2))


def _emit_csv(records: Iterable[Dict[str, Any]]) -> None:
    import csv

    fieldnames = [
        "node_id",
        "download_id",
        "ota_token",
        "token_hash",
        "created_at",
        "hardware_metadata",
    ]
    writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
    writer.writeheader()
    for record in records:
        row = dict(record)
        metadata = row.get("hardware_metadata")
        row["hardware_metadata"] = json.dumps(metadata or {})
        writer.writerow(row)


def _format_record(entry: node_credentials.NodeRegistrationWithToken) -> Dict[str, Any]:
    registration = entry.registration
    return {
        "node_id": registration.node_id,
        "download_id": registration.download_id,
        "ota_token": entry.plaintext_token,
        "token_hash": registration.token_hash,
        "created_at": registration.created_at.isoformat()
        if isinstance(registration.created_at, datetime)
        else str(registration.created_at),
        "hardware_metadata": registration.hardware_metadata,
    }


def generate_nodes(
    *,
    count: int,
    metadata_entries: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    init_auth_storage()

    metadata_iter: Optional[Iterable[Dict[str, Any]]] = None
    if metadata_entries:
        metadata_iter = metadata_entries

    with database.SessionLocal() as session:
        entries = node_credentials.create_batch(
            session,
            count,
            metadata=metadata_iter,
        )
        return [_format_record(entry) for entry in entries]


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "count",
        type=int,
        help="Number of node identifiers to generate",
    )
    parser.add_argument(
        "--metadata-file",
        type=str,
        default=None,
        help="Optional JSON file containing hardware metadata objects",
    )
    parser.add_argument(
        "--format",
        choices=("json", "csv"),
        default="json",
        help="Output format (default: json)",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    metadata_entries = _load_metadata_entries(args.metadata_file)
    records = generate_nodes(
        count=args.count,
        metadata_entries=metadata_entries,
    )

    if args.format == "csv":
        _emit_csv(records)
    else:
        _emit_json(records)

    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
