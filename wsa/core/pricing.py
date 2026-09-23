"""Оценка ног сделок в USD на момент сделки.

- стейблкоин = $1;
- SOL/ETH/BNB — по 5-минутным свечам референсной CEX (точность до минут);
- токен против токена или перевод — по рыночной цене токена: свечи CEX,
  если токен там торгуется, иначе свечи DEX-пула (GeckoTerminal, ≤180 дней).
"""
from __future__ import annotations

import logging

from ..chains import evm as evm_chain
from ..chains import solana as sol_chain
from ..sources.cex import TF_SEC
from ..util import DAY, HOUR, now
from .series import Series

log = logging.getLogger("wsa.pricing")

NATIVE_SYMBOL = {"solana": "SOL"}


def native_symbol(ctx, chain: str) -> str:
    if chain == "solana":
        return "SOL"
    return ctx.cfg.evm.chains[chain]["native"]


def native_id(chain: str) -> str:
    return sol_chain.SOL if chain == "solana" else evm_chain.NATIVE


def is_stable(chain: str, asset: str) -> bool:
    if asset == "USD":
        return True
    if chain == "solana":
        return asset in sol_chain.STABLES
    return asset in evm_chain.STABLES.get(chain, {})


def is_excluded(chain: str, asset: str) -> bool:
    if chain == "solana":
        return sol_chain.is_excluded(asset)
    return evm_chain.is_excluded(chain, asset)


def stable_like(meta: dict | None) -> bool:
    """Незнакомый стейблкоин: «USD» в тикере и цена в пределах ±3% от $1."""
    if not meta:
        return False
    sym = (meta.get("symbol") or "").upper()
    px = meta.get("price_usd")
    return "USD" in sym and px is not None and 0.97 <= px <= 1.03


class Pricer:
    def __init__(self, ctx):
        self.ctx = ctx
        self._series: dict[tuple, Series] = {}
        self._stable_cache: dict[tuple, bool] = {}

    def is_stable_like(self, chain: str, token: str) -> bool:
        k = (chain, token)
        if k not in self._stable_cache:
            self._stable_cache[k] = stable_like(self.ctx.db.token(chain, token))
        return self._stable_cache[k]

    # ---------- ряды ----------

    def major_series(self, chain: str, t0: int, t1: int) -> Series | None:
        """Свечи нативного актива сети (SOL/ETH...) на референсной бирже."""
        sym = f"{native_symbol(self.ctx, chain)}/USDT"
        tf = self.ctx.cfg.context.quote_tf
        return self._cex_series(self.ctx.ref, sym, tf, t0, t1)

    def _cex_series(self, cex, symbol: str, tf: str, t0: int, t1: int) -> Series | None:
        key = (cex.id, cex.market, symbol, tf)
        s = self._series.get(key)
        step = TF_SEC[tf]
        if s is None or not s.covers(t0) or (s.end or 0) < min(t1, now() - step):
            bars = cex.ohlcv(symbol, tf, t0 - step, t1 + step)
            if not bars:
                return None
            s = Series(bars, step, f"cex:{cex.id}:{symbol}")
            self._series[key] = s
        return s

    def token_series(self, chain: str, token: str, t0: int, t1: int, tf: str = "1h",
                     allow_dex: bool = True) -> Series | None:
        """Ряд цены токена: CEX, если он там торгуется, иначе (allow_dex) DEX-пул."""
        if token == native_id(chain):
            return self._cex_series(self.ctx.ref, f"{native_symbol(self.ctx, chain)}/USDT", tf, t0, t1)
        m = self.ctx.tokens.cex_map(chain, token, which="ref")
        if m:
            s = self._cex_series(self.ctx.ref, m["symbol"], tf, t0, t1)
            if s and len(s):
                if m.get("multiplier", 1) != 1:
                    k = m["multiplier"]
                    s = Series([[b[0], b[1] / k, b[2] / k, b[3] / k, b[4] / k, b[5] * k] for b in s.bars],
                               s.step, s.src)
                return s
        if not allow_dex or not self.ctx.cfg.context.dex_ohlcv:
            return None
        return self.dex_series(chain, token, t0, t1, tf)

    def dex_series(self, chain: str, token: str, t0: int, t1: int, tf: str = "1h") -> Series | None:
        key = ("dex", chain, token, tf)
        s = self._series.get(key)
        if s is not None and s.covers(t0):
            return s
        meta = self.ctx.tokens.meta(chain, token)
        pool = meta.get("best_pair") if meta else None
        if not pool:
            return None
        oldest = now() - 179 * DAY
        t0 = max(t0, oldest)
        if t1 <= t0:
            return None
        step = TF_SEC[tf]
        bars: dict[int, list] = {}
        before = min(t1 + step, now())
        for _ in range(12):
            try:
                chunk = self.ctx.gt.ohlcv(chain, pool, tf, before_ts=before, limit=1000, token=token)
            except Exception as e:
                log.debug("GT ohlcv %s: %s", token[:8], e)
                break
            if not chunk:
                break
            for b in chunk:
                bars[b[0]] = b
            first = chunk[0][0]
            if first <= t0 or len(chunk) < 2:
                break
            before = first
        if not bars:
            return None
        s = Series([bars[k] for k in sorted(bars)], step, f"gt:{pool}")
        self._series[key] = s
        return s

    # ---------- цены ----------

    def quote_price(self, chain: str, asset: str, ts: int) -> float | None:
        if is_stable(chain, asset) or self.is_stable_like(chain, asset):
            return 1.0
        if asset == native_id(chain):
            s = self.major_series(chain, ts - DAY, ts + DAY)
            return s.price_at(ts, max_gap=2 * HOUR) if s else None
        return None

    def token_price(self, chain: str, token: str, ts: int, allow_dex: bool = True) -> float | None:
        q = self.quote_price(chain, token, ts)
        if q is not None:
            return q
        s = self.token_series(chain, token, ts - 3 * DAY, ts + DAY, allow_dex=allow_dex)
        return s.price_at(ts, max_gap=6 * HOUR) if s else None

    def value_legs(self, chain: str, legs: list[dict], allow_dex: bool = False) -> list[dict]:
        """Проставляет usd/price/valued_by. Ноги стейблов и LST не оцениваются.

        По умолчанию только точные и быстрые источники: котировка самой сделки
        (стейбл/SOL/ETH) и свечи CEX. Свечи DEX (GeckoTerminal, ~10 запросов/мин)
        здесь не трогаем: у кошельков бывают сотни спам-токенов из эйрдропов.
        Неоценённый приход получает «неизвестную базу» и в PnL не участвует.
        """
        if not legs:
            return legs
        t0 = min(l["ts"] for l in legs)
        t1 = max(l["ts"] for l in legs)
        nat = native_id(chain)
        if any(l.get("quote") == nat or l["token"] == nat for l in legs):
            self.major_series(chain, t0 - HOUR, t1 + HOUR)  # одним куском на весь период
        # метаданные всех токенов — пакетами по 30, а не по одному
        toks = {l["token"] for l in legs} | {l["quote"] for l in legs if l.get("quote")}
        self.ctx.tokens.refresh(chain, [t for t in toks if t not in ("USD", nat) and not is_excluded(chain, t)
                                        and not (self.ctx.db.token(chain, t) or {}).get("updated_at")])
        for l in legs:
            l["usd"], l["price"], l["valued_by"] = None, None, "none"
            if is_excluded(chain, l["token"]) or self.is_stable_like(chain, l["token"]):
                continue
            if l["token"] == nat and l["side"] in ("tin", "tout"):
                continue  # движение «кэша», не сделка
            usd = None
            q = l.get("quote")
            if q and l.get("quote_qty"):
                qp = self.quote_price(chain, q, l["ts"])
                if qp is not None:
                    usd = l["quote_qty"] * qp
                    l["valued_by"] = "quote"
                else:
                    tp = self.token_price(chain, q, l["ts"], allow_dex) if not is_excluded(chain, q) else None
                    if tp:
                        usd = l["quote_qty"] * tp
                        l["valued_by"] = "market"
            if usd is None:
                tp = self.token_price(chain, l["token"], l["ts"], allow_dex)
                if tp:
                    usd = l["qty"] * tp
                    l["valued_by"] = "market"
            if usd is not None and l["qty"] > 0:
                l["usd"] = usd
                l["price"] = usd / l["qty"]
        return legs
