"""Who may do what: the roles table from the blueprint (People section) as one pure module.

Roles belong to a home membership, not to the account: one person can own one home,
be a member of a second and a guest in a third. Admin is a flag on the account and
sees every home. Nothing here touches the database except load_access(), so the whole
table is unit tested.

Output abilities (per light, switch or valve):
  view     see it and its state
  control  on/off, level, fades, solenoid pulses
  color    color, white channel, warmth (cct) and effects
Home abilities:
  scenes   create scenes and schedules
  manage   rename devices, assign rooms, set limits
  people   invite members and guests, edit grants
  claim    claim or remove devices
  updates  approve offered firmware updates
  report   report a problem

Guests get output abilities only through grants. Until grants exist (Homes and people
phase, guests slice) a guest membership grants nothing.
"""
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import HomeMember, User

HOME_ROLES = ("owner", "member", "guest")

VIEW, CONTROL, COLOR = "view", "control", "color"
OUTPUT_ABILITIES = (VIEW, CONTROL, COLOR)
SCENES, MANAGE, PEOPLE, CLAIM, UPDATES, REPORT = "scenes", "manage", "people", "claim", "updates", "report"
HOME_ABILITIES = (SCENES, MANAGE, PEOPLE, CLAIM, UPDATES, REPORT)

_HOME_CAN: dict[str, frozenset[str]] = {
    "owner": frozenset(HOME_ABILITIES),
    "member": frozenset({SCENES, REPORT}),
    "guest": frozenset({REPORT}),
}

INPUT_KINDS = frozenset({"motion"})  # sensors are watched, never driven

# Which ability each set/live field needs. Anything not listed is refused.
FIELD_ABILITY: dict[str, str] = {
    "on": CONTROL, "level": CONTROL, "fade_ms": CONTROL,
    "color": COLOR, "white": COLOR, "cct": COLOR, "effect": COLOR,
}


class Target(Protocol):
    """An output as access sees it (control.OutputInfo and access.Out both fit)."""
    home_id: int | None
    kind: str


@dataclass(frozen=True)
class Out:
    home_id: int | None
    kind: str
    dev: str = ""
    room_id: int | None = None


class AccessDenied(Exception):
    """code is 'no_such_output' when the caller may not even see the target (don't leak it),
    'forbidden' when they can see it but lack the ability."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _limit_kind(kind: str, can: frozenset[str]) -> frozenset[str]:
    return can & {VIEW} if kind in INPUT_KINDS else can


@dataclass(frozen=True)
class Access:
    user_id: int
    admin: bool = False
    roles: dict[int, str] = field(default_factory=dict)  # home id -> owner | member | guest

    def view_key(self) -> tuple:
        """Equal keys see devices identically (the hub renders one view per key)."""
        return (self.admin, tuple(sorted(self.roles.items())))

    def role(self, home_id: int | None) -> str | None:
        return None if home_id is None else self.roles.get(home_id)

    def home_ids(self) -> set[int] | None:
        """Homes whose rooms and devices this person can list; None means all (admin)."""
        if self.admin:
            return None
        return {h for h, r in self.roles.items() if r in ("owner", "member")}

    def sees_home(self, home_id: int | None) -> bool:
        """Sees every device in the home, even one with no outputs yet (owners, members, admins)."""
        homes = self.home_ids()
        return homes is None or home_id in homes

    def home_can(self, home_id: int | None) -> frozenset[str]:
        if self.admin:
            return frozenset(HOME_ABILITIES)
        return _HOME_CAN.get(self.role(home_id) or "", frozenset())

    def output_can(self, t: Target) -> frozenset[str]:
        if self.admin:
            return _limit_kind(t.kind, frozenset(OUTPUT_ABILITIES))
        role = self.role(t.home_id)
        if role in ("owner", "member"):
            return _limit_kind(t.kind, frozenset(OUTPUT_ABILITIES))
        return frozenset()  # guests: grants come later

    def sees(self, t: Target) -> bool:
        return VIEW in self.output_can(t)

    def require(self, t: Target, needed: set[str] | frozenset[str]) -> None:
        can = self.output_can(t)
        if VIEW not in can:
            raise AccessDenied("no_such_output")
        missing = set(needed) - can
        if missing:
            raise AccessDenied("forbidden", "needs " + ", ".join(sorted(missing)))

    def require_home(self, home_id: int | None, ability: str) -> None:
        if ability not in self.home_can(home_id):
            if self.admin or home_id in self.roles:
                raise AccessDenied("forbidden", f"needs {ability}")
            raise AccessDenied("no_such_home")


def abilities_for(fields: dict[str, Any]) -> set[str]:
    """The abilities a set/live body needs. Unknown fields are left to the contract check."""
    return {FIELD_ABILITY[k] for k in fields if k in FIELD_ABILITY}


ADMIN_ACCESS = Access(user_id=0, admin=True)  # admin panel views and tools


async def load_access(db: AsyncSession, user: User) -> Access:
    rows = await db.execute(select(HomeMember.home_id, HomeMember.role).where(HomeMember.user_id == user.id))
    return Access(user_id=user.id, admin=user.is_admin, roles={h: r for h, r in rows})
