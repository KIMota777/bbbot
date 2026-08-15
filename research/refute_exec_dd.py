# -*- coding: utf-8 -*-
"""Управление просадкой на уровне ПОРТФЕЛЯ. Подбор только на обучении.

ПОЧЕМУ НЕ ЧЕРЕЗ Cfg.dd_scale. В движке dd_scale смотрит на капитал ОДНОГО
рукава: одна стратегия на одной монете. Но торгуется двадцать рукавов с общего
счёта, и просадка, из-за которой в жизни режут риск, — это просадка счёта, а не
рукава. Резать по рукаву значит резать не тогда и не там. Поэтому правило
применяется здесь, при сведении рукавов в общий капитал: доля риска сделки
зависит от просадки СЧЁТА на момент её открытия.

ЧТО ЭТО В ПРИНЦИПЕ МОЖЕТ, А ЧЕГО НЕ МОЖЕТ. Снижение риска в просадке не создаёт
перевеса: если средняя сделка убыточна, оно лишь замедляет потерю, а на
развороте не даёт отыграться полным размером. Оно способно ТОЛЬКО обрезать
хвост — и вопрос ровно в том, стоит ли обрезанный хвост потерянного отскока.

ПОРЯДОК. Пороги подбираются на TRAIN (данные до 2025-06-27). VAL не трогается
до окончания подбора, экзамен — тем более.
"""
import json
import os
import pickle
import sys

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import metrics              # noqa: E402
import portfolio            # noqa: E402
import rdata                # noqa: E402
import refute_exec_engine as xe   # noqa: E402
import refute_exec_lib as L  # noqa: E402
import universe             # noqa: E402

OUT = os.path.join(DIR, "out")
CACHE = os.path.join(OUT, "refute_exec_dd_arms.pkl")


def combine_dd(trades_by_arm, risk_each=1.0, start=10000.0, ladder=None,
               dd_stop=0.0, t0=None, t1=None):
    """То же, что portfolio.combine, но доля риска зависит от просадки СЧЁТА.

    ladder: [(порог просадки, множитель риска), ...] по возрастанию порога.
    dd_stop: просадка, на которой торговля встаёт совсем (0 — не встаёт).
    Множитель определяется НА МОМЕНТ ОТКРЫТИЯ сделки и дальше не меняется —
    иначе получилось бы, что размер уже открытой позиции задним числом другой.
    """
    events = []
    for arm, trs in trades_by_arm.items():
        for t in trs:
            if t0 is not None and t["t_in"] < t0:
                continue
            if t1 is not None and t["t_in"] >= t1:
                continue
            events.append((t["t_in"], 0, arm, t))
            events.append((t["t_out"], 1, arm, t))
    events.sort(key=lambda e: (e[0], e[1]))

    eq = start
    peak = eq
    open_pos = {}
    curve_t, curve_v = [], []
    halted = False
    n_skip = 0
    for ts, kind, arm, t in events:
        if kind == 0:
            dd = (peak - eq) / peak if peak > 0 else 0.0
            if dd_stop and dd >= dd_stop:
                halted = True
            if halted:
                n_skip += 1
                continue
            k = risk_each
            if ladder:
                for thr, mult in ladder:
                    if dd >= thr:
                        k = risk_each * mult
            open_pos[id(t)] = (eq, k)
            curve_t.append(ts)
            curve_v.append(eq + eq * k * (t["low"] - 1.0))
        else:
            got = open_pos.pop(id(t), None)
            if got is None:
                continue
            base, k = got
            eq += base * k * t["ret"]
            eq = max(eq, 0.0)
            curve_t.append(ts)
            curve_v.append(eq)
            peak = max(peak, eq)
            if eq <= 0:
                break
    v = np.array([start] + curve_v) if curve_v else np.array([start])
    days = ((max(curve_t) - min(curve_t)) / rdata.DAY_MS) if curve_t else 1.0
    ret = v[-1] / start - 1.0
    dd = metrics.max_drawdown(v)[0]
    mo = portfolio.monthly_from_curve(curve_t, v)
    mv = np.array([m[1] for m in mo]) if mo else np.array([])
    return dict(ret=float(ret), maxdd=float(dd), days=days,
                mo=float((1 + ret) ** (30.0 / max(days, 1)) - 1.0)
                if ret > -1 else -1.0,
                mo_med=float(np.median(mv)) if len(mv) else 0.0,
                skipped=n_skip, trades=len(curve_t) // 2, curve=v,
                times=curve_t)


LADDERS = {
    "без управления": (None, 0.0),
    "с 10% риск x0.5": ([(0.10, 0.5)], 0.0),
    "с 10% x0.7, с 15% x0.4": ([(0.10, 0.7), (0.15, 0.4)], 0.0),
    "с 8% x0.6, с 14% x0.35, с 20% x0.2": ([(0.08, 0.6), (0.14, 0.35),
                                            (0.20, 0.2)], 0.0),
    "стоп торговли на 15%": (None, 0.15),
    "стоп торговли на 20%": (None, 0.20),
    "с 10% x0.5 + стоп на 20%": ([(0.10, 0.5)], 0.20),
}


def calib(per_arm, ladder, dd_stop, target=0.20, t0=None, t1=None):
    lo, hi = 0.02, 8.0
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        c = combine_dd(per_arm, risk_each=mid, ladder=ladder,
                       dd_stop=dd_stop, t0=t0, t1=t1)
        if c["maxdd"] > target:
            hi = mid
        else:
            lo = mid
        if hi - lo < 1e-4:
            break
    return 0.5 * (lo + hi)


def main():
    reg, _ = universe.load()
    if os.path.exists(CACHE):
        with open(CACHE, "rb") as fh:
            arms = pickle.load(fh)
    else:
        arms = {}
        for sname, x in (("база", xe.XCfg()),
                         ("половина 1.5R+трейл",
                          xe.XCfg(partial_R=1.5, partial_frac=0.5,
                                  trail_after_partial=1.0))):
            r = L.ensemble(reg, x)
            arms[sname] = r["per_arm"]
            print("посчитан %s" % sname)
        if not os.path.isdir(OUT):
            os.makedirs(OUT)
        with open(CACHE, "wb") as fh:
            pickle.dump(arms, fh)

    tr0, tr1 = rdata.SPLITS["train"]
    va0, va1 = rdata.SPLITS["val"]
    print("\nУПРАВЛЕНИЕ ПРОСАДКОЙ. Пороги подбираются на TRAIN, потом один раз")
    print("применяются к VAL. Экзамен закрыт.\n")
    out = {}
    for sname, per_arm in arms.items():
        print("=== схема исполнения: %s" % sname)
        print("%-36s %8s %8s %8s %8s %8s"
              % ("правило", "риск", "TRAIN м", "TRAIN пр", "VAL м", "VAL пр"))
        out[sname] = {}
        for lname, (ladder, ds) in LADDERS.items():
            k = calib(per_arm, ladder, ds, 0.20, tr0, tr1)
            a = combine_dd(per_arm, k, ladder=ladder, dd_stop=ds,
                           t0=tr0, t1=tr1)
            b = combine_dd(per_arm, k, ladder=ladder, dd_stop=ds,
                           t0=va0, t1=va1)
            out[sname][lname] = dict(risk=0.01 * k, train_mo=a["mo"],
                                     train_dd=a["maxdd"], val_mo=b["mo"],
                                     val_dd=b["maxdd"], skipped=b["skipped"])
            print("%-36s %7.2f%% %+7.2f%% %7.1f%% %+7.2f%% %7.1f%%"
                  % (lname, 100 * out[sname][lname]["risk"], 100 * a["mo"],
                     100 * a["maxdd"], 100 * b["mo"], 100 * b["maxdd"]))
        print("")

    with open(os.path.join(OUT, "refute_exec_dd.json"), "w",
              encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, default=float, indent=1)
    print("сохранено -> out/refute_exec_dd.json")


if __name__ == "__main__":
    main()
