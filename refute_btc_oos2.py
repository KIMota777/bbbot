# -*- coding: utf-8 -*-
"""Те же три линзы, но на данных, которых отбор не видел (2020-2023),
плюс скользящие годовые окна по всей склеенной истории 2020-2026.

Логика простая. На 2023-2026 конфиг пережил и издержки, и возмущения, и
задержку входа — но этот отрезок он же и «знал»: волны отбора видели его
целиком, о чём прежняя проверка честно предупреждала. Поэтому все три линзы
надо навести на кусок, которого он не знал. Там они значат ровно то, что
должны значить.
"""
import json
import os
import sys
import time

import numpy as np

import bots_honest as bh
import config
import evolution as ev
import evolution2 as e2
import evolution7 as e7
import evolution8 as e8
import refute_btc_delay as rd
import refute_btc_oos as ro

LEVELS = [0.10, 0.20, 0.30]
N = 90
BASE = dict(TAKER=e2.TAKER, MAKER=e2.MAKER, SLIP=e2.SLIP, FUND_8H=e2.FUND_8H)


def measure(candles, pre, g, lev):
    return ro.run(candles, pre, g, lev)


def costs(candles, pre, g, lev):
    print("  издержки:")
    for name, fk, sk in [("базовые", 1.0, 1.0), ("x1.5 / слип x2", 1.5, 2.0),
                         ("x2 / слип x3", 2.0, 3.0)]:
        old = {k: getattr(e2, k) for k in BASE}
        e2.TAKER, e2.MAKER = BASE["TAKER"] * fk, BASE["MAKER"] * fk
        e2.FUND_8H, e2.SLIP = BASE["FUND_8H"] * fk, BASE["SLIP"] * sk
        try:
            m = measure(candles, pre, g, lev)
        finally:
            for k, v in old.items():
                setattr(e2, k, v)
        print("    %-16s сделок %3d, ВР %5.1f%%, итог %+7.1f%%, "
              "без копеек %+7.1f%%, просадка %5.1f%%"
              % (name, m["trades"], m["wr"], m["comp"], m["comp_nt"], m["dd"]))
        sys.stdout.flush()


def perturb(candles, pre, g, lev):
    print("  возмущения генома (копеечные выходы обнулены):")
    for frac in LEVELS:
        rng = np.random.default_rng(7)
        nts, comps, trs = [], [], []
        for _ in range(N):
            gp = bh.perturb(g, e8.GENES8, rng, frac)
            m = measure(candles, pre, gp, lev)
            nts.append(m["comp_nt"])
            comps.append(m["comp"])
            trs.append(m["trades"])
        a = np.array(nts)
        print("    +-%2d%%: медиана %+7.1f%%, p10 %+7.1f%%, p90 %+7.1f%%, "
              "доля+ %3.0f%%, сделок (медиана) %d"
              % (frac * 100, np.median(a), np.percentile(a, 10),
                 np.percentile(a, 90), (a > 0).mean() * 100, np.median(trs)))
        sys.stdout.flush()


def delays(candles, pre, g, lev, d_for_patch):
    print("  задержка входа:")
    for n in [0, 1, 2, 3]:
        p2 = rd.shift_pre(pre, candles, n)
        patch, real = rd.patched_extremes(candles, n)
        ev.rolling_extremes = patch
        try:
            m = measure(candles, p2, g, lev)
        finally:
            ev.rolling_extremes = real
        print("    %d бар(ов): сделок %3d, ВР %5.1f%%, итог %+7.1f%%, "
              "просадка %5.1f%%"
              % (n, m["trades"], m["wr"], m["comp"], m["dd"]))
        sys.stdout.flush()


def merged_history():
    """2020-2026 одним куском: глубокая выкачка + рабочий файл 1150 дней."""
    deep = ro.fetch_deep()
    with open("history_BTCUSDT_15m_1150d.json") as fh:
        recent = json.load(fh)
    seen = {c[0] for c in deep}
    out = deep + [c for c in recent if c[0] not in seen]
    out.sort(key=lambda x: x[0])
    return out


def rolling_years(candles, g, lev):
    """Скользящее годовое окно с шагом в месяц. Главный вопрос: какая доля
    лет в истории монеты для этого конфига прибыльна, а не какая доля того
    одного года, на который его настроили."""
    pre = e2.prep(candles)
    step = 96 * 30
    win = 96 * 365
    rets, dds = [], []
    i = 0
    rows = []
    while i + win <= len(candles):
        seg = candles[i:i + win]
        m = measure(seg, e2.prep(seg), g, lev)
        rets.append(m["comp"])
        dds.append(m["dd"])
        rows.append((time.strftime("%Y-%m", time.gmtime(seg[0][0] / 1000)),
                     m["trades"], m["comp"], m["dd"]))
        i += step
    a = np.array(rets)
    print("  окон по 365 дней: %d, шаг месяц" % len(a))
    print("  медиана года %+.1f%%, худший %+.1f%%, лучший %+.1f%%, "
          "доля прибыльных лет %.0f%%"
          % (np.median(a), a.min(), a.max(), (a > 0).mean() * 100))
    print("  просадка внутри года: медиана %.1f%%, худшая %.1f%%"
          % (np.median(dds), max(dds)))
    print("  по годам старта окна (старт: сделок / итог / просадка):")
    for k in range(0, len(rows), 3):
        s, t, r, dd = rows[k]
        print("    %s  %3d  %+7.1f%%  %5.1f%%" % (s, t, r, dd))
    return a


def main():
    e2.BARS_PER_DAY = 96
    g, lev = ro.genome("normal")
    deep = ro.fetch_deep()
    pre = e2.prep(deep)
    print("=" * 96)
    print("ТРИ ЛИНЗЫ НА ЧУЖИХ ДАННЫХ 2020-2023 (BTC normal x5, 39.9 мес, "
          "93 сделки)")
    costs(deep, pre, g, lev)
    print()
    delays(deep, pre, g, lev, None)
    print()
    perturb(deep, pre, g, lev)

    print()
    print("=" * 96)
    print("СКОЛЬЗЯЩИЙ ГОД ПО ВСЕЙ ИСТОРИИ 2020-2026 (склейка глубокой "
          "выкачки и рабочего файла)")
    full = merged_history()
    print("  склеено %d баров: %s .. %s"
          % (len(full), time.strftime("%Y-%m-%d", time.gmtime(full[0][0] / 1000)),
             time.strftime("%Y-%m-%d", time.gmtime(full[-1][0] / 1000))))
    rolling_years(full, g, lev)


if __name__ == "__main__":
    main()
