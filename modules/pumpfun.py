"""Pump.fun discovery — bonding curve, graduating, and graduated tokens."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import aiohttp

from config import (
    PUMP_BONDING_SOL_TARGET,
    PUMP_INITIAL_VIRTUAL_SOL,
    SCAN_PUMPFUN_BONDING_MIN_PCT,
    SCAN_PUMPFUN_MAX_AGE_HOURS,
    SCAN_PUMPFUN_MIN_USD_MCAP,
)
from modules.utils import fetch_json

logger = logging.getLogger("solana-bot.pumpfun")

PUMP_API_BASE = "https://frontend-api-v3.pump.fun"
PUMP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://pump.fun/",
    "Origin": "https://pump.fun",
}

# On-chain program IDs (Jupiter routes through these for pump tokens)
PUMP_BONDING_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMP_AMM_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
PUMP_PROGRAMS = {PUMP_BONDING_PROGRAM, PUMP_AMM_PROGRAM}


def bonding_progress(coin: dict[str, Any]) -> float:
    """Estimate bonding curve completion % from virtual SOL reserves."""
    if coin.get("complete"):
        return 100.0
    vsol = float(coin.get("virtual_sol_reserves", 0) or 0) / 1e9
    # Clamp — API occasionally returns post-graduation reserves while complete=false
    vsol = min(vsol, PUMP_BONDING_SOL_TARGET)
    span = max(PUMP_BONDING_SOL_TARGET - PUMP_INITIAL_VIRTUAL_SOL, 1.0)
    return max(0.0, min(99.0, (vsol - PUMP_INITIAL_VIRTUAL_SOL) / span * 100.0))


def coin_age_hours(coin: dict[str, Any]) -> float:
    """Hours since creation or last trade — whichever is more recent."""
    now = datetime.now(timezone.utc)
    best = 999.0
    for key in ("last_trade_timestamp", "created_timestamp"):
        ts = coin.get(key)
        if not ts:
            continue
        if ts > 1_000_000_000_000:
            ts = ts / 1000
        created = datetime.fromtimestamp(ts, tz=timezone.utc)
        best = min(best, (now - created).total_seconds() / 3600.0)
    return best


def usd_market_cap(coin: dict[str, Any]) -> float:
    return float(coin.get("usd_market_cap") or coin.get("market_cap") or 0)


def synthetic_pair_from_coin(coin: dict[str, Any]) -> dict[str, Any]:
    """Build a DexScreener-like pair dict for scoring from Pump.fun metadata."""
    mcap = usd_market_cap(coin)
    progress = bonding_progress(coin)
    complete = bool(coin.get("complete"))
    age_h = coin_age_hours(coin)
    # Estimate liquidity from curve state
    vsol = float(coin.get("virtual_sol_reserves", 0) or 0) / 1e9
    liq_usd = mcap * 0.15 if complete else max(vsol * 150, mcap * 0.05)

    created_ms = coin.get("created_timestamp") or 0
    if created_ms > 1_000_000_000_000:
        created_ms = int(created_ms)
    else:
        created_ms = int(created_ms * 1000) if created_ms else 0

    socials = []
    if coin.get("twitter"):
        socials.append({"type": "twitter", "url": coin["twitter"]})
    if coin.get("telegram"):
        socials.append({"type": "telegram", "url": coin["telegram"]})

    return {
        "baseToken": {"symbol": coin.get("symbol", "UNKNOWN"), "address": coin.get("mint", "")},
        "dexId": "pumpswap" if complete else "pump.fun",
        "liquidity": {"usd": liq_usd},
        "marketCap": mcap,
        "fdv": mcap,
        "volume": {"h24": mcap * 0.3},
        "priceChange": {"m5": progress * 0.1, "h1": progress * 0.2},
        "pairCreatedAt": created_ms,
        "info": {"websites": [coin["website"]] if coin.get("website") else [], "socials": socials},
        "pumpfun": {
            "complete": complete,
            "bonding_progress": round(progress, 1),
            "virtual_sol": round(vsol, 2),
            "reply_count": coin.get("reply_count", 0),
            "king_of_hill": bool(coin.get("king_of_the_hill_timestamp")),
        },
    }


async def _fetch_coins(
    session: aiohttp.ClientSession,
    path: str,
    *,
    params: dict[str, str | int | bool] | None = None,
    label: str = "Pump.fun",
) -> list[dict[str, Any]]:
    try:
        data = await fetch_json(
            session, "GET", f"{PUMP_API_BASE}{path}",
            params=params, headers=PUMP_HEADERS, label=label,
        )
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("coins", []) or data.get("data", []) or []
        return []
    except Exception as exc:
        logger.warning("%s failed: %s", label, exc)
        return []


async def fetch_live(session: aiohttp.ClientSession, limit: int = 25) -> list[dict[str, Any]]:
    return await _fetch_coins(
        session, "/coins/currently-live",
        params={"limit": limit, "includeNsfw": "false"},
        label="Pump.fun live",
    )


async def fetch_latest(session: aiohttp.ClientSession, limit: int = 30) -> list[dict[str, Any]]:
    return await _fetch_coins(
        session, "/coins",
        params={
            "limit": limit, "offset": 0,
            "sort": "created_timestamp", "order": "DESC",
            "includeNsfw": "false",
        },
        label="Pump.fun latest",
    )


async def fetch_graduated_recent(session: aiohttp.ClientSession, limit: int = 20) -> list[dict[str, Any]]:
    """Freshly graduated tokens (complete=true, active in last few hours)."""
    live = await fetch_live(session, limit=40)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    for c in live:
        mint = c.get("mint", "")
        if not mint or mint in seen or not c.get("complete"):
            continue
        seen.add(mint)
        mcap = usd_market_cap(c)
        age = coin_age_hours(c)
        if mcap < SCAN_PUMPFUN_MIN_USD_MCAP:
            continue
        if age > SCAN_PUMPFUN_MAX_AGE_HOURS:
            continue
        out.append(c)
        if len(out) >= limit:
            break

    if out:
        logger.info("Pump.fun graduated recent: %d token(s)", len(out))
    return out


async def fetch_graduating(session: aiohttp.ClientSession, limit: int = 15) -> list[dict[str, Any]]:
    """Tokens near bonding curve completion (not yet migrated)."""
    live = await fetch_live(session, limit=40)
    latest = await fetch_latest(session, limit=40)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    for coin in live + latest:
        mint = coin.get("mint", "")
        if not mint or mint in seen or coin.get("complete"):
            continue
        seen.add(mint)
        progress = bonding_progress(coin)
        mcap = usd_market_cap(coin)
        age = coin_age_hours(coin)
        if progress < SCAN_PUMPFUN_BONDING_MIN_PCT:
            continue
        if mcap < SCAN_PUMPFUN_MIN_USD_MCAP * 0.5:
            continue
        if age > SCAN_PUMPFUN_MAX_AGE_HOURS:
            continue
        out.append(coin)
        if len(out) >= limit:
            break

    if out:
        logger.info(
            "Pump.fun graduating: %d token(s) (>=%.0f%% curve)",
            len(out), SCAN_PUMPFUN_BONDING_MIN_PCT,
        )
    return out


async def fetch_coin(session: aiohttp.ClientSession, mint: str) -> dict[str, Any] | None:
    try:
        return await fetch_json(
            session, "GET", f"{PUMP_API_BASE}/coins/{mint}",
            headers=PUMP_HEADERS, label=f"Pump.fun {mint[:8]}",
        )
    except Exception:
        return None
