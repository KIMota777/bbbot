# -*- coding: utf-8 -*-
"""Внешние данные для v4: funding, открытый интерес, SPX, DXY, золото.
Всё кэшируется в json. Значения дневных рядов берутся С ЛАГОМ (вчерашний
клоуз) — без заглядывания в будущее."""

import json
import os
import time

from pybit.unified_trading import HTTP


def _cache(path, builder):
    if os.path.exists(path):
        with open(path) as fh:
            return json.load(fh)
    data = builder()
    with open(path, "w") as fh:
        json.dump(data, fh)
    return data


def fetch_funding(symbol, days=1200):
    """[(ts_ms, rate_%за8ч)] по возрастанию."""
    def build():
        s = HTTP(testnet=False)
        start = int(time.time() * 1000) - days * 86400 * 1000
        end = int(time.time() * 1000)
        out = []
        for _ in range(80):
            r = s.get_funding_rate_history(category="linear", symbol=symbol,
                                           limit=200, endTime=end)
            rows = r["result"]["list"]
            if not rows:
                break
            out += [(int(x["fundingRateTimestamp"]),
                     float(x["fundingRate"]) * 100) for x in rows]
            oldest = int(rows[-1]["fundingRateTimestamp"])
            if oldest <= start or oldest >= end:
                break
            end = oldest - 1
            time.sleep(0.12)
        return sorted(set(out))
    return _cache(f"funding_{symbol}.json", build)


def fetch_oi(symbol):
    """[(ts_ms, oi)] 1h по возрастанию (глубина ~с марта 2025)."""
    def build():
        s = HTTP(testnet=False)
        out, cur = [], None
        for _ in range(80):
            kw = dict(category="linear", symbol=symbol,
                      intervalTime="1h", limit=200)
            if cur:
                kw["cursor"] = cur
            r = s.get_open_interest(**kw)
            rows = r["result"]["list"]
            if not rows:
                break
            out += [(int(x["timestamp"]), float(x["openInterest"]))
                    for x in rows]
            cur = r["result"].get("nextPageCursor")
            if not cur:
                break
            time.sleep(0.12)
        return sorted(set(out))
    return _cache(f"oi_{symbol}.json", build)


def fetch_daily_pct5():
    """{'spx': {day_ts: 5д-изм.%}, 'dxy': ..., 'gold': ...} на ВЧЕРАШНИХ данных."""
    def build():
        out = {}
        import yfinance as yf
        for key, tick in (("spx", "^GSPC"), ("dxy", "DX-Y.NYB")):
            df = yf.download(tick, period="4y", interval="1d", progress=False)
            closes = [float(x) for x in df["Close"].values.ravel()]
            days = [int(t.timestamp()) for t in df.index]
            m = {}
            for i in range(6, len(closes)):
                # известно на день days[i]+1 и позже: изменение close[i-1]/close[i-6]
                val = (closes[i - 1] / closes[i - 6] - 1) * 100
                m[days[i]] = val
            out[key] = m
        s = HTTP(testnet=False)
        r = s.get_kline(category="linear", symbol="XAUTUSDT",
                        interval="D", limit=1000)
        rows = sorted((int(x[0]), float(x[4])) for x in r["result"]["list"])
        m = {}
        for i in range(6, len(rows)):
            val = (rows[i - 1][1] / rows[i - 6][1] - 1) * 100
            m[rows[i][0] // 1000] = val
        out["gold"] = m
        return out
    raw = _cache("daily_pct5.json", build)
    return {k: {int(d): v for d, v in m.items()} for k, m in raw.items()}


def step_lookup(series):
    """series [(ts,val)] -> функция ts->последнее известное значение."""
    import bisect
    ts_list = [t for t, _ in series]
    vals = [v for _, v in series]

    def get(ts):
        i = bisect.bisect_right(ts_list, ts) - 1
        return vals[i] if i >= 0 else None
    return get


def build_aux4(candles, funding, oi, pct5):
    """Массивы v4-сигналов, выровненные по свечам."""
    fget = step_lookup(funding) if funding else (lambda ts: None)
    oi_map = dict(oi)
    fund = [None] * len(candles)
    oi_chg = [None] * len(candles)
    spx5 = [None] * len(candles)
    dxy5 = [None] * len(candles)
    gold5 = [None] * len(candles)

    def daily(m, day):
        for back in range(6):
            v = m.get(day - back * 86400)
            if v is not None:
                return v
        return None

    for i, c in enumerate(candles):
        ts = c[0]
        fund[i] = fget(ts)
        hour = ts // 3600000 * 3600000
        now, prev = oi_map.get(hour), oi_map.get(hour - 24 * 3600000)
        if now and prev:
            oi_chg[i] = (now / prev - 1) * 100
        day = ts // 1000 // 86400 * 86400
        spx5[i] = daily(pct5["spx"], day)
        dxy5[i] = daily(pct5["dxy"], day)
        gold5[i] = daily(pct5["gold"], day)
    return dict(fund=fund, oi_chg=oi_chg, spx5=spx5, dxy5=dxy5, gold5=gold5)
