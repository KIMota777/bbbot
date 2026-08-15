# -*- coding: utf-8 -*-
"""Мощная проверка накладок: срез ставится на бары, где базовый сигнал есть.

Все внешние ряды обрезаются ПО МОМЕНТУ ЗАКРЫТИЯ: незакрытый старший бар
физически исчезает, и накладка, которая его читала, обязана дать другой ответ.
"""
import numpy as np

import overlay
import rdata
import universe

REG, _ = universe.load()
FIELDS = ("entry", "exit", "stop", "tp", "trail", "size_k")
BASE_P = dict(n=55, exit_n=20, stop_atr=3.0, atr_n=14, trail_atr=0.0)
BASE = REG["donchian"]


def cut_bars_close(b, cut_ms):
    k = int(np.searchsorted(b.t + b.bar_ms(), cut_ms, "right"))
    out = rdata.Bars.__new__(rdata.Bars)
    out.symbol, out.tf = b.symbol, b.tf
    for f in ("t", "o", "h", "l", "c", "v", "turnover"):
        setattr(out, f, getattr(b, f)[:k].copy())
    return out


def truncate_cache(cut_ms):
    saved = {}
    for key, val in list(rdata._CACHE.items()):
        if key[0] == "bars":
            saved[key] = val
            rdata._CACHE[key] = cut_bars_close(val, cut_ms)
        elif key[0] in ("fund", "oi"):
            st, sv = val
            k = int(np.searchsorted(st, cut_ms, "left"))
            saved[key] = val
            rdata._CACHE[key] = (st[:k].copy(), sv[:k].copy())
    return saved


def warm():
    for s in ("BTCUSDT", "SOLUSDT"):
        for tf in ("60", "240"):
            rdata.load_bars(s, tf)
        rdata.load_funding(s)
        rdata.load_oi(s)


def probe(name, p, sym, tf, n_pts=25, builder=None):
    full_bars = rdata.load_bars(sym, tf)
    n = len(full_bars.t)

    def build(b):
        if builder is not None:
            return builder(b, name, p)
        return overlay.apply_overlay(b, BASE.build(b, BASE_P), name, p)

    ref = build(full_bars)
    base_e = np.asarray(BASE.build(full_bars, BASE_P).entry)
    lo = int(n * 0.55)                        # после начала истории ОИ
    cand = np.flatnonzero(base_e[lo:] != 0) + lo
    if not len(cand):
        return [], 0
    pts = cand[np.linspace(0, len(cand) - 1,
                           min(n_pts, len(cand))).astype(int)]
    bad = []
    for j in sorted(set(int(x) for x in pts)):
        cut_ms = int(full_bars.t[j]) + full_bars.bar_ms()
        saved = truncate_cache(cut_ms)
        try:
            part = build(rdata.load_bars(sym, tf))
        finally:
            rdata._CACHE.update(saved)
        if len(part.entry) <= j:
            continue
        for f in FIELDS:
            x = float(np.asarray(getattr(ref, f), dtype=np.float64)[j])
            y = float(np.asarray(getattr(part, f), dtype=np.float64)[j])
            if not np.isclose(x, y, rtol=1e-9, atol=1e-12, equal_nan=True):
                bad.append("бар %d %s: полный=%g обрезанный=%g" % (j, f, x, y))
    return bad, len(set(int(x) for x in pts))


def main():
    warm()
    for sym in ("BTCUSDT", "SOLUSDT"):
        for tf in ("60", "240"):
            for name in sorted(overlay.OVERLAYS):
                cs = list(overlay.combos(name))
                sel = [cs[0], cs[len(cs) // 2], cs[-1]]
                allbad, pts = [], 0
                for p in sel:
                    b, k = probe(name, p, sym, tf)
                    pts += k
                    allbad += ["%s %s" % (p, z) for z in b]
                print("%s %-8s %-4s %-16s точек=%d расхождений=%d"
                      % ("!" if allbad else "+", sym, tf, name, pts,
                         len(allbad)))
                for z in allbad[:2]:
                    print("       " + z)


if __name__ == "__main__":
    main()
