"""Token issue and device enrollment (contracts/schemas/enroll-*.schema.json)."""
import collections
import datetime as dt
import hashlib
import secrets
import time
from typing import Any

from cryptography import x509
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from . import contracts
from .config import Settings
from .models import AuditLog, Certificate, EnrollToken, Node
from .pki import CsrError, DevicePKI, cert_pem, pubkey_sha256

UTC = dt.timezone.utc
RETRY_WINDOW = dt.timedelta(hours=24)


def token_hash(token: str) -> bytes:
    # Tokens are 256-bit random values, so a plain hash is enough (no password stretching needed).
    return hashlib.sha256(token.encode()).digest()


async def issue_token(db: AsyncSession, node: Node, user_id: int | None, days: int) -> str:
    """New single-use token for node; any unused earlier token for it is revoked."""
    await db.execute(
        update(EnrollToken)
        .where(EnrollToken.node_id == node.id, EnrollToken.used_at.is_(None), EnrollToken.revoked.is_(False))
        .values(revoked=True))
    tok = secrets.token_urlsafe(32)
    db.add(EnrollToken(token_hash=token_hash(tok), node_id=node.id, created_by=user_id,
                       expires_at=dt.datetime.now(UTC) + dt.timedelta(days=days)))
    db.add(AuditLog(src=f"admin:{user_id}" if user_id else "system", action="token_issued", target=node.device_id))
    return tok


class RateLimiter:
    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.hits: dict[str, collections.deque] = collections.defaultdict(collections.deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_minute:
            return False
        q.append(now)
        return True


def _err(status: int, code: str, detail: str = "") -> tuple[int, dict[str, Any]]:
    body: dict[str, Any] = {"error": code}
    if detail:
        body["detail"] = detail[:200]
    return status, body


async def enroll(db: AsyncSession, pki: DevicePKI, s: Settings, req: Any) -> tuple[int, dict[str, Any]]:
    """Returns (http status, body). Body always matches enroll-response.schema.json."""
    try:
        contracts.validate("enroll-request", req)
    except contracts.ContractError as e:
        return _err(400, "bad_request", e.message)
    now = dt.datetime.now(UTC)
    tok = (await db.execute(
        select(EnrollToken).where(EnrollToken.token_hash == token_hash(req["token"])).with_for_update()
    )).scalar_one_or_none()
    if tok is None or tok.revoked:
        return _err(401, "token_invalid")
    node = await db.get(Node, tok.node_id)
    if node is None or node.device_id != req["device_id"]:
        return _err(400, "device_mismatch", "token belongs to another device")
    is_bridge = "bridge" in req
    if is_bridge != (node.kind == "bridge"):
        return _err(400, "chip_mismatch", f"{node.device_id} is a {node.kind}")
    if is_bridge and node.bridge != req["bridge"]:
        return _err(400, "chip_mismatch", f"{node.device_id} is a {node.bridge} bridge")
    if not is_bridge and node.chip != req["chip"]:
        return _err(400, "chip_mismatch", f"node is {node.chip}")
    try:
        csr = pki.check_csr(req["csr"], req["device_id"])
    except CsrError as e:
        return _err(400, e.code, e.detail)
    pub = pubkey_sha256(csr.public_key())

    if tok.used_at is not None:
        if tok.used_pubkey_sha256 == pub and now - tok.used_at < RETRY_WINDOW and tok.cert_id:
            cert = await db.get(Certificate, tok.cert_id)
            if cert is not None and cert.revoked_at is None:
                return 200, _ok(s, pki, node.device_id, cert.pem)
        return _err(409, "token_used", f"token was spent on {tok.used_at.date().isoformat()}")
    if tok.expires_at < now:
        return _err(401, "token_expired")

    c = pki.sign_device_csr(req["csr"], req["device_id"], years=s.device_cert_years)
    pem = cert_pem(c)
    row = Certificate(serial=format(c.serial_number, "x"), cn=node.device_id, node_id=node.id, pem=pem,
                      pubkey_sha256=pub, not_after=c.not_valid_after_utc)
    db.add(row)
    await db.flush()
    # A node has one identity at a time: re-enrolling supersedes earlier certificates.
    old = (await db.execute(select(Certificate).where(
        Certificate.node_id == node.id, Certificate.id != row.id, Certificate.revoked_at.is_(None)))).scalars().all()
    for o in old:
        o.revoked_at = now
        o.revoke_reason = "superseded"
    tok.used_at = now
    tok.used_pubkey_sha256 = pub
    tok.cert_id = row.id
    node.enrolled_at = now
    detail = {"serial": row.serial, "fw": req["fw"]}
    detail.update({k: req[k] for k in ("mac", "board_crc", "bridge", "host") if k in req})
    db.add(AuditLog(src=f"device:{node.device_id}", action="enrolled", target=node.device_id, detail=detail))
    await db.commit()
    if old:
        await write_crl(db, pki)
    return 200, _ok(s, pki, node.device_id, pem)


def _ok(s: Settings, pki: DevicePKI, device_id: str, pem: str) -> dict[str, Any]:
    return {"device_id": device_id, "cert": pem, "ca_chain": pki.chain_pem(),
            "mqtt": {"host": s.public_host, "port": s.mqtt_port}}


async def write_crl(db: AsyncSession, pki: DevicePKI) -> None:
    rows = (await db.execute(select(Certificate).where(Certificate.revoked_at.is_not(None)))).scalars().all()
    pki.write_crl([(int(r.serial, 16), r.revoked_at) for r in rows if r.revoked_at])


def cert_summary(pem: str) -> dict[str, Any]:
    c = x509.load_pem_x509_certificate(pem.encode())
    return {"not_before": c.not_valid_before_utc.isoformat(), "not_after": c.not_valid_after_utc.isoformat()}
