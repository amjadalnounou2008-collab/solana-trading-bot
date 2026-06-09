from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import aiohttp

from config import MAX_BUYS_PER_DAY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_SEND_URL
from modules.utils import format_duration, format_usd

logger = logging.getLogger("solana-bot.alerter")


@dataclass
class TradeAlert:
    token_mint: str
    token_symbol: str
    reason: str
    entry_price: float
    exit_price: float
    exit_reason: str
    entry_time: datetime
    exit_time: datetime
    pnl_sol: float
    pnl_usd: float
    spent_usd: float
    received_usd: float
    peak_multiplier: float
    daily_pnl_usd: float = 0.0
    lifetime_pnl_usd: float = 0.0
    buys_today: int = 0
    score_breakdown: dict[str, Any] | None = None
    sol_price_usd: float = 0.0


class Alerter:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.session = session
        self.enabled = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)

    async def send_message(self, text: str) -> None:
        if not self.enabled:
            logger.info("[ALERT] Telegram disabled — %s", text[:200])
            return

        url = TELEGRAM_SEND_URL.format(token=TELEGRAM_BOT_TOKEN)
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            async with self.session.post(url, json=payload) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error("Telegram send failed (%s): %s", resp.status, body)
        except Exception as exc:
            logger.error("Telegram send error: %s", exc)

    async def send_buy_alert(
        self,
        symbol: str,
        mint: str,
        amount_sol: float,
        cost_usd: float,
        reason: str,
        tx_sig: str | None,
        buys_today: int = 0,
        score_breakdown: dict[str, Any] | None = None,
    ) -> None:
        lines = [
            f"<b>🟢 BOUGHT — {symbol}</b>",
            f"<b>Cost:</b> {amount_sol:.4f} SOL ({format_usd(cost_usd)})",
            f"<b>Why:</b> {reason}",
            f"<b>Buy #</b>{buys_today}/{MAX_BUYS_PER_DAY} today",
        ]
        if score_breakdown:
            for key, value in score_breakdown.items():
                lines.append(f"<b>{key.replace('_', ' ').title()}:</b> {value}")
        lines.append(f"<b>Mint:</b> <code>{mint}</code>")
        if tx_sig:
            lines.append(f"<b>Tx:</b> <code>{tx_sig}</code>")
        await self.send_message("\n".join(lines))

    async def send_partial_sell_alert(
        self,
        symbol: str,
        sell_pct: float,
        received_usd: float,
        exit_reason: str,
        remaining_pct: float,
    ) -> None:
        await self.send_message(
            f"<b>📤 PARTIAL SELL — {symbol}</b>\n"
            f"<b>Sold:</b> {sell_pct:.0f}% ({exit_reason})\n"
            f"<b>USDC received:</b> {format_usd(received_usd)}\n"
            f"<b>Still holding:</b> {remaining_pct:.0f}%"
        )

    async def send_trade_alert(self, alert: TradeAlert) -> None:
        hold_seconds = (alert.exit_time - alert.entry_time).total_seconds()
        pnl_sign = "+" if alert.pnl_usd >= 0 else ""
        emoji = "✅" if alert.pnl_usd >= 0 else "❌"
        pct = (alert.pnl_usd / alert.spent_usd * 100.0) if alert.spent_usd > 0 else 0.0
        day_sign = "+" if alert.daily_pnl_usd >= 0 else ""
        life_sign = "+" if alert.lifetime_pnl_usd >= 0 else ""

        lines = [
            f"<b>{emoji} CLOSED → USDC — {alert.token_symbol}</b>",
            "",
            f"<b>This trade</b>",
            f"  Spent: {format_usd(alert.spent_usd)}",
            f"  Got back: {format_usd(alert.received_usd)} USDC",
            f"  PnL: {pnl_sign}{format_usd(alert.pnl_usd)} ({pnl_sign}{pct:.1f}%)",
            "",
            f"<b>Running totals</b>",
            f"  Today: {day_sign}{format_usd(alert.daily_pnl_usd)}",
            f"  All-time (tracked): {life_sign}{format_usd(alert.lifetime_pnl_usd)}",
            "",
            f"<b>Why bought:</b> {alert.reason}",
            f"<b>Exit:</b> {alert.exit_reason}",
            f"<b>Hold:</b> {format_duration(hold_seconds)} | <b>Peak:</b> {alert.peak_multiplier:.2f}x",
            f"<b>Mint:</b> <code>{alert.token_mint}</code>",
        ]
        await self.send_message("\n".join(lines))

    async def send_startup_message(self, lifetime_pnl: float = 0.0, lifetime_trades: int = 0) -> None:
        lines = [
            "<b>Solana Bot started</b>",
            "• Max <b>3 buys/day</b>, stops at <b>-$5</b> daily loss",
            "• Every buy + sell → Telegram + trade log",
            "• Sells → <b>USDC</b> with running PnL totals",
        ]
        if lifetime_trades > 0:
            sign = "+" if lifetime_pnl >= 0 else ""
            lines.append(
                f"• Tracked history: {lifetime_trades} trades, "
                f"{sign}{format_usd(lifetime_pnl)} all-time"
            )
        await self.send_message("\n".join(lines))

    async def send_error(self, context: str, error: str) -> None:
        await self.send_message(f"<b>Error</b> in {context}:\n<code>{error}</code>")
