# UltraNodeV5 MQTT Guide

This document describes how the ESP32 firmware communicates via MQTT and how to format control messages.

## Topic scheme

All topics are rooted at `ul/<node-id>/`. The node subscribes to commands addressed to itself and to the broadcast topic `ul/+/cmd/#`.

| Direction | Topic pattern | Purpose |
|-----------|---------------|---------|
| → node | `ul/<node-id>/cmd/...` | Control commands |
| ← node | `ul/<node-id>/evt/status` | Full status snapshot |
| ← node | `ul/<node-id>/evt/sensor/motion` | Motion events |

`<node-id>` is set at build time by `ul_core_get_node_id()`.

## Command payloads

Every command is a JSON object. Only fields relevant to the selected effect are included.

### Addressable RGB strips (`ws`)

`ul/<node-id>/cmd/ws/set`

Common fields:

| Field | Type | Notes |
|-------|------|-------|
| `strip` | int | Strip index (0‑3) |
| `effect` | string | One of the registered effect names |
| `brightness` | int 0‑255 | Overall brightness |

Effect‑specific fields:

| Effect | Extra fields |
|--------|-------------|
| `solid` | `color` – RGB array `[r,g,b]` with 0‑255 ints, or `hex` – string `"#RRGGBB"` |
| others (`breathe`, `rainbow`, `twinkle`, `theater_chase`, `wipe`, `gradient_scroll`) | *(none)* |

Example – set strip 1 to a green solid color:

```json
{
  "strip": 1,
  "effect": "solid",
  "brightness": 255,
  "color": [0, 255, 0]
}
```

Example – same color specified with a hex string:

```json
{
  "strip": 1,
  "effect": "solid",
  "brightness": 255,
  "hex": "#00FF00"
}
```

`ul/<node-id>/cmd/ws/power`

```json
{ "strip": <int>, "on": <bool> }
```

### White PWM channels (`white`)

`ul/<node-id>/cmd/white/set`

```json
{
  "channel": <int>,
  "effect": "<name>",
  "brightness": <int 0-255>
}
```

Registered effects: `graceful_on`, `graceful_off`, `motion_swell`, `day_night_curve`, `blink`. None of them require extra parameters.

`ul/<node-id>/cmd/white/power`

```json
{ "channel": <int>, "on": <bool> }
```

### Sensor and OTA commands

`ul/<node-id>/cmd/sensor/cooldown` – `{ "seconds": <int 10‑3600> }`

`ul/<node-id>/cmd/ota/check` – empty JSON `{}` triggers an OTA manifest check.

## Status snapshot

The node publishes its current state to `ul/<node-id>/evt/status` after every accepted command. The JSON payload contains details about each strip and channel. The `color` field is meaningful only when the corresponding strip effect is `solid`.

## Publishing from Python

Example using `paho-mqtt`:

```python
import json, paho.mqtt.client as mqtt

NODE = "node123"
client = mqtt.Client()
client.connect("broker.local")

payload = {
    "strip": 0,
    "effect": "rainbow",
    "brightness": 180
}
client.publish(f"ul/{NODE}/cmd/ws/set", json.dumps(payload), qos=1)

solid = {
    "strip": 1,
    "effect": "solid",
    "brightness": 255,
    "color": [255, 0, 0]
}
client.publish(f"ul/{NODE}/cmd/ws/set", json.dumps(solid), qos=1)
```

Only include parameters required by the chosen effect to keep messages minimal.

