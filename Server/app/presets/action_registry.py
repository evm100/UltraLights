from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Dict, List

ActionDict = Dict[str, Any]
Builder = Callable[..., ActionDict]
Reverser = Callable[[ActionDict], ActionDict]


class ActionRegistry:
    """Registry that tracks action builders and how to reverse them.

    The registry provides a central place for associating an ``action_type``
    string with the callable that builds the action payload and the callable
    that knows how to reverse that payload.  Action dictionaries returned by
    registered builders are automatically annotated with ``_action_type`` so
    that :func:`reverse` can operate without any preset-specific knowledge.
    """

    def __init__(self) -> None:
        self._builders: Dict[str, Builder] = {}
        self._reversers: Dict[str, Reverser] = {}

    def register(self, action_type: str, *, reverser: Reverser) -> Callable[[Builder], Builder]:
        """Register ``action_type`` with the given builder and reverser.

        Usage::

            @register_action("white.swell", reverser=_reverse_white_swell)
            def white_swell(...):
                return {"module": "white", ...}

        The decorator ensures that every returned payload includes
        ``_action_type`` so that the universal :func:`reverse` implementation
        can look up the appropriate reverser.
        """

        def decorator(builder: Builder) -> Builder:
            self._builders[action_type] = builder
            self._reversers[action_type] = reverser

            def wrapper(*args: Any, **kwargs: Any) -> ActionDict:
                payload = builder(*args, **kwargs)
                if not isinstance(payload, dict):
                    raise TypeError("Action builders must return a dictionary.")
                return self.apply_metadata(action_type, payload)

            wrapper.__name__ = builder.__name__
            wrapper.__doc__ = builder.__doc__
            return wrapper  # type: ignore[return-value]

        return decorator

    def apply_metadata(self, action_type: str, payload: ActionDict) -> ActionDict:
        """Attach ``_action_type`` metadata to ``payload``.

        This helper is handy when a preset builds the action dictionary by
        hand but still wants to take advantage of the registry's reversing
        capabilities.
        """

        if action_type not in self._reversers:
            raise KeyError(f"Action type '{action_type}' has not been registered.")
        result = deepcopy(payload)
        result["_action_type"] = action_type
        return result

    def reverse(self, action: ActionDict) -> ActionDict:
        """Return a reversed copy of ``action``.

        Raises a :class:`ValueError` if ``_action_type`` metadata is missing and
        :class:`KeyError` if no reverser has been registered for the action
        type.  Callers may choose to catch these exceptions if they prefer to
        ignore non-reversible actions.
        """

        action_type = action.get("_action_type")
        if action_type is None:
            raise ValueError("Preset action is missing '_action_type' metadata.")

        reverser = self._reversers.get(action_type)
        if reverser is None:
            raise KeyError(f"No reverser registered for action type '{action_type}'.")

        reversed_action = reverser(deepcopy(action))
        if not isinstance(reversed_action, dict):
            raise TypeError("Action reverser must return a dictionary.")
        reversed_action["_action_type"] = action_type
        return reversed_action

    def available_action_types(self) -> List[str]:
        """Return the sorted list of registered action types."""

        return sorted(self._reversers.keys())


action_registry = ActionRegistry()
register_action = action_registry.register
with_action_type = action_registry.apply_metadata
reverse_action = action_registry.reverse

__all__ = [
    "ActionDict",
    "ActionRegistry",
    "action_registry",
    "register_action",
    "reverse_action",
    "with_action_type",
]
