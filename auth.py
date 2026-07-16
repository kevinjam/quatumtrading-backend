"""Google OAuth (authorization code) + local session cookie auth."""
from __future__ import annotations

import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, Request, Response

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

SESSION_DAYS = 7
OAUTH_STATE_COOKIE = "oauth_state"
SESSION_COOKIE = "session_token"


def _require_env(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise HTTPException(
            status_code=503,
            detail=f"Server misconfigured: missing {name}",
        )
    return value


def google_client_id() -> str:
    return _require_env("GOOGLE_CLIENT_ID")


def google_client_secret() -> str:
    return _require_env("GOOGLE_CLIENT_SECRET")


def google_redirect_uri() -> str:
    return _require_env("GOOGLE_REDIRECT_URI")


def frontend_url() -> str:
    return (os.environ.get("FRONTEND_URL") or "http://localhost:3000").rstrip("/")


def cookie_secure() -> bool:
    raw = (os.environ.get("COOKIE_SECURE") or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    # Default: secure in production-like setups
    return frontend_url().startswith("https://")


def cookie_samesite() -> str:
    raw = (os.environ.get("COOKIE_SAMESITE") or "").strip().lower()
    if raw in ("lax", "strict", "none"):
        return raw
    # Cross-site SPA (different domains) needs None; localhost ports are same-site.
    return "none" if cookie_secure() else "lax"


def session_cookie_kwargs(max_age: Optional[int] = None) -> dict:
    kwargs = {
        "key": SESSION_COOKIE,
        "httponly": True,
        "secure": cookie_secure(),
        "samesite": cookie_samesite(),
        "path": "/",
    }
    if max_age is not None:
        kwargs["max_age"] = max_age
    return kwargs


def build_google_authorize_url(state: str) -> str:
    params = {
        "client_id": google_client_id(),
        "redirect_uri": google_redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "online",
        "include_granted_scopes": "true",
        "prompt": "select_account",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def set_oauth_state_cookie(response: Response, state: str) -> None:
    response.set_cookie(
        key=OAUTH_STATE_COOKIE,
        value=state,
        httponly=True,
        secure=cookie_secure(),
        samesite=cookie_samesite(),
        max_age=600,
        path="/",
    )


def clear_oauth_state_cookie(response: Response) -> None:
    response.delete_cookie(
        key=OAUTH_STATE_COOKIE,
        path="/",
        secure=cookie_secure(),
        samesite=cookie_samesite(),
    )


def verify_oauth_state(request: Request, state: str) -> None:
    expected = request.cookies.get(OAUTH_STATE_COOKIE)
    if not state or not expected or not secrets.compare_digest(expected, state):
        raise HTTPException(status_code=400, detail="Invalid OAuth state")


async def exchange_google_code(code: str) -> dict:
    """Exchange auth code for tokens and return Google userinfo."""
    async with httpx.AsyncClient(timeout=20) as client:
        token_res = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": google_client_id(),
                "client_secret": google_client_secret(),
                "redirect_uri": google_redirect_uri(),
                "grant_type": "authorization_code",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if token_res.status_code != 200:
            raise HTTPException(status_code=401, detail="Google token exchange failed")
        tokens = token_res.json()
        access_token = tokens.get("access_token")
        if not access_token:
            raise HTTPException(status_code=401, detail="Google access_token missing")

        info_res = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if info_res.status_code != 200:
            raise HTTPException(status_code=401, detail="Google userinfo failed")
        info = info_res.json()

    email = (info.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=401, detail="Google account has no email")
    if info.get("email_verified") is False:
        raise HTTPException(status_code=401, detail="Google email not verified")

    return {
        "email": email,
        "name": info.get("name") or "",
        "picture": info.get("picture") or "",
        "google_sub": info.get("sub") or "",
    }


async def upsert_user_and_session(db, oauth_data: dict) -> dict:
    email = oauth_data["email"]
    name = oauth_data.get("name", "")
    picture = oauth_data.get("picture", "")
    google_sub = oauth_data.get("google_sub", "")
    session_token = secrets.token_urlsafe(32)

    user_doc = await db.users.find_one({"email": email}, {"_id": 0})
    if user_doc:
        user_id = user_doc["user_id"]
        update = {"name": name, "picture": picture}
        if google_sub:
            update["google_sub"] = google_sub
        await db.users.update_one({"user_id": user_id}, {"$set": update})
    else:
        user_id = f"user_{uuid.uuid4().hex[:12]}"
        doc = {
            "user_id": user_id,
            "email": email,
            "name": name,
            "picture": picture,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if google_sub:
            doc["google_sub"] = google_sub
        await db.users.insert_one(doc)

    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    await db.user_sessions.update_one(
        {"session_token": session_token},
        {
            "$set": {
                "user_id": user_id,
                "session_token": session_token,
                "expires_at": expires_at.isoformat(),
            },
            "$setOnInsert": {"created_at": datetime.now(timezone.utc).isoformat()},
        },
        upsert=True,
    )

    return {
        "user_id": user_id,
        "email": email,
        "name": name,
        "picture": picture,
        "session_token": session_token,
    }


async def get_current_user(request: Request, db) -> dict:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    sess = await db.user_sessions.find_one({"session_token": token}, {"_id": 0})
    if not sess:
        raise HTTPException(status_code=401, detail="Invalid session")

    expires_at = sess["expires_at"]
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Session expired")

    user = await db.users.find_one({"user_id": sess["user_id"]}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user
