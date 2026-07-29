# -*- coding: utf-8 -*-
"""Сравнение старых и эволюционных конфигов на полном годе 15m."""

import evolution as ev

OLD = {
    "DOGEUSDT": dict(rsi_os=30, zone=0.25, window=400, step=0.010, levels=3,
                     mult=1.5, tp=0.040, sweep=0.020, max_bars=192, cooldown=0,
                     knife=0.0),
    "LTCUSDT": dict(rsi_os=35, zone=0.25, window=400, step=0.010, levels=3,
                    mult=1.5, tp=0.020, sweep=0.020, max_bars=192, cooldown=0,
                    knife=0.0),
    "ETHUSDT": dict(rsi_os=25, zone=0.25, window=400, step=0.015, levels=3,
                    mult=1.5, tp=0.020, sweep=0.018, max_bars=192, cooldown=0,
                    knife=0.0),
}
NEW = {
    "DOGEUSDT": dict(rsi_os=35, zone=0.28, window=518, step=0.011, levels=3,
                     mult=1.8, tp=0.038, sweep=0.020, max_bars=205, cooldown=0,
                     knife=0.0),
    "LTCUSDT": dict(rsi_os=34, zone=0.25, window=377, step=0.008, levels=3,
                    mult=1.2, tp=0.020, sweep=0.020, max_bars=160, cooldown=0,
                    knife=0.0),
    "ETHUSDT": dict(rsi_os=25, zone=0.49, window=160, step=0.014, levels=3,
                    mult=1.6, tp=0.018, sweep=0.018, max_bars=192, cooldown=0,
                    knife=0.0),
}

for sym in ["DOGEUSDT", "LTCUSDT", "ETHUSDT"]:
    candles = ev.fetch(sym, "15", 365)
    pre = ev.prep(candles)
    half = len(candles) // 2
    print(f"\n=== {sym} (год 15m) ===")
    for label, g in (("старый", OLD[sym]), ("эволюц", NEW[sym])):
        r = ev.run4(candles, pre, g)
        st = ev.stats(r)
        r1 = ev.run4(candles[:half], ev.prep(candles[:half]), g)
        r2 = ev.run4(candles[half:], ev.prep(candles[half:]), g)
        ret = (r["balance"] / ev.START - 1) * 100
        ret1 = (r1["balance"] / ev.START - 1) * 100
        ret2 = (r2["balance"] / ev.START - 1) * 100
        wr = r["wins"] / r["trades"] * 100 if r["trades"] else 0
        print(f"  {label}: год {ret:+8.1f}% ({ret1:+.1f}/{ret2:+.1f}) | "
              f"мес.мед {st['med']:+5.2f}% | P25 {st['p25']:+5.2f}% | "
              f"приб.мес {st['pos_share']*100:3.0f}% | WR {wr:4.1f}% | "
              f"DD {r['max_dd']*100:4.1f}% | сделок {r['trades']}")
