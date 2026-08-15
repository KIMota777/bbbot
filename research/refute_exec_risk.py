# -*- coding: utf-8 -*-
"""Сравнение способов исполнения ПРИ ОДИНАКОВОЙ ПРОСАДКЕ. Экзамен закрыт.

ПОЧЕМУ ПРЕДЫДУЩАЯ ТАБЛИЦА НЕ ОТВЕЧАЕТ НА ВОПРОС. Там у каждого способа своя
просадка: от 20% до 66%. Сравнивать их доходности между собой нельзя — половину
разницы даёт не качество исполнения, а то, сколько риска способ на себя берёт.
Способ с широким стопом «проигрывает» просто потому, что торгует меньшим
объёмом.

Поэтому здесь каждому способу подбирается своя доля риска так, чтобы
ИСТОРИЧЕСКАЯ ПРОСАДКА у всех равнялась ровно 20% — потолку из задания. И уже
после этого сравниваются месячные доходности. Это тот же приём, которым автор
получил свои 0.43%.

Масштабирование доли риска на сделках законно, пока размер позиции
пропорционален капиталу и плечо не упирается в потолок; случаи упора
считаются в portfolio.combine и печатаются.

ЧЕГО ЭТО НЕ ДОКАЗЫВАЕТ. Подгонка доли риска под ровно 20% на той же истории —
это тоже подбор по прошлому. Он честен как способ ПРИВЕСТИ К ОДНОМУ ЗНАМЕНАТЕЛЮ,
но полученные проценты нельзя считать ожиданием на будущее.
"""
import json
import os
import pickle
import sys
import time

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import portfolio            # noqa: E402
import rdata                # noqa: E402
import refute_exec_lib as L  # noqa: E402
import universe             # noqa: E402

OUT = os.path.join(DIR, "out")
CACHE = os.path.join(OUT, "refute_exec_arms.pkl")
TARGET_DD = 0.20


def at_risk(per_arm, k):
    c = portfolio.combine(per_arm, risk_each=k)
    mo = portfolio.monthly_from_curve(c["times"], c["curve"])
    mv = np.array([m[1] for m in mo]) if mo else np.array([])
    return c, mv


def calibrate(per_arm, target=TARGET_DD, lo=0.02, hi=6.0):
    """Двоичный поиск доли риска под заданную историческую просадку."""
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        c, _ = at_risk(per_arm, mid)
        if c["maxdd"] > target:
            hi = mid
        else:
            lo = mid
        if hi - lo < 1e-4:
            break
    return 0.5 * (lo + hi)


def main():
    reg, _ = universe.load()
    V = L.variants()
    arms = {}
    if os.path.exists(CACHE):
        with open(CACHE, "rb") as fh:
            arms = pickle.load(fh)
    for name, x in V.items():
        if name in arms:
            continue
        t0 = time.time()
        r = L.ensemble(reg, x)
        arms[name] = None if r is None else dict(per_arm=r["per_arm"],
                                                 stat=r["stat"])
        print("посчитан  %-34s [%.0fс]" % (name, time.time() - t0))
        sys.stdout.flush()
        if not os.path.isdir(OUT):
            os.makedirs(OUT)
        with open(CACHE, "wb") as fh:
            pickle.dump(arms, fh)

    print("\nВСЕ СПОСОБЫ ИСПОЛНЕНИЯ, ПРИВЕДЁННЫЕ К ПРОСАДКЕ 20%")
    print("Внеобучающие куски обучения+проверки, 980 дней, экзамен закрыт.\n")
    print("%-34s %7s %8s %8s %7s %7s %6s"
          % ("способ", "риск", "месяц", "медиана", "плюс", "сделок", "залив"))
    rows = {}
    for name in V:
        a = arms.get(name)
        if a is None:
            continue
        k = calibrate(a["per_arm"])
        c, mv = at_risk(a["per_arm"], k)
        fill = 100.0 * a["stat"]["filled"] / max(a["stat"]["placed"], 1)
        rows[name] = dict(risk=0.01 * k, mo=c["mo"], maxdd=c["maxdd"],
                          mo_med=float(np.median(mv)) if len(mv) else 0.0,
                          mo_pos=float((mv > 0).mean()) if len(mv) else 0.0,
                          trades=c["trades"], fill=fill, capped=c["capped"],
                          maker=100.0 * a["stat"]["maker"]
                          / max(a["stat"]["filled"], 1))
        print("%-34s %6.2f%% %+7.2f%% %+7.2f%% %6.0f%% %7d %5.0f%%"
              % (name, 100 * rows[name]["risk"], 100 * c["mo"],
                 100 * rows[name]["mo_med"], 100 * rows[name]["mo_pos"],
                 c["trades"], fill))

    base = rows.get("база: маркет по открытию")
    if base:
        print("\nПРЕВОСХОДСТВО НАД БАЗОЙ (та же просадка 20%%):")
        for name, r in sorted(rows.items(), key=lambda z: -z[1]["mo"]):
            print("   %-34s %+6.2f п.п. в месяц"
                  % (name, 100 * (r["mo"] - base["mo"])))

    with open(os.path.join(OUT, "refute_exec_risk.json"), "w",
              encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, default=float, indent=1)
    print("\nсохранено -> out/refute_exec_risk.json")


if __name__ == "__main__":
    main()
