"""Settings, read from UL_* environment variables (and an optional env file).

Production: /etc/ultralights/ultralights.env, written by deploy/install.sh.
Dev: server/.dev/dev.env, written by server/tools/dev.sh.
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="UL_", env_file=None, extra="ignore")

    # Database (SQLAlchemy URL, psycopg 3 driver)
    database_url: str = "postgresql+psycopg://ultralights@/ultralights"

    # Public host name nodes use for HTTPS enrollment and MQTT. Never an IP.
    public_host: str = "lights.evm100.org"
    mqtt_port: int = 8883

    # How the bridge reaches the broker (same host in production).
    mqtt_host: str = "lights.evm100.org"
    # Production connects to 127.0.0.1 and turns the host-name check off (the chain is still
    # verified); everything else should leave it on.
    mqtt_check_hostname: bool = True
    mqtt_ca_file: str | None = None  # CA that signed the broker's cert; None = system trust store

    # PKI: device CA intermediate (+ root while it hasn't been moved offline), bridge client cert, CRL.
    pki_dir: Path = Path("/etc/ultralights/pki")

    # Optional PEM put into provision.server_ca (dev servers without a public certificate).
    provision_server_ca_file: str | None = None

    # Firmware packages and other data.
    data_dir: Path = Path("/var/lib/ultralights")

    contracts_dir: Path = REPO_ROOT / "contracts"

    # Cookies are Secure unless explicitly in dev over plain http (vite dev server on localhost).
    cookie_secure: bool = True
    session_days: int = 30

    enroll_rate_per_min: int = 20
    token_days: int = 7
    device_cert_years: int = 10

    bridge_enabled: bool = True

    @property
    def firmware_dir(self) -> Path:
        return self.data_dir / "firmware"


@lru_cache
def get_settings() -> Settings:
    import os

    env_file = os.environ.get("UL_ENV_FILE")
    if env_file:
        return Settings(_env_file=env_file)
    return Settings()
