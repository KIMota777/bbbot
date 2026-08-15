# -*- coding: utf-8 -*-
u"""Недельный горизонт — последняя часть задания про горизонт.

Те же канонические правила, но бары недельные: решение принимается раз в
неделю, позиция держится неделю. Если перевес живёт ещё дальше по горизонту,
чем дневной, он должен быть виден здесь и притом ярче: издержки падают ещё
вчетверо, а шум недельной цены меньше дневного.

Отдельно печатается ГЛАВНОЕ ОГРАНИЧЕНИЕ: недельных баров во всей истории 163,
на обучении около 105. Оценка Шарпа по сотне наблюдений имеет ошибку порядка
0.7 — на этом горизонте отличить перевес от везения нельзя в принципе, и
любое красивое число здесь надо читать именно так.
"""
import os
import sys

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import rdata                       # noqa: E402
import refute_horizon_combo as C   # noqa: E402
import refute_horizon_lib as L     # noqa: E402
import refute_horizon_ts as TS     # noqa: E402

# те же сроки, что и на дневных барах, пересчитанные в недели
WRULES = [
    (u"импульс 4н",   TS.dir_tsmom, 4),
    (u"импульс 8н",   TS.dir_tsmom, 8),
    (u"импульс 13н",  TS.dir_tsmom, 13),
    (u"импульс 17н",  TS.dir_tsmom, 17),
    (u"импульс 26н",  TS.dir_tsmom, 26),
    (u"Дончиан 3н",   TS.dir_donchian, 3),
    (u"Дончиан 8н",   TS.dir_donchian, 8),
    (u"наклон MA 7н", TS.dir_maslope, 7),
    (u"наклон MA 14н", TS.dir_maslope, 14),
]


def w_composite(q, _=None):
    return np.mean(np.vstack([fn(q, a) for _n, fn, a in WRULES]), axis=0)


def main():
    t, px = L.panel(rdata.SYMBOLS, "W")
    print(u"Недельных баров всего %d." % len(t))
    for split in ("train", "val", "trainval"):
        n = int(L.in_split(t, split).sum())
        print(u"   на «%s» %d недель -> ошибка оценки Шарпа около %.2f"
              % (split, n, np.sqrt(52.0 / max(n, 1))))
    print()

    # волатильность по 30 неделям была бы длиннее всей выборки — берём 8
    old_n, old_t = TS.VOL_N, TS.TARGET_VOL
    TS.VOL_N = 8
    TS.TARGET_VOL = 0.40

    for name, fn, arg in WRULES:
        L.refute_causal_w(TS.make_weights(fn, arg), t, px)
    L.refute_causal_w(TS.make_weights(w_composite, None), t, px)
    print(u"причинность недельных правил проверена порчей будущего\n")

    for split, title in (("train", u"ОБУЧЕНИЕ"), ("val", u"ПРОВЕРКА"),
                         ("trainval", u"ОБУЧЕНИЕ+ПРОВЕРКА")):
        print(u"=== %s (недельные бары) ===" % title)
        print(u"%-18s %9s %9s %9s %7s"
              % (u"", u"итог", u"в месяц", u"просадка", u"Шарп"))
        rows = list(WRULES) + [(u"ВСЕГДА ЛОНГ", TS.dir_always_long, 0),
                               (u"СОСТАВНОЕ (все 9)", w_composite, None)]
        for name, fn, arg in rows:
            r = TS.run_on(split, TS.make_weights(fn, arg), tf="W")
            if r is None:
                continue
            print(u"%-18s %+8.1f%% %+8.2f%% %8.1f%% %7.2f"
                  % (name, 100 * r["ret"], 100 * r["mo"], 100 * r["maxdd"],
                     r["sharpe"]))
        print()

    # сравнение горизонтов в одной мере
    print(u"СОСТАВНОЕ ПРАВИЛО НА ТРЁХ ГОРИЗОНТАХ (обучение+проверка, Шарп)")
    rw = TS.run_on("trainval", TS.make_weights(w_composite, None), tf="W")
    TS.VOL_N, TS.TARGET_VOL = old_n, 0.40
    rd = TS.run_on("trainval", C.build_composite(), tf="D")
    print(u"   недельные бары: %.2f" % rw["sharpe"])
    print(u"   дневные бары:   %.2f" % rd["sharpe"])
    print(u"   (у автора на 4ч лучшее семейство давало +0.22%% в месяц вне")
    print(u"    обучения — в мере Шарпа это около 0.1–0.2)")
    TS.VOL_N, TS.TARGET_VOL = old_n, old_t


if __name__ == "__main__":
    main()
