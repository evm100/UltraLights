"""Guest grants as rules: scope, kinds, abilities, weekly windows and expiry (no database)."""
import datetime as dt

from ulserver import contracts
from ulserver.access import Access, Out
from ulserver.grants import rule_from

UTC = dt.timezone.utc
SAT = dt.datetime(2026, 11, 28, 17, 0, tzinfo=UTC)  # a Saturday


def test_examples_are_contract_valid():
    for p in (contracts._dir() / "examples" / "grant").glob("*.json"):
        contracts.validate("grant", __import__("json").loads(p.read_text()))


def test_scope_and_kinds():
    r = rule_from({"v": 1, "scope": ["room:3", "device:ul-aaaaaa"], "can": ["control"], "kinds": ["light", "switch"]}, None, SAT)
    assert r.can == {"view", "control"} and r.active
    assert r.covers("rgb", "ul-bbbbbb", 3)            # a light in the room
    assert r.covers("relay", "ul-aaaaaa", None)       # anything kind-allowed on the device
    assert not r.covers("solenoid", "ul-aaaaaa", 3)   # valves only when listed
    assert not r.covers("white", "ul-bbbbbb", 4)      # another room
    assert not r.covers("motion", "ul-aaaaaa", 3)     # sensors only when listed
    home = rule_from({"v": 1, "scope": ["home"], "can": [], "kinds": ["solenoid"]}, None, SAT)
    assert home.covers("solenoid", "ul-cccccc", None) and not home.covers("white", "ul-cccccc", None)


def test_windows_and_expiry():
    def active(doc, at, tz=None):
        return rule_from({"v": 1, "scope": ["home"], "can": [], "kinds": ["light"], **doc}, tz, at).active

    w = {"window": {"days": [5], "from": "16:00", "to": "23:59"}}
    assert active(w, SAT) and not active(w, SAT.replace(hour=15, minute=59)) and not active(w, SAT + dt.timedelta(days=1))
    # Past midnight belongs to the day the window opened on.
    late = {"window": {"days": [4], "from": "20:00", "to": "02:00"}}  # Friday night
    assert active(late, SAT.replace(hour=1, minute=30)) and not active(late, SAT.replace(hour=2))
    assert active(late, SAT - dt.timedelta(days=1, hours=-4)) and not active(late, SAT.replace(hour=21))
    assert active({"window": {"days": [5], "from": "00:00", "to": "00:00"}}, SAT)  # equal = the whole day
    # The home's time zone: 17:00 UTC is 04:00 Sunday in Sydney.
    assert not active(w, SAT, "Australia/Sydney") and active({"window": {"days": [6], "from": "03:00", "to": "05:00"}}, SAT, "Australia/Sydney")
    # Expiry is through the end of that day, in the home's time zone.
    assert active({"expires": "2026-11-28"}, SAT) and not active({"expires": "2026-11-27"}, SAT)
    assert not active({"expires": "2026-11-28"}, SAT, "Australia/Sydney")


def test_access_for_a_guest():
    grant = {"v": 1, "scope": ["room:3"], "can": ["control"], "kinds": ["light", "sensor"],
             "window": {"from": "16:00", "to": "18:00"}}
    a = Access(user_id=5, roles={1: "guest", 2: "member"}, grants={1: rule_from(grant, None, SAT)})
    lamp, strip_far, pir = Out(1, "rgb", "ul-aaaaaa", 3), Out(1, "rgb", "ul-aaaaaa", 4), Out(1, "motion", "ul-aaaaaa", 3)
    assert a.output_can(lamp) == {"view", "control"}  # no color
    assert a.output_can(strip_far) == frozenset() and a.output_can(pir) == {"view"}
    assert a.output_can(Out(2, "rgb", "ul-dddddd", None)) == {"view", "control", "color"}  # member elsewhere
    assert a.device_home_ids() == {1, 2} and a.home_ids() == {2} and not a.sees_home(1)
    # A grant left over on a non-guest membership is ignored.
    assert Access(user_id=5, roles={1: "member"}, grants=a.grants).grant(1) is None
    # Window closes at 18:00: the loaded access goes stale, and a fresh one sees nothing.
    assert not a.stale(SAT) and a.stale(SAT.replace(hour=18))
    later = Access(user_id=5, roles=a.roles, grants={1: rule_from(grant, None, SAT.replace(hour=18))})
    assert later.output_can(lamp) == frozenset() and later.device_home_ids() == {2}
    assert later.view_key() != a.view_key()
