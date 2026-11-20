#!/usr/bin/env bash
# Build one firmware profile into build/<profile>.
#   firmware/tools/build.sh c3-lights            (or c6-lights)
#   firmware/tools/build.sh c3-lights menuconfig (extra args go to idf.py; default "build")
set -euo pipefail
FW="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="${1:?usage: build.sh <profile> [idf.py args]}"
shift
FRAG="$FW/profiles/$PROFILE.defaults"
[[ -f "$FRAG" ]] || { echo "no profile $FRAG" >&2; exit 1; }
TARGET="$(sed -n 's/^CONFIG_IDF_TARGET="\(.*\)"/\1/p' "$FRAG")"
# The activation script isn't written for `set -eu`.
set +eu
# shellcheck source=/dev/null
source "$FW/tools/idfenv.sh"
set -eu
[[ -n "${IDF_PATH:-}" ]] || { echo "ESP-IDF activation failed" >&2; exit 1; }
B="$FW/build/$PROFILE"
cd "$FW"
args=("$@")
[[ ${#args[@]} -eq 0 ]] && args=(build)
python "$IDF_PATH/tools/idf.py" -B "$B" \
    -DIDF_TARGET="$TARGET" \
    -DSDKCONFIG="$B/sdkconfig" \
    -DSDKCONFIG_DEFAULTS="$FW/sdkconfig.defaults;$FRAG" \
    "${args[@]}"
