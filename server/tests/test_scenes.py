"""Scenes: capture, documents, apply with src scene:<id>, undo, and who may do what."""
from test_homes import _login, app_client, home  # noqa: F401 - fixtures

from ulserver import contracts


def _sets(c, since):
    return {t.rsplit("/", 1)[1]: p for t, p in c.sent[since:] if "/set/" in t}


def _setup(c, h):
    _login(c, h["users"]["own"][0])
    room = c.post(f"/api/homes/{h['hid']}/rooms", json={"name": "Kitchen"}).json()["id"]
    for out in ("warm", "strip", "relay"):
        assert c.put(f"/api/devices/{h['dev']}/outputs/{out}/settings", json={"room_id": room}).status_code == 200
    return room


def test_capture_apply_undo(app_client, home):
    c, hid, dev = app_client, home["hid"], home["dev"]
    room = _setup(c, home)
    # The look to save. Tests have no device reports, so desired state is what gets captured.
    assert c.put(f"/api/devices/{dev}/outputs/warm", json={"on": True, "level": 0.3}).status_code == 200
    assert c.put(f"/api/devices/{dev}/outputs/strip", json={"on": True, "level": 0.5, "color": [255, 0, 80]}).status_code == 200
    assert c.put(f"/api/devices/{dev}/outputs/relay", json={"on": True}).status_code == 200

    r = c.post(f"/api/homes/{hid}/scenes/capture", json={"name": "Evening", "room_id": room, "fade_ms": 1500})
    assert r.status_code == 200, r.text
    scene = r.json()
    items = {i["out"]: i["state"] for i in scene["items"]}
    assert items == {"warm": {"on": True, "level": 0.3}, "strip": {"on": True, "level": 0.5, "color": [255, 0, 80]}}  # lights only
    contracts.validate("scene", {"v": 1, "name": scene["name"], "room_id": room, "fade_ms": 1500, "items": scene["items"]})

    # Something else happens, then the scene comes back.
    c.put(f"/api/devices/{dev}/outputs/warm", json={"on": False})
    c.put(f"/api/devices/{dev}/outputs/strip", json={"level": 1.0, "color": [0, 0, 255]})
    n = len(c.sent)
    _login(c, home["users"]["mem"][0])  # anyone who controls the lights may apply it
    r = c.post(f"/api/scenes/{scene['id']}/apply")
    assert r.status_code == 200, r.text
    assert sorted(r.json()["set"]) == [f"{dev}/strip", f"{dev}/warm"] and r.json()["skipped"] == []
    sets = _sets(c, n)
    assert sets["warm"]["on"] is True and sets["warm"]["level"] == 0.3 and sets["warm"]["fade_ms"] == 1500
    assert sets["strip"]["color"] == [255, 0, 80] and all(p["src"] == f"scene:{scene['id']}" for p in sets.values())

    n = len(c.sent)
    r = c.post(f"/api/runs/{r.json()['run_id']}/undo")
    assert r.status_code == 200 and sorted(r.json()["set"]) == [f"{dev}/strip", f"{dev}/warm"]
    sets = _sets(c, n)
    assert sets["warm"]["on"] is False and sets["strip"]["color"] == [0, 0, 255]
    assert all(p["src"].startswith("ui:") for p in sets.values())
    assert c.post("/api/runs/999999/undo").status_code == 404


def test_undo_once(app_client, home):
    c, hid, dev = app_client, home["hid"], home["dev"]
    _setup(c, home)
    c.put(f"/api/devices/{dev}/outputs/warm", json={"on": True})
    s = c.post(f"/api/homes/{hid}/scenes", json={"v": 1, "name": "Off", "items": [
        {"dev": dev, "out": "warm", "state": {"on": False}}]}).json()
    run = c.post(f"/api/scenes/{s['id']}/apply").json()["run_id"]
    assert c.post(f"/api/runs/{run}/undo").status_code == 200
    assert c.post(f"/api/runs/{run}/undo").status_code == 409


def test_documents_are_checked(app_client, home):
    c, hid, dev = app_client, home["hid"], home["dev"]
    room = _setup(c, home)
    ok = {"v": 1, "name": "Fan on", "room_id": room, "items": [{"dev": dev, "out": "relay", "state": {"on": True}}]}
    r = c.post(f"/api/homes/{hid}/scenes", json=ok)
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    bad = [
        {**ok, "items": [{"dev": dev, "out": "pir", "state": {"on": True}}]},                 # a sensor
        {**ok, "items": [{"dev": "ul-ffffff", "out": "warm", "state": {"on": True}}]},        # not in this home
        {**ok, "items": [ok["items"][0], ok["items"][0]]},                                    # twice
        {**ok, "room_id": home["far_room"]},                                                  # someone else's room
        {**ok, "items": [{"dev": dev, "out": "warm", "state": {"on": True, "seq": 3}}]},      # not a scene field
        {**ok, "items": []},
    ]
    for doc in bad:
        r = c.post(f"/api/homes/{hid}/scenes", json=doc)
        assert r.status_code == 422 and r.json()["detail"]["error"] == "scene_invalid", doc
    assert c.put(f"/api/scenes/{sid}", json={**ok, "name": "Fan"}).json()["name"] == "Fan"
    assert [s["name"] for s in c.get(f"/api/homes/{hid}/scenes").json()] == ["Fan"]

    _login(c, home["users"]["mem"][0])  # members make scenes too
    assert c.post(f"/api/homes/{hid}/scenes", json={**ok, "name": "Mine"}).status_code == 200
    _login(c, home["users"]["far"][0])
    assert c.get(f"/api/homes/{hid}/scenes").status_code == 404
    assert c.post(f"/api/scenes/{sid}/apply").status_code == 404
    assert c.delete(f"/api/scenes/{sid}").status_code == 404
    _login(c, home["users"]["own"][0])
    assert c.delete(f"/api/scenes/{sid}").status_code == 200


def test_recapture_keeps_the_outputs(app_client, home):
    c, hid, dev = app_client, home["hid"], home["dev"]
    _setup(c, home)
    c.put(f"/api/devices/{dev}/outputs/warm", json={"on": True, "level": 0.2})
    s = c.post(f"/api/homes/{hid}/scenes", json={"v": 1, "name": "Warm", "items": [
        {"dev": dev, "out": "warm", "state": {"on": True, "level": 0.9}}]}).json()
    r = c.post(f"/api/scenes/{s['id']}/recapture")
    assert r.status_code == 200 and r.json()["items"] == [{"dev": dev, "out": "warm", "state": {"on": True, "level": 0.2}}]
