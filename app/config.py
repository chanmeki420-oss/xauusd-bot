"""Config — all secrets come from env vars, never from the repo.

Local dev: copy .env.example to .env and fill it in.
Render: set the same keys in the dashboard (render.yaml prompts for the
ones marked sync:false; JWT_SECRET, ENCRYPTION_KEY and ENGINE_API_KEY
are generated for you).
"""
from __future__ import annotations

import os
import secrets

from dotenv import load_dotenv

load_dotenv()


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _get_or_generate(name: str, path: str) -> str:
    v = _get(name)
    if v:
        return v
    v = secrets.token_urlsafe(32)
    # Persist locally so restarts keep working; on Render this never runs
    # because the env var is set in the dashboard.
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{name}={v}\n")
    except OSError:
        pass
    return v


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(BASE_DIR, ".env")
DB_PATH = os.path.join(BASE_DIR, "xauusd_bot.db")

# Public TRC20 address where customers send the $20/month subscription.
DEPOSIT_TRC20_ADDRESS = _get("DEPOSIT_TRC20_ADDRESS", "TEKx2jjjq46EM3sQR8bVPZG3fad5Cd9Jfh")
PRICE_MONTHLY_USD = float(_get("PRICE_MONTHLY_USD", "20"))
PLAN_DAYS = int(_get("PLAN_DAYS", "30"))

# Owner's Gmail — the ONLY account that sees the admin panel.
ADMIN_EMAIL = _get("ADMIN_EMAIL", "sheraz.majeed372@gmail.com").strip().lower()

# Google OAuth ("Sign in with Gmail"). Created once in Google Cloud Console;
# see DEPLOY.md. Until these are set, login shows "not configured".
GOOGLE_CLIENT_ID = _get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = _get("GOOGLE_CLIENT_SECRET")
APP_BASE_URL = _get("APP_BASE_URL", "http://localhost:8000").rstrip("/")

TRONGRID_API_KEY = _get("TRONGRID_API_KEY") or None

JWT_SECRET = _get_or_generate("JWT_SECRET", ENV_FILE)
# Fernet key for MT5 passwords. Generated once; keep it safe — losing it
# makes stored MT5 passwords undecryptable.
ENCRYPTION_KEY = _get_or_generate("ENCRYPTION_KEY", ENV_FILE)
# Shared secret the Windows engine sends to prove it is ours.
ENGINE_API_KEY = _get_or_generate("ENGINE_API_KEY", ENV_FILE)

GOOGLE_CONFIGURED = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)
