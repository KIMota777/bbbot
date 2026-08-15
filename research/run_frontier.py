# -*- coding: utf-8 -*-
"""Главный вопрос задания: какая доходность достижима при просадке 20%.

Все кандидаты приводятся к одной просадке и только потом сравниваются. Считается
на обучении и проверке; экзамен по-прежнему закрыт.

Запуск: python run_frontier.py --names a,b,c --tf 60,240
"""
import argparse
import itertools
import json
import os
import sys

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import finalists   # noqa: E402
import frontier    # noqa: E402
import portfolio   # noqa: E402
import rdata       # noqa: E402
import universe    # noqa: E402

OUT = os.path.join(DIR, "out")


def line(name, trades, tag=""):
    k_hist, dd_h = frontier.find_k(trades, 0.20, use_mc=False)
    k_mc, dd_m = frontier.find_k(trades, 0.20, use_mc=True)
    row = dict(name=name, tag=tag)
    for label, k in (("hist", k_hist), ("mc", k_mc)):
        if k is None:
            row[label] = None
            continue
        c = frontier.scale_curve(trades, k)
        mo = portfolio.monthly_from_curve(c["times"], c["curve"])
        mv = np.array([m[1] for m in mo]) if mo else np.array([])
        row[label] = dict(k=k, ret=c["ret"], mo=c["mo"], maxdd=c["maxdd"],
                          trades=c["trades"],
                          mo_med=float(np.median(mv)) if len(mv) else 0.0,
                          mo_pos=float((mv > 0).mean()) if len(mv) else 0.0)
    return row


def show(rows, title):
    print("\n" + title)
    print("%-26s %6s %9s %9s %8s %8s %7s"
          % ("стратегия", "риск", "в месяц", "медиана", "просадка", "итог",
             "мес+"))
    for r in rows:
        for label, mark in (("hist", "история"), ("mc", "Монте-Карло")):
            d = r.get(label)
            if not d:
                print("%-26s %6s  %s" % (r["name"][:26], "—",
                                         "не влезает в 20%% даже при риске 0.05%%"
                                         if label == "hist" else ""))
                continue
            print("%-26s %5.2fx %+8.2f%% %+8.2f%% %7.1f%% %+7.1f%% %6.0f%%   [%s]"
                  % (r["name"][:26], d["k"], 100 * d["mo"], 100 * d["mo_med"],
                     100 * d["maxdd"], 100 * d["ret"], 100 * d["mo_pos"],
                     mark))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--names", required=True)
    ap.add_argument("--tf", default="60,240")
    ap.add_argument("--ens", type=int, default=4,
                    help="сколько лучших слабосвязанных сливать в ансамбль")
    ap.add_argument("--out", default="frontier.json")
    a = ap.parse_args()

    reg, _ = universe.load()
    syms = rdata.SYMBOLS
    names = [n for n in a.names.split(",") if n in reg]
    saved = {}

    for tf in a.tf.split(","):
        print("\n" + "=" * 80)
        print("ТАЙМФРЕЙМ %s МИНУТ" % tf)
        print("=" * 80)
        trades = {}
        for n in names:
            _p, tr = finalists.evaluate(reg[n], tf, syms)
            if tr:
                trades[n] = tr
        rows = [line(n, tr) for n, tr in trades.items()]
        rows.sort(key=lambda r: -(r["mc"]["mo"] if r.get("mc") else -9))
        show(rows, "ОДИНОЧНЫЕ СТРАТЕГИИ, ужатые до просадки 20%")

        # --- ансамбли ---
        keys = list(trades)
        if len(keys) >= 2:
            cm, _ = frontier.correlation([trades[k] for k in keys], keys)
            if cm is not None:
                print("\nСВЯЗАННОСТЬ НЕДЕЛЬНЫХ ДОХОДНОСТЕЙ (чем ниже, тем полезнее в паре)")
                print("%-18s %s" % ("", " ".join("%6s" % k[:6] for k in keys)))
                for i, k in enumerate(keys):
                    print("%-18s %s" % (k[:18],
                                        " ".join("%6.2f" % cm[i][j]
                                                 for j in range(len(keys)))))
            best = [r["name"] for r in rows[:a.ens] if r.get("mc")]
            combos = []
            if len(best) >= 2:
                combos.append(("ансамбль: " + "+".join(x[:8] for x in best),
                               best))
            # пара с наименьшей связанностью среди прибыльных
            pos = [r["name"] for r in rows if r.get("mc")
                   and r["mc"]["mo"] > 0]
            if cm is not None and len(pos) >= 2:
                pairs = [(cm[keys.index(x)][keys.index(y)], x, y)
                         for x, y in itertools.combinations(pos, 2)]
                pairs.sort()
                for c, x, y in pairs[:2]:
                    combos.append(("пара (связь %.2f): %s+%s" % (c, x[:9], y[:9]),
                                   [x, y]))
            erows = []
            for label, members in combos:
                merged = frontier.merge([trades[m] for m in members])
                erows.append(line(label, merged, tag=",".join(members)))
            if erows:
                show(erows, "АНСАМБЛИ, ужатые до просадки 20%")
            rows += erows
        saved[tf] = rows

    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    with open(os.path.join(OUT, a.out), "w", encoding="utf-8") as fh:
        json.dump(saved, fh, ensure_ascii=False, default=float)
    print("\nсохранено -> out/%s" % a.out)
    print("\nНапоминание: это обучение и проверка. Экзамен ещё не открывали.")


if __name__ == "__main__":
    main()
