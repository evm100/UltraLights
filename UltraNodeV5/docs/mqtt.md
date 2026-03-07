# UltraNodeV5 MQTT Protocol Reference

This document is the authoritative specification for the MQTT messaging protocol used by UltraNodeV5 firmware nodes. It is intended for external broker and controller implementors.

---

## Table of Contents

1. [Broker Connection](#broker-connection)
2. [Authentication](#authentication)
3. [Topic Namespace](#topic-namespace)
4. [Subscriptions](#subscriptions)
5. [Command Topics (Inbound)](#command-topics-inbound)
   - [ws/set — Addressable RGB strips](#wsset--addressable-rgb-strips)
   - [rgb/set — Analog RGB strips](#rgbset--analog-rgb-strips)
   - [white/set — White PWM channels](#whiteset--white-pwm-channels)
   - [motion/off — Fade lights out](#motionoff--fade-lights-out)
   - [motion/on — Cancel fade](#motionon--cancel-fade)
   - [pir/status — Query PIR status](#pirstatus--query-pir-status)
   - [ota/check — Trigger OTA](#otacheck--trigger-ota)
   - [system/wipe-nvs — Factory reset](#systemwipe-nvs--factory-reset)
   - [status — Request snapshot](#status--request-snapshot)
6. [Event Topics (Outbound)](#event-topics-outbound)
   - [evt/status — ACK and snapshots](#evtstatus--ack-and-snapshots)
   - [evt/\<sensor\>/motion — Motion events](#evtsensormotion--motion-events)
   - [evt/\<sensor\>/status — Sensor status](#evtsensorstatus--sensor-status)
   - [evt/ota — OTA progress](#evtota--ota-progress)
7. [Effect Reference](#effect-reference)
   - [WS2812 / Addressable effects](#ws2812--addressable-effects)
   - [Analog RGB effects](#analog-rgb-effects)
   - [White PWM effects](#white-pwm-effects)
8. [Full Status Snapshot Schema](#full-status-snapshot-schema)
9. [Wire Examples](#wire-examples)

---

## Broker Connection

Connection parameters are compiled into firmware via `menuconfig` / `sdkconfig.defaults`.

| Parameter | Kconfig key | Default |
|-----------|-------------|---------|
| Broker URI | `CONFIG_UL_MQTT_URI` | — |
| Override dial host | `CONFIG_UL_MQTT_DIAL_HOST` | _(URI host)_ |
| Override dial port | `CONFIG_UL_MQTT_DIAL_PORT` | _(URI port)_ |
| TLS common name | `CONFIG_UL_MQTT_TLS_COMMON_NAME` | _(URI host)_ |
| Username | `CONFIG_UL_MQTT_USER` | — |
| Password | `CONFIG_UL_MQTT_PASS` | — |
| Connect timeout (ms) | `CONFIG_UL_MQTT_CONNECT_TIMEOUT_MS` | — |
| Reconnect delay (ms) | `CONFIG_UL_MQTT_RECONNECT_DELAY_MS` | — |

**Split-hostname TLS example** — certificate issued for `lights.example.org`, Mosquitto running on LAN at `192.168.1.50`:

```
CONFIG_UL_MQTT_URI="mqtts://lights.example.org:8883"
CONFIG_UL_MQTT_DIAL_HOST="192.168.1.50"
CONFIG_UL_MQTT_DIAL_PORT=8883
CONFIG_UL_MQTT_TLS_COMMON_NAME="lights.example.org"
```

The node dials `192.168.1.50:8883` but validates the server certificate against `lights.example.org`. Leaving `UL_MQTT_TLS_COMMON_NAME` blank re-uses the hostname from `UL_MQTT_URI`.

---

## Authentication

Two authentication modes are supported and may coexist during migration.

### Username / password

The firmware sends `CONFIG_UL_MQTT_USER` / `CONFIG_UL_MQTT_PASS` on every connect. The broker must permit these credentials on its listener.

### Mutual TLS (mTLS)

Enable with `CONFIG_UL_MQTT_REQUIRE_CLIENT_CERT=y`. The node refuses to connect until a certificate bundle has been provisioned into NVS. The bundle is delivered through the captive-portal provisioning flow (base64 fields `mqtt_client_certificate` and `mqtt_client_key`). The certificate Common Name equals the node ID, enabling per-node ACLs on the broker.

Set `CONFIG_UL_MQTT_CLIENT_CERT_MAX_LEN` / `CONFIG_UL_MQTT_CLIENT_KEY_MAX_LEN` if your chain or key exceeds the defaults (3 KiB / 2 KiB).

Set `CONFIG_UL_MQTT_LEGACY_USERPASS_COMPAT=y` to keep username/password active while migrating — disable once every node carries a certificate.

---

## Topic Namespace

All topics share the prefix `ul/<node-id>/`. The node ID is set at build time and is unique per device.

```
ul/<node-id>/
├── cmd/                          ← server → node  (subscribed by node)
│   ├── ws/set[/<strip>]
│   ├── rgb/set[/<strip>]
│   ├── white/set[/<channel>]
│   ├── motion/off
│   ├── motion/on
│   ├── <sensor>/status           e.g. pir/status
│   ├── ota/check
│   ├── system/wipe-nvs
│   └── status
└── evt/                          ← node → server  (published by node)
    ├── status
    ├── <sensor>/motion           e.g. pir/motion
    ├── <sensor>/status           e.g. pir/status
    └── ota
```

`<sensor>` is the sensor identifier string. The currently deployed sensor is `pir`. Additional sensors follow the same pattern without requiring firmware changes to the broker schema.

---

## Subscriptions

The node registers two subscriptions on every MQTT connect:

| Topic filter | QoS | Purpose |
|---|---|---|
| `ul/<node-id>/cmd/#` | 1 | All commands addressed to this specific node |
| `ul/+/cmd/#` | 0 | Broadcast commands addressed to any node |

A message received on the broadcast filter is processed identically to a device-specific message. The node rejects messages whose embedded node segment does not match its own ID (or `+`).

---

## Command Topics (Inbound)

All command payloads are JSON objects. An empty object `{}` is valid for commands that take no parameters.

### `ws/set` — Addressable RGB strips

**Topic:** `ul/<node-id>/cmd/ws/set` or `ul/<node-id>/cmd/ws/set/<strip>`

Controls a WS2812 (NeoPixel) addressable LED strip.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `strip` | integer 0–1 | No | Strip index. Omit when index is in the topic path — topic takes precedence if both are present |
| `effect` | string | Yes | Effect name (see [WS2812 effects](#ws2812--addressable-effects)) |
| `brightness` | integer 0–255 | Yes | Master brightness |
| `params` | array | Yes | Effect-specific parameters |

**Response:** ACK on `evt/status`. The command is also saved to NVS so the strip resumes the same state after a power cycle.

Any active motion fade is cancelled when this command is received.

**Example — solid green on strip 0:**
```json
{
  "strip": 0,
  "effect": "solid",
  "brightness": 255,
  "params": [0, 255, 0]
}
```

**Example — rainbow on strip 1 via topic path:**
```
Topic:   ul/<node-id>/cmd/ws/set/1
Payload: {"effect":"rainbow","brightness":200,"params":[32]}
```

---

### `rgb/set` — Analog RGB strips

**Topic:** `ul/<node-id>/cmd/rgb/set` or `ul/<node-id>/cmd/rgb/set/<strip>`

Controls an analog (PWM) RGB strip on up to 4 strips (0–3).

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `strip` | integer 0–3 | No | Strip index. Topic path takes precedence if both present |
| `effect` | string | Yes | Effect name (see [Analog RGB effects](#analog-rgb-effects)) |
| `brightness` | integer 0–255 | Yes | Master brightness |
| `params` | array | Yes | `[r, g, b]` integers 0–255 |

**Response:** ACK on `evt/status`. Saved to NVS.

Any active motion fade is cancelled when this command is received.

**Example — dim warm white:**
```json
{
  "strip": 0,
  "effect": "solid",
  "brightness": 128,
  "params": [255, 200, 120]
}
```

---

### `white/set` — White PWM channels

**Topic:** `ul/<node-id>/cmd/white/set` or `ul/<node-id>/cmd/white/set/<channel>`

Controls a single-channel white PWM output on up to 4 channels (0–3).

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `channel` | integer 0–3 | No | Channel index. Topic path takes precedence if both present |
| `effect` | string | Yes | Effect name (see [White PWM effects](#white-pwm-effects)) |
| `brightness` | integer 0–255 | Yes | Master brightness |
| `params` | array | Varies | Effect-specific — see effect table |

**Response:** ACK on `evt/status`. Saved to NVS.

Any active motion fade is cancelled when this command is received.

**Example — slow breathe on channel 2:**
```json
{
  "channel": 2,
  "effect": "breathe",
  "brightness": 200,
  "params": [4000]
}
```

---

### `motion/off` — Fade lights out

**Topic:** `ul/<node-id>/cmd/motion/off`

Ramps all active WS, RGB, and white channels from their current brightness down to 0 over the specified duration. Brightness values are captured at command receipt; each timer tick applies a linear step toward zero.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `fade.duration_ms` | integer | No | Total fade duration in ms. Default: 2000. Values ≤ 0 → immediate off |
| `fade.steps` | integer | No | Number of brightness steps. Default: 255. Values ≤ 0 → immediate off |

If the `fade` object is omitted entirely, the node uses the defaults (2000 ms, 255 steps).

A subsequent `motion/on` command or any `ws/set`, `rgb/set`, `white/set` command cancels the fade immediately.

**Example — two-second fade:**
```json
{
  "fade": {
    "duration_ms": 2000,
    "steps": 40
  }
}
```

**Example — immediate off:**
```json
{
  "fade": {
    "duration_ms": 0,
    "steps": 0
  }
}
```

---

### `motion/on` — Cancel fade

**Topic:** `ul/<node-id>/cmd/motion/on`

**Payload:** `{}` (empty object)

Cancels any in-progress motion fade, leaving all channels at their current brightness. No event is published. Typically sent when new motion is detected so the scene is not left mid-fade.

---

### `<sensor>/status` — Query sensor status

**Topic:** `ul/<node-id>/cmd/<sensor>/status`

Requests a status publish for the named sensor. Currently defined sensor: `pir`.

**Payload:** `{}` (empty object)

**Response:** Published on `ul/<node-id>/evt/<sensor>/status` (see [evt/\<sensor\>/status](#evtsensorstatus--sensor-status)).

**Example topic:** `ul/<node-id>/cmd/pir/status`

---

### `ota/check` — Trigger OTA

**Topic:** `ul/<node-id>/cmd/ota/check`

**Payload:** `{}`

Triggers an OTA manifest download and firmware update if a newer version is available. Progress is published on `evt/ota`. If the update succeeds, the node restarts automatically after the success event is acknowledged by the broker.

---

### `system/wipe-nvs` — Factory reset

**Topic:** `ul/<node-id>/cmd/system/wipe-nvs`

**Payload:** `{}`

Erases the NVS partition (stored Wi-Fi credentials, light states, MQTT certificates) and restarts the node. A status publish is attempted before the erase. Use with caution — the node will re-enter provisioning mode.

---

### `status` — Request snapshot

**Topic:** `ul/<node-id>/cmd/status`

**Payload:** `{}`

Requests a full device state snapshot. The response is published on `evt/status` (see [Full Status Snapshot Schema](#full-status-snapshot-schema)).

---

## Event Topics (Outbound)

### `evt/status` — ACK and snapshots

**Topic:** `ul/<node-id>/evt/status`

**QoS:** 1

Published in two forms:

#### Command ACK

Published after every `ws/set`, `rgb/set`, or `white/set` command.

**Success:**
```json
{
  "event": "ack",
  "status": "ok",
  "strip": 0,
  "effect": "solid",
  "params": [0, 255, 0]
}
```

For `rgb/set` and `white/set` the ACK also includes `brightness`:
```json
{
  "event": "ack",
  "status": "ok",
  "strip": 0,
  "brightness": 200,
  "effect": "solid",
  "params": [0, 255, 0]
}
```

For `white/set` the index field is `channel` instead of `strip`:
```json
{
  "event": "ack",
  "status": "ok",
  "channel": 1,
  "brightness": 200,
  "effect": "breathe",
  "params": [3000]
}
```

**Failure** (unknown effect name or invalid parameters):
```json
{
  "event": "ack",
  "status": "error",
  "error": "invalid effect"
}
```

#### Full Snapshot

Published in response to `cmd/status` or `cmd/ota/check`. See [Full Status Snapshot Schema](#full-status-snapshot-schema).

---

### `evt/<sensor>/motion` — Motion events

**Topic:** `ul/<node-id>/evt/<sensor>/motion`

**Example:** `ul/<node-id>/evt/pir/motion`

**QoS:** 1

Published when the sensor detects a trigger. The publish rate is rate-limited by the `CONFIG_UL_PIR_EVENT_MIN_INTERVAL_S` Kconfig option to prevent flooding.

```json
{
  "state": "MOTION_DETECTED"
}
```

| `state` value | Meaning |
|---|---|
| `MOTION_DETECTED` | Sensor GPIO is high |

---

### `evt/<sensor>/status` — Sensor status

**Topic:** `ul/<node-id>/evt/<sensor>/status`

**Example:** `ul/<node-id>/evt/pir/status`

Published in response to `cmd/<sensor>/status`.

```json
{
  "enabled": true
}
```

`enabled` is `true` when the firmware was compiled with the sensor task active (`CONFIG_UL_PIR_ENABLED=y` for the PIR sensor).

---

### `evt/ota` — OTA progress

**Topic:** `ul/<node-id>/evt/ota`

**QoS:** 1

Published at each stage of an OTA update cycle. The node waits for broker acknowledgment of the `success` message before restarting.

```json
{
  "status": "<state>",
  "detail": "<optional string>"
}
```

| `status` | `detail` | Meaning |
|---|---|---|
| `check_start` | Manifest URL | Beginning manifest download |
| `manifest_ok` | — | Manifest parsed successfully |
| `manifest_fail` | Error description | Manifest download or parse failed |
| `begin` | — | Firmware transfer starting |
| `success` | New version string | OTA complete — node will restart |
| `begin_fail` | Error description | Could not begin OTA partition write |
| `perform_fail` | Error description | Error during firmware transfer |
| `finish_fail` | Error description | Error finalizing OTA partition |

---

## Effect Reference

### WS2812 / Addressable effects

Used with `cmd/ws/set`.

| Effect | `params` layout | Notes |
|--------|----------------|-------|
| `solid` | `[r, g, b]` | Static color. Use `[0,0,0]` to turn off |
| `rainbow` | `[wavelength]` | Color cycle length in pixels |
| `color_swell` | `[r, g, b]` | Swells from 0 to master brightness over ~3 000 ms, then holds |
| `triple_wave` | `[r1,g1,b1,w1,f1, r2,g2,b2,w2,f2, r3,g3,b3,w3,f3]` | Three sine waves. `w` = wavelength in pixels, `f` = speed factor |
| `spacewaves` | `[r1,g1,b1, r2,g2,b2, r3,g3,b3]` | Three blended RGB waves |
| `fire` | `[intensity, r1,g1,b1, r2,g2,b2]` | Hot-core gradient. Values > 10 treated as percentages. **Requires PSRAM** |
| `black_ice` | `[shimmer, r1,g1,b1, r2,g2,b2, r3,g3,b3]` | Shimmer over three-color ice palette. Values > 10 treated as percentages. **Requires PSRAM** |
| `flash` | `[r1,g1,b1, r2,g2,b2]` | Alternates between two colors |

**Examples:**

```json
{ "effect": "triple_wave", "params": [255,0,0,30,0.20, 0,255,0,45,0.15, 0,0,255,60,0.10] }
{ "effect": "spacewaves",  "params": [128,0,255, 0,255,255, 255,255,255] }
{ "effect": "fire",        "params": [1.0, 255,64,0, 255,217,102] }
{ "effect": "black_ice",   "params": [1.2, 4,18,42, 102,199,250, 255,255,255] }
{ "effect": "flash",       "params": [255,0,0, 0,0,255] }
```

---

### Analog RGB effects

Used with `cmd/rgb/set`. `params` is always `[r, g, b]`.

| Effect | Behavior |
|--------|----------|
| `solid` | Static RGB output at master brightness |
| `color_swell` | Ramps from 0 to master brightness over ~3 000 ms, then holds |

---

### White PWM effects

Used with `cmd/white/set`.

| Effect | `params` layout | Behavior |
|--------|----------------|----------|
| `solid` | `[]` (empty) | Static output at master brightness |
| `breathe` | `[period_ms]` (optional) | Sinusoidal cycle. Default period if omitted |
| `swell` | `[]` (empty) | Ramps from 0 to master brightness over ~3 000 ms, then holds |

---

## Full Status Snapshot Schema

Published on `evt/status` with `"event": "snapshot"` in response to `cmd/status` or `cmd/ota/check`.

```json
{
  "event": "snapshot",
  "node": "<node-id>",
  "pir_enabled": <bool>,
  "uptime_s": <integer>,

  "ws": [
    {
      "strip":      <integer>,
      "enabled":    <bool>,
      "effect":     "<string>",
      "brightness": <integer 0-255>,
      "params":     <array>,
      "pixels":     <integer>,
      "gpio":       <integer>,
      "fps":        <integer>,
      "color":      [<r>, <g>, <b>]
    }
  ],

  "rgb": [
    {
      "strip":      <integer>,
      "enabled":    <bool>,
      "effect":     "<string>",
      "brightness": <integer 0-255>,
      "params":     <array>,
      "pwm_hz":     <integer>,
      "channels": [
        { "gpio": <integer>, "ledc_ch": <integer>, "mode": <integer> },
        { "gpio": <integer>, "ledc_ch": <integer>, "mode": <integer> },
        { "gpio": <integer>, "ledc_ch": <integer>, "mode": <integer> }
      ],
      "color": [<r>, <g>, <b>]
    }
  ],

  "white": [
    {
      "channel":    <integer>,
      "enabled":    <bool>,
      "effect":     "<string>",
      "brightness": <integer 0-255>,
      "params":     <array>,
      "gpio":       <integer>,
      "pwm_hz":     <integer>
    }
  ]
}
```

**Notes:**
- The `ws` array contains one object per configured strip (up to 2). The `rgb` array up to 4. The `white` array up to 4.
- `color` in `ws` and `rgb` reflects the last solid color set and is only meaningful when `effect` is `solid`.
- `pir_enabled` mirrors the compile-time `CONFIG_UL_PIR_ENABLED` flag.
- `params` in each strip/channel object is loaded from NVS and reflects the last persisted command.

---

## Wire Examples

### mosquitto_pub

```sh
NODE="mynode"
BROKER="192.168.1.50"

# Solid red on WS strip 0
mosquitto_pub -h $BROKER -t "ul/$NODE/cmd/ws/set/0" \
  -m '{"effect":"solid","brightness":255,"params":[255,0,0]}'

# Rainbow on WS strip 1
mosquitto_pub -h $BROKER -t "ul/$NODE/cmd/ws/set/1" \
  -m '{"effect":"rainbow","brightness":180,"params":[32]}'

# Analog RGB warm white on strip 0
mosquitto_pub -h $BROKER -t "ul/$NODE/cmd/rgb/set/0" \
  -m '{"effect":"solid","brightness":200,"params":[255,200,120]}'

# White channel 0 at 50% breathe
mosquitto_pub -h $BROKER -t "ul/$NODE/cmd/white/set/0" \
  -m '{"effect":"breathe","brightness":128,"params":[3000]}'

# Fade all lights out over 3 seconds
mosquitto_pub -h $BROKER -t "ul/$NODE/cmd/motion/off" \
  -m '{"fade":{"duration_ms":3000,"steps":60}}'

# Request full snapshot
mosquitto_pub -h $BROKER -t "ul/$NODE/cmd/status" -m '{}'

# Query PIR sensor status
mosquitto_pub -h $BROKER -t "ul/$NODE/cmd/pir/status" -m '{}'

# Trigger OTA check
mosquitto_pub -h $BROKER -t "ul/$NODE/cmd/ota/check" -m '{}'

# Broadcast snapshot request to all nodes
mosquitto_pub -h $BROKER -t "ul/+/cmd/status" -m '{}'
```

### Python (paho-mqtt)

```python
import json
import paho.mqtt.client as mqtt

NODE = "mynode"
client = mqtt.Client()
client.username_pw_set("user", "pass")
client.connect("192.168.1.50", 1883)

def pub(path, payload):
    client.publish(f"ul/{NODE}/cmd/{path}", json.dumps(payload), qos=1)

# Addressable strip — triple wave
pub("ws/set/0", {
    "effect": "triple_wave",
    "brightness": 200,
    "params": [255,0,0,30,0.20, 0,255,0,45,0.15, 0,0,255,60,0.10]
})

# White channel — swell on connect
pub("white/set/0", {
    "effect": "swell",
    "brightness": 255,
    "params": []
})

# Subscribe to motion and status events
def on_message(client, userdata, msg):
    print(msg.topic, msg.payload.decode())

client.on_message = on_message
client.subscribe(f"ul/{NODE}/evt/#", qos=1)
client.loop_forever()
```

### Subscribing to all node events

```sh
# All events from a specific node
mosquitto_sub -h $BROKER -t "ul/$NODE/evt/#" -v

# Events from all nodes on the network
mosquitto_sub -h $BROKER -t "ul/+/evt/#" -v
```
