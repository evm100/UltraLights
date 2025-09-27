"""Standalone firmware build and flash helper for UltraLights nodes."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SERVER_ROOT = PROJECT_ROOT / "Server"
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from app import database, node_builder, node_credentials  # noqa: E402
from app.auth.models import NodeRegistration  # noqa: E402
from app.config import settings  # noqa: E402
from sqlmodel import select  # noqa: E402

PROJECT_SDKCONFIG_PATHS: tuple[Path, ...] = (
    node_builder.FIRMWARE_ROOT / "sdkconfig",
)


def _resolve_paths(firmware_dir_arg: Optional[str], archive_dir_arg: Optional[str]) -> tuple[Path, Path]:
    firmware_dir = (
        Path(firmware_dir_arg).expanduser().resolve()
        if firmware_dir_arg
        else Path(settings.FIRMWARE_DIR)
    )
    archive_dir = (
        Path(archive_dir_arg).expanduser().resolve()
        if archive_dir_arg
        else node_builder.FIRMWARE_ARCHIVE_ROOT
    )
    firmware_dir.mkdir(parents=True, exist_ok=True)
    archive_dir.mkdir(parents=True, exist_ok=True)
    return firmware_dir, archive_dir


def _print_command_failure(result: node_builder.CommandResult) -> None:
    cmd = " ".join(result.command)
    print(f"Command failed ({result.returncode}): {cmd}", file=sys.stderr)
    if result.stdout:
        print("--- stdout ---", file=sys.stderr)
        print(result.stdout, file=sys.stderr)
    if result.stderr:
        print("--- stderr ---", file=sys.stderr)
        print(result.stderr, file=sys.stderr)


def _load_registrations(session) -> list[NodeRegistration]:
    node_credentials.sync_registry_nodes(session)
    rows = session.exec(select(NodeRegistration).order_by(NodeRegistration.node_id)).all()
    return rows


def _common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--database-url",
        dest="database_url",
        help="Override the authentication database URL",
    )
    parser.add_argument(
        "--firmware-dir",
        dest="firmware_dir",
        help="Destination directory for public firmware artifacts",
    )
    parser.add_argument(
        "--archive-dir",
        dest="archive_dir",
        help="Directory for private firmware archives",
    )


def _add_build_parser(subparsers) -> argparse.ArgumentParser:
    build = subparsers.add_parser(
        "build",
        help="Build firmware for a single node and archive the result",
    )
    build.add_argument("node_id", help="Node identifier to build")
    build.add_argument(
        "--firmware-version",
        required=True,
        help="Version string recorded in the sdkconfig and manifest",
    )
    build.add_argument(
        "--no-clean",
        action="store_true",
        help="Skip removing the build directory before invoking idf.py",
    )
    return build


def _add_flash_parser(subparsers) -> argparse.ArgumentParser:
    flash = subparsers.add_parser(
        "flash",
        help="Build, flash, and archive firmware for a node",
    )
    flash.add_argument("node_id", help="Node identifier to flash")
    flash.add_argument("--port", required=True, help="Serial port passed to idf.py")
    flash.add_argument(
        "--firmware-version",
        required=True,
        help="Version string recorded in the sdkconfig and manifest",
    )
    flash.add_argument(
        "--no-clean",
        action="store_true",
        help="Skip removing the build directory before invoking idf.py",
    )
    return flash


def _add_update_all_parser(subparsers) -> argparse.ArgumentParser:
    update = subparsers.add_parser(
        "update-all",
        help="Build and archive firmware for every registered node",
    )
    update.add_argument(
        "--firmware-version",
        required=True,
        help="Version string recorded in each manifest",
    )
    update.add_argument(
        "--no-clean",
        action="store_true",
        help="Skip removing the build directory before each build",
    )
    return update


def _maybe_reset_database(database_url: Optional[str]):
    if not database_url:
        return None
    original = settings.AUTH_DB_URL
    database.reset_session_factory(database_url)
    return original


def _restore_database(original_url: Optional[str]) -> None:
    if not original_url:
        return
    database.reset_session_factory(original_url)


def _handle_build(args, firmware_dir: Path, archive_dir: Path) -> int:
    with database.SessionLocal() as session:
        try:
            result = node_builder.build_individual_node(
                session,
                args.node_id,
                metadata=None,
                board=None,
                regenerate_token=False,
                run_build=True,
                firmware_version=args.firmware_version,
                clean_build=not args.no_clean,
                sdkconfig_paths=PROJECT_SDKCONFIG_PATHS,
            )
        except node_builder.NodeBuilderError as exc:  # pragma: no cover - defensive
            print(f"error: {exc}", file=sys.stderr)
            return 2

        if result.returncode != 0:
            _print_command_failure(result)
            return result.returncode or 1

        artifact = node_builder.store_build_artifacts(
            node_id=result.node_id,
            download_id=result.download_id,
            firmware_version=args.firmware_version,
            firmware_dir=firmware_dir,
            archive_root=archive_dir,
        )
        print(f"Built {result.node_id} -> {artifact.manifest_path}")
        print(f"Binary SHA256: {artifact.sha256_hex}")
        if result.project_configs:
            print("Updated configuration files:")
            for cfg in result.project_configs:
                print(f"  - {cfg}")
        return 0


def _handle_flash(args, firmware_dir: Path, archive_dir: Path) -> int:
    with database.SessionLocal() as session:
        try:
            result = node_builder.first_time_flash(
                session,
                args.node_id,
                port=args.port,
                metadata=None,
                board=None,
                ota_token=None,
                firmware_version=args.firmware_version,
                clean_build=not args.no_clean,
                sdkconfig_paths=PROJECT_SDKCONFIG_PATHS,
            )
        except node_builder.NodeBuilderError as exc:  # pragma: no cover - defensive
            print(f"error: {exc}", file=sys.stderr)
            return 2

        if result.returncode != 0:
            _print_command_failure(result)
            return result.returncode or 1

        artifact = node_builder.store_build_artifacts(
            node_id=result.node_id,
            download_id=result.download_id,
            firmware_version=args.firmware_version,
            firmware_dir=firmware_dir,
            archive_root=archive_dir,
        )
        print(f"Flashed {result.node_id} ({args.port}) -> {artifact.manifest_path}")
        print(f"Binary SHA256: {artifact.sha256_hex}")
        if result.project_configs:
            print("Updated configuration files:")
            for cfg in result.project_configs:
                print(f"  - {cfg}")
        return 0


def _handle_update_all(args, firmware_dir: Path, archive_dir: Path) -> int:
    with database.SessionLocal() as session:
        registrations = _load_registrations(session)
        if not registrations:
            print("No node registrations found")
            return 0

        exit_code = 0
        for registration in registrations:
            metadata = dict(registration.hardware_metadata or {})
            board = metadata.get("board") if isinstance(metadata.get("board"), str) else None
            try:
                result = node_builder.build_individual_node(
                    session,
                    registration.node_id,
                    metadata=metadata,
                    board=board,
                    regenerate_token=False,
                    run_build=True,
                    firmware_version=args.firmware_version,
                    clean_build=not args.no_clean,
                    sdkconfig_paths=PROJECT_SDKCONFIG_PATHS,
                )
            except node_builder.NodeBuilderError as exc:  # pragma: no cover - defensive
                print(f"error: {exc}", file=sys.stderr)
                exit_code = 2
                break

            if result.returncode != 0:
                _print_command_failure(result)
                exit_code = result.returncode or 1
                break

            artifact = node_builder.store_build_artifacts(
                node_id=result.node_id,
                download_id=result.download_id,
                firmware_version=args.firmware_version,
                firmware_dir=firmware_dir,
                archive_root=archive_dir,
            )
            print(f"Built {result.node_id} -> {artifact.manifest_path}")
        return exit_code


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _common_args(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_build_parser(subparsers)
    _add_flash_parser(subparsers)
    _add_update_all_parser(subparsers)

    args = parser.parse_args(list(argv) if argv is not None else None)

    original_db = _maybe_reset_database(args.database_url)
    original_firmware_dir = Path(settings.FIRMWARE_DIR)
    firmware_dir, archive_dir = _resolve_paths(args.firmware_dir, args.archive_dir)
    settings.FIRMWARE_DIR = firmware_dir

    try:
        if args.command == "build":
            return _handle_build(args, firmware_dir, archive_dir)
        if args.command == "flash":
            return _handle_flash(args, firmware_dir, archive_dir)
        if args.command == "update-all":
            return _handle_update_all(args, firmware_dir, archive_dir)
        parser.error("Unknown command")
        return 2
    finally:
        settings.FIRMWARE_DIR = original_firmware_dir
        _restore_database(original_db)


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
