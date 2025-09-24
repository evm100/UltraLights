import hashlib
import hmac
import json
import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

from fastapi import APIRouter, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from sqlalchemy.exc import OperationalError
from sqlmodel import Session, SQLModel

from . import database, node_credentials, registry
from .auth.service import init_auth_storage
from .config import settings

router = APIRouter()

# If you have a central config module, prefer importing from it:
# from .config import FIRMWARE_DIR, PUBLIC_BASE, API_BEARER, MANIFEST_HMAC_SECRET, LAN_PUBLIC_BASE

FIRMWARE_DIR = settings.FIRMWARE_DIR
LAN_PUBLIC_BASE = os.getenv("LAN_PUBLIC_BASE", "")  # e.g. https://lan.lights.evm100.org

def latest_symlink_for(dev_id: str) -> Path:
    return settings.FIRMWARE_DIR / f"{dev_id}_latest.bin"

def _resolve_latest(device_id: str) -> Path:
    # 1) flat symlink:  /srv/firmware/UltraNode2_latest.bin
    flat = FIRMWARE_DIR / f"{device_id}_latest.bin"
    # 2) nested file:   /srv/firmware/UltraNode2/latest.bin
    nested = FIRMWARE_DIR / device_id / "latest.bin"

    for p in (flat, nested):
        if p.exists():
            target = p.resolve() if p.is_symlink() else p
            if target.exists():
                return target
    raise HTTPException(status_code=404, detail=f"No firmware for device_id={device_id}")

def _authenticate_request(
    auth_header: Optional[str], session: Session
) -> Tuple[Optional[node_credentials.NodeCredential], str]:
    """Return the node associated with ``auth_header`` if applicable."""

    require_auth = bool(settings.API_BEARER or node_credentials.any_tokens(session))

    if not auth_header:
        if require_auth:
            raise HTTPException(status_code=401, detail="Missing bearer token")
        return None, "open"

    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")

    token = auth_header.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")

    if settings.API_BEARER and hmac.compare_digest(token, settings.API_BEARER):
        return None, "global"

    try:
        token_hash = registry.hash_node_token(token)
    except ValueError:
        raise HTTPException(status_code=403, detail="Invalid bearer token") from None

    try:
        credential = node_credentials.get_by_token_hash(session, token_hash)
    except OperationalError:
        session.rollback()
        init_auth_storage()
        SQLModel.metadata.create_all(session.get_bind())
        credential = node_credentials.get_by_token_hash(session, token_hash)
    if credential:
        return credential, "node"

    raise HTTPException(status_code=403, detail="Invalid bearer token")


def _resolve_access_context(
    *,
    authorization: Optional[str],
    device_id: Optional[str],
    download_id: Optional[str],
) -> Tuple[str, str, Optional[node_credentials.NodeCredential]]:
    """Determine which node and filesystem id a request should access."""

    with database.SessionLocal() as session:
        credential, _ = _authenticate_request(authorization, session)

        resolved_credential: Optional[node_credentials.NodeCredential] = None
        resolved_device_id: Optional[str] = None
        resolved_download_id: Optional[str] = download_id

        if credential:
            resolved_credential = credential
            node_id = credential.node_id
            if not node_id:
                raise HTTPException(status_code=403, detail="Token missing node identifier")
            node_download_id = credential.download_id
            if device_id and device_id != node_id:
                raise HTTPException(status_code=403, detail="Token not authorized for this device")
            if download_id and download_id not in {node_download_id, node_id}:
                raise HTTPException(status_code=403, detail="Token not authorized for this download id")
            resolved_device_id = node_id
            resolved_download_id = download_id or node_download_id or node_id
        else:
            matched_credential: Optional[node_credentials.NodeCredential] = None
            if download_id:
                matched_credential = node_credentials.get_by_download_id(session, download_id)
                if matched_credential:
                    resolved_credential = matched_credential
                    resolved_device_id = matched_credential.node_id
            if device_id and not resolved_device_id:
                matched_credential = node_credentials.get_by_node_id(session, device_id)
                if matched_credential:
                    resolved_credential = matched_credential
                    resolved_device_id = matched_credential.node_id
            if not resolved_device_id and download_id:
                resolved_device_id = download_id
                if resolved_credential is None:
                    _, _, legacy_node = registry.find_node(download_id)
                    if legacy_node:
                        resolved_device_id = str(legacy_node.get("id") or download_id)
            if not resolved_device_id:
                if device_id:
                    resolved_device_id = device_id
                else:
                    raise HTTPException(status_code=400, detail="device_id required")
            if not resolved_download_id:
                if resolved_credential:
                    resolved_download_id = resolved_credential.download_id
                else:
                    legacy_dl = None
                    _, _, legacy_node = registry.find_node(resolved_device_id)
                    if legacy_node:
                        legacy_value = legacy_node.get(registry.NODE_DOWNLOAD_ID_KEY)
                        if isinstance(legacy_value, str) and legacy_value:
                            legacy_dl = legacy_value
                    resolved_download_id = legacy_dl or resolved_device_id

    return resolved_device_id, resolved_download_id, resolved_credential

def _http_date(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")

def _sha256_hex(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024*1024), b""):
            h.update(chunk)
    return h.hexdigest()

def _manifest_sig(body: dict) -> Optional[str]:
    secret = settings.MANIFEST_HMAC_SECRET
    if not secret:
        return None

    payload = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if (
        all(c in "0123456789abcdef" for c in secret.lower())
        and len(secret) % 2 == 0
    ):
        try:
            key = bytes.fromhex(secret)
        except ValueError:
            key = secret.encode("utf-8")
    else:
        key = secret.encode("utf-8")

    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def _build_manifest_response(device_id: str, download_id: Optional[str]) -> JSONResponse:
    target = _resolve_latest(device_id)
    size = target.stat().st_size
    sha = _sha256_hex(target)
    name = target.name
    version = "unknown"
    if "_v" in name and name.endswith(".bin"):
        version = name.split("_v", 1)[-1].rsplit(".bin", 1)[0]

    exposed_id = download_id or device_id
    body = {
        "device_id": device_id,
        "version": version,
        "size": size,
        "sha256_hex": sha,
        "binary_url": f"{settings.PUBLIC_BASE}/firmware/{exposed_id}/latest.bin",
    }

    if download_id:
        body["download_id"] = download_id
        body["manifest_url"] = f"{settings.PUBLIC_BASE}/firmware/{download_id}/manifest"

    if LAN_PUBLIC_BASE:
        body["binary_url_lan"] = f"{LAN_PUBLIC_BASE}/firmware/{exposed_id}/latest.bin"
        if download_id:
            body["manifest_url_lan"] = f"{LAN_PUBLIC_BASE}/firmware/{download_id}/manifest"

    sig = _manifest_sig(body)
    headers = {"Cache-Control": "no-store"}
    if sig:
        headers["X-Manifest-Signature"] = sig
        body["sig"] = sig

    return JSONResponse(body, headers=headers)


@router.get("/api/firmware/v1/manifest")
def api_manifest(
    device_id: Optional[str] = None,
    download_id: Optional[str] = None,
    authorization: Optional[str] = Header(None),
):
    resolved_device_id, resolved_download_id, _ = _resolve_access_context(
        authorization=authorization,
        device_id=device_id,
        download_id=download_id,
    )
    return _build_manifest_response(resolved_device_id, resolved_download_id)


@router.get("/firmware/{download_id}/manifest")
def api_manifest_by_download(
    download_id: str,
    authorization: Optional[str] = Header(None),
):
    resolved_device_id, resolved_download_id, _ = _resolve_access_context(
        authorization=authorization,
        device_id=None,
        download_id=download_id,
    )
    return _build_manifest_response(resolved_device_id, resolved_download_id)

def _serve_file(path: Path, request: Request) -> Response:
    stat = path.stat()
    etag = f"\"{stat.st_size:x}-{int(stat.st_mtime):x}\""
    ims = request.headers.get("If-Modified-Since")
    inm = request.headers.get("If-None-Match")
    if inm == etag or (ims and _http_date(stat.st_mtime) == ims):
        return Response(status_code=304)

    range_header = request.headers.get("Range")
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    headers = {
        "Content-Type": "application/octet-stream",
        "Accept-Ranges": "bytes",
        "ETag": etag,
        "Last-Modified": _http_date(stat.st_mtime),
        "Cache-Control": "public, max-age=300",
        "X-File-SHA256": _sha256_hex(path),
    }

    def file_iter(start=0, end=None, chunk=1024*64):
        with path.open("rb") as f:
            f.seek(start)
            remaining = (end-start+1) if end is not None else None
            while True:
                if remaining is not None and remaining <= 0: break
                data = f.read(chunk if remaining is None else min(chunk, remaining))
                if not data: break
                if remaining is not None: remaining -= len(data)
                yield data

    if not range_header:
        headers["Content-Length"] = str(stat.st_size)
        return StreamingResponse(file_iter(), headers=headers, media_type=mime)

    try:
        units, rng = range_header.split("="); start_s, end_s = rng.split("-")
        if units != "bytes": raise ValueError
        start = int(start_s) if start_s else 0
        end = int(end_s) if end_s else stat.st_size - 1
        start = max(0, start); end = min(end, stat.st_size-1)
        if start > end: raise ValueError
    except Exception:
        raise HTTPException(status_code=416, detail="Invalid Range")

    headers["Content-Range"] = f"bytes {start}-{end}/{stat.st_size}"
    headers["Content-Length"] = str(end-start+1)
    return StreamingResponse(file_iter(start, end), headers=headers, media_type=mime, status_code=206)

@router.get("/firmware/{download_id}/latest.bin")
def api_latest_bin(
    download_id: str,
    request: Request,
    authorization: Optional[str] = Header(None),
):
    resolved_device_id, _, _ = _resolve_access_context(
        authorization=authorization,
        device_id=None,
        download_id=download_id,
    )
    target = _resolve_latest(resolved_device_id)
    return _serve_file(target, request)
