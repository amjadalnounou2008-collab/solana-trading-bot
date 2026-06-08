from __future__ import annotations

import asyncio
import logging
import signal

import aiohttp

import config
from modules.alerter import Alerter
from modules.coin_scanner import CoinScanner
from modules.executor import Executor
from modules.risk_manager import RiskManager
from modules.utils import setup_logging
from modules.wallet_tracker import WalletTracker

logger = logging.getLogger("solana-bot")


async def main() -> None:
    setup_logging()

    mode = "PAPER TRADE" if config.PAPER_TRADE else "LIVE TRADING"
    logger.info("=" * 60)
    logger.info("Solana Memecoin AI Trading Bot starting — %s", mode)
    logger.info("Tracking %d trader wallets", len(config.TRADERS))
    logger.info("Scanner interval: %ds | Wallet poll: %ds", config.SCAN_INTERVAL_SECONDS, config.WALLET_POLL_INTERVAL_SECONDS)
    logger.info("=" * 60)

    if not config.HELIUS_API_KEY or config.HELIUS_API_KEY.startswith("your_"):
        logger.warning("HELIUS_API_KEY not set — wallet tracking will fail")

    # Force Google DNS — Railway's default DNS can't resolve jup.ag domains
    connector = aiohttp.TCPConnector(
        resolver=aiohttp.AsyncResolver(nameservers=["8.8.8.8", "1.1.1.1"]),
        ttl_dns_cache=300,
    )
    async with aiohttp.ClientSession(connector=connector) as session:
        alerter = Alerter(session)
        executor = Executor(session)
        risk_manager = RiskManager(executor=executor, alerter=alerter)
        await risk_manager.initialize()   # connects to PostgreSQL, loads open positions
        executor.risk_manager = risk_manager

        wallet_tracker = WalletTracker(session, executor)
        coin_scanner = CoinScanner(session, executor)

        await alerter.send_startup_message()
        logger.info("Wallet: %s", executor.public_key)

        shutdown_event = asyncio.Event()

        def _handle_signal() -> None:
            logger.info("Shutdown signal received — stopping bot...")
            shutdown_event.set()
            wallet_tracker.stop()
            coin_scanner.stop()
            risk_manager.stop()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _handle_signal)

        async def _run_until_shutdown() -> None:
            await shutdown_event.wait()

        logger.info("All modules running concurrently via asyncio.gather()")

        results = await asyncio.gather(
            wallet_tracker.run(),
            coin_scanner.run(),
            risk_manager.run(),
            _run_until_shutdown(),
            return_exceptions=True,
        )

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error("Module %d raised: %s", i, result)

        logger.info("Bot stopped cleanly")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
