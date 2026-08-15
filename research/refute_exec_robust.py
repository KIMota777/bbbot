# -*- coding: utf-8 -*-
"""Проверка находки на прочность. Экзамен закрыт.

НАХОДКА, КОТОРУЮ ПРОВЕРЯЕМ. При одинаковой просадке 20% частичная фиксация
половины позиции на 1.5R с трейлом по остатку дала на внеобучающих кусках
+5.67% в месяц против +3.12% у маркета со стопом. Разница +2.56 п.п.

ПОЧЕМУ ЕЙ НЕЛЬЗЯ ВЕРИТЬ КАК ЕСТЬ. Она — лучшая из двадцати четырёх
опробованных схем исполнения. Ровно та же ошибка, за которую забраковано всё
исследование: лучший из многих хорош потому, что лучший из многих.

ТРИ ПРОВЕРКИ, КОТОРЫЕ МОГУТ ЕЁ УБИТЬ.

1. ПЕРЕНОС НА ЧУЖИЕ СТРАТЕГИИ. Схема исполнения — свойство не сигнала, а
   способа торговать. Если она настоящая, она обязана помогать НЕ ТОЛЬКО
   четырём отобранным правилам, а всем двадцати кандидатам. Считается знаковый
   критерий: скольким из двадцати стало лучше. Это независимая проверка:
   параметры схемы под остальные шестнадцать никто не подбирал.

2. ПЛАТО, А НЕ ПИК. 1.5R против 1.0R и 2.0R. Если помогает только 1.5R —
   это шум сетки, а не свойство.

3. ДВЕ ПОЛОВИНЫ ИСТОРИИ ПОРОЗНЬ. Если перевес весь пришёлся на одну половину,
   это событие, а не правило.
"""
import json
import os
import sys
import time

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import portfolio            # noqa: E402
import rdata                # noqa: E402
import refute_exec_engine as xe   # noqa: E402
import refute_exec_lib as L  # noqa: E402
import universe             # noqa: E402

OUT = os.path.join(DIR, "out")

# те же двадцать кандидатов, что разбирал post_mortem
NAMES = ["c_willr", "bos", "vol_spike", "supertrend", "c_rsi", "c_mfi",
         "c_cci", "c_bb", "macd", "atr_break", "bb_break", "vol_adj_mom",
         "mom_continuation", "vol_expansion", "rsi_mr", "ema_dist",
         "false_break", "keltner_mr", "donchian", "tsmom"]

SCHEMES = {
    "маркет+стоп (база)": xe.XCfg(),
    "половина 1.0R+трейл": xe.XCfg(partial_R=1.0, partial_frac=0.5,
                                   trail_after_partial=1.0),
    "половина 1.5R+трейл": xe.XCfg(partial_R=1.5, partial_frac=0.5,
                                   trail_after_partial=1.0),
    "половина 2.0R+трейл": xe.XCfg(partial_R=2.0, partial_frac=0.5,
                                   trail_after_partial=1.0),
    "треть 1.5R+трейл": xe.XCfg(partial_R=1.5, partial_frac=0.34,
                                trail_after_partial=1.0),
    "две трети 1.5R+трейл": xe.XCfg(partial_R=1.5, partial_frac=0.67,
                                    trail_after_partial=1.0),
}


def curve_stats(per_arm, t0=None, t1=None, risk_each=1.0):
    sel = {}
    for k, trs in per_arm.items():
        s = [t for t in trs
             if (t0 is None or t["t_in"] >= t0) and (t1 is None or
                                                     t["t_in"] < t1)]
        if s:
            sel[k] = s
    if not sel:
        return None
    c = portfolio.combine(sel, risk_each=risk_each)
    mo = portfolio.monthly_from_curve(c["times"], c["curve"])
    mv = np.array([m[1] for m in mo]) if mo else np.array([])
    # доходность, приведённая к просадке: сколько месячных процентов на
    # каждый процент просадки. Именно это сравнивается между схемами, потому
    # что доля риска у всех схем разная по построению.
    dd = max(c["maxdd"], 1e-6)
    return dict(mo=c["mo"], maxdd=c["maxdd"], trades=c["trades"],
                mo_med=float(np.median(mv)) if len(mv) else 0.0,
                per_dd=c["mo"] / dd)


def main():
    reg, _ = universe.load()
    cache = {}
    tv0, tv1 = rdata.SPLITS["trainval"]
    half = tv0 + (tv1 - tv0) // 2

    print("1) ПЕРЕНОС СХЕМЫ ИСПОЛНЕНИЯ НА ВСЕ ДВАДЦАТЬ КАНДИДАТОВ")
    print("Риск у всех 1%% на рукав; сравнивается ДОХОД НА ЕДИНИЦУ ПРОСАДКИ,")
    print("потому что схемы берут разный риск при одной доле капитала.\n")
    head = "%-18s" % "стратегия"
    for s in SCHEMES:
        head += " %19s" % s
    print(head)
    print("%-18s" % "" + "".join(" %8s %10s" % ("мес", "мес/прос")
                                 for _ in SCHEMES))

    rows = {}
    for name in NAMES:
        st = reg.get(name)
        if st is None:
            continue
        line = "%-18s" % name
        rows[name] = {}
        for sname, x in SCHEMES.items():
            t0 = time.time()
            r = L.ensemble(reg, x, members=[name])
            if r is None:
                line += " %19s" % "-"
                continue
            cache[(name, sname)] = r["per_arm"]
            s = curve_stats(r["per_arm"])
            rows[name][sname] = s
            line += " %+7.2f%% %9.2f" % (100 * s["mo"], s["per_dd"])
        print(line + "   [%.0fс]" % (time.time() - t0))
        sys.stdout.flush()

    base = "маркет+стоп (база)"
    print("\n2) ЗНАКОВЫЙ КРИТЕРИЙ: скольким из двадцати схема помогла")
    print("   (по доходу на единицу просадки; ноль различий не бывает)\n")
    for sname in SCHEMES:
        if sname == base:
            continue
        d = []
        for name, r in rows.items():
            if base in r and sname in r and r[base] and r[sname]:
                d.append(r[sname]["per_dd"] - r[base]["per_dd"])
        d = np.array(d)
        if not len(d):
            continue
        pos = int((d > 0).sum())
        n = len(d)
        # точный двусторонний знаковый критерий
        from math import comb
        p = sum(comb(n, k) for k in range(min(pos, n - pos) + 1)) / 2 ** n * 2
        p = min(1.0, p)
        print("   %-22s лучше у %2d из %2d, медиана разницы %+6.2f, p=%.3f"
              % (sname, pos, n, float(np.median(d)), p))

    print("\n3) ДВЕ ПОЛОВИНЫ ИСТОРИИ ПОРОЗНЬ (только четыре правила ансамбля)")
    print("%-22s %19s %19s" % ("схема", "первая половина", "вторая половина"))
    for sname, x in SCHEMES.items():
        r = L.ensemble(reg, x, members=L.MEMBERS)
        a = curve_stats(r["per_arm"], tv0, half)
        b = curve_stats(r["per_arm"], half, tv1)
        print("%-22s %+8.2f%% %9.2f %+8.2f%% %9.2f"
              % (sname, 100 * a["mo"], a["per_dd"],
                 100 * b["mo"], b["per_dd"]))

    with open(os.path.join(OUT, "refute_exec_robust.json"), "w",
              encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, default=float, indent=1)
    print("\nсохранено -> out/refute_exec_robust.json")


if __name__ == "__main__":
    main()
