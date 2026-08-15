# -*- coding: utf-8 -*-
u"""Обязательная проверка: ни одно правило и ни один признак не знают будущего.

Три слоя проверки, все механические — не «посмотрел и вроде правильно», а
порча будущего с требованием, чтобы прошлое не шевельнулось:

  1. ПРАВИЛА. engine.assert_causal для каждого правила и каждого сочетания
     параметров, использованных в этом направлении: 14 правил x 5 сочетаний.
  2. БАРНЫЕ ПРИЗНАКИ. ml_lib.assert_features_causal (первая попытка) и
     ml_r2_lib.assert_extra_causal (открытый интерес, поперечный срез).
  3. ПРИЗНАКИ ИЗ ИСТОРИИ СДЕЛОК. ml_r2_streak.assert_perf_causal — исходы
     сделок, закрывшихся после среза, заменяются случайными.
"""
import os
import sys

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import engine            # noqa: E402
import ml_lib            # noqa: E402
import ml_r2_lib as L    # noqa: E402
import ml_r2_pool as P   # noqa: E402
import ml_r2_streak as T  # noqa: E402
import rdata             # noqa: E402


def main():
    t0, t1 = rdata.SPLITS["trainval"]
    reg = ml_lib.registry()
    rules = sorted(set(P.RULES4) | set(P.RULES10))
    print(u"1. ПРАВИЛА: engine.assert_causal, %d правил x 5 сочетаний"
          % len(rules))
    bad, n = [], 0
    for name in rules:
        st = reg[name]
        combos = [ml_lib.center_combo(st)] + \
            [ml_lib.random_combos(st, 1, seed=s)[0] for s in (1, 2, 3, 4)]
        for p in combos:
            for sym in ("BTCUSDT", "DOGEUSDT"):
                for tf in ("60", "240"):
                    bars = rdata.load_bars(sym, tf)
                    sub, _ = bars.slice(t0, t1, warmup=L.WARMUP)
                    try:
                        engine.assert_causal(
                            lambda bb, s=st, q=p: s.build(bb, q), sub)
                        n += 1
                    except AssertionError as e:      # noqa: PERF203
                        bad.append("%s %s %s: %s" % (name, sym, tf, e))
    print(u"   пройдено проверок: %d, провалов: %d" % (n, len(bad)))
    for b in bad:
        print(u"   ПРОВАЛ: %s" % b)

    print(u"\n2. БАРНЫЕ ПРИЗНАКИ")
    for tf in ("60", "240"):
        for sym in ("BTCUSDT", "ETHUSDT", "DOGEUSDT"):
            ml_lib.assert_features_causal(tf=tf, symbol=sym)
            L.assert_extra_causal(tf=tf, symbol=sym)
    print(u"   30 признаков первой попытки и 9 новых (открытый интерес,")
    print(u"   поперечный срез): 6 сочетаний символ x таймфрейм — пройдено")

    print(u"\n3. ПРИЗНАКИ ИЗ ИСТОРИИ СДЕЛОК")
    T.assert_perf_causal()
    print(u"   perf10, perf20_wr, perf_last, gap_days, streak — пройдено")

    print(u"\nИТОГ: %s" % (u"все проверки пройдены" if not bad
                           else u"ЕСТЬ ПРОВАЛЫ, СМ. ВЫШЕ"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
