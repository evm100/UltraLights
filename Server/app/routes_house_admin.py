"""FastAPI routes for managing house memberships."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlmodel import Session, delete, select

from . import registry
from .auth.dependencies import require_admin
from .auth.models import House, HouseMembership, HouseRole, RoomAccess, User
from .auth.security import hash_password
from .auth.service import create_user
from .database import get_session


router = APIRouter(prefix="/api/house-admin", tags=["house-admin"])


def _normalize_room_list(value: Any) -> Optional[List[str]]:
    if value is None:
        return None
    if isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = [value]
    cleaned: List[str] = []
    for item in items:
        clean = str(item).strip()
        if clean:
            cleaned.append(clean)
    return cleaned


class RoomInfo(BaseModel):
    """Description of a room and its identifier."""

    id: str
    name: str


class HouseMember(BaseModel):
    """Serialized membership information for the API."""

    membership_id: int = Field(..., alias="membershipId")
    user_id: int = Field(..., alias="userId")
    username: str
    role: HouseRole
    server_admin: bool = Field(..., alias="serverAdmin")
    rooms: List[RoomInfo]

    model_config = ConfigDict(populate_by_name=True)


class MembershipListResponse(BaseModel):
    """Response body for listing memberships within a house."""

    members: List[HouseMember]
    available_rooms: List[RoomInfo] = Field(..., alias="availableRooms")

    model_config = ConfigDict(populate_by_name=True)


class MembershipCreate(BaseModel):
    """Payload for creating a new house membership."""

    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=255)
    role: HouseRole
    rooms: Optional[List[str]] = None

    model_config = ConfigDict()

    @field_validator("username")
    @classmethod
    def _clean_username(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("username cannot be empty")
        return cleaned

    @field_validator("rooms", mode="before")
    @classmethod
    def _normalize_rooms(cls, value: Any) -> Optional[List[str]]:
        return _normalize_room_list(value)


class MembershipUpdate(BaseModel):
    """Payload for updating an existing house membership."""

    role: Optional[HouseRole] = None
    password: Optional[str] = None
    rooms: Optional[List[str]] = None

    model_config = ConfigDict()

    @field_validator("password")
    @classmethod
    def _clean_password(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if not str(value).strip():
            raise ValueError("password cannot be empty")
        return value

    @field_validator("rooms", mode="before")
    @classmethod
    def _normalize_rooms(cls, value: Any) -> Optional[List[str]]:
        return _normalize_room_list(value)


def _get_house(session: Session, house_id: str) -> House:
    house = session.exec(select(House).where(House.external_id == house_id)).first()
    if not house:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown house")
    return house


def _room_lookup(house_id: str) -> Dict[str, str]:
    house = registry.find_house(house_id)
    if not house:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown house")
    mapping: Dict[str, str] = {}
    for entry in house.get("rooms", []) or []:
        if not isinstance(entry, dict):
            continue
        room_id = str(entry.get("id") or "").strip()
        if not room_id:
            continue
        name_value = entry.get("name")
        if isinstance(name_value, str):
            clean_name = name_value.strip()
            mapping[room_id] = clean_name or room_id
        else:
            mapping[room_id] = room_id
    return mapping


def _room_assignments(session: Session, membership_ids: Iterable[int]) -> Dict[int, List[str]]:
    mapping: Dict[int, List[str]] = {}
    id_list = [mid for mid in membership_ids if mid is not None]
    if not id_list:
        return mapping
    for access in session.exec(
        select(RoomAccess).where(RoomAccess.membership_id.in_(id_list))
    ):
        membership_id = access.membership_id
        room_id = str(access.room_id)
        if membership_id is None or not room_id:
            continue
        mapping.setdefault(membership_id, []).append(room_id)
    return mapping


def _serialize_member(
    membership: HouseMembership,
    user: User,
    room_map: Dict[str, str],
    assigned_ids: Iterable[str],
) -> HouseMember:
    normalized_ids = []
    seen: set[str] = set()
    for room_id in assigned_ids:
        clean_id = str(room_id).strip()
        if not clean_id or clean_id in seen:
            continue
        seen.add(clean_id)
        normalized_ids.append(clean_id)
    normalized_ids.sort(key=lambda rid: room_map.get(rid, rid).lower())
    rooms = [RoomInfo(id=rid, name=room_map.get(rid, rid)) for rid in normalized_ids]
    return HouseMember(
        membership_id=membership.id,
        user_id=membership.user_id,
        username=user.username,
        role=membership.role,
        server_admin=user.server_admin,
        rooms=rooms,
    )


def _ensure_unique_username(session: Session, username: str) -> None:
    existing = session.exec(select(User).where(User.username == username)).first()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "Username already exists")


def _apply_room_assignments(
    session: Session,
    membership: HouseMembership,
    room_ids: Iterable[str],
) -> None:
    clean_ids: List[str] = []
    seen: set[str] = set()
    for raw in room_ids:
        room_id = str(raw).strip()
        if not room_id or room_id in seen:
            continue
        seen.add(room_id)
        clean_ids.append(room_id)

    existing = session.exec(
        select(RoomAccess).where(RoomAccess.membership_id == membership.id)
    ).all()
    existing_ids = {entry.room_id: entry for entry in existing}

    for entry in existing:
        if entry.room_id not in seen:
            session.delete(entry)

    for room_id in clean_ids:
        if room_id not in existing_ids:
            session.add(RoomAccess(membership_id=membership.id, room_id=room_id))


def _get_membership(
    session: Session,
    house_db_id: int,
    membership_id: int,
) -> tuple[HouseMembership, User]:
    row = session.exec(
        select(HouseMembership, User)
        .join(User, User.id == HouseMembership.user_id)
        .where(HouseMembership.id == membership_id)
        .where(HouseMembership.house_id == house_db_id)
    ).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown membership")
    return row


@router.get("/{house_id}/members", response_model=MembershipListResponse)
def list_members(
    house_id: str,
    _admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> MembershipListResponse:
    house = _get_house(session, house_id)
    room_map = _room_lookup(house_id)
    rows = session.exec(
        select(HouseMembership, User)
        .join(User, User.id == HouseMembership.user_id)
        .where(HouseMembership.house_id == house.id)
        .order_by(User.username)
    ).all()
    membership_ids = [membership.id for membership, _ in rows if membership.id is not None]
    assignments = _room_assignments(session, membership_ids)
    members = [
        _serialize_member(membership, user, room_map, assignments.get(membership.id, []))
        for membership, user in rows
    ]
    available = [
        RoomInfo(id=room_id, name=name)
        for room_id, name in sorted(room_map.items(), key=lambda item: item[1].lower())
    ]
    return MembershipListResponse(members=members, available_rooms=available)


@router.post(
    "/{house_id}/members",
    status_code=status.HTTP_201_CREATED,
    response_model=HouseMember,
)
def create_member(
    house_id: str,
    payload: MembershipCreate,
    _admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> HouseMember:
    house = _get_house(session, house_id)
    room_map = _room_lookup(house_id)
    username = payload.username.strip()
    _ensure_unique_username(session, username)

    user = create_user(session, username, payload.password, server_admin=False)

    membership = HouseMembership(user_id=user.id, house_id=house.id, role=payload.role)
    session.add(membership)
    session.commit()
    session.refresh(membership)

    assigned_room_ids: List[str] = []
    if membership.role == HouseRole.GUEST:
        requested_rooms = payload.rooms or []
        normalized_rooms = [rid for rid in requested_rooms if rid]
        for room_id in normalized_rooms:
            if room_id not in room_map:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    f"Room '{room_id}' is not part of this house",
                )
        _apply_room_assignments(session, membership, normalized_rooms)
        assigned_room_ids = normalized_rooms
    else:
        _apply_room_assignments(session, membership, [])

    session.commit()
    if membership.role != HouseRole.ADMIN:
        assignments = _room_assignments(session, [membership.id])
        assigned_room_ids = assignments.get(membership.id, assigned_room_ids)
    return _serialize_member(membership, user, room_map, assigned_room_ids)


@router.patch("/{house_id}/members/{membership_id}", response_model=HouseMember)
def update_member(
    house_id: str,
    membership_id: int,
    payload: MembershipUpdate,
    _admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> HouseMember:
    house = _get_house(session, house_id)
    room_map = _room_lookup(house_id)
    membership, user = _get_membership(session, house.id, membership_id)

    if payload.role is not None:
        membership.role = payload.role

    target_role = membership.role

    if payload.password:
        user.hashed_password = hash_password(payload.password)

    if target_role == HouseRole.ADMIN:
        _apply_room_assignments(session, membership, [])
    elif payload.rooms is not None:
        normalized = [rid for rid in payload.rooms if rid]
        for room_id in normalized:
            if room_id not in room_map:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    f"Room '{room_id}' is not part of this house",
                )
        _apply_room_assignments(session, membership, normalized)

    session.commit()
    assignments = _room_assignments(session, [membership.id])
    assigned_rooms = assignments.get(membership.id, [])
    return _serialize_member(membership, user, room_map, assigned_rooms)


@router.delete("/{house_id}/members/{membership_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_member(
    house_id: str,
    membership_id: int,
    _admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> None:
    house = _get_house(session, house_id)
    membership, user = _get_membership(session, house.id, membership_id)
    session.exec(
        delete(RoomAccess).where(RoomAccess.membership_id == membership.id)
    )
    session.delete(membership)
    session.commit()
