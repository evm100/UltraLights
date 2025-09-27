import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import registry
from .account_linker import account_linker
from .auth import SESSION_TOKEN_TTL_SECONDS, init_auth_storage
from .auth.throttling import reset_login_rate_limiter
from .config import settings
from .database import get_session
from .motion import motion_manager
from .ota import router as ota_router
from .routes_api import router as api_router
from .routes_house_admin import router as house_admin_router
from .routes_pages import router as pages_router
from .routes_server_admin import router as server_admin_router
from .status_monitor import status_monitor

STATIC_DIR = Path(__file__).resolve().parent / "static"

# Optional embedded broker
try:
    from hbmqtt.broker import Broker
    HBMQTT_AVAILABLE = True
except Exception:
    HBMQTT_AVAILABLE = False

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    broker: Optional[Any] = None
    broker_task: Optional[asyncio.Task[Any]] = None

    if settings.EMBED_BROKER:
        if not HBMQTT_AVAILABLE:
            raise RuntimeError("EMBED_BROKER=1 but 'hbmqtt' not installed")
        broker = Broker(
            {
                "listeners": {
                    "default": {
                        "type": "tcp",
                        "bind": f"{settings.BROKER_HOST}:{settings.BROKER_PORT}",
                    }
                },
                "auth": {"allow-anonymous": True},
                "topic-check": {"enabled": False},
            }
        )
        broker_task = asyncio.create_task(broker.start())
        await asyncio.sleep(0.6)

    motion_manager.start()
    status_monitor.start()
    account_linker.start()

    registry.ensure_house_external_ids()
    init_auth_storage()
    reset_login_rate_limiter()
    app.dependency_overrides[get_session] = get_session

    try:
        yield
    finally:
        app.dependency_overrides.pop(get_session, None)
        if broker:
            await broker.shutdown()
        if broker_task:
            try:
                await broker_task
            except asyncio.CancelledError:
                pass
        motion_manager.stop()
        status_monitor.stop()
        account_linker.stop()


app = FastAPI(title="UltraLights Hub", version="2.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SESSION_SECRET,
    session_cookie="ultralights_state",
    max_age=SESSION_TOKEN_TTL_SECONDS,
    same_site="lax",
    https_only=settings.PUBLIC_BASE.startswith("https://"),
)

app.include_router(pages_router)
app.include_router(api_router)
app.include_router(house_admin_router)
app.include_router(server_admin_router)
app.include_router(ota_router)


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    ico = STATIC_DIR / "favicon.ico"
    png = STATIC_DIR / "favicon.png"
    svg = STATIC_DIR / "favicon.svg"
    if ico.exists():
        return FileResponse(ico, media_type="image/x-icon")
    if png.exists():
        return FileResponse(png, media_type="image/png")
    if svg.exists():
        return FileResponse(svg, media_type="image/svg+xml")
    # no file yet – return 204 instead of 404 to stop error spam
    return Response(status_code=204)
