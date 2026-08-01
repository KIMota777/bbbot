# -*- coding: utf-8 -*-
"""Отличим ли успех отбора от случайности?

Два прогона отбора с одинаковой методологией, но разными деталями (причинный
funding, исправленная мутация бинарных генов) дали ПРОТИВОПОЛОЖНЫЕ результаты:
победители и проигравшие поменялись местами. Это подозрение, что порог
holdout проходят не «хорошие» конфиги, а везучие.

Проверяем в лоб: берём СЛУЧАЙНЫЕ геномы (никакого обучения), прогоняем их на
том же holdout и считаем, какая доля проходит тот же порог
(n>=8, exp_r>0, PF>=1.2, sum_r>0). Это «нулевой уровень» — сколько успеха
даёт чистая случайность. Если отобранные конфиги проходят не чаще
случайных, отбор не создаёт ценности.

Запуск: python nullcheck.py   (env NULL_N — сколько случайных геномов на конфиг)
"""

import json
import os
import random

import evolution as ev
import signal_engine2 as se2
import evolution4 as e4

N_RANDOM = int(os.environ.get("NULL_N", "120"))
HOLD_FRAC = 0.28
GA_LEV = 15
SEED = 20260731


def passes(m):
    return (m["n"] >= 8 and m["exp_r"] > 0 and (m["pf"] or 0) >= 1.2
            and m["sum_r"] > 0)


def metrics(trades):
    n = len(trades)
    if not n:
        return dict(n=0, wr=0.0, exp_r=0.0, sum_r=0.0, pf=None)
    wins = [t for t in trades if t["pnl"] > 0]
    gp = sum(t["pnl"] for t in wins)
    gl = -sum(t["pnl"] for t in trades if t["pnl"] < 0)
    return dict(n=n, wr=round(len(wins) / n * 100, 1),
                exp_r=round(sum(t["r"] for t in trades) / n, 3),
                sum_r=round(sum(t["r"] for t in trades), 2),
                pf=round(gp / gl, 2) if gl > 0 else None)


def main():
    random.seed(SEED)
    c15 = ev.fetch("BTCUSDT", "15", 1150)
    ts15 = [c[0] for c in c15]
    rand_g, clamp, _mut, _cx = e4.ga_tools(se2.GENES2)

    with open("evolution12_winners.json", encoding="utf-8") as fh:
        winners = json.load(fh)

    cache = {}
    def data_of(iv):
        if iv not in cache:
            cc = ev.fetch("BTCUSDT", str(iv), 1150)
            cache[iv] = (cc, se2.prep_context(cc, interval_min=iv))
        return cache[iv]

    print("=" * 88)
    print("НУЛЕВОЙ УРОВЕНЬ: как часто СЛУЧАЙНЫЙ геном проходит тот же экзамен")
    print(f"случайных геномов на конфиг: {N_RANDOM} | порог: n>=8, exp_r>0, "
          f"PF>=1.2, sum_r>0 | плечо x{GA_LEV}")
    print("=" * 88)
    print(f"{'конфиг':20} {'случайных прошло':>18} {'доля':>7} "
          f"{'медиана exp_r':>14} {'отобранный':>12} {'вердикт'}")
    print("-" * 88)

    tot_pass = tot_n = 0
    for key in sorted(winners):
        setup = key.split("@")[0]
        iv = int(winners[key].get("interval_min") or key.split("@")[1])
        cc, ctx = data_of(iv)
        n_bars = len(cc)
        hold = int(n_bars * (1 - HOLD_FRAC))
        seg = (hold, n_bars)

        ok = 0
        exps = []
        tried = 0
        for _ in range(N_RANDOM):
            g = rand_g()
            try:
                r = se2.run_setup(setup, g, cc, ctx, c15, ts15, GA_LEV,
                                  signal_range=seg, collect_diag=False)
            except Exception:
                continue
            m = metrics(r["trades"])
            tried += 1
            if m["n"] >= 8:
                exps.append(m["exp_r"])
            if passes(m):
                ok += 1
        share = ok / max(1, tried) * 100
        med = sorted(exps)[len(exps) // 2] if exps else 0.0
        sel = winners[key]["holdout"]
        sel_pass = bool(winners[key].get("passed"))
        tot_pass += ok
        tot_n += tried
        verd = ("отобранный не лучше случайных"
                if sel["exp_r"] <= med else "отобранный выше медианы")
        print(f"{key:20} {ok:8}/{tried:<9} {share:6.1f}% {med:+14.3f} "
              f"{sel['exp_r']:+12.3f} {'ПРОШЁЛ ' if sel_pass else '       '}{verd}")

    print("-" * 88)
    print(f"ИТОГО случайных прошло: {tot_pass} из {tot_n} = "
          f"{tot_pass/max(1,tot_n)*100:.1f}%")
    exp_by_chance = tot_pass / max(1, tot_n) * len(winners)
    print(f"Ожидаемое число «прошедших» из {len(winners)} конфигов ЧИСТО ПО "
          f"СЛУЧАЙНОСТИ: {exp_by_chance:.1f}")
    n_sel = sum(1 for v in winners.values() if v.get("passed"))
    print(f"Фактически прошло после отбора: {n_sel}")
    if n_sel <= exp_by_chance + 0.5:
        print("ВЫВОД: результат отбора НЕ отличим от случайности.")
    else:
        print("ВЫВОД: отбор даёт больше проходов, чем случайность "
              "(но это ещё не доказательство прибыльности).")


if __name__ == "__main__":
    main()
