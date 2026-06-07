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
    HELIUS_RPC_URL,
    JUPITER_PRICE_URL,
    JUPITER_QUOTE_URL,
    JUPITER_SWAP_URL,
    LAMPORTS_PER_SOL,
    PAPER_TRADE,
    SOL_MINT,
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
    position_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class SellResult:
    success: bool
    mint: str
    symbol: str
    amount_tokens: float
    sol_received: float
    exit_price_usd: float
    tx_signature: str | None
    sell_pct: float


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
                data.get("data", {}).get(SOL_MINT, {}).get("price")
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
        try:
            data = await fetch_json(
                self.session,
                "GET",
                JUPITER_PRICE_URL,
                params={"ids": mint},
                label=f"Token price fetch {mint[:8]}",
            )
            price = (
                data.get("data", {}).get(mint, {}).get("price")
                or data.get(mint, {}).get("price")
            )
            result = float(price) if price else None
            self._token_price_cache[mint] = (now, result)
            return result
        except Exception:
            return cached_price

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

    async def execute_swap(self, quote: dict[str, Any]) -> str | None:
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
            "prioritizationFeeLamports": 100_000,
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
            async with self.session.post(HELIUS_RPC_URL, json=body) as resp:
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
        raw_amount = int(amount_tokens * (10**decimals))
        logger.info(
            "SELL signal — %s (%.2f%%) — %s tokens",
            symbol,
            sell_pct,
            amount_tokens,
        )

        try:
            quote = await self.get_quote(mint, SOL_MINT, raw_amount)
            out_lamports = int(quote.get("outAmount", 0))
            sol_received = lamports_to_sol(out_lamports)

            token_price = await self.get_token_price_usd(mint)
            sol_price = await self.get_sol_price_usd()
            exit_price_usd = token_price or 0.0

            tx_sig = await self.execute_swap(quote)

            result = SellResult(
                success=True,
                mint=mint,
                symbol=symbol,
                amount_tokens=amount_tokens,
                sol_received=sol_received,
                exit_price_usd=exit_price_usd,
                tx_signature=tx_sig,
                sell_pct=sell_pct,
            )
            logger.info(
                "SELL complete — %s sold %.4f tokens for %.4f SOL (tx: %s)",
                symbol,
                amount_tokens,
                sol_received,
                tx_sig,
            )
            return result

        except Exception as exc:
            logger.error("SELL failed for %s: %s", mint[:8], exc)
            return SellResult(
                success=False,
                mint=mint,
                symbol=symbol,
                amount_tokens=amount_tokens,
                sol_received=0,
                exit_price_usd=0,
                tx_signature=None,
                sell_pct=sell_pct,
            )

    async def get_token_balance(self, mint: str) -> tuple[float, int]:
        if self.paper_trade or not self.keypair:
            return 0.0, 6

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenAccountsByOwner",
            "params": [
                str(self.keypair.pubkey()),
                {"mint": mint},
                {"encoding": "jsonParsed"},
            ],
        }
        data = await fetch_json(
            self.session,
            "POST",
            HELIUS_RPC_URL,
            json_body=payload,
            label="Token balance fetch",
        )
        accounts = data.get("result", {}).get("value", [])
        if not accounts:
            return 0.0, 6

        info = accounts[0]["account"]["data"]["parsed"]["info"]
        amount = float(info["tokenAmount"]["uiAmount"] or 0)
        decimals = int(info["tokenAmount"]["decimals"])
        return amount, decimals
