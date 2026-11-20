"""Browser WebSocket: contracts/schemas/ws-client (in) and ws-server (out)."""
import asyncio
import contextlib
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import contracts
from ..bridge import NotConnected
from ..control import Control, ControlError
from ..db import sessionmaker
from ..hub import Client, Hub
from ..security import src_for, ws_auth
from ..views import devices_for, visible_home_ids

router = APIRouter()
log = logging.getLogger("ul.ws")


@router.websocket("/api/ws")
async def websocket(ws: WebSocket) -> None:
    async with sessionmaker()() as db:
        auth = await ws_auth(ws, db)
        if auth is None:
            await ws.close(code=4401)
            return
        homes = await visible_home_ids(db, auth.user)
        snapshot = await devices_for(db, auth.user)
    await ws.accept()
    hub: Hub = ws.app.state.hub
    control: Control = ws.app.state.control
    client = Client(ws, auth.user.id, auth.user.role == "admin", homes)
    hub.add(client)
    src = src_for(auth)
    await ws.send_text(json.dumps({"t": "snapshot", "devices": snapshot,
                                   "broker": ws.app.state.bridge.connected.is_set()}))

    async def writer() -> None:
        while True:
            await ws.send_text(await client.queue.get())

    async def send(msg: dict) -> None:
        await client.queue.put(json.dumps(msg))

    wtask = asyncio.create_task(writer())
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = contracts.validate("ws-client", json.loads(raw))
            except (ValueError, contracts.ContractError) as e:
                await send({"t": "error", "error": "invalid", "detail": str(e)[:200]})
                continue
            t = msg["t"]
            if t == "ping":
                await send({"t": "pong"})
                continue
            dev, out = msg["dev"], msg["out"]
            try:
                info = control.get(dev, out)
                if not (client.homes is None or (info.home_id is not None and info.home_id in client.homes)):
                    raise ControlError("no_such_output", f"{dev}/{out}")
                if t == "live":
                    control.live(dev, out, msg["state"], src)
                elif t == "set":
                    await control.set(dev, out, msg["state"], src)
            except ControlError as e:
                await send({"t": "error", "error": e.code, "detail": e.detail[:200], "dev": dev, "out": out})
            except NotConnected:
                await send({"t": "error", "error": "broker_offline", "dev": dev, "out": out})
    except WebSocketDisconnect:
        pass
    finally:
        hub.remove(client)
        wtask.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await wtask
