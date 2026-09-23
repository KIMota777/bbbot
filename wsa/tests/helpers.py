"""Синтетические данные для тестов: транзакции Solana, свечи, фейковый контекст."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from wsa.chains.solana import RENT_TOKEN_ACCOUNT, WSOL
from wsa.config import load_config
from wsa.db import DB

USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
TOKEN = "TokenXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX1"
TOKEN2 = "TokenYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYY2"
W = "WaLLetAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
POOL = "PooLBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


def tb(idx: int, mint: str, owner: str, amount: int, decimals: int = 6) -> dict:
    return {"accountIndex": idx, "mint": mint, "owner": owner,
            "uiTokenAmount": {"amount": str(amount), "decimals": decimals, "uiAmountString": str(amount / 10 ** decimals)}}


def sol_tx(sig: str, lamports_pre: list[int], lamports_post: list[int], pre_tokens: list[dict],
           post_tokens: list[dict], keys: list[str] | None = None, fee: int = 5000, err=None,
           programs: list[str] | None = None, ts: int = 1_700_000_000) -> dict:
    keys = keys or [W, POOL] + [f"Acc{i}" for i in range(len(lamports_pre) - 2)]
    return {
        "blockTime": ts, "slot": 1,
        "meta": {"err": err, "fee": fee, "preBalances": lamports_pre, "postBalances": lamports_post,
                 "preTokenBalances": pre_tokens, "postTokenBalances": post_tokens},
        "transaction": {"signatures": [sig],
                        "message": {"accountKeys": [{"pubkey": k, "signer": i == 0} for i, k in enumerate(keys)],
                                    "instructions": [{"programId": p} for p in (programs or ["JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"])]}},
    }


def leg(ts, side, token, qty, usd, sig=None, idx=0):
    return {"ts": ts, "side": side, "token": token, "qty": qty, "usd": usd, "sig": sig or f"s{ts}{side}{token}", "idx": idx,
            "price": (usd / qty) if usd else None}


def bars_linear(start: int, n: int, step: int, p0: float, drift: float = 0.0, vol: float = 1000.0) -> list[list[float]]:
    out, p = [], p0
    for i in range(n):
        o = p
        c = p * (1 + drift)
        out.append([start + i * step, o, max(o, c) * 1.001, min(o, c) * 0.999, c, vol])
        p = c
    return out


class FakeCtx:
    """Минимальный контекст для тестов движка и риска: настоящая база во временной папке."""

    def __init__(self):
        self.cfg = load_config("/nonexistent.toml")
        self.tmp = tempfile.mkdtemp(prefix="wsa-test-")
        self.cfg.general.db_path = f"{self.tmp}/t.db"
        self.db = DB(self.cfg.general.db_path)


__all__ = ["USDC", "TOKEN", "TOKEN2", "W", "POOL", "WSOL", "RENT_TOKEN_ACCOUNT", "tb", "sol_tx", "leg",
           "bars_linear", "FakeCtx"]
