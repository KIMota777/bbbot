"""CoinGecko: сектор/категории токена и на каких CEX он торгуется.

Без ключа ~10 запросов/мин на IP, с бесплатным Demo-ключом (COINGECKO_API_KEY)
100/мин и 10 000 в месяц.
Ответы кэшируются надолго: категории и листинги меняются редко.
"""
from __future__ import annotations

from ..config import secret

BASE = "https://api.coingecko.com/api/v3"

CG_PLATFORMS = {
    "solana": "solana", "ethereum": "ethereum", "base": "base", "arbitrum": "arbitrum-one",
    "optimism": "optimistic-ethereum", "polygon": "polygon-pos", "bsc": "binance-smart-chain",
}

# идентификаторы рынков CoinGecko -> биржа ccxt
CG_EXCHANGES = {
    "bybit_spot": "bybit", "bybit": "bybit-futures", "binance": "binance", "okex": "okx", "gate": "gate",
    "kucoin": "kucoin", "bitget": "bitget", "mxc": "mexc", "huobi": "htx", "gdax": "coinbase",
    "kraken": "kraken",
}

# категории CoinGecko/GeckoTerminal -> канонический сектор (порядок = приоритет)
SECTOR_RULES = [
    ("Stablecoin", ("stablecoin",)),
    ("Meme", ("meme", "dog-themed", "cat-themed", "pump.fun", "frog")),
    ("AI", ("artificial intelligence", "ai agent", " ai", "ai ", "(ai)")),
    ("RWA", ("real world asset", "rwa", "tokenized")),
    ("DePIN", ("depin",)),
    ("Gaming", ("gaming", "gamefi", "play to earn", "metaverse")),
    ("NFT", ("nft",)),
    ("L2", ("layer 2", "(l2)", "rollup")),
    ("L1", ("layer 1", "(l1)", "smart contract platform")),
    ("DeFi", ("defi", "decentralized exchange", "dex", "lending", "yield", "liquid staking",
              "derivatives", "perpetual", "decentralized finance", "launchpad", "exchange-based")),
    ("Infrastructure", ("infrastructure", "oracle", "interoperability", "bridge", "storage",
                        "zero knowledge", "privacy", "identity", "wallet")),
]


def sector_of(categories: list[str] | None) -> str:
    cats = " | ".join(c.lower() for c in (categories or []))
    if not cats:
        return "Unknown"
    cats = f" {cats} "
    for sector, keys in SECTOR_RULES:
        if any(k in cats for k in keys):
            return sector
    return "Other"


class CoinGecko:
    def __init__(self, http):
        self.http = http
        self.key = secret("COINGECKO_API_KEY")
        http.set_rate("api.coingecko.com", 1.2 if self.key else 0.15, burst=2)

    def _get(self, path: str, params: dict | None = None, ttl: int = 86400):
        headers = {"x-cg-demo-api-key": self.key} if self.key else None
        return self.http.get_json(f"{BASE}{path}", params=params, headers=headers, ttl=ttl, retries=3)

    def by_contract(self, chain: str, address: str) -> dict | None:
        platform = CG_PLATFORMS.get(chain)
        if not platform:
            return None
        try:
            d = self._get(f"/coins/{platform}/contract/{address}",
                          {"localization": "false", "tickers": "true", "market_data": "true",
                           "community_data": "false", "developer_data": "false"}, ttl=7 * 86400)
        except Exception as e:  # 404 — токена нет на CoinGecko, это нормально
            if getattr(e, "status", None) == 404:
                return None
            raise
        tickers = d.get("tickers") or []
        exchanges = sorted({CG_EXCHANGES.get(t.get("market", {}).get("identifier"),
                                             t.get("market", {}).get("identifier"))
                            for t in tickers if t.get("market")})
        md = d.get("market_data") or {}
        return {
            "id": d.get("id"),
            "symbol": (d.get("symbol") or "").upper(),
            "name": d.get("name"),
            "categories": [c for c in (d.get("categories") or []) if c],
            "exchanges": exchanges,
            "genesis_date": d.get("genesis_date"),
            "mc": (md.get("market_cap") or {}).get("usd"),
            "fdv": (md.get("fully_diluted_valuation") or {}).get("usd"),
            "circulating": md.get("circulating_supply"),
            "total_supply": md.get("total_supply"),
        }

    def coins_list(self) -> list[dict]:
        """Все монеты с адресами контрактов по сетям (один большой запрос, кэш сутки)."""
        return self._get("/coins/list", {"include_platform": "true"}, ttl=86400) or []
