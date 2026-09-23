"""GeckoTerminal: сделки пула с адресами кошельков, свечи DEX, держатели.

Публичный API: ~10 запросов/мин (по документации CoinGecko, 2026), свечи —
только за последние ~180 дней, сделки — последние 300 за сутки (с фильтром
по объёму). Самый узкий источник системы: тратим его только на сделки пулов
(поиск кандидатов), держателей и свечи токенов, которых нет на CEX.
"""
from __future__ import annotations

from datetime import datetime

BASE = "https://api.geckoterminal.com/api/v2"
HEADERS = {"Accept": "application/json;version=20230302"}

GT_NETWORKS = {
    "solana": "solana", "ethereum": "eth", "base": "base", "arbitrum": "arbitrum",
    "optimism": "optimism", "polygon": "polygon_pos", "bsc": "bsc",
}

# таймфрейм -> (путь GT, aggregate)
GT_TF = {
    "1m": ("minute", 1), "5m": ("minute", 5), "15m": ("minute", 15),
    "1h": ("hour", 1), "4h": ("hour", 4), "12h": ("hour", 12), "1d": ("day", 1),
}


def _ts(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


class GeckoTerminal:
    def __init__(self, http):
        self.http = http
        http.set_rate("api.geckoterminal.com", 0.15, burst=2)

    def _get(self, path: str, params: dict | None = None, ttl: int = 0):
        return self.http.get_json(f"{BASE}{path}", params=params, headers=HEADERS, ttl=ttl)

    def pool_trades(self, chain: str, pool: str, min_usd: float = 0) -> list[dict]:
        params = {"trade_volume_in_usd_greater_than": min_usd} if min_usd else None
        d = self._get(f"/networks/{GT_NETWORKS[chain]}/pools/{pool}/trades", params)
        out = []
        for t in d.get("data") or []:
            a = t.get("attributes") or {}
            try:
                out.append({
                    "ts": _ts(a["block_timestamp"]),
                    "tx": a.get("tx_hash"),
                    "wallet": a.get("tx_from_address"),
                    "kind": a.get("kind"),
                    "from_token": a.get("from_token_address"),
                    "to_token": a.get("to_token_address"),
                    "usd": float(a.get("volume_in_usd") or 0),
                    "block": a.get("block_number"),
                })
            except (KeyError, ValueError):
                continue
        return out

    def pool(self, chain: str, pool: str) -> dict | None:
        d = self._get(f"/networks/{GT_NETWORKS[chain]}/pools/{pool}", ttl=3600)
        return d.get("data")

    def token_info(self, chain: str, token: str) -> dict:
        """holders.count, holders.distribution_percentage.top_10, categories, gt_score..."""
        d = self._get(f"/networks/{GT_NETWORKS[chain]}/tokens/{token}/info", ttl=1800)
        return (d.get("data") or {}).get("attributes") or {}

    def ohlcv(self, chain: str, pool: str, tf: str, before_ts: int | None = None,
              limit: int = 1000, token: str | None = None) -> list[list[float]]:
        """Свечи [ts, o, h, l, c, v] по возрастанию времени, цена в USD."""
        path_tf, agg = GT_TF[tf]
        params = {"aggregate": agg, "limit": min(limit, 1000), "currency": "usd"}
        if before_ts:
            params["before_timestamp"] = int(before_ts)
        if token:
            params["token"] = token
        d = self._get(f"/networks/{GT_NETWORKS[chain]}/pools/{pool}/ohlcv/{path_tf}", params,
                      ttl=600 if not before_ts else 86400)
        rows = ((d.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
        return sorted(([int(r[0]), *map(float, r[1:6])] for r in rows), key=lambda r: r[0])
