"""Helpers for configuring MQTT clients with TLS and connection overrides."""
from __future__ import annotations

import logging
import ssl
from dataclasses import dataclass
from types import MethodType
from typing import Dict, Optional

import paho.mqtt.client as mqtt
from .config import settings


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _MQTTConnection:
    """Connection parameters derived from the active configuration."""

    connect_host: str
    dial_host: str
    port: int


_TLS_VERSION_ALIASES = {
    "": None,
    "default": None,
    "auto": None,
    "tls": None,
    "tls1.2": ssl.TLSVersion.TLSv1_2,
    "tls1.3": ssl.TLSVersion.TLSv1_3,
    "tls1_2": ssl.TLSVersion.TLSv1_2,
    "tls1_3": ssl.TLSVersion.TLSv1_3,
    "1.2": ssl.TLSVersion.TLSv1_2,
    "1.3": ssl.TLSVersion.TLSv1_3,
    "tlsv1.2": ssl.TLSVersion.TLSv1_2,
    "tlsv1.3": ssl.TLSVersion.TLSv1_3,
    "tlsv1_2": ssl.TLSVersion.TLSv1_2,
    "tlsv1_3": ssl.TLSVersion.TLSv1_3,
}


def _parse_tls_version(version: str) -> Optional[ssl.TLSVersion]:
    """Map configured TLS version strings to :class:`ssl.TLSVersion` values."""

    key = version.strip().lower()
    if key not in _TLS_VERSION_ALIASES:
        raise ValueError(
            f"Unsupported TLS version '{version}'. Expected one of: "
            + ", ".join(sorted(k for k in _TLS_VERSION_ALIASES if k)),
        )
    return _TLS_VERSION_ALIASES[key]


def configure_client_tls(client: mqtt.Client) -> None:
    """Apply TLS settings to ``client`` when enabled in configuration."""

    if not settings.BROKER_TLS_ENABLED:
        return

    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)

    if settings.BROKER_TLS_CA_FILE:
        context.load_verify_locations(cafile=settings.BROKER_TLS_CA_FILE)

    certfile = settings.BROKER_TLS_CERTFILE or None
    keyfile = settings.BROKER_TLS_KEYFILE or None
    if certfile:
        context.load_cert_chain(certfile=certfile, keyfile=keyfile or None)

    tls_version = _parse_tls_version(settings.BROKER_TLS_VERSION)
    if tls_version is not None:
        # Pin the negotiated protocol version to the configured value so the
        # client and broker stay in sync during the mqtts migration.
        context.minimum_version = tls_version
        context.maximum_version = tls_version

    if settings.BROKER_TLS_CIPHERS:
        context.set_ciphers(settings.BROKER_TLS_CIPHERS)

    if settings.BROKER_TLS_INSECURE:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    set_context = getattr(client, "tls_set_context", None)
    if callable(set_context):
        try:
            set_context(context)
        except ValueError as exc:
            message = str(exc).lower()
            if "already been configured" in message:
                logger.debug("MQTT client TLS context already configured; reusing existing context")
            else:  # pragma: no cover - propagate unexpected errors
                raise
        return

    # Fall back to the legacy ``tls_set`` API for test doubles and older Paho
    # releases that do not expose ``tls_set_context``.
    tls_kwargs: Dict[str, object] = {
        "cert_reqs": ssl.CERT_NONE if settings.BROKER_TLS_INSECURE else ssl.CERT_REQUIRED,
        "tls_version": ssl.PROTOCOL_TLS_CLIENT,
    }
    if tls_version == ssl.TLSVersion.TLSv1_2:
        tls_kwargs["tls_version"] = ssl.PROTOCOL_TLSv1_2
    elif tls_version == ssl.TLSVersion.TLSv1_3:
        tls_kwargs["tls_version"] = ssl.PROTOCOL_TLS

    if settings.BROKER_TLS_CA_FILE:
        tls_kwargs["ca_certs"] = settings.BROKER_TLS_CA_FILE
    if certfile:
        tls_kwargs["certfile"] = certfile
    if keyfile:
        tls_kwargs["keyfile"] = keyfile
    if settings.BROKER_TLS_CIPHERS:
        tls_kwargs["ciphers"] = settings.BROKER_TLS_CIPHERS

    client.tls_set(**tls_kwargs)
    insecure_set = getattr(client, "tls_insecure_set", None)
    if callable(insecure_set):
        insecure_set(settings.BROKER_TLS_INSECURE)


def configure_client_connection(
    client: mqtt.Client,
    *,
    keepalive: int = 30,
) -> _MQTTConnection:
    """Prepare ``client`` for connection and return the resolved endpoints."""

    configure_client_tls(client)

    if settings.BROKER_USERNAME or settings.BROKER_PASSWORD:
        set_credentials = getattr(client, "username_pw_set", None)
        if callable(set_credentials):
            set_credentials(settings.BROKER_USERNAME, settings.BROKER_PASSWORD)

    dial_host = settings.BROKER_CONNECT_HOST or settings.BROKER_HOST
    connect_host = dial_host
    if settings.BROKER_TLS_ENABLED:
        sni_host = settings.BROKER_TLS_SERVERNAME or settings.BROKER_HOST
        connect_host = sni_host
        if dial_host and dial_host != connect_host:
            _override_client_dial_host(client, dial_host)

    reconnect_delay = getattr(client, "reconnect_delay_set", None)
    if callable(reconnect_delay):
        # Encourage aggressive reconnects during broker upgrades without
        # overwhelming the server with rapid retries.
        reconnect_delay(min_delay=1, max_delay=30)

    return _MQTTConnection(connect_host=connect_host, dial_host=dial_host, port=settings.BROKER_PORT)


def connect_mqtt_client(
    client: mqtt.Client,
    *,
    keepalive: int = 30,
    start_async: bool = False,
    raise_on_failure: bool = True,
) -> bool:
    """Configure ``client`` and initiate a broker connection.

    When ``start_async`` is ``True`` the client uses ``connect_async`` so it can
    reconnect in the background if the broker is unavailable.  In synchronous
    mode, connection errors are logged and optionally re-raised.
    """

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
            params.dial_host or params.connect_host,
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


def _override_client_dial_host(client: mqtt.Client, dial_host: str) -> None:
    """Force ``client`` to dial ``dial_host`` while keeping TLS SNI."""

    original_create_socket = getattr(mqtt.Client, "_create_socket_connection", None)
    client_create_socket = getattr(client, "_create_socket_connection", None)
    if not callable(original_create_socket) or not callable(client_create_socket):
        logger.debug(
            "MQTT client %s does not expose _create_socket_connection; skipping dial host override",
            type(client).__name__,
        )
        return

    def _create_socket_connection_override(self: mqtt.Client):
        original_host = self._host
        try:
            self._host = dial_host
            return original_create_socket(self)
        finally:
            self._host = original_host

    client._create_socket_connection = MethodType(  # type: ignore[assignment]
        _create_socket_connection_override,
        client,
    )
