# -*- coding: utf-8 -*-
"""ВТОРАЯ ПОПЫТКА СЛОМАТЬ находку «BTC normal x5 лучше BTC final x15».

Первая линза (точка раскола) находку не сломала, но семь холдоутов 0.60..0.85
— это семь ВЛОЖЕННЫХ хвостов одной и той же истории: сделки у них общие,
поэтому «7 из 7» — далеко не семь независимых свидетельств. Здесь беру углы,
где данные действительно разные:

  A. НЕПЕРЕСЕКАЮЩИЕСЯ окна по всей истории 3.2 года. Один прогон на полной
     истории на конфиг, сделки разложены по календарным кускам — окна не
     делят между собой ни одной сделки и никто не платит за прогрев окна.
     Если normal выигрывает только в последних кусках, находка — про рынок
     последнего года, а не про конфиг.
  B. ПЛЕЧО. Сравнивались не два конфига, а две УПАКОВКИ «конфиг+плечо».
     Гоняю normal на x15 и final на x5, чтобы отделить одно от другого.
  C. ЗНАЧИМОСТЬ 31 СДЕЛКИ. Бутстрап по сделкам и t-статистика: отличим ли
     плюс от нуля вообще.
  D. СЛУЧАЙНЫЕ ГЕНОМЫ. Если случайная сетка на этом же куске даёт столько же,
     конфиг ни при чём — период такой.
  E. ПЕРЕСЕЧЕНИЕ вложенных холдоутов в числах, чтобы не переоценить «7 из 7».
"""
import math
import random
import statistics

import bots_honest as bh
import evolution2 as e2
import evolution4 as e4
import evolution5 as e5
import evolution7 as e7
import evolution8 as e8
import refute_btc_common as R

N_WIN = 12
N_RAND = 200
N_BOOT = 20000
FRACS = [0.60, 0.65, 0.70, 0.72, 0.75, 0.80, 0.85]


def main():
    c, aux = R.load()
    n = len(c)
    cfgs = [("normal x5",) + R.genome("BTCUSDT", "normal"),
            ("final x15",) + R.genome("BTCUSDT", "final")]
    h72 = int(n * 0.72)

    # ------------------------------------------------- A. непересекающиеся окна
    print("=" * 100)
    print("A. НЕПЕРЕСЕКАЮЩИЕСЯ ОКНА ПО ВСЕЙ ИСТОРИИ (%d кусков по ~%d дней), "
          "PnL $ на базе $20" % (N_WIN, 1150 // N_WIN))
    edges = [int(n * i / N_WIN) for i in range(N_WIN + 1)]
    res = {}
    for name, g, lev in cfgs:
        m = R.run_seg(c, aux, 0, n, g, lev)
        res[name] = m
        print("   %-10s на всей истории: %d сделок, итог %+.1f%% (фикс. маржа), "
              "просадка %.1f%%%s"
              % (name, m["trades"], m["ret"], m["dd"],
                 "  СЛИТ ДЕПОЗИТ" if m["ruined"] else ""))
    print()
    print("%-6s %-12s %6s %9s | %6s %9s | %s"
          % ("окно", "начало", "сд.N", "PnL N$", "сд.F", "PnL F$", "кто лучше"))
    wins = []
    for i in range(N_WIN):
        a, b = c[edges[i]][0], c[edges[i + 1] - 1][0]
        row = []
        for name, g, lev in cfgs:
            part = [p for t, p in res[name]["closes"] if a <= t <= b]
            row.append((len(part), sum(part)))
        better = "normal" if row[0][1] > row[1][1] else "final"
        wins.append(better == "normal")
        print("%-6s %-12s %6d %+9.2f | %6d %+9.2f | %s"
              % ("#%d" % (i + 1), R.day(a), row[0][0], row[0][1],
                 row[1][0], row[1][1], better))
    print("   normal лучше в %d из %d непересекающихся окон "
          "(монетка дала бы ~%.0f)" % (sum(wins), N_WIN, N_WIN / 2))
    kw = sum(1 for i in range(N_WIN) if edges[i + 1] <= h72)
    print("   окна #1..#%d целиком в ОБУЧАЮЩЕЙ части (на ней final и "
          "настраивали), #%d..#%d — холдоут." % (kw, kw + 1, N_WIN))
    print("   normal лучше в %d обучающих окнах из %d и в %d холдоутных из %d."
          % (sum(wins[:kw]), kw, sum(wins[kw:]), N_WIN - kw))

    # ------------------------------------------------------------- B. плечо
    print()
    print("=" * 100)
    print("B. РАЗДЕЛЯЕМ КОНФИГ И ПЛЕЧО на холдоуте 0.72 (сравнивались-то "
          "упаковки «конфиг+плечо»)")
    print("%-10s %6s %6s %9s %9s %8s %7s"
          % ("конфиг", "плечо", "сдел", "итог%", "б/коп%", "просад", "слит?"))
    for name, g, lev in cfgs:
        for lv in (5, 10, 15):
            m = R.run_seg(c, aux, h72, n, g, lv)
            mark = "  <- родное" if lv == lev else ""
            print("%-10s %6s %6d %+8.1f%% %+8.1f%% %7.1f%% %7s%s"
                  % (name.split()[0], "x%d" % lv, m["trades"], m["comp"],
                     m["ret_nt"], m["dd"], "ДА" if m["ruined"] else "нет", mark))

    # ------------------------------------------- C. значимость 31 сделки
    print()
    print("=" * 100)
    print("C. ЗНАЧИМОСТЬ НА ХОЛДОУТЕ 0.72 (31 сделка — мало; отличим ли плюс "
          "от нуля)")
    rng = random.Random(20260821)
    for name, g, lev in cfgs:
        m = R.run_seg(c, aux, h72, n, g, lev)
        pn = [0.0 if bh.is_tiny(p) else p for _, p in m["closes"]]
        k = len(pn)
        mean = statistics.mean(pn)
        sd = statistics.stdev(pn) if k > 1 else 0.0
        t = mean / (sd / math.sqrt(k)) if sd > 0 else float("nan")
        boot = sum(1 for _ in range(N_BOOT)
                   if sum(rng.choice(pn) for _ in range(k)) > 0) / N_BOOT * 100
        print("   %-10s сделок %2d | средняя %+.4f$ | разброс %.3f$ | "
              "t = %+.2f | бутстрап: плюс в %.1f%% пересборок"
              % (name, k, mean, sd, t, boot))
    print("   |t| меньше ~2 = на таком числе сделок результат от нуля "
          "не отличить, каким бы красивым он ни был.")

    # ------------------------------------------- D. случайные геномы
    print()
    print("=" * 100)
    print("D. %d СЛУЧАЙНЫХ ГЕНОМОВ ЯДРА (ворота выключены) НА ТОМ ЖЕ "
          "ХОЛДОУТЕ, каждый на плече своего конфига" % N_RAND)
    seg = c[h72:]
    pre = e2.prep(seg)
    sub = {k: bh.slice_aux(v, h72, n) for k, v in aux.items()}
    for name, g, lev in cfgs:
        rr = random.Random(2026)
        rets = []
        for _ in range(N_RAND):
            gr = {}
            for k, (lo, hi, is_int) in e2.GENES.items():
                v = rr.uniform(lo, hi)
                gr[k] = int(round(v)) if is_int else v
            gr.update(e8.OFF8)
            gr.update(e4.OFF4)
            gr.update(e5.OFF5)
            gr.update(e7.OFF7)
            r2 = bh.run_at(seg, pre, gr, e8.make_filter8(gr, sub), lev)
            rets.append((r2["balance"] / e2.START - 1) * 100)
        m = R.run_seg(c, aux, h72, n, g, lev)
        rank = sum(1 for x in rets if x < m["ret"]) / len(rets) * 100
        print("   %-10s (x%-2d) факт %+7.1f%% | случайные: медиана %+6.1f%%, "
              "p90 %+6.1f%%, доля>0 %3.0f%% | ранг факта %3.0f%%"
              % (name, lev, m["ret"], statistics.median(rets),
                 bh.pct(rets, 0.9),
                 sum(1 for x in rets if x > 0) / len(rets) * 100, rank))
    print("   Ранг около 50%% = конфиг не лучше случайной сетки, весь "
          "результат дал период.")

    # --------------------------------------- E. пересечение вложенных холдоутов
    print()
    print("=" * 100)
    print("E. НАСКОЛЬКО НЕЗАВИСИМЫ СЕМЬ ТОЧЕК РАСКОЛА (normal x5: сделки, "
          "общие с холдоутом 0.72)")
    b72 = set(t for t, _ in R.run_seg(c, aux, h72, n, cfgs[0][1],
                                      cfgs[0][2])["closes"])
    print("%-6s %8s %12s %8s" % ("frac", "сделок", "общих с 0.72", "своих"))
    for f in FRACS:
        m = R.run_seg(c, aux, int(n * f), n, cfgs[0][1], cfgs[0][2])
        ts = set(t for t, _ in m["closes"])
        print("%-6.2f %8d %12d %8d" % (f, len(ts), len(ts & b72), len(ts - b72)))
    print("   Собственной, не общей с 0.72 информации в каждой новой точке "
          "раскола — единицы сделок.")
    print("   Значит «7 из 7» — это не семь проверок, а одна проверка, "
          "пересчитанная семь раз.")


if __name__ == "__main__":
    main()
