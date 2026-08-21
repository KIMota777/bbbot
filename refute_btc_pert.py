# -*- coding: utf-8 -*-
"""Линза 2: возмущения генома крупнее прежних (+-10 -> +-20 -> +-30%).

Смысл проверки. Конфиг стоит там, где его поставил подбор на этих же данных.
Завтрашний рынок сдвинет оптимум — вопрос, на сколько. Если доход держится
только в узкой ямке вокруг найденной точки, это подгонка. Настоящее свойство
рынка переживает сдвиг параметров хотя бы частично.

Здесь же — то, чего в прежней проверке не было и что сильно меняет чтение
результата: СКОЛЬКО генов упирается в границу диапазона поиска. У BTC normal
step стоит ровно на минимуме (0.004), mult и knife — ровно на максимуме, окно
877 из 900. Возмущение такого гена односторонне: половина бросков зажимается
обратно в саму точку. Чем шире возмущение, тем большая доля соседей — это не
соседи, а сам конфиг. Поэтому считаю отдельно долю «фактически не сдвинутых»
геномов и отдельно — прогон только по РАБОЧИМ генам ядра (без выключенных
ворот, которые от возмущения могут только включиться).
"""
import sys

import numpy as np

import bots_honest as bh
import evolution8 as e8
import refute_btc_common as rc

# рабочие гены ядра стратегии у BTC normal (ворота у него все выключены)
CORE = ["rsi_os", "zone_l", "zone_s", "window", "step", "mult", "tp",
        "sweep", "max_bars", "cooldown", "knife"]
# выключенные ворота: значения стоят на разрешающем краю, возмущение может
# их только ВКЛЮЧИТЬ (сделать строже) — это не «сосед», а другая стратегия
OFF_GATES = [k for k in bh.NUMERIC_GENES if k not in CORE]

LEVELS = [0.10, 0.20, 0.30]
N = 90
SEEDS = [7, 77, 777]


def perturb_subset(g, keys, rng, frac):
    """То же, что bh.perturb, но возмущаются только выбранные гены."""
    old = bh.NUMERIC_GENES
    bh.NUMERIC_GENES = [k for k in old if k in keys]
    try:
        return bh.perturb(g, e8.GENES8, rng, frac)
    finally:
        bh.NUMERIC_GENES = old


def moved_share(g, gp, keys):
    """Доля генов, которые реально сдвинулись (не зажаты обратно в точку)."""
    n = mv = 0
    for k in keys:
        if k not in g:
            continue
        n += 1
        if abs(gp[k] - g[k]) > 1e-9 * max(1.0, abs(g[k])):
            mv += 1
    return mv / n if n else 0.0


def batch(d, keys, frac, seed, n=N):
    rng = np.random.default_rng(seed)
    comps, nts, trs, movs = [], [], [], []
    for _ in range(n):
        gp = perturb_subset(d["g"], keys, rng, frac)
        movs.append(moved_share(d["g"], gp, keys))
        filt = e8.make_filter8(gp, d["aux_h"])
        r, evs = rc.run_hold(d, g=gp, filt=filt)
        m = rc.nums(r, evs, d["hold"]["months"])
        comps.append(m["comp"])
        nts.append(m["comp_nt"])
        trs.append(m["trades"])
    return comps, nts, trs, movs


def row(tag, comps, nts, trs, movs):
    a = np.array(nts, dtype=float)
    b = np.array(comps, dtype=float)
    print("%-34s %+8.1f%% %+8.1f%% %+8.1f%% %6.0f%% | %+8.1f%% %6.0f%% "
          "| %5.0f %5.0f%%"
          % (tag, np.median(a), np.percentile(a, 10), np.percentile(a, 90),
             (a > 0).mean() * 100, np.median(b), (b > 0).mean() * 100,
             np.median(trs), np.mean(movs) * 100))
    sys.stdout.flush()


def main():
    d = rc.build()
    r0, e0 = rc.run_hold(d)
    m0 = rc.nums(r0, e0, d["hold"]["months"])
    print("BTC normal x5, ХОЛДАУТ. Факт: %+.1f%% (без копеек %+.1f%%), "
          "сделок %d" % (m0["comp"], m0["comp_nt"], m0["trades"]))
    print()
    print("КОПЕЕЧНЫЕ ВЫХОДЫ ОБНУЛЕНЫ (столбцы «б/коп»); справа — то же с ними.")
    print("«сдвин.» = какая доля возмущённых генов реально ушла с места, "
          "а не была зажата обратно границей диапазона.")
    print("%-34s %9s %9s %9s %7s | %9s %7s | %5s %6s"
          % ("набор генов / размах / зерно", "медиана", "p10", "p90", "доля+",
             "медиана", "доля+", "сдел", "сдвин."))

    for frac in LEVELS:
        for seed in SEEDS:
            c, nt, tr, mv = batch(d, bh.NUMERIC_GENES, frac, seed)
            row("все числовые +-%d%%, зерно %d" % (frac * 100, seed),
                c, nt, tr, mv)
        # свод по трём зёрнам
        allc, allnt, alltr, allmv = [], [], [], []
        for seed in SEEDS:
            c, nt, tr, mv = batch(d, bh.NUMERIC_GENES, frac, seed)
            allc += c
            allnt += nt
            alltr += tr
            allmv += mv
        row("  ВСЕ 270 (+-%d%%)" % (frac * 100), allc, allnt, alltr, allmv)

    print()
    print("То же, но возмущаются ТОЛЬКО рабочие гены ядра "
          "(выключенные ворота не трогаем):")
    for frac in LEVELS:
        allc, allnt, alltr, allmv = [], [], [], []
        for seed in SEEDS:
            c, nt, tr, mv = batch(d, CORE, frac, seed)
            allc += c
            allnt += nt
            alltr += tr
            allmv += mv
        row("  ядро, ВСЕ 270 (+-%d%%)" % (frac * 100), allc, allnt, alltr,
            allmv)

    print()
    print("И наоборот — только выключенные ворота (%s):" % ", ".join(OFF_GATES))
    for frac in LEVELS:
        allc, allnt, alltr, allmv = [], [], [], []
        for seed in SEEDS:
            c, nt, tr, mv = batch(d, OFF_GATES, frac, seed)
            allc += c
            allnt += nt
            alltr += tr
            allmv += mv
        row("  ворота, ВСЕ 270 (+-%d%%)" % (frac * 100), allc, allnt, alltr,
            allmv)


if __name__ == "__main__":
    main()
