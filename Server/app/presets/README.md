# Preset architecture

The ``Server/app/presets`` package turns the monolithic ``presets.py`` module
into a collection of small, composable pieces:

```
presets/
├── __init__.py            # Public API used by the rest of the application
├── README.md              # You are here
├── action_registry.py     # Keeps track of action types and how to reverse them
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
``_action_type`` metadata required for reversal.

```python
from .presets import register_action


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
```

The decorator stores both the builder and the ``_reverse_fade`` callback. When
``reverse_preset`` is invoked, it looks up the action type and calls the
registered reverser. If a preset action is created manually (without the
 decorator), use :func:`~presets.with_action_type` to attach metadata::

```python
manual_action = with_action_type("my-module.fade", {...})
```

If an action lacks metadata or no reverser is registered, ``reverse_preset``
falls back to copying the original action unchanged.

### Reversing custom actions

Reversers receive a deep copy of the action dictionary and must return a new
payload. They may inspect custom metadata to decide how to reverse. For example
``actions/color.py`` stores ``_reverse_meta`` with the target color so that
 calling ``reverse_preset`` swaps between the active color and the fallback (by
 default, full black).

### Color swell helpers

In addition to solid colors, ``actions.color`` provides
:func:`~presets.actions.color.ws_swell_action` and
:func:`~presets.actions.color.rgb_swell_action`.  Both helpers drive the
``color_swell`` effect introduced for the WS and RGB engines.  Callers provide
the node identifier, strip index, RGB color components and the swell parameters
(starting brightness, ending brightness and duration in milliseconds).  The
shared reverser swaps the start and end brightness values so that
:func:`~presets.reverse_preset` can automatically dim a swell that originally
brightened a strip—and vice versa.

When a reverser changes fields that future reversals depend on, it should also
update any metadata accordingly so the operation remains symmetric. The existing
``solid_color_action`` implementation demonstrates this by preserving the
previous color inside ``_reverse_meta``.

## Defining presets

Presets are stored in ``custom_presets.json`` as a mapping of
``{house_id: {room_id: [preset, ...]}}``.  Each preset mirrors the structure
returned by :func:`~presets.get_room_presets` and contains an ``id``, ``name``
and ``actions`` list.  The seeded entries bundled with the repository were
generated from the original Python catalog, but operators typically manage the
file indirectly: the admin UI snapshots device state to create new presets, and
the API exposes CRUD endpoints that update the JSON store safely.

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
and allows new presets to be added by composing small, well-documented pieces.
