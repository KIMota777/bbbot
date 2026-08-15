# -*- coding: utf-8 -*-
"""Прогон всех кандидатов через объявленные критерии приёмки.

TEST не читается: границы окон заканчиваются на VAL. Всё, что здесь считается,
считается на обучении и проверке, и служит одному — решить, КОГО пускать на
экзамен. Кандидат, не прошедший приёмку, на экзамен не идёт вовсе: иначе
экзамен превратится в ещё один тур отбора, и его чистота пропадёт.

Запуск: python gate.py --names a,b,c --tf 60,240
"""
import argparse
import json
import os
import sys

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import accept      # noqa: E402
import finalists   # noqa: E402
import portfolio   # noqa: E402
import rdata       # noqa: E402
import universe    # noqa: E402

OUT = os.path.join(DIR, "out")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--names", required=True)
    ap.add_argument("--tf", default="60,240")
    ap.add_argument("--out", default="gate.json")
    a = ap.parse_args()

    reg, _ = universe.load()
    syms = rdata.SYMBOLS
    survivors, report = [], {}

    for tf in a.tf.split(","):
        print("\n" + "#" * 78)
        print("# ТАЙМФРЕЙМ %s МИНУТ" % tf)
        print("#" * 78)
        for name in a.names.split(","):
            st = reg.get(name)
            if st is None:
                continue
            port, trades = finalists.evaluate(st, tf, syms)
            if not port:
                print("\n%s — сделок нет" % name)
                continue
            mc = portfolio.monte_carlo_portfolio(trades, n_sims=10000)
            mo = portfolio.monthly_from_curve(port["times"], port["curve"])
            conc = accept.concentration(trades)
            s15, _ = finalists.evaluate(st, tf, syms, slip_mult=2.0,
                                        fee_mult=1.5)
            passed, rows, warn = accept.check(name, port, mc, mo, conc,
                                              s15["ret"] if s15 else None)
            key = "%s@%s" % (name, tf)
            print("\n%s  (портфель: %+.1f%%, в месяц %+.2f%%, просадка %.1f%%)"
                  % (key, 100 * port["ret"], 100 * port["mo"],
                     100 * port["maxdd"]))
            for label, ok, got, need in rows:
                print("   %s %-28s %10s   нужно %s"
                      % ("+" if ok else "!", label, got, need))
            for label, ok, got, need in warn:
                print("   %s %-28s %10s   %s"
                      % ("." if ok else "~", label, got, need))
            print("   ИТОГ: %s" % ("ПРОШЁЛ" if passed else "НЕ ПРОШЁЛ"))
            report[key] = dict(
                passed=passed,
                checks=[[l, ok, g, n] for l, ok, g, n in rows],
                port=dict(ret=port["ret"], mo=port["mo"], maxdd=port["maxdd"],
                          trades=port["trades"],
                          max_concurrent=port["max_concurrent"]),
                mc=mc, concentration=conc,
                warnings=[[l, ok, g, n] for l, ok, g, n in warn],
                monthly=[[list(k), float(v)] for k, v in mo])
            if passed:
                survivors.append(key)

    print("\n\n" + "=" * 78)
    print("ПРОШЛИ ПРИЁМКУ И ДОПУЩЕНЫ К ЭКЗАМЕНУ: %s"
          % (", ".join(survivors) if survivors else "НИКТО"))
    print("=" * 78)
    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    with open(os.path.join(OUT, a.out), "w", encoding="utf-8") as fh:
        json.dump(dict(survivors=survivors, report=report), fh,
                  ensure_ascii=False, default=float)


if __name__ == "__main__":
    main()
