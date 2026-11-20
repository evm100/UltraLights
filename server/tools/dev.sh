#!/usr/bin/env bash
# Dev stack on the workstation, for bench testing before the cutover.
#
#   server/tools/dev.sh up      start PostgreSQL, Mosquitto (mTLS :8883) and nginx (:443 on the LAN IP),
#                               migrate, create CAs and dev TLS certs, seed templates
#   server/tools/dev.sh admin   create or reset the dev admin account (prompts)
#   server/tools/dev.sh api     run the API on 127.0.0.1:8100 with auto-reload (foreground)
#   server/tools/dev.sh web     run the Vite dev server on http://localhost:5173 (foreground)
#   server/tools/dev.sh build   build web/ into web/dist (served by the dev nginx)
#   server/tools/dev.sh test    run the server test suite
#   server/tools/dev.sh status  show containers and URLs
#   server/tools/dev.sh down    stop and remove the dev containers (data volume kept)
#   server/tools/dev.sh reset   down + delete the dev database volume and server/.dev
#
# Environment: UL_DEV_HOST (default ul-dev.lan), UL_DEV_IP (default: this machine's LAN address).
# Nodes reach the dev server by name, so add UL_DEV_HOST -> UL_DEV_IP to the LAN DNS (192.168.1.99).
# Nothing here touches the system Mosquitto on 127.0.0.1:1883.
set -euo pipefail

SERVER="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="$(cd "$SERVER/.." && pwd)"
DEV="$SERVER/.dev"
VENV="$SERVER/.venv"
HOST="${UL_DEV_HOST:-ul-dev.lan}"
IP="${UL_DEV_IP:-$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p')}"
PG=ul-dev-pg
MQ=ul-dev-mqtt
NG=ul-dev-nginx
API_PORT=8100

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }

running() { [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" = "true" ]; }

ensure_venv() {
  if [ ! -x "$VENV/bin/ulctl" ]; then
    say "creating Python venv"
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install -q --upgrade pip
    "$VENV/bin/pip" install -q -e "$SERVER[dev]"
  fi
}

write_env() {
  mkdir -p "$DEV/pki" "$DEV/tls" "$DEV/data" "$DEV/mosquitto/data"
  cat >"$DEV/dev.env" <<EOF
UL_DATABASE_URL=postgresql+psycopg://ultralights@127.0.0.1:5433/ultralights
UL_PUBLIC_HOST=$HOST
UL_MQTT_HOST=127.0.0.1
UL_MQTT_PORT=8883
UL_MQTT_CHECK_HOSTNAME=false
UL_MQTT_CA_FILE=$DEV/tls/tls-ca.crt
UL_PKI_DIR=$DEV/pki
UL_PROVISION_SERVER_CA_FILE=$DEV/tls/tls-ca.crt
UL_DATA_DIR=$DEV/data
UL_COOKIE_SECURE=false
EOF
  export UL_ENV_FILE="$DEV/dev.env"
}

ulctl() { UL_ENV_FILE="$DEV/dev.env" "$VENV/bin/ulctl" "$@"; }

start_pg() {
  if ! running $PG; then
    docker rm -f $PG >/dev/null 2>&1 || true
    say "starting PostgreSQL ($PG, 127.0.0.1:5433)"
    docker run -d --name $PG -p 127.0.0.1:5433:5432 \
      -e POSTGRES_USER=ultralights -e POSTGRES_DB=ultralights -e POSTGRES_HOST_AUTH_METHOD=trust \
      -v ul-dev-pg-data:/var/lib/postgresql postgres:18 >/dev/null
  fi
  for _ in $(seq 1 30); do
    docker exec $PG pg_isready -U ultralights -q 2>/dev/null && break
    sleep 1
  done
  docker exec $PG psql -U ultralights -tAc "SELECT 1 FROM pg_database WHERE datname='ul_test'" | grep -q 1 ||
    docker exec $PG psql -U ultralights -qc "CREATE DATABASE ul_test" >/dev/null
}

start_mqtt() {
  cp "$REPO/deploy/mosquitto/acl" "$DEV/mosquitto/acl"
  cat >"$DEV/mosquitto/mosquitto.conf" <<EOF
# Dev broker: same ACL and TLS rules as production, dev TLS certificate for $HOST.
allow_anonymous false
acl_file /ul/mosquitto/acl
persistence true
persistence_location /ul/mosquitto/data/
log_dest stdout
log_type error
log_type warning
log_type notice
log_type subscribe
listener 8883
certfile /ul/tls/server.crt
keyfile /ul/tls/server.key
cafile /ul/pki/ca-chain.crt
crlfile /ul/pki/crl.pem
require_certificate true
use_identity_as_username true
tls_version tlsv1.2
EOF
  docker rm -f $MQ >/dev/null 2>&1 || true
  say "starting Mosquitto ($MQ, mTLS on :8883)"
  docker run -d --name $MQ --network host --user "$(id -u):$(id -g)" \
    -v "$DEV:/ul" eclipse-mosquitto:2 mosquitto -c /ul/mosquitto/mosquitto.conf >/dev/null
}

start_nginx() {
  mkdir -p "$REPO/web/dist"
  cat >"$DEV/nginx.conf" <<EOF
server {
    listen $IP:443 ssl;
    server_name $HOST;
    ssl_certificate     /ul/tls/server.crt;
    ssl_certificate_key /ul/tls/server.key;
    client_max_body_size 20m;
    root /ul-web;
    location /api/ {
        proxy_pass http://127.0.0.1:$API_PORT;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \$connection_upgrade;
        proxy_read_timeout 3600s;
    }
    location / { try_files \$uri /index.html; }
}
map \$http_upgrade \$connection_upgrade { default upgrade; '' close; }
EOF
  docker rm -f $NG >/dev/null 2>&1 || true
  say "starting nginx ($NG, https://$HOST on $IP:443)"
  docker run -d --name $NG --network host \
    -v "$DEV/nginx.conf:/etc/nginx/conf.d/default.conf:ro" -v "$DEV/tls:/ul/tls:ro" \
    -v "$REPO/web/dist:/ul-web:ro" nginx:1.29-alpine >/dev/null
}

case "${1:-}" in
  up)
    [ -n "$IP" ] || { echo "cannot find the LAN IP; set UL_DEV_IP" >&2; exit 1; }
    ensure_venv
    write_env
    start_pg
    say "migrating database"
    ulctl db upgrade >/dev/null
    say "device CA"
    ulctl ca init
    say "dev TLS certificate for $HOST"
    ulctl dev-tls "$DEV/tls" "$HOST" >/dev/null
    ulctl ca crl >/dev/null
    ulctl seed
    start_mqtt
    start_nginx
    cat <<EOF

Dev stack is up.
  1. LAN DNS (192.168.1.99): add  $HOST  A  $IP
  2. server/tools/dev.sh admin     (once: create your admin account)
  3. server/tools/dev.sh api       (API on 127.0.0.1:$API_PORT)
  4. server/tools/dev.sh build     then open https://$HOST  (trust $DEV/tls/tls-ca.crt in the browser)
     or server/tools/dev.sh web    and open http://localhost:5173 (Web Serial works there too)
  Nodes provisioned from this server get server=$HOST and the dev TLS CA in provision.server_ca.
EOF
    ;;
  admin)
    ensure_venv; write_env
    read -rp "Admin username: " u
    ulctl create-admin "$u"
    ;;
  api)
    ensure_venv; write_env
    cd "$SERVER"
    exec env UL_ENV_FILE="$DEV/dev.env" "$VENV/bin/uvicorn" ulserver.main:app --host 127.0.0.1 --port $API_PORT \
      --reload --reload-dir "$SERVER/ulserver" --proxy-headers
    ;;
  web)
    cd "$REPO/web"
    [ -d node_modules ] || npm install
    exec npm run dev
    ;;
  build)
    cd "$REPO/web"
    [ -d node_modules ] || npm install
    npm run build
    ;;
  test)
    ensure_venv; write_env
    start_pg
    cd "$SERVER"
    exec env UL_ENV_FILE="$DEV/dev.env" "$VENV/bin/pytest" "${@:2}"
    ;;
  status)
    docker ps --filter name=ul-dev- --format 'table {{.Names}}\t{{.Status}}'
    echo "https://$HOST  ($IP)   API 127.0.0.1:$API_PORT   MQTT :8883"
    ;;
  down)
    docker rm -f $NG $MQ $PG >/dev/null 2>&1 || true
    say "dev containers removed (database volume ul-dev-pg-data kept)"
    ;;
  reset)
    docker rm -f $NG $MQ $PG >/dev/null 2>&1 || true
    docker volume rm ul-dev-pg-data >/dev/null 2>&1 || true
    rm -rf "$DEV"
    say "dev stack, database and server/.dev removed"
    ;;
  *)
    sed -n '2,16p' "$0"
    exit 2
    ;;
esac
