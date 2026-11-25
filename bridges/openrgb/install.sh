#!/usr/bin/env bash
# Install or update the UltraLights OpenRGB agent. Run as root on the machine that should run it
# (the Proxmox host: it is on whenever the PC is, and reaches both OpenRGB servers).
#
#   sudo bridges/openrgb/install.sh --server lights.evm100.org --device ul-xxxxxx --token TOKEN \
#        --source pve=127.0.0.1,"Proxmox host" --source vm=192.168.1.41,"GPU VM"
#
# The admin panel (Nodes > Add PC) prints this line with the device id and one-time token filled in.
# Re-running is safe: code is updated, an existing config is kept, and enrollment happens only
# when --token is given.
#
#   --source KEY=HOST[:PORT][,NAME]  an OpenRGB SDK server (repeat; used only to write a new config)
#   --server-ca FILE                 dev server only: its TLS CA
#   --connect IP                     dev server only: dial this IP, check the certificate against --server
#   --check                          show what would change, change nothing
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX=/opt/ul-openrgb
ETC=/etc/ul-openrgb
CONF=$ETC/agent.toml
STATE=/var/lib/ul-openrgb
UNIT=/etc/systemd/system/ul-openrgb.service
USER_NAME=ul-openrgb

SERVER="" DEVICE="" TOKEN="" SERVER_CA="" CONNECT="" CHECK=0
SOURCES=()
while [ $# -gt 0 ]; do
  case "$1" in
    --server) SERVER="$2"; shift 2 ;;
    --device) DEVICE="$2"; shift 2 ;;
    --token) TOKEN="$2"; shift 2 ;;
    --source) SOURCES+=("$2"); shift 2 ;;
    --server-ca) SERVER_CA="$2"; shift 2 ;;
    --connect) CONNECT="$2"; shift 2 ;;
    --check) CHECK=1; shift ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    *) echo "unknown option $1 (see --help)" >&2; exit 2 ;;
  esac
done

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m !\033[0m %s\n' "$*" >&2; }
die() { printf '\033[31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }
port_open() { timeout 3 bash -c "</dev/tcp/$1/$2" 2>/dev/null; }

[ "$(id -u)" = 0 ] || die "run as root (sudo)"
[ -f "$HERE/pyproject.toml" ] || die "run from a checkout: $HERE has no pyproject.toml"
command -v python3 >/dev/null || die "python3 is missing (apt-get install python3)"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || die "Python 3.11 or newer is needed (found $(python3 -V))"

# ---------------------------------------------------------------- what's here already
say "checking this machine ($(hostname))"
for old in rgb-control ul-bridge; do
  if systemctl list-unit-files "$old.service" >/dev/null 2>&1 && systemctl cat "$old.service" >/dev/null 2>&1; then
    state=$(systemctl is-active "$old" 2>/dev/null || true)
    warn "found the old $old.service ($state). Left alone. Once the agent works, retire it:"
    warn "    systemctl disable --now $old    (rgb-control and the agent would fight over the same LEDs)"
  fi
done
if port_open 127.0.0.1 6742; then
  echo "    OpenRGB SDK server on 127.0.0.1:6742: up"
else
  warn "no OpenRGB SDK server on 127.0.0.1:6742. Start it (systemctl status openrgb) before discovery,"
  warn "or this machine's parts won't be found. The RAM needs i2c-dev and i2c-piix4 (AMD) loaded."
fi
[ -f "$CONF" ] && echo "    existing config: $CONF (kept)"
[ -f "$STATE/device.crt" ] && echo "    existing identity: $STATE/device.crt"

if [ "$CHECK" = 1 ]; then
  echo
  echo "Would: install the agent into $PREFIX (venv), link /usr/local/bin/ul-openrgb,"
  [ -f "$CONF" ] || echo "       write $CONF from discovery (sources: ${SOURCES[*]:-none given})"
  [ -n "$TOKEN" ] && echo "       enroll as $DEVICE with $SERVER"
  echo "       install and (re)start $UNIT"
  [ -x "$PREFIX/venv/bin/ul-openrgb" ] && [ -f "$CONF" ] && "$PREFIX/venv/bin/ul-openrgb" --config "$CONF" check || true
  exit 0
fi

# ---------------------------------------------------------------- code
if ! python3 -c 'import ensurepip, venv' 2>/dev/null; then
  say "installing python3-venv"
  apt-get update -qq && apt-get install -y -qq python3-venv
fi
say "installing the agent into $PREFIX"
id "$USER_NAME" >/dev/null 2>&1 || useradd --system --home-dir "$STATE" --no-create-home --shell /usr/sbin/nologin "$USER_NAME"
install -d -m 0755 "$PREFIX" "$ETC"
install -d -m 0750 -o "$USER_NAME" -g "$USER_NAME" "$STATE"
rm -rf "$PREFIX/src.new"
mkdir -p "$PREFIX/src.new"
cp -r "$HERE/pyproject.toml" "$HERE/ul_openrgb" "$HERE/README.md" "$PREFIX/src.new/"
rm -rf "$PREFIX/src" && mv "$PREFIX/src.new" "$PREFIX/src"
[ -x "$PREFIX/venv/bin/python" ] || python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install -q --upgrade pip
"$PREFIX/venv/bin/pip" install -q --upgrade "$PREFIX/src"
ln -sf "$PREFIX/venv/bin/ul-openrgb" /usr/local/bin/ul-openrgb
echo "    $("$PREFIX/venv/bin/ul-openrgb" --help | head -1 | sed 's/^usage: //')"

# ---------------------------------------------------------------- config
if [ ! -f "$CONF" ]; then
  [ ${#SOURCES[@]} -gt 0 ] || SOURCES=("pve=127.0.0.1,$(hostname)")
  args=(--config "$CONF" discover --write)
  for s in "${SOURCES[@]}"; do args+=(--source "$s"); done
  [ -n "$SERVER" ] && args+=(--server "$SERVER")
  say "discovering OpenRGB devices"
  ul-openrgb "${args[@]}" || die "discovery found nothing to control. Fix the OpenRGB servers above and re-run."
  chmod 0644 "$CONF"
fi
if [ -n "$SERVER_CA" ] || [ -n "$CONNECT" ]; then
  # Dev server settings go at the top of the file, before the first [table].
  python3 - "$CONF" "$SERVER_CA" "$CONNECT" <<'EOF'
import re, sys
path, ca, connect = sys.argv[1:]
t = open(path).read()
for k, v in (("server_ca", ca), ("connect", connect)):
    if not v:
        continue
    t = re.sub(rf"^{k} = .*\n", "", t, flags=re.M)
    m = re.search(r"^device_id.*$|^server = .*$", t, flags=re.M)  # right under the server settings
    i = m.end() if m else 0
    t = t[:i] + f'\n{k} = "{v}"' + t[i:]
open(path, "w").write(t)
EOF
fi

# ---------------------------------------------------------------- identity
if [ -n "$TOKEN" ]; then
  [ -n "$DEVICE" ] || die "--token needs --device"
  say "enrolling as $DEVICE"
  eargs=(--config "$CONF" enroll --device "$DEVICE" --token "$TOKEN")
  [ -n "$SERVER" ] && eargs+=(--server "$SERVER")
  ul-openrgb "${eargs[@]}"
elif [ ! -f "$STATE/device.crt" ]; then
  warn "not enrolled yet: re-run with --server, --device and --token from the admin panel (Nodes > Add PC)."
fi
chown -R "$USER_NAME:$USER_NAME" "$STATE"

# ---------------------------------------------------------------- service
if [ ! -d /run/systemd/system ]; then
  warn "systemd isn't running here (a container?): skipping the service. Start the agent with: ul-openrgb run"
  ul-openrgb --config "$CONF" check || true
  exit 0
fi
say "installing $UNIT"
cat >"$UNIT" <<EOF
[Unit]
Description=UltraLights OpenRGB agent
Documentation=file://$PREFIX/src/README.md
Wants=network-online.target
After=network-online.target openrgb.service

[Service]
User=$USER_NAME
ExecStart=$PREFIX/venv/bin/ul-openrgb --config $CONF run
Restart=always
RestartSec=3
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ReadWritePaths=$STATE

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable ul-openrgb >/dev/null 2>&1
if [ -f "$STATE/device.crt" ]; then
  systemctl restart ul-openrgb
  sleep 3
  systemctl is-active --quiet ul-openrgb && echo "    ul-openrgb is running" || warn "ul-openrgb isn't running: journalctl -u ul-openrgb -n 30"
fi

echo
say "checking"
ul-openrgb --config "$CONF" check || warn "some checks failed (see above)"
cat <<EOF

Done. Useful:
  journalctl -u ul-openrgb -f          agent log
  ul-openrgb discover                  list OpenRGB devices again (edit $CONF to change parts)
  systemctl restart ul-openrgb         after editing $CONF
EOF
