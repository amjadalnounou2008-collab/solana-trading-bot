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


TRADERS: list[TraderConfig] = [
    TraderConfig("Cented", "@cented7", "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o", 0.05),
    TraderConfig("decu", "@notdecu", "4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9", 0.1),
    TraderConfig("trunoest", "@trunoest", "ardinRsN1mNYVeoJWTBsWeYeXvuR9UUDGMsCDKpb6AT", 0.1),
    TraderConfig("Cupsey", "@cupseyy", "suqh5sHtr8HyJ7q8scBimULPkPpA557prMG47xCHQfK", 0.1),
    TraderConfig("Cupsey", "@cupseyy", "2fg5QD1eD7rzNNCsvnhmXFm5hqNgwTTG8p7kQ6f3rx6f", 0.1),
    TraderConfig("Radiance", "@radiancebrr", "FAicXNV5FVqtfbpn4Zccs71XcfGeyxBSGbqLDyDJZjke", 0.1),
    TraderConfig("HeyItsYolo", "@heyitsyolotv", "Av3xWHJ5EsoLZag6pr7LKbrGgLRTaykXomDD5kBhL9YQ", 0.05),
    TraderConfig("Colercooks", "@colercooks", "99xnE2zEFi8YhmKDaikc1EvH6ELTQJppnqUwMzmpLXrs", 0.1),
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
LAMPORTS_PER_SOL = 1_000_000_000

# API endpoints
HELIUS_RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
SOLANA_SEND_RPC_URL = "https://api.mainnet-beta.solana.com"
HELIUS_TX_URL = "https://api-mainnet.helius-rpc.com/v0/addresses/{address}/transactions"
JUPITER_QUOTE_URL = "https://api.jup.ag/swap/v1/quote"
JUPITER_SWAP_URL = "https://api.jup.ag/swap/v1/swap"
JUPITER_PRICE_URL = "https://api.jup.ag/price/v2"
DEXSCREENER_PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"
DEXSCREENER_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/{mint}"
DEXSCREENER_PAIRS_URL = "https://api.dexscreener.com/latest/dex/pairs/solana/{pair}"
RUGCHECK_URL = "https://api.rugcheck.xyz/v1/tokens/{mint}/report"
BIRDEYE_OVERVIEW_URL = "https://public-api.birdeye.so/defi/token_overview"
TWITTER_SEARCH_URL = "https://api.twitter.com/2/tweets/search/recent"
TELEGRAM_SEND_URL = "https://api.telegram.org/bot{token}/sendMessage"

# Copy-trade filters
COPY_MIN_TRADER_SOL = 0.3
COPY_MAX_TRADER_SOL = 20.0
COPY_MAX_MARKET_CAP_USD = 800_000

# Scanner settings
SCAN_INTERVAL_SECONDS  = 8
SCAN_MIN_SCORE         = 68    # raised — stricter filter
SCAN_MIN_LIQUIDITY_USD = 30_000  # must have at least $30k liquidity to buy
SCAN_MIN_AGE_HOURS     = 1.0   # must be at least 1 hour old (less likely to be fresh rug)

# Wallet tracker settings
WALLET_POLL_INTERVAL_SECONDS = 5

# Risk manager settings
RISK_POLL_INTERVAL_SECONDS = 5
TP1_MULTIPLIER = 2.0      # sell 25% at 2x — lock in quick profit
TP2_MULTIPLIER = 5.0      # sell 25% at 5x — let the rest ride
TP3_MULTIPLIER = 20.0     # sell 25% at 20x — catch the moonshots
TP1_SELL_PCT = 25.0
TP2_SELL_PCT = 25.0
TP3_SELL_PCT = 25.0       # keeps 25% riding with trailing stop for 100x+
STOP_LOSS_PCT = -20.0     # tighter stop — cut losses faster
TRAILING_STOP_PCT = -15.0 # tighter trailing — protect gains after big pump
TRAILING_ACTIVATION_MULTIPLIER = 3.0
TIME_STOP_MINUTES = 30    # dump faster if not moving (30 min instead of 45)
TIME_STOP_MIN_MULTIPLIER = 1.3  # needs at least 1.3x in 30 min or we exit

# General
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 1.5
DEFAULT_SLIPPAGE_BPS = 300
