from __future__ import annotations

import base64
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

import aiohttp
from solders.keypair import Keypair
from solders.message import to_bytes_versioned
from solders.transaction import VersionedTransaction

import config
from config import (
    DEFAULT_SLIPPAGE_BPS,
    DUST_BALANCE_USD,
    EXIT_DECIMALS,
    EXIT_LABELS,
    EXIT_MINTS,
    HELIUS_RPC_URL,
    JUPITER_PRICE_URL,
    JUPITER_QUOTE_URL,
    JUPITER_SWAP_URL,
    LAMPORTS_PER_SOL,
    MIN_SELL_VALUE_USD,
    PAPER_TRADE,
    SELL_PRIORITY_FEE_LAMPORTS,
    SELL_SLIPPAGE_BPS,
    SELL_SLIPPAGE_RETRY_BPS,
    SELL_TO_STABLE,
    SOL_MINT,
    SOLANA_SEND_RPC_URL,
    WALLET_PRIVATE_KEY,
)
from modules.utils import fetch_json, lamports_to_sol, retry_async, sol_to_lamports

if TYPE_CHECKING:
    from modules.risk_manager import RiskManager

logger = logging.getLogger("solana-bot.executor")


@dataclass
class BuyResult:
    success: bool
    mint: str
    symbol: str
    amount_sol: float
    tokens_received: float
    entry_price_usd: float
    tx_signature: str | None
    reason: str
    score_breakdown: dict[str, Any] | None = None
    decimals: int = 6
    position_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class SellResult:
    success: bool
    mint: str
    symbol: str
    amount_tokens: float
    sol_received: float       # stablecoin amount (~USD) when SELL_TO_STABLE, else SOL
    exit_mint: str = ""
    exit_label: str = ""
    exit_price_usd: float
    tx_signature: str | None
    sell_pct: float
    is_dust: bool = False


class Executor:
    def __init__(self, session: aiohttp.ClientSession, risk_manager: "RiskManager | None" = None) -> None:
        self.session = session
        self.risk_manager = risk_manager
        self.keypair = self._load_keypair()
        self.paper_trade = PAPER_TRADE
        self._sol_price_usd = 150.0
        self._sol_price_last_fetch = 0.0
        self._token_price_cache: dict[str, tuple[float, float | None]] = {}

    def _load_keypair(self) -> Keypair | None:
        if not WALLET_PRIVATE_KEY or WALLET_PRIVATE_KEY.startswith("your_"):
            if not PAPER_TRADE:
                logger.warning("No valid WALLET_PRIVATE_KEY — forcing paper trade mode")
            return None
        try:
            import base58

            raw = base58.b58decode(WALLET_PRIVATE_KEY)
            return Keypair.from_bytes(raw)
        except Exception:
            try:
                import json

                secret = json.loads(WALLET_PRIVATE_KEY)
                return Keypair.from_bytes(bytes(secret))
            except Exception as exc:
                logger.error("Failed to load keypair: %s", exc)
                return None

    @property
    def public_key(self) -> str:
        if self.keypair:
            return str(self.keypair.pubkey())
        return "PAPER_WALLET"

    def _parse_exit_amount(self, out_raw: int, exit_mint: str) -> float:
        if SELL_TO_STABLE and exit_mint in EXIT_DECIMALS:
            return out_raw / (10 ** EXIT_DECIMALS[exit_mint])
        return lamports_to_sol(out_raw)

    async def _exit_value_usd(self, out_raw: int, exit_mint: str) -> float:
        amount = self._parse_exit_amount(out_raw, exit_mint)
        if SELL_TO_STABLE and exit_mint != SOL_MINT:
            return amount
        return amount * await self.get_sol_price_usd()

    async def _get_exit_quote(
        self, mint: str, raw_amount: int, slippage_bps: int,
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Try Phantom Cash (CASH) first, then USDC, then SOL."""
        for exit_mint in EXIT_MINTS:
            try:
                quote = await self.get_quote(mint, exit_mint, raw_amount, slippage_bps=slippage_bps)
                if int(quote.get("outAmount", 0)) > 0:
                    return quote, exit_mint
            except Exception:
                continue
        return None, None

    async def get_sol_price_usd(self) -> float:
        import time
        now = time.time()
        if now - self._sol_price_last_fetch < 60:
            return self._sol_price_usd
        self._sol_price_last_fetch = now
        try:
            data = await fetch_json(
                self.session,
                "GET",
                JUPITER_PRICE_URL,
                params={"ids": SOL_MINT},
                label="SOL price fetch",
            )
            price = (
                data.get(SOL_MINT, {}).get("usdPrice")
                or data.get("data", {}).get(SOL_MINT, {}).get("price")
                or data.get(SOL_MINT, {}).get("price")
            )
            if price:
                self._sol_price_usd = float(price)
        except Exception as exc:
            logger.warning("SOL price fetch failed, using cached: %s", exc)
        return self._sol_price_usd

    async def get_token_price_usd(self, mint: str) -> float | None:
        import time
        now = time.time()
        cached_time, cached_price = self._token_price_cache.get(mint, (0, None))
        if now - cached_time < 30:
            return cached_price

        result = await self._price_from_jupiter(mint)

        if not result:
            result = await self._price_from_dexscreener(mint)

        if not result:
            result = await self._price_from_gmgn(mint)

        self._token_price_cache[mint] = (now, result)
        return result

    async def _price_from_jupiter(self, mint: str) -> float | None:
        try:
            data = await fetch_json(
                self.session, "GET", JUPITER_PRICE_URL,
                params={"ids": mint}, label=f"Jupiter price {mint[:8]}",
            )
            price = (
                data.get(mint, {}).get("usdPrice")
                or data.get("data", {}).get(mint, {}).get("price")
                or data.get(mint, {}).get("price")
            )
            return float(price) if price else None
        except Exception:
            return None

    async def _price_from_dexscreener(self, mint: str) -> float | None:
        try:
            from config import DEXSCREENER_TOKEN_URL
            url = DEXSCREENER_TOKEN_URL.format(mint=mint)
            data = await fetch_json(
                self.session, "GET", url, label=f"DexScreener price {mint[:8]}",
            )
            pairs = data.get("pairs") or []
            if not pairs:
                return None
            best = max(pairs, key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0))
            price_str = best.get("priceUsd")
            return float(price_str) if price_str else None
        except Exception:
            return None

    async def _price_from_gmgn(self, mint: str) -> float | None:
        try:
            url = f"https://gmgn.ai/defi/quotation/v1/tokens/sol/{mint}"
            headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://gmgn.ai/"}
            data = await fetch_json(
                self.session, "GET", url, headers=headers,
                label=f"GMGN price {mint[:8]}",
            )
            price = (
                data.get("data", {}).get("price")
                or data.get("data", {}).get("priceUsd")
            )
            return float(price) if price else None
        except Exception:
            return None

    async def get_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        slippage_bps: int = DEFAULT_SLIPPAGE_BPS,
    ) -> dict[str, Any]:
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount),
            "slippageBps": str(slippage_bps),
        }
        return await fetch_json(
            self.session,
            "GET",
            JUPITER_QUOTE_URL,
            params=params,
            label="Jupiter quote",
        )

    async def execute_swap(
        self, quote: dict[str, Any], *, priority_fee: int = SELL_PRIORITY_FEE_LAMPORTS,
    ) -> str | None:
        if self.paper_trade or not self.keypair:
            out_amount = int(quote.get("outAmount", 0))
            logger.info(
                "[PAPER] Would execute swap — in=%s out=%s",
                quote.get("inAmount"),
                out_amount,
            )
            return f"PAPER_{uuid.uuid4().hex[:16]}"

        payload = {
            "quoteResponse": quote,
            "userPublicKey": str(self.keypair.pubkey()),
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": priority_fee,
        }
        swap_data = await fetch_json(
            self.session,
            "POST",
            JUPITER_SWAP_URL,
            json_body=payload,
            label="Jupiter swap build",
        )
        swap_tx_b64 = swap_data.get("swapTransaction")
        if not swap_tx_b64:
            raise ValueError("Jupiter swap response missing swapTransaction")

        raw_tx = VersionedTransaction.from_bytes(base64.b64decode(swap_tx_b64))
        signature = self.keypair.sign_message(to_bytes_versioned(raw_tx.message))
        signed_tx = VersionedTransaction.populate(raw_tx.message, [signature])
        encoded_tx = base64.b64encode(bytes(signed_tx)).decode()

        async def _send() -> str:
            body = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendTransaction",
                "params": [
                    encoded_tx,
                    {"skipPreflight": True, "maxRetries": 3, "encoding": "base64"},
                ],
            }
            # Use public Solana RPC for sending to avoid Helius rate limits
            async with self.session.post(SOLANA_SEND_RPC_URL, json=body) as resp:
                result = await resp.json()
                if "error" in result:
                    raise RuntimeError(result["error"])
                return result["result"]

        tx_sig = await retry_async(_send, label="Send transaction")
        logger.info("Swap submitted: %s", tx_sig)
        return tx_sig

    async def buy_token(
        self,
        mint: str,
        amount_sol: float,
        reason: str,
        symbol: str = "UNKNOWN",
        score_breakdown: dict[str, Any] | None = None,
    ) -> BuyResult:
        logger.info("BUY signal — %s (%s) for %.4f SOL — %s", symbol, mint[:8], amount_sol, reason)

        # Alert immediately when signal fires — before swap attempt
        if self.risk_manager:
            try:
                await self.risk_manager.alerter.send_message(
                    f"🔔 <b>BUY SIGNAL — {symbol}</b>\n"
                    f"Amount: {amount_sol} SOL\n"
                    f"Reason: {reason}\n"
                    f"Mint: <code>{mint[:16]}...</code>\n"
                    f"Attempting swap..."
                )
            except Exception:
                pass

        lamports = sol_to_lamports(amount_sol)

        try:
            quote = await self.get_quote(SOL_MINT, mint, lamports)
            out_amount = int(quote.get("outAmount", 0))
            out_decimals = int(quote.get("outDecimals", 6) or 6)
            tokens_received = out_amount / (10**out_decimals)

            token_price = await self.get_token_price_usd(mint)
            if not token_price and tokens_received > 0:
                sol_price = await self.get_sol_price_usd()
                token_price = (amount_sol * sol_price) / tokens_received

            tx_sig = await self.execute_swap(quote)

            result = BuyResult(
                success=True,
                mint=mint,
                symbol=symbol,
                amount_sol=amount_sol,
                tokens_received=tokens_received,
                entry_price_usd=token_price or 0.0,
                tx_signature=tx_sig,
                reason=reason,
                score_breakdown=score_breakdown,
                decimals=out_decimals,
            )

            if self.risk_manager:
                await self.risk_manager.open_position(result)

            logger.info(
                "BUY complete — %s received %.4f tokens @ $%.8f (tx: %s)",
                symbol,
                tokens_received,
                result.entry_price_usd,
                tx_sig,
            )
            return result

        except Exception as exc:
            logger.error("BUY failed for %s: %s", mint[:8], exc)
            if self.risk_manager:
                try:
                    await self.risk_manager.alerter.send_message(
                        f"❌ <b>BUY FAILED — {symbol}</b>\n"
                        f"Error: {str(exc)[:200]}"
                    )
                except Exception:
                    pass
            return BuyResult(
                success=False,
                mint=mint,
                symbol=symbol,
                amount_sol=amount_sol,
                tokens_received=0,
                entry_price_usd=0,
                tx_signature=None,
                reason=reason,
                score_breakdown=score_breakdown,
            )

    async def sell_token(
        self,
        mint: str,
        amount_tokens: float,
        decimals: int = 6,
        symbol: str = "UNKNOWN",
        sell_pct: float = 100.0,
    ) -> SellResult:
        # Always sell what's actually in the wallet — not stale tracked amounts
        wallet_amount, wallet_decimals = await self.get_token_balance(mint)
        if wallet_amount > 0:
            decimals = wallet_decimals
            amount_tokens = min(amount_tokens, wallet_amount)

        raw_amount = int(amount_tokens * (10**decimals))
        if raw_amount <= 0:
            logger.warning("SELL skip %s — zero balance in wallet", symbol)
            return SellResult(
                success=False, mint=mint, symbol=symbol, amount_tokens=0,
                sol_received=0, exit_price_usd=0, tx_signature=None, sell_pct=sell_pct,
            )

        logger.info(
            "SELL signal — %s (%.2f%%) — %.4f tokens (raw %d)",
            symbol, sell_pct, amount_tokens, raw_amount,
        )

        # Pre-check value — skip dust that spams alerts and never moves the needle
        try:
            preview, preview_mint = await self._get_exit_quote(mint, raw_amount, SELL_SLIPPAGE_BPS)
            if preview and preview_mint:
                preview_usd = await self._exit_value_usd(int(preview.get("outAmount", 0)), preview_mint)
                preview_label = EXIT_LABELS.get(preview_mint, "stable")
                if preview_usd < MIN_SELL_VALUE_USD:
                    logger.info(
                        "SELL skip %s — only $%.2f %s (dust, treating as done)",
                        symbol, preview_usd, preview_label,
                    )
                    return SellResult(
                        success=True, mint=mint, symbol=symbol, amount_tokens=amount_tokens,
                        sol_received=0, exit_price_usd=0, tx_signature=None,
                        sell_pct=sell_pct, is_dust=True,
                    )
        except Exception:
            pass

        last_exc: Exception | None = None
        for slippage in SELL_SLIPPAGE_RETRY_BPS:
            try:
                quote, exit_mint = await self._get_exit_quote(mint, raw_amount, slippage)
                if not quote or not exit_mint:
                    continue
                out_raw = int(quote.get("outAmount", 0))
                if out_raw <= 0:
                    continue
                exit_label = EXIT_LABELS.get(exit_mint, "stable")
                received = self._parse_exit_amount(out_raw, exit_mint)
                received_usd = await self._exit_value_usd(out_raw, exit_mint)
                exit_price_usd = await self.get_token_price_usd(mint) or 0.0

                logger.info(
                    "SELL attempt %s — slippage %d bps, expect $%.2f %s",
                    symbol, slippage, received_usd, exit_label,
                )
                tx_sig = await self.execute_swap(quote)

                logger.info(
                    "SELL complete — %s sold %.4f tokens for %.4f %s (tx: %s)",
                    symbol, amount_tokens, received, exit_label, tx_sig,
                )
                # One alert per meaningful sell — only full exits (100%), not every partial/dust
                if self.risk_manager and sell_pct >= 99.0 and received_usd >= MIN_SELL_VALUE_USD:
                    try:
                        await self.risk_manager.alerter.send_message(
                            f"✅ <b>SOLD — {symbol}</b>\n"
                            f"Got back: <b>${received_usd:.2f} {exit_label}</b>\n"
                            f"Tx: <code>{tx_sig[:16]}...</code>"
                        )
                    except Exception:
                        pass
                return SellResult(
                    success=True, mint=mint, symbol=symbol, amount_tokens=amount_tokens,
                    sol_received=received, exit_price_usd=exit_price_usd,
                    tx_signature=tx_sig, sell_pct=sell_pct,
                    exit_mint=exit_mint, exit_label=exit_label,
                )
            except Exception as exc:
                last_exc = exc
                logger.warning("SELL failed %s at %d bps: %s", symbol, slippage, exc)

        logger.error("SELL failed for %s after all retries: %s", mint[:8], last_exc)
        if self.risk_manager:
            try:
                await self.risk_manager.alerter.send_message(
                    f"❌ <b>SELL FAILED — {symbol}</b>\n"
                    f"Tokens still in wallet. Will retry.\n"
                    f"Error: {str(last_exc)[:150]}"
                )
            except Exception:
                pass
        return SellResult(
            success=False, mint=mint, symbol=symbol, amount_tokens=amount_tokens,
            sol_received=0, exit_price_usd=0, tx_signature=None, sell_pct=sell_pct,
        )

    async def get_token_balance(self, mint: str) -> tuple[float, int]:
        if self.paper_trade or not self.keypair:
            return 0.0, 6

        for rpc_url in (HELIUS_RPC_URL, SOLANA_SEND_RPC_URL):
            payload = {
                "jsonrpc": "2.0", "id": 1,
                "method": "getTokenAccountsByOwner",
                "params": [
                    str(self.keypair.pubkey()),
                    {"mint": mint},
                    {"encoding": "jsonParsed"},
                ],
            }
            try:
                data = await fetch_json(
                    self.session, "POST", rpc_url, json_body=payload,
                    label=f"Balance {mint[:8]}",
                )
                accounts = data.get("result", {}).get("value", [])
                if not accounts:
                    continue
                best_raw = 0
                best_amount = 0.0
                best_decimals = 6
                for acc in accounts:
                    info = acc["account"]["data"]["parsed"]["info"]
                    raw = int(info["tokenAmount"]["amount"])
                    if raw <= 0:
                        continue
                    dec = int(info["tokenAmount"]["decimals"])
                    ui = info["tokenAmount"]["uiAmount"]
                    amt = float(ui) if ui is not None else raw / (10**dec)
                    if raw > best_raw:
                        best_raw, best_amount, best_decimals = raw, amt, dec
                if best_raw > 0:
                    return best_amount, best_decimals
            except Exception:
                continue
        return 0.0, 6

    async def get_sell_quote_usd(self, mint: str, raw_amount: int) -> float | None:
        """How much USD Jupiter would pay right now (CASH/USDC or SOL×price)."""
        try:
            quote, exit_mint = await self._get_exit_quote(mint, raw_amount, SELL_SLIPPAGE_BPS)
            if not quote or not exit_mint:
                return None
            out = int(quote.get("outAmount", 0))
            return await self._exit_value_usd(out, exit_mint) if out > 0 else None
        except Exception:
            return None

    async def get_sell_quote_sol(self, mint: str, raw_amount: int) -> float | None:
        """SOL-equivalent exit value — for multiplier checks."""
        usd = await self.get_sell_quote_usd(mint, raw_amount)
        if usd is None:
            return None
        sol_price = await self.get_sol_price_usd()
        return usd / sol_price if sol_price > 0 else None
