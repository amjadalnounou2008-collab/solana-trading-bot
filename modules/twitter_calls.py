"""Persist Twitter caller alerts and compute hit-rate stats."""
from __future__ import annotations

import json
import logging
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("solana-bot.twitter_calls")

CALLS_FILE = Path("twitter_calls.json")


@dataclass
class CallRecord:
    tweet_id: str
    caller: str
    symbol: str
    mint: str
    called_at: str
    tweet_text: str
    entry_mcap_usd: float = 0.0
    entry_price_usd: float = 0.0
    peak_multiplier: float = 1.0
    last_multiplier: float = 1.0
    bonding_progress: float = 0.0
    rugcheck_ok: bool = True
    source: str = "twitter"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CallRecord":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class CallerStats:
    caller: str
    calls: int
    hits: int
    hit_rate_pct: float
    median_return_x: float
    best_return_x: float
    best_symbol: str


class CallLedger:
    def __init__(self) -> None:
        self.calls: list[CallRecord] = []
        self._load()

    def _load(self) -> None:
        if not CALLS_FILE.exists():
            return
        try:
            raw = json.loads(CALLS_FILE.read_text())
            self.calls = [CallRecord.from_dict(c) for c in raw.get("calls", [])]
            logger.info("Loaded %d Twitter call(s) from ledger", len(self.calls))
        except Exception as exc:
            logger.warning("Twitter call ledger load failed: %s", exc)

    def save(self) -> None:
        try:
            data = {"calls": [c.to_dict() for c in self.calls[-500:]]}
            CALLS_FILE.write_text(json.dumps(data, indent=2))
        except Exception as exc:
            logger.warning("Twitter call ledger save failed: %s", exc)

    def seen_tweet(self, tweet_id: str) -> bool:
        return any(c.tweet_id == tweet_id for c in self.calls)

    def add(self, record: CallRecord) -> None:
        if self.seen_tweet(record.tweet_id):
            return
        self.calls.append(record)
        self.save()

    def update_price(self, mint: str, multiplier: float) -> None:
        changed = False
        for c in self.calls:
            if c.mint != mint:
                continue
            c.last_multiplier = multiplier
            if multiplier > c.peak_multiplier:
                c.peak_multiplier = multiplier
                changed = True
        if changed:
            self.save()

    def recent_calls(self, days: int = 30) -> list[CallRecord]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        out: list[CallRecord] = []
        for c in self.calls:
            try:
                ts = datetime.fromisoformat(c.called_at)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts >= cutoff:
                    out.append(c)
            except Exception:
                continue
        return out

    def caller_leaderboard(self, days: int = 30, min_calls: int = 2) -> list[CallerStats]:
        recent = self.recent_calls(days)
        by_caller: dict[str, list[CallRecord]] = {}
        for c in recent:
            by_caller.setdefault(c.caller.lower(), []).append(c)

        stats: list[CallerStats] = []
        for caller, calls in by_caller.items():
            if len(calls) < min_calls:
                continue
            mults = [max(c.peak_multiplier, 1.0) for c in calls]
            hits = sum(1 for m in mults if m >= 2.0)
            best_idx = max(range(len(calls)), key=lambda i: mults[i])
            stats.append(CallerStats(
                caller=calls[0].caller,
                calls=len(calls),
                hits=hits,
                hit_rate_pct=round(hits / len(calls) * 100, 1),
                median_return_x=round(statistics.median(mults), 2),
                best_return_x=round(max(mults), 2),
                best_symbol=calls[best_idx].symbol,
            ))
        stats.sort(key=lambda s: (s.hit_rate_pct, s.median_return_x), reverse=True)
        return stats

    def top_calls(self, days: int = 30, limit: int = 10) -> list[CallRecord]:
        recent = self.recent_calls(days)
        return sorted(recent, key=lambda c: c.peak_multiplier, reverse=True)[:limit]

    def summary(self, days: int = 30) -> dict[str, Any]:
        recent = self.recent_calls(days)
        if not recent:
            return {"calls": 0, "hit_rate_pct": 0.0, "median_return_x": 0.0, "avg_return_x": 0.0}
        mults = [max(c.peak_multiplier, 1.0) for c in recent]
        hits = sum(1 for m in mults if m >= 2.0)
        return {
            "calls": len(recent),
            "hit_rate_pct": round(hits / len(recent) * 100, 1),
            "median_return_x": round(statistics.median(mults), 2),
            "avg_return_x": round(statistics.mean(mults), 2),
        }
