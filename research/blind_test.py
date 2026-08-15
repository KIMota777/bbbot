# -*- coding: utf-8 -*-
"""ЭКЗАМЕН. Один прогон по выборке, которую до этой минуты не читали.

Правила, которые здесь соблюдаются и которые делают этот прогон осмысленным:

  1. Конфигурация взята из FINAL_SPEC.md и не меняется. Ни состав ансамбля,
     ни доля риска, ни таймфрейм, ни длина окон.
  2. Параметры правил на экзаменационном периоде выбираются по данным,
     заканчивающимся ДО его начала, — тем же скользящим окном, что и раньше.
  3. Прогон делается ОДИН раз. Если результат плох, он остаётся результатом.
     Любая правка после просмотра экзамена превращает экзамен в ещё один тур
     отбора, и тогда честной оценки не остаётся вовсе.

Для сравнения считается то же самое на обучении и проверке, а также опорные
линии: «купил и держи» и нынешние боевые боты за тот же период.

Запуск: python blind_test.py
"""
import json
import os
import sys

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import frontier    # noqa: E402
import metrics     # noqa: E402
import portfolio   # noqa: E402
import rdata       # noqa: E402
import universe    # noqa: E402

# --- ЗАФИКСИРОВАНО В FINAL_SPEC.md -----------------------------------------
MEMBERS = ["c_willr", "bos", "vol_spike", "supertrend"]
TF = "240"
RISK_K = 0.43
SYMBOLS = rdata.SYMBOLS
IS_DAYS, OOS_DAYS = 360, 90
# ---------------------------------------------------------------------------

OUT = os.path.join(DIR, "out")


def collect(reg, t_start, t_end):
    """Сделки ансамбля с внеобучающих кусков в заданных границах."""
    per = []
    for name in MEMBERS:
        st = reg[name]
        trades = {}
        for sym in SYMBOLS:
            trs, _ = portfolio.oos_trades(st, sym, TF, is_days=IS_DAYS,
                                          oos_days=OOS_DAYS, t_start=t_start,
                                          t_end=t_end)
            if trs:
                trades[sym] = trs
        if trades:
            per.append(trades)
    return frontier.merge(per) if per else {}


def window(merged, t0, t1):
    out = {}
    for k, trs in merged.items():
        sel = [t for t in trs if t0 <= t["t_in"] < t1]
        if sel:
            out[k] = sel
    return out


def describe(tag, trades, k):
    if not trades:
        print("%-26s сделок нет" % tag)
        return None
    c = portfolio.combine(trades, risk_each=k)
    mo = portfolio.monthly_from_curve(c["times"], c["curve"])
    mv = np.array([m[1] for m in mo]) if mo else np.array([])
    mc = portfolio.monte_carlo_portfolio(trades, risk_each=k, n_sims=10000)
    print("%-26s итог %+8.2f%% | месяц %+6.2f%% | медиана %+6.2f%% | "
          "просадка %5.1f%% | сделок %4d | мес+ %3.0f%%"
          % (tag, 100 * c["ret"], 100 * c["mo"],
             100 * np.median(mv) if len(mv) else 0.0,
             100 * c["maxdd"], c["trades"],
             100 * (mv > 0).mean() if len(mv) else 0.0))
    return dict(ret=c["ret"], mo=c["mo"], maxdd=c["maxdd"],
                trades=c["trades"], days=c["days"],
                max_concurrent=c["max_concurrent"],
                mo_med=float(np.median(mv)) if len(mv) else 0.0,
                mo_pos=float((mv > 0).mean()) if len(mv) else 0.0,
                months=[[list(a), float(b)] for a, b in mo],
                mc=mc)


def main():
    print("=" * 80)
    print("ЭКЗАМЕН. Конфигурация из FINAL_SPEC.md, один прогон, без правок.")
    print("=" * 80)
    print("Ансамбль: %s" % ", ".join(MEMBERS))
    print("Таймфрейм %s мин, монеты: %s"
          % (TF, ", ".join(s.replace("USDT", "") for s in SYMBOLS)))
    print("Доля риска на рукав: %.2f%% (0.43 от базовой единицы)"
          % (100 * 0.01 * RISK_K))
    t_test0, t_test1 = rdata.SPLITS["test"]
    print("Экзамен: %d дней, до этой минуты не читался."
          % ((t_test1 - t_test0) / rdata.DAY_MS))

    reg, _ = universe.load()
    print("\nСчитаю (прогон по всей истории, окна выбираются по прошлому)...")
    merged = collect(reg, rdata.HIST_START_MS, rdata.HIST_END_MS + 1)
    if not merged:
        print("сделок нет вовсе — что-то не так")
        return

    print("\n%-26s %s" % ("период", "результат"))
    res = {}
    res["trainval"] = describe("обучение + проверка",
                               window(merged, *rdata.SPLITS["trainval"]),
                               RISK_K)
    res["test"] = describe("ЭКЗАМЕН", window(merged, t_test0, t_test1), RISK_K)

    print("\nОПОРНЫЕ ЛИНИИ ЗА ТОТ ЖЕ ЭКЗАМЕНАЦИОННЫЙ ПЕРИОД")
    for sym in SYMBOLS:
        b = rdata.load_bars(sym, "240")
        m = (b.t >= t_test0) & (b.t < t_test1)
        if m.sum() < 5:
            continue
        c = b.c[m]
        ret = c[-1] / c[0] - 1.0
        dd = metrics.max_drawdown(c)[0]
        print("   купил и держу %-5s %+8.1f%%   просадка %5.1f%%"
              % (sym.replace("USDT", ""), 100 * ret, 100 * dd))

    t = res.get("test")
    if t:
        print("\nПОМЕСЯЧНО НА ЭКЗАМЕНЕ")
        for (y, mth), v in t["months"]:
            print("   %04d-%02d  %+7.2f%%" % (y, mth, 100 * v))
        if t.get("mc"):
            mc = t["mc"]
            print("\nМОНТЕ-КАРЛО НА ЭКЗАМЕНАЦИОННЫХ СДЕЛКАХ")
            print("   просадка 50%% / 95%%: %.1f%% / %.1f%%   "
                  "вероятность уйти глубже 20%%: %.0f%%"
                  % (100 * mc["dd_p50"], 100 * mc["dd_p95"],
                     100 * mc["p_dd20"]))

    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    with open(os.path.join(OUT, "blind_test.json"), "w",
              encoding="utf-8") as fh:
        json.dump(dict(members=MEMBERS, tf=TF, risk_k=RISK_K, result=res),
                  fh, ensure_ascii=False, default=float)
    print("\nсохранено -> out/blind_test.json")
    print("\nЭкзамен состоялся. Конфигурация после этого не правится.")


if __name__ == "__main__":
    main()
