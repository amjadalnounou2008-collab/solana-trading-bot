"""
One-time cleanup script — reads all token accounts in the wallet
and swaps everything back to SOL via Jupiter.

Run with:  python sell_all.py
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import sys

import aiohttp

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", stream=sys.stdout)
logger = logging.getLogger("sell_all")

from dotenv import load_dotenv
load_dotenv()

from config import (
    HELIUS_RPC_URL,
    JUPITER_QUOTE_URL,
    JUPITER_SWAP_URL,
    LAMPORTS_PER_SOL,
    SOL_MINT,
    SOLANA_SEND_RPC_URL,
    WALLET_PRIVATE_KEY,
    DEFAULT_SLIPPAGE_BPS,
)

# ── Load keypair ──────────────────────────────────────────────────────────────
def load_keypair():
    import base58
    from solders.keypair import Keypair
    try:
        raw = base58.b58decode(WALLET_PRIVATE_KEY)
        return Keypair.from_bytes(raw)
    except Exception:
        return Keypair.from_bytes(bytes(json.loads(WALLET_PRIVATE_KEY)))

# ── Fetch all token accounts ──────────────────────────────────────────────────
async def get_all_token_accounts(session: aiohttp.ClientSession, pubkey: str) -> list[dict]:
    # Try public RPC first, then Helius as fallback
    for rpc_url in [SOLANA_SEND_RPC_URL, HELIUS_RPC_URL]:
        payload = {
            "jsonrpc": "2.0", "id": 1,
            "method": "getTokenAccountsByOwner",
            "params": [pubkey, {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
                       {"encoding": "jsonParsed"}],
        }
        try:
            async with session.post(rpc_url, json=payload,
                                    timeout=aiohttp.ClientTimeout(total=20)) as resp:
                data = await resp.json()
            if data.get("error"):
                logger.warning("RPC error from %s: %s", rpc_url[:40], data["error"])
                continue
            accounts = data.get("result", {}).get("value", [])
            logger.info("RPC %s returned %d token account(s)", rpc_url[:40], len(accounts))
            tokens = []
            for acc in accounts:
                info = acc["account"]["data"]["parsed"]["info"]
                mint = info["mint"]
                amount = float(info["tokenAmount"]["uiAmount"] or 0)
                decimals = int(info["tokenAmount"]["decimals"])
                raw_amount = int(info["tokenAmount"]["amount"])
                if amount > 0 and mint != SOL_MINT:
                    tokens.append({"mint": mint, "amount": amount,
                                   "decimals": decimals, "raw_amount": raw_amount})
            if tokens:
                return tokens
        except Exception as exc:
            logger.warning("RPC call failed (%s): %s", rpc_url[:40], exc)
    return []

# ── Get Jupiter quote ─────────────────────────────────────────────────────────
async def get_quote(session: aiohttp.ClientSession, mint: str, raw_amount: int) -> dict | None:
    try:
        async with session.get(JUPITER_QUOTE_URL, params={
            "inputMint": mint,
            "outputMint": SOL_MINT,
            "amount": str(raw_amount),
            "slippageBps": str(DEFAULT_SLIPPAGE_BPS),
        }, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            data = await resp.json(content_type=None)
            if data.get("error") or not data.get("outAmount"):
                return None
            return data
    except Exception as exc:
        logger.warning("Quote failed for %s: %s", mint[:8], exc)
        return None

# ── Execute swap ──────────────────────────────────────────────────────────────
async def execute_swap(session: aiohttp.ClientSession, quote: dict, keypair) -> str | None:
    from solders.message import to_bytes_versioned
    from solders.transaction import VersionedTransaction

    payload = {
        "quoteResponse": quote,
        "userPublicKey": str(keypair.pubkey()),
        "wrapAndUnwrapSol": True,
        "dynamicComputeUnitLimit": True,
        "prioritizationFeeLamports": 100_000,
    }
    try:
        async with session.post(JUPITER_SWAP_URL, json=payload,
                                timeout=aiohttp.ClientTimeout(total=20)) as resp:
            swap_data = await resp.json(content_type=None)
        tx_b64 = swap_data.get("swapTransaction")
        if not tx_b64:
            logger.warning("No swapTransaction in response")
            return None

        raw_tx = VersionedTransaction.from_bytes(base64.b64decode(tx_b64))
        sig = keypair.sign_message(to_bytes_versioned(raw_tx.message))
        signed = VersionedTransaction.populate(raw_tx.message, [sig])
        encoded = base64.b64encode(bytes(signed)).decode()

        body = {
            "jsonrpc": "2.0", "id": 1, "method": "sendTransaction",
            "params": [encoded, {"skipPreflight": True, "maxRetries": 3, "encoding": "base64"}],
        }
        async with session.post(SOLANA_SEND_RPC_URL, json=body) as resp:
            result = await resp.json()
            if "error" in result:
                logger.warning("Send error: %s", result["error"])
                return None
            return result.get("result")
    except Exception as exc:
        logger.warning("Swap failed: %s", exc)
        return None

# ── Main ──────────────────────────────────────────────────────────────────────
async def main():
    keypair = load_keypair()
    pubkey  = str(keypair.pubkey())
    logger.info("Wallet: %s", pubkey)

    connector = aiohttp.TCPConnector()
    async with aiohttp.ClientSession(connector=connector) as session:
        tokens = await get_all_token_accounts(session, pubkey)

        if not tokens:
            logger.info("No tokens found in wallet — nothing to sell.")
            return

        # Get SOL quote for each token to show value
        logger.info("\n%-20s  %15s  %12s" , "MINT", "AMOUNT", "SOL VALUE")
        logger.info("-" * 55)
        for i, t in enumerate(tokens):
            quote = await get_quote(session, t["mint"], t["raw_amount"])
            if quote:
                sol_val = int(quote.get("outAmount", 0)) / LAMPORTS_PER_SOL
                t["quote"] = quote
                t["sol_value"] = sol_val
                logger.info("[%d] %-20s  %15.4f  %12.6f SOL", i+1, t["mint"][:20], t["amount"], sol_val)
            else:
                t["quote"] = None
                t["sol_value"] = 0
                logger.info("[%d] %-20s  %15.4f  NO ROUTE (illiquid)", i+1, t["mint"][:20], t["amount"])
            await asyncio.sleep(0.5)

        print("\nWhich tokens do you want to sell?")
        print("Enter numbers separated by commas (e.g. 1,3) or type 'all' for everything: ", end="")
        answer = input().strip().lower()

        if answer == "all":
            to_sell = [t for t in tokens if t.get("quote")]
        else:
            try:
                indices = [int(x.strip()) - 1 for x in answer.split(",")]
                to_sell = [tokens[i] for i in indices if tokens[i].get("quote")]
            except Exception:
                logger.info("Invalid input — cancelled.")
                return

        if not to_sell:
            logger.info("Nothing selected or no routes available — cancelled.")
            return

        logger.info("\nSelling %d token(s)...", len(to_sell))
        for t in to_sell:
            mint      = t["mint"]
            raw       = t["raw_amount"]
            logger.info("Selling %s (%.4f tokens)...", mint[:20], t["amount"])

            quote = t.get("quote")
            if not quote:
                logger.warning("  → No Jupiter route for %s — skipping", mint[:8])
                continue

            sol_out = int(quote.get("outAmount", 0)) / LAMPORTS_PER_SOL
            logger.info("  → %.6f SOL back", sol_out)

            tx_sig = await execute_swap(session, quote, keypair)
            if tx_sig:
                logger.info("  ✅ Sold! TX: https://solscan.io/tx/%s", tx_sig)
            else:
                logger.warning("  ❌ Swap failed for %s", mint[:8])

            await asyncio.sleep(2)

        logger.info("Done. Check your Phantom wallet.")

if __name__ == "__main__":
    asyncio.run(main())
