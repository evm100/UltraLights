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

## Firmware build prerequisites

The ESP-IDF toolchain expects a number of helper modules to be available on the
Python path when you call `idf.py`. The upstream installer wires everything up
automatically, but our containerized development environment does not ship with
those extras pre-installed. If you see an error such as

```
No module named 'esp_idf_monitor'
```

install the optional firmware dependencies before invoking any `idf.py`
commands:

```
python -m pip install -r UltraNodeV5/requirements-dev.txt
```

Make sure you also activate an ESP-IDF environment (so `idf.py` resolves the
cross-compilers) before running `idf.py build` or `idf.py -p <port> flash`.

### Resetting a node for reprovisioning

If the installation Wi-Fi changes, press and hold the ESP32's **BOOT** button
for about five seconds. The firmware monitors that GPIO at runtime; once the
hold timer elapses it erases the saved SSID/password from NVS and reboots. On
the next boot the node falls back to the captive portal provisioning flow so new
credentials can be entered.
