"""License-key access control layered on the existing JWT auth.

Tiers (access level) and durations are INDEPENDENT on each key:
  - basic    : email alerts + a market-direction preview (no platform access)
  - moderate : alerts + read-only platform (market view, demo trades) 
  - advanced : full platform (approve trades, strategies, history, learning)

Lifecycle: active -> (at expiry) restricted (alerts off + platform locked to a
renew screen) -> (7 days later) disabled. Admin can always renew/re-enable.
"""
import os
import secrets
import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel

from db import db
from auth import get_current_user

logger = logging.getLogger("licensing")

TIER_RANK = {"basic": 1, "moderate": 2, "advanced": 3}
DURATIONS = {"1m": 30, "3m": 90, "6m": 180, "1y": 365}
GRACE_DAYS = 7


def _now():
    return datetime.now(timezone.utc)


def _parse(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:  # noqa
        return None


def gen_key() -> str:
    block = lambda: "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(4))
    return "TC-" + "-".join(block() for _ in range(4))


def compute_status(user: dict) -> str:
    """active | restricted | disabled | inactive (never redeemed)."""
    if user.get("role") == "admin":
        return "active"
    exp = _parse(user.get("license_expires_at"))
    if not user.get("tier") or not exp:
        return "inactive"
    now = _now()
    if now <= exp:
        return "active"
    if now <= exp + timedelta(days=GRACE_DAYS):
        return "restricted"
    return "disabled"


def license_view(user: dict) -> dict:
    status = compute_status(user)
    exp = _parse(user.get("license_expires_at"))
    days_left = None
    if exp:
        days_left = (exp - _now()).days
    return {
        "tier": "advanced" if user.get("role") == "admin" else user.get("tier"),
        "tier_rank": 3 if user.get("role") == "admin" else TIER_RANK.get(user.get("tier"), 0),
        "status": status,
        "expires_at": user.get("license_expires_at"),
        "days_left": days_left,
        "is_admin": user.get("role") == "admin",
    }


async def _redeem(user: dict, key_str: str) -> dict:
    key = await db.license_keys.find_one({"key": key_str.strip().upper()})
    if not key:
        raise HTTPException(400, detail="That license key isn't valid. Please check and try again.")
    if key.get("status") == "revoked":
        raise HTTPException(400, detail="This key has been revoked.")
    if key.get("redeemed_by") and key.get("redeemed_by") != user["id"]:
        raise HTTPException(400, detail="This key has already been used by another account.")
    days = DURATIONS.get(key["duration"], 30)
    now = _now()
    # renewal stacks on remaining time if still active
    cur_exp = _parse(user.get("license_expires_at"))
    base = cur_exp if (cur_exp and cur_exp > now and user.get("tier")) else now
    new_exp = base + timedelta(days=days)
    await db.users.update_one({"id": user["id"]}, {"$set": {
        "tier": key["tier"], "license_key": key["key"],
        "license_started_at": now.isoformat(), "license_expires_at": new_exp.isoformat(),
        "license_status": "active",
    }})
    await db.license_keys.update_one({"key": key["key"]}, {"$set": {
        "status": "redeemed", "redeemed_by": user["id"],
        "redeemed_username": user.get("username"), "redeemed_at": now.isoformat(),
    }})
    fresh = await db.users.find_one({"id": user["id"]}, {"_id": 0, "password_hash": 0})
    return fresh


# ---------- access dependencies ----------
async def platform_guard(request: Request) -> dict:
    """Gate for the /api platform router: must be active AND at least moderate."""
    user = await get_current_user(request)
    if user.get("role") == "admin":
        return user
    status = compute_status(user)
    if status != "active":
        raise HTTPException(403, detail={"code": "license", "status": status,
                                         "tier": user.get("tier")})
    if TIER_RANK.get(user.get("tier"), 0) < TIER_RANK["moderate"]:
        raise HTTPException(403, detail={"code": "tier", "status": status,
                                         "tier": user.get("tier"), "need": "moderate"})
    return user


def require_advanced(user: dict):
    if user.get("role") == "admin":
        return
    if TIER_RANK.get(user.get("tier"), 0) < TIER_RANK["advanced"]:
        raise HTTPException(403, detail={"code": "tier", "tier": user.get("tier"), "need": "advanced"})


async def require_admin(request: Request) -> dict:
    user = await get_current_user(request)
    if user.get("role") != "admin":
        raise HTTPException(403, detail="Admin only.")
    return user


async def refresh_statuses():
    """Daily job: persist license_status and flip alert eligibility on expiry."""
    users = await db.users.find({"role": {"$ne": "admin"}, "tier": {"$ne": None}},
                                {"_id": 0}).to_list(5000)
    for u in users:
        st = compute_status(u)
        if st != u.get("license_status"):
            await db.users.update_one({"id": u["id"]}, {"$set": {"license_status": st}})
            logger.info(f"license {u.get('username')} -> {st}")


# ---------- routers ----------
admin_router = APIRouter(prefix="/api/admin")
acct_router = APIRouter(prefix="/api/account")


class MintInput(BaseModel):
    tier: str
    duration: str
    count: int = 1
    note: str = ""


@admin_router.post("/keys")
async def mint_keys(body: MintInput, admin: dict = Depends(require_admin)):
    if body.tier not in TIER_RANK or body.duration not in DURATIONS:
        raise HTTPException(400, detail="Invalid tier or duration.")
    count = max(1, min(100, body.count))
    created = []
    for _ in range(count):
        k = gen_key()
        doc = {"key": k, "tier": body.tier, "duration": body.duration,
               "status": "unused", "note": body.note, "redeemed_by": None,
               "created_at": _now().isoformat(), "created_by": admin.get("username")}
        await db.license_keys.insert_one(doc)
        created.append(k)
    return {"created": created, "tier": body.tier, "duration": body.duration}


@admin_router.get("/keys")
async def list_keys(admin: dict = Depends(require_admin)):
    keys = await db.license_keys.find({}, {"_id": 0}).sort("created_at", -1).to_list(500)
    return {"keys": keys, "durations": DURATIONS, "tiers": list(TIER_RANK.keys())}


@admin_router.post("/keys/{key}/revoke")
async def revoke_key(key: str, admin: dict = Depends(require_admin)):
    await db.license_keys.update_one({"key": key}, {"$set": {"status": "revoked"}})
    return {"ok": True}


@admin_router.get("/users")
async def list_users(admin: dict = Depends(require_admin)):
    users = await db.users.find({"role": {"$ne": "admin"}}, {"_id": 0, "password_hash": 0}).to_list(2000)
    for u in users:
        u["status"] = compute_status(u)
    return {"users": users}


class RenewInput(BaseModel):
    username: str
    duration: str


@admin_router.post("/users/renew")
async def admin_renew(body: RenewInput, admin: dict = Depends(require_admin)):
    u = await db.users.find_one({"username": body.username})
    if not u:
        raise HTTPException(404, detail="User not found.")
    days = DURATIONS.get(body.duration, 30)
    now = _now()
    cur = _parse(u.get("license_expires_at"))
    base = cur if (cur and cur > now) else now
    new_exp = (base + timedelta(days=days)).isoformat()
    await db.users.update_one({"username": body.username},
                              {"$set": {"license_expires_at": new_exp, "license_status": "active"}})
    return {"ok": True, "expires_at": new_exp}


class RedeemInput(BaseModel):
    key: str


@acct_router.post("/redeem")
async def redeem(body: RedeemInput, user: dict = Depends(get_current_user)):
    fresh = await _redeem(user, body.key)
    return {"ok": True, "license": license_view(fresh)}


@acct_router.get("/license")
async def my_license(user: dict = Depends(get_current_user)):
    return license_view(user)
