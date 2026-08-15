# -*- coding: utf-8 -*-
u"""Правильный ноль для фильтра: сдвиг по времени, а не жребий по сделкам.

ЧТО НЕ ТАК СО СЛУЧАЙНЫМ ФИЛЬТРОМ. Он выбирает сделки поодиночке и равномерно
по всей истории. Настоящий фильтр так не делает: любой признак обстановки —
волатильность, положение относительно средних, серия удач правила — держится
неделями. Фильтр по нему оставляет не разбросанные сделки, а СПЛОШНЫЕ КУСКИ
времени. Кусок из трёхсот сделок одной недели — это не триста наблюдений, а
одно, и разброс величины при таком отборе в разы больше, чем при жребии.
Отсюда и берутся «пять сигм» на пустом месте.

ЧЕСТНЫЙ НОЛЬ: ЦИКЛИЧЕСКИЙ СДВИГ МАСКИ. Маска «оставить/пропустить»
поворачивается по кругу вдоль времени на случайную величину — внутри каждой
пары (правило, тф) отдельно, чтобы сохранить и число оставленных, и длины
сплошных кусков. Меняется только одно: с каким участком рынка эти куски
совпадают. Всё, что фильтр умел, кроме собственно попадания в нужное время,
сохраняется.

Здесь этим нулём перемеряются:
  * победители одномерного перебора (ml_r2_simple) — там были 5-8 «сигм»;
  * фильтр мета-модели на 4ч-срезе — там была прибавка +0.077% на вход.
"""
import json
import os
import sys

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import ml_r2_lib as L     # noqa: E402
import ml_r2_null as N    # noqa: E402
import ml_r2_pool as P    # noqa: E402
import ml_r2_simple as S  # noqa: E402

OUT = os.path.join(DIR, "out")
N_SHIFT = 3000


def runs(mask):
    u"""Число сплошных кусков «оставить» — грубая мера числа независимых ставок."""
    if len(mask) == 0:
        return 0
    return int(1 + np.count_nonzero(mask[1:] != mask[:-1])) // 2 + int(mask[0])


def shift_null(ret, cfg, keep, n_rep=N_SHIFT, seed=0, joint=False):
    u"""Циклический сдвиг маски вдоль времени.

    joint=False — каждая пара (правило, тф) сдвигается на свою величину.
    joint=True  — ВСЕ пары сдвигаются на одну и ту же долю истории, то есть
    примерно на одно и то же время.

    Разница между ними принципиальная. Настоящий фильтр по обстановке
    (волатильность, направление рынка) выключает ВСЕ правила ОДНОВРЕМЕННО:
    это одна ставка на один участок истории, а не четыре независимые. Если
    сдвигать каждое правило само по себе, ноль складывается из четырёх
    независимых промахов, взаимно гасящих друг друга, его разброс выходит
    заниженным — и любая общая ставка получает лишние «сигмы» просто из-за
    того, что ноль устроен не так, как проверяемое.
    """
    rng = np.random.default_rng(seed)
    idx_by = {c: np.flatnonzero(cfg == c) for c in np.unique(cfg)}
    out = np.empty(n_rep)
    for s in range(n_rep):
        k2 = np.zeros(len(ret), dtype=bool)
        frac = float(rng.random())
        for c, idx in idx_by.items():
            m = keep[idx]
            off = (int(round(frac * len(idx))) if joint
                   else int(rng.integers(len(idx))))
            k2[idx] = np.roll(m, off)
        out[s] = N.dmean_in(ret, cfg, k2)
    return out


def report(title, ret, cfg, keep, n_tries):
    real = N.dmean_in(ret, cfg, keep)
    n_by = {c: int((keep & (cfg == c)).sum()) for c in np.unique(cfg)}
    a = N.rand_null(ret, cfg, n_by, n_rep=2000)
    b = shift_null(ret, cfg, keep)
    c = shift_null(ret, cfg, keep, joint=True, seed=7)
    nr = sum(runs(keep[cfg == c_]) for c_ in np.unique(cfg))
    pj = float((c >= real).mean())
    print(u"   %-28s прибавка %+.4f%%, оставлено %d, сплошных кусков %d"
          % (title, 100 * real, int(keep.sum()), nr))
    print(u"      жребий по сделкам:   ±%.4f%%  ->  %5.2f сигмы, p = %.4f"
          % (100 * a.std(ddof=1), real / a.std(ddof=1),
             float((a >= real).mean())))
    print(u"      сдвиг врозь:         ±%.4f%%  ->  %5.2f сигмы, p = %.4f"
          % (100 * b.std(ddof=1), real / b.std(ddof=1),
             float((b >= real).mean())))
    print(u"      сдвиг ЗАОДНО:        ±%.4f%%  ->  %5.2f сигмы, p = %.4f "
          u"(с поправкой на %d попыток: %.2f)"
          % (100 * c.std(ddof=1), real / c.std(ddof=1), pj, n_tries,
             min(1.0, n_tries * pj)))
    return dict(title=title, real=real, sd_rand=float(a.std(ddof=1)),
                sd_shift=float(b.std(ddof=1)),
                sd_joint=float(c.std(ddof=1)),
                p_rand=float((a >= real).mean()),
                p_shift=float((b >= real).mean()), p_joint=pj, runs=nr,
                n_keep=int(keep.sum()), n_tries=n_tries)


def main():
    res = []
    d = L.pooled(P.RULES4)
    folds = P.purged_folds(d["t_in"], d["t_out"])
    m4 = np.array([c.endswith("|240") for c in d["cfg"]])
    d4 = {k: (v[m4] if isinstance(v, np.ndarray) else v) for k, v in d.items()}
    d4["names"] = d["names"]
    f4 = P.purged_folds(d4["t_in"], d4["t_out"], min_train=300)
    d10 = L.pooled(P.RULES10)
    f10 = P.purged_folds(d10["t_in"], d10["t_out"])

    print(u"1. ПОБЕДИТЕЛИ ОДНОМЕРНОГО ПЕРЕБОРА, ПЕРЕМЕРЕННЫЕ ПРАВИЛЬНЫМ НУЛЁМ")
    print(u"   Поправка на число попыток — 39 признаков в каждом наборе.\n")
    for tag, dd, ff, feats in (
            (u"ансамбль 1ч+4ч", d, folds, ["streak", "perf_last",
                                           "s_ema200_d"]),
            (u"только 4ч", d4, f4, ["xs_rv_med", "atr_pct", "btc_rv"]),
            (u"семейства 1ч+4ч", d10, f10, ["xs_rv_med", "streak",
                                            "s_di_diff"])):
        print(u"   --- %s" % tag)
        for fname in feats:
            j = dd["names"].index(fname)
            r = S.one_feature(dd, ff, j)
            if r is None:
                continue
            keep, ret, cfg = r
            res.append(report(fname, ret, cfg, keep, 39))
        print()

    print(u"\n2. ФИЛЬТР МЕТА-МОДЕЛИ НА 4ч-СРЕЗЕ (лучший случай всей работы)")
    print(u"   Поправка на число попыток — 40 (5 сочетаний x 2 модели x "
          u"2 веса x 2 порога).\n")
    for mk in (u"логрег", u"бустинг"):
        km, kt, rr, cc, ff2, sd = N.keep_masks(d4, f4, mk, u"|доход|", False)
        for ptag, keep in ((u"медиана", km), (u"верх 30%", kt)):
            res.append(report(u"%s|%s" % (mk, ptag), rr, cc, keep, 40))

    print(u"\n3. ТОТ ЖЕ ФИЛЬТР НА ПОЛНОЙ КУЧЕ (1ч+4ч, ансамбль)")
    km, kt, rr, cc, _, _ = N.keep_masks(d, folds, u"бустинг", u"нет", False)
    for ptag, keep in ((u"медиана", km), (u"верх 30%", kt)):
        res.append(report(u"бустинг|нет|%s" % ptag, rr, cc, keep, 96))

    r1 = np.array([r["sd_shift"] / r["sd_rand"] for r in res])
    r2 = np.array([r["sd_joint"] / r["sd_rand"] for r in res])
    print(u"\n\nСКОЛЬКО СТОИЛА ОШИБКА В НУЛЕ")
    print(u"   разброс больше, чем при жребии по сделкам:")
    print(u"      при сдвиге врозь  — в %.1f раза (от %.1f до %.1f)"
          % (r1.mean(), r1.min(), r1.max()))
    print(u"      при сдвиге заодно — в %.1f раза (от %.1f до %.1f)"
          % (r2.mean(), r2.min(), r2.max()))
    print(u"   во столько же раз завышены «сигмы», посчитанные по жребию.")
    for key, tag in (("p_rand", u"жребий"), ("p_shift", u"сдвиг врозь"),
                     ("p_joint", u"сдвиг заодно")):
        a = len([r for r in res if r[key] < 0.05])
        b = len([r for r in res if r[key] * r["n_tries"] < 0.05])
        print(u"   ноль «%s»: выдержали %d из %d, с поправкой на число "
              u"попыток %d из %d" % (tag, a, len(res), b, len(res)))

    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    with open(os.path.join(OUT, "ml_r2_block.json"), "w",
              encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, default=float)
    print(u"\nсохранено -> out/ml_r2_block.json")


if __name__ == "__main__":
    main()
