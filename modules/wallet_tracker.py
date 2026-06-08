from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import aiohttp

from config import (
    COPY_MAX_MARKET_CAP_USD,
    COPY_MAX_TRADER_SOL,
    COPY_MIN_TRADER_SOL,
    HELIUS_API_KEY,
    HELIUS_TX_URL,
    SOL_MINT,
    TRADER_BY_ADDRESS,
    TRADERS,
    USDC_MINT,
    WALLET_POLL_INTERVAL_SECONDS,
)
from modules.utils import fetch_json, lamports_to_sol

if TYPE_CHECKING:
    from modules.executor import Executor

logger = logging.getLogger("solana-bot.wallet_tracker")

STABLE_MINTS = {SOL_MINT, USDC_MINT}


class WalletTracker:
    def __init__(self, session: aiohttp.ClientSession, executor: "Executor") -> None:
        self.session = session
        self.executor = executor
        self._last_signatures: dict[str, str] = {}
        self._seen_signatures: dict[str, set[str]] = {t.address: set() for t in TRADERS}
        self._running = False

    async def _fetch_transactions(self, address: str) -> list[dict[str, Any]]:
        url = HELIUS_TX_URL.format(address=address)
        params = {
            "api-key": HELIUS_API_KEY,
            "limit": 10,
            "type": "SWAP",
        }
        data = await fetch_json(
            self.session,
            "GET",
            url,
            params=params,
            label=f"Helius tx fetch {address[:8]}",
        )
        if isinstance(data, list):
            return data
        return data.get("data", data) if isinstance(data, dict) else []

    def _estimate_sol_spent(self, tx: dict[str, Any], trader_address: str) -> float:
        sol_spent = 0.0

        for transfer in tx.get("nativeTransfers", []):
            if transfer.get("fromUserAccount") == trader_address:
                sol_spent += lamports_to_sol(transfer.get("amount", 0))

        for transfer in tx.get("tokenTransfers", []):
            mint = transfer.get("mint", "")
            if mint == SOL_MINT and transfer.get("fromUserAccount") == trader_address:
                sol_spent += float(transfer.get("tokenAmount", 0) or 0)

        if sol_spent == 0:
            fee_payer = tx.get("feePayer", "")
            if fee_payer == trader_address:
                account_data = tx.get("accountData", [])
                for acct in account_data:
                    if acct.get("account") == trader_address:
                        native_change = acct.get("nativeBalanceChange", 0)
                        if native_change < 0:
                            sol_spent = lamports_to_sol(abs(native_change))

        return sol_spent

    def _detect_buy(self, tx: dict[str, Any], trader_address: str) -> tuple[str, str] | None:
        if tx.get("type") not in ("SWAP", "UNKNOWN", None):
            if tx.get("type") and "SWAP" not in str(tx.get("type", "")).upper():
                pass

        received_tokens: list[tuple[str, float, str]] = []

        for transfer in tx.get("tokenTransfers", []):
            to_user = transfer.get("toUserAccount", "")
            mint = transfer.get("mint", "")
            amount = float(transfer.get("tokenAmount", 0) or 0)

            if to_user == trader_address and mint not in STABLE_MINTS and amount > 0:
                symbol = transfer.get("tokenSymbol") or mint[:6]
                received_tokens.append((mint, amount, symbol))

        if not received_tokens:
            events = tx.get("events", {}) or {}
            swap_event = events.get("swap", {})
            if swap_event:
                token_outputs = swap_event.get("tokenOutputs", [])
                for output in token_outputs:
                    mint = output.get("mint", "")
                    if mint and mint not in STABLE_MINTS:
                        symbol = output.get("symbol") or mint[:6]
                        received_tokens.append((mint, float(output.get("rawTokenAmount", {}).get("tokenAmount", 0) or 0), symbol))

        if not received_tokens:
            return None

        mint, _, symbol = max(received_tokens, key=lambda x: x[1])
        return mint, symbol

    async def _get_market_cap(self, mint: str) -> float | None:
        from config import DEXSCREENER_TOKEN_URL

        try:
            url = DEXSCREENER_TOKEN_URL.format(mint=mint)
            data = await fetch_json(self.session, "GET", url, label=f"Market cap {mint[:8]}")
            pairs = data.get("pairs") or []
            if not pairs:
                return None
            best = max(pairs, key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0))
            return float(best.get("marketCap") or best.get("fdv") or 0)
        except Exception:
            return None

    async def _process_transaction(self, trader_address: str, tx: dict[str, Any]) -> None:
        signature = tx.get("signature", "")
        if not signature:
            return

        seen = self._seen_signatures.setdefault(trader_address, set())
        if signature in seen:
            return
        seen.add(signature)
        if len(seen) > 500:
            self._seen_signatures[trader_address] = set(list(seen)[-250:])

        buy = self._detect_buy(tx, trader_address)
        if not buy:
            return

        mint, symbol = buy
        trader = TRADER_BY_ADDRESS.get(trader_address)
        if not trader:
            return

        sol_spent = self._estimate_sol_spent(tx, trader_address)
        logger.info(
            "Detected buy from %s (%s) — %s spent %.4f SOL on %s",
            trader.name,
            trader.handle,
            symbol,
            sol_spent,
            mint[:8],
        )

        if sol_spent < COPY_MIN_TRADER_SOL:
            logger.info("Skipping — trader spent %.4f SOL (< %.1f min)", sol_spent, COPY_MIN_TRADER_SOL)
            return
        if sol_spent > COPY_MAX_TRADER_SOL:
            logger.info("Skipping — trader spent %.4f SOL (> %.0f max)", sol_spent, COPY_MAX_TRADER_SOL)
            return

        market_cap = await self._get_market_cap(mint)
        if market_cap and market_cap > COPY_MAX_MARKET_CAP_USD:
            logger.info(
                "Skipping — market cap $%.0f exceeds $%d limit",
                market_cap,
                COPY_MAX_MARKET_CAP_USD,
            )
            return

        reason = f"Copy trade from {trader.name} ({trader.handle}) — spent {sol_spent:.3f} SOL"
        await self.executor.buy_token(
            mint=mint,
            amount_sol=trader.copy_amount_sol,
            reason=reason,
            symbol=symbol,
        )

    async def _poll_wallet(self, trader_address: str) -> None:
        try:
            transactions = await self._fetch_transactions(trader_address)
            if not transactions:
                return

            newest_sig = transactions[0].get("signature", "")
            last_known = self._last_signatures.get(trader_address)

            if last_known is None:
                self._last_signatures[trader_address] = newest_sig
                for tx in transactions:
                    sig = tx.get("signature", "")
                    if sig:
                        self._seen_signatures[trader_address].add(sig)
                logger.info("Initialized wallet tracker for %s", trader_address[:8])
                return

            if newest_sig == last_known:
                return

            new_txs = []
            for tx in transactions:
                sig = tx.get("signature", "")
                if sig == last_known:
                    break
                new_txs.append(tx)

            self._last_signatures[trader_address] = newest_sig

            for tx in reversed(new_txs):
                await self._process_transaction(trader_address, tx)

        except Exception as exc:
            logger.error("Error polling wallet %s: %s", trader_address[:8], exc)

    async def run(self) -> None:
        self._running = True
        trader_names = ", ".join(f"{t.name}" for t in TRADERS)
        logger.info(
            "Wallet tracker started — polling %d wallets every %ds (%s)",
            len(TRADERS),
            WALLET_POLL_INTERVAL_SECONDS,
            trader_names,
        )

        while self._running:
            for trader in TRADERS:
                if not self._running:
                    break
                await self._poll_wallet(trader.address)
                await asyncio.sleep(1.0)
            await asyncio.sleep(WALLET_POLL_INTERVAL_SECONDS)

    def stop(self) -> None:
        self._running = False
