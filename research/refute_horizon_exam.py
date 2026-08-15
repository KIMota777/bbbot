# -*- coding: utf-8 -*-
u"""ЭКЗАМЕН. Один прогон, один раз, по уже принятому решению.

Решение зафиксировано в refute_horizon_decide.py и сохранено в
out/refute_horizon_spec.json ДО того, как этот файл был запущен хоть раз.
Здесь на закрытых 169 днях считается ИТОГ этого решения — и ничего больше.
Ни один результат отсюда не имеет права изменить состав, параметры или
размер риска: иначе экзамен превращается в очередной тур отбора, и в работе
не остаётся ни одной непорченой оценки.

ЧТО ПРЕДЪЯВЛЯЕТСЯ (объявлено до прогона, обе позиции)

  ОСНОВНОЕ  — составное дневное правило: среднее девяти канонических
              трендовых правил, пять монет, обе стороны, размер обратен
              30-дневной волатильности, целевая волатильность подобрана на
              обучении+проверке под просадку 20%.
  ВТОРОЕ    — то же самое на недельных барах, целевая волатильность подобрана
              там же и так же.

  КОНТРОЛЬ  — «просто держать» пять монет равными долями. Без него число с
              экзамена нечитаемо: неизвестно, что было с самим рынком.

Обе позиции объявлены заранее именно потому, что выбрать одну из двух ПОСЛЕ
прогона было бы подгонкой под экзамен.
"""
import json
import os
import sys

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import rdata                        # noqa: E402
import refute_horizon_combo as C    # noqa: E402
import refute_horizon_lib as L      # noqa: E402
import refute_horizon_ts as TS      # noqa: E402
import refute_horizon_weekly as W   # noqa: E402

OUT = os.path.join(DIR, "out")


def calib(build, tf, target_dd=0.20):
    u"""Целевая волатильность под просадку 20% — считается на ОБУЧЕНИИ+ПРОВЕРКЕ."""
    lo, hi = 0.03, 1.20
    for _ in range(18):
        mid = 0.5 * (lo + hi)
        TS.TARGET_VOL = mid
        r = TS.run_on("trainval", build, tf=tf)
        if r["maxdd"] > target_dd:
            hi = mid
        else:
            lo = mid
    return lo


def line(name, r):
    print(u"%-26s %+8.1f%% %+8.2f%% %+8.2f%% %8.1f%% %7.2f %7.0f%%"
          % (name, 100 * r["ret"], 100 * r["mo"], 100 * r["mo_med"],
             100 * r["maxdd"], r["sharpe"], 100 * r["mo_pos"]))


def main():
    print(__doc__)
    res = {}

    # --- основное: дневное составное -----------------------------------
    TS.VOL_N = 30
    vol_d = calib(C.build_composite(), "D")
    TS.TARGET_VOL = vol_d
    tv_d = TS.run_on("trainval", C.build_composite(), tf="D")
    te_d = TS.run_on("test", C.build_composite(), tf="D")
    bh_tv = TS.run_on("trainval", TS.make_weights(TS.dir_always_long, 0), tf="D")
    bh_te = TS.run_on("test", TS.make_weights(TS.dir_always_long, 0), tf="D")

    # --- второе: недельное составное ------------------------------------
    TS.VOL_N = 8
    wb = TS.make_weights(W.w_composite, None)
    vol_w = calib(wb, "W")
    TS.TARGET_VOL = vol_w
    tv_w = TS.run_on("trainval", wb, tf="W")
    te_w = TS.run_on("test", wb, tf="W")

    print(u"целевая волатильность: дневное %.1f%%, недельное %.1f%% годовых\n"
          % (100 * vol_d, 100 * vol_w))
    print(u"%-26s %9s %9s %9s %9s %7s %8s"
          % (u"", u"итог", u"в месяц", u"медиана", u"просадка", u"Шарп",
             u"плюс мес"))
    print(u"-- обучение+проверка, 980 дней " + "-" * 44)
    line(u"дневное составное", tv_d)
    line(u"недельное составное", tv_w)
    line(u"контроль: просто держать", bh_tv)
    print(u"-- ЭКЗАМЕН, 169 дней " + "-" * 54)
    line(u"дневное составное", te_d)
    line(u"недельное составное", te_w)
    line(u"контроль: просто держать", bh_te)

    print(u"\nРЫНОК НА ЭКЗАМЕНЕ: «просто держать» дало %+.1f%% — период %s."
          % (100 * bh_te["ret"],
             u"падающий" if bh_te["ret"] < 0 else u"растущий"))

    print(u"\nСРАВНЕНИЕ С ИТОГОМ АВТОРА")
    print(u"   ансамбль автора (4ч):   обучение+проверка %+6.2f%% в месяц -> "
          u"экзамен %+6.2f%% в месяц" % (2.88, -3.36))
    print(u"   дневное составное:      обучение+проверка %+6.2f%% в месяц -> "
          u"экзамен %+6.2f%% в месяц" % (100 * tv_d["mo"], 100 * te_d["mo"]))
    print(u"   недельное составное:    обучение+проверка %+6.2f%% в месяц -> "
          u"экзамен %+6.2f%% в месяц" % (100 * tv_w["mo"], 100 * te_w["mo"]))
    print(u"   просадка автора на экзамене 36.2%% при обещанных 18.2%%;")
    print(u"   здесь дневное %.1f%% при обещанных %.1f%%, недельное %.1f%% "
          u"при %.1f%%." % (100 * te_d["maxdd"], 100 * tv_d["maxdd"],
                            100 * te_w["maxdd"], 100 * tv_w["maxdd"]))

    for k, v in (("vol_d", vol_d), ("vol_w", vol_w)):
        res[k] = v
    for k, r in (("tv_d", tv_d), ("te_d", te_d), ("tv_w", tv_w),
                 ("te_w", te_w), ("bh_tv", bh_tv), ("bh_te", bh_te)):
        res[k] = {x: float(r[x]) for x in
                  ("ret", "mo", "mo_med", "maxdd", "sharpe", "mo_pos", "days")}
    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    with open(os.path.join(OUT, "refute_horizon_exam.json"), "w",
              encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    print(u"\nсохранено -> out/refute_horizon_exam.json")
    print(u"\nБОЛЬШЕ НА ЭКЗАМЕНЕ НИЧЕГО НЕ СЧИТАЕТСЯ.")


if __name__ == "__main__":
    main()
