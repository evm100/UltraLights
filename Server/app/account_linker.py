"""Bridge MQTT account credential events into the database."""
from __future__ import annotations

import json
import logging
import threading
from typing import Any, Optional

import paho.mqtt.client as mqtt

from . import database, node_credentials
from .mqtt_tls import connect_mqtt_client


_LOGGER = logging.getLogger(__name__)


class AccountLinker:
    """Listen for account credential events and persist associations."""

    def __init__(self) -> None:
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        enable_logger = getattr(self.client, "enable_logger", None)
        if callable(enable_logger):
            enable_logger()
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self._loop_thread: Optional[threading.Thread] = None
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        connect_mqtt_client(
            self.client,
            keepalive=30,
            start_async=True,
            raise_on_failure=False,
        )
        loop_start = getattr(self.client, "loop_start", None)
        if callable(loop_start):
            loop_start()
        else:
            self._loop_thread = threading.Thread(
                target=self.client.loop_forever,
                daemon=True,
            )
            self._loop_thread.start()
        self._running = True

    def stop(self) -> None:
        if not self._running:
            return
        loop_stop = getattr(self.client, "loop_stop", None)
        if callable(loop_stop):
            loop_stop()
        if self._loop_thread is not None:
            self._loop_thread.join(timeout=5.0)
            self._loop_thread = None
        self.client.disconnect()
        self._running = False

    # MQTT callbacks -------------------------------------------------
    def _on_connect(
        self, client: mqtt.Client, userdata, flags, reason_code, properties=None
    ) -> None:
        client.subscribe("ul/+/evt/account")

    def _on_message(self, client: mqtt.Client, userdata, msg: mqtt.MQTTMessage) -> None:  # type: ignore[override]
        topic = msg.topic or ""
        parts = topic.split("/")
        if len(parts) < 4 or parts[0] != "ul" or parts[2] != "evt":
            return
        node_id = parts[1]
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception:
            _LOGGER.warning("Failed to decode account payload from node '%s'", node_id)
            return
        if not isinstance(payload, dict):
            return
        event = payload.get("event")
        if event and event != "account_credentials":
            return
        username = payload.get("username")
        password = payload.get("password")
        if not isinstance(username, str) or not isinstance(password, str):
            _LOGGER.warning(
                "Malformed account payload for node '%s': missing username/password",
                node_id,
            )
            return
        self.handle_credentials(node_id, username, password)

    # Persistence helpers -------------------------------------------
    def handle_credentials(
        self, node_id: str, username: str, password: str
    ) -> Optional[Any]:
        """Record credentials for ``node_id``. Separated for tests."""

        try:
            with database.SessionLocal() as session:
                return node_credentials.record_account_credentials(
                    session, node_id, username, password
                )
        except ValueError:
            _LOGGER.warning(
                "Ignoring empty account credentials for node '%s'", node_id
            )
        except KeyError:
            _LOGGER.warning(
                "Received account credentials for unknown node '%s'", node_id
            )
        except Exception:  # pragma: no cover - defensive logging
            _LOGGER.exception(
                "Failed to persist account credentials for node '%s'", node_id
            )
        return None


account_linker = AccountLinker()

__all__ = ["account_linker", "AccountLinker"]
