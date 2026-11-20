from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..enroll import enroll

router = APIRouter()


@router.post("/api/v1/enroll")
async def enroll_device(request: Request, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """Device enrollment. No session: the one-time token is the credential."""
    ip = request.headers.get("x-real-ip") or (request.client.host if request.client else "?")
    if not request.app.state.enroll_limiter.allow(ip):
        return JSONResponse({"error": "rate_limited"}, status_code=429)
    if not request.app.state.pki.initialized:
        return JSONResponse({"error": "token_invalid", "detail": "server CA not initialized"}, status_code=401)
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "bad_request", "detail": "body is not JSON"}, status_code=400)
    status, resp = await enroll(db, request.app.state.pki, request.app.state.settings, body)
    return JSONResponse(resp, status_code=status)
