#!/usr/bin/env bash
# Put the host back the way v5 needs it, using the backups install.sh made:
#   - stop and disable the UltraLights units (ours to stop)
#   - remove the UltraLights nginx site and restore the v5 site(s) from /var/backups/ultralights-v5/nginx
#   - remove the UltraLights Mosquitto listener (frees port 8883) and restart Mosquitto if it runs
# It does not delete the new database, CA or data; run install.sh again to switch back.
# Afterwards start v5 the way you stopped it (the preflight printed those commands).
#
#   sudo ./deploy/rollback-v5.sh
set -euo pipefail

BACKUP=/var/backups/ultralights-v5
[ "$(id -u)" = 0 ] || { echo "run as root: sudo $0" >&2; exit 1; }
[ -d "$BACKUP" ] || { echo "no backups in $BACKUP; nothing to roll back" >&2; exit 1; }
read -rp "Stop UltraLights and restore the v5 nginx site? [y/N] " ok
[ "$ok" = y ] || [ "$ok" = Y ] || exit 1

if [ -d /run/systemd/system ]; then
  systemctl disable --now ultralights-api ultralights-scheduler ultralights-broker-sync.path 2>/dev/null || true
fi

rm -f /etc/nginx/sites-enabled/ultralights
if [ -f "$BACKUP/nginx/links.txt" ]; then
  while IFS= read -r line; do
    link="${line%% -> *}"
    target="${line##* -> }"
    name="$(basename "$link")"
    install -d "$(dirname "$target")"
    [ -e "$target" ] || cp -a "$BACKUP/nginx/$name" "$target"
    [ "$link" = "$target" ] || ln -sf "$target" "$link"
    echo "==> restored $link"
  done <"$BACKUP/nginx/links.txt"
fi
nginx -t && systemctl reload nginx

if [ -f /etc/mosquitto/conf.d/ultralights.conf ]; then
  mv /etc/mosquitto/conf.d/ultralights.conf "$BACKUP/ultralights.conf.disabled"
  if systemctl is-active --quiet mosquitto; then systemctl restart mosquitto; fi
  echo "==> UltraLights broker listener removed (port 8883 is free)"
fi

cat <<EOF
Rolled back. Now start v5 again, for example:
  docker start <v5 mosquitto container>      # docker ps -a to find it
  (start the v5 API the way it ran before: systemd unit, tmux, ...)
To return to the new server later: sudo ./deploy/install.sh
EOF
