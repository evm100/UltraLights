from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from .config import settings
from . import registry
from .effects import WS_EFFECTS, WHITE_EFFECTS

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    houses = settings.DEVICE_REGISTRY
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "houses": houses, "title": "UltraLights"},
    )


@router.get("/house/{house_id}", response_class=HTMLResponse)
def house_page(request: Request, house_id: str):
    house = registry.find_house(house_id)
    if not house:
        return templates.TemplateResponse(
            "base.html",
            {"request": request, "content": "Unknown house"},
            status_code=404,
        )
    return templates.TemplateResponse(
        "house.html",
        {
            "request": request,
            "house": house,
            "title": house.get("name", house_id),
            "subtitle": house.get("name", house_id),
        },
    )


@router.get("/house/{house_id}/room/{room_id}", response_class=HTMLResponse)
def room_page(request: Request, house_id: str, room_id: str):
    house, room = registry.find_room(house_id, room_id)
    if not room:
        return templates.TemplateResponse(
            "base.html",
            {"request": request, "content": "Unknown room"},
            status_code=404,
        )
    title = f"{house.get('name', house_id)} - {room.get('name', room_id)}"
    return templates.TemplateResponse(
        "room.html",
        {
            "request": request,
            "house": house,
            "room": room,
            "title": title,
            "subtitle": title,
        },
    )

@router.get("/node/{node_id}", response_class=HTMLResponse)
def node_page(request: Request, node_id: str):
    house, room, node = registry.find_node(node_id)
    if not node:
        return templates.TemplateResponse(
            "base.html", {"request": request, "content": "Unknown node"}, status_code=404
        )
    title = node.get("name", node_id)
    if room and house:
        subtitle = f"{house.get('name', house['id'])} • {room.get('name', room['id'])}"
    else:
        subtitle = None
    return templates.TemplateResponse(
        "node.html",
        {
            "request": request,
            "node": node,
            "title": title,
            "subtitle": subtitle,
            "ws_effects": WS_EFFECTS,
            "white_effects": WHITE_EFFECTS,
        },
    )
