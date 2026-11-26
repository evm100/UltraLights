"""Owner tools: rooms, the room toggle, device and output settings, members and invites."""
import contextlib
import json
import secrets

import pytest
from conftest import CONTRACTS
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from ulserver.config import get_settings

C3_BOARD = json.loads((CONTRACTS / "examples/board/c3-bench.json").read_text())
PW = "a long enough password"


def _sql(q, **kw):
    eng = create_engine(get_settings().database_url)
    with eng.begin() as c:
        r = c.execute(text(q), kw)
        rows = r.all() if r.returns_rows else None
    eng.dispose()
    return rows


def _seed_outputs(dev):
    """Outputs normally arrive with the device's info; keys follow the bench board's default mapping."""
    nid = _sql("SELECT id FROM nodes WHERE device_id = :d", d=dev)[0][0]
    for key, kind, caps in (("warm", "white", ["onoff", "level"]), ("strip", "argb", ["onoff", "level", "color"]),
                            ("relay", "relay", ["onoff"]), ("pir", "motion", ["motion"])):
        _sql("INSERT INTO outputs (node_id, key, kind, name, caps) VALUES (:n, :k, :kind, '', CAST(:caps AS jsonb))",
             n=nid, k=key, kind=kind, caps=json.dumps(caps))


def _login(c, username):
    c.cookies.clear()
    c.headers.pop("X-UL-CSRF", None)
    r = c.post("/api/auth/login", json={"username": username, "password": PW})
    assert r.status_code == 200, r.text
    c.headers["X-UL-CSRF"] = r.json()["csrf"]
    return r.json()


@pytest.fixture()
def home(admin):
    sfx = secrets.token_hex(3)
    hid = admin.post("/api/admin/homes", json={"name": f"H-{sfx}"}).json()["id"]
    other = admin.post("/api/admin/homes", json={"name": f"O-{sfx}"}).json()["id"]
    dev = admin.post("/api/admin/nodes", json={"name": "Kitchen node", "board": C3_BOARD}).json()["id"]
    admin.put(f"/api/admin/nodes/{dev}", json={"home_id": hid})
    _seed_outputs(dev)
    users = {}
    for name, h, role in (("own", hid, "owner"), ("mem", hid, "member"), ("far", other, "owner")):
        r = admin.post("/api/admin/users", json={"username": f"{name}{sfx}", "password": PW, "home_id": h, "home_role": role})
        users[name] = (f"{name}{sfx}", r.json()["id"])
    far_room = admin.post(f"/api/admin/homes/{other}/rooms", json={"name": "Elsewhere"}).json()["id"]
    return {"hid": hid, "other": other, "dev": dev, "users": users, "far_room": far_room}


@contextlib.contextmanager
def _captured_app():
    """A fresh app (its control cache sees the seeded outputs) with publishes captured and
    commands answered at once."""
    from ulserver.main import create_app

    with TestClient(create_app(), base_url="http://testserver") as c:
        sent = []

        async def publish(topic, payload, schema=None, qos=0, retain=False):
            sent.append((topic, payload))
            waiters = c.app.state.control.cmd_waiters
            if topic.endswith("/cmd") and waiters is not None and payload["id"] in waiters:
                waiters[payload["id"]].set_result({"type": "cmd_result", "cmd_id": payload["id"], "ok": True})

        c.app.state.control.publish = publish
        c.sent = sent
        yield c


@pytest.fixture()
def app_client(pki, home):
    with _captured_app() as c:
        yield c


def test_rooms_are_managed_by_owners(app_client, home):
    c, hid = app_client, home["hid"]
    _login(c, home["users"]["own"][0])
    a = c.post(f"/api/homes/{hid}/rooms", json={"name": "Kitchen"}).json()["id"]
    b = c.post(f"/api/homes/{hid}/rooms", json={"name": "Den"}).json()["id"]
    assert [r["name"] for r in next(h for h in c.get("/api/homes").json() if h["id"] == hid)["rooms"]] == ["Kitchen", "Den"]
    assert c.put(f"/api/homes/{hid}/rooms/order", json={"room_ids": [b, a]}).status_code == 200
    assert c.put(f"/api/homes/{hid}/rooms/order", json={"room_ids": [b]}).status_code == 422
    assert c.put(f"/api/homes/{hid}/rooms/{a}", json={"name": "Galley"}).status_code == 200
    assert [r["name"] for r in next(h for h in c.get("/api/homes").json() if h["id"] == hid)["rooms"]] == ["Den", "Galley"]
    # Someone else's room can't be reached through this home.
    assert c.put(f"/api/homes/{hid}/rooms/{home['far_room']}", json={"name": "X"}).status_code == 404

    _login(c, home["users"]["mem"][0])
    r = c.post(f"/api/homes/{hid}/rooms", json={"name": "Nope"})
    assert r.status_code == 403 and r.json()["detail"]["error"] == "forbidden"
    _login(c, home["users"]["far"][0])
    assert c.post(f"/api/homes/{hid}/rooms", json={"name": "Nope"}).status_code == 404
    assert c.get(f"/api/homes/{hid}/devices").status_code == 404


def test_output_settings_go_into_config(app_client, home):
    c, hid, dev = app_client, home["hid"], home["dev"]
    _login(c, home["users"]["own"][0])
    room = c.post(f"/api/homes/{hid}/rooms", json={"name": "Kitchen"}).json()["id"]
    r = c.put(f"/api/devices/{dev}/outputs/warm/settings", json={"name": "Undercabinet", "room_id": room, "max_level": 0.6})
    assert r.status_code == 200, r.text
    assert r.json()["config_rev"] == 1 and r.json()["published"] is True
    topic, cfg = c.sent[-1]
    assert topic == f"ul/v1/d/{dev}/config"
    warm = next(o for o in cfg["outputs"] if o["key"] == "warm")
    assert warm["name"] == "Undercabinet" and warm["max_level"] == 0.6
    assert {o["key"] for o in cfg["outputs"]} == {"warm", "cool", "strip", "relay"}  # default mapping materialised

    settings = {o["key"]: o for o in c.get(f"/api/homes/{hid}/devices").json()[0]["outputs"]}
    assert settings["warm"] == {"key": "warm", "kind": "white", "name": "Undercabinet", "room_id": room,
                                "max_level": 0.6, "power_on": "last"}
    assert "max_level" not in settings["relay"] and "power_on" not in settings["pir"]

    # Back to the default removes the key; an unchanged mapping doesn't bump the revision.
    r = c.put(f"/api/devices/{dev}/outputs/warm/settings", json={"max_level": 1, "power_on": "off"})
    assert r.json()["config_rev"] == 2
    warm = next(o for o in c.sent[-1][1]["outputs"] if o["key"] == "warm")
    assert "max_level" not in warm and warm["power_on"] == "off"
    n = len(c.sent)
    r = c.put(f"/api/devices/{dev}/outputs/warm/settings", json={"room_id": None})
    assert "config_rev" not in r.json() and len([s for s in c.sent[n:] if s[0].endswith("/config")]) == 0

    for out, body in (("relay", {"max_level": 0.5}), ("pir", {"power_on": "on"}), ("warm", {"room_id": home["far_room"]})):
        assert c.put(f"/api/devices/{dev}/outputs/{out}/settings", json=body).status_code == 422, (out, body)
    assert c.put(f"/api/devices/{dev}/outputs/warm/settings", json={"max_level": 0.01}).status_code == 422

    assert c.put(f"/api/devices/{dev}", json={"name": "Kitchen ESP"}).status_code == 200
    assert next(d for d in c.get("/api/devices").json() if d["id"] == dev)["name"] == "Kitchen ESP"

    _login(c, home["users"]["mem"][0])
    assert c.put(f"/api/devices/{dev}/outputs/warm/settings", json={"name": "X"}).status_code == 403
    assert c.put(f"/api/devices/{dev}", json={"name": "X"}).status_code == 403
    _login(c, home["users"]["far"][0])
    assert c.put(f"/api/devices/{dev}/outputs/warm/settings", json={"name": "X"}).status_code == 404


def test_room_power_switches_lights_only(app_client, home):
    c, hid, dev = app_client, home["hid"], home["dev"]
    _login(c, home["users"]["own"][0])
    room = c.post(f"/api/homes/{hid}/rooms", json={"name": "Kitchen"}).json()["id"]
    for out in ("warm", "strip", "relay", "pir"):
        assert c.put(f"/api/devices/{dev}/outputs/{out}/settings", json={"room_id": room}).status_code == 200
    _login(c, home["users"]["mem"][0])  # members control everything in the home
    n = len(c.sent)
    r = c.post(f"/api/homes/{hid}/rooms/{room}/power", json={"on": False})
    assert r.status_code == 200, r.text
    assert sorted(r.json()["set"]) == [f"{dev}/strip", f"{dev}/warm"]
    sets = {t.rsplit("/", 1)[1]: p for t, p in c.sent[n:] if "/set/" in t}
    assert set(sets) == {"warm", "strip"} and all(p["on"] is False and p["src"].startswith("ui:") for p in sets.values())

    _login(c, home["users"]["far"][0])
    assert c.post(f"/api/homes/{hid}/rooms/{room}/power", json={"on": True}).status_code == 404


def test_members_and_last_owner(app_client, home):
    c, hid = app_client, home["hid"]
    own_name, own_id = home["users"]["own"]
    _, mem_id = home["users"]["mem"]
    _login(c, own_name)
    ms = {m["user_id"]: m for m in c.get(f"/api/homes/{hid}/members").json()}
    assert ms[own_id]["role"] == "owner" and ms[own_id]["you"] and ms[mem_id]["role"] == "member"
    r = c.put(f"/api/homes/{hid}/members/{own_id}", json={"role": "member"})
    assert r.status_code == 409 and r.json()["detail"]["error"] == "last_owner"
    assert c.delete(f"/api/homes/{hid}/members/{own_id}").status_code == 409
    assert c.put(f"/api/homes/{hid}/members/{mem_id}", json={"role": "visitor"}).status_code == 422
    # Co-owner first, then the original owner may step down.
    assert c.put(f"/api/homes/{hid}/members/{mem_id}", json={"role": "owner"}).status_code == 200
    assert c.put(f"/api/homes/{hid}/members/{own_id}", json={"role": "member"}).status_code == 200
    assert c.get(f"/api/homes/{hid}/members").status_code == 403  # now a member: no people ability


def test_invites(app_client, home):
    c, hid = app_client, home["hid"]
    _login(c, home["users"]["own"][0])
    assert c.post(f"/api/homes/{hid}/invites", json={"role": "guest"}).status_code == 422
    inv = c.post(f"/api/homes/{hid}/invites", json={"role": "member"}).json()
    assert inv["url"].endswith(f"/#/invite/{inv['token']}")
    assert [i["id"] for i in c.get(f"/api/homes/{hid}/invites").json()] == [inv["id"]]
    revoked = c.post(f"/api/homes/{hid}/invites", json={"role": "owner"}).json()
    assert c.delete(f"/api/homes/{hid}/invites/{revoked['id']}").status_code == 200
    co = c.post(f"/api/homes/{hid}/invites", json={"role": "owner"}).json()

    # Signed out: the link shows what it offers, then creates an account and signs it in.
    c.cookies.clear()
    c.headers.pop("X-UL-CSRF", None)
    v = c.get(f"/api/invites/{inv['token']}").json()
    assert v["role"] == "member" and v["home"].startswith("H-") and v["signed_in_as"] is None
    assert c.post(f"/api/invites/{inv['token']}/accept", json={}).status_code == 422
    name = f"new{secrets.token_hex(3)}"
    r = c.post(f"/api/invites/{inv['token']}/accept", json={"username": name, "password": PW, "display_name": "New"})
    assert r.status_code == 200 and r.json() == {"home_id": hid, "role": "member"}
    me = c.get("/api/auth/me").json()
    assert me["username"] == name and [(h["id"], h["role"]) for h in me["homes"]] == [(hid, "member")]
    assert c.get(f"/api/invites/{inv['token']}").status_code == 404  # single use
    assert c.get(f"/api/invites/{revoked['token']}").status_code == 404

    # Signed in: the current account joins (CSRF required), here as a co-owner.
    far_name, far_id = home["users"]["far"]
    _login(c, far_name)
    hdr = c.headers.pop("X-UL-CSRF")
    assert c.post(f"/api/invites/{co['token']}/accept", json={}).status_code == 403
    c.headers["X-UL-CSRF"] = hdr
    assert c.post(f"/api/invites/{co['token']}/accept", json={}).json() == {"home_id": hid, "role": "owner"}
    roles = {h["id"]: h["role"] for h in c.get("/api/auth/me").json()["homes"]}
    assert roles == {hid: "owner", home["other"]: "owner"}

    # A member invite never demotes an owner.
    again = c.post(f"/api/homes/{hid}/invites", json={"role": "member"}).json()
    assert c.post(f"/api/invites/{again['token']}/accept", json={}).json()["role"] == "owner"


VALVE_CONFIG = [
    {"key": "warm", "kind": "white", "channels": {"w": "warm"}},
    {"key": "valve", "kind": "solenoid", "name": "Bed valve", "channels": {"coil": "relay"}, "max_on_ms": 600000},
]


@pytest.fixture()
def garden(admin, home):
    """The bench node remapped with a valve on the relay channel, as the device would report it."""
    dev = home["dev"]
    assert admin.put(f"/api/admin/nodes/{dev}/config", json={"outputs": VALVE_CONFIG}).status_code == 200
    nid = _sql("SELECT id FROM nodes WHERE device_id = :d", d=dev)[0][0]
    _sql("INSERT INTO outputs (node_id, key, kind, name, caps, meta) VALUES "
         "(:n, 'valve', 'solenoid', 'Bed valve', '[\"pulse\"]', '{\"max_on_ms\": 600000}')", n=nid)
    return home


@pytest.fixture()
def garden_client(pki, garden):
    with _captured_app() as c:
        yield c


def test_valves_pulse_within_their_limit_and_close(garden_client, garden):
    from ulserver import contracts

    c, dev = garden_client, garden["dev"]
    _login(c, garden["users"]["mem"][0])  # members may run valves
    r = c.post(f"/api/devices/{dev}/outputs/valve/pulse", json={"ms": 15 * 60_000})
    assert r.status_code == 200, r.text
    assert r.json()["ms"] == 600000 and r.json()["limited"] is True and r.json()["result"]["ok"] is True
    topic, cmd = c.sent[-1]
    assert topic == f"ul/v1/d/{dev}/cmd" and cmd["op"] == "pulse" and cmd["ms"] == 600000
    r = c.post(f"/api/devices/{dev}/outputs/valve/pulse", json={"ms": 5 * 60_000})
    assert r.json()["ms"] == 300000 and r.json()["limited"] is False

    assert c.post(f"/api/devices/{dev}/outputs/valve/close").status_code == 200
    topic, cmd = c.sent[-1]
    assert cmd["op"] == "close" and cmd["out"] == "valve"
    contracts.validate("cmd", cmd)
    assert not any(t.endswith("/set/valve") for t, _ in c.sent)  # never a retained set

    assert c.post(f"/api/devices/{dev}/outputs/valve/pulse", json={"ms": 7_200_001}).status_code == 422
    r = c.post(f"/api/devices/{dev}/outputs/warm/pulse", json={"ms": 1000})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "not_a_solenoid"
    assert c.post(f"/api/devices/{dev}/outputs/warm/close").status_code == 422
    _login(c, garden["users"]["far"][0])
    assert c.post(f"/api/devices/{dev}/outputs/valve/pulse", json={"ms": 1000}).status_code == 404
    assert c.post(f"/api/devices/{dev}/outputs/valve/close").status_code == 404


def test_owner_sets_valve_run_limit(garden_client, garden):
    c, hid, dev = garden_client, garden["hid"], garden["dev"]
    _login(c, garden["users"]["own"][0])
    valve = next(o for o in c.get(f"/api/homes/{hid}/devices").json()[0]["outputs"] if o["key"] == "valve")
    assert valve["max_on_ms"] == 600000 and "power_on" not in valve
    r = c.put(f"/api/devices/{dev}/outputs/valve/settings", json={"max_on_ms": 45 * 60_000})
    assert r.status_code == 200, r.text
    entry = next(o for o in c.sent[-1][1]["outputs"] if o["key"] == "valve")
    assert entry["max_on_ms"] == 2_700_000
    for body in ({"max_on_ms": 30_000}, {"max_on_ms": 7_200_001}, {"max_on_ms": None}, {"power_on": "on"}):
        assert c.put(f"/api/devices/{dev}/outputs/valve/settings", json=body).status_code == 422, body
    assert c.put(f"/api/devices/{dev}/outputs/warm/settings", json={"max_on_ms": 60_000}).status_code == 422
    _login(c, garden["users"]["mem"][0])
    assert c.put(f"/api/devices/{dev}/outputs/valve/settings", json={"max_on_ms": 60_000}).status_code == 403
