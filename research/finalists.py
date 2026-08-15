# -*- coding: utf-8 -*-
"""Кандидаты: портфель по всем монетам, устойчивость, Монте-Карло.

Здесь отбираются те, кого не стыдно вынести на экзамен. Правила отбора
объявлены заранее и не меняются после просмотра чисел:

  1. Стратегия торгуется на ВСЕХ пяти монетах сразу, равной долей риска.
     Никакого выбора монеты — иначе мы отбираем монету, а не стратегию.
  2. Параметры на каждом окне выбираются только по прошлому (тот же протокол).
  3. Кандидат обязан пережить удорожание издержек в полтора раза.
  4. Просадка по Монте-Карло на 95-м перцентиле не должна выходить за 20%.

Запуск: python finalists.py --names false_break,ema_dist,... --tf 60
Результат печатается и складывается в research/out/finalists.json.
"""
import argparse
import json
import os
import sys

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import portfolio   # noqa: E402
import rdata       # noqa: E402
import universe    # noqa: E402

OUT = os.path.join(DIR, "out")
RISK_PER_ASSET = 0.01          # доля капитала на сделку, на каждую монету


def evaluate(st, tf, symbols, risk=RISK_PER_ASSET, slip_mult=1.0,
             fee_mult=1.0, anchored=False, t_start=None, t_end=None,
             is_days=360, oos_days=90):
    trades = {}
    for sym in symbols:
        trs, _wf = portfolio.oos_trades(
            st, sym, tf, is_days=is_days, oos_days=oos_days,
            anchored=anchored, t_start=t_start, t_end=t_end,
            risk=risk, slip_mult=slip_mult,
            fee=rdata.TAKER_FEE * fee_mult)
        if trs:
            trades[sym] = trs
    if not trades:
        return None, {}
    return portfolio.combine(trades), trades


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--names", required=True)
    ap.add_argument("--tf", default="60")
    ap.add_argument("--out", default="finalists.json")
    a = ap.parse_args()

    reg, _ = universe.load()
    syms = rdata.SYMBOLS
    names = [n for n in a.names.split(",") if n in reg]
    report = {}

    for tf in a.tf.split(","):
        print("\n" + "=" * 78)
        print("ТАЙМФРЕЙМ %s МИНУТ — портфель из %d монет, риск %.1f%% на сделку"
              % (tf, len(syms), 100 * RISK_PER_ASSET))
        print("=" * 78)
        print("%-18s %9s %8s %8s %7s %7s %6s"
              % ("стратегия", "итог", "в месяц", "просадка", "сделок",
                 "макс.одн", "монет"))
        base = {}
        for name in names:
            st = reg[name]
            r, trades = evaluate(st, tf, syms)
            if not r:
                print("%-18s  сделок нет" % name)
                continue
            base[name] = (r, trades)
            print("%-18s %+8.1f%% %+7.2f%% %7.1f%% %7d %8d %6d"
                  % (name, 100 * r["ret"], 100 * r["mo"], 100 * r["maxdd"],
                     r["trades"], r["max_concurrent"], len(trades)))

        print("\nУСТОЙЧИВОСТЬ К ИЗДЕРЖКАМ (тот же портфель, дороже исполнение)")
        print("%-18s %10s %10s %10s" % ("стратегия", "база",
                                        "x1.5 / x2", "x2 / x3"))
        for name in list(base):
            st = reg[name]
            r0 = base[name][0]
            r1, _ = evaluate(st, tf, syms, slip_mult=2.0, fee_mult=1.5)
            r2, _ = evaluate(st, tf, syms, slip_mult=3.0, fee_mult=2.0)
            print("%-18s %+9.1f%% %+9.1f%% %+9.1f%%"
                  % (name, 100 * r0["ret"],
                     100 * r1["ret"] if r1 else float("nan"),
                     100 * r2["ret"] if r2 else float("nan")))
            report.setdefault(name, {})["tf" + tf] = dict(
                base=dict(ret=r0["ret"], mo=r0["mo"], maxdd=r0["maxdd"],
                          trades=r0["trades"]),
                stress15=dict(ret=r1["ret"], maxdd=r1["maxdd"]) if r1 else None,
                stress20=dict(ret=r2["ret"], maxdd=r2["maxdd"]) if r2 else None)

        print("\nМОНТЕ-КАРЛО (блочный бутстрэп, 10 000 прогонов)")
        print("%-18s %9s %9s %9s %9s %8s"
              % ("стратегия", "просад 50", "просад 95", "P(DD>20%)",
                 "доход 5%", "P(минус)"))
        for name in list(base):
            _r, trades = base[name]
            mc = portfolio.monte_carlo_portfolio(trades, n_sims=10000)
            if not mc:
                continue
            print("%-18s %8.1f%% %8.1f%% %8.0f%% %+8.1f%% %7.0f%%"
                  % (name, 100 * mc["dd_p50"], 100 * mc["dd_p95"],
                     100 * mc["p_dd20"], 100 * mc["ret_p5"],
                     100 * mc["p_neg"]))
            report.setdefault(name, {}).setdefault("tf" + tf, {})["mc"] = mc

        print("\nПОМЕСЯЧНО (портфель, внеобучающие куски)")
        for name in list(base):
            r = base[name][0]
            mo = portfolio.monthly_from_curve(r["times"], r["curve"])
            vals = np.array([m[1] for m in mo]) if mo else np.array([])
            if not len(vals):
                continue
            print("%-18s месяцев %2d | медиана %+6.2f%% | плюсовых %3.0f%% | "
                  "худший %+6.1f%% | лучший %+6.1f%%"
                  % (name, len(vals), 100 * np.median(vals),
                     100 * (vals > 0).mean(), 100 * vals.min(),
                     100 * vals.max()))
            report.setdefault(name, {}).setdefault("tf" + tf, {})["monthly"] = \
                [[list(k), float(v)] for k, v in mo]

    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    with open(os.path.join(OUT, a.out), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, default=float)
    print("\nсохранено -> out/%s" % a.out)


if __name__ == "__main__":
    main()
