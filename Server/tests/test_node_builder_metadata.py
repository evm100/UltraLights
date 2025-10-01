import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import database, node_builder, node_credentials
from app.auth.service import init_auth_storage
from app.config import settings


@pytest.fixture()
def auth_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "auth.sqlite3"
    db_url = f"sqlite:///{db_path}"
    original_db = settings.AUTH_DB_URL
    database.reset_session_factory(db_url)
    monkeypatch.setattr(settings, "AUTH_DB_URL", db_url)
    init_auth_storage()
    try:
        yield
    finally:
        database.reset_session_factory(original_db)
        monkeypatch.setattr(settings, "AUTH_DB_URL", original_db)


def test_white_channel_defaults_are_preserved():
    metadata = {
        "board": "esp32",
        "white": [
            {"index": 0, "enabled": True, "gpio": 21},
        ],
    }

    overrides = node_builder.metadata_to_overrides(metadata)

    assert overrides["CONFIG_UL_WHT0_PWM_HZ"][0] == 3000
    assert overrides["CONFIG_UL_WHT0_MIN"][0] == 0
    assert overrides["CONFIG_UL_WHT0_MAX"][0] == 255
    assert overrides["CONFIG_UL_WHT0_LEDC_CH"][0] == 0
    assert "CONFIG_UL_WHT1_MIN" not in overrides


def test_ws2812_channel_values_are_copied():
    metadata = {
        "board": "esp32",
        "ws2812": [
            {"index": 0, "enabled": True, "gpio": 5, "pixels": 120},
        ],
    }

    overrides = node_builder.metadata_to_overrides(metadata)

    assert overrides["CONFIG_UL_WS0_ENABLED"][0] is True
    assert overrides["CONFIG_UL_WS0_GPIO"][0] == 5
    assert overrides["CONFIG_UL_WS0_PIXELS"][0] == 120


def test_ws2812_flip_rg_override_flag():
    metadata = {"board": "esp32", "ws2812_flip_rg": True}

    overrides = node_builder.metadata_to_overrides(metadata)

    assert overrides["CONFIG_UL_WS_FLIP_RG"][0] is True

    disabled_metadata = {"board": "esp32", "ws2812_flip_rg": False}
    disabled_overrides = node_builder.metadata_to_overrides(disabled_metadata)

    assert disabled_overrides["CONFIG_UL_WS_FLIP_RG"][0] is False


def test_project_sdkconfig_updates_include_metadata(tmp_path, monkeypatch: pytest.MonkeyPatch, auth_db):
    project_config = tmp_path / "sdkconfig"
    project_config.write_text("CONFIG_UL_NODE_ID=\"default\"\n", encoding="utf-8")

    captured: dict[str, tuple] = {}

    def fake_update(values, *, config_paths):
        captured.update(values)
        assert list(config_paths) == [project_config]
        return [Path(path) for path in config_paths]

    def fake_render(**kwargs):
        output = tmp_path / "generated_sdkconfig"
        output.write_text("CONFIG_FAKE=1\n", encoding="utf-8")
        return output

    monkeypatch.setattr(node_builder, "update_sdkconfig_files", fake_update)
    monkeypatch.setattr(node_builder, "render_sdkconfig", fake_render)
    monkeypatch.setattr(node_builder, "clean_build_dir", lambda: None)

    metadata = {
        "board": "esp32",
        "ws2812": [{"index": 0, "enabled": True, "gpio": 6, "pixels": 150}],
        "rgb": [
            {
                "index": 0,
                "enabled": True,
                "r_gpio": 18,
                "g_gpio": 19,
                "b_gpio": 21,
            }
        ],
        "white": [{"index": 0, "enabled": True, "gpio": 4}],
        "pir": {"enabled": True, "gpio": 33},
    }

    with database.SessionLocal() as session:
        entry = node_credentials.create_batch(session, 1, metadata=[metadata])[0]
        node_id = entry.registration.node_id
        node_builder.build_individual_node(
            session,
            node_id,
            run_build=False,
            clean_build=False,
            sdkconfig_paths=[project_config],
        )

    assert captured["CONFIG_UL_NODE_ID"][0] == node_id
    assert captured["CONFIG_UL_WS0_ENABLED"][0] is True
    assert captured["CONFIG_UL_WS0_GPIO"][0] == 6
    assert captured["CONFIG_UL_WS0_PIXELS"][0] == 150
    assert captured["CONFIG_UL_WS_FLIP_RG"][0] is False
    assert captured["CONFIG_UL_RGB0_ENABLED"][0] is True
    assert captured["CONFIG_UL_RGB0_R_GPIO"][0] == 18
    assert captured["CONFIG_UL_RGB0_G_GPIO"][0] == 19
    assert captured["CONFIG_UL_RGB0_B_GPIO"][0] == 21
    assert captured["CONFIG_UL_WHT0_ENABLED"][0] is True
    assert captured["CONFIG_UL_WHT0_GPIO"][0] == 4
    assert captured["CONFIG_UL_WHT0_PWM_HZ"][0] == 3000
    assert captured["CONFIG_UL_WHT0_MIN"][0] == 0
    assert captured["CONFIG_UL_WHT0_MAX"][0] == 255
    assert captured["CONFIG_UL_PIR_GPIO"][0] == 33
