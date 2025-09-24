#!/usr/bin/env python3
"""Management helpers for bootstrapping the UltraLights server."""
from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path
from typing import Dict, Iterable, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import database
from app.auth.models import House, HouseMembership, HouseRole, User
from app.auth.security import hash_password
from app.auth.service import create_user, init_auth_storage, record_audit_event
from app.config import settings
from sqlmodel import select


def _update_env_file(env_path: Path, updates: Dict[str, str]) -> None:
    lines: List[str]
    if env_path.exists():
        lines = env_path.read_text().splitlines()
    else:
        lines = []

    rendered: List[str] = []
    seen: set[str] = set()
    for line in lines:
        key, sep, value = line.partition("=")
        stripped_key = key.strip()
        if sep and stripped_key in updates:
            rendered.append(f"{stripped_key}={updates[stripped_key]}")
            seen.add(stripped_key)
        else:
            rendered.append(line)

    for key, value in updates.items():
        if key not in seen:
            rendered.append(f"{key}={value}")

    env_path.write_text("\n".join(rendered) + "\n")


def _log_system_event(action: str, summary: str, data: Dict[str, object] | None = None) -> None:
    with database.SessionLocal() as session:
        record_audit_event(
            session,
            actor=None,
            action=action,
            summary=summary,
            data=data or {},
            commit=True,
        )


def _command_create_admin(args: argparse.Namespace) -> int:
    init_auth_storage()
    with database.SessionLocal() as session:
        existing = session.exec(select(User).where(User.username == args.username)).first()
        if existing:
            if not args.force:
                print(f"User '{args.username}' already exists; skipping")
                return 0
            existing.hashed_password = hash_password(args.password)
            existing.server_admin = True
            session.commit()
            session.refresh(existing)
            _log_system_event(
                "admin_password_rotated",
                f"Rotated credentials for {existing.username}",
                {"user_id": existing.id},
            )
            print(f"Updated password for existing admin '{existing.username}'")
            return 0

        user = create_user(
            session,
            args.username,
            args.password,
            server_admin=True,
        )
        _log_system_event(
            "admin_bootstrap",
            f"Created initial server admin {user.username}",
            {"user_id": user.id},
        )
        print(f"Created server admin '{user.username}' (id={user.id})")
        return 0


def _command_rotate_secrets(args: argparse.Namespace) -> int:
    env_path = Path(args.env_file).expanduser().resolve()
    updates = {
        "SESSION_SECRET": secrets.token_urlsafe(48),
        "API_BEARER": secrets.token_urlsafe(32),
        "MANIFEST_HMAC_SECRET": secrets.token_hex(32),
    }
    env_path.parent.mkdir(parents=True, exist_ok=True)
    _update_env_file(env_path, updates)
    init_auth_storage()
    _log_system_event(
        "secrets_rotated",
        "Generated new server secrets",
        {"env_file": str(env_path), "keys": sorted(updates)},
    )
    print(f"Wrote new secrets to {env_path}")
    return 0


def _command_seed_sample_data(args: argparse.Namespace) -> int:
    init_auth_storage()
    created: List[str] = []
    with database.SessionLocal() as session:
        houses = session.exec(select(House).order_by(House.id)).all()
        if not houses:
            print("No houses found in the registry; nothing to seed")
            return 0

        for house in houses:
            username = f"{args.prefix}{house.external_id}"
            existing_user = session.exec(select(User).where(User.username == username)).first()
            if existing_user:
                continue

            user = create_user(
                session,
                username,
                args.password,
                server_admin=False,
            )
            membership = HouseMembership(
                user_id=user.id,
                house_id=house.id,
                role=HouseRole.ADMIN,
            )
            session.add(membership)
            session.commit()
            session.refresh(membership)
            created.append(user.username)

    if created:
        _log_system_event(
            "sample_data_seeded",
            "Seeded sample house administrators",
            {"users": created},
        )
        print("Created sample users:", ", ".join(created))
    else:
        print("Sample users already present; no changes made")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        dest="database_url",
        help="Override the authentication database URL",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_admin = subparsers.add_parser(
        "create-admin", help="Create or update the initial server admin",
    )
    create_admin.add_argument("--username", required=True)
    create_admin.add_argument("--password", required=True)
    create_admin.add_argument(
        "--force",
        action="store_true",
        help="Update the password if the user already exists",
    )
    create_admin.set_defaults(func=_command_create_admin)

    rotate = subparsers.add_parser(
        "rotate-secrets", help="Generate new shared secrets and update the env file",
    )
    rotate.add_argument(
        "--env-file",
        default=".env",
        help="Path to the environment file (default: %(default)s)",
    )
    rotate.set_defaults(func=_command_rotate_secrets)

    seed = subparsers.add_parser(
        "seed-sample-data",
        help="Create demo users for each house in the registry",
    )
    seed.add_argument(
        "--password",
        default="sample-password",
        help="Password assigned to the sample users (default: %(default)s)",
    )
    seed.add_argument(
        "--prefix",
        default="demo-",
        help="Username prefix for generated users (default: %(default)s)",
    )
    seed.set_defaults(func=_command_seed_sample_data)

    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.database_url:
        database.reset_session_factory(args.database_url)
        settings.AUTH_DB_URL = args.database_url

    handler = getattr(args, "func", None)
    if handler is None:
        parser.print_help()
        return 1

    return handler(args)


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())

