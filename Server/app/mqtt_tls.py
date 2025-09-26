"""Helpers for configuring MQTT clients with TLS."""
from __future__ import annotations

import ssl
from typing import Any, Dict

import paho.mqtt.client as mqtt

from .config import settings


def configure_client_tls(client: mqtt.Client) -> None:
    """Apply TLS settings to ``client`` when enabled in configuration."""

    if not settings.BROKER_TLS_ENABLED:
        return

    tls_kwargs: Dict[str, Any] = {
        "tls_version": ssl.PROTOCOL_TLS_CLIENT,
        "cert_reqs": ssl.CERT_REQUIRED,
    }

    if settings.BROKER_TLS_CA_FILE:
        tls_kwargs["ca_certs"] = settings.BROKER_TLS_CA_FILE
    if settings.BROKER_TLS_CERTFILE:
        tls_kwargs["certfile"] = settings.BROKER_TLS_CERTFILE
    if settings.BROKER_TLS_KEYFILE:
        tls_kwargs["keyfile"] = settings.BROKER_TLS_KEYFILE

    client.tls_set(**tls_kwargs)
    client.tls_insecure_set(settings.BROKER_TLS_INSECURE)


def connect_mqtt_client(client: mqtt.Client, *, keepalive: int = 30) -> None:
    """Configure TLS (if enabled) and connect ``client`` to the broker."""

    configure_client_tls(client)
    client.connect(settings.BROKER_HOST, settings.BROKER_PORT, keepalive=keepalive)
