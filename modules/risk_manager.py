from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from config import (
    RISK_POLL_INTERVAL_SECONDS,
    STOP_LOSS_PCT,
    TIME_STOP_MINUTES,
    TIME_STOP_MIN_MULTIPLIER,
    TP1_MULTIPLIER,
    TP1_SELL_PCT,
    TP2_MULTIPLIER,
    TP2_SELL_PCT,
    TP3_MULTIPLIER,
    TP3_SELL_PCT,
    TRAILING_ACTIVATION_MULTIPLIER,
    TRAILING_STOP_PCT,
)
from modules.alerter import Alerter, TradeAlert
from modules.utils import format_duration

if TYPE_CHECKING:
    from modules.executor import BuyResult, Executor

logger = logging.getLogger("solana-bot.risk_manager")

POSITIONS_FILE = "positions.json"


def _save_positions(positions: dict) -> None:
    try:
        data = {}
        for pid, p in positions.items():
            if p.closed:
                continue
            data[pid] = {
                "position_id": p.position_id,
                "mint": p.mint,
                "symbol": p.symbol,
                "entry_price_usd": p.entry_price_usd,
                "entry_time": p.entry_time.isoformat(),
                "initial_tokens": p.initial_tokens,
                "remaining_tokens": p.remaining_tokens,
                "initial_sol": p.initial_sol,
                "reason": p.reason,
                "score_breakdown": p.score_breakdown,
                "decimals": p.decimals,
                "peak_multiplier": p.peak_multiplier,
                "trailing_active": p.trailing_active,
                "trailing_peak_multiplier": p.trailing_peak_multiplier,
                "tp1_hit": p.tp1_hit,
                "tp2_hit": p.tp2_hit,
                "tp3_hit": p.tp3_hit,
                "total_sol_received": p.total_sol_received,
                "partial_exits": [
                    {k: str(v) if isinstance(v, datetime) else v for k, v in e.items()}
                    for e in p.partial_exits
                ],
            }
        with open(POSITIONS_FILE, "w") as f:
            json.dump(data, f)
    except Exception as exc:
        logger.warning("Failed to save positions: %s", exc)


def _load_positions() -> dict:
    if not os.path.exists(POSITIONS_FILE):
        return {}
    try:
        with open(POSITIONS_FILE) as f:
            data = json.load(f)
        positions = {}
        for pid, d in data.items():
            p = Position(
                position_id=d["position_id"],
                mint=d["mint"],
                symbol=d["symbol"],
                entry_price_usd=d["entry_price_usd"],
                entry_time=datetime.fromisoformat(d["entry_time"]),
                initial_tokens=d["initial_tokens"],
                remaining_tokens=d["remaining_tokens"],
                initial_sol=d["initial_sol"],
                reason=d["reason"],
                score_breakdown=d.get("score_breakdown"),
                decimals=d.get("decimals", 6),
                peak_multiplier=d.get("peak_multiplier", 1.0),
                trailing_active=d.get("trailing_active", False),
                trailing_peak_multiplier=d.get("trailing_peak_multiplier", 1.0),
                tp1_hit=d.get("tp1_hit", False),
                tp2_hit=d.get("tp2_hit", False),
                tp3_hit=d.get("tp3_hit", False),
                total_sol_received=d.get("total_sol_received", 0.0),
            )
            positions[pid] = p
        logger.info("Loaded %d open position(s) from disk", len(positions))
        return positions
    except Exception as exc:
        logger.warning("Failed to load positions from disk: %s", exc)
        return {}


@dataclass
class Position:
    position_id: str
    mint: str
    symbol: str
    entry_price_usd: float
    entry_time: datetime
    initial_tokens: float
    remaining_tokens: float
    initial_sol: float
    reason: str
    score_breakdown: dict[str, Any] | None = None
    decimals: int = 6
    peak_multiplier: float = 1.0
    trailing_active: bool = False
    trailing_peak_multiplier: float = 1.0
    tp1_hit: bool = False
    tp2_hit: bool = False
    tp3_hit: bool = False
    closed: bool = False
    total_sol_received: float = 0.0
    partial_exits: list[dict[str, Any]] = field(default_factory=list)
    price_miss_count: int = 0  # consecutive price fetch failures


class RiskManager:
    def __init__(self, executor: "Executor", alerter: Alerter) -> None:
        self.executor = executor
        self.alerter = alerter
        self.positions: dict[str, Position] = _load_positions()
        self._running = False

    async def open_position(self, buy: "BuyResult") -> None:
        if not buy.success or buy.tokens_received <= 0:
            return

        position = Position(
            position_id=buy.position_id,
            mint=buy.mint,
            symbol=buy.symbol,
            entry_price_usd=buy.entry_price_usd,
            entry_time=datetime.now(timezone.utc),
            initial_tokens=buy.tokens_received,
            remaining_tokens=buy.tokens_received,
            initial_sol=buy.amount_sol,
            reason=buy.reason,
            score_breakdown=buy.score_breakdown,
        )
        self.positions[buy.position_id] = position
        _save_positions(self.positions)
        logger.info(
            "Position opened — %s | %.4f tokens @ $%.8f | monitoring started",
            buy.symbol,
            buy.tokens_received,
            buy.entry_price_usd,
        )

    def _current_multiplier(self, position: Position, current_price: float) -> float:
        if position.entry_price_usd <= 0:
            return 1.0
        return current_price / position.entry_price_usd

    async def _sell_partial(
        self,
        position: Position,
        sell_pct_of_remaining: float,
        exit_reason: str,
    ) -> float:
        if position.remaining_tokens <= 0:
            return 0.0

        tokens_to_sell = position.remaining_tokens * (sell_pct_of_remaining / 100.0)
        if tokens_to_sell <= 0:
            return 0.0

        sell_result = await self.executor.sell_token(
            mint=position.mint,
            amount_tokens=tokens_to_sell,
            decimals=position.decimals,
            symbol=position.symbol,
            sell_pct=sell_pct_of_remaining,
        )

        if sell_result.success:
            position.remaining_tokens -= tokens_to_sell
            position.total_sol_received += sell_result.sol_received
            position.partial_exits.append(
                {
                    "reason": exit_reason,
                    "tokens": tokens_to_sell,
                    "sol": sell_result.sol_received,
                    "time": datetime.now(timezone.utc),
                }
            )
            _save_positions(self.positions)
            logger.info(
                "Partial sell — %s | %s | %.2f%% | %.4f SOL",
                position.symbol,
                exit_reason,
                sell_pct_of_remaining,
                sell_result.sol_received,
            )
            return sell_result.sol_received
        return 0.0

    async def _close_position(self, position: Position, exit_reason: str, exit_price: float) -> None:
        if position.closed:
            return

        if position.remaining_tokens > 0:
            await self._sell_partial(position, 100.0, exit_reason)

        position.closed = True
        _save_positions(self.positions)
        exit_time = datetime.now(timezone.utc)
        pnl_sol = position.total_sol_received - position.initial_sol
        sol_price = await self.executor.get_sol_price_usd()
        pnl_usd = pnl_sol * sol_price

        alert = TradeAlert(
            token_mint=position.mint,
            token_symbol=position.symbol,
            reason=position.reason,
            entry_price=position.entry_price_usd,
            exit_price=exit_price,
            exit_reason=exit_reason,
            entry_time=position.entry_time,
            exit_time=exit_time,
            pnl_sol=pnl_sol,
            pnl_usd=pnl_usd,
            peak_multiplier=position.peak_multiplier,
            score_breakdown=position.score_breakdown,
            sol_price_usd=sol_price,
        )
        await self.alerter.send_trade_alert(alert)

        hold_time = (exit_time - position.entry_time).total_seconds()
        logger.info(
            "Position closed — %s | %s | hold %s | PnL %.4f SOL | peak %.2fx",
            position.symbol,
            exit_reason,
            format_duration(hold_time),
            pnl_sol,
            position.peak_multiplier,
        )

    async def _evaluate_position(self, position: Position) -> None:
        if position.closed:
            return

        current_price = await self.executor.get_token_price_usd(position.mint)
        if not current_price or current_price <= 0:
            position.price_miss_count += 1
            # Force-sell after 10 consecutive price failures (~50s) — token is likely dead/illiquid
            if position.price_miss_count >= 10:
                logger.warning(
                    "Force-selling %s — price unavailable for %d consecutive checks",
                    position.symbol, position.price_miss_count,
                )
                await self._close_position(position, "no_price_data", position.entry_price_usd)
            return
        position.price_miss_count = 0  # reset on successful price fetch

        multiplier = self._current_multiplier(position, current_price)
        position.peak_multiplier = max(position.peak_multiplier, multiplier)

        pnl_pct = (multiplier - 1.0) * 100.0
        hold_minutes = (datetime.now(timezone.utc) - position.entry_time).total_seconds() / 60.0

        # Full stop loss at -25%
        if pnl_pct <= STOP_LOSS_PCT:
            await self._close_position(position, "SL", current_price)
            return

        # Activate trailing stop after 3x
        if multiplier >= TRAILING_ACTIVATION_MULTIPLIER:
            position.trailing_active = True
            position.trailing_peak_multiplier = max(position.trailing_peak_multiplier, multiplier)

        # Trailing stop: -18% from peak after 3x
        if position.trailing_active:
            drawdown_from_peak = (
                (multiplier - position.trailing_peak_multiplier) / position.trailing_peak_multiplier
            ) * 100.0
            if drawdown_from_peak <= TRAILING_STOP_PCT:
                await self._close_position(position, "trailing", current_price)
                return

        # Time stop: 45 min if under 1.5x
        if hold_minutes >= TIME_STOP_MINUTES and multiplier < TIME_STOP_MIN_MULTIPLIER:
            await self._close_position(position, "time_stop", current_price)
            return

        # Take profit levels
        if multiplier >= TP1_MULTIPLIER and not position.tp1_hit:
            position.tp1_hit = True
            await self._sell_partial(position, TP1_SELL_PCT, "TP1")

        if multiplier >= TP2_MULTIPLIER and not position.tp2_hit:
            position.tp2_hit = True
            await self._sell_partial(position, TP2_SELL_PCT, "TP2")

        if multiplier >= TP3_MULTIPLIER and not position.tp3_hit:
            position.tp3_hit = True
            await self._sell_partial(position, TP3_SELL_PCT, "TP3")
            if position.remaining_tokens <= 0:
                await self._close_position(position, "TP3", current_price)

    async def run(self) -> None:
        self._running = True
        logger.info("Risk manager started — polling every %ds", RISK_POLL_INTERVAL_SECONDS)

        while self._running:
            try:
                open_positions = [p for p in self.positions.values() if not p.closed]
                if open_positions:
                    logger.debug("Monitoring %d open position(s)", len(open_positions))
                    await asyncio.gather(
                        *[self._evaluate_position(p) for p in open_positions],
                        return_exceptions=True,
                    )
            except Exception as exc:
                logger.error("Risk manager loop error: %s", exc)

            await asyncio.sleep(RISK_POLL_INTERVAL_SECONDS)

    def stop(self) -> None:
        self._running = False
