# -*- coding: utf-8 -*-
u"""Итог направления «машинное обучение»: что осталось после всех проверок.

ПОРОГ БЕЗУБЫТОЧНОСТИ ФИЛЬТРА. Любой фильтр выбрасывает часть входов. Если
оставлена доля f, а средняя доходность входа была m, то месячная доходность
сохранится только при условии

        (m + прибавка) * f  >=  m       то есть   прибавка >= m * (1 - f) / f

При f = 1/2 это значит: прибавка обязана быть НЕ МЕНЬШЕ самой средней
доходности входа. Иначе фильтр, даже безупречно честный и статистически
значимый, всё равно уменьшает заработок: он отрезает половину сделок ради
прибавки, которая этой половины не стоит.

Именно здесь и заканчивается вся история. Прибавка нашлась — небольшая, зато
устойчивая по знаку. Порог безубыточности она не берёт.

Считается по всем сочетаниям параметров, а не по одному, и по обоим наборам
правил. Ничего не выбирается: печатается всё.
"""
import json
import os
import sys

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import ml_lib           # noqa: E402
import ml_r2_lib as L   # noqa: E402
import ml_r2_null as N  # noqa: E402
import ml_r2_pool as P  # noqa: E402
import ml_r2_simple as S  # noqa: E402

OUT = os.path.join(DIR, "out")
COMBOS = [u"срединное", u"случайное 1", u"случайное 2", u"случайное 3",
          u"случайное 4"]
FEATS = ["perf10", "streak", "xs_rv_med"]


def main():
    rows = []
    print(u"ФИЛЬТР ПРОТИВ ПОРОГА БЕЗУБЫТОЧНОСТИ")
    print(u"   «нужно» = m*(1-f)/f — прибавка, при которой месячная доходность")
    print(u"   хотя бы не упадёт. «взял» = да, если прибавка её достигла.\n")
    print(u"   %-10s %-13s %-6s %8s %8s %8s %8s %8s %5s"
          % (u"признак", u"сочетание", u"набор", u"остав.", u"m,вход",
             u"прибавка", u"нужно", u"мес,было", u"взял"))
    for feat in FEATS:
        for stag, rules, tfs in ((u"анс", P.RULES4, ("60", "240")),
                                 (u"сем", P.RULES10, ("60", "240")),
                                 (u"анс4ч", P.RULES4, ("240",)),
                                 (u"сем4ч", P.RULES10, ("240",))):
            for tag in COMBOS:
                combo = "center" if tag == u"срединное" else int(tag.split()[-1])
                d = L.pooled(rules, tfs=tfs, combo=combo)
                if d is None or len(d["y"]) < 400:
                    continue
                folds = P.purged_folds(d["t_in"], d["t_out"], min_train=300)
                r = S.one_feature(d, folds, d["names"].index(feat))
                if r is None:
                    continue
                keep, ret, cfg = r
                te = np.concatenate([f["test"] for f in folds])
                ti, to = d["t_in"][te], d["t_out"][te]
                g = N.dmean_in(ret, cfg, keep)
                f = float(keep.mean())
                m = float(ret.mean())
                need = m * (1 - f) / f if f > 0 else np.inf
                mo0 = ml_lib.monthly_rate(ret, ti, to)
                mo1 = ml_lib.monthly_rate(ret[keep], ti[keep], to[keep])
                ok = (m <= 0) or (g >= need)
                rows.append(dict(feat=feat, set=stag, combo=tag, gain=g,
                                 frac=f, m=m, need=need, mo0=mo0, mo1=mo1,
                                 better=bool(mo1 > mo0)))
                print(u"   %-10s %-13s %-6s %7.0f%% %+7.4f%% %+7.4f%% "
                      u"%+7.4f%% %+7.2f%% %5s"
                      % (feat, tag, stag, 100 * f, 100 * m, 100 * g,
                         100 * need, 100 * mo0, u"да" if ok else u"нет"))
    print()
    for feat in FEATS:
        sub = [r for r in rows if r["feat"] == feat]
        pos = [r for r in sub if r["m"] > 0]
        print(u"   %-10s: месяц стал выше в %d случаях из %d; "
              u"порог безубыточности взят в %d из %d прибыльных"
              % (feat, sum(1 for r in sub if r["better"]), len(sub),
                 sum(1 for r in pos if r["gain"] >= r["need"]), len(pos)))

    print(u"\n\nПОЧЕМУ «МЕСЯЦ СТАЛ ВЫШЕ» ЗДЕСЬ НЕ ДОКАЗАТЕЛЬСТВО")
    a = [r for r in rows if r["m"] > 0]
    b = [r for r in rows if r["m"] <= 0]
    print(u"   там, где правила БЕЗ фильтра были прибыльны (%d случаев):"
          % len(a))
    print(u"      месяц с фильтром выше в %d из %d"
          % (sum(1 for r in a if r["better"]), len(a)))
    print(u"      среднее: было %+.2f%%, стало %+.2f%%"
          % (100 * np.mean([r["mo0"] for r in a]),
             100 * np.mean([r["mo1"] for r in a])))
    print(u"   там, где правила БЕЗ фильтра были убыточны (%d случаев):"
          % len(b))
    print(u"      месяц с фильтром выше в %d из %d"
          % (sum(1 for r in b if r["better"]), len(b)))
    print(u"      среднее: было %+.2f%%, стало %+.2f%%"
          % (100 * np.mean([r["mo0"] for r in b]),
             100 * np.mean([r["mo1"] for r in b])))
    print(u"\n   Фильтр стягивает и хорошее, и плохое к одному и тому же")
    print(u"   уровню: убыточное подтягивает вверх, прибыльное тянет вниз.")
    print(u"   Так ведёт себя уменьшение ставки, а не умение выбирать входы.")

    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    with open(os.path.join(OUT, "ml_r2_final.json"), "w",
              encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, default=float)
    print(u"\nсохранено -> out/ml_r2_final.json")


if __name__ == "__main__":
    main()
