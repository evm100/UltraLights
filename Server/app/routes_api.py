from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlmodel import Session, select
from .mqtt_bus import MqttBus
from . import node_credentials, registry
from .auth.access import AccessPolicy, HouseContext
from .auth.dependencies import get_current_user
from .auth.models import House, User
from .effects import WS_EFFECTS, WHITE_EFFECTS, RGB_EFFECTS
from .presets import (
    get_preset,
    apply_preset,
    get_room_presets,
    save_custom_preset,
    delete_custom_preset,
    reorder_custom_presets,
    snapshot_to_actions,
)
from .motion import motion_manager
from .motion_schedule import motion_schedule
from .motion_prefs import motion_preferences
from .status_monitor import status_monitor
from .brightness_limits import brightness_limits
from .channel_names import channel_names
from .config import settings
from .database import get_session


router = APIRouter()
logger = logging.getLogger(__name__)
BUS: Optional[MqttBus] = None

DEFAULT_SNAPSHOT_TIMEOUT = 3.0
MAX_CUSTOM_PRESET_NAME_LENGTH = 64
MAX_NODE_NAME_LENGTH = 120


def _build_policy(session: Session, user: User) -> AccessPolicy:
    return AccessPolicy.from_session(session, user)


class AssignNodePayload(BaseModel):
    """Request body for assigning a pending node to a room."""

    node_id: str = Field(..., alias="nodeId")
    room_id: str = Field(..., alias="roomId")
    name: Optional[str] = Field(None, alias="name", max_length=MAX_NODE_NAME_LENGTH)

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("node_id", "room_id")
    @classmethod
    def _require_value(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("must be a string")
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must be a non-empty string")
        return cleaned

    @field_validator("name", mode="before")
    @classmethod
    def _clean_name(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None


class MoveNodePayload(BaseModel):
    """Request body for moving an assigned node to another room."""

    room_id: str = Field(..., alias="roomId")
    name: Optional[str] = Field(None, alias="name", max_length=MAX_NODE_NAME_LENGTH)

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("room_id")
    @classmethod
    def _clean_room(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("must be a string")
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must be a non-empty string")
        return cleaned

    @field_validator("name", mode="before")
    @classmethod
    def _clean_name(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None


def _require_house(policy: AccessPolicy, house_id: str):
    try:
        return policy.ensure_house(house_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown house") from exc
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden") from exc


def _require_room(policy: AccessPolicy, house_id: str, room_id: str):
    try:
        return policy.ensure_room(house_id, room_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown room") from exc
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden") from exc


def _require_node(policy: AccessPolicy, node_id: str):
    try:
        return policy.ensure_node(node_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown node id") from exc
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden") from exc


def _ensure_can_manage_house(house_ctx: HouseContext, user: User) -> None:
    if not house_ctx.access.can_manage(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")


def _house_record(session: Session, house_ctx: HouseContext) -> Optional[House]:
    house = house_ctx.access.house
    if house is not None:
        return house
    return session.exec(
        select(House).where(House.external_id == house_ctx.external_id)
    ).first()


def get_bus() -> MqttBus:
    global BUS
    if BUS is None:
        BUS = MqttBus()
    return BUS


def _valid_node(node_id: str) -> Dict[str, Any]:
    _, _, node = registry.find_node(node_id)
    if node:
        return node
    raise HTTPException(404, "Unknown node id")


def _normalize_limits(raw_limits: Dict[str, Dict[str, int]]) -> Dict[str, Dict[str, int]]:
    cleaned: Dict[str, Dict[str, int]] = {}
    for module, channels in raw_limits.items():
        if not isinstance(channels, dict):
            continue
        module_key = str(module)
        module_limits: Dict[str, int] = {}
        for channel, value in channels.items():
            channel_key = str(channel)
            try:
                limit_value = int(value)
            except (TypeError, ValueError):
                continue
            module_limits[channel_key] = max(0, min(255, limit_value))
        if module_limits:
            cleaned[module_key] = module_limits
    return cleaned


def _normalize_light_entries(
    items: Any,
    index_key: str,
    limits: Dict[str, int],
    *,
    include_color: bool = False,
    names: Optional[Dict[str, str]] = None,
) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    if not isinstance(items, list):
        return result
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_index = item.get(index_key)
        try:
            index = int(raw_index)
        except (TypeError, ValueError):
            continue
        entry: Dict[str, Any] = {}
        entry["enabled"] = bool(item.get("enabled"))
        effect = item.get("effect")
        if isinstance(effect, str):
            entry["effect"] = effect
        brightness = item.get("brightness")
        if isinstance(brightness, (int, float)):
            entry["brightness"] = max(0, min(255, int(brightness)))
        params = item.get("params")
        if isinstance(params, list):
            clean_params: List[Any] = []
            for value in params:
                if isinstance(value, (int, float)):
                    clean_params.append(float(value))
                elif isinstance(value, str):
                    clean_params.append(value)
            entry["params"] = clean_params
        if include_color:
            color = item.get("color")
            if isinstance(color, list):
                clean_color: List[int] = []
                for value in color[:3]:
                    try:
                        clean_color.append(max(0, min(255, int(value))))
                    except (TypeError, ValueError):
                        clean_color.append(0)
                if clean_color:
                    while len(clean_color) < 3:
                        clean_color.append(0)
                    entry["color"] = clean_color[:3]
        name_value: Optional[str] = None
        raw_name = item.get("name")
        if isinstance(raw_name, str):
            clean_name = raw_name.strip()
            if clean_name:
                name_value = clean_name
        if isinstance(names, dict):
            stored = names.get(str(index))
            if isinstance(stored, str):
                clean_stored = stored.strip()
                if clean_stored:
                    name_value = clean_stored
        if name_value:
            entry["name"] = name_value

        limit = limits.get(str(index))
        if limit is not None:
            entry["limit"] = int(limit)
        result[str(index)] = entry
    return result


def _format_node_state(
    node_id: str,
    node: Dict[str, Any],
    seq: int,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    uptime_value: Optional[int] = None
    uptime = payload.get("uptime_s")
    if isinstance(uptime, (int, float)):
        uptime_value = max(0, int(uptime))

    limits = _normalize_limits(brightness_limits.get_limits_for_node(node_id))
    name_map = channel_names.get_names_for_node(node_id)

    modules: Dict[str, Any] = {}
    available: set[str] = set()

    ws_state = _normalize_light_entries(
        payload.get("ws"),
        "strip",
        limits.get("ws", {}),
        include_color=True,
        names=name_map.get("ws", {}),
    )
    if ws_state:
        modules["ws"] = ws_state
        available.add("ws")

    rgb_state = _normalize_light_entries(
        payload.get("rgb"),
        "strip",
        limits.get("rgb", {}),
        include_color=True,
        names=name_map.get("rgb", {}),
    )
    if rgb_state:
        modules["rgb"] = rgb_state
        available.add("rgb")

    white_state = _normalize_light_entries(
        payload.get("white"),
        "channel",
        limits.get("white", {}),
        names=name_map.get("white", {}),
    )
    if white_state:
        modules["white"] = white_state
        available.add("white")

    ota_payload = payload.get("ota")
    if isinstance(ota_payload, dict) and ota_payload:
        modules["ota"] = ota_payload
        available.add("ota")

    registry_modules = node.get("modules")
    if isinstance(registry_modules, list):
        for mod in registry_modules:
            mod_key = str(mod)
            if mod_key == "sensor":
                continue
            if mod_key not in {"ws", "rgb", "white"}:
                available.add(mod_key)

    available_modules = sorted(mod for mod in available if mod != "sensor")
    if not available_modules and isinstance(registry_modules, list):
        available_modules = [
            str(mod)
            for mod in registry_modules
            if str(mod) != "sensor"
        ]

    return {
        "node": node_id,
        "seq": seq,
        "uptime_s": uptime_value,
        "modules": modules,
        "limits": limits,
        "available_modules": available_modules,
    }


def _module_index_sort_key(value: Any) -> tuple[int, Any]:
    try:
        return (0, int(str(value)))
    except (TypeError, ValueError):
        return (1, str(value))


def _modules_to_snapshot(modules: Dict[str, Any]) -> Dict[str, Any]:
    snapshot: Dict[str, Any] = {}
    if not isinstance(modules, dict):
        return snapshot

    for module_name, module_state in modules.items():
        if not isinstance(module_state, dict):
            continue

        if module_name == "white":
            entries: List[Dict[str, Any]] = []
            for key, entry in sorted(module_state.items(), key=lambda item: _module_index_sort_key(item[0])):
                if not isinstance(entry, dict):
                    continue
                try:
                    channel = int(str(key))
                except (TypeError, ValueError):
                    continue
                payload_entry = dict(entry)
                payload_entry["channel"] = channel
                entries.append(payload_entry)
            if entries:
                snapshot["white"] = entries
            continue

        if module_name in {"ws", "rgb"}:
            entries = []
            for key, entry in sorted(module_state.items(), key=lambda item: _module_index_sort_key(item[0])):
                if not isinstance(entry, dict):
                    continue
                try:
                    strip = int(str(key))
                except (TypeError, ValueError):
                    continue
                payload_entry = dict(entry)
                payload_entry["strip"] = strip
                entries.append(payload_entry)
            if entries:
                snapshot[module_name] = entries

    return snapshot


@router.delete("/api/node/{node_id}")
def api_remove_node(
    node_id: str,
    *,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    policy = _build_policy(session, current_user)
    registration = node_credentials.get_registration_by_node_id(session, node_id)
    credential = node_credentials.get_by_node_id(session, node_id)

    house_entry, room_entry, _ = registry.find_node(node_id)

    if room_entry is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Unassign this node from its room before removing it.",
        )

    if registration and registration.room_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Unassign this node from its room before removing it.",
        )
    if credential and credential.room_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Unassign this node from its room before removing it.",
        )

    house_slug: Optional[str] = None
    if house_entry is not None:
        house_slug = registry.get_house_slug(house_entry)
    elif registration and registration.house_slug:
        house_slug = registration.house_slug
    elif credential and credential.house_slug:
        house_slug = credential.house_slug

    if not house_slug:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown node id")

    house_ctx = _require_house(policy, house_slug)
    _ensure_can_manage_house(house_ctx, current_user)

    removed = None
    try:
        removed = registry.remove_node(node_id)
    except KeyError:
        removed = None
    node_credentials.delete_credentials(session, node_id)

    try:
        bus = get_bus()
    except Exception:
        logger.exception("Failed to acquire MQTT bus when removing node %s", node_id)
    else:
        try:
            bus.wipe_nvs(node_id)
        except Exception:
            logger.exception("Failed to queue NVS wipe command for node %s", node_id)

    motion_manager.forget_node(node_id)
    status_monitor.forget(node_id)
    payload: Dict[str, Any] = {"ok": True}
    if removed is not None:
        payload["node"] = removed
    else:
        payload["node_id"] = node_id
    return payload


@router.post("/api/node/{node_id}/name")
def api_set_node_name(
    node_id: str,
    payload: Dict[str, Any],
    *,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    policy = _build_policy(session, current_user)
    node_ctx = _require_node(policy, node_id)
    _ensure_can_manage_house(node_ctx.room.house, current_user)
    raw_name = payload.get("name")
    if raw_name is None:
        raise HTTPException(400, "missing name")
    if not isinstance(raw_name, str):
        raise HTTPException(400, "invalid name")
    clean_name = raw_name.strip()
    if not clean_name:
        raise HTTPException(400, "invalid name")
    if len(clean_name) > MAX_NODE_NAME_LENGTH:
        raise HTTPException(400, "name too long")
    try:
        node = registry.set_node_name(node_id, clean_name)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown node id")
    node_credentials.ensure_for_node(
        session,
        node_id=node_id,
        house_slug=node_ctx.room.house.slug,
        room_id=node_ctx.room.room["id"],
        display_name=clean_name,
    )
    motion_manager.update_node_name(node_id, clean_name)
    return {"ok": True, "node": node}


@router.post("/api/house/{house_id}/rooms")
def api_add_room(
    house_id: str,
    payload: Dict[str, str],
    *,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    policy = _build_policy(session, current_user)
    house_ctx = _require_house(policy, house_id)
    _ensure_can_manage_house(house_ctx, current_user)
    name = str(payload.get("name", "")).strip()
    if not name:
        raise HTTPException(400, "missing name")
    try:
        room = registry.add_room(house_id, name)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown house")
    return {"ok": True, "room": room}


@router.post("/api/house/{house_id}/rooms/reorder")
def api_reorder_rooms(
    house_id: str,
    payload: Dict[str, Any],
    *,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    policy = _build_policy(session, current_user)
    house_ctx = _require_house(policy, house_id)
    _ensure_can_manage_house(house_ctx, current_user)
    order = payload.get("order")
    if not isinstance(order, list):
        raise HTTPException(400, "missing order")
    try:
        new_order = registry.reorder_rooms(house_id, order)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown house")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "order": [str(room.get("id")) for room in new_order]}


@router.delete("/api/house/{house_id}/rooms/{room_id}")
def api_delete_room(
    house_id: str,
    room_id: str,
    *,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    policy = _build_policy(session, current_user)
    room_ctx = _require_room(policy, house_id, room_id)
    _ensure_can_manage_house(room_ctx.house, current_user)

    seen: set[str] = set()
    node_ids: List[str] = []
    nodes = room_ctx.room.get("nodes")
    if isinstance(nodes, list):
        for entry in nodes:
            if not isinstance(entry, dict):
                continue
            raw_id = entry.get("id")
            if not isinstance(raw_id, str):
                continue
            node_id = raw_id.strip()
            if not node_id or node_id in seen:
                continue
            seen.add(node_id)
            node_ids.append(node_id)

    try:
        removed = registry.remove_room(house_id, room_id)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown room")

    for node_id in node_ids:
        motion_manager.forget_node(node_id)
        status_monitor.forget(node_id)
        node_credentials.delete_credentials(session, node_id)

    motion_manager.forget_room(room_ctx.house.slug, room_id)
    motion_schedule.remove_room(room_ctx.house.slug, room_id)

    return {"ok": True, "room": removed, "removed_nodes": node_ids}


@router.post("/api/house/{house_id}/nodes/assign")
def api_assign_pending_node(
    house_id: str,
    payload: AssignNodePayload,
    *,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    policy = _build_policy(session, current_user)
    house_ctx = _require_house(policy, house_id)
    _ensure_can_manage_house(house_ctx, current_user)
    room_ctx = _require_room(policy, house_id, payload.room_id)

    registration = node_credentials.get_registration_by_node_id(
        session, payload.node_id
    )
    if registration is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown node id")
    if registration.room_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Node already assigned")
    if (
        registration.assigned_user_id not in (None, current_user.id)
        and not current_user.server_admin
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")

    desired_name = (
        payload.name
        or registration.display_name
        or registration.node_id
        or payload.node_id
    )

    house_db = _house_record(session, house_ctx)
    assigned_house_id = house_db.id if house_db is not None else None
    assigned_user_id = registration.assigned_user_id or current_user.id

    try:
        updated = node_credentials.assign_registration_to_room(
            session,
            node_id=payload.node_id,
            house_slug=house_ctx.slug,
            room_id=payload.room_id,
            display_name=desired_name,
            assigned_house_id=assigned_house_id,
            assigned_user_id=assigned_user_id,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    room_name = str(room_ctx.room.get("name") or room_ctx.room.get("id") or "")
    return {
        "ok": True,
        "node": {
            "id": updated.node_id,
            "name": updated.display_name,
            "roomId": updated.room_id,
            "roomName": room_name,
            "house": updated.house_slug,
        },
    }


@router.post("/api/house/{house_id}/nodes/{node_id}/move")
def api_move_node(
    house_id: str,
    node_id: str,
    payload: MoveNodePayload,
    *,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    policy = _build_policy(session, current_user)
    house_ctx = _require_house(policy, house_id)
    _ensure_can_manage_house(house_ctx, current_user)
    room_ctx = _require_room(policy, house_id, payload.room_id)

    registration = node_credentials.get_registration_by_node_id(session, node_id)
    credential = node_credentials.get_by_node_id(session, node_id)
    if registration is None and credential is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown node id")

    desired_name = (
        payload.name
        or (registration.display_name if registration and registration.display_name else None)
        or (credential.display_name if credential else None)
        or node_id
    )

    assigned_house_id = (
        registration.assigned_house_id if registration else None
    )
    if assigned_house_id is None:
        house_db = _house_record(session, house_ctx)
        assigned_house_id = house_db.id if house_db is not None else None

    assigned_user_id = registration.assigned_user_id if registration else None

    try:
        updated = node_credentials.assign_registration_to_room(
            session,
            node_id=node_id,
            house_slug=house_ctx.slug,
            room_id=payload.room_id,
            display_name=desired_name,
            assigned_house_id=assigned_house_id,
            assigned_user_id=assigned_user_id,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    room_name = str(room_ctx.room.get("name") or room_ctx.room.get("id") or "")
    return {
        "ok": True,
        "node": {
            "id": updated.node_id,
            "name": updated.display_name,
            "roomId": updated.room_id,
            "roomName": room_name,
            "house": updated.house_slug,
        },
    }


@router.post("/api/house/{house_id}/nodes/{node_id}/unassign")
def api_unassign_node(
    house_id: str,
    node_id: str,
    *,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    policy = _build_policy(session, current_user)
    house_ctx = _require_house(policy, house_id)
    _ensure_can_manage_house(house_ctx, current_user)

    registration = node_credentials.get_registration_by_node_id(session, node_id)
    credential = node_credentials.get_by_node_id(session, node_id)
    if registration is None and credential is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown node id")

    modules_for_registry: Optional[List[str]] = None
    _, _, existing_node = registry.find_node(node_id)
    if existing_node is not None:
        raw_modules = existing_node.get("modules")
        if isinstance(raw_modules, list):
            cleaned: List[str] = []
            for entry in raw_modules:
                text = str(entry).strip()
                if text:
                    cleaned.append(text)
            modules_for_registry = cleaned or None

    try:
        updated = node_credentials.unassign_node(
            session,
            node_id=node_id,
            assigned_user_id=current_user.id,
        )
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    metadata = (
        updated.hardware_metadata
        if isinstance(updated.hardware_metadata, dict)
        else {}
    )
    if not modules_for_registry:
        raw_meta_modules = metadata.get("modules")
        if isinstance(raw_meta_modules, list):
            cleaned_meta: List[str] = []
            for entry in raw_meta_modules:
                text = str(entry).strip()
                if text:
                    cleaned_meta.append(text)
            modules_for_registry = cleaned_meta or None

    try:
        registry.move_node_to_unassigned(
            node_id,
            house_ctx.slug,
            name=updated.display_name or node_id,
            modules=modules_for_registry,
        )
    except KeyError:
        pass

    motion_manager.forget_node(node_id)
    status_monitor.forget(node_id)

    return {
        "ok": True,
        "node": {
            "id": updated.node_id,
            "name": updated.display_name,
            "house": updated.house_slug,
            "roomId": updated.room_id,
        },
    }


@router.post("/api/house/{house_id}/room/{room_id}/nodes")
def api_add_node(
    house_id: str,
    room_id: str,
    _payload: Dict[str, Any],
    *,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    policy = _build_policy(session, current_user)
    room_ctx = _require_room(policy, house_id, room_id)
    _ensure_can_manage_house(room_ctx.house, current_user)
    raw_name = _payload.get("name")
    if raw_name is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing name")
    if not isinstance(raw_name, str):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid name")

    clean_name = raw_name.strip()
    if not clean_name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid name")
    if len(clean_name) > MAX_NODE_NAME_LENGTH:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "name too long")

    modules_value = _payload.get("modules")
    modules: Optional[List[str]] = None
    if modules_value is not None:
        if not isinstance(modules_value, list):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid modules")
        cleaned_modules: List[str] = []
        for entry in modules_value:
            text = str(entry).strip()
            if text:
                cleaned_modules.append(text)
        modules = cleaned_modules or None

    try:
        node_entry = registry.add_node(
            house_id,
            room_id,
            clean_name,
            modules=modules,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    house_record = _house_record(session, room_ctx.house)
    ensured = node_credentials.ensure_for_node(
        session,
        node_id=node_entry["id"],
        house_slug=room_ctx.house.slug,
        room_id=room_ctx.room["id"],
        display_name=clean_name,
        assigned_house_id=house_record.id if house_record else None,
    )

    credential = ensured.credential
    try:
        node_entry = registry.set_node_download_id(
            node_entry["id"],
            credential.download_id,
        )
    except Exception:
        # Best effort clean-up when persisting the download id fails.
        try:
            registry.remove_node(node_entry["id"])
        except Exception:  # pragma: no cover - defensive cleanup
            pass
        raise

    manifest_url = f"{settings.PUBLIC_BASE}/firmware/{credential.download_id}/manifest"
    binary_url = f"{settings.PUBLIC_BASE}/firmware/{credential.download_id}/latest.bin"

    credentials_payload: Dict[str, Any] = {
        "nodeId": credential.node_id,
        "downloadId": credential.download_id,
        "manifestUrl": manifest_url,
        "binaryUrl": binary_url,
    }
    if ensured.plaintext_token:
        credentials_payload["otaToken"] = ensured.plaintext_token

    return {"ok": True, "node": node_entry, "credentials": credentials_payload}


@router.get("/api/house/{house_id}/room/{room_id}/presets")
def api_list_room_presets(
    house_id: str,
    room_id: str,
    *,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    policy = _build_policy(session, current_user)
    room_ctx = _require_room(policy, house_id, room_id)
    presets = get_room_presets(room_ctx.house.slug, room_id)
    return {"presets": presets}


@router.post("/api/house/{house_id}/room/{room_id}/presets")
def api_create_room_preset(
    house_id: str,
    room_id: str,
    payload: Dict[str, Any],
    *,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    policy = _build_policy(session, current_user)
    room_ctx = _require_room(policy, house_id, room_id)

    raw_name = payload.get("name")
    if raw_name is None:
        raise HTTPException(400, "missing preset name")
    name = str(raw_name).strip()
    if not name:
        raise HTTPException(400, "missing preset name")
    if len(name) > MAX_CUSTOM_PRESET_NAME_LENGTH:
        raise HTTPException(400, "preset name too long")

    timeout_raw = payload.get("timeout", DEFAULT_SNAPSHOT_TIMEOUT)
    try:
        timeout_value = float(timeout_raw)
    except (TypeError, ValueError):
        raise HTTPException(400, "invalid timeout")
    if timeout_value != timeout_value or timeout_value <= 0:
        raise HTTPException(400, "timeout must be positive")

    node_entries: List[tuple[str, Dict[str, Any]]] = []
    seen_nodes: set[str] = set()
    room_nodes = room_ctx.room.get("nodes")
    if not isinstance(room_nodes, list):
        room_nodes = []
    for entry in room_nodes:
        if not isinstance(entry, dict):
            continue
        node_id = entry.get("id")
        if not isinstance(node_id, str):
            continue
        node_key = node_id.strip()
        if not node_key or node_key in seen_nodes:
            continue
        node = _valid_node(node_key)
        node_entries.append((node_key, node))
        seen_nodes.add(node_key)

    actions: List[Dict[str, Any]] = []
    bus = get_bus()
    for node_id, node in node_entries:
        info = status_monitor.status_for(node_id)
        since_seq_raw = info.get("seq") if isinstance(info, dict) else 0
        try:
            since_seq = int(since_seq_raw)
        except (TypeError, ValueError):
            since_seq = 0

        bus.status_request(node_id)
        seq, snapshot_payload = status_monitor.wait_for_snapshot(
            node_id, since_seq, timeout_value
        )
        if not isinstance(snapshot_payload, dict) or snapshot_payload.get("event") != "snapshot":
            raise HTTPException(504, f"Timed out waiting for status snapshot from {node_id}")

        formatted_state = _format_node_state(node_id, node, seq, snapshot_payload)
        modules = formatted_state.get("modules")
        module_snapshot = _modules_to_snapshot(modules if isinstance(modules, dict) else {})
        node_actions = snapshot_to_actions(node_id, module_snapshot)
        if node_actions:
            actions.extend(node_actions)

    existing_ids = {
        str(p.get("id"))
        for p in get_room_presets(room_ctx.house.slug, room_id)
        if isinstance(p, dict) and p.get("id") is not None
    }
    base_id = registry.slugify(name)
    if not base_id:
        base_id = "preset"
    preset_id = base_id
    counter = 2
    while preset_id in existing_ids:
        preset_id = f"{base_id}-{counter}"
        counter += 1

    saved = save_custom_preset(
        room_ctx.house.slug,
        room_id,
        {"id": preset_id, "name": name, "actions": actions, "source": "custom"},
    )

    presets = get_room_presets(room_ctx.house.slug, room_id)
    return {"ok": True, "preset": saved, "presets": presets}


@router.delete("/api/house/{house_id}/room/{room_id}/presets")
def api_delete_room_preset(
    house_id: str,
    room_id: str,
    preset_id: str,
    *,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    policy = _build_policy(session, current_user)
    room_ctx = _require_room(policy, house_id, room_id)

    preset_key = str(preset_id).strip()
    if not preset_key:
        raise HTTPException(400, "invalid preset id")

    if not delete_custom_preset(room_ctx.house.slug, room_id, preset_key):
        raise HTTPException(404, "Unknown preset")

    presets = get_room_presets(room_ctx.house.slug, room_id)
    return {"ok": True, "presets": presets}


@router.post("/api/house/{house_id}/room/{room_id}/presets/reorder")
def api_reorder_room_presets(
    house_id: str,
    room_id: str,
    payload: Dict[str, Any],
    *,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    policy = _build_policy(session, current_user)
    room_ctx = _require_room(policy, house_id, room_id)

    order = payload.get("order")
    if not isinstance(order, list):
        raise HTTPException(400, "order must be provided as a list")

    try:
        presets = reorder_custom_presets(room_ctx.house.slug, room_id, order)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except KeyError:
        raise HTTPException(404, "Unknown preset")

    return {"ok": True, "presets": presets}


@router.post("/api/house/{house_id}/room/{room_id}/preset/{preset_id}")
def api_apply_preset(
    house_id: str,
    room_id: str,
    preset_id: str,
    *,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    policy = _build_policy(session, current_user)
    room_ctx = _require_room(policy, house_id, room_id)
    preset = get_preset(room_ctx.house.slug, room_id, preset_id)
    if not preset:
        raise HTTPException(404, "Unknown preset")
    apply_preset(get_bus(), preset)
    return {"ok": True}


@router.get("/api/house/{house_id}/room/{room_id}/motion-immune")
def api_get_motion_immune(
    house_id: str,
    room_id: str,
    *,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    policy = _build_policy(session, current_user)
    room_ctx = _require_room(policy, house_id, room_id)
    immune = sorted(
        motion_preferences.get_room_immune_nodes(room_ctx.house.slug, room_id)
    )
    return {
        "house_id": room_ctx.house.external_id,
        "room_id": room_id,
        "immune": immune,
    }


@router.post("/api/house/{house_id}/room/{room_id}/motion-immune")
def api_set_motion_immune(
    house_id: str,
    room_id: str,
    payload: Dict[str, Any],
    *,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    policy = _build_policy(session, current_user)
    room_ctx = _require_room(policy, house_id, room_id)
    _ensure_can_manage_house(room_ctx.house, current_user)

    if not isinstance(payload, dict):
        raise HTTPException(400, "invalid payload")

    raw_list = payload.get("immune", [])
    if raw_list is None:
        raw_list = []
    if not isinstance(raw_list, list):
        raise HTTPException(400, "invalid immune list")

    available_nodes = {
        str(node.get("id"))
        for node in room_ctx.room.get("nodes", [])
        if node.get("id") is not None
    }

    clean_list: list[str] = []
    for value in raw_list:
        node_id = str(value).strip()
        if not node_id:
            continue
        if node_id not in available_nodes:
            raise HTTPException(400, f"unknown node: {node_id}")
        if node_id not in clean_list:
            clean_list.append(node_id)

    stored = motion_preferences.set_room_immune_nodes(
        room_ctx.house.slug, room_id, clean_list
    )
    immune = sorted(stored)
    return {
        "ok": True,
        "immune": immune,
        "house_id": room_ctx.house.external_id,
    }


@router.post("/api/house/{house_id}/room/{room_id}/motion-schedule")
def api_set_motion_schedule(
    house_id: str,
    room_id: str,
    payload: Dict[str, Any],
    *,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    policy = _build_policy(session, current_user)
    room_ctx = _require_room(policy, house_id, room_id)
    _ensure_can_manage_house(room_ctx.house, current_user)
    if (room_ctx.house.slug, room_id) not in motion_manager.room_sensors:
        raise HTTPException(404, "Motion schedule not supported for this room")
    schedule = payload.get("schedule")
    if not isinstance(schedule, list):
        raise HTTPException(400, "invalid schedule")
    if len(schedule) != motion_schedule.slot_count:
        raise HTTPException(400, "invalid schedule length")
    valid_presets = {p["id"] for p in get_room_presets(room_ctx.house.slug, room_id)}
    clean: List[Optional[str]] = []
    for value in schedule:
        if value in (None, "", "none"):
            clean.append(None)
            continue
        if not isinstance(value, str):
            raise HTTPException(400, "invalid preset value")
        if value not in valid_presets:
            raise HTTPException(400, f"unknown preset: {value}")
        clean.append(value)
    stored = motion_schedule.set_schedule(room_ctx.house.slug, room_id, clean)
    return {
        "ok": True,
        "schedule": stored,
        "house_id": room_ctx.house.external_id,
    }


@router.post("/api/house/{house_id}/room/{room_id}/motion-schedule/color")
def api_set_motion_schedule_color(
    house_id: str,
    room_id: str,
    payload: Dict[str, Any],
    *,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    policy = _build_policy(session, current_user)
    room_ctx = _require_room(policy, house_id, room_id)
    _ensure_can_manage_house(room_ctx.house, current_user)
    if (room_ctx.house.slug, room_id) not in motion_manager.room_sensors:
        raise HTTPException(404, "Motion schedule not supported for this room")
    if not isinstance(payload, dict):
        raise HTTPException(400, "invalid payload")
    preset_id = payload.get("preset")
    if not isinstance(preset_id, str) or not preset_id.strip():
        raise HTTPException(400, "invalid preset")
    preset_key = preset_id.strip()
    color_value = payload.get("color")
    if not isinstance(color_value, str) or not color_value.strip():
        raise HTTPException(400, "invalid color")
    valid_presets = {
        str(p.get("id"))
        for p in get_room_presets(room_ctx.house.slug, room_id)
        if p.get("id") is not None
    }
    if preset_key not in valid_presets:
        raise HTTPException(404, f"unknown preset: {preset_key}")
    try:
        stored = motion_schedule.set_preset_color(
            room_ctx.house.slug, room_id, preset_key, color_value
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {
        "ok": True,
        "preset": preset_key,
        "color": stored,
        "house_id": room_ctx.house.external_id,
    }

# ---- Node command APIs -------------------------------------------------

@router.post("/api/node/{node_id}/ws/set")
def api_ws_set(
    node_id: str,
    payload: Dict[str, Any],
    *,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    policy = _build_policy(session, current_user)
    _require_node(policy, node_id)
    _valid_node(node_id)
    try:
        strip = int(payload.get("strip"))
    except Exception:
        raise HTTPException(400, "invalid strip")
    if not 0 <= strip < 4:
        raise HTTPException(400, "invalid strip")
    effect = str(payload.get("effect", "")).strip()
    if effect not in WS_EFFECTS:
        raise HTTPException(400, "invalid effect")
    try:
        brightness = int(payload.get("brightness"))
    except Exception:
        raise HTTPException(400, "invalid brightness")
    if not 0 <= brightness <= 255:
        raise HTTPException(400, "invalid brightness")
    params = payload.get("params")
    if params is not None:
        if not isinstance(params, list):
            raise HTTPException(400, "invalid params")
        clean: list[object] = []
        for p in params:
            if isinstance(p, (int, float)):
                clean.append(float(p))
            elif isinstance(p, str):
                clean.append(p)
            else:
                raise HTTPException(400, "invalid params")
        params = clean
    get_bus().ws_set(node_id, strip, effect, brightness, params)
    return {"ok": True}

@router.post("/api/node/{node_id}/white/set")
def api_white_set(
    node_id: str,
    payload: Dict[str, Any],
    *,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    policy = _build_policy(session, current_user)
    _require_node(policy, node_id)
    _valid_node(node_id)
    try:
        channel = int(payload.get("channel"))
    except Exception:
        raise HTTPException(400, "invalid channel")
    if not 0 <= channel < 4:
        raise HTTPException(400, "invalid channel")
    effect = str(payload.get("effect", "")).strip()
    if effect not in WHITE_EFFECTS:
        raise HTTPException(400, "invalid effect")
    try:
        brightness = int(payload.get("brightness"))
    except Exception:
        raise HTTPException(400, "invalid brightness")
    if not 0 <= brightness <= 255:
        raise HTTPException(400, "invalid brightness")
    params = payload.get("params")
    if params is not None:
        if not (
            isinstance(params, list)
            and all(isinstance(p, (int, float)) for p in params)
        ):
            raise HTTPException(400, "invalid params")
        params = [float(p) for p in params]
    get_bus().white_set(node_id, channel, effect, brightness, params)
    return {"ok": True}


@router.post("/api/node/{node_id}/rgb/set")
def api_rgb_set(
    node_id: str,
    payload: Dict[str, Any],
    *,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    policy = _build_policy(session, current_user)
    _require_node(policy, node_id)
    _valid_node(node_id)
    try:
        strip = int(payload.get("strip"))
    except Exception:
        raise HTTPException(400, "invalid strip")
    if not 0 <= strip < 4:
        raise HTTPException(400, "invalid strip")
    effect = str(payload.get("effect", "")).strip()
    if effect not in RGB_EFFECTS:
        raise HTTPException(400, "invalid effect")
    try:
        brightness = int(payload.get("brightness"))
    except Exception:
        raise HTTPException(400, "invalid brightness")
    if not 0 <= brightness <= 255:
        raise HTTPException(400, "invalid brightness")
    params = payload.get("params")
    if params is not None:
        if not isinstance(params, list):
            raise HTTPException(400, "invalid params")
        if len(params) < 3:
            raise HTTPException(400, "invalid params")
        clean: List[int] = []
        for value in params:
            if not isinstance(value, (int, float)):
                raise HTTPException(400, "invalid params")
            v = int(value)
            if not 0 <= v <= 255:
                raise HTTPException(400, "invalid params")
            clean.append(v)
        params = clean
    get_bus().rgb_set(node_id, strip, effect, brightness, params)
    return {"ok": True}


@router.post("/api/node/{node_id}/{module}/brightness-limit")
def api_set_brightness_limit(
    node_id: str,
    module: str,
    payload: Dict[str, Any],
    *,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    policy = _build_policy(session, current_user)
    node_ctx = _require_node(policy, node_id)
    _ensure_can_manage_house(node_ctx.room.house, current_user)
    node = node_ctx.node
    module_key = str(module).lower()
    if module_key not in {"ws", "white", "rgb"}:
        raise HTTPException(404, "unsupported module")
    if module_key not in node.get("modules", []):
        raise HTTPException(404, "module not available")
    try:
        channel = int(payload.get("channel"))
    except Exception:
        raise HTTPException(400, "invalid channel")
    if not 0 <= channel < 4:
        raise HTTPException(400, "invalid channel")
    limit = payload.get("limit")
    if limit is None:
        brightness_limits.set_limit(node_id, module_key, channel, None)
        return {"ok": True, "limit": None}
    try:
        value = int(limit)
    except Exception:
        raise HTTPException(400, "invalid limit")
    if not 0 <= value <= 255:
        raise HTTPException(400, "invalid limit")
    stored = brightness_limits.set_limit(node_id, module_key, channel, value)
    return {"ok": True, "limit": stored}


@router.post("/api/node/{node_id}/{module}/channel-name")
def api_set_channel_name(
    node_id: str,
    module: str,
    payload: Dict[str, Any],
    *,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    policy = _build_policy(session, current_user)
    node_ctx = _require_node(policy, node_id)
    _ensure_can_manage_house(node_ctx.room.house, current_user)
    node = node_ctx.node
    module_key = str(module).lower()
    if module_key not in {"ws", "white", "rgb"}:
        raise HTTPException(404, "unsupported module")
    if module_key not in node.get("modules", []):
        raise HTTPException(404, "module not available")
    try:
        channel = int(payload.get("channel"))
    except Exception:
        raise HTTPException(400, "invalid channel")
    if not 0 <= channel < 4:
        raise HTTPException(400, "invalid channel")
    name = payload.get("name")
    if name is not None and not isinstance(name, str):
        raise HTTPException(400, "invalid name")
    stored = channel_names.set_name(node_id, module_key, channel, name)
    return {"ok": True, "name": stored}


@router.post("/api/node/{node_id}/motion")
def api_node_motion(
    node_id: str,
    payload: Dict[str, Any],
    *,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    policy = _build_policy(session, current_user)
    node_ctx = _require_node(policy, node_id)
    _ensure_can_manage_house(node_ctx.room.house, current_user)
    enabled = bool(payload.get("enabled", True))
    try:
        duration = int(payload.get("duration", 30))
    except Exception:
        raise HTTPException(400, "invalid duration")
    if not 10 <= duration <= 3600:
        raise HTTPException(400, "invalid duration")
    motion_manager.configure_node(node_id, enabled, duration)
    return {"ok": True}

@router.post("/api/node/{node_id}/ota/check")
def api_ota_check(
    node_id: str,
    *,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    policy = _build_policy(session, current_user)
    node_ctx = _require_node(policy, node_id)
    _ensure_can_manage_house(node_ctx.room.house, current_user)
    get_bus().ota_check(node_id)
    return {"ok": True}


@router.get("/api/node/{node_id}/state")
def api_node_state(
    node_id: str,
    timeout: float = 3.0,
    *,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    policy = _build_policy(session, current_user)
    node_ctx = _require_node(policy, node_id)
    node = node_ctx.node
    try:
        timeout_value = float(timeout)
    except (TypeError, ValueError):
        raise HTTPException(400, "invalid timeout")
    if timeout_value != timeout_value or timeout_value <= 0:
        raise HTTPException(400, "timeout must be positive")

    info = status_monitor.status_for(node_id)
    since_seq_raw = info.get("seq") if isinstance(info, dict) else 0
    try:
        since_seq = int(since_seq_raw)
    except (TypeError, ValueError):
        since_seq = 0

    get_bus().status_request(node_id)
    seq, payload = status_monitor.wait_for_snapshot(node_id, since_seq, timeout_value)
    if not isinstance(payload, dict) or payload.get("event") != "snapshot":
        raise HTTPException(504, "Timed out waiting for status snapshot")

    return _format_node_state(node_id, node, seq, payload)


@router.get("/api/node/{node_id}/status")
def api_node_status(node_id: str):
    _valid_node(node_id)
    info = status_monitor.status_for(node_id)

    def _iso(ts: Optional[float]) -> Optional[str]:
        if ts is None:
            return None
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

    signal_value: Optional[float] = None
    signal = info.get("signal_dbi")
    if isinstance(signal, (int, float)):
        signal_value = float(signal)

    return {
        "node": node_id,
        "online": bool(info.get("online")),
        "status": info.get("status"),
        "last_ok": _iso(info.get("last_ok")),
        "last_seen": _iso(info.get("last_seen")),
        "signal_dbi": signal_value,
        "timeout": status_monitor.timeout,
        "now": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/api/admin/status")
def api_admin_status(
    house_id: Optional[str] = None,
    *,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    policy = _build_policy(session, current_user)
    filter_slug: Optional[str] = None
    if house_id is not None:
        house_ctx = _require_house(policy, house_id)
        _ensure_can_manage_house(house_ctx, current_user)
        filter_slug = house_ctx.slug
    elif not policy.manages_any_house():
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")

    snapshot = status_monitor.snapshot()

    def _iso(ts: Optional[float]) -> Optional[str]:
        if ts is None:
            return None
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

    nodes: Dict[str, Dict[str, Any]] = {}
    for house, _, node in registry.iter_nodes():
        if filter_slug:
            slug = registry.get_house_slug(house)
            if slug != filter_slug:
                continue
        external_id = registry.get_house_external_id(house)
        access = policy.get_house_access(external_id)
        if not access or not access.can_manage(current_user):
            continue
        node_id = node["id"]
        info = snapshot.get(node_id, {})
        signal_value: Optional[float] = None
        signal = info.get("signal_dbi")
        if isinstance(signal, (int, float)):
            signal_value = float(signal)
        nodes[node_id] = {
            "online": bool(info.get("online")),
            "last_ok": _iso(info.get("last_ok")),
            "last_seen": _iso(info.get("last_seen")),
            "last_snapshot": _iso(info.get("last_snapshot")),
            "status": info.get("status"),
            "signal_dbi": signal_value,
        }
    now = datetime.now(timezone.utc).isoformat()
    return {"now": now, "timeout": status_monitor.timeout, "nodes": nodes}
