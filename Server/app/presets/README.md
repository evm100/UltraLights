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
└── catalog/               # House/room preset definitions built from actions
    ├── __init__.py
    ├── del_sur.py
    ├── sdsu.py
    └── shared.py
```

Importers continue to use ``from .presets import get_room_presets`` and friends,
but underneath each concern now lives in its own module.

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

House and room presets live under ``catalog/``. Each module returns a dictionary
mapping room identifiers to lists of presets. Preset definitions simply stitch
 together the reusable actions:

```python
from ..actions import white_swell_actions

PRESETS = {
    "my-room": [
        {
            "id": "wake-up",
            "name": "Wake Up",
            "actions": white_swell_actions(["my-room-node"], 0, 200, 4000),
        }
    ]
}
```

To add a new house, drop a module alongside ``del_sur.py`` and ``sdsu.py`` and
update ``catalog/__init__.py`` to include it in ``ROOM_PRESETS``. Presets can
freely mix existing action builders or new ones that you register.

## Quick reference

* ``presets.actions`` – reusable action builders such as
  :func:`~presets.actions.white.white_swell_action`,
  :func:`~presets.actions.color.ws_swell_action`,
  :func:`~presets.actions.color.rgb_swell_action` and
  :func:`~presets.actions.color.solid_color_action`.
* ``presets.action_registry`` – the registry instance plus helper functions
  (:func:`~presets.register_action`, :func:`~presets.reverse_action`,
  :func:`~presets.with_action_type`).
* ``presets.catalog`` – house/room preset data structures assembled from the
  action helpers.

This modular layout keeps effect builders isolated, makes reversibility explicit
and allows new presets to be added by composing small, well-documented pieces.
