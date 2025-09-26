from __future__ import annotations

from copy import deepcopy
import json
import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import database, node_credentials, ota, registry
from app.auth.service import init_auth_storage
from app.config import settings
from scripts import generate_node_ids, manage_node_credentials, provision_node_firmware


class _NoopBus:
    def __getattr__(self, name):  # pragma: no cover - defensive
        def _noop(*args, **kwargs):
            return None

        return _noop


@pytest.fixture()
def ota_environment(tmp_path, monkeypatch):
    original_registry = deepcopy(settings.DEVICE_REGISTRY)
    original_firmware = settings.FIRMWARE_DIR
    original_public_base = settings.PUBLIC_BASE
    original_api_bearer = settings.API_BEARER
    original_db_url = settings.AUTH_DB_URL

    test_registry = [
        {
            "id": "test-house",
            "name": "Test House",
            "rooms": [
                {
                    "id": "lab",
                    "name": "Lab",
                    "nodes": [
                        {
                            "id": "test-node",
                            "name": "Test Node",
                            "kind": "ultranode",
                            "modules": ["ota"],
                        }
                    ],
                }
            ],
        }
    ]

    monkeypatch.setattr(settings, "DEVICE_REGISTRY", deepcopy(test_registry))
    monkeypatch.setattr(registry.settings, "DEVICE_REGISTRY", settings.DEVICE_REGISTRY)
    monkeypatch.setattr(registry, "save_registry", lambda: None)

    firmware_dir = tmp_path / "firmware"
    firmware_dir.mkdir()
    monkeypatch.setattr(settings, "FIRMWARE_DIR", firmware_dir)
    monkeypatch.setattr(registry.settings, "FIRMWARE_DIR", firmware_dir)
    monkeypatch.setattr(ota, "FIRMWARE_DIR", firmware_dir)

    monkeypatch.setattr(settings, "PUBLIC_BASE", "https://example.test")
    monkeypatch.setattr(ota.settings, "PUBLIC_BASE", "https://example.test")
    monkeypatch.setattr(settings, "API_BEARER", "shared-secret")
    monkeypatch.setattr(ota.settings, "API_BEARER", "shared-secret")

    db_path = tmp_path / "auth.sqlite3"
    db_url = f"sqlite:///{db_path}"
    database.reset_session_factory(db_url)
    monkeypatch.setattr(ota.settings, "AUTH_DB_URL", db_url)
    init_auth_storage()

    yield {
        "registry": settings.DEVICE_REGISTRY,
        "firmware_dir": firmware_dir,
    }

    monkeypatch.setattr(settings, "DEVICE_REGISTRY", original_registry)
    monkeypatch.setattr(registry.settings, "DEVICE_REGISTRY", original_registry)
    monkeypatch.setattr(settings, "FIRMWARE_DIR", original_firmware)
    monkeypatch.setattr(registry.settings, "FIRMWARE_DIR", original_firmware)
    monkeypatch.setattr(ota, "FIRMWARE_DIR", original_firmware)
    monkeypatch.setattr(settings, "PUBLIC_BASE", original_public_base)
    monkeypatch.setattr(ota.settings, "PUBLIC_BASE", original_public_base)
    monkeypatch.setattr(settings, "API_BEARER", original_api_bearer)
    monkeypatch.setattr(ota.settings, "API_BEARER", original_api_bearer)
    database.reset_session_factory(original_db_url)
    monkeypatch.setattr(settings, "AUTH_DB_URL", original_db_url)
    monkeypatch.setattr(ota.settings, "AUTH_DB_URL", original_db_url)


@pytest.fixture()
def node_credential_info(ota_environment):
    download_id = "DLTESTID1234"
    with database.SessionLocal() as session:
        node_credentials.ensure_for_node(
            session,
            node_id="test-node",
            house_slug="test-house",
            room_id="lab",
            display_name="Test Node",
            download_id=download_id,
        )
        credential, token = node_credentials.rotate_token(session, "test-node")
        download_id = credential.download_id

    node_dir = settings.FIRMWARE_DIR / "test-node"
    node_dir.mkdir(parents=True, exist_ok=True)
    (node_dir / "latest.bin").write_bytes(b"OTA")

    return {
        "token": token,
        "download_id": download_id,
        "device_id": "test-node",
    }


@pytest.fixture()
def client(ota_environment, monkeypatch):
    import app.mqtt_bus

    monkeypatch.setattr(app.mqtt_bus, "MqttBus", lambda *args, **kwargs: _NoopBus())

    sys.modules.pop("app.motion", None)
    sys.modules.pop("app.status_monitor", None)
    import app.motion
    import app.status_monitor

    monkeypatch.setattr(app.motion.MotionManager, "start", lambda self: None)
    monkeypatch.setattr(app.motion.MotionManager, "stop", lambda self: None)
    monkeypatch.setattr(app.status_monitor.StatusMonitor, "start", lambda self: None)
    monkeypatch.setattr(app.status_monitor.StatusMonitor, "stop", lambda self: None)

    sys.modules.pop("app.main", None)
    from app.main import app as fastapi_app

    test_client = TestClient(fastapi_app)
    try:
        init_auth_storage()
        SQLModel.metadata.create_all(database.engine)
        with database.SessionLocal() as session:
            node_credentials.sync_registry_nodes(session)
        yield test_client
    finally:
        test_client.close()


def test_manifest_uses_download_id_with_node_token(node_credential_info, client):
    response = client.get(
        f"/firmware/{node_credential_info['download_id']}/manifest",
        headers={"Authorization": f"Bearer {node_credential_info['token']}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["device_id"] == node_credential_info["device_id"]
    assert data["download_id"] == node_credential_info["download_id"]
    assert data["binary_url"].endswith(
        f"/firmware/{node_credential_info['download_id']}/latest.bin"
    )
    assert data["manifest_url"].endswith(
        f"/firmware/{node_credential_info['download_id']}/manifest"
    )


def test_binary_download_requires_matching_token(node_credential_info, client):
    url = f"/firmware/{node_credential_info['download_id']}/latest.bin"
    response = client.get(url, headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 403

    ok = client.get(url, headers={"Authorization": f"Bearer {node_credential_info['token']}"})
    assert ok.status_code == 200
    assert ok.content == b"OTA"


def test_manifest_rejects_cross_node_access(node_credential_info, client):
    other = registry.generate_download_id()
    response = client.get(
        f"/firmware/{other}/manifest",
        headers={"Authorization": f"Bearer {node_credential_info['token']}"},
    )
    assert response.status_code == 403


def test_manifest_allows_global_token(node_credential_info, client):
    response = client.get(
        "/api/firmware/v1/manifest",
        params={"device_id": node_credential_info["device_id"]},
        headers={"Authorization": "Bearer shared-secret"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["binary_url"].endswith(
        f"/firmware/{node_credential_info['download_id']}/latest.bin"
    )


def test_manifest_missing_token_is_rejected(node_credential_info, client):
    response = client.get(
        f"/firmware/{node_credential_info['download_id']}/manifest",
    )
    assert response.status_code == 401


def test_manage_node_credentials_cli_creates_token(tmp_path, monkeypatch):
    original_registry = deepcopy(settings.DEVICE_REGISTRY)
    original_firmware = settings.FIRMWARE_DIR
    original_db_url = settings.AUTH_DB_URL

    test_registry = [
        {
            "id": "cli-house",
            "name": "CLI House",
            "rooms": [
                {
                    "id": "room",
                    "name": "Room",
                    "nodes": [
                        {
                            "id": "cli-node",
                            "name": "CLI Node",
                            "kind": "ultranode",
                            "modules": ["ota"],
                        }
                    ],
                }
            ],
        }
    ]

    monkeypatch.setattr(settings, "DEVICE_REGISTRY", deepcopy(test_registry))
    monkeypatch.setattr(registry.settings, "DEVICE_REGISTRY", settings.DEVICE_REGISTRY)
    monkeypatch.setattr(registry, "save_registry", lambda: None)

    firmware_dir = tmp_path / "fw"
    firmware_dir.mkdir()
    monkeypatch.setattr(settings, "FIRMWARE_DIR", firmware_dir)
    monkeypatch.setattr(registry.settings, "FIRMWARE_DIR", firmware_dir)
    monkeypatch.setattr(settings, "PUBLIC_BASE", "https://example.test")
    monkeypatch.setattr(registry.settings, "PUBLIC_BASE", "https://example.test")
    monkeypatch.setattr(ota.settings, "PUBLIC_BASE", "https://example.test")

    db_path = tmp_path / "auth.sqlite3"
    db_url = f"sqlite:///{db_path}"
    database.reset_session_factory(db_url)
    monkeypatch.setattr(ota.settings, "AUTH_DB_URL", db_url)
    init_auth_storage()

    result = manage_node_credentials.main(
        [
            "cli-node",
            "--token",
            "plain-token",
            "--download-id",
            "CLISLOT123",
        ]
    )
    assert result == 0

    node = registry.find_node("cli-node")[2]
    assert node is not None
    assert node[registry.NODE_DOWNLOAD_ID_KEY] == "CLISLOT123"
    assert registry.NODE_TOKEN_HASH_KEY not in node

    with database.SessionLocal() as session:
        record = node_credentials.get_by_node_id(session, "cli-node")
        assert record is not None
        assert record.download_id == "CLISLOT123"
        assert record.token_hash == registry.hash_node_token("plain-token")

    download_dir = firmware_dir / "CLISLOT123"
    assert download_dir.exists()
    assert download_dir.is_dir()
    assert not download_dir.is_symlink()

    monkeypatch.setattr(settings, "DEVICE_REGISTRY", original_registry)
    monkeypatch.setattr(registry.settings, "DEVICE_REGISTRY", original_registry)
    monkeypatch.setattr(settings, "FIRMWARE_DIR", original_firmware)
    monkeypatch.setattr(registry.settings, "FIRMWARE_DIR", original_firmware)
    database.reset_session_factory(original_db_url)
    monkeypatch.setattr(settings, "AUTH_DB_URL", original_db_url)
    monkeypatch.setattr(ota.settings, "AUTH_DB_URL", original_db_url)


def test_provision_node_firmware_updates_sdkconfig(tmp_path, monkeypatch, capsys):
    original_registry = deepcopy(settings.DEVICE_REGISTRY)
    original_firmware = settings.FIRMWARE_DIR
    original_db_url = settings.AUTH_DB_URL

    test_registry = [
        {
            "id": "provision-house",
            "name": "Provision House",
            "rooms": [
                {
                    "id": "room",
                    "name": "Room",
                    "nodes": [
                        {
                            "id": "provision-node",
                            "name": "Provision Node",
                            "kind": "ultranode",
                            "modules": ["ota"],
                        }
                    ],
                }
            ],
        }
    ]

    monkeypatch.setattr(settings, "DEVICE_REGISTRY", deepcopy(test_registry))
    monkeypatch.setattr(registry.settings, "DEVICE_REGISTRY", settings.DEVICE_REGISTRY)
    monkeypatch.setattr(registry, "save_registry", lambda: None)

    firmware_dir = tmp_path / "fw"
    firmware_dir.mkdir()
    monkeypatch.setattr(settings, "FIRMWARE_DIR", firmware_dir)
    monkeypatch.setattr(registry.settings, "FIRMWARE_DIR", firmware_dir)

    sdkconfig = tmp_path / "sdkconfig"
    sdkconfig.write_text(
        "\n".join(
            [
                'CONFIG_UL_NODE_ID="placeholder"',
                'CONFIG_UL_OTA_MANIFEST_URL="https://example.test/firmware/placeholder/manifest"',
                'CONFIG_UL_OTA_BEARER_TOKEN="old"',
                "",
            ]
        )
    )

    db_path = tmp_path / "auth.sqlite3"
    db_url = f"sqlite:///{db_path}"
    database.reset_session_factory(db_url)
    monkeypatch.setattr(ota.settings, "AUTH_DB_URL", db_url)
    init_auth_storage()

    with database.SessionLocal() as session:
        node_credentials.ensure_for_node(
            session,
            node_id="provision-node",
            house_slug="provision-house",
            room_id="room",
            display_name="Provision Node",
            download_id="DLINITIAL",
            token_hash=registry.hash_node_token("seed-token"),
        )
        node_credentials.sync_registry_nodes(session)

    result = provision_node_firmware.main(
        [
            "provision-node",
            "--config",
            str(sdkconfig),
            "--rotate-download",
        ]
    )
    assert result == 0
    output = capsys.readouterr().out

    config_text = sdkconfig.read_text()
    assert 'CONFIG_UL_NODE_ID="provision-node"' in config_text
    manifest_match = re.search(
        r'CONFIG_UL_OTA_MANIFEST_URL="([^"]+)"', config_text
    )
    token_match = re.search(
        r'CONFIG_UL_OTA_BEARER_TOKEN="([^"]+)"', config_text
    )
    assert manifest_match and token_match
    manifest_url = manifest_match.group(1)
    token_value = token_match.group(1)

    with database.SessionLocal() as session:
        record = node_credentials.get_by_node_id(session, "provision-node")
        assert record is not None
        expected_suffix = f"/firmware/{record.download_id}/manifest.json"
        assert manifest_url.endswith(expected_suffix)
        assert record.token_hash == registry.hash_node_token(token_value)
        assert record.provisioned_at is not None

    download_dir = firmware_dir / record.download_id
    assert download_dir.exists()
    assert download_dir.is_dir()
    assert not download_dir.is_symlink()

    assert "Bearer Token" in output
    assert token_value in output

    monkeypatch.setattr(settings, "DEVICE_REGISTRY", original_registry)
    monkeypatch.setattr(registry.settings, "DEVICE_REGISTRY", original_registry)
    monkeypatch.setattr(settings, "FIRMWARE_DIR", original_firmware)
    monkeypatch.setattr(registry.settings, "FIRMWARE_DIR", original_firmware)
    database.reset_session_factory(original_db_url)
    monkeypatch.setattr(settings, "AUTH_DB_URL", original_db_url)
    monkeypatch.setattr(ota.settings, "AUTH_DB_URL", original_db_url)


def test_pre_registered_node_provisioning(tmp_path, ota_environment, capsys):
    metadata = {"gpio": {"relay": 5}, "enabled": True}
    records = generate_node_ids.generate_nodes(
        count=1, metadata_entries=[metadata]
    )
    record = records[0]
    node_id = record["node_id"]

    sdkconfig = tmp_path / "sdkconfig"
    sdkconfig.write_text("\n")

    exit_code = provision_node_firmware.main(
        [node_id, "--config", str(sdkconfig)]
    )
    assert exit_code == 0
    output = capsys.readouterr().out

    config_text = sdkconfig.read_text()
    assert f'CONFIG_UL_NODE_ID="{node_id}"' in config_text
    assert f'CONFIG_UL_OTA_BEARER_TOKEN="{record["ota_token"]}"' in config_text
    metadata_json = json.dumps(metadata, separators=(",", ":"), sort_keys=True)
    assert f'CONFIG_UL_NODE_METADATA="{metadata_json}"' in config_text

    download_dir = settings.FIRMWARE_DIR / record["download_id"]
    assert download_dir.exists()
    assert download_dir.is_dir()

    with database.SessionLocal() as session:
        registration = node_credentials.get_registration_by_node_id(session, node_id)
        assert registration is not None
        assert registration.provisioning_token == record["ota_token"]
        assert registration.provisioned_at is not None

    assert "Hardware metadata" in output
