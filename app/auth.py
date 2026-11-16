"""Google OAuth ("Sign in with Gmail") + session cookies.

Flow: /auth/google/login -> Google -> /auth/google/callback -> userinfo
-> find-or-create user -> signed JWT session cookie (HttpOnly).

No Google credentials are stored. The only thing kept about the user is
their Gmail address, name and profile picture URL.
"""
from __future__ import annotations

import secrets
import time
from urllib.parse import urlencode

import httpx
import jwt

from . import config, db

SESSION_COOKIE = "xauusd_session"
SESSION_TTL_SECONDS = 30 * 24 * 3600  # 30 days

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


def _redirect_uri() -> str:
    return f"{config.APP_BASE_URL}/auth/google/callback"


def login_url() -> str:
    """URL to send the customer to (with a one-time state token)."""
    state = secrets.token_urlsafe(24)
    params = {
        "client_id": config.GOOGLE_CLIENT_ID,
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}", state


def make_session(user_id: int, email: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {"sub": str(user_id), "email": email, "iat": now,
         "exp": now + SESSION_TTL_SECONDS},
        config.JWT_SECRET, algorithm="HS256")


def read_session(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        return jwt.decode(token, config.JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


async def exchange_code(code: str) -> dict:
    """code -> Google userinfo. Raises ValueError with a plain message."""
    try:
        async with httpx.AsyncClient(timeout=25, trust_env=False) as client:
            r = await client.post(GOOGLE_TOKEN_URL, data={
                "code": code,
                "client_id": config.GOOGLE_CLIENT_ID,
                "client_secret": config.GOOGLE_CLIENT_SECRET,
                "redirect_uri": _redirect_uri(),
                "grant_type": "authorization_code",
            })
            if r.status_code != 200:
                raise ValueError("Google sign-in failed. Please try again.")
            access = r.json().get("access_token")
            if not access:
                raise ValueError("Google sign-in failed. Please try again.")
            u = await client.get(GOOGLE_USERINFO_URL,
                                 headers={"Authorization": f"Bearer {access}"})
            if u.status_code != 200:
                raise ValueError("Could not read your Google profile.")
            info = u.json()
    except httpx.HTTPError:
        raise ValueError("Could not reach Google. Please try again.")
    email = (info.get("email") or "").strip().lower()
    if not email or not info.get("email_verified", True):
        raise ValueError("A verified Gmail address is required.")
    return {
        "email": email,
        "name": info.get("name") or "",
        "picture": info.get("picture") or "",
    }


def find_or_create_user(email: str, name: str, picture: str) -> dict:
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO users(email, name, picture) VALUES(?, ?, ?) "
            "ON CONFLICT(email) DO UPDATE SET name = excluded.name, "
            "picture = excluded.picture",
            (email, name, picture))
        conn.commit()
        row = conn.execute(
            "SELECT id, email, name, picture FROM users WHERE email = ?",
            (email,)).fetchone()
        return dict(row)
    finally:
        conn.close()
