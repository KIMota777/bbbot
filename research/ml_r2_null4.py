# -*- coding: utf-8 -*-
u"""Единственное место, где фильтр выглядит полезным: 4ч-срез. Разбор на излом.

ЧТО НАШЛОСЬ. На четырёхчасовом срезе — том самом, который и торгуется по
FINAL_SPEC, — мета-модель с весом |доходность| подняла внеобучающий результат
с +2.77% до +4.61% в месяц, прибавка +0.077% на вход. Это выше того, чего
не хватает системе (+0.038% на вход), и потому отмахнуться нельзя: если число
настоящее, вывод исследования придётся менять.

ЧЕТЫРЕ СПОСОБА ЕГО УБИТЬ, И ВСЕ ЧЕТЫРЕ ОБЯЗАТЕЛЬНЫ.

  1. ЖИВЁТ ЛИ ОНО НА ДРУГИХ СОЧЕТАНИЯХ ПАРАМЕТРОВ ПРАВИЛ. Всё считано на
     срединном сочетании сетки. Ровно на этом и погорело всё исследование:
     лучший из многих хорош тем, что он лучший из многих. Здесь то же самое
     считается на четырёх случайных сочетаниях (зерно задано заранее, ни одно
     не выбрано по результату).

  2. СЛУЧАЙНЫЙ ФИЛЬТР ТОГО ЖЕ РАЗМЕРА, 5000 раз — честный разброс величины на
     настоящих, тяжелохвостых доходностях.

  3. ПЕРЕМЕШАННЫЕ МЕТКИ — цена свободы подгонки при тех же признаках и той же
     процедуре.

  4. ПО ОКНАМ, ПО СТОРОНЕ И БЕЗ ХВОСТОВ. Прибавка, разложенная по окнам
     проверки; доля длинных среди оставленных против всех; та же прибавка на
     доходностях, подрезанных по 1-му и 99-му перцентилю.
"""
import json
import os
import sys
import time

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import ml_lib          # noqa: E402
import ml_r2_lib as L  # noqa: E402
import ml_r2_null as N  # noqa: E402
import ml_r2_pool as P  # noqa: E402

OUT = os.path.join(DIR, "out")
COMBOS = [u"срединное", u"случайное 1", u"случайное 2", u"случайное 3",
          u"случайное 4"]
N_RAND = 5000
N_SHUF = 150


def load(tag):
    combo = "center" if tag == u"срединное" else int(tag.split()[-1])
    d = L.pooled(P.RULES4, tfs=("240",), combo=combo)
    if d is None or len(d["y"]) < 400:
        return None, None
    return d, P.purged_folds(d["t_in"], d["t_out"], min_train=300)


def main():
    t_start = time.time()
    print(u"1. ЖИВЁТ ЛИ ПРИБАВКА НА ДРУГИХ СОЧЕТАНИЯХ ПАРАМЕТРОВ ПРАВИЛ")
    print(u"   Четыре правила ансамбля, только 4ч, пять монет. Прибавка —")
    print(u"   доходность оставленного входа минус доходность всех входов,")
    print(u"   внутри каждого правила отдельно.\n")
    print(u"   %-13s %6s | %-8s %-8s | %9s %9s"
          % (u"сочетание", u"входов", u"модель", u"веса", u"медиана",
             u"верх 30%"))
    rows = []
    store = {}
    for tag in COMBOS:
        d, folds = load(tag)
        if d is None:
            print(u"   %-13s   мало сделок" % tag)
            continue
        store[tag] = (d, folds)
        for mk in (u"логрег", u"бустинг"):
            for wk in (u"нет", u"|доход|"):
                r = P.run_variant(d, folds, mk, wk, False)
                if r is None:
                    continue
                a = r[u"медиана"]["dmean_in"]
                b = r[u"верх 30%"]["dmean_in"]
                rows.append(dict(combo=tag, model=mk, weight=wk, med=a,
                                 top=b, n=len(d["y"]),
                                 base_mo=r["base_mo"],
                                 mo_med=r[u"медиана"]["mo"],
                                 mo_top=r[u"верх 30%"]["mo"]))
                print(u"   %-13s %6d | %-8s %-8s | %+8.4f%% %+8.4f%%"
                      % (tag, len(d["y"]), mk, wk, 100 * a, 100 * b))

    for wk in (u"нет", u"|доход|"):
        v = np.array([r["med"] for r in rows if r["weight"] == wk]
                     + [r["top"] for r in rows if r["weight"] == wk])
        v = v[np.isfinite(v)]
        se = v.std(ddof=1) / np.sqrt(len(v))
        print(u"\n   веса «%s»: положительных %d из %d, среднее %+.4f%% "
              u"± %.4f%%" % (wk, int((v > 0).sum()), len(v),
                             100 * v.mean(), 100 * se))
    cen = [r for r in rows if r["combo"] == u"срединное"]
    oth = [r for r in rows if r["combo"] != u"срединное"]
    for what in ("med", "top"):
        a = np.array([r[what] for r in cen])
        b = np.array([r[what] for r in oth])
        print(u"   порог «%s»: срединное сочетание %+.4f%%, четыре "
              u"случайных %+.4f%% (в %.1f раза меньше)"
              % (u"медиана" if what == "med" else u"верх 30%",
                 100 * a.mean(), 100 * b.mean(),
                 a.mean() / b.mean() if b.mean() > 0 else float("inf")))

    print(u"\n\n2-4. РАЗБОР ЛУЧШЕГО СЛУЧАЯ: срединное сочетание, логрег, "
          u"вес |доход|")
    d, folds = store[u"срединное"]
    for mk in (u"логрег", u"бустинг"):
        km, kt, rr, cc, ff, sd = N.keep_masks(d, folds, mk, u"|доход|", False)
        print(u"\n   --- %s, входов вне обучения %d, окон %d"
              % (mk, len(rr), len(folds)))
        for ptag, keep in ((u"медиана", km), (u"верх 30%", kt)):
            real = N.dmean_in(rr, cc, keep)
            n_by = {c: int((keep & (cc == c)).sum()) for c in np.unique(cc)}
            nul = N.rand_null(rr, cc, n_by, n_rep=N_RAND)
            se = float(nul.std(ddof=1))
            pv = float((nul >= real).mean())
            lo, hi = np.percentile(rr, [1, 99])
            print(u"      порог «%s»: прибавка %+.4f%%, оставлено %d из %d"
                  % (ptag, 100 * real, int(keep.sum()), len(rr)))
            print(u"         2. случайный фильтр: %+.4f%% ± %.4f%%; "
                  u"настоящая = %.2f сигмы; p = %.3f"
                  % (100 * nul.mean(), 100 * se, real / se, pv))
            print(u"         4а. без хвостов (подрезка 1/99): %+.4f%%"
                  % (100 * N.dmean_in(np.clip(rr, lo, hi), cc, keep)))
            print(u"         4б. длинных среди всех %.0f%%, среди "
                  u"оставленных %.0f%%"
                  % (100 * (sd > 0).mean(), 100 * (sd[keep] > 0).mean()))
            per = [N.dmean_in(rr[ff == i], cc[ff == i], keep[ff == i])
                   for i in range(len(folds))]
            per = np.array([x for x in per if np.isfinite(x)])
            print(u"         4в. по окнам: %s   положительных %d из %d"
                  % (" ".join("%+.3f%%" % (100 * x) for x in per),
                     int((per > 0).sum()), len(per)))
            rows.append(dict(best=True, model=mk, thr=ptag, real=real,
                             null_sd=se, p=pv, per_fold=list(per)))

        vals = []
        n_shuf = N_SHUF if mk == u"логрег" else N_SHUF // 3
        for rep in range(n_shuf):
            _, kt2, rr2, cc2, _, _ = N.keep_masks(d, folds, mk, u"|доход|",
                                                  False, seed=rep,
                                                  shuffle=True)
            vals.append(N.dmean_in(rr2, cc2, kt2))
        vals = np.array([v for v in vals if np.isfinite(v)])
        real_top = N.dmean_in(rr, cc, kt)
        pv = float((vals >= real_top).mean())
        print(u"      3. перемешанные метки, %d повторов: %+.4f%% ± %.4f%%, "
              u"95%% = %+.4f%%" % (len(vals), 100 * vals.mean(),
                                   100 * vals.std(ddof=1),
                                   100 * np.percentile(vals, 95)))
        print(u"         настоящая %+.4f%%, доля шумовых не хуже: %.1f%% "
              u"(с поправкой на 40 попыток: %.2f)"
              % (100 * real_top, 100 * pv, min(1.0, 40 * pv)))

    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    with open(os.path.join(OUT, "ml_r2_null4.json"), "w",
              encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, default=float)
    print(u"\nсохранено -> out/ml_r2_null4.json   (%.0f с)"
          % (time.time() - t_start))


if __name__ == "__main__":
    main()
