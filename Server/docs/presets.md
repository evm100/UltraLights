# Room Presets

The server exposes room-level "presets"—named groups of MQTT actions that apply
to every node in a room.  Presets are stored in ``Server/app/custom_presets.json``
and are surfaced on each room page and through the API.  Each entry maps a house
and room identifier to a list of action payloads that were captured from the
hardware as a raw snapshot.

## Capturing a preset

1. The admin UI queries the nodes in a room for their current state and collects
   the white/WS/RGB snapshots that firmware reports.
2. The snapshot is passed to :func:`app.presets.snapshot_to_actions`, which
   flattens the data into discrete MQTT actions tagged with the appropriate
   ``node`` and module metadata.  The helper preserves the original parameters so
   nothing is lost in translation.
3. The resulting action list, together with a ``name`` and ``id``, is saved via
   :func:`app.presets.save_custom_preset`.  The custom preset store persists the
   payload verbatim to ``custom_presets.json``.
4. When a preset is activated, :func:`app.presets.apply_preset` sends each action
   directly through :class:`app.mqtt_bus.MqttBus`, replaying the exact state that
   was captured when the preset was saved.

Because the server never rewrites the payload, operators can reason about presets
as snapshots: whatever the UI saw during capture is what will be published over
MQTT during playback.

## Example: swell all white channels

``custom_presets.json`` bundles a few seeded presets that mirror the original
catalog.  The entry below shows how a snapshot that swells all white channels can
be represented.  Triggering this preset causes each node's white channels (0–3)
to brighten from off to their configured master brightness over three seconds
before holding that output level.

```json
{
  "del-sur": {
    "room-1": [
      {
        "id": "white-swell-100",
        "name": "White Swell 0→100",
        "actions": [
          {"module": "white", "node": "del-sur-room-1-node1", "channel": 0,
           "effect": "swell", "brightness": 255, "params": []},
          {"module": "white", "node": "del-sur-room-1-node1", "channel": 1,
           "effect": "swell", "brightness": 255, "params": []},
          {"module": "white", "node": "del-sur-room-1-node1", "channel": 2,
           "effect": "swell", "brightness": 255, "params": []},
          {"module": "white", "node": "del-sur-room-1-node1", "channel": 3,
           "effect": "swell", "brightness": 255, "params": []},
          {"module": "white", "node": "node", "channel": 0,
           "effect": "swell", "brightness": 255, "params": []},
          {"module": "white", "node": "node", "channel": 1,
           "effect": "swell", "brightness": 255, "params": []},
          {"module": "white", "node": "node", "channel": 2,
           "effect": "swell", "brightness": 255, "params": []},
          {"module": "white", "node": "node", "channel": 3,
           "effect": "swell", "brightness": 255, "params": []}
        ],
        "source": "seed"
      }
    ]
  }
}
```

## Kitchen presets

Both houses include a ``kitchen`` room with several predefined presets showcasing
more targeted swells:

* **Swell On** – channels 0‑2 swell from off to full brightness.
* **Midnight Snack** – channel 0 swells from off to a night-light level while
  channel 1 remains dim.
* **Kitchen's Closed** – channel 2 swells to full while channels 0 and 1 stay
  off.
* **Normal** – channels 0‑2 swell from off to a comfortable mid-level.

These definitions live in ``custom_presets.json`` exactly as the UI captured
them.  Operators can overwrite them at any time by saving new presets through
the admin interface or by editing the JSON file directly when doing bulk
changes.
