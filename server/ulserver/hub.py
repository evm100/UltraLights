"""Fan-out of device updates to connected browsers (messages per contracts/schemas/ws-server)."""
import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

log = logging.getLogger("ul.hub")


@dataclass(eq=False)
class Client:
    ws: WebSocket
    user_id: int
    admin: bool
    homes: set[int] | None  # None = all
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=512))

    def sees(self, home_id: int | None) -> bool:
        return self.homes is None or (home_id is not None and home_id in self.homes)


class Hub:
    def __init__(self) -> None:
        self.clients: set[Client] = set()

    def add(self, c: Client) -> None:
        self.clients.add(c)

    def remove(self, c: Client) -> None:
        self.clients.discard(c)

    def broadcast(self, msg: dict[str, Any], home_id: int | None, admin_only: bool = False,
                  admin_msg: dict[str, Any] | None = None) -> None:
        """admin_msg, if given, replaces msg for admin clients (richer device views)."""
        data = json.dumps(msg, separators=(",", ":"))
        admin_data = json.dumps(admin_msg, separators=(",", ":")) if admin_msg is not None else data
        for c in list(self.clients):
            if admin_only and not c.admin:
                continue
            if not c.sees(home_id):
                continue
            try:
                c.queue.put_nowait(admin_data if c.admin else data)
            except asyncio.QueueFull:
                log.warning("client %s too slow, dropping connection", c.user_id)
                self.clients.discard(c)
                asyncio.get_running_loop().create_task(c.ws.close(code=1013))

    def refresh_user(self, user_id: int, homes: set[int] | None) -> None:
        for c in self.clients:
            if c.user_id == user_id:
                c.homes = homes
