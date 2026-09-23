"""Birdeye (необязательно, бесплатный ключ BIRDEYE_API_KEY): лучшие трейдеры.

Бесплатный тариф Standard: 30 000 CU в месяц, 1 запрос/с. Используем три метода
(по документации data.birdeye.so, сентябрь 2026):
- /defi/v2/tokens/top_traders — топ по реализованному PnL за 30d/90d (25 CU, 10 строк);
- /trader/gainers-losers — лидеры сети по PnL за неделю/30d/90d (25 CU);
- /wallet/v2/pnl/summary — сводка PnL кошелька для быстрого отсева (20 CU).
Без ключа модуль молчит — система работает на бесплатных источниках.
"""
from __future__ import annotations

import logging

from ..config import secret

log = logging.getLogger("wsa.birdeye")

BASE = "https://public-api.birdeye.so"
BE_CHAINS = {"solana": "solana", "ethereum": "ethereum", "base": "base", "arbitrum": "arbitrum",
             "optimism": "optimism", "polygon": "polygon", "bsc": "bsc"}


class Birdeye:
    def __init__(self, http):
        self.http = http
        self.key = secret("BIRDEYE_API_KEY")
        http.set_rate("public-api.birdeye.so", 0.9, burst=1)

    @property
    def enabled(self) -> bool:
        return bool(self.key)

    def _get(self, path: str, chain: str, params: dict, ttl: int = 3600):
        if not self.key:
            raise RuntimeError("BIRDEYE_API_KEY не задан")
        headers = {"X-API-KEY": self.key, "x-chain": BE_CHAINS.get(chain, chain), "accept": "application/json"}
        d = self.http.get_json(f"{BASE}{path}", params=params, headers=headers, ttl=ttl)
        if isinstance(d, dict) and d.get("success") is False:
            raise RuntimeError(f"Birdeye {path}: {d.get('message')}")
        return (d or {}).get("data") or {}

    def top_traders(self, chain: str, token: str, time_frame: str = "30d", sort_by: str = "realized_pnl",
                    pages: int = 3) -> list[dict]:
        out = []
        for i in range(pages):
            data = self._get("/defi/v2/tokens/top_traders", chain,
                             {"address": token, "time_frame": time_frame, "sort_by": sort_by,
                              "sort_type": "desc", "offset": i * 10, "limit": 10})
            items = data.get("items") or []
            out += [{"wallet": x.get("owner"), "realized_pnl": x.get("realizedPnl"), "total_pnl": x.get("totalPnl"),
                     "trades": x.get("trade"), "volume_usd": x.get("volumeUsd") or x.get("volume"),
                     "tags": x.get("tags")} for x in items if x.get("owner")]
            if len(items) < 10:
                break
        return out

    def gainers(self, chain: str, period: str = "30d", limit: int = 100) -> list[dict]:
        data = self._get("/trader/gainers-losers", chain,
                         {"type": period, "sort_by": "PnL", "offset": 0, "limit": min(limit, 100)})
        return [{"wallet": x.get("address"), "pnl": x.get("pnl"), "realized_pnl": x.get("realized_pnl"),
                 "trades": x.get("trade_count"), "volume_usd": x.get("volume")}
                for x in (data.get("items") or []) if x.get("address")]

    def pnl_summary(self, chain: str, wallet: str, duration: str = "90d") -> dict:
        data = self._get("/wallet/v2/pnl/summary", chain, {"wallet": wallet, "duration": duration}, ttl=6 * 3600)
        s = data.get("summary") or {}
        counts = s.get("counts") or {}
        pnl = s.get("pnl") or {}
        cash = s.get("cashflow_usd") or {}
        return {"win_rate": counts.get("win_rate"), "buys": counts.get("total_buy"),
                "sells": counts.get("total_sell"), "invested": cash.get("total_invested"),
                "realized": pnl.get("realized_profit_usd"), "unrealized": pnl.get("unrealized_usd"),
                "total": pnl.get("total_usd")}
