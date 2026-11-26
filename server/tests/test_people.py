"""Per-home roles through the real API and WebSocket: one person, a different role in each home."""
import asyncio
import json
import secrets

import pytest
from conftest import CONTRACTS
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from ulserver import contracts
from ulserver.access import Access
from ulserver.config import get_settings
from ulserver.hub import ACCESS_CHANGED, Client, Hub

C3_BOARD = json.loads((CONTRACTS / "examples/board/c3-bench.json").read_text())
PW = "a long enough password"


def _node(admin, name, home_id):
    dev = admin.post("/api/admin/nodes", json={"name": name, "board": C3_BOARD}).json()["id"]
    assert admin.put(f"/api/admin/nodes/{dev}", json={"home_id": home_id}).status_code == 200
    return dev


def _seed_outputs(devs):
    """Outputs normally arrive with a device's info message; insert them directly here."""
    eng = create_engine(get_settings().database_url)
    with eng.begin() as c:
        for dev in devs:
            nid = c.execute(text("SELECT id FROM nodes WHERE device_id = :d"), {"d": dev}).scalar_one()
            c.execute(text("INSERT INTO outputs (node_id, key, kind, name, caps) VALUES "
                           "(:n, 'lamp', 'rgbcct', 'Lamp', '[\"onoff\",\"level\",\"color\",\"cct\"]'), "
                           "(:n, 'pir', 'motion', 'Motion', '[\"motion\"]')"), {"n": nid})
    eng.dispose()


def _login(c, username):
    c.cookies.clear()
    c.headers.pop("X-UL-CSRF", None)
    r = c.post("/api/auth/login", json={"username": username, "password": PW})
    assert r.status_code == 200, r.text
    c.headers["X-UL-CSRF"] = r.json()["csrf"]
    return r.json()


@pytest.fixture()
def world(admin):
    sfx = secrets.token_hex(3)
    homes = {k: admin.post("/api/admin/homes", json={"name": f"P-{k}"}).json()["id"] for k in ("own", "mem", "gst", "far")}
    devs = {k: _node(admin, f"P-{k}", h) for k, h in homes.items()}
    _seed_outputs(devs.values())
    r = admin.post("/api/admin/users", json={"username": f"multi{sfx}", "password": PW, "home_id": homes["own"], "home_role": "owner"})
    assert r.status_code == 200, r.text
    uid = r.json()["id"]
    for k, role in (("mem", "member"), ("gst", "guest")):
        assert admin.post(f"/api/admin/homes/{homes[k]}/members", json={"user_id": uid, "role": role}).status_code == 200
    # A second owner of the first home, who also owns another.
    r = admin.post("/api/admin/users", json={"username": f"coowner{sfx}", "password": PW, "home_id": homes["own"], "home_role": "owner"})
    assert admin.post(f"/api/admin/homes/{homes['far']}/members", json={"user_id": r.json()["id"], "role": "owner"}).status_code == 200
    return homes, devs, uid, sfx


def test_admin_rejects_bad_roles(admin):
    h = admin.post("/api/admin/homes", json={"name": "Roles"}).json()["id"]
    assert admin.post("/api/admin/users", json={"username": "badrole", "password": PW, "home_id": h, "home_role": "admin"}).status_code == 422
    u = admin.post("/api/admin/users", json={"username": "plain", "password": PW}).json()["id"]
    assert admin.post(f"/api/admin/homes/{h}/members", json={"user_id": u, "role": "boss"}).status_code == 422
    assert not next(x for x in admin.get("/api/admin/users").json() if x["username"] == "plain")["is_admin"]


def test_one_person_many_roles(pki, world):
    homes, devs, uid, sfx = world
    from ulserver.main import create_app

    with TestClient(create_app(), base_url="http://testserver") as c:  # fresh app: control cache sees the outputs
        me = _login(c, f"multi{sfx}")
        assert me["admin"] is False
        roles = {h["id"]: (h["role"], set(h["can"])) for h in me["homes"]}
        assert roles[homes["own"]] == ("owner", {"scenes", "manage", "people", "claim", "updates", "report"})
        assert roles[homes["mem"]] == ("member", {"scenes", "report"})
        assert roles[homes["gst"]] == ("guest", {"report"})
        assert homes["far"] not in roles

        seen = {d["id"]: d for d in c.get("/api/devices").json()}
        assert set(seen) == {devs["own"], devs["mem"]}  # guest home shows nothing until grants exist
        lamp = next(o for o in seen[devs["mem"]]["outputs"] if o["key"] == "lamp")
        pir = next(o for o in seen[devs["mem"]]["outputs"] if o["key"] == "pir")
        assert lamp["can"] == ["view", "control", "color"] and pir["can"] == ["view"]
        assert "admin" not in seen[devs["own"]]
        assert {h["id"] for h in c.get("/api/homes").json()} == {homes["own"], homes["mem"]}

        # Allowed: passes access, then fails only because tests run without a broker.
        assert c.put(f"/api/devices/{devs['own']}/outputs/lamp", json={"on": True, "color": [255, 0, 0]}).status_code == 503
        assert c.put(f"/api/devices/{devs['mem']}/outputs/lamp", json={"level": 0.3}).status_code == 503
        # Sensors can't be driven; other homes don't exist as far as this person knows.
        r = c.put(f"/api/devices/{devs['own']}/outputs/pir", json={"on": True})
        assert r.status_code == 403 and r.json()["detail"]["error"] == "forbidden"
        for k in ("gst", "far"):
            r = c.put(f"/api/devices/{devs[k]}/outputs/lamp", json={"on": True})
            assert r.status_code == 404 and r.json()["detail"]["error"] == "no_such_output"
            assert c.post(f"/api/devices/{devs[k]}/outputs/lamp/pulse", json={"ms": 1000}).status_code == 404
        assert c.get("/api/admin/users").status_code == 403

        with c.websocket_connect("/api/ws") as ws:
            snap = contracts.validate("ws-server", ws.receive_json())
            assert {d["id"] for d in snap["devices"]} == {devs["own"], devs["mem"]}
            ws.send_json({"t": "set", "dev": devs["far"], "out": "lamp", "state": {"on": True}})
            err = contracts.validate("ws-server", ws.receive_json())
            assert err["error"] == "no_such_output"
            ws.send_json({"t": "live", "dev": devs["own"], "out": "pir", "state": {"on": True}})
            assert ws.receive_json()["error"] == "forbidden"

        # Co-owner sees both homes they own; the other person's member home stays hidden.
        _login(c, f"coowner{sfx}")
        assert {d["id"] for d in c.get("/api/devices").json()} == {devs["own"], devs["far"]}


def test_removing_membership_hides_the_home(admin, world):
    homes, devs, uid, sfx = world
    assert admin.delete(f"/api/admin/homes/{homes['mem']}/members/{uid}").status_code == 200
    assert admin.delete(f"/api/admin/homes/{homes['mem']}/members/{uid}").status_code == 404
    _login(admin, f"multi{sfx}")
    assert devs["mem"] not in {d["id"] for d in admin.get("/api/devices").json()}


class _FakeWS:
    def __init__(self):
        self.closed = None

    async def close(self, code):
        self.closed = code


async def test_hub_filters_and_refreshes():
    from ulserver.models import Node, Output

    hub = Hub()
    clients = {name: Client(ws=_FakeWS(), access=a) for name, a in {
        "admin": Access(1, admin=True), "owner": Access(2, roles={7: "owner"}),
        "guest": Access(3, roles={7: "guest"}), "other": Access(4, roles={8: "owner"})}.items()}
    for c in clients.values():
        hub.add(c)
    node = Node(device_id="ul-0000aa", name="N", chip="esp32c3", kind="node", home_id=7, online=True, info={})
    lamp = Output(key="lamp", kind="white", name="Lamp", caps=["onoff", "level"], node_id=1)
    hub.broadcast_device(node, [lamp])
    got = {k: c.queue.qsize() for k, c in clients.items()}
    assert got == {"admin": 1, "owner": 1, "guest": 0, "other": 0}
    assert "admin" in json.loads(clients["admin"].queue.get_nowait())["device"]
    assert "admin" not in json.loads(clients["owner"].queue.get_nowait())["device"]
    hub.broadcast_admin({"t": "health", "dev": "ul-0000aa", "health": {}})
    assert clients["admin"].queue.qsize() == 1 and clients["owner"].queue.qsize() == 0

    hub.refresh_user(2)
    await asyncio.sleep(0)
    assert clients["owner"].ws.closed == ACCESS_CHANGED and clients["owner"] not in hub.clients
    assert clients["admin"].ws.closed is None
