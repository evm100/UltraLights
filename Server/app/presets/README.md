# Preset architecture

The ``Server/app/presets`` package turns the monolithic ``presets.py`` module
into a collection of small, composable pieces:

```
presets/
├── __init__.py            # Public API used by the rest of the application
├── README.md              # You are here
├── action_registry.py     # Keeps track of action types and their reversers
├── actions/               # Reusable "lego" blocks that build action payloads
│   ├── __init__.py
│   ├── color.py
│   └── white.py
└── custom_store.py        # JSON-backed storage for room-level presets
```

Importers continue to use ``from .presets import get_room_presets`` and friends.
Preset data now lives in ``custom_presets.json`` alongside the server code and
is loaded through :class:`~presets.custom_store.CustomPresetStore`.  The JSON
file ships with a handful of seeded presets (marked with ``"source": "seed"``)
so that rooms have sensible defaults out of the box, but operators can add or
replace entries entirely through the web UI/API.

## Building reusable actions

Every effect/action is defined by a small builder function in ``actions/``. A
builder returns the payload that will eventually be sent to ``MqttBus``. Each
builder is registered with the central :class:`~presets.action_registry.ActionRegistry`
via the :func:`~presets.register_action` decorator, which automatically adds the
``_action_type`` metadata required for reversal when it is needed.

```python
from .presets import register_action, reverse_action


def _reverse_fade(action: dict[str, object]) -> dict[str, object]:
    action["params"] = list(reversed(action["params"]))
    return action


@register_action("my-module.fade", reverser=_reverse_fade)
def fade_action(node: str, channel: int, start: int, end: int) -> dict[str, object]:
    return {
        "node": node,
        "module": "my-module",
        "channel": channel,
        "effect": "fade",
        "params": [start, end],
    }


# Later, if the UI needs to undo the change:
reversed_payload = reverse_action(fade_action("node-1", 0, 0, 255))
```

The decorator stores both the builder and the ``_reverse_fade`` callback. If a
preset action is created manually (without the decorator), use
:func:`~presets.with_action_type` to attach metadata::

```python
manual_action = with_action_type("my-module.fade", {...})
```

If an action lacks metadata or no reverser is registered, ``reverse_action``
falls back to copying the original payload unchanged.

### Reversing custom actions

Reversers receive a deep copy of the action dictionary and must return a new
payload. They may inspect custom metadata to decide how to reverse. For example
``actions/color.py`` stores ``_reverse_meta`` with the target color so that
calling :func:`~presets.reverse_action` swaps between the active color and the
fallback (by default, full black).

When a reverser changes fields that future reversals depend on, it should also
update any metadata accordingly so the operation remains symmetric. The existing
``solid_color_action`` implementation demonstrates this by preserving the
previous color inside ``_reverse_meta``.

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

* ``presets.actions`` – reusable action builders such as
  :func:`~presets.actions.white.white_swell_action`,
  :func:`~presets.actions.color.ws_swell_action`,
  :func:`~presets.actions.color.rgb_swell_action` and
  :func:`~presets.actions.color.solid_color_action`.
* ``presets.action_registry`` – the registry instance plus helper functions
  (:func:`~presets.register_action`, :func:`~presets.reverse_action`,
  :func:`~presets.with_action_type`).
* ``custom_presets.json`` – JSON payload loaded by
  :class:`~presets.custom_store.CustomPresetStore` and exposed via
  :func:`~presets.get_room_presets`.

This modular layout keeps effect builders isolated, makes reversibility explicit
when it is required and allows new presets to be added by composing small,
well-documented pieces.
