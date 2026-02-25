"""Helpers for configuring standard MQTT clients with connection overrides."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import paho.mqtt.client as mqtt
from .config import settings

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class _MQTTConnection:
    """Connection parameters derived from the active configuration."""
    connect_host: str
    port: int

def configure_client_connection(
    client: mqtt.Client,
    *,
    keepalive: int = 30,
) -> _MQTTConnection:
    """Prepare ``client`` for basic connection and return the resolved endpoints."""

    if settings.BROKER_USERNAME or settings.BROKER_PASSWORD:
        set_credentials = getattr(client, "username_pw_set", None)
        if callable(set_credentials):
            set_credentials(settings.BROKER_USERNAME, settings.BROKER_PASSWORD)

    dial_host = settings.BROKER_CONNECT_HOST or settings.BROKER_HOST

    reconnect_delay = getattr(client, "reconnect_delay_set", None)
    if callable(reconnect_delay):
        # Encourage aggressive reconnects during broker upgrades without
        # overwhelming the server with rapid retries.
        reconnect_delay(min_delay=1, max_delay=30)

    return _MQTTConnection(connect_host=dial_host, port=settings.BROKER_PORT)

def connect_mqtt_client(
    client: mqtt.Client,
    *,
    keepalive: int = 30,
    start_async: bool = False,
    raise_on_failure: bool = True,
) -> bool:
    """Configure ``client`` and initiate a standard unencrypted broker connection."""

    params = configure_client_connection(client, keepalive=keepalive)

    try:
        if start_async:
            connect_async = getattr(client, "connect_async", None)
            if callable(connect_async):
                connect_async(params.connect_host, params.port, keepalive=keepalive)
                return True
        client.connect(params.connect_host, params.port, keepalive=keepalive)
        return True
    except Exception as exc:
        logger.error(
            "MQTT connection to %s:%d failed: %s",
            params.connect_host,
            params.port,
            exc,
        )
        if start_async:
            # Fall back to an async reconnect attempt so clients can recover
            # automatically once the broker returns.
            try:
                connect_async = getattr(client, "connect_async", None)
                if callable(connect_async):
                    connect_async(params.connect_host, params.port, keepalive=keepalive)
            except Exception:
                logger.debug("Unable to schedule async reconnect", exc_info=True)
        if raise_on_failure:
            raise
        return False
