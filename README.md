# UltraLights Setup

This repository contains two cooperating projects:

- `Server/` – FastAPI-based control surface for houses, rooms, presets and OTA
  distribution.
- `UltraNodeV5/` – ESP-IDF firmware for the UltraNode controllers.

Most provisioning work touches both halves of the stack, so refer back to the
server documentation whenever you onboard a new controller.

## Provisioning checklist

1. Use the admin UI (or edit `Server/app/device_registry.json`) to register the
   house, room and node.
2. Before flashing firmware, review the [house-prefixed node ID
   guide](Server/docs/node-ids.md) and mirror the generated identifier in
   `UltraNodeV5/sdkconfig.defaults` and your OTA artifact paths.
3. Build and flash `UltraNodeV5`, then upload the binary to the OTA location
   referenced in the config.
4. Reload the server to pick up registry changes and confirm the node reports in
   under the expected `ul/<node-id>/...` topics.

Following the checklist keeps the registry, firmware and OTA distribution in
sync so devices can report status and accept updates immediately after they boot
on the network.
