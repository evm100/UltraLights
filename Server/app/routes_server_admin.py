from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, func
from sqlmodel import Session, select

from . import node_builder, node_credentials, registry
from .auth.dependencies import require_admin
from .auth.models import (
    House,
    HouseMembership,
    HouseRole,
    NodeRegistration,
    RoomAccess,
    User,
)
from .auth.service import create_user, record_audit_event
from .config import settings
from .database import get_session

router = APIRouter(prefix="/api/server-admin", tags=["server-admin"])


class RotateHouseIdRequest(BaseModel):
    """Payload for rotating a house external identifier."""

    confirm: bool = Field(..., description="Confirmation flag to rotate the ID")


class RotateHouseIdResponse(BaseModel):
    """Response payload after rotating a house id."""

    house_id: str = Field(..., alias="houseId")
    new_id: str = Field(..., alias="newId")

    model_config = ConfigDict(populate_by_name=True)


class HouseAdminCreateRequest(BaseModel):
    """Request payload for creating a new house administrator."""

    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=255)

    model_config = ConfigDict()

    @field_validator("username")
    @classmethod
    def _clean_username(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("username cannot be empty")
        return cleaned


class HouseAdminResponse(BaseModel):
    """Serialized information about a created house admin."""

    membership_id: int = Field(..., alias="membershipId")
    user_id: int = Field(..., alias="userId")
    username: str
    house_id: str = Field(..., alias="houseId")

    model_config = ConfigDict(populate_by_name=True)


class HouseCreateRequest(BaseModel):
    """Payload describing a new house to be created."""

    name: str = Field(..., min_length=1, max_length=128)
    id: str | None = Field(
        default=None,
        description="Legacy slug identifier. Will be derived from the name if omitted.",
        max_length=settings.MAX_HOUSE_ID_LENGTH,
    )
    rooms: list[dict[str, Any]] | None = Field(
        default_factory=list,
        description="Initial room definitions for the house.",
    )
    external_id: str | None = Field(
        default=None,
        description="Optional public identifier. Randomized if blank.",
        max_length=settings.MAX_HOUSE_ID_LENGTH,
    )

    model_config = ConfigDict()

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("name cannot be empty")
        return cleaned


class HouseCreateResponse(BaseModel):
    """Response returned after creating a house."""

    id: str
    name: str
    external_id: str = Field(..., alias="externalId")

    model_config = ConfigDict(populate_by_name=True)


BOARD_CHOICES = tuple(sorted(node_builder.SUPPORTED_TARGETS))


class Ws2812ChannelConfig(BaseModel):
    index: int = Field(..., ge=0, le=1)
    enabled: bool = False
    gpio: Optional[int] = Field(default=None, ge=0, le=48)
    pixels: Optional[int] = Field(default=None, ge=0, le=4096)

    model_config = ConfigDict(extra="forbid")


class WhiteChannelConfig(BaseModel):
    index: int = Field(..., ge=0, le=3)
    enabled: bool = False
    gpio: Optional[int] = Field(default=None, ge=0, le=48)
    ledc_channel: Optional[int] = Field(default=None, alias="ledcChannel", ge=0, le=7)
    pwm_hz: Optional[int] = Field(default=None, alias="pwmHz", ge=1, le=50000)
    minimum: Optional[int] = Field(default=None, alias="minimum", ge=0, le=4095)
    maximum: Optional[int] = Field(default=None, alias="maximum", ge=0, le=4095)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class RgbChannelConfig(BaseModel):
    index: int = Field(..., ge=0, le=3)
    enabled: bool = False
    pwm_hz: Optional[int] = Field(default=None, alias="pwmHz", ge=1, le=50000)
    ledc_mode: Optional[int] = Field(default=None, alias="ledcMode", ge=0, le=1)
    r_gpio: Optional[int] = Field(default=None, alias="rGpio", ge=0, le=48)
    r_ledc_ch: Optional[int] = Field(default=None, alias="rLedcChannel", ge=0, le=7)
    g_gpio: Optional[int] = Field(default=None, alias="gGpio", ge=0, le=48)
    g_ledc_ch: Optional[int] = Field(default=None, alias="gLedcChannel", ge=0, le=7)
    b_gpio: Optional[int] = Field(default=None, alias="bGpio", ge=0, le=48)
    b_ledc_ch: Optional[int] = Field(default=None, alias="bLedcChannel", ge=0, le=7)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class PirSensorConfig(BaseModel):
    enabled: bool = False
    gpio: Optional[int] = Field(default=None, ge=0, le=48)

    model_config = ConfigDict(extra="forbid")


class NodeHardwareConfig(BaseModel):
    board: str = Field(default="esp32")
    ws2812: List[Ws2812ChannelConfig] = Field(default_factory=list)
    white: List[WhiteChannelConfig] = Field(default_factory=list)
    rgb: List[RgbChannelConfig] = Field(default_factory=list)
    pir: Optional[PirSensorConfig] = None
    overrides: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")

    @field_validator("board")
    @classmethod
    def _validate_board(cls, value: str) -> str:
        normalized = value.lower().strip()
        if normalized not in node_builder.SUPPORTED_TARGETS:
            raise ValueError("Unsupported board")
        return normalized

    @field_validator("overrides")
    @classmethod
    def _validate_overrides(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        cleaned: Dict[str, Any] = {}
        for key, raw in value.items():
            if not isinstance(key, str) or not key.startswith("CONFIG_"):
                raise ValueError("Override keys must start with CONFIG_")
            cleaned[key] = raw
        return cleaned


class NodeFactoryCreateRequest(BaseModel):
    count: int = Field(default=1, ge=1, le=50)
    display_name: Optional[str] = Field(default=None, alias="displayName")
    hardware: NodeHardwareConfig
    assign_house_slug: Optional[str] = Field(default=None, alias="assignHouseSlug")
    assign_room_id: Optional[str] = Field(default=None, alias="assignRoomId")
    assign_user_id: Optional[int] = Field(default=None, alias="assignUserId")
    assign_house_id: Optional[int] = Field(default=None, alias="assignHouseId")

    model_config = ConfigDict(populate_by_name=True)


class NodeFactoryCreatedNode(BaseModel):
    node_id: str = Field(..., alias="nodeId")
    download_id: str = Field(..., alias="downloadId")
    ota_token: str = Field(..., alias="otaToken")
    manifest_url: str = Field(..., alias="manifestUrl")
    metadata: Dict[str, Any]

    model_config = ConfigDict(populate_by_name=True)


class NodeFactoryCreateResponse(BaseModel):
    nodes: List[NodeFactoryCreatedNode]


class CommandOutput(BaseModel):
    command: List[str]
    returncode: int
    stdout: str
    stderr: str
    cwd: str


class NodeFactoryBuildRequest(BaseModel):
    node_id: Optional[str] = Field(default=None, alias="nodeId")
    use_test_node: bool = Field(default=False, alias="useTestNode")
    regenerate_token: bool = Field(default=False, alias="regenerateToken")
    skip_build: bool = Field(default=False, alias="skipBuild")
    hardware: Optional[NodeHardwareConfig] = None

    model_config = ConfigDict(populate_by_name=True)


class NodeFactoryBuildResponse(CommandOutput):
    node_id: str = Field(..., alias="nodeId")
    download_id: str = Field(..., alias="downloadId")
    manifest_url: str = Field(..., alias="manifestUrl")
    sdkconfig_path: str = Field(..., alias="sdkconfigPath")
    target: str
    metadata: Dict[str, Any]

    model_config = ConfigDict(populate_by_name=True)


class NodeFactoryFlashRequest(BaseModel):
    node_id: Optional[str] = Field(default=None, alias="nodeId")
    use_test_node: bool = Field(default=False, alias="useTestNode")
    port: str
    hardware: Optional[NodeHardwareConfig] = None

    model_config = ConfigDict(populate_by_name=True)


class NodeFactoryUpdateRequest(BaseModel):
    firmware_version: str = Field(..., alias="firmwareVersion")

    model_config = ConfigDict(populate_by_name=True)


class NodeFactoryCommandResponse(CommandOutput):
    message: Optional[str] = None


class NodeFactoryRegistrationInfo(BaseModel):
    node_id: str = Field(..., alias="nodeId")
    download_id: str = Field(..., alias="downloadId")
    display_name: Optional[str] = Field(default=None, alias="displayName")
    board: Optional[str] = None
    assigned: bool
    house_slug: Optional[str] = Field(default=None, alias="houseSlug")
    room_id: Optional[str] = Field(default=None, alias="roomId")

    model_config = ConfigDict(populate_by_name=True)


class NodeFactoryListResponse(BaseModel):
    available: List[NodeFactoryRegistrationInfo]
    assigned: List[NodeFactoryRegistrationInfo]


def _hardware_to_metadata(config: NodeHardwareConfig) -> Dict[str, Any]:
    return config.model_dump(mode="python", by_alias=False, exclude_none=True)


def _command_output(result: node_builder.CommandResult) -> CommandOutput:
    return CommandOutput(
        command=[str(part) for part in result.command],
        returncode=result.returncode,
        stdout=result.stdout or "",
        stderr=result.stderr or "",
        cwd=str(result.cwd),
    )


def _registration_summary(registration: NodeRegistration) -> NodeFactoryRegistrationInfo:
    metadata = registration.hardware_metadata or {}
    board = None
    if isinstance(metadata, dict):
        raw_board = metadata.get("board")
        if isinstance(raw_board, str):
            board = raw_board
    assigned = bool(registration.assigned_at or registration.house_slug or registration.room_id)
    return NodeFactoryRegistrationInfo(
        node_id=registration.node_id,
        download_id=registration.download_id,
        display_name=registration.display_name,
        board=board,
        assigned=assigned,
        house_slug=registration.house_slug,
        room_id=registration.room_id,
    )


def _get_house_row(session: Session, external_id: str, *, display_name: str) -> House:
    house = session.exec(select(House).where(House.external_id == external_id)).first()
    if house:
        if display_name and house.display_name != display_name:
            house.display_name = display_name
        return house
    house = House(display_name=display_name or external_id, external_id=external_id)
    session.add(house)
    session.flush()
    return house


def _ensure_unique_username(session: Session, username: str) -> None:
    existing = session.exec(select(User).where(User.username == username)).first()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "Username already exists")


@router.post(
    "/houses",
    status_code=status.HTTP_201_CREATED,
    response_model=HouseCreateResponse,
)
def create_house(
    payload: HouseCreateRequest,
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> HouseCreateResponse:
    name = payload.name.strip()

    raw_slug = (payload.id or "").strip() or name
    slug = registry.slugify(raw_slug)
    if not slug:
        slug = "house"
    if len(slug) > settings.MAX_HOUSE_ID_LENGTH:
        slug = slug[: settings.MAX_HOUSE_ID_LENGTH]

    existing_slugs = {
        registry.get_house_slug(house)
        for house in settings.DEVICE_REGISTRY
        if isinstance(house, dict)
    }
    if slug in existing_slugs:
        raise HTTPException(status.HTTP_409_CONFLICT, "House id already exists.")

    provided_external = (payload.external_id or "").strip()
    if provided_external:
        if len(provided_external) > settings.MAX_HOUSE_ID_LENGTH:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "External id exceeds maximum length.",
            )
        existing_external_ids = {
            registry.get_house_external_id(house)
            for house in settings.DEVICE_REGISTRY
            if isinstance(house, dict)
        }
        if provided_external in existing_external_ids:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "House external id already exists.",
            )

    rooms_payload: list[dict[str, Any]] = []
    if payload.rooms:
        for entry in payload.rooms:
            if isinstance(entry, dict):
                rooms_payload.append(entry)

    new_house: Dict[str, Any] = {
        "id": slug,
        "name": name,
        "rooms": rooms_payload,
    }
    if provided_external:
        new_house["external_id"] = provided_external

    registry_list = registry.settings.DEVICE_REGISTRY
    registry_list.append(new_house)
    if settings.DEVICE_REGISTRY is not registry_list:
        settings.DEVICE_REGISTRY.append(new_house)

    registry.ensure_house_external_ids()
    registry.save_registry()

    external_id = registry.get_house_external_id(new_house)
    house_row = _get_house_row(
        session,
        external_id,
        display_name=name,
    )
    session.refresh(house_row)

    record_audit_event(
        session,
        actor=current_user,
        action="house_created",
        summary=f"Created house {name}",
        data={"slug": slug, "external_id": external_id},
        commit=True,
    )

    return HouseCreateResponse(id=slug, name=name, external_id=external_id)


@router.post(
    "/houses/{house_id}/rotate-id",
    response_model=RotateHouseIdResponse,
)
def rotate_house_id(
    house_id: str,
    payload: RotateHouseIdRequest,
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> RotateHouseIdResponse:
    if not payload.confirm:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Confirmation required")

    house_entry = registry.find_house(house_id)
    if not house_entry:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown house")

    current_external = registry.get_house_external_id(house_entry)
    new_external = registry.rotate_house_external_id(house_id)

    display_name = str(
        house_entry.get("name") or house_entry.get("id") or new_external
    )
    house_row = _get_house_row(session, current_external, display_name=display_name)
    house_row.external_id = new_external

    record_audit_event(
        session,
        actor=current_user,
        action="house_id_rotated",
        summary=f"Rotated house id for {display_name}",
        data={"previous": current_external, "new": new_external},
        commit=True,
    )

    return RotateHouseIdResponse(house_id=current_external, new_id=new_external)


@router.post(
    "/houses/{house_id}/admins",
    status_code=status.HTTP_201_CREATED,
    response_model=HouseAdminResponse,
)
def create_house_admin(
    house_id: str,
    payload: HouseAdminCreateRequest,
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> HouseAdminResponse:
    house_entry = registry.find_house(house_id)
    if not house_entry:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown house")

    external_id = registry.get_house_external_id(house_entry)
    display_name = str(
        house_entry.get("name") or house_entry.get("id") or external_id
    )
    house_row = _get_house_row(session, external_id, display_name=display_name)

    username = payload.username.strip()
    _ensure_unique_username(session, username)

    user = create_user(session, username, payload.password, server_admin=False)

    membership = HouseMembership(
        user_id=user.id,
        house_id=house_row.id,
        role=HouseRole.ADMIN,
    )
    session.add(membership)
    session.flush()

    record_audit_event(
        session,
        actor=current_user,
        action="house_admin_created",
        summary=f"Created house admin {username}",
        data={"house": external_id, "user": username},
        commit=True,
    )

    session.refresh(membership)
    return HouseAdminResponse(
        membership_id=membership.id,
        user_id=user.id,
        username=user.username,
        house_id=external_id,
    )


@router.delete(
    "/accounts/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    response_class=Response,
)
def delete_account(
    user_id: int,
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> None:
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")

    if user.server_admin:
        remaining_admins = session.exec(
            select(func.count())
            .select_from(User)
            .where(User.server_admin.is_(True))
            .where(User.id != user_id)
        ).one()
        if not remaining_admins:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Cannot remove the last server admin.",
            )

    if current_user.id == user_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Cannot remove your own account.",
        )

    membership_ids = session.exec(
        select(HouseMembership.id).where(HouseMembership.user_id == user_id)
    ).all()
    membership_ids = [mid for mid in membership_ids if mid is not None]
    if membership_ids:
        session.exec(
            delete(RoomAccess).where(RoomAccess.membership_id.in_(membership_ids))
        )
        session.exec(
            delete(HouseMembership).where(HouseMembership.id.in_(membership_ids))
        )

    username = user.username
    was_server_admin = user.server_admin
    session.delete(user)

    record_audit_event(
        session,
        actor=current_user,
        action="account_removed",
        summary=f"Removed account {username}",
        data={
            "user": username,
            "server_admin": was_server_admin,
            "membership_ids": membership_ids,
        },
    )
    session.commit()


@router.get(
    "/node-factory/registrations",
    response_model=NodeFactoryListResponse,
)
def list_node_factory(
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> NodeFactoryListResponse:
    registrations = session.exec(
        select(NodeRegistration).order_by(NodeRegistration.created_at)
    ).all()
    available: List[NodeFactoryRegistrationInfo] = []
    assigned: List[NodeFactoryRegistrationInfo] = []
    for registration in registrations:
        info = _registration_summary(registration)
        if info.assigned:
            assigned.append(info)
        else:
            available.append(info)
    return NodeFactoryListResponse(available=available, assigned=assigned)


@router.post(
    "/node-factory/registrations",
    status_code=status.HTTP_201_CREATED,
    response_model=NodeFactoryCreateResponse,
)
def create_node_factory_registrations(
    payload: NodeFactoryCreateRequest,
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> NodeFactoryCreateResponse:
    metadata = _hardware_to_metadata(payload.hardware)
    metadata.setdefault("board", payload.hardware.board)
    entries = node_credentials.create_batch(
        session,
        payload.count,
        metadata=(metadata.copy() for _ in range(payload.count)),
    )

    created_nodes: List[NodeFactoryCreatedNode] = []
    node_ids: List[str] = []
    needs_commit = False
    base_display = (payload.display_name or "").strip()

    for index, entry in enumerate(entries, start=1):
        registration = entry.registration
        display_name = base_display
        if base_display:
            if payload.count > 1:
                display_name = f"{base_display} {index}"
            registration.display_name = display_name
            needs_commit = True

        registration.hardware_metadata = metadata.copy()
        needs_commit = True

        if (
            payload.assign_house_slug
            or payload.assign_room_id
            or payload.assign_user_id
            or payload.assign_house_id
        ):
            registration = node_credentials.claim_registration(
                session,
                registration.node_id,
                house_slug=payload.assign_house_slug,
                room_id=payload.assign_room_id,
                display_name=display_name or registration.display_name,
                assigned_user_id=payload.assign_user_id,
                assigned_house_id=payload.assign_house_id,
                hardware_metadata=metadata,
            )
        else:
            session.add(registration)

        manifest_url = f"{settings.PUBLIC_BASE}/firmware/{registration.download_id}/manifest.json"
        created_nodes.append(
            NodeFactoryCreatedNode(
                nodeId=registration.node_id,
                downloadId=registration.download_id,
                otaToken=entry.plaintext_token,
                manifestUrl=manifest_url,
                metadata=metadata.copy(),
            )
        )
        node_ids.append(registration.node_id)

    if needs_commit:
        session.commit()

    record_audit_event(
        session,
        actor=current_user,
        action="node_factory_created",
        summary=f"Generated {len(created_nodes)} node registrations",
        data={
            "nodes": node_ids,
            "board": metadata.get("board"),
        },
    )
    session.commit()

    return NodeFactoryCreateResponse(nodes=created_nodes)


@router.post(
    "/node-factory/build",
    response_model=NodeFactoryBuildResponse,
)
def build_node_factory_firmware(
    payload: NodeFactoryBuildRequest,
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> NodeFactoryBuildResponse:
    registration: Optional[NodeRegistration] = None
    metadata: Dict[str, Any]

    if payload.use_test_node:
        test_metadata = (
            _hardware_to_metadata(payload.hardware)
            if payload.hardware
            else {}
        )
        registration = node_builder.ensure_test_registration(
            session,
            metadata=test_metadata,
        )
        node_id = registration.node_id
        metadata = test_metadata or dict(registration.hardware_metadata or {})
    else:
        if not payload.node_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "nodeId is required")
        node_id = payload.node_id
        registration = node_credentials.get_registration_by_node_id(session, node_id)
        if registration is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Node not found")
        metadata = (
            _hardware_to_metadata(payload.hardware)
            if payload.hardware
            else dict(registration.hardware_metadata or {})
        )

    if payload.hardware:
        registration.hardware_metadata = metadata.copy()
        session.add(registration)
        session.commit()

    result = node_builder.build_individual_node(
        session,
        node_id,
        metadata=metadata,
        board=metadata.get("board"),
        regenerate_token=payload.regenerate_token,
        run_build=not payload.skip_build,
    )

    command_output = _command_output(result)
    registration = node_credentials.get_registration_by_node_id(session, node_id)
    metadata_payload = metadata.copy()
    return NodeFactoryBuildResponse(
        nodeId=node_id,
        downloadId=result.download_id,
        manifestUrl=result.manifest_url,
        sdkconfigPath=str(result.sdkconfig_path),
        target=result.target,
        metadata=metadata_payload,
        **command_output.model_dump(),
    )


@router.post(
    "/node-factory/flash",
    response_model=NodeFactoryBuildResponse,
)
def flash_node_factory_firmware(
    payload: NodeFactoryFlashRequest,
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> NodeFactoryBuildResponse:
    if payload.use_test_node:
        registration = node_builder.ensure_test_registration(
            session,
            metadata=(
                _hardware_to_metadata(payload.hardware)
                if payload.hardware
                else None
            ),
        )
        node_id = registration.node_id
        metadata = (
            _hardware_to_metadata(payload.hardware)
            if payload.hardware
            else dict(registration.hardware_metadata or {})
        )
    else:
        if not payload.node_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "nodeId is required")
        node_id = payload.node_id
        registration = node_credentials.get_registration_by_node_id(session, node_id)
        if registration is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Node not found")
        metadata = (
            _hardware_to_metadata(payload.hardware)
            if payload.hardware
            else dict(registration.hardware_metadata or {})
        )

    if payload.hardware:
        registration.hardware_metadata = metadata.copy()
        session.add(registration)
        session.commit()

    result = node_builder.first_time_flash(
        session,
        node_id,
        port=payload.port,
        metadata=metadata,
        board=metadata.get("board"),
    )

    command_output = _command_output(result)
    return NodeFactoryBuildResponse(
        nodeId=node_id,
        downloadId=result.download_id,
        manifestUrl=result.manifest_url,
        sdkconfigPath=str(result.sdkconfig_path),
        target=result.target,
        metadata=metadata.copy(),
        **command_output.model_dump(),
    )


@router.post(
    "/node-factory/update-all",
    response_model=NodeFactoryCommandResponse,
)
def update_all_nodes_command(
    payload: NodeFactoryUpdateRequest,
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> NodeFactoryCommandResponse:
    result = node_builder.update_all_nodes(payload.firmware_version)
    output = _command_output(result)
    message = (
        "Bulk firmware update completed"
        if result.returncode == 0
        else "Bulk firmware update failed"
    )
    record_audit_event(
        session,
        actor=current_user,
        action="node_factory_update_all",
        summary="Triggered updateAllNodes",
        data={
            "firmware_version": payload.firmware_version,
            "returncode": result.returncode,
        },
    )
    session.commit()
    return NodeFactoryCommandResponse(
        message=message,
        **output.model_dump(),
    )


__all__ = ["router"]
