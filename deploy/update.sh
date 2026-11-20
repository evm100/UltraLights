#!/usr/bin/env bash
# Update a running install: pull, then re-run install.sh (migrations, web build, configs,
# restart of the UltraLights units). Same options as install.sh, e.g. --domain.
#
#   sudo ./deploy/update.sh
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ "$(id -u)" = 0 ] || { echo "run as root: sudo $0" >&2; exit 1; }

owner="$(stat -c %U "$REPO")"
echo "==> pulling $(git -C "$REPO" rev-parse --abbrev-ref HEAD) as $owner"
before="$(git -C "$REPO" rev-parse --short HEAD)"
if [ "$owner" = root ]; then git -C "$REPO" pull --ff-only; else sudo -u "$owner" git -C "$REPO" pull --ff-only; fi
after="$(git -C "$REPO" rev-parse --short HEAD)"
echo "    $before -> $after"

# install.sh re-execs from the freshly pulled tree.
exec "$REPO/deploy/install.sh" --skip-admin "$@"
