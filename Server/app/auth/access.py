"""Access control helpers for page and API routes."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from sqlmodel import Session, select

from .. import registry
from ..config import settings
from .models import House, HouseMembership, HouseRole, RoomAccess, User


@dataclass
class HouseAccess:
    """Represents the scope a user has within a house."""

    house: Optional[House]
    membership_id: Optional[int]
    role: Optional[HouseRole]
    allowed_rooms: Optional[Set[str]] = None

    def can_manage(self, user: User) -> bool:
        """Return ``True`` if ``user`` may administer the house."""

        if user.server_admin:
            return True
        return self.role == HouseRole.ADMIN

    def can_view_room(self, room_id: str) -> bool:
        """Return ``True`` if ``room_id`` is visible under this access."""

        if self.allowed_rooms is None:
            return True
        return room_id in self.allowed_rooms


@dataclass
class HouseContext:
    """Holds registry data for a house filtered by access."""

    original: Dict[str, Any]
    filtered: Dict[str, Any]
    slug: str
    external_id: str
    access: HouseAccess


@dataclass
class RoomContext:
    """Holds registry data for a room filtered by access."""

    house: HouseContext
    room: Dict[str, Any]
    filtered_room: Dict[str, Any]


@dataclass
class NodeContext:
    """Holds registry data for a node filtered by access."""

    room: RoomContext
    node: Dict[str, Any]


def _load_memberships(session: Session, user: User) -> Sequence[tuple[HouseMembership, House]]:
    return session.exec(
        select(HouseMembership, House)
        .join(House, House.id == HouseMembership.house_id)
        .where(HouseMembership.user_id == user.id)
    ).all()


def _load_room_access(session: Session, membership_ids: Iterable[int]) -> Sequence[RoomAccess]:
    id_list = [mid for mid in membership_ids if mid is not None]
    if not id_list:
        return []
    return session.exec(
        select(RoomAccess)
        .where(RoomAccess.membership_id.in_(id_list))
    ).all()


def _registry_houses() -> List[Dict[str, Any]]:
    registry.ensure_house_external_ids(persist=False)
    return settings.DEVICE_REGISTRY


def build_access_map(session: Session, user: User) -> Dict[str, HouseAccess]:
    """Return a mapping of ``external_id`` to :class:`HouseAccess`."""

    access: Dict[str, HouseAccess] = {}

    if user.server_admin:
        for house in session.exec(select(House)).all():
            if not isinstance(house.external_id, str):
                continue
            access[house.external_id] = HouseAccess(
                house=house,
                membership_id=None,
                role=HouseRole.ADMIN,
                allowed_rooms=None,
            )

        for entry in _registry_houses():
            external_id = registry.get_house_external_id(entry)
            access.setdefault(
                external_id,
                HouseAccess(
                    house=None,
                    membership_id=None,
                    role=HouseRole.ADMIN,
                    allowed_rooms=None,
                ),
            )
        return access

    membership_rows = _load_memberships(session, user)
    membership_lookup: Dict[int, HouseAccess] = {}

    for membership, house in membership_rows:
        external_id = house.external_id
        if not isinstance(external_id, str):
            continue
        if membership.role == HouseRole.ADMIN:
            allowed_rooms: Optional[Set[str]] = None
        else:
            allowed_rooms = set()
        entry = HouseAccess(
            house=house,
            membership_id=membership.id,
            role=membership.role,
            allowed_rooms=allowed_rooms,
        )
        access[external_id] = entry
        if membership.id is not None and allowed_rooms is not None:
            membership_lookup[membership.id] = entry

    if membership_lookup:
        for entry in _load_room_access(session, membership_lookup.keys()):
            house_entry = membership_lookup.get(entry.membership_id)
            if not house_entry or house_entry.allowed_rooms is None:
                continue
            room_id = str(entry.room_id).strip()
            if room_id:
                house_entry.allowed_rooms.add(room_id)

    return access


def _filter_rooms(house: Dict[str, Any], allowed_rooms: Optional[Set[str]]) -> List[Dict[str, Any]]:
    rooms: List[Dict[str, Any]] = []
    raw_rooms = house.get("rooms")
    if not isinstance(raw_rooms, list):
        return rooms
    for entry in raw_rooms:
        if not isinstance(entry, dict):
            continue
        room_id = str(entry.get("id") or "").strip()
        if not room_id:
            continue
        if allowed_rooms is not None and room_id not in allowed_rooms:
            continue
        rooms.append(dict(entry))
    return rooms


class AccessPolicy:
    """Authorization helper for requests."""

    def __init__(self, user: User, houses: Dict[str, HouseAccess]):
        self.user = user
        self._houses = houses

    @classmethod
    def from_session(cls, session: Session, user: User) -> "AccessPolicy":
        return cls(user=user, houses=build_access_map(session, user))

    def get_house_access(self, external_id: str) -> Optional[HouseAccess]:
        entry = self._houses.get(external_id)
        if entry is None and self.user.server_admin:
            entry = HouseAccess(
                house=None,
                membership_id=None,
                role=HouseRole.ADMIN,
                allowed_rooms=None,
            )
            self._houses[external_id] = entry
        return entry

    def houses_for_templates(self) -> List[Dict[str, Any]]:
        houses: List[Dict[str, Any]] = []
        for entry in _registry_houses():
            filtered = self.filter_house(entry)
            if filtered is not None:
                houses.append(filtered)
        return houses

    def manages_any_house(self) -> bool:
        return any(access.can_manage(self.user) for access in self._houses.values())

    def filter_house(self, house: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        external_id = registry.get_house_external_id(house)
        access = self.get_house_access(external_id)
        if access is None:
            return None
        filtered = dict(house)
        filtered["rooms"] = _filter_rooms(house, access.allowed_rooms)
        return filtered

    def ensure_house(self, house_id: str) -> HouseContext:
        house = registry.find_house(house_id)
        if not house:
            raise LookupError("house not found")
        external_id = registry.get_house_external_id(house)
        access = self.get_house_access(external_id)
        if access is None:
            raise PermissionError("house forbidden")
        filtered = self.filter_house(house)
        if filtered is None:
            raise PermissionError("house forbidden")
        slug = registry.get_house_slug(house)
        return HouseContext(
            original=house,
            filtered=filtered,
            slug=slug,
            external_id=external_id,
            access=access,
        )

    def ensure_room(self, house_id: str, room_id: str) -> RoomContext:
        house = self.ensure_house(house_id)
        room = None
        for entry in house.original.get("rooms", []) or []:
            if isinstance(entry, dict) and entry.get("id") == room_id:
                room = entry
                break
        if room is None:
            raise LookupError("room not found")
        if not house.access.can_view_room(room_id):
            raise PermissionError("room forbidden")
        filtered_room = next(
            (r for r in house.filtered.get("rooms", []) if r.get("id") == room_id),
            None,
        )
        if filtered_room is None:
            filtered_room = dict(room)
        return RoomContext(house=house, room=room, filtered_room=dict(filtered_room))

    def ensure_node(self, node_id: str) -> NodeContext:
        house, room, node = registry.find_node(node_id)
        if not node or not house or not room:
            raise LookupError("node not found")
        external_id = registry.get_house_external_id(house)
        room_id = str(room.get("id") or "")
        if not room_id:
            raise LookupError("node room missing")
        access = self.get_house_access(external_id)
        if access is None or not access.can_view_room(room_id):
            raise PermissionError("node forbidden")
        filtered_house = self.filter_house(house)
        if filtered_house is None:
            raise PermissionError("node forbidden")
        house_ctx = HouseContext(
            original=house,
            filtered=filtered_house,
            slug=registry.get_house_slug(house),
            external_id=external_id,
            access=access,
        )
        filtered_room = next(
            (r for r in filtered_house.get("rooms", []) if r.get("id") == room_id),
            None,
        )
        if filtered_room is None:
            filtered_room = dict(room)
        room_ctx = RoomContext(
            house=house_ctx,
            room=room,
            filtered_room=dict(filtered_room),
        )
        return NodeContext(room=room_ctx, node=dict(node))


__all__ = [
    "AccessPolicy",
    "HouseAccess",
    "HouseContext",
    "NodeContext",
    "RoomContext",
    "build_access_map",
]

