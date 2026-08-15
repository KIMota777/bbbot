# -*- coding: utf-8 -*-
"""Строгая проверка накладок: обрезаем ПО ВРЕМЕНИ и свои бары, и все внешние ряды.

Если накладка причинна, удаление всей истории после момента T не имеет права
изменить ни один сигнал на барах до T.
"""
import numpy as np

import overlay
import rdata
import universe

REG, _ = universe.load()
FIELDS = ("entry", "exit", "stop", "tp", "trail", "size_k")
BASE_P = dict(n=55, exit_n=20, stop_atr=3.0, atr_n=14, trail_atr=0.0)
BASE = REG["donchian"]


def cut_bars(b, cut_ms):
    # оставляем только бары, ЗАКРЫВШИЕСЯ не позже cut_ms: незакрытый старший
    # бар — ровно то, что нельзя читать, и его надо убрать физически
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
            rdata._CACHE[key] = cut_bars(val, cut_ms)
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


def pick(name, k=6):
    cs = list(overlay.combos(name))
    if len(cs) <= k:
        return cs
    return [cs[i] for i in np.linspace(0, len(cs) - 1, k).astype(int)]


def main():
    warm()
    for sym in ("BTCUSDT", "SOLUSDT"):
        for tf in ("60", "240"):
            full_bars = rdata.load_bars(sym, tf)
            n = len(full_bars.t)
            for name in sorted(overlay.OVERLAYS):
                for p in pick(name):
                    def build(b, _p=p, _n=name):
                        return overlay.apply_overlay(
                            b, BASE.build(b, BASE_P), _n, _p)
                    ref = build(full_bars)
                    for fr in (0.6, 0.75, 0.9):
                        i = int(n * fr)
                        cut_ms = int(full_bars.t[i])
                        saved = truncate_cache(cut_ms)
                        try:
                            part = build(rdata.load_bars(sym, tf))
                        finally:
                            rdata._CACHE.update(saved)
                        k = len(part.entry)
                        # последний бар обрезанной выборки не сравниваем:
                        # у него по построению нет следующего
                        k = max(0, k - 1)
                        for f in FIELDS:
                            x = np.asarray(getattr(ref, f),
                                           dtype=np.float64)[:k]
                            y = np.asarray(getattr(part, f),
                                           dtype=np.float64)[:k]
                            d = ~np.isclose(x, y, rtol=1e-9, atol=1e-12,
                                            equal_nan=True)
                            if d.any():
                                print("! %s %s tf=%s %s  cut=%.2f %s: "
                                      "%d/%d, первое на баре %d"
                                      % (name, sym, tf, p, fr, f,
                                         int(d.sum()), k,
                                         int(np.flatnonzero(d)[0])))
    print("готово")


if __name__ == "__main__":
    main()
