#!/usr/bin/env bash
# UltraLights server install for Ubuntu Server 26.04 LTS. Safe to re-run.
#
#   sudo ./deploy/install.sh --check      report what's in the way (v5 leftovers); changes nothing
#   sudo ./deploy/install.sh              install or update everything
#
# Options:
#   --domain NAME     public host name (default lights.evm100.org)
#   --email ADDR      Let's Encrypt account email, only needed if no certificate exists yet
#   --skip-admin      don't prompt for the first admin account
#   --no-certbot      never request a certificate (testing; nginx/Mosquitto need one to start)
#   --no-start        install units but don't start or restart anything
#   --https-listen A  serve HTTPS on A ("127.0.0.1:8443") with PROXY protocol, behind a :443
#                     stream router; normally detected from the nginx config
#
# What it never does: stop or disable services it didn't create (v5's API, containers, units).
# It prints the commands for you instead. See deploy/README.md for the cutover runbook.
set -euo pipefail

DOMAIN=lights.evm100.org
EMAIL=""
CHECK=0
SKIP_ADMIN=0
NO_CERTBOT=0
NO_START=0
HTTPS_BEHIND=""
while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK=1 ;;
    --domain) DOMAIN="$2"; shift ;;
    --email) EMAIL="$2"; shift ;;
    --skip-admin) SKIP_ADMIN=1 ;;
    --no-certbot) NO_CERTBOT=1 ;;
    --no-start) NO_START=1 ;;
    --https-listen) HTTPS_BEHIND="$2"; shift ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
  shift
done

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_USER=ultralights
ETC=/etc/ultralights
ENV_FILE=$ETC/ultralights.env
PKI=$ETC/pki
DATA=/var/lib/ultralights
VENV=/usr/local/lib/ultralights/venv
WEBROOT=/var/www/ultralights
API_PORT=8100
BACKUP=/var/backups/ultralights-v5
SYSTEMD=0; [ -d /run/systemd/system ] && SYSTEMD=1

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m!!\033[0m %s\n' "$*"; }
die() { printf '\033[31mxx\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- preflight
# Who listens on a TCP port: prints "name pid unit" lines (unit from the process cgroup).
listeners() {
  ss -ltnpH "sport = :$1" 2>/dev/null | grep -o 'users:(([^)]*))' | grep -o '"[^"]*",pid=[0-9]*' | sort -u |
  while IFS= read -r p; do
    name="${p#\"}"; name="${name%%\"*}"
    pid="${p##*pid=}"
    cg="$(cat "/proc/$pid/cgroup" 2>/dev/null | head -1)"
    unit="$(printf '%s' "$cg" | grep -oE '[^/]+\.(service|scope)' | tail -1)"
    case "$cg" in *docker*) unit="docker:${unit:-container}";; esac
    echo "$name $pid ${unit:-?}"
  done
}

BLOCKERS=()
STOP_HINTS=()
BROKER_HINTS=()

# The host Mosquitto may already serve other projects (their own files in conf.d). With the
# default per_listener_settings false, Mosquitto allows one global acl_file, so adding ours stops
# the broker for everyone. With true, each listener has its own security options, and they bind
# to the listener line above them, so another file's options placed before its listener would
# land on whichever listener the previous file left open. Both cases are blockers.
MOSQ_SECURITY='acl_file|password_file|allow_anonymous|psk_file|plugin|plugin_opt_.*|auth_plugin|auth_opt_.*|auto_id_prefix|allow_zero_length_clientid'
broker_preflight() {
  local main="${1:-/etc/mosquitto}/mosquitto.conf" dir="${1:-/etc/mosquitto}/conf.d" others=() f pls
  [ -r "$main" ] || return 0
  for f in "$dir"/*.conf; do
    [ -f "$f" ] && [ "$(basename "$f")" != ultralights.conf ] && others+=("$f")
  done
  [ ${#others[@]} -gt 0 ] || return 0
  echo "   mosquitto: shared with ${others[*]}"
  pls="$(grep -hE '^[[:space:]]*per_listener_settings[[:space:]]' "$main" "${others[@]}" | awk '{print $2}' | sort -u | xargs)"
  if [ "$pls" != true ]; then
    BLOCKERS+=("Mosquitto is shared with other projects but per_listener_settings is ${pls:-unset (false)}; it must be true")
    BROKER_HINTS+=("in $main add 'per_listener_settings true' above include_dir, and delete any 'per_listener_settings false' in conf.d")
  fi
  for f in "${others[@]}"; do
    if awk -v re="^($MOSQ_SECURITY)\$" '$1 == "listener" {exit 1} $1 ~ re {found=1; exit 0} END {exit !found}' "$f"; then
      BLOCKERS+=("$f sets security options (acl_file, allow_anonymous, ...) before its listener line")
      BROKER_HINTS+=("in $f move those options to just below its 'listener' line (and add 'allow_anonymous false' there if absent)")
    fi
  done
}

# HTTPS listener. Normally the site listens on 443 itself. On a host where an nginx stream block
# already owns :443 (an ssl_preread router passing TLS through with PROXY protocol, as NBE's
# /etc/nginx/nbe-stream.conf does), a second "listen 443" stops nginx on its next restart. The site
# then listens where the other sites behind the router do ("listen 127.0.0.1:8443 ssl proxy_protocol").
https_preflight() {
  [ -z "$HTTPS_BEHIND" ] || { echo "   https: behind a :443 router on $HTTPS_BEHIND (--https-listen)"; return 0; }
  command -v nginx >/dev/null 2>&1 || return 0
  local dump
  dump="$(nginx -T 2>/dev/null)" || return 0
  printf '%s\n' "$dump" | awk '
    /^[[:space:]]*stream[[:space:]]*\{/ { s = 1 }
    s && /^[[:space:]]*listen[[:space:]]+([^;]*:)?443[[:space:];]/ { l = 1 }
    s && /^[[:space:]]*ssl_preread[[:space:]]+on/ { p = 1 }
    END { exit !(l && p) }' || return 0
  HTTPS_BEHIND="$(printf '%s\n' "$dump" | grep -oE '^[[:space:]]*listen[[:space:]]+[0-9.]+:[0-9]+[[:space:]]+ssl[^;]*proxy_protocol' |
    awk '{print $2}' | sort | uniq -c | sort -rn | awk 'NR == 1 {print $2}')"
  if [ -n "$HTTPS_BEHIND" ]; then
    echo "   https: :443 is owned by an nginx stream router; the site will listen on $HTTPS_BEHIND (PROXY protocol), like the other sites behind it"
  else
    BLOCKERS+=("an nginx stream block owns :443 (ssl_preread) but no site listens behind it with 'ssl proxy_protocol'; pass --https-listen ADDR:PORT")
  fi
}
https_listen() { # the lines that replace __HTTPS_LISTEN__ in the site template
  if [ -z "$HTTPS_BEHIND" ]; then
    printf '    listen 443 ssl;\n    listen [::]:443 ssl;\n'
  else
    printf '    listen %s ssl proxy_protocol;\n' "$HTTPS_BEHIND"
    printf '    set_real_ip_from %s;\n    real_ip_header proxy_protocol;\n    port_in_redirect off;\n' "${HTTPS_BEHIND%:*}"
  fi
}

preflight() {
  bold "Preflight for $DOMAIN"
  if [ -r /etc/os-release ]; then
    . /etc/os-release
    if [ "${ID:-}" != ubuntu ] || [ "${VERSION_ID:-}" != "26.04" ]; then
      warn "this script targets Ubuntu 26.04 LTS; found ${PRETTY_NAME:-unknown}"
    else
      echo "   OS: $PRETTY_NAME"
    fi
  fi
  [ "$(id -u)" = 0 ] || warn "not running as root: process names for ports may be hidden"

  for port in 80 443 8883 $API_PORT 8000 1883; do
    while read -r name pid unit; do
      [ -n "${name:-}" ] || continue
      ours=0
      case "$port:$name:$unit" in
        80:nginx:*|443:nginx:*) ours=1 ;;                       # nginx is shared; the v5 site is handled below
        8883:mosquitto:mosquitto.service) ours=1 ;;
        "$API_PORT":*:ultralights-api.service) ours=1 ;;
      esac
      if [ "$ours" = 1 ]; then
        echo "   port $port: $name ($unit) - fine"
        continue
      fi
      case "$port" in
        8883|"$API_PORT"|80|443) BLOCKERS+=("port $port is held by $name (pid $pid, $unit)") ;;
        *) echo "   port $port: $name (pid $pid, $unit) - probably v5; not needed by the new server" ;;
      esac
      case "$unit" in
        docker:*) STOP_HINTS+=("docker ps --format '{{.Names}} {{.Image}}'   # then: docker stop <name> && docker update --restart=no <name>") ;;
        *.service) STOP_HINTS+=("sudo systemctl disable --now $unit") ;;
        *) STOP_HINTS+=("sudo kill $pid   # $name on port $port (started by hand? check tmux/screen/cron too)") ;;
      esac
    done < <(listeners "$port")
  done

  if command -v docker >/dev/null 2>&1; then
    while read -r cname cimage _; do
      [ -n "${cname:-}" ] || continue
      echo "   container $cname ($cimage) is running - v5 broker?"
      STOP_HINTS+=("docker stop $cname && docker update --restart=no $cname")
    done < <(docker ps --format '{{.Names}} {{.Image}} {{.Status}}' 2>/dev/null | grep -iE 'mosquitto|ultra|light' || true)
  fi

  if [ -d /etc/nginx ]; then
    while IFS= read -r f; do
      case "$(basename "$f")" in ultralights) continue ;; esac
      echo "   nginx: $f serves $DOMAIN - will be backed up to $BACKUP and disabled"
    done < <(grep -lsE "server_name[^;]*\b${DOMAIN//./\\.}\b" /etc/nginx/sites-enabled/* /etc/nginx/conf.d/*.conf 2>/dev/null || true)
  fi

  https_preflight
  broker_preflight

  if [ "$SYSTEMD" = 1 ]; then
    while read -r unit _; do
      case "$unit" in ultralights-*) continue ;; esac
      echo "   systemd unit $unit looks like v5"
      STOP_HINTS+=("sudo systemctl disable --now $unit")
    done < <(systemctl list-units --type=service --all --no-legend --plain 2>/dev/null | grep -iE 'ultra|lights|uvicorn' || true)
  fi

  if [ -d "/etc/letsencrypt/live/$DOMAIN" ]; then
    echo "   certificate: /etc/letsencrypt/live/$DOMAIN exists - will be reused"
  else
    echo "   certificate: none yet - certbot will request one (needs port 80 reachable from the internet)"
  fi
  case "$REPO" in /root/*|/home/*) warn "repo is at $REPO; the service user needs read access (clone to /opt/ultralights)";; esac

  if [ ${#STOP_HINTS[@]} -gt 0 ]; then
    bold "Stop v5 yourself before installing (this script won't):"
    printf '%s\n' "${STOP_HINTS[@]}" | sort -u | sed 's/^/   /'
  fi
  if [ ${#BROKER_HINTS[@]} -gt 0 ]; then
    bold "Mosquitto is shared; change its config yourself (this script won't edit other projects' files):"
    printf '   %s\n' "${BROKER_HINTS[@]}"
  fi
  if [ ${#BLOCKERS[@]} -gt 0 ]; then
    bold "Blocking:"
    printf '   %s\n' "${BLOCKERS[@]}"
    return 1
  fi
  echo "   nothing blocking"
}

if [ "$CHECK" = 1 ]; then
  preflight && exit 0 || exit 1
fi

[ "$(id -u)" = 0 ] || die "run as root: sudo $0"
if ! preflight; then
  die "resolve the blocking items above, then run again (nothing was changed)"
fi

# ---------------------------------------------------------------- packages
say "installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq nginx certbot postgresql mosquitto python3 python3-venv nodejs npm git rsync ca-certificates openssl >/dev/null
node_major="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
[ "$node_major" -ge 20 ] || die "Node.js 20 or newer is needed to build the web UI (found $(node -v 2>/dev/null))"

# ---------------------------------------------------------------- user and directories
say "user and directories"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin "$APP_USER"
install -d -o root -g "$APP_USER" -m 0750 "$ETC"
install -d -o "$APP_USER" -g "$APP_USER" -m 0750 "$PKI" "$DATA" "$DATA/firmware"
install -d -m 0755 "$WEBROOT" /var/www/certbot "$(dirname "$VENV")"
install -d -m 0700 "$BACKUP"
sudo -u "$APP_USER" test -r "$REPO/server/pyproject.toml" || die "$APP_USER can't read $REPO; clone the repo somewhere world-readable such as /opt/ultralights"

# ---------------------------------------------------------------- database
say "PostgreSQL"
if [ -z "$(pg_lsclusters -h 2>/dev/null)" ]; then
  pgver="$(find /usr/lib/postgresql -mindepth 1 -maxdepth 1 -printf '%f\n' | sort -V | tail -1)"
  pg_createcluster "$pgver" main >/dev/null
fi
if [ "$SYSTEMD" = 1 ]; then
  systemctl enable --now postgresql >/dev/null
else
  while read -r ver cname _; do pg_ctlcluster "$ver" "$cname" start 2>/dev/null || true; done < <(pg_lsclusters -h)
fi
pg_up=0
for _ in $(seq 1 30); do sudo -u postgres psql -qtAc 'select 1' >/dev/null 2>&1 && { pg_up=1; break; }; sleep 1; done
[ "$pg_up" = 1 ] || die "PostgreSQL didn't come up (pg_lsclusters; journalctl -u postgresql)"
sudo -u postgres psql -qtAc "select 1 from pg_roles where rolname='$APP_USER'" | grep -q 1 ||
  sudo -u postgres createuser "$APP_USER"
sudo -u postgres psql -qtAc "select 1 from pg_database where datname='ultralights'" | grep -q 1 ||
  sudo -u postgres createdb -O "$APP_USER" ultralights

# ---------------------------------------------------------------- settings
say "settings in $ENV_FILE"
cat >"$ENV_FILE.new" <<EOF
# Written by deploy/install.sh. Re-running install.sh rewrites this file.
UL_DATABASE_URL=postgresql+psycopg://$APP_USER@/ultralights
UL_PUBLIC_HOST=$DOMAIN
UL_MQTT_HOST=127.0.0.1
UL_MQTT_PORT=8883
UL_MQTT_CHECK_HOSTNAME=false
UL_PKI_DIR=$PKI
UL_DATA_DIR=$DATA
UL_CONTRACTS_DIR=$REPO/contracts
UL_COOKIE_SECURE=true
EOF
install -o root -g "$APP_USER" -m 0640 "$ENV_FILE.new" "$ENV_FILE"
rm -f "$ENV_FILE.new"

# ---------------------------------------------------------------- Python app
say "Python environment ($VENV)"
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -e "$REPO/server"
ulctl() { (cd "$DATA" && sudo -u "$APP_USER" env UL_ENV_FILE="$ENV_FILE" "$VENV/bin/ulctl" "$@"); }
ulctl db upgrade
ulctl ca init
ulctl seed

# ---------------------------------------------------------------- web UI
say "building the web UI"
owner="$(stat -c %U "$REPO")"
build() { if [ "$owner" = root ]; then (cd "$REPO/web" && "$@"); else sudo -u "$owner" bash -c "cd '$REPO/web' && $*"; fi; }
build npm ci --no-audit --no-fund --loglevel=error
build npm run build --silent
rsync -a --delete "$REPO/web/dist/" "$WEBROOT/"

# ---------------------------------------------------------------- certificate
LE="/etc/letsencrypt/live/$DOMAIN"
nginx_site() { # $1 = template
  sed -e "s|__DOMAIN__|$DOMAIN|g" -e "s|__API_PORT__|$API_PORT|g" -e "s|__WEBROOT__|$WEBROOT|g" "$1" |
    awk -v block="$(https_listen)" '$1 == "__HTTPS_LISTEN__" { print block; next } { print }'
}
backup_v5_sites() {
  while IFS= read -r f; do
    case "$(basename "$f")" in ultralights) continue ;; esac
    real="$(readlink -f "$f")"
    if [ ! -e "$BACKUP/nginx/$(basename "$f")" ]; then
      install -d "$BACKUP/nginx"
      cp -a "$real" "$BACKUP/nginx/$(basename "$f")"
      echo "$f -> $real" >>"$BACKUP/nginx/links.txt"
    fi
    rm -f "$f"
    say "disabled v5 nginx site $f (backup in $BACKUP/nginx)"
  done < <(grep -lsE "server_name[^;]*\b${DOMAIN//./\\.}\b" /etc/nginx/sites-enabled/* /etc/nginx/conf.d/*.conf 2>/dev/null || true)
}
reload_nginx() {
  nginx -t -q || die "nginx config test failed; nothing was reloaded"
  if [ "$NO_START" = 0 ]; then
    if [ "$SYSTEMD" = 1 ]; then systemctl reload-or-restart nginx; else nginx -s reload 2>/dev/null || nginx; fi
  fi
}

say "nginx"
backup_v5_sites
if [ ! -f "$LE/fullchain.pem" ]; then
  [ "$NO_CERTBOT" = 0 ] || die "no certificate for $DOMAIN and --no-certbot given"
  # Plain-HTTP site first so certbot's webroot challenge can be answered.
  cat >/etc/nginx/sites-available/ultralights <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    location ^~ /.well-known/acme-challenge/ { root /var/www/certbot; default_type text/plain; }
    location / { return 503; }
}
EOF
  ln -sf /etc/nginx/sites-available/ultralights /etc/nginx/sites-enabled/ultralights
  reload_nginx
  say "requesting a Let's Encrypt certificate for $DOMAIN"
  if [ -n "$EMAIL" ]; then acct=(--email "$EMAIL"); else acct=(--register-unsafely-without-email); fi
  certbot certonly --webroot -w /var/www/certbot -d "$DOMAIN" --agree-tos --non-interactive "${acct[@]}"
fi
nginx_site "$REPO/deploy/nginx/ultralights.conf" >/etc/nginx/sites-available/ultralights
ln -sf /etc/nginx/sites-available/ultralights /etc/nginx/sites-enabled/ultralights
reload_nginx

# ---------------------------------------------------------------- broker
say "Mosquitto (mutual TLS on 8883)"
if [ ! -e "$BACKUP/mosquitto.tar.gz" ] && [ -d /etc/mosquitto ]; then
  tar -C /etc -czf "$BACKUP/mosquitto.tar.gz" mosquitto
fi
# Ubuntu's AppArmor profile lets Mosquitto read /etc/mosquitto/* and /etc/mosquitto/certs/** but not
# other subdirectories, so the ACL sits at the top level and the TLS files under certs/.
rm -rf /etc/mosquitto/ultralights            # earlier installs used this path; AppArmor denies it
install -o mosquitto -g mosquitto -m 0600 "$REPO/deploy/mosquitto/acl" /etc/mosquitto/ultralights.acl
install -o root -g root -m 0644 "$REPO/deploy/mosquitto/ultralights.conf" /etc/mosquitto/conf.d/ultralights.conf
install -o root -g root -m 0755 "$REPO/deploy/bin/ultralights-broker-sync" /usr/local/sbin/ultralights-broker-sync
install -d /etc/letsencrypt/renewal-hooks/deploy
cat >/etc/letsencrypt/renewal-hooks/deploy/ultralights-mosquitto <<'EOF'
#!/bin/sh
# Renewed certificate: give Mosquitto the new copy (nginx is reloaded by certbot's own hooks or below).
/usr/local/sbin/ultralights-broker-sync
systemctl reload nginx 2>/dev/null || true
EOF
chmod 0755 /etc/letsencrypt/renewal-hooks/deploy/ultralights-mosquitto
/usr/local/sbin/ultralights-broker-sync --no-restart

# ---------------------------------------------------------------- services
# Mosquitto may also carry other projects' listeners. If it won't start with our file, take the
# file back out and start it again, so a mistake here never leaves their broker down.
restart_broker() {
  systemctl reset-failed mosquitto 2>/dev/null || true
  systemctl restart mosquitto && return 0
  mv /etc/mosquitto/conf.d/ultralights.conf "$BACKUP/ultralights.conf.rejected"
  systemctl reset-failed mosquitto 2>/dev/null || true
  systemctl restart mosquitto || warn "Mosquitto doesn't start even without our listener"
  die "Mosquitto rejected conf.d/ultralights.conf; it was moved to $BACKUP/ultralights.conf.rejected and the broker restarted without it. Look in /var/log/mosquitto/mosquitto.log (its errors go there, not to the journal) and journalctl -k | grep DENIED (AppArmor)"
}

say "systemd units"
for u in ultralights-api.service ultralights-scheduler.service; do
  sed -e "s|__REPO__|$REPO|g" -e "s|__VENV__|$VENV|g" -e "s|__API_PORT__|$API_PORT|g" "$REPO/deploy/systemd/$u" >"/etc/systemd/system/$u"
done
install -m 0644 "$REPO/deploy/systemd/ultralights-broker-sync.service" "$REPO/deploy/systemd/ultralights-broker-sync.path" /etc/systemd/system/
if [ "$SYSTEMD" = 1 ]; then
  systemctl daemon-reload
  systemctl enable -q mosquitto ultralights-api ultralights-scheduler ultralights-broker-sync.path
  if [ "$NO_START" = 0 ]; then
    restart_broker
    systemctl start ultralights-broker-sync.path
    systemctl restart ultralights-api ultralights-scheduler
  fi
else
  warn "no systemd here: units installed but not started"
fi

if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q '^Status: active'; then
  say "firewall: allowing 80, 443 and 8883"
  ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null; ufw allow 8883/tcp >/dev/null
fi

# ---------------------------------------------------------------- first admin
admins="$(sudo -u postgres psql -d ultralights -qtAc "select count(*) from users where is_admin" 2>/dev/null || echo 0)"
if [ "$admins" = 0 ] && [ "$SKIP_ADMIN" = 0 ] && [ -t 0 ]; then
  bold "Create your admin account"
  read -rp "Username: " admin_user
  ulctl create-admin "$admin_user"
elif [ "$admins" = 0 ]; then
  warn "no admin account yet: sudo -u $APP_USER env UL_ENV_FILE=$ENV_FILE $VENV/bin/ulctl create-admin <name>"
fi

# ---------------------------------------------------------------- summary
bold "Done."
cat <<EOF
   Web UI        https://$DOMAIN
   Broker        $DOMAIN:8883 (mutual TLS)
   API           127.0.0.1:$API_PORT  (journalctl -u ultralights-api -f)
   Settings      $ENV_FILE
   Device CA     $PKI
   Data          $DATA
   v5 backups    $BACKUP
EOF
if [ -f "$PKI/root-ca.key" ]; then
  warn "the device root CA key is still on this server ($PKI/root-ca.key)."
  echo "   Copy it offline, then remove it:"
  echo "     sudo env UL_ENV_FILE=$ENV_FILE $VENV/bin/ulctl ca export-root /media/<usb>"
  echo "     sudo -u $APP_USER env UL_ENV_FILE=$ENV_FILE $VENV/bin/ulctl ca remove-root-key --yes"
fi
echo "   Router: forward 443 and 8883 to this host. LAN DNS (192.168.1.99): $DOMAIN -> this host's LAN address."
