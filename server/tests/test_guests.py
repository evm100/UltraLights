"""Guests: invites that carry a grant, what a guest sees and may do, windows, expiry, and
owners changing a grant."""
import datetime as dt
import secrets

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from test_homes import PW, _login, app_client, home  # noqa: F401 - fixtures

from ulserver import contracts

UTC = dt.timezone.utc


def _grant(**kw):
    return {"v": 1, "can": ["control"], "kinds": ["light"], **kw}


def _rooms(c, h):
    _login(c, h["users"]["own"][0])
    kitchen = c.post(f"/api/homes/{h['hid']}/rooms", json={"name": "Kitchen"}).json()["id"]
    den = c.post(f"/api/homes/{h['hid']}/rooms", json={"name": "Den"}).json()["id"]
    for out in ("warm", "strip", "relay"):
        assert c.put(f"/api/devices/{h['dev']}/outputs/{out}/settings", json={"room_id": kitchen}).status_code == 200
    return kitchen, den


def _join_as_guest(c, h, grant):
    _login(c, h["users"]["own"][0])
    r = c.post(f"/api/homes/{h['hid']}/invites", json={"role": "guest", "grant": grant})
    assert r.status_code == 200, r.text
    inv = r.json()
    c.cookies.clear()
    c.headers.pop("X-UL-CSRF", None)
    offer = c.get(f"/api/invites/{inv['token']}").json()
    assert offer["role"] == "guest" and offer["grant"]["can"] == grant["can"] and "scope" not in offer["grant"]
    name = f"gst{secrets.token_hex(3)}"
    r = c.post(f"/api/invites/{inv['token']}/accept", json={"username": name, "password": PW})
    assert r.json() == {"home_id": h["hid"], "role": "guest"}
    me = c.get("/api/auth/me").json()
    c.headers["X-UL-CSRF"] = me["csrf"]
    return name, me["id"]


def _outs(c):
    return {o["key"]: o["can"] for d in c.get("/api/devices").json() for o in d["outputs"]}


def test_guest_invite_checks(app_client, home):
    c, hid = app_client, home["hid"]
    kitchen, _ = _rooms(c, home)
    url = f"/api/homes/{hid}/invites"
    assert c.post(url, json={"role": "guest"}).status_code == 422  # a guest needs a grant
    assert c.post(url, json={"role": "member", "grant": _grant(scope=["home"])}).status_code == 422
    r = c.post(url, json={"role": "guest", "grant": _grant(scope=[f"room:{home['far_room']}"])})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "grant_invalid"  # another home's room
    assert c.post(url, json={"role": "guest", "grant": _grant(scope=["device:ul-000000"])}).status_code == 422
    assert c.post(url, json={"role": "guest", "grant": _grant(scope=["home", f"room:{kitchen}"])}).status_code == 422
    assert c.post(url, json={"role": "guest", "grant": _grant(scope=["home"], can=["view"])}).status_code == 422
    r = c.post(url, json={"role": "guest", "grant": _grant(scope=["home"], expires="2030-01-01")})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "needs_timezone"
    _login(c, home["users"]["mem"][0])
    assert c.post(url, json={"role": "guest", "grant": _grant(scope=["home"])}).status_code == 403


def test_a_guest_sees_and_does_only_the_grant(app_client, home):
    c, hid, dev = app_client, home["hid"], home["dev"]
    kitchen, den = _rooms(c, home)
    _join_as_guest(c, home, _grant(label="Dinner", scope=[f"room:{kitchen}"]))

    assert _outs(c) == {"warm": ["view", "control"], "strip": ["view", "control"]}  # no relay, no sensor, no color
    h = next(x for x in c.get("/api/homes").json() if x["id"] == hid)
    assert h["role"] == "guest" and [r["name"] for r in h["rooms"]] == ["Kitchen"]  # not the Den
    assert h["grant"]["label"] == "Dinner" and h["grant"]["active"] and "scope" not in h["grant"]

    assert c.put(f"/api/devices/{dev}/outputs/warm", json={"on": True, "level": 0.4}).status_code == 200
    r = c.put(f"/api/devices/{dev}/outputs/strip", json={"color": [255, 0, 0]})
    assert r.status_code == 403 and r.json()["detail"]["error"] == "forbidden"
    assert c.put(f"/api/devices/{dev}/outputs/relay", json={"on": True}).status_code == 404
    since = len(c.sent)
    r = c.post(f"/api/homes/{hid}/rooms/{kitchen}/power", json={"on": False})
    assert r.status_code == 200
    assert {t.rsplit("/", 1)[1] for t, _ in c.sent[since:] if "/set/" in t} == {"warm", "strip"}
    assert c.post(f"/api/homes/{hid}/rooms/{den}/power", json={"on": True}).status_code == 404
    for path in (f"/api/homes/{hid}/scenes", f"/api/homes/{hid}/schedules", f"/api/homes/{hid}/devices"):
        assert c.get(path).status_code in (403, 404), path
    assert c.get(f"/api/homes/{hid}/members").status_code == 403

    with c.websocket_connect("/api/ws") as ws:
        snap = contracts.validate("ws-server", ws.receive_json())
        assert {o["key"] for d in snap["devices"] for o in d["outputs"]} == {"warm", "strip"}


def test_owner_changes_a_grant(app_client, home):
    c, hid, dev = app_client, home["hid"], home["dev"]
    kitchen, den = _rooms(c, home)
    gname, gid = _join_as_guest(c, home, _grant(scope=[f"room:{kitchen}"]))

    # The guest's live page is closed (4001) when an owner changes the grant, so it reloads.
    guest_ws = TestClient(c.app, base_url="http://testserver")
    _login(guest_ws, gname)
    with guest_ws.websocket_connect("/api/ws") as ws:
        ws.receive_json()
        _login(c, home["users"]["own"][0])
        grant = _grant(scope=[f"room:{kitchen}"], can=["control", "color"], kinds=["light", "switch"])
        assert c.put(f"/api/homes/{hid}/members/{gid}/grant", json=grant).status_code == 200
        with pytest.raises(WebSocketDisconnect) as e:
            while True:
                ws.receive_json()
        assert e.value.code == 4001
    assert _outs(guest_ws) == {k: ["view", "control", "color"] for k in ("warm", "strip", "relay")}

    # Moving a light out of the granted room takes it out of the guest's reach.
    assert c.put(f"/api/devices/{dev}/outputs/strip/settings", json={"room_id": den}).status_code == 200
    assert set(_outs(guest_ws)) == {"warm", "relay"}
    assert guest_ws.put(f"/api/devices/{dev}/outputs/strip", json={"on": True}).status_code == 404

    # Hours and an end date need the home's time zone; outside them the guest sees nothing.
    now = dt.datetime.now(UTC)
    shut = {"from": (now + dt.timedelta(hours=2)).strftime("%H:%M"), "to": (now + dt.timedelta(hours=3)).strftime("%H:%M")}
    r = c.put(f"/api/homes/{hid}/members/{gid}/grant", json=_grant(scope=["home"], window=shut))
    assert r.status_code == 422 and r.json()["detail"]["error"] == "needs_timezone"
    assert c.put(f"/api/homes/{hid}/place", json={"timezone": "UTC"}).status_code == 200
    r = c.put(f"/api/homes/{hid}/members/{gid}/grant", json=_grant(scope=["home"], window=shut))
    assert r.json()["state"] == {"active": False, "expired": False}
    assert _outs(guest_ws) == {}
    h = next(x for x in guest_ws.get("/api/homes").json() if x["id"] == hid)
    assert h["rooms"] == [] and h["grant"]["active"] is False and h["grant"]["window"] == shut
    assert guest_ws.put(f"/api/devices/{dev}/outputs/warm", json={"on": True}).status_code == 404

    yesterday = (now - dt.timedelta(days=1)).date().isoformat()
    c.put(f"/api/homes/{hid}/members/{gid}/grant", json=_grant(scope=["home"], expires=yesterday))
    g = next(m for m in c.get(f"/api/homes/{hid}/members").json() if m["user_id"] == gid)
    assert g["role"] == "guest" and g["grant"]["expires"] == yesterday and g["grant_state"]["expired"]
    assert _outs(guest_ws) == {}

    # Grants are only for guests; a guest made a member drops theirs and sees everything.
    _, mem_id = home["users"]["mem"]
    r = c.put(f"/api/homes/{hid}/members/{mem_id}/grant", json=_grant(scope=["home"]))
    assert r.status_code == 409 and r.json()["detail"]["error"] == "not_a_guest"
    assert c.put(f"/api/homes/{hid}/members/{gid}", json={"role": "member"}).status_code == 200
    assert "grant" not in next(m for m in c.get(f"/api/homes/{hid}/members").json() if m["user_id"] == gid)
    assert set(_outs(guest_ws)) == {"warm", "strip", "relay", "pir"}
    assert c.put(f"/api/homes/{hid}/members/{gid}", json={"role": "guest"}).status_code == 200
    assert _outs(guest_ws) == {}  # a guest again starts with nothing


def test_guest_invite_never_demotes(app_client, home):
    c, hid = app_client, home["hid"]
    _login(c, home["users"]["own"][0])
    inv = c.post(f"/api/homes/{hid}/invites", json={"role": "guest", "grant": _grant(scope=["home"])}).json()
    _login(c, home["users"]["mem"][0])
    assert c.post(f"/api/invites/{inv['token']}/accept", json={}).json() == {"home_id": hid, "role": "member"}
    assert "pir" in _outs(c)
