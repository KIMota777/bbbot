# -*- coding: utf-8 -*-
"""Разбор провала. ДИАГНОСТИКА, а не второй тур отбора.

Экзамен состоялся и дал минус. Здесь считается, что происходило на
экзаменационном периоде с ОСТАЛЬНЫМИ кандидатами — не для того, чтобы выбрать
из них нового победителя (это превратило бы экзамен в очередной тур отбора и
уничтожило бы единственную честную оценку в работе), а чтобы ответить на
вопрос: подвёл отбор или подвёл сам подход.

Разница важна:
  * если на экзамене плохо ВСЕМ — значит на этих данных такими средствами
    перевеса нет вовсе, и никакой отбор не помог бы;
  * если кто-то из отвергнутых сработал хорошо — значит перевес, возможно,
    существует, но наш способ его находить не работает.

Ни один результат отсюда не даёт права поменять финальную конфигурацию.
"""
import json
import os
import sys

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import finalists   # noqa: E402
import metrics     # noqa: E402
import portfolio   # noqa: E402
import rdata       # noqa: E402
import robust      # noqa: E402
import universe    # noqa: E402

OUT = os.path.join(DIR, "out")
NAMES = ["c_willr", "bos", "vol_spike", "supertrend", "c_rsi", "c_mfi",
         "c_cci", "c_bb", "macd", "atr_break", "bb_break", "vol_adj_mom",
         "mom_continuation", "vol_expansion", "rsi_mr", "ema_dist",
         "false_break", "keltner_mr", "donchian", "tsmom"]


def split_metrics(trades, t0, t1, k=1.0):
    sel = {}
    for sym, trs in trades.items():
        s = [t for t in trs if t0 <= t["t_in"] < t1]
        if s:
            sel[sym] = s
    if not sel:
        return None
    c = portfolio.combine(sel, risk_each=k)
    mo = portfolio.monthly_from_curve(c["times"], c["curve"])
    mv = np.array([m[1] for m in mo]) if mo else np.array([])
    return dict(ret=c["ret"], mo=c["mo"], maxdd=c["maxdd"],
                trades=c["trades"],
                mo_med=float(np.median(mv)) if len(mv) else 0.0)


def main():
    reg, _ = universe.load()
    tv0, tv1 = rdata.SPLITS["trainval"]
    te0, te1 = rdata.SPLITS["test"]
    rows = []
    print("ЧТО ПОКАЗАЛИ ВСЕ КАНДИДАТЫ НА ЭКЗАМЕНЕ (диагностика, не отбор)")
    print("Риск у всех одинаковый — базовый 1%% на рукав, чтобы сравнивать")
    print("между собой, а не с потолком просадки.\n")
    print("%-18s | %-28s | %-28s"
          % ("стратегия", "обучение+проверка", "ЭКЗАМЕН"))
    print("%-18s | %8s %8s %9s | %8s %8s %9s"
          % ("", "месяц", "просад", "сделок", "месяц", "просад", "сделок"))
    for name in NAMES:
        st = reg.get(name)
        if st is None:
            continue
        trades = {}
        for sym in rdata.SYMBOLS:
            trs, _ = portfolio.oos_trades(st, sym, "240",
                                          t_start=rdata.HIST_START_MS,
                                          t_end=rdata.HIST_END_MS + 1)
            if trs:
                trades[sym] = trs
        if not trades:
            continue
        a = split_metrics(trades, tv0, tv1)
        b = split_metrics(trades, te0, te1)
        if not a or not b:
            continue
        rows.append(dict(name=name, tv=a, test=b))
        print("%-18s | %+7.2f%% %7.1f%% %9d | %+7.2f%% %7.1f%% %9d"
              % (name, 100 * a["mo"], 100 * a["maxdd"], a["trades"],
                 100 * b["mo"], 100 * b["maxdd"], b["trades"]))

    tv = np.array([r["tv"]["mo"] for r in rows])
    te = np.array([r["test"]["mo"] for r in rows])
    print("\nСВОДКА ПО %d КАНДИДАТАМ" % len(rows))
    print("   на обучении+проверке в плюсе: %d из %d, медиана %+.2f%% в месяц"
          % (int((tv > 0).sum()), len(tv), 100 * np.median(tv)))
    print("   на ЭКЗАМЕНЕ в плюсе:          %d из %d, медиана %+.2f%% в месяц"
          % (int((te > 0).sum()), len(te), 100 * np.median(te)))
    if len(tv) > 3:
        c = float(np.corrcoef(tv, te)[0][1])
        # Доверительный интервал обязателен. Корреляция по двум десяткам
        # наблюдений — величина крайне неточная, и назвать её отрицательной,
        # не показав интервал, значит утверждать больше, чем измерено.
        # Преобразование Фишера: z = atanh(r), ошибка 1/sqrt(n-3).
        n = len(tv)
        z = np.arctanh(np.clip(c, -0.999, 0.999))
        se = 1.0 / np.sqrt(max(n - 3, 1))
        lo, hi = np.tanh(z - 1.96 * se), np.tanh(z + 1.96 * se)
        print("   связь «хорошо там» и «хорошо тут»: %+.2f" % c)
        print("   доверительный интервал 95%%: от %+.2f до %+.2f (n=%d)"
              % (lo, hi, n))
        if lo < 0 < hi:
            print("   Интервал накрывает ноль, поэтому честная формулировка —")
            print("   НЕ «связь отрицательная», а «связи НЕ ОБНАРУЖЕНО»: по")
            print("   этим данным результат на обучении не даёт никаких")
            print("   оснований предсказывать результат на новых данных.")
            print("   Утверждение скромнее, а по смыслу — сильнее.")

    print("\n\nРЕЖИМЫ РЫНКА: где ансамбль зарабатывал, а где терял")
    print("Режим определяется на баре ВХОДА и только по прошлому.")
    import engine
    import protocol   # noqa: F401
    for name in ("c_willr", "vol_spike", "supertrend", "bos"):
        st = reg[name]
        bars = rdata.load_bars("BTCUSDT", "240")
        sub, off = bars.slice(rdata.HIST_START_MS, rdata.HIST_END_MS + 1,
                              warmup=600)
        p = next(st.combos())
        res = engine.run(sub, st.build(sub, p), engine.Cfg(risk_frac=0.01),
                         start_i=off)
        rg = robust.regime_split(res, sub)
        print("\n   %s (BTC, одно сочетание — для иллюстрации режимов):" % name)
        for k, v in sorted(rg.items(), key=lambda x: -x[1]["ret"]):
            print("      %-20s %+8.1f%%  сделок %4d  выигрышных %3.0f%%"
                  % (k, 100 * v["ret"], v["trades"], 100 * v["wr"]))

    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    with open(os.path.join(OUT, "post_mortem.json"), "w",
              encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, default=float)
    print("\nсохранено -> out/post_mortem.json")


if __name__ == "__main__":
    main()
