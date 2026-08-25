# -*- coding: utf-8 -*-
"""АТАКА 2. Шум одной сделки и эталон «купил и держал» на том же плече."""
import sys, time
import numpy as np
import attack_significance_lib as L
import evolution2 as e2
import ext_data as xd

RANKS = [1, 2, 3, 4, 5, 13, 23, 26, 30, 31, 32, 47]


def bh_bench(candles, lev=5.0):
    """Купил и держал: (а) простое x5 к движению цены; (б) ежедневная
    перебалансировка плеча x5 с проверкой ликвидации."""
    p0, p1 = candles[0][4], candles[-1][4]
    simple = (p1 / p0 - 1) * 100 * lev
    step = 96                                   # день = 96 свечей по 15м
    eq, liq = 1.0, False
    for i in range(step, len(candles), step):
        r = candles[i][4] / candles[i - step][4] - 1
        eq *= (1 + lev * r)
        if eq <= 0:
            eq, liq = 0.0, True
            break
    return simple, (eq - 1) * 100, liq


def main():
    pct5 = xd.fetch_daily_pct5()
    rows = L.rows()
    print("%-4s %-8s %-26s %-5s %6s %9s %9s %8s %8s"
          % ("ранг", "мон", "откуда", "пол", "сдел", "итог%", "сред.сдел$",
             "t", "p(дв)"))
    for rk in RANKS:
        x = rows[rk - 1]
        hs = L.halves(x["sym"], x["genome"], pct5)
        for h in ("train", "hold"):
            d = L.run_half(hs[h], x["genome"], 5.0)
            pn = np.array([p for _, p in d["closes"]])
            n = len(pn)
            if n < 2:
                print("%-4d %-8s %-26s %-5s %6d  — сделок нет"
                      % (rk, x["sym"], (x["src"] + "/" + x["tag"])[:26], h, n))
                continue
            mu, sd = pn.mean(), pn.std(ddof=1)
            se = sd / np.sqrt(n)
            t = mu / se if se else 0.0
            from math import erfc, sqrt
            p = erfc(abs(t) / sqrt(2))
            print("%-4d %-8s %-26s %-5s %6d %+9.2f %+10.4f %8.2f %8.4f"
                  % (rk, x["sym"], (x["src"] + "/" + x["tag"])[:26], h, n,
                     d["comp"], mu, t, p))
        sys.stdout.flush()

    print("\n=== КУПИЛ И ДЕРЖАЛ, x5, те же половины ===")
    for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "LTCUSDT", "DOGEUSDT"):
        c, _ = L.prep_sym(sym, pct5)
        n = len(c)
        hh = int(n * 0.72)
        for name, a, b in (("train", 0, hh), ("hold", hh, n)):
            s, comp, liq = bh_bench(c[a:b])
            print("  %-8s %-5s  простое x5 %+10.1f%%   с перебалансировкой "
                  "%+12.1f%% %s" % (sym, name, s, comp, "ЛИКВИД" if liq else ""))


if __name__ == "__main__":
    main()
