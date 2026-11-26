"""OTA: targets, push, the device download, and following cmd_result + info to done / failed."""
import hashlib
import json
import secrets

import pytest
from conftest import CONTRACTS
from test_api import _package
from test_homes import C3_BOARD, _captured_app, _sql

from ulserver import contracts

INFO = json.loads((CONTRACTS / "examples/info/c3-bench-default.json").read_text())
APP = b"\xe9" * 5000  # the app image in test_api._package
SHA = hashlib.sha256(APP).hexdigest()


@pytest.fixture()
def c(pki, admin_creds):
    with _captured_app() as c:
        r = c.post("/api/auth/login", json={"username": admin_creds[0], "password": admin_creds[1]})
        c.headers["X-UL-CSRF"] = r.json()["csrf"]
        yield c


def _feed(c, dev, kind, doc):
    c.portal.call(c.app.state.bridge.handle, f"ul/v1/d/{dev}/{kind}", json.dumps(doc).encode())


def _node(c, fw="0.1.0", profile="c3-lights", online=True):
    dev = c.post("/api/admin/nodes", json={"name": f"N {secrets.token_hex(2)}", "board": C3_BOARD}).json()["id"]
    _feed(c, dev, "info", {**INFO, "id": dev, "fw": fw, "profile": profile})
    _feed(c, dev, "status", {"online": online})
    return dev


def _pkg(c):
    ver = f"7.{secrets.randbelow(10**6)}.0"
    r = c.post("/api/admin/firmware", files={"file": ("p.zip", _package({"version": ver}), "application/zip")})
    assert r.status_code == 200, r.text
    return r.json()["id"], ver


def _push(c, pid, *devs, force=False):
    r = c.post("/api/admin/ota", json={"package_id": pid, "devices": list(devs), "force": force})
    assert r.status_code == 200, r.text
    return r.json()["results"]


def _update(c, dev):
    return c.get("/api/admin/ota", params={"dev": dev}).json()["updates"][0]


def _last_cmd(c, dev):
    t, cmd = next((t, p) for t, p in reversed(c.sent) if t == f"ul/v1/d/{dev}/cmd")
    contracts.validate("cmd", cmd)
    return cmd


def test_push_and_follow_to_done(c):
    dev = _node(c)
    pid, ver = _pkg(c)
    t = {x["dev"]: x for x in c.get(f"/api/admin/firmware/{pid}/targets").json()}
    assert t[dev]["eligible"] and t[dev]["fw"] == "0.1.0"
    [r] = _push(c, pid, dev)
    assert r["ok"] and r["update"]["status"] == "sent" and r["update"]["from_version"] == "0.1.0"
    cmd = _last_cmd(c, dev)
    assert cmd["op"] == "ota" and cmd["version"] == ver and cmd["sha256"] == SHA
    assert cmd["url"] == f"https://lights.test/api/v1/fw/{SHA}.bin"
    # one update per node at a time
    assert _push(c, pid, dev)[0]["error"] == "busy"
    # the device's download: no session, no client certificate
    from fastapi.testclient import TestClient

    f = TestClient(c.app, base_url="http://testserver").get(f"/api/v1/fw/{SHA}.bin")
    assert f.status_code == 200 and f.content == APP
    assert c.get(f"/api/v1/fw/{'0' * 64}.bin").status_code == 404
    assert c.get("/api/v1/fw/nothex.bin").status_code == 422
    # progress, written, back on the new version
    _feed(c, dev, "evt", {"type": "cmd_result", "cmd_id": cmd["id"], "ok": True, "progress": 40})
    assert _update(c, dev)["status"] == "downloading" and _update(c, dev)["progress"] == 40
    _feed(c, dev, "evt", {"type": "cmd_result", "cmd_id": cmd["id"], "ok": True})
    assert _update(c, dev)["status"] == "restarting"
    _feed(c, dev, "info", {**INFO, "id": dev, "fw": ver, "profile": "c3-lights"})
    u = _update(c, dev)
    assert u["status"] == "done" and u["finished_at"]
    # now it runs ver: same version needs force
    assert _push(c, pid, dev)[0]["error"] == "same_version"
    assert _push(c, pid, dev, force=True)[0]["ok"]


def test_rollback_device_error_and_checks(c):
    dev = _node(c)
    pid, ver = _pkg(c)
    _push(c, pid, dev)
    cmd = _last_cmd(c, dev)
    # an info while downloading (e.g. a config change) on the old version is not a failure
    _feed(c, dev, "info", {**INFO, "id": dev, "fw": "0.1.0", "profile": "c3-lights"})
    assert _update(c, dev)["status"] == "sent"
    _feed(c, dev, "evt", {"type": "cmd_result", "cmd_id": cmd["id"], "ok": True})
    _feed(c, dev, "info", {**INFO, "id": dev, "fw": "0.1.0", "profile": "c3-lights"})
    u = _update(c, dev)
    assert u["status"] == "failed" and u["error"] == "rolled back: came back on 0.1.0"

    _push(c, pid, dev)
    cmd = _last_cmd(c, dev)
    _feed(c, dev, "evt", {"type": "cmd_result", "cmd_id": cmd["id"], "ok": False, "error": "sha256_mismatch"})
    assert _update(c, dev)["error"] == "sha256_mismatch"
    # results for someone else's command id change nothing
    _push(c, pid, dev)
    cmd = _last_cmd(c, dev)
    other = _node(c)
    _feed(c, other, "evt", {"type": "cmd_result", "cmd_id": cmd["id"], "ok": False, "error": "x"})
    assert _update(c, dev)["status"] == "sent"

    off = _node(c, online=False)
    wrong = _node(c, profile="c3-other")
    res = {r["dev"]: r for r in _push(c, pid, off, wrong, "ul-ffffff")}
    assert res[off]["error"] == "offline" and res[wrong]["error"] == "wrong_profile"
    assert res["ul-ffffff"]["error"] == "no_such_node"
    targets = {x["dev"] for x in c.get(f"/api/admin/firmware/{pid}/targets").json()}
    assert off in targets and wrong not in targets


def test_quiet_update_times_out(c):
    dev = _node(c)
    pid, _ = _pkg(c)
    _push(c, pid, dev)
    _sql("UPDATE ota_updates SET updated_at = now() - interval '10 minutes' WHERE status = 'sent'")
    u = _update(c, dev)
    assert u["status"] == "failed" and u["error"] == "no answer from the node"


def test_not_connected_fails_at_once(pki, admin_creds):
    from fastapi.testclient import TestClient

    from ulserver.main import create_app

    with TestClient(create_app(), base_url="http://testserver") as c:  # real bridge.publish: broker not connected
        r = c.post("/api/auth/login", json={"username": admin_creds[0], "password": admin_creds[1]})
        c.headers["X-UL-CSRF"] = r.json()["csrf"]
        dev = _node(c)
        pid, _ = _pkg(c)
        [r] = _push(c, pid, dev)
        assert not r["ok"] and r["error"] == "not_sent" and r["update"]["status"] == "failed"
