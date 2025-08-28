import os, json
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()  # reads .env in the project root

class Settings:
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

    MQTT_COMPAT_PUBLISH_BOTH = os.getenv("MQTT_COMPAT_PUBLISH_BOTH", "1") == "1"
    # legacy/global topics
    LEGACY_TOPIC_COLOR  = os.getenv("LEGACY_TOPIC_COLOR", "strip/cmd/color")
    LEGACY_TOPIC_EFFECT = os.getenv("LEGACY_TOPIC_EFFECT", "strip/cmd/effect")
    LEGACY_TOPIC_SPACEY = os.getenv("LEGACY_TOPIC_SPACEY", "strip/cmd/spacey")
    LEGACY_TOPIC_OTA    = os.getenv("LEGACY_TOPIC_OTA",    "strip/cmd/ota")

    # registry of houses/rooms/nodes as JSON
    DEFAULT_REGISTRY = [
        {
            "id": "del-sur",
            "name": "Del Sur",
            "rooms": [
                {
                    "id": "kitchen",
                    "name": "Kitchen",
                    "nodes": [
                        {
                            "id": "del-sur-kitchen-node1",
                            "name": "Kitchen Node",
                            "kind": "rgb",
                            "modules": ["color", "effect", "brightness", "motion", "ota"],
                        }
                    ],
                },
                {
                    "id": "room-1",
                    "name": "Room 1",
                    "nodes": [
                        {
                            "id": "del-sur-room-1-node1",
                            "name": "Room 1 Node",
                            "kind": "rgb",
                            "modules": ["color", "effect", "brightness", "motion", "ota"],
                        }
                    ],
                },
            ],
        },
        {
            "id": "sdsu",
            "name": "SDSU",
            "rooms": [
                {
                    "id": "kitchen",
                    "name": "Kitchen",
                    "nodes": [
                        {
                            "id": "sdsu-kitchen-node1",
                            "name": "Kitchen Node",
                            "kind": "rgb",
                            "modules": ["color", "effect", "brightness", "motion", "ota"],
                        }
                    ],
                },
                {
                    "id": "room-1",
                    "name": "Room 1",
                    "nodes": [
                        {
                            "id": "sdsu-room-1-node1",
                            "name": "Room 1 Node",
                            "kind": "rgb",
                            "modules": ["color", "effect", "brightness", "motion", "ota"],
                        }
                    ],
                },
            ],
        },
    ]
    REGISTRY_FILE = Path(os.getenv("REGISTRY_FILE", str(Path(__file__).with_name("device_registry.json"))))
    if REGISTRY_FILE.exists():
        DEVICE_REGISTRY = json.loads(REGISTRY_FILE.read_text())
    else:
        DEVICE_REGISTRY = DEFAULT_REGISTRY
        REGISTRY_FILE.write_text(json.dumps(DEVICE_REGISTRY, indent=2))

settings = Settings()
