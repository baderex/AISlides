import hashlib
import secrets
import time

from fastapi import Cookie, HTTPException, Response
from pwdlib import PasswordHash
from sqlalchemy import select

from app.config import settings
from app.db import LoginSession, Session, User

COOKIE = "framecraft_session"
LOCAL_USER = "00000000-0000-0000-0000-000000000001"
SESSION_SECONDS = 60 * 60 * 24 * 7
passwords = PasswordHash.recommended()
dummy = passwords.hash("framecraft-invalid-user")


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def login_session(db, user, response: Response):
    token = secrets.token_urlsafe(36)
    db.add(LoginSession(id=digest(token), owner_id=user.id, expires=int(time.time()) + SESSION_SECONDS))
    response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True, samesite="lax",
                        secure=settings().app_env == "production", path="/")


def resolve(framecraft_session: str | None = Cookie(default=None)) -> str:
    with Session() as db:
        if framecraft_session:
            item = db.get(LoginSession, digest(framecraft_session))
            if item and item.expires > int(time.time()):
                return item.owner_id
        if settings().implicit_session and db.get(User, LOCAL_USER):
            return LOCAL_USER
    raise HTTPException(401, "Sign in to access your workspace.")


def authenticate(db, email, password):
    user = db.scalar(select(User).where(User.email == email))
    valid = passwords.verify(password, user.password if user and user.password else dummy)
    return user if valid and user and user.password else None
