from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlmodel import Session, select

from . import registry
from .auth.dependencies import require_admin
from .auth.models import House, HouseMembership, HouseRole, User
from .auth.service import create_user, record_audit_event
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


__all__ = ["router"]
