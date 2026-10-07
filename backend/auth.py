"""Auth: single shared account, bcrypt hashing, JWT bearer tokens."""
import os
from datetime import datetime, timezone, timedelta
import uuid
import bcrypt
import jwt
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel

from db import db

JWT_ALGORITHM = "HS256"
auth_router = APIRouter(prefix="/api/auth")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def _secret() -> str:
    return os.environ["JWT_SECRET"]


def create_access_token(user_id: str, username: str) -> str:
    payload = {
        "sub": user_id,
        "username": username,
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
        "type": "access",
    }
    return jwt.encode(payload, _secret(), algorithm=JWT_ALGORITHM)


async def seed_admin():
    username = os.environ["ADMIN_USERNAME"]
    password = os.environ["ADMIN_PASSWORD"]
    existing = await db.users.find_one({"username": username})
    if existing is None:
        await db.users.insert_one({
            "id": str(uuid.uuid4()),
            "username": username,
            "email": username,
            "role": "admin",
            "password_hash": hash_password(password),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    else:
        upd = {}
        if not verify_password(password, existing["password_hash"]):
            upd["password_hash"] = hash_password(password)
        if existing.get("role") != "admin":
            upd["role"] = "admin"
        if upd:
            await db.users.update_one({"username": username}, {"$set": upd})


async def get_current_user(request: Request) -> dict:
    auth_header = request.headers.get("Authorization", "")
    token = auth_header[7:] if auth_header.startswith("Bearer ") else None
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(token, _secret(), algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = await db.users.find_one({"id": payload["sub"]}, {"_id": 0, "password_hash": 0})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


class LoginInput(BaseModel):
    username: str
    password: str


class RegisterInput(BaseModel):
    email: str
    password: str
    license_key: str


def _user_public(user: dict) -> dict:
    from licensing import license_view
    return {"id": user["id"], "username": user["username"],
            "email": user.get("email"), "role": user.get("role", "user"),
            "license": license_view(user)}


@auth_router.post("/register")
async def register(body: RegisterInput):
    import licensing
    email = body.email.strip().lower()
    if "@" not in email or len(body.password) < 6:
        raise HTTPException(status_code=400, detail="Enter a valid email and a password of at least 6 characters.")
    if await db.users.find_one({"username": email}):
        raise HTTPException(status_code=400, detail="An account with this email already exists. Please log in.")
    uid = str(uuid.uuid4())
    user = {"id": uid, "username": email, "email": email, "role": "user",
            "password_hash": hash_password(body.password),
            "created_at": datetime.now(timezone.utc).isoformat(), "tier": None}
    await db.users.insert_one(user)
    fresh = await licensing._redeem(user, body.license_key)  # validates + activates (or raises)
    token = create_access_token(uid, email)
    return {"token": token, "user": _user_public(fresh)}


@auth_router.post("/login")
async def login(body: LoginInput):
    ident = body.username.strip()
    user = await db.users.find_one({"username": ident}) or \
        await db.users.find_one({"username": ident.lower()}) or \
        await db.users.find_one({"email": ident.lower()})
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email/username or password")
    token = create_access_token(user["id"], user["username"])
    return {"token": token, "user": _user_public(user)}


@auth_router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    return _user_public(user)
