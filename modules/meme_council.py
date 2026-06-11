"""
Meme Council — rule-based multi-agent gate (HERMES-style, zero LLM credits).

Five agents vote before any scanner/copy buy. Default: 4/5 must approve.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from config import (
    COPY_REBUY_COOLDOWN_HOURS,
    SCAN_MIN_LIQUIDITY_USD,
    SCAN_MIN_MCAP_USD,
    SCAN_MAX_MCAP_USD,
    SCAN_PUMPFUN_ALLOW_BONDING,
    SCAN_PUMPFUN_BONDING_MIN_PCT,
    SCAN_PUMPFUN_MIN_USD_MCAP,
)

logger = logging.getLogger("solana-bot.council")

GRADUATED_DEXES = {"raydium", "orca", "meteora", "pumpswap"}


class Vote(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    ABSTAIN = "abstain"


@dataclass
class AgentVote:
    name: str
    code: str
    vote: Vote
    reason: str


@dataclass
class CouncilResult:
    approved: bool
    score: str
    votes: list[AgentVote] = field(default_factory=list)

    def summary_lines(self) -> list[str]:
        icons = {
            Vote.APPROVE: "✅",
            Vote.REJECT: "❌",
            Vote.ABSTAIN: "⏭",
        }
        lines = [f"<b>🛡️ Meme Council {self.score}</b>"]
        for v in self.votes:
            lines.append(f"{icons[v.vote]} <b>{v.name}</b> — {v.reason}")
        return lines


@dataclass
class TokenCandidate:
    mint: str
    symbol: str
    pair: dict[str, Any]
    rugcheck_ok: bool
    rugcheck_score: float
    birdeye: dict[str, Any]
    twitter_mentions: int
    score: float
    breakdown: dict[str, Any]
    source: str = "scanner"
    sell_route_ok: bool = True
    on_loss_cooldown: bool = False
    prior_losses: int = 0


def _guard(candidate: TokenCandidate) -> AgentVote:
    if candidate.on_loss_cooldown:
        return AgentVote(
            "GUARD", "GRD", Vote.REJECT,
            f"loss cooldown ({COPY_REBUY_COOLDOWN_HOURS}h) — traded before",
        )
    if candidate.prior_losses >= 2:
        return AgentVote(
            "GUARD", "GRD", Vote.REJECT,
            f"burned {candidate.prior_losses}x before on this mint",
        )
    if not candidate.rugcheck_ok:
        return AgentVote(
            "GUARD", "GRD", Vote.REJECT,
            f"RugCheck fail (score {candidate.rugcheck_score:.0f})",
        )
    return AgentVote(
        "GUARD", "GRD", Vote.APPROVE,
        f"RugCheck ok (score {candidate.rugcheck_score:.0f})",
    )


def _depth(candidate: TokenCandidate) -> AgentVote:
    liq = float(candidate.pair.get("liquidity", {}).get("usd", 0) or 0)
    mcap = float(candidate.pair.get("marketCap") or candidate.pair.get("fdv") or 0)
    dex = (candidate.pair.get("dexId") or "?").lower()
    pump = candidate.pair.get("pumpfun") or {}
    progress = float(pump.get("bonding_progress", 0) or 0)
    on_bonding = dex == "pump.fun" or (not pump.get("complete") and progress > 0)

    if on_bonding and SCAN_PUMPFUN_ALLOW_BONDING:
        if progress >= SCAN_PUMPFUN_BONDING_MIN_PCT and mcap >= SCAN_PUMPFUN_MIN_USD_MCAP * 0.5:
            return AgentVote(
                "DEPTH", "DEP", Vote.APPROVE,
                f"pump.fun graduating {progress:.0f}% | mcap ${mcap:,.0f}",
            )
        return AgentVote(
            "DEPTH", "DEP", Vote.REJECT,
            f"pump bonding too early ({progress:.0f}% / need {SCAN_PUMPFUN_BONDING_MIN_PCT:.0f}%)",
        )

    if liq < SCAN_MIN_LIQUIDITY_USD:
        return AgentVote("DEPTH", "DEP", Vote.REJECT, f"liq ${liq:,.0f} too thin")
    if mcap < SCAN_MIN_MCAP_USD or mcap > SCAN_MAX_MCAP_USD:
        return AgentVote("DEPTH", "DEP", Vote.REJECT, f"mcap ${mcap:,.0f} out of range")
    if dex not in GRADUATED_DEXES:
        return AgentVote("DEPTH", "DEP", Vote.REJECT, f"not graduated (dex={dex})")
    return AgentVote(
        "DEPTH", "DEP", Vote.APPROVE,
        f"liq ${liq:,.0f} | mcap ${mcap:,.0f} | {dex}",
    )


def _flow(candidate: TokenCandidate) -> AgentVote:
    txns = candidate.pair.get("txns", {}) or {}
    h24 = txns.get("h24", {}) or {}
    buys = float(h24.get("buys", 0) or 0)
    sells = float(h24.get("sells", 0) or 0)
    total = buys + sells
    pressure = (buys / total * 100.0) if total > 0 else 50.0

    be = candidate.birdeye or {}
    be_buys = float(be.get("buy24h", 0) or 0)
    be_sells = float(be.get("sell24h", 0) or 0)
    be_total = be_buys + be_sells
    if be_total > 20:
        pressure = be_buys / be_total * 100.0

    vol = float(candidate.pair.get("volume", {}).get("h24", 0) or 0)
    if pressure >= 58 and vol >= 10_000:
        return AgentVote(
            "FLOW", "FLW", Vote.APPROVE,
            f"buy pressure {pressure:.0f}% | vol ${vol:,.0f}",
        )
    if pressure < 45:
        return AgentVote(
            "FLOW", "FLW", Vote.REJECT,
            f"sell-heavy {pressure:.0f}% buy pressure",
        )
    return AgentVote(
        "FLOW", "FLW", Vote.ABSTAIN,
        f"neutral flow {pressure:.0f}%",
    )


def _social(candidate: TokenCandidate) -> AgentVote:
    info = candidate.pair.get("info", {}) or {}
    websites = info.get("websites") or []
    socials = info.get("socials") or []
    has_social = bool(websites or socials)
    mentions = candidate.twitter_mentions

    if mentions >= 3:
        return AgentVote(
            "SOCIAL", "SOC", Vote.APPROVE,
            f"{mentions} Twitter mentions",
        )
    if has_social and candidate.score >= 70:
        return AgentVote("SOCIAL", "SOC", Vote.APPROVE, "DexScreener socials listed")
    if mentions == 0 and not has_social:
        return AgentVote("SOCIAL", "SOC", Vote.ABSTAIN, "no social signal")
    return AgentVote("SOCIAL", "SOC", Vote.APPROVE, "weak but present socials")


def _route(candidate: TokenCandidate) -> AgentVote:
    if not candidate.sell_route_ok:
        return AgentVote(
            "ROUTE", "RTE", Vote.REJECT,
            "no Jupiter sell route (honeypot risk)",
        )
    return AgentVote("ROUTE", "RTE", Vote.APPROVE, "sell route verified")


def evaluate(candidate: TokenCandidate, min_approve: int = 4) -> CouncilResult:
    votes = [
        _guard(candidate),
        _depth(candidate),
        _flow(candidate),
        _social(candidate),
        _route(candidate),
    ]
    approve = sum(1 for v in votes if v.vote == Vote.APPROVE)
    reject = sum(1 for v in votes if v.vote == Vote.REJECT)
    approved = approve >= min_approve and reject == 0
    score = f"{approve}/5"

    icons = " ".join(
        "✅" if v.vote == Vote.APPROVE else "❌" if v.vote == Vote.REJECT else "⏭"
        for v in votes
    )
    logger.info(
        "Council %s %s — %s (%s) %s",
        score, "FIRE" if approved else "SKIP",
        candidate.symbol, candidate.source, icons,
    )
    return CouncilResult(approved=approved, score=score, votes=votes)
