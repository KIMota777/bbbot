# -*- coding: utf-8 -*-
"""Аудит: плотность сигналов по ВСЕЙ сетке параметров каждой стратегии."""
import sys

import numpy as np

import rdata
import universe

REG, _ = universe.load()


def main():
    sym = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    tf = sys.argv[2] if len(sys.argv) > 2 else "60"
    bars = rdata.load_bars(sym, tf)
    n = len(bars.t)
    print("%s tf=%s баров=%d" % (sym, tf, n))
    print("%-20s %6s %6s %6s %7s %7s %7s" %
          ("стратегия", "комб", "пусто", ">30%", "мин%", "медиана%", "макс%"))
    rows = []
    for name in sorted(REG):
        st = REG[name]
        fr = []
        dead = []
        dense = []
        for p in st.combos():
            try:
                s = st.build(bars, p)
            except Exception as e:  # noqa: BLE001
                print("  ОШИБКА %s %s: %s" % (name, p, e))
                continue
            f = float((np.asarray(s.entry) != 0).mean())
            fr.append(f)
            if f == 0.0:
                dead.append(p)
            if f > 0.30:
                dense.append((f, p))
        if not fr:
            continue
        fr = np.array(fr)
        rows.append((name, len(fr), len(dead), len(dense),
                     fr.min(), np.median(fr), fr.max(), dead, dense))
        print("%-20s %6d %6d %6d %7.3f %7.3f %7.3f" %
              (name, len(fr), len(dead), len(dense),
               100 * fr.min(), 100 * np.median(fr), 100 * fr.max()))
    print("\n=== ПУСТЫЕ КОМБИНАЦИИ ===")
    for r in rows:
        if r[2]:
            print("%s: %d/%d пустых, напр. %s" % (r[0], r[2], r[1], r[7][:3]))
    print("\n=== СЛИШКОМ ПЛОТНЫЕ (>30%% баров вход) ===")
    for r in rows:
        if r[3]:
            top = sorted(r[8], key=lambda z: -z[0])[:3]
            print("%s: %d/%d, напр. %s" %
                  (r[0], r[3], r[1], [(round(f, 3), p) for f, p in top]))


if __name__ == "__main__":
    main()
