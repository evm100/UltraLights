# UltraLights (v6 rewrite, branch `beta`)

## Sources of truth

- `docs/blueprint.html` is the design. If code and blueprint disagree, raise it; when a decision changes, update the blueprint in the same change.
- `docs/hardware-notes.md` holds verified ESP32-C6 facts (datasheet/TRM). Prefer it over memory for chip details.

## The v5 code is history

- v5 lives only at the tag `v5-final`. Do not read, port or copy v5 code unless the task explicitly asks for it.
- The useful v5 lessons are already extracted in the blueprint's "Lessons from v5" section. If you do consult v5 (`git show v5-final:<path>`), re-justify anything you take against the blueprint, because many v5 choices came from S3-era constraints or inexperience.

## Firmware conventions (`firmware/`)

- ESP-IDF v6.1. Two first-class targets: `esp32c6` (the PCB) and `esp32c3` (legacy and hand-built nodes). Every component must build for both. Activate with `source ~/.espressif/tools/activate_idf_v6.1.sh`. That script defines `idf.py` as an alias, which non-interactive shells don't expand, so in scripts and tool calls run `python "$IDF_PATH/tools/idf.py" ...` in the same shell after sourcing.
- `main/` only sets boot order: no feature logic, no `#ifdef`s on components.
- Every feature, driver, protocol or diagnostic is its own component in `components/`, named `ul_<name>`, with `CMakeLists.txt`, `Kconfig` (enable switch + options), `include/` (public API), `src/` and `test/`.
- Drivers implement `ul_core`'s channel interface and register with it. Services reach hardware only through `ul_core`. No component depends on `main`.
- Pins are data: no component hard-codes a GPIO. Each node carries a board descriptor (chip, channels, pins, reset button) in its own partition, written by the admin node builder at flash time. Output mapping (grouping, names, limits) comes from the server at run time.
- Firmware images are per chip + feature profile (sdkconfig fragments in `profiles/`), never per node. Chip differences go in per-chip source files inside a component, not scattered `#ifdef`s.
- Nodes reach the server by name (`lights.evm100.org`), never a hard-coded IP. MQTT is always mutual TLS on 8883.
- Pure logic (effects, contract encoding, state) must build and run as host tests.

## Working style

- Spec before code: check the blueprint, then build small end-to-end slices with a clear acceptance check.
- Message formats come from `contracts/`; don't hand-roll JSON shapes in firmware, server or web.
- Never commit secrets (keys, certificates, `.env`, databases).
