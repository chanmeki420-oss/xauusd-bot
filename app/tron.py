"""TRC20 USDT deposit verification via the TronGrid API (read-only, no key needed
for the free tier; TRONGRID_API_KEY only raises rate limits).

Verification steps for a submitted TXID:
  1. The transaction exists and its contractRet is SUCCESS.
  2. Its decoded events contain a Transfer on the official USDT TRC20 contract
     (TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t). Events only exist for confirmed txs.
  3. The Transfer's `to` equals our DEPOSIT_TRC20_ADDRESS.
  4. The transferred amount (6 decimals) covers the expected amount and the
     minimum deposit.
  5. The TXID was never used before (UNIQUE constraint in DB).

Addresses in TronGrid events are 0x-prefixed hex; they are converted to
TRON base58check form for comparison.
"""
from __future__ import annotations

import hashlib
import time

import httpx

TRONGRID_BASE = "https://api.trongrid.io"
USDT_CONTRACT_BASE58 = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
USDT_DECIMALS = 6

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


class TronVerifyError(Exception):
    """Raised when a deposit TXID fails verification (message is user-facing)."""


def _b58encode(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    out = ""
    while n > 0:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    # leading zero bytes -> '1's
    pad = len(b) - len(b.lstrip(b"\x00"))
    return "1" * pad + out


def hex_to_tron_address(h: str) -> str:
    """'0x'-style 20-byte hex -> TRON base58check address (starts with 'T')."""
    h = h.lower().removeprefix("0x")
    raw = bytes.fromhex(h)
    if len(raw) != 20:
        raise ValueError(f"bad address hex length: {h}")
    payload = b"\x41" + raw
    check = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    return _b58encode(payload + check)


def tron_address_valid(addr: str) -> bool:
    """Basic structural check of a TRON base58check address."""
    if not addr or len(addr) != 34 or not addr.startswith("T"):
        return False
    try:
        raw = addr.encode()
        n = 0
        for ch in raw:
            n = n * 58 + _B58.index(chr(ch))
        full = n.to_bytes(25, "big")
        payload, check = full[:-4], full[-4:]
        return hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4] == check
    except Exception:
        return False


def _headers(api_key: str | None) -> dict:
    return {"TRON-PRO-API-KEY": api_key} if api_key else {}


async def _get_tx(txid: str, api_key: str | None) -> dict:
    """Fetch raw transaction; confirms it exists and succeeded on-chain."""
    try:
        async with httpx.AsyncClient(timeout=25, trust_env=False) as c:
            r = await c.post(
                f"{TRONGRID_BASE}/wallet/gettransactionbyid",
                json={"value": txid},
                headers=_headers(api_key),
            )
    except httpx.HTTPError:
        raise TronVerifyError(
            "Could not reach the TRON network. Try again shortly.")
    if r.status_code != 200:
        raise TronVerifyError("Could not reach the TRON network. Try again shortly.")
    data = r.json()
    if not data or "raw_data" not in data:
        raise TronVerifyError("Transaction not found on the TRON network.")
    rets = data.get("ret") or []
    if not rets or rets[0].get("contractRet") != "SUCCESS":
        raise TronVerifyError("Transaction exists but did not succeed on-chain.")
    return data


async def _get_events(txid: str, api_key: str | None) -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=25, trust_env=False) as c:
            r = await c.get(
                f"{TRONGRID_BASE}/v1/transactions/{txid}/events",
                headers=_headers(api_key),
            )
    except httpx.HTTPError:
        raise TronVerifyError(
            "Could not reach the TRON network. Try again shortly.")
    if r.status_code != 200:
        raise TronVerifyError("Transaction is not confirmed yet. Wait a minute and retry.")
    return r.json().get("data", [])


def extract_usdt_transfer(events: list[dict], deposit_address: str) -> dict | None:
    """Pure parsing helper (unit-testable): find a USDT Transfer event paying
    `deposit_address`. Returns {to, from, value_raw} or None."""
    want = deposit_address.strip()
    for e in events:
        if e.get("event_name") != "Transfer":
            continue
        if e.get("contract_address") != USDT_CONTRACT_BASE58:
            continue
        res = e.get("result") or {}
        try:
            to_addr = hex_to_tron_address(res["to"])
        except (KeyError, ValueError):
            continue
        if to_addr != want:
            continue
        try:
            value = int(res["value"])
        except (KeyError, ValueError, TypeError):
            continue
        return {
            "to": to_addr,
            "from": hex_to_tron_address(res["from"]) if res.get("from") else "",
            "value_raw": value,
        }
    return None


async def verify_usdt_deposit(
    txid: str,
    expected_amount: float,
    deposit_address: str,
    min_deposit: float,
    api_key: str | None = None,
) -> dict:
    """Full verification. Returns {txid, amount, from_address, to_address}."""
    txid = txid.strip().lower().removeprefix("0x")
    if len(txid) != 64 or any(c not in "0123456789abcdef" for c in txid):
        raise TronVerifyError("That doesn't look like a valid transaction hash.")
    if expected_amount <= 0:
        raise TronVerifyError("Expected amount must be positive.")

    await _get_tx(txid, api_key)  # exists + SUCCESS
    # Small delay: a tx can be fetchable a moment before events are indexed.
    time.sleep(1)
    events = await _get_events(txid, api_key)
    found = extract_usdt_transfer(events, deposit_address)
    if found is None:
        raise TronVerifyError(
            "No confirmed USDT (TRC20) transfer to our deposit address "
            "was found in that transaction."
        )
    amount = found["value_raw"] / 10**USDT_DECIMALS
    if amount + 1e-9 < expected_amount:
        raise TronVerifyError(
            f"Transaction moved {amount:.6f} USDT, less than the {expected_amount:.2f} "
            "USDT you entered. Enter the exact amount received."
        )
    if amount + 1e-9 < min_deposit:
        raise TronVerifyError(
            f"Deposits below {min_deposit:.2f} USDT are not accepted."
        )
    return {
        "txid": txid,
        "amount": amount,
        "from_address": found["from"],
        "to_address": found["to"],
    }
