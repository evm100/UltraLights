#!/usr/bin/env bash
# Run deploy/install.sh twice inside a throwaway ubuntu:26.04 container (no systemd, no certbot:
# a self-signed certificate stands in for Let's Encrypt), then check that nginx serves the web UI
# and proxies the API, that the database is migrated and the device CA exists.
#
#   deploy/tests/container-smoke.sh
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

if [ "${1:-}" != inside ]; then
  exec docker run --rm -v "$REPO:/src:ro" ubuntu:26.04 bash /src/deploy/tests/container-smoke.sh inside
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq sudo openssl iproute2 procps rsync curl >/dev/null
rsync -a --exclude node_modules --exclude .venv --exclude .dev --exclude dist --exclude '*.egg-info' /src/ /opt/ultralights/

D=lights.test
mkdir -p /etc/letsencrypt/live/$D
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 2 -subj "/CN=$D" \
  -addext "subjectAltName=DNS:$D" -keyout /etc/letsencrypt/live/$D/privkey.pem -out /etc/letsencrypt/live/$D/fullchain.pem 2>/dev/null

echo "===== first install"
/opt/ultralights/deploy/install.sh --domain $D --no-certbot --skip-admin
echo "===== second install (idempotent)"
/opt/ultralights/deploy/install.sh --domain $D --no-certbot --skip-admin
echo "===== --check"
/opt/ultralights/deploy/install.sh --domain $D --check || true

echo "===== starting the API by hand (no systemd in the container)"
cd /var/lib/ultralights
sudo -u ultralights env $(grep -v '^#' /etc/ultralights/ultralights.env | xargs) \
  /usr/local/lib/ultralights/venv/bin/uvicorn ulserver.main:app --app-dir /opt/ultralights/server \
  --host 127.0.0.1 --port 8100 >/tmp/api.log 2>&1 &
for _ in $(seq 1 30); do curl -s 127.0.0.1:8100/api/health >/dev/null && break; sleep 1; done

fail=0
check() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi; }
check "nginx serves the SPA over TLS" "curl -sk --resolve $D:443:127.0.0.1 https://$D/ | grep -q '<div id=\"app\">'"
check "SPA routes fall back to index.html" "curl -sk --resolve $D:443:127.0.0.1 https://$D/some/route | grep -q '<div id=\"app\">'"
check "API proxied" "curl -sk --resolve $D:443:127.0.0.1 https://$D/api/health | grep -q '\"ok\":true'"
check "HTTP redirects to HTTPS" "curl -s -o /dev/null -w '%{http_code}' --resolve $D:80:127.0.0.1 http://$D/ | grep -q 301"
check "enrollment endpoint answers per contract" "curl -sk --resolve $D:443:127.0.0.1 -H 'content-type: application/json' -d '{}' https://$D/api/v1/enroll | grep -q bad_request"
check "database migrated" "sudo -u postgres psql -d ultralights -tAc 'select count(*) from board_templates' | grep -q 1"
check "device CA created" "test -f /etc/ultralights/pki/device-ca.crt && test -f /etc/ultralights/pki/ul-server.key"
check "broker files synced" "test -f /etc/mosquitto/certs/ultralights/crl.pem && test -f /etc/mosquitto/certs/ultralights/server.key && test -f /etc/mosquitto/ultralights.acl"
check "secrets not world-readable" "[ \$(stat -c %a /etc/ultralights/pki/device-ca.key) = 600 ] && [ \$(stat -c %a /etc/ultralights/ultralights.env) = 640 ]"
install -d -o mosquitto /run/mosquitto
mosquitto -c /etc/mosquitto/mosquitto.conf -d
sleep 2
check "mosquitto listens on 8883 with TLS" "ss -ltn | grep -q ':8883 '"
apt-get install -y -qq mosquitto-clients >/dev/null
(cd /var/lib/ultralights && sudo -u ultralights env UL_ENV_FILE=/etc/ultralights/ultralights.env \
  /usr/local/lib/ultralights/venv/bin/ulctl issue-client ul-c0ffee /var/lib/ultralights/t >/dev/null)
pub() { timeout 8 mosquitto_pub -h 127.0.0.1 -p 8883 --cafile /etc/letsencrypt/live/$D/fullchain.pem --insecure -q 1 "$@" -t ul/v1/d/ul-c0ffee/status -m '{"online":true}'; }
check "device certificate accepted by the broker" "pub -i ul-c0ffee --cert /var/lib/ultralights/t/ul-c0ffee.crt --key /var/lib/ultralights/t/ul-c0ffee.key"
check "no client certificate is refused" "! pub -i anon 2>/dev/null"
check "bridge reaches the broker (TLS up; self-signed cert is the only complaint)" "grep -q 'self-signed certificate' /tmp/api.log"
[ "$fail" = 0 ] || { echo '--- api log'; tail -20 /tmp/api.log; echo '--- mosquitto log'; tail -20 /var/log/mosquitto/mosquitto.log 2>/dev/null; }
exit $fail
