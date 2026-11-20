#!/usr/bin/env bash
# Run the firmware host tests: build the pure-C parts with Unity, run them,
# check every encoded message against contracts/schemas, and check that the
# generated chip rules match contracts/chips.json.
#   firmware/tools/host_test.sh
set -euo pipefail
FW="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="$FW/build/host"

python3 "$FW/tools/gen_chip_rules.py" --check
rm -rf "$BUILD/encoded"
cmake -S "$FW/test/host" -B "$BUILD" -DCMAKE_BUILD_TYPE=Debug >/dev/null
cmake --build "$BUILD" -j >/dev/null
"$BUILD/ul_host_tests"
python3 "$FW/tools/check_encoded.py" "$BUILD/encoded"
