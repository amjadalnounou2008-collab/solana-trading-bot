from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import aiohttp

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_SEND_URL
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

    async def send_buy_alert(
        self,
        symbol: str,
        mint: str,
        amount_sol: float,
        cost_usd: float,
        reason: str,
        tx_sig: str | None,
        score_breakdown: dict[str, Any] | None = None,
    ) -> None:
        lines = [
            f"<b>🟢 BOUGHT — {symbol}</b>",
            f"<b>Cost:</b> {amount_sol:.4f} SOL ({format_usd(cost_usd)})",
            f"<b>Why:</b> {reason}",
        ]
        if score_breakdown:
            for key, value in score_breakdown.items():
                lines.append(f"<b>{key.replace('_', ' ').title()}:</b> {value}")
        lines.append(f"<b>Mint:</b> <code>{mint}</code>")
        if tx_sig:
            lines.append(f"<b>Tx:</b> <code>{tx_sig}</code>")
        await self.send_message("\n".join(lines))

    async def send_trade_alert(self, alert: TradeAlert) -> None:
        hold_seconds = (alert.exit_time - alert.entry_time).total_seconds()
        pnl_sign = "+" if alert.pnl_usd >= 0 else ""
        emoji = "✅" if alert.pnl_usd >= 0 else "❌"
        pct = 0.0
        if alert.spent_usd > 0:
            pct = (alert.pnl_usd / alert.spent_usd) * 100.0

        lines = [
            f"<b>{emoji} SOLD → USDC — {alert.token_symbol}</b>",
            "",
            f"<b>Spent:</b> {format_usd(alert.spent_usd)}",
            f"<b>Got back:</b> {format_usd(alert.received_usd)} USDC",
            f"<b>PnL:</b> {pnl_sign}{format_usd(alert.pnl_usd)} ({pnl_sign}{pct:.1f}%)",
            "",
            f"<b>Why bought:</b> {alert.reason}",
            f"<b>Exit reason:</b> {alert.exit_reason}",
            f"<b>Hold:</b> {format_duration(hold_seconds)}",
            f"<b>Peak:</b> {alert.peak_multiplier:.2f}x",
            f"<b>Mint:</b> <code>{alert.token_mint}</code>",
        ]
        await self.send_message("\n".join(lines))

    async def send_startup_message(self) -> None:
        await self.send_message(
            "<b>Solana Bot started</b>\n"
            "• Copy-trades vetted wallets\n"
            "• Scans DexScreener for coins\n"
            "• Sells everything → <b>USDC</b>\n"
            "• 1 alert per buy, 1 alert per sell (with real PnL)"
        )

    async def send_error(self, context: str, error: str) -> None:
        await self.send_message(f"<b>Error</b> in {context}:\n<code>{error}</code>")
