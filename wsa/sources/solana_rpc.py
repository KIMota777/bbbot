"""Solana JSON-RPC: подписи и разобранные транзакции кошелька.

Работает с любым RPC (публичные без ключа, Helius с ключом). Несколько
эндпоинтов опрашиваются по кругу, у каждого свой лимит: публичный
mainnet-beta держит ~40 запросов на метод за 10 секунд.
"""
from __future__ import annotations

import itertools
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

from ..http import HttpError

log = logging.getLogger("wsa.rpc")


class RpcError(Exception):
    pass


class _Endpoint:
    def __init__(self, url: str):
        self.url = url
        parts = urlsplit(url)
        self.key = "rpc:" + parts.netloc
        self.name = parts.netloc
        self.fails = 0
        self.down_until = 0.0
        self.nominal = 1.0
        self.rate = 1.0
        self.ok_streak = 0
        # Helius принимает пакеты getTransaction (до 100 в пакете); публичные — нет:
        # publicnode отвечает 400, mainnet-beta режет часть пакета лимитом
        self.batch = "helius" in parts.netloc


class SolanaRpc:
    # замер сентября 2026: mainnet-beta отвечает ~0.8с и уже на 1 запрос/с отдаёт 429,
    # publicnode ~0.3с без ошибок. Под параллельной нагрузкой оба начинают
    # придерживать соединения — поэтому лимиты скромные, а таймаут короткий.
    KNOWN_RATES = {"api.mainnet-beta.solana.com": 1.5, "solana-rpc.publicnode.com": 4.0}

    def __init__(self, http, urls: list[str], rps_per_url: float = 3.0, workers: int = 3):
        if not urls:
            raise ValueError("не задан ни один RPC Solana")
        self.http = http
        self.endpoints = [_Endpoint(u) for u in urls]
        for ep in self.endpoints:
            rate = 9.0 if "helius" in ep.name else self.KNOWN_RATES.get(ep.name, rps_per_url)
            ep.nominal = ep.rate = rate
            http.set_rate(ep.key, rate, burst=max(1.0, rate))
        self.workers = workers
        self._lock = threading.Lock()
        self._id = itertools.count(1)

    def _pick(self, exclude: set | None = None) -> _Endpoint:
        """Живой эндпоинт, у которого раньше всех освободится лимит."""
        with self._lock:
            now = time.monotonic()
            pool = [e for e in self.endpoints if not exclude or e.name not in exclude] or self.endpoints
            alive = [e for e in pool if e.down_until <= now]
            if not alive:
                return min(pool, key=lambda e: e.down_until)
            return min(alive, key=lambda e: self.http.limiter(e.key).wait_time())

    def call(self, method: str, params: list, attempts: int | None = None, exclude: set | None = None,
             _used: list | None = None):
        attempts = attempts or max(3, 2 * len(self.endpoints))
        timeout = 15 if method == "getTransaction" else 25
        last: Exception | None = None
        for _ in range(attempts):
            ep = self._pick(exclude)
            if _used is not None:
                _used.append(ep.name)
            body = {"jsonrpc": "2.0", "id": next(self._id), "method": method, "params": params}
            try:
                d = self.http.post_json(ep.url, body, limiter_key=ep.key, retries=0, timeout=timeout)
            except HttpError as e:
                last = e
                self._fail(ep, 20 if e.status == 429 else 10)
                continue
            if "error" in d:
                err = d["error"]
                msg = str(err.get("message", err))
                code = err.get("code")
                # перегрузка/лимит — пробуем другой эндпоинт; логическая ошибка — наверх
                if code in (-32005, -32429, 429, -32603) or "rate" in msg.lower() or "limit" in msg.lower():
                    last = RpcError(msg)
                    self._fail(ep, 15)
                    continue
                raise RpcError(f"{method}: {msg}")
            self._ok(ep)
            return d.get("result")
        raise RpcError(f"{method}: все RPC недоступны ({last})")

    # адаптивная частота (AIMD): ошибка — вдвое медленнее, 50 успехов подряд — на 25% быстрее.
    # Публичные узлы после долгой нагрузки начинают душить IP; так скан замедляется,
    # а не падает и не зависает на таймаутах.
    def _ok(self, ep: _Endpoint) -> None:
        ep.fails = 0
        ep.ok_streak += 1
        if ep.ok_streak >= 50 and ep.rate < ep.nominal:
            ep.ok_streak = 0
            ep.rate = min(ep.nominal, ep.rate * 1.25)
            self.http.set_rate(ep.key, ep.rate, burst=max(1.0, ep.rate))

    def _fail(self, ep: _Endpoint, cooldown: float) -> None:
        ep.fails += 1
        ep.ok_streak = 0
        ep.rate = max(0.3, ep.rate / 2)
        self.http.set_rate(ep.key, ep.rate, burst=1.0)
        if ep.fails >= 2:
            ep.down_until = time.monotonic() + cooldown * min(ep.fails, 6)
            log.info("RPC %s: ошибки подряд, пауза %.0fс, частота %.1f/с", ep.name, cooldown * min(ep.fails, 6), ep.rate)

    # ---------- методы ----------

    def signatures(self, address: str, before: str | None = None, until: str | None = None,
                   limit: int = 1000) -> list[dict]:
        opts: dict = {"limit": min(limit, 1000)}
        if before:
            opts["before"] = before
        if until:
            opts["until"] = until
        return self.call("getSignaturesForAddress", [address, opts]) or []

    # в 2026 в сети уже есть транзакции версии 1: без этого параметра RPC их не отдаёт
    TX_OPTS = {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1, "commitment": "confirmed"}

    def transaction(self, sig: str) -> dict | None:
        """Транзакция по подписи. Пустой ответ — не «нет транзакции», а часто неархивный
        узел (publicnode отдаёт null на старые транзакции): спрашиваем другой узел."""
        tried: set[str] = set()
        for _ in range(len(self.endpoints)):
            used: list[str] = []
            res = self.call("getTransaction", [sig, self.TX_OPTS], exclude=tried, _used=used)
            if res is not None:
                return res
            tried.update(used[-1:])
            if len(tried) >= len(self.endpoints):
                break
        return None

    def _batch(self, ep: _Endpoint, sigs: list[str]) -> dict[str, dict | None]:
        body = [{"jsonrpc": "2.0", "id": i, "method": "getTransaction", "params": [s, self.TX_OPTS]}
                for i, s in enumerate(sigs)]
        d = self.http.post_json(ep.url, body, limiter_key=ep.key + ":batch", retries=2, timeout=60)
        out: dict[str, dict | None] = {}
        if isinstance(d, list):
            for item in d:
                i = item.get("id")
                if isinstance(i, int) and 0 <= i < len(sigs) and item.get("result"):
                    out[sigs[i]] = item["result"]
        return out

    def transactions(self, sigs: list[str], progress=None) -> dict[str, dict | None]:
        """Параллельная загрузка; общий лимит соблюдает лимитер каждого эндпоинта."""
        out: dict[str, dict | None] = {}
        if not sigs:
            return out
        batch_eps = [ep for ep in self.endpoints if ep.batch and ep.down_until <= time.monotonic()]
        if batch_eps:
            ep = batch_eps[0]
            # пакет из 10 = 10 запросов по лимиту 10/с: один пакет в секунду
            self.http.set_rate(ep.key + ":batch", 1.0, burst=1)
            rest = []
            for i in range(0, len(sigs), 10):
                part = sigs[i:i + 10]
                try:
                    got = self._batch(ep, part)
                except HttpError as e:
                    log.warning("пакетная загрузка не удалась (%s), перехожу на одиночные", e)
                    rest += sigs[i:]
                    break
                out.update(got)
                rest += [s for s in part if s not in got]
                if progress and (i // 10) % 5 == 0:
                    progress(min(i + 10, len(sigs)), len(sigs))
            sigs = rest
            if not sigs:
                return out
        done = 0
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futs = {pool.submit(self._tx_safe, s): s for s in sigs}
            for f in futs:
                s = futs[f]
                out[s] = f.result()
                done += 1
                if progress and done % 50 == 0:
                    progress(done, len(sigs))
        return out

    def _tx_safe(self, sig: str) -> dict | None:
        try:
            return self.transaction(sig)
        except RpcError as e:
            log.warning("транзакция %s не загружена: %s", sig[:12], e)
            return None

    def balance(self, address: str) -> float:
        r = self.call("getBalance", [address, {"commitment": "confirmed"}])
        return (r or {}).get("value", 0) / 1e9

    def token_accounts(self, owner: str) -> list[dict]:
        """Токен-счета кошелька (SPL и Token-2022): mint, количество."""
        out = []
        for program in ("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                        "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"):
            r = self.call("getTokenAccountsByOwner",
                          [owner, {"programId": program}, {"encoding": "jsonParsed"}]) or {}
            for acc in r.get("value", []):
                info = (((acc.get("account") or {}).get("data") or {}).get("parsed") or {}).get("info") or {}
                amt = info.get("tokenAmount") or {}
                ui = amt.get("uiAmount")
                if ui:
                    out.append({"mint": info.get("mint"), "qty": float(ui)})
        return out
