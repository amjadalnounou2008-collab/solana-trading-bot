from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import aiohttp

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_SEND_URL
from modules.utils import format_duration, format_sol, format_usd

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
    peak_multiplier: float
    score_breakdown: dict[str, Any] | None = None
    sol_price_usd: float = 0.0


class Alerter:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.session = session
        self.enabled = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)

    async def send_message(self, text: str) -> None:
        if not self.enabled:
            logger.info("[ALERT] Telegram disabled — %s", text[:120])
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

    async def send_trade_alert(self, alert: TradeAlert) -> None:
        hold_seconds = (alert.exit_time - alert.entry_time).total_seconds()
        pnl_sign = "+" if alert.pnl_sol >= 0 else ""

        lines = [
            "<b>Trade Completed</b>",
            "",
            f"<b>Token:</b> {alert.token_symbol} (<code>{alert.token_mint[:8]}...</code>)",
            f"<b>Why bought:</b> {alert.reason}",
            f"<b>Entry price:</b> {format_usd(alert.entry_price)}",
            f"<b>Exit price:</b> {format_usd(alert.exit_price)}",
            f"<b>Exit reason:</b> {alert.exit_reason}",
            f"<b>Hold time:</b> {format_duration(hold_seconds)}",
            f"<b>PnL:</b> {pnl_sign}{format_sol(alert.pnl_sol)} ({pnl_sign}{format_usd(alert.pnl_usd)})",
            f"<b>Peak multiplier:</b> {alert.peak_multiplier:.2f}x",
        ]

        if alert.score_breakdown:
            lines.append("")
            lines.append("<b>Score breakdown:</b>")
            for key, value in alert.score_breakdown.items():
                lines.append(f"  • {key}: {value}")

        message = "\n".join(lines)
        logger.info("Sending trade alert for %s — %s", alert.token_symbol, alert.exit_reason)
        await self.send_message(message)

    async def send_startup_message(self) -> None:
        await self.send_message("<b>Solana Memecoin Bot started</b> — monitoring wallets and scanning DexScreener.")

    async def send_error(self, context: str, error: str) -> None:
        await self.send_message(f"<b>Error</b> in {context}:\n<code>{error}</code>")
