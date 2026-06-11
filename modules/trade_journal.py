"""Persist every trade + running PnL so nothing is lost between restarts."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("solana-bot.journal")

LOG_FILE = Path("trade_log.jsonl")
STATS_FILE = Path("trade_stats.json")
RESET_TRADE_STATS = os.getenv("RESET_TRADE_STATS", "false").lower() in ("true", "1", "yes")


@dataclass
class TradeStats:
    lifetime_pnl_usd: float = 0.0
    lifetime_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_spent_usd: float = 0.0
    total_received_usd: float = 0.0
    last_updated: str = ""

    def save(self) -> None:
        self.last_updated = datetime.now(timezone.utc).isoformat()
        try:
            STATS_FILE.write_text(json.dumps({
                "lifetime_pnl_usd": self.lifetime_pnl_usd,
                "lifetime_trades": self.lifetime_trades,
                "wins": self.wins,
                "losses": self.losses,
                "total_spent_usd": self.total_spent_usd,
                "total_received_usd": self.total_received_usd,
                "last_updated": self.last_updated,
            }, indent=2))
        except Exception as exc:
            logger.warning("Could not save trade stats: %s", exc)

    @classmethod
    def load(cls) -> "TradeStats":
        if RESET_TRADE_STATS:
            reset_trade_stats()
            return cls()
        try:
            if STATS_FILE.exists():
                d = json.loads(STATS_FILE.read_text())
                return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})
        except Exception as exc:
            logger.warning("Could not load trade stats: %s", exc)
        return cls()


def reset_trade_stats(clear_log: bool = False) -> None:
    """Wipe inflated lifetime stats (one-time via RESET_TRADE_STATS=true)."""
    try:
        if STATS_FILE.exists():
            STATS_FILE.unlink()
        logger.info("Trade stats reset — lifetime PnL counters cleared")
    except Exception as exc:
        logger.warning("Could not delete trade_stats.json: %s", exc)
    if clear_log and LOG_FILE.exists():
        try:
            LOG_FILE.unlink()
            logger.info("Trade log cleared")
        except Exception as exc:
            logger.warning("Could not delete trade_log.jsonl: %s", exc)


def log_event(event: str, **data: object) -> None:
    row = {
        "time": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **data,
    }
    try:
        with LOG_FILE.open("a") as f:
            f.write(json.dumps(row, default=str) + "\n")
    except Exception as exc:
        logger.warning("Could not write trade log: %s", exc)
    logger.info("TRADE LOG — %s | %s", event, {k: v for k, v in data.items() if k != "mint"})
