# UltraLights

Self-hosted lighting and switched-load control: custom ESP32-C6 nodes, a web app with an admin panel, and workers for firmware builds and (later) AI scenes.

This branch (`beta`) is a ground-up rewrite. The previous generation (ESP32-S3 `UltraNodeV5` firmware and its FastAPI server) is preserved at the tag `v5-final`.

## Start here

- [`docs/blueprint.html`](docs/blueprint.html): the design. Architecture, device contract, firmware components, interface, roles, admin panel, provisioning, phases and open questions.
- [`docs/hardware-notes.md`](docs/hardware-notes.md): verified ESP32-C6 facts and board decisions.

## Layout

```
contracts/   device contract schemas, shared by firmware, server and web
firmware/    ESP-IDF 6.1 project for the ESP32-C6 (slim main + ul_* components)
server/      API, auth, scheduler, MQTT bridge
web/         user UI and admin panel
workers/     build worker, AI worker (later)
bridges/     OpenRGB bridge agent, others later
hardware/    PCB design
deploy/      broker, proxy and database configuration
docs/        blueprint and hardware notes
```

Folders are created as each phase starts, so some of these don't exist yet.
