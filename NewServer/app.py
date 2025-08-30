"""FastAPI server exposing lighting control endpoints."""

import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from mqtt import publish, request_status

app = FastAPI()

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    status = await asyncio.to_thread(request_status)
    ws = [s for s in status.get("ws", []) if s.get("enabled")]
    white = [w for w in status.get("white", []) if w.get("enabled")]
    sensors = status.get("sensors", {})
    has_motion = sensors.get("pir_enabled") or sensors.get("ultra_enabled")

    rssi = status.get("wifi_rssi")
    strength = None
    if isinstance(rssi, (int, float)):
        strength = max(0, min(100, 2 * (rssi + 100)))

    ws_effects = [
        "solid",
        "triple_wave",
        "breathe",
        "rainbow",
        "twinkle",
        "theater_chase",
        "wipe",
        "gradient_scroll",
    ]
    white_effects = [
        "graceful_on",
        "graceful_off",
        "motion_swell",
        "day_night_curve",
        "blink",
    ]
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "effects_ws": ws_effects,
            "effects_white": white_effects,
            "ws_count": len(ws),
            "white_count": len(white),
            "sensors": sensors if has_motion else None,
            "wifi_rssi": rssi,
            "wifi_strength": strength,
        },
    )


@app.post("/api/ws/set")
async def api_ws_set(payload: dict) -> dict:
    publish("cmd/ws/set", payload)
    return {"status": "ok"}


@app.post("/api/ws/power")
async def api_ws_power(payload: dict) -> dict:
    publish("cmd/ws/power", payload)
    return {"status": "ok"}


@app.post("/api/white/set")
async def api_white_set(payload: dict) -> dict:
    publish("cmd/white/set", payload)
    return {"status": "ok"}


@app.post("/api/white/power")
async def api_white_power(payload: dict) -> dict:
    publish("cmd/white/power", payload)
    return {"status": "ok"}


@app.post("/api/ota/check")
async def api_ota_check() -> dict:
    publish("cmd/ota/check", {})
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=5000, reload=True)
