from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class TraderConfig:
    name: str
    handle: str
    address: str
    copy_amount_sol: float


# Copy best GMGN-vetted traders + autonomous market scanner (both run together)
ENABLE_COPY_TRADING = True
COPY_BUY_SOL = 0.03   # per copy trade — small for ~$30 balance

TRADERS: list[TraderConfig] = [
    # Tier 1 — best win rate + selective (milkybids picks)
    TraderConfig("jijo", "@gmgn", "4BdKaxN8G6ka4GYtQQWk4G4dZRUTX2vQH9GcXdBREFUk", COPY_BUY_SOL),    # 84.6% WR, 79 txns/mo
    TraderConfig("Sheep", "@gmgn", "78N177fzNJpp8pG49xDv1efYcTMSzo9tPTKEA9mAVkh2", COPY_BUY_SOL),   # 89.3% WR
    TraderConfig("nyhrox", "@gmgn", "6S8GezkxYUfZy9JPtYnanbcZTMB87Wjt1qx3c6ELajKC", COPY_BUY_SOL),  # 66% WR, active
    # Tier 2 — more trades, still passes GMGN filters
    TraderConfig("AU73", "@gmgn", "AU73C47eNaF5yhpAgB2CtYqPxREGsXQkSsbqgahEYW6h", COPY_BUY_SOL),    # 65.5% WR, 63 txns
    TraderConfig("flock", "@gmgn", "F1WT79Jkw3BkBDUfCbrKKo15ghZNCEjvnjxQpiCfPuRM", COPY_BUY_SOL),  # 60% WR, 11 txns
    TraderConfig("insentos", "@gmgn", "7SDs3PjT2mswKQ7Zo4FTucn9gJdtuW4jaacPA65BseHS", COPY_BUY_SOL),  # 66.7% WR
    TraderConfig("ALJ4P5", "@gmgn", "ALJ4P5QNyHeLEjpKGmA1eUfJHSEGQMjY8HLnDkSgjczb", COPY_BUY_SOL),  # 71% WR, 46 txns
]

TRADER_BY_ADDRESS: dict[str, TraderConfig] = {t.address: t for t in TRADERS}

# Environment
WALLET_PRIVATE_KEY: str = os.getenv("WALLET_PRIVATE_KEY", "")
HELIUS_API_KEY: str = os.getenv("HELIUS_API_KEY", "")
BIRDEYE_API_KEY: str = os.getenv("BIRDEYE_API_KEY", "")
TWITTER_BEARER_TOKEN: str = os.getenv("TWITTER_BEARER_TOKEN", "")
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
PAPER_TRADE: bool = os.getenv("PAPER_TRADE", "true").lower() in ("true", "1", "yes")

# Solana constants
SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
# Phantom Cash — Phantom's own USD stablecoin (what the app calls "Cash")
CASH_MINT = "CASHx9KJUStyftLFWGvEVf59SGeG9sh5FfcnZMVPCASH"
LAMPORTS_PER_SOL = 1_000_000_000

# API endpoints
HELIUS_RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
SOLANA_SEND_RPC_URL = "https://api.mainnet-beta.solana.com"
HELIUS_TX_URL = "https://api-mainnet.helius-rpc.com/v0/addresses/{address}/transactions"
JUPITER_QUOTE_URL = "https://api.jup.ag/swap/v1/quote"
JUPITER_SWAP_URL = "https://api.jup.ag/swap/v1/swap"
JUPITER_PRICE_URL = "https://api.jup.ag/price/v3"
DEXSCREENER_PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"
DEXSCREENER_BOOSTS_URL = "https://api.dexscreener.com/token-boosts/latest/v1"
DEXSCREENER_TOP_BOOSTS_URL = "https://api.dexscreener.com/token-boosts/top/v1"
DEXSCREENER_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/{mint}"
DEXSCREENER_PAIRS_URL = "https://api.dexscreener.com/latest/dex/pairs/solana/{pair}"
RUGCHECK_URL = "https://api.rugcheck.xyz/v1/tokens/{mint}/report"
BIRDEYE_OVERVIEW_URL = "https://public-api.birdeye.so/defi/token_overview"
BIRDEYE_TRENDING_URL = "https://public-api.birdeye.so/defi/token_trending"
TWITTER_SEARCH_URL = "https://api.twitter.com/2/tweets/search/recent"
TELEGRAM_SEND_URL = "https://api.telegram.org/bot{token}/sendMessage"

# Copy-trade filters (milkybids-style: graduated tokens from vetted wallets)
COPY_MIN_TRADER_SOL = 0.3
COPY_MAX_TRADER_SOL = 20.0
COPY_MAX_MARKET_CAP_USD = 800_000
COPY_GRADUATED_ONLY = True          # only copy tokens on Raydium/Orca (left pump.fun curve)
COPY_MIN_GRADUATED_LIQUIDITY_USD = 15_000
COPY_SKIP_IF_HOLDING = True         # don't buy same token twice
COPY_REBUY_COOLDOWN_HOURS = 24     # after a losing exit, don't copy-buy same token again

# Scanner settings — autonomous discovery (Axiom Pulse "graduated" style)
SCAN_INTERVAL_SECONDS  = 12
SCAN_MIN_SCORE         = 62
SCAN_MIN_LIQUIDITY_USD = 15_000   # graduated pool minimum
SCAN_MIN_MCAP_USD      = 25_000   # skip micro-dead coins
SCAN_MAX_MCAP_USD      = 600_000  # memecoin sweet spot
SCAN_MIN_AGE_HOURS     = 0.5      # at least 30 min old
SCAN_GRADUATED_ONLY    = True     # Raydium/Orca/Meteora only — sellable
SCAN_REQUIRE_SELL_TEST = True     # verify Jupiter sell route BEFORE buying
SCANNER_BUY_SOL        = 0.03     # small size for ~$30 balance

# Wallet tracker settings
WALLET_POLL_INTERVAL_SECONDS = 20  # 8 wallets × 1s gap = ~28s per full cycle, stays under free tier

# Risk manager settings
RISK_POLL_INTERVAL_SECONDS = 5
TP1_MULTIPLIER = 1.5      # sell 25% at 1.5x — bank profit before dump
TP2_MULTIPLIER = 3.0      # sell 25% at 3x
TP3_MULTIPLIER = 10.0    # sell 25% at 10x
TP1_SELL_PCT = 25.0
TP2_SELL_PCT = 25.0
TP3_SELL_PCT = 25.0       # keeps 25% riding with trailing stop for 100x+
STOP_LOSS_PCT = -20.0     # tighter stop — cut losses faster
TRAILING_STOP_PCT = -15.0 # tighter trailing — protect gains after big pump
TRAILING_ACTIVATION_MULTIPLIER = 3.0
TIME_STOP_MINUTES = 15        # flat for 15 min → sell
TIME_STOP_MIN_MULTIPLIER = 1.1  # needs 1.1x in 15 min or exit
MAX_HOLD_MINUTES = 45         # never hold longer than 45 min — force sell

# General
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 1.5
JUPITER_MIN_INTERVAL_SEC = 1.5       # min gap between Jupiter quote/swap calls
JUPITER_429_RETRIES = 5              # extra retries when rate-limited
BUY_MINT_COOLDOWN_SEC = 300          # don't buy same token again within 5 min
DEFAULT_SLIPPAGE_BPS = 300
SELL_SLIPPAGE_BPS = 1000          # 10% slippage on sells — meme coins move fast
SELL_SLIPPAGE_RETRY_BPS = [1000, 2500, 5000, 10000]
SELL_PRIORITY_FEE_LAMPORTS = 300_000

# All sells swap to USDC (dollars) — falls back to SOL only if no USDC route
SELL_TO_STABLE = True
EXIT_MINTS: list[str] = [USDC_MINT] if SELL_TO_STABLE else [SOL_MINT]
EXIT_DECIMALS: dict[str, int] = {USDC_MINT: 6, SOL_MINT: 9}
EXIT_LABELS: dict[str, str] = {
    USDC_MINT: "USDC",
    SOL_MINT: "SOL",
}
EXIT_MINT = EXIT_MINTS[0]
EXIT_LABEL = EXIT_LABELS[EXIT_MINT]
MIN_SELL_VALUE_USD = 0.50   # skip dust sells that spam alerts and waste fees
DUST_BALANCE_USD = 0.25     # treat tiny leftover as sold — stop retry loop
