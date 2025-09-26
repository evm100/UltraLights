#!/usr/bin/env bash
source .venv/bin/activate
set -euo pipefail

# Load .env (KEY=VALUE lines). This exports everything without parsing hacks.
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

# Sanity: warn if JSON looks multiline (common mistake)
if grep -q '^DEVICE_REGISTRY_JSON=\[' .env 2>/dev/null; then
  echo "WARNING: DEVICE_REGISTRY_JSON must be single-line and quoted. See sample in .env."
fi

# Decide TLS vs HTTP
OPTS=(--host "${WEB_HOST:-0.0.0.0}" --port "${WEB_PORT:-443}" --proxy-headers --forwarded-allow-ips='*' --log-level info)

if [[ -n "${SSL_CERTFILE:-}" && -n "${SSL_KEYFILE:-}" && -f "${SSL_CERTFILE}" && -f "${SSL_KEYFILE}" ]]; then
  OPTS+=(--ssl-certfile "$SSL_CERTFILE" --ssl-keyfile "$SSL_KEYFILE")
  echo "Starting HTTPS on ${WEB_HOST:-0.0.0.0}:${WEB_PORT:-443} using ${SSL_CERTFILE}"
else
  echo "TLS cert/key missing; starting HTTP on ${WEB_HOST:-0.0.0.0}:${WEB_PORT:-8080}"
  OPTS=(--host "${WEB_HOST:-0.0.0.0}" --port "${WEB_PORT:-8080}" --proxy-headers --forwarded-allow-ips='*' --log-level info)
fi

if command -v mosquitto_sub >/dev/null 2>&1; then
  default_mqtt_port="1883"
  if [[ "${BROKER_TLS_ENABLED:-1}" != "0" ]]; then
    default_mqtt_port="8883"
  fi

  MQTT_SUB_OPTS=(-h "${BROKER_HOST:-localhost}" -p "${BROKER_PORT:-$default_mqtt_port}" -t "#" -v)

  if [[ -n "${BROKER_USERNAME:-}" ]]; then
    MQTT_SUB_OPTS+=(-u "$BROKER_USERNAME")
  fi
  if [[ -n "${BROKER_PASSWORD:-}" ]]; then
    MQTT_SUB_OPTS+=(-P "$BROKER_PASSWORD")
  fi

  if [[ "${BROKER_TLS_ENABLED:-1}" != "0" ]]; then
    if [[ -n "${BROKER_TLS_CA_FILE:-}" && -f "${BROKER_TLS_CA_FILE}" ]]; then
      MQTT_SUB_OPTS+=(--cafile "$BROKER_TLS_CA_FILE")
    fi
    if [[ -n "${BROKER_TLS_CERTFILE:-}" && -f "${BROKER_TLS_CERTFILE}" ]]; then
      MQTT_SUB_OPTS+=(--cert "$BROKER_TLS_CERTFILE")
    fi
    if [[ -n "${BROKER_TLS_KEYFILE:-}" && -f "${BROKER_TLS_KEYFILE}" ]]; then
      MQTT_SUB_OPTS+=(--key "$BROKER_TLS_KEYFILE")
    fi
    if [[ "${BROKER_TLS_INSECURE:-0}" != "0" ]]; then
      MQTT_SUB_OPTS+=(--insecure)
    fi
  fi

  if command -v gnome-terminal >/dev/null 2>&1 && [[ -n "${DISPLAY:-}" ]]; then
    mqtt_cmd=$(printf '%q ' mosquitto_sub "${MQTT_SUB_OPTS[@]}")
    mqtt_cmd=${mqtt_cmd% }
    gnome-terminal -- bash -c "${mqtt_cmd}; exec bash" &
  else
    echo "gnome-terminal unavailable; starting background mosquitto_sub subscriber"
    mosquitto_sub "${MQTT_SUB_OPTS[@]}" &
  fi
else
  echo "WARNING: MQTT monitor disabled (requires gnome-terminal or mosquitto_sub)" >&2
fi

exec uvicorn app.main:app "${OPTS[@]}"
