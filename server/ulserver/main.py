"""FastAPI app: REST API, WebSocket, enrollment, and the MQTT bridge as a background task."""
import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from fastapi import FastAPI

from . import db as dbm
from .api import admin, auth, devices, enroll_api, ws
from .bridge import Bridge
from .config import get_settings
from .control import Control
from .enroll import RateLimiter, write_crl
from .hub import Hub
from .pki import DevicePKI

log = logging.getLogger("ul")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
    sm = dbm.sessionmaker()
    hub = Hub()
    pki = DevicePKI(s.pki_dir)
    bridge = Bridge(s, hub, sm)
    control = Control(bridge.publish, sm)
    control.cmd_waiters = bridge.cmd_waiters
    bridge.control = control
    app.state.settings = s
    app.state.hub = hub
    app.state.pki = pki
    app.state.bridge = bridge
    app.state.control = control
    app.state.enroll_limiter = RateLimiter(s.enroll_rate_per_min)
    async with sm() as db:
        await control.load_all(db)
        if pki.initialized:
            await write_crl(db, pki)
        else:
            log.warning("device CA not initialized: run `ulctl ca init`; enrollment is disabled")
    task = None
    if s.bridge_enabled and pki.initialized and (s.pki_dir / "ul-server.crt").exists():
        task = asyncio.create_task(bridge.run(), name="mqtt-bridge")
    elif s.bridge_enabled:
        log.warning("bridge certificate missing: run `ulctl ca init`; MQTT bridge not started")
    try:
        yield
    finally:
        bridge.stop()
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await dbm.dispose()


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = FastAPI(title="UltraLights", version="0.1.0", lifespan=lifespan, docs_url="/api/docs",
                  openapi_url="/api/openapi.json", redoc_url=None)
    app.include_router(enroll_api.router)
    app.include_router(auth.router)
    app.include_router(devices.router)
    app.include_router(admin.router)
    app.include_router(ws.router)

    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True, "broker": app.state.bridge.connected.is_set()}

    return app


app = create_app()
