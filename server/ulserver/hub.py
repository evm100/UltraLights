"""Fan-out of device updates to connected browsers (messages per contracts/schemas/ws-server).

Every message is filtered through the client's access: state goes only to people who can
see that output, device views are rendered per client (their outputs and abilities), and
health and events go to admins only.
"""
import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

from .access import Access, Target
from .models import Node, Output
from .views import device_view

log = logging.getLogger("ul.hub")

ACCESS_CHANGED = 4001  # close code: memberships changed, reconnect for a fresh snapshot


@dataclass(eq=False)
class Client:
    ws: WebSocket
    access: Access
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=512))

    @property
    def user_id(self) -> int:
        return self.access.user_id


def _dump(msg: dict[str, Any]) -> str:
    return json.dumps(msg, separators=(",", ":"))


class Hub:
    def __init__(self) -> None:
        self.clients: set[Client] = set()

    def add(self, c: Client) -> None:
        self.clients.add(c)

    def remove(self, c: Client) -> None:
        self.clients.discard(c)

    def _put(self, c: Client, data: str) -> None:
        try:
            c.queue.put_nowait(data)
        except asyncio.QueueFull:
            log.warning("client %s too slow, dropping connection", c.user_id)
            self._drop(c, 1013)

    def _drop(self, c: Client, code: int) -> None:
        self.clients.discard(c)
        if c.ws is not None:
            asyncio.get_running_loop().create_task(c.ws.close(code=code))

    def broadcast_output(self, msg: dict[str, Any], t: Target) -> None:
        data = _dump(msg)
        for c in list(self.clients):
            if c.access.sees(t):
                self._put(c, data)

    def broadcast_admin(self, msg: dict[str, Any]) -> None:
        data = _dump(msg)
        for c in list(self.clients):
            if c.access.admin:
                self._put(c, data)

    def broadcast_device(self, node: Node, outputs: list[Output]) -> None:
        """Render the device once per distinct access and send each client its own view."""
        cache: dict[Any, str | None] = {}
        for c in list(self.clients):
            k = c.access.view_key()
            if k not in cache:
                v = device_view(node, outputs, c.access)
                cache[k] = None if v is None else _dump({"t": "device", "device": v})
            if cache[k] is not None:
                self._put(c, cache[k])

    def refresh_user(self, user_id: int) -> None:
        """Their access changed: close their sockets so they reconnect with a fresh snapshot."""
        for c in [c for c in self.clients if c.user_id == user_id]:
            self._drop(c, ACCESS_CHANGED)
