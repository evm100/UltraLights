#!/usr/bin/env bash
# Run the QEMU tests (ESP32-C3): on-chip key generation and CSR with mbedTLS 4/PSA,
# then check the CSR's signature with openssl.
#
# Needs qemu-system-riscv32 from Espressif:
#   python "$IDF_PATH/tools/idf_tools.py" install qemu-riscv32   (and: sudo apt install libslirp0)
# or point UL_QEMU at the binary (and LD_LIBRARY_PATH at libslirp if it isn't installed).
set -euo pipefail
FW="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
set +eu
# shellcheck source=/dev/null
source "$FW/tools/idfenv.sh"
set -eu
QEMU="${UL_QEMU:-qemu-system-riscv32}"
B="$FW/build/qemu"
OUT="$B/run"
mkdir -p "$OUT"

python "$IDF_PATH/tools/idf.py" -C "$FW/test/qemu" -B "$B" -DSDKCONFIG="$B/sdkconfig" build >"$OUT/build.log" 2>&1 ||
    { tail -30 "$OUT/build.log"; exit 1; }
(cd "$B" && python -m esptool --chip esp32c3 merge-bin --pad-to-size 4MB -o "$OUT/flash.bin" @flash_args >/dev/null)
rm -f "$OUT/serial.log"
timeout 60 "$QEMU" -nographic -machine esp32c3 -drive "file=$OUT/flash.bin,if=mtd,format=raw" \
    -serial "file:$OUT/serial.log" -monitor none >/dev/null 2>&1 &
pid=$!
for _ in $(seq 60); do
    sleep 1
    grep -q UL_QEMU_DONE "$OUT/serial.log" 2>/dev/null && break
done
kill "$pid" 2>/dev/null || true

log="$(sed 's/\x1b\[[0-9;]*m//g' "$OUT/serial.log")"
grep -E ':(PASS|FAIL)' <<<"$log" || true
grep -q '0 Failures' <<<"$log" || { echo "QEMU tests failed; log: $OUT/serial.log"; exit 1; }
sed -n '/UL_CSR_BEGIN/,/UL_CSR_END/p' <<<"$log" | sed '1d;$d' | tr -d '\r' >"$OUT/csr.pem"
openssl req -in "$OUT/csr.pem" -noout -verify -subject
echo "QEMU tests passed"
