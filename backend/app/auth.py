"""Registration and login (argon2 password hashes, signed JWT tokens), and the dependency that names the caller."""
from __future__ import annotations

import logging
import os
import secrets
import time
from functools import lru_cache

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from . import config
from .store import Store, UsernameTaken, get_store
from .userfiles import UserFiles, get_user_files, username_problem

log = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

PASSWORD_MIN, PASSWORD_MAX = 8, 128
_hasher = PasswordHasher()
_bearer = HTTPBearer(auto_error=False)
# Checked against when the username doesn't exist, so an unknown user takes as long to turn away as a wrong password.
_NO_USER_HASH = _hasher.hash("no such user")


@lru_cache
def _secret() -> str:
    if config.AUTH_SECRET:
        return config.AUTH_SECRET
    if config.COSMOS_ENDPOINT:  # a key made per instance would make every other instance reject the logins
        raise RuntimeError("AUTH_SECRET must be set when the app runs against Cosmos DB (Azure).")
    path = config.VAR_DIR / "auth_secret"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(48))
    return path.read_text().strip()


def _login_response(username: str) -> dict:
    now = int(time.time())
    claims = {"sub": username, "iat": now, "exp": now + config.AUTH_TOKEN_HOURS * 3600}
    return {"access_token": jwt.encode(claims, _secret(), algorithm="HS256"), "token_type": "bearer", "username": username}


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
    """The username the request's token was issued to. 401 without a valid one."""
    unauthorized = {"WWW-Authenticate": "Bearer"}
    if credentials is None:
        raise HTTPException(401, "Log in first.", headers=unauthorized)
    try:
        claims = jwt.decode(credentials.credentials, _secret(), algorithms=["HS256"], options={"require": ["exp", "sub"]})
    except jwt.PyJWTError:
        raise HTTPException(401, "Your login expired or is invalid. Log in again.", headers=unauthorized) from None
    return claims["sub"]


class Credentials(BaseModel):
    username: str
    password: str


def _password_problem(password: str) -> str | None:
    if len(password) < PASSWORD_MIN:
        return f"Password must be at least {PASSWORD_MIN} characters."
    if len(password) > PASSWORD_MAX:
        return f"Password must be at most {PASSWORD_MAX} characters."
    return None


def _password_matches(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


@router.post("/register", status_code=201)
def register(body: Credentials, store: Store = Depends(get_store), files: UserFiles = Depends(get_user_files)) -> dict:
    username = body.username.strip().lower()
    problem = username_problem(username) or _password_problem(body.password)
    if problem:
        raise HTTPException(422, problem)
    try:
        store.create_user(username, _hasher.hash(body.password))
    except UsernameTaken:
        raise HTTPException(409, "That username is taken.") from None
    try:
        files.ensure(username)
    except Exception:  # the account exists; the file space is created on the first upload instead
        log.exception("could not create the file space of %s yet", username)
    return _login_response(username)


@router.post("/login")
def login(body: Credentials, store: Store = Depends(get_store)) -> dict:
    username = body.username.strip().lower()
    user = None if username_problem(username) else store.get_user(username)
    password_ok = len(body.password) <= PASSWORD_MAX and _password_matches(
        user.password_hash if user else _NO_USER_HASH, body.password
    )
    if user is None or not password_ok:
        raise HTTPException(401, "Wrong username or password.")
    return _login_response(username)


@router.get("/me")
def me(username: str = Depends(current_user), store: Store = Depends(get_store)) -> dict:
    """Also the check that a token still belongs to an account (the database may have been reset since)."""
    if store.get_user(username) is None:
        raise HTTPException(401, "This account no longer exists.", headers={"WWW-Authenticate": "Bearer"})
    return {"username": username}
