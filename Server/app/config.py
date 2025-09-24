import os, json
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()  # reads .env in the project root

class Settings:
    DATA_DIR = Path(os.getenv("DATA_DIR", "./data")).expanduser().resolve()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
    WEB_PORT = int(os.getenv("WEB_PORT", "443"))
    PUBLIC_BASE = os.getenv("PUBLIC_BASE", "https://lights.evm100.org")

    SSL_CERTFILE = os.getenv("SSL_CERTFILE", "")
    SSL_KEYFILE  = os.getenv("SSL_KEYFILE", "")

    BROKER_HOST = os.getenv("BROKER_HOST", "127.0.0.1")
    BROKER_PORT = int(os.getenv("BROKER_PORT", "1883"))
    EMBED_BROKER = os.getenv("EMBED_BROKER", "0") == "1"

    FIRMWARE_DIR = Path(os.getenv("FIRMWARE_DIR", "./firmware"))
    FIRMWARE_DIR.mkdir(parents=True, exist_ok=True)

    API_BEARER = os.getenv("API_BEARER", "")
    MANIFEST_HMAC_SECRET = os.getenv("MANIFEST_HMAC_SECRET", "")

    MAX_HOUSE_ID_LENGTH = int(os.getenv("MAX_HOUSE_ID_LENGTH", "22"))
    AUTH_DB_URL = os.getenv(
        "AUTH_DB_URL", f"sqlite:///{DATA_DIR / 'auth.sqlite3'}"
    )
    SESSION_SECRET = os.getenv("SESSION_SECRET", "dev-session-secret")
    INITIAL_ADMIN_USERNAME = os.getenv("INITIAL_ADMIN_USERNAME", "")
    INITIAL_ADMIN_PASSWORD = os.getenv("INITIAL_ADMIN_PASSWORD", "")
    LOGIN_ATTEMPT_LIMIT = int(os.getenv("LOGIN_ATTEMPT_LIMIT", "5"))
    LOGIN_ATTEMPT_WINDOW = int(os.getenv("LOGIN_ATTEMPT_WINDOW", "300"))
    LOGIN_BACKOFF_SECONDS = int(os.getenv("LOGIN_BACKOFF_SECONDS", "900"))

    # ------------------------------------------------------------------
    # Device registry ---------------------------------------------------

    # registry of houses/rooms/nodes as JSON
    DEFAULT_REGISTRY = [
        {
            "id": "del-sur",
            "name": "Del Sur",
            "rooms": [
                {
                    "id": "kitchen",
                    "name": "Cocina",
                    "nodes": [
                        {
                            "id": "kitchen",
                            "name": "Cocina",
                            "kind": "ultranode",
                            "modules": ["white", "ota"],
                        }
                    ],
                },
                {
                    "id": "master",
                    "name": "Master",
                    "nodes": [
                        {
                            "id": "master-closet",
                            "name": "Master Closet",
                            "kind": "ultranode",
                            "modules": ["white", "ota"],
                        }
                    ],
                },
            ],
        }
    ]
    REGISTRY_FILE = Path(
        os.getenv("REGISTRY_FILE", str(Path(__file__).with_name("device_registry.json")))
    )
    MOTION_SCHEDULE_FILE = Path(
        os.getenv(
            "MOTION_SCHEDULE_FILE",
            str(Path(__file__).with_name("motion_schedule.json")),
        )
    )
    CUSTOM_PRESET_FILE = Path(
        os.getenv(
            "CUSTOM_PRESET_FILE",
            str(Path(__file__).with_name("custom_presets.json")),
        )
    )
    MOTION_PREFS_FILE = Path(
        os.getenv(
            "MOTION_PREFS_FILE",
            str(Path(__file__).with_name("motion_prefs.json")),
        )
    )
    BRIGHTNESS_LIMITS_FILE = Path(
        os.getenv(
            "BRIGHTNESS_LIMITS_FILE",
            str(Path(__file__).with_name("brightness_limits.json")),
        )
    )
    CHANNEL_NAMES_FILE = Path(
        os.getenv(
            "CHANNEL_NAMES_FILE",
            str(Path(__file__).with_name("channel_names.json")),
        )
    )
    if REGISTRY_FILE.exists():
        DEVICE_REGISTRY = json.loads(REGISTRY_FILE.read_text())
    else:
        DEVICE_REGISTRY = DEFAULT_REGISTRY
        REGISTRY_FILE.write_text(json.dumps(DEVICE_REGISTRY, indent=2))

    def resolve_data_path(self, path: str | os.PathLike[str]) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.DATA_DIR / candidate
        return candidate.expanduser().resolve()

settings = Settings()
