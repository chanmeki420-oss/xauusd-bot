"""XAUUSD Bot — customer panel (centralized model).

Customers sign in with Gmail, connect their MT5 account (server / login /
password — password is encrypted, never shown back), pay $20/month in
USDT (TRC20) with on-chain TXID verification, and press Start/Stop.
The bot itself runs on the owner's Windows server (see
~/workspace/xauusd-bot-server/engine.py), which polls /api/engine/*.

Admin panel: only the owner's Gmail (ADMIN_EMAIL) sees it. Everyone else
gets the customer dashboard only. No profit is ever promised anywhere.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone

import httpx
import jwt
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import auth, config, db
from .crypto import decrypt_password, encrypt_password
from .tron import TronVerifyError, verify_usdt_deposit

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")

app = FastAPI(title="XAUUSD Bot")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_price_cache: dict = {"value": None, "at": None}


# ------------------------------------------------------------------ #
# Helpers                                                             #
# ------------------------------------------------------------------ #
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _user_from_request(request: Request) -> dict | None:
    sess = auth.read_session(request.cookies.get(auth.SESSION_COOKIE))
    if not sess:
        return None
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT id, email, name, picture, blocked FROM users WHERE id = ?",
            (int(sess["sub"]),)).fetchone()
        if not row:
            return None
        user = dict(row)
        if user.pop("blocked"):
            return None  # blocked by admin: treated as signed out
        return user
    finally:
        conn.close()


def _require_user(request: Request) -> dict:
    user = _user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Please sign in with Gmail.")
    return user


def _require_admin(request: Request) -> dict:
    user = _require_user(request)
    if user["email"].lower() != config.ADMIN_EMAIL:
        raise HTTPException(status_code=403, detail="Admin only.")
    return user


def _is_admin(user: dict | None) -> bool:
    return bool(user and user["email"].lower() == config.ADMIN_EMAIL)


def _active_license(user_id: int) -> dict | None:
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT id, starts_at, ends_at FROM licenses "
            "WHERE user_id = ? AND active = 1 AND ends_at > datetime('now') "
            "ORDER BY ends_at DESC LIMIT 1", (user_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _price_usd() -> float:
    try:
        return float(db.get_setting("price_usd", str(config.PRICE_MONTHLY_USD)))
    except ValueError:
        return config.PRICE_MONTHLY_USD


def _deposit_address() -> str:
    return db.get_setting("deposit_address", config.DEPOSIT_TRC20_ADDRESS)


def _engine_auth(key: str | None) -> None:
    if not key or key != config.ENGINE_API_KEY:
        raise HTTPException(status_code=401, detail="Bad engine key.")


# ------------------------------------------------------------------ #
# Pages + public config                                               #
# ------------------------------------------------------------------ #
@app.on_event("startup")
def _startup() -> None:
    db.init_db()


@app.get("/", response_class=HTMLResponse)
async def index():
    with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8") as f:
        return f.read()


@app.get("/privacy", response_class=HTMLResponse)
async def privacy():
    with open(os.path.join(STATIC_DIR, "privacy.html"), encoding="utf-8") as f:
        return f.read()


@app.get("/api/health")
async def health():
    return {"ok": True}


@app.get("/api/config")
async def public_config():
    return {
        "google_configured": config.GOOGLE_CONFIGURED,
        "price_usd": _price_usd(),
        "deposit_address": _deposit_address(),
        "plan_days": config.PLAN_DAYS,
    }


@app.get("/api/price")
async def live_price():
    """Live XAU/USD: Yahoo gold futures, fallback Kraken PAXG."""
    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            r = await client.get(
                "https://query1.finance.yahoo.com/v8/finance/chart/GC=F",
                headers={"User-Agent": "Mozilla/5.0"})
            meta = r.json()["chart"]["result"][0]["meta"]
            value = float(meta["regularMarketPrice"])
            _price_cache["value"] = value
            _price_cache["at"] = _now().isoformat()
            return {"symbol": "XAU/USD", "price": value, "source": "yahoo"}
    except Exception:
        pass
    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            r = await client.get(
                "https://api.kraken.com/0/public/Ticker?pair=PAXGUSD")
            value = float(r.json()["result"]["PAXGUSD"]["c"][0])
            _price_cache["value"] = value
            _price_cache["at"] = _now().isoformat()
            return {"symbol": "XAU/USD", "price": value, "source": "kraken"}
    except Exception:
        pass
    if _price_cache["value"]:
        return {"symbol": "XAU/USD", "price": _price_cache["value"],
                "source": "cached", "at": _price_cache["at"]}
    raise HTTPException(status_code=502, detail="Price feed unavailable.")


# ------------------------------------------------------------------ #
# Auth (Google / Gmail)                                               #
# ------------------------------------------------------------------ #
@app.get("/auth/google/login")
async def google_login():
    if not config.GOOGLE_CONFIGURED:
        return RedirectResponse("/?error=google_not_configured", status_code=302)
    url, state = auth.login_url()
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie("oauth_state", state, max_age=600, httponly=True,
                    samesite="lax")
    return resp


@app.get("/auth/google/callback")
async def google_callback(request: Request, code: str = "",
                          state: str = ""):
    if not code or state != request.cookies.get("oauth_state"):
        return RedirectResponse("/?error=google_failed", status_code=302)
    try:
        info = await auth.exchange_code(code)
    except ValueError:
        return RedirectResponse("/?error=google_failed", status_code=302)
    user = auth.find_or_create_user(info["email"], info["name"],
                                    info["picture"])
    resp = RedirectResponse("/", status_code=302)
    resp.set_cookie(auth.SESSION_COOKIE, auth.make_session(user["id"], user["email"]),
                    max_age=auth.SESSION_TTL_SECONDS, httponly=True,
                    samesite="lax")
    resp.delete_cookie("oauth_state")
    return resp


@app.post("/auth/logout")
async def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.SESSION_COOKIE)
    return resp


@app.get("/api/me")
async def me(request: Request):
    user = _user_from_request(request)
    if not user:
        return {"user": None}
    lic = _active_license(user["id"])
    return {"user": user, "is_admin": _is_admin(user),
            "license": lic}


# ------------------------------------------------------------------ #
# Customer: MT5 accounts                                              #
# ------------------------------------------------------------------ #
@app.get("/api/mt5")
async def list_mt5(request: Request):
    user = _require_user(request)
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT id, broker, server, login, bot_enabled, status, "
            "last_heartbeat, created_at FROM mt5_accounts "
            "WHERE user_id = ? ORDER BY id", (user["id"],)).fetchall()
        return {"accounts": [dict(r) for r in rows]}
    finally:
        conn.close()


@app.post("/api/mt5")
async def add_mt5(request: Request):
    user = _require_user(request)
    body = await request.json()
    broker = (body.get("broker") or "").strip()[:80]
    server = (body.get("server") or "").strip()
    login = (body.get("login") or "").strip()
    password = body.get("password") or ""
    if not server or not login or not password:
        raise HTTPException(status_code=400,
                            detail="Server, login and password are required.")
    if not login.isdigit():
        raise HTTPException(status_code=400,
                            detail="MT5 login must be the account number.")
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO mt5_accounts(user_id, broker, server, login, "
            "password_enc) VALUES(?, ?, ?, ?, ?)",
            (user["id"], broker, server, login, encrypt_password(password)))
        conn.commit()
        return {"ok": True}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400,
                            detail="This MT5 account is already connected.")
    finally:
        conn.close()


@app.put("/api/mt5/{account_id}/bot")
async def set_bot(account_id: int, request: Request):
    user = _require_user(request)
    body = await request.json()
    enabled = bool(body.get("enabled"))
    if enabled and not _active_license(user["id"]):
        raise HTTPException(
            status_code=402,
            detail="An active $20/month license is required to start the bot. "
                   "Pay on the Pay page first.")
    conn = db.connect()
    try:
        cur = conn.execute(
            "UPDATE mt5_accounts SET bot_enabled = ? "
            "WHERE id = ? AND user_id = ?",
            (1 if enabled else 0, account_id, user["id"]))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Account not found.")
        conn.commit()
        return {"ok": True, "bot_enabled": enabled}
    finally:
        conn.close()


@app.delete("/api/mt5/{account_id}")
async def delete_mt5(account_id: int, request: Request):
    user = _require_user(request)
    conn = db.connect()
    try:
        conn.execute("DELETE FROM mt5_accounts WHERE id = ? AND user_id = ?",
                     (account_id, user["id"]))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ------------------------------------------------------------------ #
# Customer: payments + license                                        #
# ------------------------------------------------------------------ #
@app.get("/api/license")
async def license_status(request: Request):
    user = _require_user(request)
    return {"license": _active_license(user["id"]),
            "price_usd": _price_usd(),
            "deposit_address": _deposit_address(),
            "plan_days": config.PLAN_DAYS}


@app.post("/api/payments/submit")
async def submit_payment(request: Request):
    user = _require_user(request)
    body = await request.json()
    txid = (body.get("txid") or "").strip()
    price = _price_usd()
    try:
        result = await verify_usdt_deposit(
            txid, expected_amount=price,
            deposit_address=_deposit_address(),
            min_deposit=price,
            api_key=config.TRONGRID_API_KEY)
    except TronVerifyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    now = _now()
    ends = now + timedelta(days=config.PLAN_DAYS)
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO payments(user_id, txid, amount, from_address, "
            "status) VALUES(?, ?, ?, ?, 'verified')",
            (user["id"], result["txid"], result["amount"],
             result["from_address"]))
        conn.execute(
            "UPDATE licenses SET active = 0 WHERE user_id = ?", (user["id"],))
        conn.execute(
            "INSERT INTO licenses(user_id, starts_at, ends_at, active) "
            "VALUES(?, ?, ?, 1)",
            (user["id"], now.isoformat(), ends.isoformat()))
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400,
                            detail="This transaction was already used.")
    finally:
        conn.close()
    return {"ok": True, "amount": result["amount"],
            "license_until": ends.date().isoformat()}


@app.get("/api/payments")
async def list_payments(request: Request):
    user = _require_user(request)
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT txid, amount, from_address, status, created_at "
            "FROM payments WHERE user_id = ? ORDER BY id DESC LIMIT 50",
            (user["id"],)).fetchall()
        return {"payments": [dict(r) for r in rows]}
    finally:
        conn.close()


@app.get("/api/trades")
async def list_trades(request: Request):
    user = _require_user(request)
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT mt5_login, ticket, symbol, direction, lot, open_price, "
            "close_price, profit, opened_at, closed_at FROM trades "
            "WHERE user_id = ? ORDER BY id DESC LIMIT 100",
            (user["id"],)).fetchall()
        return {"trades": [dict(r) for r in rows]}
    finally:
        conn.close()


# ------------------------------------------------------------------ #
# Admin (owner's Gmail only)                                          #
# ------------------------------------------------------------------ #
@app.get("/api/admin/overview")
async def admin_overview(request: Request):
    _require_admin(request)
    conn = db.connect()
    try:
        users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        active = conn.execute(
            "SELECT COUNT(*) c FROM licenses WHERE active = 1 "
            "AND ends_at > datetime('now')").fetchone()["c"]
        accts = conn.execute("SELECT COUNT(*) c FROM mt5_accounts").fetchone()["c"]
        running = conn.execute(
            "SELECT COUNT(*) c FROM mt5_accounts WHERE bot_enabled = 1"
        ).fetchone()["c"]
        paid = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) s FROM payments "
            "WHERE status = 'verified'").fetchone()["s"]
        return {"users": users, "active_licenses": active,
                "mt5_accounts": accts, "bots_running": running,
                "revenue_usd": paid}
    finally:
        conn.close()


@app.get("/api/admin/customers")
async def admin_customers(request: Request):
    _require_admin(request)
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT u.id, u.email, u.name, u.created_at, u.blocked, "
            "MAX(l.ends_at) AS license_until, "
            "(SELECT COUNT(*) FROM mt5_accounts a WHERE a.user_id = u.id) "
            "AS accounts, "
            "(SELECT COUNT(*) FROM mt5_accounts a WHERE a.user_id = u.id "
            "AND a.bot_enabled = 1) AS bots_running "
            "FROM users u LEFT JOIN licenses l ON l.user_id = u.id "
            "AND l.active = 1 GROUP BY u.id ORDER BY u.id DESC"
        ).fetchall()
        return {"customers": [dict(r) for r in rows]}
    finally:
        conn.close()


@app.post("/api/admin/customers/{user_id}/bot")
async def admin_set_bot(user_id: int, request: Request):
    """Force-start or force-stop a customer's bot (all their MT5 accounts)."""
    _require_admin(request)
    body = await request.json()
    enabled = bool(body.get("enabled"))
    conn = db.connect()
    try:
        cur = conn.execute(
            "UPDATE mt5_accounts SET bot_enabled = ? WHERE user_id = ?",
            (1 if enabled else 0, user_id))
        conn.commit()
        return {"ok": True, "bot_enabled": enabled,
                "accounts_changed": cur.rowcount}
    finally:
        conn.close()


@app.post("/api/admin/customers/{user_id}/license")
async def admin_license(user_id: int, request: Request):
    """Grant/extend a license (action=grant, days) or revoke it
    (action=revoke). Lets the owner give a free trial or cut off a user."""
    _require_admin(request)
    body = await request.json()
    action = (body.get("action") or "").strip().lower()
    conn = db.connect()
    try:
        exists = conn.execute(
            "SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="User not found.")
        if action == "revoke":
            conn.execute(
                "UPDATE licenses SET active = 0 WHERE user_id = ?",
                (user_id,))
            conn.execute(
                "UPDATE mt5_accounts SET bot_enabled = 0 WHERE user_id = ?",
                (user_id,))
            conn.commit()
            return {"ok": True, "license": None}
        if action == "grant":
            try:
                days = int(body.get("days", 30))
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="Bad days value.")
            if days <= 0 or days > 365:
                raise HTTPException(status_code=400,
                                    detail="Days must be 1-365.")
            now = _now()
            row = conn.execute(
                "SELECT ends_at FROM licenses WHERE user_id = ? AND active = 1 "
                "AND ends_at > datetime('now') ORDER BY ends_at DESC LIMIT 1",
                (user_id,)).fetchone()
            if row:
                ends = datetime.fromisoformat(row["ends_at"])
                if ends.tzinfo is None:
                    ends = ends.replace(tzinfo=timezone.utc)
                new_end = ends + timedelta(days=days)
                conn.execute(
                    "UPDATE licenses SET ends_at = ? WHERE user_id = ? "
                    "AND active = 1 AND ends_at = ?",
                    (new_end.isoformat(), user_id, row["ends_at"]))
            else:
                new_end = now + timedelta(days=days)
                conn.execute(
                    "UPDATE licenses SET active = 0 WHERE user_id = ?",
                    (user_id,))
                conn.execute(
                    "INSERT INTO licenses(user_id, starts_at, ends_at, active) "
                    "VALUES(?, ?, ?, 1)",
                    (user_id, now.isoformat(), new_end.isoformat()))
            conn.commit()
            return {"ok": True,
                    "license_until": new_end.date().isoformat()}
        raise HTTPException(status_code=400,
                            detail="action must be grant or revoke.")
    finally:
        conn.close()


@app.post("/api/admin/customers/{user_id}/block")
async def admin_block(user_id: int, request: Request):
    """Block or unblock a user. Blocked users cannot sign in and their
    bots are force-stopped; their payment records are kept."""
    _require_admin(request)
    body = await request.json()
    blocked = bool(body.get("blocked"))
    conn = db.connect()
    try:
        cur = conn.execute(
            "UPDATE users SET blocked = ? WHERE id = ?",
            (1 if blocked else 0, user_id))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="User not found.")
        if blocked:
            conn.execute(
                "UPDATE mt5_accounts SET bot_enabled = 0 WHERE user_id = ?",
                (user_id,))
        conn.commit()
        return {"ok": True, "blocked": blocked}
    finally:
        conn.close()


@app.get("/api/admin/payments")
async def admin_payments(request: Request):
    _require_admin(request)
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT p.txid, p.amount, p.from_address, p.status, p.created_at, "
            "u.email FROM payments p JOIN users u ON u.id = p.user_id "
            "ORDER BY p.id DESC LIMIT 100").fetchall()
        return {"payments": [dict(r) for r in rows]}
    finally:
        conn.close()


@app.post("/api/admin/settings")
async def admin_settings(request: Request):
    _require_admin(request)
    body = await request.json()
    if "price_usd" in body:
        try:
            price = float(body["price_usd"])
            if price <= 0:
                raise ValueError
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Bad price.")
        db.set_setting("price_usd", str(price))
    if "deposit_address" in body:
        addr = (body["deposit_address"] or "").strip()
        if len(addr) != 34 or not addr.startswith("T"):
            raise HTTPException(status_code=400, detail="Bad TRON address.")
        db.set_setting("deposit_address", addr)
    return {"ok": True, "price_usd": _price_usd(),
            "deposit_address": _deposit_address()}


@app.get("/api/admin/backup")
async def admin_backup(request: Request):
    """Download all records as JSON (admin only). Use it regularly: on
    Render's free tier the SQLite file is wiped on redeploy, so keep a
    backup. Encrypted MT5 passwords are NOT included — they can only be
    restored from a server backup of the .db file itself."""
    _require_admin(request)
    conn = db.connect()
    try:
        dump: dict = {}
        for table in ("users", "mt5_accounts", "licenses", "payments",
                      "trades", "settings"):
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            dump[table] = [dict(r) for r in rows]
        for a in dump["mt5_accounts"]:
            a.pop("password_enc", None)
        return dump
    finally:
        conn.close()


# ------------------------------------------------------------------ #
# Engine API (Windows server bridge)                                  #
# ------------------------------------------------------------------ #
@app.get("/api/engine/customers")
async def engine_customers(x_engine_key: str | None = Header(default=None)):
    """The engine polls this: every enabled account with an active license,
    with the MT5 password decrypted. Guarded by ENGINE_API_KEY and served
    over HTTPS only in production."""
    _engine_auth(x_engine_key)
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT u.id AS user_id, u.email, a.server, a.login, "
            "a.password_enc, a.bot_enabled FROM mt5_accounts a "
            "JOIN users u ON u.id = a.user_id AND u.blocked = 0 "
            "JOIN licenses l ON l.user_id = u.id AND l.active = 1 "
            "AND l.ends_at > datetime('now') "
            "WHERE a.bot_enabled = 1").fetchall()
        out = []
        for r in rows:
            try:
                pw = decrypt_password(r["password_enc"])
            except ValueError:
                continue
            out.append({"user_id": r["user_id"], "email": r["email"],
                        "mt5_server": r["server"], "mt5_login": r["login"],
                        "mt5_password": pw})
        return {"customers": out}
    finally:
        conn.close()


@app.post("/api/engine/report")
async def engine_report(request: Request,
                        x_engine_key: str | None = Header(default=None)):
    """Engine heartbeat + closed-trade reports."""
    _engine_auth(x_engine_key)
    body = await request.json()
    login = str(body.get("login") or "")
    status = (body.get("status") or "offline")[:40]
    trades = body.get("trades") or []
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT id, user_id FROM mt5_accounts WHERE login = ?",
            (login,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Unknown MT5 login.")
        conn.execute(
            "UPDATE mt5_accounts SET status = ?, last_heartbeat = "
            "datetime('now') WHERE id = ?",
            (status, row["id"]))
        for t in trades:
            conn.execute(
                "INSERT INTO trades(user_id, mt5_login, ticket, symbol, "
                "direction, lot, open_price, close_price, profit, opened_at, "
                "closed_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(mt5_login, ticket) DO UPDATE SET "
                "close_price = excluded.close_price, "
                "profit = excluded.profit, closed_at = excluded.closed_at",
                (row["user_id"], login, str(t.get("ticket", "")),
                 t.get("symbol", "XAUUSD"), t.get("direction", ""),
                 float(t.get("lot", 0.01)), float(t.get("open_price", 0)),
                 float(t.get("close_price", 0)), float(t.get("profit", 0)),
                 t.get("opened_at", ""), t.get("closed_at", "")))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()
