"""DexScreener: снимок пары/токена (цена, ликвидность, MC, FDV, возраст пула).

Трейдеров публичный API не отдаёт — только рынок. Лимит 300 запросов/мин.
"""
from __future__ import annotations

import re

from ..util import chunks

BASE = "https://api.dexscreener.com"

# сеть DexScreener -> наше имя сети
DS_CHAINS = {
    "solana": "solana", "ethereum": "ethereum", "base": "base", "arbitrum": "arbitrum",
    "optimism": "optimism", "polygon": "polygon", "bsc": "bsc",
}
OUR_TO_DS = {v: k for k, v in DS_CHAINS.items()}


def parse_url(url: str) -> tuple[str, str]:
    """https://dexscreener.com/solana/<pair> -> ('solana', '<pair>').

    Адрес из ссылки бывает в нижнем регистре — для Solana он недействителен
    (base58 чувствителен к регистру), поэтому канонический адрес берём из ответа API.
    """
    m = re.search(r"dexscreener\.com/([a-z0-9_-]+)/([A-Za-z0-9]+)", url)
    if not m:
        raise ValueError(f"не похоже на ссылку DexScreener: {url}")
    chain = DS_CHAINS.get(m.group(1))
    if not chain:
        raise ValueError(f"сеть {m.group(1)} пока не поддерживается")
    return chain, m.group(2)


def clean_symbol(sym: str | None) -> str:
    return (sym or "").strip().lstrip("$").upper()


class DexScreener:
    def __init__(self, http):
        self.http = http
        http.set_rate("api.dexscreener.com", 4.0, burst=8)

    def pair(self, chain: str, pair: str) -> dict | None:
        d = self.http.get_json(f"{BASE}/latest/dex/pairs/{OUR_TO_DS.get(chain, chain)}/{pair}", ttl=20)
        pairs = d.get("pairs") or ([d["pair"]] if d.get("pair") else [])
        return pairs[0] if pairs else None

    def tokens(self, chain: str, addresses: list[str], ttl: int = 60) -> dict[str, dict]:
        """Лучшая пара по каждому токену, до 30 адресов за запрос."""
        out: dict[str, dict] = {}
        ds_chain = OUR_TO_DS.get(chain, chain)
        for part in chunks(list(dict.fromkeys(addresses)), 30):
            data = self.http.get_json(f"{BASE}/tokens/v1/{ds_chain}/{','.join(part)}", ttl=ttl)
            for p in data or []:
                addr = p.get("baseToken", {}).get("address")
                if not addr:
                    continue
                prev = out.get(addr)
                if prev is None or (p.get("liquidity") or {}).get("usd", 0) > (prev.get("liquidity") or {}).get("usd", 0):
                    out[addr] = p
        return out

    def token_pairs(self, chain: str, token: str, ttl: int = 3600) -> list[dict]:
        data = self.http.get_json(f"{BASE}/token-pairs/v1/{OUR_TO_DS.get(chain, chain)}/{token}", ttl=ttl)
        return data or []

    def search(self, q: str) -> list[dict]:
        d = self.http.get_json(f"{BASE}/latest/dex/search", params={"q": q}, ttl=60)
        return d.get("pairs") or []


def snapshot_from_pair(p: dict) -> dict:
    """Плоский снимок пары для token_snapshots / контекста входа."""
    liq = p.get("liquidity") or {}
    vol = p.get("volume") or {}
    chg = p.get("priceChange") or {}
    tx = (p.get("txns") or {}).get("h24") or {}

    def f(x):
        try:
            return float(x) if x is not None else None
        except (TypeError, ValueError):
            return None

    return {
        "price": f(p.get("priceUsd")),
        "mc": f(p.get("marketCap")),
        "fdv": f(p.get("fdv")),
        "liquidity": f(liq.get("usd")),
        "vol24": f(vol.get("h24")),
        "vol6": f(vol.get("h6")),
        "vol1": f(vol.get("h1")),
        "chg_5m": (f(chg.get("m5")) or 0) / 100 if chg.get("m5") is not None else None,
        "chg_1h": (f(chg.get("h1")) or 0) / 100 if chg.get("h1") is not None else None,
        "chg_6h": (f(chg.get("h6")) or 0) / 100 if chg.get("h6") is not None else None,
        "chg_24h": (f(chg.get("h24")) or 0) / 100 if chg.get("h24") is not None else None,
        "buys_24h": tx.get("buys"),
        "sells_24h": tx.get("sells"),
        "pair_created": int(p["pairCreatedAt"] / 1000) if p.get("pairCreatedAt") else None,
        "pair": p.get("pairAddress"),
        "dex": p.get("dexId"),
        "symbol": clean_symbol((p.get("baseToken") or {}).get("symbol")),
        "name": (p.get("baseToken") or {}).get("name"),
    }
