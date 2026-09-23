"""Признаки момента входа и что было с ценой после него.

Главное правило — никакого заглядывания в будущее: признаки входа считаются
только по свечам, закрытым ДО сделки, и по цене исполнения самой сделки.
Форвард-доходности (что было потом) — отдельный словарь, в признаки не идут.
"""
from __future__ import annotations

import math

from ..util import DAY, HOUR, clean, mean, stdev
from .series import Series

FWD = {"1h": HOUR, "6h": 6 * HOUR, "24h": DAY, "3d": 3 * DAY, "7d": 7 * DAY, "30d": 30 * DAY}

ENTRY_TYPES = ("early", "breakout", "momentum", "mean_reversion", "neutral", "unknown")
ENTRY_RU = {"early": "ранний вход", "breakout": "пробой", "momentum": "моментум",
            "mean_reversion": "покупка падения", "neutral": "нейтрально", "unknown": "нет данных"}


def _closed(s: Series, t0: int, t1: int) -> list[list[float]]:
    """Свечи, полностью закрытые в интервале [t0, t1]."""
    return [b for b in s.window(t0, t1) if b[0] + s.step <= t1]


def _rsi(closes: list[float], n: int = 14) -> float | None:
    if len(closes) < n + 1:
        return None
    gains = losses = 0.0
    for a, b in zip(closes[-n - 1:-1], closes[-n:]):
        d = b - a
        if d > 0:
            gains += d
        else:
            losses -= d
    if losses == 0:
        return 100.0
    rs = (gains / n) / (losses / n)
    return 100 - 100 / (1 + rs)


def price_features(s: Series | None, ts: int, price: float | None) -> dict:
    """Рынок на момент ts по ряду s (часовые свечи)."""
    f: dict = {}
    if s is None or not len(s):
        return f
    p = price or s.close_before(ts)
    if not p:
        return f
    f["price"] = p

    def ret(delta):
        ref = s.close_before(ts - delta)
        return p / ref - 1 if ref else None

    f["ret_1h"] = ret(HOUR)
    f["ret_4h"] = ret(4 * HOUR)
    f["ret_24h"] = ret(DAY)
    f["ret_7d"] = ret(7 * DAY)
    f["ret_30d"] = ret(30 * DAY)

    last24 = _closed(s, ts - DAY, ts)
    prev7 = _closed(s, ts - 8 * DAY, ts - DAY)
    v24 = sum(b[5] * b[4] for b in last24)
    v7 = sum(b[5] * b[4] for b in prev7)
    if last24 and prev7 and v7 > 0:
        f["vol_ratio"] = v24 / (v7 / (len(prev7) * s.step / DAY))
    f["vol_24h_usd"] = v24 if last24 else None

    w7 = _closed(s, ts - 7 * DAY, ts)
    if w7:
        hi = max(b[2] for b in w7)
        lo = min(b[3] for b in w7)
        f["dd_high_7d"] = p / hi - 1 if hi else None
        f["range_pos_7d"] = (p - lo) / (hi - lo) if hi > lo else None
        before = _closed(s, ts - 7 * DAY, ts - 2 * HOUR)
        if before:
            f["breakout_7d"] = p >= max(b[2] for b in before)
    w30 = _closed(s, ts - 30 * DAY, ts)
    if w30:
        hi30 = max(b[2] for b in w30)
        f["dd_high_30d"] = p / hi30 - 1 if hi30 else None
    closes = [b[4] for b in _closed(s, ts - 8 * DAY, ts)]
    if len(closes) >= 25:
        rets = [math.log(b / a) for a, b in zip(closes[-25:-1], closes[-24:]) if a > 0 and b > 0]
        sd = stdev(rets)
        f["volatility_24h"] = sd * math.sqrt(24) if sd is not None else None
        f["sma24_gap"] = p / (sum(closes[-24:]) / 24) - 1
        f["rsi_14"] = _rsi(closes[-15:] + [p])
    if len(closes) >= 168:
        f["sma7d_gap"] = p / (sum(closes[-168:]) / 168) - 1
    return f


def forward(s: Series | None, ts: int, price: float | None) -> dict:
    """Что было с ценой после входа: доходности на горизонтах, MFE/MAE за 7 дней."""
    out: dict = {}
    if s is None or not len(s) or not price:
        return out
    for name, d in FWD.items():
        c = s.close_before(ts + d)
        if c and s.end and ts + d <= s.end:
            out[name] = c / price - 1
    # максимальный рост/просадка после входа в пределах каждого горизонта —
    # по ним калибруются тейки и стоп стратегии с тем же горизонтом
    for name, d in (("6h", 6 * HOUR), ("24h", DAY), ("3d", 3 * DAY), ("7d", 7 * DAY)):
        after = s.window(ts + 1, ts + d)
        if after and (s.end or 0) >= ts + min(d, DAY):
            out[f"mfe_{name}"] = max(b[2] for b in after) / price - 1
            out[f"mae_{name}"] = min(b[3] for b in after) / price - 1
    after30 = s.window(ts + 1, ts + 30 * DAY)
    if after30:
        out["max_30d"] = max(b[2] for b in after30) / price - 1
    return out


def regime(btc: Series | None, native: Series | None, ts: int) -> dict:
    out = {}
    for name, s in (("btc", btc), ("native", native)):
        if s is None or not len(s):
            continue
        now_p = s.close_before(ts)
        wk = s.close_before(ts - 7 * DAY)
        d1 = s.close_before(ts - DAY)
        if now_p and wk:
            out[f"{name}_ret_7d"] = now_p / wk - 1
        if now_p and d1:
            out[f"{name}_ret_24h"] = now_p / d1 - 1
    return out


def classify(f: dict, cfg) -> str:
    """Тип входа по приоритету: ранний > пробой > моментум > покупка падения > нейтрально."""
    c = cfg.context
    age = f.get("token_age_days")
    if age is not None and age <= c.early_age_days:
        return "early"
    if "ret_24h" not in f and "dd_high_7d" not in f:
        return "unknown"
    if f.get("breakout_7d") and (f.get("vol_ratio") or 0) >= c.breakout_vol_ratio:
        return "breakout"
    r24, r7 = f.get("ret_24h"), f.get("ret_7d")
    if (r24 is not None and r24 >= c.momentum_ret_24h) or \
            (r7 is not None and r7 >= c.momentum_ret_7d and (f.get("sma24_gap") or 0) > 0):
        return "momentum"
    dd = f.get("dd_high_7d")
    if (dd is not None and dd <= c.meanrev_dd_7d) or (r24 is not None and r24 <= c.meanrev_ret_24h):
        return "mean_reversion"
    return "neutral"


# числовые признаки, по которым строится стратегия (в порядке важности для отчёта)
NUMERIC = ["ret_1h", "ret_4h", "ret_24h", "ret_7d", "ret_30d", "vol_ratio", "dd_high_7d", "dd_high_30d",
           "range_pos_7d", "volatility_24h", "sma24_gap", "sma7d_gap", "rsi_14", "token_age_days",
           "mc_entry", "fdv_entry", "liquidity_now", "days_since_listing", "size_rel",
           "btc_ret_7d", "native_ret_7d", "holders_growth_24h", "top10_pct", "consensus_24h"]
CATEGORICAL = ["entry_type", "sector", "cex_listed", "breakout_7d", "buy_index_bucket"]

FEATURE_RU = {
    "ret_1h": "изменение цены за 1ч до входа", "ret_4h": "изменение за 4ч до входа",
    "ret_24h": "изменение за 24ч до входа", "ret_7d": "изменение за 7д до входа",
    "ret_30d": "изменение за 30д до входа", "vol_ratio": "объём 24ч к среднему за неделю",
    "dd_high_7d": "отставание от 7-дневного максимума", "dd_high_30d": "отставание от 30-дневного максимума",
    "range_pos_7d": "положение в 7-дневном диапазоне", "volatility_24h": "волатильность 24ч",
    "sma24_gap": "цена к средней за 24ч", "sma7d_gap": "цена к средней за 7д", "rsi_14": "RSI(14) по часам",
    "token_age_days": "возраст токена, дней", "mc_entry": "капитализация на входе",
    "fdv_entry": "FDV на входе", "liquidity_now": "ликвидность пула", "days_since_listing": "дней с листинга на CEX",
    "size_rel": "размер позиции к обычному для кошелька", "btc_ret_7d": "BTC за 7д",
    "native_ret_7d": "SOL/ETH за 7д", "holders_growth_24h": "рост держателей за 24ч",
    "top10_pct": "доля топ-10 держателей", "consensus_24h": "сколько других отслеживаемых кошельков купили за 24ч",
    "entry_type": "тип входа", "sector": "сектор", "cex_listed": "уже торговался на CEX",
    "breakout_7d": "пробой 7-дневного максимума", "buy_index_bucket": "первая покупка или добор",
}


def bucket_buy_index(i: int) -> str:
    return "first" if i <= 1 else ("add2" if i == 2 else "add3+")


def summarize(values: list[float | None]) -> dict:
    v = clean(values)
    if not v:
        return {"n": 0}
    v.sort()

    def q(p):
        pos = (len(v) - 1) * p
        lo = int(pos)
        hi = min(lo + 1, len(v) - 1)
        return v[lo] + (v[hi] - v[lo]) * (pos - lo)

    return {"n": len(v), "mean": mean(v), "p25": q(0.25), "p50": q(0.5), "p75": q(0.75),
            "pos_share": sum(1 for x in v if x > 0) / len(v)}
