# External Node Bridge — Setup Guide

This directory contains `openrgb_bridge.py`, an MQTT bridge that makes a
non-ESP32 machine (e.g. a Debian 13 PC running OpenRGB) behave as a
first-class UltraLights node.  The server sees it as a normal node with a
single RGB strip — presets, the color wheel, motion automation, and
online/offline status all work without any special handling.

---

## Prerequisites

### On the UltraLights server
1. Register the external node via **Server Administration → External nodes**.
   Pick the house and room, give it a display name, and click
   *Register external node*.
2. Copy the **node ID** shown after registration.  You will need it to
   configure the bridge.

### On the Debian 13 machine
- Python 3.11+ (ships with Trixie)
- Network access to the MQTT broker (same broker the UltraLights server
  uses — see `BROKER_HOST` / `BROKER_PORT` in `Server/.env`)

Install the two Python dependencies:

```bash
pip install paho-mqtt requests
```

`requests` is only needed if you have a local FastAPI app controlling
OpenRGB.  If you handle light control entirely inside the bridge's
`on_message` callback, you can drop the `requests` dependency.

---

## MQTT broker connection

The bridge connects to the **same Mosquitto broker** as the UltraLights
server.  You need four pieces of information from the server's `.env`
(or from whoever administers the broker):

| What                | Server `.env` key      | Bridge flag / env var    | Default       |
|---------------------|------------------------|--------------------------|---------------|
| Broker hostname/IP  | `BROKER_HOST`          | `--broker-host` / `UL_BROKER_HOST` | `127.0.0.1` |
| Broker port         | `BROKER_PORT`          | `--broker-port` / `UL_BROKER_PORT` | `1883`       |
| Username (optional) | `BROKER_USERNAME`      | `--broker-user` / `UL_BROKER_USER` | *(none)*     |
| Password (optional) | `BROKER_PASSWORD`      | `--broker-pass` / `UL_BROKER_PASS` | *(none)*     |

If the broker is on the same LAN but not on `localhost`, use the LAN IP
(e.g. `192.168.1.50`).

### TLS
The current bridge uses **unencrypted MQTT** (port 1883), matching the
server's default `mqtt_tls.py` configuration.  If you later enable TLS on
the broker, you will need to add `client.tls_set(...)` before
`client.connect()` in the bridge script and point it at the CA certificate.

### Mosquitto ACLs
If your Mosquitto config uses ACLs, ensure the bridge's credentials are
allowed to:
- **Subscribe** to `ul/<node-id>/cmd/#`
- **Publish** to `ul/<node-id>/evt/status`

---

## Running the bridge

### Minimal (all defaults, broker on localhost)
```bash
python openrgb_bridge.py --node-id <paste-node-id-here>
```

### Full example
```bash
python openrgb_bridge.py \
  --node-id abc123def456 \
  --broker-host 192.168.1.50 \
  --broker-port 1883 \
  --broker-user ultralights \
  --broker-pass secret \
  --rgb-endpoint http://127.0.0.1:9100 \
  --heartbeat-sec 10
```

### Using environment variables
```bash
export UL_NODE_ID=abc123def456
export UL_BROKER_HOST=192.168.1.50
export UL_BROKER_USER=ultralights
export UL_BROKER_PASS=secret
export UL_RGB_ENDPOINT=http://127.0.0.1:9100
python openrgb_bridge.py
```

---

## What the bridge does

| Behavior               | MQTT topic                          | Direction        | When                  |
|-------------------------|-------------------------------------|------------------|-----------------------|
| Receive color commands  | `ul/<id>/cmd/rgb/set/0`             | Server → Bridge  | User picks a color    |
| Receive status requests | `ul/<id>/cmd/status`                | Server → Bridge  | Node page loads       |
| Receive motion off      | `ul/<id>/cmd/motion/off`            | Server → Bridge  | Motion timeout fires  |
| Receive motion on       | `ul/<id>/cmd/motion/on`             | Server → Bridge  | New motion detected   |
| Publish ACK             | `ul/<id>/evt/status`                | Bridge → Server  | After each command    |
| Publish heartbeat       | `ul/<id>/evt/status`                | Bridge → Server  | Every ~10 seconds     |
| Publish snapshot        | `ul/<id>/evt/status`                | Bridge → Server  | On `cmd/status`       |

### Heartbeat
Every `--heartbeat-sec` seconds (default 10), the bridge publishes:
```json
{"event": "ack", "status": "ok"}
```
The server's `StatusMonitor` uses this to mark the node as **online**.
If the bridge stops, the node goes offline after ~30 seconds (the
server's default timeout).

### Snapshot response
When the server requests state (e.g. the node detail page loads), the
bridge publishes a full snapshot identical in schema to what ESP32
firmware sends:
```json
{
  "event": "snapshot",
  "node": "<node-id>",
  "pir_enabled": false,
  "uptime_s": 12345,
  "ws": [],
  "rgb": [{
    "strip": 0,
    "enabled": true,
    "effect": "solid",
    "brightness": 255,
    "params": [255, 0, 0],
    "pwm_hz": 0,
    "channels": [],
    "color": [255, 0, 0]
  }],
  "white": []
}
```

### Command ACK
After processing an `rgb/set` command, the bridge publishes:
```json
{
  "event": "ack",
  "status": "ok",
  "strip": 0,
  "brightness": 255,
  "effect": "solid",
  "params": [255, 0, 0]
}
```

---

## RGB endpoint integration

When a color command arrives over MQTT, the bridge POSTs to
`<rgb-endpoint>/rgb` with:
```json
{"effect": "solid", "brightness": 255, "params": [255, 0, 0]}
```

Your FastAPI app (or whatever controls the actual LEDs) should expose
this endpoint and translate the command into OpenRGB API calls.  The
bridge does not care about the response body — it only checks the
status code.

If you don't have a separate HTTP endpoint yet and want to control
OpenRGB directly from the bridge, you can replace the
`forward_to_endpoint()` function with direct `openrgb-python` SDK calls.

---

## Running as a systemd service

Create `/etc/systemd/system/ul-bridge.service`:

```ini
[Unit]
Description=UltraLights MQTT Bridge
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=<your-user>
Environment=UL_NODE_ID=<paste-node-id>
Environment=UL_BROKER_HOST=192.168.1.50
Environment=UL_BROKER_USER=ultralights
Environment=UL_BROKER_PASS=secret
Environment=UL_RGB_ENDPOINT=http://127.0.0.1:9100
ExecStart=/usr/bin/python3 /path/to/openrgb_bridge.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ul-bridge
sudo journalctl -u ul-bridge -f
```

---

## Troubleshooting

| Symptom                        | Check                                                        |
|--------------------------------|--------------------------------------------------------------|
| Node shows offline             | Bridge running? Broker reachable? Credentials correct?       |
| Color changes don't apply      | Is `UL_RGB_ENDPOINT` correct? Is the FastAPI app running?    |
| "Node not found" on flash      | Expected — external nodes cannot be flashed (by design)      |
| Preset doesn't affect PC       | Verify the preset was saved *after* the external node was     |
|                                | added to the room (re-save the preset)                       |
| Motion doesn't fade PC lights  | Check that the node is not in the motion immunity list       |
