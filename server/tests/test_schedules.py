"""Schedules: the home's place, document checks, the internal fire endpoint (once only),
and the scheduler's choice of what is due."""
import datetime as dt

from test_homes import _login, app_client, home  # noqa: F401 - fixtures

from ulserver.models import Home, Schedule
from ulserver.scheduler import GRACE, due

UTC = dt.timezone.utc


def _owner_with_place(c, h, tz="Europe/London", loc=True):
    _login(c, h["users"]["own"][0])
    body = {"timezone": tz, **({"latitude": 51.5, "longitude": -0.12} if loc else {})}
    assert c.put(f"/api/homes/{h['hid']}/place", json=body).status_code == 200


def _fire(c, sid, phase, at, token=True):
    hdr = {"X-UL-Internal": c.app.state.internal_token} if token else {}
    return c.post(f"/api/internal/schedules/{sid}/fire", json={"phase": phase, "at": at.isoformat()}, headers=hdr)


def test_place(app_client, home):
    c, hid = app_client, home["hid"]
    _login(c, home["users"]["own"][0])
    assert c.put(f"/api/homes/{hid}/place", json={"timezone": "Mars/Olympus"}).status_code == 422
    assert c.put(f"/api/homes/{hid}/place", json={"timezone": "UTC", "latitude": 10}).status_code == 422
    assert c.put(f"/api/homes/{hid}/place", json={"timezone": "America/Vancouver", "latitude": 49.28, "longitude": -123.12}).status_code == 200
    assert c.get(f"/api/homes/{hid}/place").json()["timezone"] == "America/Vancouver"
    _login(c, home["users"]["mem"][0])
    assert c.put(f"/api/homes/{hid}/place", json={"timezone": "UTC"}).status_code == 403


def test_documents_are_checked(app_client, home):
    c, hid, dev = app_client, home["hid"], home["dev"]
    relay = {"v": 1, "name": "Fan", "at": {"time": "07:00"}, "do": {"dev": dev, "out": "relay", "on": True}}
    _login(c, home["users"]["own"][0])
    r = c.post(f"/api/homes/{hid}/schedules", json=relay)
    assert r.status_code == 422 and "time zone" in r.json()["detail"]["errors"][0]
    _owner_with_place(c, home, loc=False)
    r = c.post(f"/api/homes/{hid}/schedules", json=relay)
    assert r.status_code == 200, r.text
    assert r.json()["next"]["phase"] == "start"
    bad = [
        {**relay, "at": {"sun": "sunset"}},                                   # no location
        {**relay, "do": {"dev": dev, "out": "pir", "on": True}},              # a sensor
        {**relay, "do": {"dev": dev, "out": "relay", "run_ms": 600000}},      # not a valve
        {**relay, "do": {"dev": "ul-ffffff", "out": "relay", "on": True}},    # not this home
        {**relay, "do": {"room": home["far_room"], "on": True}},              # someone else's room
        {**relay, "do": {"scene": 999999}},
        {**relay, "at": {"time": "7:00"}},
    ]
    for doc in bad:
        r = c.post(f"/api/homes/{hid}/schedules", json=doc)
        assert r.status_code == 422 and r.json()["detail"]["error"] == "schedule_invalid", doc
    _login(c, home["users"]["mem"][0])  # members make schedules
    assert c.post(f"/api/homes/{hid}/schedules", json={**relay, "name": "Mine"}).status_code == 200
    _login(c, home["users"]["far"][0])
    assert c.get(f"/api/homes/{hid}/schedules").status_code == 404


def test_fire_once_and_window(app_client, home):
    c, hid, dev = app_client, home["hid"], home["dev"]
    _owner_with_place(c, home)
    doc = {"v": 1, "name": "Evening fan", "at": {"time": "19:00"}, "until": {"time": "23:00"},
           "do": {"dev": dev, "out": "relay", "on": True}}
    sid = c.post(f"/api/homes/{hid}/schedules", json=doc).json()["id"]
    later = dt.datetime.now(UTC) + dt.timedelta(minutes=1)

    assert _fire(c, sid, "start", later, token=False).status_code == 404  # needs the token
    assert _fire(c, sid, "start", later).status_code == 404               # and a loopback caller
    c.app.state.internal_hosts.add("testclient")
    r = c.post(f"/api/internal/schedules/{sid}/fire", json={"phase": "start", "at": later.isoformat()},
               headers={"X-UL-Internal": c.app.state.internal_token, "X-Forwarded-For": "1.2.3.4"})
    assert r.status_code == 404                                            # never through the proxy

    n = len(c.sent)
    r = _fire(c, sid, "start", later)
    assert r.status_code == 200 and r.json()["fired"] is True
    (topic, p), = [(t, p) for t, p in c.sent[n:] if "/set/" in t]
    assert topic.endswith("/set/relay") and p["on"] is True and p["src"] == f"sched:{sid}"
    assert _fire(c, sid, "start", later).json() == {"fired": False, "reason": "already_fired"}
    r = _fire(c, sid, "until", later + dt.timedelta(hours=4))
    assert r.json()["fired"] is True and c.sent[-1][1]["on"] is False
    view = next(s for s in c.get(f"/api/homes/{hid}/schedules").json() if s["id"] == sid)
    assert view["last_result"]["phase"] == "until"

    # Disabled schedules don't fire; run-now still works for trying one out.
    assert c.put(f"/api/schedules/{sid}", json={**doc, "enabled": False}).status_code == 200
    assert _fire(c, sid, "start", later + dt.timedelta(days=1)).json()["fired"] is False
    assert c.post(f"/api/schedules/{sid}/run").json()["set"] == [f"{dev}/relay"]


def test_scene_and_room_actions(app_client, home):
    c, hid, dev = app_client, home["hid"], home["dev"]
    _owner_with_place(c, home)
    c.app.state.internal_hosts.add("testclient")
    room = c.post(f"/api/homes/{hid}/rooms", json={"name": "Den"}).json()["id"]
    for out in ("warm", "strip", "relay"):
        c.put(f"/api/devices/{dev}/outputs/{out}/settings", json={"room_id": room})
    scene = c.post(f"/api/homes/{hid}/scenes", json={"v": 1, "name": "Glow", "items": [
        {"dev": dev, "out": "strip", "state": {"on": True, "color": [255, 0, 0]}}]}).json()["id"]
    at = dt.datetime.now(UTC) + dt.timedelta(minutes=1)
    s1 = c.post(f"/api/homes/{hid}/schedules", json={"v": 1, "name": "Glow", "at": {"time": "18:00"}, "until": {"time": "22:00"},
                                                    "do": {"scene": scene}}).json()["id"]
    n = len(c.sent)
    assert _fire(c, s1, "start", at).json()["result"]["set"] == [f"{dev}/strip"]
    # A scene's sets carry scene:<id>; that the schedule triggered it is on the run and in the audit log.
    assert c.sent[-1][1]["src"] == f"scene:{scene}" and c.sent[-1][1]["color"] == [255, 0, 0]
    _fire(c, s1, "until", at + dt.timedelta(hours=4))
    assert c.sent[-1][1]["on"] is False and len(c.sent) == n + 2
    s2 = c.post(f"/api/homes/{hid}/schedules", json={"v": 1, "name": "Den off", "at": {"time": "23:30"},
                                                    "do": {"room": room, "on": False}}).json()["id"]
    r = _fire(c, s2, "start", at).json()["result"]
    assert sorted(r["set"]) == [f"{dev}/strip", f"{dev}/warm"]  # lights only, as the room toggle


def test_due_picks_unfired_events_within_grace():
    h = Home(id=1, name="H", timezone="UTC")
    s = Schedule(id=5, home_id=1, enabled=True, doc={"v": 1, "name": "x", "at": {"time": "07:00"}, "do": {"scene": 1}})
    now = dt.datetime(2024, 5, 1, 7, 2, tzinfo=UTC)
    s.last_start = dt.datetime(2024, 4, 30, 7, 0, tzinfo=UTC)
    assert due([s], {1: h}, now) == [(5, "start", dt.datetime(2024, 5, 1, 7, 0, tzinfo=UTC))]
    s.last_start = dt.datetime(2024, 5, 1, 7, 0, tzinfo=UTC)
    assert due([s], {1: h}, now) == []                                    # already fired
    s.last_start = None
    assert due([s], {1: h}, now + GRACE) == []                            # missed: skipped, not late
    assert due([s], {1: Home(id=1, name="H")}, now) == []                 # no time zone


async def test_tick_fires_through_the_api(app_client, home, tmp_path, monkeypatch):
    import ulserver.scheduler as sched
    from test_homes import _sql

    c, hid, dev = app_client, home["hid"], home["dev"]
    _owner_with_place(c, home, tz="UTC", loc=False)
    c.app.state.internal_hosts.add("testclient")
    at = (dt.datetime.now(UTC) - dt.timedelta(minutes=1)).replace(second=0, microsecond=0)
    sid = c.post(f"/api/homes/{hid}/schedules", json={"v": 1, "name": "Tick", "at": {"time": at.strftime("%H:%M")},
                                                     "do": {"dev": dev, "out": "relay", "on": True}}).json()["id"]
    _sql("UPDATE schedules SET last_start = now() - interval '1 day' WHERE id = :i", i=sid)  # as if made yesterday
    token = tmp_path / "internal.token"
    token.write_text(c.app.state.internal_token)
    calls = []

    def post(url, tok, body):
        calls.append(url)
        r = c.post(url.removeprefix("http://api"), json=body, headers={"X-UL-Internal": tok})
        return r.json()

    monkeypatch.setattr(sched, "_post", post)
    from ulserver import db as dbm
    await sched.tick(dbm.sessionmaker(), "http://api", token)
    assert calls == [f"http://api/api/internal/schedules/{sid}/fire"]
    assert c.sent[-1][0].endswith("/set/relay") and c.sent[-1][1]["src"] == f"sched:{sid}"
    calls.clear()
    await sched.tick(dbm.sessionmaker(), "http://api", token)
    assert calls == []  # fired once
