"""
Find copy-trade wallets using milkybids-style filters:
  - Win rate 60%+
  - 30-day PnL 50%+
  - 7-day txns < 200

Run locally:  python3 scout_wallets.py

GMGN may block datacenter IPs — if the API fails, use gmgn.ai manually
and paste addresses into config.py TRADERS list.
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

from modules.utils import fetch_json

logger = logging.getLogger("solana-bot.wallet_scout")

GMGN_RANK_URL = "https://gmgn.ai/defi/quotation/v1/rank/sol/wallets/{period}"
GMGN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://gmgn.ai/",
    "Origin": "https://gmgn.ai",
}

# milkybids filters
MIN_WIN_RATE = 0.60
MIN_PNL_30D = 0.50       # 50%+ return over 30 days
MAX_TXNS_7D = 200
DEFAULT_COPY_SOL = 0.05


async def fetch_gmgn_wallets(
    session: aiohttp.ClientSession,
    period: str = "7d",
    orderby: str = "pnl_7d",
    limit: int = 50,
) -> list[dict[str, Any]]:
    url = GMGN_RANK_URL.format(period=period)
    try:
        data = await fetch_json(
            session, "GET", url,
            params={"orderby": orderby, "direction": "desc", "limit": str(limit)},
            headers=GMGN_HEADERS,
            label="GMGN wallet rank",
        )
        return data.get("data", {}).get("rank", []) or data.get("data", []) or []
    except Exception as exc:
        logger.warning("GMGN wallet rank failed: %s", exc)
        return []


def _pct(value: Any) -> float:
    """Normalize GMGN fields that may be 0-1 or 0-100."""
    v = float(value or 0)
    return v / 100.0 if v > 1 else v


def filter_wallets(wallets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    passed = []
    for w in wallets:
        addr = w.get("address") or w.get("wallet_address", "")
        if not addr:
            continue

        winrate = _pct(w.get("winrate_30d") or w.get("winrate_7d") or w.get("winrate", 0))
        pnl_30d = _pct(w.get("pnl_30d") or w.get("pnl_30d_rate", 0))
        txns_7d = int(w.get("txs_7d") or w.get("txns_7d") or w.get("tx_count_7d", 0))

        if winrate < MIN_WIN_RATE:
            continue
        if pnl_30d < MIN_PNL_30D:
            continue
        if txns_7d >= MAX_TXNS_7D:
            continue

        passed.append({
            "address": addr,
            "name": w.get("name") or w.get("twitter_username") or addr[:8],
            "winrate": winrate,
            "pnl_30d": pnl_30d,
            "txns_7d": txns_7d,
            "pnl_7d": _pct(w.get("pnl_7d", 0)),
            "realized_profit_7d": float(w.get("realized_profit_7d", 0) or 0),
        })
    return passed


def format_trader_configs(wallets: list[dict[str, Any]], copy_sol: float = DEFAULT_COPY_SOL) -> str:
    lines = []
    for w in wallets:
        name = str(w["name"]).replace('"', "'")[:20]
        lines.append(
            f'    TraderConfig("{name}", "@gmgn", "{w["address"]}", {copy_sol}),'
        )
    return "\n".join(lines)
