# UltraNodeV5 MQTT Guide

This document describes how the ESP32 firmware communicates via MQTT and how to format control messages.

## Topic scheme

All topics are rooted at `ul/<node-id>/`. The node subscribes to commands addressed to itself and to the broadcast topic `ul/+/cmd/#`.

| Direction | Topic pattern | Purpose |
|-----------|---------------|---------|
| → node | `ul/<node-id>/cmd/...` | Control commands |
| ← node | `ul/<node-id>/evt/status` | Status updates and snapshots |
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

On success, the node replies on `ul/<node-id>/evt/status` with the chosen effect and echoed parameters.

The contents of `params` depend on the chosen effect:

* `rainbow` – one integer `[wavelength]` controlling the color cycle in pixels
* `solid` – RGB `[r,g,b]` values
* `triple_wave` – fifteen numbers defining three sine waves `[r1,g1,b1,w1,f1,r2,g2,b2,w2,f2,r3,g3,b3,w3,f3]`
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

Example – triple wave combining red, green, and blue waves:

```json
{
  "strip": 0,
  "effect": "triple_wave",
  "brightness": 200,
  "speed": 0.5,
  "params": [
    255, 0, 0, 30, 0.20,
    0, 255, 0, 45, 0.15,
    0, 0, 255, 60, 0.10
  ]
}
```

Shell command using `mosquitto_pub`:

```sh
mosquitto_pub -t "ul/<node-id>/cmd/ws/set" -m '{"strip":0,"effect":"triple_wave","brightness":200,"speed":0.5,"params":[255,0,0,30,0.20,0,255,0,45,0.15,0,0,255,60,0.10]}'
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
  "brightness": <int 0-255>,
  "effect": "<name>",
  "params": [<int>, ...]
}
```

Registered effects: `solid` and `breathe`.
* `solid` – static output with no parameters.
* `breathe` – optional params: `[period_ms]` to control the breath cycle length.

### Sensor and OTA commands

`ul/<node-id>/cmd/sensor/cooldown` – `{ "seconds": <int 10‑3600> }`

`ul/<node-id>/cmd/ota/check` – empty JSON `{}` triggers an OTA manifest check.

`ul/<node-id>/cmd/status` – request a full status snapshot.

## Status and snapshots

Most commands produce a short acknowledgement on `ul/<node-id>/evt/status`:

* General commands reply with `{ "status": "ok" }`.
* `ws/set` echoes the chosen effect and its parameters.

To retrieve the full device state, publish an empty JSON object to `ul/<node-id>/cmd/status`. The node then responds on `ul/<node-id>/evt/status` with a detailed snapshot describing each strip and channel. The `color` field is meaningful only when the corresponding strip effect is `solid`.

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

