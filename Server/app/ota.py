import hmac, hashlib, mimetypes, os
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Request, Header, HTTPException, Response
from fastapi.responses import JSONResponse, StreamingResponse
from .config import settings

router = APIRouter()

# If you have a central config module, prefer importing from it:
# from .config import FIRMWARE_DIR, PUBLIC_BASE, API_BEARER, MANIFEST_HMAC_SECRET, LAN_PUBLIC_BASE

# Otherwise, read from environment with sensible defaults:
FIRMWARE_DIR = Path(os.getenv("FIRMWARE_DIR", "/srv/firmware"))
FIRMWARE_DIR.mkdir(parents=True, exist_ok=True)

PUBLIC_BASE = os.getenv("PUBLIC_BASE", "https://lights.evm100.org")
API_BEARER = os.getenv("API_BEARER", "")
MANIFEST_HMAC_SECRET = os.getenv("MANIFEST_HMAC_SECRET", "")
LAN_PUBLIC_BASE = os.getenv("LAN_PUBLIC_BASE", "")  # keep if you use split-horizon override

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

def _require_bearer(auth_header: Optional[str]):
    if not settings.API_BEARER: return
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    if auth_header.split(" ",1)[1] != settings.API_BEARER:
        raise HTTPException(status_code=403, detail="Invalid bearer token")

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
    if not secret: return None
    blob = hashlib.sha256()
    canonical = hashlib.sha256()  # (just to keep name; not used)
    payload = (hashlib.json.dumps(body, sort_keys=True, separators=(",", ":"))
               if hasattr(hashlib, "json") else __import__("json").dumps(body, sort_keys=True, separators=(",", ":"))).encode()
    key = bytes.fromhex(secret) if all(c in "0123456789abcdef" for c in secret.lower()) and len(secret)%2==0 else secret.encode()
    return hmac.new(key, payload, hashlib.sha256).hexdigest()

@router.get("/api/firmware/v1/manifest")
def api_manifest(device_id: str, authorization: Optional[str] = Header(None)):
    _require_bearer(authorization)
    target = _resolve_latest(device_id)
    size = target.stat().st_size
    sha = _sha256_hex(target)
    name = target.name
    version = "unknown"
    if "_v" in name and name.endswith(".bin"):
        version = name.split("_v",1)[-1].rsplit(".bin",1)[0]
    body = {
        "device_id": device_id,
        "version": version,
        "size": size,
        "sha256_hex": sha,
        "binary_url": f"{settings.PUBLIC_BASE}/firmware/{device_id}/latest.bin"
    }
    if LAN_PUBLIC_BASE:
        body["binary_url_lan"] = f"{LAN_PUBLIC_BASE}/firmware/{device_id}/latest.bin"
    sig = _manifest_sig(body)
    headers = {"Cache-Control": "no-store"}
    if sig:
        headers["X-Manifest-Signature"] = sig
        body["sig"] = sig
    return JSONResponse(body, headers=headers)

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

@router.get("/firmware/{device_id}/latest.bin")
def api_latest_bin(device_id: str, request: Request, authorization: Optional[str] = Header(None)):
    _require_bearer(authorization)
    target = _resolve_latest(device_id)
    return _serve_file(target, request)
