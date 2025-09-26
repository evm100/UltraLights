# UltraNodeV5 MQTT Guide

This document describes how the ESP32 firmware communicates via MQTT and how to format control messages.

## Broker connection settings

The firmware reads its connection details from the MQTT section of
`menuconfig` (or from `sdkconfig.defaults` when building in CI). The
`UL_MQTT_URI` option continues to define the canonical broker address, scheme
and default port. When migrating to `mqtts` it is common to terminate TLS on a
broker that is only reachable via a LAN IP. Use the new
`UL_MQTT_DIAL_HOST`/`UL_MQTT_DIAL_PORT` pair to point the transport layer at
that local endpoint while keeping certificate validation tied to
`UL_MQTT_TLS_COMMON_NAME`.

Example: if your certificate is issued for `lights.example.org` but Mosquitto is
reachable inside the house at `192.168.1.50`, set:

```
CONFIG_UL_MQTT_URI="mqtts://lights.example.org:8883"
CONFIG_UL_MQTT_DIAL_HOST="192.168.1.50"
CONFIG_UL_MQTT_DIAL_PORT=8883
CONFIG_UL_MQTT_TLS_COMMON_NAME="lights.example.org"
```

The client will dial `192.168.1.50:8883`, validate the certificate against
`UL_MQTT_TLS_COMMON_NAME`, and only mark the MQTT subsystem ready once the TLS
handshake succeeds.

Leaving `UL_MQTT_TLS_COMMON_NAME` blank reuses the hostname from
`UL_MQTT_URI`, which is useful when the certificate already matches the broker
URI and only the dial host differs.

## Topic scheme

All topics are rooted at `ul/<node-id>/`. The node subscribes to commands addressed to itself and to the broadcast topic `ul/+/cmd/#`.

| Direction | Topic pattern | Purpose |
|-----------|---------------|---------|
| → node | `ul/<node-id>/cmd/...` | Control commands |
| ← node | `ul/<node-id>/evt/status` | Status updates and snapshots |
| ← node | `ul/<node-id>/evt/sensor/motion` | Motion events |
| ← node | `ul/<node-id>/evt/motion/status` | Motion module capabilities |
| ← node | `ul/<node-id>/evt/ota` | OTA check and update progress |

`<node-id>` is set at build time by `ul_core_get_node_id()`.

## Command payloads

Every command is a JSON object. For addressable strips the payload always includes the same top‑level keys and an effect‑specific parameter array.

### Addressable RGB strips (`ws`)

`ul/<node-id>/cmd/ws/set/<strip>`

Fields:

| Field | Type | Notes |
|-------|------|-------|
| `strip` | int | Strip index (0‑3). Optional when encoded in the topic path |
| `effect` | string | One of the registered effect names |
| `brightness` | int 0‑255 | Overall brightness |
| `params` | array | Effect‑specific parameters |

Animations advance at the firmware's fixed frame rate. Individual effects may
expose timing controls via their parameter arrays when a different pace is
required.

On success, the node replies on `ul/<node-id>/evt/status` with the chosen effect and echoed parameters.

The contents of `params` depend on the chosen effect:

* `rainbow` – one integer `[wavelength]` controlling the color cycle in pixels
* `solid` – RGB `[r,g,b]` values
* `color_swell` – RGB `[r,g,b]` base colour that swells from 0 to the configured brightness over 3 000 ms before holding steady
* `triple_wave` – fifteen numbers defining three sine waves `[r1,g1,b1,w1,f1,r2,g2,b2,w2,f2,r3,g3,b3,w3,f3]`
* `spacewaves` – nine integers specifying three RGB waves `[r1,g1,b1,r2,g2,b2,r3,g3,b3]`
* `fire` – intensity and colour gradient `[intensity,r1,g1,b1,r2,g2,b2]`. Values above `10` are treated as percentages (the web UI sends `0-200` for convenience). Requires PSRAM-enabled firmware.
* `black_ice` – shimmer intensity with a three colour ice palette `[shimmer,r1,g1,b1,r2,g2,b2,r3,g3,b3]`. Values above `10` are treated as percentages (the web UI sends `0-200`). Requires PSRAM-enabled firmware.
* `flash` – six integers `[r1,g1,b1,r2,g2,b2]`

Example – set strip 1 to a green solid color:

```json
{
  "strip": 1,
  "effect": "solid",
  "brightness": 255,
  "params": [0, 255, 0]
}
```

The `strip` index identifies which RGB strip to control. It is echoed in the
topic path (`ws/set/<strip>`), so the field may be omitted from the payload and
the topic value takes precedence. This allows each strip's last state to be
retained independently.

Example – triple wave combining red, green, and blue waves:

```json
{
  "strip": 0,
  "effect": "triple_wave",
  "brightness": 200,
  "params": [
    255, 0, 0, 30, 0.20,
    0, 255, 0, 45, 0.15,
    0, 0, 255, 60, 0.10
  ]
}
```

Shell command using `mosquitto_pub`:

```sh
mosquitto_pub -t "ul/<node-id>/cmd/ws/set/0" -m '{"strip":0,"effect":"triple_wave","brightness":200,"params":[255,0,0,30,0.20,0,255,0,45,0.15,0,0,255,60,0.10]}'
```

Example – spacewaves with three calm colors:

```json
{
  "strip": 0,
  "effect": "spacewaves",
  "brightness": 200,
  "params": [128, 0, 255, 0, 255, 255, 255, 255, 255]
}
```

Example – flash between red and blue:

```json
{
  "strip": 0,
  "effect": "flash",
  "brightness": 255,
  "params": [255, 0, 0, 0, 0, 255]
}
```

To turn a strip off, publish a `ws/set` command with the `solid` effect and
RGB parameters `[0, 0, 0]`.

### Analog RGB strips (`rgb`)

`ul/<node-id>/cmd/rgb/set/<strip>`

```json
{
  "strip": <int>,
  "brightness": <int 0-255>,
  "effect": "<name>",
  "params": [<int>, <int>, <int>]
}
```

Analog strips expose three PWM channels and support two effects:

* `solid` – static RGB `[r, g, b]` output.
* `color_swell` – RGB `[r, g, b]` base colour that swells from 0 to the master brightness over 3 000 ms, then holds that level.

Publish with `brightness: 0` to turn a strip off while preserving its colour for
the next command.

### White PWM channels (`white`)

`ul/<node-id>/cmd/white/set/<channel>`

```json
{
  "channel": <int>,
  "brightness": <int 0-255>,
  "effect": "<name>",
  "params": [<int>, ...]
}
```

The `channel` value selects the white PWM output (0‑3). It is also encoded in
the topic path, so the field may be omitted from the payload and the topic
value will be used. This enables each channel's state to be retained
separately.

Registered effects: `solid`, `breathe`, and `swell`.
* `solid` – static output with no parameters.
* `breathe` – optional params: `[period_ms]` to control the breath cycle length.
* `swell` – no parameters. Brightens from 0 to full output over 3 000 ms and then holds the final level. The master brightness
  scales the curve so a brightness of 128 yields a 0→128 swell.

### Sensor and OTA commands

`ul/<node-id>/cmd/sensor/motion` – configure motion behaviour. Fields:

| Field | Type | Notes |
|-------|------|-------|
| `pir_motion_time` | int | Seconds PIR detection persists |
| `motion_on_channel` | int | Legacy brightness override channel |
| `state0/1` | object | Optional command payloads |

Each `stateN` object may contain `ws` and `white` sub‑objects matching the
payloads for `ws/set` and `white/set`. When the node enters a new motion state,
these commands execute locally, enabling preset effects.

Motion events are published on `ul/<node-id>/evt/sensor/motion` with payload
`{"sid":"pir","state":"<MOTION_*>"}`. The PIR sensor emits
`MOTION_DETECTED` when motion starts and `MOTION_CLEAR` when it ends.

Motion state meanings:

1. `0` – no motion
2. `1` – motion detected

`ul/<node-id>/cmd/motion/status` – request the motion module capabilities. The
node responds on `ul/<node-id>/evt/motion/status` with:

```json
{ "pir_enabled": <bool> }
```

`pir_enabled` reports whether the firmware was built with the PIR task enabled
and ready to publish motion events.

`ul/<node-id>/cmd/motion/off` – request a fade-out of the active motion preset.

```json
{
  "fade": {
    "duration_ms": <int>,
    "steps": <int>
  }
}
```

The `fade` object is optional; when omitted the node falls back to an
eight-step ramp spread across roughly two seconds (2 000 ms). Any explicitly
provided `duration_ms` or `steps` values override those defaults, and values
≤ 0 switch to an immediate turn-off. When the command is accepted, the node
captures the current brightness of every active WS, RGB, and white channel and
walks them down evenly toward zero on each timer tick. If fresh motion activity
arrives (`motion/on` followed by new preset commands), the fade is cancelled so
the new scene can take over without fighting the ramp-down.

Motion immunity is honoured by omission: controllers exclude immune fixtures
from this command, so nodes that never receive `motion/off` simply maintain
their existing levels.

`ul/<node-id>/cmd/ota/check` – empty JSON `{}` triggers an OTA manifest check.

OTA progress events are published on `ul/<node-id>/evt/ota` with payload
`{"status":"<state>","detail":"..."}` describing each step.

`ul/<node-id>/cmd/status` – request a full status snapshot.

## Status and snapshots

Most commands produce a short acknowledgement on `ul/<node-id>/evt/status`:

* General commands reply with `{ "status": "ok" }`.
* `ws/set` echoes the chosen effect and its parameters.

To retrieve the full device state, publish an empty JSON object to `ul/<node-id>/cmd/status`. The node then responds on `ul/<node-id>/evt/status` with a detailed snapshot describing each strip and channel. The `color` fields in the `ws` and `rgb` sections are meaningful only when the corresponding strip effect is `solid`.

Snapshots also include `"pir_enabled": <bool>` so controllers can detect
whether motion events are expected from the node.

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
    "params": [32]
}
client.publish(f"ul/{NODE}/cmd/ws/set/{payload['strip']}", json.dumps(payload), qos=1)

solid = {
    "strip": 1,
    "effect": "solid",
    "brightness": 255,
    "params": [255, 0, 0]
}
client.publish(f"ul/{NODE}/cmd/ws/set/{solid['strip']}", json.dumps(solid), qos=1)
```

Always include the global fields; tailor the `params` array to the selected effect.

Example – fire with a hot red core and yellow embers:

```json
{
  "strip": 0,
  "effect": "fire",
  "brightness": 220,
  "params": [1.0, 255, 64, 0, 255, 217, 102]
}
```

Example – Black Ice with a dark blue base, icy cracks, and bright white sparkles:

```json
{
  "strip": 0,
  "effect": "black_ice",
  "brightness": 210,
  "params": [1.2, 4, 18, 42, 102, 199, 250, 255, 255, 255]
}
```

