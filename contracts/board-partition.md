# The `ul_board` partition

Every node has a small data partition named `ul_board` (type `data`, subtype `0x40`, at least 4 KB). It holds the board descriptor and the one-time provisioning data. The node builder writes it, along with the firmware image, over Web Serial. A CLI fallback is `firmware/tools/` plus `contracts/tools/ulboard.py`. Firmware only ever reads it.

The partition's offset and size are recorded in each firmware package's `manifest.json` (`board_partition`), so the builder never hard-codes them. Firmware should keep the offset the same across all partition tables so a descriptor can be rewritten without knowing the flash size. The proposed layout is: `nvs` at 0x9000 (16 KB), `otadata` at 0xD000, `ul_board` at 0xF000 (4 KB), and apps from 0x10000. The firmware track owns the final layout.

## Format

All integers are little endian.

| Offset | Size | Field |
|---|---|---|
| 0 | 4 | magic `ULBD` |
| 4 | 2 | format, `1` |
| 6 | 2 | flags, `0` (reserved) |
| 8 | 4 | payload length in bytes |
| 12 | 4 | CRC-32 of the payload (IEEE 802.3, same as `zlib.crc32`) |
| 16 | n | payload: UTF-8 JSON, valid against `schemas/partition.schema.json` |
| 16 + n | rest | `0xFF` |

- The builder writes compact, key-sorted JSON (`ulboard.canonical`), so the same descriptor always gives the same CRC.
- The CRC, as 8 lower-case hex digits, is `board_crc` in `info` and in the enrollment request.
- Firmware must check magic, format, length and CRC before parsing, then validate the board against `chips.json` rules. It refuses to drive any pin that fails, and reports why in `info.config_error` and an `evt` error. It also checks that `board.chip` matches the chip it's running on.
- An erased or invalid partition means "no board". The node then opens the setup page with a notice, and drives no outputs.

## Test vector

`examples/vectors/ul_board.c3-bench.bin` is the exact 4096-byte blob for `examples/partition/c3-bench.json`. Its CRC is in `ul_board.c3-bench.crc` (`f7f67a5a`). The firmware host tests decode it, and the server tests encode `c3-bench.json` and compare byte for byte.
