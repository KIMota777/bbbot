"""CEX через ccxt: список рынков, сопоставление токена с тикером, свечи с кэшем.

Сопоставление DEX-токена с тикером CEX — главная ловушка: на Solana десятки
«PEPE», и только один из них — тот, что торгуется на Bybit. Поэтому тикер
принимается, только если цена CEX совпадает с ценой DEX в пределах допуска.
У перпетуалов бывают множители: 1000PEPE = 1000 PEPE.
"""
from __future__ import annotations

import logging
import re
import threading
import time

import ccxt

from ..util import now, parse_dur

log = logging.getLogger("wsa.cex")

MULT_RE = re.compile(r"^(1000000|100000|10000|1000|100|10)([A-Z][A-Z0-9]*)$")
# обёрнутые активы на DEX -> тикер на CEX
ALIASES = {"WETH": "ETH", "WBTC": "BTC", "CBBTC": "BTC", "WSOL": "SOL", "WBNB": "BNB",
           "WPOL": "POL", "WMATIC": "POL", "MATIC": "POL", "WAVAX": "AVAX"}

TF_SEC = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400}


class Cex:
    def __init__(self, exchange_id: str = "bybit", market: str = "spot", quote: str = "USDT",
                 db=None, keys: dict | None = None, mode: str = "public"):
        self.id = exchange_id
        self.market = market  # spot | linear
        self.quote = quote
        self.db = db
        self.mode = mode
        opts = {"defaultType": "spot" if market == "spot" else "swap"}
        params = {"enableRateLimit": True, "options": opts, "timeout": 30000}
        if keys:
            params.update(keys)
        self.ex = getattr(ccxt, exchange_id)(params)
        if mode == "testnet":
            self.ex.set_sandbox_mode(True)
        elif mode == "demo":
            if hasattr(self.ex, "enable_demo_trading"):
                self.ex.enable_demo_trading(True)
            else:
                raise ValueError(f"{exchange_id}: демо-торговля в ccxt не поддерживается, используйте testnet")
        self._markets: dict | None = None
        self._markets_at = 0.0
        self._by_base: dict[str, list[dict]] = {}
        self._tickers: dict = {}
        self._tickers_at = 0.0
        self._lock = threading.RLock()

    # ---------- рынки ----------

    def _want(self, m: dict) -> bool:
        if not m.get("active", True) or m.get("quote") != self.quote:
            return False
        if self.market == "spot":
            return bool(m.get("spot"))
        return bool(m.get("swap") and m.get("linear"))

    def markets(self) -> dict:
        with self._lock:
            if self._markets is None or time.time() - self._markets_at > 6 * 3600:
                self._markets = self.ex.load_markets(reload=self._markets is not None)
                self._markets_at = time.time()
                self._by_base = {}
                for sym, m in self._markets.items():
                    if not self._want(m):
                        continue
                    base = m["base"].upper()
                    mult = 1.0
                    mm = MULT_RE.match(base)
                    if mm:
                        mult = float(mm.group(1))
                        base = mm.group(2)
                    self._by_base.setdefault(base, []).append({"symbol": sym, "multiplier": mult})
            return self._markets

    def symbols(self) -> list[str]:
        self.markets()
        return sorted(x["symbol"] for lst in self._by_base.values() for x in lst)

    def candidates(self, token_symbol: str) -> list[dict]:
        self.markets()
        s = (token_symbol or "").strip().lstrip("$").upper()
        s = ALIASES.get(s, s)
        return list(self._by_base.get(s, []))

    def tickers(self, max_age: float = 60) -> dict:
        with self._lock:
            if time.time() - self._tickers_at > max_age:
                self.markets()
                try:
                    self._tickers = self.ex.fetch_tickers(params={"type": "spot" if self.market == "spot" else "swap"})
                except Exception as e:
                    log.warning("%s: fetch_tickers не удался: %s", self.id, e)
                    self._tickers = self._tickers or {}
                self._tickers_at = time.time()
            return self._tickers

    def last_price(self, symbol: str) -> float | None:
        t = self.tickers().get(symbol) or {}
        p = t.get("last") or t.get("close")
        if p:
            return float(p)
        try:
            t = self.ex.fetch_ticker(symbol)
            return float(t.get("last") or t.get("close"))
        except Exception:
            return None

    def turnover_24h(self, symbol: str) -> float | None:
        t = self.tickers().get(symbol) or {}
        q = t.get("quoteVolume")
        if q:
            return float(q)
        b, last = t.get("baseVolume"), t.get("last")
        return float(b) * float(last) if b and last else None

    def resolve(self, token_symbol: str, ref_price: float | None, tol: float = 0.05) -> dict | None:
        """Тикер CEX для токена DEX; без совпадения цены — None (другой токен)."""
        for c in self.candidates(token_symbol):
            px = self.last_price(c["symbol"])
            if not px:
                continue
            unit = px / c["multiplier"]
            if ref_price and ref_price > 0:
                if abs(unit / ref_price - 1) > tol:
                    continue
            elif ref_price is not None:
                continue
            return {"exchange": self.id, "market": self.market, "symbol": c["symbol"],
                    "multiplier": c["multiplier"], "price": unit}
        return None

    def order_book_top(self, symbol: str) -> tuple[float | None, float | None]:
        ob = self.ex.fetch_order_book(symbol, limit=5)
        bid = ob["bids"][0][0] if ob.get("bids") else None
        ask = ob["asks"][0][0] if ob.get("asks") else None
        return bid, ask

    # ---------- свечи ----------

    def listed_since(self, symbol: str) -> int | None:
        """Время первой дневной свечи = дата листинга (кэш на неделю)."""
        key = f"listed:{self.id}:{symbol}"
        if self.db is not None:
            v = self.db.cache_get(key, 7 * 86400)
            if v is not None:
                return v or None
        try:
            bars = self.ex.fetch_ohlcv(symbol, "1d", since=1500000000000, limit=2)
            ts = int(bars[0][0] / 1000) if bars else 0
        except Exception as e:
            log.debug("listed_since %s: %s", symbol, e)
            ts = 0
        if self.db is not None:
            self.db.cache_set(key, ts)
        return ts or None

    def ohlcv(self, symbol: str, tf: str, start: int, end: int | None = None) -> list[list[float]]:
        """Свечи [ts_сек, o, h, l, c, v] по возрастанию; недостающие куски догружаются."""
        step = TF_SEC.get(tf) or parse_dur(tf)
        end = min(end or now(), now())
        start = int(start // step * step)
        if self.db is None:
            return self._fetch_range(symbol, tf, start, end, step)
        src = f"{self.id}:{self.market}"
        for gs, ge in self._gaps(src, symbol, tf, start, end):
            bars = self._fetch_range(symbol, tf, gs, ge, step)
            if bars:
                self.db.executemany(
                    "INSERT OR REPLACE INTO ohlcv(src, symbol, tf, ts, o, h, l, c, v) VALUES(?,?,?,?,?,?,?,?,?)",
                    [(src, symbol, tf, int(b[0]), *b[1:6]) for b in bars])
            # покрытие не распространяем на ещё не закрытую свечу
            cover_end = min(ge, now() - step)
            if cover_end > gs:
                self.db.execute("INSERT OR REPLACE INTO ohlcv_cover(src, symbol, tf, start_ts, end_ts) VALUES(?,?,?,?,?)",
                                (src, symbol, tf, gs, cover_end))
        rows = self.db.query("SELECT ts, o, h, l, c, v FROM ohlcv WHERE src=? AND symbol=? AND tf=? AND ts>=? AND ts<=?"
                             " ORDER BY ts", (src, symbol, tf, start, end))
        return [[r["ts"], r["o"], r["h"], r["l"], r["c"], r["v"]] for r in rows]

    def _gaps(self, src: str, symbol: str, tf: str, start: int, end: int) -> list[tuple[int, int]]:
        rows = self.db.query("SELECT start_ts, end_ts FROM ohlcv_cover WHERE src=? AND symbol=? AND tf=?"
                             " AND end_ts>=? AND start_ts<=? ORDER BY start_ts", (src, symbol, tf, start, end))
        gaps, cur = [], start
        for r in rows:
            if r["start_ts"] > cur:
                gaps.append((cur, r["start_ts"]))
            cur = max(cur, r["end_ts"])
        if cur < end:
            gaps.append((cur, end))
        return [(a, b) for a, b in gaps if b - a > 0]

    def _fetch_range(self, symbol: str, tf: str, start: int, end: int, step: int) -> list[list[float]]:
        out: list[list[float]] = []
        since = start * 1000
        limit = 1000 if self.id in ("bybit", "binance") else 300
        guard = 0
        while since <= end * 1000 and guard < 500:
            guard += 1
            try:
                bars = self.ex.fetch_ohlcv(symbol, tf, since=since, limit=limit)
            except ccxt.BadSymbol:
                return out
            except (ccxt.NetworkError, ccxt.ExchangeError) as e:
                log.warning("%s %s %s: %s — повтор", self.id, symbol, tf, e)
                time.sleep(2)
                continue
            if not bars:
                break
            for b in bars:
                ts = int(b[0] / 1000)
                if start <= ts <= end:
                    out.append([ts, float(b[1]), float(b[2]), float(b[3]), float(b[4]), float(b[5] or 0)])
            last = int(bars[-1][0])
            if last < since:
                break
            since = last + step * 1000
            if len(bars) < 2:
                break
        # дедупликация по времени
        uniq = {b[0]: b for b in out}
        return [uniq[k] for k in sorted(uniq)]
