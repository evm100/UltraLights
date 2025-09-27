import sys
from pathlib import Path

import pytest

SERVER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVER_ROOT.parent
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app import database, node_builder, node_credentials  # noqa: E402
from app.auth.service import init_auth_storage  # noqa: E402
from app.config import settings  # noqa: E402
from tools.firmware_cli import cli as firmware_cli  # noqa: E402


@pytest.fixture()
def cli_environment(tmp_path, monkeypatch: pytest.MonkeyPatch):
    firmware_dir = tmp_path / "firmware"
    archive_dir = tmp_path / "archive"
    db_path = tmp_path / "auth.sqlite3"
    db_url = f"sqlite:///{db_path}"
    original_db = settings.AUTH_DB_URL
    database.reset_session_factory(db_url)
    monkeypatch.setattr(settings, "AUTH_DB_URL", db_url)
    init_auth_storage()
    try:
        yield firmware_dir, archive_dir, db_url
    finally:
        database.reset_session_factory(original_db)
        monkeypatch.setattr(settings, "AUTH_DB_URL", original_db)


def _make_build_result(node_id: str, download_id: str) -> node_builder.BuildResult:
    return node_builder.BuildResult(
        command=["idf.py", "build"],
        returncode=0,
        stdout="",
        stderr="",
        cwd=node_builder.FIRMWARE_ROOT,
        node_id=node_id,
        sdkconfig_path=node_builder.FIRMWARE_ROOT / "sdkconfig.test",
        manifest_url=f"https://example.com/{download_id}/manifest.json",
        download_id=download_id,
        target="esp32",
        metadata={},
        ota_token="token",
        sdkconfig_values={},
    )


def _make_artifact(node_id: str, download_id: str) -> node_builder.ArtifactRecord:
    dummy = Path("/tmp/placeholder")
    return node_builder.ArtifactRecord(
        node_id=node_id,
        download_id=download_id,
        version="2024.09",
        latest_binary=dummy,
        archive_binary=dummy,
        manifest_path=dummy,
        versioned_manifest_path=dummy,
        size=1,
        sha256_hex="00",
    )


def test_cli_build_invokes_builder_and_archiver(cli_environment, monkeypatch: pytest.MonkeyPatch):
    firmware_dir, archive_dir, db_url = cli_environment

    with database.SessionLocal() as session:
        entry = node_credentials.create_batch(session, 1)[0]
        node_id = entry.registration.node_id
        download_id = entry.registration.download_id

    calls = {}

    def fake_build(session, node_id_arg, **kwargs):
        calls["build"] = {
            "node": node_id_arg,
            "kwargs": kwargs,
        }
        return _make_build_result(node_id_arg, download_id)

    def fake_store(**kwargs):
        calls.setdefault("store", []).append(kwargs)
        return _make_artifact(kwargs["node_id"], kwargs["download_id"])

    monkeypatch.setattr(node_builder, "build_individual_node", fake_build)
    monkeypatch.setattr(node_builder, "store_build_artifacts", fake_store)

    exit_code = firmware_cli.main(
        [
            "--database-url",
            db_url,
            "--firmware-dir",
            str(firmware_dir),
            "--archive-dir",
            str(archive_dir),
            "build",
            node_id,
            "--firmware-version",
            "2024.09",
        ]
    )

    assert exit_code == 0
    assert calls["build"]["node"] == node_id
    assert calls["build"]["kwargs"]["firmware_version"] == "2024.09"
    assert (
        calls["build"]["kwargs"]["sdkconfig_paths"]
        == firmware_cli.PROJECT_SDKCONFIG_PATHS
    )
    assert calls["store"][0]["node_id"] == node_id
    assert Path(calls["store"][0]["firmware_dir"]) == firmware_dir
    assert Path(calls["store"][0]["archive_root"]) == archive_dir


def test_cli_update_all_builds_every_registration(cli_environment, monkeypatch: pytest.MonkeyPatch):
    firmware_dir, archive_dir, db_url = cli_environment

    with database.SessionLocal() as session:
        entries = node_credentials.create_batch(
            session,
            2,
            metadata=[{"board": "esp32"}, {"board": "esp32c3"}],
        )
        node_downloads = {
            entry.registration.node_id: entry.registration.download_id
            for entry in entries
        }
        ids = list(node_downloads.keys())

    built_nodes = []
    stored_nodes = []

    def fake_build(session, node_id_arg, **kwargs):
        built_nodes.append(node_id_arg)
        download = node_downloads.get(node_id_arg, f"fallback-{len(built_nodes)}")
        assert kwargs.get("sdkconfig_paths") == firmware_cli.PROJECT_SDKCONFIG_PATHS
        return _make_build_result(node_id_arg, download)

    def fake_store(**kwargs):
        stored_nodes.append(kwargs["node_id"])
        return _make_artifact(kwargs["node_id"], kwargs["download_id"])

    monkeypatch.setattr(node_builder, "build_individual_node", fake_build)
    monkeypatch.setattr(node_builder, "store_build_artifacts", fake_store)

    exit_code = firmware_cli.main(
        [
            "--database-url",
            db_url,
            "--firmware-dir",
            str(firmware_dir),
            "--archive-dir",
            str(archive_dir),
            "update-all",
            "--firmware-version",
            "2024.09",
        ]
    )

    assert exit_code == 0
    assert set(ids).issubset(set(built_nodes))
    assert set(ids).issubset(set(stored_nodes))
