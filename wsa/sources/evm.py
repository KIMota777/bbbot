"""EVM-история адреса через Etherscan-совместимый API.

Что реально доступно бесплатно (проверено по документации, сентябрь 2026):
- Blockscout PRO API с бесплатным ключом (BLOCKSCOUT_API_KEY, proapi_…):
  5 запросов/с, ~5000 запросов в сутки, Ethereum/Base/Arbitrum/Optimism/Polygon;
- Etherscan V2 с бесплатным ключом (ETHERSCAN_API_KEY): 3 запроса/с, но
  бесплатно только Ethereum, Polygon, Arbitrum; максимум 1000 записей на запрос;
- Blockscout без ключа — ~10 запросов в ЧАС на эксплорер: только для пробы.
Листаем от новых к старым, сдвигая endblock (окно page×offset больше не гарантировано).
"""
from __future__ import annotations

import logging

from ..config import secret
from ..http import HttpError

log = logging.getLogger("wsa.evm")

ETHERSCAN_V2 = "https://api.etherscan.io/v2/api"
BLOCKSCOUT_PRO = "https://api.blockscout.com/v2/api"
ETHERSCAN_FREE_CHAINS = {1, 137, 42161}
PAGE = 1000


class EvmExplorer:
    def __init__(self, http, chain: str, chain_cfg: dict):
        self.http = http
        self.chain = chain
        self.chainid = int(chain_cfg["chainid"])
        bs_key = secret("BLOCKSCOUT_API_KEY")
        es_key = secret("ETHERSCAN_API_KEY")
        if es_key and self.chainid in ETHERSCAN_FREE_CHAINS:
            self.kind, self.base, self.key, rps = "etherscan", ETHERSCAN_V2, es_key, 2.5
        elif bs_key:
            self.kind, self.base, self.key, rps = "blockscout-pro", BLOCKSCOUT_PRO, bs_key, 4.0
        elif es_key:
            # платная для Etherscan сеть: попробуем, биржа ответит ошибкой, если тариф не тот
            self.kind, self.base, self.key, rps = "etherscan", ETHERSCAN_V2, es_key, 2.5
        elif chain_cfg.get("blockscout"):
            self.kind, self.base, self.key, rps = "blockscout", chain_cfg["blockscout"].rstrip("/") + "/api", None, 10 / 3600
            log.warning("%s: без ключа Blockscout отвечает ~10 раз в час — добавьте BLOCKSCOUT_API_KEY", chain)
        else:
            raise ValueError(f"{chain}: нужен BLOCKSCOUT_API_KEY или ETHERSCAN_API_KEY")
        self.limiter_key = f"evm:{self.kind}"
        http.set_rate(self.limiter_key, rps, burst=max(1.0, rps))

    def _get(self, params: dict) -> list[dict]:
        p = dict(params)
        if self.kind == "etherscan":
            p.update(chainid=self.chainid, apikey=self.key)
        elif self.kind == "blockscout-pro":
            p.update(chain_id=self.chainid, apikey=self.key)
        d = self.http.get_json(self.base, params=p, limiter_key=self.limiter_key, timeout=45)
        res = d.get("result")
        if isinstance(res, list):
            return res
        msg = f"{d.get('message', '')} {res}"
        if "No transactions found" in msg or "No records found" in msg:
            return []
        if "rate limit" in msg.lower() or "max calls" in msg.lower():
            raise HttpError(429, self.base, msg)
        raise HttpError(0, self.base, msg)

    def account_list(self, action: str, address: str, cutoff_ts: int = 0, max_items: int = 5000,
                     startblock: int | None = None) -> list[dict]:
        """Действия адреса от новых к старым: tokentx | txlist | txlistinternal."""
        out: list[dict] = []
        seen: set = set()
        endblock: int | None = None
        for _ in range(200):
            params = {"module": "account", "action": action, "address": address,
                      "page": 1, "offset": PAGE, "sort": "desc"}
            if endblock is not None:
                params["endblock"] = endblock
            if startblock is not None:
                params["startblock"] = startblock
            try:
                rows = self._get(params)
            except HttpError as e:
                log.warning("%s %s %s: %s", self.chain, action, address[:10], e)
                break
            fresh = 0
            for r in rows:
                k = (r.get("hash"), r.get("logIndex"), r.get("traceId"), r.get("contractAddress"),
                     r.get("from"), r.get("to"), r.get("value"))
                if k in seen:
                    continue
                seen.add(k)
                out.append(r)
                fresh += 1
            if len(rows) < PAGE or not fresh or len(out) >= max_items:
                break
            oldest_ts = int(rows[-1].get("timeStamp") or 0)
            if cutoff_ts and oldest_ts < cutoff_ts:
                break
            # включительно: строки того же блока, не влезшие в страницу, придут снова (дедуп выше)
            endblock = int(rows[-1]["blockNumber"])
        if cutoff_ts:
            out = [r for r in out if int(r.get("timeStamp") or 0) >= cutoff_ts]
        return out[:max_items]
