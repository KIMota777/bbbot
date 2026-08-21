# -*- coding: utf-8 -*-
"""Сведение улик: одно и то же меряется на отрезке, который подбор ВИДЕЛ,
и на отрезке, которого он не видел. Разница между колонками и есть ответ.
"""
import json
import sys
import time

import numpy as np

import bots_honest as bh
import evolution2 as e2
import evolution8 as e8
import refute_btc_oos as ro
import refute_btc_oos2 as r2

CUT = "2023-06-21"          # отсюда начинаются изученные 1150 дней


def ms(day):
    return int(time.mktime(time.strptime(day, "%Y-%m-%d")) * 1000)


def main():
    e2.BARS_PER_DAY = 96
    g, lev = ro.genome("normal")
    full = r2.merged_history()
    cut = ms(CUT)

    # --- скользящий год: чужие окна против подогнанных
    step, win = 96 * 30, 96 * 365
    seen, unseen = [], []
    i = 0
    while i + win <= len(full):
        seg = full[i:i + win]
        m = ro.run(seg, e2.prep(seg), g, lev)
        # окно считаю «чужим», если оно целиком раньше начала изученных данных
        (seen if seg[-1][0] > cut else unseen).append((m["comp"], m["dd"]))
        i += step
    print("СКОЛЬЗЯЩИЙ ГОД, разделённый по тому, видел ли отбор эти данные")
    for name, rows in (("чужие данные (окно кончается до 2023-06)", unseen),
                       ("данные, которые подбор видел", seen)):
        a = np.array([x[0] for x in rows])
        dd = np.array([x[1] for x in rows])
        print("  %-42s окон %2d | медиана года %+6.1f%% | доля прибыльных "
              "%3.0f%% | худший %+6.1f%% | худшая просадка %.1f%%"
              % (name, len(a), np.median(a), (a > 0).mean() * 100, a.min(),
                 dd.max()))
    sys.stdout.flush()

    # --- лестница плечей на чужих данных по правилу самого проекта
    print()
    print("ЛЕСТНИЦА ПЛЕЧЕЙ на чужих данных 2020-2023 (правило проекта: "
          "берём наибольшее плечо с просадкой <= 20%%)")
    deep = ro.fetch_deep()
    pre = e2.prep(deep)
    good = []
    for l in (5, 8, 10, 12, 15):
        m = ro.run(deep, pre, g, l)
        ok = m["dd"] <= 20.0 and m["comp"] > 0 and not m["ruined"]
        if ok:
            good.append(l)
        print("  x%-3d итог %+7.1f%%, просадка %5.1f%%%s   %s"
              % (l, m["comp"], m["dd"], "  СЛИВ" if m["ruined"] else "",
                 "проходит" if ok else "НЕ проходит"))
    print("  -> на чужих данных правило проекта разрешает: %s"
          % ("x%d" % good[-1] if good else "НИ ОДНОГО ПЛЕЧА, включая x5"))

    # --- устойчивость на всей истории 2020-2026 сразу
    print()
    print("ВОЗМУЩЕНИЯ ГЕНОМА НА ВСЕЙ ИСТОРИИ 2020-2026 (копеечные обнулены)")
    pre_f = e2.prep(full)
    base = ro.run(full, pre_f, g, lev)
    print("  факт на всей истории: сделок %d, ВР %.1f%%, итог %+.1f%%, "
          "просадка %.1f%%"
          % (base["trades"], base["wr"], base["comp"], base["dd"]))
    for frac in (0.10, 0.20, 0.30):
        rng = np.random.default_rng(7)
        nts = []
        for _ in range(90):
            gp = bh.perturb(g, e8.GENES8, rng, frac)
            nts.append(ro.run(full, pre_f, gp, lev)["comp_nt"])
        a = np.array(nts)
        print("  +-%2d%%: медиана %+7.1f%%, p10 %+7.1f%%, доля+ %3.0f%%"
              % (frac * 100, np.median(a), np.percentile(a, 10),
                 (a > 0).mean() * 100))
        sys.stdout.flush()


if __name__ == "__main__":
    main()
