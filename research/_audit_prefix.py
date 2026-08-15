# -*- coding: utf-8 -*-
"""Строгая проверка причинности ОБРЕЗАНИЕМ СПРАВА.

Если сигнал на баре i зависит только от баров 0..i, то отрезание хвоста
выборки не имеет права изменить ни одного значения на оставшихся барах.
Это точная проверка на КАЖДОМ баре, а не вероятностная около одной точки,
как engine.assert_causal.
"""
import sys

import numpy as np

import rdata
import universe

REG, _ = universe.load()
FIELDS = ("entry", "exit", "stop", "tp", "trail", "size_k")


def cut_bars(bars, k):
    out = rdata.Bars.__new__(rdata.Bars)
    out.symbol, out.tf = bars.symbol, bars.tf
    for f in ("t", "o", "h", "l", "c", "v", "turnover"):
        setattr(out, f, getattr(bars, f)[:k].copy())
    return out


def check(build, bars, fracs=(0.5, 0.65, 0.8, 0.95)):
    full = build(bars)
    n = len(bars.t)
    bad = []
    for fr in fracs:
        k = int(n * fr)
        part = build(cut_bars(bars, k))
        for f in FIELDS:
            x = np.asarray(getattr(full, f), dtype=np.float64)[:k]
            y = np.asarray(getattr(part, f), dtype=np.float64)[:k]
            d = ~np.isclose(x, y, rtol=1e-9, atol=1e-12, equal_nan=True)
            if d.any():
                first = int(np.flatnonzero(d)[0])
                bad.append("cut=%.2f %s: %d/%d расхождений, первое на баре %d"
                           % (fr, f, int(d.sum()), k, first))
    return bad


def pick(st, k=4):
    cs = list(st.combos())
    if len(cs) <= k:
        return cs
    return [cs[i] for i in np.linspace(0, len(cs) - 1, k).astype(int)]


def main():
    only = sys.argv[1:] or None
    for sym in ("BTCUSDT", "SOLUSDT"):
        for tf in ("60", "240"):
            bars = rdata.load_bars(sym, tf)
            for name in sorted(REG):
                if only and name not in only:
                    continue
                st = REG[name]
                for p in pick(st):
                    try:
                        bad = check(lambda b, _p=p, _s=st: _s.build(b, _p),
                                    bars)
                    except Exception as e:  # noqa: BLE001
                        print("ИСКЛ %s %s %s %s: %s"
                              % (name, sym, tf, p, e))
                        continue
                    if bad:
                        print("! %s %s tf=%s %s" % (name, sym, tf, p))
                        for b in bad[:4]:
                            print("      " + b)
    print("готово")


if __name__ == "__main__":
    main()
