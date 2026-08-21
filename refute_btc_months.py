# -*- coding: utf-8 -*-
"""ПОПЫТКА СЛОМАТЬ НАХОДКУ, ЧАСТЬ 4: помесячный профиль и цена ожидания.

Блок 13. КАК СОБРАН ПЛЮС ПО МЕСЯЦАМ. Итог за период — самая грубая мера:
  один удачный месяц закрывает полгода топтания. Волны отбора мерили именно
  помесячный профиль (p25 и медиана месяца), поэтому смотрим его же: доля
  прибыльных месяцев, медианный месяц, худшие месяцы — на обеих половинах,
  при одном плече x5.

Блок 14. СКОЛЬКО ВРЕМЕНИ БОТ ПРОСТО СИДИТ В МИНУСЕ. Считаем по кривой
  сделок: наибольшая серия месяцев без нового максимума капитала. Красивый
  итог за 10 месяцев ничего не стоит, если внутри него полгода под водой.
"""
import statistics
import sys
import time

import bots_honest as bh
import refute_btc_cache as cache
import config
import evolution2 as e2
import evolution7 as e7
import evolution8 as e8


def seg(candles, aux, a, b):
    c = candles[a:b]
    return dict(candles=c, pre=e2.prep(c),
                aux={k: bh.slice_aux(v, a, b) for k, v in aux.items()},
                months=(c[-1][0] - c[0][0]) / (30 * 86400000))


def run(s, g, lev):
    evs = []
    r = bh.run_at(s["candles"], s["pre"], g, e8.make_filter8(g, s["aux"]),
                  lev, events=evs)
    return r, evs


def genome(mode):
    p = config.SYMBOL_PARAMS["BTCUSDT"][mode]
    g = e7.cfg_to_genome(p, mode)
    for k, v in e8.OFF8.items():
        g.setdefault(k, v)
    return g


def main():
    tee = bh.Tee("refute_btc_months_out.txt")
    old, sys.stdout = sys.stdout, tee
    try:
        body()
    finally:
        sys.stdout = old


def body():
    print("ПОПЫТКА ОПРОВЕРЖЕНИЯ, ЧАСТЬ 4 — " + time.strftime("%Y-%m-%d %H:%M"))
    c, aux = cache.load()
    e2.BARS_PER_DAY = 96
    n = len(c)
    h = int(n * bh.HOLD_FRAC)
    gn, gf = genome("normal"), genome("final")
    parts = (("обучение", seg(c, aux, 0, h)), ("холдоут", seg(c, aux, h, n)))

    print("\n" + "=" * 100)
    print("БЛОК 13. ПОМЕСЯЧНЫЙ ПРОФИЛЬ, ОБА КОНФИГА НА x5")
    print("  %-9s %-7s %6s %8s %9s %9s %9s %9s"
          % ("кусок", "конфиг", "мес", "приб.мес", "медиана", "p25",
             "худший", "лучший"))
    for tag, s in parts:
        for name, g in (("normal", gn), ("final", gf)):
            r, _ = run(s, g, 5)
            st = e2.stats(r)
            nm = max(1, int(r["months"]))
            rets = [(r["monthly"].get(m, 0.0) / e2.START) * 100
                    for m in range(nm)]
            print("  %-9s %-7s %6d %7.0f%% %+8.2f%% %+8.2f%% %+8.2f%% "
                  "%+8.2f%%"
                  % (tag, name, nm, st["pos_share"] * 100, st["med"],
                     st["p25"], min(rets), max(rets)))
    print("\n  Напоминание: критерий отбора волн — p25 + 0.5*медиана. Столбцы "
          "«медиана» и «p25» — это ровно он, по кускам.")

    print("\n" + "=" * 100)
    print("БЛОК 14. СКОЛЬКО МЕСЯЦЕВ ПОДРЯД БЕЗ НОВОГО МАКСИМУМА КАПИТАЛА")
    for tag, s in parts:
        for name, g in (("normal", gn), ("final", gf)):
            r, evs = run(s, g, 5)
            nm = max(1, int(r["months"]))
            bal, peak, best, cur = e2.START, e2.START, 0, 0
            for m in range(nm):
                bal += r["monthly"].get(m, 0.0)
                if bal >= peak:
                    peak, cur = bal, 0
                else:
                    cur += 1
                    best = max(best, cur)
            print("  %-9s %-7s самая долгая просадка по месяцам: %2d мес "
                  "из %d" % (tag, name, best, nm))


if __name__ == "__main__":
    main()
