from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import aiohttp

from config import (
    BIRDEYE_API_KEY,
    BIRDEYE_OVERVIEW_URL,
    DEXSCREENER_PROFILES_URL,
    DEXSCREENER_TOKEN_URL,
    RUGCHECK_URL,
    SCAN_INTERVAL_SECONDS,
    SCAN_MIN_LIQUIDITY_USD,
    SCAN_MIN_AGE_HOURS,
    SCAN_MIN_SCORE,
    TWITTER_BEARER_TOKEN,
    TWITTER_SEARCH_URL,
)
from modules.utils import clamp, fetch_json

GMGN_TRENDING_URL = "https://gmgn.ai/defi/quotation/v1/rank/sol/swaps/1h"
GMGN_SIGNALS_URL  = "https://gmgn.ai/defi/quotation/v1/signals/sol"
GMGN_TOKEN_URL    = "https://gmgn.ai/defi/quotation/v1/tokens/sol/{mint}"

GMGN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://gmgn.ai/",
    "Origin": "https://gmgn.ai",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}

if TYPE_CHECKING:
    from modules.executor import Executor

logger = logging.getLogger("solana-bot.coin_scanner")

DEFAULT_BUY_SOL = 0.05  # reduced — smaller risk per trade


class CoinScanner:
    def __init__(self, session: aiohttp.ClientSession, executor: "Executor") -> None:
        self.session = session
        self.executor = executor
        self._seen_mints: set[str] = set()
        self._running = False

    async def _fetch_latest_profiles(self) -> list[dict[str, Any]]:
        data = await fetch_json(
            self.session,
            "GET",
            DEXSCREENER_PROFILES_URL,
            label="DexScreener profiles",
        )
        if not isinstance(data, list):
            return []
        return [p for p in data if p.get("chainId") == "solana"]

    async def _fetch_pair_data(self, mint: str) -> dict[str, Any] | None:
        url = DEXSCREENER_TOKEN_URL.format(mint=mint)
        data = await fetch_json(self.session, "GET", url, label=f"DexScreener token {mint[:8]}")
        pairs = data.get("pairs") or []
        if not pairs:
            return None
        return max(pairs, key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0))

    async def _rugcheck_score(self, mint: str) -> tuple[bool, float]:
        """
        RugCheck score: 0 = safe, higher = MORE RISKY.
        We reject anything above 500 (medium-high risk).
        Returns (is_safe, risk_score).
        """
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
            rugged = data.get("rugged", False)
            # Reject if score too high (risky) or flagged
            too_risky = score > 500
            return not (is_honeypot or rugged or too_risky), score
        except Exception:
            return True, 50.0

    async def _birdeye_data(self, mint: str) -> dict[str, Any]:
        if not BIRDEYE_API_KEY or BIRDEYE_API_KEY.startswith("your_"):
            return {}
        try:
            headers = {"X-API-KEY": BIRDEYE_API_KEY, "x-chain": "solana"}
            data = await fetch_json(
                self.session,
                "GET",
                BIRDEYE_OVERVIEW_URL,
                params={"address": mint},
                headers=headers,
                label=f"Birdeye {mint[:8]}",
            )
            return data.get("data", {}) or {}
        except Exception:
            return {}

    async def _twitter_mentions(self, symbol: str) -> int:
        if not TWITTER_BEARER_TOKEN or TWITTER_BEARER_TOKEN.startswith("your_"):
            return 0
        if not symbol or symbol == "UNKNOWN":
            return 0
        try:
            headers = {"Authorization": f"Bearer {TWITTER_BEARER_TOKEN}"}
            params = {
                "query": f"${symbol} OR #{symbol} -is:retweet lang:en",
                "max_results": 10,
                "tweet.fields": "created_at",
            }
            data = await fetch_json(
                self.session,
                "GET",
                TWITTER_SEARCH_URL,
                params=params,
                headers=headers,
                label=f"Twitter search {symbol}",
            )
            return data.get("meta", {}).get("result_count", 0)
        except Exception:
            return 0

    def _score_token(
        self,
        pair: dict[str, Any],
        rugcheck_ok: bool,
        rugcheck_score: float,
        birdeye: dict[str, Any],
        twitter_mentions: int,
    ) -> tuple[float, dict[str, Any]]:
        liquidity_usd = float(pair.get("liquidity", {}).get("usd", 0) or 0)
        market_cap = float(pair.get("marketCap") or pair.get("fdv") or 0)
        volume_24h = float(pair.get("volume", {}).get("h24", 0) or 0)
        price_change_5m = float(pair.get("priceChange", {}).get("m5", 0) or 0)
        price_change_1h = float(pair.get("priceChange", {}).get("h1", 0) or 0)
        pair_created = pair.get("pairCreatedAt")
        age_hours = 999.0
        if pair_created:
            created_dt = datetime.fromtimestamp(pair_created / 1000, tz=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - created_dt).total_seconds() / 3600

        buy_pressure = max(price_change_5m, price_change_1h * 0.3, 0)

        # Liquidity (0-20)
        if liquidity_usd >= 100_000:
            liquidity_score = 20
        elif liquidity_usd >= 50_000:
            liquidity_score = 16
        elif liquidity_usd >= 20_000:
            liquidity_score = 12
        elif liquidity_usd >= 10_000:
            liquidity_score = 8
        elif liquidity_usd >= 5_000:
            liquidity_score = 4
        else:
            liquidity_score = 0

        # Market cap (0-15) — sweet spot for memecoins
        if 50_000 <= market_cap <= 500_000:
            mcap_score = 15
        elif 20_000 <= market_cap < 50_000:
            mcap_score = 12
        elif 500_000 < market_cap <= 1_000_000:
            mcap_score = 10
        elif 1_000_000 < market_cap <= 2_000_000:
            mcap_score = 6
        else:
            mcap_score = 3

        # Age (0-15) — newer tokens score higher
        if age_hours <= 1:
            age_score = 15
        elif age_hours <= 6:
            age_score = 12
        elif age_hours <= 24:
            age_score = 8
        elif age_hours <= 72:
            age_score = 4
        else:
            age_score = 1

        # Buy pressure (0-20)
        buy_pressure_score = clamp(buy_pressure * 2, 0, 20)

        # Volume (0-15)
        if volume_24h >= 500_000:
            volume_score = 15
        elif volume_24h >= 200_000:
            volume_score = 12
        elif volume_24h >= 100_000:
            volume_score = 9
        elif volume_24h >= 50_000:
            volume_score = 6
        elif volume_24h >= 10_000:
            volume_score = 3
        else:
            volume_score = 0

        # Twitter mentions (0-15)
        if twitter_mentions >= 50:
            twitter_score = 15
        elif twitter_mentions >= 20:
            twitter_score = 12
        elif twitter_mentions >= 10:
            twitter_score = 9
        elif twitter_mentions >= 5:
            twitter_score = 6
        elif twitter_mentions >= 1:
            twitter_score = 3
        else:
            twitter_score = 0

        # RugCheck safety score (0-10) — lower risk score = safer = more points
        if not rugcheck_ok:
            safety_score = 0
        elif rugcheck_score <= 100:
            safety_score = 10   # very safe
        elif rugcheck_score <= 200:
            safety_score = 7
        elif rugcheck_score <= 350:
            safety_score = 4
        else:
            safety_score = 1   # borderline — passes 500 filter but still risky

        # Birdeye buy/sell ratio bonus (0-5)
        buy_24h = float(birdeye.get("buy24h", 0) or 0)
        sell_24h = float(birdeye.get("sell24h", 0) or 0)
        if buy_24h + sell_24h > 0:
            buy_ratio = buy_24h / (buy_24h + sell_24h)
            birdeye_score = clamp(buy_ratio * 5, 0, 5)
        else:
            birdeye_score = 0

        total = (
            liquidity_score
            + mcap_score
            + age_score
            + buy_pressure_score
            + volume_score
            + twitter_score
            + safety_score
            + birdeye_score
        )
        total = clamp(total, 0, 100)

        breakdown = {
            "liquidity": f"{liquidity_score}/20 (${liquidity_usd:,.0f})",
            "market_cap": f"{mcap_score}/15 (${market_cap:,.0f})",
            "age": f"{age_score}/15 ({age_hours:.1f}h)",
            "buy_pressure": f"{buy_pressure_score:.0f}/20 ({buy_pressure:.1f}%)",
            "volume": f"{volume_score}/15 (${volume_24h:,.0f})",
            "twitter": f"{twitter_score}/15 ({twitter_mentions} mentions)",
            "rugcheck": f"{safety_score:.0f}/10 (score {rugcheck_score:.0f})",
            "birdeye_buy_ratio": f"{birdeye_score:.1f}/5",
            "total": f"{total:.0f}/100",
        }
        return total, breakdown

    async def _evaluate_token(self, mint: str) -> None:
        if mint in self._seen_mints:
            return

        pair = await self._fetch_pair_data(mint)
        if not pair:
            self._seen_mints.add(mint)
            return

        symbol = pair.get("baseToken", {}).get("symbol", "UNKNOWN")

        # Hard filters — skip before spending API calls
        liquidity_usd = float(pair.get("liquidity", {}).get("usd", 0) or 0)
        if liquidity_usd < SCAN_MIN_LIQUIDITY_USD:
            logger.info("Scanner skip %s — liquidity $%.0f < $%.0f minimum",
                        symbol, liquidity_usd, SCAN_MIN_LIQUIDITY_USD)
            self._seen_mints.add(mint)
            return

        pair_created = pair.get("pairCreatedAt")
        if pair_created:
            age_hours = (datetime.now(timezone.utc) - datetime.fromtimestamp(
                pair_created / 1000, tz=timezone.utc)).total_seconds() / 3600
            if age_hours < SCAN_MIN_AGE_HOURS:
                logger.info("Scanner skip %s — only %.1fh old (min %.1fh)",
                            symbol, age_hours, SCAN_MIN_AGE_HOURS)
                self._seen_mints.add(mint)
                return

        rugcheck_ok, rugcheck_score = await self._rugcheck_score(mint)
        if not rugcheck_ok:
            logger.info("Scanner skip %s — RugCheck flagged (score %.0f)", symbol, rugcheck_score)
            self._seen_mints.add(mint)
            return

        birdeye = await self._birdeye_data(mint)
        twitter_mentions = await self._twitter_mentions(symbol)
        score, breakdown = self._score_token(pair, rugcheck_ok, rugcheck_score, birdeye, twitter_mentions)

        logger.info(
            "Scanned %s (%s) — score %.0f/100 | liq $%s | mcap $%s",
            symbol,
            mint[:8],
            score,
            f"{float(pair.get('liquidity', {}).get('usd', 0) or 0):,.0f}",
            f"{float(pair.get('marketCap') or pair.get('fdv') or 0):,.0f}",
        )

        self._seen_mints.add(mint)

        if score >= SCAN_MIN_SCORE:
            # Verify Jupiter can actually price this token before buying
            jupiter_price = await self.executor.get_token_price_usd(mint)
            if not jupiter_price or jupiter_price <= 0:
                logger.info(
                    "Scanner skip %s (score %.0f) — Jupiter has no price, token not tradeable yet",
                    symbol, score,
                )
                return

            reason = f"Autonomous discovery — score {score:.0f}/100 (threshold {SCAN_MIN_SCORE})"
            logger.info("BUY signal from scanner — %s scored %.0f", symbol, score)
            await self.executor.buy_token(
                mint=mint,
                amount_sol=DEFAULT_BUY_SOL,
                reason=reason,
                symbol=symbol,
                score_breakdown=breakdown,
            )

    async def _fetch_gmgn_trending(self) -> list[str]:
        """Fetch trending tokens from GMGN ranked by 1h swap volume."""
        try:
            params = {
                "limit": "20",
                "orderby": "swaps",
                "direction": "desc",
                "filters[]": ["renounced", "frozen"],
            }
            data = await fetch_json(
                self.session, "GET", GMGN_TRENDING_URL,
                params=params, headers=GMGN_HEADERS, label="GMGN trending",
            )
            tokens = data.get("data", {}).get("rank", []) or []
            mints = [t.get("address", "") for t in tokens if t.get("address")]
            if mints:
                logger.info("GMGN trending: %d tokens", len(mints))
            return mints
        except Exception as exc:
            logger.warning("GMGN trending fetch failed: %s", exc)
            return []

    async def _fetch_gmgn_signals(self) -> list[str]:
        """Fetch smart money buy signals from GMGN."""
        try:
            data = await fetch_json(
                self.session, "GET", GMGN_SIGNALS_URL,
                headers=GMGN_HEADERS, label="GMGN signals",
            )
            signals = data.get("data", []) or []
            mints = [s.get("token_address", "") for s in signals if s.get("token_address")]
            if mints:
                logger.info("GMGN smart money signals: %d token(s)", len(mints))
            return mints
        except Exception as exc:
            logger.warning("GMGN signals fetch failed: %s", exc)
            return []

    async def _evaluate_gmgn_token(self, mint: str, source: str) -> None:
        """Evaluate a GMGN-sourced token — uses lower score threshold since pre-filtered."""
        if mint in self._seen_mints:
            return
        self._seen_mints.add(mint)

        try:
            pair = await self._fetch_pair_data(mint)
            if not pair:
                return

            symbol = pair.get("baseToken", {}).get("symbol", "UNKNOWN")
            rugcheck_ok, rugcheck_score = await self._rugcheck_score(mint)
            if not rugcheck_ok:
                logger.info("GMGN skip %s — RugCheck flagged", symbol)
                return

            birdeye = await self._birdeye_data(mint)
            twitter_mentions = await self._twitter_mentions(symbol)
            score, breakdown = self._score_token(pair, rugcheck_ok, rugcheck_score, birdeye, twitter_mentions)

            # GMGN tokens get a 10-point boost — pre-filtered by smart money
            boosted_score = min(score + 10, 100)
            logger.info(
                "GMGN [%s] %s (%s) — score %d/100 (boosted from %d) | liq $%s",
                source, symbol, mint[:8], boosted_score, score,
                f"{float(pair.get('liquidity', {}).get('usd', 0) or 0):,.0f}",
            )

            threshold = SCAN_MIN_SCORE - 5  # slightly lower threshold for GMGN (57)
            if boosted_score >= threshold:
                # Verify Jupiter can price this token before buying
                jupiter_price = await self.executor.get_token_price_usd(mint)
                if not jupiter_price or jupiter_price <= 0:
                    logger.info(
                        "GMGN skip %s (score %d) — Jupiter has no price, skipping",
                        symbol, boosted_score,
                    )
                    return

                reason = f"GMGN {source} — score {boosted_score}/100 (raw {score}, threshold {threshold})"
                logger.info("BUY signal from GMGN [%s] — %s scored %d", source, symbol, boosted_score)
                await self.executor.buy_token(
                    mint=mint,
                    amount_sol=DEFAULT_BUY_SOL,
                    reason=reason,
                    symbol=symbol,
                    score_breakdown=breakdown,
                )
        except Exception as exc:
            logger.error("Error evaluating GMGN token %s: %s", mint[:8], exc)

    async def _scan_cycle(self) -> None:
        # ── DexScreener scan ──────────────────────────────────────────────────
        profiles = await self._fetch_latest_profiles()
        solana_tokens = [
            p.get("tokenAddress", "") for p in profiles
            if p.get("tokenAddress") and p.get("tokenAddress") not in self._seen_mints
        ]
        if solana_tokens:
            logger.info("Scanner found %d new Solana token(s) to evaluate", len(solana_tokens))
        for mint in solana_tokens[:15]:
            try:
                await self._evaluate_token(mint)
            except Exception as exc:
                logger.error("Error evaluating %s: %s", mint[:8], exc)

        # GMGN disabled — Railway datacenter IPs are blocked by GMGN (403)
        # DexScreener scanning handles discovery instead

    async def run(self) -> None:
        self._running = True
        logger.info(
            "Coin scanner started — DexScreener + GMGN every %ds (min score %d)",
            SCAN_INTERVAL_SECONDS,
            SCAN_MIN_SCORE,
        )

        while self._running:
            try:
                await self._scan_cycle()
            except Exception as exc:
                logger.error("Scanner cycle error: %s", exc)
            await asyncio.sleep(SCAN_INTERVAL_SECONDS)

    def stop(self) -> None:
        self._running = False
