# Room Presets

The server can expose room-level "presets"—named groups of actions that
apply to every node in a room.  Presets are defined in
`Server/app/presets.py` and are surfaced on each room page and through the
API.

## Example: swell all white channels

`presets.py` includes helpers, `_white_swell_action` and
`_white_swell_actions`, that generate the necessary MQTT commands to run the
`swell` effect on specific white channels for a list of nodes.  Channels may be
faded either up or down by choosing appropriate start and end brightness values.
The example below fades all white channels from off to a brightness of 100 over
five seconds for both nodes in `del-sur`'s `room-1`:

```python
ROOM_PRESETS = {
    "del-sur": {
        "room-1": [
            {
                "id": "white-swell-100",
                "name": "White Swell 0→100",
                "actions": _white_swell_actions(
                    ["del-sur-room-1-node1", "node"], start=0, end=100, ms=5000
                ),
            }
        ]
    }
}
```

Triggering this preset causes each node's white channels (0–3) to fade from
brightness 0 to 100 in five seconds and hold that final level.

## Kitchen presets

Both houses include a `kitchen` room with several predefined presets showcasing
more targeted swells:

* **Swell On** – channels 0‑2 swell from 0 to 100 over five seconds.
* **Midnight Snack** – channel 0 swells 0→10 and channel 1 swells 0→50.
* **Kitchen's Closed** – channel 2 swells 100→255 while channels 0 and 1 dim
  from 100 to 0.
* **Normal** – channels 0‑2 swell from 0 to 150 over five seconds.

Each preset is defined with `_white_swell_action` calls specifying the node,
channel, start brightness, end brightness and duration in milliseconds.
