from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import database, node_credentials
from app.auth.service import create_user, init_auth_storage
from app.config import settings
from app import node_builder


class _NoopBus:
    def __getattr__(self, name: str):  # pragma: no cover - simple stub
        def _noop(*args, **kwargs):
            return None

        return _noop


@pytest.fixture()
def admin_client(tmp_path, monkeypatch: pytest.MonkeyPatch):
    import app.mqtt_bus
    import app.registry as registry_module
    import app.motion
    import app.status_monitor
    from app.main import app as fastapi_app

    monkeypatch.setattr(app.mqtt_bus, "MqttBus", lambda *args, **kwargs: _NoopBus())

    registry_data: List[Dict] = [
        {
            "id": "alpha",
            "name": "Alpha House",
            "external_id": "alpha-public",
            "rooms": [
                {"id": "living", "name": "Living Room", "nodes": []},
            ],
        }
    ]

    registry_file = tmp_path / "registry.json"
    registry_file.write_text(json.dumps(registry_data))
    monkeypatch.setattr(settings, "REGISTRY_FILE", registry_file)
    monkeypatch.setattr(settings, "DEVICE_REGISTRY", registry_data[:])
    monkeypatch.setattr(registry_module.settings, "REGISTRY_FILE", registry_file)
    monkeypatch.setattr(registry_module.settings, "DEVICE_REGISTRY", registry_data[:])
    registry_module.ensure_house_external_ids(persist=False)

    monkeypatch.setattr(app.motion.motion_manager, "start", lambda: None)
    monkeypatch.setattr(app.motion.motion_manager, "stop", lambda: None)
    monkeypatch.setattr(app.status_monitor.status_monitor, "start", lambda: None)
    monkeypatch.setattr(app.status_monitor.status_monitor, "stop", lambda: None)

    db_path = tmp_path / "auth.sqlite3"
    db_url = f"sqlite:///{db_path}"
    original_db_url = settings.AUTH_DB_URL
    database.reset_session_factory(db_url)
    monkeypatch.setattr(settings, "AUTH_DB_URL", db_url)
    monkeypatch.setattr(node_builder.settings, "AUTH_DB_URL", db_url)
    init_auth_storage()

    with database.SessionLocal() as session:
        create_user(session, "admin", "pass", server_admin=True)

    with TestClient(fastapi_app, base_url="https://testserver") as client:
        login = client.post(
            "/login",
            data={"username": "admin", "password": "pass"},
            follow_redirects=False,
        )
        assert login.status_code == 303
        yield client

    database.reset_session_factory(original_db_url)


def test_create_node_registrations(admin_client: TestClient):
    payload = {
        "count": 2,
        "displayName": "Batch Node",
        "hardware": {
            "board": "esp32c3",
            "ws2812": [
                {"index": 0, "enabled": True, "gpio": 6, "pixels": 60},
            ],
            "white": [],
            "rgb": [],
            "overrides": {"CONFIG_UL_WIFI_RESET_BUTTON_GPIO": 0},
        },
    }

    response = admin_client.post(
        "/api/server-admin/node-factory/registrations",
        json=payload,
    )

    assert response.status_code == 201
    data = response.json()
    assert len(data["nodes"]) == 2

    with database.SessionLocal() as session:
        regs = node_credentials.list_available_registrations(session)
        assert len(regs) == 2
        assert all(reg.hardware_metadata.get("board") == "esp32c3" for reg in regs)
        assert all(reg.display_name.startswith("Batch Node") for reg in regs)


def test_build_node_uses_metadata(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch):
    with database.SessionLocal() as session:
        entry = node_credentials.create_batch(
            session,
            1,
            metadata=[{"board": "esp32", "ws2812": []}],
        )[0]
        node_id = entry.registration.node_id
        session.expunge_all()

    captured = {}

    def _fake_build(session, node_id_arg, **kwargs):
        captured["node_id"] = node_id_arg
        captured["metadata"] = kwargs.get("metadata")
        return node_builder.BuildResult(
            command=["idf.py", "build"],
            returncode=0,
            stdout="ok",
            stderr="",
            cwd=node_builder.FIRMWARE_ROOT,
            node_id=node_id_arg,
            sdkconfig_path=node_builder.FIRMWARE_ROOT / "sdkconfig.test",
            manifest_url="https://example.com/manifest",
            download_id="download123",
            target="esp32",
        )

    monkeypatch.setattr(node_builder, "build_individual_node", _fake_build)

    response = admin_client.post(
        "/api/server-admin/node-factory/build",
        json={"nodeId": node_id, "skipBuild": True},
    )

    assert response.status_code == 200
    assert captured["node_id"] == node_id
    assert captured["metadata"]["board"] == "esp32"
    payload = response.json()
    assert payload["downloadId"] == "download123"
    assert payload["returncode"] == 0


def test_flash_node_invokes_builder(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch):
    with database.SessionLocal() as session:
        entry = node_credentials.create_batch(session, 1, metadata=[{"board": "esp32"}])[0]
        node_id = entry.registration.node_id
        session.expunge_all()

    def _fake_flash(session, node_id_arg, **kwargs):
        return node_builder.BuildResult(
            command=["idf.py", "-p", kwargs["port"], "build", "flash"],
            returncode=0,
            stdout="flashed",
            stderr="",
            cwd=node_builder.FIRMWARE_ROOT,
            node_id=node_id_arg,
            sdkconfig_path=node_builder.FIRMWARE_ROOT / "sdkconfig.flash",
            manifest_url="https://example.com/manifest",
            download_id="download456",
            target="esp32",
        )

    monkeypatch.setattr(node_builder, "first_time_flash", _fake_flash)

    response = admin_client.post(
        "/api/server-admin/node-factory/flash",
        json={"nodeId": node_id, "port": "/dev/ttyUSB0"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["stdout"].startswith("flashed")
    assert payload["downloadId"] == "download456"


def test_update_all_nodes(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch):
    def _fake_update(version: str):
        return node_builder.CommandResult(
            command=["updateAllNodes.sh", version],
            returncode=0,
            stdout="ok",
            stderr="",
            cwd=node_builder.FIRMWARE_ROOT,
        )

    monkeypatch.setattr(node_builder, "update_all_nodes", _fake_update)

    response = admin_client.post(
        "/api/server-admin/node-factory/update-all",
        json={"firmwareVersion": "2024.07"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["returncode"] == 0
    assert payload["message"].startswith("Bulk firmware update")
