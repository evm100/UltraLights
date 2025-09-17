import asyncio, signal
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from .config import settings
from .routes_api import router as api_router
from .routes_pages import router as pages_router
from .ota import router as ota_router
from .motion import motion_manager
from .status_monitor import status_monitor
from fastapi.responses import FileResponse, Response
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# Optional embedded broker
try:
    from hbmqtt.broker import Broker
    HBMQTT_AVAILABLE = True
except Exception:
    HBMQTT_AVAILABLE = False

app = FastAPI(title="UltraLights Hub", version="2.0")
app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(pages_router)
app.include_router(api_router)
app.include_router(ota_router)

BROKER = None

@app.on_event("startup")
async def on_start():
    global BROKER
    if settings.EMBED_BROKER:
        if not HBMQTT_AVAILABLE:
            raise RuntimeError("EMBED_BROKER=1 but 'hbmqtt' not installed")
        BROKER = Broker({
            "listeners": {"default": {"type":"tcp","bind": f"{settings.BROKER_HOST}:{settings.BROKER_PORT}"}},
            "auth": {"allow-anonymous": True},
            "topic-check": {"enabled": False}
        })
        asyncio.create_task(BROKER.start())
        await asyncio.sleep(0.6)
    motion_manager.start()
    status_monitor.start()

@app.on_event("shutdown")
async def on_stop():
    if BROKER:
        await BROKER.shutdown()
    motion_manager.stop()
    status_monitor.stop()


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
