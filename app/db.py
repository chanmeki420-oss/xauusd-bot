"""SQLite storage for the XAUUSD Bot panel.

Tables:
  users         — Gmail accounts (Google OAuth)
  mt5_accounts  — one row per customer MT5 account; password is Fernet-
                  encrypted, never stored plain
  licenses      — $20/month subscriptions (30 days from verified payment)
  payments      — every submitted TXID; txid UNIQUE so one payment can
                  never activate two licenses
  trades        — trade reports sent in by the Windows engine
  settings      — owner-editable values (price, deposit address)
"""
from __future__ import annotations

import sqlite3

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT UNIQUE NOT NULL,
  name TEXT DEFAULT '',
  picture TEXT DEFAULT '',
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS mt5_accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  broker TEXT DEFAULT '',
  server TEXT NOT NULL,
  login TEXT NOT NULL,
  password_enc TEXT NOT NULL,
  bot_enabled INTEGER NOT NULL DEFAULT 0,
  status TEXT DEFAULT 'offline',
  last_heartbeat TEXT DEFAULT '',
  created_at TEXT DEFAULT (datetime('now')),
  UNIQUE(user_id, server, login)
);
CREATE TABLE IF NOT EXISTS licenses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  starts_at TEXT NOT NULL,
  ends_at TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS payments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  txid TEXT UNIQUE NOT NULL,
  amount REAL NOT NULL,
  from_address TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'verified',
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  mt5_login TEXT NOT NULL,
  ticket TEXT NOT NULL,
  symbol TEXT DEFAULT 'XAUUSD',
  direction TEXT DEFAULT '',
  lot REAL DEFAULT 0.01,
  open_price REAL DEFAULT 0,
  close_price REAL DEFAULT 0,
  profit REAL DEFAULT 0,
  opened_at TEXT DEFAULT '',
  closed_at TEXT DEFAULT '',
  UNIQUE(mt5_login, ticket)
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        # Migration: block flag for admin user control (added 2026-11-16).
        try:
            conn.execute(
                "ALTER TABLE users ADD COLUMN blocked INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass  # column already exists
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES('price_usd', ?)",
            (str(config.PRICE_MONTHLY_USD),))
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES('deposit_address', ?)",
            (config.DEPOSIT_TRC20_ADDRESS,))
        conn.commit()
    finally:
        conn.close()


def get_setting(key: str, default: str = "") -> str:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def set_setting(key: str, value: str) -> None:
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value))
        conn.commit()
    finally:
        conn.close()
