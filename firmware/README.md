# UltraLights firmware

ESP-IDF 6.1 project for every node: the ESP32-C6 PCB, the ESP32-C3 legacy nodes and
hand-built prototypes. One tree, a few images (chip + feature profile); pins come from
the board descriptor each node carries, never from the build. Design:
[`docs/blueprint.html#firmware`](../docs/blueprint.html). Messages: [`contracts/`](../contracts).

## Commands

```sh
firmware/tools/build.sh c3-lights          # build -> firmware/build/c3-lights
firmware/tools/build.sh c6-lights
firmware/tools/build.sh c3-lights menuconfig   # any idf.py action
firmware/tools/host_test.sh                # pure-C tests on this machine (no chip)
firmware/tools/qemu_test.sh                # on-chip crypto tests in QEMU (C3), see the script for setup
firmware/tools/package.py c3-lights        # -> firmware/build/packages/c3-lights-<version>.zip
firmware/tools/flash.py --dry-run firmware/build/packages/c3-lights-0.1.0.zip --board-json node.json
```

`tools/idfenv.sh` activates ESP-IDF from the EIM install (`~/.espressif/tools/activate_idf_v6.1.sh`)
in a way that works inside scripts; source it for an `idf` shell function.

The version comes from `version.txt`. Managed components (cJSON, esp-mqtt left ESP-IDF in v6)
are pinned in each component's `idf_component.yml`, with one lock file per chip
(`dependencies.esp32c3.lock`, `dependencies.esp32c6.lock`), both committed.

## Profiles

| Profile | Chip | Flash | Partition table |
|---|---|---|---|
| `c3-lights` | ESP32-C3 | 4 MB | `partitions/4mb.csv` |
| `c6-lights` | ESP32-C6 | 8 MB | `partitions/8mb.csv` |

A profile is `sdkconfig.defaults` plus `profiles/<name>.defaults`. Each component has a
Kconfig enable switch (`CONFIG_UL_<NAME>_ENABLE`), so a profile can drop a feature
without touching `main`.

## Partition layout

The partition table sits at **0xC000** (not the default 0x8000), leaving 48 KB for the
bootloader. Secure boot and flash encryption make it larger, and the table can't move
over OTA later. `nvs`, `otadata` and `ul_board` have the same offsets in every table,
so a descriptor can be rewritten without knowing the flash size.

| Name | Type | Offset | Size | 4 MB (C3) | 8 MB (C6) |
|---|---|---|---|---|---|
| bootloader | | 0x0 | up to 48 KB | | |
| partition table | | 0xC000 | 4 KB | | |
| `nvs` | data/nvs | 0xD000 | 24 KB | yes | yes |
| `otadata` | data/ota | 0x13000 | 8 KB | yes | yes |
| `ul_board` | data/0x40 | **0x15000** | 4 KB | yes | yes |
| (free) | | 0x16000 | 40 KB | | |
| `ota_0` | app | 0x20000 | | 1.875 MB | 3 MB |
| `ota_1` | app | | | 0x200000, 1.875 MB | 0x320000, 3 MB |
| `coredump` | data/coredump | | 64 KB | 0x3E0000 | 0x620000 |
| `ul_clips` | data/0x41 | | | | 0x630000, 1.8 MB (effect clips, later) |

The firmware package's `manifest.json` records every offset, including `board_partition`,
so the node builder never hard-codes them.

## Layout

```
main/app_main.c        calls ul_core_boot(), nothing else
components/
  ul_contract/         JSON encode/decode of every contract message (pure C, host-tested)
  ul_board/            reads the ul_board partition, checks pins (rules generated from contracts/chips.json)
  ul_core/             device model: modules, channels, outputs, mapping, state, fades, NVS
  ul_pwm/              LEDC; per-chip fade file (C6 gamma multi-fade, C3 chained hardware fades)
  ul_argb/             RMT strips, solid color with fades; per-chip RMT layout
  ul_switch/           relay/solenoid GPIOs
  ul_servo/            servo button presser (LEDC, 50 Hz) for cycle outputs
  ul_status/           status LED patterns
  ul_wifi/             station, backoff, DNS override, SNTP
  ul_provision/        setup access point, captive DNS, setup page, reset button
  ul_identity/         on-chip P-256 key, CSR, HTTPS enrollment, certificate in NVS
  ul_mqtt/             mutual-TLS MQTT, all contract topics, commands
  ul_health/           metrics for the health topic
  ul_ota/              OTA command, rollback confirmation
test/host/             CMake + Unity host test runner (tests live in components/*/test)
test/qemu/             IDF app with on-chip tests for QEMU
tools/                 build, test, package, flash, chip-rule generator
```

### How a component plugs in

Drivers and services register themselves with `UL_MODULE_REGISTER(id, name, stage, order, init)`
and are built with `WHOLE_ARCHIVE`, so `main` never lists them. `ul_core_boot()` runs:

1. NVS (erased if unreadable, e.g. after a v5 node is converted), netif, event loop.
2. `ul_board_load()`: magic, format, CRC, schema, chip match, pin rules. A rejected board drives no outputs.
3. Driver stage (`ul_pwm` 10, `ul_servo` 15, `ul_argb` 20, `ul_switch` 30): each registers the channels it found.
4. Saved config (or the default mapping), saved state, `power_on`, first render.
5. Service stage: `ul_status` 0, `ul_wifi` 10, `ul_health` 15, `ul_provision` 20, `ul_identity` 30, `ul_mqtt` 40, `ul_ota` 50.

Services talk through `ul_core` events (`UL_EV_NET_UP`, `UL_EV_IDENTITY_READY`, `UL_EV_STATE`, ...).

### Fades

`ul_core` renders a state change into up to 8 points along the way (level, CCT and color
interpolated, then put through the CIE curve), so long fades look even. Short changes
(live sliders, under 300 ms) are one point. Then:

- **PWM**: one point is a single LEDC hardware fade. Several points become one LEDC gamma
  multi-fade on the **C6**, which needs no CPU. On the **C3**, whose LEDC has no gamma RAM,
  they are chained linear hardware fades started by a timer.
- **Strips**: a render task interpolates between points at up to 60 fps, idle otherwise.

## Conventions this code relies on

- No component hard-codes a GPIO; all pins come from the descriptor.
- Chip differences sit in per-chip files picked by `${IDF_TARGET}`: `ul_board/src/chip/`,
  `ul_pwm/src/chip/`, `ul_argb/src/chip/`, plus the partition tables and profiles.
- Server-to-device messages ignore unknown fields (forward compatible); board descriptors are strict.
- `seq` is written as integer text (cJSON would print 16-digit numbers in exponent form).

## Status

**Verified**
- Both profiles build without warnings.
- Host tests pass: every contract example decodes or is rejected as expected; encoders
  reproduce the examples; every encoded message passes the schemas; the partition test
  vector matches; pin rules match `pins.py`.
- QEMU (C3): on-chip key generation and CSR pass, and openssl verifies the CSR.
- QEMU (C3): the full app boots through board load, all drivers, config and state, up to Wi-Fi start.
  QEMU has no radio.

**Not yet tested on hardware**
- LEDC frequencies and fades, including the C6 multi-fade and the C3 chained fades.
- RMT strip timing and the 1+3 block split.
- Wi-Fi, the DNS override, and the setup page on real phones.
- Enrollment and MQTT against the real server.
- OTA.

Each component's `test/README.md` lists the on-target checks to run on the bench.
