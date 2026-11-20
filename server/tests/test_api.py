"""REST: sign-in and CSRF, node builder, provisioning blob, enrollment end to end, config, firmware."""
import base64
import datetime as dt
import hashlib
import io
import json
import zipfile

import pytest
from conftest import CONTRACTS
from cryptography import x509
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from ulserver import contracts
from ulserver.simdev import make_csr

C3_BOARD = json.loads((CONTRACTS / "examples/board/c3-bench.json").read_text())


def enroll_body(dev, token, csr, chip="esp32c3"):
    return {"device_id": dev, "token": token, "csr": csr, "chip": chip, "mac": "02:00:00:00:00:01",
            "fw": "0.1.0", "profile": "c3-lights", "board_crc": "00000000"}


def new_node(admin, board=C3_BOARD, name="Bench"):
    r = admin.post("/api/admin/nodes", json={"name": name, "board": board})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def provision(admin, dev, **kw):
    r = admin.post(f"/api/admin/nodes/{dev}/provision", json=kw)
    assert r.status_code == 200, r.text
    body = r.json()
    payload = contracts.ulboard().decode(base64.b64decode(body["blob_b64"]))
    return body, payload


def test_login_and_csrf(client, admin_creds):
    assert client.get("/api/devices").status_code == 401
    assert client.post("/api/auth/login", json={"username": "admin", "password": "nope"}).status_code == 401
    r = client.post("/api/auth/login", json={"username": admin_creds[0], "password": admin_creds[1]})
    assert r.status_code == 200 and "ul_session" in r.cookies
    # Signed in, but a state-changing request without the CSRF header is refused.
    assert client.post("/api/admin/homes", json={"name": "X"}).status_code == 403
    assert client.post("/api/admin/homes", json={"name": "X"}, headers={"X-UL-CSRF": "wrong"}).status_code == 403
    assert client.post("/api/admin/homes", json={"name": "Home"}, headers={"X-UL-CSRF": r.json()["csrf"]}).status_code == 200
    assert client.get("/api/auth/me").json()["role"] == "admin"


def test_builder_rejects_pin_errors(admin):
    bad = json.loads((CONTRACTS / "examples/board-rules/c3-conflict.json").read_text())
    bad.pop("_expect")
    r = admin.post("/api/admin/nodes", json={"name": "Bad", "board": bad})
    assert r.status_code == 422 and "pin_conflict:0" in r.json()["detail"]["checks"]["errors"]
    r = admin.post("/api/admin/boards/check", json={"board": bad})
    assert r.json()["errors"] == ["pin_conflict:0"]


def test_templates_start_with_only_pcb(admin):
    from ulserver.cli import _seed
    import asyncio
    asyncio.run(_seed())
    names = [t["name"] for t in admin.get("/api/admin/templates").json()]
    assert names == ["PCB rev A"]


def test_provision_blob(admin):
    dev = new_node(admin)
    body, payload = provision(admin, dev, wifi_ssid="Net", wifi_psk="secretpass", dns="192.168.1.99")
    contracts.validate("partition", payload)
    assert payload["board"] == C3_BOARD and body["size"] == 4096
    p = payload["provision"]
    assert p["device_id"] == dev and p["server"] == "lights.test" and len(p["token"]) >= 32
    assert p["wifi"] == {"ssid": "Net", "psk": "secretpass"} and p["dns"] == "192.168.1.99"
    assert body["crc"] == contracts.ulboard().crc_hex(payload)
    # Descriptor-only rewrite: no token.
    _, p2 = provision(admin, dev, token=False)
    assert "token" not in p2["provision"]


def test_enrollment_end_to_end(admin, pki):
    dev = new_node(admin)
    _, payload = provision(admin, dev)
    tok = payload["provision"]["token"]
    key, csr = make_csr(dev)

    def post(body):
        r = admin.post("/api/v1/enroll", json=body)
        contracts.validate("enroll-response", r.json())
        return r

    # Wrong CN, wrong chip, wrong token, malformed body
    _, other_csr = make_csr("ul-ffffff")
    r = post(enroll_body(dev, tok, other_csr))
    assert (r.status_code, r.json()["error"]) == (400, "device_mismatch")
    r = post(enroll_body(dev, tok, csr, chip="esp32c6"))
    assert (r.status_code, r.json()["error"]) == (400, "chip_mismatch")
    r = post(enroll_body(dev, "x" * 43, csr))
    assert (r.status_code, r.json()["error"]) == (401, "token_invalid")
    r = post({"device_id": dev})
    assert (r.status_code, r.json()["error"]) == (400, "bad_request")
    r = post(enroll_body(dev, tok, "-----BEGIN CERTIFICATE REQUEST-----\ngarbage\n-----END CERTIFICATE REQUEST-----\n"))
    assert (r.status_code, r.json()["error"]) == (400, "csr_invalid")

    # Good
    r = post(enroll_body(dev, tok, csr))
    assert r.status_code == 200, r.text
    body = r.json()
    cert = x509.load_pem_x509_certificate(body["cert"].encode())
    assert cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == dev
    assert ExtendedKeyUsageOID.CLIENT_AUTH in cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    ca = x509.load_pem_x509_certificate(body["ca_chain"].encode())
    cert.verify_directly_issued_by(ca)
    assert body["mqtt"] == {"host": "lights.test", "port": 8883}

    # Retry with the same key: same certificate (lost response is safe).
    r2 = post(enroll_body(dev, tok, csr))
    assert r2.status_code == 200 and r2.json()["cert"] == body["cert"]
    # Same token, different key: spent.
    _, csr2 = make_csr(dev)
    r3 = post(enroll_body(dev, tok, csr2))
    assert (r3.status_code, r3.json()["error"]) == (409, "token_used")

    # Re-enroll with a fresh token supersedes the old certificate and puts it in the CRL.
    _, payload2 = provision(admin, dev)
    r4 = post(enroll_body(dev, payload2["provision"]["token"], csr2))
    assert r4.status_code == 200
    crl = x509.load_pem_x509_crl(pki.p("crl.pem").read_bytes())
    assert crl.get_revoked_certificate_by_serial_number(cert.serial_number) is not None
    certs = admin.get("/api/admin/certs").json()
    assert {c["revoke_reason"] for c in certs if c["cn"] == dev} == {None, "superseded"}


def test_expired_and_revoked_tokens(admin):
    from sqlalchemy import create_engine, text

    from ulserver.config import get_settings

    dev = new_node(admin)
    _, payload = provision(admin, dev)
    tok = payload["provision"]["token"]
    eng = create_engine(get_settings().database_url)
    with eng.begin() as c:
        c.execute(text("UPDATE enroll_tokens SET expires_at = now() - interval '1 hour' WHERE token_hash = :h"),
                  {"h": hashlib.sha256(tok.encode()).digest()})
    eng.dispose()
    _, csr = make_csr(dev)
    r = admin.post("/api/v1/enroll", json=enroll_body(dev, tok, csr))
    assert (r.status_code, r.json()["error"]) == (401, "token_expired")
    # A newer token revokes the older unused one.
    _, p1 = provision(admin, dev)
    _, p2 = provision(admin, dev)
    r = admin.post("/api/v1/enroll", json=enroll_body(dev, p1["provision"]["token"], csr))
    assert r.json()["error"] == "token_invalid"
    assert admin.post("/api/v1/enroll", json=enroll_body(dev, p2["provision"]["token"], csr)).status_code == 200


def test_config_validation(admin):
    dev = new_node(admin)
    good = json.loads((CONTRACTS / "examples/config/c3-bench-cct.json").read_text())["outputs"]
    r = admin.put(f"/api/admin/nodes/{dev}/config", json={"outputs": good})
    assert r.status_code == 200 and r.json() == {"rev": 1, "published": False}  # no broker in this test
    r = admin.put(f"/api/admin/nodes/{dev}/config", json={"outputs": [{"key": "x", "kind": "cct", "channels": {"warm": "warm"}}]})
    assert r.status_code == 422
    d = admin.get(f"/api/admin/nodes/{dev}").json()
    assert d["config"]["rev"] == 1 and d["config"]["outputs"] == good


def _package(manifest_patch=None, corrupt=None):
    files = {"bootloader.bin": b"\x00" * 100, "partition-table.bin": b"\x01" * 3072,
             "ota_data_initial.bin": b"\xff" * 8192, "ultralights.bin": b"\xe9" * 5000}
    m = json.loads((CONTRACTS / "examples/firmware-manifest/c3-lights.json").read_text())
    for f in m["files"]:
        f["size"] = len(files[f["name"]])
        f["sha256"] = hashlib.sha256(files[f["name"]]).hexdigest()
    m.update(manifest_patch or {})
    if corrupt:
        files[corrupt] = b"tampered"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", json.dumps(m))
        for n, b in files.items():
            z.writestr(n, b)
    return buf.getvalue()


def test_firmware_upload(admin):
    r = admin.post("/api/admin/firmware", files={"file": ("fw.zip", _package(), "application/zip")})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    f = admin.get(f"/api/admin/firmware/{pid}/files/ultralights.bin")
    assert f.status_code == 200 and f.content == b"\xe9" * 5000
    assert admin.get(f"/api/admin/firmware/{pid}/files/..%2Fsecrets").status_code == 404
    r = admin.post("/api/admin/firmware", files={"file": ("fw.zip", _package(corrupt="ultralights.bin"), "application/zip")})
    assert r.status_code == 422
    r = admin.post("/api/admin/firmware", files={"file": ("fw.zip", _package({"profile": "c6-lights"}), "application/zip")})
    assert r.status_code == 422 and "does not match chip" in r.text


def test_owner_sees_only_own_home(client, admin):
    h1 = admin.post("/api/admin/homes", json={"name": "Mine"}).json()["id"]
    h2 = admin.post("/api/admin/homes", json={"name": "Theirs"}).json()["id"]
    d1 = new_node(admin, name="In mine")
    d2 = new_node(admin, name="In theirs")
    admin.put(f"/api/admin/nodes/{d1}", json={"home_id": h1})
    admin.put(f"/api/admin/nodes/{d2}", json={"home_id": h2})
    r = admin.post("/api/admin/users", json={"username": "owner1", "password": "owner password 1", "role": "owner", "home_id": h1})
    assert r.status_code == 200
    admin.post("/api/auth/logout")
    client.headers.pop("X-UL-CSRF", None)
    r = client.post("/api/auth/login", json={"username": "owner1", "password": "owner password 1"})
    assert r.status_code == 200
    ids = {d["id"] for d in client.get("/api/devices").json()}
    assert d1 in ids and d2 not in ids
    assert client.get("/api/admin/nodes").status_code == 403


def test_token_expiry_is_seven_days(admin):
    dev = new_node(admin)
    body, _ = provision(admin, dev)
    exp = dt.datetime.fromisoformat(body["token_expires"])
    assert dt.timedelta(days=6, hours=23) < exp - dt.datetime.now(dt.timezone.utc) <= dt.timedelta(days=7)
