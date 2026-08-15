# -*- coding: utf-8 -*-
u"""Последняя проверка «серии»: живёт ли она вне срединного сочетания параметров.

ПОЧЕМУ ЭТО РЕШАЕТ ВСЁ. Каждое правило имеет сетку параметров. Всё, что
считалось до сих пор, считалось на ОДНОМ сочетании — срединном по сетке. Оно
выбрано без взгляда в результат, и потому не подгонка; но оно одно. Если
свойство настоящее, оно обязано быть и при других настройках того же правила:
«серия проигрышей» не может зависеть от того, ставим мы стоп в три ATR или в
два. Если же оно живёт только на срединном сочетании — это не свойство рынка,
а совпадение, и цена ему та же, что и всему, что уже провалилось на экзамене.

На 4ч-срезе (ml_r2_streak, раздел 4) проверка уже дала тревожный ответ:
2 из 5 сочетаний в плюсе, среднее +0.009% ± 0.021%. Здесь то же самое
считается на полной куче, где значимость была самой высокой, и на втором,
непересекающемся наборе правил.
"""
import json
import os
import sys

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import ml_lib           # noqa: E402
import ml_r2_block as B  # noqa: E402
import ml_r2_lib as L   # noqa: E402
import ml_r2_null as N  # noqa: E402
import ml_r2_pool as P  # noqa: E402
import ml_r2_simple as S  # noqa: E402

OUT = os.path.join(DIR, "out")
COMBOS = [u"срединное", u"случайное 1", u"случайное 2", u"случайное 3",
          u"случайное 4"]
FEATS = ["streak", "perf_last", "perf10", "xs_rv_med"]


def main():
    out = []
    for stag, rules in ((u"ансамбль", P.RULES4),
                        (u"семейства", P.RULES10)):
        print(u"\n%s: 1ч+4ч, пять монет" % stag.upper())
        print(u"   %-13s %7s | %s"
              % (u"сочетание", u"входов",
                 " ".join("%10s" % f for f in FEATS)))
        tab = {f: [] for f in FEATS}
        for tag in COMBOS:
            combo = "center" if tag == u"срединное" else int(tag.split()[-1])
            d = L.pooled(rules, combo=combo)
            if d is None or len(d["y"]) < 1000:
                continue
            folds = P.purged_folds(d["t_in"], d["t_out"])
            if not folds:
                continue
            cells = []
            for f in FEATS:
                r = S.one_feature(d, folds, d["names"].index(f))
                if r is None:
                    cells.append(float("nan"))
                    continue
                keep, ret, cfg = r
                g = N.dmean_in(ret, cfg, keep)
                cells.append(g)
                if np.isfinite(g):
                    tab[f].append(g)
                out.append(dict(set=stag, combo=tag, feat=f, gain=g,
                                n=len(d["y"])))
            print(u"   %-13s %7d | %s"
                  % (tag, len(d["y"]),
                     " ".join("%+9.4f%%" % (100 * c) for c in cells)))
        print(u"   %-13s %7s | %s"
              % (u"в плюсе", "",
                 " ".join("%6d из %d" % (int((np.array(tab[f]) > 0).sum()),
                                         len(tab[f])) for f in FEATS)))
        print(u"   %-13s %7s | %s"
              % (u"среднее", "",
                 " ".join("%+9.4f%%" % (100 * np.mean(tab[f]))
                          for f in FEATS)))
        print(u"   %-13s %7s | %s"
              % (u"± ошибка", "",
                 " ".join("%10.4f" % (100 * np.std(tab[f], ddof=1)
                                      / np.sqrt(len(tab[f])))
                          for f in FEATS)))
        for f in FEATS:
            a = np.array(tab[f])
            se = a.std(ddof=1) / np.sqrt(len(a))
            print(u"   %-10s: %+.4f%% ± %.4f%%  ->  %s"
                  % (f, 100 * a.mean(), 100 * se,
                     u"НЕОТЛИЧИМО ОТ НУЛЯ" if abs(a.mean()) < 2 * se
                     else u"отличимо от нуля"))

    print(u"\n\nОБЪЕДИНЁННЫЕ СОЧЕТАНИЯ: всё вместе, одной таблицей")
    for f in FEATS:
        a = np.array([r["gain"] for r in out
                      if r["feat"] == f and np.isfinite(r["gain"])])
        se = a.std(ddof=1) / np.sqrt(len(a))
        print(u"   %-10s: %d наблюдений, в плюсе %d, среднее %+.4f%% ± "
              u"%.4f%%  ->  %s"
              % (f, len(a), int((a > 0).sum()), 100 * a.mean(), 100 * se,
                 u"НЕОТЛИЧИМО ОТ НУЛЯ" if abs(a.mean()) < 2 * se
                 else u"отличимо от нуля"))

    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    with open(os.path.join(OUT, "ml_r2_combo.json"), "w",
              encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, default=float)
    print(u"\nсохранено -> out/ml_r2_combo.json")


if __name__ == "__main__":
    main()
