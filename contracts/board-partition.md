# The `ul_board` partition

Every node has a small data partition named `ul_board` (type `data`, subtype `0x40`, at least 4 KB). It holds the board descriptor and the one-time provisioning data. The node builder writes it, along with the firmware image, over Web Serial. A CLI fallback is `firmware/tools/` plus `contracts/tools/ulboard.py`. Firmware only ever reads it.

The partition's offset and size are recorded in each firmware package's `manifest.json` (`board_partition`), so the builder never hard-codes them. The offset is the same in every partition table, so a descriptor can be rewritten without knowing the flash size.

Layout (`firmware/partitions/`, details in `firmware/README.md`):

| Item | Offset | Size |
|---|---|---|
| Partition table | 0xC000 | 4 KB |
| `nvs` | 0xD000 | 24 KB |
| `otadata` | 0x13000 | 8 KB |
| `ul_board` | 0x15000 | 4 KB |
| Apps | from 0x20000 | |

The partition table sits at 0xC000 rather than the default 0x8000 so a secure-boot bootloader has room later.

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
- Firmware must check magic, format, length and CRC before parsing, then validate the board against `chips.json` rules. If any pin rule fails, or `board.chip` doesn't match the chip it runs on, the firmware rejects the whole board and drives no outputs. `info.config_error` then reads `board rejected: <codes>`, using the same codes as `tools/pins.py`. The provisioning data still works, so a rejected node can enroll and report its error. Pin warnings are only logged.
- An erased or invalid partition means "no board". The node then opens the setup page with a notice, and drives no outputs.

## Test vector

`examples/vectors/ul_board.c3-bench.bin` is the exact 4096-byte blob for `examples/partition/c3-bench.json`. Its CRC is in `ul_board.c3-bench.crc` (`f7f67a5a`). The firmware host tests decode it, and the server tests encode `c3-bench.json` and compare byte for byte.
