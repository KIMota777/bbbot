# -*- coding: utf-8 -*-
u"""Что осталось: не модель, а один рубильник по волатильности рынка.

Единственное, что прошло всё — правильный ноль, поправку на число попыток,
пять сочетаний параметров, оба набора правил и порог безубыточности — это не
мета-модель, а ОДИН признак с ОДНИМ порогом: xs_rv_med, медианная по пяти
монетам реализованная волатильность за 96 баров, порог — её медиана на
обучающем куске, сторона выбрана обучением.

Здесь он разбирается до конца, и главный вопрос не «значим ли он», а
СКОЛЬКО В НЁМ НЕЗАВИСИМЫХ НАБЛЮДЕНИЙ. Волатильность держится месяцами.
Фильтр по ней — это не тысяча решений, а несколько десятков: «в этот кусок
истории торгуем, в тот нет». Двадцать совпадений на двух наборах правил и
пяти сочетаниях параметров выглядят как двадцать подтверждений, но приходятся
на ОДНИ И ТЕ ЖЕ несколько эпизодов рынка 2023-2026 годов. Это одно
наблюдение, повторённое двадцать раз, а не двадцать наблюдений.
"""
import json
import os
import sys

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)

import ml_lib            # noqa: E402
import ml_r2_block as B  # noqa: E402
import ml_r2_lib as L    # noqa: E402
import ml_r2_null as N   # noqa: E402
import ml_r2_pool as P   # noqa: E402
import ml_r2_simple as S  # noqa: E402
import ml_r2_streak as T  # noqa: E402
import rdata             # noqa: E402

OUT = os.path.join(DIR, "out")


def main():
    sets = []
    for stag, rules in ((u"ансамбль 4ч", P.RULES4),
                        (u"семейства 4ч", P.RULES10)):
        d = L.pooled(rules, tfs=("240",))
        f = P.purged_folds(d["t_in"], d["t_out"], min_train=300)
        sets.append((stag, d, f))
    j = sets[0][1]["names"].index("xs_rv_med")

    print(u"1. КУДА СМОТРИТ РУБИЛЬНИК")
    for stag, d, folds in sets:
        dirs = T.direction(d, folds, j)
        side = [u"выше медианы" if x[1] == u"после выигрышей"
                else u"ниже медианы" for x in dirs]
        print(u"   %-14s %s" % (stag, ", ".join(side)))
        print(u"   %-14s порог по окнам: %s"
              % ("", ", ".join("%.4f" % x[0] for x in dirs)))

    print(u"\n2. СКОЛЬКО В ЭТОМ НЕЗАВИСИМЫХ РЕШЕНИЙ")
    for stag, d, folds in sets:
        r = S.one_feature(d, folds, j)
        keep, ret, cfg = r
        te = np.concatenate([f["test"] for f in folds])
        ti = d["t_in"][te]
        nblocks = sum(B.runs(keep[cfg == c]) for c in np.unique(cfg))
        # сколько РАЗНЫХ календарных недель попало под «торгуем»
        wk = np.unique((ti[keep] // (7 * rdata.DAY_MS)))
        wk_all = np.unique((ti // (7 * rdata.DAY_MS)))
        print(u"   %-14s сделок %d, оставлено %d (%.0f%%), сплошных "
              u"кусков %d" % (stag, len(ret), int(keep.sum()),
                              100 * keep.mean(), nblocks))
        print(u"   %-14s недель с торговлей %d из %d — вот сколько тут"
              u" по-настоящему разных решений" % ("", len(wk), len(wk_all)))

    print(u"\n3. ПРИБАВКА ПО ОКНАМ ПРОВЕРКИ ОТДЕЛЬНО")
    for stag, d, folds in sets:
        r = S.one_feature(d, folds, j)
        keep, ret, cfg = r
        off, per = 0, []
        for f in folds:
            n = len(f["test"])
            s = slice(off, off + n)
            per.append(N.dmean_in(ret[s], cfg[s], keep[s]))
            off += n
        per = np.array(per)
        print(u"   %-14s %s   положительных %d из %d"
              % (stag, " ".join("%+.3f%%" % (100 * x) for x in per),
                 int(np.nansum(per > 0)), len(per)))

    print(u"\n4. ПРАВИЛЬНЫЙ НОЛЬ И ДЕНЬГИ")
    res = []
    for stag, d, folds in sets:
        r = S.one_feature(d, folds, j)
        keep, ret, cfg = r
        te = np.concatenate([f["test"] for f in folds])
        ti, to = d["t_in"][te], d["t_out"][te]
        mo0 = ml_lib.monthly_rate(ret, ti, to)
        mo1 = ml_lib.monthly_rate(ret[keep], ti[keep], to[keep])
        res.append(B.report(u"xs_rv_med / %s" % stag, ret, cfg, keep, 117))
        print(u"      месяц: без фильтра %+.2f%%, с фильтром %+.2f%% "
              u"(риск 1%% на рукав)" % (100 * mo0, 100 * mo1))
        res[-1].update(mo0=mo0, mo1=mo1, set=stag)

    print(u"\n5. ЧЕГО ЭТО НЕ ДОКАЗЫВАЕТ")
    print(u"   Рубильник по волатильности — решение о РЕЖИМЕ РЫНКА, а не о")
    print(u"   входе. Его нельзя проверить числом сделок: сколько бы правил и")
    print(u"   монет ни навесить, все они включаются и выключаются вместе, в")
    print(u"   одни и те же несколько десятков недель. Три года истории дают")
    print(u"   единицы независимых наблюдений о режиме, и никакая мета-модель")
    print(u"   этого не исправит. Проверять такое надо не на этой выборке.")
    print(u"   К мета-разметке (то есть к заданию этого направления) находка")
    print(u"   отношения не имеет: модель со всеми 47 признаками, включая")
    print(u"   этот, порога безубыточности не взяла ни разу.")

    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    with open(os.path.join(OUT, "ml_r2_regime.json"), "w",
              encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, default=float)
    print(u"\nсохранено -> out/ml_r2_regime.json")


if __name__ == "__main__":
    main()
