"""Build queue and worker API: tokens, claim, reports, cancel, package upload, version guard, stale sweep."""
import datetime as dt
import secrets

from test_api import _package

from ulserver import contracts


def _worker(admin, name=None):
    r = admin.post("/api/admin/workers", json={"name": name or f"ws-{secrets.token_hex(3)}"})
    assert r.status_code == 200, r.text
    return r.json()


def _h(w):
    return {"Authorization": f"Bearer {w['token']}"}


def _drain(admin, client, w):
    """Cancel anything left queued by other tests so claims here are deterministic."""
    for j in admin.get("/api/admin/builds").json()["jobs"]:
        if j["status"] in ("queued", "running"):
            admin.post(f"/api/admin/builds/{j['id']}/cancel")


def _report(client, w, jid, **kw):
    r = client.post(f"/api/worker/jobs/{jid}/report", json={"v": 1, "type": "report", **kw}, headers=_h(w))
    assert r.status_code == 200, r.text
    assert contracts.is_valid("worker", r.json())
    return r.json()


def _upload(client, w, jid, pkg):
    return client.post(f"/api/worker/jobs/{jid}/package", files={"file": ("p.zip", pkg, "application/zip")}, headers=_h(w))


def test_worker_auth(admin, client):
    assert client.post("/api/worker/claim").status_code == 401
    assert client.post("/api/worker/claim", headers={"Authorization": "Bearer ulw_nope"}).status_code == 401
    w = _worker(admin)
    assert w["token"].startswith("ulw_")
    me = client.get("/api/worker/me", headers=_h(w))
    assert me.status_code == 200 and me.json()["name"] == w["name"]
    assert admin.post("/api/admin/workers", json={"name": w["name"]}).status_code == 409
    # rotating the token locks the old one out
    new = admin.post(f"/api/admin/workers/{w['id']}/token").json()
    assert client.get("/api/worker/me", headers=_h(w)).status_code == 401
    assert client.get("/api/worker/me", headers=_h(new)).status_code == 200
    ws = admin.get("/api/admin/builds").json()["workers"]
    assert next(x for x in ws if x["id"] == w["id"])["online"] is True
    assert admin.delete(f"/api/admin/workers/{w['id']}").json() == {"ok": True}
    assert client.get("/api/worker/me", headers=_h(new)).status_code == 401


def test_queue_validation(admin):
    assert "c3-lights" in admin.get("/api/admin/build-profiles").json()
    assert admin.post("/api/admin/builds", json={"profile": "c3-nope"}).status_code == 422
    for ref in ("--upload-pack=x", "a/../b", "", "x" * 101):
        assert admin.post("/api/admin/builds", json={"profile": "c3-lights", "ref": ref}).status_code == 422, ref


def test_build_happy_path(admin, client):
    w = _worker(admin)
    _drain(admin, client, w)
    assert client.post("/api/worker/claim", headers=_h(w)).status_code == 204
    j = admin.post("/api/admin/builds", json={"profile": "c3-lights", "ref": "beta", "replace": True}).json()
    assert j["status"] == "queued"
    r = client.post("/api/worker/claim", headers=_h(w))
    assert r.status_code == 200
    doc = r.json()
    assert contracts.is_valid("worker", doc) and doc == {"v": 1, "type": "job", "id": j["id"], "kind": "build",
                                                         "profile": "c3-lights", "ref": "beta"}
    assert client.post("/api/worker/claim", headers=_h(w)).status_code == 204  # nothing else queued
    assert _report(client, w, j["id"], log="step 1\n") == {"v": 1, "type": "ack", "cancel": False}
    # another worker can't touch it
    other = _worker(admin)
    assert client.post(f"/api/worker/jobs/{j['id']}/report", json={"v": 1, "type": "report"},
                       headers=_h(other)).status_code == 404
    r = _upload(client, w, j["id"], _package())
    assert r.status_code == 200, r.text
    d = admin.get(f"/api/admin/builds/{j['id']}").json()
    assert d["status"] == "succeeded" and d["package_id"] == r.json()["package_id"]
    assert "step 1" in d["log"] and "stored" in d["log"]
    assert d["worker"] == w["name"] and d["version"] and d["commit"]
    # done: reports get cancel, uploads get 409
    assert _report(client, w, j["id"])["cancel"] is True
    assert _upload(client, w, j["id"], _package()).status_code == 409


def test_package_checks_and_version_guard(admin, client):
    w = _worker(admin)
    _drain(admin, client, w)
    # profile mismatch
    j = admin.post("/api/admin/builds", json={"profile": "c6-lights"}).json()
    client.post("/api/worker/claim", headers=_h(w))
    assert _upload(client, w, j["id"], _package()).status_code == 422
    assert admin.get(f"/api/admin/builds/{j['id']}").json()["status"] == "failed"
    # dirty tree
    j = admin.post("/api/admin/builds", json={"profile": "c3-lights", "replace": True}).json()
    client.post("/api/worker/claim", headers=_h(w))
    assert _upload(client, w, j["id"], _package({"commit": "abcdef1-dirty"})).status_code == 422
    # first build of a version is stored; a different commit at the same version needs replace
    ver = f"9.{secrets.randbelow(1000)}.{secrets.randbelow(1000)}"
    for commit, replace, code in (("aaaaaaa", False, 200), ("aaaaaaa", False, 200), ("bbbbbbb", False, 409),
                                  ("bbbbbbb", True, 200)):
        j = admin.post("/api/admin/builds", json={"profile": "c3-lights", "replace": replace}).json()
        client.post("/api/worker/claim", headers=_h(w))
        r = _upload(client, w, j["id"], _package({"version": ver, "commit": commit}))
        assert r.status_code == code, (commit, replace, r.text)
    assert "bump firmware/version.txt" in admin.get(f"/api/admin/builds/{j['id'] - 1}").json()["error"]


def test_cancel_and_failure(admin, client):
    w = _worker(admin)
    _drain(admin, client, w)
    q = admin.post("/api/admin/builds", json={"profile": "c3-lights"}).json()
    assert admin.post(f"/api/admin/builds/{q['id']}/cancel").json()["status"] == "cancelled"
    assert client.post("/api/worker/claim", headers=_h(w)).status_code == 204
    j = admin.post("/api/admin/builds", json={"profile": "c3-lights"}).json()
    client.post("/api/worker/claim", headers=_h(w))
    assert admin.post(f"/api/admin/builds/{j['id']}/cancel").json()["cancel_requested"] is True
    assert _report(client, w, j["id"], log="more\n")["cancel"] is True
    assert admin.get(f"/api/admin/builds/{j['id']}").json()["status"] == "cancelled"
    j = admin.post("/api/admin/builds", json={"profile": "c3-lights"}).json()
    client.post("/api/worker/claim", headers=_h(w))
    assert _report(client, w, j["id"], log="boom\n", failed="idf.py build exited with 2")["cancel"] is True
    d = admin.get(f"/api/admin/builds/{j['id']}").json()
    assert d["status"] == "failed" and d["error"] == "idf.py build exited with 2"
    bad = client.post(f"/api/worker/jobs/{j['id']}/report", json={"v": 1, "type": "ack", "cancel": True}, headers=_h(w))
    assert bad.status_code == 422


async def test_stale_job_fails(admin, client):
    from sqlalchemy import update

    from ulserver import db as dbm
    from ulserver.models import BuildJob

    w = _worker(admin)
    _drain(admin, client, w)
    j = admin.post("/api/admin/builds", json={"profile": "c3-lights"}).json()
    client.post("/api/worker/claim", headers=_h(w))
    async with dbm.sessionmaker()() as db:
        old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=10)
        await db.execute(update(BuildJob).where(BuildJob.id == j["id"]).values(heartbeat_at=old))
        await db.commit()
    d = admin.get(f"/api/admin/builds/{j['id']}").json()
    assert d["status"] == "failed" and d["error"] == "worker stopped responding"


def test_trim_log():
    from ulserver import builds
    from ulserver.models import BuildJob

    j = BuildJob(log="")
    for _ in range(30):
        builds.append_log(j, ("x" * 99 + "\n") * 500)
    assert len(j.log) <= builds.LOG_LIMIT + 50 and j.log.startswith("[earlier output trimmed]\n")
