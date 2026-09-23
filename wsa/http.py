"""HTTP-клиент: лимит запросов на хост, повторы с отступом, кэш в SQLite.

Бесплатные API режут частоту жёстко (GeckoTerminal ~30/мин, публичный RPC
Solana ~40/10с), поэтому лимитер общий для всех потоков процесса.
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import threading
import time
from urllib.parse import urlsplit

import requests

log = logging.getLogger("wsa.http")

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) wallet-strategy-analyzer/0.1"


class HttpError(Exception):
    def __init__(self, status: int, url: str, body: str = ""):
        super().__init__(f"HTTP {status} {url} {body[:200]}")
        self.status = status
        self.url = url
        self.body = body


class RateLimiter:
    """Токен-бакет: rate запросов в секунду, burst — запас на короткий всплеск."""

    def __init__(self, rate: float, burst: float | None = None):
        self.rate = max(rate, 0.01)
        self.capacity = burst if burst is not None else max(1.0, rate)
        self.tokens = self.capacity
        self.t = time.monotonic()
        self.lock = threading.Lock()
        self.penalty_until = 0.0

    def acquire(self) -> None:
        while True:
            with self.lock:
                now = time.monotonic()
                if now < self.penalty_until:
                    wait = self.penalty_until - now
                else:
                    self.tokens = min(self.capacity, self.tokens + (now - self.t) * self.rate)
                    self.t = now
                    if self.tokens >= 1:
                        self.tokens -= 1
                        return
                    wait = (1 - self.tokens) / self.rate
            time.sleep(min(wait, 5.0))

    def wait_time(self) -> float:
        """Сколько ждать до свободного токена (0 — можно сразу)."""
        with self.lock:
            now = time.monotonic()
            if now < self.penalty_until:
                return self.penalty_until - now
            tokens = min(self.capacity, self.tokens + (now - self.t) * self.rate)
            return 0.0 if tokens >= 1 else (1 - tokens) / self.rate

    def penalize(self, seconds: float) -> None:
        with self.lock:
            self.penalty_until = max(self.penalty_until, time.monotonic() + seconds)
            self.tokens = 0


class Http:
    def __init__(self, db=None, default_rate: float = 5.0):
        self.db = db
        self.default_rate = default_rate
        self._limiters: dict[str, RateLimiter] = {}
        self._lock = threading.Lock()
        self._local = threading.local()
        self.stats: dict[str, int] = {}

    def session(self) -> requests.Session:
        s = getattr(self._local, "s", None)
        if s is None:
            s = requests.Session()
            s.headers.update({"User-Agent": UA, "Accept": "application/json"})
            self._local.s = s
        return s

    def limiter(self, key: str, rate: float | None = None, burst: float | None = None) -> RateLimiter:
        with self._lock:
            lim = self._limiters.get(key)
            if lim is None:
                lim = RateLimiter(rate or self.default_rate, burst)
                self._limiters[key] = lim
            return lim

    def set_rate(self, key: str, rate: float, burst: float | None = None) -> None:
        with self._lock:
            self._limiters[key] = RateLimiter(rate, burst)

    @staticmethod
    def _key(method: str, url: str, params, body) -> str:
        raw = json.dumps([method, url, params or {}, body], sort_keys=True, default=str)
        return hashlib.sha1(raw.encode()).hexdigest()

    def request_json(self, method: str, url: str, params: dict | None = None, body=None,
                     headers: dict | None = None, ttl: int = 0, limiter_key: str | None = None,
                     timeout: float = 25, retries: int = 4, ok_statuses: tuple = (200,)):
        """JSON-запрос. ttl>0 — ответ берётся из кэша SQLite, если он свежее ttl секунд."""
        ck = None
        if ttl and self.db is not None:
            ck = self._key(method, url, params, body)
            cached = self.db.cache_get(ck, ttl)
            if cached is not None:
                return cached
        host = limiter_key or urlsplit(url).netloc
        lim = self.limiter(host)
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            lim.acquire()
            self.stats[host] = self.stats.get(host, 0) + 1
            try:
                r = self.session().request(method, url, params=params,
                                           json=body if body is not None else None,
                                           headers=headers, timeout=timeout)
            except (requests.ConnectionError, requests.Timeout) as e:
                last_exc = e
                time.sleep(min(30, (2 ** attempt) + random.random()))
                continue
            if r.status_code in ok_statuses:
                try:
                    data = r.json()
                except ValueError as e:
                    raise HttpError(r.status_code, url, "не JSON: " + r.text[:200]) from e
                if ck is not None:
                    self.db.cache_set(ck, data)
                return data
            if r.status_code == 429 or r.status_code >= 500:
                ra = r.headers.get("Retry-After")
                try:
                    wait = float(ra) if ra else (2 ** attempt) * 2
                except ValueError:
                    wait = (2 ** attempt) * 2
                wait = min(60.0, wait) + random.random()
                lim.penalize(wait)
                log.debug("HTTP %s от %s, жду %.1fс", r.status_code, host, wait)
                last_exc = HttpError(r.status_code, url, r.text)
                continue
            raise HttpError(r.status_code, url, r.text)
        if isinstance(last_exc, HttpError):
            raise last_exc
        raise HttpError(0, url, f"сеть недоступна: {last_exc}")

    def get_json(self, url: str, params: dict | None = None, **kw):
        return self.request_json("GET", url, params=params, **kw)

    def post_json(self, url: str, body, **kw):
        return self.request_json("POST", url, body=body, **kw)
