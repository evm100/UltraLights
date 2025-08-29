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

gnome-terminal -- bash -c "mosquitto_sub -t \"#\" -v; exec bash" &

exec uvicorn app.main:app "${OPTS[@]}"
