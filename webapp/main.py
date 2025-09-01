from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import mqtt
from .models import NodeConfig, RGBStrip, WhiteChannel, Sensor

# Example node configuration. Additional nodes can be added to this
# dictionary and will automatically appear on the home page.
NODES = {
    "demo": NodeConfig(
        node_id="demo",
        rgb_strips=[RGBStrip(0, "Strip 0"), RGBStrip(1, "Strip 1")],
        white_channels=[WhiteChannel(0, "White 0")],
        sensors=[Sensor("motion", "Motion Sensor")],
    ),
}

app = FastAPI(title="UltraNode Controller")

app.mount("/static", StaticFiles(directory="webapp/static"), name="static")
templates = Jinja2Templates(directory="webapp/templates")


@app.on_event("startup")
def startup_event():
    mqtt.init_client()


@app.on_event("shutdown")
def shutdown_event():
    mqtt.stop_client()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "nodes": NODES.values()})


@app.get("/node/{node_id}", response_class=HTMLResponse)
async def node_page(node_id: str, request: Request):
    node = NODES.get(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Unknown node")
    return templates.TemplateResponse("node.html", {"request": request, "node": node})


@app.get("/api/nodes/{node_id}/status")
async def node_status(node_id: str):
    return mqtt.get_status(node_id)


@app.post("/api/nodes/{node_id}/ws/set")
async def cmd_ws_set(node_id: str, payload: dict):
    mqtt.publish(node_id, "ws/set", payload)
    return {"ok": True}


@app.post("/api/nodes/{node_id}/ws/power")
async def cmd_ws_power(node_id: str, payload: dict):
    mqtt.publish(node_id, "ws/power", payload)
    return {"ok": True}


@app.post("/api/nodes/{node_id}/white/set")
async def cmd_white_set(node_id: str, payload: dict):
    mqtt.publish(node_id, "white/set", payload)
    return {"ok": True}


@app.post("/api/nodes/{node_id}/sensor/cooldown")
async def cmd_sensor_cooldown(node_id: str, payload: dict):
    mqtt.publish(node_id, "sensor/cooldown", payload)
    return {"ok": True}


@app.post("/api/nodes/{node_id}/ota/check")
async def cmd_ota_check(node_id: str):
    mqtt.publish(node_id, "ota/check", {})
    return {"ok": True}
