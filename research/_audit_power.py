# -*- coding: utf-8 -*-
"""Проверка причинности с ВЫСОКОЙ МОЩНОСТЬЮ.

Слабость и engine.assert_causal, и простого обрезания в том, что утечка на
k баров видна только на последних k барах перед срезом, а сигналы редкие:
на 2-5% баров. Одна точка среза почти наверняка попадёт в пустоту.

Здесь срез ставится ИМЕННО НА БАРЫ С СИГНАЛОМ: строим сигнал на префиксе
bars[:j+1] и сверяем последний бар j с тем, что даёт полный прогон. Если бар j
подглядывал хоть на один бар вперёд, значения обязаны разойтись.
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


def probe(build, bars, n_sig=20, n_rand=10, seed=0):
    full = build(bars)
    n = len(bars.t)
    e = np.asarray(full.entry)
    lo = int(n * 0.35)                       # заведомо после прогрева
    cand = np.flatnonzero(e[lo:] != 0) + lo
    rng = np.random.default_rng(seed)
    pts = []
    if len(cand):
        pts += list(cand[np.linspace(0, len(cand) - 1,
                                     min(n_sig, len(cand))).astype(int)])
    pts += list(rng.integers(lo, n - 1, n_rand))
    bad = []
    for j in sorted(set(int(x) for x in pts)):
        part = build(cut_bars(bars, j + 1))
        for f in FIELDS:
            x = float(np.asarray(getattr(full, f), dtype=np.float64)[j])
            y = float(np.asarray(getattr(part, f), dtype=np.float64)[j])
            if not np.isclose(x, y, rtol=1e-9, atol=1e-12, equal_nan=True):
                bad.append("бар %d поле %s: полный=%g обрезанный=%g"
                           % (j, f, x, y))
    return bad, len(set(int(x) for x in pts))


def main():
    only = sys.argv[1:] or None
    sym, tf = "BTCUSDT", "60"
    bars = rdata.load_bars(sym, tf)
    for name in sorted(REG):
        if only and name not in only:
            continue
        st = REG[name]
        cs = list(st.combos())
        sel = [cs[0], cs[len(cs) // 2]]
        allbad = []
        pts = 0
        for p in sel:
            b, k = probe(lambda x, _p=p, _s=st: _s.build(x, _p), bars)
            pts += k
            allbad += ["%s %s" % (p, z) for z in b]
        print("%s %-18s точек=%d расхождений=%d"
              % ("!" if allbad else "+", name, pts, len(allbad)))
        for z in allbad[:3]:
            print("      " + z)


if __name__ == "__main__":
    main()
