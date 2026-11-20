from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..models import AuditLog, User
from ..security import COOKIE, Auth, create_session, end_session, optional_auth, require_auth, verify_or_dummy

router = APIRouter(prefix="/api/auth")


class Login(BaseModel):
    username: str
    password: str


def me_view(auth: Auth) -> dict:
    u = auth.user
    return {"id": u.id, "username": u.username, "display_name": u.display_name or u.username, "role": u.role,
            "csrf": auth.session.csrf}


@router.post("/login")
async def login(body: Login, response: Response, db: AsyncSession = Depends(get_db)) -> dict:
    user = (await db.execute(select(User).where(User.username == body.username.strip().lower()))).scalar_one_or_none()
    if not verify_or_dummy(user, body.password):
        db.add(AuditLog(src="ui:anon", action="login_failed", target=body.username[:64]))
        await db.commit()
        raise HTTPException(401, "wrong username or password")
    assert user is not None
    token, sess = await create_session(db, user)
    db.add(AuditLog(src=f"ui:{user.id}", action="login", target=user.username))
    await db.commit()
    s = get_settings()
    response.set_cookie(COOKIE, token, httponly=True, secure=s.cookie_secure, samesite="lax",
                        max_age=s.session_days * 86400, path="/")
    return me_view(Auth(user, sess))


@router.post("/logout")
async def logout(request: Request, response: Response, auth: Auth = Depends(require_auth),
                 db: AsyncSession = Depends(get_db)) -> dict:
    await end_session(db, request.cookies.get(COOKIE, ""))
    await db.commit()
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
async def me(auth: Auth | None = Depends(optional_auth)) -> dict:
    if auth is None:
        raise HTTPException(401, "not signed in")
    return me_view(auth)
