# Preset architecture

The ``Server/app/presets`` package is responsible for loading, saving and
applying room-level presets.  A preset is a list of raw MQTT actions captured
from the admin UI or API; the payload is persisted unchanged so that the server
can replay the exact snapshot that was saved by an operator.

## Package layout

```
presets/
├── __init__.py            # Public API used by the rest of the application
├── README.md              # You are here
└── custom_store.py        # JSON-backed storage for room-level presets
```

Importers continue to use ``from .presets import get_room_presets`` and friends.
Preset data now lives in ``custom_presets.json`` alongside the server code and
is loaded through :class:`~presets.custom_store.CustomPresetStore`.  The JSON
file ships with a handful of seeded presets (marked with ``"source": "seed"``)
so that rooms have sensible defaults out of the box, but operators can add or
replace entries entirely through the web UI/API.

## Raw snapshot workflow

The preset pipeline keeps snapshots verbatim from capture through playback:

1. The admin UI records device state for a node and calls
   :func:`~presets.snapshot_to_actions` to normalize the snapshot into per-module
   MQTT actions tagged with the target ``node``.  The helper filters out invalid
   entries (for example, missing ``strip``/``channel`` identifiers) but otherwise
   leaves the payload untouched.
2. The resulting actions, along with preset metadata such as ``id`` and
   ``name``, are saved via :func:`~presets.save_custom_preset`.  The
   :class:`~presets.custom_store.CustomPresetStore` persists the list to
   ``custom_presets.json``.
3. When a preset is triggered, :func:`~presets.apply_preset` walks each stored
   action and sends it directly to :class:`~app.mqtt_bus.MqttBus` using the
   module-specific publish helpers (``ws_set``, ``rgb_set`` or ``white_set``).

Because the payloads mirror the captured snapshot, no translation step is
required during playback.  Any fields that were present when the preset was
saved—effect identifiers, brightness levels or additional parameters—are sent
through exactly as they were recorded.

## Motion automation and shutdown

Motion-triggered scenes still load presets through :func:`~presets.apply_preset`,
but the shutdown path is now handled by MQTT broadcasts rather than generating
synthetic "reverse" actions. Each preset stores the raw snapshot produced by the
admin UI/API, and those actions are replayed verbatim whenever the scene is
activated. When motion times out, the server publishes a ``motion/off`` message
so firmware can fade the lights using its local transition logic instead of the
server reconstructing a dimmed payload.

```python
# Server side: automation decides the room is empty
bus.publish("house/living-room/motion/off", {"preset_id": "evening"})

# Firmware side: subscribed handler performs the fade
if topic.endswith("/motion/off"):
    fade_to_black(duration_ms=1500)  # device-local timing
```

Because nodes now manage their own fades, presets remain focused on capturing
the exact device snapshot that operators saved. There is no extra metadata for
"off" variants—everything needed to shut the room down is driven by the
``motion/off`` broadcast and the firmware's fade routine.

## Defining presets

Presets are stored in ``custom_presets.json`` as a mapping of
``{house_id: {room_id: [preset, ...]}}``.  Each preset mirrors the structure
returned by :func:`~presets.get_room_presets` and contains an ``id``, ``name``
and ``actions`` list.  The seeded entries bundled with the repository were
generated from the original Python catalog, but operators typically manage the
file indirectly: the admin UI snapshots device state to create new presets, and
the API exposes CRUD endpoints that update the JSON store safely. The stored
``actions`` are the raw snapshots captured at save time; they are not rewritten
for automation shutdown.

When editing the file manually, remember to preserve unique identifiers—custom
presets saved through the UI will overwrite existing entries with matching IDs.

## Quick reference

* :func:`~presets.get_room_presets` – return the presets configured for a
  ``house_id``/``room_id``.
* :func:`~presets.list_custom_presets` – list custom presets for the provided
  identifiers.
* :func:`~presets.save_custom_preset` – normalize and persist a preset
  definition.
* :func:`~presets.delete_custom_preset` – remove a stored preset.
* :func:`~presets.apply_preset` – replay the stored actions through
  :class:`~app.mqtt_bus.MqttBus`.
* :func:`~presets.snapshot_to_actions` – translate a node snapshot into action
  payloads suitable for saving in a preset.

This modular layout keeps preset handling focused on the high-level lifecycle:
capturing raw device snapshots, storing them safely and replaying them through a
shared MQTT bus.
