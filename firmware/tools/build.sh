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
idf() {
    python "$IDF_PATH/tools/idf.py" -B "$B" \
        -DIDF_TARGET="$TARGET" \
        -DSDKCONFIG="$B/sdkconfig" \
        -DSDKCONFIG_DEFAULTS="$FW/sdkconfig.defaults;$FRAG" \
        "$@"
}
# ESP-IDF finds components only when a build directory is configured, so a component added
# since then (a new ul_* folder) would be silently left out of the image. Reconfigure when
# the set of component folders differs from the last successful build.
STAMP="$B/.ul_components"
COMPONENTS="$(cd "$FW/components" && printf '%s\n' */ | tr -d / | tr '\n' ' ')"
if [[ -f "$B/CMakeCache.txt" && "$(cat "$STAMP" 2>/dev/null)" != "$COMPONENTS" ]]; then
    echo "components changed since the last build of $PROFILE: reconfiguring" >&2
    idf reconfigure
fi
idf "${args[@]}"
echo "$COMPONENTS" > "$STAMP"
