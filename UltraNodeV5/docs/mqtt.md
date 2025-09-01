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

Every command is a JSON object. For addressable strips the payload always includes the same top‑level keys and an effect‑specific parameter array.

### Addressable RGB strips (`ws`)

`ul/<node-id>/cmd/ws/set`

Fields:

| Field | Type | Notes |
|-------|------|-------|
| `strip` | int | Strip index (0‑3) |
| `effect` | string | One of the registered effect names |
| `brightness` | int 0‑255 | Overall brightness |
| `speed` | number | Multiplier for frame advance (1.0 = normal) |
| `params` | array | Effect‑specific parameters |

The contents of `params` depend on the chosen effect:

* `rainbow` – one integer `[wavelength]` controlling the color cycle in pixels
* `solid` – RGB `[r,g,b]` values
* `triple_wave` – up to three waves; each wave uses five values in the order `[r,g,b,freq,velocity]`
* `flash` – six integers `[r1,g1,b1,r2,g2,b2]`

Example – set strip 1 to a green solid color:

```json
{
  "strip": 1,
  "effect": "solid",
  "brightness": 255,
  "speed": 1.0,
  "params": [0, 255, 0]
}
```

Example – triple wave with three colored sine waves:

```json
{
  "strip": 0,
  "effect": "triple_wave",
  "brightness": 200,
  "speed": 0.5,
  "params": [
    255, 0, 0, 1.0, 0.1,
    0, 255, 0, 2.0, 0.15,
    0, 0, 255, 0.5, 0.2
  ]
}
```

Example – flash between red and blue:

```json
{
  "strip": 0,
  "effect": "flash",
  "brightness": 255,
  "speed": 1.0,
  "params": [255, 0, 0, 0, 0, 255]
}
```

Example – flash between red and blue:

```json
{
  "strip": 0,
  "effect": "flash",
  "brightness": 255,
  "speed": 1.0,
  "params": [255, 0, 0, 0, 0, 255]
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
    "brightness": 180,
    "speed": 1.0,
    "params": [32]
}
client.publish(f"ul/{NODE}/cmd/ws/set", json.dumps(payload), qos=1)

solid = {
    "strip": 1,
    "effect": "solid",
    "brightness": 255,
    "speed": 1.0,
    "params": [255, 0, 0]
}
client.publish(f"ul/{NODE}/cmd/ws/set", json.dumps(solid), qos=1)
```

Always include the global fields; tailor the `params` array to the selected effect.

