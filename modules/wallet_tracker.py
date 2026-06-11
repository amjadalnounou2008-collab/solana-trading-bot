from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

import aiohttp

from config import (
    COPY_GRADUATED_ONLY,
    COPY_MAX_MARKET_CAP_USD,
    COPY_MAX_TRADER_SOL,
    COPY_MIN_GRADUATED_LIQUIDITY_USD,
    COPY_MIN_TRADER_SOL,
    COPY_SKIP_IF_HOLDING,
    DEXSCREENER_TOKEN_URL,
    RUGCHECK_URL,
    CASH_MINT,
    SELL_SLIPPAGE_BPS,
    SOL_MINT,
    SOLANA_SEND_RPC_URL,
    TRADER_BY_ADDRESS,
    TRADERS,
    USDC_MINT,
    WALLET_POLL_INTERVAL_SECONDS,
    WALLET_RPC_BACKOFF_SECONDS,
    WALLET_SIGNATURE_LIMIT,
)
from modules.utils import fetch_json, lamports_to_sol, sol_to_lamports

if TYPE_CHECKING:
    from modules.executor import Executor

logger = logging.getLogger("solana-bot.wallet_tracker")

STABLE_MINTS = {SOL_MINT, USDC_MINT, CASH_MINT}


class WalletTracker:
    def __init__(self, session: aiohttp.ClientSession, executor: "Executor") -> None:
        self.session = session
        self.executor = executor
        self._last_signatures: dict[str, str] = {}
        self._seen_signatures: dict[str, set[str]] = {t.address: set() for t in TRADERS}
        self._running = False
        self._trader_index = 0
        self._last_rpc_warn_at = 0.0

    async def _rpc_call(self, method: str, params: list[Any]) -> Any:
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        async with self.session.post(
            SOLANA_SEND_RPC_URL,
            json=body,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            if resp.status == 429:
                now = time.monotonic()
                if now - self._last_rpc_warn_at >= 300:
                    logger.warning(
                        "RPC 429 — backing off %ds (copy polling slowed)",
                        WALLET_RPC_BACKOFF_SECONDS,
                    )
                    self._last_rpc_warn_at = now
                return None
            data = await resp.json(content_type=None)
        if data.get("error"):
            raise RuntimeError(data["error"])
        return data.get("result")

    async def _list_signatures(self, address: str) -> list[str] | None:
        try:
            result = await self._rpc_call(
                "getSignaturesForAddress",
                [address, {"limit": WALLET_SIGNATURE_LIMIT}],
            )
            if result is None:
                return None
            return [row["signature"] for row in result if row.get("signature")]
        except Exception as exc:
            logger.warning("RPC signature fetch error %s: %s", address[:8], exc)
            return []

    async def _get_transaction(self, signature: str) -> dict[str, Any] | None:
        try:
            result = await self._rpc_call(
                "getTransaction",
                [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
            )
            return result
        except Exception as exc:
            logger.warning("RPC tx fetch failed %s: %s", signature[:12], exc)
            return None

    def _account_pubkey(self, key: Any) -> str:
        if isinstance(key, str):
            return key
        return str(key.get("pubkey", ""))

    def _tx_to_helius_shape(
        self, signature: str, tx: dict[str, Any], trader_address: str,
    ) -> dict[str, Any] | None:
        meta = tx.get("meta") or {}
        if meta.get("err"):
            return None

        pre_token: dict[str, float] = {}
        for bal in meta.get("preTokenBalances") or []:
            if bal.get("owner") != trader_address:
                continue
            mint = bal.get("mint", "")
            if mint in STABLE_MINTS:
                continue
            ui = bal.get("uiTokenAmount") or {}
            pre_token[mint] = float(ui.get("uiAmount") or 0)

        token_transfers: list[dict[str, Any]] = []
        for bal in meta.get("postTokenBalances") or []:
            if bal.get("owner") != trader_address:
                continue
            mint = bal.get("mint", "")
            if mint in STABLE_MINTS:
                continue
            ui = bal.get("uiTokenAmount") or {}
            post_amt = float(ui.get("uiAmount") or 0)
            delta = post_amt - pre_token.get(mint, 0.0)
            if delta <= 0:
                continue
            token_transfers.append({
                "toUserAccount": trader_address,
                "mint": mint,
                "tokenAmount": delta,
                "tokenSymbol": mint[:6],
            })

        if not token_transfers:
            return None

        native_transfers: list[dict[str, Any]] = []
        message = tx.get("transaction", {}).get("message", {})
        account_keys = message.get("accountKeys", [])
        trader_idx = next(
            (i for i, key in enumerate(account_keys) if self._account_pubkey(key) == trader_address),
            None,
        )
        if trader_idx is not None:
            pre_bal = meta.get("preBalances", [])[trader_idx]
            post_bal = meta.get("postBalances", [])[trader_idx]
            if post_bal < pre_bal:
                native_transfers.append({
                    "fromUserAccount": trader_address,
                    "amount": pre_bal - post_bal,
                })

        return {
            "signature": signature,
            "type": "SWAP",
            "tokenTransfers": token_transfers,
            "nativeTransfers": native_transfers,
        }

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
                        received_tokens.append((
                            mint,
                            float(output.get("rawTokenAmount", {}).get("tokenAmount", 0) or 0),
                            symbol,
                        ))

        if not received_tokens:
            return None

        mint, _, symbol = max(received_tokens, key=lambda x: x[1])
        return mint, symbol

    async def _get_best_pair(self, mint: str) -> dict | None:
        try:
            url = DEXSCREENER_TOKEN_URL.format(mint=mint)
            data = await fetch_json(self.session, "GET", url, label=f"DexScreener {mint[:8]}")
            pairs = data.get("pairs") or []
            if not pairs:
                return None
            return max(pairs, key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0))
        except Exception:
            return None

    async def _get_market_cap(self, mint: str) -> float | None:
        pair = await self._get_best_pair(mint)
        if not pair:
            return None
        return float(pair.get("marketCap") or pair.get("fdv") or 0)

    async def _is_graduated(self, mint: str) -> bool:
        pair = await self._get_best_pair(mint)
        if not pair:
            return False
        dex = (pair.get("dexId") or "").lower()
        liq = float(pair.get("liquidity", {}).get("usd", 0) or 0)
        graduated_dexes = {"raydium", "orca", "meteora", "pumpswap"}
        return dex in graduated_dexes and liq >= COPY_MIN_GRADUATED_LIQUIDITY_USD

    async def _rugcheck_ok(self, mint: str) -> bool:
        try:
            url = RUGCHECK_URL.format(mint=mint)
            data = await fetch_json(self.session, "GET", url, label=f"RugCheck {mint[:8]}")
            score = float(data.get("score", 0) or 0)
            risks = data.get("risks", []) or []
            is_honeypot = any(
                "honeypot" in str(r.get("name", "")).lower()
                or "cannot sell" in str(r.get("description", "")).lower()
                for r in risks
            )
            if data.get("rugged") or is_honeypot or score > 500:
                return False
            return True
        except Exception:
            logger.info("Skipping %s — RugCheck unavailable", mint[:8])
            return False

    def _already_holding(self, mint: str) -> bool:
        rm = self.executor.risk_manager
        return rm.is_holding(mint) if rm else False

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

        can_buy, skip = await self.executor.can_trade(mint)
        if not can_buy:
            logger.info("Skipping %s — %s", symbol, skip)
            return

        if COPY_GRADUATED_ONLY:
            if not await self._is_graduated(mint):
                logger.info(
                    "Skipping %s — not graduated (needs Raydium/Orca pool with $%dk+ liq)",
                    symbol, COPY_MIN_GRADUATED_LIQUIDITY_USD // 1000,
                )
                return

        if COPY_SKIP_IF_HOLDING and self._already_holding(mint):
            logger.info("Skipping %s — already holding", symbol)
            return

        if not await self._rugcheck_ok(mint):
            logger.info("Skipping %s — RugCheck failed (honeypot/rug/high risk)", symbol)
            return

        market_cap = await self._get_market_cap(mint)
        if market_cap and market_cap > COPY_MAX_MARKET_CAP_USD:
            logger.info(
                "Skipping — market cap $%.0f exceeds $%d limit",
                market_cap,
                COPY_MAX_MARKET_CAP_USD,
            )
            return

        try:
            buy_quote = await self.executor.get_quote(
                SOL_MINT, mint, sol_to_lamports(trader.copy_amount_sol),
            )
            test_amount = max(int(buy_quote.get("outAmount", 0)) // 10, 1)
            sell_quote, _ = await self.executor._get_exit_quote(
                mint, test_amount, SELL_SLIPPAGE_BPS,
            )
            if not sell_quote or int(sell_quote.get("outAmount", 0)) <= 0:
                logger.info("Skipping %s — Jupiter sell route failed (unsellable)", symbol)
                return
        except Exception:
            logger.info("Skipping %s — could not verify sell route", symbol)
            return

        pair = await self._get_best_pair(mint)
        liq = float(pair.get("liquidity", {}).get("usd", 0) or 0) if pair else 0
        dex = (pair.get("dexId") or "?") if pair else "?"
        mcap_str = f"${market_cap:,.0f}" if market_cap else "unknown"
        breakdown = {
            "market_cap": mcap_str,
            "liquidity": f"${liq:,.0f}",
            "dex": dex,
            "trader": f"{trader.name} ({trader.handle})",
            "trader_spent": f"{sol_spent:.3f} SOL",
        }
        buy_sol = await self.executor.calc_buy_size_sol()
        reason = f"Copy {trader.name} — bought {symbol}"
        await self.executor.buy_token(
            mint=mint,
            amount_sol=buy_sol,
            reason=reason,
            symbol=symbol,
            score_breakdown=breakdown,
        )

    async def _poll_wallet(self, trader_address: str) -> None:
        try:
            signatures = await self._list_signatures(trader_address)
            if signatures is None:
                await asyncio.sleep(WALLET_RPC_BACKOFF_SECONDS)
                return
            if not signatures:
                return

            newest_sig = signatures[0]
            last_known = self._last_signatures.get(trader_address)

            if last_known is None:
                self._last_signatures[trader_address] = newest_sig
                self._seen_signatures[trader_address].update(signatures)
                logger.info("Initialized wallet tracker for %s", trader_address[:8])
                return

            if newest_sig == last_known:
                return

            new_sigs: list[str] = []
            for sig in signatures:
                if sig == last_known:
                    break
                new_sigs.append(sig)

            self._last_signatures[trader_address] = newest_sig

            for sig in reversed(new_sigs):
                raw_tx = await self._get_transaction(sig)
                if not raw_tx:
                    continue
                shaped = self._tx_to_helius_shape(sig, raw_tx, trader_address)
                if not shaped:
                    continue
                await self._process_transaction(trader_address, shaped)
                await asyncio.sleep(0.3)

        except Exception as exc:
            logger.error("Error polling wallet %s: %s", trader_address[:8], exc)

    async def run(self) -> None:
        self._running = True
        trader_names = ", ".join(f"{t.name}" for t in TRADERS)
        logger.info(
            "Wallet tracker started — public RPC, rotating %d wallets every %ds (%s)",
            len(TRADERS),
            WALLET_POLL_INTERVAL_SECONDS,
            trader_names,
        )

        while self._running:
            trader = TRADERS[self._trader_index % len(TRADERS)]
            self._trader_index += 1
            await self._poll_wallet(trader.address)
            await asyncio.sleep(WALLET_POLL_INTERVAL_SECONDS)

    def stop(self) -> None:
        self._running = False
