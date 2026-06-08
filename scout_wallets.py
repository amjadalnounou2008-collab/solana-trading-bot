#!/usr/bin/env python3
"""Find smart wallets on GMGN (milkybids strategy). Run locally."""
from __future__ import annotations

import asyncio
import logging
import sys

import aiohttp

from modules.utils import setup_logging
from modules.wallet_scout import (
    DEFAULT_COPY_SOL,
    MAX_TXNS_7D,
    MIN_PNL_30D,
    MIN_WIN_RATE,
    fetch_gmgn_wallets,
    filter_wallets,
    format_trader_configs,
)

setup_logging()
logger = logging.getLogger("scout_wallets")


async def main() -> None:
    print("=" * 60)
    print("  Wallet Scout — milkybids filters")
    print(f"  Win rate >= {MIN_WIN_RATE:.0%}")
    print(f"  30d PnL   >= {MIN_PNL_30D:.0%}")
    print(f"  7d txns   <  {MAX_TXNS_7D}")
    print("=" * 60)

    async with aiohttp.ClientSession() as session:
        wallets = await fetch_gmgn_wallets(session, period="7d", limit=100)

    if not wallets:
        print("\nGMGN API blocked or unavailable from this network.")
        print("\nDo this manually on gmgn.ai:")
        print("  1. CopyTrade → Rank → sort by 7d PnL")
        print("  2. Pick wallets with 60%+ win rate, 50%+ 30d PnL, <200 weekly txns")
        print("  3. Verify on nova.trade Top Traders tab")
        print("  4. Paste addresses into config.py TRADERS list")
        sys.exit(1)

    qualified = filter_wallets(wallets)
    print(f"\nFetched {len(wallets)} wallets → {len(qualified)} pass filters\n")

    if not qualified:
        print("No wallets passed filters. Try loosening criteria in modules/wallet_scout.py")
        sys.exit(0)

    print(f"{'NAME':<16} {'WIN%':>6} {'30d PnL':>8} {'7d txns':>8}  ADDRESS")
    print("-" * 70)
    for w in qualified[:20]:
        print(
            f"{w['name']:<16} {w['winrate']:>5.0%} {w['pnl_30d']:>7.0%} "
            f"{w['txns_7d']:>8}  {w['address']}"
        )

    print("\n--- Paste into config.py TRADERS ---\n")
    print(format_trader_configs(qualified[:10], DEFAULT_COPY_SOL))
    print(f"\nSuggested copy size: {DEFAULT_COPY_SOL} SOL per trade")


if __name__ == "__main__":
    asyncio.run(main())
