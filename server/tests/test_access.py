"""The roles table (blueprint, People section) as a matrix. Pure: no database."""
import pytest

from ulserver.access import (
    CLAIM, COLOR, CONTROL, MANAGE, PEOPLE, REPORT, SCENES, UPDATES, VIEW,
    Access, AccessDenied, Out, abilities_for,
)

HOME, OTHER = 1, 2
LIGHT = Out(HOME, "rgbcct", "ul-000001")
RELAY = Out(HOME, "relay", "ul-000001")
VALVE = Out(HOME, "solenoid", "ul-000001")
SENSOR = Out(HOME, "motion", "ul-000001")
ELSEWHERE = Out(OTHER, "rgb", "ul-000002")
UNHOMED = Out(None, "white", "ul-000003")

ADMIN = Access(1, admin=True)
OWNER = Access(2, roles={HOME: "owner"})
MEMBER = Access(3, roles={HOME: "member"})
GUEST = Access(4, roles={HOME: "guest"})
STRANGER = Access(5)

FULL = {VIEW, CONTROL, COLOR}


@pytest.mark.parametrize("who,expect", [
    (ADMIN, FULL), (OWNER, FULL), (MEMBER, FULL),
    (GUEST, set()),  # until grants exist a guest membership grants nothing
    (STRANGER, set()),
])
def test_output_abilities(who, expect):
    for t in (LIGHT, RELAY, VALVE):
        assert who.output_can(t) == expect


def test_sensors_are_view_only():
    for who in (ADMIN, OWNER, MEMBER):
        assert who.output_can(SENSOR) == {VIEW}


def test_other_homes_and_unassigned_devices():
    for who in (OWNER, MEMBER, GUEST, STRANGER):
        assert not who.sees(ELSEWHERE) and not who.sees(UNHOMED)
    assert ADMIN.sees(ELSEWHERE) and ADMIN.sees(UNHOMED)


@pytest.mark.parametrize("who,expect", [
    (ADMIN, {SCENES, MANAGE, PEOPLE, CLAIM, UPDATES, REPORT}),
    (OWNER, {SCENES, MANAGE, PEOPLE, CLAIM, UPDATES, REPORT}),
    (MEMBER, {SCENES, REPORT}),
    (GUEST, {REPORT}),
    (STRANGER, set()),
])
def test_home_abilities(who, expect):
    assert who.home_can(HOME) == expect


def test_roles_are_per_home():
    # Owns home 1, member of home 2, guest in home 3.
    a = Access(9, roles={1: "owner", 2: "member", 3: "guest"})
    assert MANAGE in a.home_can(1) and MANAGE not in a.home_can(2) and a.home_can(3) == {REPORT}
    assert a.sees(Out(1, "white")) and a.sees(Out(2, "white")) and not a.sees(Out(3, "white"))
    assert a.home_ids() == {1, 2}
    # Owner of two homes.
    b = Access(10, roles={1: "owner", 2: "owner"})
    assert b.home_can(1) == b.home_can(2) == OWNER.home_can(HOME)


def test_require_codes():
    with pytest.raises(AccessDenied) as e:
        STRANGER.require(LIGHT, {CONTROL})
    assert e.value.code == "no_such_output"  # doesn't leak that it exists
    with pytest.raises(AccessDenied) as e:
        OWNER.require(SENSOR, {CONTROL})
    assert e.value.code == "forbidden"
    OWNER.require(LIGHT, {CONTROL, COLOR})
    with pytest.raises(AccessDenied) as e:
        MEMBER.require_home(HOME, MANAGE)
    assert e.value.code == "forbidden"
    with pytest.raises(AccessDenied) as e:
        MEMBER.require_home(OTHER, SCENES)
    assert e.value.code == "no_such_home"


def test_field_abilities():
    assert abilities_for({"on": True, "level": 0.4, "fade_ms": 200}) == {CONTROL}
    assert abilities_for({"color": [1, 2, 3]}) == {COLOR}
    assert abilities_for({"cct": 3000}) == {COLOR}
    assert abilities_for({"effect": {"id": "flow"}, "on": True}) == {CONTROL, COLOR}
    assert abilities_for({}) == set()


def test_view_key_groups_identical_access():
    assert Access(1, roles={1: "owner"}).view_key() == Access(2, roles={1: "owner"}).view_key()
    assert Access(1, roles={1: "owner"}).view_key() != Access(1, roles={1: "member"}).view_key()


def test_sees_home():
    assert ADMIN.sees_home(None) and ADMIN.sees_home(OTHER)
    assert OWNER.sees_home(HOME) and MEMBER.sees_home(HOME)
    assert not GUEST.sees_home(HOME) and not OWNER.sees_home(OTHER) and not OWNER.sees_home(None)
