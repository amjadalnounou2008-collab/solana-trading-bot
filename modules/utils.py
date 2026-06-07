from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any, Callable, TypeVar

import aiohttp

from config import MAX_RETRIES, RETRY_DELAY_SECONDS

T = TypeVar("T")

logger = logging.getLogger("solana-bot")


def setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(handler)


async def retry_async(
    coro_factory: Callable[[], Any],
    retries: int = MAX_RETRIES,
    delay: float = RETRY_DELAY_SECONDS,
    label: str = "operation",
) -> T:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return await coro_factory()
        except Exception as exc:
            last_error = exc
            logger.warning(
                "%s failed (attempt %d/%d): %s",
                label,
                attempt,
                retries,
                exc,
            )
            if attempt < retries:
                await asyncio.sleep(delay * attempt)
    raise last_error  # type: ignore[misc]


async def fetch_json(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    *,
    params: dict | None = None,
    json_body: dict | None = None,
    headers: dict | None = None,
    label: str = "API request",
) -> Any:
    async def _do() -> Any:
        async with session.request(
            method,
            url,
            params=params,
            json=json_body,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    return await retry_async(_do, label=label)


def sol_to_lamports(sol: float) -> int:
    return int(sol * 1_000_000_000)


def lamports_to_sol(lamports: int | float) -> float:
    return float(lamports) / 1_000_000_000


def format_usd(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"${value:,.2f}"


def format_sol(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.4f} SOL"


def format_duration(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))
