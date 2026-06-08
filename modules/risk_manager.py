from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from config import (
    COPY_REBUY_COOLDOWN_HOURS,
    DUST_BALANCE_USD,
    MAX_HOLD_MINUTES,
    RISK_POLL_INTERVAL_SECONDS,
    SELL_TO_STABLE,
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

DATABASE_URL   = os.getenv("DATABASE_URL", "")
POSITIONS_FILE = "positions.json"   # fallback when no DB


# ── Helpers ──────────────────────────────────────────────────────────────────

def _pos_to_dict(p: "Position") -> dict:
    return {
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


def _dict_to_pos(d: dict) -> "Position":
    return Position(
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


# ── File-based fallback ───────────────────────────────────────────────────────

def _file_save(positions: dict) -> None:
    try:
        data = {pid: _pos_to_dict(p) for pid, p in positions.items() if not p.closed}
        with open(POSITIONS_FILE, "w") as f:
            json.dump(data, f)
    except Exception as exc:
        logger.warning("File save failed: %s", exc)


def _file_load() -> dict:
    if not os.path.exists(POSITIONS_FILE):
        return {}
    try:
        with open(POSITIONS_FILE) as f:
            data = json.load(f)
        positions = {pid: _dict_to_pos(d) for pid, d in data.items()}
        logger.info("Loaded %d position(s) from file", len(positions))
        return positions
    except Exception as exc:
        logger.warning("File load failed: %s", exc)
        return {}


# ── PostgreSQL store ──────────────────────────────────────────────────────────

class PositionStore:
    """
    Persists positions in PostgreSQL when DATABASE_URL is set,
    otherwise falls back to a local JSON file.
    Positions survive Railway restarts and redeploys either way.
    """

    def __init__(self) -> None:
        self._pool = None

    async def initialize(self) -> None:
        if not DATABASE_URL:
            logger.warning("DATABASE_URL not set — using file fallback (positions lost on redeploy)")
            return
        try:
            import asyncpg
            self._pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=3)
            async with self._pool.acquire() as conn:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS positions (
                        position_id TEXT PRIMARY KEY,
                        data        JSONB    NOT NULL,
                        closed      BOOLEAN  NOT NULL DEFAULT FALSE,
                        updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                """)
            logger.info("PostgreSQL position store ready")
        except Exception as exc:
            logger.error("PostgreSQL init failed, using file fallback: %s", exc)
            self._pool = None

    async def save(self, position: "Position") -> None:
        if self._pool is None:
            return  # file save handled by caller
        try:
            data = json.dumps(_pos_to_dict(position))
            async with self._pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO positions (position_id, data, closed, updated_at)
                    VALUES ($1, $2::jsonb, $3, NOW())
                    ON CONFLICT (position_id)
                    DO UPDATE SET data=EXCLUDED.data, closed=EXCLUDED.closed, updated_at=NOW()
                """, position.position_id, data, position.closed)
        except Exception as exc:
            logger.warning("DB save failed: %s", exc)

    async def load_all(self) -> dict:
        if self._pool is None:
            return _file_load()
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT data FROM positions WHERE closed = FALSE"
                )
            positions = {}
            for row in rows:
                d = json.loads(row["data"])
                p = _dict_to_pos(d)
                positions[p.position_id] = p
            logger.info("Loaded %d open position(s) from PostgreSQL", len(positions))
            return positions
        except Exception as exc:
            logger.warning("DB load failed, trying file: %s", exc)
            return _file_load()


# ── Data model ────────────────────────────────────────────────────────────────

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
    price_miss_count: int = 0
    sell_fail_count: int = 0


# ── Risk Manager ─────────────────────────────────────────────────────────────

class RiskManager:
    def __init__(self, executor: "Executor", alerter: Alerter) -> None:
        self.executor = executor
        self.alerter  = alerter
        self.store    = PositionStore()
        self.positions: dict[str, Position] = {}
        self._running = False
        self._loss_cooldown: dict[str, datetime] = {}  # mint → don't rebuy until expired

    def on_cooldown(self, mint: str) -> bool:
        until = self._loss_cooldown.get(mint)
        if not until:
            return False
        if datetime.now(timezone.utc) >= until:
            del self._loss_cooldown[mint]
            return False
        return True

    async def initialize(self) -> None:
        await self.store.initialize()
        self.positions = await self.store.load_all()

    async def _persist(self, position: Position) -> None:
        if self.store._pool is not None:
            await self.store.save(position)
        else:
            _file_save(self.positions)

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
            decimals=buy.decimals,
        )
        self.positions[buy.position_id] = position
        await self._persist(position)
        logger.info(
            "Position opened — %s | %.4f tokens @ $%.8f | monitoring started",
            buy.symbol, buy.tokens_received, buy.entry_price_usd,
        )

    def _current_multiplier(self, position: Position, current_price: float) -> float:
        if position.entry_price_usd <= 0:
            return 1.0
        return current_price / position.entry_price_usd

    async def _sync_wallet_balance(self, position: Position) -> None:
        """Keep tracked balance in sync with what's actually in the wallet."""
        amount, decimals = await self.executor.get_token_balance(position.mint)
        if amount > 0:
            position.remaining_tokens = amount
            position.decimals = decimals

    async def _sell_partial(self, position: Position, sell_pct: float, exit_reason: str) -> bool:
        await self._sync_wallet_balance(position)
        if position.remaining_tokens <= 0:
            return True

        tokens_to_sell = position.remaining_tokens * (sell_pct / 100.0)
        if tokens_to_sell <= 0:
            return True

        sell_result = await self.executor.sell_token(
            mint=position.mint,
            amount_tokens=tokens_to_sell,
            decimals=position.decimals,
            symbol=position.symbol,
            sell_pct=sell_pct,
        )
        if sell_result.success:
            position.sell_fail_count = 0
            if sell_result.is_dust:
                position.remaining_tokens = 0
            else:
                await self._sync_wallet_balance(position)
                if position.remaining_tokens > 0:
                    raw = int(position.remaining_tokens * (10 ** position.decimals))
                    rem_usd = await self.executor.get_sell_quote_usd(position.mint, raw)
                    if rem_usd is not None and rem_usd < DUST_BALANCE_USD:
                        logger.info("Dust cleared — %s ($%.2f left, stopping retries)",
                                      position.symbol, rem_usd)
                        position.remaining_tokens = 0
                position.total_sol_received += sell_result.sol_received
            position.partial_exits.append({
                "reason": exit_reason,
                "tokens": tokens_to_sell,
                "sol": sell_result.sol_received,
                "time": datetime.now(timezone.utc),
            })
            await self._persist(position)
            unit = sell_result.exit_label or ("stable" if SELL_TO_STABLE else "SOL")
            logger.info("Partial sell — %s | %s | %.2f%% | %.4f %s",
                        position.symbol, exit_reason, sell_pct, sell_result.sol_received, unit)
            return True

        position.sell_fail_count += 1
        await self._persist(position)
        logger.warning("Sell failed for %s (%s) — attempt %d, will retry",
                         position.symbol, exit_reason, position.sell_fail_count)
        return False

    async def _close_position(self, position: Position, exit_reason: str, exit_price: float) -> None:
        if position.closed:
            return

        if position.remaining_tokens > 0:
            sold = await self._sell_partial(position, 100.0, exit_reason)
            if not sold:
                return  # keep position open — sell failed, retry next poll

        await self._sync_wallet_balance(position)
        if position.remaining_tokens > 0.0001:
            return  # tokens still in wallet after sell attempt

        position.closed = True
        await self._persist(position)

        exit_time = datetime.now(timezone.utc)
        sol_price = await self.executor.get_sol_price_usd()
        entry_usd = position.initial_sol * sol_price
        if SELL_TO_STABLE:
            pnl_usd = position.total_sol_received - entry_usd
            pnl_sol = pnl_usd / sol_price if sol_price > 0 else 0.0
        else:
            pnl_sol = position.total_sol_received - position.initial_sol
            pnl_usd = pnl_sol * sol_price

        spent_usd = entry_usd
        received_usd = position.total_sol_received if SELL_TO_STABLE else position.total_sol_received * sol_price

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
            spent_usd=spent_usd,
            received_usd=received_usd,
            peak_multiplier=position.peak_multiplier,
            score_breakdown=position.score_breakdown,
            sol_price_usd=sol_price,
        )
        await self.alerter.send_trade_alert(alert)

        if pnl_usd < 0:
            self._loss_cooldown[position.mint] = exit_time + timedelta(hours=COPY_REBUY_COOLDOWN_HOURS)

        hold_time = (exit_time - position.entry_time).total_seconds()
        logger.info("Position closed — %s | %s | hold %s | PnL %.4f SOL | peak %.2fx",
                    position.symbol, exit_reason, format_duration(hold_time), pnl_sol,
                    position.peak_multiplier)

    async def _evaluate_position(self, position: Position) -> None:
        if position.closed:
            return

        current_price = await self.executor.get_token_price_usd(position.mint)
        if not current_price or current_price <= 0:
            position.price_miss_count += 1
            # Force-sell after 10 consecutive price failures (~50s) — token is dead/illiquid
            if position.price_miss_count >= 6:
                logger.warning("Force-selling %s — no price data for %d checks",
                               position.symbol, position.price_miss_count)
                await self._close_position(position, "no_price_data", position.entry_price_usd)
            return
        position.price_miss_count = 0

        multiplier   = self._current_multiplier(position, current_price)
        position.peak_multiplier = max(position.peak_multiplier, multiplier)
        pnl_pct      = (multiplier - 1.0) * 100.0
        hold_minutes = (datetime.now(timezone.utc) - position.entry_time).total_seconds() / 60.0

        # Also check real Jupiter sell quote — price feed can lie on meme coins
        await self._sync_wallet_balance(position)
        if position.remaining_tokens > 0:
            raw = int(position.remaining_tokens * (10 ** position.decimals))
            quote_sol = await self.executor.get_sell_quote_sol(position.mint, raw)
            if quote_sol and position.initial_sol > 0:
                quote_mult = quote_sol / position.initial_sol
                position.peak_multiplier = max(position.peak_multiplier, quote_mult)
                quote_pnl_pct = (quote_mult - 1.0) * 100.0
                if quote_pnl_pct > pnl_pct:
                    pnl_pct = quote_pnl_pct
                    multiplier = quote_mult

        # Take profits FIRST — don't let a fast dump skip TP
        if multiplier >= TP1_MULTIPLIER and not position.tp1_hit:
            if await self._sell_partial(position, TP1_SELL_PCT, "TP1"):
                position.tp1_hit = True
                await self._persist(position)

        if multiplier >= TP2_MULTIPLIER and not position.tp2_hit:
            if await self._sell_partial(position, TP2_SELL_PCT, "TP2"):
                position.tp2_hit = True
                await self._persist(position)

        if multiplier >= TP3_MULTIPLIER and not position.tp3_hit:
            if await self._sell_partial(position, TP3_SELL_PCT, "TP3"):
                position.tp3_hit = True
                await self._persist(position)
                if position.remaining_tokens <= 0:
                    await self._close_position(position, "TP3", current_price)
                    return

        # Stop loss
        if pnl_pct <= STOP_LOSS_PCT:
            await self._close_position(position, "SL", current_price)
            return

        # Activate trailing stop after 3x
        if multiplier >= TRAILING_ACTIVATION_MULTIPLIER:
            position.trailing_active = True
            position.trailing_peak_multiplier = max(position.trailing_peak_multiplier, multiplier)

        # Trailing stop -18% from peak
        if position.trailing_active:
            drawdown = ((multiplier - position.trailing_peak_multiplier)
                        / position.trailing_peak_multiplier) * 100.0
            if drawdown <= TRAILING_STOP_PCT:
                await self._close_position(position, "trailing", current_price)
                return

        # Time stop — not moving up
        if hold_minutes >= TIME_STOP_MINUTES and multiplier < TIME_STOP_MIN_MULTIPLIER:
            await self._close_position(position, "time_stop", current_price)
            return

        # Hard max hold — never sit in a bag forever
        if hold_minutes >= MAX_HOLD_MINUTES:
            logger.info("Max hold %d min — force exit %s", MAX_HOLD_MINUTES, position.symbol)
            await self._close_position(position, "max_hold", current_price)
            return

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
