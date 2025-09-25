from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, func
from sqlmodel import Session, select

from . import registry
from .auth.dependencies import require_admin
from .auth.models import House, HouseMembership, HouseRole, RoomAccess, User
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


__all__ = ["router"]
